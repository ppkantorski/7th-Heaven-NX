#!/usr/bin/env python3
"""Name the OpenGL call that is failing. No rebuild, no quality cost.

    python3 probe_glcall.py                  # show what is live
    python3 probe_glcall.py --gate blit      # make ONLY that one fatal
    python3 probe_glcall.py --gate unbind0
    python3 probe_glcall.py --gate rebind
    python3 probe_glcall.py --restore        # back to the shipped build

WHAT WE ALREADY KNOW, AND WHY THIS IS ONE COMMAND
-------------------------------------------------
`ff7nx_glerror` ships mode `flip`: the three end-of-frame reporters have
their fatal `udf` NOPed, the other ten are untouched and still fatal.

Hardware says the game does NOT crash in that configuration, and it DOES
corrupt. Those two facts together are a measurement:

  * none of the ten render-target reporters is firing -- if one were, the
    game would die, because their traps are still live;
  * therefore the OpenGL error is at one of the THREE end-of-frame calls,
    and it is only invisible because we NOPed exactly those three.

So the question has already been narrowed from thirteen to three without
anyone giving up a setting. This tool answers which of the three.

HOW
---
It restores the fatal `udf` on ONE of the three and leaves the other two
NOPed. If that call is the one erroring, the game dies the moment it does --
which is the same crash you already know, so nothing new is being risked.
If it does not die, that call is clean and you try the next.

    --gate unbind0   +0x113C6C4  glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0)
    --gate blit      +0x113C75C  glBlitFramebuffer   <- the presentation blit
    --gate rebind    +0x113C7CC  glBindFramebuffer(saved fbo)

Worst case is three boots. `blit` is the one to try first: it is the
presentation blit, it reads its width and height from `*(0x3FEB730)`, and it
is the only one of the three that touches the render-target geometry this
build changes.

WHAT AN ANSWER BUYS
-------------------
An erroring `glBlitFramebuffer` means the read or draw framebuffer is
incomplete or mismatched at present time -- a concrete defect in the
render-target setup, findable in the binary, and fixable without lowering
anything. An erroring `glBindFramebuffer` means the framebuffer object
itself is stale, which points at object lifetime instead.

Right now we have neither, and every remaining theory is unfalsified. This
is the cheapest way to make one of them falsifiable.

Nothing here changes a texture, an archive, or a resolution. It edits one
word of `exefs/main` and `--restore` puts the shipped build back exactly.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import types


def ensure_lz4():
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

import ff7nx_glerror as G                                      # noqa: E402
import nso_patcher                                             # noqa: E402

TITLE_ID = '0100A5B00BDC6000'
DEFAULT_MAIN = (HERE / 'sdout' / 'atmosphere' / 'contents' / TITLE_ID /
                'exefs' / 'main')
BACKUP_SUFFIX = '.pre-glcall'

# The three end-of-frame gates, by the name of the call each one guards.
# Derived from ff7nx_glerror rather than typed again, so the two cannot
# drift apart.
GATE_BY_NAME = {
    'unbind0': 0x113C6C4,
    'blit':    0x113C75C,
    'rebind':  0x113C7CC,
}
assert set(GATE_BY_NAME.values()) == set(G.FLIP_VAS), \
    'the end-of-frame gate set no longer matches ff7nx_glerror.FLIP_VAS'


def _image(nso):
    end = max(seg.va + len(seg.data) for seg in nso.segments)
    img = bytearray(end)
    for seg in nso.segments:
        img[seg.va:seg.va + len(seg.data)] = seg.data
    return bytes(img)


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _hex(w):
    return ' '.join('%02X' % b for b in struct.pack('<I', w))


def show(img, target):
    print('module: %s' % target)
    bad = G.verify(img)
    live = []
    for nm, gva in GATE_BY_NAME.items():
        trap = G.REPORT_TRAPS[gva]
        w = _word(img, trap)
        state = ('FATAL (will crash if this call errors)' if w == G.FATAL
                 else 'silenced' if w == G.NOP else 'UNKNOWN %08X' % w)
        if w == G.FATAL:
            live.append(nm)
        label = [g[3] for g in G.GATES if g[0] == gva][0]
        print('  %-8s +0x%07X  %-38s %s' % (nm, trap, state, label))
    n_other = sum(1 for g in G.GATES if g[0] not in G.FLIP_VAS
                  and _word(img, G.REPORT_TRAPS[g[0]]) == G.FATAL)
    print('  (the other %d render-target reporters are fatal: %s)'
          % (len(G.GATES) - 3, 'yes' if n_other == 10 else '%d of 10' % n_other))
    for line in bad:
        print('  ! ' + line)
    if len(live) == 0:
        print('  -> the shipped build: all three end-of-frame reporters '
              'silenced')
    elif len(live) == 1:
        print('  -> DIAGNOSTIC: only %r is fatal. If the game dies, that is '
              'the failing call.' % live[0])
    else:
        print('  -> %d of the three are fatal; a crash would not tell them '
              'apart. Pick one with --gate.' % len(live))
    return bool(bad)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--main', type=Path, default=DEFAULT_MAIN)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--gate', choices=sorted(GATE_BY_NAME),
                   help='make ONLY this end-of-frame reporter fatal')
    g.add_argument('--restore', action='store_true',
                   help='silence all three again (the shipped build)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    target = a.main.resolve()
    if not target.is_file():
        raise SystemExit('no sdout module: %s' % target)
    nso = nso_patcher.read_nso(target)
    img = _image(nso)
    if show(img, target):
        raise SystemExit('refusing to write over a module whose reporters do '
                         'not match')
    if not (a.gate or a.restore):
        print('\nnothing changed. Pass --gate <name> or --restore.')
        return 0

    ps = []
    for nm, gva in GATE_BY_NAME.items():
        trap = G.REPORT_TRAPS[gva]
        want = G.FATAL if (a.gate == nm) else G.NOP
        cur = _word(img, trap)
        if cur == want:
            continue
        ps.append({'name': '%s reporter @ +0x%07X -> %s'
                           % (nm, trap,
                              'FATAL' if want == G.FATAL else 'silenced'),
                   'va': trap, 'expect': _hex(cur), 'set': _hex(want)})
    if not ps:
        print('\nalready in that state; nothing to do.')
        return 0
    try:
        applied = nso_patcher.apply_spec(
            nso, {'name': 'probe_glcall', 'patches': ps})
        rebuilt = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        raise SystemExit('refused: %s' % exc)
    print('')
    for line in applied:
        print('  ' + line)
    if a.dry_run:
        print('dry run complete; no files changed')
        return 0
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(target, backup)
        print('  backup: %s' % backup)
    fd, tmp = tempfile.mkstemp(prefix='.glcall-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(rebuilt)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    check = _image(nso_patcher.read_nso(target))
    for nm, gva in GATE_BY_NAME.items():
        want = G.FATAL if (a.gate == nm) else G.NOP
        if _word(check, G.REPORT_TRAPS[gva]) != want:
            raise SystemExit('post-write check failed on %r; restore %s'
                             % (nm, backup))
    print('  sha256: %s' % hashlib.sha256(target.read_bytes()).hexdigest())
    print('\nCopy atmosphere/contents/%s/exefs/main to the SD card.'
          % TITLE_ID)
    if a.gate:
        print('\nPlay until lower Junon corrupts.')
        print('  IT CRASHES  -> %r is the failing call.' % a.gate)
        print('  IT CORRUPTS WITHOUT CRASHING -> %r is clean; try the next '
              'one.' % a.gate)
        print('\nOrder worth trying: blit, then rebind, then unbind0.')
        print('--restore puts the shipped build back.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
