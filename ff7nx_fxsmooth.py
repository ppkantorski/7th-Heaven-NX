#!/usr/bin/env python3
"""ff7nx_fxsmooth.py -- anti-alias the outline of an additive FX layer.

BUILD 618r. del3 (Costa del Sol beach), hardware 10-03: "the water edge
looks kind of choppy / dirty".

MEASURED: the surf is additive FX (layer 4, pages 16-23). Cosmos drew it
with a BINARY alpha, so the foam is near-white right up to a 1-texel
staircase, and the 1024 -> 768 resample leaves a single row of half-lit
texels along it that the build's anti-banding dither turns into a dotted
fringe. On the sand the additive white saturates, so the staircase and the
dots are exactly what shows as a choppy, dirty line.

FIX: on every truecolor FX page, the band within BAND texels of the
boundary between the lit art and a LARGE unlit area (the effect's
outline, not the dark gaps of its pattern) is replaced by a gaussian-blurred copy of the cell (sigma SIGMA,
edges replicated, so cell borders inside the foam never darken). Only that
band changes, and it is re-quantised without dither. The foam's interior,
every other page, every record and the memory are untouched.

SEVENTH_NX_NO_FX_SMOOTH=1 disables.
"""
from __future__ import annotations

import os

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_FX_SMOOTH'
FIELDS = ('del3',)
SIGMA = 1.2
BAND = 2
LIT = 8                    # 0..255: below this a texel counts as unlit
HOLE = 21                  # unlit areas narrower than this are pattern


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _blur(img, sigma):
    from scipy.ndimage import gaussian_filter
    return np.stack([gaussian_filter(img[..., c], sigma, mode='nearest')
                     for c in range(img.shape[-1])], -1)


def _band(lit, n, hole):
    """Texels within `n` of the boundary between lit art and a LARGE unlit
    area (one that holds a hole x hole square): the outline of the effect,
    not the dark gaps inside its pattern."""
    from scipy.ndimage import binary_dilation, binary_erosion
    st = np.ones((3, 3), bool)
    sq = np.ones((hole, hole), bool)
    unlit = ~lit
    big = binary_dilation(binary_erosion(unlit, sq, border_value=1), sq)
    big &= unlit
    return ((binary_dilation(big, st, iterations=n) & lit)
            | (big & binary_dilation(lit, st, iterations=n)))


def smooth_page(buf, px, grid):
    """New 565 page (uint16 array) with the effect's outline smoothed, one
    cell at a time (neighbouring atlas cells are not neighbours on screen;
    a whole-page blur drew lines along the atlas rows). Cell edges are
    replicated, so the outline stays continuous across cells."""
    v = buf.astype(np.uint32)
    rgb = np.stack([((v >> 11) & 31) << 3, ((v >> 5) & 63) << 2,
                    (v & 31) << 3], -1).astype(np.float64)
    out = buf.copy()
    cs = px // grid
    changed = 0
    for cy in range(grid):
        for cx in range(grid):
            sl = (slice(cy * cs, (cy + 1) * cs), slice(cx * cs, (cx + 1) * cs))
            c = rgb[sl]
            lit = c.max(-1) >= LIT
            if lit.all() or not lit.any():
                continue
            band = _band(lit, BAND, HOLE)
            if not band.any():
                continue
            b = _blur(c, SIGMA)
            q = np.clip(np.floor(b / 8.0 + 0.5), 0, 31).astype(np.uint16)
            new = (q[..., 0] << 11) | ((q[..., 1] << 1) << 5) | q[..., 2]
            cell = out[sl].copy()
            cell[band] = new[band]
            out[sl] = cell
            changed += int(band.sum())
    return out, changed


def plan_field(sec9):
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_fxpages as FXP
    pl, t0, t1 = FN.parse_texture_block(sec9, DC.parse_pages(sec9)[3])
    px = DC.parse_pages(sec9)[3]
    total = 0
    slots = []
    for p in pl:
        if p is None or p.depth != 2 or not (FXP.FX_LO <= p.slot < FXP.FX_HI):
            continue
        grid = 8 if p.size_flag else 16
        a = np.frombuffer(p.data, '<u2').reshape(p.px, p.px)
        new, n = smooth_page(a, p.px, grid)
        if n:
            pl[p.slot] = FN.Page(p.slot, p.size_flag, 2,
                                 new.astype('<u2').tobytes(), p.px)
            total += n
            slots.append(p.slot)
    if not slots:
        raise ValueError('no truecolor FX page with an outline')
    return FN.replace_texture_block(sec9, pl, t0, t1), {
        'slots': slots, 'texels': total}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled():
        return total
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
            parts[8], info = plan_field(parts[8])
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %d outline texel(s) on FX pages %s'
                              % (name, info['texels'],
                                 ','.join(map(str, info['slots']))))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  FX SMOOTH (BUILD 618r): additive FX outlines '
                   'anti-aliased (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! fx smooth %s: %s' % (name, why))
    return '\n'.join(out)
