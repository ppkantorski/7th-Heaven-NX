#!/usr/bin/env python3
"""ff7nx_fxcut.py -- give a 1x animated light effect an HD edge where it
meets the foreground art. BUILD 618z7.

psdun_4 (hardware 10-05): "the light effect is creating very jagged looking
pixellated edges where it meets the textures".

MEASURED. psdun_4's green glow is 876 additive records on layer 2 (146
positions x 6 animation states, paletted pages 15-18, palette-animated by
the field's script). It is 1x art: the glow is cut along the foreground
rocks (the platform's top edge, the left and right cliffs) on the 1997
16-texel grid, so its outline is a staircase three screen pixels a step
over Cosmos's smooth HD rock silhouette -- glow spills onto the rock on one
step and stops short of it on the next. It cannot be converted to truecolor
without freezing its palette animation.

FIX (FIELDS only), the glow itself stays 1x and animated:
  1. the rock's HD silhouette is found near the glow's 1x outline: the 1x
     mask smoothed (sigma 1.5 texels) is averaged with a per-pixel colour
     decision between the local background and the local rock (gaussian
     local means of the texels well inside / well outside the glow), so a
     clear colour edge (the platform) is followed exactly and an ambiguous
     one (the dark cliffs) gets a smooth curve through the staircase;
  2. each animation state's glow is grown by up to two 1x texels, inside
     its own cell and with its own palette indices, wherever the HD
     background is not yet lit (the staircase's notches);
  3. the rock is redrawn ON TOP of the glow as new opaque layer-2 records
     (the layer-1 art, keyed outside the HD silhouette), depth just in
     front of the glow and as far as it (characters stay in front). The
     glow is then visible exactly up to the HD edge, in every state.
  4. (user, 10-05: "edges should be smooth") the rock's outer ring is
     anti-aliased: it carries up to 60% of the glow it covers (coverage from
     a 0.8 px soft edge; glow = the states' mean at the ADPAL level measured
     on the hardware shot), and the silhouette is grown by one HD pixel so
     the glow's own last 1x pixel never leaves a dark gap. The colour
     decision only counts where it is decisive, so the dark cliffs follow a
     smooth curve through the staircase instead of wobbling.
Layer 1 is unchanged; the new cells go on a truecolor page outside the FX
band with room (608b gate). Refused on any doubt.

NOT FIXED HERE: brightness steps INSIDE the glow (the pillar, the leaf's
underside) are the 1x glow's own shading. Making those HD needs the glow in
true colour, which freezes its script-driven brightness (Cosmos's DDS for
pages 15-18 fails the art check, so it would also have to be re-drawn).
A version that treated dim glow as rock (fxcut.v3) left static/live seams
along 1x texel lines and was not shipped.

SEVENTH_NX_NO_FX_CUT=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_FX_CUT'
FIELDS = ('psdun_4',)
K = 3                       # HD pixels per field unit (768 pages)
GROW = 2                    # 1x texels the glow may grow into a notch
SIGMA_MASK = 1.5            # texels
SIGMA_COLOUR = 6.0          # HD px, local colour models
MIN_PART = 60               # HD px: smaller rock / background islands dropped
GLOW_ADPAL = -4             # 5-bit ADPAL offset measured on the hardware shot
AA_SIGMA = 0.8              # HD px, the soft edge
AA_MAX = 0.6                # at most this much glow on a rock pixel
UV_CELL = 625000
TILE = 52


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _rgb565(v):
    v = v.astype(np.uint32)
    return np.stack([((v >> 11) & 31) << 3, ((v >> 6) & 31) << 3,
                     (v & 31) << 3], -1).astype(np.float32)


def _rgb555(c):
    c = c.astype(np.int64)
    return np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31], -1).astype(
        np.float32) * 8.0


def _enc565(rgb):
    q = np.clip(np.floor(rgb / 8.0 + 0.5), 0, 31).astype(np.uint32)
    v = (q[..., 0] << 11 | (q[..., 1] << 1) << 5 | q[..., 2]).astype(
        np.uint16)                                # green LSB clear (1555)
    v[v == 0] = 0x0841
    return v


def rock_mask(l1, cov):
    """HD bool mask of the foreground rock near the glow's 1x outline.
    l1: HD layer-1 RGB; cov: HD bool, the glow's 1x coverage (any state)."""
    from scipy import ndimage as ND
    din = ND.distance_transform_edt(cov)
    dout = ND.distance_transform_edt(~cov)
    band_w = GROW * K + 1
    sureb = cov & (din > band_w)
    surer = ~cov & (dout > band_w) & (l1.max(-1) > 0)
    band = (cov & (din <= band_w)) | (~cov & (dout <= band_w))

    def lmean(mask):
        w = ND.gaussian_filter(mask.astype(np.float32), SIGMA_COLOUR)
        m = np.stack([ND.gaussian_filter(l1[..., c] * mask, SIGMA_COLOUR)
                      for c in range(3)], -1)
        return m / np.maximum(w, 1e-4)[..., None], w
    mb, wb = lmean(sureb)
    mr, wr = lmean(surer)
    db = np.linalg.norm(l1 - mb, axis=-1)
    dr = np.linalg.norm(l1 - mr, axis=-1)
    p = ND.gaussian_filter(dr / (db + dr + 1e-3), 1.0)     # 1 = background
    # how decisive the colour is: 0 when rock and background look alike
    conf = ND.gaussian_filter(np.abs(dr - db) / (dr + db + 1e-3), 2.0)
    conf = np.clip((conf - 0.15) / 0.25, 0.0, 1.0)
    S = ND.gaussian_filter(cov.astype(np.float32), SIGMA_MASK * K)
    pc = np.where((wb > 0.02) & (wr > 0.02), p, S)
    wcol = 0.5 * conf
    q = ND.gaussian_filter((1 - wcol) * S + wcol * pc, 2.0)
    rock = ~cov
    rock[band] = q[band] < 0.5
    for inv in (False, True):
        m = ~rock if inv else rock
        lab, n = ND.label(m)
        if n:
            sz = ND.sum(np.ones_like(m), lab, range(1, n + 1))
            keep = np.isin(lab, 1 + np.nonzero(sz >= MIN_PART)[0])
            rock = ~keep if inv else keep
    return rock


