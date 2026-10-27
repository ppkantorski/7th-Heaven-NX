"""BUILD 766/766b: ff7nx_floordepth HOLE_FIELDS -- games_2: art standing in
front of the main floor (the 1P/2P console and the machine's ring in a
walkmesh hole, the raised pedestal) gets a depth, per texel, so field
shadows cast on the floor's plane stop painting over it.

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
import os
import struct

import numpy as np
import pytest

import ff7nx_floordepth as F   # noqa: E402

ROOT = _nx_root
FLEVEL = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'workingdir', 'data', 'field', 'flevel.lgp')


def _raw(env):
    import lgp
    p = os.environ.get(env)
    if p and os.path.exists(p):
        return list(lgp.split_sections(open(p, 'rb').read()))
    return None


@pytest.fixture(scope='module')
def vanilla():
    import lgp
    got = _raw('NX_GAMES_2_RAW')
    if got:
        return got
    if not os.path.exists(FLEVEL):
        pytest.skip('needs the dump')
    a = lgp.Archive(FLEVEL)
    return list(lgp.split_sections(a.decompressed(a.index['games_2'])))


@pytest.fixture(scope='module')
def built():
    """a decompressed games_2 from a 7th Heaven build (truecolor pages)"""
    got = _raw('NX_GAMES_2_BUILT')
    if not got:
        pytest.skip('set NX_GAMES_2_BUILT to a built, decompressed games_2')
    return got


def _rec(s9, layer):
    rows = {r[0]: r for r in F._rows(s9)}
    _l, _c, f, n = rows[layer]
    return [s9[f + i * F.REC:f + (i + 1) * F.REC] for i in range(n)]


def _depth_masks(s9):
    """{(x, y): [(ID, k x k opaque or None)]} of layer-2 records"""
    import diag_common as DC
    import field_bg_native as FN
    pl, _ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    plist = FN.parse_texture_block(s9, px)[0]
    out = {}
    for r in _rec(s9, 2):
        idv = struct.unpack_from('<h', r, 24)[0]
        if idv <= 16 or r[26] or r[28]:
            continue
        m = None
        if pm[r[32]].depth == 2:
            u, v = struct.unpack_from('<II', r, 42)
            cx, cy = int(round(u / F.UV_CELL)), int(round(v / F.UV_CELL))
            pg = np.frombuffer(plist[r[32]].data, '<u2').reshape(px, px)
            m = pg[cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] != 0
        out.setdefault(struct.unpack_from('<hh', r, 2), []).append((idv, m))
    return out


def _hidden(masks, sx, sy, dep):
    xy = (int(np.floor(sx / 16)) * 16, int(np.floor(sy / 16)) * 16)
    for idv, m in masks.get(xy, ()):
        if dep / 4.0 <= idv:
            continue
        if m is None:
            return True
        n = m.shape[0]
        if m[min(n - 1, int((sy - xy[1]) * n / 16)), min(n - 1, int((sx - xy[0]) * n / 16))]:
            return True
    return False


def _bodies_newly_hidden(parts, s9):
    """standing anywhere on the walkmesh: body points (feet to head) a new
    depth record hides that the 1997 layer 2 did not"""
    R, o, zoom = F._camera(parts[1])
    tris = F._walk_tris(parts[4])
    old, new = _depth_masks(parts[8]), _depth_masks(s9)
    added = {}
    for xy, lst in new.items():
        olds = {i for i, _m in old.get(xy, ())}
        for idv, m in lst:
            if idv not in olds:
                added.setdefault(xy, []).append((idv, m))
    bad = []
    w = [(a / 4, b / 4, (4 - a - b) / 4) for a in range(5) for b in range(5 - a)]
    for t in range(len(tris)):
        for ww in w:
            foot = ww[0] * tris[t, 0] + ww[1] * tris[t, 1] + ww[2] * tris[t, 2]
            for h in np.linspace(3, 100, 8):
                rad = 12 if h < 25 else 20
                for a_ in np.linspace(0, 2 * np.pi, 8, endpoint=False):
                    P = foot + (rad * np.cos(a_), rad * np.sin(a_), h - 10)
                    C = R @ P + o
                    sx, sy, dep = zoom * C[0] / C[2], zoom * C[1] / C[2], C[2]
                    if _hidden(added, sx, sy, dep) and not _hidden(old, sx, sy, dep):
                        bad.append((t, h))
    return bad


def _shadow_hidden(parts, s9, box):
    """share of a shadow on the main floor (plane 11 under, 3 nearer: the
    shadow's depth bias) inside screen box (x0, x1, y0, y1) behind a depth
    record. BUILD 780: points inside games_2's 'keep' polygons (plain floor
    that takes shadows on purpose) are not counted."""
    R, o, zoom = F._camera(parts[1])
    Ri = np.linalg.inv(R)
    b = Ri @ (-o)
    masks = _depth_masks(s9)
    keep = F.HOLE_FIELDS['games_2'].get('keep', ())
    tot = hid = 0
    for sx in np.arange(box[0], box[1], 0.5):
        for sy in np.arange(box[2], box[3], 0.5):
            if any(F._in_poly(k, np.array(sx), np.array(sy)) for k in keep):
                continue
            d = Ri @ np.array([sx / zoom, sy / zoom, 1.0])
            tot += 1
            hid += _hidden(masks, sx, sy, (-11 - b[2]) / d[2] - 3)
    return hid / max(tot, 1)


def test_780_keep_floor_takes_shadows():
    """BUILD 780: no front copy is made over the plain floor in 'keep'."""
    built = _raw('NX_GAMES_2_BUILT')
    if built is None:
        pytest.skip('set NX_GAMES_2_BUILT to a built, decompressed games_2')
    parts = built
    s9, _n = F.plan_holes('games_2', parts[1], parts[4], parts[8])
    keep = F.HOLE_FIELDS['games_2']['keep']
    R, o, zoom = F._camera(parts[1])
    Ri = np.linalg.inv(R)
    b = Ri @ (-o)
    masks = _depth_masks(s9)
    m0 = _depth_masks(parts[8])
    new = tot = 0
    for sx in np.arange(124, 160, 0.5):
        for sy in np.arange(186, 214, 0.5):
            if not any(F._in_poly(k, np.array(sx), np.array(sy))
                       for k in keep):
                continue
            d = Ri @ np.array([sx / zoom, sy / zoom, 1.0])
            z = (-11 - b[2]) / d[2] - 3
            tot += 1
            new += _hidden(masks, sx, sy, z) and not _hidden(m0, sx, sy, z)
    assert tot > 200 and new == 0


# hardware 10-20/21 (screen units): v1's console shadow, the sword tip on
# the machine's blue ring above it, Cloud's shadow on the pedestal's pad
# (766b); under the console, on its stand and the pedestal top (766c)
CONSOLE, RING, PAD = (64, 92, 112, 128), (77, 92, 100, 108), (87, 131, 157, 168)
STAND, TOP = (64, 100, 140, 158), (68, 82, 160, 168)
# hardware 10-21 (771): Cloud beside the rail's right post -- his shadow on
# the pedestal rim, which is 1997 layer-2 art deeper than the shadow
WEDGE = (122, 140, 176, 192)


def test_766_vanilla_pages_move_whole_squares(vanilla):
    parts = vanilla
    s9, n = F.plan_holes('games_2', parts[1], parts[4], parts[8])
    assert n == 28 and len(s9) == len(parts[8])
    old = {r[0]: r[3] for r in F._rows(parts[8])}
    new = {r[0]: r[3] for r in F._rows(s9)}
    assert new[1] == old[1] - n and new[2] == old[2] + n
    assert _shadow_hidden(parts, s9, CONSOLE) > 0.99
    assert _shadow_hidden(parts, s9, RING) > 0.99


def test_766_nobody_standing_is_newly_hidden(vanilla):
    parts = vanilla
    s9, _n = F.plan_holes('games_2', parts[1], parts[4], parts[8])
    assert _bodies_newly_hidden(parts, s9) == []


def test_766_the_background_looks_the_same(vanilla):
    import ff7nx_framesim as FS
    parts = vanilla
    N = list(parts)
    N[8], _n = F.plan_holes('games_2', parts[1], parts[4], parts[8])
    a = FS.Field(parts).render(cam=(0, 0), K=1, mark=False)
    b = FS.Field(N).render(cam=(0, 0), K=1, mark=False)
    assert not (a != b).any()


def test_766_twice_is_once(vanilla):
    parts = vanilla
    s9, n = F.plan_holes('games_2', parts[1], parts[4], parts[8])
    again, n2 = F.plan_holes('games_2', parts[1], parts[4], s9)
    assert n > 0 and n2 == 0 and again == s9


def test_766b_built_pages_split_by_texel(built):
    """the truecolor build: mixed squares are split (object texels to
    layer-2 copies, a band of rows per depth, 766c), floor texels stay;
    every reported spot covered, nobody standing newly hidden, no pixel
    changed at 3x at any scroll (blended overlays keep their art under
    them), and planning twice is planning once"""
    import ff7nx_framesim as FS
    parts = built
    s9, n = F.plan_holes('games_2', parts[1], parts[4], parts[8])
    adds = (len(s9) - len(parts[8])) // F.REC
    assert n >= 80 and adds >= 50
    for box in (CONSOLE, RING, PAD, STAND, TOP, WEDGE):
        assert _shadow_hidden(parts, s9, box) > 0.98, box
    assert _bodies_newly_hidden(parts, s9) == []
    N = list(parts)
    N[8] = s9
    A, B_ = FS.Field(parts), FS.Field(N)
    for cam in ((0, 0), (-120, -100), (120, 100), (-60, 50), (60, -50)):
        a = A.render(cam=cam, K=3, mark=False)
        b = B_.render(cam=cam, K=3, mark=False)
        assert not (a != b).any(), cam
    again, n2 = F.plan_holes('games_2', parts[1], parts[4], s9)
    assert n2 == 0 and again == s9


def test_766c_blended_overlays_keep_their_art_under_them(built):
    """the machine's glow (blended layer-2 records at tiles (0..64, 80..96))
    is drawn over the layer-1 art: nothing of that art moves to layer 2"""
    parts = built
    s9, _n = F.plan_holes('games_2', parts[1], parts[4], parts[8])
    blended = set()
    for r in _rec(parts[8], 2):
        if r[26] or r[28]:
            blended.add(struct.unpack_from('<hh', r, 2))
    assert blended
    old = {struct.unpack_from('<hh', r, 2) for r in _rec(parts[8], 1)}
    newl1 = {struct.unpack_from('<hh', r, 2) for r in _rec(s9, 1)}
    assert not ((old - newl1) & blended)            # none moved whole
    import diag_common as DC
    import field_bg_native as FN
    pl, _ts, _te, px = DC.parse_pages(s9)
    k = px // 16
    plist = FN.parse_texture_block(s9, px)[0]
    added = _rec(s9, 2)[len(_rec(parts[8], 2)):]
    for r in added:
        xy = struct.unpack_from('<hh', r, 2)
        if xy not in blended:
            continue
        u, v = struct.unpack_from('<II', r, 42)
        cx, cy = int(round(u / F.UV_CELL)), int(round(v / F.UV_CELL))
        mine = np.frombuffer(plist[r[32]].data, '<u2').reshape(px, px)[
            cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] != 0
        for q in _rec(parts[8], 2):
            if struct.unpack_from('<hh', q, 2) != xy or not (q[26] or q[28]):
                continue
            pg = q[34] if q[28] and q[34] else q[32]
            uu, vv = struct.unpack_from('<II', q, 42)
            ox, oy = int(round(uu / F.UV_CELL)), int(round(vv / F.UV_CELL))
            theirs = np.frombuffer(plist[pg].data, '<u2').reshape(px, px)[
                oy * k:(oy + 1) * k, ox * k:(ox + 1) * k] != 0
            assert not (mine & theirs).any(), xy


def test_766_switch(monkeypatch):
    monkeypatch.setenv(F.HOLE_OFF_ENV, '1')
    assert F.hole_disabled()
    monkeypatch.setenv(F.HOLE_OFF_ENV, '')
    assert not F.hole_disabled()


def test_771_front_copies_never_change_the_picture_or_cut_a_leg(built):
    """the pedestal rim beside the right post is 1997 layer-2 art whose IDs
    lie behind a floor shadow there: copies of exactly the visible pixels
    take the depth that hides it; the 1997 records stay as they were"""
    parts = built
    old2 = _rec(parts[8], 2)
    s9, _n = F.plan_holes('games_2', parts[1], parts[4], parts[8])
    new2 = _rec(s9, 2)
    assert new2[:len(old2)] == old2[:len(old2)] or set(old2) <= set(new2)
    assert _shadow_hidden(parts, s9, WEDGE) > 0.98
    assert _bodies_newly_hidden(parts, s9) == []


def test_771_dilate():
    m = np.zeros((7, 7), bool)
    m[3, 3] = True
    d = F._dilate(m, 1)
    assert d.sum() == 9 and d[2:5, 2:5].all()
    e = np.zeros((5, 5), bool)
    e[0, 4] = True
    assert F._dilate(e, 2).sum() == 9          # clipped at the border, no wrap
