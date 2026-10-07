#!/usr/bin/env python3
"""ff7nx_floordepth.py -- floor squares that hide what is below the floor.

BUILD 620h. mds7pb_1 (7th Heaven, the pinball lift), hardware 10-07: "when
going down the elevator ... cloud's dynamic weapon pokes through the floor
at certain points ... his sword goes under some squares, and above other
squares ... not all squares on the floor have the same ordering properties".
The user: Cloud keeps his weapon; the floor squares should take priority.

WHAT THE FIELD IS. Layer 1 (601 squares, the whole floor among them) is
drawn with no depth: ID 4095, IDBig 0, so a model is always painted over it.
Only layer-2 squares are depth-tested (z = IDBig / 1e7, x86 0x62BCB2): the
tables, stools, bar, the hatch art -- and the few squares the 1997 artists
placed around the shaft so that Cloud's BODY disappears as he sinks. The
Ninostyle weapon on his back reaches outside them, over plain floor.

MEASURED IN THIS FIELD. Every layer-2 square's ID is the camera-space depth
of the floor under it / 4 (ratio 3.9..4.1 over 207 squares; objects stand
a little above the floor), and IDBig follows the field's own perspective
curve z = A - B / ID exactly (A 1.01624, B 130.054, max error 3e-6).

WHAT THIS DOES. Each layer-1 square whose four corners all see the floor
plane (z = 0) outside the lift openings becomes a layer-2 square at the
depth of the FARTHEST floor point it covers, plus MARGIN IDs. A point on or
above the floor is always nearer than the floor behind it, so everything
standing on the floor draws as before; a point below the floor (the
sinking weapon) is farther than the floor in front of it and is hidden.
A square is never placed nearer than any existing layer-2 square it
overlaps, so rugs, shadows, tables and the hatch keep drawing on top. Where a
square looks into a lift opening, the surface it shows is the shaft's wall or
bottom (the openings are boxes from the walkmesh), so its depth is the
deepest of those: Cloud inside the shaft is nearer and stays visible, while
the part of his weapon that reaches past the shaft wall, under the floor, is
hidden. Depth is the deepest of 5x5 rays over the square.
Records only move from layer 1 to layer 2 (width/height 16, ID, IDBig set):
the record count, the texture block and every page binding are unchanged.

SEVENTH_NX_NO_FLOOR_DEPTH=1 disables.
"""
from __future__ import annotations

