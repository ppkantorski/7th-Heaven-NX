#!/usr/bin/env python3
"""ff7nx_catshades.py -- "deal with it" sunglasses on the Tsuna's-domain cat.
BUILD 620e.

The user (10-06): "i have this 3d model for the deal with it sunglasses. i
would like for you to put them on the cat so that it covers its eyes, and
size it to fit on the cat's head properly ... it would only apply here."

WHAT. The user's FBX (Sketchfab "deal with it" glasses: a black pixel-art
frame with 3.5 x 5.5 cm "holes" showing a white box behind it -- 388 black
triangles, 12 white quads) is stored as plain geometry in
assets/catshades/dealwithit_glasses.npz (the user's own model, converted).
At build time a COPY of utmin1's cat is added to char.lgp:
    NKDA.HRC  = BDGA.HRC with the head bone (atama) pointing at NKDB.RSD
    NKDB.RSD  = BDGF.RSD (the head) with PLY/MAT/GRP -> NKDC
    NKDC.P    = BDHA.P (the head mesh) + the glasses, in the same group and
                render state (untextured, vertex colours: the cat has no
                texture at all), every glasses triangle in both windings
and only the easter-egg cat's model record uses NKDA (ff7nx_easteregg);
utmin1's cats are untouched. The glasses ride the head bone, so they move
with every animation; the skeleton is unchanged, so BDGA's animations play.

FIT (head-bone local space, measured on the head mesh and FAAC frame 0):
  * the eyes are the head's yellow vertices (194,194,29): x +-0.31..0.72,
    centred on the face; the glasses are centred on them, their back plane
    GAP in front of the frontmost eye vertex;
  * axes from the bone's frame-0 pose: right = +X, up = model -Y, forward =
    model -Z (both expressed in the head's local frame), so the glasses sit
    level and face where the cat looks;
  * width WIDTH (head is 2.27 wide, eyes 1.44): scale WIDTH / 139.4.
If anything is not as expected the copy is written WITHOUT glasses (an exact
copy of the cat), so the field never references a missing file.

SEVENTH_NX_NO_CAT_SHADES=1 disables (and the cat uses BDGA again).
"""
from __future__ import annotations

import os
import re
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_CAT_SHADES'
HERE = os.path.dirname(os.path.abspath(__file__))
ASSET = os.path.join(HERE, 'assets', 'catshades', 'dealwithit_glasses.npz')
SRC_HRC, SRC_RSD, SRC_P = 'bdga', 'bdgf', 'bdha'
NEW_HRC, NEW_RSD, NEW_P = 'nkda', 'nkdb', 'nkdc'
HEAD_BONE = 'atama'
POSE_ANIM = 'faac'
EYE_RGB = (194, 194, 29)
WIDTH = 2.3             # glasses width in head units (head 2.27, eyes 1.44): covers the eyes from the sides too
GAP = 0.0               # panel back on the eyes' front (no parallax peek from above/below)
LIFT = 0.04             # 620f: up 0.10 (user: "a little higher up, not as close to his nose"); bottom edge still 0.015 below the eyes
BLACK = (28, 28, 30)
WHITE = (235, 235, 235)
HDR = ['version', 'off04', 'vertex_type', 'numverts', 'numnormals',
       'num_unk1', 'numtex', 'numvcol', 'numedges', 'numpolys', 'num_unk2',
       'num_unk3', 'numhundreds', 'numgroups', 'numbbox', 'normidx']


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def available():
    return not disabled() and os.path.exists(ASSET)


# Set by apply_to_char once char.lgp is known to hold NKDA (glasses or the
# plain-copy fallback). ff7nx_easteregg names NKDA only when this is True,
# and the char pass runs before flevel, so the reference can't dangle.
READY = False


def wanted():
    """The glasses only exist for the easter cat."""
    try:
        import ff7nx_easteregg
        if ff7nx_easteregg.disabled():
            return False
    except Exception:                                          # noqa: BLE001
        return False
    return available()


