#!/usr/bin/env python3
"""ff7nx_bgmoviefield.py -- let a background movie show in the 16:9 margins.
BUILD 618z.

las4_3, hardware 10-05: "a fmv that isnt filling the entire screen
properly" -- the picture is pillarboxed to 4:3 although ff7nx_moviealign
zooms the background movie to fill 16:9 (it does in las4_2).

CAUSE. Cosmos's las4_3 adds 152 layer-1 tiles in the 16:9 margins, all on
one flat BLACK cell (page 2). Layer 1 is not colour-keyed, so they paint
opaque black over the zoomed movie in both margins. (In las4_2 Cosmos added
none.) Cosmos ships them as placeholders for a picture FFNx draws 4:3.

FIX. In a field whose script turns a background movie on (BGMOVIE 1), the
layer-1 records outside the 4:3 picture whose cell is entirely black are
removed. Nothing else changes; a field without BGMOVIE is never touched.

SEVENTH_NX_NO_BGMOVIE_MARGIN=1 disables.
"""
from __future__ import annotations

import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_BGMOVIE_MARGIN'
BGMOVIE = 0x27
TILE = 52
BLACK = 8                  # every texel at or under this (0..255)


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def has_bgmovie(sec0):
    import echo_s_flevel as ES
    for a, b in ES._routine_blocks(sec0):
        st, _ = ES._decode_block(sec0, a, b)
        for o, op, z in st:
            if op == BGMOVIE and z >= 2 and sec0[o + 1] == 1:
                return True
    return False


def plan_field(parts):
    import diag_common as DC
    import ff7nx_marginblack as MB
    import ff7nx_parallaxfill as PF
    if not has_bgmovie(parts[0]):
        raise ValueError('no background movie')
    s9 = parts[8]
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    cols, _h, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    sv = DC.survey(s9)
    rows = PF._layers(s9, sv['back_start'], sv['tex_start'])
    lay = [r for r in rows if r[0] == 1]
    if not lay:
        raise ValueError('no layer 1')
    _l, count_at, first, count = lay[0]
    keep, drop = [], 0
    for i in range(count):
        o = first + i * TILE
        r = s9[o:o + TILE]
        x, _y = struct.unpack_from('<hh', s9, o + 2)
        inside = -160 <= x < 160
        p = pm.get(s9[o + 32])
        if inside or s9[o + 28] or s9[o + 26] or p is None:
            keep.append(r)
            continue
        sx, sy = struct.unpack_from('<hh', s9, o + 10)
        if p.depth == 1:
            a = np.frombuffer(p.data, np.uint8, count=65536).reshape(
                256, 256)[sy // 16 * 16:sy // 16 * 16 + 16,
                          sx // 16 * 16:sx // 16 * 16 + 16]
            q = min(s9[o + 22], npg - 1)
            c = pal[q][a]
            mx = int(np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31]).max()) * 8
        else:
            k = px // 16
            a = np.frombuffer(p.data, '<u2').reshape(px, px)[
                sy // 16 * k:sy // 16 * k + k, sx // 16 * k:sx // 16 * k + k]
            v = a.astype(np.uint32)
            mx = int(max(((v >> 11) & 31).max() * 8, ((v >> 5) & 63).max() * 4,
                         (v & 31).max() * 8))
        if mx <= BLACK:
            drop += 1
        else:
            keep.append(r)
    if not drop:
        raise ValueError('no black margin tiles on layer 1')
    buf = bytearray(s9)
    buf[first:first + count * TILE] = b''.join(keep)
    struct.pack_into('<H', buf, count_at, len(keep))
    return bytes(buf), drop


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': []}
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
            parts[8], n = plan_field(parts)
        except Exception:                                      # noqa: BLE001
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s:%d' % (name, n))
    return st


def summarise(st):
    if not st.get('names'):
        return ''
    return ('  BGMOVIE MARGIN (BUILD 618z): black layer-1 placeholder tiles '
            'removed from the 16:9 margins of background-movie fields so the '
            'zoomed movie shows there (%s). %s=1 disables.'
            % (', '.join(st['names']), OFF_ENV))
