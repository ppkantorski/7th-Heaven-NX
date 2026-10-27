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

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
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
    # BUILD 620k3. mtcrl_4 (hardware 10-07: "small details on the edge that
    # look like they shouldnt be there (orange pixels above the track) ...
    # could any of this also be made smoother with AA"). The 1x key leaves
    # every slanted rail and brace a staircase of 3-texel steps; on the
    # rails' top edge each step corner reads as an orange stub. Layer 2
    # only, against the scrolling layer 3 (so binary, no blend), with a
    # gentler blur than ztruck's so the 2..4-texel braces keep their width.
    'mtcrl_4': [(2, None, None, None, (1,))],
    # BUILD 620k5. las4_4 (hardware 10-08 of 620k4: "las4_4 looks decent,
    # but there are a couple weird dips, and some of the edges still look
    # pretty jagged"). The crystal pyramids (layer 2, 109 truecolor
    # records) stand against the aurora: every ridge is a 3-texel
    # staircase, with 1-2 texel notches where tiles meet. Layer 1 (the
    # floor blocks) is guarded: edges against it are left exactly as they
    # are. The blended glow records on layer 2 are not touched.
    'las4_4': [(2, None, None, None, (1,))],
    # BUILD 717. jun_i1 (hardware: "the edge here looks a bit rough with
    # little black jagged parts on it"). The shop counter's left post
    # (layer 2, palette 3) stands in front of the glass's additive glow;
    # its right edge is the 1x key, a 3-texel staircase that runs 1..3
    # texels past Cosmos's own edge, and those overhang texels carry the
    # art's dark matte. Only the post's own records (palette 3) are laid
    # out, so its edge is judged against what is really behind it. The
    # 718 fringe recolour (FRINGE_BAND, 0.8) is off since 720: CUT_RIM.
    'jun_i1': [(2, None, None, None, (), {3})],
}
# BUILD 778. fship_2 and its story copies (hardware 10-22: "the edges are
# too jagged on parts of the ships frame ... at 1080p / 1440p ... clean it
# up a bit more without dropping actual texture, perhaps add smoothing /
# anti-aliasing too along the curves that overlap with the parallax
# field"). The bridge's window frame is layer 2 (static truecolor) over the
# animated sky (layer 3, param 1, six states): every strut is a 3-texel
# staircase. Layer 1 (the floor) is guarded; parts thinner than THICK keep
# their cut (railings, cables); the sky behind is sampled at every state
# over the camera's whole range and the art's own edge texels are blended
# toward it where it is steady enough (thick_only).
FSHIP = ('fship_2', 'fship_22', 'fship_23', 'fship_24', 'fship_25')
for _f in FSHIP:
    TARGETS[_f] = [(2, None, None, None, (1,))]
