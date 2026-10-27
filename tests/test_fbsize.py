#!/usr/bin/env python3
"""tests/test_fbsize.py -- the capture's texel count, two shifts in place.

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
import ff7nx_fbsize as S                                       # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def test_the_stock_words_and_anchors_are_what_the_module_claims(img):
    assert struct.unpack_from('<I', img, S.H_SITE)[0] == S.H_STOCK
    assert struct.unpack_from('<I', img, S.W_SITE)[0] == S.W_STOCK
    assert S.verify(img) == []
    assert S.read_state(img) == 'capture 1x (stock)'


def _divides(word, reg, numerator):
    """Execute the shift and report what the division produced."""
    cpu = arm64emu.Cpu(arm64emu.Mem())
    cpu.set(reg, numerator)
    cpu.run(0x1000, [word])
    return cpu.get(reg)


def test_the_stock_shifts_really_divide_by_640_and_480():
    """Positive control, on the two words already in the binary.

    make_framebuffer_tex computes w*640 then divides by 640, and h*480 then
    divides by 480 -- identities at the port's own 640x480. Feed the same
    numerators and check the stock words return w and h unchanged.
    """
    magic_w, magic_h = 0xCCCCCCCD, 0x88888889
    for w in (64, 128, 192, 256):
        assert _divides(S.W_STOCK, 9, (w * 640) * magic_w) == w
    for h in (128, 256):
        assert _divides(S.H_STOCK, 8, (h * 480) * magic_h) == h


def test_every_size_multiplies_by_exactly_its_factor():
    magic_w, magic_h = 0xCCCCCCCD, 0x88888889
    for k in S.LEGAL:
        hw, ww = S.words_for(k)
        for w in (64, 128, 256):
            assert _divides(ww, 9, (w * 640) * magic_w) == w * k
        for h in (128, 256):
            assert _divides(hw, 8, (h * 480) * magic_h) == h * k


def test_stock_keeps_its_own_encodings_so_revert_is_byte_identical():
    assert S.words_for(1) == (S.H_STOCK, S.W_STOCK)


def test_the_origin_shifts_next_door_are_never_touched(tmp_path, monkeypatch):
    """fb_tex.x and fb_tex.y stay in staging coordinates. Their shifts sit one
    and two words away from the ones this module writes -- close enough that
    "we only touch two addresses" is worth checking against the bytes rather
    than trusting plan()."""
    for k in S.LEGAL:
        monkeypatch.setenv(S.SIZE_ENV, str(k))
        out = tmp_path / ('main_origin%d' % k)
        shutil.copy(MAIN, out)
        before = nxmap.Main(str(out)).img
        untouched = {va: struct.unpack_from('<I', before, va)[0]
                     for va in (0x10DBC70, 0x10DBC74, 0x10DBC78, 0x10DBC90,
                                0x10DBC94)}
        assert S.apply_all(out, log=lambda *_: None) == 0
        after = nxmap.Main(str(out)).img
        for va, want in untouched.items():
            assert struct.unpack_from('<I', after, va)[0] == want, hex(va)
        # and exactly two words in the whole image changed
        diff = [i for i in range(0, len(before), 4)
                if before[i:i + 4] != after[i:i + 4]]
        assert diff == ([] if k == 1 else [S.H_SITE, S.W_SITE]), diff


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path, monkeypatch):
    for k in S.LEGAL:
        monkeypatch.setenv(S.SIZE_ENV, str(k))
        out = tmp_path / ('main%d' % k)
        shutil.copy(MAIN, out)
        assert S.apply_all(out, log=lambda *_: None) == 0
        got = nxmap.Main(str(out)).img
        assert S.verify(got) == []
        assert S.read_state(got).startswith('capture %dx' % k)
        assert S.apply_all(out, log=lambda *_: None) == 0        # idempotent
        assert S.apply_all(out, revert=True, log=lambda *_: None) == 0
        assert out.read_bytes() == open(MAIN, 'rb').read()


def test_stock_size_turns_the_module_off(monkeypatch):
    monkeypatch.setenv(S.SIZE_ENV, '1')
    assert S.enabled() is False
    monkeypatch.setenv(S.SIZE_ENV, '2')
    assert S.enabled() is True


def test_nonsense_falls_back_rather_than_writing_garbage(monkeypatch):
    for bad in ('0', '3', '8', 'banana', ''):
        monkeypatch.setenv(S.SIZE_ENV, bad)
        assert S.size() in S.LEGAL


def test_the_resample_gate_no_longer_follows_this_retracted_module():
    """RETRACTED, build 278. The cave's shift now comes from ff7nx_fxcapscale,
    which scales ONE capture. If this module ever drove it again it would scale
    all twenty-five and break the swirl a second time."""
    import inspect

    import ff7nx_fbresample as F
    src = inspect.getsource(F.size_shift)
    assert 'import ff7nx_fxcapscale' in src
    assert 'import ff7nx_fbsize' not in src


def test_it_refuses_when_the_neighbourhood_moved(img):
    bad = bytearray(img)
    struct.pack_into('<I', bad, 0x10DBC94, 0xD503201F)
    assert S.verify(bad) != []
