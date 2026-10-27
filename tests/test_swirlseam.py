#!/usr/bin/env python3
"""tests/test_swirlseam.py -- the battle-entry swirl's vertical seam.

BUILD 315. The swirl's UVs use the PSX inclusive-texel convention
(`u1 = i*32 + 32 - 1`, and `- 2` in the column where the two half-textures
meet), while this renderer maps `u -> u / tex_format.width`. One UV unit falls
down the crack at every quad boundary; the one you see is the middle of the
screen, because that is where the character stands.

Build 314 closed the `u` endpoints with `sub #0` and could not touch `v`,
because `v2 = j*32 + 32` reaches 256 and would wrap the bottom row to zero.
This build replaces both with ONE word that subtracts only when the result
would not fit the byte -- `sub wD, wD, wD, lsr #8` -- so both axes close and
the bottom edge saturates at 255 instead of wrapping.

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

import ff7nx_swirlseam as S                                    # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')

U1, U3 = 0x14, 0x24
V_FIELDS = (0x0D, 0x15, 0x1D, 0x25)


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


SUB_IMM = 0xFFC00000, 0x51000000


def test_the_fourteen_stock_words_are_what_the_module_claims(img):
    for va, (stock, _field) in sorted(S.SITES.items()):
        got = _w(img, va)
        assert got == stock, '+0x%X is %08X, expected %08X' % (va, got, stock)
    assert S.verify(img) == []
    assert S.read_state(img).startswith('swirl seam stock')


def test_both_axes_are_covered_and_nothing_is_left_open():
    """BUILD 314 patched only `u`, which is why it was a partial repair. Both
    UV endpoints of every quad are here now."""
    kinds = sorted(f for _s, f in S.SITES.values())
    assert kinds.count('u1') == 4 and kinds.count('v2') == 4
    assert kinds.count('u3') == 3 and kinds.count('v3') == 3
    assert len(S.SITES) == 14


def test_every_site_is_a_sub_immediate_with_one_register(img):
    """The replacement is `sub wD, wD, wD, lsr #8`, which is only equivalent if
    the stock instruction already has the same register as source and
    destination."""
    for va, (stock, _f) in sorted(S.SITES.items()):
        assert (stock & SUB_IMM[0]) == SUB_IMM[1], hex(va)
        assert (stock & 31) == ((stock >> 5) & 31), hex(va)
        assert ((stock >> 10) & 0xFFF) in (1, 2), hex(va)


def test_the_saturating_subtract_does_what_the_header_says():
    """`wD - (wD >> 8)`: nothing below 256, exactly one at 256. That is the
    whole rule, and it is what lets u and v share a single word."""
    for v in (0, 1, 32, 64, 128, 160, 224, 254, 255):
        assert v - (v >> 8) == v, v
    assert 256 - (256 >> 8) == 255
    # the two values that actually occur
    assert 4 * 32 + 32 == 160                      # u1 / u3 peak
    assert 7 * 32 + 32 == 256                      # v2 / v3 peak, the wrap
    assert 160 <= 255 and 256 > 255


def test_the_replacement_keeps_the_register_and_the_width():
    for va, (stock, _f) in sorted(S.SITES.items()):
        got = S.patched_word(stock)
        rd = stock & 31
        assert (got & 31) == rd, hex(va)
        assert ((got >> 5) & 31) == rd, hex(va)
        assert ((got >> 16) & 31) == rd, hex(va)
        assert (got & 0xFF200000) == 0x4B000000, hex(va)   # SUB shifted, 32-bit
        assert ((got >> 22) & 3) == 1, hex(va)             # LSR
        assert ((got >> 10) & 0x3F) == 8, hex(va)          # by 8


def test_patched_word_refuses_anything_that_is_not_that_shape():
    for bad in (0xD503201F, 0x51000509, 0x11000508):
        with pytest.raises(ValueError):
            S.patched_word(bad)


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path):
    out = tmp_path / 'main_seam'
    shutil.copy(MAIN, out)
    assert S.apply_all(out, log=lambda *_: None) == 0
    got = nxmap.Main(str(out)).img
    assert S.verify(got) == []
    assert S.installed(got) is True
    assert S.read_state(got) == 'swirl seam closed'
    assert S.apply_all(out, log=lambda *_: None) == 0
    assert S.apply_all(out, revert=True, log=lambda *_: None) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()


def test_it_writes_those_fourteen_words_and_nothing_else(tmp_path):
    out = tmp_path / 'main_scope'
    shutil.copy(MAIN, out)
    before = nxmap.Main(str(out)).img
    assert S.apply_all(out, log=lambda *_: None) == 0
    after = nxmap.Main(str(out)).img
    diff = {i for i in range(0, len(before), 4)
            if before[i:i + 4] != after[i:i + 4]}
    assert diff == set(S.SITES), [hex(x) for x in sorted(diff)]
    for va, want in sorted(S.ANCHORS.items()):
        assert _w(after, va) == want, hex(va)


def test_it_refuses_when_the_uv_writer_has_moved(img):
    for va in list(S.ANCHORS)[:6]:
        bad = bytearray(img)
        struct.pack_into('<I', bad, va, 0xD503201F)          # nop
        assert S.verify(bad) != [], hex(va)


def test_a_mixed_state_is_refused_rather_than_half_applied(img):
    bad = bytearray(img)
    va = sorted(S.SITES)[0]
    struct.pack_into('<I', bad, va, S.patched_word(S.SITES[va][0]))
    assert S.installed(bad) is None
    assert S.verify(bad) != []


def test_the_env_switch(monkeypatch):
    monkeypatch.delenv(S.SEAM_ENV, raising=False)
    assert S.enabled() is True
    for v in ('0', 'off', 'stock', 'no'):
        monkeypatch.setenv(S.SEAM_ENV, v)
        assert S.enabled() is False
    monkeypatch.setenv(S.SEAM_ENV, '1')
    assert S.enabled() is True
