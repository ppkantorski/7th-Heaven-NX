#!/usr/bin/env python3
"""ff7nx_specks.py -- stray bright specks removed by hand. BUILD 618z.

las4_3, hardware 10-05: "these 2 bright dots look weird / out of place".
Both are tiny 1997 debris specks that Cosmos repainted as bright
yellow-green blobs on layer 2 (opaque, truecolor page 26):
  * x 101, y -30 -- a lone 8x6-texel blob, the whole content of the
    (96, -32) cell (vanilla: 7 index texels);
  * x 61, y -20 -- a bright nub on the top edge of the big rock, rows
    34..43 / columns 36..46 of the (48, -32) cell, lum 130-190 against the
    rock's 20-30.
Their texels become transparent (the movie shows through, as around every
other rock). Exclusive cells only; refused if a cell is shared or its
content is not what was measured.

SEVENTH_NX_NO_SPECKS=1 disables.
"""
from __future__ import annotations

import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_SPECKS'
# field: [(layer, (x, y), (r0, r1, c0, c1) in 1/48-cell texels, min lum,
#          expected texel count range)]
SPECKS = {
    'las4_3': [(2, (96, -32), (0, 48, 0, 48), 100, (30, 80)),
               (2, (48, -32), (34, 44, 36, 47), 40, (25, 70))],
}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def plan_field(name, parts):
    import diag_common as DC
    import field_bg_native as FN
    s9 = bytes(parts[8])
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    uses = {}
    recs = {}
    for layer, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            bl = s9[o + 28]
            pg = s9[o + 34] if bl else s9[o + 32]
            sx, sy = struct.unpack_from('<hh', s9, o + (14 if bl else 10))
            uses[(pg, sx // 16, sy // 16)] = uses.get(
                (pg, sx // 16, sy // 16), 0) + 1
            if not bl and not s9[o + 26]:
                recs[(layer,) + struct.unpack_from('<hh', s9, o + 2)] = o
    data = {}
    n = 0
    for layer, xy, (r0, r1, c0, c1), lmin, (lo, hi) in SPECKS[name.lower()]:
        o = recs.get((layer,) + xy)
        if o is None:
            raise ValueError('no record at %s' % (xy,))
        pg = s9[o + 32]
        p = pm[pg]
        if p.depth != 2 or p.px // 16 != 48:
            raise ValueError('cell %s is not a 768 truecolor cell' % (xy,))
        sx, sy = struct.unpack_from('<hh', s9, o + 10)
        if uses[(pg, sx // 16, sy // 16)] != 1:
            raise ValueError('cell %s is shared' % (xy,))
        if pg not in data:
            data[pg] = np.frombuffer(p.data, '<u2').reshape(px, px).copy()
        cell = data[pg][sy // 16 * k:sy // 16 * k + k,
                        sx // 16 * k:sx // 16 * k + k]
        win = cell[r0:r1, c0:c1]
        lum = (((win >> 11) & 31) + ((win >> 6) & 31) + (win & 31)) * 8 / 3
        m = (win != 0) & (lum >= lmin)
        if not lo <= int(m.sum()) <= hi:
            raise ValueError('speck at %s: %d texels, expected %d..%d'
                             % (xy, int(m.sum()), lo, hi))
        win[m] = 0
        n += int(m.sum())
        # tidy the silhouette where the speck was (hardware preview 10-05:
        # "a little dirtiness to the edge"): drop specks of texels left
        # floating, then smooth the edge locally (close + open 3x3; a texel
        # the closing adds takes its nearest rock colour)
        from scipy import ndimage as ND
        R0, R1 = max(r0 - 6, 0), min(r1 + 4, k)
        C0, C1 = max(c0 - 10, 0), min(c1 + 2, k)
        alpha = cell != 0
        lab, nl = ND.label(alpha, np.ones((3, 3), bool))
        if nl > 1:
            sizes = ND.sum(alpha, lab, range(1, nl + 1))
            for i_, sz in enumerate(sizes, 1):
                if sz < 40:
                    cell[lab == i_] = 0
        reg = cell[R0:R1, C0:C1]
        a0 = reg != 0
        a1 = ND.binary_opening(ND.binary_closing(a0, np.ones((3, 3), bool)),
                               np.ones((3, 3), bool))
        # the window's border keeps its texels (no step at the window edge)
        a1[0, :], a1[-1, :] = a0[0, :], a0[-1, :]
        a1[:, 0], a1[:, -1] = a0[:, 0], a0[:, -1]
        if a0.any():
            _d, (iy, ix) = ND.distance_transform_edt(~a0, return_indices=True)
            add = a1 & ~a0
            reg[add] = reg[iy[add], ix[add]]
            reg[a0 & ~a1] = 0
    plist, t0, t1 = FN.parse_texture_block(s9, px)
    for s_, d in data.items():
        q = plist[s_]
        plist[s_] = FN.Page(s_, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(s9, plist, t0, t1), n


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in SPECKS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], n = plan_field(name, parts)
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s: %d texel(s)' % (name, n))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  SPECKS (BUILD 618z): stray bright specks made transparent '
                   '(%s). %s=1 disables.' % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! specks %s: %s' % (name, why))
    return '\n'.join(out)
