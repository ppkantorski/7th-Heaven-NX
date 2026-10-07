#!/usr/bin/env python3
"""ff7nx_fxpalfix.py -- Cosmos's 16:9 FX tiles that name a palette the field
does not have.

BUILD 618. cosmo2 (Cosmo Canyon under Meteor, the ending): the animated sky
layer -- layer 2, param 1, state 1 additive glow (palettes 2/3) and state 2
subtractive shade (palettes 4/5) on the PALETTED pages 15..18 -- is driven
at runtime by the field script rewriting those palettes. Cosmos's widescreen
section 9 adds 240 margin tiles for it (120 per state, every one on a cell of
its own on pages 17/18) and gives them PALETTE 6. cosmo2 has six palettes
(0..5): on PC, FFNx draws Cosmos's DDS and never applies the index; on the
Switch the index IS applied and reads past the palette table -- the wrong
colours on both sides and the step at the 4:3 edge (hardware report).
(field_bg_pagecap.clamp_palettes keys by the base texture id and source
cell, not the FX page/UV these tiles draw from, so it never caught them.)

FIX, per bad tile, so the margins become part of the SAME animated effect:
  * palette: the one the centre tiles of the same blend mode / param / state
    use -- the nearest such tile's palette when it fits as well (within 2/255
    of the best) -- so the script's palette animation drives the margins in
    step with the picture;
  * cell: a group whose centre cells are one uniform index (cosmo2's state-2
    shade: index 1 everywhere) gets that index everywhere; otherwise the
    cell is requantised from Cosmos's own page art (box-filtered to the
    page's 1x texels, coverage >= 1/2 opaque) into that palette, index 0 kept
    for uncovered texels. A cell with no art keeps its texels and only gets
    the palette.
Only cells used by no in-range tile are rewritten; anything else refuses
the field. FIELDS is the measured list; SEVENTH_NX_NO_FX_PALFIX=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN

OFF_ENV = 'SEVENTH_NX_NO_FX_PALFIX'
FIELDS = ('cosmo2',)   # fr_e's out-of-range tiles sit on a truecolor page: harmless
UV_CELL = 625000
TILE = 16
NEAR_SLACK = 2.0


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _pal_rgb(sec3):
    import ff7nx_marginblack as MB
    cols, _hdr, npg, _cpp = MB.palette_colours(sec3)
    v = cols.astype(np.uint32)
    rgb = np.stack([(v & 31) << 3, ((v >> 5) & 31) << 3,
                    ((v >> 10) & 31) << 3], -1).astype(np.float64)
    return cols, rgb, npg


def plan_field(name, sec9, sec3, art):
    """(new section 9, stats) -- raises on any doubt."""
    cols, prgb, npg = _pal_rgb(sec3)
    pages_l, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pages_l if p is not None}
    rows = []
    for _layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            if not sec9[o + 28]:
                continue
            u, v = struct.unpack_from('<II', sec9, o + 42)
            rows.append((o, sec9[o + 34], u // UV_CELL, v // UV_CELL,
                         (sec9[o + 30], sec9[o + 26], sec9[o + 27]),
                         sec9[o + 22],
                         struct.unpack_from('<hh', sec9, o + 2)))
    bad = [r for r in rows if r[5] >= npg]
    if not bad:
        raise ValueError('no out-of-range palettes')
    good = [r for r in rows if r[5] < npg]
    good_cells = {(r[1], r[2], r[3]) for r in good}
    for r in bad:
        p = pm.get(r[1])
        if p is None or p.depth != 1 or p.px != 256 or p.size_flag:
            raise ValueError('page %d is not a 256px paletted page' % r[1])
        if (r[1], r[2], r[3]) in good_cells:
            raise ValueError('cell %r is shared with in-range tiles'
                             % ((r[1], r[2], r[3]),))
    arrays = {s: np.frombuffer(pm[s].data, np.uint8).reshape(256, 256).copy()
              for s in {r[1] for r in bad} | {r[1] for r in good}
              if pm[s].depth == 1 and pm[s].px == 256}
    by_group = collections.defaultdict(collections.Counter)
    by_gpage = collections.defaultdict(collections.Counter)
    idx_set = collections.defaultdict(set)
    near = collections.defaultdict(list)
    for o, pg, cu, cv, g, pal, xy in good:
        by_group[g][pal] += 1
        by_gpage[(pg, g)][pal] += 1
        near[g].append((xy, pal))
        if pg in arrays:
            idx_set[g].update(np.unique(
                arrays[pg][cv * TILE:(cv + 1) * TILE,
                           cu * TILE:(cu + 1) * TILE]).tolist())
    uniform = {g: (by_group[g].most_common(1)[0][0], next(iter(s)))
               for g, s in idx_set.items() if len(s) == 1}
    sheets = {}
    buf = bytearray(sec9)
    st = collections.Counter()
    for o, pg, cu, cv, g, pal, (x, y) in bad:
        cand = [q for q, _ in (by_gpage.get((pg, g)) or by_group.get(g)
                               or collections.Counter()).most_common()]
        if not cand:
            cand = list(range(npg))
        cell = arrays[pg][cv * TILE:(cv + 1) * TILE, cu * TILE:(cu + 1) * TILE]
        if g in uniform:
            q, val = uniform[g]
            cell[:] = val
            buf[o + 22] = q
            st['uniform'] += 1
            continue
        nq = None
        if near.get(g):
            nq = min(near[g], key=lambda t: (abs(t[0][1] - y),
                                             abs(t[0][0] - x)))[1]
        if pg not in sheets:
            got = art(name, pg, 0) if art is not None else None
            sheets[pg] = got[0] if got else None
        sh = sheets[pg]
        k = sh.shape[0] // 256 if sh is not None else 0
        hd = None
        if sh is not None and k >= 1:
            hd = sh[cv * TILE * k:(cv + 1) * TILE * k,
                    cu * TILE * k:(cu + 1) * TILE * k].astype(np.float64)
        if hd is None or hd.shape[:2] != (TILE * k, TILE * k) or \
                not (hd[..., 3] >= 128).any():
            buf[o + 22] = nq if nq is not None else cand[0]
            st['palette_only'] += 1
            continue
        a = hd[..., 3:] / 255.0
        cov = a.reshape(TILE, k, TILE, k, 1).mean((1, 3))[..., 0]
        rgb = ((hd[..., :3] * a).reshape(TILE, k, TILE, k, 3).sum((1, 3))
               / np.maximum(a.reshape(TILE, k, TILE, k, 1).sum((1, 3)), 1e-6))
        opaque = cov >= 0.5
        results = {}
        for q in set(cand) | ({nq} if nq is not None else set()):
            ok = np.nonzero(cols[q])[0]
            ok = ok[ok > 0]
            if not len(ok):
                continue
            d = ((rgb[..., None, :] - prgb[q][ok][None, None]) ** 2).sum(-1)
            e = float(np.sqrt(d.min(-1))[opaque].mean()) if opaque.any() \
                else 0.0
            results[q] = (e, ok[d.argmin(-1)])
        if not results:
            buf[o + 22] = nq if nq is not None else cand[0]
            st['palette_only'] += 1
            continue
        q = min(results, key=lambda z: results[z][0])
        if nq in results and results[nq][0] <= results[q][0] + NEAR_SLACK:
            q = nq
        e, idx = results[q]
        cell[:] = np.where(opaque, idx, 0).astype(np.uint8)
        buf[o + 22] = q
        st['requantised'] += 1
        st['err_sum'] += e
    plist, ts2, te2 = FN.parse_texture_block(bytes(buf), px)
    for s, a in arrays.items():
        p = plist[s]
        plist[s] = FN.Page(s, p.size_flag, p.depth, a.tobytes(), p.px)
    out = FN.replace_texture_block(bytes(buf), plist, ts2, te2)
    st['tiles'] = len(bad)
    return out, dict(st)


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled():
        return total
    encode = encode or archive.encode_field
    for name in FIELDS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], st = plan_field(name, parts[8], parts[3], art)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:80]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %d tile(s) (%d mask, %d from Cosmos art, '
                              '%d palette only)' % (
                                  name, st['tiles'], st.get('uniform', 0),
                                  st.get('requantised', 0),
                                  st.get('palette_only', 0)))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  FX PALETTE FIX (BUILD 618): 16:9 effect tiles moved onto '
                   'the palettes the picture animates (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! fx palette fix %s: %s' % (name, why))
    return '\n'.join(out)
