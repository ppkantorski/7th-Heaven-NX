#!/usr/bin/env python3
"""
ifrit_verify.py -- model Ifrit's heat warp end to end and prove what it shows.

Same method as tools/escape_verify.py, and for the same reason: builds 389..399
each verified a patch against the model that produced it. This computes, for
every band the effect draws, WHICH PART OF THE FRAME lands WHERE on screen --
and asks whether that map is the identity.

EVERYTHING BELOW IS READ OUT OF ff7_en_switch, NOT INFERRED
==========================================================
The three captures, x86 0x595DE7, mode-2 arm at 0x595EA6:

    cap1 = (x=0,   y=0,   w=0x100=256, h=0xA6=166)
    cap2 = (x=512, y=0,   w=0x41 = 65, h=0x58= 88)
    cap3 = (x=512, y=176, w=0x41 = 65, h=0x58= 88)
    xscale = yscale = [0x9AD1A8] = 2      (+0x10C and +0x110, two separate
                                           loads of the same global)

The build pass, x86 0x595A05:

    wave 1   21 bands   u 1..254   v = i*8 .. i*8+8 (last band clamps to 166)
                        y = v * scale + rect.y
    wave 2   11 bands   u 1..64    v = i*8 .. i*8+8   (band 0's v_top is 1)
                        y = v * scale + rect.y
    wave 3   10 bands   u 1..64    v = i*8 .. i*8+8 (last clamps to 0x4E=78)
                        y = (v + 0x58) * scale + rect.y      <-- the offset

The draw pass, x86 0x596186 / 0x596351 / 0x59651F, one band per iteration:

    x0 = (sine(...) + 0x200) >> 10        x1 = x0 + 0xFF   (wave 1)
    x2 = (sine(...) + 0x200) >> 10        x3 = x2 + 0xFF
    then all four:  x = x_raw * scale + rect.x

`sine` is the shimmer and averages zero, so the bands sit at raw x 0..255
(waves 2/3 at 255..320) plus a few units of wobble.

WHICH PASS RUNS WHEN -- THE THING BUILD 411a MISSED
===================================================
The function has a frame-0 gate at the top:

    x86 0x595A20  movsx edx, word [state+2]    the effect's frame counter
    x86 0x595A26  jne 0x5960FA                 -> SKIP the build pass
    ARM +0x70EAA8 cbz w8, #0x70EBB8            the same gate, inverted

So the 42 records, their UVs, **their y coordinates** and the three captures
are built ONCE, on frame 0. Every later frame resets the display-list pointer
and runs the three draw loops, which rewrite **x alone**.

A transform applied in the draw-pass cave is therefore idempotent for x and
COMPOUNDING for y. Build 411a scaled y there and the recording shows exactly
that: the picture magnifies further every frame for about eleven frames, then
the coordinates overflow their `strh` and the screen becomes vertical stripes
of single texels. `check_idempotence` below is the assertion that was missing,
and it is now part of acceptance.

THE MAPPING LAW, ESTABLISHED FOR ESCAPE AND THE SAME HERE
=========================================================
`0x682DB2` rounds the capture's w and h up to powers of two before the thunk
sees them, and the thunk builds `fb_tex.w = xscale * tex_w`. Only
`tex_format.width` is read as a UV divisor, so

    staging column = fb_tex.x + u * xscale          (tex_w cancels exactly)
    staging row    = fb_tex.y + v * yscale

and the staging surface holds the WHOLE 16:9 frame in 640 columns. So:

    cap1  u 1..254   -> staging   2..508      = game -104 .. 570
    cap2  u 1..64    -> staging 514..640      = game  578 .. 747
    cap3  u 1..64    -> staging 514..640      = game  578 .. 747

**The three captures already cover the entire 16:9 frame.** The module note in
ff7nx_battlewide says the capture is "0x100 + 0x41 = 321 texels = 642 game
units wide and CANNOT be widened usefully" -- that converted staging columns
into game units one-for-one, which is exactly the anamorphic error
ff7nx_fbcapture warns about ("640 staging columns are NOT 640 game units").
It is why FFNx's source-side widening was ported when the fix belongs at the
destination, the same way ff7nx_swirlscale found for the swirl.

RUN
===
    python3 tools/ifrit_verify.py
    python3 tools/ifrit_verify.py --png /tmp/ifrit.png       (needs Pillow)

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import escape_verify as EV                                      # noqa: E402
import ff7nx_escapescale as ES                                  # noqa: E402
import ff7nx_escapetall as ET                                   # noqa: E402

STAGING_W, STAGING_H = EV.STAGING_W, EV.STAGING_H
FRAME_H = 480
game_x = EV.game_x_of_staging
pot = EV.pot

SCALE = 2                       # [0x9AD1A8], the raw -> final multiplier
RECT = (0, 0)                   # [0x9AAD4C], [0x9AAD50]

# (x, y, w, h) -- x86 0x595EA6, the mode-2 arm
CAPS = {'1': (0, 0, 0x100, 0xA6),
        '2': (512, 0, 0x41, 0x58),
        '3': (512, 176, 0x41, 0x58)}
CAP3_Y_NEW = 264                # 0x58 * 3, paired with yscale 3

# Part 3, the resolution: raise the AUTHORED w/h so tex_format -- which is the
# GPU target's size under ff7nx_gpucap, and the UV divisor -- gets bigger. The
# mapping is `fb_tex.x + u * xscale`, in which tex_w cancels exactly, so this
# cannot move the picture. It is ff7nx_fbsize's "more texels, same UVs", per
# effect. Only wave 1 needs it: it is 79% of the screen at 256 texels.
CAP1_WH_NEW = (0x200, 0x14C)    # tex_format 256x256 -> 512x512

# (cap, bands, u_lo, u_hi, raw_x0, raw_w, v_clamp, v_offset)
WAVES = (
    ('1', 21, 1, 254, 0,     0xFF, 0xA6, 0),
    ('2', 11, 1, 0x40, 0xFF, 0x41, None, 0),
    ('3', 10, 1, 0x40, 0xFF, 0x41, 0x4E, 0x58),
)


def fb(cap, xscale, yscale, y_override=None):
    x, y, w, h = cap
    if y_override is not None:
        y = y_override
    return {'x': x, 'y': y, 'tex_w': pot(w), 'tex_h': pot(h),
            'w': xscale * pot(w), 'h': yscale * pot(h),
            'xscale': xscale, 'yscale': yscale}


def bands(tall, sharp=False):
    """Every band: its screen rect and the staging rect it samples."""
    yscale = 3 if tall else SCALE
    out = []
    for name, n, u_lo, u_hi, raw_x0, raw_w, v_clamp, v_off in WAVES:
        cap = CAPS[name]
        if sharp and name == '1':
            cap = (cap[0], cap[1]) + CAP1_WH_NEW
        y_over = CAP3_Y_NEW if (tall and name == '3') else None
        t = fb(cap, SCALE, yscale, y_over)
        for i in range(n):
            v_top = i * 8
            if name in ('2', '3') and i == 0:
                v_top = 1                      # x86 0x595BE5 / 0x595D2F
            v_bot = i * 8 + 8
            if v_clamp is not None and i >= n - 1:
                v_bot = v_clamp
            # geometry, with the sine at its mean of zero
            gx0 = raw_x0 * SCALE + RECT[0]
            gx1 = (raw_x0 + raw_w) * SCALE + RECT[0]
            gy0 = (v_top + v_off) * SCALE + RECT[1]
            gy1 = (v_bot + v_off) * SCALE + RECT[1]
            out.append({
                'wave': name, 'band': i,
                'screen': [ES.expected(gx0), ES.expected(gx1),
                           ET.expected(gy0) if tall else gy0,
                           ET.expected(gy1) if tall else gy1],
                'stock_screen': [gx0, gx1, gy0, gy1],
                'source': [game_x(t['x'] + u_lo * t['xscale']),
                           game_x(t['x'] + u_hi * t['xscale']),
                           t['y'] + v_top * t['yscale'],
                           t['y'] + v_bot * t['yscale']],
                'tex': t,
            })
    return out


# Which vertex fields each pass writes, from the x86. The cave may only ever
# transform a field the DRAW pass rebuilds; anything else compounds.
DRAW_PASS_WRITES = (0x08, 0x10, 0x18, 0x20)          # x0..x3, from the sine
BUILD_PASS_WRITES = (0x0A, 0x12, 0x1A, 0x22)         # y0..y3, frame 0 only


def check_idempotence(cave_offsets, frames=30):
    """Run the cave's transform `frames` times, as the hardware would.

    For a field the draw pass rebuilds, the value is fresh each frame and the
    result is the same every time. For a field written once, the transform
    compounds -- and `strh` truncates it, which is what turned the screen into
    stripes.
    """
    out = {}
    for off in cave_offsets:
        rebuilt = off in DRAW_PASS_WRITES
        y = 332
        seen = []
        for _ in range(frames):
            if rebuilt:
                y = 332                      # the draw pass writes it afresh
            y = ET.expected(y) if off in BUILD_PASS_WRITES else ES.expected(y)
            y = ((y + 0x8000) & 0xFFFF) - 0x8000      # the strh
            seen.append(y)
        out[off] = (len(set(seen)) == 1, seen[0], seen[-1])
    return out


def report(label, widened, tall, sharp=False):
    print('\n' + '=' * 74)
    print(label)
    print('=' * 74)
    bs = bands(tall, sharp)
    worst_x = worst_y = 0.0
    sx0, sx1 = 1e9, -1e9
    sy0, sy1 = 1e9, -1e9
    for b in bs:
        scr = b['screen'] if widened else b['stock_screen']
        if not tall:
            scr = list(scr)
        src = b['source']
        worst_x = max(worst_x, abs(scr[0] - src[0]), abs(scr[1] - src[1]))
        worst_y = max(worst_y, abs(scr[2] - src[2]), abs(scr[3] - src[3]))
        sx0, sx1 = min(sx0, scr[0]), max(sx1, scr[1])
        sy0, sy1 = min(sy0, scr[2]), max(sy1, scr[3])
    for name in ('1', '2', '3'):
        b = [x for x in bs if x['wave'] == name]
        t = b[0]['tex']
        scr0 = (b[0]['screen'] if widened else b[0]['stock_screen'])
        scrN = (b[-1]['screen'] if widened else b[-1]['stock_screen'])
        print('  wave %s  capture fb_tex=(x=%d y=%d w=%d h=%d) '
              'tex_format=%dx%d  yscale=%d'
              % (name, t['x'], t['y'], t['w'], t['h'],
                 t['tex_w'], t['tex_h'], t['yscale']))
        print('           %2d bands drawn at screen x %.1f..%.1f, y %d..%d'
              % (len(b), scr0[0], scr0[1], scr0[2], scrN[3]))
        print('           sampling            game x %.1f..%.1f, rows %d..%d'
              % (b[0]['source'][0], b[0]['source'][1],
                 b[0]['source'][2], b[-1]['source'][3]))
        # texels available vs screen pixels covered, with gpucap on
        span_cols = (b[0]['source'][1] - b[0]['source'][0]) / EV.WIDE_W_EXACT
        px = span_cols * 1280
        rows = b[-1]['source'][3] - b[0]['source'][2]
        pxv = rows * 720 / FRAME_H
        print('           %dx%d target texels for %.0fx%.0f screen px  -> '
              '%.2f x %.2f px/texel'
              % (t['tex_w'], t['tex_h'], px, pxv,
                 px / t['tex_w'], pxv / t['tex_h']))
    print('  drawn region   screen x %.1f..%.1f   y %d..%d'
          % (sx0, sx1, sy0, sy1))
    print('  worst deviation from 1:1   x %.1f game units   y %d rows'
          % (worst_x, worst_y))
    return worst_x, worst_y, (sx0, sx1, sy0, sy1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('--png')
    args = ap.parse_args(argv)

    wx_s, wy_s, cov_s = report(
        'STOCK  (no widening, no height)', widened=False, tall=False)
    wx_w, wy_w, cov_w = report(
        'BUILD 411 part 1  (x\' = (4x-320)/3 on the four vertex x shorts)',
        widened=True, tall=False)
    wx_t, wy_t, cov_t = report(
        'BUILD 411 part 2  (+ y\' = 3y/2, yscale 3, cap3.y 176 -> 264)',
        widened=True, tall=True)
    wx_r, wy_r, cov_r = report(
        'BUILD 411 part 3  (+ wave 1 authored w/h 0x100/0xA6 -> 0x200/0x14C)',
        widened=True, tall=True, sharp=True)

    print('\n' + '=' * 74)
    print('ACCEPTANCE')
    print('=' * 74)
    ok = True

    def check(name, cond, detail=''):
        nonlocal ok
        print('  [%s] %s%s' % ('PASS' if cond else 'FAIL', name,
                               '  -- ' + detail if detail else ''))
        ok = ok and cond

    frame_lo = EV.WIDE_X + 1 / 3
    frame_hi = frame_lo + EV.WIDE_W_EXACT

    check('stock reproduces the reported squeeze into the 4:3 region',
          abs(cov_s[0] - 0) < 2 and abs(cov_s[1] - 640) < 2,
          'drawn across screen x %.0f..%.0f while sampling game %.0f..%.0f'
          % (cov_s[0], cov_s[1], frame_lo, frame_hi))
    check('stock is off by the full widescreen margin', wx_s > 100,
          'worst %.0f game units' % wx_s)
    # The junction between cap1 and cap2 is the only place part 1 is not
    # exact, and the size of it is fixed by the effect's own UV literals:
    #   cap1 samples to staging  0 + 254*2 = 508   (u stops at 254, not 256)
    #   cap2 samples from      512 +   1*2 = 514   (u starts at 1, not 0)
    # while the bands meet at staging 510. So the crack is 6 staging columns,
    # split 2 on cap1's side and 4 on cap2's. Exactly the PSX inclusive-texel
    # convention ff7nx_swirlseam fixes for the swirl, at one edge instead of
    # nine. Derived here, not fitted.
    per_col = EV.WIDE_W_EXACT / STAGING_W
    j = 255 * SCALE                                    # the band junction
    left = (j - (CAPS['1'][0] + 254 * SCALE)) * per_col
    right = ((CAPS['2'][0] + 1 * SCALE) - j) * per_col
    check('part 1 leaves only the effect\'s own one-texel inset',
          abs(wx_w - max(left, right)) < 0.5,
          'worst %.1f game units; the crack at the wave1/wave2 junction is '
          '%.1f + %.1f = %.1f units wide, from u stopping at 254 and '
          'restarting at 1' % (wx_w, left, right, left + right))
    check('part 1 spans the whole 16:9 width',
          cov_w[0] <= frame_lo + 2 and cov_w[1] >= frame_hi - 2,
          'covers %.1f..%.1f' % (cov_w[0], cov_w[1]))
    check('stock and part 1 are already 1:1 vertically',
          wy_s == 0 and wy_w == 0)
    check('stock stops at the battle viewport height', cov_s[3] == 332,
          'y 0..%d' % cov_s[3])
    check('part 2 reaches the bottom of the frame', cov_t[3] >= FRAME_H,
          'y 0..%d, clipped at %d' % (cov_t[3], FRAME_H))
    check('part 2 stays 1:1 vertically', wy_t == 0,
          'worst %d rows' % wy_t)
    check('part 2 leaves the horizontal alone', abs(wx_t - wx_w) < 0.01)
    g_stock, g_tall = _seam(tall=False), _seam(tall=True)
    check('wave 3 meets wave 2 as closely as it does in stock',
          g_tall == g_stock * 3 // 2,
          'stock leaves %d rows from the v=1 inset; yscale 3 scales that to '
          '%d. Not a new seam -- the same one, 1.5x' % (g_stock, g_tall))
    check('wave 3 would be 88 rows out WITHOUT the cap3.y move',
          _seam(tall=True, move_cap3=False) <= -80,
          'gap %d rows -- this is what build 397 reported as a seam'
          % _seam(tall=True, move_cap3=False))
    check('part 3 does not move the picture at all',
          abs(wx_r - wx_w) < 0.01 and wy_r == wy_t and cov_r == cov_t,
          'tex_w cancels in `x + u*xscale`, so it cannot')
    check('part 3 brings wave 1 to about one screen pixel per texel',
          _pxt(sharp=True) < 2.1 < _pxt(sharp=False),
          '%.2f px/texel -> %.2f' % (_pxt(False), _pxt(True)))

    print()
    import ff7nx_battlewide as BW
    cave_offs = [off for off, _fn in BW._ifrit_ops()]
    idem = check_idempotence(cave_offs)
    check('the cave touches only fields the draw pass rebuilds',
          all(off in DRAW_PASS_WRITES for off in cave_offs),
          'cave offsets %s' % [hex(o) for o in cave_offs])
    check('so every field it touches is stable over 30 frames',
          all(v[0] for v in idem.values()),
          ', '.join('+0x%X %s' % (o, 'stable' if v[0] else
                                  'frame1=%d frame30=%d' % (v[1], v[2]))
                    for o, v in sorted(idem.items())))
    legacy = check_idempotence([off for off, _fn in BW._ifrit_ops(True)])
    y_bad = [o for o in BUILD_PASS_WRITES if o in legacy and not legacy[o][0]]
    check('and build 411a\'s version would have been caught by that',
          len(y_bad) == 4,
          'y compounded to %d by frame 30, wrapped through the strh'
          % legacy[0x0A][2])

    if args.png:
        render(args.png)
    print('\n%s' % ('ALL CHECKS PASSED' if ok else 'CHECKS FAILED'))
    return 0 if ok else 1


def _pxt(sharp):
    b = [x for x in bands(True, sharp) if x['wave'] == '1'][0]
    px = (b['source'][1] - b['source'][0]) / EV.WIDE_W_EXACT * 1280
    return px / b['tex']['tex_w']


def _seam(tall, move_cap3=True):
    """Rows between wave 2's last band and wave 3's first, on screen."""
    global CAP3_Y_NEW
    saved = CAP3_Y_NEW
    if not move_cap3:
        CAP3_Y_NEW = CAPS['3'][1]
    try:
        bs = bands(tall)
        w2 = [b for b in bs if b['wave'] == '2']
        w3 = [b for b in bs if b['wave'] == '3']
        # the screen row where wave 2 ends and where wave 3 starts
        end2 = (w2[-1]['screen'] if tall else w2[-1]['stock_screen'])[3]
        start3 = (w3[0]['screen'] if tall else w3[0]['stock_screen'])[2]
        # and whether wave 3 samples the row it is drawn on
        off = w3[0]['source'][2] - start3
        return int(start3 - end2) + int(off)
    finally:
        CAP3_Y_NEW = saved


def render(path):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print('\n(Pillow not installed -- skipping the PNG)')
        return
    W, H = 1280, 720
    src = EV.render.__globals__  # reuse escape_verify's landmarked frame
    # build the same source frame escape_verify uses
    im = Image.new('RGB', (W, H), (20, 24, 34))
    d = ImageDraw.Draw(im)
    for i in range(0, W, 80):
        g = game_x(i * STAGING_W / W)
        d.line([(i, 0), (i, H)], fill=(60, 66, 80))
        d.text((i + 3, 4), '%d' % g, fill=(150, 160, 180))
    d.rectangle([40, 120, 200, 380], fill=(200, 40, 40))
    d.rectangle([W - 200, 120, W - 40, 380], fill=(40, 90, 210))
    d.rectangle([W // 2 - 14, 60, W // 2 + 14, H - 60], fill=(230, 210, 40))
    ui = int(H * 336 / 480)
    d.rectangle([0, ui, W, H], fill=(34, 26, 46))
    for i in range(0, W, 80):
        d.line([(i, ui), (i, H)], fill=(80, 60, 100))
    d.rectangle([160, ui + 30, 520, H - 30], fill=(190, 60, 170))
    d.rectangle([W - 520, ui + 30, W - 160, H - 30], fill=(60, 180, 140))
    d.text((8, ui + 6), 'UI BAND (game y 336..480)', fill=(230, 220, 240))
    sp = im.load()

    def sample(gx, row):
        px = int(round((gx - (EV.WIDE_X + 1 / 3)) * W / EV.WIDE_W_EXACT))
        py = int(round(row * H / STAGING_H))
        if not (0 <= px < W and 0 <= py < H):
            return (0, 0, 0)
        return sp[px, py]

    panels = [('SOURCE FRAME', im)]
    for label, widened, tall in (
            ('stock: squeezed into the central 4:3', False, False),
            ('411 part 1: widened', True, False),
            ('411 part 2: + full height', True, True)):
        out = Image.new('RGB', (W, H), (0, 0, 0))
        op = out.load()
        for b in bands(tall):
            scr = b['screen'] if widened else b['stock_screen']
            src_r = b['source']
            x0 = int(round((scr[0] - (EV.WIDE_X + 1 / 3)) * W
                           / EV.WIDE_W_EXACT))
            x1 = int(round((scr[1] - (EV.WIDE_X + 1 / 3)) * W
                           / EV.WIDE_W_EXACT))
            y0 = int(round(scr[2] * H / FRAME_H))
            y1 = int(round(scr[3] * H / FRAME_H))
            if x1 <= x0 or y1 <= y0:
                continue
            for px in range(max(0, x0), min(W, x1)):
                fx = (px - x0) / (x1 - x0)
                gx = src_r[0] + fx * (src_r[1] - src_r[0])
                for py in range(max(0, y0), min(H, y1)):
                    fy = (py - y0) / (y1 - y0)
                    row = src_r[2] + fy * (src_r[3] - src_r[2])
                    op[px, py] = sample(gx, row)
        panels.append((label, out))

    strip = Image.new('RGB', (W, H * len(panels)), (0, 0, 0))
    dd = ImageDraw.Draw(strip)
    for i, (label, p) in enumerate(panels):
        strip.paste(p, (0, H * i))
        dd.text((8, H * i + 8), label, fill=(255, 255, 255))
    strip.save(path)
    print('\nwrote %s' % path)


if __name__ == '__main__':
    raise SystemExit(main())
