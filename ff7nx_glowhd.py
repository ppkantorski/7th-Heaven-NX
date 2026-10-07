#!/usr/bin/env python3
"""ff7nx_glowhd.py -- a 1x animated additive glow redrawn in true colour,
its animation frames kept. BUILD 618z8.

psdun_4 (hardware 10-05): "psdun_4 has very rough choppy edges, not smooth
... blocky on what it is overlapping with like the rocks in the background.
can that be true color and animated safely? ... i dont want to lose frames".

THE GLOW. 876 additive records on layer 2: 146 positions x 6 animation
states (param 1, the script's BGON/BGOFF cycle), paletted pages 15-18, all
cells exclusive. The 1997 art is 1x, so every edge of the light -- against
the rocks and inside it (the pillar, the leaf) -- is a 3-pixel staircase.
Cosmos's DDS for these pages fails the art check (ff7nx_fxpages art veto),
so there is no HD source to convert.

WHAT THIS DOES (FIELDS only):
  * each state's glow is assembled at 1x across the whole field (so the
    upscale has no cell seams), converted to RGB through its palette at the
    brightness measured on hardware (ADPAL ~ -4, see below), and upscaled
    to 3x with a smooth (bicubic) filter: the light's own gradients and its
    inner bright/dim edges become smooth instead of stepped;
  * its outer edge against the foreground rock is cut along the rock's HD
    silhouette (ff7nx_fxcut.rock_mask) with a soft 0.8 px edge, so the light
    ends cleanly at the rock instead of spilling over in steps;
  * pages 15-18 become truecolor 768 (additive blend is wired for truecolor
    slots 15..23), every record keeps its page, cell, param and state: the
    6-frame animation is untouched.

WHAT IT COSTS. The palette is no longer read for this glow. The field's
script slowly changes the glow's brightness through the palette (ADPAL on
var5[6], decremented every ~2 s while it is above 10): on hardware it
measured -2 (5-bit units) in one screenshot and -6 in a later one. The true
colour glow keeps ONE level (BRIGHTNESS, the middle of those). Texture
memory 15.4 -> 27.6 MB. SEVENTH_NX_NO_GLOW_HD=1 restores the 1x glow (and
ff7nx_fxcut's edge treatment).
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_GLOW_HD'
FIELDS = ('psdun_4',)
BRIGHTNESS = -4             # 5-bit ADPAL offset frozen into the true colour
K = 3
EDGE_SIGMA = 0.8            # HD px
EDGE_TEXELS = 2             # 1x texels from the glow's outer edge
EDGE_DIM = 0.8              # dimmer than this x the inner texel: replaced
EDGE_DARK = 6.0             # 0..255: an outline pixel is this much darker
EDGE_CURVE = 7.0            # HD px: the outline-only edge's steps smoothed
EDGE_CURVE_COL = 2.0        # HD px: the colour-decided edge, lightly
EDGE_SHARP = 4.0            # then re-sharpened (edge ~1.5 px)
EDGE_BAND = 3.5             # HD px either side of rock_mask's boundary
EDGE_SURE = 3.0             # HD px: sure rock / background beyond this
EDGE_SIGMA_COL = 5.0        # HD px, the local colour means
EDGE_CONF = 24.0            # 0..255 colour distance needed to unmix


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def rock_alpha(img, rock):
    """Per HD pixel, how much of it is the foreground rock (0..1), from
    layer 1's own colours (618z9). rock_mask decides rock/background in
    whole pixels from the smoothed 1x outline and is off by 1-2 px along
    the edge (hardware 10-05: a thin glow line on the rock in places, a
    dark notch between rock and glow in others). Within EDGE_BAND px of
    its boundary each pixel is unmixed against the local rock and
    background colours (means over the sure parts nearby): the rock's own
    anti-aliased edge decides, so the glow neither overlaps the rock nor
    leaves a gap. Where rock and background colours are too close to tell
    (EDGE_CONF), the boundary is a soft version of rock_mask's."""
    from scipy import ndimage as ND
    din = ND.distance_transform_edt(rock)
    dout = ND.distance_transform_edt(~rock)
    inner = rock & (din > EDGE_SURE)
    outer = ~rock & (dout > EDGE_SURE)

    def lmean(m):
        w = ND.gaussian_filter(m.astype(np.float32), EDGE_SIGMA_COL)
        v = np.stack([ND.gaussian_filter(img[..., c] * m, EDGE_SIGMA_COL)
                      for c in range(3)], -1)
        return v / np.maximum(w, 1e-4)[..., None], w
    R, wr = lmean(inner)
    B, wb = lmean(outer)
    d = R - B
    n2 = (d * d).sum(-1)
    t = np.clip(((img - B) * d).sum(-1) / np.maximum(n2, 1.0), 0.0, 1.0)
    # a dark outline between the two (darker than both: Cosmos's upscale of
    # the 1997 cut line, a sawtooth of near-black dots) is background, so
    # the glow covers it instead of leaving a dotted dark line
    # (only where the rock is the brighter side: against a bright
    # background a dark pixel is the dark rock itself)
    lum = img.mean(-1)
    lit_rock = R.mean(-1) > B.mean(-1) + EDGE_DARK
    dark = lit_rock & (lum < np.minimum(R.mean(-1), B.mean(-1)) - EDGE_DARK)
    t = np.where(dark, 0.0, t)
    t = ND.gaussian_filter(t, 0.5)
    conf = np.clip((np.sqrt(n2) - EDGE_CONF) / EDGE_CONF, 0.0, 1.0)
    conf = conf * (wr > 0.05) * (wb > 0.05)
    # where colour cannot decide (dark rock against the dark cave wall),
    # the edge is rock_mask's outline SMOOTHED into a curve: blurred over
    # one 1x texel (removes its stair steps), then re-sharpened to a
    # ~1.5 px anti-aliased edge (hardware 10-05: "this edge should be a
    # smooth curve")
    # each decision as a smooth curve: blurred (stair steps, thorns and
    # single-pixel decisions go), then re-sharpened to an anti-aliased
    # edge of ~1.5 px (hardware 10-05: "this edge should be a smooth
    # curve", "there is what looks like a thorn on it"). The outline-only
    # decision (rock_mask, built from the 1x staircase) is smoothed much
    # more than the colour one, which follows the HD rock itself.
    col = np.where(rock & (din > EDGE_BAND), 1.0,
                   np.where(~rock & (dout > EDGE_BAND), 0.0, t))
    sm_col = ND.gaussian_filter(col, EDGE_CURVE_COL)
    sm_geo = ND.gaussian_filter(rock.astype(np.float32), EDGE_CURVE)
    cs = ND.gaussian_filter(conf, EDGE_CURVE)
    sm = cs * sm_col + (1 - cs) * sm_geo
    a = np.clip((sm - 0.5) * EDGE_SHARP + 0.5, 0.0, 1.0)
    conf = conf * lit_rock
    return a, np.where((din > EDGE_BAND) | (dout > EDGE_BAND), 0.0, conf)


