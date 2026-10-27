#!/usr/bin/env python3
"""tools/fit_residual2.py -- the residual screen map, by exhaustive search.

Same question as fit_residual.py and the same model

    shown_screen = ( ax * true_x + bx ,  ay * true_y + by )

but a search that can actually find the answer. Registering high-passed grass
gives a correlation peak that is SHARP -- measured on a control pair (the same
frame twice), NCC falls from 0.977 to 0.13 two pixels off the truth. A
coordinate descent stepping 50 px never sees it. So:

    for every (ax, ay) on a grid   resample the truth by that scale
    then                           score EVERY integer (bx, by) at once,
                                   by FFT cross-correlation

The masked normalised correlation over the overlap needs four correlations,
all of them FFTs:

    Sts = corr(t.tm, s.sm)    Stt = corr(t^2.tm, sm)
    Sss = corr(tm, s^2.sm)    N   = corr(tm, sm)
    St  = corr(t.tm, sm)      Ss  = corr(tm, s.sm)

    ncc = (Sts - St.Ss/N) / sqrt((Stt - St^2/N)(Sss - Ss^2/N))

which is the ordinary Pearson correlation restricted to the pixels both masks
accept, for each shift. Nothing here assumes the answer is near anything.

Usage:  fit_residual2.py TRUTH.png WARPED.png [--ax lo hi step] [--ay ...]

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
import sys

import numpy as np

sys.path.insert(0, __file__.rsplit('/', 1)[0])
from fit_residual import BAND, grass_mask, highpass, load  # noqa: E402

MIN_OVERLAP = 20000


def corr(a, b, shape):
    """sum_p a(p) b(p - d), for every integer d, via FFT."""
    A = np.fft.rfft2(a, shape)
    B = np.fft.rfft2(b[::-1, ::-1], shape)
    return np.fft.irfft2(A * B, shape)


def best_shift(t, tm, s, sm):
    H, W = t.shape
    shape = (2 * H, 2 * W)
    t = t * tm
    s = s * sm
    tm = tm.astype(np.float64)
    sm = sm.astype(np.float64)
    N = corr(tm, sm, shape)
    Sts = corr(t, s, shape)
    St = corr(t, sm, shape)
    Ss = corr(tm, s, shape)
    Stt = corr(t * t, sm, shape)
    Sss = corr(tm, s * s, shape)
    with np.errstate(invalid='ignore', divide='ignore'):
        num = Sts - St * Ss / N
        va = Stt - St * St / N
        vb = Sss - Ss * Ss / N
        ncc = num / np.sqrt(va * vb)
    ncc[~np.isfinite(ncc)] = -2.0
    ncc[N < MIN_OVERLAP] = -2.0
    k = int(np.argmax(ncc))
    iy, ix = divmod(k, shape[1])
    # corr index d maps to a shift of (d - (size-1)) in each axis
    dy = iy - (H - 1)
    dx = ix - (W - 1)
    return float(ncc[iy, ix]), dx, dy, float(N[iy, ix])


def resample(src, srcm, ax, ay, H, W):
    """Put truth point q at (ax*qx, ay*qy) on an H x W grid."""
    yy, xx = np.mgrid[0:H, 0:W]
    qx = np.rint(xx / ax).astype(np.int64)
    qy = np.rint(yy / ay).astype(np.int64)
    ok = (qx >= 0) & (qx < W) & (qy >= 0) & (qy < H)
    qxc = np.clip(qx, 0, W - 1)
    qyc = np.clip(qy, 0, H - 1)
    out = src[qyc, qxc]
    m = ok & srcm[qyc, qxc]
    return out * m, m


def main():
    truth, warped = sys.argv[1], sys.argv[2]
    a = sys.argv[3:]

    def rng(flag, default):
        if flag in a:
            i = a.index(flag)
            lo, hi, st = (float(x) for x in a[i + 1:i + 4])
            return np.arange(lo, hi + 1e-9, st)
        return default

    axs = rng('--ax', np.arange(0.15, 2.401, 0.05))
    ays = rng('--ay', np.arange(0.40, 2.401, 0.05))

    T, Wp = load(truth), load(warped)
    H, W = T.shape[:2]
    src = highpass(T.mean(2))
    tgt = highpass(Wp.mean(2))
    srcm = grass_mask(T, *BAND)
    tgtm = grass_mask(Wp, BAND[0] + 2, BAND[1])
    print('usable floor pixels: truth %d  warped %d' % (srcm.sum(), tgtm.sum()))
    print('grid: %d ax x %d ay = %d candidates'
          % (len(axs), len(ays), len(axs) * len(ays)))

    out = []
    for ax in axs:
        row = []
        for ay in ays:
            s, sm = resample(src, srcm, ax, ay, H, W)
            n, dx, dy, cnt = best_shift(tgt, tgtm, s, sm)
            out.append((n, float(ax), float(dx), float(ay), float(dy), cnt))
            row.append(n)
        print('  ax %5.2f   best ncc over ay: %+.4f  (at ay %.2f)'
              % (ax, max(row), ays[int(np.argmax(row))]))
        sys.stdout.flush()

    out.sort(reverse=True)
    print()
    print('top 12:')
    for n, ax, dx, ay, dy, cnt in out[:12]:
        print('  ncc %+.4f   ax %6.3f  bx %+7.1f   ay %6.3f  by %+7.1f   '
              'n=%d' % (n, ax, dx, ay, dy, cnt))
    n, ax, dx, ay, dy, cnt = out[0]
    print()
    print('BEST  ncc %+.4f over %d pixels' % (n, cnt))
    print('  shown_x = %.5f * true_x + %.2f' % (ax, dx))
    print('  shown_y = %.5f * true_y + %.2f' % (ay, dy))
    return 0


if __name__ == '__main__':
    sys.exit(main())
