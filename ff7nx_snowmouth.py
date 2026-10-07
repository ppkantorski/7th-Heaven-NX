"""
BUILD 617 -- a mouth for Cloud's snowboard model.

The snowboard Cloud (object 0 of `for_ev.tmd` / `for_gs.tmd`: a 75-vertex,
140-triangle Gouraud head) has eyes -- object 14, five raw-textured
triangles over `eyes.tex` -- and no mouth. This adds one, drawn the same way
the eyes are:

GEOMETRY. Six new vertices on object 14, a 6 x 3 unit patch centred under
the eyes at x -3/0/+3, y -10/-7 (the eyes span y -25..-12, the chin is at
y -3). Each vertex sits on the head's own surface -- the frontmost object-0
triangle under it, sampled in the file -- moved toward the camera to the
nearest whole unit at least 0.5 in front of the skin (about 1 unit; 617a's
2-unit lift read as a mouth floating before his face on hardware), and
interior points are checked against the head the same way. The centre column follows the face's centre ridge
(vertex 8 -> vertex 10), so the patch folds with the face instead of
cutting through it. Four triangles, the same primitive the eyes use
(mode 0x25, raw texture, no light, eyes.tex's own CLUT and texture page,
copied from object 14's first triangle).

TEXTURE. The patch maps to the free bottom-right corner of eyes.tex (the
eye art ends at row 47 of 64): region u 31..55, v 166..175 -- entirely
outside the eye triangles' UV footprint (u 3..45, above the line from
(44,163) to (8,171)), so no mouth texel can show inside an eye and no eye
texel inside the mouth -- which is eyes.tex rows ~51..63, columns ~35..63
after BUILD 548's 56x48 -> 64x64 stretch
(ff7nx_minigametex / ff7nx_ddstex.stretch_to_region). A mouth is drawn there
with two new entries in the texture's 16-colour palette (8 are free): a
short warm dark line about 4 units wide and 0.6 of a unit thick,
at y -8.3, a softer
shade under it and at the slightly turned-down corners.
Everything else in the patch stays index 0, which is colour-keyed, so only
the mouth itself shows. The rows it uses must be empty first (checked).

HOW THE FILE CHANGES. Object 14's vertex and primitive blocks are copied,
extended, and appended at the end of the file; only object 14's table entry
is repointed. No other byte moves. Each file must be the stock head and
either the stock or the BUILD 547 face first; anything else passes through.
SEVENTH_NX_SNOW_MOUTH=0 turns it off.
"""
from __future__ import annotations

import os
import math
import struct

import tex as TX

ENV = 'SEVENTH_NX_SNOW_MOUTH'
FACE = 14
HEAD = 0
TMD_NAMES = ('for_ev.tmd', 'for_gs.tmd')
TEX_NAME = 'eyes.tex'

# head vertices the placement depends on (object 0), checked before use
HEAD_KEYS = {8: (0, -12, -15), 0: (-7, -7, -10), 3: (8, -7, -10),
             10: (0, -3, -9), 1: (-9, -12, -9), 2: (9, -12, -9)}
FACE_NV = 7
FACE_NPRI = 5
# The stock face maps its two eyes differently: the left eye is two
# triangles (1,3,2)/(1,2,0), the right eye three around an extra vertex 4 at
# (5,-23), and the right side's UVs are one texel off the left's (u 3 vs 4,
# v 170 vs 171, 132 vs 131). With the texture stretched 64/56 x 64/48 that
# is visible: the right eye comes out a pinch smaller and lower. The left
# eye's two triangles are MIRRORED onto the right (vertex 1->6, 0->5; 2 and
# 3 are on the centre line) with the same UVs -- the eye art is one eye,
# mirrored by the geometry, exactly as the stock mapping intends -- and
# vertex 4 is left unused. Same texture, same vertices, same placement.
LEFT_EYE = {1, 3, 2, 0}
MIRROR = {1: 6, 0: 5, 2: 2, 3: 3}
PATCHED_NPRI = 4 + 4
GRID_X = (-3, 0, 3)
GRID_Y = (-10, -7)
LIFT = 2                  # (617a; 617b places each vertex by MIN_CLEAR)
MIN_CLEAR = 0.5
# region UVs of the patch (the eyes use u 3..45, v 128..171 of the same
# 56 x 48 region)
U0, U1 = 31, 55
V0, V1 = 166, 175
REGION_W, REGION_H, REGION_V = 56, 48, 128