def plan_field(parts, debug=None):
    """(new section 9, info) -- raises on any doubt."""
    from PIL import Image
    from scipy import ndimage as ND
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_marginblack as MB
    import ff7nx_fxcut as FC
    sec9 = bytes(parts[8])
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    if k != 16 * K:
        raise ValueError('pages are %d px' % px)
    cols, _h, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    users = collections.Counter()
    l1, fx = {}, []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            pg = sec9[o + 34] if bl else sec9[o + 32]
            sx, sy = struct.unpack_from('<hh', sec9, o + (14 if bl else 10))
            users[(pg, sx // 16, sy // 16)] += 1
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            if layer == 1 and not bl and not sec9[o + 26]:
                l1.setdefault((x, y), o)
            elif layer == 2 and bl and sec9[o + 26]:
                fx.append(o)
    if not fx:
        raise ValueError('no animated glow')
    slots = sorted({sec9[o + 34] for o in fx})
    if any(pm[s].depth != 1 for s in slots) or not all(
            0x0F <= s <= 0x17 for s in slots):
        raise ValueError('glow pages %s are not paletted FX slots' % slots)
    for o in fx:
        sx, sy = struct.unpack_from('<hh', sec9, o + 14)
        if users[(sec9[o + 34], sx // 16, sy // 16)] != 1:
            raise ValueError('glow cells are shared')
        if struct.unpack_from('<HH', sec9, o + 18) != (16, 16):
            raise ValueError('glow tile is not 16x16')
    # every record of those pages must be a glow record (we rewrite pages)
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            pg = sec9[o + 34] if bl else sec9[o + 32]
            if pg in slots and o not in set(fx):
                raise ValueError('page %d is shared with other records' % pg)
    xs = [struct.unpack_from('<h', sec9, o + 2)[0] for o in fx]
    ys = [struct.unpack_from('<h', sec9, o + 4)[0] for o in fx]
    X0, Y0 = min(xs) - 16, min(ys) - 16
    X1, Y1 = max(xs) + 32, max(ys) + 32
    W1, H1 = X1 - X0, Y1 - Y0
    W, H = W1 * K, H1 * K
    # layer 1 at HD, for the rock silhouette
    img = np.zeros((H, W, 3), np.float32)
    for (x, y), o in l1.items():
        if not (X0 <= x < X1 and Y0 <= y < Y1):
            continue
        p = pm.get(sec9[o + 32])
        if p is None:
            continue
        sx, sy = struct.unpack_from('<hh', sec9, o + 10)
        if p.depth == 2:
            rgb = FC._rgb565(np.frombuffer(p.data, '<u2').reshape(px, px)[
                sy // 16 * k:sy // 16 * k + k, sx // 16 * k:sx // 16 * k + k])
        else:
            a = np.frombuffer(p.data, np.uint8, count=65536).reshape(
                256, 256)[sy:sy + 16, sx:sx + 16]
            rgb = np.repeat(np.repeat(FC._rgb555(pal[sec9[o + 22]][a]), K, 0),
                            K, 1)
        img[(y - Y0) * K:(y - Y0 + 16) * K, (x - X0) * K:(x - X0 + 16) * K] \
            = rgb
    # each state's glow at 1x, in RGB at the frozen brightness
    states = sorted({sec9[o + 27] for o in fx})
    glow1 = {s: np.zeros((H1, W1, 3), np.float32) for s in states}
    lit1 = {s: np.zeros((H1, W1), bool) for s in states}
    for o in fx:
        s = sec9[o + 27]
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        sx, sy = struct.unpack_from('<hh', sec9, o + 14)
        a = np.frombuffer(pm[sec9[o + 34]].data, np.uint8, count=65536
                          ).reshape(256, 256)[sy:sy + 16, sx:sx + 16]
        c = pal[sec9[o + 22]][a]
        lit = (c & 0x7FFF) != 0
        c5 = np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31],
                      -1).astype(np.float32)
        c5 = np.clip(c5 + BRIGHTNESS, 0, 31)
        glow1[s][y - Y0:y - Y0 + 16, x - X0:x - X0 + 16] = np.where(
            lit[..., None], c5 * 8.0, 0.0)
        lit1[s][y - Y0:y - Y0 + 16, x - X0:x - X0 + 16] = lit
    union = np.zeros((H1, W1), bool)
    for s in states:
        union |= lit1[s]
    up = lambda m: np.repeat(np.repeat(m, K, 0), K, 1)
    rock = FC.rock_mask(img, up(union))
    ra, conf = rock_alpha(img, rock)
    cover = 1.0 - ra
    # where the colours say "surely background" (confident unmixing, no
    # rock in the pixel), the glow may be brightened at its edge
    bright_ok = ND.gaussian_filter(
        np.clip(conf * cover - 0.5, 0, 0.5) * 2.0, 1.0)
    # the HD glow: bicubic upscale of each state, cut by the soft cover
    hd = {}

    def upscale(g):
        return np.clip(np.stack([np.asarray(Image.fromarray(g[..., c]).resize(
            (W, H), Image.BICUBIC)) for c in range(3)], -1), 0, 255)

    def extend(g, m):
        # extend the light a little past its 1x outline before the upscale,
        # so the rock cut (not the 1x staircase) decides where it ends
        dist, (iy, ix) = ND.distance_transform_edt(~m, return_indices=True)
        return np.where((dist <= 2)[..., None] & ~m[..., None], g[iy, ix], g)
    for s in states:
        g = glow1[s]
        m = lit1[s]
        big = upscale(extend(g, m)) if m.any() else upscale(g)
        # the 1997 glow's outermost texels are often dimmer (its own 1x
        # anti-aliasing against the old rock): upscaled they make a dotted
        # dark line along the rock. Within EDGE_TEXELS of the unlit side a
        # texel under EDGE_DIM x its nearest inner texel takes that texel's
        # colour -- used only where the pixel is surely background (618z9).
        core = ND.binary_erosion(m, np.ones((3, 3), bool),
                                 iterations=EDGE_TEXELS) if m.any() else m
        if core.any():
            _dc, (cy_, cx_) = ND.distance_transform_edt(
                ~core, return_indices=True)
            ref = g[cy_, cx_]
            dim = m & ~core & (g.max(-1) < EDGE_DIM * ref.max(-1))
            g2 = np.where(dim[..., None], ref, g)
            big2 = upscale(extend(g2, m))
            big = big + (np.maximum(big, big2) - big) * bright_ok[..., None]
        big = big * cover[..., None]
        # outside every 1x texel this state ever lit (grown by 2), nothing
        reach = up(ND.binary_dilation(m, np.ones((5, 5), bool)))
        hd[s] = np.where(reach[..., None], big, 0.0)
    if debug is not None:
        debug.update(img=img, rock=rock, hd=hd, cover=cover)
    # encode into the same cells, pages now truecolor
    datas = {s_: np.zeros((px, px), np.uint16) for s_ in slots}
    for o in fx:
        s = sec9[o + 27]
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        sx, sy = struct.unpack_from('<hh', sec9, o + 14)
        Y, X = (y - Y0) * K, (x - X0) * K
        cell = hd[s][Y:Y + k, X:X + k]
        q = np.clip(np.floor(cell / 8.0 + 0.5), 0, 31).astype(np.uint32)
        v = (q[..., 0] << 11 | (q[..., 1] << 1) << 5 | q[..., 2]).astype(
            np.uint16)
        v[q.max(-1) == 0] = 0
        cy, cx = sy // 16, sx // 16
        datas[sec9[o + 34]][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = v
    plist, t0, t1 = FN.parse_texture_block(sec9, px)
    for s_, d in datas.items():
        q_ = plist[s_]
        plist[s_] = FN.Page(s_, q_.size_flag, 2, d.tobytes(), px)
    new = FN.replace_texture_block(sec9, plist, t0, t1)
    return new, {'records': len(fx), 'pages': slots, 'states': len(states)}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': [], 'done': set()}
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
            parts[8], info = plan_field(parts)
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['done'].add(name)
        st['names'].append('%s: %d glow records over %d states, pages %s '
                           'truecolor' % (name, info['records'],
                                          info['states'],
                                          ','.join(map(str, info['pages']))))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  GLOW HD (BUILD 618z8): 1x animated glow redrawn in true '
                   'colour, frames kept, brightness frozen at %d (%s). %s=1 '
                   'disables.' % (BRIGHTNESS, '; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! glow hd %s: %s' % (name, why))
    return '\n'.join(out)
