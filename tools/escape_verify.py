#!/usr/bin/env python3
"""
escape_verify.py -- model Escape end to end and prove what each build shows.

WHY THIS EXISTS
===============
Every wrong Escape build in 402..408 was verified against the same model that
produced it. This script verifies the other direction: it computes, for every
screen pixel the mesh covers, WHICH GAME-X OF THE FRAME that pixel ends up
displaying -- and then asks whether that map is the identity.

The map is exact, so this is arithmetic rather than image comparison. An
identity map means "a snapshot of the whole 16:9 screen, shown 1:1". A map
that takes two screen positions to the same source x is the doubling, and the
script reports the duplicated span in game units.

THE CHAIN IT MODELS, WITH THE SITE FOR EACH STEP
================================================
    escape_setup       x86 0x5D577B  mode 2: A=(0,0,160,168) B=(320,0,160,168)
                                     scale 2 (both xscale and yscale)
    0x682DB2           +0xB53F34/+0xB54014 round w,h up to powers of two
    thunk              +0x10F2380    w = xscale*tex_w, h = yscale*tex_h
    make_framebuffer_tex +0x10DBB50  fb_tex=(x,y,w,h) tex_format=(tex_w,tex_h)
    ff7nx_fbcapture    +0x10F23B4    origin correction, gated on xscale == 1
    loader             +0x10D70B0    field_0 == 1 -> CPU readback, else GPU
      ff7nx_fbwindow   +0x10D7138    CPU only: slide the rect back on-surface
      GPU quad         +0x10D7478    u0=x/surf_w  u1=(x+w)/surf_w
    ff7nx_gpucap       +0x10D7264    GPU target = tex_format, not fb_tex
    escape_uv_init     x86 0x5D59B0  u = col*8 (A) / col*8-160 (B), v = row*8
    escape_mesh        x86 0x5D602A  final = raw*2 + rect, 41 x 22 grid
    2D overlay         FINDINGS-85   ortho(0,640,480,0) then x *= WS_SCALE

The overlay step collapses: game_x = 320 + 0.75*(x/320 - 1)*426.667 = x. So
one overlay unit is one game unit, and a 0..640 mesh covers game 0..640 --
the central 4:3 with 106.67 game units of black down each side, which is the
reported symptom. ff7nx_escapescale's (4x - 320)/3 is read out of the module
itself rather than retyped here.

RUN
===
    python3 tools/escape_verify.py
    python3 tools/escape_verify.py --png /tmp/escape.png      (needs Pillow)

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

import ff7nx_escapescale as ES                                  # noqa: E402
import ff7nx_escapetall as ET                                   # noqa: E402
import ff7nx_ws as WS                                           # noqa: E402

STAGING_W, STAGING_H = 640, 480
WIDE_X = WS.WIDE_VIEWPORT_X                  # -107
WIDE_W = WS.WIDE_VIEWPORT_WIDTH              # 854
WIDE_W_EXACT = 640 * 4 / 3                   # 853.333..., what the blit uses

# escape_setup, mode 2 (x86 0x5D5815..0x5D5845)
RECT_A = (0, 0, 160, 168)
RECT_B = (320, 0, 160, 168)
XSCALE = 2                                   # ebp-0x04, unchanged
YSCALE_STOCK = 2                             # obj+0x110, stock = the same dword
RIPPLE_C_STOCK = 120                         # raw row*8, x86 0x5D59FB / 0x5D60C3

# escape_mesh
COLS, ROWS = 41, 22                          # grid points
RAW_STEP = 8
MESH_SCALE = 2                               # [0x9AD1A8]
RECT_ORIGIN = (0, 0)                         # [0x9AAD4C], [0x9AAD50]
SPLIT_QUAD = 20                              # quads 0..19 -> A, 20..39 -> B

# escape_uv_init, x86 0x5D59B0, read instruction by instruction:
#   col <  20   u_left  = col*8            0x5D5C64  shl ecx, 3
#               u_right = col*8 + 8        0x5D5CC4  lea ecx, [eax*8 + 8]
#               col == 19 overrides u_right                     0x5D5D25 cmp 19
#                       = col*8 + 7        0x5D5D2E  lea ecx, [eax*8 + 7]
#   col >= 20   u_left  = col*8 - 160      0x5D5D97  lea ecx, [eax*8 - 0xa0]
#               u_right = col*8 - 152      0x5D5DFB  lea ecx, [eax*8 - 0x98]
#               col == 20 overrides u_left                      0x5D5E5C cmp 20
#                       = col*8 - 159      0x5D5E65  lea ecx, [eax*8 - 0x9f]
#   v_top = row*8, v_bottom = row*8 + 8, no special case
# The two overrides are the PSX inclusive-texel convention -- on hardware
# [u0, u0+31] covers 32 texels because the endpoint is included, so the last
# column of a page pulls in by one. This renderer maps u / tex_format.width,
# so the pull-in is a real 1-texel loss instead. ff7nx_swirlseam is the same
# defect in the battle-entry swirl. It survives the fix as a 4-staging-column
# pinch at dead centre, and it is the only place the fix is not exactly 1:1.
SEAM_QUADS = (19, 20)


def u_left(col):
    if col < SPLIT_QUAD:
        return col * 8
    return col * 8 - (159 if col == SPLIT_QUAD else 160)


def u_right(col):
    if col < SPLIT_QUAD:
        return col * 8 + (7 if col == SPLIT_QUAD - 1 else 8)
    return col * 8 - 152


def pot(n):
    """x86 0x690240 / 0x690270: round up to a power of two."""
    p = 1
    while p < n:
        p <<= 1
    return p


def fb_tex(rect, xscale, yscale):
    """make_framebuffer_tex, in 640x480 staging space (k cancels)."""
    x, y, w, h = rect
    tex_w, tex_h = pot(w), pot(h)
    return {'x': x, 'y': y, 'w': xscale * tex_w, 'h': yscale * tex_h,
            'tex_w': tex_w, 'tex_h': tex_h,
            'xscale': xscale, 'yscale': yscale}


def apply_fbcapture(t):
    """+0x10F23B4 cave. `cmp w5, w1` is xscale*tex_w vs tex_w, so it fires
    only for 1:1 captures. Escape at xscale 2 is excluded -- as the swirl is."""
    if t['w'] != t['tex_w']:
        return t, False
    x = (t['x'] + 107) * 640 // 854
    x = min(x, 640 - t['w'])
    x = max(x, 0)
    out = dict(t, x=x)
    return out, True


def apply_fbwindow(t):
    """+0x10D7138, CPU branch only:
       if (origin + size > surf) origin = max(0, surf - size)."""
    moved = []
    out = dict(t)
    if out['x'] + out['w'] > STAGING_W:
        out['x'] = max(0, STAGING_W - out['w'])
        moved.append('x %d -> %d' % (t['x'], out['x']))
    if out['y'] + out['h'] > STAGING_H:
        out['y'] = max(0, STAGING_H - out['h'])
        moved.append('y %d -> %d' % (t['y'], out['y']))
    return out, moved


def staging_col_for_u(t, u):
    """Which staging column a mesh UV of `u` ends up showing.

    Both paths agree: the texture/target is `fb_tex.w` (or, under gpucap,
    `tex_format.width`) wide and holds `fb_tex.w` staging columns starting at
    `fb_tex.x`; the mesh addresses it as `u / tex_format.width`. The tex_w
    cancels and the answer is `x + u * xscale` either way.
    """
    return t['x'] + u * t['xscale']


def staging_row_for_v(t, v):
    return t['y'] + v * t['yscale']


def written_cols(t):
    """How many columns of the texture the CPU copy actually fills."""
    return max(0, min(STAGING_W, t['x'] + t['w']) - t['x'])


def game_x_of_staging(c):
    """The blit into the staging surface is anamorphic: 640 columns hold the
    whole 853.33-game-unit frame (ff7nx_fbcapture 2-4)."""
    return WIDE_X + 1 / 3 + c * WIDE_W_EXACT / STAGING_W


def build(gpu_path, escapescale, fbwindow=True, fbcapture=True, tall=False):
    """Return the per-quad map: screen-x span -> source game-x span."""
    yscale = ET.YSCALE_NEW if tall else YSCALE_STOCK
    tiles = {}
    for name, rect in (('A', RECT_A), ('B', RECT_B)):
        t = fb_tex(rect, XSCALE, yscale)
        fired = False
        if fbcapture:
            t, fired = apply_fbcapture(t)
        slid = []
        if not gpu_path and fbwindow:
            t, slid = apply_fbwindow(t)
        t['fbcapture_fired'] = fired
        t['fbwindow_slid'] = slid
        t['unwritten_from'] = None if gpu_path else written_cols(t)
        tiles[name] = t

    spans = []
    for q in range(COLS - 1):
        tile = tiles['A' if q < SPLIT_QUAD else 'B']
        ul, ur = u_left(q), u_right(q)
        # geometry
        gl = q * RAW_STEP * MESH_SCALE + RECT_ORIGIN[0]
        gr = (q + 1) * RAW_STEP * MESH_SCALE + RECT_ORIGIN[0]
        if escapescale:
            gl, gr = ES.expected(gl), ES.expected(gr)
        spans.append({
            'quad': q, 'tile': 'A' if q < SPLIT_QUAD else 'B',
            'screen': (gl, gr),
            'source': (game_x_of_staging(staging_col_for_u(tile, ul)),
                       game_x_of_staging(staging_col_for_u(tile, ur))),
            'u': (ul, ur),
            'unwritten': (tile['unwritten_from'] is not None and
                          max(ul, ur) * tile['xscale'] >
                          tile['unwritten_from']),
        })
    return tiles, spans


def report(label, gpu_path, escapescale, **kw):
    tiles, spans = build(gpu_path, escapescale, **kw)
    print('\n' + '=' * 74)
    print(label)
    print('=' * 74)
    for n in ('A', 'B'):
        t = tiles[n]
        lo = staging_col_for_u(t, 0)
        hi = staging_col_for_u(t, 160)
        print('  capture %s  fb_tex=(x=%d y=%d w=%d h=%d) tex_format=%dx%d '
              'xscale=%d' % (n, t['x'], t['y'], t['w'], t['h'],
                             t['tex_w'], t['tex_h'], t['xscale']))
        print('             mesh addresses staging cols %d..%d  = game x '
              '%.1f..%.1f' % (lo, hi, game_x_of_staging(lo),
                              game_x_of_staging(hi)))
        if t['fbcapture_fired']:
            print('             ff7nx_fbcapture origin correction FIRED')
        if t['fbwindow_slid']:
            print('             ff7nx_fbwindow SLID the rect: %s'
                  % ', '.join(t['fbwindow_slid']))
        if t['unwritten_from'] is not None and t['unwritten_from'] < t['w']:
            print('             CPU copy fills %d of %d texture columns'
                  % (t['unwritten_from'], t['w']))

    # the map, as an error against the identity. The two seam vertices are
    # reported separately: they are the known inclusive-texel pull-in, not a
    # property of the capture path, so lumping them in would hide a real
    # regression behind a constant 2.67.
    worst, worst_q, seam = 0.0, None, 0.0
    for s in spans:
        for sc, so in zip(s['screen'], s['source']):
            e = abs(so - sc)
            if s['quad'] in SEAM_QUADS:
                seam = max(seam, e)
                continue
            if e > worst:
                worst, worst_q = e, s
    cover = (spans[0]['screen'][0], spans[-1]['screen'][1])
    print('  mesh covers screen game x %.1f .. %.1f   (frame is %.1f .. %.1f)'
          % (cover[0], cover[1], WIDE_X + 1 / 3,
             WIDE_X + 1 / 3 + WIDE_W_EXACT))
    print('  screen-x -> source-x   worst deviation from identity: %.2f game '
          'units%s' % (worst, '' if worst_q is None
                       else ' (quad %d, tile %s)'
                       % (worst_q['quad'], worst_q['tile'])))
    print('                         at the two seam quads %s: %.2f'
          % (SEAM_QUADS, seam))

    # duplication: does any source x appear under two different screen x?
    dup = None
    a = [s for s in spans if s['tile'] == 'A']
    b = [s for s in spans if s['tile'] == 'B']
    a_lo, a_hi = a[0]['source'][0], a[-1]['source'][1]
    b_lo, b_hi = b[0]['source'][0], b[-1]['source'][1]
    ov = min(a_hi, b_hi) - max(a_lo, b_lo)
    if ov > 1:
        dup = (max(a_lo, b_lo), min(a_hi, b_hi))
    print('  tile A shows game x %.1f..%.1f,  tile B shows %.1f..%.1f'
          % (a_lo, a_hi, b_lo, b_hi))
    if dup:
        print('  >>> DOUBLED: game x %.1f..%.1f (%.0f units, %.0f%% of the '
              'frame) is drawn in BOTH halves'
              % (dup[0], dup[1], dup[1] - dup[0],
                 100 * (dup[1] - dup[0]) / WIDE_W_EXACT))
    else:
        print('  >>> no source region is drawn twice')
    bad = [s['quad'] for s in spans if s['unwritten']]
    if bad:
        print('  >>> quads sampling texture columns the copy never wrote: %s'
              % bad)
    return worst, seam, dup, cover


def rows_report(tall):
    """The vertical map, row by row: screen y vs the staging row shown there.

    Geometry  overlay y = raw*MESH_SCALE, then ff7nx_escapetall's transform.
    Sampling  staging row = v*yscale, v = raw (escape_uv_init's `shl eax, 3`
              matches the mesh's own raw step, so v and raw are the same
              number).
    """
    yscale = ET.YSCALE_NEW if tall else YSCALE_STOCK
    ripple_c = ET.RIPPLE_C_NEW if tall else RIPPLE_C_STOCK
    t = fb_tex(RECT_A, XSCALE, yscale)

    def geo(raw):
        y = raw * MESH_SCALE + RECT_ORIGIN[1]
        return ET.expected(y) if tall else y

    worst = 0
    for row in range(ROWS):
        raw = row * RAW_STEP
        screen = geo(raw)
        src = staging_row_for_v(t, raw)
        if screen <= FRAME_H:                 # only judge what is on screen
            worst = max(worst, abs(src - screen))
    covered = min(geo((ROWS - 1) * RAW_STEP), FRAME_H)
    centre = geo(ripple_c)
    print('\nvertical (%s):' % ('tall' if tall else 'stock'))
    print('  yscale %d, ripple Y centre raw %d' % (yscale, ripple_c))
    print('  mesh reaches screen y %d (clipped to %d); visible band 0..%d'
          % (geo((ROWS - 1) * RAW_STEP), FRAME_H, covered))
    print('  screen y -> staging row: worst deviation %d row(s)  -> %s'
          % (worst, '1:1' if worst == 0 else 'MISMATCH'))
    print('  ring centre at screen y %d  (the frame centre is %d)'
          % (centre, FRAME_H // 2))
    per_raw_x = WIDE_W_EXACT / ((COLS - 1) * RAW_STEP)
    per_raw_y = geo(RAW_STEP * 2) / (RAW_STEP * 2)
    print('  ring aspect: %.3f game units across per raw unit vs %.3f down '
          '-> %+.1f%%' % (per_raw_x, per_raw_y,
                          100 * (per_raw_y / per_raw_x - 1)))
    return covered, worst, centre, per_raw_y / per_raw_x


FRAME_H = 480


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('--png', help='also render a landmarked frame here')
    args = ap.parse_args(argv)

    print('escapescale transform, read from the module: '
          'x=0 -> %d, 320 -> %d, 640 -> %d'
          % (ES.expected(0), ES.expected(320), ES.expected(640)))

    w_stock, s_stock, d_stock, _ = report(
        'STOCK  (field_0 = 1, CPU readback; no escapescale)',
        gpu_path=False, escapescale=False)
    w_408, s_408, d_408, _ = report(
        'BUILD 408 state  (CPU readback + escapescale)',
        gpu_path=False, escapescale=True)
    w_fix, s_fix, d_fix, cov = report(
        'BUILD 409 FIX  (field_0 = 0, GPU path + escapescale)',
        gpu_path=True, escapescale=True)
    w_t, s_t, d_t, cov_t = report(
        'BUILD 410  (409 + escapetall: yscale 3, y\' = 3y/2, ripple C 80)',
        gpu_path=True, escapescale=True, tall=True)
    cov409, worst409, c409, asp409 = rows_report(tall=False)
    cov410, worst410, c410, asp410 = rows_report(tall=True)

    print('\n' + '=' * 74)
    print('ACCEPTANCE')
    print('=' * 74)
    ok = True

    def check(name, cond, detail=''):
        nonlocal ok
        print('  [%s] %s%s' % ('PASS' if cond else 'FAIL', name,
                               '  -- ' + detail if detail else ''))
        ok = ok and cond

    check('stock reproduces the reported doubling', d_stock is not None,
          '' if d_stock is None else 'game x %.0f..%.0f drawn twice'
          % d_stock)
    check('the fix draws no source region twice', d_fix is None)
    check('the fix is 1:1 with the screen away from the seam', w_fix < 1.0,
          'worst %.2f game units -- sub-pixel, and it is escapescale\'s '
          'sdiv truncating (4x-320)/3 to an integer' % w_fix)
    check('the only deviation is the derived 1-texel seam pull-in',
          abs(s_fix - WIDE_W_EXACT / STAGING_W * 2) < 0.01,
          '%.2f game units at quads %s, = 2 staging columns'
          % (s_fix, SEAM_QUADS))
    check('the fix spans the full 16:9 width',
          cov[0] <= WIDE_X + 2 and cov[1] >= WIDE_X + WIDE_W_EXACT - 2,
          'covers %.1f..%.1f' % cov)
    check('escapescale alone does NOT fix the doubling',
          d_408 is not None,
          'which is why builds 404-408 still showed it')
    check('the ripple origin stays at the frame centre',
          160 * MESH_SCALE == 320 and c409 == FRAME_H // 2)
    print()
    check('tall still draws no source region twice', d_t is None)
    check('tall is still 1:1 horizontally away from the seam', w_t < 1.0,
          'worst %.2f game units' % w_t)
    check('tall covers the full frame height', cov410 >= FRAME_H,
          'visible band 0..%d of %d' % (cov410, FRAME_H))
    check('tall is 1:1 vertically', worst410 == 0,
          'worst %d staging row(s)' % worst410)
    check('tall keeps the ring centre on the frame centre',
          c410 == FRAME_H // 2, 'screen y %d' % c410)
    check('tall makes the rings rounder, not flatter',
          abs(asp410 - 1) < abs(asp409 - 1),
          '409 %+.1f%% -> 410 %+.1f%%'
          % (100 * (asp409 - 1), 100 * (asp410 - 1)))
    check('409 stopped at the UI line (so tall is the thing that changes it)',
          cov409 < FRAME_H, 'visible band 0..%d' % cov409)

    if args.png:
        render(args.png)
    print('\n%s' % ('ALL CHECKS PASSED' if ok else 'CHECKS FAILED'))
    return 0 if ok else 1


# ---------------------------------------------------------------- rendering
def render(path):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print('\n(Pillow not installed -- skipping the PNG)')
        return
    W, H = 1280, 720

    def src():
        im = Image.new('RGB', (W, H), (20, 24, 34))
        d = ImageDraw.Draw(im)
        for i in range(0, W, 80):                      # vertical ruler
            g = WIDE_X + 1 / 3 + i * WIDE_W_EXACT / W
            d.line([(i, 0), (i, H)], fill=(60, 66, 80))
            d.text((i + 3, 4), '%d' % g, fill=(150, 160, 180))
        d.rectangle([40, 120, 200, 380], fill=(200, 40, 40))       # left
        d.rectangle([W - 200, 120, W - 40, 380], fill=(40, 90, 210))  # right
        d.rectangle([W // 2 - 14, 60, W // 2 + 14, H - 60],
                    fill=(230, 210, 40))                            # centre
        # The UI band, painted as CONTENT rather than black -- the point of
        # the 410 panel is whether the mesh reaches down here, and a black
        # band would be indistinguishable from not reaching it. Whether the
        # real capture surface holds the UI or void is the one open question.
        ui = int(H * 336 / 480)
        d.rectangle([0, ui, W, H], fill=(34, 26, 46))
        for i in range(0, W, 80):
            d.line([(i, ui), (i, H)], fill=(80, 60, 100))
        d.rectangle([160, ui + 30, 520, H - 30], fill=(190, 60, 170))
        d.rectangle([W - 520, ui + 30, W - 160, H - 30], fill=(60, 180, 140))
        d.text((8, ui + 6), 'UI BAND (game y 336..480)', fill=(230, 220, 240))
        return im

    source = src()
    sp = source.load()

    def staging(c, r):
        gx = game_x_of_staging(c)
        px = int(round((gx - (WIDE_X + 1 / 3)) * W / WIDE_W_EXACT))
        py = int(round(r * H / STAGING_H))
        if not (0 <= px < W and 0 <= py < H):
            return (0, 0, 0)
        return sp[px, py]

    panels = []
    for label, gpu, esc, tall in (
            ('stock: CPU readback, 4:3', False, False, False),
            ('408: + escapescale', False, True, False),
            ('409: GPU path + escapescale', True, True, False),
            ('410: + escapetall (full height)', True, True, True)):
        tiles, spans = build(gpu, esc, tall=tall)
        out = Image.new('RGB', (W, H), (0, 0, 0))
        op = out.load()
        for s in spans:
            x0 = int(round((s['screen'][0] - (WIDE_X + 1 / 3))
                           * W / WIDE_W_EXACT))
            x1 = int(round((s['screen'][1] - (WIDE_X + 1 / 3))
                           * W / WIDE_W_EXACT))
            if x1 <= x0:
                continue
            t = tiles[s['tile']]
            for px in range(max(0, x0), min(W, x1)):
                f = (px - x0) / (x1 - x0)
                u = s['u'][0] + f * (s['u'][1] - s['u'][0])
                c = staging_col_for_u(t, u)
                for row in range(ROWS - 1):
                    def gy(r):
                        yy = r * RAW_STEP * MESH_SCALE
                        return ET.expected(yy) if tall else yy
                    y0 = int(round(gy(row) * H / 480))
                    y1 = int(round(gy(row + 1) * H / 480))
                    for py in range(max(0, y0), min(H, y1)):
                        g = (py - y0) / max(1, y1 - y0)
                        v = (row + g) * RAW_STEP
                        op[px, py] = staging(c, staging_row_for_v(t, v))
        panels.append((label, out))

    strip = Image.new('RGB', (W, H * (len(panels) + 1)), (0, 0, 0))
    strip.paste(source, (0, 0))
    from PIL import ImageDraw as _D
    d = _D.Draw(strip)
    d.text((8, H - 24), 'SOURCE FRAME', fill=(255, 255, 255))
    for i, (label, im) in enumerate(panels):
        strip.paste(im, (0, H * (i + 1)))
        d.text((8, H * (i + 1) + 8), label, fill=(255, 255, 255))
    strip.save(path)
    print('\nwrote %s' % path)


if __name__ == '__main__':
    raise SystemExit(main())
