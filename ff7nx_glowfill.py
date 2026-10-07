#!/usr/bin/env python3
"""ff7nx_glowfill.py -- carry an additive glow down over the hole cut into it.

BUILD 617. sninn_2 (Icicle Inn, upstairs): the window light in the lower
left is a static additive layer-2 FX sheet on the 256px PALETTED page 15.
The 1997 sheet has a hole stepped out of its bottom (a mound of snow the
light used to stop at) and the 16:9 margin cells continue that hole with a
different outline, so at the 4:3 edge the glow jumps and below it the plain
snow shows. By day white glow over white snow hides it; at night the snow
is tinted and the hole reads as "a patch missing with rough edges"
(hardware report, night, after 617a). Carry the light down over the hole.

What is written: the region's FX cells, re-drawn from Cosmos's own sheet
for them where the provider has it (smooth, and continuous across the 4:3
edge -- the 1997 cells and the Cosmos-painted margin are not; used only if
it agrees with what the cells draw today, else the cells' own light with the
margin side faded into the 4:3 side). The hole -- texels with (near) no
light connected to the region's bottom edge -- and everything under each
column's brightest texel is filled with that peak light carried down,
blurred mostly across columns so it forms one sheet, and blended in with a
feathered weight; only ever brighter. Quantised to the record's own
palette. Every cell must be read by exactly one record, a static (param 0)
mode-1 additive layer-2 record on a paletted page; a record on a script-
animated palette is moved onto the region's static palette (FINDINGS-74:
one the page's 4:3 records use). Records keep page, UV, position, blend and state. SEVENTH_NX_NO_GLOW_FILL=1
disables.
"""
from __future__ import annotations

import os
import struct
from collections import deque

import numpy as np

import diag_common as DC
import field_bg_native as FN

OFF_ENV = 'SEVENTH_NX_NO_GLOW_FILL'
UV_SCALE = 10_000_000
# field -> region (field units, x0, y0, x1, y1; exclusive ends) and the
# static palette its cells use
TARGETS = {'sninn_2': {'box': (-224, -24, -48, 120), 'palette': 2,
                       'seam': -160}}
DARK = 48.0           # max channel below this = no light (the hole + rim)
ITERS = 1500
FEATHER = 6          # columns either side of the hole that blend in
PEAK_WIN = 8
PEAK_SIGMA = 6.0
FEATHER_SIGMA = 2.0
CARRY_SY, CARRY_SX = 3.0, 12.0   # carried light's blur, rows / columns
RAMP = 6.0           # texels over which the fill rises to the peak
GROW = 2             # the hole's dim rim, texels
SEAM_R = 16          # margin columns the seam correction fades over
SEAM_SIGMA = 1.5
COSMOS_AGREE = 12.0  # mean abs, 0..255, over texels both light
MAX_TEXELS = 0.30     # of the region


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _palettes(sec4):
    import render_field as RF
    return RF._pal_rgb(sec4).astype(np.float32)


def _blur2(a, sigma):
    """Separable Gaussian, edges replicated; 2-D or HxWxC float arrays.
    `sigma` is one value or (rows, columns)."""
    sy, sx = sigma if isinstance(sigma, tuple) else (sigma, sigma)
    a = np.asarray(a, np.float32)
    for ax, sg in ((0, sy), (1, sx)):
        r = int(3 * sg)
        k = np.arange(-r, r + 1)
        ker = np.exp(-0.5 * (k / sg) ** 2).astype(np.float32)
        ker /= ker.sum()
        pad = [(0, 0)] * a.ndim
        pad[ax] = (r, r)
        p = np.pad(a, pad, mode='edge')
        a = sum(ker[i] * np.take(p, range(i, i + a.shape[ax]), axis=ax)
                for i in range(len(ker)))
    return a


