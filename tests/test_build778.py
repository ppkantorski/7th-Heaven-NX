"""BUILD 778: mogu_1's water (pairs kept, newest drawn first, the wrap
copy) and its corner tile (blackcell never uses an FX-band page), games_2's
rectangle, nmkin_3's steam cut, fship_2's frame edges, rckt2's dead arrow.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import struct  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import ff7nx_blackcell as BC  # noqa: E402
import ff7nx_deadarrow as DA  # noqa: E402
import ff7nx_edgesmooth as ES  # noqa: E402
import ff7nx_floordepth as FD  # noqa: E402
import ff7nx_ventcrop as VC  # noqa: E402
import ff7nx_waterloop as W  # noqa: E402


def test_waterloop_script_keeps_pairs_and_wraps_through_the_copy():
    sec = b'\x00' * 16 + W.HEAD_OLD + W.LOOP_OLD + b'\x12\x10'
    out, changed = W.fix_script(sec)
    assert changed and len(out) == len(sec)
    assert W.fix_script(out) == (out, False)
    with pytest.raises(ValueError):
        W.fix_script(b'\x00' * 64)
    # simulate one cycle: the frame drawn first among those on is shown
    order = [W.COPY] + sorted(W.FRAMES, key=lambda f: (-f[0], -f[1]))
    on = {(1, 0), (1, 1)}
    shown = []
    on.discard(W.COPY)
    on.add(W.FRAMES[0])
    shown.append(next(f for f in order if f in on))
    for m in range(16):
        step = W.LOOP_NEW[11 * m:11 * m + 8]
        assert step[0] == 0xE1 and step[4] == 0xE0
        on.discard((step[2], step[3]))
        on.add((step[6], step[7]))
        assert len(on) == 2                    # always a pair: full coverage
        if m < 15:
            shown.append(next(f for f in order if f in on))
    pics = [(1, 0) if f == W.COPY else f for f in shown]
    assert sorted(pics) == sorted(W.FRAMES)     # each picture once a cycle
    assert pics == W.FRAMES[1:] + W.FRAMES[:1]  # in order, nothing held


def test_blackcell_never_offers_an_fx_band_page():
    class P:
        def __init__(s, slot, depth=1, px=256):
            s.slot, s.depth, s.px, s.size_flag = slot, depth, px, 0
            s.data = bytes(65536)
    pages = {0: P(0), 25: P(25), 1: P(1)}
    by_cell = {(0, x, y): [1] for x in range(0, 256, 16)
               for y in range(0, 256, 16)}
    by_cell.update({(1, x, y): [1] for x in range(0, 256, 16)
                    for y in range(0, 256, 16)})
    assert BC._spare_cell(0, pages, by_cell, {}) is None   # not page 25
    del by_cell[(1, 32, 48)]
    assert BC._spare_cell(0, pages, by_cell, {}) == (1, (32, 48))


def test_games_2_rectangle_inside_deep_polygon():
    cfg = FD.HOLE_FIELDS['games_2']
    gx, gy = np.meshgrid([126.0, 132.0], [180.0, 181.5])
    out = FD._deep(cfg, gx, gy, np.full(gx.shape, 1000.0))
    assert (out == 1000.0 - cfg['deep'][0]['bias']).all()


def test_ventcrop_follows_the_opening_ellipse():
    """BUILD 780: kept inside the rim's ellipse below its long axis, all
    kept above it; the cut is the arc, not a curve turning inwards.
    (781's dark-rim clamp is tested separately.)"""
    cfg = dict(VC.FIELDS['nmkin_3']); cfg.pop('dark', None)
    (cx, cy), (ra, rb), rot = cfg['ellipse']
    assert VC.coverage(cfg, cx, cy) == 1.0                  # the middle
    assert VC.coverage(cfg, cx, cy - 20) == 1.0             # steam above
    assert VC.coverage(cfg, cx, cy + rb + 3) == 0.0         # below the rim
    assert VC.coverage(cfg, cx, cy + rb - 1) == 1.0         # just inside
    # along the lower arc the 0.5 contour stays on the grown ellipse
    import math
    t = math.radians(rot)
    for ang in (20, 45, 70, 90, 110, 135, 160):
        e = math.radians(ang)
        u, v = (ra + cfg['grow']) * math.cos(e), (rb + cfg['grow']) * math.sin(e)
        x = cx + u * math.cos(t) - v * math.sin(t)
        y = cy + u * math.sin(t) + v * math.cos(t)
        assert abs(VC.coverage(cfg, x, y) - 0.5) < 0.02, ang


def test_781_ventcrop_never_leaves_steam_on_the_black_rim():
    cfg = VC.FIELDS['nmkin_3']
    for x, yd in cfg['dark']:
        assert VC.coverage(cfg, x, yd + 1.0) == 0.0, x      # on the black
    # left of the dark table's span the 780 ellipse alone decides
    plain = dict(cfg); plain.pop('dark')
    for x in (105.0, 110.0):
        for y in (-52.0, -45.0, -42.0):
            assert VC.coverage(cfg, x, y) == VC.coverage(plain, x, y)


def test_fship_targets_guard_the_floor_and_sample_the_sky():
    for f in ES.FSHIP:
        assert ES.TARGETS[f] == [(2, None, None, None, (1,))]
        assert ES.AA[f]['states'] == 'auto' and ES.AA[f]['thick_only']


def test_deadarrow_checks_its_coordinates():
    sec8 = bytearray(0x224 + 16 * 12)
    struct.pack_into('<4i', sec8, 0x224 + 32, -27, 2615, 0, 1)
    out, n = DA.fix_sec8('rckt2', bytes(sec8))
    assert n == 1 and struct.unpack_from('<i', out, 0x224 + 44)[0] == 0
    assert DA.fix_sec8('rckt2', out)[1] == 0
    struct.pack_into('<4i', sec8, 0x224 + 32, -26, 2615, 0, 1)
    with pytest.raises(ValueError):
        DA.fix_sec8('rckt2', bytes(sec8))
    assert DA.fix_sec8('rckt', bytes(sec8))[1] == 0


# ------------------------------------------------------------- BUILD 780
def test_780_bandguard_finds_a_plain_tile_on_an_fx_page():
    import ff7nx_bandguard as BG

    def rec(x, y, page, fx=0, fxpage=0, par=0, st=0):
        r = bytearray(52)
        struct.pack_into('<hh', r, 2, x, y)
        r[26], r[27], r[28], r[32], r[34] = par, st, fx, page, fxpage
        return bytes(r)
    van = {(1, -160, -120, 0, 0, 0): [0]}
    b9 = bytes(rec(-160, -120, 25))
    v9 = bytes(rec(-160, -120, 0))
    import types
    orig = BG._records
    try:
        BG._records = lambda s9: {(1, -160, -120, 0, 0, 0): [0]}
        assert BG.misplaced(b9, v9) == [(0, 0)]
        assert BG.misplaced(v9, v9) == []
        b9b = bytes(rec(-160, -120, 26))
        assert BG.misplaced(b9b, v9) == []          # an opaque slot: fine
    finally:
        BG._records = orig
    assert van


def test_780_water_corner_is_a_tangent_fillet():
    import math
    import ff7nx_waterloop as WL
    ax, ay = WL.CORNER['apex']
    u1 = np.array(WL.CORNER['u1'])
    u2 = np.array(WL.CORNER['u2'])
    assert abs(np.linalg.norm(u2) - 1) < 1e-6
    assert WL.CORNER_R > 10                       # visibly rounded
    inside = WL._in_tri(np.array([ax + 0.5]), np.array([ay + 0.2]),
                        (ax, ay), (ax + 5, ay), (ax - 3, ay + 3))
    assert inside.all()


def test_780_daynight_searches_newest_first():
    import ff7nx_daynight as D
    import inspect
    src = inspect.getsource(D.build_defer_draw_cave)
    assert 'BSS_DEF_HEAD' in src and 'NEWEST FIRST' in src
