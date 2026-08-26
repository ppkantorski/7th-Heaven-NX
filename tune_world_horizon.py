#!/usr/bin/env python3
"""Tune the world map's horizon fall-off, the only lever left on pop-in.

WHY THIS EXISTS
---------------
The native world renderer draws a 5x5 block neighbourhood and nothing more.
That is not a constant this project can widen -- see
FINDINGS-WORLDMAP-DRAW-DISTANCE.md.  Its far edge sits between 2.0 and 3.0
blocks (16384..24576 world units) depending on where the camera is inside its
own block, so the edge sweeps in and out as you fly, and the horizon changes
shape.  Terrain that reaches that edge simply stops.

What CAN be moved is how quickly distant terrain is bent down out of sight.
`world_transform_block_vertices_75F0AD` -- replaced in the Switch port by a
hand-written routine at module +0x10F28B0 -- lowers every vertex by

    sink = ( (z/4 - onset) * DEPTH  +  |screen_x - 320| * EDGE ) ** 2

    DEPTH  double at module +0x11AE928, stock 1/64
    EDGE   double at module +0x11AE820, stock 1/32
    onset  world_curvature_onset_E045D8, 5000 minus altitude * 2500 / 256,
           so roughly 2500 in the Highwind and 5000 on foot

Both doubles are referenced from exactly one instruction each, both inside
that routine (verified by scanning every ADRP/LDR pair in .text), so nothing
else in the game moves with them.

The term is ZERO until z passes 4 * onset -- about 10000 units in the air --
and grows as a square after that.  DEPTH is therefore a pure far-field knob:
raising it (a SMALLER denominator) tucks the block edge under the horizon and
stops the pop-in, at the cost of a nearer, more strongly curved horizon.  It
does not touch anything in the foreground at all.  EDGE only acts away from
the screen centre, so it shapes the left and right of the horizon and leaves
the middle of the view alone.

This trades visible distance for stability.  It is a judgement call that only
hardware can settle, which is why this is a separate script with a preview
rather than something wired into the build.

    python3 tune_world_horizon.py                      # show, change nothing
    python3 tune_world_horizon.py --depth 48           # a gentle first try
    python3 tune_world_horizon.py --depth 40 --edge 28
    python3 tune_world_horizon.py --reset              # back to 1/64, 1/32
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
        size = lib.LZ4_decompress_safe(data, out, len(data), uncompressed_size)
        if size < 0:
            raise RuntimeError('LZ4 decompression failed (%d)' % size)
        return out.raw[:size]

    def compress(data, store_size=False, **_kwargs):
        if store_size:
            raise RuntimeError('the NSO fallback requires raw LZ4 blocks')
        capacity = lib.LZ4_compressBound(len(data))
        out = ctypes.create_string_buffer(capacity)
        size = lib.LZ4_compress_default(data, out, len(data), capacity)
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

import nso_patcher                                            # noqa: E402


HERE = Path(__file__).resolve().parent
TITLE_ID = '0100A5B00BDC6000'
DEFAULT_MAIN = (HERE / 'sdout' / 'atmosphere' / 'contents' / TITLE_ID /
                'exefs' / 'main')

DEPTH_VA = 0x011AE928          # double, stock 0.015625 == 1/64
EDGE_VA = 0x011AE820           # double, stock 0.031250 == 1/32
STOCK_DEPTH = 1.0 / 64.0
STOCK_EDGE = 1.0 / 32.0

# The instruction that loads each one.  Fingerprinted so a module whose
# layout differs from the measured one is refused rather than corrupted.
DEPTH_LDR = (0x010F29A8, bytes.fromhex('09 95 44 FD'))   # ldr d9, [x8,#0x928]
EDGE_LDR = (0x010F29A0, bytes.fromhex('08 11 44 FD'))    # ldr d8, [x8,#0x820]
ADRP_PAGE = 0x011AE000

BLOCK = 8192
HIGHWIND_ONSET = 2500          # E045D8 at full altitude; on foot it is 5000


def read(nso, va, size):
    segment, offset = nso_patcher.segment_for_va(nso, va, size)
    return segment.data[offset:offset + size]


def write_doubles(nso, pairs):
    """Replace the coefficients through the project's verified-write path.

    apply_spec re-checks every `expect` byte before anything is committed, so
    a module whose literals have already moved is refused rather than half
    written -- the same all-or-nothing rule the world-map patcher uses.
    """
    patches = []
    for va, have, want, name in pairs:
        patches.append({
            'name': name,
            'va': va,
            'expect': struct.pack('<d', have).hex(),
            'set': struct.pack('<d', want).hex(),
        })
    return nso_patcher.apply_spec(nso, {
        'name': 'world horizon fall-off coefficients',
        'patches': patches,
    })


def adrp_page(word, pc):
    immlo = (word >> 29) & 3
    immhi = (word >> 5) & 0x7FFFF
    imm = (immhi << 2) | immlo
    if imm & (1 << 20):
        imm -= 1 << 21
    return (pc & ~0xFFF) + imm * 4096


def verify_site(nso):
    """Refuse to touch a module whose curvature routine is not the measured one."""
    for va, word in (DEPTH_LDR, EDGE_LDR):
        if read(nso, va, 4) != word:
            raise RuntimeError('the curvature load at 0x%X is not the '
                               'expected instruction' % va)
        prev = struct.unpack('<I', read(nso, va - 4, 4))[0]
        if (prev & 0x9F000000) != 0x90000000 or adrp_page(prev, va - 4) != ADRP_PAGE:
            raise RuntimeError('the ADRP feeding 0x%X does not address the '
                               'measured literal page' % va)


def sink(depth, edge, z, onset, screen_dx):
    a = (z / 4.0 - onset) * depth
    if z / 4.0 - onset < 1:
        return 0.0
    return (a + screen_dx * edge) ** 2


def arc_radius(depth, onset=HIGHWIND_ONSET):
    """Where the horizon arc lands, in blocks, expressed model-free.

    The absolute sink at which terrain stops being visible depends on camera
    height and pitch, neither of which this script knows.  What it does know
    is the sink the STOCK coefficient produced at 3.0 blocks -- an amount the
    hardware has already shown is enough to put terrain out of sight, because
    3.0 blocks is the far end of the window and terrain there is not what is
    popping.  So: solve for the radius at which the proposed coefficient
    reproduces that same sink.

    "Whatever used to vanish at 3.0 blocks now vanishes at this radius."

    That number is the whole decision, because the block window is a SQUARE
    and the horizon is an ARC.  The square's nearest face is 2.00 blocks away
    and its corners are 2.83.  An arc inside 2.00 is inside the square in
    every direction, so terrain always leaves the picture by curving away and
    never by running out of geometry.
    """
    ref = (3.0 * BLOCK / 4.0 - onset) * STOCK_DEPTH
    return 4.0 * (onset + ref / depth) / BLOCK


def preview(depth, edge, label):
    r = arc_radius(depth)
    verdict = ('inside the square in every direction' if r <= 2.0 else
               'inside the corners but NOT the near faces' if r <= 2.83 else
               'outside the block window -- the edge will show')
    print('  %s   depth = 1/%-6.4g   edge = 1/%-6.4g' %
          (label, 1 / depth, 1 / edge))
    print('    horizon arc %.2f blocks (square: 2.00 near face, 2.83 corner)'
          % r)
    print('      -> %s' % verdict)
    print('    distance          centre    4:3 edge   16:9 edge')
    for blocks in (1.0, 1.5, 2.0, 2.5, 3.0):
        z = blocks * BLOCK
        row = [sink(depth, edge, z, HIGHWIND_ONSET, dx) for dx in (0, 320, 427)]
        print('    %.1f blocks %6d  %8.0f  %10.0f  %10.0f'
              % (blocks, z, row[0], row[1], row[2]))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--main', type=Path, default=DEFAULT_MAIN)
    ap.add_argument('--depth', type=float, default=None, metavar='N',
                    help='depth fall-off as 1/N; stock 64. Lower N sinks the '
                         'far horizon harder and hides the block edge.')
    ap.add_argument('--edge', type=float, default=None, metavar='N',
                    help='screen-edge fall-off as 1/N; stock 32. Only affects '
                         'the left and right of the view.')
    ap.add_argument('--reset', action='store_true',
                    help='restore both stock coefficients')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    target = args.main.resolve()
    if not target.is_file():
        raise SystemExit('no existing sdout module: %s' % target)

    try:
        nso = nso_patcher.read_nso(target)
        verify_site(nso)
        have_depth = struct.unpack('<d', read(nso, DEPTH_VA, 8))[0]
        have_edge = struct.unpack('<d', read(nso, EDGE_VA, 8))[0]
    except Exception as exc:                                  # noqa: BLE001
        raise SystemExit('horizon tuner refused: %s' % exc)

    print('world horizon fall-off, sink in world units below true height')
    print('  (Highwind altitude, curvature onset %d -- nothing sinks before '
          '%d units)' % (HIGHWIND_ONSET, HIGHWIND_ONSET * 4))
    print()
    preview(have_depth, have_edge, 'current ')

    if args.reset:
        want_depth, want_edge = STOCK_DEPTH, STOCK_EDGE
    else:
        want_depth = 1.0 / args.depth if args.depth else have_depth
        want_edge = 1.0 / args.edge if args.edge else have_edge

    if (want_depth, want_edge) == (have_depth, have_edge):
        print()
        print('nothing to change.  The 5x5 block window ends somewhere between')
        print('2.0 and 3.0 blocks, so the sink at 2.0 blocks is what decides')
        print('whether its edge is visible.  Raise it by lowering --depth.')
        return 0

    print()
    preview(want_depth, want_edge, 'proposed')

    if args.dry_run:
        print('\ndry run complete; no files changed')
        return 0

    try:
        write_doubles(nso, [
            (DEPTH_VA, have_depth, want_depth, 'world horizon depth fall-off'),
            (EDGE_VA, have_edge, want_edge, 'world horizon edge fall-off'),
        ])
        rebuilt = nso_patcher.rebuild(nso)
    except Exception as exc:                                  # noqa: BLE001
        raise SystemExit('horizon tuner refused: %s' % exc)

    backup = target.with_name(target.name + '.pre-horizon-tuning')
    if not backup.exists():
        shutil.copy2(target, backup)
        print('\nbackup: %s' % backup)
    else:
        print('\nbackup already exists and was preserved: %s' % backup)

    fd, tmp_name = tempfile.mkstemp(prefix='.horizon-', dir=target.parent)
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
    got = (struct.unpack('<d', read(check, DEPTH_VA, 8))[0],
           struct.unpack('<d', read(check, EDGE_VA, 8))[0])
    if got != (want_depth, want_edge):
        raise SystemExit('post-write verification failed; restore %s' % backup)
    print('patched: %s' % target)
    print('sha256 : %s' % hashlib.sha256(target.read_bytes()).hexdigest())
    print('only the two curvature coefficients changed; every world-map')
    print('correction, flevel.lgp and all Cosmos content are untouched')
    print('(the literals live in a compressed segment, so this rewrites that')
    print(' segment\'s LZ4 payload -- the sha differs from the world-map')
    print(' patcher\'s even after --reset, while the decompressed image does')
    print(' not.  Compare with the .pre-horizon-tuning backup if in doubt.)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