# the mouth in eyes.tex pixels (64 x 64): (row, first col, last col, ink).
# Two new palette entries, warm like the skin it sits on: a dark line and a
# softer shade for the underside and the slightly turned-down corners.
INKS = {'line': (86, 48, 44), 'soft': (128, 82, 73)}
# columns: right of the eye triangles' UV footprint. About 4 units wide and
# 0.6 thick on the face, centred at y -8.3 -- midway between the bottom of
# the eyes (y ~-14.5 on screen) and the chin (y -3).
MOUTH = (
    (57, 41, 57, 'line'),
    (58, 42, 56, 'line'),
    (57, 40, 40, 'soft'), (57, 58, 58, 'soft'),
    (58, 40, 41, 'soft'), (58, 57, 58, 'soft'),
    (59, 44, 54, 'soft'),
)
CLEAR_ROWS = (50, 64)     # tex rows that must be empty before drawing
CLEAR_COLS = (34, 64)


def enabled():
    return os.environ.get(ENV, '1').strip().lower() not in ('0', 'off', 'no',
                                                            'false')


# --------------------------------------------------------------- the model
def _table(data, o):
    return struct.unpack_from('<IIIIIIi', data, 12 + 28 * o)


def _verts(data, vt, n):
    return [struct.unpack_from('<hhh', data, 12 + vt + 8 * i)
            for i in range(n)]


def _prims(data, pt, n):
    out, p = [], 12 + pt
    for _ in range(n):
        olen, ilen, flg, mode = struct.unpack_from('<BBBB', data, p)
        out.append(data[p:p + 4 + ilen * 4])
        p += 4 + ilen * 4
    return out


def _layout_ok(data):
    ident, flags, nobj = struct.unpack_from('<III', data, 0)
    if ident != 0x41 or flags != 0 or nobj <= FACE:
        return False
    vt, nv, *_r = _table(data, HEAD)
    head = _verts(data, vt, nv)
    return all(i < nv and head[i] == v for i, v in HEAD_KEYS.items())


def state(name, data):
    """'stock' (7-vertex face) | 'patched' | 'unknown'"""
    if name.lower() not in TMD_NAMES:
        return 'unknown'
    try:
        if not _layout_ok(data):
            return 'unknown'
        _vt, nv, _nt, _nn, pt, npri, _sc = _table(data, FACE)
    except struct.error:
        return 'unknown'
    if (nv, npri) == (FACE_NV, FACE_NPRI):
        return 'stock'
    if (nv, npri) == (FACE_NV + 6, PATCHED_NPRI):
        return 'patched'
    return 'unknown'


def _surface(data):
    """z of the frontmost object-0 triangle under (x, y), or None."""
    vt, nv, _nt, _nn, pt, npri, _sc = _table(data, HEAD)
    V = _verts(data, vt, nv)
    tris = []
    for raw in _prims(data, pt, npri):
        mode, ilen = raw[3], raw[1]
        if mode in (0x30, 0x31) and ilen == 5:
            tris.append(struct.unpack_from('<HHH', raw, 16))

    def z_at(x, y):
        best = None
        for vi in tris:
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = [V[i] for i in vi]
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if not den:
                continue
            l1 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / den
            l2 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / den
            l3 = 1 - l1 - l2
            if min(l1, l2, l3) < -1e-6:
                continue
            z = l1 * az + l2 * bz + l3 * cz
            if best is None or z < best:
                best = z
        return best
    return z_at


