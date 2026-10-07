#!/usr/bin/env python3
"""ff7nx_chunkparallax.py -- draw a field's parallax layers exactly as the
Cosmos widescreen chunk lays them out, from the Cosmos HD sheets.

BUILD 618k. crater_1 (Northern Crater, the walk down the crater wall),
hardware 10-02: the wall (layer 4) looked 1997-resolution in places, had
ragged dashed edges, stopped 21 units short of the left 16:9 edge, and a
second copy of the wall's top edge hung at the top of the screen.

MEASURED (ff7nx_framesim reproduces all of it from the built archive):
  * Cosmos ships `crater_1.chunk.9`, a widescreen layout: layer 3 (sky) is
    70 tiles over x -224..224, layer 4 (wall) 131 tiles over x -224..224,
    y -128..192, on pages 8/9/10, each with its own HD sheet. Drawn
    directly from those sheets the wall is complete to the 16:9 edge -- this
    is what FFNx shows (it does not fill a 1024-wide layer).
  * ff7nx_parallaxfill treated layer 4 as a repeating backdrop because its
    header speed is non-zero (32, i.e. 1/8 of the camera) and copied its 10
    rows every 320 units (y -608..352, 438 records): the copied top edge is
    the strip at the top of the screen. The exact reach of this layer is
    bg.y 93..152 -- rows -139..160 -- which the chunk already covers. It
    joins NON_TILEABLE_OVERLAYS there.
  * crater_1 is the archive's most budget-bound field. With 3.4x the
    records, the page repack left pages 8 and 10 paletted (1x) and the cells
    of the leftmost column on a page whose cells are empty (keyed), so the
    wall stopped short.

FIX: after every page pass, layers 3 and 4 are re-drawn from the chunk: each
record is matched to its chunk record by (layer, x, y), its cell is cut from
the Cosmos sheet for that chunk page, and the cells are packed into depth-2
32-unit pages (8x8 cells) in the slots layers 3/4 already own (free opaque
low slots if more are needed). Positions, palettes, states and every other
layer are untouched. Refused (field left byte-identical) unless every
layer-3/4 record has exactly one chunk match, the slots are not shared with
layers 1/2, and the field stays inside FIELD_MB_CAP and the raw cap.

FIELDS lists the measured fields; SEVENTH_NX_NO_CHUNK_PARALLAX=1 disables.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import field_bg_repack as FR

OFF_ENV = 'SEVENTH_NX_NO_CHUNK_PARALLAX'
FIELDS = {'crater_1': (1, 2, 3, 4)}
CHUNK = 'limit break/flevel.lgp/%s.chunk.9'
SHEET = 'limit break/field/%s/%s_%02d_00.dds'
GRID = 8                                  # 32-unit cells per page side
UV_STEP = 1250000                         # 1e7 / 8
OPAQUE_SLOTS = list(range(1, 15)) + [26, 27, 28]

# BUILD 618m. ART PATCHES -- measured defects in Cosmos's own widened art.
# crater_1 sheet 8 cell (0,4) (the wall's top edge at x -160..-128, y -96):
# Cosmos painted a small mound on the ridge (local columns ~60..91) that
# ends in a vertical cut; vanilla has no mound there (the ridge descends
# smoothly). The mound's texels above the straight ridge line from column
# 48 (row 103) to column 96 (row 111) are made transparent. Applied only if
# the mound is found where it was measured (its top above row 98 at column
# 72), else skipped.
ART_PATCHES = {
    'crater_1': [{'page': 8, 'cell': (0, 4), 'cols': (48, 96),
                  'rows': (103, 111), 'probe': (72, 98)}],
}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _cosmos_reader(provider):
    for rec in provider.slots.values():
        path = rec[0]
        if os.path.basename(path).lower().startswith('cosmoslimitbreak'):
            r = provider.readers.get(path)
            if r is None:
                r = provider.readers[path] = FR.IroReader(path)
            return r
    return None


def _records(sec9, layers):
    _pl, ts, _te, _px = DC.parse_pages(sec9)
    out = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            out.append((layer, o))
    return out


def _apply_patches(name, page, cell_xy, cell, patched):
    for p in ART_PATCHES.get(name, ()):
        if p['page'] != page or tuple(p['cell']) != tuple(cell_xy):
            continue
        k = cell.shape[0]
        sc = k / 128.0
        a = cell[..., 3] > 8
        pc, pr = p['probe']
        col = a[:, int(pc * sc)]
        if not col.any() or np.argmax(col) >= pr * sc:
            continue                                  # mound not found
        cell = cell.copy()
        c0, c1 = p['cols']
        r0, r1 = p['rows']
        for c in range(int(c0 * sc), int(c1 * sc) + 1):
            base = (r0 + (r1 - r0) * (c / sc - c0) / float(c1 - c0)) * sc
            cell[:int(base), c, 3] = 0
        patched.append((page, tuple(cell_xy)))
    return cell


def plan_field(name, sec9, reader, layers=(3, 4), mb_cap=None,
               other_bytes=0, raw_cap=None):
    """(new section 9, (cells, slots, patched)) -- raises on any doubt.

    BUILD 618m: any of layers 1..4. A record keeps its own tile size; 16-unit
    tiles go on 16x16-cell pages (size flag 0), 32-unit tiles on 8x8-cell
    pages (size flag 1), each cut from the Cosmos sheet of its chunk page at
    that page's own grid.
    """
    import dds_decode
    from PIL import Image
    chunk = reader.read(CHUNK % name)
    if not chunk:
        raise ValueError('no Cosmos chunk.9')
    cpages, _cts, _cte, _cpx = DC.parse_pages(chunk)
    cgrid = {p.slot: (8 if p.size_flag else 16) for p in cpages if p}
    cmap = {}
    for layer, o in _records(chunk, layers):
        if layer not in layers:
            continue
        x, y = struct.unpack_from('<hh', chunk, o + 2)
        u, v = struct.unpack_from('<II', chunk, o + 42)
        key = (layer, x, y)
        if key in cmap:
            raise ValueError('chunk has two records at %r' % (key,))
        pg = chunk[o + 32]
        g = cgrid.get(pg)
        if g is None:
            raise ValueError('chunk page %d missing' % pg)
        cmap[key] = (pg, int(round(u / 1e7 * g)), int(round(v / 1e7 * g)), g)
    pages_l, _ts, _te, px = DC.parse_pages(sec9)
    recs = _records(sec9, layers)
    mine, others = [], set()
    layer_of = {}
    for layer, o in recs:
        bind = sec9[o + 34] if sec9[o + 28] else sec9[o + 32]
        if layer in layers:
            mine.append(o)
            layer_of[o] = layer
        else:
            others.add(bind)
            others.add(sec9[o + 32])
    if len(mine) != len(cmap):
        raise ValueError('%d layer-%s records, chunk has %d'
                         % (len(mine), layers, len(cmap)))
    want, order = {}, {8: [], 16: []}
    for o in mine:
        layer = layer_of[o]
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        c = cmap.get((layer, x, y))
        if c is None:
            raise ValueError('record (%d,%d,%d) not in the chunk'
                             % (layer, x, y))
        size = max(struct.unpack_from('<HH', sec9, o + 18)) or 16
        g = 8 if size == 32 else 16
        if size not in (16, 32) or c[3] != g:
            raise ValueError('record (%d,%d) size %d vs chunk grid %d'
                             % (x, y, size, c[3]))
        if sec9[o + 28]:
            raise ValueError('record (%d,%d) is an FX tile' % (x, y))
        want[o] = c
        if c not in order[g]:
            order[g].append(c)
    owned = sorted({sec9[o + 32] for o in mine})
    if set(owned) & others:
        raise ValueError('slots %s are shared with other layers'
                         % sorted(set(owned) & others))
    plist, t0, t1 = FN.parse_texture_block(sec9, px)
    present = {p.slot for p in plist if p is not None}
    pool = [s for s in OPAQUE_SLOTS if s in owned or s not in present]
    groups = {}
    for g in (16, 8):
        need = -(-len(order[g]) // (g * g))
        if len(pool) < need:
            raise ValueError('need %d more opaque slots' % (need - len(pool)))
        groups[g], pool = pool[:need], pool[need:]
    sheets, patched, pages, where = {}, [], {}, {}
    for g in (16, 8):
        cs = px // g
        for s in groups[g]:
            pages[s] = (g, np.zeros((px, px, 4), np.uint8))
        for i, c in enumerate(order[g]):
            pg, cx, cy, _g = c
            if pg not in sheets:
                blob = reader.read(SHEET % (name, name, pg))
                if not blob:
                    raise ValueError('no Cosmos sheet for page %d' % pg)
                rgba, w, h = dds_decode.decode_dds(blob)
                sheets[pg] = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)
            img = sheets[pg]
            k = img.shape[0] // g
            cell = img[cy * k:(cy + 1) * k, cx * k:(cx + 1) * k]
            if cell.shape[:2] != (k, k):
                raise ValueError('cell out of range on sheet %d' % pg)
            if g == 8:
                cell = _apply_patches(name, pg, (cx, cy), cell, patched)
            if k != cs:
                cell = np.asarray(Image.fromarray(np.ascontiguousarray(cell))
                                  .resize((cs, cs), Image.LANCZOS))
            s = groups[g][i // (g * g)]
            n = i % (g * g)
            nx, ny = n % g, n // g
            pages[s][1][ny * cs:(ny + 1) * cs, nx * cs:(nx + 1) * cs] = cell
            where[c] = (s, nx, ny)
    buf = bytearray(sec9)
    for o, c in want.items():
        s, nx, ny = where[c]
        g = c[3]
        unit = 256 // g
        buf[o + 32] = s
        struct.pack_into('<hh', buf, o + 10, nx * unit, ny * unit)
        struct.pack_into('<II', buf, o + 42, nx * (10000000 // g),
                         ny * (10000000 // g))
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), px)
    for s in owned:
        if s not in pages:
            plist[s] = None
    for s, (g, img) in pages.items():
        plist[s] = FN.Page(s, 1 if g == 8 else 0, 2, FR.rgba_to_565_buf(
            img.tobytes(), px * px, width=px), px)
    if mb_cap is not None:
        run = sum(FR._page_bytes(p.px, p.depth) for p in plist
                  if p is not None)
        if run > mb_cap * 1048576.0:
            raise ValueError('%.2f MB over the %.0f MB cap'
                             % (run / 1048576.0, mb_cap))
    out = FN.replace_texture_block(bytes(buf), plist, t0, t1)
    if raw_cap is not None and other_bytes + len(out) > raw_cap:
        raise ValueError('raw %d over cap %d' % (other_bytes + len(out),
                                                  raw_cap))
    import field_bg_pagecap as PC
    counts = PC.effective_counts(out, px)
    over = {k: v for k, v in counts.items() if v > PC.MAX_TILES_PER_PAGE}
    if over:
        raise ValueError('binding-page cap: %s' % over)
    return out, (sum(len(v) for v in order.values()), sorted(pages), patched)


def apply_to_flevel(archive, payloads, art, encode=None, mb_cap=None,
                    raw_cap=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    provider = getattr(art, 'provider', None)
    if disabled() or provider is None:
        return total
    reader = _cosmos_reader(provider)
    if reader is None:
        return total
    encode = encode or archive.encode_field
    for name, layers in FIELDS.items():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            other = len(raw) - len(parts[8])
            parts[8], (n, slots, patched) = plan_field(
                name, parts[8], reader, layers, mb_cap=mb_cap,
                other_bytes=other, raw_cap=raw_cap)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %d cells on pages %s, art patches %d'
                              % (name, n, ','.join(str(s) for s in slots),
                                 len(patched)))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  CHUNK PARALLAX (BUILD 618k): layers 3/4 drawn from the '
                   'Cosmos widescreen chunk and HD sheets (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! chunk parallax %s: %s' % (name, why))
    return '\n'.join(out)
