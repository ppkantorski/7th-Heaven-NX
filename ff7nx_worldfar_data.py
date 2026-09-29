#!/usr/bin/env python3
"""wm0.far -- the far-field terrain the native 5x5 window never draws.

BUILD 585. Generated at build time from the wm0.map the build ships (a mod's,
or the dump's). The runtime half is `ff7nx_worldfar`: it reads this file into
the world map's own allocation and pushes each tile through the SAME native
functions that draw the 5x5 window (transform 0x75F0AD, cull 0x75F263,
submit 0x75F68C), so textures, Gaia's HD art, day/night and the spherical
world all apply to it exactly as they apply to the near terrain.

Every tile is an ordinary native mesh record -- tris (12 bytes), verts
(s16 x,y,z,0), normals (s16 x,y,z,0) -- because that is the only format the
native submit understands, and it keeps the native limits:

  * <= 120 vertices (the transform always processes 122 and its scratch
    buffer holds 128);
  * s16 coordinates, so an L2 tile is centred on its sector.

Two levels:

  L1  one per map mesh (36 x 28 = 1008), 8192 units, 5 x 5 lattice (2048),
      32 triangles. Used near the window.
  L2  one per sector (9 x 7 = 63), 32768 units, 9 x 9 lattice (4096),
      128 triangles; a sector that is flat and one texture everywhere (open
      sea) is 5 x 5 (8192), 32 triangles. Used far away.

Heights are the native surface sampled exactly at the lattice points, so two
tiles of the same level share identical edges. Where levels meet (L1 vs the
native window, L2 vs L1) the finer side has extra vertices on the shared
edge, so every tile carries a SKIRT per edge -- a strip hanging down (-y;
heights are +y up, the stored normals point -y as the native ones do) by the
largest gap the native edge can open against the coarse one, both windings
(the native submit culls back faces) -- and the runtime submits a skirt only
on edges that border a finer neighbour.

Texture per quad is the one covering most of the quad's area in the native
mesh, mapped once across the quad using that texture's own texel rectangle
as the native triangles use it. Walkmap byte and the texinfo high bits come
from the largest native triangle of that texture in the quad.
"""
from __future__ import annotations

import struct

import numpy as np

import lgp

SECTOR = 0xB800
MX, MZ = 36, 28             # meshes
SX, SZ = 9, 7               # sectors
MESH = 8192
L1_N = 5                    # lattice points per L1 side
L2_N = 9
L2_FLAT_N = 5
MAX_VERTS = 120
MAGIC = b'WFAR'
VERSION = 4
# BUILD 591: story-progress variants. wm0.map carries 69 sectors: the 63 of
# the map and six replacements the game swaps in by world progress
# ([0xE28CB4], x86 0x750F54): (base sector, replacement, progress must be >).
# The Northern Crater, the Junon/Mideel/Temple changes are these.
VARIANTS = ((50, 63, 0), (41, 64, 1), (42, 65, 1), (60, 66, 2), (47, 67, 3),
            (48, 68, 3))
NALT = len(VARIANTS)
N1 = MX * MZ + 16 * NALT        # L1 tiles: the map's, then 16 per variant
N2 = SX * SZ + NALT             # L2 tiles: the map's, then one per variant
HDR = 32
ENTRY = 32                  # index entry size
# tris, verts, norms (file offsets); main tri count; skirt first tri x4;
# skirt tri count x4; vertex count; flags; ymin; ymax
ENTRY_FMT = '<IIIH4H4BBBhh'
assert struct.calcsize(ENTRY_FMT) == ENTRY
# v2 (BUILD 587): vertices are packed s16 x,y,z (6 bytes), normals s8 x,y,z
# (3 bytes, unit * 127); after the tile index comes one MESHREF per map mesh.
# v3 (BUILD 588): each MESHREF is the offset (in wm0.far) and length of that
# mesh's own LZS block from wm0.map, copied in after the tiles.
MESHREF_FMT = '<II'
MESHREF = 8
SKIRT_MARGIN = 64
SKIRT_MIN = 64
L2_UV_KEEP = 0.5          # far tiles map the centre half of each texture


