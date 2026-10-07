#!/usr/bin/env python3
"""ff7nx_lampfill.py -- light the lamp heads inside their own glow.

BUILD 617. games (Wonder Square): every lamp post stands in a static
additive halo (layer 2, mode 1, param 0) on a truecolor page. Cosmos's halo
has each lamp's silhouette cut out of it -- the pole, the arms and the two
egg-shaped lamp heads -- so on hardware the heads read as dark holes in
the middle of their own light ("missing the inner portions"). Fill the
HEADS, not the poles (user direction): only dark blobs too wide for a pole
or an arm, surrounded by the halo, in a warm (lamp) halo -- the WONDER
sign's cyan glow and its letters are left alone.

Per head: texels much darker than the halo around them (the halo estimated
by a grey closing wider than a head), opened with a square wider than a
pole or an arm so only the heads survive, then grown back over their own
soft rim. Every head is filled solid with one colour, the brightest texel of any
head's fringe (so all the lamps match -- user direction), its rim blended up to it over a few
texels, and only ever brighter. Every texel written
belongs to exactly one tile whose cell no other record reads. Records are
not touched. SEVENTH_NX_NO_LAMP_FILL=1 disables.
"""
from __future__ import annotations

import os
import struct
from collections import deque

import numpy as np

import diag_common as DC
import field_bg_native as FN

OFF_ENV = 'SEVENTH_NX_NO_LAMP_FILL'
UV_SCALE = 10_000_000
UNIT = 16
TARGETS = frozenset({'games'})
REF_K = 41            # texels (768): grey closing wider than a lamp head
DARK = 0.55           # of the halo: a head texel
RIM = 0.85            # of the halo: the head's soft rim
OPEN_K = 9            # texels: wider than a pole or an arm
GROW_K = 3            # texels: the head's soft rim
MIN_REF = 60.0        # 0..255: only inside a real halo
AREA = (400, 8000)    # texels per head
ASPECT = 1.2          # head bbox width / height, at least
RING_LIT = 0.6        # of the ring around a head that must be halo
POLE_LOOK = 12        # texels below a head searched for the pole
POLE_W = 8            # texels: a pole is at most this wide
FRINGE = 12           # texels: the halo around a head the peak comes from
FEATHER = 3          # texels: rim blended up to the peak


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _unpack(v):
    v = v.astype(np.int32)
    return np.stack((((v >> 11) & 31) * 255.0 / 31,
                     ((v >> 6) & 31) * 255.0 / 31,
                     (v & 31) * 255.0 / 31), -1).astype(np.float32)


def _pack(rgb):
    q = np.clip(np.rint(np.asarray(rgb) * 31.0 / 255.0), 0, 31).astype(
        np.int32)
    return ((q[..., 0] << 11) | ((q[..., 1] << 1) << 5) | q[..., 2]).astype(
        np.uint16)


def _filt(a, k, op):
    """Separable k x k max/min filter, edges replicated."""
    r = k // 2
    for ax in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[ax] = (r, r)
        p = np.pad(a, pad, mode='edge')
        w = np.lib.stride_tricks.sliding_window_view(p, k, axis=ax)
        a = op(w, axis=-1)
    return a


