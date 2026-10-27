#!/usr/bin/env python3
"""tests/test_fxrim.py -- the outer-ring UV clamp, one word in place.

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
import ff7nx_fxrim as R                                        # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def test_the_stock_word_is_the_clamp_this_module_claims(img):
    assert struct.unpack_from('<I', img, R.HOOK)[0] == R.STOCK
    assert R.verify(img) == []
    assert R.read_state(img) == 'rim cap 127 (stock)'


def test_the_stock_encoding_really_loads_127():
    """Positive control: execute the word already in the binary."""
    cpu = arm64emu.Cpu(arm64emu.Mem())
    cpu.set(R.REG, 0xDEADBEEF)
    cpu.run(0x1000, [R.STOCK])
    assert cpu.get(R.REG) == 127


def test_every_legal_cap_really_loads_that_value():
    for c in R.LEGAL:
        cpu = arm64emu.Cpu(arm64emu.Mem())
        cpu.set(R.REG, 0xDEADBEEF)
        cpu.run(0x1000, [R.word_for(c)])
        assert cpu.get(R.REG) == c


def test_no_legal_cap_can_invert_the_outer_ring():
    """r0 = 4*j is not clamped and is 124 on the ring this clamp serves, so
    every accepted cap must be >= 124 or the ring's UVs run backwards."""
    assert min(R.LEGAL) >= 124
    assert R.DEFAULT_CAP in R.LEGAL


def test_stock_keeps_its_own_encoding_so_revert_is_byte_identical():
    assert R.word_for(127) == R.STOCK


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path, monkeypatch):
    for c in R.LEGAL:
        monkeypatch.setenv(R.RIM_ENV, str(c))
        out = tmp_path / ('main%d' % c)
        shutil.copy(MAIN, out)
        assert R.apply_all(out, log=lambda *_: None) == 0
        got = nxmap.Main(str(out)).img
        assert R.read_state(got).startswith('rim cap %d' % c)
        assert R.verify(got) == []
        assert R.apply_all(out, log=lambda *_: None) == 0        # idempotent
        assert R.apply_all(out, revert=True, log=lambda *_: None) == 0
        assert out.read_bytes() == open(MAIN, 'rb').read()


def test_stock_cap_turns_the_module_off(monkeypatch):
    monkeypatch.setenv(R.RIM_ENV, '127')
    assert R.enabled() is False
    monkeypatch.setenv(R.RIM_ENV, '124')
    assert R.enabled() is True


def test_nonsense_falls_back_rather_than_writing_garbage(monkeypatch):
    for bad in ('0', '64', '200', 'banana', ''):
        monkeypatch.setenv(R.RIM_ENV, bad)
        assert R.cap() in R.LEGAL


def test_it_refuses_when_the_neighbourhood_moved(img):
    bad = bytearray(img)
    struct.pack_into('<I', bad, R.HOOK - 4, 0xD503201F)         # nop an anchor
    assert R.verify(bad) != []
