#!/usr/bin/env python3
"""Stray texels on front-most layer-2 tiles. BUILD 776.

HARDWARE 10-21 (md8_2): "a weird stray pixel patch floating in the air ...
it shows up on top of my character no matter where I walk". MEASURED: a
3x5-texel yellow-green speck in the top-right corner of the layer-2 tile at
(112, 128), ID 7 -- the foreground lamp post's tile, drawn in front of
every model. The 1997 tile (vanilla page 6) has NO texel in that corner;
the upscaled art carries a scrap of a neighbouring atlas cell there.

FIX: for the front-most layer-2 tiles of the listed fields (ID <= MAX_ID),
each 8-connected island of opaque HD texels that does not touch the 1997
tile's own opaque texels (grown by one 1997 texel) is cleared to the
transparent key, when it is no larger than MAX_ISLAND 1997 texels (a
speck; a bigger piece is real art the 1997 tile split differently). Every
texel the 1997 art covers is kept, so the shape of
the post (and of anything else in front) is unchanged; only islands with
no 1997 counterpart go. A cell another record also draws is left alone.

SEVENTH_NX_NO_STRAYFRONT=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_STRAYFRONT'
FIELDS = {'md8_2': {'max_id': 16}}
REC = 52
MAX_ISLAND = 2          # 1997 texels: a speck, never a piece of an object
UV = 1e7


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _rows(s9):
    import ff7nx_floordepth as FD
    return FD._rows(s9)


def _vanilla_masks(v9):
    """{(x, y, id): 16x16 bool} of the 1997 layer-2 tiles."""
    import diag_common as DC
    pl, _ts, _te, _px = DC.parse_pages(v9)
    pm = {p.slot: p for p in pl if p is not None}
    out = {}
    for lay, _c, first, n in _rows(v9):
        if lay != 2:
            continue
        for i in range(n):
            r = v9[first + i * REC:first + (i + 1) * REC]
            p = pm.get(r[32])
            if p is None or p.depth != 1 or r[28]:
                continue
            x, y = struct.unpack_from('<hh', r, 2)
            idv = struct.unpack_from('<h', r, 24)[0]
            sx, sy = r[10], r[11]
            a = np.frombuffer(p.data, np.uint8).reshape(256, 256)
            m = a[sy:sy + 16, sx:sx + 16] != 0
            if m.shape == (16, 16):
                k = (x, y, idv)
                out[k] = m if k not in out else (out[k] | m)
    return out


def _islands(m):
    lab = np.zeros(m.shape, np.int32)
    n = 0
    for y0, x0 in zip(*np.nonzero(m)):
        if lab[y0, x0]:
            continue
        n += 1
        st = [(y0, x0)]
        lab[y0, x0] = n
        while st:
            y, x = st.pop()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if (0 <= yy < m.shape[0] and 0 <= xx < m.shape[1]
                            and m[yy, xx] and not lab[yy, xx]):
                        lab[yy, xx] = n
                        st.append((yy, xx))
    return lab, n


def clean(s9, v9, max_id):
    """(section 9, [(x, y, id, texels cleared)])."""
    import diag_common as DC
    import field_bg_native as FN
    pl, _ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    van = _vanilla_masks(v9)
    users = collections.Counter()
    recs = []
    for lay, _c, first, n in _rows(s9):
        for i in range(n):
            r = s9[first + i * REC:first + (i + 1) * REC]
            u, v = struct.unpack_from('<II', r, 42)
            key = (r[32], int(round(u / UV * 16)), int(round(v / UV * 16)))
            users[key] += 1
            if r[28]:
                users[(r[34],) + key[1:]] += 1
            if lay == 2 and not r[28]:
                recs.append((r, key))
    plist, t0, t1 = FN.parse_texture_block(s9, px)
    datas = {}
    done = []
    for r, key in recs:
        idv = struct.unpack_from('<h', r, 24)[0]
        x, y = struct.unpack_from('<hh', r, 2)
        p = pm.get(r[32])
        if idv > max_id or p is None or p.depth != 2 or users[key] != 1:
            continue
        vm = van.get((x, y, idv))
        if vm is None:
            continue
        k = p.px // 256
        cs = 16 * k
        _slot, cx, cy = key
        if r[32] not in datas:
            datas[r[32]] = np.frombuffer(plist[r[32]].data, '<u2').reshape(
                p.px, p.px).copy()
        cell = datas[r[32]][cy * cs:(cy + 1) * cs, cx * cs:(cx + 1) * cs]
        g = vm.copy()
        g[1:] |= vm[:-1]; g[:-1] |= vm[1:]
        g[:, 1:] |= g[:, :-1].copy(); g[:, :-1] |= g[:, 1:].copy()
        grown = np.repeat(np.repeat(g, k, 0), k, 1)
        lab, nl = _islands(cell != 0)
        cut = 0
        for j in range(1, nl + 1):
            isl = lab == j
            if not (isl & grown).any() and isl.sum() <= MAX_ISLAND * k * k:
                cell[isl] = 0
                cut += int(isl.sum())
        if cut:
            done.append((x, y, idv, cut))
    if not done:
        return s9, done
    for slot, arr in datas.items():
        q = plist[slot]
        plist[slot] = FN.Page(slot, q.size_flag, 2, arr.tobytes(), q.px)
    out = FN.replace_texture_block(s9, plist, t0, t1)
    if len(out) != len(s9):
        raise ValueError('size changed')
    return out, done


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name, cfg in FIELDS.items():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            v9 = lgp.split_sections(archive.decompressed(entry))[8]
            parts[8], done = clean(parts[8], v9, cfg['max_id'])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        if done:
            payloads[name] = encode(lgp.join_sections(parts))
            st['names'].append('%s: %d texel(s) on %d tile(s)' % (
                name, sum(d[3] for d in done), len(done)))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  STRAY FRONT TEXELS (BUILD 776): %s. %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! stray front texels %s: %s' % (name, why))
    return '\n'.join(out)