import math
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_FLOOR_DEPTH'
REC = 52
MARGIN = 2                      # IDs (x4 = camera depth units) past the floor
# field -> floor plane z, lift openings (x0, x1, y0, y1 in world units)
# Openings are boxes (x0, x1, y0, y1, bottom z) from the walkmesh: the
# lift footprints (Cloud/Barret's at -127/-255 under x -78..-3, y 148..254;
# the pinball's at -255 under y 288..338), walls straight down.
# 'keep_out': world boxes (x0, x1, y0, y1) on the floor plane. A square
# with ANY ray landing inside one stays a plain layer-1 square (BUILD 620j):
# mds7pb_1's box is the pinball machine's shaft, the raised platform in
# front of it, the floor beside it and the back wall behind it (rays over
# the wall land on the plane past y 338) -- hardware 10-07: "the textures
# beside and under / behind the pinball machine elevator ... affecting the
# back of the machine when it comes back up ... simply everything to the
# right and left and behind where we stand".
FIELDS = {
    'mds7pb_1': {'plane': 0.0,
                 'holes': ((-78, -3, 148, 254, -60),),
                 'keep_out': ((-160, 80, 254, 10000),),
                 # the pinball machine's whole travel (lift script: it sinks
                 # to z -280), padded: no square may hide any point of it
                 'protect': ((-85, 3, 282, 344, -285, 180),),
                 # BUILD 620k. The 1997 layer-2 floor squares in front of
                 # Cloud's lift and around the table (hardware 10-07: "my
                 # character poke[s] through the table region") carry IDs up
                 # to 26 deeper than the floor they show, so the sinking body
                 # drew over them. Inside this screen box (tile x0, x1, y0,
                 # y1) every such square is pulled up to the floor's depth;
                 # the squares sharing a cell keep their order.
                 'relayer': (0, 176, 0, 144),
                 # BUILD 620k. Lift script (section 0), exact byte patterns
                 # preceded by their opcode (a5 XYZI, c2 LADER, banks 00 00).
                 'script': (
                     # The machine's UP position was (-35, 322): 7 units
                     # right and 6 back of its DOWN position (-42, 316), so
                     # its floor plate overhung the opening's right edge
                     # (hardware 10-07: "the floor panel ... always peaking
                     # above the floor ... to the right") and left a gap on
                     # the left. Up now takes down's x (-42) and stands 3
                     # units further forward than down's y (313, 620k2:
                     # hardware 10-07 of 620k showed the rim's yellow line
                     # still uncovered along the plate's front edge, about
                     # 2 units wide).
                     ('a5', 'DDFF420100000000', 'D6FF390100000000', 2),
                     ('c2', 'DDFF420100000000', 'D6FF390100000000', 1),
                     # Riding up (Cloud / Tifa leading): from (-35,182,-255)
                     # to (-31,152,0) -- a slanted rise that ended at the
                     # opening's front edge, so his body crossed under the
                     # floor in front of it. Now straight up at (-35, 188),
                     # as close to the cabinet (front at y ~209) as he
                     # stands; he still walks off to (-31, 102). Triangle 30.
                     ('a5', 'DDFFB60001FF0000', 'DDFFBC0001FF0000', 2),
                     ('c2', 'E1FF980000001400', 'DDFFBC0000001E00', 2),
                 )},
}
SCRIPT_OFF_ENV = 'SEVENTH_NX_NO_PINLIFT'
SAMPLES = 5                     # rays per square side


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _camera(sec1):
    c = bytes(sec1)
    R = np.array(struct.unpack_from('<9h', c, 0), float).reshape(3, 3) / 4096.
    o = np.array(struct.unpack_from('<3i', c, 20), float)
    zoom = struct.unpack_from('<H', c, 36)[0]
    return R, o, zoom


def _floor_hit(R, o, zoom, sx, sy, plane):
    """(camera depth, world point) where screen (sx, sy) sees z = plane."""
    Ri = np.linalg.inv(R)
    a = np.einsum('ij,j->i', Ri, np.array([sx / zoom, sy / zoom, 1.0]))
    b = np.einsum('ij,j->i', Ri, -o)
    if abs(a[2]) < 1e-9:
        return None
    t = (plane - b[2]) / a[2]
    if t <= 0:
        return None
    return t, b + t * a


def visible_depth(R, o, zoom, sx, sy, plane, holes):
    """Camera depth of the first surface the ray through (sx, sy) meets:
    the floor plane, or -- where it crosses the plane inside an opening --
    the opening's walls / bottom. None if the ray never meets the floor."""
    Ri = np.linalg.inv(R)
    a = np.einsum('ij,j->i', Ri, np.array([sx / zoom, sy / zoom, 1.0]))
    b = np.einsum('ij,j->i', Ri, -o)
    if abs(a[2]) < 1e-9:
        return None
    t = (plane - b[2]) / a[2]
    if t <= 0:
        return None
    p = b + t * a
    for x0, x1, y0, y1, zb in holes:
        if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
            exits = []
            for k, lo, hi in ((0, x0, x1), (1, y0, y1)):
                if abs(a[k]) > 1e-12:
                    for bound in (lo, hi):
                        tk = (bound - b[k]) / a[k]
                        if tk > t:
                            exits.append(tk)
            exits.append((zb - b[2]) / a[2])
            return min(e for e in exits if e > t)
    return t