# BUILD 720. jun_i1 again (hardware, on the 718 preview: "this one portion
# also looks a bit dirty"). 718 recoloured the post's dark rim with the
# colour FRINGE_IN texels inside, which is paler than the edge: a light
# stripe down both edges, and a 2-texel jog where the ramp began. The rim
# is now CUT instead: a texel within CUT_RIM[0] of the key, darker than
# CUT_RIM[1] x the colour FRINGE_IN inside it, with another record behind
# it (never a hole), is keyed BEFORE the re-cut, so the gaussian follows
# where the post's real colour ends and refills only from post colour.
# A rim texel whose nearest outside texel is covered by different records
# (beside the post's lower half the shelf panel is missing under the glass,
# so a cut there showed the bare glow as a white line) is not cut but
# re-blended half post colour, half what shows beside it.
CUT_RIM = {'jun_i1': (3, 0.8)}
# BUILD 620k4 (hardware 10-08 of 620k3: "could be a bit smoother on some
# of the track edges"): 1.4 -> 2.4, the rails' top-edge bumps rounded off
# (the thick-only rule still keeps the lattice); plus the swept AA ring (AA).
SIGMAS = {'mtcrl_4': 2.4, 'las4_4': 2.6}
SIGMAS.update({_f: 2.0 for _f in FSHIP})
# Only edges where BOTH sides are wide are re-cut: a texel is added only if
# the transparent side around it is at least THICK texels deep, removed only
# if the art side is. The lattice's narrow gaps and 3-texel braces are left
# exactly as they are (tried without: acute corners filled in, +19.5k
# texels); rails, posts and the outer silhouette lose their stair steps.
THICK = {'mtcrl_4': 4.0}
THICK.update({_f: 4.0 for _f in FSHIP})
# BUILD 620k3. 1x stubs: whole 3x3-texel blocks of ONE colour (the 1997
# pixel, never upscaled) standing out of the silhouette with transparent
# space on at least two sides -- the orange nubs over mtcrl_4's rails.
STUBS = {'mtcrl_4'}
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
    # BUILD 620k4. mtcrl_4 (hardware 10-08 of 620k3: "could be a bit
    # smoother on some of the track edges"). The camera pans x -277..277
    # (pinned in y) over a screen-fixed sky (layer 4) and parallax rocks
    # (layer 3, speed 96). What a layer-2 texel has behind it is sampled at
    # 33 camera positions over that pan: where it barely changes (the sky:
    # std <= MAX_STD per channel) the rails' edge is blended toward its
    # mean, so the silhouette against the sky is a real anti-aliased ramp.
    # Where the rocks pass behind, the edge stays binary. Only parts at
    # least RING_THIN texels thick (the rails, posts, outer edge); the
    # lattice's 2..4-texel braces are left as they are.
    'mtcrl_4': {'sweep': (-277, 277, 33), 'cam_y': 0, 'max_std': 40.0,
                'thick_only': True, 'layers': {2: ('ring',)}},
    # BUILD 620k5. las4_4: the camera does not move; behind the pyramids is
    # the aurora (not background tiles; the framesim cannot draw it). Its
    # colour along the ridges, measured on the user's 620k4 screenshot
    # (median of the sky 4..14 px above every ridge): (134, 255, 254).
    # Only the art's own edge texels are blended (thick_only): the aurora
    # dims during the FMV lead-in, and a texel outside the art would then
    # show as a cyan line. 620k5b: only edges that face open sky -- never a
    # tile edge where the picture goes on in layer 1 (620k5 blended those:
    # the thin cyan staircase lines across the screen, hardware 10-08) --
    # the art's dark 1x-stepped rim recoloured from inside first, and a
    # deeper ramp (lo_inner 0.15).
    'las4_4': {'sky': (134, 255, 254), 'thick_only': True, 'lo_inner': 0.15,
               'layers': {2: ('ring',)}},
}
for _f in FSHIP:
    AA[_f] = {'sweep': (-64, 64, 5), 'sweep_y': (-24, 136, 4),
              'states': 'auto',
              'max_std': 45.0, 'thick_only': True, 'layers': {2: ('ring',)}}
RING_LO = 0.25          # alpha at or below this: transparent (fainter
#                         texels are mostly the background mean, which
#                         is what mismatches a passing rock)
RING_HI = 0.94          # alpha at or above this: left as the art
RING_SIGMA = 3.0        # texels: averages out the 3-texel steps
RING_STEEP = 1.6        # ramp ~2.5 texels wide
RING_THIN = 4.5         # parts thinner than this keep the SIGMA coverage
RING_OPEN = 12.0        # thick_only: open space at least this deep beside
RING_LO_INNER = 0.45    # thick_only: lowest alpha blended (art texels only)
SKY_REACH = 3           # 'sky' mode: texels this close to open sky
SKY_RIM = 2             # 'sky' mode: rim texels recoloured from inside
SKY_RIM_IN = 4
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


def _records(s9, ts, pm, layer, static=True, pals=None):
    import diag_common as DC
    out = []
    for lay, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        if lay != layer:
            continue
        for o in offs:
            if static and (s9[o + 28] or s9[o + 26]):
                continue
            if pals is not None and s9[o + 22] not in pals:
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


