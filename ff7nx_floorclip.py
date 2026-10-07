#!/usr/bin/env python3
"""ff7nx_floorclip.py -- carry a foreground floor into the 16:9 margin on the
foreground layer. BUILD 618z.

woa_1, hardware 10-04 (builds 385/386): with the wind haze on, the rock
floor at the bottom right is clear inside the 4:3 picture but hazed in the
widescreen margin, with a vertical seam at the 4:3 edge.

CAUSE. In the 1997 field the floor is LAYER 2 (opaque, drawn after the
haze, which is layer 3) and stops at the 4:3 edge (x = 160). Cosmos's
margin art paints the floor's continuation on LAYER 1, under the haze.

FIX (FIELDS only): the margin's layer-1 art is split along the floor's
edge -- dark rock connected to layer 2's floor at the 4:3 edge or to the
bottom of the picture, against the bright waterfall -- and the rock part is
added as NEW layer-2 records over the same cells (same depth as layer 2's
floor beside them, opaque, keyed outside the rock). The haze then goes
behind the rock in the margin exactly as it does inside 4:3; the cut is a
per-texel line that follows the floor's silhouette. Layer 1 is unchanged.
New cells go into unused cells of a truecolor page already used by layer
2's floor; refused when there is no room or a page would pass 256 bindings.

SEVENTH_NX_NO_FLOOR_CLIP=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_FLOOR_CLIP'
FIELDS = {'woa_1': {'x0': 160, 'x1': 224},
          # woa_3 (user, 10-05): a small mossy patch at the bottom right;
          # brighter rock against a pale sky, so a higher threshold
          'woa_3': {'x0': 160, 'x1': 224, 'dark': 165.0, 'open': False},
          # woa_2 (user, 10-05): "the same correction ... to scissor the
          # ground on the right, just like the other woa_ fields"
          # mossy rock up to brightness ~95 against the white splash
          'woa_2': {'x0': 160, 'x1': 224, 'dark': 100.0}}
DARK = 44.0              # rock below this mean brightness (cliff 48-66)
EMPTY = 0.0              # ...and above this (an unpainted margin texel)
FLAT = 3.0               # a cell flatter than this was never painted
TOP_SLACK = 8            # field units above layer 2's edge top
UV_CELL = 625000
TILE = 52


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _rgb(v):
    v = v.astype(np.uint32)
    return np.stack([((v >> 11) & 31) << 3, ((v >> 6) & 31) << 3,
                     (v & 31) << 3], -1).astype(np.float32)


def plan_field(name, parts, debug=None):
    """(new section 9, info) -- raises on any doubt."""
    from scipy import ndimage as ND
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_parallaxfill as PF
    spec = FIELDS[name.lower()]
    sec9 = parts[8]
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    survey = DC.survey(sec9)
    rows = PF._layers(sec9, survey['back_start'], survey['tex_start'])
    lay = {r[0]: r for r in rows}
    if 1 not in lay or 2 not in lay:
        raise ValueError('needs layers 1 and 2')
    used = collections.defaultdict(set)
    binds = collections.Counter()
    l1, l2 = {}, {}
    for layer, _c, first, n in rows:
        for i in range(n):
            o = first + i * TILE
            for pg, so in ((sec9[o + 32], 10),) + (
                    ((sec9[o + 34], 14),) if sec9[o + 28] else ()):
                sx, sy = struct.unpack_from('<hh', sec9, o + so)
                used[pg].add((sx // 16, sy // 16))
                binds[pg] += 1
            if sec9[o + 28] or sec9[o + 26]:
                continue
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            if layer == 1:
                l1.setdefault((x, y), o)
            elif layer == 2:
                l2[(x, y)] = o
    x0, x1 = spec['x0'], spec['x1']
    cand = sorted((xy, o) for xy, o in l1.items()
                  if x0 <= xy[0] < x1 and xy not in l2
                  and pm[sec9[o + 32]].depth == 2)
    if not cand:
        raise ValueError('no margin layer-1 cells')
    edge = sorted((xy, o) for xy, o in l2.items() if xy[0] == x0 - 16)
    if not edge:
        raise ValueError('no layer-2 floor at the 4:3 edge')
    ys = [xy[1] for xy, _o in cand] + [xy[1] for xy, _o in edge]
    y0, y1 = min(ys), max(ys) + 16
    W = (x1 - (x0 - 16)) * k // 16
    H = (y1 - y0) * k // 16
    img = np.zeros((H, W, 3), np.float32)
    have = np.zeros((H, W), bool)
    seed = np.zeros((H, W), bool)
    flat = []

    def blk(o, so=10, pgoff=32):
        p = pm[sec9[o + pgoff]]
        if p.depth != 2:
            return None
        sx, sy = struct.unpack_from('<hh', sec9, o + so)
        return np.frombuffer(p.data, '<u2').reshape(px, px)[
            sy // 16 * k:sy // 16 * k + k, sx // 16 * k:sx // 16 * k + k]
    for (x, y), o in cand:
        b = blk(o)
        if b is None:
            raise ValueError('margin cell not truecolor')
        X, Y = (x - x0 + 16) * k // 16, (y - y0) * k // 16
        img[Y:Y + k, X:X + k] = _rgb(b)
        # a cell Cosmos left unpainted (flat 0x0841 lift) is not floor
        if float(_rgb(b).mean(-1).std()) > FLAT:
            have[Y:Y + k, X:X + k] = True
        else:
            flat.append((X, Y))
    for (x, y), o in edge:
        b = blk(o)
        if b is None:
            continue
        X, Y = 0, (y - y0) * k // 16
        seed[Y:Y + k, X + k - 2:X + k] = b[:, -2:] != 0
    lum = img.mean(-1)
    if not seed.any():
        raise ValueError('layer 2 draws nothing at the 4:3 edge')
    # the floor cannot rise above layer 2's own floor top at the 4:3 edge
    # (the dark cliff behind the waterfall belongs to the hazed backdrop)
    top = int(np.nonzero(seed.any(1))[0].min()) - TOP_SLACK * k // 16
    dark = have & (lum < spec.get('dark', DARK)) & (lum >= EMPTY)
    dark[:max(top, 0)] = False
    dark = ND.binary_closing(dark, np.ones((5, 5), bool)) & have
    if spec.get('open', True):
        dark = ND.binary_opening(dark, np.ones((3, 3), bool))
    # rock = dark texels connected to layer 2's floor edge or the bottom row
    grow = seed.copy()
    grow[:, k - 1:k + 1] |= seed[:, k - 2:k]
    lab, n = ND.label(dark | grow, np.ones((3, 3), bool))
    keep = set(np.unique(lab[grow & (lab > 0)]).tolist())
    keep |= set(np.unique(lab[-1][lab[-1] > 0]).tolist())
    rock = np.isin(lab, list(keep)) & have & dark
    rock = ND.binary_fill_holes(rock) & have
    # the floor runs down to the bottom of the picture: below its top edge
    # every texel of a column is floor (closes the 1-texel rim the opening
    # leaves at the bottom, and unpainted flat cells inside the floor)
    painted = have.copy()
    for X, Y in flat:
        painted[Y:Y + k, X:X + k] = True
    below = np.maximum.accumulate(rock, axis=0)
    rock = below & painted
    rock[:, :k] = False                       # the 4:3 edge column is layer 2's
    if debug is not None:
        debug['img'], debug['rock'] = img, rock
    if not rock.any():
        raise ValueError('no rock in the margin')
    # cells and records
    pgs = collections.Counter(sec9[o + 32] for _xy, o in edge)
    page = pgs.most_common(1)[0][0]
    todo = []
    for (x, y), o in cand:
        X, Y = (x - x0 + 16) * k // 16, (y - y0) * k // 16
        m = rock[Y:Y + k, X:X + k]
        if m.any():
            todo.append(((x, y), o, m))
    # a floor position Cosmos filled with a paletted filler (woa_1's bottom
    # right corner, a flat black 1x tile) gets the floor cell to its left
    have_xy = {xy for xy, _o, _m in todo}
    srcs = {xy: (o, m) for xy, o, m in todo}
    for y in range(y0, y1, 16):
        for x in range(x0, x1, 16):
            if (x, y) in have_xy or (x - 16, y) not in srcs:
                continue
            up = (x, y - 16)
            lo, lm = srcs[(x - 16, y)]
            if up not in srcs or not srcs[up][1][-1].all() or not lm.all():
                continue
            if (x, y) not in l1 or (x, y) in l2:
                continue
            todo.append(((x, y), lo, lm))
            have_xy.add((x, y))
    # the floor's own page first; when it is full (woa_2), any other
    # truecolor page of this field with room (a layer-2 record may name any
    # page)
    l2pages = collections.Counter(sec9[o + 32] for o in l2.values())
    order = [page] + [s_ for s_, _n in l2pages.most_common() if s_ != page] \
        + sorted(s_ for s_, p_ in pm.items() if p_.depth == 2
                 and s_ != page and s_ not in l2pages)
    for s_ in order:
        p_ = pm.get(s_)
        if p_ is None or p_.depth != 2 or p_.px != px:
            continue
        if 0x0F <= s_ < 0x1A:                 # truecolor FX pages: never a
            continue                          # base page (608b gate)
        fr = [(cx, cy) for cy in range(16) for cx in range(16)
              if (cx, cy) not in used[s_]]
        if len(fr) >= len(todo) and binds[s_] + len(todo) <= 256:
            page, free = s_, fr
            break
    else:
        raise ValueError('%d cells needed, no truecolor page has room'
                         % len(todo))
    p = pm[page]
    data = np.frombuffer(p.data, '<u2').reshape(px, px).copy()
    _l, count_at, first, count = lay[2]
    recs = [sec9[first + i * TILE:first + (i + 1) * TILE]
            for i in range(count)]
    new = []
    for i, ((x, y), o, m) in enumerate(todo):
        cx, cy = free[i]
        b = blk(o)
        cell = np.where(m, b, 0).astype(np.uint16)
        cell[m & (cell == 0)] = 0x0841            # opaque black, not the key
        cell = cell & 0xFFDF                      # green LSB clear (1555)
        data[cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = cell
        # template: the layer-2 floor record nearest in y at the 4:3 edge
        tpl = min(edge, key=lambda e: abs(e[0][1] - y))[1]
        r = bytearray(sec9[tpl:tpl + TILE])
        struct.pack_into('<hh', r, 2, x, y)
        struct.pack_into('<hh', r, 10, cx * 16, cy * 16)
        struct.pack_into('<II', r, 42, cx * UV_CELL, cy * UV_CELL)
        r[32] = page
        new.append(bytes(r))
    # close the seam: layer 2's floor keys its own last texel or two at the
    # 4:3 edge, which let the haze through as a thin line beside the new
    # cells; where the margin floor starts, those texels take its colour
    seam = 0
    cells_used = collections.Counter()
    for layer, _c, fst, cnt in rows:
        for i in range(cnt):
            o = fst + i * TILE
            pg_ = sec9[o + 34] if sec9[o + 28] else sec9[o + 32]
            sx, sy = struct.unpack_from('<hh', sec9, o + (14 if sec9[o + 28]
                                                          else 10))
            cells_used[(pg_, sx // 16, sy // 16)] += 1
    enc = img.copy()
    datas = {page: data}
    for (x, y), o in edge:
        pg_ = sec9[o + 32]
        sx, sy = struct.unpack_from('<hh', sec9, o + 10)
        if cells_used[(pg_, sx // 16, sy // 16)] != 1 or pm[pg_].depth != 2:
            continue
        if pg_ not in datas:
            datas[pg_] = np.frombuffer(pm[pg_].data, '<u2').reshape(
                px, px).copy()
        dd_ = datas[pg_]
        Y = (y - y0) * k // 16
        cell = dd_[sy // 16 * k:sy // 16 * k + k, sx // 16 * k:sx // 16 * k + k]
        r_ = rock[Y:Y + k, k]
        for c_ in (k - 1, k - 2):
            hole = r_ & (cell[:, c_] == 0)
            if hole.any():
                q_ = np.clip(np.floor(enc[Y:Y + k, k] / 8.0 + 0.5), 0, 31)
                q_ = q_.astype(np.uint32)
                v = (q_[:, 0] << 11 | (q_[:, 1] << 1) << 5 | q_[:, 2])
                v = np.where(v == 0, 0x0841, v).astype(np.uint16)
                cell[hole, c_] = v[hole]
                seam += int(hole.sum())
    buf = bytearray(sec9)
    buf[first:first + count * TILE] = b''.join(recs + new)
    struct.pack_into('<H', buf, count_at, count + len(new))
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), px)
    for pg_, dd_ in datas.items():
        q = plist[pg_]
        plist[pg_] = FN.Page(pg_, q.size_flag, 2, dd_.tobytes(), px)
    return FN.replace_texture_block(bytes(buf), plist, t0, t1), {
        'records': len(new), 'texels': int(rock.sum()), 'page': page,
        'seam': seam}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
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
            parts[8], info = plan_field(name, parts)
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s: %d layer-2 record(s), %d texels on page %d'
                           % (name, info['records'], info['texels'],
                              info['page']))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  FLOOR CLIP (BUILD 618z): the floor carried into the '
                   '16:9 margin on layer 2, cut along its edge, so the haze '
                   'stays behind it (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! floor clip %s: %s' % (name, why))
    return '\n'.join(out)
