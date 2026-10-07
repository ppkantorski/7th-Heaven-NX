"""
BUILD 618 -- Tifa's snowboard face: no more blinking, and a mouth.

WHICH OBJECTS (for_gs.tmd only; for_ev.tmd has no Tifa):
  * head 249 (84 vertices, 156 Gouraud triangles) and its face, object 264:
    7 vertices, 6 raw-textured triangles (mode 0x25) over the LEFT half of
    tifaeye.tex (u 8..52, v 1..63 -- the brown eye, mirrored by geometry);
  * head 265 and its face, object 279 (10 vertices, 9 triangles over the
    RIGHT half of the same texture, the blue eye) -- the other face that
    shares tifaeye.tex.

WHY SHE BLINKS. Exactly Cloud's BUILD 547 finding: every vertex of each face
IS a vertex of its head (264's 7 on head 249, 279's 10 on head 265), so the
decal is flush with the skin. The PlayStation drew it after the head; this
port depth-tests it, the two tie, and as she moves the tie flips -- the eyes
flash on and off. FIX: each face vertex moves LIFT units toward the camera
(z - 2), as Cloud's did (hardware-proven since BUILD 547). Same x/y, same
UVs, same triangles: the eyes look exactly as they do now, they just stop
flickering. (User: "tifa's eyes look fine, it's just her blinking.")

THE MOUTH (object 264 only), drawn the way Cloud's is (ff7nx_snowmouth):
six new vertices in a 6 x 3 unit patch at x -3/0/+3, y -9/-6 -- below the
eyes (their lowest vertex is y -11) and above the chin (at x +-3 her face
ends between y -6 and -5) -- each on head 249's own surface, at the nearest
whole unit at least MIN_CLEAR in front of it; four triangles wound like the
face's, same primitive and CLUT/tpage as the face. They map to a free block
of tifaeye.tex, u 53..68 x v 44..53: right of face 264's UV footprint
(u <= 52) and left of face 279's below row 32 (u >= 69), checked empty. The
mouth itself is two new 16-colour palette entries (9 are free): a short
dark warm line and a softer shade under it; everything else stays index 0
(colour-keyed).

HOW THE FILES CHANGE. The face vertex z values change in place; object
264's vertex and primitive blocks are copied, extended and appended at the
end of the file and only its table entry is repointed. Every input must be
the stock layout (or the already-patched one, which passes through);
anything else is left alone. SEVENTH_NX_SNOW_TIFA=0 turns it all off;
SEVENTH_NX_SNOW_TIFA_MOUTH=0 keeps the anti-blink lift but no mouth.
"""
from __future__ import annotations

import math
import os
import struct

import tex as TX

ENV = 'SEVENTH_NX_SNOW_TIFA'
MOUTH_ENV = 'SEVENTH_NX_SNOW_TIFA_MOUTH'
TMD_NAME = 'for_gs.tmd'
TEX_NAME = 'tifaeye.tex'
NOBJ = 284

LIFT = 2
MIN_CLEAR = 0.5
# face object -> (head object, stock vertices)
FACES = {
    264: (249, ((9, -13, -8), (0, -14, -13), (9, -25, -9), (-9, -25, -9),
                (0, -23, -13), (0, -11, -13), (-9, -13, -8))),
    279: (265, ((0, -13, -15), (9, -10, -9), (0, -7, -15), (11, -16, -10),
                (6, -16, -13), (2, -20, -15), (-2, -20, -15), (-6, -16, -13),
                (-11, -16, -10), (-9, -10, -9))),
}
FACE_NPRI = {264: 6, 279: 9}
MOUTH_FACE = 264
GRID_X = (-3, 0, 3)
GRID_Y = (-9, -6)
U0, U1 = 53, 68
V0, V1 = 44, 53
TEX_W, TEX_H = 128, 64
CLEAR = (33, 64, 53, 69)            # rows r0..r1, cols c0..c1 that must be 0
INKS = {'line': (104, 54, 50), 'soft': (150, 98, 88)}
# (row, first col, last col, ink): about 3.5 units wide, at y ~-7.5
MOUTH = (
    (48, 57, 64, 'line'),
    (48, 56, 56, 'soft'), (48, 65, 65, 'soft'),
    (49, 58, 63, 'soft'),
)


def enabled():
    return os.environ.get(ENV, '1').strip().lower() not in (
        '0', 'off', 'no', 'false')


def mouth_enabled():
    return enabled() and os.environ.get(MOUTH_ENV, '1').strip().lower() \
        not in ('0', 'off', 'no', 'false')


