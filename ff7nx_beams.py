#!/usr/bin/env python3
"""ff7nx_beams.py -- the Honey Bee Inn room's laser cones, redrawn and carried
past the 4:3 edge.

BUILD 617c. colne_6 (the "&" room) has three projector beams: static
additive layer-2 sheets (mode 1, param 0) on the 256px PALETTED page 15, one
palette each (5 and 10 yellow-green, 9 magenta). The 1997 art stops dead at
x = -160 / +160, and Cosmos ships nothing for page 15 (its DDS is empty), so
in 16:9 the beams end in a vertical cut (hardware report). User direction:
they are cones and should simply keep going, straight, to the screen edges;
and the layer should be higher quality.

WHAT IS MEASURED, PER CONE (all from the field's own page and palette):
  * its two edges -- the half-maximum boundary, column by column -- are
    straight lines (mean residual 0.4-0.8 texel); their intersection is the
    projector (apex);
  * its level: the 75th percentile of the trusted (away from tip and cut)
    along-beam gain, times the cone's own colour.
HOW IT IS DRAWN: constant along the beam (bar a TIP_R-unit ramp off the
projector); ACROSS it a Gaussian in the beam's own angle, FWHM = the
measured width, its wings let down to zero by TAIL sigmas.
THE STEPS (review history: dither = grain, ramp = contours, flat = no fade,
Gaussian at 5 bits = hard bands). A 16-bit page has 5 bits a channel, so the
yellow beam (~52/255) has only ~6 levels above black. DEFAULT (mode 3):
blend mode 3 is dst + src/4, stock FF7 for a depth-1 page, so each palette
step adds ~2/255 -- ~25 levels for the yellow fade. Page 15 stays the stock
256px paletted page (1x texels, the cones box-filtered from 4x), palettes
5/9/10 become 31-level ramps of their cone colour, and every tile whose
light fits 31 quarter-levels is mode 3; only the magenta core (~110/255) is
too bright and stays mode 1 (two stacked mode-3 records would break the
256-record cap). ONE LAYER, PREMIXED (617e): all cones are summed into
one light field before it is cut into tiles, one record per position, so
crossing beams add and nothing is hidden (617c's record-per-cone lost the
upper beam inside the lower one's faint wing on hardware: a stepped black
wedge; 617d's partial merge disagreed at tile edges: blocks). Shape as
617c (wings to 1.7 sigma). Mixed colours are exact 5-bit qa*Y + qb*M in the
free palette indices. 617f: every lit palette channel is stored one
5-bit level up -- measured on hardware, the port shows a blended tile's
palette colour one level darker per channel (see enc() below); that offset
was the triangular gap and the rectangular edge. Cells de-duplicated. SEVENTH_NX_BEAMS_MODE=1 selects the 3x truecolor path below.

WHAT CHANGES IN THE FILE (SEVENTH_NX_BEAMS_MODE=1 path):
  * page 15 becomes a 768px truecolor page in the native additive band
    (the FINDINGS-194 ladder draws a depth-2 page there additively, as
    every fxpages page already does); every cell is re-rendered from the
    models;
  * records are added only where a cone has light and NO beam record exists
    yet (cones overlapping at one position share one tile, summed -- additive
    light adds); each is a copy of a beam record with only its position,
    cell and UV changed. All beam records are z 10000, so order cannot move.
  * hard limits: page 15 has 256 cells, and texture id 0 -- which every beam
    record carries -- may hold at most 256 records (FINDINGS-110); the pass
    refuses rather than exceed either.

Anything not matching the measured signature leaves the field byte-identical.
SEVENTH_NX_NO_BEAMS=1 disables.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN

OFF_ENV = 'SEVENTH_NX_NO_BEAMS'
UV_CELL = 625000                 # UV_SCALE / 16
TILE = 16
TARGETS = {'colne_6': {'page': 15, 'palettes': (5, 9, 10), 'records': 168,
                       'box': (-224, -160, 224, 160)}}
LIGHT_MIN = 4.0                  # 0..255 max channel: below one 565 step
MAX_RECORDS_PER_PAGE = 256
MAX_FIT_RESID = 1.5
G_CAP = 1.05
FLAT = os.environ.get('SEVENTH_NX_BEAMS_RAMP', '') != '1'
TIP_R = 24.0
TAIL = 1.7                       # sigmas: where the Gaussian wings end
TAIL_W = 0.7                     # sigmas: the wings' let-down to zero
EDGE_W = 4.0                     # units: the soft edge, constant along the beam


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _pack(rgb):
    """5-bit channels, rounded. 617c tried a 4x4 ordered dither against the
    banding of these dim beams; on hardware-scale it read as a dot grain
    (user: "should look smooth, not grainy"), so the cones are rounded
    plainly -- the model itself is smooth, the steps are one 5-bit level."""
    # each channel rounds on its own: for these yellow/magenta beams the
    # two lit channels step at slightly different places, which doubles the
    # visible levels across a fade (tried locking them together -- fewer,
    # harder steps)
    # one level for the whole colour: the brightest channel is rounded and
    # the others follow it in proportion, so every step of the fade is a
    # step in brightness only -- rounding the channels separately put
    # green/red-tinted stripes between the steps
    rgb = np.asarray(rgb, np.float64)
    mx = rgb.max(-1, keepdims=True)
    qm = np.clip(np.rint(mx * 31.0 / 255.0), 0, 31)
    q = np.clip(np.rint(qm * rgb / np.maximum(mx, 1e-6)), 0, 31).astype(
        np.int32)
    return ((q[..., 0] << 11) | ((q[..., 1] << 1) << 5) | q[..., 2]).astype(
        np.uint16)


# ------------------------------------------------------------- the cones
def _edges(lum, x0, y0):
    X, T, B = [], [], []
    for c in range(lum.shape[1]):
        col = lum[:, c]
        mx = col.max()
        if mx < 12:
            continue
        idx = np.nonzero(col >= 0.5 * mx)[0]
        if idx.max() + 1 - idx.min() < 3:
            continue
        X.append(c + x0 + 0.5)
        T.append(idx.min() + y0)
        B.append(idx.max() + 1 + y0)
    return np.array(X), np.array(T, float), np.array(B, float)


def _polar(m, xs, ys):
    ax, ay = m['apex']
    dx, dy = xs - ax, ys - ay
    if m['left']:
        dx = -dx
    r = np.hypot(dx, dy)
    th = np.arctan2(dy, dx)
    st = -m['pt'][0] if m['left'] else m['pt'][0]
    sb = -m['pb'][0] if m['left'] else m['pb'][0]
    tt, tb = np.arctan(st), np.arctan(sb)
    return r, (th - tt) / (tb - tt)


def fit_cone(light, x0, y0):
    """The cone model from one cone's canvas (native texels), or raise."""
    lum = light.max(-1)
    X, T, B = _edges(lum, x0, y0)
    if len(X) < 24:
        raise ValueError('too few beam columns')
    pt = np.polyfit(X, T, 1)
    pb = np.polyfit(X, B, 1)
    resid = max(np.abs(np.polyval(pt, X) - T).mean(),
                np.abs(np.polyval(pb, X) - B).mean())
    if resid > MAX_FIT_RESID or abs(pt[0] - pb[0]) < 1e-3:
        raise ValueError('beam edges are not straight (%.2f)' % resid)
    ax = (pb[1] - pt[1]) / (pt[0] - pb[0])
    ay = np.polyval(pt, ax)
    m = {'apex': (ax, ay), 'pt': pt, 'pb': pb, 'left': X.mean() < ax,
         'resid': resid}
    H, W = lum.shape
    yy, xx = np.mgrid[0:H, 0:W]
    r, u = _polar(m, xx + x0 + 0.5, yy + y0 + 0.5)
    core = (u > 0.25) & (u < 0.75) & (lum > 0)
    rb = np.arange(0, 400, 4)
    G = np.full(len(rb), np.nan)
    for i, a in enumerate(rb):
        k = core & (r >= a) & (r < a + 4)
        if k.sum() > 3:
            G[i] = lum[k].mean()
    ok = ~np.isnan(G)
    if ok.sum() < 10:
        raise ValueError('beam gain not measurable')
    rmin, rmax = rb[ok].min(), rb[ok].max() + 4
    Gs = np.interp(rb, rb[ok], G[ok])
    Gs = np.convolve(np.pad(Gs, 2, mode='edge'), np.ones(5) / 5, 'valid')
    tr = (rb >= 0.25 * rmax) & (rb <= rmax - 28)
    sl, ic = np.polyfit(rb[tr] + 2, Gs[tr], 1)
    gmax = Gs[tr].max()
    rb2 = np.arange(0, 1200, 4)
    trend = np.minimum(sl * (rb2 + 2) + ic, G_CAP * gmax)
    Gfull = np.where(rb2 > rmax - 28, trend,
                     np.interp(rb2, rb, Gs))
    Gr = np.interp(r, rb2 + 2, Gfull)
    sel = (r > rmin + 8) & (r < rmax - 4) & (Gr > 1)
    ratio = np.where(Gr > 0, lum / np.maximum(Gr, 1e-3), 0)
    ub = np.linspace(-1, 2, 121)
    P = np.zeros(len(ub))
    for i, a in enumerate(ub):
        k = sel & (u >= a) & (u < a + 0.025)
        if k.sum() > 5:
            P[i] = ratio[k].mean()
    P = np.convolve(np.pad(P, 3, mode='edge'), np.ones(7) / 7, 'valid')
    rgb = light[core].sum(0) / max(lum[core].sum(), 1.0)
    if FLAT:
        # 5-bit light cannot hold a slow ramp: every step of a gentle
        # gradient becomes a broad flat band with a contour line round it,
        # and dithering it reads as grain (both rejected on review). So the
        # beam is a flat-topped cone: its level is the trusted beam's 75th
        # percentile, its across-profile is flattened wherever it is within
        # 10% of the core, and only the two soft edges and the first
        # TIP_R units off the projector ramp -- short, steep ramps whose
        # steps sit a texel or two apart and read as an anti-aliased edge.
        lev = float(np.percentile(Gs[tr], 75))
        m['level'] = lev
        ramp = np.clip(rb2 / TIP_R, 0, 1)
        Gfull = lev * ramp * ramp * (3 - 2 * ramp)
        core_p = P[(ub > 0.3) & (ub < 0.7)].mean()
        Pn = np.clip(P / max(core_p, 1e-6), 0, None)
        hi_ = np.nonzero(Pn >= 0.9)[0]
        if len(hi_):
            Pn[hi_[0]:hi_[-1] + 1] = 1.0
        P = np.minimum(Pn, 1.0)
    m.update(rb=rb2, G=Gfull, ub=ub, P=P, rgb=rgb)
    return m


