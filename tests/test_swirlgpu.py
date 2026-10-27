#!/usr/bin/env python3
"""tests/test_swirlgpu.py -- the swirl off the CPU readback.

BUILD 318. The capture loader branches on `field_0` (+0x10D70AC, `cmp w8, #1`)
into either a byte-wise CPU copy or a GPU render-to-texture, and the swirl's
creator already submits each half twice -- once on each path. Sending the two
CPU submissions down the GPU path removes the readback from battle entry at any
surface scale, and leaves Kujata (which keeps `field_0 = 1`) alone.

The replacement word is the game's own: the other two submissions of the same
two rects already write `field_0 = 0` with `str wzr, [x0]`, and that is
asserted here rather than assumed.

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

import ff7nx_swirlgpu as G                                     # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')

SELECTOR = 0x10D70AC               # ldr w8, [x20, #0x24]
SELECTOR_CMP = 0x10D70B0           # cmp w8, #1


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


def test_the_two_stock_words_are_what_the_module_claims(img):
    for va in G.SITES:
        got = _w(img, va)
        assert got == G.STORE_W8, '+0x%X is %08X' % (va, got)
    assert G.verify(img) == []
    assert G.read_state(img).startswith('swirl captures on the CPU')


def test_every_anchor_that_proves_the_four_submissions(img):
    for va, want in sorted(G.ANCHORS.items()):
        got = _w(img, va)
        assert got == want, '+0x%X is %08X, expected %08X' % (va, got, want)


def test_the_replacement_is_the_games_own_encoding(img):
    """`str wzr, [x0]` is lifted from the two submissions that already ask for
    the GPU path, not invented. If those moved, the module must refuse."""
    for va in G.WITNESS:
        assert _w(img, va) == G.STORE_WZR, hex(va)
    assert G.words_for(True) == {va: G.STORE_WZR for va in G.SITES}
    assert G.words_for(False) == {va: G.STORE_W8 for va in G.SITES}


def test_the_selector_this_depends_on_is_intact(img):
    """`field_0 == 1` is what chooses the CPU readback. If that test moved, a
    zero in the field would not mean what this module needs it to mean."""
    assert _w(img, SELECTOR) == 0xB9402688       # ldr w8, [x20, #0x24]
    w = _w(img, SELECTOR_CMP)
    # ff7nx_fbpath may replace this with a branch into its own gate; either is
    # legitimate, anything else means the loader moved.
    assert w == 0x7100051F or (w & 0xFC000000) == 0x14000000, '%08X' % w


def test_the_two_patched_sites_are_the_field_zero_stores_and_not_the_rects(img):
    """Each site must be the store immediately after an `add w0, wN, #0xc8`
    and a translate call -- i.e. the field_0 write, not one of the rect
    writes at +0xDC..+0xE8."""
    for va in G.SITES:
        assert _w(img, va - 0x10) == 0x11032100, hex(va)   # add w0, w8, #0xc8
        assert (_w(img, va - 8) & 0xFC000000) == 0x94000000  # bl translate
        assert _w(img, va - 4) == 0x320003E8               # mov w8, #1


def test_kujata_is_not_in_the_patch_set():
    """Kujata's capture keeps field_0 = 1 and stays on the CPU path, which is
    where `punch`, the resample and the window clamp all live. Its creator is
    guest 0x500935, nowhere near these two addresses."""
    for va in G.SITES:
        assert not 0x473860 <= va <= 0x473D30, hex(va)
    assert len(G.SITES) == 2


def test_the_env_switch(monkeypatch):
    monkeypatch.delenv(G.GPU_ENV, raising=False)
    assert G.enabled() is True
    for v in ('0', 'off', 'cpu', 'stock', 'no'):
        monkeypatch.setenv(G.GPU_ENV, v)
        assert G.enabled() is False
    monkeypatch.setenv(G.GPU_ENV, '1')
    assert G.enabled() is True


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path):
    out = tmp_path / 'main_gpu'
    shutil.copy(MAIN, out)
    assert G.apply_all(out, log=lambda *_: None) == 0
    got = nxmap.Main(str(out)).img
    assert G.verify(got) == []
    assert G.installed(got) is True
    assert G.read_state(got) == 'swirl captures on the GPU path'
    assert G.apply_all(out, log=lambda *_: None) == 0
    assert G.apply_all(out, revert=True, log=lambda *_: None) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()


def test_it_writes_those_two_words_and_nothing_else(tmp_path):
    out = tmp_path / 'main_scope'
    shutil.copy(MAIN, out)
    before = nxmap.Main(str(out)).img
    assert G.apply_all(out, log=lambda *_: None) == 0
    after = nxmap.Main(str(out)).img
    diff = {i for i in range(0, len(before), 4)
            if before[i:i + 4] != after[i:i + 4]}
    assert diff == set(G.SITES), [hex(x) for x in sorted(diff)]
    for va, want in sorted(G.ANCHORS.items()):
        assert _w(after, va) == want, hex(va)


def test_it_refuses_when_the_creator_has_moved(img):
    for va in list(G.ANCHORS) + list(G.SITES):
        bad = bytearray(img)
        struct.pack_into('<I', bad, va, 0xD503201F)          # nop
        assert G.verify(bad) != [], hex(va)


def test_a_mixed_state_is_refused(img):
    bad = bytearray(img)
    struct.pack_into('<I', bad, G.SITES[0], G.STORE_WZR)
    assert G.installed(bad) is None
    assert G.verify(bad) != []
