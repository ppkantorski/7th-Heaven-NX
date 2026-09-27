#!/usr/bin/env python3
r"""
ff7nx_condormap.py -- put SYW's Fort Condor map page back where the game
draws it (BUILD 567).

WHAT IS WRONG
=============
Fort Condor's battlefield is one 512 x 1024 picture cut into eight 256 x 256
pages, map0..map7 of condor.lgp (two columns, four rows). The SYW Unified
Minigames pack replaces each page with a 1024 x 1024 upscale.

Seven of the eight line up with the game's own page to the texel. map4 --
left column, third row, the mountainside under the reactor -- does not: its
whole picture sits 24 px (6 game units) LOWER than the page it replaces.
Measured against the vanilla page in eight separate windows of the page,
every one says (dx, dy) = (0, -24) with the error at its minimum there; the
seven good pages all measure (0, 0). So on screen:

  * across its top edge (map y 512) the art jumps -- its first 6 units are a
    copy of the bottom of map2 above it: "repeating data";
  * across its bottom edge the last 6 units of the real page are missing.

It is the mod's art, not our blit: the vanilla pages are continuous across
both edges, and the stitched SYW pages show the same break with no game code
involved (_mg/condor/syw_seam512.png).

THE FIX
=======
Before conversion, each condor map page's DDS is compared with the vanilla
page it replaces (at the vanilla grid, +/-10 units, both axes). A page that
is clearly better aligned at a non-zero offset (error below 80 % of the
error at 0,0) is shifted by that offset at full resolution; the strip the
shift uncovers is taken from the vanilla page, bicubic-upscaled -- the only
source of those texels. Today that is map4 and only map4; a future SYW
release that fixes the page measures (0, 0) and is left untouched.
"""
from __future__ import annotations

import re

import numpy as np

import tex

VERSION = b'condormap-567'
_PAGE = re.compile(r'^map[0-7]\.tex$', re.I)
SEARCH = 10               # vanilla units, each way
GAIN = 0.8                # best error must be under 80 % of the error at 0


def applies(archive, name):
    return archive.lower().startswith('condor') and bool(_PAGE.match(name))


def _vanilla_rgba(source):
    """The vanilla page at its native size, RGB float, through palette 0."""
    import ff7nx_ddstex
    t = tex.parse(source)
    w, h = t['width'], t['height']
    return ff7nx_ddstex.vanilla_palette_rgba(source, 0, w, h)


def _grey(a):
    return a[:, :, :3].astype(np.float64).mean(axis=2)


def measure(syw_rgba, van_rgba):
    """(dy, dx) in VANILLA texels such that syw[y] ~ van[y + dy], plus the
    errors at that offset and at (0, 0)."""
    from PIL import Image
    h, w = van_rgba.shape[:2]
    small = np.asarray(Image.fromarray(np.ascontiguousarray(
        syw_rgba[:, :, :3])).resize((w, h), Image.BOX), np.float64).mean(2)
    v = _grey(van_rgba)
    m = SEARCH
    core = small[m:h - m, m:w - m]
    best = None
    zero = None
    for dy in range(-m, m + 1):
        for dx in range(-m, m + 1):
            e = float(np.mean(np.abs(core - v[m + dy:h - m + dy,
                                               m + dx:w - m + dx])))
            if (dy, dx) == (0, 0):
                zero = e
            if best is None or e < best[0]:
                best = (e, dy, dx)
    return best[1], best[2], best[0], zero


def realign(syw_rgba, van_rgba, dy, dx):
    """Shift the SYW page so syw'[y] ~ van[y]; fill the uncovered strip."""
    from PIL import Image
    H, W = syw_rgba.shape[:2]
    h, w = van_rgba.shape[:2]
    ky, kx = H // h, W // w
    sy, sx = dy * ky, dx * kx
    up = np.asarray(Image.fromarray(np.ascontiguousarray(van_rgba))
                    .resize((W, H), Image.BICUBIC), np.uint8).copy()
    out = up.copy()
    # syw[y] ~ van[y + dy]  =>  out[y] = syw[y - sy]
    ys = slice(max(0, sy), H + min(0, sy))
    xs = slice(max(0, sx), W + min(0, sx))
    ysrc = slice(max(0, -sy), H - max(0, sy))
    xsrc = slice(max(0, -sx), W - max(0, sx))
    out[ys, xs] = syw_rgba[ysrc, xsrc]
    return out


def fix_blobs(archive, name, source, blobs, log=lambda *_: None):
    """blobs {palette: DDS bytes | RGBA array} -> same, realigned if needed."""
    if not applies(archive, name):
        return blobs
    import ff7nx_ddstex
    van = _vanilla_rgba(source)
    out = dict(blobs)
    for p, b in blobs.items():
        rgba = ff7nx_ddstex._decode(b)[0]
        dy, dx, err, zero = measure(rgba, van)
        if (dy, dx) != (0, 0) and err < GAIN * zero:
            out[p] = realign(rgba, van, dy, dx)
            log('  %s/%s: SYW page sits %d,%d units off the game\'s page '
                '(error %.1f -> %.1f); realigned, %d-unit strip from vanilla'
                % (archive, name, -dx, -dy, zero, err, max(abs(dy), abs(dx))))
    return out
