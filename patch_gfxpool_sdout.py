#!/usr/bin/env python3
"""A/B the GRAPHICS pool in a built sdout module. No rebuild.

    python3 patch_gfxpool_sdout.py                 # show what is live
    python3 patch_gfxpool_sdout.py --mb 384        # the shipped default
    python3 patch_gfxpool_sdout.py --mb 512        # one rung higher
    python3 patch_gfxpool_sdout.py --stock         # back to the port's 256

WHY THIS EXISTS
---------------
`nv::InitializeGraphics` is called once, at module +0x113BD7C, with a
hardcoded 256 MB. That block is the NVN/GL pool -- textures, render targets,
vertex and command memory. This build spends far more of it than the game
the port sized it for: +25.78 MB of permanent field render target at 3x, and
768px truecolor field pages costing 3.38 MB apiece against a vanilla
paletted page's 0.31 MB, plus 768px world and battle texture caps.

Whether that is the cause of the end-of-frame fault in the crash reports is
NOT established -- see FINDINGS-302, which gets as far as "the faulting call
is the last call of gfx_drv_flip, reached through UserExceptionHandler" and
stops there rather than guessing. This tool exists so the question can be
answered by playing rather than by reading more disassembly: two words, no
rebuild, and the same route on each setting.

It edits only `exefs/main`. flevel.lgp, char.lgp and every other archive are
untouched, so the SD card only needs `atmosphere/contents/<title>/exefs/main`
copied over again.

FAILURE MODE, SO IT IS NOT A SURPRISE
-------------------------------------
The pool is a plain malloc out of nnSdk's heap. If the console cannot give
it, the malloc returns NULL and `nv::InitializeGraphics(NULL, n)` aborts on
the FIRST FRAME. A size that is too big means the game does not boot -- it
does not mean a subtle new bug. If that happens, run `--stock` and copy the
module across again.

A backup of the module as it stood before the first run of this tool is kept
beside it as `main.pre-gfxpool`.
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
        n = lib.LZ4_decompress_safe(data, out, len(data), uncompressed_size)
        if n < 0:
            raise RuntimeError('LZ4 decompression failed (%d)' % n)
        return out.raw[:n]

    def compress(data, store_size=False, **_kw):
        if store_size:
            raise RuntimeError('the NSO fallback requires raw LZ4 blocks')
        cap = lib.LZ4_compressBound(len(data))
        out = ctypes.create_string_buffer(cap)
        n = lib.LZ4_compress_default(data, out, len(data), cap)
        if n <= 0:
            raise RuntimeError('LZ4 compression failed (%d)' % n)
        return out.raw[:n]

    block.decompress = decompress
    block.compress = compress
    pkg = types.ModuleType('lz4')
    pkg.block = block
    sys.modules['lz4'] = pkg
    sys.modules['lz4.block'] = block


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

ensure_lz4()

import ff7nx_gfxpool                                           # noqa: E402
import nso_patcher                                             # noqa: E402

TITLE_ID = '0100A5B00BDC6000'
DEFAULT_MAIN = (HERE / 'sdout' / 'atmosphere' / 'contents' / TITLE_ID /
                'exefs' / 'main')
BACKUP_SUFFIX = '.pre-gfxpool'


def _read_word(nso, va):
    seg, off = nso_patcher.segment_for_va(nso, va, 4)
    return int.from_bytes(bytes(seg.data[off:off + 4]), 'little')


def _image(nso):
    """A module-offset-addressable image of the decompressed segments.

    ff7nx_gfxpool reads by module offset. Building a sparse image out of the
    already-decompressed segments avoids a second full NSO parse and, more
    importantly, reads the module AS THE PATCHER SEES IT -- so `--show`
    after a write reports the bytes that were actually written.
    """
    end = max(seg.va + len(seg.data) for seg in nso.segments)
    img = bytearray(end)
    for seg in nso.segments:
        img[seg.va:seg.va + len(seg.data)] = seg.data
    return bytes(img)


def show(nso, target):
    img = _image(nso)
    mb = ff7nx_gfxpool.read_mb(img)
    print('module: %s' % target)
    print('live:   %s'
          % ('%d MB%s' % (mb, '   (the port\'s stock value)'
                          if mb == ff7nx_gfxpool.STOCK_MB else '')
             if mb is not None else 'UNDECODABLE -- refusing to guess'))
    for i, (rd, what) in sorted(ff7nx_gfxpool.SITE['fields'].items()):
        va = ff7nx_gfxpool.SITE['va'] + 4 * i
        print('   +0x%07X  w%d  %08X   %s'
              % (va, rd, _read_word(nso, va), what))
    problems = ff7nx_gfxpool.verify_site(img)
    for line in problems:
        print('!  ' + line)
    return mb, problems


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--main', type=Path, default=DEFAULT_MAIN,
                    help='built sdout exefs/main (default: %(default)s)')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--mb', type=int,
                   help='pool size in MB (%s)'
                        % ', '.join('%d' % m for m in ff7nx_gfxpool.sizes()))
    g.add_argument('--stock', action='store_true',
                   help='restore the port\'s %d MB'
                        % ff7nx_gfxpool.STOCK_MB)
    ap.add_argument('--dry-run', action='store_true',
                    help='verify and rebuild in memory without writing')
    args = ap.parse_args(argv)

    target = args.main.resolve()
    if not target.is_file():
        raise SystemExit('no sdout module: %s' % target)

    if not ff7nx_gfxpool.selftest(lambda *_: None):
        ff7nx_gfxpool.selftest()
        raise SystemExit('encoder selftest FAILED -- nothing was written')

    nso = nso_patcher.read_nso(target)
    live, problems = show(nso, target)
    if problems:
        raise SystemExit('refusing to write over a module whose graphics '
                         'bring-up block does not match')

    want = ff7nx_gfxpool.STOCK_MB if args.stock else args.mb
    if want is None:
        print('\nnothing changed. Pass --mb <n> or --stock to switch.')
        print('ladder: %s MB'
              % ', '.join('%d' % m for m in ff7nx_gfxpool.sizes()))
        return 0

    # `encodable` refuses anything below stock, which would otherwise make
    # `--stock` unreachable from a raised module. Allow exactly stock.
    if want != ff7nx_gfxpool.STOCK_MB:
        why = ff7nx_gfxpool.encodable(want)
        if why:
            raise SystemExit('refused: %s' % why)
    if want == live:
        print('\nalready %d MB; nothing to do.' % want)
        return 0

    img = _image(nso)
    try:
        patches = ff7nx_gfxpool.patches(img, want) if want != \
            ff7nx_gfxpool.STOCK_MB else _stock_patches(img)
        if not patches:
            print('\nalready %d MB; nothing to do.' % want)
            return 0
        applied = nso_patcher.apply_spec(nso, {
            'name': 'graphics pool -> %d MB' % want,
            'patches': patches,
        })
        rebuilt = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        raise SystemExit('refused: %s' % exc)

    print('')
    for line in applied:
        print('  ' + line)
    if args.dry_run:
        print('dry run complete; no files changed')
        return 0

    backup = target.with_name(target.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(target, backup)
        print('backup: %s' % backup)

    fd, tmp = tempfile.mkstemp(prefix='.gfxpool-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(rebuilt)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    check = nso_patcher.read_nso(target)
    now = ff7nx_gfxpool.read_mb(_image(check))
    if now != want:
        raise SystemExit('post-write check read back %s, expected %d MB; '
                         'restore %s' % (now, want, backup))
    print('now:    %d MB' % now)
    print('sha256: %s' % hashlib.sha256(target.read_bytes()).hexdigest())
    print('\nCopy atmosphere/contents/%s/exefs/main to the SD card and play '
          'the same route as before. Nothing else changed.' % TITLE_ID)
    return 0


def _stock_patches(img):
    """Put the port's own words back, whatever the module currently holds."""
    out = []
    for i, (rd, what) in sorted(ff7nx_gfxpool.SITE['fields'].items()):
        va = ff7nx_gfxpool.SITE['va'] + 4 * i
        cur = int.from_bytes(img[va:va + 4], 'little')
        new = ff7nx_gfxpool.encode_size(rd, ff7nx_gfxpool.STOCK_BYTES)
        if cur == new:
            continue
        out.append({'name': 'graphics pool @ +0x%07X (%s) -> stock'
                            % (va, what),
                    'va': va,
                    'expect': ' '.join('%02X' % b for b in
                                       cur.to_bytes(4, 'little')),
                    'set': ' '.join('%02X' % b for b in
                                    new.to_bytes(4, 'little'))})
    return out


if __name__ == '__main__':
    raise SystemExit(main())