def mouth_geometry(data):
    """The six vertices and the four (vertex, uv) triangles, or raise."""
    z_at = _surface(data)
    verts = []
    for y in GRID_Y:
        for x in GRID_X:
            zs = z_at(x, y)
            if zs is None:
                raise ValueError('no head surface under (%d, %d)' % (x, y))
            # as close to the skin as whole units allow (617b: two units
            # in front read as a mouth floating before his face): the
            # nearest integer at least MIN_CLEAR in front of the head
            z = int(math.floor(zs - MIN_CLEAR))
            verts.append((x, y, z))
    # interior clearance: sample each triangle against the head
    def uv(x, y):
        return (int(round(U0 + (x - GRID_X[0]) * (U1 - U0)
                          / (GRID_X[-1] - GRID_X[0]))),
                int(round(V0 + (y - GRID_Y[0]) * (V1 - V0)
                          / (GRID_Y[-1] - GRID_Y[0]))))
    # 0 1 2 / 3 4 5
    tris = ((0, 3, 1), (1, 3, 4), (1, 4, 2), (2, 4, 5))
    for a, b, c in tris:
        for s in range(11):
            for t in range(11 - s):
                w = (s / 10.0, t / 10.0, 1 - s / 10.0 - t / 10.0)
                x = sum(wi * verts[k][0] for wi, k in zip(w, (a, b, c)))
                y = sum(wi * verts[k][1] for wi, k in zip(w, (a, b, c)))
                z = sum(wi * verts[k][2] for wi, k in zip(w, (a, b, c)))
                zs = z_at(x, y)
                if zs is not None and zs - z < MIN_CLEAR - 1e-6:
                    raise ValueError('mouth patch cuts into the head at '
                                     '(%.1f, %.1f)' % (x, y))
    return verts, [((a, b, c), (uv(*verts[a][:2]), uv(*verts[b][:2]),
                                uv(*verts[c][:2]))) for a, b, c in tris]


def patch_tmd(name, data, revert=False):
    """Return (new bytes, note). Unknown input comes back unchanged."""
    st = state(name, data)
    if st == 'unknown':
        return data, '%s: head/face not the stock layout -- no mouth' % name
    if revert:
        return data, '%s: revert is by rebuilding from source' % name
    if st == 'patched':
        return data, '%s: mouth already present' % name
    vt, nv, nt, nn, pt, npri, sc = _table(data, FACE)
    face_v = _verts(data, vt, nv)
    face_p = _prims(data, pt, npri)
    tmpl = face_p[0]
    if tmpl[3] != 0x25 or tmpl[1] != 6:
        return data, '%s: face primitive not the expected type' % name
    verts, tris = mouth_geometry(data)
    left = [raw for raw in face_p
            if set(struct.unpack_from('<HHH', raw, 20)) <= LEFT_EYE]
    if len(left) != 2:
        return data, '%s: left-eye triangles not found -- no mouth' % name
    face_p = list(left)
    for raw in left:
        vi = struct.unpack_from('<HHH', raw, 20)
        uv = [(raw[4], raw[5]), (raw[8], raw[9]), (raw[12], raw[13])]
        m = [MIRROR[v] for v in vi]
        # mirroring reverses the winding; swap two corners to keep it
        order = (0, 2, 1)
        p = bytearray(raw)
        for slot, k in enumerate(order):
            p[4 + 4 * slot], p[5 + 4 * slot] = uv[k]
        struct.pack_into('<HHH', p, 20, *[m[k] for k in order])
        face_p.append(bytes(p))
    for raw in face_p:
        a, b, c = [face_v[i] for i in struct.unpack_from('<HHH', raw, 20)]
        area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if area == 0:
            return data, '%s: degenerate face triangle -- no mouth' % name
    signs = {((face_v[struct.unpack_from('<HHH', r, 20)[1]][0]
               - face_v[struct.unpack_from('<HHH', r, 20)[0]][0])
              * (face_v[struct.unpack_from('<HHH', r, 20)[2]][1]
                 - face_v[struct.unpack_from('<HHH', r, 20)[0]][1])
              - (face_v[struct.unpack_from('<HHH', r, 20)[1]][1]
                 - face_v[struct.unpack_from('<HHH', r, 20)[0]][1])
              * (face_v[struct.unpack_from('<HHH', r, 20)[2]][0]
                 - face_v[struct.unpack_from('<HHH', r, 20)[0]][0])) > 0
             for r in face_p}
    if len(signs) != 1:
        return data, '%s: face winding would be mixed -- no mouth' % name
    face_ccw = signs.pop()
    # The mouth must wind the way the eyes do or the port culls it as a
    # back face -- which is exactly what the first hardware test showed
    # (patched files, no mouth). Orient every mouth triangle to match.
    allv = face_v + verts
    fixed = []
    for (a, b, c), uvs in tris:
        A_, B_, C_ = allv[nv + a], allv[nv + b], allv[nv + c]
        ccw = ((B_[0] - A_[0]) * (C_[1] - A_[1])
               - (B_[1] - A_[1]) * (C_[0] - A_[0])) > 0
        if ccw != face_ccw:
            (a, b, c), uvs = (a, c, b), (uvs[0], uvs[2], uvs[1])
        fixed.append(((a, b, c), uvs))
    tris = fixed
    out = bytearray(data)
    while len(out) % 4:
        out.append(0)
    new_vt = len(out) - 12
    for v in face_v + verts:
        out += struct.pack('<hhhh', v[0], v[1], v[2], 0)
    new_pt = len(out) - 12
    for raw in face_p:
        out += raw
    for (a, b, c), ((u0, v0), (u1, v1), (u2, v2)) in tris:
        p = bytearray(tmpl)
        p[4], p[5] = u0, v0
        p[8], p[9] = u1, v1
        p[12], p[13] = u2, v2
        struct.pack_into('<HHH', p, 20, nv + a, nv + b, nv + c)
        out += p
    struct.pack_into('<IIIIIIi', out, 12 + 28 * FACE, new_vt, nv + 6, nt,
                     nn, new_pt, len(face_p) + 4, sc)
    if state(name, bytes(out)) != 'patched':
        raise ValueError('%s: mouth patch did not verify' % name)
    return bytes(out), ('%s: mouth added -- 6 vertices on the face at z %s, '
                        '4 triangles over eyes.tex; right eye mapped as the '
                        'mirror of the left' % (
                            name, '/'.join(str(v[2]) for v in verts)))


