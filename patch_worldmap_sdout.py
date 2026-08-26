#!/usr/bin/env python3
"""Patch the completed sdout/main with the world-map corrections only.

This intentionally does not invoke build.py, read flevel.lgp, or rebuild any
Cosmos content.  It is a hardware-test shortcut for an sdout that already
contains Build 177's 60 FPS and 16:9 output.  The ordinary build/GUI path uses
the same patch definitions through ff7nx_ws.apply_module.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types


def ensure_lz4():
    """Supply the two raw-block operations if python-lz4 is unavailable."""
    try:
        import lz4.block  # noqa: F401
        return
    except ImportError:
        pass
    library = ctypes.util.find_library('lz4')
    if not library:
        raise SystemExit('need python-lz4 or a system liblz4 installation')
    lib = ctypes.CDLL(library)
    lib.LZ4_decompress_safe.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                        ctypes.c_int, ctypes.c_int]
    lib.LZ4_decompress_safe.restype = ctypes.c_int
    lib.LZ4_compressBound.argtypes = [ctypes.c_int]
    lib.LZ4_compressBound.restype = ctypes.c_int
    lib.LZ4_compress_default.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                         ctypes.c_int, ctypes.c_int]
    lib.LZ4_compress_default.restype = ctypes.c_int

    block = types.ModuleType('lz4.block')

    def decompress(data, uncompressed_size):
        out = ctypes.create_string_buffer(uncompressed_size)
        size = lib.LZ4_decompress_safe(
            data, out, len(data), uncompressed_size)
        if size < 0:
            raise RuntimeError('LZ4 decompression failed (%d)' % size)
        return out.raw[:size]

    def compress(data, store_size=False, **_kwargs):
        if store_size:
            raise RuntimeError('the NSO fallback requires raw LZ4 blocks')
        capacity = lib.LZ4_compressBound(len(data))
        out = ctypes.create_string_buffer(capacity)
        size = lib.LZ4_compress_default(
            data, out, len(data), capacity)
        if size <= 0:
            raise RuntimeError('LZ4 compression failed (%d)' % size)
        return out.raw[:size]

    block.decompress = decompress
    block.compress = compress
    package = types.ModuleType('lz4')
    package.block = block
    sys.modules['lz4'] = package
    sys.modules['lz4.block'] = block


ensure_lz4()

import ff7nx_widescreen as W
import ff7nx_ws as WS
import nso_patcher
import nxmap


HERE = Path(__file__).resolve().parent
TITLE_ID = '0100A5B00BDC6000'
DEFAULT_MAIN = (HERE / 'sdout' / 'atmosphere' / 'contents' / TITLE_ID /
                'exefs' / 'main')
SUBMIT_VAS = (0x00F58668, 0x00F58674)
MANAGED_VAS = (W.WORLD_EDGE_BLOCK_HOOK,) + SUBMIT_VAS
NOP = bytes.fromhex('1F 20 03 D5')
SKY_MOVZ_W23_20 = 0x52800297


def patch_bytes(patch, key):
    return bytes.fromhex(patch[key])


def current_bytes(nso, va, size=4):
    segment, offset = nso_patcher.segment_for_va(nso, va, size)
    return segment.data[offset:offset + size]


def final_patches():
    patches = [dict(p) for p in W.WORLD_PATCHES
               if p['va'] in MANAGED_VAS]
    if tuple(p['va'] for p in patches) != MANAGED_VAS:
        raise RuntimeError('the corrected edge/submit patches are missing')
    return patches


def classify(have, old, new):
    return ('old' if have == old else 'new' if have == new else 'unknown')


def branch_target(pc, insn):
    """Decode an unconditional AArch64 B, rejecting every other opcode."""
    if insn & 0xFC000000 != 0x14000000:
        raise ValueError('not an unconditional branch')
    imm = insn & 0x03FFFFFF
    if imm & 0x02000000:
        imm -= 0x04000000
    return pc + imm * 4


def sky_hook_state(nso):
    """Recognise the stock hook or the exact three-instruction sky cave."""
    hook = int.from_bytes(current_bytes(nso, W.WORLD_SKY_BOTTOM_HOOK),
                          'little')
    if hook == W.WORLD_SKY_BOTTOM_ORIG:
        return 'old'
    try:
        pc = branch_target(W.WORLD_SKY_BOTTOM_HOOK, hook)
        if int.from_bytes(current_bytes(nso, pc), 'little') != SKY_MOVZ_W23_20:
            return 'unknown'
        pc += 4
        word = int.from_bytes(current_bytes(nso, pc), 'little')
        # emit_hooked may chain across padding runs.  Skip at most one link
        # between each of the three logical cave instructions.
        if word & 0xFC000000 == 0x14000000:
            pc = branch_target(pc, word)
            word = int.from_bytes(current_bytes(nso, pc), 'little')
        if word != W.WORLD_SKY_BOTTOM_STORE:
            return 'unknown'
        pc += 4
        word = int.from_bytes(current_bytes(nso, pc), 'little')
        if (word & 0xFC000000 == 0x14000000
                and branch_target(pc, word) != W.WORLD_SKY_BOTTOM_HOOK + 4):
            pc = branch_target(pc, word)
            word = int.from_bytes(current_bytes(nso, pc), 'little')
        return ('new' if branch_target(pc, word) ==
                W.WORLD_SKY_BOTTOM_HOOK + 4 else 'unknown')
    except (IndexError, ValueError):
        return 'unknown'


def state(nso):
    direct = []
    for patch in final_patches():
        have = current_bytes(nso, patch['va'])
        direct.append(classify(have, patch_bytes(patch, 'expect'),
                               patch_bytes(patch, 'set')))
    null_have = current_bytes(nso, W.WORLD_NULL_MESH_GUARD)
    null_state = classify(
        null_have, NOP, W.WORLD_NULL_MESH_GUARD_WORD.to_bytes(4, 'little'))
    return direct, null_state, sky_hook_state(nso)


def verify_common_baseline(nso):
    """Require every unaffected Build-177 word before applying corrections."""
    for patch in W.PATCHES:
        if current_bytes(nso, patch['va']) != patch_bytes(patch, 'set'):
            raise RuntimeError('%s is not in its built 16:9 state' %
                               patch['name'])
    for patch in W.WORLD_PATCHES:
        if patch['va'] in MANAGED_VAS:
            continue
        if current_bytes(nso, patch['va']) != patch_bytes(patch, 'set'):
            raise RuntimeError('%s is not in its Build-177 state' %
                               patch['name'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main', type=Path, default=DEFAULT_MAIN,
                        help='existing sdout exefs/main (default: %(default)s)')
    parser.add_argument('--dry-run', action='store_true',
                        help='verify and rebuild in memory without writing')
    args = parser.parse_args(argv)
    target = args.main.resolve()
    if not target.is_file():
        raise SystemExit('no existing sdout module: %s' % target)

    try:
        nso = nso_patcher.read_nso(target)
        direct_state, null_state, hook_state = state(nso)
        final_direct = ['new'] * len(MANAGED_VAS)
        if (direct_state == final_direct and null_state == 'new'
                and hook_state == 'new'):
            print('already patched: %s' % target)
            return 0
        if ('unknown' in direct_state or null_state == 'unknown'):
            raise RuntimeError('refusing a partial/unrecognised world-map '
                               'state: edge/submit=%s, null-guard=%s, '
                               'sky-hook=%s' %
                               (direct_state, null_state, hook_state))
        verify_common_baseline(nso)

        pending = []
        # Build 177 removed the wrong (first) CBZ. Restore it before removing
        # the actual FFNx-equivalent second check.
        if null_state == 'old':
            pending.append({
                'name': 'restore current-mesh null safety guard',
                'va': W.WORLD_NULL_MESH_GUARD,
                'expect': '1F 20 03 D5',
                'set': '68 22 00 34',
            })
        for patch, patch_state in zip(final_patches(), direct_state):
            if patch_state == 'old':
                pending.append(patch)
        direct_log = []
        if pending:
            direct_log = nso_patcher.apply_spec(nso, {
                'name': 'world-map corrected terrain guards/submission',
                'patches': pending,
            })
        cave_log = []
        if hook_state == 'old':
            mapped = nxmap.Main(str(target))
            cave_log = nso_patcher.apply_spec(
                nso, WS.world_sky_cave_spec(mapped, print))
        rebuilt = nso_patcher.rebuild(nso)
    except Exception as exc:  # noqa: BLE001 - command must fail closed
        raise SystemExit('world-map sdout patch refused: %s' % exc)

    print('verified %d terrain correction word(s) and %d sky cave word(s)' %
          (len(direct_log), len(cave_log)))
    if args.dry_run:
        print('dry run complete; no files changed')
        return 0

    # Keep the earlier pre-horizon backup as a separate recovery point.  This
    # one captures the user's known-good horizon build immediately before the
    # corrected terrain-guard mapping is installed.
    backup = target.with_name(target.name + '.pre-worldmap-mesh-guard-fix')
    if not backup.exists():
        shutil.copy2(target, backup)
        print('backup: %s' % backup)
    else:
        print('backup already exists and was preserved: %s' % backup)

    fd, tmp_name = tempfile.mkstemp(prefix='.worldmap-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(rebuilt)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)

    check = nso_patcher.read_nso(target)
    direct_state, null_state, hook_state = state(check)
    if (direct_state != ['new'] * len(MANAGED_VAS)
            or null_state != 'new' or hook_state != 'new'):
        raise SystemExit('post-write verification failed; restore %s' % backup)
    print('patched: %s' % target)
    print('sha256 : %s' % hashlib.sha256(target.read_bytes()).hexdigest())
    print('flevel.lgp and all Cosmos content were left untouched')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
