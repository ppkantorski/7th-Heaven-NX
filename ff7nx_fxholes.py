#!/usr/bin/env python3
"""ff7nx_fxholes.py -- fill pinholes in additive effect art. BUILD 618y.

woa_1, hardware 10-04: "small batches of missing pixels on the fx layer".
The wind haze (layer 3, additive, paletted 1x pages) has little black dashes
inside the light: texels quantised to palette index 0 (or, on a truecolor
page, stored as 0) in the middle of bright haze. On an additive page black
adds nothing, so each one is a hole the background shows through, and the
haze grid repeats them all over the screen.

FIX (every field; additive FX pages, slots 15..23; only cells some record
draws): an 8-connected group of unlit texels is filled with the nearest lit
texel's value when
  * it holds at most MAX_HOLE texels on a paletted page, MAX_HOLE_TC on a
    truecolor page (less than one native pixel),
  * every texel around it (its one-texel ring, inside the cell) is lit, and
  * that ring's mean brightness is at least MIN_RING (0..255).
A real dark feature of an effect is larger, or not fully surrounded by
light. Nothing else changes.

SEVENTH_NX_NO_FX_HOLES=1 disables.
"""
from __future__ import annotations

import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_FX_HOLES'
FX_LO, FX_HI = 0x0F, 0x18
MAX_HOLE = 48                # texels, paletted (1x) pages (618z: 12 -> 48)
MAX_HOLE_TC = 0              # truecolor pages are left alone: their
                             # dropouts are under a third of a native
                             # pixel, and del3's surf (confirmed) is one
MIN_RING = 40.0
# BUILD 618z (woa_1, hardware 10-04 build 386: "the steam effect still has
# missing squares"): a glyph-shaped hole of ~30 texels of index 109 (colour
# 0 in palette 2) inside a lit steam frame -- vanilla's twin cell is lit
# there. Holes the VANILLA twin paints over are filled up to MAX_HOLE_V.
MAX_HOLE_V = 64
SMALL_HOLE = 12               # up to this size any enclosed hole is filled
VLIT_FRAC = 0.8
UV_CELL = 625000


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _fill_cell(vals, lit, bright, max_hole, vlit=None):
    """(new vals, filled count) for one cell. `vlit`: the vanilla twin's
    lit mask, allowing larger holes the 1997 frame painted."""
    from scipy import ndimage as ND
    holes = ~lit
    if not holes.any() or lit.sum() == 0:
        return vals, 0
    lab, n = ND.label(holes, np.ones((3, 3), bool))
    if not n:
        return vals, 0
    sizes = ND.sum(holes, lab, range(1, n + 1))
    filled = np.zeros_like(holes)
    for i, sz in enumerate(sizes, 1):
        comp = lab == i
        big = sz > SMALL_HOLE and vals.dtype == np.uint8
        if big and sz <= max_hole and not (vals[comp] != 0).all():
            # a larger hole must be palette-zeroed art (a nonzero index the
            # palette maps to colour 0, woa_1's index 109 glyph), never
            # index 0 -- zz6's sparkle has index-0 wedges between its rays
            continue
        if sz > max_hole and not (
                vlit is not None and sz <= MAX_HOLE_V
                and float(vlit[comp].mean()) >= VLIT_FRAC):
            continue
        ring = ND.binary_dilation(comp, np.ones((3, 3), bool)) & ~comp
        if not ring.any() or not lit[ring].all():
            continue
        if float(bright[ring].mean()) < MIN_RING:
            continue
        filled |= comp
    if not filled.any():
        return vals, 0
    _d, (iy, ix) = ND.distance_transform_edt(~lit, return_indices=True)
    out = vals.copy()
    out[filled] = vals[iy[filled], ix[filled]]
    return out, int(filled.sum())


