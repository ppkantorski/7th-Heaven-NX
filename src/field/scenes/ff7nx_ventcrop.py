#!/usr/bin/env python3
"""nmkin_3: the steam is cut along the vent's rim. BUILD 778.

HARDWARE 10-22: "the smoke fx to be cleanly clipped / cropped along the
bottom edge of the steam vent ... crop it along the dark curve rim that is
the opening" and, with a drawing, "a smooth curve of crop ... the right side
would have a smooth curve to it not a sudden disconnect / flat region".

The steam is param 2 (6 states) on FX page 15, additive, layer 2. Cosmos's
plume runs a few texels past the opening's lower lip onto the dark rim, and
its lower edge ends in a flat step on the right.

(BUILD 780 replaced the curve below by the opening's fitted ellipse; see
`coverage`.) THE CURVE was the opening's lower edge, measured on the background at the
capture's camera (frame-sim match, cam (-25, -27)): the lowest texel of the
lit interior per column, the interior silhouette's dip bridged, Gaussian
smoothed (sigma 4 texels), lowered 2/3 unit onto the dark lip, in FIELD
units (layer 2 is fixed to the background, so one curve holds for every
camera). Past its right end it turns upward along a quarter circle of
radius ARC, so the cut rounds the corner instead of stepping. Left of its
left end nothing is cut (the plume never reaches below it there).

Every steam texel below the curve is removed and a FEATHER-wide band fades
in (additive: colour times coverage, 0x0000 where nothing is left). Only
cells drawn by ONE tile position are edited (all of them, measured); the
page stays truecolor and every record is unchanged.

SEVENTH_NX_NO_VENTCROP=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_VENTCROP'
REC = 52
UV_CELL = 625000
FIELDS = {
    'nmkin_3': {
        'param': 2, 'page': 15,
        # BUILD 780: the vent's lip as an ellipse (field units): centre,
        # semi-axes, rotation of the long axis in degrees. User, 10-22, with
        # a drawing: "the cropping should be like this, up to where the seam
        # turns dark from brown" -- the lip's outer edge, below the lit
        # opening. Fitted to that edge measured on the background at the
        # capture's camera (x 114..132, where brown meets the black pipe)
        # and to the drawing's ends (x 134..140), concentric in y and
        # rotation with the opening's rim; max error 0.46 unit.
        'ellipse': ((124.7, -48.074), (15.9, 7.186), 4.078),
        'grow': 0.15,
        'feather': 0.67,
        # BUILD 781. Hardware 10-22 on 780: "the black region has fx that
        # is overlapping with it on the bottom ... it shouldnt along the
        # bottom rim" -- right of x ~134 the lip ellipse runs below the rim,
        # where the pipe is already black. The black's own top edge, measured
        # on the background at the capture's camera (the first of four rows
        # darker than 24), lightly smoothed: steam is also cut below it.
        'dark': ((112.0, -44.00), (112.5, -43.93), (113.0, -43.74), (113.5, -43.41),
                 (114.0, -43.07), (114.5, -42.78), (115.0, -42.63), (115.5, -42.56),
                 (116.0, -42.56), (116.5, -42.45), (117.0, -42.22), (117.5, -41.93),
                 (118.0, -41.71), (118.5, -41.56), (119.0, -41.44), (119.5, -41.33),
                 (120.0, -41.22), (120.5, -41.11), (121.0, -41.00), (121.5, -40.89),
                 (122.0, -40.78), (122.5, -40.71), (123.0, -40.67), (123.5, -40.67),
                 (124.0, -40.67), (124.5, -40.68), (125.0, -40.71), (125.5, -40.75),
                 (126.0, -40.79), (126.5, -40.83), (127.0, -40.87), (127.5, -40.91),
                 (128.0, -40.96), (128.5, -41.01), (129.0, -41.07), (129.5, -41.16),
                 (130.0, -41.26), (130.5, -41.31), (131.0, -41.33), (131.5, -41.35),
                 (132.0, -41.41), (132.5, -41.54), (133.0, -41.74), (133.5, -42.02),
                 (134.0, -42.33), (134.5, -42.67), (135.0, -43.04), (135.5, -43.48),
                 (136.0, -44.11), (136.5, -44.93), (137.0, -45.82), (137.5, -46.52),
                 (138.0, -46.89), (138.5, -47.00), (139.0, -47.00), (139.5, -47.00),
                 (140.0, -47.00), (140.5, -47.00), (141.0, -47.00))},
}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def coverage(cfg, fx, fy):
    """BUILD 780. How much of the steam stays at field (fx, fy), 0..1.

    Hardware 10-22 on 778's cut: "we need a smooth bottom to it, covering
    the curve ... instead you made it go up and inwards here instead of
    tracing out the smooth edge of the arc", and blue pixels along the cut.
    The cut is now the vent opening's own ellipse: below its long axis the
    steam is kept only inside the ellipse (grown by `grow`), so the bottom
    follows the rim's arc all the way round into its ends; above the axis
    nothing is cut. A `feather`-wide ramp softens both edges."""
    (cx, cy), (ra, rb), rot = cfg['ellipse']
    g, f = cfg['grow'], cfg['feather']
    t = np.radians(rot)
    dx, dy = np.asarray(fx, float) - cx, np.asarray(fy, float) - cy
    u = dx * np.cos(t) + dy * np.sin(t)          # along the long axis
    v = -dx * np.sin(t) + dy * np.cos(t)         # across it, + = down
    a, b = ra + g, rb + g
    r = np.sqrt((u / a) ** 2 + (v / b) ** 2)
    d_ell = (r - 1.0) * b                        # ~ distance past the rim
    keep_ell = np.clip(0.5 - d_ell / f, 0, 1)
    keep_up = np.clip(0.5 - v / f, 0, 1)         # above the long axis
    dark = cfg.get('dark')
    if dark:
        dxs = np.array([q[0] for q in dark])
        dys = np.array([q[1] for q in dark])
        fxa = np.asarray(fx, float)
        yd = np.interp(fxa, dxs, dys)
        keep_d = np.clip(0.5 - (np.asarray(fy, float) - yd) / f, 0, 1)
        keep_d = np.where((fxa < dxs[0]) | (fxa > dxs[-1]), 1.0, keep_d)
        keep_ell = np.minimum(keep_ell, keep_d)
    return np.maximum(keep_ell, keep_up)


def _565(v):
    v = v.astype(np.uint32)
    return np.stack([(v >> 11) & 31, (v >> 5) & 63, v & 31], -1)


def plan(name, sec9):
    """(new section 9, cells edited). Raises unless the field is as built."""
    import diag_common as DC
    import ff7nx_floordepth as FD
    import field_bg_native as FN
    cfg = FIELDS[name]
    pl, _ts, _te, px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pl if p is not None}
    pg = cfg['page']
    if pages.get(pg) is None or pages[pg].depth != 2 \
            or pages[pg].size_flag:
        raise ValueError('page %d is not a 16-unit truecolor page' % pg)
    k = px // 16
    users = {}
    steam = []
    for lay, _c, first, n in FD._rows(sec9):
        for i in range(n):
            o = first + i * REC
            r = sec9[o:o + REC]
            u, v = struct.unpack_from('<II', r, 42)
            page = r[34] if r[28] else r[32]
            key = (page, u // UV_CELL, v // UV_CELL)
            pos = (lay,) + struct.unpack_from('<hh', r, 2)
            users.setdefault(key, set()).add(pos)
            if lay == 2 and r[28] and r[34] == pg and r[26] == cfg['param']:
                steam.append((key, pos))
    if not steam:
        raise ValueError('no steam records')
    d = np.frombuffer(pages[pg].data, '<u2').reshape(px, px).copy()
    t = (np.arange(k) + 0.5) / (k / 16.0)
    done, edited = set(), 0
    for key, (_lay, x, y) in steam:
        if key in done:
            continue
        done.add(key)
        if len(users[key]) != 1:
            raise ValueError('steam cell %r is drawn at two places' % (key,))
        _p, cx, cy = key
        fx = x + t[None, :]
        fy = y + t[:, None]
        cov = coverage(cfg, fx, fy)
        if cov.min() >= 1:
            continue
        blk = d[cy * k:(cy + 1) * k, cx * k:(cx + 1) * k]
        c = _565(blk).astype(np.float32)
        # BUILD 780: the port reads a depth-2 texel's green as
        # (v & 0x07E0) >> 1, so an ODD 6-bit green spills into blue's top
        # bit -- the blue pixels 778 left along the cut. Scale the colour
        # as 5:5:5 and write green back with its low bit clear.
        r5, g5, b5 = c[..., 0], np.floor(c[..., 1] / 2), c[..., 2]
        r5, g5, b5 = (np.rint(ch * cov) for ch in (r5, g5, b5))
        new = ((r5.astype(np.uint32) << 11) | (g5.astype(np.uint32) << 6)
               | b5.astype(np.uint32)).astype(np.uint16)
        faint = np.maximum(np.maximum(r5, g5), b5) < 2
        new[(cov <= 0) | faint & (cov < 1)] = 0
        keep = cov >= 1
        new[keep] = blk[keep]
        new[blk == 0] = 0
        if not np.array_equal(new, blk):
            d[cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = new
            edited += 1
    if not edited:
        return sec9, 0
    plist, t0, t1 = FN.parse_texture_block(sec9, px)
    q = plist[pg]
    plist[pg] = FN.Page(pg, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(sec9, plist, t0, t1), edited


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': [], 'cells': 0}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in FIELDS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], n = plan(name, parts[8])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        if n:
            payloads[name] = encode(lgp.join_sections(parts))
            st['names'].append(name)
            st['cells'] += n
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  VENT CROP (BUILD 778): %s -- steam cut along the vent '
                   'rim, %d cells. %s=1 disables.'
                   % (', '.join(st['names']), st['cells'], OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! vent crop %s: %s' % (name, why))
    return '\n'.join(out)