def _table(d, o):
    return struct.unpack_from('<IIIIIIi', d, 12 + 28 * o)


def _verts(d, vt, n):
    return [struct.unpack_from('<hhh', d, 12 + vt + 8 * i) for i in range(n)]


def _prims(d, pt, n):
    out, p = [], 12 + pt
    for _ in range(n):
        _olen, ilen, _flg, _mode = struct.unpack_from('<BBBB', d, p)
        out.append(d[p:p + 4 + ilen * 4])
        p += 4 + ilen * 4
    return out


def _header_ok(d):
    ident, flags, nobj = struct.unpack_from('<III', d, 0)
    return ident == 0x41 and flags == 0 and nobj == NOBJ


def face_state(d, f):
    """'stock' | 'lifted' | 'mouth' (lifted + mouth) | 'unknown'"""
    head, stock = FACES[f]
    vt, nv, _nt, _nn, _pt, npri, _sc = _table(d, f)
    V = _verts(d, vt, nv)
    hv = set(_verts(d, *_table(d, head)[:2]))
    n = len(stock)
    if nv < n:
        return 'unknown'
    if tuple(V[:n]) == stock and npri == FACE_NPRI[f] and nv == n:
        return 'stock' if all(v in hv for v in stock) else 'unknown'
    lifted = tuple((x, y, z - LIFT) for x, y, z in stock)
    if tuple(V[:n]) != lifted:
        return 'unknown'
    if nv == n and npri == FACE_NPRI[f]:
        return 'lifted'
    if f == MOUTH_FACE and nv == n + 6 and npri == FACE_NPRI[f] + 4:
        return 'mouth'
    return 'unknown'


def _surface(d, head):
    vt, nv, _nt, _nn, pt, npri, _sc = _table(d, head)
    V = _verts(d, vt, nv)
    tris = [struct.unpack_from('<HHH', r, 16) for r in _prims(d, pt, npri)
            if r[3] in (0x30, 0x31) and r[1] == 5]

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


def _uv(x, y):
    return (int(round(U0 + (x - GRID_X[0]) * (U1 - U0)
                      / (GRID_X[-1] - GRID_X[0]))),
            int(round(V0 + (y - GRID_Y[0]) * (V1 - V0)
                      / (GRID_Y[-1] - GRID_Y[0]))))


def mouth_geometry(d):
    head = FACES[MOUTH_FACE][0]
    z_at = _surface(d, head)
    verts = []
    for y in GRID_Y:
        for x in GRID_X:
            zs = z_at(x, y)
            if zs is None:
                raise ValueError('no head surface under (%d, %d)' % (x, y))
            verts.append((x, y, int(math.floor(zs - MIN_CLEAR))))
    tris = ((0, 3, 1), (1, 3, 4), (1, 4, 2), (2, 4, 5))
    for a, b, c in tris:
        for s in range(11):
            for t in range(11 - s):
                w = (s / 10.0, t / 10.0, 1 - s / 10.0 - t / 10.0)
                x, y, z = (sum(wi * verts[k][j] for wi, k in zip(w, (a, b, c)))
                           for j in range(3))
                zs = z_at(x, y)
                if zs is not None and zs - z < MIN_CLEAR - 1e-6:
                    raise ValueError('mouth cuts into the head at (%.1f, %.1f)'
                                     % (x, y))
    return verts, tris