class FarError(Exception):
    pass


# ------------------------------------------------------------- wm0.map
def mesh_blocks(path):
    """[LZS bytes] per mesh, mesh order mz * 36 + mx: the game's own
    compressed full-detail meshes, carried inside wm0.far (v3) so the runtime
    never reads a file while the world map runs."""
    d = open(path, 'rb').read()
    return [d[o:o + ln] for o, ln in mesh_refs(path)]


def mesh_refs(path):
    """[(file offset of the LZS data, its length)] per mesh in wm0.map."""
    d = open(path, 'rb').read()
    refs = {}
    nsec = SX * SZ + (NALT if len(d) >= SECTOR * (SX * SZ + NALT) else 0)
    for si in range(nsec):
        base = si * SECTOR
        offs = struct.unpack_from('<16I', d, base)
        for k, o in enumerate(offs):
            clen = struct.unpack_from('<I', d, base + o)[0]
            mx = (si % SX) * 4 + (k & 3)
            mz = (si // SX) * 4 + (k >> 2)
            if si < SX * SZ:
                refs[mz * MX + mx] = (base + o + 4, clen)
            else:
                refs[MX * MZ + 16 * (si - SX * SZ) + k] = (base + o + 4, clen)
    for a, (bs, _alt, _p) in enumerate(VARIANTS):
        for k in range(16):
            if MX * MZ + 16 * a + k not in refs:     # no variants in this map
                mx = (bs % SX) * 4 + (k & 3)
                mz = (bs // SX) * 4 + (k >> 2)
                refs[MX * MZ + 16 * a + k] = refs[mz * MX + mx]
    return [refs[i] for i in range(N1)]


def read_meshes(path):
    """[(sector)][16] -> (tris u8[n,12], verts i16[n,4], norms i16[n,4])."""
    d = open(path, 'rb').read()
    if len(d) < SECTOR * SX * SZ:
        raise FarError('wm0.map is %d bytes, want >= %d'
                       % (len(d), SECTOR * SX * SZ))
    out = []
    nsec = SX * SZ + (NALT if len(d) >= SECTOR * (SX * SZ + NALT) else 0)
    for si in range(nsec):
        s = d[si * SECTOR:(si + 1) * SECTOR]
        offs = struct.unpack('<16I', s[:64])
        ms = []
        for o in offs:
            clen = struct.unpack('<I', s[o:o + 4])[0]
            raw = lgp.lzs_decompress(s[o + 4:o + 4 + clen])
            nt, nv = struct.unpack('<HH', raw[:4])
            p = 4
            tris = np.frombuffer(raw[p:p + nt * 12], np.uint8).reshape(nt, 12)
            p += nt * 12
            verts = np.frombuffer(raw[p:p + nv * 8], '<i2').reshape(nv, 4)
            ms.append((tris.copy(), verts.astype(np.int64)))
        out.append(ms)
    return out


def mesh_at(M, mx, mz):
    return M[(mz >> 2) * SX + (mx >> 2)][(mz & 3) * 4 + (mx & 3)]


def texid(tris):
    return (tris[:, 10].astype(np.int64) | (tris[:, 11].astype(np.int64) << 8)) & 0x1FF


def tex_rects(M):
    """tex id -> (u0, v0, u1, v1) over every native triangle using it."""
    rects = {}
    for sec in M:
        for tris, _v in sec:
            if not len(tris):
                continue
            t = texid(tris)
            u = tris[:, [4, 6, 8]].astype(np.int64)
            v = tris[:, [5, 7, 9]].astype(np.int64)
            for k in np.unique(t):
                sel = t == k
                r = (int(u[sel].min()), int(v[sel].min()),
                     int(u[sel].max()), int(v[sel].max()))
                if k in rects:
                    a = rects[k]
                    r = (min(a[0], r[0]), min(a[1], r[1]),
                         max(a[2], r[2]), max(a[3], r[3]))
                rects[int(k)] = r
    return rects


# ------------------------------------------------------------- sampling
class MeshSurface:
    """One native mesh as a height function and per-triangle attributes."""

    def __init__(self, tris, verts):
        self.tris = tris
        self.v = verts
        if len(tris):
            i = tris[:, :3].astype(np.int64)
            self.a = verts[i[:, 0], :3].astype(np.float64)
            self.b = verts[i[:, 1], :3].astype(np.float64)
            self.c = verts[i[:, 2], :3].astype(np.float64)
            ab = self.b - self.a
            ac = self.c - self.a
            self.area = 0.5 * np.abs(ab[:, 0] * ac[:, 2] - ab[:, 2] * ac[:, 0])
            self.cen = (self.a + self.b + self.c) / 3.0
            self.tex = texid(tris)
        else:
            self.area = np.zeros(0)

    def height(self, x, z):
        """y at (x, z) on the surface, from the containing triangle."""
        a, b, c = self.a, self.b, self.c
        d = ((b[:, 2] - c[:, 2]) * (a[:, 0] - c[:, 0])
             + (c[:, 0] - b[:, 0]) * (a[:, 2] - c[:, 2]))
        ok = np.abs(d) > 1e-9
        d = np.where(ok, d, 1.0)
        l1 = ((b[:, 2] - c[:, 2]) * (x - c[:, 0])
              + (c[:, 0] - b[:, 0]) * (z - c[:, 2])) / d
        l2 = ((c[:, 2] - a[:, 2]) * (x - c[:, 0])
              + (a[:, 0] - c[:, 0]) * (z - c[:, 2])) / d
        l3 = 1.0 - l1 - l2
        eps = 1e-6
        inside = ok & (l1 >= -eps) & (l2 >= -eps) & (l3 >= -eps)
        if inside.any():
            k = int(np.argmax(inside))
            return float(l1[k] * a[k, 1] + l2[k] * b[k, 1] + l3[k] * c[k, 1])
        # outside every triangle (a hole in the mesh): nearest vertex
        vv = self.v[:, :3].astype(np.float64)
        k = int(np.argmin((vv[:, 0] - x) ** 2 + (vv[:, 2] - z) ** 2))
        return float(vv[k, 1])

    def dominant(self, x0, z0, x1, z1):
        """(tex, byte3, texinfo-high) covering most of the rectangle."""
        if not len(self.area):
            return None
        cx, cz = self.cen[:, 0], self.cen[:, 2]
        sel = (cx >= x0) & (cx < x1) & (cz >= z0) & (cz < z1)
        if not sel.any():
            # triangles larger than the quad: the one holding its centre
            mx, mz = 0.5 * (x0 + x1), 0.5 * (z0 + z1)
            k = int(np.argmin((cx - mx) ** 2 + (cz - mz) ** 2))
            sel = np.zeros(len(cx), bool)
            sel[k] = True
        return sel


def edge_profile(surf, axis, value, lo, hi):
    """Native vertices on the line axis==value within [lo, hi]: (t, y)."""
    v = surf.v
    other = 2 if axis == 0 else 0
    sel = (v[:, axis] == value) & (v[:, other] >= lo) & (v[:, other] <= hi)
    return [(int(t), int(y)) for t, y in zip(v[sel][:, other], v[sel][:, 1])]


# ------------------------------------------------------------- building
def _normal(h, i, j, step):
    n = h.shape[0]
    i0, i1 = max(i - 1, 0), min(i + 1, n - 1)
    j0, j1 = max(j - 1, 0), min(j + 1, n - 1)
    yx = (h[j, i1] - h[j, i0]) / ((i1 - i0) * step)
    yz = (h[j1, i] - h[j0, i]) / ((j1 - j0) * step)
    v = np.array([yx, -1.0, yz])
    v = v / np.linalg.norm(v) * 4096.0
    return [int(round(v[0])), int(round(v[1])), int(round(v[2])), 0]


class Tile:
    def __init__(self):
        self.verts = []          # [x, y, z, 0]
        self.norms = []
        self.main = []           # 12-byte tris
        self.skirts = [[], [], [], []]   # N, E, S, W
        self.ymin = 0
        self.ymax = 0
        self.flags = 0           # bit 0: flat single-texture L2 (8192 grid)

    def add_vert(self, x, y, z, n):
        if not (-32768 <= x <= 32767 and -32768 <= z <= 32767
                and -32768 <= y <= 32767):
            raise FarError('vertex out of s16 range')
        self.verts.append([int(x), int(y), int(z), 0])
        self.norms.append(n)
        return len(self.verts) - 1


def _tri(a, b, c, uva, uvb, uvc, attr):
    tex, b3, hi = attr
    info = (tex & 0x1FF) | (hi << 9)
    return bytes([a, b, c, b3, uva[0], uva[1], uvb[0], uvb[1],
                  uvc[0], uvc[1], info & 0xFF, (info >> 8) & 0xFF])


def _narrow(rect, keep):
    """The centred `keep` fraction of a texel rectangle (far tiles: fewer
    texels per screen pixel, so less shimmer at grazing angles)."""
    u0, v0, u1, v1 = rect
    cu, cv = 0.5 * (u0 + u1), 0.5 * (v0 + v1)
    hu, hv = 0.5 * (u1 - u0) * keep, 0.5 * (v1 - v0) * keep
    return (int(round(cu - hu)), int(round(cv - hv)),
            int(round(cu + hu)), int(round(cv + hv)))


def _quad(tile, i00, i10, i01, i11, attr, rects, keep=1.0):
    u0, v0, u1, v1 = (rects[attr[0]] if keep >= 1.0
                      else _narrow(rects[attr[0]], keep))
    # the native corner convention (wm0.map mesh 0, tris 0/1):
    # p00 -> (u1, v1), p10 -> (u0, v1), p01 -> (u1, v0), p11 -> (u0, v0)
    tile.main.append(_tri(i00, i10, i01, (u1, v1), (u0, v1), (u1, v0), attr))
    tile.main.append(_tri(i01, i10, i11, (u1, v0), (u0, v1), (u0, v0), attr))


def _skirt(tile, edge, idx, drop, attr, rects):
    """Hang a strip below the lattice verts `idx` (in order along edge)."""
    u0, v0, u1, v1 = rects[attr[0]]
    low = []
    for k in idx:
        x, y, z, _ = tile.verts[k]
        low.append(tile.add_vert(x, y - drop, z, tile.norms[k]))
    out = tile.skirts[edge]
    for s in range(len(idx) - 1):
        a, b = idx[s], idx[s + 1]
        la, lb = low[s], low[s + 1]
        uv = ((u0, v0), (u1, v0), (u0, v1), (u1, v1))
        out.append(_tri(a, b, la, uv[0], uv[1], uv[2], attr))
        out.append(_tri(la, b, lb, uv[2], uv[1], uv[3], attr))
        out.append(_tri(a, la, b, uv[0], uv[2], uv[1], attr))
        out.append(_tri(la, lb, b, uv[2], uv[3], uv[1], attr))


def _dominant_attr(surfs_rects, rects):
    """surfs_rects: [(surf, x0, z0, x1, z1)] in each surf's local coords."""
    score = {}
    best_tri = {}
    for surf, x0, z0, x1, z1 in surfs_rects:
        sel = surf.dominant(x0, z0, x1, z1)
        if sel is None:
            continue
        for k in np.nonzero(sel)[0]:
            t = int(surf.tex[k])
            ar = float(surf.area[k]) + 1e-3
            score[t] = score.get(t, 0.0) + ar
            if t not in best_tri or ar > best_tri[t][0]:
                tr = surf.tris[k]
                best_tri[t] = (ar, int(tr[3]), int(tr[11]) >> 1)
    if not score:
        return None
    t = max(score, key=lambda k: (score[k], -k))
    if t not in rects:
        return None
    return (t, best_tri[t][1], best_tri[t][2])


def _edge_drop(profile_pts, lattice_pts):
    """Largest |native - linear(lattice)| along one edge, plus a margin."""
    if not profile_pts:
        return SKIRT_MIN
    lt = np.array([p[0] for p in lattice_pts], float)
    ly = np.array([p[1] for p in lattice_pts], float)
    dev = 0.0
    for t, y in profile_pts:
        dev = max(dev, abs(y - float(np.interp(t, lt, ly))))
    return int(max(SKIRT_MIN, dev + SKIRT_MARGIN))


def build_l1(M, rects, mx, mz):
    tris, verts = mesh_at(M, mx, mz)
    surf = MeshSurface(tris, verts)
    step = MESH // (L1_N - 1)
    h = np.zeros((L1_N, L1_N))
    for j in range(L1_N):
        for i in range(L1_N):
            h[j, i] = round(surf.height(i * step, j * step))
    t = Tile()
    idx = {}
    for j in range(L1_N):
        for i in range(L1_N):
            idx[i, j] = t.add_vert(i * step, int(h[j, i]), j * step,
                                   _normal(h, i, j, step))
    attrs = {}
    for j in range(L1_N - 1):
        for i in range(L1_N - 1):
            a = _dominant_attr([(surf, i * step, j * step, (i + 1) * step,
                                 (j + 1) * step)], rects)
            if a is None:
                a = next(iter(attrs.values())) if attrs else None
            if a is None:
                raise FarError('mesh %d,%d has no textured triangle' % (mx, mz))
            attrs[i, j] = a
            _quad(t, idx[i, j], idx[i + 1, j], idx[i, j + 1],
                  idx[i + 1, j + 1], a, rects)
    n = L1_N - 1
    edges = {
        0: ([idx[i, 0] for i in range(L1_N)], 2, 0, attrs[0, 0]),      # N z=0
        1: ([idx[n, j] for j in range(L1_N)], 0, MESH, attrs[n - 1, 0]),  # E
        2: ([idx[i, n] for i in range(L1_N)], 2, MESH, attrs[0, n - 1]),  # S
        3: ([idx[0, j] for j in range(L1_N)], 0, 0, attrs[0, 0]),      # W
    }
    for e, (line, axis, val, a) in edges.items():
        prof = edge_profile(surf, axis, val, 0, MESH)
        lat = [((t.verts[k][2] if axis == 0 else t.verts[k][0]),
                t.verts[k][1]) for k in line]
        _skirt(t, e, line, _edge_drop(prof, lat), a, rects)
    t.ymin = int(h.min())
    t.ymax = int(h.max())
    return t


def build_l2(M, rects, sx, sz, l1h):
    """l1h[(mx,mz)] -> 5x5 heights of that mesh (shared lattice)."""
    # 17x17 lattice at 2048 across the sector from the L1 samples
    H = np.zeros((17, 17))
    for lz in range(4):
        for lx in range(4):
            H[lz * 4:lz * 4 + 5, lx * 4:lx * 4 + 5] = l1h[sx * 4 + lx, sz * 4 + lz]
    surfs = {}
    for lz in range(4):
        for lx in range(4):
            tr, vv = mesh_at(M, sx * 4 + lx, sz * 4 + lz)
            surfs[lx, lz] = MeshSurface(tr, vv)
    half = 2 * MESH

    def quad_attr(qx0, qz0, qx1, qz1):
        parts = []
        for lz in range(4):
            for lx in range(4):
                ox, oz = lx * MESH, lz * MESH
                x0, z0 = max(qx0, ox), max(qz0, oz)
                x1, z1 = min(qx1, ox + MESH), min(qz1, oz + MESH)
                if x0 < x1 and z0 < z1:
                    parts.append((surfs[lx, lz], x0 - ox, z0 - oz,
                                  x1 - ox, z1 - oz))
        return _dominant_attr(parts, rects)

    flat_attr = quad_attr(0, 0, 4 * MESH, 4 * MESH)
    flat = (H.max() == H.min())
    if flat:
        for lz in range(4):
            for lx in range(4):
                s = surfs[lx, lz]
                if len(s.area) and (np.unique(s.tex).size != 1
                                    or int(s.tex[0]) != flat_attr[0]):
                    flat = False
    n = L2_FLAT_N if flat else L2_N
    stride = 16 // (n - 1)
    step = 4 * MESH // (n - 1)
    h = H[::stride, ::stride]
    t = Tile()
    idx = {}
    for j in range(n):
        for i in range(n):
            idx[i, j] = t.add_vert(i * step - half, int(h[j, i]),
                                   j * step - half, _normal(h, i, j, step))
    attrs = {}
    for j in range(n - 1):
        for i in range(n - 1):
            a = (flat_attr if flat else
                 quad_attr(i * step, j * step, (i + 1) * step, (j + 1) * step))
            if a is None:
                a = flat_attr
            attrs[i, j] = a
            _quad(t, idx[i, j], idx[i + 1, j], idx[i, j + 1],
                  idx[i + 1, j + 1], a, rects, keep=L2_UV_KEEP)
    m = n - 1
    # the L1 lattice along each edge is the finer neighbour it meets
    full = np.arange(17)
    edges = {
        0: ([idx[i, 0] for i in range(n)], [(k * 2048 - half, H[0, k]) for k in full], attrs[0, 0]),
        1: ([idx[m, j] for j in range(n)], [(k * 2048 - half, H[k, 16]) for k in full], attrs[m - 1, 0]),
        2: ([idx[i, m] for i in range(n)], [(k * 2048 - half, H[16, k]) for k in full], attrs[0, m - 1]),
        3: ([idx[0, j] for j in range(n)], [(k * 2048 - half, H[k, 0]) for k in full], attrs[0, 0]),
    }
    # BUILD 587: the finer neighbour can also be the full native mesh (the
    # ring the runtime draws at native detail), so the drop covers its edge
    # vertices too, not only the 2048 lattice
    def native_edge(e):
        pts = []
        for k in range(4):
            if e == 0:
                s_, axis, val, off = surfs[k, 0], 2, 0, k * MESH
            elif e == 2:
                s_, axis, val, off = surfs[k, 3], 2, MESH, k * MESH
            elif e == 1:
                s_, axis, val, off = surfs[3, k], 0, MESH, k * MESH
            else:
                s_, axis, val, off = surfs[0, k], 0, 0, k * MESH
            if len(s_.area):
                pts += [(tt + off - half, yy)
                        for tt, yy in edge_profile(s_, axis, val, 0, MESH)]
        return pts
    for e, (line, fine, a) in edges.items():
        lat = [((t.verts[k][2] if e in (1, 3) else t.verts[k][0]),
                t.verts[k][1]) for k in line]
        prof = sorted(fine + native_edge(e))
        _skirt(t, e, line, _edge_drop(prof, lat), a, rects)
    t.ymin = int(h.min())
    t.ymax = int(h.max())
    t.flags = 1 if flat else 0
    return t, flat


def build(map_path):
    """-> (bytes of wm0.far, stats)."""
    M = read_meshes(map_path)
    rects = tex_rects(M)
    l1 = {}
    l1h = {}
    step = MESH // (L1_N - 1)
    for mz in range(MZ):
        for mx in range(MX):
            t = build_l1(M, rects, mx, mz)
            l1[mx, mz] = t
            h = np.zeros((L1_N, L1_N))
            for j in range(L1_N):
                for i in range(L1_N):
                    h[j, i] = t.verts[j * L1_N + i][1]
            l1h[mx, mz] = h
    l2 = {}
    nflat = 0
    for sz in range(SZ):
        for sx in range(SX):
            t, flat = build_l2(M, rects, sx, sz, l1h)
            l2[sx, sz] = t
            nflat += flat
    # BUILD 591: each variant sector as the game shows it after the story
    # moves on -- its own 16 L1 tiles and its L2 tile, built on a copy of the
    # map with that one sector replaced (neighbours and seams as they are
    # then). A map without the replacement sectors repeats the base ones.
    alt1, alt2 = [], []
    for a, (bs, alt, _p) in enumerate(VARIANTS):
        sx, sz = bs % SX, bs // SX
        if len(M) > alt:
            Ma = list(M)
            Ma[bs] = M[alt]
            ha = dict(l1h)
            tl = []
            for k in range(16):
                mx, mz = sx * 4 + (k & 3), sz * 4 + (k >> 2)
                t = build_l1(Ma, rects, mx, mz)
                h = np.zeros((L1_N, L1_N))
                for j in range(L1_N):
                    for i in range(L1_N):
                        h[j, i] = t.verts[j * L1_N + i][1]
                ha[mx, mz] = h
                tl.append(t)
            t2, _flat = build_l2(Ma, rects, sx, sz, ha)
        else:
            tl = [l1[sx * 4 + (k & 3), sz * 4 + (k >> 2)] for k in range(16)]
            t2 = l2[sx, sz]
        alt1 += tl
        alt2.append(t2)
    tiles = [l1[mx, mz] for mz in range(MZ) for mx in range(MX)] + alt1
    tiles += [l2[sx, sz] for sz in range(SZ) for sx in range(SX)] + alt2
    return pack(tiles, mesh_blocks(map_path)), {'l1': len(l1), 'l2': len(l2), 'l2_flat': nflat,
                         'variants': NALT if len(M) > SX * SZ else 0,
                         'l1_tris': sum(len(t.main) for t in l1.values()),
                         'l2_tris': sum(len(t.main) for t in l2.values())}


def pack(tiles, blocks):
    """Header, tile index, mesh refs, then per tile: tris (main + 4 skirts),
    verts (s16 x3), norms (s8 x3)."""
    n = len(tiles)
    refs_at = HDR + n * ENTRY
    data_at = refs_at + len(blocks) * MESHREF
    body = bytearray()
    index = bytearray()
    for t in tiles:
        if len(t.verts) > MAX_VERTS:
            raise FarError('tile has %d vertices (> %d)'
                           % (len(t.verts), MAX_VERTS))
        at = data_at + len(body)
        tri = b''.join(t.main)
        sk_first, sk_n = [], []
        count = len(t.main)
        for e in range(4):
            sk_first.append(count)
            sk_n.append(len(t.skirts[e]))
            tri += b''.join(t.skirts[e])
            count += len(t.skirts[e])
        if count > 0xFFFF:
            raise FarError('too many triangles in a tile')
        body += tri
        vat = data_at + len(body)
        for v in t.verts:
            body += struct.pack('<3h', *v[:3])
        nat = data_at + len(body)
        for v in t.norms:
            q = [max(-127, min(127, int(round(c * 127.0 / 4096.0))))
                 for c in v[:3]]
            body += struct.pack('<3b', *q)
        body += b'\0' * (-len(body) % 4)
        index += struct.pack(ENTRY_FMT, at, vat, nat, len(t.main),
                             *sk_first, *sk_n, len(t.verts), t.flags,
                             max(-32768, t.ymin), min(32767, t.ymax))
    refs = []
    for blk in blocks:
        refs.append((data_at + len(body), len(blk)))
        body += blk
        body += b'\0' * (-len(body) % 4)
    ref = b''.join(struct.pack(MESHREF_FMT, o, ln) for o, ln in refs)
    hdr = struct.pack('<4sIIIIIII', MAGIC, VERSION, N1, N2,
                      data_at, data_at + len(body), ENTRY, refs_at)
    return hdr + bytes(index) + ref + bytes(body)
