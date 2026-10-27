#!/usr/bin/env python3
"""tests/test_fxorigin.py -- where Kujata's disc actually IS.

BUILD 360. The disc is centred at world z = -5000, 5000 units behind the
battlefield whose floor it copies, so the two symmetries cannot coincide.
Build 359 moved the snapshot WINDOW instead, which decides what is painted on
the disc and cannot decide where the disc is drawn -- the operator's hardware
result ("still behind the enemies") is what separated the two.

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
import importlib
import os
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                # noqa: E402
import arm64emu                                                # noqa: E402
import ff7nx_fxorigin as O                                     # noqa: E402


def _find_dump():
    here = os.path.join(_HERE, 'dump', 'exefs', 'main')
    if os.path.exists(here):
        return here
    sib = os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                       '7th_heaven_nx', 'dump', 'exefs', 'main')
    return sib if os.path.exists(sib) else here


DUMP = _find_dump()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(O.Z_ENV, raising=False)
    importlib.reload(O)
    yield
    importlib.reload(O)


def test_stock_is_minus_5000_and_the_word_round_trips():
    assert O.word_for(O.Z_STOCK_VALUE) == O.Z_STOCK
    assert O.z() == O.DEFAULT_Z == O.Z_STOCK_VALUE
    # BUILD 361: back to stock. A 4.80x scale error displaces every off-centre
    # feature by (s-1)*d, so the origin cannot be judged until the scale is
    # right -- and moving the disc invalidates the FIT_HUV everything rests on.
    assert O.enabled() is False


def test_the_words_really_load_those_values_on_a_cpu():
    """Execute them. MOVN is easy to get wrong by one."""
    for zv in (-5000, -4000, -1, 0, 1, 2000, 8000, -8000):
        cpu = arm64emu.Cpu(arm64emu.Mem())
        cpu.set(O.Z_REG, 0xDEADBEEF)
        cpu.run(0x1000, [O.word_for(zv)])
        got = cpu.get(O.Z_REG) & 0xFFFFFFFF
        assert got == (zv & 0xFFFFFFFF), (zv, hex(got))


def test_positive_side_agrees_with_a64():
    for zv in (0, 1, 255, 4096, 8000):
        assert O.word_for(zv) == A.movz(O.Z_REG, zv)


def test_read_state_names_the_number_back():
    class Img(bytearray):
        pass
    for zv in (-5000, -1234, 0, 777):
        img = Img(O.Z_SITE + 8)
        struct.pack_into('<I', img, O.Z_SITE, O.word_for(zv))
        assert O.read_state(img).startswith('field origin z = %d' % zv)


def test_out_of_band_is_refused_not_clamped(monkeypatch):
    with pytest.raises(ValueError):
        O.word_for(99999)
    for bad in ('banana', '99999', '-99999'):
        monkeypatch.setenv(O.Z_ENV, bad)
        assert O.z() == O.DEFAULT_Z
    monkeypatch.setenv(O.Z_ENV, 'stock')
    assert O.z() == O.Z_STOCK_VALUE and O.enabled() is False


def test_the_screen_move_is_what_the_note_claims():
    """The prediction, recomputed from FIT_HUV and the disc's own geometry so
    it cannot drift away from the write-up: -5000 -> 0 moves the origin about
    108 px left and 79 px down on 1280x720."""
    import ff7nx_fbresample as R
    import ff7nx_fxdisc as D

    def inv3(a):
        (a0, a1, a2, a3, a4, a5, a6, a7, a8) = a
        c = [a4 * a8 - a5 * a7, a2 * a7 - a1 * a8, a1 * a5 - a2 * a4,
             a5 * a6 - a3 * a8, a0 * a8 - a2 * a6, a2 * a3 - a0 * a5,
             a3 * a7 - a4 * a6, a1 * a6 - a0 * a7, a0 * a4 - a1 * a3]
        d = a0 * c[0] + a1 * c[3] + a2 * c[6]
        return [x / d for x in c]

    M = inv3(R.FIT_HUV)

    def scr(u, v):
        d = M[6] * u + M[7] * v + M[8]
        return ((M[0] * u + M[1] * v + M[2]) / d,
                (M[3] * u + M[4] * v + M[5]) / d)

    c = 3.0 * D.k_for(D.percent()) / 256.0 / 4.0     # world units per UV unit
    dv = 5000.0 / c
    ax, ay = scr(128.0, 128.0)                       # z = -5000, stock
    bx, by = scr(128.0, 128.0 - dv)                  # z = 0
    assert -120 < (bx - ax) * 1280 < -95, (bx - ax) * 1280
    assert 65 < (by - ay) * 720 < 95, (by - ay) * 720
    # and it is monotone in z, so the table in the header is usable as a dial
    prev = None
    for zv in (-5000, -4000, -3000, -2000, -1000, 0, 1000):
        s = scr(128.0, 128.0 - (5000.0 + zv) / c)
        if prev is not None:
            assert s[1] > prev[1] and s[0] < prev[0], zv
        prev = s


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_against_the_real_image():
    import nxmap
    m = nxmap.Main(DUMP)
    assert O.verify(m.img) == []
    assert O.read_state(m.img) == 'field origin z = -5000 (stock)'
    assert struct.unpack_from('<I', m.img, O.Z_SITE)[0] == O.Z_STOCK


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_the_anchors_are_really_in_the_image():
    import nxmap
    m = nxmap.Main(DUMP)
    for va, want in sorted(O.ANCHORS.items()):
        got = struct.unpack_from('<I', m.img, va)[0]
        assert got == want, ('+0x%X' % va, hex(got), hex(want))


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_it_shares_no_site_with_fxdisc_or_fxscale():
    """Three modules write into the same routine. If any two claimed the same
    word one would always refuse."""
    import ff7nx_fxdisc as D
    import ff7nx_fxscale as S
    for other in (D, S):
        sites = set()
        for name in dir(other):
            if name.endswith('_SITE'):
                sites.add(getattr(other, name))
        assert O.Z_SITE not in sites, other.__name__


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_plan_and_revert(monkeypatch):
    import nxmap
    m = nxmap.Main(DUMP)
    patches, notes, problems = O.plan(m)             # default is stock now
    assert problems == [] and patches == []
    patches, _, problems = O.plan(m, revert=True)
    assert problems == [] and patches == []
    monkeypatch.setenv(O.Z_ENV, '0')
    importlib.reload(O)
    patches, _, problems = O.plan(m)
    assert problems == [] and len(patches) == 1
    assert bytes.fromhex(patches[0]['set']) == struct.pack('<I', O.word_for(0))
    assert bytes.fromhex(patches[0]['expect']) == struct.pack('<I', O.Z_STOCK)
    monkeypatch.setenv(O.Z_ENV, '-2000')
    importlib.reload(O)
    patches, _, _ = O.plan(m)
    assert bytes.fromhex(patches[0]['set']) == struct.pack(
        '<I', O.word_for(-2000))


def test_the_distortion_table_in_findings_361():
    """The measurement the origin work is now blocked on, recomputed here so
    the note cannot drift from the code: composing FIT_HUV (where the disc
    draws a texel) with the capture geometry (where that texel came from)
    gives how far off 1:1 the field is."""
    import math
    import ff7nx_fbresample as R

    def inv3(a):
        (a0, a1, a2, a3, a4, a5, a6, a7, a8) = a
        c = [a4 * a8 - a5 * a7, a2 * a7 - a1 * a8, a1 * a5 - a2 * a4,
             a5 * a6 - a3 * a8, a0 * a8 - a2 * a6, a2 * a3 - a0 * a5,
             a3 * a7 - a4 * a6, a1 * a6 - a0 * a7, a0 * a4 - a1 * a3]
        d = a0 * c[0] + a1 * c[3] + a2 * c[6]
        return [x / d for x in c]

    M = inv3(R.FIT_HUV)

    def S(u, v):
        d = M[6] * u + M[7] * v + M[8]
        return ((M[0] * u + M[1] * v + M[2]) / d,
                (M[3] * u + M[4] * v + M[5]) / d)

    h = 0.25
    ax, ay = S(128 - h, 128.0)
    bx, by = S(128 + h, 128.0)
    cx, cy = S(128.0, 128 - h)
    dx, dy = S(128.0, 128 + h)
    # one texel is one game unit: 1/854 of the frame across, 1/480 down
    horiz = math.hypot((bx - ax) / (2 * h), (by - ay) / (2 * h)) * 854.0
    vert = math.hypot((dx - cx) / (2 * h), (dy - cy) / (2 * h)) * 480.0
    assert 4.6 < horiz < 5.0, horiz          # 4.80x too wide
    assert 0.80 < vert < 1.00, vert          # 0.90x
    assert horiz / vert > 4.0, (horiz, vert)  # and they disagree 5.3 : 1,
    #                                          which no isotropic dial can fix


def test_the_disc_reaches_almost_to_the_horizon():
    """"further backwards towards the mountains than it should be" -- the
    vanishing line falls straight out of FIT_HUV's denominator."""
    import ff7nx_fbresample as R

    def inv3(a):
        (a0, a1, a2, a3, a4, a5, a6, a7, a8) = a
        c = [a4 * a8 - a5 * a7, a2 * a7 - a1 * a8, a1 * a5 - a2 * a4,
             a5 * a6 - a3 * a8, a0 * a8 - a2 * a6, a2 * a3 - a0 * a5,
             a3 * a7 - a4 * a6, a1 * a6 - a0 * a7, a0 * a4 - a1 * a3]
        d = a0 * c[0] + a1 * c[3] + a2 * c[6]
        return [x / d for x in c]

    a, b, c = R.FIT_HUV[6], R.FIT_HUV[7], R.FIT_HUV[8]
    M = inv3(R.FIT_HUV)

    def S(u, v):
        d = M[6] * u + M[7] * v + M[8]
        return ((M[0] * u + M[1] * v + M[2]) / d,
                (M[3] * u + M[4] * v + M[5]) / d)

    fx, fy = S(128.0, 255.0)                  # the far rim
    horizon = (a * fx + c) / -b
    assert 0.03 < horizon < 0.06, horizon     # ~33 px of 720
    assert fy > horizon                       # the rim stays below it ...
    assert (fy - horizon) * 720 < 200         # ... but only just
