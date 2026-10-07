#!/usr/bin/env python3
"""ff7nx_tvnotch.py -- square off a paletted TV picture's cut corners.

BUILD 617. mktpb (the Wall Market bar) has a small TV whose four programmes
are an additive layer-2 animation (param 2, states 1/2/4/8) on a 256px
PALETTED page. Its page stays paletted on purpose: Cosmos replaces it by
content hash, frame by frame, so there is no single HD page to seat. The
1997 picture is a 36 x 22 rectangle with its corners stepped off -- 8 texels
missing along the top-right, 7 along the bottom-left, 1-2 at the others --
which against Cosmos's clean HD bezel reads as sharp inner notches
(latest hardware report).

For each state, the texels inside the picture rectangle that are dark AND
connected to the rectangle's border through dark texels (the cut corners,
never dark detail inside the picture) are given the palette index of the
nearest lit picture texel. Same palette, same page, same cells; nothing
outside the rectangle is touched and every changed cell is read by exactly
one record. SEVENTH_NX_NO_TV_NOTCH=1 disables.
"""
from __future__ import annotations

import os
import struct
from collections import deque

import numpy as np

import diag_common as DC
import field_bg_native as FN

OFF_ENV = 'SEVENTH_NX_NO_TV_NOTCH'
UV_SCALE = 10_000_000
# field -> animation param and the picture rectangle (field units, inclusive)
TARGETS = {'mktpb': {'param': 2, 'rect': (33, -102, 68, -81)}}
LIT = 16            # 0..255 max channel of the palette colour
MAX_FRAC = 0.06     # of the rectangle, per state


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _palettes(sec4):
    import render_field as RF
    return RF._pal_rgb(sec4)


def plan_field(name, sec9, sec4):
    """[(slot, py, px, index)] and stats."""
    st = {'texels': 0, 'states': 0}
    spec = TARGETS.get(name.lower())
    if spec is None:
        return [], st
    pal = _palettes(sec4)
    pages_l, ts, _te, _px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pages_l if p is not None}
    rows = [(layer, off) for layer, offs in DC.walk_layers(
        sec9, sec9.find(b'BACK'), ts) for off in offs]

    def cell_of(off, slot):
        p = pages.get(slot)
        if p is None or p.size_flag:
            return None
        n = p.px if p.depth == 2 else 256
        step = n // 16
        u, v = struct.unpack_from('<II', sec9, off + 42)
        return (slot, int(round(u / UV_SCALE * 16)) * step,
                int(round(v / UV_SCALE * 16)) * step)

    readers = {}
    states = {}
    for layer, off in rows:
        for sl in ((sec9[off + 32], sec9[off + 34]) if sec9[off + 28]
                   else (sec9[off + 32],)):
            c = cell_of(off, sl)
            if c is not None:
                readers[c] = readers.get(c, 0) + 1
        if (layer == 2 and sec9[off + 28] and sec9[off + 26] == spec['param']
                and max(struct.unpack_from('<HH', sec9, off + 18)) == 16):
            states.setdefault(sec9[off + 27], []).append(off)
    x0, y0, x1, y1 = spec['rect']
    plans = []
    for state, offs in sorted(states.items()):
        tx = [struct.unpack_from('<h', sec9, o + 2)[0] for o in offs]
        ty = [struct.unpack_from('<h', sec9, o + 4)[0] for o in offs]
        ox, oy = min(tx), min(ty)
        W, H = max(tx) - ox + 16, max(ty) - oy + 16
        idx = np.zeros((H, W), np.int32)
        lit = np.zeros((H, W), bool)
        where = np.full((H, W), -1, np.int32)
        for i, o in enumerate(offs):
            slot = sec9[o + 34]
            p = pages.get(slot)
            c = cell_of(o, slot)
            if p is None or p.depth != 1 or c is None or readers.get(c) != 1:
                continue
            if p.px != 256:
                raise ValueError('paletted page %d is %dpx; this pass works '
                                 'on the native 256' % (slot, p.px))
            a = np.frombuffer(p.data, np.uint8).reshape(256, 256)[
                c[2]:c[2] + 16, c[1]:c[1] + 16]
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            sl = (slice(y - oy, y - oy + 16), slice(x - ox, x - ox + 16))
            idx[sl] = a
            rgb = pal[min(sec9[o + 22], len(pal) - 1)][a]
            lit[sl] = (a != 0) & (rgb.max(-1) > LIT)
            where[sl] = i
        rx0, ry0, rx1, ry1 = x0 - ox, y0 - oy, x1 - ox, y1 - oy
        if rx0 < 0 or ry0 < 0 or rx1 >= W or ry1 >= H:
            continue
        inside = np.zeros((H, W), bool)
        inside[ry0:ry1 + 1, rx0:rx1 + 1] = True
        dark = inside & ~lit & (where >= 0)
        # cut corners: dark texels reachable from the rectangle's border
        notch = np.zeros((H, W), bool)
        q = deque()
        for yy in range(ry0, ry1 + 1):
            for xx in range(rx0, rx1 + 1):
                if (yy in (ry0, ry1) or xx in (rx0, rx1)) and dark[yy, xx]:
                    notch[yy, xx] = True
                    q.append((yy, xx))
        while q:
            yy, xx = q.popleft()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = yy + dy, xx + dx
                if (0 <= ny < H and 0 <= nx < W and dark[ny, nx]
                        and not notch[ny, nx]):
                    notch[ny, nx] = True
                    q.append((ny, nx))
        if not notch.any() or notch.sum() > MAX_FRAC * inside.sum():
            continue
        # nearest lit picture texel's index, ring by ring
        val = np.where(lit & inside, idx, -1)
        todo = notch.copy()
        for _ in range(32):
            if not todo.any():
                break
            new = val.copy()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                sh = np.full_like(val, -1)
                sh[max(dy, 0):H + min(dy, 0), max(dx, 0):W + min(dx, 0)] = \
                    val[max(-dy, 0):H + min(-dy, 0), max(-dx, 0):W + min(-dx, 0)]
                take = todo & (new < 0) & (sh >= 0)
                new[take] = sh[take]
            todo &= new < 0
            val = new
        for yy, xx in zip(*np.nonzero(notch & (val >= 0))):
            o = offs[where[yy, xx]]
            slot = sec9[o + 34]
            c = cell_of(o, slot)
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            plans.append((slot, c[2] + yy - (y - oy), c[1] + xx - (x - ox),
                          int(val[yy, xx])))
        st['states'] += 1
    st['texels'] = len(plans)
    return plans, st