def _protected(R, o, zoom, cfg):
    """(screen x, screen y, depth ID) of points sampled through the
    'protect' volumes, or None."""
    pts = []
    for x0, x1, y0, y1, z0, z1 in cfg.get('protect', ()):
        g = np.mgrid[x0:x1 + 1:4, y0:y1 + 1:4, z0:z1 + 1:5]
        pts.append(g.reshape(3, -1).T.astype(float))
    if not pts:
        return None
    P = np.concatenate(pts)
    # BUILD 620k2. Not `R @ P.T`: numpy on macOS (Accelerate BLAS) raises
    # spurious divide-by-zero / overflow / invalid warnings from matmul on
    # finite input (seen in the user's 620k build log). einsum's own loop
    # never calls BLAS; the result is checked finite.
    C = np.einsum('ij,nj->ni', R, P) + o
    if not np.isfinite(C).all() or (C[:, 2] <= 0).any():
        raise ValueError('protected volume behind the camera')
    return np.stack([zoom * C[:, 0] / C[:, 2], zoom * C[:, 1] / C[:, 2],
                     C[:, 2] / 4.0], 1)


def _kept_out(R, o, zoom, x, y, steps, cfg):
    boxes = cfg.get('keep_out', ())
    if not boxes:
        return False
    for dx in steps:
        for dy in steps:
            h = _floor_hit(R, o, zoom, x + dx, y + dy, cfg['plane'])
            if h is None:
                continue
            px, py = h[1][0], h[1][1]
            for x0, x1, y0, y1 in boxes:
                if x0 <= px <= x1 and y0 <= py <= y1:
                    return True
    return False


def _rows(sec9):
    import ff7nx_ripplefade as RF
    return RF._rows(sec9)


def depth_curve(sec9):
    """(A, B) of IDBig/1e7 = A - B / ID over the field's layer-2 squares."""
    ids, zs = [], []
    for layer, _c, first, n in _rows(sec9):
        if layer != 2:
            continue
        for i in range(n):
            o = first + i * REC
            idv = struct.unpack_from('<h', sec9, o + 24)[0]
            big = struct.unpack_from('<I', sec9, o + 38)[0]
            if idv > 16 and big:
                ids.append(idv)
                zs.append(big / 1e7)
    if len(ids) < 20:
        raise ValueError('too few layer-2 squares to fit the depth curve')
    ids = np.array(ids, float)
    zs = np.array(zs)
    M = np.stack([np.ones_like(ids), -1.0 / ids], 1)
    co = np.linalg.lstsq(M, zs, rcond=None)[0]
    err = float(np.abs(np.einsum('ij,j->i', M, co) - zs).max())
    if err > 1e-4:
        raise ValueError('depth curve does not fit (max error %.2g)' % err)
    return float(co[0]), float(co[1])


