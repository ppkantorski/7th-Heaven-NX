#!/usr/bin/env python3
"""tests/test_fxdisc.py -- a fine percentage on Kujata's floor disc.

The point of the module is that the disc scales about its OWN centre, so the
image cannot slide sideways the way a capture-side span change does. That is a
property of the arithmetic (R = K*j for every ring j, with j = 0 at the
centre), and it is what these execute.

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
import shutil
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as E                                           # noqa: E402
import ff7nx_fxdisc as D                                       # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')

BASE = 0xD00000


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def _radius(j, pct):
    """Execute the cave for ring j and report the radius it produces."""
    cpu = E.Cpu(E.Mem())
    cpu.set(D.J3_REG, 3 * j)               # w8 = 3j, as +0x473244 leaves it
    cpu.set(D.R_REG, 0xBADF00D)            # w26 is dead on entry; poison it
    addrs = [BASE + 4 * i for i in range(D.N_WORDS)]
    code = dict(zip(addrs, D.body_words(lambda i: addrs[i], pct)))
    pc = cpu.run(BASE, None, code=code, start_pc=BASE)
    assert pc == D.RETURN_VA, hex(pc)
    return cpu.get(D.R_REG)


def test_the_stock_words_and_the_neighbourhood_are_what_the_module_claims(img):
    assert struct.unpack_from('<I', img, D.HOOK)[0] == D.HOOK_STOCK
    assert struct.unpack_from('<I', img, D.OUTER_SITE)[0] == D.OUTER_STOCK
    assert D.verify(img) == []
    assert D.read_state(img) == 'disc radius 384*j, outer 12192 (100%, stock)'


def test_one_hundred_per_cent_reproduces_the_stock_radius_exactly():
    """The stock instruction is `lsl w26, w8, #7`, i.e. R = 3j * 128 = 384j."""
    for j in range(0, 33):
        assert _radius(j, 100) == 384 * j


def test_the_disc_scales_about_its_own_centre():
    """Ring 0 is the centre. R = K*j is zero there for EVERY K, so no scale
    can move the centre -- which is the whole reason this is the right side to
    correct on. A capture-side span change pivots on the rect's left edge and
    slides the picture instead."""
    for pct in (25, 50, 75, 90, 100, 120, 150):
        assert _radius(0, pct) == 0


def test_every_percentage_scales_every_ring_by_the_same_factor():
    """BUILD 321 keeps the multiplier in 8.8 fixed point and shifts the product
    down, so a ring is `(3j * K8) >> 8` rather than `j * ((3*K8) >> 8)`. Those
    agree exactly when the shift divides evenly and otherwise differ by the
    truncation -- at most one world unit, never more."""
    for pct in (50, 75, 90, 110, 150):
        per_ring = D.radius_per_ring(D.k_for(pct))
        for j in (1, 2, 8, 16, 31, 32):
            got = _radius(j, pct)
            assert abs(got - per_ring * j) <= j, (pct, j, got)
            assert got == (3 * j * D.k_for(pct)) >> D.SCALE_BITS, (pct, j)


def test_the_dial_is_fractional_and_a_half_per_cent_is_a_different_build():
    """The point of build 321. The old cave held an integer 128*pct/100, so
    the dial moved in 0.78% steps and 93.5 installed the same word as 94."""
    assert D.k_for(93.5) != D.k_for(94.0)
    assert D.radius_per_ring(D.k_for(93.5)) != D.radius_per_ring(D.k_for(94.0))
    # and what "94" has actually been installing all along
    assert D.radius_per_ring(D.k_for(94.0)) == 360
    assert D.pct_of(D.k_for(93.75)) == 93.75
    # the resolution is one world unit per ring, about 0.26%
    steps = sorted({D.radius_per_ring(D.k_for(94.0 + n / 100.0))
                    for n in range(-40, 41)})
    assert len(steps) >= 3
    assert all(b - a == 1 for a, b in zip(steps, steps[1:]))


def test_a_fractional_setting_round_trips_through_the_env(monkeypatch):
    for text, want in (('93.5', 93.5), ('94', 94.0), ('93.75', 93.75),
                       ('100', 100.0)):
        monkeypatch.setenv(D.DISC_ENV, text)
        assert D.percent() == want
    monkeypatch.setenv(D.DISC_ENV, '93.5')
    assert D.enabled() is True


def test_the_widescreen_ratio_is_the_derived_setting():
    """854/640 is the amount the frame gained; 640/854 undoes it. FFNx applies
    exactly this constant, as a multiplier on the drawn quad
    (animations.cpp:1071), which is what this module is copying."""
    assert round(100 * 640 / 854) == 75
    assert D.radius_per_ring(D.k_for(75)) == 288
    assert D.outer_for(D.k_for(75)) == 9144
    # and the 127-ring identity the stock word already satisfies
    assert D.outer_for(D.k_for(100)) == 0x2FA0


def test_the_outer_ring_tracks_the_radius(img):
    for pct in (50, 75, 90, 100, 150):
        k = D.k_for(pct)
        assert D.outer_for(k) == 127 * D.radius_per_ring(k) // 4
        assert 0 < D.outer_for(k) <= 0xFFFF


def test_nonsense_falls_back_rather_than_writing_garbage(monkeypatch):
    for bad in ('0', '24', '151', 'banana', '', '24.99', '150.01', 'nan'):
        monkeypatch.setenv(D.DISC_ENV, bad)
        assert D.percent() == D.DEFAULT_PERCENT


def test_the_default_preserves_the_authored_geometry(monkeypatch):
    """BUILD 362. The old 94% default relied on a repeated field-texture
    motif outside Kujata's actual capture window. With no valid measurement
    supporting a mesh change, an ordinary build must leave both stock words
    untouched."""
    monkeypatch.delenv(D.DISC_ENV, raising=False)
    assert D.percent() == 100
    assert D.enabled() is False           # 100 writes nothing at all
    monkeypatch.setenv(D.DISC_ENV, '94')
    assert D.enabled() is True            # the old value remains opt-in


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path, monkeypatch):
    for pct in (75, 90, 93.5, 93.75):
        monkeypatch.setenv(D.DISC_ENV, str(pct))
        out = tmp_path / ('main_disc%s' % str(pct).replace('.', '_'))
        shutil.copy(MAIN, out)
        assert D.apply_all(out, log=lambda *_: None) == 0
        got = nxmap.Main(str(out)).img
        assert D.verify(got) == []
        assert D.installed_k8(got) == D.k_for(pct)
        assert D.apply_all(out, log=lambda *_: None) == 0
        assert D.apply_all(out, revert=True, log=lambda *_: None) == 0
        assert out.read_bytes() == open(MAIN, 'rb').read()


def test_it_writes_the_two_sites_and_its_cave_and_nothing_else(tmp_path,
                                                               monkeypatch):
    monkeypatch.setenv(D.DISC_ENV, '75')
    out = tmp_path / 'main_scope'
    shutil.copy(MAIN, out)
    before = nxmap.Main(str(out)).img
    assert D.apply_all(out, log=lambda *_: None) == 0
    after = nxmap.Main(str(out)).img
    diff = [i for i in range(0, len(before), 4)
            if before[i:i + 4] != after[i:i + 4]]
    logical, physical = D._walk(after)
    assert len(logical) == D.N_WORDS
    assert set(diff) == set(physical) | {D.HOOK, D.OUTER_SITE}
    for va in sorted(D.ANCHORS):
        assert struct.unpack_from('<I', after, va)[0] == D.ANCHORS[va], hex(va)


def test_the_installed_cave_executes_the_same_way_as_the_generator(
        tmp_path, monkeypatch):
    monkeypatch.setenv(D.DISC_ENV, '75')
    out = tmp_path / 'main_roundtrip'
    shutil.copy(MAIN, out)
    assert D.apply_all(out, log=lambda *_: None) == 0
    img = nxmap.Main(str(out)).img
    addrs, _ = D._walk(img)
    words = [struct.unpack_from('<I', img, a)[0] for a in addrs]
    assert words == D.body_words(lambda i: addrs[i], 75)


def test_it_refuses_when_the_ring_builder_moved(img):
    for va in (0x473244, 0x47324C, 0x473230):
        bad = bytearray(img)
        struct.pack_into('<I', bad, va, 0xD503201F)
        assert D.verify(bad) != [], hex(va)
