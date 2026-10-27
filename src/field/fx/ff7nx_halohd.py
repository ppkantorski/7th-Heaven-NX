#!/usr/bin/env python3
"""ff7nx_halohd.py -- las4_42's 1x additive light redrawn in true colour,
anti-aliased against the pyramids' HD silhouettes, its animation kept.
BUILD 620k3, reworked in 620k4.

las4_42 (hardware 10-07): "right before the fmv triggers looks pretty
pixellated along the edges, slightly dirty too ... the outline / sillouette
around the straight edges and the yellow glowing effect". 620k3 redrew the
halo; hardware 10-08: "the edges are still a bit rough looking. there are
some staircase patterns. some weird dirty stuff floating, sides not
smooth".

THE FIELD. Over a black sky, crystal pyramids: their tops are opaque
layer-2 Cosmos art (page 26), their bottoms and the floor blocks layer 1.
Two additive 1x effects sit on top, both on the paletted FX pages 15/16:
  * the yellow-green HALO (palette 2, 261 records): entity `shade1` pulses
    it 56..62 /64 (MPPAL) and, once var5[4] is set (the FMV's lead-in),
    fades it out to 0;
  * the cyan face HIGHLIGHTS (palette 3, 60 records): entity `shade2`
    steps its multiplier through 66, 51, 36, 21, 6 and back, one step a
    tick -- five values only.
Every pyramid edge was outlined by the halo's and the highlights' 1x
staircases, the highlights' 1x cells showed as flat teal rectangles at
their bright steps, and the halo had 1x holes (dark dots).

WHAT THIS DOES (FIELDS only):
  * PYRAMID EDGES. Behind layer 2's silhouette there is only the black sky
    (and the halo drawn over it). The silhouette's alpha is the layer-2
    opacity blurred and steepened (a ~2.5-texel ramp, as ztruck's ring);
    edge texels become alpha x art (over black) and the halo is drawn there
    at (1 - alpha): together an exact anti-aliased edge against the halo,
    and still a clean edge against black when the halo is off. Parts
    thinner than RING_THIN keep their binary cut. Only where no layer-1 art
    lies within 6 HD px (an L2 tile ending against layer 1 is a seam, not a
    silhouette), only in cells no other position reads. The art's own
    dark 1x-stepped rim (RIM px) takes the colour from a few texels in.
  * HALO. Composed at 1x at the light this port shows at the pulse's middle
    (MULT, through ff7nx_palstates.hw_light), small enclosed holes and
    one-texel dark notches filled, its own dim 1997 edge (EDGE_BAND texels
    before the pyramids) re-grown from the full halo and grown under them, upscaled 3x (bicubic) and weighted by
    (1 - alpha). Drawn by COPIES of the halo records (param HALO_P) on new
    truecolor FX pages. The original 1x records stay, under param HALO_Q:
    shade1's pulse loop is rewritten to BGON HALO_P / BGOFF HALO_Q and hold
    the multiplier at MULT; its fade branch is entered through BGOFF
    HALO_P / BGON HALO_Q -- so the lead-in fade still runs, on the 1x halo
    (vanilla's own records and palette ops, ~2 s), and the HD halo is what
    shows the rest of the time.
  * HIGHLIGHTS. The five multiplier values become five tile states: per
    value the 1x highlight light (hw_light at that value) is grown under
    the pyramids, upscaled 3x, softened inside the records' footprint and
    cut to the opaque art (layer-2 alpha, layer 1): no light over the sky,
    no flat 16-unit rectangles, no 1x staircase. One truecolor
    record set per value (params HL_P0..HL_P0+4, state bit 0); the 1x
    records are removed. shade2's loop becomes blin68_2's:
    IF; BGOFF v; v +- 1; BGON v; BACK -- v walks the params, one step a
    tick exactly as the multiplier did.

WHAT IT COSTS. The halo holds its pulse's middle (+-5 %) while the HD copy
shows; during the lead-in fade the halo is the 1997 one. Four truecolor FX
pages (~4.7 MB). SEVENTH_NX_NO_HALO_HD=1 keeps the field as built.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_HALO_HD'
FIELDS = {'las4_42': {'halo': 2, 'mult': 59, 'hl': 3,
                      'hl_values': (6, 21, 36, 51, 66)}}
K = 3
REC = 52
UV = 10_000_000
UV_CELL = UV // 16
GRID = 16
FX_LO, FX_HI = 0x0F, 0x18
MAX_BIND = 256
GROW = 3               # 1x px the halo / highlights are extended under art
EDGE_BAND = 4          # 1x px of the halo's own dim edge re-grown
RING_SIGMA = 3.0       # HD px: averages out the art's 3-texel steps
RING_STEEP = 2.0
RING_THIN = 4.5        # parts thinner than this keep their binary cut
RING_LO = 0.04         # alpha below this: transparent
RIM = 3                # HD px: the art's edge fringe recoloured from inside
HL_BLUR = 4.5          # HD px: the highlights' 1x cell edges softened
HL_FEATHER = 6.0       # HD px: fade at the highlights' outer footprint
SKY_MAX = 8            # layer-1 texels at or under this (max channel) count
#                        as black sky behind
HOLE_MAX = 6           # 1x halo holes up to this many texels are filled
EMPTY = 1.5            # an HD cell under this everywhere draws nothing
HALO_P, HALO_Q, HL_P0 = 1, 2, 3


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _h(*parts):
    return bytes.fromhex(''.join(parts))


# ----------------------------------------------------------------- scripts
def _shade1_patch(mult):
    """shade1 (palette 2). Pulse: IF var5[4]==0 { up-loop; down-loop;
    SKIP to the outer BACK } else { fade-loop }. New pulse: each tick BGON
    HALO_P, BGOFF HALO_Q, var5[2] = MULT, BACK -- and the IF's else-jump
    goes through BGOFF HALO_P, BGON HALO_Q, SKIP into the untouched fade
    loop. 0x48 bytes, same length; the rest is RET, never reached."""
    mp = 'ea0055500010020202ff' 'e700001000' 'e6001002ff'
    old = _h('8050024200', 'e5000200ff', '145004000043',
             '1450023e031b', '76500201', mp, '121e',
             '14500238021b', '78500201', mp, '121e', '1021',
             '14500200021b', '78500201', mp, '121e', '1268', '00')
    new = (_h('8050024200', 'e5000200ff', '14500400000f',
              'e000%02x00' % HALO_P, 'e100%02x00' % HALO_Q,
              '805002%02x' % mult, '1212',
              'e100%02x00' % HALO_P, 'e000%02x00' % HALO_Q, '102b'))
    new += b'\x00' * (0x52 - len(new))
    new += _h('14500200021b', '78500201', mp, '121e', '1268', '00')
    assert len(new) == len(old) == 0x75, (len(new), len(old))
    return old, new


def _shade2_patch(n):
    """shade2 (palette 3): v walked 66 -> 6 by 15 and back, MPPAL x v/64.
    v now IS the level's param (HL_P0 + index, state 0)."""
    mp = 'ea0055502030030303ff' 'e700203000' 'e6003003ff'
    old = _h('8050034200', 'e5000320ff',
             '1450033e031b', '7650030f', mp, '121e',
             '14500310021b', '7850030f', mp, '121e', '1240', '00')
    lo, hi = HL_P0, HL_P0 + n - 1
    up = _h('145003%02x031b' % hi, 'e1500300', '76500301', 'e0500300',
            '1212')
    down = _h('145003%02x021b' % lo, 'e1500300', '78500301', 'e0500300',
              '1212')
    new = (_h('805003%02x' % hi, '00', 'e5000320ff')
           + up + b'\x00' * (32 - len(up))
           + down + b'\x00' * (32 - len(down)) + _h('1240', '00'))
    assert len(new) == len(old), (len(new), len(old))
    return old, new