# ------------------------------------------------------------------ .p
def parse_p(d):
    h = dict(zip(HDR, struct.unpack_from('<16I', d, 0)))
    if h['version'] != 1:
        raise ValueError('not a version-1 .p')
    o = 128
    S = {}
    for name, n, sz in (('verts', h['numverts'], 12),
                        ('normals', h['numnormals'], 12),
                        ('unk1', h['num_unk1'], 12),
                        ('tex', h['numtex'], 8), ('vcol', h['numvcol'], 4),
                        ('pcol', h['numpolys'], 4),
                        ('edges', h['numedges'], 4),
                        ('polys', h['numpolys'], 24),
                        ('unk2', h['num_unk2'], 24),
                        ('unk3', h['num_unk3'], 3),
                        ('hundreds', h['numhundreds'], 100),
                        ('groups', h['numgroups'], 56),
                        ('bbox', h['numbbox'], 28)):
        S[name] = d[o:o + n * sz]
        o += n * sz
    if h['normidx']:
        S['normidx'] = d[o:o + h['numverts'] * 4]
        o += h['numverts'] * 4
    if o != len(d):
        raise ValueError('.p is %d bytes, walked %d' % (len(d), o))
    return h, S, d[:128]


def _argb(rgb):
    r, g, b = rgb
    return 0xFF000000 | (r << 16) | (g << 8) | b


def add_mesh(pdata, tris_xyz, tri_rgb):
    """The head .p with these triangles appended to its (single) group, in
    both windings, flat-shaded."""
    h, S, head = parse_p(pdata)
    if h['numgroups'] != 1 or h['numtex'] or h['num_unk1'] \
            or h['num_unk2'] or h['num_unk3'] or not h['normidx']:
        raise ValueError('head .p is not one untextured group')
    g = list(struct.unpack('<14I', S['groups']))
    if g[0] != 1 or g[1] != 0 or g[3] != 0 or g[2] != h['numpolys'] \
            or g[4] != h['numverts']:
        raise ValueError('head group %s' % g)
    nv0, nn0, ne0 = h['numverts'], h['numnormals'], h['numedges']
    tag2 = struct.unpack_from('<I', S['polys'], 20)[0]
    verts, cols, norms, polys, pcols, edges, nidx = [], [], [], [], [], [], []
    for tri, rgb in zip(tris_xyz, tri_rgb):
        a, b, c = (np.asarray(p, np.float64) for p in tri)
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n)
        if ln < 1e-12:
            continue
        n /= ln
        for wind, nn in (((0, 1, 2), n), ((0, 2, 1), -n)):
            vi = nv0 + len(verts)
            ni = nn0 + len(norms)
            ei = ne0 + len(edges)
            pts = (a, b, c)
            for k in range(3):
                verts.append(pts[wind[k]])
                cols.append(_argb(rgb))
                nidx.append(ni)
            norms.append(nn)
            edges += [(vi, vi + 1), (vi + 1, vi + 2), (vi + 2, vi)]
            polys.append(struct.pack('<10HI', 0, vi, vi + 1, vi + 2,
                                     ni, ni, ni, ei, ei + 1, ei + 2, tag2))
            pcols.append(_argb(rgb))
    if nv0 + len(verts) > 0xFFFF or ne0 + len(edges) > 0xFFFF:
        raise ValueError('too many vertices for a .p')
    V = np.frombuffer(S['verts'], '<f4').reshape(-1, 3)
    allv = np.vstack([V, np.asarray(verts, np.float32)])
    S['verts'] += np.asarray(verts, '<f4').tobytes()
    S['normals'] += np.asarray(norms, '<f4').tobytes()
    S['vcol'] += np.asarray(cols, '<u4').tobytes()
    S['pcol'] += np.asarray(pcols, '<u4').tobytes()
    S['edges'] += b''.join(struct.pack('<HH', i, j) for i, j in edges)
    S['polys'] += b''.join(polys)
    S['normidx'] += np.asarray(nidx, '<u4').tobytes()
    g[2] += len(polys)
    g[4] += len(verts)
    S['groups'] = struct.pack('<14I', *g)
    bb = struct.unpack('<I6f', S['bbox'])
    mx, mn = allv.max(0), allv.min(0)
    S['bbox'] = struct.pack('<I6f', bb[0], *mx.tolist(), *mn.tolist())
    h.update(numverts=len(allv), numnormals=nn0 + len(norms),
             numvcol=len(allv), numedges=ne0 + len(edges),
             numpolys=h['numpolys'] + len(polys))
    out = bytearray(struct.pack('<16I', *[h[k] for k in HDR]) + head[64:128])
    for k in ('verts', 'normals', 'unk1', 'tex', 'vcol', 'pcol', 'edges',
              'polys', 'unk2', 'unk3', 'hundreds', 'groups', 'bbox',
              'normidx'):
        out += S[k]
    out = bytes(out)
    h2, S2, _ = parse_p(out)                                    # re-parse
    if h2['numpolys'] != h['numpolys']:
        raise ValueError('re-parse disagrees')
    return out, len(polys)


