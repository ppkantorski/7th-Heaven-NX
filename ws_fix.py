#!/usr/bin/env python3
"""
ws_fix.py -- the field render target, on an already-built card. No rebuild.

    python3 ws_fix.py status        # what the card holds, and what it does
    python3 ws_fix.py fix           # apply, 1x (428x240)
    python3 ws_fix.py fix --scale 3 # 1280x720, exactly 16:9
    python3 ws_fix.py fix --width 426
    python3 ws_fix.py revert        # back to the build as it was
    python3 ws_fix.py explain       # the mechanism, in 40 lines

The BUILD now ships this: `7th_heaven_nx.py` -> Settings -> Display ->
"Field render resolution". This script is the fast loop -- one command, no
rebuild -- for trying the other scales on a card you already have, and for
`status`, which decodes the module and tells you the band period it will
produce before you boot it.

WHAT IT CHANGES
===============
Eight module words and one number in two shaders.

  1. `ff7nx_fieldbuf` resizes the port's low-resolution field render target
     from 320x240. Four `movz` width immediates -- +0x10D5358 (the
     allocation) and +0x10DF760 / +0x10DF7E0 / +0x10DF804 (the driver's
     render-mode switch) -- and the four heights beside them.

  2. `#define WS_SCALE` in tlmain_vv.glsl and lmain_vv.glsl becomes
     320n/width, so the wider buffer is filled with proportionally more
     world instead of the same world stretched.

Nothing else moves. The tile window (HANDOFF-49), the gfx_drv_init 16:9
words, 60 FPS, the field-background set and the upscale shaders are all left
exactly as the build made them.
"""
import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ws_ab                                                    # noqa: E402
import ff7nx_fieldbuf as fb                                     # noqa: E402


SNAPNAME = 'before_fieldbuf'


# --------------------------------------------------------------------------
def _framing_is_on(main):
    """
    Is the gfx_drv_init 16:9 logical-width patch applied? Resizing the field
    buffer is meaningless without it -- and actively wrong, because a
    428-wide buffer stretched into a 4:3 render target is anamorphic.
    """
    import struct
    import nxmap
    img = nxmap.Main(main).img
    # +0x10D5284 holds the reciprocal magic. 0x8889 == /240 (4:3),
    # 0x16C3 == /180 (16:9). See ff7nx_widescreen.py.
    return struct.unpack_from('<I', img, 0x10D5284)[0] == 0x5282D868


def _tile_window(main):
    """(left, right) currently in the module, or None if undecodable."""
    try:
        import ff7nx_wsclamp
        import nxmap
    except ImportError:
        return None
    try:
        img = nxmap.Main(main).img
        vals = {n: ff7nx_wsclamp.read_value(img, n)
                for n in ('left1', 'left2', 'right1', 'right2')}
    except Exception:                                          # noqa: BLE001
        return None
    if any(v is None for v in vals.values()):
        return None
    return (min(vals['left1'], vals['left2']),
            min(vals['right1'], vals['right2']))


def _want(a):
    """(width, height) from --scale / --width / --size, or None."""
    if a.size:
        w, h = (int(x) for x in a.size.lower().split('x'))
        return w, h
    p = fb.preset(a.scale)
    if p is None:
        return fb.STOCK_WIDTH, fb.STOCK_HEIGHT
    return (a.width or p['width']), p['height']


def _set_shader_scale(path, value):
    """
    Rewrite `#define WS_SCALE` with enough digits to round-trip.

    ws_ab's own writer uses %.6g, which is plenty physically -- 5e-7 of
    scale is 2e-4 of a pixel across the whole buffer -- but it makes
    `status` report a beat period of five million pixels instead of "none".
    """
    text = open(path).read()
    pat = re.compile(r'^([ \t]*#define[ \t]+WS_SCALE[ \t]+)([0-9.]+)',
                     re.MULTILINE)
    m = pat.search(text)
    if not m:
        return None
    old = float(m.group(2))
    new = pat.sub(lambda mm: mm.group(1) + ('%.8f' % value), text, count=1)
    tmp = path + '.tmp'
    open(tmp, 'w').write(new)
    os.replace(tmp, path)
    return old


