"""
BUILD 730. Battle background seams.

Some battle stages build their sky out of two stacked eight-sided prisms of
the same radius: an upper one and a lower one, each with its own texture.
The two prisms OVERLAP vertically by a sliver (qy/pu, the las4 stages: 150
units, from y=-75 to y=+75). In that band two surfaces lie in exactly the
same plane, so the depth test decides between them pixel by pixel and the
seam sparkles as the camera moves. That is z-fighting, and it is in the
vanilla geometry; the mod's HD art only makes it obvious.

`trim_overlaps` moves both boundary rings to the middle of the overlap, so
the two prisms meet edge to edge with identical vertex positions and no
coplanar band is left. Each moved vertex's texture coordinate slides along
its wall edge by the same fraction, so the art is not stretched: it is
clipped by exactly the part that used to be hidden under the other prism.

`fix_column_line` repairs a one-pixel bright vertical line baked into four
of Avalanche Arisen's HD tiles (column 464 of STAGE40_T02/T03 and
STAGE70_T01/T02, the same art reused by both stages).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import struct

HEADER = 128


def _parse(d):
    """Positions, texcoords, polys (global vertex/texcoord ids) of a .p."""
    if len(d) < HEADER:
        return None
    h = struct.unpack_from('<16i', d, 0)
    nv, nn, nu1, nt, nc, ne, npo, nu2, nu3, nh, ng = h[3:14]
    if h[0] != 1 or min(h[3:15]) < 0 or nv <= 0 or npo <= 0 or ng <= 0:
        return None
    o = HEADER
    vo = o
    o += 12 * nv + 12 * nn + 12 * nu1
    to = o
    o += 8 * nt + 4 * nc + 4 * npo + 4 * ne
    po = o
    o += 24 * npo + 24 * nu2 + 3 * nu3 + 100 * nh
    go = o
    o += 56 * ng
    if o > len(d):
        return None
    V = [list(struct.unpack_from('<3f', d, vo + 12 * i)) for i in range(nv)]
    groups = [struct.unpack_from('<14i', d, go + 56 * i) for i in range(ng)]
    tex = {}       # vertex id -> texcoord id
    polys = []
    gverts = []    # per group: its vertex ids
    for g in groups:
        p0, pn, v0, vn, t0, tused = g[1], g[2], g[3], g[4], g[11], g[12]
        if p0 < 0 or pn < 0 or p0 + pn > npo or v0 < 0 or v0 + vn > nv:
            return None
        gverts.append(range(v0, v0 + vn))
        for li in range(vn):
            if tused and 0 <= t0 + li < nt:
                tex[v0 + li] = t0 + li
        for k in range(p0, p0 + pn):
            idx = struct.unpack_from('<3H', d, po + 24 * k + 2)
            if max(idx) >= vn:
                return None
            polys.append(tuple(v0 + i for i in idx))
    return dict(V=V, vo=vo, to=to, tex=tex, polys=polys, gverts=gverts)


def _key(v):
    return (round(v[0]), round(v[2]))


def _ring(p, y):
    return [i for i, v in enumerate(p['V']) if abs(v[1] - y) < 0.5]


def _partners(p, ring, y):
    """For each ring vertex, the vertex straight up/down its wall edge."""
    nb = {}
    for a, b, c in p['polys']:
        for x, z in ((a, b), (b, c), (c, a)):
            nb.setdefault(x, set()).add(z)
            nb.setdefault(z, set()).add(x)
    V = p['V']
    out = {}
    for i in ring:
        best = None
        for j in nb.get(i, ()):
            if _key(V[j]) == _key(V[i]) and abs(V[j][1] - y) >= 0.5:
                if best is None or abs(V[j][1] - y) < abs(V[best][1] - y):
                    best = j
        if best is None:
            return None
        out[i] = best
    return out


def _sides(p):
    ys = [v[1] for v in p['V']]
    return min(ys), max(ys)


def find_overlaps(parsed):
    """
    Pairs (upper, lower, y_top_of_lower, y_bottom_of_upper) of meshes whose
    boundary rings sit at the same x,z and overlap vertically. FF7 battle
    space is y-down, so the upper mesh has the smaller y values.
    """
    out = []
    names = sorted(parsed)
    for a in names:
        pa = parsed[a]
        a_lo, a_hi = _sides(pa)
        ra = _ring(pa, a_hi)
        ka = {_key(pa['V'][i]) for i in ra}
        if len(ka) < 6:
            continue
        for b in names:
            if b == a or b[:2] != a[:2]:
                continue
            pb = parsed[b]
            b_lo, b_hi = _sides(pb)
            if not (b_lo < a_hi and a_lo < b_lo and a_hi < b_hi):
                continue
            band = a_hi - b_lo
            if band > 0.1 * min(a_hi - a_lo, b_hi - b_lo):
                continue
            rb = _ring(pb, b_lo)
            if {_key(pb['V'][i]) for i in rb} != ka:
                continue
            if _partners(pa, ra, a_hi) is None or _partners(pb, rb, b_lo) is None:
                continue
            out.append((a, b, b_lo, a_hi))
    return out


def _move_ring(d, p, y_from, y_to):
    d = bytearray(d)
    ring = _ring(p, y_from)
    part = _partners(p, ring, y_from)
    for i in ring:
        j = part[i]
        yj = p['V'][j][1]
        f = (y_to - y_from) / (yj - y_from)
        struct.pack_into('<f', d, p['vo'] + 12 * i + 4, y_to)
        ti, tj = p['tex'].get(i), p['tex'].get(j)
        if ti is not None and tj is not None:
            u0, v0 = struct.unpack_from('<2f', d, p['to'] + 8 * ti)
            u1, v1 = struct.unpack_from('<2f', d, p['to'] + 8 * tj)
            struct.pack_into('<2f', d, p['to'] + 8 * ti,
                             u0 + (u1 - u0) * f, v0 + (v1 - v0) * f)
    return bytes(d)


# BUILD 731. The big ground/abyss discs under most stages (qyba under the
# las4 platforms, and the "an" disc of thirty-odd other stages) are painted
# one colour on purpose: every vertex samples the same one vanilla texel, give
# or take a texel (u 0.988..0.992 on qyae). With the HD tiles that one texel
# is three to four pixels of busy art, and the 256-colour dither on top; the
# disc is sixty thousand units wide, so those few pixels are blown up across
# the whole floor as blotchy "garbage". Pointing every vertex of such a group
# at one spot gives the flat colour the original intended.
FLAT_UV_SPAN = 2.5 / 256     # at most ~2 vanilla texels across the group
FLAT_MIN_EXTENT = 20000.0    # and the group is a stage-sized surface


def _flatten_uv(d, p):
    """Return (bytes, n_groups) with every tiny-UV large group collapsed."""
    b = None
    n = 0
    for verts in p['gverts']:
        ts = sorted({p['tex'][i] for i in verts if i in p['tex']})
        if len(ts) < 2:
            continue
        uv = [struct.unpack_from('<2f', d, p['to'] + 8 * t) for t in ts]
        us = [q[0] for q in uv]
        vs = [q[1] for q in uv]
        if max(us) - min(us) > FLAT_UV_SPAN or max(vs) - min(vs) > FLAT_UV_SPAN:
            continue
        if len(set(uv)) < 2:
            continue
        pts = [p['V'][i] for i in verts]
        ext = max(max(q[k] for q in pts) - min(q[k] for q in pts)
                  for k in range(3))
        if ext < FLAT_MIN_EXTENT:
            continue
        cu = (max(us) + min(us)) / 2.0
        cv = (max(vs) + min(vs)) / 2.0
        if b is None:
            b = bytearray(d)
        for t in ts:
            struct.pack_into('<2f', b, p['to'] + 8 * t, cu, cv)
        n += 1
    return (bytes(b) if b is not None else d), n


def flatten_floors(files):
    """files: {name: bytes}. Returns ({name: fixed bytes}, [names])."""
    out = {}
    for n in sorted(files):
        if len(n) != 4 or not n.isalpha():
            continue
        p = _parse(files[n])
        if p is None:
            continue
        d, k = _flatten_uv(files[n], p)
        if k:
            out[n] = d
    return out, sorted(out)


# BUILD 731. The same overlap on the ground. Flat stage pieces at one height
# (sand, surf, sea in the ol beach stage; ok/on/po/qm/rh/... the same way)
# are laid with thin overlapping strips where they join, so the join
# flickers exactly like the sky band did. Trimming arbitrary ground outlines
# would mean re-cutting triangles; instead each piece is given a "layer" so
# that in every strip one piece is consistently a few units higher and wins
# the depth test. The piece under the battle's origin, where everyone stands,
# stays at layer 0, so nobody's feet move.
GROUND_LIFT = 20.0       # units per layer, upward (FF7 battle y is down)
SLIVER_SHARE = 0.1       # an overlap this small a share of both is a join


def _clip(subj, clipper):
    out = subj
    for i in range(len(clipper)):
        ax, az = clipper[i]
        bx, bz = clipper[(i + 1) % len(clipper)]
        inp, out = out, []
        if not inp:
            break
        for j in range(len(inp)):
            p, q = inp[j], inp[j - 1]
            sp = (bx - ax) * (p[1] - az) - (bz - az) * (p[0] - ax)
            sq = (bx - ax) * (q[1] - az) - (bz - az) * (q[0] - ax)
            if sp >= 0:
                if sq < 0:
                    t = sq / (sq - sp)
                    out.append((q[0] + (p[0] - q[0]) * t, q[1] + (p[1] - q[1]) * t))
                out.append(p)
            elif sq >= 0:
                t = sq / (sq - sp)
                out.append((q[0] + (p[0] - q[0]) * t, q[1] + (p[1] - q[1]) * t))
    return out


def _parea(pts):
    if len(pts) < 3:
        return 0.0
    return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1]
                   for i in range(len(pts)))) / 2.0


def _xz_tris(p):
    out = []
    for t in p['polys']:
        tri = [(p['V'][i][0], p['V'][i][2]) for i in t]
        s = ((tri[1][0] - tri[0][0]) * (tri[2][1] - tri[0][1])
             - (tri[2][0] - tri[0][0]) * (tri[1][1] - tri[0][1]))
        if abs(s) < 1e-3:
            continue
        if s < 0:
            tri = tri[::-1]
        bb = (min(q[0] for q in tri), max(q[0] for q in tri),
              min(q[1] for q in tri), max(q[1] for q in tri))
        out.append((tri, bb, abs(s) / 2.0))
    return out


def _overlap(ta, tb):
    ov = 0.0
    for a, ba, _ in ta:
        for b, bb, _ in tb:
            if (ba[1] <= bb[0] or bb[1] <= ba[0]
                    or ba[3] <= bb[2] or bb[3] <= ba[2]):
                continue
            ov += _parea(_clip(a, b))
    return ov


def _covers_origin(tris):
    for tri, bb, _ in tris:
        if bb[0] <= 0 <= bb[1] and bb[2] <= 0 <= bb[3]:
            if _parea(_clip(tri, [(-1, -1), (1, -1), (1, 1), (-1, 1)])) > 0:
                return True
    return False


def layer_grounds(files):
    """files: {name: bytes}. Returns ({name: lifted bytes}, {name: layer})."""
    flat = {}
    for n in sorted(files):
        if len(n) != 4 or not n.isalpha():
            continue
        p = _parse(files[n])
        if p is None:
            continue
        ys = [v[1] for v in p['V']]
        if max(ys) - min(ys) < 0.5:
            tris = _xz_tris(p)
            if tris:
                flat.setdefault((n[:2], round(ys[0])), {})[n] = (p, tris)
    out, layers = {}, {}
    for (_st, y), ms in sorted(flat.items()):
        names = sorted(ms)
        area = {n: sum(t[2] for t in ms[n][1]) for n in names}
        adj = {n: set() for n in names}
        stacked = set()
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ov = _overlap(ms[a][1], ms[b][1])
                if ov <= 1e4:
                    continue
                if ov < SLIVER_SHARE * area[a] and ov < SLIVER_SHARE * area[b]:
                    adj[a].add(b)
                    adj[b].add(a)
                else:
                    stacked.update((a, b))     # real layers: not ours
        names = [n for n in names if adj[n] and n not in stacked]
        if not names:
            continue
        order = sorted(names, key=lambda n: (not _covers_origin(ms[n][1]),
                                             -area[n], n))
        lv = {}
        for n in order:
            used = {lv[m] for m in adj[n] if m in lv}
            k = 0
            while k in used:
                k += 1
            lv[n] = k
        for n, k in lv.items():
            if not k:
                continue
            p = ms[n][0]
            b = bytearray(files[n])
            for i in range(len(p['V'])):
                struct.pack_into('<f', b, p['vo'] + 12 * i + 4,
                                 p['V'][i][1] - GROUND_LIFT * k)
            out[n] = bytes(b)
            layers[n] = k
    return out, layers


def fix_stages(files):
    """All stage fixes; returns ({name: bytes}, overlaps, floors, grounds)."""
    trimmed, done = trim_overlaps(files)
    merged = dict(files)
    merged.update(trimmed)
    flat, floors = flatten_floors(merged)
    merged.update(flat)
    lifted, grounds = layer_grounds(merged)
    out = dict(trimmed)
    out.update(flat)
    for n, d in lifted.items():
        # a lifted piece may also have had its UVs flattened: keep both
        if n in flat:
            p = _parse(d)
            d, _k = _flatten_uv(d, p)
        out[n] = d
    return out, done, floors, grounds


def trim_overlaps(files):
    """
    files: {name: bytes} of battle.lgp entries (anything; non-.p ignored).
    Returns ({name: fixed bytes}, [(upper, lower, band)]) for the meshes
    changed. A texcoord shared by two vertices is moved once per vertex,
    which only happens if a mesh reuses one texcoord on both a ring vertex
    and another vertex -- refused rather than guessed.
    """
    parsed = {}
    for n, d in files.items():
        if len(n) == 4 and n.isalpha():
            p = _parse(d)
            if p is not None:
                parsed[n] = p
    pairs = find_overlaps(parsed)
    out, done = {}, []
    used = set()
    for a, b, b_lo, a_hi in pairs:
        if a in used or b in used:
            continue
        ok = True
        for n, y in ((a, a_hi), (b, b_lo)):
            ring = _ring(parsed[n], y)
            ts = [parsed[n]['tex'].get(i) for i in ring]
            if len(set(ts)) != len(ts):
                ok = False
        if not ok:
            continue
        mid = (a_hi + b_lo) / 2.0
        out[a] = _move_ring(files[a], parsed[a], a_hi, mid)
        out[b] = _move_ring(files[b], parsed[b], b_lo, mid)
        used.update((a, b))
        done.append((a, b, a_hi - b_lo))
    return out, done


# Avalanche Arisen tiles with a one-pixel bright column baked into the art,
# by the battle.lgp name they replace: STAGE40_T02/T03 -> puae/puaf and
# STAGE70_T01/T02 -> qyad/qyae (the same two images). Measured: column 464
# is brighter than the mean of its neighbours by 8.0 (upper sky, line only in
# its painted rows) and 27.7 (lower sky); the next strongest column in the
# same tiles is 5.2 and 21.1 at the never-sampled right edge.
LINE_NAMES = frozenset({'puae', 'puaf', 'qyad', 'qyae'})
LINE_COLUMN = 464
LINE_MIN_EXCESS = 6.0
LINE_TAG = 'L1'


def fix_column_line(rgba, w, h, col=LINE_COLUMN):
    """
    Replace column `col` with the mean of its two neighbours wherever it is
    brighter than both, but only if the column as a whole stands out (mean
    excess over its neighbours above LINE_MIN_EXCESS) -- so a different or
    already-fixed release of the art is left alone. Returns (bytes, n_px).
    """
    if w != 1024 or h < 1:
        return rgba, 0
    b = bytearray(rgba)
    tot = 0.0
    for y in range(h):
        r = (y * w + col) * 4
        lum = sum(b[r:r + 3])
        nb = (sum(b[r - 4:r - 1]) + sum(b[r + 4:r + 7])) / 2.0
        tot += (lum - nb) / 3.0
    if tot / h < LINE_MIN_EXCESS:
        return rgba, 0
    n = 0
    for y in range(h):
        r = (y * w + col) * 4
        lum = sum(b[r:r + 3])
        if lum > sum(b[r - 4:r - 1]) and lum > sum(b[r + 4:r + 7]):
            for c in range(3):
                b[r + c] = (b[r - 4 + c] + b[r + 4 + c] + 1) // 2
            n += 1
    return bytes(b), n