def _boxsum(m, k):
    r = k // 2
    p = np.pad(m.astype(np.int32), r)
    c = np.pad(p.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    H, W = m.shape
    return c[k:k + H, k:k + W] - c[:H, k:k + W] - c[k:k + H, :W] + c[:H, :W]


def _erode(m, k):
    return _boxsum(m, k) == k * k


def _dilate(m, k):
    return _boxsum(m, k) > 0


def _components(m):
    lab = np.zeros(m.shape, np.int32)
    n = 0
    H, W = m.shape
    for y, x in zip(*np.nonzero(m)):
        if lab[y, x]:
            continue
        n += 1
        lab[y, x] = n
        q = deque([(y, x)])
        while q:
            cy, cx = q.popleft()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < H and 0 <= nx < W and m[ny, nx] and not lab[ny, nx]:
                    lab[ny, nx] = n
                    q.append((ny, nx))
    return lab, n


def plan_field(name, sec9):
    """[(slot, py, px, new565)] and stats."""
    st = {'texels': 0, 'heads': 0, 'rejected': 0}
    if name.lower() not in TARGETS:
        return [], st
    pages_l, ts, _te, page_px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pages_l if p is not None}
    scale = page_px // 256
    step = UNIT * scale
    readers = {}
    fx = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            uv = bytes(sec9[o + 42:o + 50])
            keys = [(sec9[o + 32], uv)]
            if sec9[o + 28]:
                keys.append((sec9[o + 34], uv))
            for k in keys:
                readers[k] = readers.get(k, 0) + 1
            if (sec9[o + 28] and layer == 2 and sec9[o + 26] == 0
                    and sec9[o + 30] == 1):
                fx.append(o)
    if not fx:
        return [], st
    xs = [struct.unpack_from('<h', sec9, o + 2)[0] for o in fx]
    ys = [struct.unpack_from('<h', sec9, o + 4)[0] for o in fx]
    x0, y0 = min(xs), min(ys)
    W = (max(xs) - x0 + UNIT) * scale
    H = (max(ys) - y0 + UNIT) * scale
    light = np.zeros((H, W, 3), np.float32)
    owner = np.full((H, W), -1, np.int32)
    count = np.zeros((H, W), np.int16)
    cells = []
    for i, o in enumerate(fx):
        slot = sec9[o + 34]
        p = pages.get(slot)
        if p is None or p.depth != 2 or p.px != page_px or p.size_flag:
            cells.append(None)
            continue
        u, v = struct.unpack_from('<II', sec9, o + 42)
        cx = int(round(u / UV_SCALE * 16)) * step
        cy = int(round(v / UV_SCALE * 16)) * step
        blk = np.frombuffer(p.data, '<u2').reshape(page_px, page_px)[
            cy:cy + step, cx:cx + step]
        dx, dy = (xs[i] - x0) * scale, (ys[i] - y0) * scale
        light[dy:dy + step, dx:dx + step] += _unpack(blk) * (
            blk != FN.EMPTY)[..., None]
        count[dy:dy + step, dx:dx + step] += 1
        excl = readers.get((slot, bytes(sec9[o + 42:o + 50]))) == 1
        owner[dy:dy + step, dx:dx + step] = i if excl else -2
        cells.append((slot, cx, cy, dx, dy))
    usable = (count == 1) & (owner >= 0)
    lum = light.max(-1)
    ref = _filt(_filt(lum, REF_K, np.max), REF_K, np.min)
    halo = ref >= MIN_REF
    dark = halo & (lum < DARK * ref)
    loose = halo & (lum < RIM * ref)
    # opening: only blobs wider than a pole or an arm survive; a small grow
    # takes back the head's own soft rim and nothing of the pole beside it
    heads = _dilate(_dilate(_erode(dark, OPEN_K), OPEN_K), GROW_K) & loose
    lab, n = _components(heads)
    fill = np.zeros((H, W), bool)
    for k in range(1, n + 1):
        comp = lab == k
        area = int(comp.sum())
        ring = _dilate(comp, 9) & ~comp
        rl = ring & ~loose
        cy_, cx_ = np.nonzero(comp)
        bw, bh = cx_.max() - cx_.min() + 1, cy_.max() - cy_.min() + 1
        # a lamp head is an egg lying on its side: wider than tall (the
        # dark pillar bracket beside the front-left lamp is not)
        ok = (AREA[0] <= area <= AREA[1] and ring.any()
              and bw >= ASPECT * bh)
        if ok:
            lit = rl.sum() / float(ring.sum())
            col = light[rl].mean(0) if rl.any() else np.zeros(3)
            warm = col[0] >= 0.75 * col[1] and col[2] <= 0.85 * col[1]
            ok = lit >= RING_LIT and warm
        if not ok:
            st['rejected'] += 1
            continue
        # the pole: where a head's blob swallowed it (two heads touching,
        # the pole between them), it continues straight below the blob as
        # a thin dark run. Carry those columns up through the blob and keep
        # them out of the fill.
        bot = int(cy_.max())
        below = dark[bot + 2:min(bot + 2 + POLE_LOOK, H),
                     int(cx_.min()):int(cx_.max()) + 1]
        if below.shape[0] >= POLE_LOOK // 2:
            cols = np.nonzero(below.mean(0) >= 0.8)[0]
            if 0 < len(cols) <= POLE_W:
                c0_ = max(int(cx_.min()) + int(cols.min()) - 1, 0)
                c1_ = int(cx_.min()) + int(cols.max()) + 2
                comp[:, c0_:c1_] &= ~dark[:, c0_:c1_]
                st['poles'] = st.get('poles', 0) + 1
        fill |= comp
        st['heads'] += 1
    if not fill.any():
        return [], st
    # Every head is lit with ONE colour (user direction): the brightest
    # texel of any head's fringe -- the halo within FRINGE texels of a head
    # -- so all the lamps match. The rim just outside each head blends up to
    # it over FEATHER texels.
    f = light.copy()
    lab2, n2 = _components(fill)
    fringe = _dilate(fill, 2 * FRINGE + 1) & ~loose & halo
    if not fringe.any():
        return [], st
    yy, xx = np.nonzero(fringe)
    j = int(np.argmax(lum[yy, xx]))
    peak = light[yy[j], xx[j]]
    st['peak'] = tuple(int(round(c)) for c in peak)
    for k in range(1, n2 + 1):
        comp = lab2 == k
        f[comp] = np.maximum(light[comp], peak)
        inner = comp
        for d in range(1, FEATHER + 1):
            ring = _dilate(inner, 3) & ~inner & ~fill
            w = 1.0 - d / float(FEATHER + 1)
            f[ring] = np.maximum(f[ring], light[ring] * (1 - w) + peak * w)
            fill |= ring
            inner = inner | ring
    plans = []
    for y, x in zip(*np.nonzero(fill & usable)):
        slot, cx, cy, dx, dy = cells[owner[y, x]]
        v = int(_pack(f[y, x]))
        if v == FN.EMPTY:
            continue
        plans.append((slot, cy + y - dy, cx + x - dx, v))
    st['texels'] = len(plans)
    return plans, st


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    import ff7nx_fxseam as FS
    total = {'fields': 0, 'texels': 0, 'names': [], 'refused': []}
    if disabled():
        return total
    encode = encode or archive.encode_field
    for name in sorted(TARGETS):
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            payload = payloads.get(name)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            plans, st = plan_field(name, parts[8])
            if not plans:
                continue
            parts[8] = FS.apply_plans(parts[8], plans)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append(
                (name, '%s: %s' % (type(exc).__name__, str(exc)[:80])))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['fields'] += 1
        total['texels'] += st['texels']
        total['names'].append('%s:%d head(s), %d texel(s)' % (
            name, st['heads'], st['texels']))
    return total


def summarise(st):
    out = []
    if st.get('fields'):
        out.append('  LAMP FILL (BUILD 617): lamp heads lit inside their own '
                   'halo (%s). %s=1 disables.' % (', '.join(st['names']),
                                                  OFF_ENV))
    if st.get('refused'):
        out.append('  ! lamp fill: %s' % ', '.join(
            '%s: %s' % r for r in st['refused']))
    return '\n'.join(out)