def _vanilla_lit(vparts):
    """{(layer, param, state, x, y, z, palette): lit mask} for the
    vanilla field's paletted FX records (unique keys only)."""
    import collections
    import diag_common as DC
    import ff7nx_marginblack as MB
    s9 = vparts[8]
    pl, ts, _te, _px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    cols, _h, npg, cpp = MB.palette_colours(vparts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    out, seen = {}, collections.Counter()
    for layer, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            if not s9[o + 28]:
                continue
            p = pm.get(s9[o + 34])
            q = s9[o + 22]
            if p is None or p.depth != 1 or p.size_flag or q >= npg:
                continue
            key = _rkey(s9, layer, o)
            sx, sy = struct.unpack_from('<hh', s9, o + 14)
            a = np.frombuffer(p.data, np.uint8, count=65536).reshape(
                256, 256)[sy // 16 * 16:sy // 16 * 16 + 16,
                          sx // 16 * 16:sx // 16 * 16 + 16]
            out[key] = (pal[q][a] & 0x7FFF) != 0
            seen[key] += 1
    return {k: v for k, v in out.items() if seen[k] == 1}


def _rkey(s9, layer, o):
    return (layer, s9[o + 26], s9[o + 27]) + struct.unpack_from(
        '<hh', s9, o + 2) + (struct.unpack_from('<I', s9, o + 38)[0],
                             s9[o + 22])


def plan_field(parts, vparts=None):
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_marginblack as MB
    s9 = parts[8]
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    cols, _h, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    vlit = _vanilla_lit(vparts) if vparts is not None else {}
    cells = {}
    keys = {}
    for _l, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            if not s9[o + 28] or s9[o + 30] != 1:
                continue
            slot = s9[o + 34]
            p = pm.get(slot)
            if p is None or not (FX_LO <= slot < FX_HI):
                continue
            grid = 8 if p.size_flag else 16
            if p.depth == 1:
                sx, sy = struct.unpack_from('<hh', s9, o + 14)
                cs = 256 // grid
                key = (slot, sx // cs, sy // cs)
            else:
                u, v = struct.unpack_from('<II', s9, o + 42)
                key = (slot, int(round(u / 1e7 * grid)),
                       int(round(v / 1e7 * grid)))
            cells.setdefault(key, set()).add(s9[o + 22])
            keys.setdefault(key, []).append(_rkey(s9, _l, o))
    data = {}
    total = 0
    for (slot, cx, cy), pals in sorted(cells.items()):
        p = pm[slot]
        grid = 8 if p.size_flag else 16
        if p.depth == 1:
            if len(pals) != 1:
                continue                     # judged through one palette
            q = next(iter(pals))
            if q >= npg:
                continue
            if slot not in data:
                data[slot] = np.frombuffer(p.data, np.uint8,
                                           count=65536).reshape(256, 256).copy()
            cs = 256 // grid
            sl = (slice(cy * cs, (cy + 1) * cs), slice(cx * cs, (cx + 1) * cs))
            a = data[slot][sl]
            c = pal[q][a]
            lit = (c & 0x7FFF) != 0
            rgb = np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31], -1) * 8
            vl = None
            if grid == 16:
                masks = [vlit[k_] for k_ in keys.get((slot, cx, cy), ())
                         if k_ in vlit]
                if masks:
                    vl = np.logical_or.reduce(masks)
            new, n = _fill_cell(a, lit, rgb.mean(-1).astype(np.float32),
                                MAX_HOLE, vl)
        else:
            if not MAX_HOLE_TC:
                continue
            if slot not in data:
                data[slot] = np.frombuffer(p.data, '<u2').reshape(
                    p.px, p.px).copy()
            cs = p.px // grid
            scale = max(1, p.px // 256)
            sl = (slice(cy * cs, (cy + 1) * cs), slice(cx * cs, (cx + 1) * cs))
            a = data[slot][sl]
            lit = a != 0
            v = a.astype(np.uint32)
            rgb = np.stack([((v >> 11) & 31) << 3, ((v >> 5) & 63) << 2,
                            (v & 31) << 3], -1)
            new, n = _fill_cell(a, lit, rgb.mean(-1).astype(np.float32),
                                MAX_HOLE_TC)
        if n:
            data[slot][sl] = new
            total += n
    if not total:
        raise ValueError('no pinholes')
    plist, t0, t1 = FN.parse_texture_block(s9, px)
    for slot, d in data.items():
        q = plist[slot]
        raw = d.tobytes() if q.depth == 1 else d.astype('<u2').tobytes()
        plist[slot] = FN.Page(slot, q.size_flag, q.depth, raw, q.px)
    return FN.replace_texture_block(s9, plist, t0, t1), total


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'texels': 0}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in archive.names():
        entry = archive.index.get(name)
        try:
            if entry is None or not archive.is_field(entry):
                continue
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            if parts[8].find(b'BACK') < 0:
                continue
            vparts = list(lgp.split_sections(archive.decompressed(entry)))
            parts[8], n = plan_field(parts, vparts)
        except Exception:                                      # noqa: BLE001
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s:%d' % (name, n))
        st['texels'] += n
    return st


def summarise(st):
    if not st.get('names'):
        return ''
    return ('  FX HOLES (BUILD 618z): %d pinhole texel(s) filled in additive '
            'effect art across %d field(s) (%s). %s=1 disables.'
            % (st['texels'], len(st['names']), ', '.join(st['names'][:25])
               + (' ...' if len(st['names']) > 25 else ''), OFF_ENV))
