"""tests/test_hextarm.py -- HEXT constants translated to ARM64 (BUILD 866).

Needs the dump (ff7_en + exefs/main); skipped without it.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import os

import pytest

import build
import ff7nx_hextarm as H

DUMP = build.find_game_dump(nxpaths.ROOT)
pytestmark = pytest.mark.skipif(
    DUMP is None or not DUMP.exe or not DUMP.nso or not H.available(),
    reason='needs the game dump and capstone')


def _tools():
    import nxmap
    return open(DUMP.exe, 'rb').read(), nxmap.Main(DUMP.nso)


# PS3Tweaks StatusSpeedMult/2x, verbatim
STATUS_2X = """
00435335 = 0C
00435345 = 0F
00435375 = 0B
00435386 = 07
004353BC = 0E
004353CD = 3F
"""


def test_status_speed_second_pair_only():
    exe, main = _tools()
    words = {o: (s, n) for o, s, n, _ in H.plan(exe, main, STATUS_2X)}
    # the SECOND sar 0xd / and 0x1fff pair (x86 0x435333), not the first
    assert words == {
        0x98348: (0x130D7D19, 0x130C7D19), 0x98380: (0x12003108, 0x12002D08),
        0x98434: (0x130C7D19, 0x130B7D19), 0x9846C: (0x12002D08, 0x12002908),
        0x98538: (0x130F7D19, 0x130E7D19), 0x98570: (0x12003908, 0x12003508)}


def test_shift_nopped_is_shift_zero():
    exe, main = _tools()
    (o, s, n, _), = H.plan(exe, main, '005C6F01 = 90 90')
    assert (o, s, n) == (0x7F5634, 0x13017D1A, 0x13007D1A)


def test_register_constant_refused():
    # mov ecx, 0x64 feeds an idiv the compiler turned into a magic multiply
    exe, main = _tools()
    with pytest.raises(H.Refused):
        H.plan(exe, main, '005CA933 = B9 32 00 00 00')


def test_cave_jump_refused():
    exe, main = _tools()
    with pytest.raises(H.Refused):
        H.plan(exe, main, '005C77D0 = E8 5B E2 1E 00 90 90')


def test_data_patch_refused():
    exe, main = _tools()
    with pytest.raises(H.Refused):
        H.plan(exe, main, '00924E60 = E8 03 00 00')
