#!/usr/bin/env python3
"""ff7nx_edgesmooth.py -- cut-out edges redrawn as smooth curves at HD
resolution. BUILD 618z12.

ztruck (hardware 10-06): "its edges look super super jagged/rough" (the
scrolling rocks) and "the truck's edges could also afford to be less jagged
/ staircase like"; and a thin "hole" at the top of a rock.

WHY. A depth-2 texel is either a colour or 0 (transparent); there is no
alpha. Cosmos's cut-outs keep the 1x transparency: measured on the built
ztruck, 95 % of the 3x3 texel blocks on layer 4's silhouettes and 95 % on
layer 2's (the truck) are all-or-nothing, so every edge is a staircase of
whole field units -- 3 texels a step, ~4 px a step on screen once zoomed.
The rocks also carry a dark matte fringe on their tops. The hole is the
layer-4 period seam: the art's first two texel columns are transparent in
~25 rows where its last column is rock, so a 2-texel slit of sky shows at
every wrap.

WHAT. Per listed layer, the layer's static truecolor records are laid out
as one canvas (texels at 3 per unit), and:
  * seam (wrapped layers only): within 8 texels of the period seam a row's
    gap of at most 6 texels with art on both sides is filled;
  * the opacity is blurred (gaussian, SIGMA texels) and cut again at 0.5:
    the staircase becomes a curve at texel resolution, the silhouette
    otherwise where it was (thin parts thin a little, nothing moves);
  * texels the curve adds take the nearest original texel's colour;
    texels it removes become 0;
  * fringe (opt-in, layer 4): a texel within 2 of the new edge darker than
    FRINGE x the texel FRINGE_IN inside takes that inner texel's colour.
Wrapped layers are processed with the period wrapped round, so the seam
stays seamless. On the truck layer nothing changes where layer 1 is behind
(only the edge against the scenery behind the truck). Texels are only ever
copied from existing ones, so green stays even (field_bg_native) and none
becomes an accidental 0. A cell another record also uses is left alone.

Still binary: an edge against a SCROLLING layer cannot be pre-blended, and
the format has no alpha. The steps go from 3 texels to 1.

SEVENTH_NX_NO_EDGE_SMOOTH=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_EDGE_SMOOTH'
K = 3
SIGMA = 2.0
SEAM_BAND = 8
SEAM_GAP = 6
FRINGE = 0.55
FRINGE_BAND = 2
FRINGE_IN = 4
# field -> [(layer, wrap period or None, x0 of the period, fringe, guard)]
#   guard: the layers whose art behind this one may not be uncovered
#   fringe: None, or (band in texels, darker-than ratio)
TARGETS = {
    'ztruck': [(4, 352, -176, (FRINGE_BAND, FRINGE), ()),
               (2, None, None, (FRINGE_BAND, 0.9), (1,))],
}
# BUILD 618z13. Anti-aliasing for a layer whose camera is pinned:
#   ring  -- the silhouette's edge texels blended toward the MEAN of what
#            scrolls behind them (layers 3/4 averaged over their period at
#            the pinned camera), so a staircase becomes a gradient;
#   lines -- long dark painted lines inside the art (door / window / hood
#            outlines) redrawn anti-aliased: both sides are the art's own
#            texels, so this is exact.
# field -> {layer: options}; 'cam' is the pinned camera, 'period' the
# scrolling layers' period after ff7nx_hrepeat.
AA = {
    'ztruck': {'cam': (0, 16), 'period': 704, 'speeds': (5, 50),
               'layers': {2: ('ring', 'lines')}},
}
RING_LO = 0.25          # alpha at or below this: transparent (fainter
#                         texels are mostly the background mean, which
#                         is what mismatches a passing rock)
RING_HI = 0.94          # alpha at or above this: left as the art
RING_SIGMA = 3.0        # texels: averages out the 3-texel steps
RING_STEEP = 1.6        # ramp ~2.5 texels wide
RING_THIN = 4.5         # parts thinner than this keep the SIGMA coverage
LINE_HAT = 40           # black-hat depth (0..255) of a painted line
LINE_SIZE = 9           # its closing window (texels)
LINE_EXTENT = 60        # a line is at least this long (texels)
LINE_MIN = 80           # and this many texels
LINE_SIGMA = 1.0



def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _lum(t):
    t = t.astype(np.int64)
    return (((t >> 11) & 31) * 3 + ((t >> 6) & 31) * 6 + (t & 31)) / 10.0


def _records(s9, ts, pm, layer, static=True):
    import diag_common as DC
    out = []
    for lay, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        if lay != layer:
            continue
        for o in offs:
            if static and (s9[o + 28] or s9[o + 26]):
                continue
            out.append(o)
    return out


def _canvas(s9, recs, pm, px, xr=None):
    """The layer's composite (texels at 3 per unit). Stacked tiles (several
    depths at one place, the truck's cab) are composited the way the
    engine draws layer 2: the smallest z on top. Returns (tex, own, use,
    origin, at) -- own[y, x] = the index into `use` of the record whose
    texel shows there (-1 where nothing does), at[(x, y)] = its records."""
    k = px // 16
    use = []
    for o in recs:
        if pm[s9[o + 32]].depth != 2:
            continue
        x, y = struct.unpack_from('<hh', s9, o + 2)
        if xr and not xr[0] <= x < xr[1]:
            continue
        n = struct.unpack_from('<H', s9, o + 18)[0] or 16
        use.append((x, y, n, o))
    if not use:
        raise ValueError('no truecolor records')
    x0 = min(u[0] for u in use) if not xr else xr[0]
    y0 = min(u[1] for u in use)
    x1 = max(u[0] + u[2] for u in use) if not xr else xr[1]
    y1 = max(u[1] + u[2] for u in use)
    tex = np.zeros(((y1 - y0) * K, (x1 - x0) * K), np.uint16)
    own = -np.ones(tex.shape, np.int64)
    at = collections.defaultdict(list)
    # bottom first: the largest z, so the smallest ends on top
    order = sorted(range(len(use)), key=lambda i: -struct.unpack_from(
        '<I', s9, use[i][3] + 38)[0])
    for i in order:
        x, y, n, o = use[i]
        sx, sy = struct.unpack_from('<hh', s9, o + 10)
        d = np.frombuffer(pm[s9[o + 32]].data, '<u2').reshape(px, px)
        Y, X, m = (y - y0) * K, (x - x0) * K, n * K
        blk = d[sy // 16 * k:sy // 16 * k + m, sx // 16 * k:sx // 16 * k + m]
        hit = blk != 0
        tex[Y:Y + m, X:X + m][hit] = blk[hit]
        own[Y:Y + m, X:X + m][hit] = i
        at[(x, y)].append(i)
    return tex, own, use, (x0, y0), at


def _here(at, use, fx, fy):
    """Records whose tile covers field unit (fx, fy)."""
    out = []
    for (qx, qy), lst in at.items():
        n = use[lst[0]][2]
        if qx <= fx < qx + n and qy <= fy < qy + n:
            out += lst
    return out


def _cover(s9, recs, pm, px, org, shape):
    """Where the guard layers draw anything (paletted records: their whole
    rectangle, to be safe)."""
    k = px // 16
    cov = np.zeros(shape, bool)
    for o in recs:
        x, y = struct.unpack_from('<hh', s9, o + 2)
        n = struct.unpack_from('<H', s9, o + 18)[0] or 16
        Y, X, m = (y - org[1]) * K, (x - org[0]) * K, n * K
        y_a, x_a = max(Y, 0), max(X, 0)
        y_b, x_b = min(Y + m, shape[0]), min(X + m, shape[1])
        if y_a >= y_b or x_a >= x_b:
            continue
        p = pm[s9[o + 32]]
        if p.depth != 2:
            cov[y_a:y_b, x_a:x_b] = True
            continue
        sx, sy = struct.unpack_from('<hh', s9, o + 10)
        d = np.frombuffer(p.data, '<u2').reshape(px, px)[
            sy // 16 * k:sy // 16 * k + m, sx // 16 * k:sx // 16 * k + m]
        cov[y_a:y_b, x_a:x_b] |= d[y_a - Y:y_b - Y, x_a - X:x_b - X] != 0
    return cov


def smooth(tex, wrap=False, fringe=False, frozen=None):
    """New texels for one canvas."""
    from scipy import ndimage as ND
    pad = 24 if wrap else 0
    t = np.concatenate([tex[:, -pad:], tex, tex[:, :pad]], 1) if pad else tex
    m = t != 0
    stats = {'seam': 0}
    if wrap:
        W = tex.shape[1]
        for sx in (pad, pad + W):                  # the seam, both copies
            lo, hi = sx - SEAM_BAND, sx + SEAM_BAND
            for r in range(m.shape[0]):
                row = m[r]
                j = lo
                while j < hi:
                    if not row[j] and row[j - 1]:
                        e = j
                        while e < hi + SEAM_GAP and not row[e]:
                            e += 1
                        if e - j <= SEAM_GAP and row[e]:
                            row[j:e] = True
                            stats['seam'] += e - j
                        j = e
                    j += 1
    a = ND.gaussian_filter(m.astype(np.float32), SIGMA, mode='nearest')
    m2 = a > 0.5
    if frozen is not None:
        fz = np.concatenate([frozen[:, -pad:], frozen, frozen[:, :pad]], 1) \
            if pad else frozen
        m2 = np.where(fz, t != 0, m2)
    src = t != 0
    _d, (iy, ix) = ND.distance_transform_edt(~src, return_indices=True)
    out = np.where(m2, t, 0).astype(np.uint16)
    add = m2 & ~src
    out[add] = t[iy[add], ix[add]]
    if fringe:
        din = ND.distance_transform_edt(m2)
        band = m2 & (din <= fringe[0])
        inner = din >= FRINGE_IN
        _d2, (jy, jx) = ND.distance_transform_edt(~inner, return_indices=True)
        cand = band & inner[jy, jx]
        ref = out[jy, jx]
        dark = cand & (_lum(out) < fringe[1] * _lum(ref))
        if frozen is not None:
            dark &= ~fz
        out[dark] = ref[dark]
        stats['fringe'] = int(dark[:, pad:pad + tex.shape[1]].sum()
                              if pad else dark.sum())
    if pad:
        out = out[:, pad:pad + tex.shape[1]]
        add = add[:, pad:pad + tex.shape[1]]
    stats['added'] = int(add.sum())
    stats['removed'] = int(((tex != 0) & (out == 0)).sum())
    return out, stats


def _rgbf(t):
    t = t.astype(np.int64)
    return np.stack([((t >> 11) & 31) << 3, ((t >> 6) & 31) << 3,
                     (t & 31) << 3], -1).astype(np.float32)


def _enc(c):
    """RGB (0..255) -> 565 with green's low bit clear, never 0."""
    q = np.clip(np.rint(c / 8.0), 0, 31).astype(np.int64)
    v = (q[..., 0] << 11) | (q[..., 1] << 6) | q[..., 2]
    return np.where(v == 0, 1, v).astype(np.uint16)