# ------------------------------------------------------------- skeleton
def hrc_bones(text):
    L = [l.strip() for l in text.replace('\r', '').split('\n')]
    L = [l for l in L if l and not l.startswith(':')]
    out = []
    for i in range(0, len(L) - 3, 4):
        out.append((L[i], L[i + 1], float(L[i + 2]), L[i + 3]))
    return out


def _R(ax, deg):
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    if ax == 0:
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if ax == 1:
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def _rot(e):
    M = np.eye(3)
    for ax in (1, 0, 2):
        M = M @ _R(ax, e[ax])
    return M


def pose(bones, anim, frame=0):
    """{bone: (matrix, start point)} at `frame` of a field animation."""
    v, f, b = struct.unpack_from('<III', anim, 0)
    if b != len(bones):
        raise ValueError('animation has %d bones, skeleton %d'
                         % (b, len(bones)))
    x = np.frombuffer(anim, '<f4', offset=36).reshape(f, 6 + 3 * b)
    W = {'root': (_rot(x[frame, :3]), x[frame, 3:6].astype(float))}
    out = {}
    for i, (nm, par, ln, _r) in enumerate(bones):
        PM, PP = W[par]
        M = PM @ _rot(x[frame, 6 + 3 * i:9 + 3 * i])
        out[nm] = (M, PP)
        W[nm] = (M, PP + M @ np.array([0, 0, -ln]))
    return out


# ------------------------------------------------------------- the fit
def glasses_in_head(head_p, M):
    """Glasses triangles (head-local) + colours, fitted on the eyes."""
    z = np.load(ASSET)
    h, S, _ = parse_p(head_p)
    V = np.frombuffer(S['verts'], '<f4').reshape(-1, 3).astype(float)
    vc = np.frombuffer(S['vcol'], '<u4')
    rgb = np.stack([(vc >> 16) & 255, (vc >> 8) & 255, vc & 255], 1)
    eye = (rgb == EYE_RGB).all(1)
    if eye.sum() < 6:
        raise ValueError('no eyes on the head mesh')
    R = M.T @ np.array([1.0, 0, 0])
    U = M.T @ np.array([0, -1.0, 0])
    F = M.T @ np.array([0, 0, -1.0])
    E = V[eye].mean(0)
    E = E + F * ((V[eye] - E) @ F).max()             # the eyes' front
    bv, wv = z['black_v'].astype(float), z['white_v'].astype(float)
    front = bv[bv[:, 2] > 0]
    xc = (front[:, 0].min() + front[:, 0].max()) / 2.0
    yc = (front[:, 1].min() + front[:, 1].max()) / 2.0
    zb = front[:, 2].min()                            # the panel's back
    s = WIDTH / (bv[:, 0].max() - bv[:, 0].min())
    o = E + F * GAP + U * LIFT

    def tf(p):
        return (o + np.outer(p[:, 0] - xc, R) * s
                + np.outer(p[:, 1] - yc, U) * s
                + np.outer(p[:, 2] - zb, F) * s)
    B, Wt = tf(bv), tf(wv)
    tris = [B[t] for t in z['black_t']] + [Wt[t] for t in z['white_t']]
    cols = [BLACK] * len(z['black_t']) + [WHITE] * len(z['white_t'])
    return tris, cols