def patch_tmd(d, mouth=True):
    """Return (new bytes, note). Unknown input comes back unchanged."""
    if not _header_ok(d):
        return d, '%s: not the stock 284-object layout -- Tifa untouched' \
            % TMD_NAME
    try:
        states = {f: face_state(d, f) for f in FACES}
    except struct.error:
        return d, '%s: unreadable -- Tifa untouched' % TMD_NAME
    if 'unknown' in states.values():
        return d, '%s: face objects not stock (%s) -- Tifa untouched' % (
            TMD_NAME, ', '.join('%d %s' % kv for kv in sorted(states.items())))
    out = bytearray(d)
    notes = []
    for f, (head, stock) in FACES.items():
        if states[f] != 'stock':
            continue
        vt = _table(d, f)[0]
        for i, (x, y, z) in enumerate(stock):
            struct.pack_into('<hhh', out, 12 + vt + 8 * i, x, y, z - LIFT)
        notes.append('face %d lifted %d off head %d' % (f, LIFT, head))
    if mouth and states[MOUTH_FACE] != 'mouth':
        d2 = bytes(out)
        verts, tris = mouth_geometry(d2)
        vt, nv, nt, nn, pt, npri, sc = _table(d2, MOUTH_FACE)
        face_v = _verts(d2, vt, nv)
        face_p = _prims(d2, pt, npri)
        tmpl = face_p[0]
        if tmpl[3] != 0x25 or tmpl[1] != 6:
            raise ValueError('face primitive not mode 0x25')

        def ccw(a, b, c):
            return ((b[0] - a[0]) * (c[1] - a[1])
                    - (b[1] - a[1]) * (c[0] - a[0])) > 0
        signs = {ccw(*[face_v[i] for i in struct.unpack_from('<HHH', r, 20)])
                 for r in face_p}
        if len(signs) != 1:
            raise ValueError('face winding is mixed')
        face_ccw = signs.pop()
        while len(out) % 4:
            out.append(0)
        new_vt = len(out) - 12
        for v in face_v + verts:
            out += struct.pack('<hhhh', v[0], v[1], v[2], 0)
        new_pt = len(out) - 12
        for r in face_p:
            out += r
        for a, b, c in tris:
            if ccw(verts[a], verts[b], verts[c]) != face_ccw:
                b, c = c, b
            p = bytearray(tmpl)
            for slot, k in enumerate((a, b, c)):
                p[4 + 4 * slot], p[5 + 4 * slot] = _uv(*verts[k][:2])
            struct.pack_into('<HHH', p, 20, nv + a, nv + b, nv + c)
            out += p
        struct.pack_into('<IIIIIIi', out, 12 + 28 * MOUTH_FACE, new_vt,
                         nv + 6, nt, nn, new_pt, npri + 4, sc)
        if face_state(bytes(out), MOUTH_FACE) != 'mouth':
            raise ValueError('mouth patch did not verify')
        notes.append('mouth on face %d at z %s' % (
            MOUTH_FACE, '/'.join(str(v[2]) for v in verts)))
    if not notes:
        return d, '%s: Tifa already patched' % TMD_NAME
    return bytes(out), '%s: %s' % (TMD_NAME, '; '.join(notes))


def patch_tex(data):
    t = TX.parse(data)
    if (t is None or t['width'] != TEX_W or t['height'] != TEX_H
            or not t['palette_flag'] or t['bytes_per_pixel'] != 1
            or t['colors_per_palette'] != 16 or t['num_palettes'] != 1):
        return data, '%s: not the 128x64 16-colour texture -- no mouth' \
            % TEX_NAME
    pix_off = len(data) - TEX_W * TEX_H
    pal_off = pix_off - 16 * 4
    px = bytearray(data[pix_off:])
    pal = bytearray(data[pal_off:pix_off])
    if bytes(pal) != bytes(t['palette'])[:64] or \
            bytes(px) != bytes(t['pixels'])[:TEX_W * TEX_H]:
        return data, '%s: unexpected layout -- no mouth' % TEX_NAME
    want = {}
    for row, a, b, ink in MOUTH:
        for c in range(a, b + 1):
            want[row * TEX_W + c] = ink
    r0, r1, c0, c1 = CLEAR
    region = [px[r * TEX_W + c] for r in range(r0, r1) for c in range(c0, c1)]

    def find(rgb):
        r, g, b = rgb
        for i in range(16):
            if tuple(pal[4 * i:4 * i + 4]) == (b, g, r, 255):
                return i
        return None
    slot = {k: find(rgb) for k, rgb in INKS.items()}
    if any(region):
        if all(v is not None for v in slot.values()) and all(
                px[k] == slot[v] for k, v in want.items()):
            return data, '%s: mouth already drawn' % TEX_NAME
        return data, '%s: mouth block not empty -- no mouth' % TEX_NAME
    used = set(px)
    free = [i for i in range(1, 16) if i not in used
            and pal[4 * i:4 * i + 4] == b'\0\0\0\0']
    for k, (r, g, b) in INKS.items():
        if slot[k] is None:
            if not free:
                return data, '%s: no free palette entry -- no mouth' % TEX_NAME
            slot[k] = free.pop(0)
            pal[4 * slot[k]:4 * slot[k] + 4] = bytes((b, g, r, 255))  # BGRA
    for k, v in want.items():
        px[k] = slot[v]
    out = bytearray(data)
    out[pal_off:pix_off] = pal
    out[pix_off:] = px
    if TX.parse(bytes(out)) is None:
        return data, '%s: patched texture does not parse -- no mouth' \
            % TEX_NAME
    return bytes(out), '%s: mouth drawn (%d texels)' % (TEX_NAME, len(want))