def plan_field(parts, debug=None):
    """(new section 9, info) -- raises on any doubt."""
    from scipy import ndimage as ND
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_marginblack as MB
    import ff7nx_parallaxfill as PF
    sec9 = bytes(parts[8])
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16                                  # HD px per 16-unit cell
    if k != 16 * K:
        raise ValueError('pages are %d px, expected %d' % (px, 256 * K))
    cols, _h, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    used = collections.defaultdict(set)
    binds = collections.Counter()
    l1, fx = {}, []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            for pg, so in ((sec9[o + 32], 10),) + (
                    ((sec9[o + 34], 14),) if bl else ()):
                sx, sy = struct.unpack_from('<hh', sec9, o + so)
                used[pg].add((sx // 16, sy // 16))
                binds[pg] += 1
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            if layer == 1 and not bl and not sec9[o + 26]:
                l1.setdefault((x, y), o)
            elif (layer == 2 and bl and sec9[o + 26] and sec9[o + 30] == 1
                  and struct.unpack_from('<HH', sec9, o + 18) == (16, 16)):
                p = pm.get(sec9[o + 34])
                if p is None or p.depth != 1:
                    raise ValueError('glow record not on a paletted page')
                fx.append(o)
    if not fx:
        raise ValueError('no animated additive layer-2 effect')
    cellkey = lambda o: (sec9[o + 34],) + tuple(
        v // 16 for v in struct.unpack_from('<hh', sec9, o + 14))
    if any(cnt != 1 for cnt in collections.Counter(
            cellkey(o) for o in fx).values()):
        raise ValueError('glow cells are shared')
    xs = [struct.unpack_from('<h', sec9, o + 2)[0] for o in fx]
    ys = [struct.unpack_from('<h', sec9, o + 4)[0] for o in fx]
    X0, Y0 = min(xs) - 16, min(ys) - 16
    X1, Y1 = max(xs) + 32, max(ys) + 32
    W1, H1 = X1 - X0, Y1 - Y0                     # 1x texels
    W, H = W1 * K, H1 * K
    # ---- layer 1, HD
    img = np.zeros((H, W, 3), np.float32)
    for (x, y), o in l1.items():
        if not (X0 <= x < X1 and Y0 <= y < Y1):
            continue
        p = pm.get(sec9[o + 32])
        if p is None:
            continue
        sx, sy = struct.unpack_from('<hh', sec9, o + 10)
        if p.depth == 2:
            rgb = _rgb565(np.frombuffer(p.data, '<u2').reshape(px, px)[
                sy // 16 * k:sy // 16 * k + k, sx // 16 * k:sx // 16 * k + k])
        else:
            q = sec9[o + 22]
            if q >= len(pal):
                continue
            a = np.frombuffer(p.data, np.uint8, count=65536).reshape(
                256, 256)[sy:sy + 16, sx:sx + 16]
            rgb = np.repeat(np.repeat(_rgb555(pal[q][a]), K, 0), K, 1)
        X, Y = (x - X0) * K, (y - Y0) * K
        img[Y:Y + 16 * K, X:X + 16 * K] = rgb
    # ---- the glow, per state, at 1x
    states = sorted({sec9[o + 27] for o in fx})
    idx = {s: np.zeros((H1, W1), np.int32) - 1 for s in states}
    lit = {s: np.zeros((H1, W1), bool) for s in states}
    palof = {s: np.zeros((H1, W1), np.int32) for s in states}
    for o in fx:
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        s = sec9[o + 27]
        p = pm[sec9[o + 34]]
        sx, sy = struct.unpack_from('<hh', sec9, o + 14)
        q = sec9[o + 22]
        if q >= len(pal):
            raise ValueError('glow palette %d missing' % q)
        a = np.frombuffer(p.data, np.uint8, count=65536).reshape(
            256, 256)[sy:sy + 16, sx:sx + 16]
        X, Y = x - X0, y - Y0
        idx[s][Y:Y + 16, X:X + 16] = a
        lit[s][Y:Y + 16, X:X + 16] = (pal[q][a] & 0x7FFF) != 0
        palof[s][Y:Y + 16, X:X + 16] = q
    union1 = np.zeros((H1, W1), bool)
    for s in states:
        union1 |= lit[s]
    up = lambda m: np.repeat(np.repeat(m, K, 0), K, 1)
    cov = up(union1)
    rock = rock_mask(img, cov)
    # one HD pixel outwards: the glow's own last pixel before the edge is
    # often missing (1x texel boundaries); the rock's anti-aliased ring
    # covers it instead of a dark gap
    rock = ND.binary_dilation(rock, np.ones((3, 3), bool)) | rock
    bg = ~rock
    # ---- 2. grow each state into the notches (1x texels with HD
    # background, outside the union, next to that state's own glow)
    bgtex = bg.reshape(H1, K, W1, K).mean((1, 3)) >= 0.25
    zone = ~union1 & (ND.distance_transform_edt(~union1) <= GROW)
    grown = 0
    new_lit = {}
    yy, xx = np.mgrid[0:H1, 0:W1]
    for s in states:
        L = lit[s].copy()
        I = idx[s].copy()
        for _it in range(GROW):
            want = zone & bgtex & ~L
            if not want.any():
                break
            add = np.zeros_like(L)
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                src_l = np.roll(np.roll(L, dy, 0), dx, 1)
                src_i = np.roll(np.roll(I, dy, 0), dx, 1)
                # same 16-texel cell only (each cell is its own record)
                same = (((yy - dy) // 16 == yy // 16)
                        & ((xx - dx) // 16 == xx // 16))
                take = want & ~add & src_l & same
                I[take] = src_i[take]
                add |= take
            L |= add
            grown += int(add.sum())
        new_lit[s] = (L, I)
    # 2b. the 1997 glow ends in a row or two of DIM texels along the rock
    # (its own 1x anti-aliasing): once the edge is cut in HD those read as
    # a dark stair-stepped fringe. Within 2 texels of the rock, a texel
    # darker than 70% of its inner neighbour (same cell, farther from the
    # rock) takes that neighbour's index, inside out.
    rocktex = rock.reshape(H1, K, W1, K).any((1, 3))
    rdist = ND.distance_transform_cdt(~rocktex, metric='chessboard')
    brightened = 0
    for s in states:
        L, I = new_lit[s]
        P = palof[s]
        for ring in (2, 1, 0):
            br = np.where(L, _rgb555(pal[P, np.clip(I, 0, cpp - 1)]).max(-1),
                          0.0)
            cand = L & (rdist == ring)
            best_i = I.copy()
            best_b = np.zeros_like(br)
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                nb = np.roll(np.roll(br, dy, 0), dx, 1)
                ni = np.roll(np.roll(I, dy, 0), dx, 1)
                nd = np.roll(np.roll(rdist, dy, 0), dx, 1)
                nl = np.roll(np.roll(L, dy, 0), dx, 1)
                same = (((yy - dy) // 16 == yy // 16)
                        & ((xx - dx) // 16 == xx // 16))
                ok = cand & nl & same & (nd > ring) & (nb > best_b)
                best_b[ok] = nb[ok]
                best_i[ok] = ni[ok]
            fix = cand & (br < 0.7 * best_b)
            I[fix] = best_i[fix]
            brightened += int(fix.sum())
    union2 = np.zeros((H1, W1), bool)
    for s in states:
        union2 |= new_lit[s][0]
    # ---- 3. the rock over the glow
    cut = rock & up(union2) & (img.max(-1) > 0)
    # glow estimate per HD pixel: mean over the states of the (grown,
    # brightened) glow, at the brightness measured on hardware (ADPAL ~ -4)
    gsum = np.zeros((H1, W1, 3), np.float32)
    gcnt = np.zeros((H1, W1), np.float32)
    for st_ in states:
        L, I = new_lit[st_]
        c5 = np.stack([pal[palof[st_], np.clip(I, 0, cpp - 1)] & 31,
                       (pal[palof[st_], np.clip(I, 0, cpp - 1)] >> 5) & 31,
                       (pal[palof[st_], np.clip(I, 0, cpp - 1)] >> 10) & 31],
                      -1).astype(np.float32)
        c5 = np.clip(c5 + GLOW_ADPAL, 0, 31) * 8.0
        gsum[L] += c5[L]
        gcnt[L] += 1
    glow1 = gsum / np.maximum(gcnt, 1)[..., None]
    glow = ND.gaussian_filter(np.repeat(np.repeat(glow1, K, 0), K, 1),
                              (1.0, 1.0, 0))
    # soft background coverage of each rock pixel near the edge
    cover = ND.gaussian_filter(bg.astype(np.float32), AA_SIGMA)
    cover = np.where(rock, np.clip(cover * 2.0, 0.0, AA_MAX), 0.0)
    aa = img + cover[..., None] * glow
    if debug is not None:
        debug.update(img=img, cov=cov, rock=rock, cut=cut,
                     union1=union1, union2=union2)
    if not cut.any():
        raise ValueError('nothing to cut')
    # write the grown glow back into its cells
    datas = {}
    for o in fx:
        s = sec9[o + 27]
        L, I = new_lit[s]
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        X, Y = x - X0, y - Y0
        pg = sec9[o + 34]
        if pg not in datas:
            datas[pg] = np.frombuffer(pm[pg].data, np.uint8,
                                      count=65536).reshape(256, 256).copy()
        sx, sy = struct.unpack_from('<hh', sec9, o + 14)
        cell = datas[pg][sy:sy + 16, sx:sx + 16]
        Lc, Ic = L[Y:Y + 16, X:X + 16], I[Y:Y + 16, X:X + 16]
        ch = Lc & (Ic != idx[s][Y:Y + 16, X:X + 16])
        cell[ch] = Ic[ch]
    # cutout cells
    todo = []
    for y in range(Y0, Y1, 16):
        for x in range(X0, X1, 16):
            Yp, Xp = (y - Y0) * K, (x - X0) * K
            m = cut[Yp:Yp + 16 * K, Xp:Xp + 16 * K]
            if m.shape != (16 * K, 16 * K) or not m.any():
                continue
            if (x, y) not in l1:
                continue
            v = np.where(m, _enc565(aa[Yp:Yp + 16 * K, Xp:Xp + 16 * K]),
                         0).astype(np.uint16)
            todo.append((x, y, v))
    slot = None
    for s_ in sorted(pm, key=lambda s: binds[s]):
        p = pm[s_]
        if p.depth != 2 or p.px != px or p.size_flag or 0x0F <= s_ < 0x1A:
            continue
        fr = [(cx, cy) for cy in range(16) for cx in range(16)
              if (cx, cy) not in used[s_]]
        if len(fr) >= len(todo) and binds[s_] + len(todo) <= 256:
            slot = s_
            break
    if slot is None:
        raise ValueError('%d cells needed, no truecolor page has room'
                         % len(todo))
    dd = np.frombuffer(pm[slot].data, '<u2').reshape(px, px).copy()
    zmin = min(struct.unpack_from('<I', sec9, o + 38)[0] for o in fx)
    tpl = bytearray(sec9[fx[0]:fx[0] + TILE])
    new = []
    for i, (x, y, v) in enumerate(todo):
        cx, cy = fr[i]
        dd[cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = v
        r = bytearray(tpl)
        struct.pack_into('<hh', r, 2, x, y)
        struct.pack_into('<hh', r, 10, cx * 16, cy * 16)
        struct.pack_into('<hh', r, 14, 0, 0)
        struct.pack_into('<I', r, 38, zmin - 1)
        struct.pack_into('<II', r, 42, cx * UV_CELL, cy * UV_CELL)
        r[26] = r[27] = r[28] = r[30] = 0
        r[32] = slot
        r[34] = 0
        new.append(bytes(r))
    # insert right after the last glow record of layer 2
    survey = DC.survey(sec9)
    rows = PF._layers(sec9, survey['back_start'], survey['tex_start'])
    lay = {r_[0]: r_ for r_ in rows}
    _l, count_at, first, count = lay[2]
    last = max(fx)
    if not (first <= last < first + count * TILE):
        raise ValueError('glow records outside layer 2')
    at = last + TILE
    buf = bytearray(sec9)
    buf[at:at] = b''.join(new)
    struct.pack_into('<H', buf, count_at, count + len(new))
    s9n = bytes(buf)
    plist, t0, t1 = FN.parse_texture_block(s9n, px)
    for pg, d in datas.items():
        q_ = plist[pg]
        plist[pg] = FN.Page(pg, q_.size_flag, 1, d.tobytes(), q_.px)
    q_ = plist[slot]
    plist[slot] = FN.Page(slot, q_.size_flag, 2, dd.tobytes(), q_.px)
    return FN.replace_texture_block(s9n, plist, t0, t1), {
        'records': len(new), 'page': slot, 'grown': grown,
        'brightened': brightened,
        'cut': int(cut.sum()), 'states': len(states)}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None,
                    skip=()):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in FIELDS:
        if name in skip:                # ff7nx_glowhd redrew this glow
            continue
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], info = plan_field(parts)
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s: %d rock record(s) on page %d, glow grown by '
                           '%d texel(s) over %d states'
                           % (name, info['records'], info['page'],
                              info['grown'], info['states']))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  FX CUT (BUILD 618z7): the 1x glow ends at the HD rock '
                   'edge (rock redrawn over it on layer 2, glow grown into '
                   'the staircase notches) (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! fx cut %s: %s' % (name, why))
    return '\n'.join(out)