def _composite(s9, recs, pm, px, org, shape, ts):
    """BUILD 720: what recs show (RGB float, NaN where nothing), in the
    canvas raster: layer 1 first, then layer 2 from the largest z, FX
    records from their FX page and added when their blend is additive.
    Paletted pages are skipped."""
    import diag_common as DC
    k = px // 16
    lay = {}
    for l_, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            lay[o] = l_
    out = np.full(shape + (3,), np.nan, np.float32)
    order = sorted((o for o in recs if lay.get(o) in (1, 2)),
                   key=lambda o: (lay[o] > 1, -struct.unpack_from(
                       '<H', s9, o + 24)[0]))
    for o in order:
        fx = s9[o + 28]
        p = pm.get(s9[o + 34] if fx else s9[o + 32])
        if p is None or p.depth != 2:
            continue
        x, y = struct.unpack_from('<hh', s9, o + 2)
        n = struct.unpack_from('<H', s9, o + 18)[0] or 16
        Y, X, m = (y - org[1]) * K, (x - org[0]) * K, n * K
        y_a, x_a = max(Y, 0), max(X, 0)
        y_b, x_b = min(Y + m, shape[0]), min(X + m, shape[1])
        if y_a >= y_b or x_a >= x_b:
            continue
        u, v = struct.unpack_from('<II', s9, o + 42)
        cx, cy = round(u / 625000), round(v / 625000)
        d = np.frombuffer(p.data, '<u2').reshape(px, px)[
            cy * k:cy * k + m, cx * k:cx * k + m][
            y_a - Y:y_b - Y, x_a - X:x_b - X]
        hit = d != 0
        dst = out[y_a:y_b, x_a:x_b]
        c = _rgbf(d)
        if fx and s9[o + 30] == 1:
            base = np.nan_to_num(dst)
            dst[hit] = np.minimum(base[hit] + c[hit], 255)
        else:
            dst[hit] = c[hit]
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


def _stubs(t, m):
    """(mask without the 1x stubs, texels removed)."""
    from scipy import ndimage as ND
    H, W = t.shape
    H3, W3 = H // K, W // K
    b = t[:H3 * K, :W3 * K].reshape(H3, K, W3, K).transpose(0, 2, 1, 3) \
        .reshape(H3, W3, K * K).astype(np.int64)
    op = (b != 0).all(-1)
    r, g, bl = (b >> 11) & 31, (b >> 5) & 63, b & 31
    rng = np.maximum.reduce([r.max(-1) - r.min(-1),
                             (g.max(-1) - g.min(-1)) // 2,
                             bl.max(-1) - bl.min(-1)])
    uni = op & (rng <= 1)
    emp = (b == 0).sum(-1) >= 6
    lab, n = ND.label(emp)
    if not n:
        return m, 0
    sz = ND.sum(emp, lab, range(1, n + 1))
    ext = np.isin(lab, [i + 1 for i in range(n) if sz[i] > 200])
    pad = np.pad(ext, 1)
    nb = (pad[:-2, 1:-1].astype(int) + pad[2:, 1:-1] + pad[1:-1, :-2]
          + pad[1:-1, 2:])
    st = uni & (nb >= 2)
    full = np.zeros_like(m)
    full[:H3 * K, :W3 * K] = np.kron(st, np.ones((K, K), bool))
    return m & ~full, int((m & full).sum())


def smooth(tex, wrap=False, fringe=False, frozen=None, sigma=None,
           thick=None, stubs=False, solid=None):
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
    if stubs:
        m, stats['stubs'] = _stubs(t, m)
    mb = m if solid is None else (m | solid)
    a = ND.gaussian_filter(mb.astype(np.float32), sigma or SIGMA,
                           mode='nearest')
    m2 = a > 0.5
    if thick:
        fg = ND.maximum_filter(ND.distance_transform_edt(m), size=7)
        bg = ND.maximum_filter(ND.distance_transform_edt(~m), size=7)
        m2 = np.where(m, m2 | (fg < thick), m2 & (bg >= thick))
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