def patch_script(sec0, cfg):
    s0 = bytes(sec0)
    for old, new in (_shade1_patch(cfg['mult']),
                     _shade2_patch(len(cfg['hl_values']))):
        if s0.count(old) != 1:
            raise ValueError('script pattern found %d times' % s0.count(old))
        s0 = s0.replace(old, new)
    return s0


# ----------------------------------------------------------------- helpers
def _rgb565(t):
    t = t.astype(np.int64)
    return np.stack([((t >> 11) & 31) << 3, ((t >> 6) & 31) << 3,
                     (t & 31) << 3], -1).astype(np.float64)


def _enc(c):
    """RGB -> 565, green's low bit clear, black stays 0 (transparent)."""
    q = np.clip(np.floor(c / 8.0 + 0.5), 0, 31).astype(np.uint32)
    v = (q[..., 0] << 11 | (q[..., 1] << 1) << 5 | q[..., 2]).astype(
        np.uint16)
    v[q.max(-1) == 0] = 0
    return v


def _enc_opaque(c):
    v = _enc(c)
    v[v == 0] = 0x0841
    return v


def _bicubic(img, W, H):
    from PIL import Image
    return np.clip(np.stack([np.asarray(Image.fromarray(
        img[..., ch].astype(np.float32)).resize((W, H), Image.BICUBIC))
        for ch in range(3)], -1), 0, 255).astype(np.float64)