def background_mean(parts, cam, period, n=32):
    """(mean RGB HxWx3, valid HxW) of layers 3/4 at the pinned camera, in
    the K=3 screen raster, averaged over the scroll period. Layer 4 is in
    front of layer 3 and the two scroll independently, so the mean is
    cov4 * mean4 + (1 - cov4) * mean3."""
    import ff7nx_framesim as FS
    F = FS.Field(parts)
    acc = {}
    for lay in (3, 4):
        s = c = None
        for i in range(n):
            off = (i * period // n, 0)
            img = F.render(cam=cam, K=K, states={}, mark=True,
                           layers=(lay,), scroll3=off, scroll4=off)
            img = img.astype(np.float32)
            drawn = ~((img[..., 0] == 255) & (img[..., 1] == 0)
                      & (img[..., 2] == 255))
            if s is None:
                s = np.zeros(img.shape, np.float32)
                c = np.zeros(drawn.shape, np.float32)
            s += img * drawn[..., None]
            c += drawn
        acc[lay] = (s, c)
    s3, c3 = acc[3]
    s4, c4 = acc[4]
    m3 = s3 / np.maximum(c3, 1)[..., None]
    m4 = s4 / np.maximum(c4, 1)[..., None]
    cov4 = (c4 / n)[..., None]
    mean = cov4 * m4 + (1 - cov4) * m3
    valid = (c3 >= n) | (c4 >= n)
    return mean, valid


def aa_ring(tex, out, frozen, bg, valid):
    """The silhouette as a smooth alpha ramp, each edge texel blended toward
    the background mean by its alpha.

    The alpha is the cut layer's opacity blurred at RING_SIGMA (3 texels:
    wide enough to average out the art's 3-texel steps) and steepened to a
    ~2.5-texel ramp. Thin parts (the mirror arms, under RING_THIN texels
    thick) would fade at that width, so near them the narrower SIGMA
    coverage is used instead. Texels the ramp puts outside (alpha ~0) are
    cleared; the ones it puts on the edge are blended."""
    from scipy import ndimage as ND
    m2 = out != 0
    g = lambda x, s_: ND.gaussian_filter(x.astype(np.float32), s_,
                                         mode='nearest')
    a3 = np.clip((g(m2, RING_SIGMA) - 0.5) * RING_STEEP + 0.5, 0, 1)
    a2 = g(tex != 0, SIGMA)
    thick = ND.grey_dilation(ND.distance_transform_edt(m2), size=(11, 11))
    alpha = np.where(thick < RING_THIN, a2, a3)
    _d, (iy, ix) = ND.distance_transform_edt(~m2, return_indices=True)
    T = _rgbf(out)[iy, ix]
    ok = valid.copy()
    if frozen is not None:
        ok &= ~frozen
    near = ND.binary_dilation(m2 ^ ND.binary_erosion(m2), iterations=4)
    ok &= near
    blend = ok & (alpha > RING_LO) & (alpha < RING_HI)
    clear = ok & m2 & (alpha <= RING_LO)
    col = alpha[..., None] * T + (1 - alpha[..., None]) * bg
    res = out.copy()
    res[blend] = _enc(col[blend])
    res[clear] = 0
    return res, {'ring': int((blend & ~m2).sum()),
                 'ring_in': int((blend & m2).sum()),
                 'ring_cleared': int(clear.sum())}


def aa_lines(out, frozen):
    """Long dark painted lines inside the art, anti-aliased."""
    from scipy import ndimage as ND
    C = _rgbf(out)
    m = out != 0
    lum = C.mean(-1)
    din = ND.distance_transform_edt(m)
    bh = ND.grey_closing(lum, size=(LINE_SIZE, LINE_SIZE)) - lum
    L = (bh > LINE_HAT) & (din > 4)
    lab, n = ND.label(L, structure=np.ones((3, 3)))
    keep = np.zeros(n + 1, bool)
    sizes = ND.sum(np.ones(L.shape), lab, range(1, n + 1))
    for i, sl in enumerate(ND.find_objects(lab)):
        ext = max(sl[0].stop - sl[0].start, sl[1].stop - sl[1].start)
        if ext >= LINE_EXTENT and sizes[i] >= LINE_MIN:
            keep[i + 1] = True
    L = keep[lab]
    if not L.any():
        return out, {'lines': 0}
    W = (m & ~L).astype(np.float32)
    g = lambda x, s_: ND.gaussian_filter(x, s_, mode='nearest')
    fill = np.stack([g(C[..., c] * W, 2.0) for c in range(3)], -1) / \
        np.maximum(g(W, 2.0), 1e-4)[..., None]
    Lf = L.astype(np.float32)
    lcol = np.stack([g(C[..., c] * Lf, 1.5) for c in range(3)], -1) / \
        np.maximum(g(Lf, 1.5), 1e-4)[..., None]
    alpha = np.clip((g(Lf, LINE_SIGMA) - 0.15) / 0.6, 0, 1)[..., None]
    region = ND.binary_dilation(L, iterations=3) & m & (din > 2)
    if frozen is not None:
        region &= ~frozen
    new = alpha * lcol + (1 - alpha) * fill
    res = out.copy()
    res[region] = _enc(new[region])
    return res, {'lines': int(region.sum()), 'line_comps': int(keep.sum())}


def plan(name, sec9, parts=None):
    """(new section 9, info) -- raises on any doubt."""
    import diag_common as DC
    import field_bg_native as FN
    s9 = bytes(sec9)
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    if px // 16 != 16 * K:
        raise ValueError('pages are %d px' % px)
    k = px // 16
    # who uses each cell: a wrapped layer's repeat copies (x one period
    # apart, same cell) count once
    wrapped = {l_: (per, x0) for l_, per, x0, _f, _g in TARGETS[name] if per}
    users = collections.defaultdict(set)
    for lay, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            x, y = struct.unpack_from('<hh', s9, o + 2)
            if lay in wrapped:
                x = (x - wrapped[lay][1]) % wrapped[lay][0]
            n = struct.unpack_from('<H', s9, o + 18)[0] or 16
            for po, so in ((32, 10), (34, 14)):
                if po == 34 and not s9[o + 28]:
                    continue
                sx, sy = struct.unpack_from('<hh', s9, o + so)
                for cy in range(sy // 16, (sy + n - 1) // 16 + 1):
                    for cx in range(sx // 16, (sx + n - 1) // 16 + 1):
                        users[(s9[o + po], cx, cy)].add((lay, x, y, o if
                                                         lay not in wrapped
                                                         else 0))
    datas = {}
    info = {}
    for layer, period, x0, fringe, guard in TARGETS[name]:
        recs = _records(s9, ts, pm, layer)
        xr = (x0, x0 + period) if period else None
        tex, own, use, org, at = _canvas(s9, recs, pm, px, xr)
        frozen = None
        if guard:
            g = []
            for gl in guard:
                g += _records(s9, ts, pm, gl, static=False)
            frozen = _cover(s9, g, pm, px, org, tex.shape)
        if period and tex.shape[1] != period * K:
            raise ValueError('layer %d canvas is not one period' % layer)
        out, st = smooth(tex, wrap=bool(period), fringe=fringe,
                         frozen=frozen)
        opts = AA.get(name, {}).get('layers', {}).get(layer, ())
        if 'lines' in opts:
            out, s_ = aa_lines(out, frozen)
            st.update(s_)
        if 'ring' in opts and parts is not None:
            cfg = AA[name]
            mean, valid = background_mean(parts, cfg['cam'], cfg['period'])
            # canvas texel (py, px) -> K=3 screen raster
            r0 = (org[1] - cfg['cam'][1] + 120) * K
            c0 = (org[0] - cfg['cam'][0]) * K + int(213.5 * K)
            H_, W_ = tex.shape
            bg = np.zeros((H_, W_, 3), np.float32)
            ok = np.zeros((H_, W_), bool)
            ys0, ys1 = max(0, -r0), min(H_, mean.shape[0] - r0)
            xs0, xs1 = max(0, -c0), min(W_, mean.shape[1] - c0)
            if ys0 < ys1 and xs0 < xs1:
                bg[ys0:ys1, xs0:xs1] = mean[r0 + ys0:r0 + ys1,
                                            c0 + xs0:c0 + xs1]
                ok[ys0:ys1, xs0:xs1] = valid[r0 + ys0:r0 + ys1,
                                             c0 + xs0:c0 + xs1]
            out, s_ = aa_ring(tex, out, frozen, bg, ok)
            st.update(s_)
        from scipy import ndimage as ND
        _d, (iy, ix) = ND.distance_transform_edt(tex == 0,
                                                 return_indices=True)
        z = [struct.unpack_from('<I', s9, u[3] + 38)[0] for u in use]
        blocks = {}

        def block(i):
            if i not in blocks:
                x, y, n, o = use[i]
                sx, sy = struct.unpack_from('<hh', s9, o + 10)
                blocks[i] = np.frombuffer(pm[s9[o + 32]].data, '<u2').reshape(
                    px, px)[sy // 16 * k:sy // 16 * k + n * K,
                            sx // 16 * k:sx // 16 * k + n * K].copy()
            return blocks[i]
        ys, xs = np.nonzero(out != tex)
        for py, px_ in zip(ys.tolist(), xs.tolist()):
            fx, fy = org[0] + px_ // K, org[1] + py // K
            v = int(out[py, px_])
            if v == 0:
                for i in _here(at, use, fx, fy):
                    b_ = block(i)
                    b_[py - (use[i][1] - org[1]) * K,
                       px_ - (use[i][0] - org[0]) * K] = 0
                continue
            if own[py, px_] >= 0:
                i = int(own[py, px_])
            else:
                here = _here(at, use, fx, fy)
                if not here:
                    continue
                src = int(own[iy[py, px_], ix[py, px_]])
                same = [i for i in here if src >= 0 and z[i] == z[src]]
                i = same[0] if same else min(here, key=lambda j: z[j])
            b_ = block(i)
            b_[py - (use[i][1] - org[1]) * K,
               px_ - (use[i][0] - org[0]) * K] = v
        skipped = 0
        cells = set()
        for i, b_ in sorted(blocks.items()):
            x, y, n, o = use[i]
            pg = s9[o + 32]
            sx, sy = struct.unpack_from('<hh', s9, o + 10)
            mine = n // 16
            if any(len(users[(pg, cx, cy)]) > 1
                   for cy in range(sy // 16, sy // 16 + mine)
                   for cx in range(sx // 16, sx // 16 + mine)):
                skipped += 1
                continue
            if pg not in datas:
                datas[pg] = np.frombuffer(pm[pg].data, '<u2').reshape(
                    px, px).copy()
            datas[pg][sy // 16 * k:sy // 16 * k + n * K,
                      sx // 16 * k:sx // 16 * k + n * K] = b_
            cells.add((pg, sx, sy))
        st['skipped_shared'] = skipped
        st['records'] = len(cells)
        info[layer] = st
    if not datas:
        raise ValueError('nothing to change')
    plist, t0, t1 = FN.parse_texture_block(s9, px)
    for pg, d in datas.items():
        q = plist[pg]
        plist[pg] = FN.Page(pg, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(s9, plist, t0, t1), info


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in TARGETS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], info = plan(name, parts[8], parts)
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s: %s' % (name, ', '.join(
            'layer %d +%d/-%d texels%s%s%s%s' % (
                l_, v['added'], v['removed'],
                ', seam %d' % v['seam'] if v.get('seam') else '',
                ', fringe %d' % v['fringe'] if v.get('fringe') else '',
                ', AA ring %d+%d' % (v['ring'], v['ring_in'])
                if v.get('ring') else '',
                ', AA lines %d' % v['lines'] if v.get('lines') else '')
            for l_, v in info.items())))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  EDGE SMOOTH (BUILD 618z13): cut-out staircases redrawn '
                   'as curves at HD resolution (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! edge smooth %s: not applied -- %s' % (name, why))
    return '\n'.join(out)
