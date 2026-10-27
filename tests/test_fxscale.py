#!/usr/bin/env python3
"""tests/test_fxscale.py -- the animated-field disc scale, two words in place.

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

import arm64emu                                                # noqa: E402
import ff7nx_fxscale as S                                      # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def test_the_stock_words_and_anchors_are_what_the_module_claims(img):
    assert struct.unpack_from('<I', img, S.SHIFT_SITE)[0] == S.SHIFT_STOCK
    assert struct.unpack_from('<I', img, S.OUTER_SITE)[0] == S.OUTER_STOCK
    assert S.verify(img) == []
    assert S.read_state(img) == 'ring radius 384*j, outer 12192 (stock)'


def _exec(word, reg, preset=None):
    cpu = arm64emu.Cpu(arm64emu.Mem())
    if preset:
        for r, v in preset.items():
            cpu.set(r, v)
    cpu.run(0x1000, [word])
    return cpu.get(reg)


def test_the_stock_shift_really_builds_384j():
    """Positive control: execute the word already in the binary."""
    assert _exec(S.SHIFT_STOCK, 26, {8: 3 * 31}) == 384 * 31
    assert _exec(S.OUTER_STOCK, 23) == 12192


def test_every_scale_keeps_the_outer_ring_matching_its_own_texel_size():
    """The j = 32 ring is 127 texels out. If the two words disagree, the last
    ring's geometry and its UVs describe different circles."""
    for s in S.LEGAL:
        w_shift, w_outer = S.words_for(s)
        R = _exec(w_shift, 26, {8: 3 * 31})
        per_texel = R / (4 * 31)
        assert per_texel == int(per_texel)
        assert _exec(w_outer, 23) == 127 * per_texel


def test_the_scales_are_the_documented_factors():
    assert {s: S.outer_for(s) for s in S.LEGAL} == {5: 3048, 6: 6096, 7: 12192}
    assert S.DEFAULT_SHIFT in S.LEGAL
    assert S.STOCK_SHIFT == 7


def test_stock_keeps_its_own_encodings_so_revert_is_byte_identical():
    assert S.words_for(S.STOCK_SHIFT) == (S.SHIFT_STOCK, S.OUTER_STOCK)


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path, monkeypatch):
    for s in S.LEGAL:
        monkeypatch.setenv(S.SCALE_ENV, str(s))
        out = tmp_path / ('main%d' % s)
        shutil.copy(MAIN, out)
        assert S.apply_all(out, log=lambda *_: None) == 0
        got = nxmap.Main(str(out)).img
        assert S.verify(got) == []
        assert str(S.outer_for(s)) in S.read_state(got)
        assert S.apply_all(out, log=lambda *_: None) == 0       # idempotent
        assert S.apply_all(out, revert=True, log=lambda *_: None) == 0
        assert out.read_bytes() == open(MAIN, 'rb').read()


def test_stock_scale_turns_the_module_off(monkeypatch):
    monkeypatch.setenv(S.SCALE_ENV, '7')
    assert S.enabled() is False
    monkeypatch.setenv(S.SCALE_ENV, '6')
    assert S.enabled() is True


def test_nonsense_falls_back_rather_than_writing_garbage(monkeypatch):
    for bad in ('0', '9', '31', 'banana', ''):
        monkeypatch.setenv(S.SCALE_ENV, bad)
        assert S.shift() in S.LEGAL


def test_it_refuses_when_the_neighbourhood_moved(img):
    bad = bytearray(img)
    struct.pack_into('<I', bad, 0x473244, 0xD503201F)
    assert S.verify(bad) != []