def _grow(img1, lit1, allow1, n):
    """img1 extended by nearest lit texel into `allow1` within n texels."""
    from scipy import ndimage as ND
    dist, (iy, ix) = ND.distance_transform_edt(~lit1, return_indices=True)
    g = (~lit1) & (dist <= n) & allow1
    return np.where(g[..., None], img1[iy, ix], img1), lit1 | g


def _fill_holes(img1, lit1, keep_out1):
    """Small enclosed unlit groups inside the lit halo take their ring's
    mean (the 1x holes that showed as dark dots)."""
    from scipy import ndimage as ND
    # one-texel dark notches inside the lit halo (all 8 neighbours lit,
    # under 0.6 of their median) take that median
    img1 = img1.copy()
    full = ND.minimum_filter(lit1.astype(np.uint8), size=3).astype(bool)
    lum = img1.max(-1)
    med = ND.median_filter(lum, size=3)
    dark = full & (lum < 0.6 * med) & ~keep_out1
    if dark.any():
        for c in range(3):
            mc = ND.median_filter(img1[..., c], size=3)
            img1[..., c] = np.where(dark, mc, img1[..., c])
    notches = int(dark.sum())
    lab, n = ND.label(~lit1)
    if not n:
        return img1, lit1, notches
    sizes = ND.sum(np.ones_like(lab), lab, range(1, n + 1))
    out, lit = img1.copy(), lit1.copy()
    filled = notches
    for i, sl in enumerate(ND.find_objects(lab)):
        if sizes[i] > HOLE_MAX:
            continue
        m = lab[sl] == i + 1
        if keep_out1[sl][m].any():
            continue
        y0, x0 = sl[0].start, sl[1].start
        big = (slice(max(0, y0 - 1), sl[0].stop + 1),
               slice(max(0, x0 - 1), sl[1].stop + 1))
        mm = np.zeros(lit1[big].shape, bool)
        mm[y0 - big[0].start:y0 - big[0].start + m.shape[0],
           x0 - big[1].start:x0 - big[1].start + m.shape[1]] = m
        ring = ND.binary_dilation(mm) & ~mm
        if not lit1[big][ring].all():
            continue                      # touches the open edge
        col = img1[big][ring].mean(0)
        sub = out[big]
        sub[mm] = col
        lit[big][mm] = True
        filled += int(mm.sum())
    return out, lit, filled


