#!/usr/bin/env python3
"""ff7nx_fxhdlift.py -- draw a field's additive FX tiles from HD art.

BUILD 618c. bugin3 (the Ancient Forest's sky globe): Meteor is drawn by the
animated param-4 layer -- 15 FX tiles, blend mode 1 (additive), on the
PALETTED 256px pages 15..18. A paletted page is drawn at 1x, so Meteor
showed 16x16 blocks against the HD picture around it (user report:
"pixellated"). Cosmos painted it at full resolution in its sheets.

FIX: copy each tile's cell out of the Cosmos sheet for its FX page and
palette into one new truecolor page (depth 2, the build's page size) in a
free slot of 15..23 -- the slots the engine draws additively for blend mode
1 (FINDINGS-194) -- and point the tile's FX page (+34), second source (+14)
and UV (+42) at it. Alpha is premultiplied into the colour (black adds
nothing), so the soft edge stays soft. The original cells and the palette
animation of every other tile are untouched; the lifted tiles keep their
param/state, so the script still turns them on and off.

FIELDS maps field -> params to lift. Refused unless every selected tile is
FX with blend mode 1, a slot in 15..23 is free and the cells fit (<= 256).
SEVENTH_NX_NO_FX_HDLIFT=1 disables.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import field_bg_repack as FR

OFF_ENV = 'SEVENTH_NX_NO_FX_HDLIFT'
FIELDS = {'bugin3': (4,)}
UV_CELL = 625000
TILE = 16
SLOTS = range(15, 24)


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def plan_field(name, sec9, params, art):
    """(new section 9, (tiles, cells, slot)) -- raises on any doubt."""
    pages_l, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pages_l if p is not None}
    if px < 2 * 256:
        raise ValueError('page size %d is not HD' % px)
    tiles = []
    for _layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            if sec9[o + 28] and sec9[o + 26] in params:
                tiles.append(o)
    if not tiles:
        raise ValueError('no FX tiles with param %r' % (params,))
    for o in tiles:
        if sec9[o + 30] != 1:
            raise ValueError('tile blend mode %d, not additive' % sec9[o + 30])
        p = pm.get(sec9[o + 34])
        if p is None or p.depth != 1:
            raise ValueError('FX page %d is not paletted' % sec9[o + 34])
    free = [s for s in SLOTS if s not in pm]
    if not free:
        raise ValueError('no free slot in 15..23')
    slot = free[0]
    m = px // TILE
    page = np.zeros((px, px, 4), np.uint8)
    cells = {}
    buf = bytearray(sec9)
    for o in tiles:
        pg, pal = sec9[o + 34], sec9[o + 22]
        u, v = struct.unpack_from('<II', sec9, o + 42)
        cu, cv = u // UV_CELL, v // UV_CELL
        key = (pg, pal, cu, cv)
        if key not in cells:
            n = len(cells)
            if n >= TILE * TILE:
                raise ValueError('more than 256 cells')
            got = art(name, pg, pal)
            if not got or got[0] is None:
                raise ValueError('no art for page %d palette %d' % (pg, pal))
            img = got[0]
            k = img.shape[0] // TILE
            cell = img[cv * k:(cv + 1) * k, cu * k:(cu + 1) * k]
            if cell.shape[:2] != (k, k):
                raise ValueError('art cell out of range')
            if k != m:
                from PIL import Image
                cell = np.asarray(Image.fromarray(
                    np.ascontiguousarray(cell, np.uint8)).resize(
                        (m, m), Image.LANCZOS))
            nx, ny = n % TILE, n // TILE
            page[ny * m:(ny + 1) * m, nx * m:(nx + 1) * m] = cell
            cells[key] = (nx, ny)
        nx, ny = cells[key]
        buf[o + 34] = slot
        struct.pack_into('<HH', buf, o + 14, nx * TILE, ny * TILE)
        struct.pack_into('<II', buf, o + 42, nx * UV_CELL, ny * UV_CELL)
    a = page[..., 3].astype(np.uint16)
    page[..., :3] = ((page[..., :3].astype(np.uint16) * a[..., None] + 127)
                     // 255).astype(np.uint8)
    page[..., 3] = 255
    data = FR.rgba_to_565_buf(page.tobytes(), px * px, width=px,
                              black_ok=True)
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), px)
    plist[slot] = FN.Page(slot, 0, 2, data, px)
    return (FN.replace_texture_block(bytes(buf), plist, t0, t1),
            (len(tiles), len(cells), slot))


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled() or art is None:
        return total
    encode = encode or archive.encode_field
    for name, params in sorted(FIELDS.items()):
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], (nt, nc, slot) = plan_field(name, parts[8], params, art)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:80]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %d tile(s), %d cell(s) on page %d'
                              % (name, nt, nc, slot))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  FX HD LIFT (BUILD 618c): additive effect tiles drawn '
                   'from HD art (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! fx hd lift %s: %s' % (name, why))
    return '\n'.join(out)