# --------------------------------------------------------------------------
def cmd_status(sdout, _a):
    main, _ = ws_ab.paths(sdout)
    print('== card state ==')
    print()
    ws_ab.status(sdout)
    print()
    size = fb.read_size(main)
    bad = fb.verify_sites(main)
    print('  field buffer        : %s'
          % ('%d x %d%s' % (size[0], size[1],
                            '   << STOCK -- THE BANDS LIVE HERE'
                            if size == (fb.STOCK_WIDTH, fb.STOCK_HEIGHT)
                            else '   (resized)')
             if size else 'undecodable'))
    if bad:
        for b in bad:
            print('      ! ' + b)
        return 2
    print('  16:9 logical width  : %s'
          % ('ON' if _framing_is_on(main) else 'off (4:3 render target)'))

    shdir = ws_ab.live_shader_dir(sdout) or ws_ab.shader_dirs(sdout)[0]
    scales = {}
    n = max(1, size[1] // fb.STOCK_HEIGHT) if size else 1
    for s in ws_ab.SHADERS:
        sc = ws_ab.shader_scale(os.path.join(shdir, s))
        scales[s] = sc
        want = fb.ws_scale(size[0], n) if size else None
        note = ''
        if sc is not None and want is not None:
            note = ('   <- matches the buffer' if abs(sc - want) < 5e-5
                    else '   ** should be %.8f for a %d px buffer **'
                         % (want, size[0]))
        print('  %-20s: %s%s'
              % (s, 'WS_SCALE %.8f' % sc if sc is not None else 'not present',
                 note))

    sc = scales.get('tlmain_vv.glsl')
    if size and sc is not None:
        print()
        print('  what this combination does to the field background:')
        for line in fb.diagnose(size[0], sc):
            print('    ' + line)

    if size:
        need = fb.tile_window_minima(size[0], n)
        tw = _tile_window(main)
        print()
        print('  tile window needs   : left >= %d, right >= %d'
              % (need['left'], need['right']))
        if tw:
            print('  tile window has     : left = %d, right = %d   (%s)'
                  % (tw[0], tw[1],
                     'covers' if tw[0] >= need['left'] and tw[1] >= need['right']
                     else '** SHORT -- run ff7nx_wsclamp --wide **'))
    return 0


def cmd_fix(sdout, a):
    main, _ = ws_ab.paths(sdout)
    width, height = _want(a)
    n = max(1, height // fb.STOCK_HEIGHT)
    print('== field render target -> %d x %d ==' % (width, height))
    print()

    if not _framing_is_on(main):
        print('! the gfx_drv_init 16:9 logical-width patch is NOT on this')
        print('  module, so the render target is still 4:3. Resizing the')
        print('  field buffer on top of that would give you an anamorphic')
        print('  picture, not widescreen.')
        print()
        print('  Run `python3 ws_ab.py on` first, then this.')
        return 2

    bad = fb.verify_sites(main)
    if bad:
        print('! refusing to patch -- the sites do not match this module:')
        for b in bad:
            print('    ' + b)
        return 2

    os.makedirs(ws_ab.SNAPDIR, exist_ok=True)
    if not os.path.isdir(os.path.join(ws_ab.SNAPDIR, 'original')):
        ws_ab.snapshot(sdout, 'original')
    if not os.path.isdir(os.path.join(ws_ab.SNAPDIR, SNAPNAME)):
        ws_ab.snapshot(sdout, SNAPNAME)

    print('  module:')
    rc = fb.apply(main, width, height, log=lambda s: print('  ' + s))
    if rc:
        return rc

    scale = fb.ws_scale(width, n)
    print()
    print('  shaders:')
    shdir = ws_ab.ensure_shaders(sdout, log=lambda s: print('  ' + s))
    for s in ws_ab.SHADERS:
        p = os.path.join(shdir, s)
        old = ws_ab.shader_scale(p)
        if old is None:
            print('    ! %s has no `#define WS_SCALE` -- that is not the '
                  'widescreen copy. Reinstall it from '
                  'custom_shaders/wide_screen and run this again.' % s)
            return 2
        _set_shader_scale(p, scale)
        print('    %-18s WS_SCALE %.8f -> %.8f' % (s, old, scale))

    need = fb.tile_window_minima(width, n)
    tw = _tile_window(main)
    print()
    print('  tile window: needs left >= %d, right >= %d%s'
          % (need['left'], need['right'],
             ';  module has left = %d, right = %d -- %s'
             % (tw[0], tw[1],
                'covers, nothing to do' if tw[0] >= need['left']
                and tw[1] >= need['right'] else 'SHORT, run ff7nx_wsclamp')
             if tw else ' (could not decode -- check ff7nx_wsclamp --show)'))

    print()
    for line in fb.describe(width, height, scale):
        print('  ' + line)
    print()
    print('  Copy sdout/ to the SD card and reboot.')
    print()
    print('  `python3 ws_fix.py revert` puts the card back byte for byte.')
    print()
    saved = _saved_field_buffer()
    if saved is not None and saved != n:
        print('  ! settings.json still says Field render resolution = %d.'
              % saved)
        print('    THE NEXT BUILD WILL PUT IT BACK. This tool edits the')
        print('    module that is already in sdout/; it does not change the')
        print('    setting the build reads. If you want %dx to survive a'
              % n)
        print('    rebuild, set it in Settings -> Display as well.')
    return 0


def _saved_field_buffer():
    """Field render resolution in settings.json, or None."""
    import json
    for cand in ('settings.json', os.path.join('..', 'settings.json')):
        path = os.path.join(_HERE, cand)
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                return int(json.load(f)['__global__']['field_buffer'])
        except Exception:                                      # noqa: BLE001
            return None
    return None


def cmd_revert(sdout, _a):
    print('== back to the build as it was ==')
    for name in (SNAPNAME, 'original'):
        if os.path.isdir(os.path.join(ws_ab.SNAPDIR, name)):
            return ws_ab.restore(sdout, name)
    print('! no snapshot to restore from -- nothing has been changed yet.')
    return 2


EXPLAIN = """
THE BANDS -- MECHANISM
======================
1.  The port renders the field into a hardcoded 320x240 offscreen buffer,
    not into the screen-sized render target. Proof:
      * gfx_drv_init +0x10D5358 builds a `320 | 240<<32` size struct and
        creates EIGHT render targets from it.
      * the driver's render-mode switch +0x10DF6E0 writes 320 and 240 into
        the current-target-size globals [[0x12CE578]] / [[0x12CE580]] for
        modes 0, 2 and 3, and restores the real target size for 1 and 4.
      * gfx_drv_setviewport +0x10D6760 maps game space onto that size as
        px = target_w * x / 640. target_w = 320 => 640 game units land on
        320 pixels.

2.  320 px is not arbitrary. A field background tile is 16x16 source texels
    and covers 32x32 game units, so at 320 px it lands EXACTLY 1:1 -- one
    texel, one pixel. The 2xSaI / HQ4x "background scaler" then upscales
    that buffer to the screen. That is why those shaders exist at all.

3.  Widescreen broke the 1:1 and nothing else did. WS_SCALE 0.75 packs
    853.33 game units into the same 320 pixels, so a 16-texel tile is
    rasterised into 12 pixels. 16-into-12 is a minification whose sampling
    phase repeats every 3 buffer pixels. At 720p the buffer is blown up 4x
    (320 -> 1280), so 3 buffer px become 12 SCREEN px.

4.  That is the measured fundamental, with the measured harmonic at 6, and
    it is locked to the buffer's pixel grid -- which is locked to the
    screen. Hence "glued to the screen", hence the shimmer as the art slides
    through a stationary sampling grid.

EVERY HANDOFF-50 MEASUREMENT AGREES
===================================
  12 px fundamental, 6 px harmonic .... 3 buffer px x 4
  4:3 clean (0.021 vs 0.307) .......... 640 units -> 320 px = 1:1
  stretched (state 3) clean ........... WS_SCALE 1.0 -> 1:1
  ortho 2/854 (state 4) bands ......... same squeeze, different route
  WS_SCALE 0.6667: 12 and 6 VANISH,
      an 8 px component APPEARS ....... 960 units -> 320 px; 16 texels into
                                        10.67 px repeats every 2 buffer px
                                        = 8 screen px. Nothing else predicts
                                        that number.
  supersample barely moves 12 px ...... supersample resizes the RENDER
                                        TARGET, not the 320x240 buffer
  character models unaffected ......... models are geometry, not a texel grid
  24 px top/bottom letterbox .......... viewport (0,16,640,448) -> buffer
                                        rows 8..232 of 240; x3 = 24 px

THE FIX, AND WHY IT IS A LADDER
===============================
With buffer W x H and shader scale S the field gets W*S/640 buffer pixels
per game unit across and H/480 down. Whole pixels per texel -- which is what
"no beat" means -- needs both to be n/2, so

    H = 240n,   S = 320n / W,   visible span = 2W/n units

and W alone picks the aspect ratio. W must be even, or game x = 0 lands on a
half pixel and every tile edge is blended across two of them.

    n   W      H     S            span     aspect vs 16:9
    1   428    240   0.74766355   856      +0.31%   <- confirmed on hardware
    2   854    480   0.74941452   854      +0.08%
    3   1280   720   0.75         853.33   EXACT

n=3 is the arithmetically perfect one: exactly 16:9, S stays exactly 0.75,
and at 720p handheld the field is rendered at native screen resolution. It
costs 9x the field fill rate and it magnifies the pre-rendered background
with the hardware sampler before the 2xSaI/HQ4x kernel sees it, so it is a
different LOOK, not simply a better one. Try `crisp` with it.
"""


COMMANDS = {'status': cmd_status, 'fix': cmd_fix, 'revert': cmd_revert}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=sorted(list(COMMANDS) + ['explain']))
    ap.add_argument('--scale', type=int, default=fb.DEFAULT_SCALE,
                    help='1 = 428x240 (default), 2 = 854x480, '
                         '3 = 1280x720, 0 = back to stock 320x240')
    ap.add_argument('--width', type=int,
                    help='override the width for this scale (must be even)')
    ap.add_argument('--size', metavar='WxH', help='an explicit buffer size')
    ap.add_argument('--sdout', help='path to your sdout/ (auto-detected)')
    ap.add_argument('--shader-dir', help='where the card reads .glsl from')
    a = ap.parse_args(argv)

    if a.command == 'explain':
        print(EXPLAIN)
        return 0
    ws_ab.SHADER_DIR_OVERRIDE = a.shader_dir
    sdout = ws_ab.find_sdout(a.sdout)
    if not sdout:
        print('! could not find sdout/. Run this from your 7th_heaven_nx '
              'folder, or pass --sdout <path>.')
        return 2
    main_path, _ = ws_ab.paths(sdout)
    if not os.path.exists(main_path):
        print('! %s does not exist -- build first.' % main_path)
        return 2
    return COMMANDS[a.command](sdout, a)


if __name__ == '__main__':
    raise SystemExit(main())
