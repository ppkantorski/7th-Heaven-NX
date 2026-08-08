#!/usr/bin/env python3
"""
ws_quickfix.py -- strip the 16:9 framing back to the four words that are
actually measured, WITHOUT a rebuild.

    python3 ws_quickfix.py /path/to/sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main

WHY THIS EXISTS
===============
Six hardware builds have gone into this at ~20 minutes each, and most of
that time was spent rebuilding 380 MB of archives that the widescreen work
does not touch. The framing lives entirely in `exefs/main` plus two shader
files, both of which are a copy away. This edits the module you already
built, in place, so the next test is a file copy instead of a build.

WHAT IT REVERTS, AND WHY
========================
Everything except `gfx_drv_init`'s four words. Specifically:

  game_w 640 -> 854, x3        The wrong lever. FFNx NEVER changes
                               `game_width` -- `setD3DProjection` scales the
                               GAME'S OWN projection matrix by 0.75 instead
                               (renderer.cpp:2435) and `setD3DViweport` is a
                               bare memcpy. Changing game_w moved the
                               viewport matrix for every draw, which is what
                               stretched the UI.
  2D ortho _11 2/640 -> 2/854  Superseded. The shader now does the scaling,
                               so the ortho goes back to stock and the two
                               mechanisms cannot double up.
  field mode-2 854/427         Content widening. Meaningless until the
  field uncrop 480/240         framing itself is proven, and it is what made
  parallax x5                  the last result hard to read.

KEPT: the four `gfx_drv_init` words (logical width 240 -> 180). Those make
the render target 16:9 and the presentation blit fill the screen. That part
is measured -- the 960px-centred result in the ws-2d build is exactly what
they predict -- and the shader scale is defined against them.

SAFETY
======
Every word is verified against its expected value before anything is
written, the same rule `nso_patcher` applies. A word that is already stock
is reported and skipped, so running this twice is harmless. The file is
written via a temp + rename, and `--dry-run` shows the plan without
touching anything.
"""
import argparse
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _w(hexstr):
    return bytes(int(b, 16) for b in hexstr.split())


# (va, patched_bytes, stock_bytes, description)
#
# Taken verbatim from ff7nx_ws.py's tables, with `set` and `expect` swapped:
# we are putting the ORIGINAL word back.
REVERT = [
    (0x10DA018, 'E8 5C 8F 52', 'A8 99 99 52', '2D ortho _11 -> stock 2/640'),
    (0x10DA01C, '28 63 A7 72', '88 69 A7 72', '2D ortho _11 hi -> stock'),
    (0x10DA038, '08 E8 F7 D2', '08 F0 F7 D2', '2D ortho _41 -> stock -1.0'),

    (0x10D67F4, 'CB 6A 80 52', '2B 55 49 B9', 'game_w -> stock (setviewport)'),
    (0x10D9480, 'CF 6A 80 52', 'AF 55 49 B9', 'game_w -> stock (end_scene)'),
    (0x10D9E60, 'D0 6A 80 52', 'D0 55 49 B9', 'game_w -> stock (per-draw)'),

    (0x09298D4, 'C8 6A 80 52', '08 50 80 52', 'field viewport w -> stock 640'),
    (0x0929938, '78 35 80 52', '18 28 80 52', 'field half-width -> stock 320'),
    (0x09298BC, '08 3C 80 52', 'E8 0B 1A 32', 'field viewport h -> stock 448'),
    (0x0929964, '08 1E 80 52', 'E8 0B 1B 32', 'field half-height -> stock 224'),

    (0x0A07CFC, '09 2D 07 51', '09 81 05 51', 'layer3 left_offset -> stock'),
    (0x0A07DB4, '08 55 03 51', '08 81 02 51', 'layer3 half_width -> stock'),
    (0x0A08B44, '09 2D 07 51', '09 81 05 51', 'layer4 left_offset a -> stock'),
    (0x0A08BFC, '08 55 03 51', '08 81 02 51', 'layer4 half_width -> stock'),
    (0x0A08CDC, '09 2D 07 51', '09 81 05 51', 'layer4 left_offset b -> stock'),
]

# Left alone on purpose. Listed so the log can say so.
KEPT = [
    (0x10D5284, 'logical width divisor 240 -> 180, magic lo'),
    (0x10D5288, 'logical width divisor 240 -> 180, magic hi'),
    (0x10D52A4, 'drop the add-back'),
    (0x10D52AC, 'magic shift 7 -> 0'),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().split('\n')[0])
    ap.add_argument('main', help='the built exefs/main to edit in place')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    try:
        import nso_patcher
        import nxmap
    except ImportError as exc:
        print('! run this from your 7th_heaven_nx folder (%s)' % exc)
        return 2
    from pathlib import Path

    path = Path(args.main)
    if not path.exists():
        print('! no such file: %s' % path)
        return 2

    # nxmap decompresses the NSO so we can read the CURRENT words and report
    # honestly rather than assuming which build produced this file.
    img = nxmap.Main(str(path)).img
    patches, already, unknown = [], [], []
    for va, patched, stock, why in REVERT:
        cur = bytes(img[va:va + 4])
        if cur == _w(patched):
            patches.append({'name': why, 'va': va,
                            'expect': patched, 'set': stock})
        elif cur == _w(stock):
            already.append((va, why))
        else:
            unknown.append((va, why, ' '.join('%02X' % b for b in cur)))

    print('module: %s' % path)
    print('  %d word(s) to revert, %d already stock, %d unrecognised'
          % (len(patches), len(already), len(unknown)))
    for va, why, got in unknown:
        print('  ! +%08X holds %s -- expected either the patched or the '
              'stock word. NOT touching it. (%s)' % (va, got, why))
    if unknown:
        print('  Refusing to write a module I do not recognise. Rebuild with')
        print('  16:9 set to Off, then run this against a fresh ws build.')
        return 1

    for va, why in KEPT:
        cur = ' '.join('%02X' % b for b in img[va:va + 4])
        print('  keeping +%08X  %s  (%s)' % (va, cur, why))

    if not patches:
        print('  nothing to do -- this module is already the baseline.')
        return 0
    if args.dry_run:
        for p in patches:
            print('    would revert +%08X  %s' % (p['va'], p['name']))
        return 0

    nso = nso_patcher.read_nso(path)
    applied = nso_patcher.apply_spec(nso, {
        'name': '16:9 framing -> baseline (gfx_drv_init only)',
        'patches': patches})
    data = nso_patcher.rebuild(nso)

    tmp = str(path) + '.wsfix'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, str(path))
    for line in applied:
        print('  ' + line)
    print()
    print('  done. Now copy BOTH of these to your SD card:')
    print('    exefs/main                          (this file)')
    print('    romfs/shaders/tlmain_vv.glsl        (from ws_test/shaders/)')
    print('    romfs/shaders/lmain_vv.glsl         (from ws_test/shaders/)')
    print('  under /atmosphere/contents/0100A5B00BDC6000/')
    return 0


if __name__ == '__main__':
    sys.exit(main())
