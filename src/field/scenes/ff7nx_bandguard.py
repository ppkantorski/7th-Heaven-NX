#!/usr/bin/env python3
"""A plain background tile must never sit on an effect-band page. BUILD 780.

HARDWARE 10-22, mogu_1: "a missing square in one of the texture layers ...
it needs to be fixed here and anywhere else it may exist." Still there after
778.

THE PORT TAKES A TILE'S BLEND MODE FROM ITS PAGE SLOT (field_bg_dense /
field_bg_compact: 0x0F..0x17 additive, 0x18..0x19 average, the rest opaque).
mogu_1's top-left layer-1 tile (-160,-120; vanilla page 0) ends up on page 25
-- the water's average-band page -- in a cell the water does not use. So it
is drawn half transparent over nothing (the square), and its texels are
whatever the FX conversion put in that corner of the water sheet. 778 closed
one way in (ff7nx_blackcell's spare cells); the shipped 779 flevel shows the
tile still lands there, so another pass does it too.

This guard runs last and does not care which pass did it. Every record with
no FX page that sits on a slot 0x0F..0x19 while its vanilla twin (same
layer, position, param, state, order) was on an opaque-band slot is moved to
an unreferenced cell of an opaque truecolor page (slot >= 0x1A, the field's
page size), and that cell gets the art the twin drew: Cosmos's picture of
the twin's page and palette when the build has it, else the 1997 cell
through its palette, upscaled. Census of the 10-22 build: mogu_1's tile is
the only one. Anything doubtful is refused, never guessed.

SEVENTH_NX_NO_BAND_GUARD=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_BAND_GUARD'
REC = 52
UV_CELL = 625000
FX_BAND = (0x0F, 0x1A)
OPAQUE_D2 = 0x1A


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _records(sec9):
    import ff7nx_floordepth as FD
    out = {}
    for lay, _c, first, n in FD._rows(sec9):
        for i in range(n):
            o = first + i * REC
            r = sec9[o:o + REC]
            key = (lay,) + struct.unpack_from('<hh', r, 2) + (r[26], r[27],
                                                             r[28])
            out.setdefault(key, []).append(o)
    return out


def misplaced(sec9, van9):
    """[(offset, vanilla offset)] -- plain records moved into the FX bands."""
    b, v = _records(sec9), _records(van9)
    out = []
    for key, offs in b.items():
        if key[5]:
            continue
        vo = v.get(key, [])
        for j, o in enumerate(offs):
            if not FX_BAND[0] <= sec9[o + 32] < FX_BAND[1]:
                continue
            if j >= len(vo):
                continue
            vs = van9[vo[j] + 32]
            if FX_BAND[0] <= vs < FX_BAND[1]:
                continue
            out.append((o, vo[j]))
    return out


def _vanilla_cell(parts_v, van9, vo, side):
    """The 1997 cell the twin drew, RGB float, upscaled to side x side."""
    import diag_common as DC
    import ff7nx_marginblack as MB
    pl, _ts, _te, _px = DC.parse_pages(van9)
    pm = {p.slot: p for p in pl if p is not None}
    p = pm.get(van9[vo + 32])
    if p is None or p.depth != 1:
        return None
    sx, sy = struct.unpack_from('<hh', van9, vo + 10)
    cols, _h, npg, cpp = MB.palette_colours(parts_v[3])
    pal = van9[vo + 22]
    if pal >= npg:
        return None
    a = np.frombuffer(p.data, np.uint8, count=65536).reshape(256, 256)
    idx = a[sy:sy + 16, sx:sx + 16]
    c = np.asarray(cols, np.int64).reshape(npg, cpp)[pal][idx]
    rgb = np.stack([(c >> 0) & 31, (c >> 5) & 31, (c >> 10) & 31], -1)
    rgb = rgb.astype(np.float32) * (255.0 / 31.0)
    from PIL import Image
    im = Image.fromarray(np.clip(np.rint(rgb), 0, 255).astype(np.uint8))
    return np.asarray(im.resize((side, side), Image.BICUBIC), np.float32)


def _cosmos_cell(art, name, van9, vo, side):
    if art is None:
        return None
    try:
        got = art(name, van9[vo + 32], van9[vo + 22])
    except Exception:                                          # noqa: BLE001
        return None
    if got is None:
        return None
    img = got[0]
    k = img.shape[1] // 256
    if k < 1:
        return None
    sx, sy = struct.unpack_from('<hh', van9, vo + 10)
    blk = img[sy * k:(sy + 16) * k, sx * k:(sx + 16) * k]
    if blk.shape[:2] != (16 * k, 16 * k) or blk[..., 3].mean() < 128:
        return None
    from PIL import Image
    im = Image.fromarray(np.ascontiguousarray(blk[..., :3]).astype(np.uint8))
    return np.asarray(im.resize((side, side), Image.BOX), np.float32)


def plan(name, parts, parts_v, art=None):
    """(new section 9, moved). Raises on any doubt."""
    import diag_common as DC
    import field_bg_native as FN
    sec9, van9 = parts[8], parts_v[8]
    bad = misplaced(sec9, van9)
    if not bad:
        return sec9, 0
    pl, _ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    taken, uses = set(), {}
    for _key, offs in _records(sec9).items():
        for o in offs:
            u, v = struct.unpack_from('<II', sec9, o + 42)
            pg = sec9[o + 34] if sec9[o + 28] else sec9[o + 32]
            taken.add((pg, u // UV_CELL, v // UV_CELL))
            taken.add((sec9[o + 32],) + tuple(
                c // 16 for c in struct.unpack_from('<hh', sec9, o + 10)))
            uses[sec9[o + 32]] = uses.get(sec9[o + 32], 0) + 1
    free = [(s, cx, cy) for s in sorted(pm)
            if s >= OPAQUE_D2 and pm[s].depth == 2 and pm[s].px == px
            and not pm[s].size_flag and uses.get(s, 0) < 255
            for cy in range(16) for cx in range(16)
            if (s, cx, cy) not in taken]
    if len(free) < len(bad):
        raise ValueError('no free opaque truecolor cell')
    buf = bytearray(sec9)
    datas = {}
    moved = 0
    for o, vo in bad:
        rgb = _cosmos_cell(art, name, van9, vo, k)
        if rgb is None:
            rgb = _vanilla_cell(parts_v, van9, vo, k)
        if rgb is None:
            raise ValueError('no art for the record at %#x' % o)
        s, cx, cy = free.pop(0)
        rgba = np.empty((k, k, 4), np.uint8)
        rgba[..., :3] = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
        rgba[..., 3] = 255
        cell = np.frombuffer(FN.rgba_bytes_to_565(rgba.tobytes(), k * k),
                             '<u2').reshape(k, k)
        if s not in datas:
            datas[s] = np.frombuffer(pm[s].data, '<u2').reshape(px, px).copy()
        datas[s][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = cell
        buf[o + 32] = s
        struct.pack_into('<hh', buf, o + 10, cx * 16, cy * 16)
        struct.pack_into('<II', buf, o + 42, cx * UV_CELL, cy * UV_CELL)
        moved += 1
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), px)
    for s, d in datas.items():
        q = plist[s]
        plist[s] = FN.Page(s, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(bytes(buf), plist, t0, t1), moved


def apply_to_flevel(archive, payloads, art=None, encode=None,
                    log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': [], 'tiles': 0}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in archive.names():
        entry = archive.index.get(name)
        p = payloads.get(name)
        if entry is None or not p or not archive.is_field(entry):
            continue
        try:
            raw = lgp.lzs_decompress(p[4:])
            parts = list(lgp.split_sections(raw))
            if parts[8].find(b'BACK') < 0:
                continue
            parts_v = list(lgp.split_sections(archive.decompressed(entry)))
            new9, n = plan(name, parts, parts_v, art)
        except Exception as exc:                               # noqa: BLE001
            if 'BACK' not in str(exc):
                st['refused'].append((name, str(exc)[:100]))
            continue
        if n:
            parts[8] = new9
            payloads[name] = encode(lgp.join_sections(parts))
            st['names'].append(name)
            st['tiles'] += n
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  BAND GUARD (BUILD 780): %s -- %d plain tile(s) moved off '
                   'an effect-band page. %s=1 disables.'
                   % (', '.join(st['names']), st['tiles'], OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! band guard %s: %s' % (name, why))
    return '\n'.join(out)
