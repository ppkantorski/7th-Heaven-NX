#!/usr/bin/env python3
"""ff7nx_cutout.py -- hand-drawn silhouette cut-outs for HD background tiles.

BUILD 618. junbin21 (the Junon Shinra office): the chair-back in the middle
of the window is a near-rectangular block of opaque texels in the Cosmos art.
Its sides and top carry a dark surround, and an additive orange rim (fx page
16) is traced round the RECTANGLE, so over the parallax sea it reads as a
smeared box. The user trimmed it by hand in a screenshot (user: "match my
edit exactly"); that trim is the MASK below, at the screenshot's 3 px per
field unit -- which is also the HD page's texel pitch, so it maps 1:1.

WHAT IS CUT, for each cut-out:
  * every texel of the named solid records (layer 2, the chair's own z) that
    falls on a '.' of the mask becomes key (0, transparent) -- the parallax
    sky and sea behind then show, as in the user's edit;
  * the same for the named additive fx records, so the orange rim that traced
    the old rectangle goes too; fx texels inside the silhouette stay.
Registration: the screenshot-to-field offset was found by matching the
trim's opaque pixels against a render of the built field (minimum residual
at ORIGIN; one pixel off in any direction is clearly worse). Every record
must match exactly once, its cell must be used by no other record, pages
must be depth 2 at 768 (HD) or 256 (sampled); otherwise the field is left
untouched. SEVENTH_NX_NO_CUTOUT=1 disables.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN

OFF_ENV = 'SEVENTH_NX_NO_CUTOUT'
UV_CELL = 625000
TILE = 16
MASK_PX = 3                    # mask pixels per field unit

# junbin21 chair, from the user's trimmed screenshot (alpha >= 128 -> '#').
# Mask pixel (j, i) covers field ((ORIGIN + (j, i)) / MASK_PX).
JUNBIN21_MASK = (
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '.................................................................',
    '......................####............###........................',
    '.....................#####################.......................',
    '.....................######################......................',
    '.....................######################......................',
    '......................#####################......................',
    '......................#####################......................',
    '.....................######################......................',
    '.....................######################......................',
    '....................#######################......................',
    '....................#######################......................',
    '....................#######################......................',
    '.....................#####################.......................',
    '......................####################.......................',
    '.......................###################.......................',
    '.......................##################........................',
    '.......................##################........................',
    '.......................##################........................',
    '.......................###################.......................',
    '.......................###################.......................',
    '.......................###################.......................',
    '.......................###################.......................',
    '.......................###################.......................',
    '.......................###################.......................',
    '.......................####################......................',
    '.......................####################......................',
    '.......................####################......................',
    '......................#####################......................',
    '......................#####################......................',
    '......................#####################......................',
    '......................######################.....................',
    '......................######################.....................',
    '.....................#######################.....................',
    '.....................########################....................',
    '.....................########################....................',
    '.....................########################....................',
    '....................##########################...................',
    '....................##########################...................',
    '....................##########################...................',
    '...................###########################..#.#.#.......#...#',
    '##################.##############################################',
    '##################.##############################################',
)

CUTS = {
    'junbin21': {
        'origin': (-39, -275),
        'mask': JUNBIN21_MASK,
        # (x, y, page, z of the solid record -- None for the additive fx one)
        'records': (
            (-16, -96, 26, 9850260), (0, -96, 27, 9850260),
            (-16, -80, 27, 9850260), (0, -80, 26, 9850260),
            (-16, -96, 16, None), (0, -96, 16, None),
            (-16, -80, 16, None), (0, -80, 16, None),
        ),
    },
}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _find(sec9, ts, spec):
    """[(spec record, offset, page, cell u, cell v)], each matched once."""
    want = {r: [] for r in spec['records']}
    uses = {}
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            fx = bool(sec9[o + 28])
            page = sec9[o + 34] if fx else sec9[o + 32]
            u, v = struct.unpack_from('<II', sec9, o + 42)
            uses.setdefault((page, u, v), []).append(o)
            if layer != 2:
                continue
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            z = struct.unpack_from('<I', sec9, o + 38)[0]
            for r in want:
                if (r[0], r[1], r[2]) != (x, y, page):
                    continue
                if (r[3] is None) == fx and (r[3] is None or r[3] == z):
                    want[r].append(o)
    out = []
    for r, offs in want.items():
        if len(offs) != 1:
            raise ValueError('record %r matched %d times' % (r, len(offs)))
        o = offs[0]
        fx = bool(sec9[o + 28])
        page = sec9[o + 34] if fx else sec9[o + 32]
        u, v = struct.unpack_from('<II', sec9, o + 42)
        if len(uses[(page, u, v)]) != 1:
            raise ValueError('cell of record %r is shared' % (r,))
        out.append((r, o, page, u // UV_CELL, v // UV_CELL))
    return out


def plan_field(name, sec9):
    """(new section 9, texels cut) -- raises on any doubt."""
    spec = CUTS[name.lower()]
    mask = np.array([[c == '.' for c in row] for row in spec['mask']])
    mh, mw = mask.shape
    ox, oy = spec['origin']
    pages_l, ts, _te, page_px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pages_l if p is not None}
    recs = _find(sec9, ts, spec)
    arrays = {}
    cut = 0
    for (x, y, _pg, _z), _o, page, cu, cv in recs:
        p = pages.get(page)
        if p is None or p.depth != 2 or p.px not in (256, 768):
            raise ValueError('page %d is not a depth-2 page' % page)
        s = p.px // 256                       # texels per field unit
        if page not in arrays:
            arrays[page] = np.frombuffer(p.data, '<u2').reshape(
                p.px, p.px).copy()
        n = TILE * s
        cell = arrays[page][cv * n:(cv + 1) * n, cu * n:(cu + 1) * n]
        b, c = np.mgrid[0:n, 0:n]
        j = np.floor((x + (c + 0.5) / s) * MASK_PX).astype(int) - ox
        i = np.floor((y + (b + 0.5) / s) * MASK_PX).astype(int) - oy
        inside = (i >= 0) & (i < mh) & (j >= 0) & (j < mw)
        hit = np.zeros(inside.shape, bool)
        hit[inside] = mask[i[inside], j[inside]]
        hit &= cell != 0
        cut += int(hit.sum())
        cell[hit] = 0
    if not cut:
        raise ValueError('nothing to cut (already applied?)')
    plist, ts2, te2 = FN.parse_texture_block(sec9, page_px)
    for page, a in arrays.items():
        q = plist[page]
        plist[page] = FN.Page(page, q.size_flag, q.depth, a.tobytes(), q.px)
    return FN.replace_texture_block(sec9, plist, ts2, te2), cut


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    total = {'fields': 0, 'names': [], 'refused': []}
    if disabled():
        return total
    encode = encode or archive.encode_field
    for name in CUTS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            payload = payloads.get(name)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], n = plan_field(name, parts[8])
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append(
                (name, '%s: %s' % (type(exc).__name__, str(exc)[:80])))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['fields'] += 1
        total['names'].append('%s: %d texels' % (name, n))
    return total


def summarise(st):
    out = []
    if st.get('fields'):
        out.append('  CUTOUT (BUILD 618): hand-trimmed silhouettes applied '
                   '(%s). %s=1 disables.' % ('; '.join(st['names']), OFF_ENV))
    if st.get('refused'):
        out.append('  ! cutout: %s' % ', '.join(
            '%s: %s' % r for r in st['refused']))
    return '\n'.join(out)
