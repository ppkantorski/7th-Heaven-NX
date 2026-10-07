#!/usr/bin/env python3
"""ff7nx_wheelfix.py -- an animated wheel that animates only the wheel.
BUILD 618z14; rim kept static BUILD 619.

ztruck (hardware 10-06): "this spinning tire is causing pixel distortions on
the paint of the car ... as it goes from frame to frame, i notice the paint
has slight distortions on it, kind of in the form of the cutout".

WHY. The rear wheel is 10 layer-2 tiles (x -112..-48, y 88..104) with three
animation states (param 1, states 1/2/4) drawn over a static copy of the same
place. Each state tile is a whole 16x16-unit square: tyre, wheel arch AND
the paint around it, and Cosmos upscaled each frame on its own. So the
paint differs from frame to frame (noise of 8-16 levels on most texels) and
the arch's cut-out edge moves a few texels between frames -- the paint
shimmers in the shape of the arch while the wheel turns.

WHAT. Only the tyre turns. In the static copy the tyre is the dark, grey
region reaching into the wheel (the whitewall ring, fitted as a circle,
finds the wheel; the arch's paint is orange, so saturation separates the two
even in the arch's shadow); every state texel outside that region is made
transparent, so the static copy's paint and arch edge show in every frame.
The user: "the wheel effect should just be cleanly applied to all the dark
regions ... where the paint of the car meets the tire".
Nothing outside that disk changes between frames any more; inside it the
three frames are exactly as before. A cell another record uses is left
alone (refused).

SEVENTH_NX_NO_WHEEL_FIX=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_WHEEL_FIX'
K = 3
TARGETS = {'ztruck': {'layer': 2, 'box': (-128, 72, -32, 121)}}
RADIUS_K = 1.65       # the tread's outer edge, x the whitewall radius
HUB_K = 1.3           # always the wheel
TYRE_SAT = 40         # tyre texels are grey ...
TYRE_LUM = 100        # ... and dark; the arch's paint is orange
EDGE = 3              # BUILD 619: texels of the tyre's rim kept static


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _rgbf(t):
    t = t.astype(np.int64)
    return np.stack([((t >> 11) & 31) << 3, ((t >> 6) & 31) << 3,
                     (t & 31) << 3], -1).astype(np.float32)


def plan(name, sec9):
    import diag_common as DC
    import field_bg_native as FN
    cfg = TARGETS[name]
    s9 = bytes(sec9)
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    if k != 16 * K:
        raise ValueError('pages are %d px' % px)
    x0, y0, x1, y1 = cfg['box']
    anim = collections.defaultdict(dict)
    base = {}
    users = collections.Counter()
    for lay, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            pg = s9[o + 34] if s9[o + 28] else s9[o + 32]
            sx, sy = struct.unpack_from('<hh', s9, o + (14 if s9[o + 28]
                                                         else 10))
            users[(pg, sx // 16, sy // 16)] += 1
            if lay != cfg['layer'] or s9[o + 28]:
                continue
            x, y = struct.unpack_from('<hh', s9, o + 2)
            if not (x0 <= x < x1 and y0 <= y < y1):
                continue
            if struct.unpack_from('<H', s9, o + 18)[0] not in (0, 16):
                raise ValueError('not a 16-unit tile at %d,%d' % (x, y))
            if pm[s9[o + 32]].depth != 2:
                raise ValueError('paletted tile at %d,%d' % (x, y))
            if s9[o + 26]:
                if s9[o + 27] in anim[(x, y)]:
                    raise ValueError('two state-%d tiles at %d,%d'
                                     % (s9[o + 27], x, y))
                anim[(x, y)][s9[o + 27]] = o
            else:
                base.setdefault((x, y), o)
    if not anim:
        raise ValueError('no animated tiles')
    states = sorted({s for v in anim.values() for s in v})
    if any(sorted(v) != states for v in anim.values()):
        raise ValueError('positions have different states')
    if set(anim) - set(base):
        raise ValueError('an animated tile with no static copy under it')
    xs = sorted({p[0] for p in anim})
    ys = sorted({p[1] for p in anim})
    W, H = (xs[-1] - xs[0] + 16) * K, (ys[-1] - ys[0] + 16) * K

    def cell(o):
        sx, sy = struct.unpack_from('<hh', s9, o + 10)
        return s9[o + 32], sx // 16, sy // 16

    def tex(o):
        pg, cx, cy = cell(o)
        return np.frombuffer(pm[pg].data, '<u2').reshape(px, px)[
            cy * k:cy * k + k, cx * k:cx * k + k]
    b = np.zeros((H, W), np.uint16)
    for (x, y) in anim:
        Y, X = (y - ys[0]) * K, (x - xs[0]) * K
        b[Y:Y + k, X:X + k] = tex(base[(x, y)])
    B = _rgbf(b)
    lum, sat = B.mean(-1), B.max(-1) - B.min(-1)
    ring = (lum > 150) & (sat < 40)
    yy, xx = np.nonzero(ring)
    if len(xx) < 100:
        raise ValueError('no whitewall ring')
    keep = np.ones(len(xx), bool)
    for _ in range(4):                      # fit, keep the ring, refit
        A = np.c_[2 * xx[keep], 2 * yy[keep], np.ones(keep.sum())]
        cx_, cy_, c = np.linalg.lstsq(
            A, (xx[keep] ** 2 + yy[keep] ** 2).astype(float), rcond=None)[0]
        r = float(np.sqrt(c + cx_ ** 2 + cy_ ** 2))
        keep = np.abs(np.hypot(xx - cx_, yy - cy_) - r) < 4
    if keep.mean() < 0.4 or keep.sum() < 100 or not 15 < r < 80:
        raise ValueError('the whitewall is not a circle (r %.1f, %d%% on it)'
                         % (r, 100 * keep.mean()))
    from scipy import ndimage as ND
    Yg, Xg = np.mgrid[:H, :W]
    rad = np.hypot(Xg - cx_, Yg - cy_)
    # the tyre: the dark, grey (unsaturated) region of the static copy that
    # reaches into the wheel, holes filled, plus the hub and whitewall --
    # it ends exactly where the arch's paint begins
    dark = (sat < TYRE_SAT) & (lum < TYRE_LUM)
    lab, _n = ND.label(dark)
    touch = set(np.unique(lab[(rad < RADIUS_K * r) & dark]).tolist()) - {0}
    tyre = np.isin(lab, sorted(touch))
    tyre = ND.binary_fill_holes(tyre | (rad <= HUB_K * r))
    tyre = ND.binary_opening(tyre, iterations=1) | (rad <= HUB_K * r)
    # BUILD 619 (hardware 10-06): "a thin white outline around the edge of
    # the tire between the tire and the truck's paint ... constantly under
    # distortion". MEASURED: each frame's own rim sits a texel or two off the
    # static copy's -- frame vs static differ by 11-12 levels at depth 1 of
    # the mask, 5-6 at depth 2, 3.5 at depth 3, and ~3 (the tread turning)
    # from there in; the light texels between tyre and paint are all within
    # 3 texels of the edge. So the last EDGE texels of the tyre come from the
    # static copy too: the rim is one picture in every frame and the tread
    # still turns inside it.
    core = ND.binary_erosion(tyre, iterations=EDGE, border_value=1)
    outside = ~(core | (rad <= HUB_K * r))
    datas = {}
    cleared = 0
    for (x, y), v in sorted(anim.items()):
        Y, X = (y - ys[0]) * K, (x - xs[0]) * K
        o_mask = outside[Y:Y + k, X:X + k]
        if not o_mask.any():
            continue
        for st, o in sorted(v.items()):
            pg, ccx, ccy = cell(o)
            if users[(pg, ccx, ccy)] > 1:
                raise ValueError('state cell shared at %d,%d' % (x, y))
            if pg not in datas:
                datas[pg] = np.frombuffer(pm[pg].data, '<u2').reshape(
                    px, px).copy()
            blk = datas[pg][ccy * k:ccy * k + k, ccx * k:ccx * k + k]
            cleared += int((o_mask & (blk != 0)).sum())
            blk[o_mask] = 0
    plist, t0, t1 = FN.parse_texture_block(s9, px)
    for pg, d in datas.items():
        q = plist[pg]
        plist[pg] = FN.Page(pg, q.size_flag, 2, d.tobytes(), q.px)
    info = {'tiles': len(anim), 'states': len(states),
            'radius': round(r * RADIUS_K / K, 1), 'cleared': cleared}
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
            parts[8], info = plan(name, parts[8])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s: %d tiles x %d frames, %d texels outside '
                           'the tyre made transparent'
                           % (name, info['tiles'], info['states'],
                              info['cleared']))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  WHEEL FIX (BUILD 618z14): animated wheel frames keep '
                   'only the tyre; the paint comes from the static copy (%s). '
                   '%s=1 disables.' % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! wheel fix %s: not applied -- %s' % (name, why))
    return '\n'.join(out)