def _smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _smoother(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * t * (t * (6 * t - 15) + 10)


def _ease(t):
    """0 -> 1, quick off zero and easing (no kink) into the peak: the
    dark first 5-bit steps -- the ones whose contrast shows -- stay a
    texel or two wide; the long, gentle part of the fade is spent at the
    bright end where one step is a small fraction of the light."""
    t = np.clip(t, 0.0, 1.0)
    e = 1.0 - (1.0 - t) ** 3
    return e * _smooth(t * 6.0)


def render_cone(m, x0, y0, w, h, scale):
    yy, xx = np.mgrid[0:h * scale, 0:w * scale]
    xs, ys = x0 + (xx + 0.5) / scale, y0 + (yy + 0.5) / scale
    if FLAT:
        # flat level; each edge a fixed EDGE_W-unit smooth falloff centred
        # on the measured half-maximum line, whatever the distance from the
        # projector -- an edge that widened with the beam would spread its
        # few 5-bit steps into visible stripes
        ax, ay = m['apex']
        dx, dy = xs - ax, ys - ay
        if m['left']:
            dx = -dx
        r = np.hypot(dx, dy)
        th = np.arctan2(dy, dx)
        st = -m['pt'][0] if m['left'] else m['pt'][0]
        sb = -m['pb'][0] if m['left'] else m['pb'][0]
        tt, tb = np.arctan(st), np.arctan(sb)
        lo_, hi_ = min(tt, tb), max(tt, tb)
        # across-beam: a Gaussian in the beam's own angle, its half-max at
        # the measured edges, so the light falls off continuously from the
        # centre line into long soft wings; the wings are let down to zero
        # (smoothly) at TAIL half-widths from the centre. The 5-bit steps run
        # as straight rays from the projector.
        span = hi_ - lo_
        mid = 0.5 * (lo_ + hi_)
        sig = span / 2.3548                     # FWHM == measured width
        z = (th - mid) / sig
        P = np.exp(-0.5 * z * z) * _smooth((TAIL - np.abs(z)) / TAIL_W)
        L = (m['level'] * P * _smooth(r / TIP_R)
             * (dx > 0))
    else:
        r, u = _polar(m, xs, ys)
        L = np.interp(r, m['rb'] + 2, m['G']) * np.interp(
            u, m['ub'] + 0.0125, m['P'], left=0, right=0)
    return L[..., None] * m['rgb'][None, None, :]


# ------------------------------------------------------------- the field
def plan_field(name, sec9, sec4):
    """(new section 9, stats) -- unchanged on any doubt (raises)."""
    import render_field as RF
    spec = TARGETS[name.lower()]
    slot = spec['page']
    pages_l, ts, _te, page_px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pages_l if p is not None}
    page = pages.get(slot)
    if page is None or page.depth != 1 or page.px != 256 or page.size_flag:
        raise ValueError('page %d is not the 256px paletted beam page' % slot)
    if page_px % 256:
        raise ValueError('page size %d' % page_px)
    scale = page_px // 256
    pal = RF._pal_rgb(sec4).astype(np.float32)
    x0, y0, x1, y1 = spec['box']
    W, H = x1 - x0, y1 - y0
    beams, texid_count = [], {}
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            texid_count[sec9[o + 32]] = texid_count.get(sec9[o + 32], 0) + 1
            if sec9[o + 28] and sec9[o + 34] == slot:
                beams.append((layer, o))
            elif sec9[o + 32] == slot or (sec9[o + 28] and
                                          sec9[o + 34] == slot):
                raise ValueError('page %d is read by a non-beam record' % slot)
    if len(beams) != spec['records']:
        raise ValueError('%d beam records, expected %d'
                         % (len(beams), spec['records']))
    canv = {q: np.zeros((H, W, 3), np.float32) for q in spec['palettes']}
    src = np.frombuffer(page.data, np.uint8).reshape(256, 256)
    cells, recs = set(), []
    texid = None
    for layer, o in beams:
        q = sec9[o + 22]
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        sx, sy = struct.unpack_from('<HH', sec9, o + 14)
        if (layer != 2 or sec9[o + 26] or sec9[o + 27] or sec9[o + 30] != 1
                or q not in canv or sx % TILE or sy % TILE
                or max(struct.unpack_from('<HH', sec9, o + 18)) != TILE
                or struct.unpack_from('<II', sec9, o + 42)
                != (sx // TILE * UV_CELL, sy // TILE * UV_CELL)
                or (sx, sy) in cells or x % TILE or y % TILE
                or not (x0 <= x < x1 and y0 <= y < y1)):
            raise ValueError('beam record at %d,%d off signature' % (x, y))
        if texid is None:
            texid = sec9[o + 32]
        elif sec9[o + 32] != texid:
            raise ValueError('beam records on two texture ids')
        cells.add((sx, sy))
        a = src[sy:sy + TILE, sx:sx + TILE]
        c = pal[q][a]
        c[a == 0] = 0
        canv[q][y - y0:y - y0 + TILE, x - x0:x - x0 + TILE] += c
        recs.append((o, q, x, y, sx, sy))
    models = {q: fit_cone(canv[q], x0, y0) for q in canv}
    hi = {q: render_cone(m, x0, y0, W, H, scale) for q, m in models.items()}
    lo_max = {q: hi[q].max(-1).reshape(H, scale, W, scale).max((1, 3))
              for q in hi}
    step = TILE * scale

    def cell_light(q, x, y):
        X, Y = (x - x0) * scale, (y - y0) * scale
        return hi[q][Y:Y + step, X:X + step]

    def lit(q, x, y):
        return lo_max[q][y - y0:y - y0 + TILE, x - x0:x - x0 + TILE].max() \
            > LIGHT_MIN

    by_pos = {}
    for o, q, x, y, sx, sy in recs:
        by_pos.setdefault((x, y), []).append((o, q, sx, sy))
    # what each existing record carries, and the positions that need one
    content = {}                       # record offset -> [palettes]
    new_pos = []
    for y in range(y0, y1, TILE):
        for x in range(x0, x1, TILE):
            want = [q for q in canv if lit(q, x, y)]
            here = by_pos.get((x, y), [])
            if here:
                own = {q for _o, q, _sx, _sy in here}
                for o, q, _sx, _sy in here:
                    content[o] = [q]
                extra = [q for q in want if q not in own]
                content[here[0][0]] = content[here[0][0]] + extra
            elif want:
                new_pos.append((x, y, want))
    n_after = texid_count.get(texid, 0) + len(new_pos)
    if n_after > MAX_RECORDS_PER_PAGE:
        raise ValueError('texture id %d would hold %d records (> %d)'
                         % (texid, n_after, MAX_RECORDS_PER_PAGE))
    free = [(cx * TILE, cy * TILE) for cy in range(16) for cx in range(16)
            if (cx * TILE, cy * TILE) not in cells]
    if len(new_pos) > len(free):
        raise ValueError('%d new cells, %d free' % (len(new_pos), len(free)))
    out_page = np.zeros((page_px, page_px), np.uint16)

    def put(sx, sy, qs, x, y):
        if not qs:
            return
        rgb = sum(cell_light(q, x, y) for q in qs)
        out_page[sy * scale:sy * scale + step,
                 sx * scale:sx * scale + step] = _pack(np.clip(rgb, 0, 255))

    for o, q, x, y, sx, sy in recs:
        put(sx, sy, content.get(o, [q]), x, y)
    template = bytes(sec9[recs[0][0]:recs[0][0] + FN.TILE_SIZE])
    added = []
    for (x, y, qs), (sx, sy) in zip(new_pos, free):
        put(sx, sy, qs, x, y)
        r = bytearray(template)
        struct.pack_into('<hh', r, 2, x, y)
        struct.pack_into('<HH', r, 14, sx, sy)
        struct.pack_into('<II', r, 42, sx // TILE * UV_CELL,
                         sy // TILE * UV_CELL)
        r[22] = qs[0]
        added.append(bytes(r))
    # records first (moves the texture block), then the page
    import ff7nx_parallaxfill as PF
    back = sec9.find(b'BACK')
    layers = PF._layers(sec9, back, ts)
    l2 = [L for L in layers if L[0] == 2]
    if not l2:
        raise ValueError('no layer 2')
    _l, count_at, first, n = l2[0]
    buf = bytearray(sec9)
    end = first + n * FN.TILE_SIZE
    buf[end:end] = b''.join(added)
    struct.pack_into('<H', buf, count_at, n + len(added))
    sec = bytes(buf)
    plist, ts2, te2 = FN.parse_texture_block(sec, page_px)
    p = plist[slot]
    plist[slot] = FN.Page(slot, p.size_flag, 2, out_page.tobytes(), page_px)
    sec = FN.replace_texture_block(sec, plist, ts2, te2)
    st = {'cones': len(models), 'added': len(added),
          'records': len(recs) + len(added), 'texid_records': n_after,
          'fit': max(m['resid'] for m in models.values())}
    return sec, st


# ------------------------------------------------- mode 3 (the default)
# The 3x truecolor page above is 5 bits a channel: the yellow beam (peak
# ~52/255) has ~6 levels above black, so any fade across it is ~6 hard bands.
# Blend mode 3 (dst + src/4) is stock FF7 for a depth-1 page -- the +18 alias
# registration FINDINGS-194 leaves byte-identical -- and it makes every
# palette step a QUARTER of a 5-bit step (~2/255): the yellow fade gets ~25
# levels, the magenta core (peak ~110) is two stacked records. The price is
# the page staying the paletted 256px original, i.e. 1x texels; the cones are
# rendered at 4x and box-filtered to them.
M3_SS = 4                         # supersampling per texel
M3_QUARTER = 255.0 / 31.0 / 4.0   # light added by one palette level, mode 3
M3_MIN_Q = 1.5                    # global floor, in quarter-levels
XFER_SLOPE = 8.67                 # measured: drawn = 8.67*c - 13.4 (x blend factor)
XFER_OFFSET = 13.4
# 617g: ONE blend mode for every tile (default 3). Two modes cannot meet at
# a tile edge without a step: mode 1 draws in 8.7/255 steps, mode 3 in 2.2,
# so the same light rounds differently on the two sides (measured through
# the transfer above). Mode 3 tops out at 0.25*T(31) = 64/255, so the
# brightest light (the magenta core, fitted peak ~110, and the overlaps)
# rolls off smoothly into that ceiling; everything under 60% of it is
# untouched.
# SEVENTH_NX_BEAMS_FORCE_MODE=1 uses mode 1 everywhere instead (full
# magenta brightness, coarse 5-bit bands); =0 restores per-tile modes.
FORCE_MODE = int(os.environ.get('SEVENTH_NX_BEAMS_FORCE_MODE', '3') or 0)


def mode3_enabled():
    return os.environ.get('SEVENTH_NX_BEAMS_MODE', '3').strip() != '1'


def _ramp_palette(old, rgb):
    """palette entries: index k (1..31) is the cone colour with its brightest
    channel at 5-bit level k; index 0 and 32..255 keep/zero as below."""
    rgb = np.asarray(rgb, np.float64)
    rgb = rgb / rgb.max()
    out = np.zeros(len(old), np.uint16)
    out[0] = old[0]
    for k in range(1, 32):
        c = np.clip(np.rint(k * rgb), 0, 31).astype(int)
        out[k] = 0x8000 | c[0] | (c[1] << 5) | (c[2] << 10)
    return out


def _best_fit(wants, pal_ids, cap):
    """(sets, assign) or None: best-fit-decreasing bin packing of colour
    sets into palettes of `cap` entries."""
    order = sorted(range(len(wants)), key=lambda i: (-len(wants[i]), i))
    sets = {q: set() for q in pal_ids}
    assign = [None] * len(wants)
    for i in order:
        w = wants[i]
        best = None
        for q in pal_ids:
            add = len(w - sets[q])
            if len(sets[q]) + add > cap:
                continue
            key = (add, -len(sets[q] & w), len(sets[q]))
            if best is None or key < best[0]:
                best = (key, q)
        if best is None:
            return None
        sets[best[1]] |= w
        assign[i] = best[1]
    return sets, assign


def _pack_or_merge(flats, pal_ids, cap, max_merge=1):
    """((mixes, assign, remap), merges) with no tile aliased, or
    (None, merges) when even merging colours <= max_merge stored levels
    apart does not fit."""
    remap = {}
    fixed = set()

    def canon(c):
        while c in remap:
            c = remap[c]
        return c
    merges = 0
    while True:
        wants = [frozenset(canon(c) for c in f) - {(0, 0, 0)} for f in flats]
        got = _best_fit(wants, pal_ids, cap)
        if got is not None:
            sets, assign = got
            mixes = {q: {c: j + 1 for j, c in enumerate(sorted(sets[q]))}
                     for q in pal_ids}
            full = {}
            for f in flats:
                for c in f:
                    if c != (0, 0, 0):
                        full[c] = canon(c)
            return (mixes, assign, {c: d for c, d in full.items()
                                    if c != d}), merges
        # merge the rarest colour into its nearest neighbour (L-inf <= 1)
        count = {}
        for w in wants:
            for c in w:
                count[c] = count.get(c, 0) + 1
        keys = sorted(count, key=lambda c: (count[c], c))
        arr = np.array(keys)
        done = False
        # a colour that has absorbed another is never merged itself, so no
        # texel ends more than max_merge levels from its own colour
        for i, c in enumerate(keys):
            if c in fixed:
                continue
            d = np.abs(arr - np.array(c)).max(1)
            d[i] = 99
            j = int(d.argmin())
            if d[j] <= max_merge:
                remap[c] = keys[j]
                fixed.add(keys[j])
                merges += 1
                done = True
                break
        if not done:
            return None, merges


def plan_field_mode3(name, sec9, sec4):
    """(new section 9, new section 4, stats) -- raises on any doubt."""
    import render_field as RF
    import ff7nx_marginblack as MB
    spec = TARGETS[name.lower()]
    slot = spec['page']
    pages_l, ts, _te, page_px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pages_l if p is not None}
    page = pages.get(slot)
    if page is None or page.depth != 1 or page.px != 256 or page.size_flag:
        raise ValueError('page %d is not the 256px paletted beam page' % slot)
    pal = RF._pal_rgb(sec4).astype(np.float32)
    x0, y0, x1, y1 = spec['box']
    W, H = x1 - x0, y1 - y0
    beams, texid_count = [], {}
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            texid_count[sec9[o + 32]] = texid_count.get(sec9[o + 32], 0) + 1
            if sec9[o + 28] and sec9[o + 34] == slot:
                beams.append((layer, o))
            elif sec9[o + 32] == slot:
                raise ValueError('page %d is read by a non-beam record' % slot)
    if len(beams) != spec['records']:
        raise ValueError('%d beam records, expected %d'
                         % (len(beams), spec['records']))
    canv = {q: np.zeros((H, W, 3), np.float32) for q in spec['palettes']}
    src = np.frombuffer(page.data, np.uint8).reshape(256, 256)
    cells, offs_ = set(), []
    texid = None
    for layer, o in beams:
        q = sec9[o + 22]
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        sx, sy = struct.unpack_from('<HH', sec9, o + 14)
        if (layer != 2 or sec9[o + 26] or sec9[o + 27] or sec9[o + 30] != 1
                or q not in canv or sx % TILE or sy % TILE
                or max(struct.unpack_from('<HH', sec9, o + 18)) != TILE
                or struct.unpack_from('<II', sec9, o + 42)
                != (sx // TILE * UV_CELL, sy // TILE * UV_CELL)
                or (sx, sy) in cells or x % TILE or y % TILE
                or not (x0 <= x < x1 and y0 <= y < y1)):
            raise ValueError('beam record at %d,%d off signature' % (x, y))
        if texid is None:
            texid = sec9[o + 32]
        elif sec9[o + 32] != texid:
            raise ValueError('beam records on two texture ids')
        cells.add((sx, sy))
        a = src[sy:sy + TILE, sx:sx + TILE]
        c = pal[q][a]
        c[a == 0] = 0
        canv[q][y - y0:y - y0 + TILE, x - x0:x - x0 + TILE] += c
        offs_.append(o)
    # palette pages used by anything other than these records: refuse
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            if sec9[o + 22] in canv and o not in offs_:
                raise ValueError('palette %d shared' % sec9[o + 22])
    models = {q: fit_cone(canv[q], x0, y0) for q in canv}
    # light per cone at 1x (box-filtered from M3_SS), brightest channel, in
    # mode-3 palette levels; a level above 31 needs a second stacked record
    # ONE LAYER, PREMIXED. All cones are summed into a single light field
    # first (the yellow cones as one yellow, the magenta on its own), and
    # that field is cut into tiles -- one record per position, every texel
    # carrying all the light there is, so where the beams cross they add
    # and no tile edge can show.
    #   * 617c drew a record per cone; where a cone's invisible faint wing
    #     overlapped another cone's record the other record's texels did not
    #     draw on hardware (two blended records at one position and z), which
    #     cut a stepped black wedge out of the upper beam;
    #   * 617d merged only the tiles where two cones met and dropped a cone's
    #     faint wing everywhere else, so merged and unmerged tiles disagreed
    #     at every tile edge: blocks.
    # Colours: yellow-only texels use the yellow ramp (palette of the first
    # yellow cone, indices 1..31), magenta-only the magenta ramp; a tile with
    # both gets exact 5-bit mixes, qa*yellow + qb*magenta rounded per
    # channel (a mix with qb = 0 IS the ramp colour), stored in the free
    # indices: all of the other yellow palette, then 32..255 of the two
    # ramp palettes. Per tile: mode 3 (quarter steps) when the light fits in
    # 31 quarter-levels, else mode 1 (only the magenta core).
    Q = M3_QUARTER
    lows = {}
    for q, m in models.items():
        hi = render_cone(m, x0, y0, W, H, M3_SS).max(-1)
        lows[q] = hi.reshape(H, M3_SS, W, M3_SS).mean((1, 3))
    rgbn = {q: np.asarray(m['rgb'], np.float64) / max(m['rgb'])
            for q, m in models.items()}
    pal_ids = sorted(models)
    mag = max(pal_ids, key=lambda q: rgbn[q][2])          # blue-heavy
    yel = [q for q in pal_ids if q != mag]
    if len(yel) < 1:
        raise ValueError('no yellow cone')
    ypal = yel[0]
    Yn = sum(rgbn[q] for q in yel) / len(yel)
    Yn = Yn / Yn.max()
    Mn = rgbn[mag]
    Ly = sum(lows[q] for q in yel)
    Lm = lows[mag]

    # one global floor: light under MIN_Q quarter-levels (~3/255) is zero
    # everywhere, the same rule in every tile
    tot = np.maximum(Ly * Yn.max(), 0) + Lm
    Ly = np.where(tot < M3_MIN_Q * Q, 0.0, Ly)
    Lm = np.where(tot < M3_MIN_Q * Q, 0.0, Lm)

    def ramp_colour(k, rgb):
        return tuple(int(v) for v in np.clip(np.rint(k * rgb), 0, 31))
    pal_out = {ypal: {}, mag: {}}
    for k in range(1, 32):
        pal_out[ypal][ramp_colour(k, Yn)] = pal_out[ypal].get(
            ramp_colour(k, Yn), k)
        pal_out[mag][ramp_colour(k, Mn)] = pal_out[mag].get(
            ramp_colour(k, Mn), k)
    ramp_y = np.array([ramp_colour(k, Yn) for k in range(32)])
    ramp_m = np.array([ramp_colour(k, Mn) for k in range(32)])
    # 617g: COLOURS FROM THE MEASURED TRANSFER. Read straight off the 617f
    # hardware screenshot (every margin texel of known stored value, ~60k
    # samples, both modes): a blended tile's palette channel c is drawn as
    #     T(c) = max(0, 8.67 c - 13.4)            (0..255, per channel)
    # times 1 (mode 1) or 1/4 (mode 3) -- the offset rides inside the blend
    # factor (mode 3's measured offset is ~1/4 of mode 1's). Rendering 617f
    # through T reproduces the screenshot to +-1 on both sides of the
    # rectangular edge. A stored value therefore is T's inverse for the
    # tile's own mode: the same light comes out the same in a mode-1 and a
    # mode-3 tile, which is what the edges needed (617f's "+1 level" was the
    # wrong inverse: mode-1 tiles still crushed the small channels -- the
    # yellow's green under the magenta -- by ~10/255).
    T_A, T_B = XFER_SLOPE, XFER_OFFSET

    def stored(c8, mode):
        # the stored value whose DRAWN light is nearest the wanted light;
        # 0 included, so a faint texel is left dark rather than lifted to
        # the first visible step
        k = 1.0 if mode == 1 else 0.25
        lv = np.arange(32, dtype=np.float64)
        drawn = np.maximum(T_A * lv - T_B, 0.0) * k
        drawn[1] = 1e9                     # same as 0, never chosen
        v = np.abs(c8[..., None] - drawn).argmin(-1)
        return v.astype(np.int64)
    m3_max = (T_A * 31 - T_B) / 4.0 - 1.0
    # 618d: colours are packed into the palettes AFTER every tile is known.
    # 617g packed them first-fit in scan order and, with the palettes full,
    # drew 53 of 207 tiles with the NEAREST stored colour instead -- one of
    # them (0,0), on the bed where the yellow and magenta cross, lost its
    # yellow: the visible square (hardware, 10-01). Now: best-fit-decreasing
    # over all tiles (largest colour set first, into the palette it adds
    # fewest new colours to); if that still does not fit, colours one level
    # apart are merged (<= 1 stored step, ~2/255 drawn in mode 3) until it
    # does. No tile is aliased; the 617g path remains only as a last resort.
    tiles_ = []
    for y in range(y0, y1, TILE):
        for x in range(x0, x1, TILE):
            vy = Ly[y - y0:y - y0 + TILE, x - x0:x - x0 + TILE]
            vm = Lm[y - y0:y - y0 + TILE, x - x0:x - x0 + TILE]
            c8 = vy[..., None] * Yn + vm[..., None] * Mn
            mode = 3 if c8.max() <= m3_max else 1
            if FORCE_MODE:
                mode = FORCE_MODE
                if mode == 3:
                    # soft knee into mode 3's ceiling: light below 60% of
                    # it is untouched, the magenta core and the overlaps
                    # roll off smoothly instead of clipping flat
                    kn = 0.6 * m3_max
                    over = np.maximum(c8 - kn, 0.0)
                    c8 = np.where(c8 > kn, kn + (m3_max - kn) * (
                        1.0 - np.exp(-over / (m3_max - kn))), c8)
            col = stored(c8, mode)
            if not col.any():
                continue
            kind = ('mixed' if vy.max() > Q and vm.max() > Q else
                    'yellow' if vy.max() > Q else 'magenta')
            tiles_.append([x, y, mode, col, kind])
    need, modes = [], {}
    packed, merges = _pack_or_merge(
        [[tuple(c) for c in t[3].reshape(-1, 3).tolist()] for t in tiles_],
        pal_ids, 255)
    if packed is not None:
        mixes, assign, remap = packed
        modes[('merged_colours', 0)] = merges
        modes[('merge_max_levels', 0)] = max(
            [max(abs(a - b) for a, b in zip(c, d)) for c, d in remap.items()]
            or [0])
        for (x, y, mode, col, kind), q in zip(tiles_, assign):
            have = mixes[q]
            idx = np.array([0 if c == (0, 0, 0) else have[remap.get(c, c)]
                            for c in (tuple(v) for v in
                                      col.reshape(-1, 3).tolist())],
                           np.uint8).reshape(TILE, TILE)
            need.append((x, y, q, mode, idx))
            modes[(kind, mode)] = modes.get((kind, mode), 0) + 1
    else:
        pools = [(q, list(range(1, 256))) for q in pal_ids]
        mixes = {q: {} for q, _ in pools}        # palette -> {colour: idx}
        for x, y, mode, col, kind in tiles_:
            flat = [tuple(c) for c in col.reshape(-1, 3).tolist()]
            want = set(flat) - {(0, 0, 0)}
            for q, free in pools:
                have = mixes[q]
                new_c = [c for c in sorted(want) if c not in have]
                if len(have) + len(new_c) <= len(free):
                    for c in new_c:
                        have[c] = free[len(have)]
                    break
            else:
                q, free = max(pools, key=lambda t: sum(
                    c in mixes[t[0]] for c in want))
                have = mixes[q]
                for c in sorted(want):
                    if c in have:
                        continue
                    if len(have) < len(free):
                        have[c] = free[len(have)]
                    else:
                        keys = np.array([k for k in have
                                         if k[0] != 'alias'])
                        near = keys[((keys - np.array(c)) ** 2).sum(1)
                                    .argmin()]
                        have.setdefault(('alias',) + c, have[tuple(near)])
                modes[('aliased', 0)] = modes.get(('aliased', 0), 0) + 1
            idx = np.array([0 if c == (0, 0, 0) else
                            have.get(c, have.get(('alias',) + c, 0))
                            for c in flat],
                           np.uint8).reshape(TILE, TILE)
            need.append((x, y, q, mode, idx))
            modes[(kind, mode)] = modes.get((kind, mode), 0) + 1
    merged = [e for e in need if e[2] not in (ypal, mag) or e[4].max() >= 32]
    # cells, de-duplicated by content
    cell_of, page_out = {}, np.zeros((256, 256), np.uint8)
    grid = [(cx * TILE, cy * TILE) for cy in range(16) for cx in range(16)]
    zero = bytes(TILE * TILE)
    for _x, _y, _q, _md, k in need:
        b = k.tobytes()
        if b == zero or b in cell_of:
            continue
        if len(cell_of) >= len(grid):
            raise ValueError('more than 256 distinct cells (%d needed)' % len(set(e[4].tobytes() for e in need)))
        sx, sy = grid[len(cell_of)]
        cell_of[b] = (sx, sy)
        page_out[sy:sy + TILE, sx:sx + TILE] = k
    n_after = texid_count.get(texid, 0) - len(offs_) + max(len(need),
                                                           len(offs_))
    if n_after > MAX_RECORDS_PER_PAGE:
        raise ValueError('texture id %d would hold %d records (> %d)'
                         % (texid, n_after, MAX_RECORDS_PER_PAGE))
    if len(need) < len(offs_):
        # spare original records: one all-zero cell, drawn as nothing
        if len(cell_of) >= len(grid):
            raise ValueError('no cell for the spare records')
        spare = grid[len(cell_of)]
    buf = bytearray(sec9)

    def write(r, off, x, y, q, sxy, mode):
        sx, sy = sxy
        struct.pack_into('<hh', r, off + 2, x, y)
        struct.pack_into('<HH', r, off + 10, sx, sy)
        struct.pack_into('<HH', r, off + 14, sx, sy)
        struct.pack_into('<II', r, off + 42, sx // TILE * UV_CELL,
                         sy // TILE * UV_CELL)
        r[off + 22] = q
        r[off + 30] = mode

    for i, o in enumerate(offs_):
        if i < len(need):
            x, y, q, md, k = need[i]
            write(buf, o, x, y, q, cell_of[k.tobytes()], md)
        else:
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            write(buf, o, x, y, sec9[o + 22], spare, 1)
    template = bytes(sec9[offs_[0]:offs_[0] + FN.TILE_SIZE])
    added = []
    for x, y, q, md, k in need[len(offs_):]:
        r = bytearray(template)
        write(r, 0, x, y, q, cell_of[k.tobytes()], md)
        added.append(bytes(r))
    import ff7nx_parallaxfill as PF
    back = sec9.find(b'BACK')
    l2 = [L for L in PF._layers(bytes(buf), back, ts) if L[0] == 2]
    if not l2:
        raise ValueError('no layer 2')
    _l, count_at, first, nrec = l2[0]
    end = first + nrec * FN.TILE_SIZE
    buf[end:end] = b''.join(added)
    struct.pack_into('<H', buf, count_at, nrec + len(added))
    sec = bytes(buf)
    plist, ts2, te2 = FN.parse_texture_block(sec, page_px)
    p = plist[slot]
    plist[slot] = FN.Page(slot, p.size_flag, 1, page_out.tobytes(), p.px)
    sec = FN.replace_texture_block(sec, plist, ts2, te2)
    cols, hdr, npg, cpp = MB.palette_colours(sec4)
    cols = cols.copy()
    for q in models:
        row = np.zeros(cpp, np.uint16)
        row[0] = cols[q][0]
        for c, j in mixes.get(q, {}).items():
            if c[0] == 'alias':
                continue
            r_, g_, b_ = c
            row[j] = 0x8000 | r_ | (g_ << 5) | (b_ << 10)
        cols[q] = row
    s4 = bytearray(sec4)
    s4[hdr:hdr + cols.nbytes] = cols.astype('<u2').tobytes()
    st = {'cones': len(models), 'added': len(added),
          'records': max(len(need), len(offs_)), 'texid_records': n_after,
          'cells': len(cell_of), 'merged': len(merged),
          'positions': len({(e[0], e[1]) for e in need}),
          'modes': modes,
          'fit': max(m['resid'] for m in models.values())}
    return sec, bytes(s4), st


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    total = {'fields': 0, 'names': [], 'refused': []}
    if disabled():
        return total
    encode = encode or archive.encode_field
    for name in TARGETS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            payload = payloads.get(name)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            if mode3_enabled():
                parts[8], parts[3], st = plan_field_mode3(
                    name, parts[8], parts[3])
            else:
                parts[8], st = plan_field(name, parts[8], parts[3])
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append(
                (name, '%s: %s' % (type(exc).__name__, str(exc)[:80])))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['fields'] += 1
        total['names'].append(
            '%s: %d cones, %d records (+%d), texture id holds %d/256, '
            '%d colour(s) merged <=%d level, %d tile(s) aliased'
            % (name, st['cones'], st['records'], st['added'],
               st['texid_records'],
               st.get('modes', {}).get(('merged_colours', 0), 0),
               st.get('modes', {}).get(('merge_max_levels', 0), 0),
               st.get('modes', {}).get(('aliased', 0), 0)))
    return total


def summarise(st):
    out = []
    if st.get('fields'):
        out.append('  BEAMS (BUILD 617c): projector cones redrawn as '
                   'Gaussian beams, blend mode 3 (quarter steps) on the '
                   'paletted page, carried past the 4:3 edge (%s). %s=1 '
                   'disables; SEVENTH_NX_BEAMS_MODE=1 uses the 3x '
                   'truecolor page instead.'
                   % ('; '.join(st['names']), OFF_ENV))
    if st.get('refused'):
        out.append('  ! beams: %s' % ', '.join(
            '%s: %s' % r for r in st['refused']))
    return '\n'.join(out)
