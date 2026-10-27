#!/usr/bin/env python3
"""tools/fit_local.py -- the residual map's LOCAL scale, by patch matching.

Takes a patch of the warped field, rescales it by (1/sx, 1/sy) over a grid,
and template-matches each against the plain screenshot by FFT normalised
cross-correlation. The winning (sx, sy, x, y) is the local mapping there,
measured, with nothing assumed about its global form.

THE TRAP THIS AVOIDS. The obvious version -- fix the patch in WARPED space
and let the rescaled template shrink as the scale rises -- cannot be scored
across scales: a smaller template correlates better with anything, so the
search runs away to the top of the grid and comes back with nonsense and a
RISING score. (It did: sx, sy pinned to the ceiling, one patch "matched" at
the far side of the screen, ncc climbing all the way.) Here the TRUTH-space
template is a fixed (tw, th) and the warped region grows instead, so every
candidate is the same number of pixels and the correlations are comparable.

Usage:
    fit_local.py TRUTH.png WARPED.png cx,cy [cx,cy ...]
    SXR/SYR = 'lo,hi,step'   TPL = 'tw,th'   SEARCH = 'x0,x1,y0,y1'

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


def _rng(name, default):
    return np.arange(*[float(x)
                       for x in os.environ.get(name, default).split(',')])


SX = _rng('SXR', '0.40,3.21,0.10')
SY = _rng('SYR', '0.40,4.81,0.10')
TPL = [int(x) for x in os.environ.get('TPL', '90,56').split(',')]
SEARCH = [int(x) for x in os.environ.get('SEARCH', '0,1280,150,500')
          .split(',')]


def load(p):
    return np.asarray(Image.open(p).convert('RGB')).astype(np.float64)


def highpass(g, k=6):
    n = 2 * k + 1
    p = np.pad(g, k + 1, mode='edge')
    c = np.cumsum(np.cumsum(p, 0), 1)
    s = (c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]) / (n * n)
    return g - s[:g.shape[0], :g.shape[1]]


def ncc_map(big, patch):
    ph, pw = patch.shape
    BH, BW = big.shape
    shape = (BH + ph, BW + pw)
    p = patch - patch.mean()
    F = np.fft.rfft2(big, shape)
    O = np.fft.rfft2(np.ones_like(p)[::-1, ::-1], shape)
    num = np.fft.irfft2(F * np.fft.rfft2(p[::-1, ::-1], shape), shape)
    s1 = np.fft.irfft2(F * O, shape)
    s2 = np.fft.irfft2(np.fft.rfft2(big * big, shape) * O, shape)
    n = ph * pw
    with np.errstate(invalid='ignore', divide='ignore'):
        var = s2 - s1 * s1 / n
        out = num / np.sqrt(var * (p * p).sum())
    out[~np.isfinite(out)] = -2.0
    return out[ph - 1:BH, pw - 1:BW]


def fit_patch(truth_hp, warped_g, cx, cy):
    x0, x1, y0, y1 = SEARCH
    big = truth_hp[y0:y1, x0:x1]
    tw, th = TPL
    H, W = warped_g.shape
    out = []
    for sx in SX:
        for sy in SY:
            ww, wh = int(round(tw * sx)), int(round(th * sy))
            a, b = cx - ww // 2, cy - wh // 2
            if a < 0 or b < 0 or a + ww > W or b + wh > H:
                continue
            src = Image.fromarray(
                np.clip(warped_g[b:b + wh, a:a + ww], 0, 255).astype(np.uint8))
            pt = highpass(np.asarray(src.resize((tw, th), Image.LANCZOS),
                                     dtype=np.float64), k=6)
            r = ncc_map(big, pt)
            k = int(np.argmax(r))
            iy, ix = divmod(k, r.shape[1])
            out.append((float(r[iy, ix]), float(sx), float(sy),
                        ix + x0 + tw / 2.0, iy + y0 + th / 2.0))
    out.sort(reverse=True)
    return out


def main():
    T, Wp = load(sys.argv[1]), load(sys.argv[2])
    truth_hp = highpass(T.mean(2), k=6)
    wg = Wp.mean(2)
    print('template %dx%d in TRUTH space; search x %d-%d y %d-%d'
          % (TPL[0], TPL[1], *SEARCH))
    print('warped centre    ncc     sx    sy   truth centre       runner-up')
    for spec in sys.argv[3:]:
        cx, cy = (int(v) for v in spec.split(',')[:2])
        o = fit_patch(truth_hp, wg, cx, cy)
        if not o:
            print('  (%4d,%4d)  no candidate' % (cx, cy))
            continue
        v, sx, sy, tx, ty = o[0]
        print('  (%4d,%4d) %+.4f  %.2f  %.2f  (%6.1f,%6.1f)   '
              '%+.4f sx%.2f sy%.2f' % (cx, cy, v, sx, sy, tx, ty,
                                       o[1][0], o[1][1], o[1][2]))
        sys.stdout.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
