"""BUILD 737: the battle Pause window, vertically centred.

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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import capstone                                                # noqa: E402
import ff7nx_pausepos as P                                     # noqa: E402

MODULE = os.path.join(ROOT, 'dump', 'exefs', 'main')
need_dump = pytest.mark.skipif(not os.path.exists(MODULE),
                               reason='needs the dumped main')
MD = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)


def dis(word):
    i = next(MD.disasm(struct.pack('<I', word), 0))
    return i.mnemonic + ' ' + i.op_str


def test_the_box_lands_mid_gap_in_both_spaces():
    assert P.SHIFT_640 == 39 and P.SHIFT_320 == 19
    assert abs(P.BOX_CENTRE_PX + 1.5 * P.SHIFT_640 - P.TARGET_PX) <= 1
    assert abs(P.BOX_CENTRE_PX + 3 * P.SHIFT_320 - P.TARGET_PX) <= 1


def test_the_words_are_movs_of_w8():
    for _va, y, d, _what in P.SITES:
        assert dis(P.movz_w8(y)) == 'mov w8, #%#x' % y
        assert dis(P.movz_w8(y + d)) == 'mov w8, #%#x' % (y + d)


@need_dump
def test_stock_module(tmp_path):
    t = P._text(MODULE)
    assert not P.check_anchors(t, log=lambda *_: None)
    assert P.state(t) == 'stock'


@need_dump
def test_apply_is_exactly_four_words_and_reverts(tmp_path):
    import nxmap
    out = tmp_path / 'main'
    shutil.copy(MODULE, out)
    before = nxmap.Main(str(out)).text
    assert P.apply(out, log=lambda *_: None) == 0
    after = nxmap.Main(str(out)).text
    diff = [va for va in range(0, len(before), 4)
            if before[va:va + 4] != after[va:va + 4]]
    assert diff == sorted(va for va, _, _, _ in P.SITES)
    assert P.state(after) == 'installed'
    assert P.apply(out, log=lambda *_: None) == 0          # idempotent
    assert P.apply(out, revert=True, log=lambda *_: None) == 0
    assert nxmap.Main(str(out)).text == before


def test_env_switch():
    assert P.enabled({})
    assert not P.enabled({P.PAUSEPOS_ENV: '0'})