def cut_rim(tex, behind, band, ratio, sig=None, beside=None):
    """BUILD 720: key the dark anti-aliased rim (see CUT_RIM). sig: which
    other records cover each texel (a hash); a texel is only cut where the
    same records cover the nearest keyed texel outside it, so the cut shows
    what already shows beside the edge and never opens a gap onto a
    different record (jun_i1: the glass behind the shelf panel). There,
    when beside (what the other records show, RGB, NaN = nothing) has a
    colour at that outside texel, the rim texel is re-blended half and half
    from the colour inside and the colour beside it instead of staying
    dark.
    Returns (tex, cut, blended)."""
    from scipy import ndimage as ND
    m = tex != 0
    din, (oy, ox) = ND.distance_transform_edt(m, return_indices=True)
    inner = din >= FRINGE_IN
    _d, (jy, jx) = ND.distance_transform_edt(~inner, return_indices=True)
    ref = tex[jy, jx]
    dark = (m & (din <= band) & inner[jy, jx]
            & (_lum(tex) < ratio * _lum(ref)))
    if behind is not None:
        dark &= behind
    out = tex.copy()
    mix = np.zeros(tex.shape, bool)
    if sig is not None:
        same = sig == sig[oy, ox]
        if beside is not None:
            nb = beside[oy, ox]
            mix = dark & ~same & ~np.isnan(nb[..., 0])
            out[mix] = _enc(0.5 * _rgbf(ref[mix]) + 0.5 * nb[mix])
        dark &= same
    out[dark] = 0
    return out, int(dark.sum()), int(mix.sum())


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