# ------------------------------------------------------------- char.lgp
def plan_char(get):
    """{entry: bytes} -- the glasses cat (NKDA...). Never raises for the
    glasses: on any doubt the copy is the plain cat."""
    hrc = get(SRC_HRC + '.hrc')
    rsd = get(SRC_RSD + '.rsd')
    p = get(SRC_P + '.p')
    if hrc is None or rsd is None or p is None:
        raise ValueError('no %s in char.lgp' % SRC_HRC)
    text = hrc.decode('latin1')
    bones = hrc_bones(text)
    head = [b for b in bones if b[0] == HEAD_BONE]
    if len(head) != 1 or head[0][3].split()[1:] != [SRC_RSD.upper()]:
        raise ValueError('%s head bone: %s' % (SRC_HRC, head))
    # the HRC: only the head bone's RSD line changes
    pat = re.compile(r'(\n%s\r?\n[^\n]*\n[^\n]*\n\s*1\s+)%s\b'
                     % (HEAD_BONE, SRC_RSD.upper()))
    new_hrc, n = pat.subn(lambda m: m.group(1) + NEW_RSD.upper(), text)
    if n != 1:
        raise ValueError('head RSD line not found once')
    new_rsd = re.sub(r'=%s\.' % SRC_P.upper(), '=%s.' % NEW_P.upper(),
                     rsd.decode('latin1'))
    if new_rsd.count('=%s.' % NEW_P.upper()) != 3:
        raise ValueError('head RSD lines')
    note = 'plain cat'
    new_p = p
    try:
        anim = get(POSE_ANIM + '.a')
        M, _start = pose(bones, anim, 0)[HEAD_BONE]
        tris, cols = glasses_in_head(p, M)
        new_p, npoly = add_mesh(p, tris, cols)
        note = 'glasses (%d triangles)' % npoly
    except Exception as exc:                                  # noqa: BLE001
        note = 'plain cat -- glasses refused: %s' % str(exc)[:80]
    return {NEW_HRC + '.hrc': new_hrc.encode('latin1'),
            NEW_RSD + '.rsd': new_rsd.encode('latin1'),
            NEW_P + '.p': new_p}, note


def apply_to_char(path, log=lambda *_: None):
    import lgp
    global READY
    READY = False
    st = {'written': False, 'added': [], 'note': '', 'refused': None}
    if not wanted():
        return st
    try:
        a = lgp.Archive(path)

        def get(n):
            e = a.index.get(n)
            return e['payload'] if e else None
        want, st['note'] = plan_char(get)
        for name, data in sorted(want.items()):
            have = get(name)
            if have == data:
                continue
            if have is not None:
                a.index[name]['payload'] = data
            else:
                a.add(name, data)
            st['added'].append(name)
        if st['added']:
            tmp = path + '.catshades-tmp'
            a.write(tmp)
            os.replace(tmp, path)
            st['written'] = True
        READY = True
    except Exception as exc:                                   # noqa: BLE001
        st['refused'] = str(exc)[:160]
    return st


def summarise_char(st):
    if st.get('refused'):
        return ('  ! cat shades (BUILD 620e): char.lgp not changed -- %s'
                % st['refused'])
    if st.get('note'):
        return ('  CAT SHADES (BUILD 620e): %s.hrc for the Tsuna\'s-domain cat '
                '-- %s%s. %s=1 disables.'
                % (NEW_HRC.upper(), st['note'],
                   '' if st.get('added') else ' (already present)', OFF_ENV))
    return ''
