#!/usr/bin/env python3
"""ff7nx_edgeclean.py -- clean the cut-out edges of background art that is
drawn over a MOVIE (nothing behind it in the field). BUILD 618y.

las4_2 (City of the Ancients, the waterfall steps), hardware 10-04: "lots of
dirty pixels on the textures around the edges". The waterfall there is not
field art at all: it is the background movie last4_2, and the field only
draws the stepping stones (opaque layer 2) and the water drops over it.

Cosmos cut the stones out of upscaled frames that had the green waterfall
behind them. Their soft edge texels carry that green, and a few
half-transparent specks sit outside the silhouette. FFNx blends those with
alpha, so they melt into the movie; the Switch page keys transparency hard,
so every half-transparent texel becomes either a hole or a solid speck:
the dotted dark/teal outline in the report. In an ordinary field the lower
layers hide this; over a movie nothing does.

FIX (per field in FIELDS; only static opaque records, only on truecolor
pages, only cells no other record shares):
  1. the opaque texels are composed in field space, so silhouettes that
     span several cells are judged as one shape;
  2. specks: opaque islands smaller than SPECK texels are keyed;
  3. rim: every opaque texel within RIM texels of the keyed outside takes
     the mean colour of the solid interior around it (texels at least
     RIM+1 inside the edge, within a 9x9 window); a rim texel with no interior near it (a
     one-texel whisker) is keyed.
Nothing inside the silhouette changes, no record or page is added.

SEVENTH_NX_NO_EDGE_CLEAN=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_EDGE_CLEAN'
FIELDS = ('las4_2', 'las4_3', 'mtcrl_4')  # las4_3: user 10-05, rough edges
# las4_3's Cosmos page is fully opaque (the backdrop baked in around the
# stones), so its alpha is no cut: its edges are smoothed from the built
# silhouette instead -- the 1x stair steps rounded at page resolution.
SMOOTH = {'las4_3': 1.6}            # gaussian sigma, texels
# BUILD 620k2. mtcrl_4 (hardware 10-07: the track and its cross bracing
# "rough along the edges, and a few dirty spots here and there ... some
# of the parallel cross shaped wood support ... very dirty looking ... i
# dont want to make it look worse"). A lattice, not stones: Cosmos's alpha
# cut thins the 2..4-texel braces (tried: the crosses went faint), and a
# brace has no interior two texels in, so the whisker rule would key it.
# Here the silhouette stays exactly as built; only:
#   * the rim takes the interior colour where there IS interior (rails,
#     posts), never keyed;
#   * dirt: small dark blotches inside the wood -- opaque texels darker
#     than the median of the opaque texels around them (window DIRT[0])
#     by DIRT[1] (lum, 0..255), in islands of at most DIRT[2] texels, at
#     least one texel inside the edge -- take that median colour.
OPTS = {'mtcrl_4': {'cosmos_cut': False, 'whiskers': False, 'rim': False,
                    'dirt': (7, 28, 60)}}
DIRT_MIN_LUM, DIRT_MAX_DROP = 45.0, 80.0
SPECK = 40                # texels (at 3x: under ~4 native pixels)
RIM = 1                   # rim depth, texels
WIN = 4                   # interior search radius (9x9)


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _rgb(v):
    v = v.astype(np.uint32)
    return np.stack([((v >> 11) & 31) << 3, ((v >> 5) & 63) << 2,
                     (v & 31) << 3], -1).astype(np.float32)


def _565(rgb):
    """R5G6B5 with GREEN'S LOW BIT CLEAR (BUILD 618z).

    The port widens 565 to 1555 as ((v & 0xF800) >> 1) | ((v & 0x07E0) >> 1)
    | (v & 0x1F): green's low bit lands in BLUE's top bit. Every odd-green
    texel this pass wrote came out with +128 blue -- las4_2's blue stones
    (hardware 10-04, build 386). field_bg_repack keeps that bit clear on
    every texel; so does this now (green on the same 31-level grid)."""
    q = np.clip(np.floor(np.asarray(rgb, np.float32) / 8.0 + 0.5),
                0, 31).astype(np.uint32)
    return (q[..., 0] << 11 | (q[..., 1] << 1) << 5
            | q[..., 2]).astype(np.uint16)


def _nanmedian(c, win):
    """Median of the finite values in a win x win window (NaN = not art),
    by sorting the stacked shifts -- no per-texel Python."""
    r = win // 2
    H, W = c.shape
    pad = np.pad(c, r, mode='constant', constant_values=np.nan)
    stack = np.stack([pad[dy:dy + H, dx:dx + W] for dy in range(win)
                      for dx in range(win)], 0)
    with np.errstate(all='ignore'):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            m = np.nanmedian(stack, 0)
    return np.where(np.isfinite(m), m, 0.0).astype(np.float32)


def plan_field(sec9, name=None, vparts=None, art=None):
    """(new section 9, stats) -- raises when there is nothing safe to do.

    BUILD 618y (second pass): with `art`, every static opaque cell that has
    a vanilla twin takes Cosmos's own cell -- its colour, and its ALPHA as
    the cut (>= 50%), so the silhouette follows Cosmos's smooth HD outline
    instead of the 1997 page's 1x stair steps. The rim (one texel inside the
    cut) takes the colour of the nearest texel at least two inside, so no
    matte colour survives on the edge. Specks are removed as before."""
    from scipy import ndimage as ND
    import diag_common as DC
    import field_bg_native as FN
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    vtwin = {}
    if vparts is not None and art is not None:
        vs9 = vparts[8]
        _vpl, vts, _vte, _vpx = DC.parse_pages(vs9)
        seen = collections.Counter()
        for layer, offs in DC.walk_layers(vs9, vs9.find(b'BACK'), vts):
            for o in offs:
                if vs9[o + 28] or vs9[o + 26]:
                    continue
                key = (layer,) + struct.unpack_from('<hh', vs9, o + 2) + (
                    struct.unpack_from('<I', vs9, o + 38)[0], vs9[o + 22])
                sx, sy = struct.unpack_from('<hh', vs9, o + 10)
                vtwin[key] = (vs9[o + 32], sx, sy, vs9[o + 22])
                seen[key] += 1
        vtwin = {kk: vv for kk, vv in vtwin.items() if seen[kk] == 1}
    recs, uses = [], collections.Counter()
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            if sec9[o + 28]:
                pg = sec9[o + 34]
                sx, sy = struct.unpack_from('<hh', sec9, o + 14)
                uses[(pg, sx // 16, sy // 16)] += 1
            pg = sec9[o + 32]
            sx, sy = struct.unpack_from('<hh', sec9, o + 10)
            uses[(pg, sx // 16, sy // 16)] += 1
            if sec9[o + 28] or sec9[o + 26]:
                continue
            p = pm.get(pg)
            if p is None or p.depth != 2 or p.size_flag:
                continue
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            key = (layer, x, y, struct.unpack_from('<I', sec9, o + 38)[0],
                   sec9[o + 22])
            recs.append((o, pg, sx // 16, sy // 16, x, y, vtwin.get(key)))
    recs = [r for r in recs if uses[(r[1], r[2], r[3])] == 1]
    if not recs:
        raise ValueError('no exclusive static opaque cells')
    x0 = min(r[4] for r in recs)
    y0 = min(r[5] for r in recs)
    W = (max(r[4] for r in recs) + 16 - x0) * k // 16
    H = (max(r[5] for r in recs) + 16 - y0) * k // 16
    raw = np.zeros((H, W), np.uint16)
    own = np.full((H, W), -1, np.int32)
    data = {s: np.frombuffer(pm[s].data, '<u2').reshape(px, px).copy()
            for s in {r[1] for r in recs}}
    for i, r in enumerate(recs):
        o, pg, cx, cy, x, y = r[:6]
        X, Y = (x - x0) * k // 16, (y - y0) * k // 16
        sl = (slice(Y, Y + k), slice(X, X + k))
        if (own[sl] >= 0).any():
            raise ValueError('overlapping opaque records')
        raw[sl] = data[pg][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k]
        own[sl] = i
    img = _rgb(raw)
    m = raw != 0
    cos_cells = 0
    opt = OPTS.get(name, {})
    if art is not None and vtwin and name not in SMOOTH \
            and opt.get('cosmos_cut', True):
        import ff7nx_fxmargin as FXM
        import ff7nx_fxpages as FP
        sheets = {}
        for i, r in enumerate(recs):
            tw = r[6]
            if tw is None:
                continue
            vpg, vsx, vsy, q = tw
            sel = FP._selected_palette(art.provider, name, vpg, q)
            if sel is None:
                continue
            if (vpg, sel) not in sheets:
                sheets[(vpg, sel)] = FXM._provider_rgba(art, name, vpg, sel,
                                                        px)
            sh = sheets[(vpg, sel)]
            if sh is None:
                continue
            c = sh[vsy // 16 * k:(vsy // 16 + 1) * k,
                   vsx // 16 * k:(vsx // 16 + 1) * k]
            X, Y = (r[4] - x0) * k // 16, (r[5] - y0) * k // 16
            sl = (slice(Y, Y + k), slice(X, X + k))
            m[sl] = c[..., 3] >= 128
            img[sl] = c[..., :3].astype(np.float32)
            cos_cells += 1
    if name in SMOOTH:
        # round the stair steps: blur the silhouette, cut at half; new edge
        # texels take the nearest original colour
        sm = ND.gaussian_filter(m.astype(np.float32), SMOOTH[name]) >= 0.5
        sm &= own >= 0
        if m.any():
            _d, (iy, ix) = ND.distance_transform_edt(~m, return_indices=True)
            img = img[iy, ix]
        m = sm
    lab, n = ND.label(m, np.ones((3, 3), bool))
    sizes = ND.sum(m, lab, range(1, n + 1))
    speck = np.isin(lab, [i + 1 for i, s_ in enumerate(sizes) if s_ < SPECK])
    m2 = m & ~speck
    rim = m2 & ND.binary_dilation(~m2, np.ones((3, 3), bool),
                                  iterations=RIM)
    inner = ND.binary_erosion(m2, np.ones((3, 3), bool),
                              iterations=RIM + 1)
    near_inner = ND.binary_dilation(inner, np.ones((3, 3), bool),
                                    iterations=RIM + WIN)
    if opt.get('whiskers', True):
        whisker = rim & ~near_inner
        fix = rim & ~whisker
    else:
        whisker = np.zeros_like(rim)
        fix = rim & near_inner
    if not opt.get('rim', True):
        fix = np.zeros_like(rim)
    out = img.copy()
    if inner.any():
        _d, (iy, ix) = ND.distance_transform_edt(~inner, return_indices=True)
        out[fix] = img[iy[fix], ix[fix]]
    dirt_n = 0
    if opt.get('dirt'):
        win, dl, dmax = opt['dirt']
        lum = np.einsum('hwc,c->hw', out, np.array([0.299, 0.587, 0.114], np.float32))
        body = ND.binary_erosion(m2, np.ones((3, 3), bool))
        med = np.zeros_like(out)
        for ch in range(3):
            c = np.where(m2, out[..., ch], np.nan)
            med[..., ch] = _nanmedian(c, win)
        mlum = np.einsum('hwc,c->hw', med, np.array([0.299, 0.587, 0.114], np.float32))
        # near-black slots (the rails' holes, painted gaps) are drawing,
        # not dirt: only mid-dark blotches qualify
        cand = body & (lum < mlum - dl) & (lum >= DIRT_MIN_LUM) \
            & (mlum - lum <= DIRT_MAX_DROP)
        dlab, dn = ND.label(cand, np.ones((3, 3), bool))
        if dn:
            dsz = ND.sum(cand, dlab, range(1, dn + 1))
            small = np.isin(dlab, [i + 1 for i, s_ in enumerate(dsz)
                                   if s_ <= dmax])
            out[small] = med[small]
            dirt_n = int(small.sum())
    keep = m2 & ~whisker
    new = _565(out)
    new[keep & (new == 0)] = 0x0841          # opaque black: RGB(8,8,8), not the key
    new[~keep] = 0
    changed = int((new != raw).sum())
    if not changed:
        raise ValueError('edges already clean')
    for i, r in enumerate(recs):
        o, pg, cx, cy, x, y = r[:6]
        X, Y = (x - x0) * k // 16, (y - y0) * k // 16
        data[pg][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = \
            new[Y:Y + k, X:X + k]
    plist, t0, t1 = FN.parse_texture_block(sec9, px)
    for s_, d in data.items():
        q = plist[s_]
        plist[s_] = FN.Page(s_, q.size_flag, 2, d.astype('<u2').tobytes(),
                            px)
    return FN.replace_texture_block(sec9, plist, t0, t1), {
        'specks': int(speck.sum()), 'whiskers': int(whisker.sum()),
        'dirt': dirt_n,
        'rim': int(fix.sum()), 'cells': len(recs), 'cosmos': cos_cells}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None,
                    art=None):
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
            vparts = list(lgp.split_sections(archive.decompressed(entry)))
            parts[8], st = plan_field(parts[8], name, vparts, art)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %d cells (%d cut from Cosmos alpha), %d '
                              'speck, %d whisker, %d rim texel(s)'
                              % (name, st['cells'], st.get('cosmos', 0),
                                 st['specks'], st['whiskers'], st['rim']))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  EDGE CLEAN (BUILD 618y): cut-out edges over the '
                   'background movie cleaned (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! edge clean %s: %s' % (name, why))
    return '\n'.join(out)