def _cosmos_canvas(art, field, cells, x0, y0, H, W):
    """The region as Cosmos's premultiplied page art draws it, or None."""
    import ff7nx_fxart as FA
    out = np.zeros((H, W, 3), np.float32)
    pages = {}
    for (x, y), (_o, slot, sx, sy, _own) in cells.items():
        if slot not in pages:
            try:
                pages[slot] = FA._premul_page(art, field, slot, 0)
            except Exception:                                  # noqa: BLE001
                pages[slot] = None
        pg = pages[slot]
        if pg is None:
            return None
        c = pg[sy:sy + 16, sx:sx + 16].copy()
        c[c.max(-1) < 3] = 0
        out[y - y0:y - y0 + 16, x - x0:x - x0 + 16] = c
    return out


def plan_field(name, sec9, sec4, animated=frozenset(), art=None):
    """([(slot, py, px, index)], [(offset, new palette)], stats)."""
    st = {'texels': 0, 'cells': 0, 'retargeted': 0}
    spec = TARGETS.get(name.lower())
    if spec is None:
        return [], [], st
    pal = _palettes(sec4)
    q = spec['palette']
    if q >= len(pal) or q in animated:
        raise ValueError('palette %d is not static' % q)
    pages_l, ts, _te, _px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pages_l if p is not None}
    readers = {}
    rows = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            keys = [(sec9[o + 32], sec9[o + 10], sec9[o + 12])]
            if sec9[o + 28] or sec9[o + 34]:
                keys.append((sec9[o + 34], sec9[o + 14], sec9[o + 16]))
            for k in keys:
                readers[k] = readers.get(k, 0) + 1
            rows.append((layer, o))
    x0, y0, x1, y1 = spec['box']
    W, H = x1 - x0, y1 - y0
    rgb = np.zeros((H, W, 3), np.float32)
    have = np.zeros((H, W), bool)
    cells = {}
    for layer, o in rows:
        if not sec9[o + 28]:
            continue
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        if not (x0 <= x < x1 and y0 <= y < y1):
            continue
        slot, sx, sy = sec9[o + 34], sec9[o + 14], sec9[o + 16]
        p = pages.get(slot)
        if sec9[o + 26]:
            continue            # an animated sheet (the rays): not this light
        ok = (layer == 2 and sec9[o + 30] == 1
              and p is not None and p.depth == 1 and not p.size_flag
              and sx % 16 == 0 and sy % 16 == 0
              and max(struct.unpack_from('<HH', sec9, o + 18)) == 16
              and readers.get((slot, sx, sy)) == 1
              and (x - x0) % 16 == 0 and (y - y0) % 16 == 0
              and (x, y) not in cells)
        if not ok:
            raise ValueError('tile at %d,%d is not an exclusive static '
                             'additive cell' % (x, y))
        own = sec9[o + 22]
        a = np.frombuffer(p.data, np.uint8).reshape(256, 256)[
            sy:sy + 16, sx:sx + 16]
        c = pal[min(own, len(pal) - 1)][a]
        c[a == 0] = 0
        if own != q:
            if own not in animated:
                raise ValueError('tile at %d,%d on palette %d' % (x, y, own))
            c[:] = 0            # an animated light: judge it as no light
        sl = (slice(y - y0, y - y0 + 16), slice(x - x0, x - x0 + 16))
        rgb[sl] = c
        have[sl] = True
        cells[(x, y)] = (o, slot, sx, sy, own)
    if have.mean() < 0.9:
        raise ValueError('region not covered by static FX cells')
    # Cosmos's own sheet, where the provider has it: smooth, and continuous
    # across the 4:3 edge (the 1997 cells and the Cosmos-painted margin are
    # not). Used only if it agrees with what the cells draw today.
    from_cosmos = False
    if art is not None:
        cos = _cosmos_canvas(art, name.lower(), cells, x0, y0, H, W)
        if cos is not None:
            lit = have & (rgb.max(-1) >= DARK) & (cos.max(-1) >= DARK)
            if lit.any() and float(np.abs(cos - rgb)[lit].mean()) < COSMOS_AGREE:
                rgb = cos
                from_cosmos = True
    st['cosmos'] = from_cosmos
    dark = (rgb.max(-1) < DARK) & have
    hole = np.zeros((H, W), bool)
    dq = deque((H - 1, xx) for xx in range(W) if dark[H - 1, xx])
    for yy, xx in dq:
        hole[yy, xx] = True
    while dq:
        yy, xx = dq.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = yy + dy, xx + dx
            if 0 <= ny < H and 0 <= nx < W and dark[ny, nx] and not hole[ny, nx]:
                hole[ny, nx] = True
                dq.append((ny, nx))
    for _ in range(GROW):
        g = hole.copy()
        g[1:] |= hole[:-1]; g[:-1] |= hole[1:]
        g[:, 1:] |= hole[:, :-1]; g[:, :-1] |= hole[:, 1:]
        hole = g
    if not hole.any():
        return [], [], st
    if hole.sum() > MAX_TEXELS * hole.size:
        raise ValueError('hole covers %d%% of the region'
                         % (100 * hole.sum() // hole.size))
    f = rgb.copy()
    # The 16:9 margin was painted from Cosmos, the 4:3 cells are 1997: at
    # the 4:3 edge the light steps. Fade the margin side into the 4:3 side
    # over SEAM_R columns (the 4:3 art is never changed by this).
    band = np.zeros((H, W), bool)
    if spec.get('seam') is not None and not from_cosmos:
        bx = spec['seam'] - x0
        if 2 <= bx <= W - 2 and bx >= SEAM_R:
            d = f[:, bx:bx + 2].mean(1) - f[:, bx - 2:bx].mean(1)
            k = np.arange(-9, 10)
            ker = np.exp(-0.5 * (k / SEAM_SIGMA) ** 2)
            ker /= ker.sum()
            pad = np.pad(d, ((9, 9), (0, 0)), mode='edge')
            d = np.stack([np.convolve(pad[:, c], ker, 'valid')
                          for c in range(3)], -1)
            for j in range(SEAM_R):
                w = (j + 1) / float(SEAM_R)
                col = bx - SEAM_R + j
                f[:, col] = np.clip(f[:, col] + d * w, 0, 255)
            band[:, bx - SEAM_R:bx] = True
    # Hardware review: the hole is filled with the glow's PEAK, continuous
    # with the light above it. Per column the light is carried down from its
    # brightest texel (a running maximum), so every column meets the fill at
    # its own colour; the carried light is then blurred in 2D so no column
    # or row step survives, and blended in with a feathered weight.
    lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    f0 = f
    carry = f0.copy()
    below = np.zeros((H, W), bool)
    for c in range(W):
        best, bl = None, -1.0
        for y in range(H):
            known_px = have[y, c] and not hole[y, c]
            l = float(f0[y, c] @ np.array([0.299, 0.587, 0.114], np.float32))
            if known_px and l >= bl:
                best, bl = f0[y, c], l
                continue
            if best is not None and (hole[y, c] or bl > l):
                carry[y, c] = best
                below[y, c] = True
    hcols = np.nonzero(hole.any(0))[0]
    lo, hi = int(hcols.min()), int(hcols.max())
    zone = np.zeros((H, W), np.float32)
    zone[:, max(0, lo - FEATHER):min(W, hi + FEATHER + 1)] = 1.0
    wgt = _blur2(below.astype(np.float32) * zone, FEATHER_SIGMA)
    wgt = np.maximum(wgt, hole.astype(np.float32))
    # a column whose own peak is dim (the 4:3 edge's notch) takes the
    # brightest carried light within PEAK_WIN columns, and the carried
    # light is blurred mostly ACROSS columns, weighted to the carried
    # texels only, so it forms one sheet instead of per-column streaks
    Y = np.array([0.299, 0.587, 0.114], np.float32)
    lc = np.where(below, carry @ Y, -1.0)
    cw = carry.copy()
    for c in range(W):
        a0, a1 = max(0, c - PEAK_WIN), min(W, c + PEAK_WIN + 1)
        src = a0 + lc[:, a0:a1].argmax(1)
        rows = np.nonzero(below[:, c])[0]
        cw[rows, c] = carry[rows, src[rows]]
    bm = below.astype(np.float32)
    num = _blur2(cw * bm[..., None], (CARRY_SY, CARRY_SX))
    den = _blur2(bm, (CARRY_SY, CARRY_SX))[..., None]
    cb = np.where(den > 1e-3, num / np.maximum(den, 1e-3), f0)
    tgt = np.where(below[..., None], np.maximum(f0, cb), f0)
    f = f0 * (1 - wgt[..., None]) + tgt * wgt[..., None]
    hole = np.abs(f - f0).max(-1) > 0.5
    cand = pal[q][1:]
    plans, retarget = [], []
    for (x, y), (o, slot, sx, sy, own) in sorted(cells.items()):
        sl = (slice(y - y0, y - y0 + 16), slice(x - x0, x - x0 + 16))
        m = hole[sl] | band[sl]
        if from_cosmos:
            m = np.ones_like(m)
        if not m.any():
            continue
        a = np.frombuffer(pages[slot].data, np.uint8).reshape(256, 256)[
            sy:sy + 16, sx:sx + 16].copy()
        if own != q:
            # the cell moves onto the static palette: every texel is
            # re-expressed in it (an animated light counted as no light)
            m = np.ones_like(m)
            retarget.append((o, q))
        want = f[sl][m]
        d = ((want[:, None, :] - cand[None]) ** 2).sum(-1)
        idx = d.argmin(1) + 1
        idx[want.max(-1) < 3] = 0
        yy, xx = np.nonzero(m)
        for j in range(len(idx)):
            if a[yy[j], xx[j]] != idx[j]:
                plans.append((slot, sy + yy[j], sx + xx[j], int(idx[j])))
        st['cells'] += 1
    st['texels'] = len(plans)
    st['retargeted'] = len(retarget)
    return plans, retarget, st


def apply_plans(sec9, plans, retarget=()):
    import ff7nx_tvnotch as TV
    out = TV.apply_plans(sec9, plans) if plans else sec9
    if retarget:
        buf = bytearray(out)
        for o, q in retarget:
            buf[o + 22] = q
        out = bytes(buf)
    return out


def apply_to_flevel(archive, payloads, art=None, encode=None,
                    log=lambda *_: None):
    import lgp
    import ff7nx_palanim
    total = {'fields': 0, 'texels': 0, 'names': [], 'refused': []}
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
            plans, rt, st = plan_field(
                name, parts[8], parts[3],
                ff7nx_palanim.animated_palettes(parts[0]), art=art)
            if not plans and not rt:
                continue
            parts[8] = apply_plans(parts[8], plans, rt)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append(
                (name, '%s: %s' % (type(exc).__name__, str(exc)[:80])))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['fields'] += 1
        total['texels'] += st['texels']
        total['names'].append('%s:%d texel(s) in %d cell(s)%s%s' % (
            name, st['texels'], st['cells'],
            ' from Cosmos\'s sheet' if st.get('cosmos') else '',
            ', %d record(s) onto the static palette' % st['retargeted']
            if st['retargeted'] else ''))
    return total


def summarise(st):
    out = []
    if st.get('fields'):
        out.append('  GLOW FILL (BUILD 617): an additive light carried down '
                   'over the hole stepped out of its bottom (%s). %s=1 '
                   'disables.' % (', '.join(st['names']), OFF_ENV))
    if st.get('refused'):
        out.append('  ! glow fill: %s' % ', '.join(
            '%s: %s' % r for r in st['refused']))
    return '\n'.join(out)