# -------------------------------------------------------------------- plan
def plan_field(name, parts, mb_cap=35.0, raw_cap=None):
    """(new section 0, new section 9, info) -- raises on any doubt."""
    from scipy import ndimage as ND
    import diag_common as DC
    import field_bg_native as FN
    import field_bg_pagecap as PC
    import field_bg_repack as FR
    import ff7nx_edgesmooth as ES
    import ff7nx_framesim as FS
    import ff7nx_marginblack as MB
    import ff7nx_palstates as PS
    cfg = FIELDS[name]
    sec0 = patch_script(parts[0], cfg)
    sec9 = bytes(parts[8])
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    if k != 16 * K:
        raise ValueError('pages are %d px' % px)
    cols, _h, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)

    def cell_of(o, po=32):
        if po == 34:
            u, v = struct.unpack_from('<II', sec9, o + 42)
            return (sec9[o + 34], int(round(u / UV_CELL)),
                    int(round(v / UV_CELL)))
        sx, sy = struct.unpack_from('<hh', sec9, o + 10)
        return (sec9[o + 32], sx // 16, sy // 16)

    halo, hl, l1, l2 = [], [], [], []
    users = collections.defaultdict(set)
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            users[cell_of(o, 34 if bl else 32)].add((layer, x, y))
            if sec9[o + 26]:
                raise ValueError('field already uses tile params')
            if bl:
                if layer != 2 or sec9[o + 30] != 1:
                    raise ValueError('unexpected blended record')
                if pm[sec9[o + 34]].depth != 1:
                    raise ValueError('effect already on a truecolor page')
                if struct.unpack_from('<HH', sec9, o + 18) != (16, 16):
                    raise ValueError('effect tile is not 16x16')
                if sec9[o + 22] == cfg['halo']:
                    halo.append(o)
                elif sec9[o + 22] == cfg['hl']:
                    hl.append(o)
                else:
                    raise ValueError('unexpected effect palette')
            elif layer == 1:
                l1.append(o)
            elif layer == 2:
                l2.append(o)
    if len(halo) < 100 or len(hl) < 20:
        raise ValueError('halo / highlight records not found')
    # ---- the canvas: every effect record and the art around it
    allr = halo + hl + l2
    xs = [struct.unpack_from('<h', sec9, o + 2)[0] for o in allr]
    ys = [struct.unpack_from('<h', sec9, o + 4)[0] for o in allr]
    X0, Y0 = min(xs) - 16, min(ys) - 16
    W1, H1 = max(xs) + 32 - X0, max(ys) + 32 - Y0
    W, H = W1 * K, H1 * K

    def effect_1x(recs, palette, mult):
        img = np.zeros((H1, W1, 3), np.float64)
        lit = np.zeros((H1, W1), bool)
        for o in recs:
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            pg, cx, cy = cell_of(o, 34)
            a = np.frombuffer(pm[pg].data, np.uint8, count=65536).reshape(
                256, 256)[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
            c = pal[palette][a]
            on = (c & 0x7FFF) != 0
            rgb = np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31], -1) * 8.0
            img[y - Y0:y - Y0 + 16, x - X0:x - X0 + 16] += np.where(
                on[..., None], PS.hw_light(rgb, mult), 0.0)
            lit[y - Y0:y - Y0 + 16, x - X0:x - X0 + 16] |= on
        return img, lit & (img.max(-1) > 0)

    # ---- layer 1 behind (HD, canvas): present and non-black
    F = FS.Field(list(parts))
    l1rgb = np.zeros((H, W, 3), np.float64)
    l1on = np.zeros((H, W), bool)
    for o in l1:
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        n = max(struct.unpack_from('<HH', sec9, o + 18)) or 16
        blk = F._block(o, 1, n)
        if blk is None:
            continue
        rgb, key = blk
        idx = np.arange(n * K) * rgb.shape[0] // (n * K)
        rgb, key = rgb[idx][:, idx], key[idx][:, idx]
        X, Y = (x - X0) * K, (y - Y0) * K
        xa, ya, xb, yb = max(0, X), max(0, Y), min(W, X + n * K), \
            min(H, Y + n * K)
        if xa >= xb or ya >= yb:
            continue
        l1rgb[ya:yb, xa:xb] = rgb[ya - Y:yb - Y, xa - X:xb - X]
        l1on[ya:yb, xa:xb] = True
    l1art = l1on & (l1rgb.max(-1) > SKY_MAX)
    # ---- layer 2: the composite, its alpha, the anti-aliased edge
    for o in l2:
        if pm[sec9[o + 32]].depth != 2:
            raise ValueError('layer-2 art on a paletted page')
        u, v = struct.unpack_from('<II', sec9, o + 42)
        sx, sy = struct.unpack_from('<hh', sec9, o + 10)
        if (sx // 16, sy // 16) != (int(round(u / UV_CELL)),
                                    int(round(v / UV_CELL))):
            raise ValueError('layer-2 source and UV disagree')
    tex, own, use, org, at = ES._canvas(sec9, l2, pm, px)
    m2 = tex != 0
    g = lambda a_, s_: ND.gaussian_filter(a_.astype(np.float32), s_,
                                          mode='nearest')
    a3 = np.clip((g(m2, RING_SIGMA) - 0.5) * RING_STEEP + 0.5, 0, 1)
    thick = ND.grey_dilation(ND.distance_transform_edt(m2), size=(11, 11))
    alpha = np.where(thick < RING_THIN, m2.astype(np.float32), a3)
    alpha[alpha < RING_LO] = 0.0
    # canvas (tex) <-> effect canvas offsets
    oy, ox = (org[1] - Y0) * K, (org[0] - X0) * K
    th, tw = tex.shape
    if oy < 0 or ox < 0 or oy + th > H or ox + tw > W:
        raise ValueError('layer 2 outside the effect canvas')
    # black sky behind: no layer-1 art within 6 HD px (an L2 tile ending
    # flush against layer 1 is an internal seam, not a silhouette)
    sky = ~ND.binary_dilation(l1art, iterations=6)[oy:oy + th, ox:ox + tw]
    near = ND.binary_dilation(m2 ^ ND.binary_erosion(m2), iterations=5)
    din = ND.distance_transform_edt(m2)
    # the art's own rim (Cosmos's dark 1x-stepped fringe) is part of the
    # staircase: within RIM texels of the edge the colour is the art's a
    # few texels in (thick parts only)
    rim = m2 & (din < RIM) & (thick >= RING_THIN)
    edit = near & sky & ((alpha != m2.astype(np.float32)) | rim)
    inner = din >= RIM + 1
    if not inner.any():
        raise ValueError('layer 2 has no interior')
    _d, (iy, ix) = ND.distance_transform_edt(~inner, return_indices=True)
    T = _rgb565(tex)[iy, ix]
    _d2, (jy, jx) = ND.distance_transform_edt(~m2, return_indices=True)
    thin = thick < RING_THIN
    T[thin] = _rgb565(tex)[jy, jx][thin]
    newtex = tex.copy()
    newtex[edit] = _enc(alpha[edit][:, None] * T[edit])
    newtex[edit & (alpha <= 0)] = 0
    # where the edge stays binary, alpha is the binary mask
    alpha_eff = np.where(edit, alpha, m2.astype(np.float32))
    A = np.zeros((H, W), np.float32)
    A[oy:oy + th, ox:ox + tw] = alpha_eff
    opa_bin = np.zeros((H, W), bool)
    opa_bin[oy:oy + th, ox:ox + tw] = m2
    # ---- write layer 2 back (exclusive cells only)
    z = [struct.unpack_from('<I', sec9, u_[3] + 38)[0] for u_ in use]
    blocks = {}

    def block(i):
        if i not in blocks:
            x, y, n, o = use[i]
            sx, sy = struct.unpack_from('<hh', sec9, o + 10)
            blocks[i] = np.frombuffer(pm[sec9[o + 32]].data, '<u2').reshape(
                px, px)[sy // 16 * k:sy // 16 * k + n * K,
                        sx // 16 * k:sx // 16 * k + n * K].copy()
        return blocks[i]
    for py_, px_ in zip(*np.nonzero(newtex != tex)):
        fx, fy = org[0] + px_ // K, org[1] + py_ // K
        here = ES._here(at, use, fx, fy)
        if not here:
            continue
        i = int(own[py_, px_]) if own[py_, px_] >= 0 else \
            min(here, key=lambda j: z[j])
        b_ = block(i)
        b_[py_ - (use[i][1] - org[1]) * K,
           px_ - (use[i][0] - org[0]) * K] = newtex[py_, px_]
    datas = {}
    l2_written = 0
    for i, b_ in blocks.items():
        x, y, n, o = use[i]
        pg = sec9[o + 32]
        sx, sy = struct.unpack_from('<hh', sec9, o + 10)
        if any(len(users[(pg, cx, cy)]) > 1
               for cy in range(sy // 16, sy // 16 + n // 16)
               for cx in range(sx // 16, sx // 16 + n // 16)):
            continue
        if pg not in datas:
            datas[pg] = np.frombuffer(pm[pg].data, '<u2').reshape(
                px, px).copy()
        datas[pg][sy // 16 * k:sy // 16 * k + n * K,
                  sx // 16 * k:sx // 16 * k + n * K] = b_
        l2_written += 1
    # ---- halo
    up1 = opa_bin.reshape(H1, K, W1, K).max((1, 3))
    himg, hlit = effect_1x(halo, cfg['halo'], cfg['mult'])
    himg, hlit, holes = _fill_holes(himg, hlit,
                                    ND.binary_dilation(up1, iterations=1))
    # 1997's halo dims over its last 1..2 texels before the pyramid (its
    # own soft cut, along the 1x staircase): those texels are re-grown from
    # the full halo beyond them, so the only edge left is the HD one
    band = ND.binary_dilation(up1, iterations=EDGE_BAND) & ~up1
    src = hlit & ~band & ~up1
    hg, hreach = _grow(himg, src, up1 | band, GROW + EDGE_BAND)
    hreach |= hlit
    hbig = _bicubic(hg, W, H)
    reach = np.repeat(np.repeat(ND.binary_dilation(hreach, np.ones((3, 3),
                                                                bool)),
                                K, 0), K, 1)
    hd_halo = hbig * (1.0 - A)[..., None] * reach[..., None]
    lit_in = float(himg[~up1].sum())
    lit_out = float(hd_halo.sum()) / (K * K)
    if not 0.7 < lit_out / max(lit_in, 1.0) < 1.3:
        raise ValueError('redrawn halo carries %.2fx the light'
                         % (lit_out / max(lit_in, 1.0)))
    # ---- highlights, one HD picture per multiplier value
    # (over layer 1 the light stays wherever 1997 drew it: its dark faces
    # are lit by it too)
    artmask = np.maximum(A, l1on.astype(np.float32))
    art1 = (artmask > 0.5).reshape(H1, K, W1, K).max((1, 3))
    # the highlight records' footprint: light never leaves it, and fades
    # out over HL_FEATHER px at its outer edge (1997's cells end in flat
    # 16-unit rectangles there)
    cover = np.zeros((H, W), np.float32)
    for o in hl:
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        cover[(y - Y0) * K:(y - Y0 + 16) * K, (x - X0) * K:(x - X0 + 16) * K] = 1
    feather = np.clip(ND.distance_transform_edt(cover > 0) / HL_FEATHER, 0, 1)
    wmask = artmask * cover
    hl_imgs = []
    for v in cfg['hl_values']:
        img1, lit1 = effect_1x(hl, cfg['hl'], v)
        gimg, _r = _grow(img1, lit1, art1, GROW)
        big = _bicubic(gimg, W, H)
        # 1997's highlight cells end in flat 16-unit rectangles over the
        # art: softened before the cut (the cut keeps the silhouette)
        # (normalised over the art, so the light does not fade toward the
        # silhouette: that read as a dark outline)
        wsum = np.maximum(ND.gaussian_filter(wmask, HL_BLUR), 1e-3)
        big = np.stack([ND.gaussian_filter(big[..., c] * wmask, HL_BLUR)
                        / wsum for c in range(3)], -1)
        hl_imgs.append(big * (wmask * feather)[..., None])
    # ---- new records and cells
    new_recs = []          # (template record offset, param, HD cell image)
    for o in halo:
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        Y, X = (y - Y0) * K, (x - X0) * K
        c = hd_halo[Y:Y + k, X:X + k]
        if c.max() >= EMPTY:
            new_recs.append((o, HALO_P, c))
    for i_, img in enumerate(hl_imgs):
        for o in hl:
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            Y, X = (y - Y0) * K, (x - X0) * K
            c = img[Y:Y + k, X:X + k]
            if c.max() >= EMPTY:
                new_recs.append((o, HL_P0 + i_, c))
    free_slots = [s for s in range(FX_LO, FX_HI) if s not in pm]
    place, page_cells, page_orig = [], collections.defaultdict(dict), \
        collections.defaultdict(dict)
    si, n_ = 0, 0
    for j, (o, param, c) in enumerate(new_recs):
        if n_ >= min(GRID * GRID, MAX_BIND):
            si, n_ = si + 1, 0
        if si >= len(free_slots):
            raise ValueError('%d HD cells do not fit the FX band'
                             % len(new_recs))
        s = free_slots[si]
        place.append((s, n_))
        page_cells[s][n_] = c
        page_orig[s][n_] = struct.unpack_from('<hh', sec9, o + 2)
        n_ += 1
    # ---- section 9: records
    rows = {r[0]: r for r in PS._layer_rows(sec9)}
    lyr, count_at, first, count = rows[2]
    hlset, haloset = set(hl), set(halo)
    keep = []
    for i in range(count):
        o = first + i * REC
        if o in hlset:
            continue
        r = bytearray(sec9[o:o + REC])
        if o in haloset:
            r[26], r[27] = HALO_Q, 1
        keep.append(bytes(r))
    added = []
    for (o, param, _c), (s, n) in zip(new_recs, place):
        b = bytearray(sec9[o:o + REC])
        nx, ny = n % GRID, n // GRID
        b[26], b[27] = param, 1
        b[34] = s
        struct.pack_into('<hh', b, 14, nx * 16, ny * 16)
        struct.pack_into('<II', b, 42, nx * UV_CELL, ny * UV_CELL)
        added.append(bytes(b))
    buf = bytearray(sec9)
    buf[first:first + count * REC] = b''.join(keep + added)
    struct.pack_into('<H', buf, count_at, len(keep) + len(added))
    # ---- section 9: pages
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), px)
    for pg, d in datas.items():
        q_ = plist[pg]
        plist[pg] = FN.Page(pg, q_.size_flag, 2, d.tobytes(), px)
    for s, cells in page_cells.items():
        enc = PS._encode_cells(cells, px, page_orig[s])
        while len(plist) <= s:
            plist.append(None)
        plist[s] = FN.Page(s, 0, 2, enc.astype('<u2').tobytes(), px)
    run = sum(FR._page_bytes(p.px, p.depth) for p in plist if p is not None)
    if run > mb_cap * 1048576.0:
        raise ValueError('%.2f MB over the %.1f MB cap'
                         % (run / 1048576.0, mb_cap))
    out = FN.replace_texture_block(bytes(buf), plist, t0, t1)
    other = sum(len(x) for x in parts) - len(sec9)
    if raw_cap is not None and other + len(out) > raw_cap:
        raise ValueError('raw %d over the cap %d' % (other + len(out),
                                                     raw_cap))
    over = {s: v for s, v in PC.effective_counts(out, px).items()
            if v > MAX_BIND}
    if over:
        raise ValueError('binding cap: %s' % over)
    hl_counts = collections.Counter(p_ for _o, p_, _c in new_recs
                                    if p_ >= HL_P0)
    return sec0, out, {
        'halo': sum(1 for _o, p_, _c in new_recs if p_ == HALO_P),
        'hl': [hl_counts[HL_P0 + i] for i in range(len(hl_imgs))],
        'holes': holes, 'edge_texels': int((newtex != tex).sum()),
        'l2_records': l2_written, 'slots': sorted(page_cells),
        'light': round(lit_out / max(lit_in, 1.0), 3),
        'mb': round(run / 1048576.0, 2)}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None,
                    mb_cap=35.0, raw_cap=None):
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
            parts[0], parts[8], info = plan_field(name, parts, mb_cap,
                                                  raw_cap)
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append(
            '%s: halo %d HD records (1x kept for the fade), highlights %s '
            'per level, %d pyramid edge texels anti-aliased, %d holes '
            'filled, light x%.2f, %.1f MB'
            % (name, info['halo'], '/'.join(map(str, info['hl'])),
               info['edge_texels'], info['holes'], info['light'],
               info['mb']))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  HALO HD (BUILD 620k4): las4_42 light in true colour, '
                   'anti-aliased against the pyramids, pulse and fade kept '
                   '(%s). %s=1 disables.' % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! halo hd %s: %s' % (name, why))
    return '\n'.join(out)