def _relayer(cfg, R, o, zoom, A, B, s9):
    """(new section 9, cells changed). Layer-2 squares (16x16, ID < 4000)
    inside cfg['relayer'] whose IDs are deeper than the deepest floor point
    they show (+MARGIN) are moved up to it. Records sharing a cell keep
    their relative order and stay behind any nearer record of that cell."""
    box = cfg.get('relayer')
    if not box:
        return s9, 0
    x0, x1, y0, y1 = box
    out = bytearray(s9)
    cells = {}
    for layer, _c, first, n in _rows(s9):
        if layer != 2:
            continue
        for i in range(n):
            o_ = first + i * REC
            x, y = struct.unpack_from('<hh', s9, o_ + 2)
            w = max(struct.unpack_from('<HH', s9, o_ + 18)) or 16
            cells.setdefault((x, y, w), []).append(
                (struct.unpack_from('<h', s9, o_ + 24)[0], o_))
    prot = _protected(R, o, zoom, cfg)
    steps = np.linspace(0, 16, SAMPLES)
    changed = 0
    for (x, y, w), recs in cells.items():
        if w != 16 or x % 16 or y % 16 or not (x0 <= x < x1 and y0 <= y < y1):
            continue
        if any(idv >= 4000 or idv <= 16 for idv, _o in recs):
            continue
        if _kept_out(R, o, zoom, x, y, steps, cfg):
            continue
        ds = [visible_depth(R, o, zoom, x + dx, y + dy, cfg['plane'],
                            cfg['holes']) for dx in steps for dy in steps]
        if any(d is None for d in ds):
            continue
        d = int(math.ceil(max(ds) / 4.0)) + MARGIN
        near = [idv for idv, _o in recs if idv <= d]
        deep = sorted((idv, o_) for idv, o_ in recs if idv > d)
        if not deep:
            continue
        base = max([d] + [v + 1 for v in near])
        new_ids = [base + k for k in range(len(deep))]
        if new_ids[-1] >= deep[-1][0] and new_ids[0] >= deep[0][0]:
            continue
        if prot is not None:
            inside = ((prot[:, 0] >= x - 1) & (prot[:, 0] < x + 17)
                      & (prot[:, 1] >= y - 1) & (prot[:, 1] < y + 17))
            if inside.any() and (prot[inside, 2] > new_ids[0]).any():
                continue
        for (old_id, o_), nid in zip(deep, new_ids):
            nid = min(nid, old_id)
            struct.pack_into('<h', out, o_ + 24, nid)
            struct.pack_into('<I', out, o_ + 38,
                             int(round((A - B / nid) * 1e7)))
        changed += 1
    return bytes(out), changed


def plan_script(name, sec0):
    """(new section 0, replacements made) -- raises unless every pattern is
    found exactly as often as expected."""
    pats = FIELDS[name].get('script', ())
    s0 = bytearray(sec0)
    total = 0
    for op, old, new, count in pats:
        ob = bytes.fromhex(op + '0000' + old)
        nb = bytes.fromhex(op + '0000' + new)
        if len(ob) != len(nb):
            raise ValueError('pattern length')
        hits = []
        i = s0.find(ob)
        while i >= 0:
            hits.append(i)
            i = s0.find(ob, i + 1)
        if len(hits) != count:
            if bytes(s0).count(nb) == count:
                continue                       # already applied
            raise ValueError('%s %s found %d times, expected %d'
                             % (op, old, len(hits), count))
        for i in hits:
            s0[i:i + len(nb)] = nb
        total += len(hits)
    return bytes(s0), total


