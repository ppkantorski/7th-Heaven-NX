#!/usr/bin/env python3
"""tools/simulate_field.py -- render the warped field the way the game does.

The whole chain, forward, on a real screenshot:

    screen p  --FIT_HUV-->  (u, v)                the disc's own homography
              --x 1<<k-->   texel (i, j)          the renderer's UV scale
              -->           staging (col, row)    col = fb_tex.x + i*640/854
                                                  row = fb_tex.y + j
              --box-->      frame (x, y)          where the blit put the frame
    out[p] = clean_screenshot[frame]

Feed it a CLEAN screenshot and it produces what the field should look like on
top of it. Compare that against the real field-up screenshot and the model is
either right or it is not -- no fitting, no residual, just look at it.

The one free parameter is the capture BOX: where in the staging surface the
blit puts the 1280x720 frame. `--search` sweeps it and reports which box
reproduces the real screenshot best, which is the measurement three builds
have failed to get any other way.

Usage:
    simulate_field.py CLEAN.png [FIELDUP.png] [--box bx,by,bw,bh] [--search]
                      [--out FILE]

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import sys

import numpy as np
from PIL import Image

# the disc's screen <-> UV homography, validated four times against the
# operator's own `texel` probe (median residual 1.26 UV units over 437k px)
FIT_HUV = np.array([[1342.942886, -1996.873329, -1162.038969],
                    [-460.5917001, 1948.840443, -1452.684385],
                    [0.1640381782, -23.54451286, 1.0]])
W, H = 1280, 720
K = 4.0                 # texels per UV unit at SEVENTH_NX_FB_SURFACE=4
STEP = 640.0 / 854.0    # the anamorphic source step, per texel
FBX, FBY = 320.0, 0.0   # fb_tex.x / .y at surface k=4
SURF_W, SURF_H = 2560.0, 1920.0


def uv_of_screen(xs, ys):
    xn, yn = xs / W, ys / H
    den = FIT_HUV[2, 0] * xn + FIT_HUV[2, 1] * yn + FIT_HUV[2, 2]
    u = (FIT_HUV[0, 0] * xn + FIT_HUV[0, 1] * yn + FIT_HUV[0, 2]) / den
    v = (FIT_HUV[1, 0] * xn + FIT_HUV[1, 1] * yn + FIT_HUV[1, 2]) / den
    return u, v


def render(clean, box, band=(196, 496)):
    """The field, drawn on top of `clean`, for one capture box."""
    bx, by, bw, bh = box
    out = clean.copy()
    ys, xs = np.mgrid[band[0]:band[1], 0:W]
    u, v = uv_of_screen(xs.astype(float) + 0.5, ys.astype(float) + 0.5)
    col = FBX + (K * u) * STEP
    row = FBY + (K * v)
    fx = (col - bx) * W / bw
    fy = (row - by) * H / bh
    ix = np.rint(fx).astype(np.int64)
    iy = np.rint(fy).astype(np.int64)
    ok = ((u > 0) & (u < 256) & (v > 0) & (v < 256)
          & (col >= 0) & (col < SURF_W) & (row >= 0) & (row < SURF_H)
          & (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H))
    sub = out[band[0]:band[1]]
    src = clean[np.clip(iy, 0, H - 1), np.clip(ix, 0, W - 1)]
    sub[ok] = src[ok]
    out[band[0]:band[1]] = sub
    return out, ok


def grade(sim, real, ok, band=(196, 496)):
    """Masked correlation of the high-passed floor -- the same measure the
    registration tools use, so a good number here means the same thing."""
    def hp(g, k=6):
        n = 2 * k + 1
        p = np.pad(g, k + 1, mode='edge')
        c = np.cumsum(np.cumsum(p, 0), 1)
        s = (c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]) / (n * n)
        return g - s[:g.shape[0], :g.shape[1]]
    a = hp(sim.mean(2))[band[0]:band[1]]
    b = hp(real.mean(2))[band[0]:band[1]]
    m = ok & (np.abs(b) > 0)
    if m.sum() < 5000:
        return -2.0, int(m.sum())
    x, y = a[m], b[m]
    x = x - x.mean()
    y = y - y.mean()
    d = np.sqrt((x * x).sum() * (y * y).sum())
    return (float((x * y).sum() / d) if d else -2.0), int(m.sum())


def main():
    a = sys.argv[1:]
    clean = np.asarray(Image.open(a[0]).convert('RGB')).astype(np.float64)
    real = None
    if len(a) > 1 and not a[1].startswith('--'):
        real = np.asarray(Image.open(a[1]).convert('RGB')).astype(np.float64)
    box = (0.0, 0.0, SURF_W, SURF_H)
    if '--box' in a:
        box = tuple(float(x) for x in a[a.index('--box') + 1].split(','))
    out = a[a.index('--out') + 1] if '--out' in a else None

    if '--search' in a and real is not None:
        best = []
        for bw in (289, 400, 512, 640, 854, 1024, 1280, 1707, 2048, 2560):
            for bh in (480, 720, 960, 1170, 1440, 1920):
                for bx in np.arange(0, 2200, 160.0):
                    for by in np.arange(0, 1700, 240.0):
                        if bx + bw > SURF_W + 400 or by + bh > SURF_H + 400:
                            continue
                        sim, ok = render(clean, (bx, by, bw, bh))
                        s, n = grade(sim, real, ok)
                        best.append((s, bx, by, bw, bh, n))
        best.sort(reverse=True)
        print('top capture boxes (masked NCC against the real field):')
        for s, bx, by, bw, bh, n in best[:10]:
            print('  ncc %+.4f   box (%6.0f, %6.0f, %6.0f, %6.0f)  n=%d'
                  % (s, bx, by, bw, bh, n))
        box = tuple(best[0][1:5])

    sim, ok = render(clean, box)
    print('box (%.0f, %.0f, %.0f, %.0f)  covered %.1f%% of the band'
          % (*box, 100.0 * ok.mean()))
    if real is not None:
        s, n = grade(sim, real, ok)
        print('  masked NCC against the real field-up shot: %+.4f (n=%d)' % (s, n))
    if out:
        Image.fromarray(np.clip(sim, 0, 255).astype(np.uint8)).save(out)
        print('  wrote', out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
