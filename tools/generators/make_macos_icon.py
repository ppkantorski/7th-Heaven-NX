#!/usr/bin/env python3
"""tools/generators/make_macos_icon.py -- assets/icon_macos.png from icon.png.

macOS 26 draws every app icon as a rounded square; an icon of any other
shape (icon.png is a circle) is shrunk onto a grey plate. So the Mac app
gets its own icon: the same art, filling Apple's 824 px rounded square on a
1024 px canvas. Inside the circle it is icon.png's pixels unchanged.

The corners outside the circle are rebuilt from how the art is made: a
vertical gradient (each row one colour) under translucent bands that all
run at 45 degrees (the sword, its handle, their edges). So a corner pixel
takes its band from where its 45-degree line meets the circle, and its
row's gradient from its own height: the stripes carry on straight to the
edge, at the width they have inside. (Continuing the rim radially, as this
used to, fanned every stripe out and drew a seam where two fans met.)

    python3 tools/generators/make_macos_icon.py

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
import os

import numpy as np
from PIL import Image, ImageFilter

SRC = os.path.join(nxpaths.ROOT, 'assets', 'icon.png')
DEST = os.path.join(nxpaths.ROOT, 'assets', 'icon_macos.png')
CANVAS = 1024
BODY = 824          # Apple's icon grid: the shape, centred on the canvas
EXPONENT = 5        # superellipse close to Apple's rounded square
RIM = 0.96          # sample inside the circle's anti-aliased edge
TAPS = 32           # samples along the line, inwards
TAP_STEP = 3        # px between them
KEEP = 5            # the median is of the first KEEP that are not lettering
# The lettering is not part of the bands: white, and its drop shadows in
# near-black (the darkest background is 41,20,0). A sample that lands on
# either is left out of the median, or a letter touching the rim (the H's
# and the X's shadows do) is drawn on to the edge.
LETTER_LIGHT = 200  # every channel above this
LETTER_DARK = 36    # every channel at or below this
SUPERSAMPLE = 4


def _bilinear(img, x, y):
    """img sampled at float coordinates (pixel centres at +0.5)."""
    h, w = img.shape[:2]
    x = np.clip(x - 0.5, 0, w - 1.001)
    y = np.clip(y - 0.5, 0, h - 1.001)
    x0, y0 = x.astype(int), y.astype(int)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    return ((img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx) * (1 - fy)
            + (img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx) * fy)


def _row_gradient(rgb, alpha):
    """The background colour of every row. The top half's left edge and the
    bottom half's right edge are plain background in this art (the bands
    cross the other two), sampled a few pixels in from the anti-aliasing,
    then a little smoothing, and the ends held for the rows the circle
    barely reaches."""
    n = rgb.shape[0]
    g = np.full((n, 3), np.nan, np.float32)
    inset = max(3, n // 100)
    for y in range(n):
        xs = np.nonzero(alpha[y] > 0.99)[0]
        if len(xs) < 2 * inset + 1:
            continue
        x = xs[0] + inset if y < n // 2 else xs[-1] - inset
        g[y] = rgb[y, x]
    ok = ~np.isnan(g[:, 0])
    rows = np.arange(n)
    for c in range(3):
        g[:, c] = np.interp(rows, rows[ok], g[ok, c])
    k = max(1, n // 120)
    pad = np.pad(g, ((k, k), (0, 0)), mode='edge')
    return np.stack([np.median(np.stack([pad[i:i + n, c]
                                         for i in range(2 * k + 1)]), 0)
                     for c in range(3)], 1)


def make(src=SRC):
    im = Image.open(src).convert('RGBA')
    box = im.getchannel('A').getbbox()
    side = min(box[2] - box[0], box[3] - box[1])
    im = im.crop((box[0], box[1], box[0] + side, box[1] + side))
    art = np.asarray(im.resize((BODY, BODY), Image.LANCZOS)).astype(np.float32)
    rgb, alpha = art[..., :3], art[..., 3] / 255
    g = _row_gradient(rgb, alpha)
    # the lettering, grown by its anti-aliased edge
    letters = Image.fromarray(((rgb.min(-1) > LETTER_LIGHT)
                               | (rgb.max(-1) <= LETTER_DARK)
                               ).astype(np.uint8) * 255)
    letters = np.asarray(letters.filter(ImageFilter.MaxFilter(7))) > 0

    half = BODY / 2
    r = RIM * half
    y, x = np.mgrid[0:BODY, 0:BODY]
    dx, dy = x + 0.5 - half, y + 0.5 - half
    out = rgb.copy()
    outside = np.hypot(dx, dy) > r

    # The 45-degree line through (dx, dy) is x + y = c. Inside the circle
    # only when |c| < r * sqrt 2; beyond that the line misses it, and those
    # corners (top-left, bottom-right) are plain background.
    c = dx + dy
    hits = np.abs(c) < r * np.sqrt(2)
    # Where the line meets the circle, at the end nearer this pixel:
    # points (c/2 + t, c/2 - t), at radius r when c^2/2 + 2t^2 = r^2.
    t = np.sqrt(np.maximum(0, (r * r - c * c / 2) / 2))
    t = np.where(dx - dy >= 0, t, -t)
    gp = g[y]
    # A band is the gradient seen through a translucent layer: lighter ones
    # are white over it, darker ones black. Measure the layer's strength
    # (signed: + white, - black) at a few points along the line, inwards
    # from the rim, and keep the median, so one odd pixel on the rim cannot
    # become a streak.
    taps = []
    for k in range(TAPS):
        back = k * TAP_STEP * np.sign(t) / np.sqrt(2)
        qx, qy = c / 2 + t - back + half, c / 2 - t + back + half
        seen = _bilinear(rgb, qx, qy)
        gq = g[np.clip(qy.astype(int), 0, BODY - 1)]
        # summed over the channels: the gradient's blue is near 0, and a
        # per-channel ratio there turns a 1-level difference into a big one
        diff = (seen - gq).sum(-1)
        up = diff / np.maximum((255 - gq).sum(-1), 1)
        down = diff / np.maximum(gq.sum(-1), 1)
        letter = letters[np.clip(qy.astype(int), 0, BODY - 1),
                         np.clip(qx.astype(int), 0, BODY - 1)]
        taps.append(np.where(letter, np.nan, np.where(diff >= 0, up, down)))
    taps = np.stack(taps)
    valid = ~np.isnan(taps)
    taps[valid & (np.cumsum(valid, 0) > KEEP)] = np.nan
    a = np.nan_to_num(np.nanmedian(taps, 0))
    a = np.clip(a, -1, 1)
    light = a >= 0
    a = np.abs(a)[..., None]
    over = np.where(light[..., None], 255.0, 0.0)
    band = gp * (1 - a) + over * a
    fill = np.where(hits[..., None], band, gp)
    out = np.where(outside[..., None], fill, rgb)

    n = BODY * SUPERSAMPLE
    yy, xx = np.mgrid[0:n, 0:n]
    u = (xx + 0.5 - n / 2) / (n / 2)
    v = (yy + 0.5 - n / 2) / (n / 2)
    shape = ((np.abs(u) ** EXPONENT + np.abs(v) ** EXPONENT) <= 1)
    mask = Image.fromarray(shape.astype(np.uint8) * 255).resize(
        (BODY, BODY), Image.LANCZOS)
    rgba = np.dstack([np.clip(np.round(out), 0, 255),
                      np.asarray(mask, np.float32)]).astype(np.uint8)

    full = Image.new('RGBA', (CANVAS, CANVAS), (0, 0, 0, 0))
    off = (CANVAS - BODY) // 2
    full.paste(Image.fromarray(rgba, 'RGBA'), (off, off))
    return full


if __name__ == '__main__':
    make().save(DEST, optimize=True)
    print('wrote', DEST)
