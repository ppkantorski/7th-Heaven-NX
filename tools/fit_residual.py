#!/usr/bin/env python3
"""tools/fit_residual.py -- measure the residual screen map of a proj build.

Registers the warped field in a `SEVENTH_NX_FB_PROBE=proj` screenshot against
the same floor in a plain screenshot, and reports the axis-aligned residual

    shown_screen = ( ax * true_x + bx ,  ay * true_y + by )

Why axis-aligned is the right family -- it is not an assumption about the
picture, it is forced by the arithmetic:

    the proj body paints texel (i,j) with surface pixel   M(i,j)
    it was built as                                       M = BOX . S
    it is drawn at screen                                 S(i,j)
    so what lands at screen p came from true screen        BOX_true^-1 . BOX . p

Both boxes are blit destination rects, so both are axis-aligned, and so is
their composition. The disc's own screen<->UV homography CANCELS EXACTLY --
which is why an error here cannot be diagnosed by looking at the disc's
geometry, and why a picture that is only SCALED along the two axes can come
back looking rotated: an axis-aligned scale changes the angle of every
oblique feature.

Usage:
    fit_residual.py TRUTH.png WARPED.png [ax bx ay by ...]

Extra arguments are seed hypotheses (groups of four). With none, it uses a
default spread. The winner is whichever seed refines to the highest masked
normalised cross-correlation; the NCC value itself says whether the
axis-aligned family describes the picture at all.

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
from PIL import Image

# the floor, above the HUD, with the models cut out
HUD = (150, 486, 1132, 704)
KUJATA = (556, 0, 968, 302)
MIDDLE = (424, 246, 904, 384)
BAND = (198, 492)


def load(path):
    return np.asarray(Image.open(path).convert('RGB')).astype(np.float64)


def highpass(g, k=9):
    """Subtract a box mean: kills the floor's lighting ramp and leaves the
    grass texture, which is the only thing worth matching on."""
    n = 2 * k + 1
    p = np.pad(g, k + 1, mode='edge')
    c = np.cumsum(np.cumsum(p, 0), 1)
    s = (c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]) / (n * n)
    return g - s[:g.shape[0], :g.shape[1]]


def grass_mask(img, y0, y1):
    R, G, B = img[..., 0], img[..., 1], img[..., 2]
    H, W = R.shape
    yy, xx = np.mgrid[0:H, 0:W]
    m = (yy >= y0) & (yy < y1) & (G > R + 6) & (G > B + 6) & (G > 40)
    for (a, b, c, d) in (HUD, KUJATA, MIDDLE):
        m &= ~((yy >= b) & (yy < d) & (xx >= a) & (xx < c))
    return m


class Fit:
    def __init__(self, truth, warped):
        T, Wp = load(truth), load(warped)
        self.H, self.W = T.shape[:2]
        self.src_hp = highpass(T.mean(2))
        self.tgt_hp = highpass(Wp.mean(2))
        self.src_m = grass_mask(T, *BAND)
        self.tgt_m = grass_mask(Wp, BAND[0] + 2, BAND[1])
        ys, xs = np.nonzero(self.tgt_m)
        self.ty, self.tx = ys.astype(np.float64), xs.astype(np.float64)
        self.tv = self.tgt_hp[ys, xs]

    def score(self, p):
        ax, bx, ay, by = p
        if abs(ax) < 1e-3 or abs(ay) < 1e-3:
            return -2.0, 0
        qx = np.rint((self.tx - bx) / ax).astype(np.int64)
        qy = np.rint((self.ty - by) / ay).astype(np.int64)
        ok = ((qx >= 0) & (qx < self.W) & (qy >= 0) & (qy < self.H))
        if ok.sum() < 3000:
            return -2.0, int(ok.sum())
        qx, qy = qx[ok], qy[ok]
        ok2 = self.src_m[qy, qx]
        n = int(ok2.sum())
        if n < 3000:
            return -2.0, n
        a = self.tv[ok][ok2]
        b = self.src_hp[qy[ok2], qx[ok2]]
        a = a - a.mean()
        b = b - b.mean()
        d = np.sqrt((a * a).sum() * (b * b).sum())
        return (float((a * b).sum() / d) if d else -2.0), n

    def refine(self, seed, step=(0.05, 50.0, 0.05, 50.0), rounds=80):
        cur = np.array(seed, dtype=np.float64)
        step = np.array(step, dtype=np.float64)
        s, n = self.score(cur)
        for _ in range(rounds):
            moved = False
            for i in range(4):
                for d in (+1, -1):
                    t = cur.copy()
                    t[i] += d * step[i]
                    sc, nn = self.score(t)
                    if sc > s:
                        s, cur, n, moved = sc, t, nn, True
            if not moved:
                step = step * 0.5
                if step[0] < 2e-4:
                    break
        return s, cur, n


DEFAULT_SEEDS = [
    # from the red-line correspondence, upper-to-upper
    (0.34841, 155.0, 1.28638, -128.3),
    # the same two points, correspondence reversed (a point reflection)
    (-0.34841, 669.0, -1.28638, 573.1),
    # the shipping build's own family, for reference
    (1.0, 0.0, 1.0, 0.0),
    (0.5, 300.0, 1.0, 0.0),
    (2.0, -600.0, 1.0, 0.0),
    (0.25, 400.0, 1.6, -200.0),
]


def main():
    truth, warped = sys.argv[1], sys.argv[2]
    rest = [float(x) for x in sys.argv[3:]]
    seeds = ([tuple(rest[i:i + 4]) for i in range(0, len(rest) - 3, 4)]
             or DEFAULT_SEEDS)
    f = Fit(truth, warped)
    print('usable floor pixels: truth %d   warped %d'
          % (f.src_m.sum(), f.tgt_m.sum()))
    print()
    out = []
    for seed in seeds:
        s0, _ = f.score(np.array(seed))
        s, p, n = f.refine(seed)
        out.append((s, tuple(p), n, seed))
        print('seed ax%+8.4f bx%+8.1f ay%+8.4f by%+8.1f  ncc %+.4f'
              % (*seed, s0))
        print('   -> ax%+8.5f bx%+8.2f ay%+8.5f by%+8.2f  ncc %+.4f  n=%d'
              % (*p, s, n))
    out.sort(reverse=True)
    s, p, n, seed = out[0]
    print()
    print('BEST  ncc %+.4f over %d pixels' % (s, n))
    print('  shown_x = %.5f * true_x + %.2f' % (p[0], p[1]))
    print('  shown_y = %.5f * true_y + %.2f' % (p[2], p[3]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