def background_sweep(parts, org, shape, cfg):
    """BUILD 620k4. (mean RGB, ok) in canvas texels: what layers 3/4 put
    behind each texel of a layer-1/2 canvas whose top-left is field `org`,
    over camera x positions sweeping cfg['sweep'] = (lo, hi, n) at
    cfg['cam_y']. ok = always drawn behind and per-channel std <=
    cfg['max_std']."""
    import ff7nx_framesim as FS
    F = FS.Field(parts)
    lo, hi, n = cfg['sweep']
    H, W = shape
    s1 = np.zeros((H, W, 3), np.float64)
    s2 = np.zeros((H, W, 3), np.float64)
    cnt = np.zeros((H, W), np.int32)
    seen = np.zeros((H, W), np.int32)
    # BUILD 778: an animated sky (fship_2: layer 3 is param 1, six states)
    # is sampled in every state, and a camera that also pans in y over a
    # sweep_y = (lo, hi, n) grid.
    if 'sweep_y' in cfg:
        ylo, yhi, yn = cfg['sweep_y']
        cys = [int(round(v)) for v in np.linspace(ylo, yhi, yn)]
    else:
        cys = [cfg.get('cam_y', 0)]
    states = cfg.get('states', ({},))
    if states == 'auto':
        # every (param, state bit) the scrolling layers' records name
        import diag_common as DC
        s9 = parts[8]
        _pl, ts, _te, _px = DC.parse_pages(s9)
        seen_ = set()
        for lay, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
            if lay in (3, 4):
                for o in offs:
                    if s9[o + 26]:
                        seen_.add((s9[o + 26], s9[o + 27]))
        states = tuple({p_: b_} for p_, b_ in sorted(seen_)) or ({},)
    combos = [((int(round(cx)), cy), st_) for cx in np.linspace(lo, hi, n)
              for cy in cys for st_ in states]
    n = len(combos) // len(states)
    for cam, st_ in combos:
        img = F.render(cam=cam, K=K, states=st_, mark=True,
                       layers=(3, 4)).astype(np.float64)
        drawn = ~((img[..., 0] == 255) & (img[..., 1] == 0)
                  & (img[..., 2] == 255))
        r0 = int(round((org[1] - cam[1] + 120) * K))
        c0 = int(round((org[0] - cam[0] + 213.5) * K))
        ys0, ys1 = max(0, -r0), min(H, img.shape[0] - r0)
        xs0, xs1 = max(0, -c0), min(W, img.shape[1] - c0)
        if ys0 >= ys1 or xs0 >= xs1:
            continue
        sub = img[r0 + ys0:r0 + ys1, c0 + xs0:c0 + xs1]
        dr = drawn[r0 + ys0:r0 + ys1, c0 + xs0:c0 + xs1]
        s1[ys0:ys1, xs0:xs1] += sub * dr[..., None]
        s2[ys0:ys1, xs0:xs1] += sub * sub * dr[..., None]
        cnt[ys0:ys1, xs0:xs1] += dr
        seen[ys0:ys1, xs0:xs1] += 1
    c = np.maximum(cnt, 1)[..., None]
    mean = s1 / c
    std = np.sqrt(np.maximum(s2 / c - mean * mean, 0)).max(-1)
    ok = (seen >= max(3, n // 4)) & (cnt == seen) & (std <= cfg['max_std'])
    # (n: camera positions; `seen` counts every state at each of them)
    return mean.astype(np.float32), ok


def aa_ring(tex, out, frozen, bg, valid, thick_only=False, lo_inner=None,
            steep=None, solid=None):
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
    # BUILD 620k5b: texels another layer draws (solid) count as art for the
    # ramp, so an art edge that continues in that layer is no silhouette
    ms = m2 if solid is None else (m2 | solid)
    a3 = np.clip((g(ms, RING_SIGMA) - 0.5) * (steep or RING_STEEP) + 0.5,
                 0, 1)
    a2 = g((tex != 0) if solid is None else ((tex != 0) | solid), SIGMA)
    thick = ND.grey_dilation(ND.distance_transform_edt(m2), size=(11, 11))
    alpha = np.where(thick < RING_THIN, a2, a3)
    if thick_only:
        # thin parts keep their binary cut: alpha 1 inside, 0 outside
        alpha = np.where(thick < RING_THIN, m2.astype(np.float32), a3)
    _d, (iy, ix) = ND.distance_transform_edt(~m2, return_indices=True)
    T = _rgbf(out)[iy, ix]
    ok = valid.copy()
    if frozen is not None:
        ok &= ~frozen
    near = ND.binary_dilation(m2 ^ ND.binary_erosion(m2), iterations=4)
    ok &= near
    if thick_only:
        # only the outer silhouette: the transparent side must open into a
        # wide space (holes in the rails and lattice gaps stay binary)
        bgd = ND.maximum_filter(ND.distance_transform_edt(~m2), size=31)
        ok &= bgd >= RING_OPEN
    lo = (lo_inner or RING_LO_INNER) if thick_only else RING_LO
    blend = ok & (alpha > lo) & (alpha < RING_HI)
    if thick_only:
        # the background behind changes with the camera: only the art's
        # own edge texels are softened (never a texel that is mostly the
        # background mean -- that showed as a pale fringe at other pans)
        blend &= m2
    clear = ok & m2 & (alpha <= RING_LO)
    col = alpha[..., None] * T + (1 - alpha[..., None]) * bg
    res = out.copy()
    res[blend] = _enc(col[blend])
    res[clear] = 0
    return res, {'ring': int((blend & ~m2).sum()),
                 'ring_in': int((blend & m2).sum()),
                 'ring_cleared': int(clear.sum())}


# BUILD 620k5. mtcrl_4 west (hardware 10-08 of 620k4: "very rough looking
# edges ... little black outlines, the wood cross bars arent smooth
# looking, have some dirty pixels and dirty cutouts"). The 1997 cut-out
# kept a black matte along the bars: measured on the built layer-2
# canvas, 99.7k of its 145k cut-edge texels are near black (lum < 40),
# (lum < 50 and 4 texels reach: 65.6k recoloured),
# 1..2 texels thick, mostly under the beams and along the braces. A near
# black texel within DEOUT_BAND of the cut takes the colour of the nearest
# wood texel, if that is at most DEOUT_REACH away -- a part that is dark
# through and through (the rails, the curled broken rail ends, where the
# dark line IS the drawing) has none that close and keeps it. Only along
# open space (a rail's cut-out holes keep their edges).
DEOUTLINE = {'mtcrl_4'}
DEOUT_BAND = 2.0        # texels from the cut
DEOUT_BLACK = 50.0      # lum (0..255) of a matte texel
DEOUT_WOOD = 60.0       # lum of a texel that can lend its colour
DEOUT_REACH = 4.0       # texels
DEOUT_OPEN = 4.0        # the transparent side opens at least this deep


# BUILD 620k5b. mtcrl_4 (hardware 10-08 of 620k5: "little protruding dots
# on the curve of the track that should be smooth"; the lattice "should be
# 2 diagonal straight bars each way inside a box of wood ... choppy /
# sloppy"). The cut-out's edges follow the 1x key: a bar's edge wobbles a
# texel or two and the rail's top carries one-texel nubs. Each edge texel
# is re-decided by the coverage averaged ALONG the local edge direction
# (structure tensor of the opacity, ORIENT_SIGMA / ORIENT_INT), over a
# line ORIENT_LEN texels long: a straight bar's edge becomes straight, a
# nub or notch shorter than the line is averaged away. Only where the edge
# has one clear direction (coherence >= ORIENT_COH): crossings and corners
# keep their shape. Added texels take the nearest texel's colour.
ORIENT = {'mtcrl_4'}
ORIENT_SIGMA = 1.0
ORIENT_INT = 3.0
ORIENT_LEN = 15
ORIENT_COH = 0.55
ORIENT_ANGLES = 24


def orient_smooth(out, frozen):
    """(new canvas, (added, removed))."""
    from scipy import ndimage as ND
    m = (out != 0).astype(np.float32)
    gy = ND.gaussian_filter(m, ORIENT_SIGMA, order=(1, 0))
    gx = ND.gaussian_filter(m, ORIENT_SIGMA, order=(0, 1))
    jxx = ND.gaussian_filter(gx * gx, ORIENT_INT)
    jyy = ND.gaussian_filter(gy * gy, ORIENT_INT)
    jxy = ND.gaussian_filter(gx * gy, ORIENT_INT)
    tr = jxx + jyy
    coh = np.sqrt((jxx - jyy) ** 2 + 4 * jxy * jxy) / np.maximum(tr, 1e-6)
    # edge direction = perpendicular to the dominant gradient
    theta = 0.5 * np.arctan2(2 * jxy, jxx - jyy) + np.pi / 2
    idx = np.round((theta % np.pi) / np.pi * ORIENT_ANGLES).astype(int) \
        % ORIENT_ANGLES
    avg = np.zeros_like(m)
    r = ORIENT_LEN // 2
    for a in range(ORIENT_ANGLES):
        sel = idx == a
        if not sel.any():
            continue
        t = a * np.pi / ORIENT_ANGLES
        k = np.zeros((ORIENT_LEN, ORIENT_LEN), np.float32)
        for i in range(-r, r + 1):
            k[int(round(r + i * np.sin(t))), int(round(r + i * np.cos(t)))] = 1
        k /= k.sum()
        avg[sel] = ND.convolve(m, k, mode='nearest')[sel]
    edge = ND.binary_dilation(m > 0, iterations=2) & \
        ND.binary_dilation(m == 0, iterations=2)
    use = edge & (coh >= ORIENT_COH) & (tr > 1e-3)
    if frozen is not None:
        use &= ~frozen
    new_m = np.where(use, avg >= 0.5, m > 0)
    src = out != 0
    _d, (iy, ix) = ND.distance_transform_edt(~src, return_indices=True)
    res = np.where(new_m, out, 0).astype(np.uint16)
    add = new_m & ~src
    res[add] = out[iy[add], ix[add]]
    return res, (int(add.sum()), int((src & ~new_m).sum()))


def deoutline(out, frozen):
    """(new canvas, texels recoloured)."""
    from scipy import ndimage as ND
    m = out != 0
    if not m.any():
        return out, 0
    C = _rgbf(out)
    lum = C.mean(-1)
    din = ND.distance_transform_edt(m)
    wood = m & (lum >= DEOUT_WOOD)
    dw, (iy, ix) = ND.distance_transform_edt(~wood, return_indices=True)
    bgd = ND.maximum_filter(ND.distance_transform_edt(~m), size=15)
    bad = m & (lum < DEOUT_BLACK) & (din <= DEOUT_BAND) & \
        (dw <= DEOUT_REACH) & (bgd >= DEOUT_OPEN)
    if frozen is not None:
        bad &= ~frozen
    res = out.copy()
    res[bad] = out[iy[bad], ix[bad]]
    return res, int(bad.sum())


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
    wrapped = {t[0]: (t[1], t[2]) for t in TARGETS[name] if t[1]}
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
    for layer, period, x0, fringe, guard, *opt in TARGETS[name]:
        # optional 6th field: only these palettes' records (BUILD 717)
        recs = _records(s9, ts, pm, layer, pals=opt[0] if opt else None)
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
        src_tex = tex
        behind = None
        if opt:
            mine = set(recs)
            others = [o for lay in (1, 2, 3, 4)
                      for o in _records(s9, ts, pm, lay, static=False)
                      if o not in mine]
            behind = _cover(s9, others, pm, px, org, tex.shape)
        if name in CUT_RIM:
            sig = np.zeros(tex.shape, np.uint64)
            rng = np.random.default_rng(720)
            for o in others:
                h = np.uint64(rng.integers(1, 2 ** 63))
                sig ^= np.where(_cover(s9, [o], pm, px, org, tex.shape),
                                h, np.uint64(0))
            beside = _composite(s9, others, pm, px, org, tex.shape, ts)
            src_tex, n_cut, n_mix = cut_rim(tex, behind, *CUT_RIM[name],
                                            sig=sig, beside=beside)
        out, st = smooth(src_tex, wrap=bool(period), fringe=fringe,
                         frozen=frozen, sigma=SIGMAS.get(name),
                         thick=THICK.get(name), stubs=name in STUBS,
                         solid=frozen if 'sky' in AA.get(name, {}) else None)
        if name in CUT_RIM:
            st['cut'] = n_cut
            st['rim_blend'] = n_mix
            st['removed'] = int(((tex != 0) & (out == 0)).sum())
        if opt:
            # a palette-filtered target: never key a texel that has nothing
            # of any other record behind it (it would draw black)
            hole = (out == 0) & (tex != 0) & ~behind
            out = np.where(hole, tex, out)
            st['kept_holes'] = int(hole.sum())
        if name in ORIENT:
            out, (st['orient_add'], st['orient_cut']) = orient_smooth(out,
                                                                    frozen)
        if name in DEOUTLINE:
            out, st['deoutline'] = deoutline(out, frozen)
        opts = AA.get(name, {}).get('layers', {}).get(layer, ())
        if 'lines' in opts:
            out, s_ = aa_lines(out, frozen)
            st.update(s_)
        if 'ring' in opts and 'sky' in AA.get(name, {}):
            from scipy import ndimage as ND
            cfg = AA[name]
            bg = np.empty(tex.shape + (3,), np.float32)
            bg[...] = np.asarray(cfg['sky'], np.float32)
            # BUILD 620k5b: only edges that face the sky -- not a tile
            # edge where the picture goes on in layer 1 (620k5 blended
            # those too: thin cyan lines along the tiles, hardware 10-08)
            sol = frozen if frozen is not None else np.zeros(tex.shape, bool)
            sky = (out == 0) & ~sol
            ok = ND.binary_dilation(sky, iterations=SKY_REACH)
            # the art's own 1x-stepped dark rim takes its colour from
            # SKY_RIM_IN texels inside (else the steps stay in the colour)
            dsk = ND.distance_transform_edt(~sky)
            inner = (out != 0) & (dsk >= SKY_RIM_IN)
            _d, (iy, ix) = ND.distance_transform_edt(~inner,
                                                     return_indices=True)
            rim = (out != 0) & (dsk <= SKY_RIM) & inner[iy, ix]
            if frozen is not None:
                rim &= ~frozen
            out = out.copy()
            out[rim] = out[iy[rim], ix[rim]]
            st['rim'] = int(rim.sum())
            out, s_ = aa_ring(tex, out, frozen, bg, ok,
                              thick_only=cfg.get('thick_only', False),
                              lo_inner=cfg.get('lo_inner'),
                              steep=cfg.get('steep'), solid=sol)
            st.update(s_)
        elif 'ring' in opts and parts is not None and 'sweep' in AA[name]:
            bg, ok = background_sweep(parts, org, tex.shape, AA[name])
            out, s_ = aa_ring(tex, out, frozen, bg, ok,
                              thick_only=AA[name].get('thick_only', False))
            st.update(s_)
        elif 'ring' in opts and parts is not None:
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
                if v.get('ring') or v.get('ring_in') else '',
                ', AA lines %d' % v['lines'] if v.get('lines') else '')
            + (', black matte %d' % v['deoutline']
               if v.get('deoutline') else '')
            for l_, v in info.items())))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  EDGE SMOOTH (BUILD 618z13..620k5): cut-out staircases redrawn '
                   'as curves at HD resolution (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! edge smooth %s: not applied -- %s' % (name, why))
    return '\n'.join(out)