def apply_plans(sec9, plans):
    plist, tex_start, tex_end = FN.parse_texture_block(
        sec9, DC.parse_pages(sec9)[3])
    arrays = {}
    for slot, py, px, v in plans:
        p = plist[slot]
        if p.depth != 1:
            raise ValueError('slot %d is not paletted' % slot)
        if slot not in arrays:
            arrays[slot] = np.frombuffer(p.data, np.uint8).reshape(
                256, 256).copy()
        arrays[slot][py, px] = v
    before = sec9[:tex_start] + sec9[tex_end:]
    for slot, arr in arrays.items():
        p = plist[slot]
        plist[slot] = FN.Page(slot, p.size_flag, p.depth, arr.tobytes(), p.px)
    out = FN.replace_texture_block(sec9, plist, tex_start, tex_end)
    _p, s2, e2 = FN.parse_texture_block(out, DC.parse_pages(out)[3])
    if out[:s2] + out[e2:] != before or len(out) != len(sec9):
        raise ValueError('non-texture bytes changed')
    return out


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
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
            plans, st = plan_field(name, parts[8], parts[3])
            if not plans:
                continue
            parts[8] = apply_plans(parts[8], plans)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append(
                (name, '%s: %s' % (type(exc).__name__, str(exc)[:80])))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['fields'] += 1
        total['texels'] += st['texels']
        total['names'].append('%s:%d in %d state(s)' % (name, st['texels'],
                                                        st['states']))
    return total


def summarise(st):
    out = []
    if st.get('fields'):
        out.append('  TV NOTCH (BUILD 617): %d cut-corner texel(s) of a '
                   'paletted TV picture squared off with its own neighbouring '
                   'palette index (%s). %s=1 disables.'
                   % (st['texels'], ', '.join(st['names']), OFF_ENV))
    if st.get('refused'):
        out.append('  ! tv notch: %s' % ', '.join(
            '%s: %s' % r for r in st['refused']))
    return '\n'.join(out)
