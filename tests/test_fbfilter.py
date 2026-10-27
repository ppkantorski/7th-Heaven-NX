#!/usr/bin/env python3
"""tests/test_fbfilter.py -- the capture blit must not invent texels.

BUILD 312. The scene target is cleared to (0,0,0,0) and drawn with hard edges,
so every pixel in it is either (C, 255) or (0,0,0,0). The blit that fills the
capture surface sets a LINEAR sampler, which at a silhouette averages the two
and produces a texel that exists nowhere in the scene -- dark, partly covered,
and outside both categories the capture path handles. POINT sampling removes
that.

The 0-is-POINT reading is not assumed: the same vtable slot is called with
zeros in the sibling blit at +0x10DBEE8, and that witness is asserted.

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

import ff7nx_fbfilter as F                                     # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def test_the_two_stock_words_are_what_the_module_claims(img):
    for va, want in sorted(F.words_for(F.LINEAR).items()):
        got = struct.unpack_from('<I', img, va)[0]
        assert got == want, '+0x%X is %08X, expected %08X' % (va, got, want)
    assert F.verify(img) == []
    assert F.read_state(img) == 'capture blit LINEAR (stock)'


def test_every_anchor_around_the_call_is_intact(img):
    """The filter setter, its other three arguments, the address-mode call
    above it and the SetRenderTargets below -- so "this really is the
    scene -> capture blit" is checked, not assumed."""
    for va, want in sorted(F.ANCHORS.items()):
        got = struct.unpack_from('<I', img, va)[0]
        assert got == want, '+0x%X is %08X, expected %08X' % (va, got, want)


def test_the_point_witness_is_present_and_is_where_the_meaning_comes_from(img):
    """0 = POINT is read off the binary, not guessed: the SAME vtable slot is
    called with three zeros in the sibling blit. Without this the patch would
    be a coin flip between sharper and blurrier."""
    for va, want in sorted(F.POINT_WITNESS.items()):
        got = struct.unpack_from('<I', img, va)[0]
        assert got == want, '+0x%X is %08X, expected %08X' % (va, got, want)
    assert F.words_for(F.POINT)[F.MIN_SITE] == F.POINT_WITNESS[0x10DBEE8]
    assert F.words_for(F.POINT)[F.MAG_SITE] == F.POINT_WITNESS[0x10DBEEC]


def test_the_words_disassemble_to_what_the_module_says():
    capstone = pytest.importorskip('capstone')
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    want = {
        F.LINEAR: {F.MIN_SITE: 'mov w1, #1', F.MAG_SITE: 'mov w2, #1'},
        F.POINT: {F.MIN_SITE: 'mov w1, wzr', F.MAG_SITE: 'mov w2, wzr'},
    }
    for mode, texts in want.items():
        got = F.words_for(mode)
        for va, text in texts.items():
            ins = next(md.disasm(struct.pack('<I', got[va]), va))
            assert '%s %s' % (ins.mnemonic, ins.op_str) == text, (mode, hex(va))


def test_only_the_min_and_mag_arguments_move():
    """The mip filter and the three address-mode arguments are NOT touched:
    clamping is what keeps the blit from wrapping at the surface edge, and
    changing it would be a different bug entirely."""
    a, b = F.words_for(F.POINT), F.words_for(F.LINEAR)
    assert set(a) == set(b) == {F.MIN_SITE, F.MAG_SITE}
    assert 0x10DADA4 in F.ANCHORS and 0x10DAD7C in F.ANCHORS


def test_linear_is_the_stock_image_word_for_word():
    assert F.words_for(F.LINEAR) == {F.MIN_SITE: F.MIN_STOCK,
                                     F.MAG_SITE: F.MAG_STOCK}


def test_point_is_the_default_and_stock_is_reachable(monkeypatch):
    monkeypatch.delenv(F.FILTER_ENV, raising=False)
    assert F.filter_mode() == F.POINT
    assert F.enabled() is True
    for v in ('linear', 'stock', '1', 'ON'):
        monkeypatch.setenv(F.FILTER_ENV, v)
        assert F.filter_mode() == F.LINEAR
        assert F.enabled() is False
    for v in ('point', 'nearest', '0', 'off'):
        monkeypatch.setenv(F.FILTER_ENV, v)
        assert F.filter_mode() == F.POINT


def test_nonsense_falls_back_rather_than_writing_garbage(monkeypatch):
    for bad in ('2', 'banana', '', '-1'):
        monkeypatch.setenv(F.FILTER_ENV, bad)
        assert F.filter_mode() == F.DEFAULT_FILTER


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv(F.FILTER_ENV, 'point')
    out = tmp_path / 'main_filter'
    shutil.copy(MAIN, out)
    assert F.apply_all(out, log=lambda *_: None) == 0
    got = nxmap.Main(str(out)).img
    assert F.verify(got) == []
    assert F.installed(got) == F.POINT
    assert F.read_state(got) == 'capture blit POINT sampled'
    assert F.apply_all(out, log=lambda *_: None) == 0
    assert F.apply_all(out, revert=True, log=lambda *_: None) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()


def test_it_writes_those_two_words_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setenv(F.FILTER_ENV, 'point')
    out = tmp_path / 'main_scope'
    shutil.copy(MAIN, out)
    before = nxmap.Main(str(out)).img
    assert F.apply_all(out, log=lambda *_: None) == 0
    after = nxmap.Main(str(out)).img
    diff = {i for i in range(0, len(before), 4)
            if before[i:i + 4] != after[i:i + 4]}
    assert diff == {F.MIN_SITE, F.MAG_SITE}, [hex(x) for x in sorted(diff)]
    for va, want in sorted(F.ANCHORS.items()):
        assert struct.unpack_from('<I', after, va)[0] == want, hex(va)
    for va, want in sorted(F.POINT_WITNESS.items()):
        assert struct.unpack_from('<I', after, va)[0] == want, hex(va)


def test_it_refuses_when_the_blit_it_targets_has_moved(img):
    for va in list(F.ANCHORS)[:5] + list(F.POINT_WITNESS)[:2]:
        bad = bytearray(img)
        struct.pack_into('<I', bad, va, 0xD503201F)          # nop
        assert F.verify(bad) != [], hex(va)


def test_punch_is_the_default_again_because_311_undid_307(monkeypatch):
    """BUILD 311's `unpremul` copies a fully opaque texel byte for byte, which
    RE-ADMITS exactly the opaque-dark texels `punch` removes -- so it undid
    build 307 on hardware. POINT sampling and `punch` are the pair: the first
    stops blends being made, the second drops the void."""
    import ff7nx_fbresample as R
    monkeypatch.delenv(R.PROBE_ENV, raising=False)
    assert R.mode() == 'punch'
    monkeypatch.setenv(R.PROBE_ENV, 'unpremul')
    assert R.mode() == 'unpremul'
