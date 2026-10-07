#!/usr/bin/env python3
"""ff7nx_cosmosrestore.py -- put Cosmos's HD art back into 16:9 cells that
the build filled with low-detail art.

BUILD 618. cosmo2 (Cosmo Canyon under Meteor): the user saw low-quality
texture in both 16:9 margins and a step at the 4:3 edge. MEASURED on the
built field: the 120 margin tiles of layer 1 sit on HD page 27 (768px), but
their cells hold almost no sub-texel detail -- mean 3x3-block std 0.76
against 5.0-7.8 for the 4:3 cells -- and differ from Cosmos's own sheet
(cosmo2_27_00) by 73/255 on average. The art Cosmos painted for them IS in
that sheet, at the cells its chunk.9 names (3x3-block std 3.7: smooth sky,
real detail): the page repack relocated the margin cells, and what landed
in them was a low-detail fill rather than the sheet's art.

FIX, per built tile outside 4:3 on a truecolor page (not FX): find the
Cosmos chunk.9 tile at the same layer / position / z, read its cell out of
Cosmos's sheet for its page, and write it into the built tile's own cell
(565, key = fully transparent). Only cells used by no in-picture tile are
written; a margin tile with no Cosmos twin, or a twin whose sheet cell is
empty, is left alone. FIELDS is the measured list;
SEVENTH_NX_NO_COSMOS_RESTORE=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import field_bg_repack as FR

OFF_ENV = 'SEVENTH_NX_NO_COSMOS_RESTORE'
FIELDS = ('cosmo2',)
UV_CELL = 625000
TILE = 16
HERE = os.path.dirname(os.path.abspath(__file__))
CHUNK_DIR = os.path.join(HERE, 'cache', 'CosmosLimitBreak', 'LIMIT BREAK',
                         'flevel.lgp')


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _margin(x):
    return x < -160 or x >= 160


def _tiles(sec9):
    _pl, ts, _te, _px = DC.parse_pages(sec9)
    out = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            z = struct.unpack_from('<I', sec9, o + 38)[0]
            out.append((layer, o, x, y, z))
    return out


def plan_field(name, sec9, chunk9, art):
    """(new section 9, cells written) -- raises on any doubt."""
    pages_l, _ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pages_l if p is not None}
    twins = collections.defaultdict(list)
    for layer, o, x, y, z in _tiles(chunk9):
        if chunk9[o + 28] or not _margin(x):
            continue
        sx, sy = struct.unpack_from('<HH', chunk9, o + 10)
        twins[(layer, x, y, z)].append((chunk9[o + 32], sx, sy))
    users = collections.defaultdict(list)          # (page, cu, cv) -> x list
    todo = []
    for layer, o, x, y, z in _tiles(sec9):
        fx = bool(sec9[o + 28])
        page = sec9[o + 34] if fx else sec9[o + 32]
        u, v = struct.unpack_from('<II', sec9, o + 42)
        key = (page, u // UV_CELL, v // UV_CELL)
        users[key].append(x)
        if fx or not _margin(x):
            continue
        p = pm.get(page)
        if p is None or p.depth != 2 or p.px != px:
            continue
        t = twins.get((layer, x, y, z))
        if t and len(t) == 1:
            todo.append((key, t[0]))
    arrays = {}
    sheets = {}
    packed = {}
    written = 0
    done = set()
    # 618c: copy the SAME packed 565 the 4:3 cells were built from
    # (PageArt.buf -- the built page 26 is byte-identical to it), not a
    # re-encode of the unpacked RGB: the repack's packing sits ~3/255 below
    # rgba_to_565_buf's rounding in red and blue, and that offset was the
    # remaining step at the 4:3 edge on hardware.
    provider = getattr(art, 'provider', None)
    opener = provider.open(name) if provider is not None else None
    for key, (cpage, sx, sy) in todo:
        if key in done or any(not _margin(x) for x in users[key]):
            continue
        page, cu, cv = key
        m = px // TILE
        if opener is not None:
            if cpage not in packed:
                try:
                    pa = opener(cpage, 0)
                except Exception:                              # noqa: BLE001
                    pa = None
                packed[cpage] = pa if (pa is not None
                                       and pa.px == px) else None
            pa = packed[cpage]
            if pa is not None:
                k = px // 256
                src = np.frombuffer(pa.buf, '<u2').reshape(px, px)[
                    sy * k:sy * k + m, sx * k:sx * k + m]
                if src.shape == (m, m) and src.any():
                    if page not in arrays:
                        arrays[page] = np.frombuffer(
                            pm[page].data, '<u2').reshape(px, px).copy()
                    arrays[page][cv * m:(cv + 1) * m,
                                 cu * m:(cu + 1) * m] = src
                    done.add(key)
                    written += 1
                    continue
        if cpage not in sheets:
            got = art(name, cpage, 0)
            sheets[cpage] = got[0] if got else None
        sh = sheets[cpage]
        if sh is None:
            continue
        k = sh.shape[0] / 256.0
        y0, x0 = int(round(sy * k)), int(round(sx * k))
        n = int(round(TILE * k))
        cell = sh[y0:y0 + n, x0:x0 + n]
        if cell.shape[:2] != (n, n) or not (cell[..., 3] >= 8).any():
            continue
        if n != m:
            from PIL import Image
            cell = np.asarray(Image.fromarray(cell.astype(np.uint8))
                              .resize((m, m), Image.LANCZOS))
        rgba = np.ascontiguousarray(cell, np.uint8).copy()
        rgba[rgba[..., 3] < 8] = 0
        buf = FR.rgba_to_565_buf(rgba.tobytes(), m * m, width=m)
        if page not in arrays:
            arrays[page] = np.frombuffer(pm[page].data, '<u2').reshape(
                px, px).copy()
        arrays[page][cv * m:(cv + 1) * m, cu * m:(cu + 1) * m] = \
            np.frombuffer(buf, '<u2').reshape(m, m)
        done.add(key)
        written += 1
    if not written:
        raise ValueError('nothing to restore')
    plist, ts2, te2 = FN.parse_texture_block(sec9, px)
    for s, a in arrays.items():
        q = plist[s]
        plist[s] = FN.Page(s, q.size_flag, q.depth, a.tobytes(), q.px)
    return FN.replace_texture_block(sec9, plist, ts2, te2), written


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled() or art is None:
        return total
    encode = encode or archive.encode_field
    for name in FIELDS:
        entry = archive.index.get(name)
        path = os.path.join(CHUNK_DIR, name + '.chunk.9')
        if entry is None or not os.path.exists(path):
            continue
        try:
            chunk9 = open(path, 'rb').read()
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], n = plan_field(name, parts[8], chunk9, art)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:80]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %d margin cell(s)' % (name, n))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  COSMOS RESTORE (BUILD 618): HD art back in 16:9 cells '
                   '(%s). %s=1 disables.' % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! cosmos restore %s: %s' % (name, why))
    return '\n'.join(out)
