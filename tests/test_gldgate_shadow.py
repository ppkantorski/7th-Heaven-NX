"""BUILD 861: gldgate's tubes (native/shadows.c tube_jump).

In field 497 (gldgate) a model in a tube (walkmesh triangles 38..51) casts
no shadow; during a JUMP the shadow belongs to the start triangle while the
model rises and to the destination while it falls. So a jump into a tube
shows the shadow up to the top of the jump, a jump out of one from the top
on. Run on the real blob in unicorn with the field harness of
test_shadows.py; other fields are unchanged.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os
import struct
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import test_shadows as T                                       # noqa: E402

EV0 = 0xCC1670                       # the event array (x86 0x634D4D)
EV_PAGE = 0x340000
PLAT_Z, TUBE_Z = 0, -120


def _scene(fid, named, dest=0, jump=None, at=(100.0, 100.0)):
    """jump = None (standing) or (step t, vertical speed v)"""
    cam = T._camera()
    tri = lambda z: ((0, 0, z), (1000, 0, z), (0, 1000, z))      # noqa: E731
    tris = [tri(PLAT_Z)] * 38 + [tri(TUBE_Z)] * 14              # 38..51 tubes
    m = T.Machine()
    floor = TUBE_Z if 38 <= named <= 51 else PLAT_Z
    # mid-jump the body is above the platform (it leaves or lands on it)
    origin = np.array([at[0], at[1], 30.0 if jump else floor - 10.0])
    pages = T._field_world(m, tris, cam, origin)
    tab = T.LEVEL + 0x3000
    g = lambda a: T.LVL_PAGE + (a - T.LEVEL)                      # noqa: E731
    m.uc.mem_write(T.CAM_PAGE + 0x738, struct.pack('<I', tab))
    m.uc.mem_write(T.CAM_PAGE + 0x73E, struct.pack('<H', 1))
    m.uc.mem_write(T.CAM_PAGE + 0x468, struct.pack('<H', fid))
    m.uc.mem_write(g(tab + 0x178), struct.pack('<I', T.S110 - 0x38))
    m.uc.mem_map(EV_PAGE, 0x10000)
    m.uc.mem_map(0x330000, 0x10000)
    m.uc.mem_write(pages + 8 * 0xCC0, struct.pack('<Q', 0x330000))
    m.uc.mem_write(pages + 8 * 0xCC1, struct.pack('<Q', EV_PAGE))
    m.uc.mem_write(0x330000 + 0xB60, struct.pack('<I', EV0))
    e = EV_PAGE + (EV0 & 0xFFF)
    m.uc.mem_write(e + 0x0C, struct.pack('<3i', int(origin[0]) << 12,
                                         int(origin[1]) << 12,
                                         int(origin[2] + 10) << 12))
    m.uc.mem_write(e + 0x78, struct.pack('<hh', named, dest))
    if jump:
        t, v = jump
        m.uc.mem_write(e + 0x62, struct.pack('<H', 3 << 8))      # movement 3
        m.uc.mem_write(e + 0x70, struct.pack('<h', 1))           # running
        m.uc.mem_write(e + 0x2C, struct.pack('<i', v))
        m.uc.mem_write(e + 0x30, struct.pack('<hh', 23, t))      # H, step
    got = T._field_draw(m, [T._part(cam, origin + (0, 0, 80))], pages)
    shadow = got[0] == 2 and m.u32(T.CTX + 4) != 0
    z = T._landed(cam, got[1], np.zeros((1, 3)))[0][2] if got[0] == 2 else None
    return shadow, z


RISING = (5, 16000 * 11)             # apex at step 11 of 23
FALLING = (15, 16000 * 11)


def test_standing_on_the_platform_keeps_its_shadow():
    assert _scene(497, 10)[0]


def test_standing_in_a_tube_casts_none():
    for t in (38, 45, 51):
        assert not _scene(497, t)[0], t


def test_jump_in_shows_until_the_top_then_none():
    shadow, z = _scene(497, 10, dest=47, jump=RISING)
    assert shadow and abs(z - (PLAT_Z - 11)) < 1e-2
    assert not _scene(497, 10, dest=47, jump=FALLING)[0]


def test_jump_out_none_until_the_top_then_on_the_platform():
    assert not _scene(497, 47, dest=10, jump=RISING)[0]
    shadow, z = _scene(497, 47, dest=10, jump=FALLING)
    assert shadow and abs(z - (PLAT_Z - 11)) < 1e-2      # the platform's


def test_other_fields_unchanged():
    # the same falling jump into triangle 47 elsewhere keeps the stock rule
    assert _scene(496, 10, dest=47, jump=FALLING)[0]
    assert _scene(496, 45)[0]


# ---- BUILD 861: games (field 505), the staircase edge ----------------------

def _games(fid, named, heights, x=-9.0):
    """per part height above the model origin: drew a shadow?"""
    cam = T._camera()
    tri = ((-2000, -2000, 0), (2000, -2000, 0), (-2000, 2000, 0))
    m = T.Machine()
    origin = np.array([x, -59.0, -10.0])
    pages = T._field_world(m, [tri] * 12, cam, origin)
    tab = T.LEVEL + 0x3000
    g = lambda a: T.LVL_PAGE + (a - T.LEVEL)                      # noqa: E731
    m.uc.mem_write(T.CAM_PAGE + 0x738, struct.pack('<I', tab))
    m.uc.mem_write(T.CAM_PAGE + 0x73E, struct.pack('<H', 1))
    m.uc.mem_write(T.CAM_PAGE + 0x468, struct.pack('<H', fid))
    m.uc.mem_write(g(tab + 0x178), struct.pack('<I', T.S110 - 0x38))
    m.uc.mem_map(0x330000, 0x10000)
    m.uc.mem_map(EV_PAGE, 0x10000)
    m.uc.mem_write(pages + 8 * 0xCC0, struct.pack('<Q', 0x330000))
    m.uc.mem_write(pages + 8 * 0xCC1, struct.pack('<Q', EV_PAGE))
    m.uc.mem_write(0x330000 + 0xB60, struct.pack('<I', EV0))
    m.uc.mem_write(EV_PAGE + (EV0 & 0xFFF) + 0x78, struct.pack('<h', named))
    parts = [T._part(cam, origin + (0, 0, 80))]                 # the hips first
    parts += [T._part(cam, origin + (0, 0, h)) for h in heights]
    got = T._field_draw(m, parts, pages)
    acts = [a for a in got if not isinstance(a, np.ndarray)]
    return [a == 2 for a in acts[1:]]


def test_games_stairs_drop_parts_landing_past_the_edge():
    # landing x = -9 - 0.5 (h + 1): h 20 -> -19.5 (kept), h 100 -> -59.5
    assert _games(505, 4, (20.0, 100.0)) == [True, False]
    assert _games(505, 9, (20.0, 100.0)) == [True, False]      # the landing


def test_games_elsewhere_and_other_fields_unchanged():
    assert _games(505, 10, (20.0, 100.0)) == [True, True]      # main floor
    assert _games(504, 4, (20.0, 100.0)) == [True, True]
    # a part landing inside the edge on the stairs keeps its shadow
    assert _games(505, 4, (20.0, 100.0), x=40.0) == [True, True]
