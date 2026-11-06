"""BUILD 891: eye/mouth decals lifted off the face (ff7nx_decallift).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import struct

import numpy as np
import pytest

import ff7nx_decallift as D                                   # noqa: E402


def make_p(groups):
    """groups: [(verts[[x,y,z]...], tris[[a,b,c] local], uvs or None)].
    A face that looks along -z; the head is a flat 4x4 plate at z = 0."""
    V, UV, polys, G, H = [], [], [], [], []
    for gi, (verts, tris, uvs) in enumerate(groups):
        vs, ts = len(V), len(UV)
        V += verts
        if uvs:
            UV += uvs
        ps = len(polys)
        for a, b, c in tris:
            polys.append(struct.pack('<10HI', 0, a, b, c, 0, 0, 0, 0, 0, 0, 0))
        G.append(struct.pack('<14I', 2 if uvs else 1, ps, len(tris), vs,
                             len(verts), 0, 0, 0, 0, 0, 0, ts, 1 if uvs else 0,
                             gi))
        H.append(struct.pack('<25I', 1, 1, 0, 0, gi, *([0] * 20)))
    n = len(V)
    hdr = struct.pack('<16I', 1, 1, 0, n, 0, 0, len(UV), n, 0, len(polys),
                      0, 0, len(H), len(G), 1, 0) + bytes(64)
    out = hdr + np.asarray(V, '<f4').tobytes()
    out += np.asarray(UV, '<f4').tobytes() if UV else b''
    out += b'\0' * (4 * n) + b'\0' * (4 * len(polys)) + b''.join(polys)
    out += b''.join(H) + b''.join(G)
    out += struct.pack('<I6f', 0, 2, 2, 1, -2, -2, -1)
    return out


PLATE = ([[-2, -2, 0], [2, -2, 0], [-2, 2, 0], [2, 2, 0]],
         [[0, 1, 2], [1, 3, 2]])
# a box-ish back so the plate's centre lies behind it (+z); the face is -z
BACK = [[-2, -2, 3], [2, -2, 3], [-2, 2, 3], [2, 2, 3]]


def head_groups(eye_z):
    verts = PLATE[0] + BACK
    tris = PLATE[1] + [[4, 6, 5], [5, 6, 7]]
    uv = [[0, 0]] * len(verts)
    eye = ([[-1, -0.5, eye_z], [0, -0.5, eye_z], [-1, 0.5, eye_z],
            [0, 0.5, eye_z]], [[0, 1, 2], [1, 3, 2]],
           [[0, 0], [1, 0], [0, 1], [1, 1]])
    return [(verts, tris, None), eye, (verts, tris, uv)]


def eye_z_of(p):
    h, off, V, UV, groups = D.read_model(p)
    g = groups[1]
    return V[g['vstart']:g['vstart'] + g['nverts'], 2]


def test_flush_eye_is_lifted_to_the_clearance():
    p = make_p(head_groups(-0.007))
    new, rep = D.plan_model(p, floor=0.04)
    assert new is not None and len(new) == len(p)
    assert rep[0][1] == 'lifted'
    assert np.allclose(eye_z_of(new), -0.04, atol=1e-4)
    # the face and its untextured copy did not move
    assert new[128:128 + 12 * 8] == p[128:128 + 12 * 8]


def test_lift_is_idempotent():
    p = make_p(head_groups(-0.007))
    new, _ = D.plan_model(p, floor=0.04)
    again, rep = D.plan_model(new, floor=0.04)
    assert again is None and rep[0][1] == 'clear'


def test_already_clear_is_untouched():
    new, rep = D.plan_model(make_p(head_groups(-0.06)), floor=0.04)
    assert new is None and rep[0][1] == 'clear'


def test_buried_decal_is_left_alone():
    new, rep = D.plan_model(make_p(head_groups(0.05)), floor=0.04)
    assert new is None and rep[0][1] == 'buried'


def test_tie_zone_decal_is_lifted():
    new, rep = D.plan_model(make_p(head_groups(0.002)), floor=0.04)
    assert new is not None and rep[0][1] == 'lifted'


def test_blocked_by_another_part():
    gs = head_groups(-0.007)
    # a brim 0.02 in front of the eye
    gs.insert(2, ([[-1.5, -1, -0.02], [0.5, -1, -0.02], [-1.5, 1, -0.02],
                   [0.5, 1, -0.02]], [[0, 1, 2], [1, 3, 2]], None))
    new, rep = D.plan_model(make_p(gs), floor=0.04)
    assert new is None and rep[0][1] == 'blocked'


def test_invisible_texels_are_not_measured():
    p = make_p(head_groups(0.05))           # buried, but nothing visible
    new, rep = D.plan_model(p, lambda ti: np.zeros((4, 4), bool), 0.04)
    assert new is None and rep[0][1] == 'invisible'


def test_switch(monkeypatch):
    monkeypatch.setenv(D.ENV, '0')
    assert not D.enabled()
    monkeypatch.setenv(D.ENV, '0.06')
    assert D.clearance() == pytest.approx(0.06)
    monkeypatch.delenv(D.ENV)
    assert D.clearance() == D.CLEARANCE


def test_visible_mask_32bit_colour_key():
    import tex
    w = h = 2
    hdr = bytearray(tex.HEADER_LEN)
    struct.pack_into('<I', hdr, tex.O_VERSION, 1)
    struct.pack_into('<I', hdr, tex.O_WIDTH, w)
    struct.pack_into('<I', hdr, tex.O_HEIGHT, h)
    struct.pack_into('<I', hdr, tex.O_BYTES_PER_PIXEL, 4)
    px = bytes([0, 0, 0, 0, 10, 20, 30, 0, 0, 0, 0, 255, 1, 0, 0, 0])
    m = D.visible_mask(bytes(hdr) + px)
    assert m.tolist() == [[False, True], [False, True]]
