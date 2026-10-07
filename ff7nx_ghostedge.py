#!/usr/bin/env python3
"""ff7nx_ghostedge.py -- clean layer 1's pale ghost of layer 2's cut-out edge.
BUILD 618z.

trnad_4, hardware 10-05: "dirty pixels behind the fx layer" -- pale specks
and a thin pale line in the gaps between the rock pillars, under the green
lifestream.

CAUSE. Cosmos's layer-1 picture still carries the rocks (a slightly
different, upscaled silhouette), while layer 2 draws the rocks cut out.
In the gaps, layer 1 shows through, and the old silhouette's pale rim and
the odd bright speck sit just outside layer 2's edge.

FIX (FIELDS only; truecolor pages; static records; exclusive layer-1
cells): isolated pale specks (<= SPECK texels, PALE over the local
visible colour) in the visible gaps take that colour; and in the ring of
layer-1 texels within RING texels OUTSIDE layer 2's
opaque footprint, a texel brighter than the local gap colour (the mean of
the gap texels farther out, in a WIN window) by more than PALE takes that
local gap colour. Darker texels and everything else are untouched, and
nothing under layer 2 changes.

SEVENTH_NX_NO_GHOST_EDGE=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_GHOST_EDGE'
FIELDS = ('trnad_4',)
RING = 5                 # texels outside layer 2's edge (3 per field unit)
WIN = 21                 # gap-colour window
PALE = 10.0              # brighter than the gap by this much (0..255 mean)
SPECK = 12               # texels: an isolated pale island (1-2 native px)
SPECK_NEAR = 24          # ...within this many texels of layer 2's art


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _rgb(v):
    v = v.astype(np.uint32)
    return np.stack([((v >> 11) & 31) << 3, ((v >> 6) & 31) << 3,
                     (v & 31) << 3], -1).astype(np.float32)


def _565(rgb):
    q = np.clip(np.floor(rgb / 8.0 + 0.5), 0, 31).astype(np.uint32)
    v = (q[..., 0] << 11 | (q[..., 1] << 1) << 5 | q[..., 2]).astype(
        np.uint16)
    return np.where(v == 0, 0x0841, v).astype(np.uint16)


def plan_field(sec9, vparts=None):
    from scipy import ndimage as ND
    import diag_common as DC
    import field_bg_native as FN
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    uses = collections.Counter()
    l1, l2 = [], []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            pg = sec9[o + 34] if bl else sec9[o + 32]
            sx, sy = struct.unpack_from('<hh', sec9, o + (14 if bl else 10))
            uses[(pg, sx // 16, sy // 16)] += 1
            p = pm.get(pg)
            if (bl or sec9[o + 26] or p is None or p.depth != 2
                    or p.size_flag or layer not in (1, 2)):
                continue
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            (l1 if layer == 1 else l2).append((pg, sx // 16, sy // 16, x, y))
    if not l1 or not l2:
        raise ValueError('needs truecolor layers 1 and 2')
    allr = l1 + l2
    x0 = min(r[3] for r in allr)
    y0 = min(r[4] for r in allr)
    W = (max(r[3] for r in allr) + 16 - x0) * k // 16
    H = (max(r[4] for r in allr) + 16 - y0) * k // 16
    pages = {s: np.frombuffer(pm[s].data, '<u2').reshape(px, px)
             for s in {r[0] for r in allr}}
    c1 = np.zeros((H, W), np.uint16)
    have = np.zeros((H, W), bool)
    own = np.zeros((H, W), bool)
    for pg, cx, cy, x, y in l1:
        X, Y = (x - x0) * k // 16, (y - y0) * k // 16
        c1[Y:Y + k, X:X + k] = pages[pg][cy * k:(cy + 1) * k,
                                         cx * k:(cx + 1) * k]
        have[Y:Y + k, X:X + k] = True
        if uses[(pg, cx, cy)] == 1:
            own[Y:Y + k, X:X + k] = True
    cov = np.zeros((H, W), bool)
    for pg, cx, cy, x, y in l2:
        X, Y = (x - x0) * k // 16, (y - y0) * k // 16
        cov[Y:Y + k, X:X + k] |= pages[pg][cy * k:(cy + 1) * k,
                                           cx * k:(cx + 1) * k] != 0
    # vanilla's layer 1 at the same place (1x, upsampled): a pale texel the
    # 1997 picture also has (a waterfall streak beside a rock) is art
    vl = None
    if vparts is not None:
        import ff7nx_marginblack as MB
        vs9 = vparts[8]
        vpl, vts, _vte, _vpx = DC.parse_pages(vs9)
        vpm = {p.slot: p for p in vpl if p is not None}
        cols, _h, npg, cpp = MB.palette_colours(vparts[3])
        pal = cols.astype(np.int64).reshape(npg, cpp)
        vl = np.full((H, W), -1.0, np.float32)
        vcov = np.zeros((H, W), bool)
        f = k // 16
        for layer, offs in DC.walk_layers(vs9, vs9.find(b'BACK'), vts):
            if layer not in (1, 2):
                continue
            for o in offs:
                if layer == 2:
                    if vs9[o + 28] or vs9[o + 26]:
                        continue
                    x, y = struct.unpack_from('<hh', vs9, o + 2)
                    X, Y = (x - x0) * k // 16, (y - y0) * k // 16
                    p = vpm.get(vs9[o + 32])
                    if (p is None or p.depth != 1 or X < 0 or Y < 0
                            or X + k > W or Y + k > H):
                        continue
                    sx, sy = struct.unpack_from('<hh', vs9, o + 10)
                    a_ = np.frombuffer(p.data, np.uint8, count=65536).reshape(
                        256, 256)[sy:sy + 16, sx:sx + 16]
                    q = min(vs9[o + 22], npg - 1)
                    m_ = (pal[q][a_] & 0x7FFF) != 0
                    vcov[Y:Y + k, X:X + k] |= np.repeat(np.repeat(m_, f, 0),
                                                        f, 1)
                    continue
                x, y = struct.unpack_from('<hh', vs9, o + 2)
                X, Y = (x - x0) * k // 16, (y - y0) * k // 16
                if X < 0 or Y < 0 or X + k > W or Y + k > H:
                    continue
                p = vpm.get(vs9[o + 32])
                if p is None:
                    continue
                sx, sy = struct.unpack_from('<hh', vs9, o + 10)
                if p.depth == 1:
                    a_ = np.frombuffer(p.data, np.uint8, count=65536).reshape(
                        256, 256)[sy:sy + 16, sx:sx + 16]
                    q = min(vs9[o + 22], npg - 1)
                    c = pal[q][a_]
                    lv = np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31],
                                  -1).mean(-1) * 8.0
                else:
                    a_ = np.frombuffer(p.data, '<u2').reshape(256, 256)[
                        sy:sy + 16, sx:sx + 16]
                    lv = _rgb(a_).mean(-1)
                vl[Y:Y + k, X:X + k] = np.repeat(np.repeat(lv, f, 0), f, 1)
    near = ND.binary_dilation(cov, np.ones((3, 3), bool), iterations=RING)
    ring = near & ~cov & have & own
    gap = have & ~near & ~cov
    rgb = _rgb(c1)
    lum = rgb.mean(-1)
    g = gap.astype(np.float32)
    den = ND.uniform_filter(g, WIN)
    ref = np.stack([ND.uniform_filter(rgb[..., i] * g, WIN) for i in range(3)],
                   -1) / np.maximum(den, 1e-6)[..., None]
    fix = ring & (den > 0.01) & (lum > ref.mean(-1) + PALE)
    # isolated pale specks anywhere in the visible gaps (the two dots under
    # the lifestream): small bright islands against the local gap colour
    vis = have & own & ~cov
    v = vis.astype(np.float32)
    vden = ND.uniform_filter(v, WIN)
    vref = np.stack([ND.uniform_filter(rgb[..., i] * v, WIN)
                     for i in range(3)], -1) / np.maximum(vden, 1e-6)[..., None]
    # only near the rocks: the sky's mist streaks are fine art of their own
    close = ND.binary_dilation(cov, np.ones((3, 3), bool), iterations=SPECK_NEAR)
    pale = vis & close & (vden > 0.05) & (lum > vref.mean(-1) + PALE)
    lab, nl = ND.label(pale, np.ones((3, 3), bool))
    if nl:
        sizes = ND.sum(pale, lab, range(1, nl + 1))
        small = np.isin(lab, [i + 1 for i, z in enumerate(sizes)
                              if z <= SPECK])
        ref = np.where((small & ~fix)[..., None], vref, ref)
        fix |= small
        specks = small
    else:
        specks = np.zeros_like(fix)
    if vl is not None:
        # keep what vanilla also paints pale (within a 3x3 1x neighbourhood)
        # ...unless vanilla's own layer 2 covered it there: then that texel
        # was never seen in 1997, and Cosmos's narrower cut-out exposes a
        # fragment of the rock behind (trnad_4's pale rock tip)
        vmax = ND.maximum_filter(vl, size=3 * (k // 16) + 1)
        hidden97 = ND.binary_dilation(vcov, np.ones((3, 3), bool))
        fix &= ((vl >= 0) & ((lum > vmax + PALE) | hidden97)) | specks
    if not fix.any():
        raise ValueError('no pale ghost texels')
    new = c1.copy()
    new[fix] = _565(ref[fix])
    data = {}
    for pg, cx, cy, x, y in l1:
        if uses[(pg, cx, cy)] != 1:
            continue
        X, Y = (x - x0) * k // 16, (y - y0) * k // 16
        f = fix[Y:Y + k, X:X + k]
        if not f.any():
            continue
        if pg not in data:
            data[pg] = pages[pg].copy()
        data[pg][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = new[Y:Y + k,
                                                                 X:X + k]
    plist, t0, t1 = FN.parse_texture_block(sec9, px)
    for s, d in data.items():
        q = plist[s]
        plist[s] = FN.Page(s, q.size_flag, 2, d.astype('<u2').tobytes(), px)
    return FN.replace_texture_block(sec9, plist, t0, t1), int(fix.sum())


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
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
            vparts = list(lgp.split_sections(archive.decompressed(entry)))
            parts[8], n = plan_field(parts[8], vparts)
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s: %d texel(s)' % (name, n))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  GHOST EDGE (BUILD 618z): the pale rim of the old '
                   'rock silhouette cleaned in the gaps beside layer 2 (%s). '
                   '%s=1 disables.' % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! ghost edge %s: %s' % (name, why))
    return '\n'.join(out)