def plan(name, sec1, sec9):
    """(new section 9, stats) -- raises on any doubt."""
    cfg = FIELDS[name]
    s9 = bytes(sec9)
    R, o, zoom = _camera(sec1)
    A, B = depth_curve(s9)
    s9, relayered = _relayer(cfg, R, o, zoom, A, B, s9)
    rows = {r[0]: r for r in _rows(s9)}
    if 1 not in rows or 2 not in rows:
        raise ValueError('no layer 1 or 2')
    _l, c1, f1, n1 = rows[1]
    _l, c2, f2, n2 = rows[2]
    if f2 <= f1:
        raise ValueError('layer order')
    # existing layer-2 squares: screen box -> nearest-allowed ID floor
    l2 = []
    for i in range(n2):
        r = s9[f2 + i * REC:f2 + (i + 1) * REC]
        x, y = struct.unpack_from('<hh', r, 2)
        w = max(struct.unpack_from('<HH', r, 18)) or 16
        l2.append((x, y, w, struct.unpack_from('<h', r, 24)[0]))
    prot = _protected(R, o, zoom, cfg)
    move, keep = [], []
    for i in range(n1):
        r = bytearray(s9[f1 + i * REC:f1 + (i + 1) * REC])
        x, y = struct.unpack_from('<hh', r, 2)
        if r[26] or r[28] or struct.unpack_from('<h', r, 24)[0] != 4095:
            keep.append(bytes(r))
            continue
        steps = np.linspace(0, 16, SAMPLES)
        if _kept_out(R, o, zoom, x, y, steps, cfg):
            keep.append(bytes(r))
            continue
        ds = [visible_depth(R, o, zoom, x + dx, y + dy, cfg['plane'],
                            cfg['holes']) for dx in steps for dy in steps]
        if any(d is None for d in ds):
            keep.append(bytes(r))
            continue
        idv = int(math.ceil(max(ds) / 4.0)) + MARGIN
        for (lx, ly, lw, lid) in l2:
            if lx < x + 16 and x < lx + lw and ly < y + 16 and y < ly + lw \
                    and lid > 16:
                idv = max(idv, lid + 1)
        if not 16 < idv < 4095:
            keep.append(bytes(r))
            continue
        if prot is not None:
            inside = ((prot[:, 0] >= x - 1) & (prot[:, 0] < x + 17)
                      & (prot[:, 1] >= y - 1) & (prot[:, 1] < y + 17))
            if inside.any() and (prot[inside, 2] > idv).any():
                keep.append(bytes(r))
                continue
        big = int(round((A - B / idv) * 1e7))
        struct.pack_into('<HH', r, 18, 16, 16)
        struct.pack_into('<h', r, 24, idv)
        struct.pack_into('<I', r, 38, big)
        move.append(bytes(r))
    if not move:
        raise ValueError('no floor square qualifies')
    out = bytearray(s9)
    l1 = b''.join(keep)
    l2b = s9[f2:f2 + n2 * REC] + b''.join(move)
    # layer 1 block, its trailer, the layer-2 header up to its records
    mid = s9[f1 + n1 * REC:f2]
    new = (s9[:f1] + l1 + mid + l2b + s9[f2 + n2 * REC:])
    if len(new) != len(s9):
        raise ValueError('size changed')
    out = bytearray(new)
    struct.pack_into('<H', out, c1, len(keep))
    shift = (n1 - len(keep)) * REC
    struct.pack_into('<H', out, c2 - shift, n2 + len(move))
    out = bytes(out)
    rows2 = {r[0]: r for r in _rows(out)}
    if rows2[1][3] != len(keep) or rows2[2][3] != n2 + len(move):
        raise ValueError('re-walk disagrees')
    return out, {'moved': len(move), 'kept': len(keep), 'curve': (A, B),
                 'relayered': relayered}


def script_disabled():
    return os.environ.get(SCRIPT_OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': [], 'script': []}
    if disabled() and script_disabled():
        return st
    encode = encode or archive.encode_field
    for name in FIELDS:
        if name not in archive.index:
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(archive.index[name]))
            parts = list(lgp.split_sections(raw))
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:90]))
            continue
        changed = False
        if not disabled():
            try:
                parts[8], info = plan(name, parts[1], parts[8])
                changed = True
                st['names'].append(
                    '%s (%d floor squares depth-tested, %d squares pulled '
                    'up to the floor)' % (name, info['moved'],
                                          info['relayered']))
            except Exception as exc:                           # noqa: BLE001
                st['refused'].append((name, str(exc)[:90]))
        if not script_disabled() and FIELDS[name].get('script'):
            try:
                parts[0], n = plan_script(name, parts[0])
                changed = True
                st['script'].append('%s (%d words)' % (name, n))
            except Exception as exc:                           # noqa: BLE001
                st['refused'].append((name + ' script', str(exc)[:90]))
        if changed:
            payloads[name] = encode(lgp.join_sections(parts))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  FLOOR DEPTH (BUILD 620h): floor squares now hide what '
                   'is below the floor: %s. %s=1 disables.'
                   % (', '.join(st['names']), OFF_ENV))
    if st.get('script'):
        out.append('  PINBALL LIFT (BUILD 620k): the machine stands centred on '
                   'its opening and the ride up is straight, close to the '
                   'cabinet: %s. %s=1 disables.'
                   % (', '.join(st['script']), SCRIPT_OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! floor depth %s: unchanged -- %s' % (name, why))
    return '\n'.join(out)