# ------------------------------------------------------------- the texture
def patch_tex(data):
    """Return (new bytes, note). Anything unexpected comes back unchanged."""
    t = TX.parse(data)
    if (t is None or t['width'] != 64 or t['height'] != 64
            or not t['palette_flag'] or t['bytes_per_pixel'] != 1
            or t['colors_per_palette'] != 16 or t['num_palettes'] != 1):
        return data, 'eyes.tex: not the 64x64 16-colour face texture -- no mouth'
    pix_off = len(data) - 64 * 64
    pal_off = pix_off - 16 * 4
    px = bytearray(data[pix_off:])
    pal = bytearray(data[pal_off:pix_off])
    if bytes(pal) != bytes(t['palette'])[:64] or \
            bytes(px) != bytes(t['pixels'])[:4096]:
        return data, 'eyes.tex: unexpected layout -- no mouth'
    want = {}
    for row, a, b, ink in MOUTH:
        for c in range(a, b + 1):
            want[row * 64 + c] = ink
    r0, r1 = CLEAR_ROWS
    c0, c1 = CLEAR_COLS
    region = [px[r * 64 + c] for r in range(r0, r1) for c in range(c0, c1)]
    slot = {k: _find_rgb(pal, rgb) for k, rgb in INKS.items()}
    if any(region):
        if all(v is not None for v in slot.values()) and all(
                px[k] == slot[v] for k, v in want.items()):
            return data, 'eyes.tex: mouth already drawn'
        return data, 'eyes.tex: the mouth corner is not empty -- no mouth'
    used = set(px)
    free = [i for i in range(1, 16) if i not in used
            and pal[4 * i:4 * i + 4] == b'\0\0\0\0']
    for k, rgb in INKS.items():
        if slot[k] is None:
            if not free:
                return data, 'eyes.tex: no free palette entry -- no mouth'
            slot[k] = free.pop(0)
            r, g, b = rgb
            pal[4 * slot[k]:4 * slot[k] + 4] = bytes((b, g, r, 255))  # BGRA
    for k, v in want.items():
        px[k] = slot[v]
    out = bytearray(data)
    out[pal_off:pix_off] = pal
    out[pix_off:] = px
    if TX.parse(bytes(out)) is None:
        return data, 'eyes.tex: patched texture does not parse -- no mouth'
    return bytes(out), ('eyes.tex: mouth drawn (%d texels, palette %s)'
                        % (len(want), '/'.join(str(slot[k]) for k in INKS)))


def _find_rgb(pal, rgb):
    r, g, b = rgb
    for i in range(16):
        if tuple(pal[4 * i:4 * i + 4]) == (b, g, r, 255):
            return i
    return None
