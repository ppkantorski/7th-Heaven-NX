"""BUILD 538-540: minigame widescreen words (chocobo, highway, snowboard).

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
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import ff7nx_minifade as M  # noqa: E402
import nso_patcher  # noqa: E402

MAIN = os.path.join(HERE, 'dump', 'exefs', 'main')
EXE = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'resources', 'ff7_1.02',
                   'ff7_en')


def _t(path):
    return M._text(nso_patcher.read_nso(nso_patcher.Path(path)))


def test_stock_anchors_and_state():
    t = _t(MAIN)
    M.check_anchors(t, MAIN)
    assert all(M.group_state(t, g) == 'stock' for g in M.GROUPS)


def test_apply_revert_roundtrip():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, 'main')
        shutil.copy(MAIN, p)
        assert M.apply(p, log=lambda *_: None) == 0
        t = _t(p)
        assert all(M.group_state(t, g) == 'applied' for g in M.GROUPS)
        M.check_anchors(t, p)          # anchors hold on the patched module
        once = open(p, 'rb').read()
        assert M.apply(p, log=lambda *_: None) == 0
        assert open(p, 'rb').read() == once
        assert M.apply(p, revert=True, log=lambda *_: None) == 0
        assert _t(p) == _t(MAIN)
    finally:
        shutil.rmtree(d)


def test_snowboard_values():
    assert M.SNOW_TILE == 214 and M.SNOW_SKY_W == 747
    words = {va: new for g, (_x, _a, s) in M.GROUPS.items()
             if g.startswith('snowboard') for _n, va, _s, new, _an in s}
    assert len(words) == 13


def test_exe_float_roundtrip():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, 'ff7_en')
        shutil.copy(EXE, p)
        assert M.apply_exe(p, log=lambda *_: None) == 0
        data = open(p, 'rb').read()
        off = M._pe_off(data, 0x7B7DB8)
        assert struct.unpack_from('<f', data, off)[0] == -107.0
        diff = [i for i, (a, b) in enumerate(zip(open(EXE, 'rb').read(),
                                                 data)) if a != b]
        assert all(off <= i < off + 4 for i in diff)
        assert M.apply_exe(p, revert=True, log=lambda *_: None) == 0
        assert open(p, 'rb').read() == open(EXE, 'rb').read()
    finally:
        shutil.rmtree(d)


def test_submarine_fill_words():
    g = M.GROUPS['submarine time-limit fill']
    assert g[0] == 0x7916F7 and g[1] == 0x1077050
    new = {n: w for n, _va, _s, w, _a in g[2]}
    assert new['width'] == 0x52806AC8            # mov w8, #854
    assert new['x value'] == 0x12800D4B          # mov w11, #-107
    assert new['x store'] == 0xB900000B          # str w11, [x0]


def test_coaster_fade_w9_dead_after_each_site():
    # BUILD 562: w9 is written by the new words; nothing may read the old w9
    # before the translator call or a fresh write.
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    t = _t(MAIN)
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    g = M.GROUPS['coaster end fade'][2]
    last = {}
    for _n, va, _s, new, _a in g:
        last[va] = new
    for start in (0x8A0CEC, 0x8A0F2C, 0x8A1180, 0x8A13DC):
        va = start + 4
        while True:
            w = struct.unpack_from('<I', t, va)[0]
            if M._is_bl_to(w, va, M.TRANSLATE):
                break
            ins = next(md.disasm(struct.pack('<I', w), va))
            r, wr = ins.regs_access()
            names_r = {ins.reg_name(x) for x in r}
            names_w = {ins.reg_name(x) for x in wr}
            assert not names_r & {'w9', 'x9'}, hex(va)
            if names_w & {'w9', 'x9'}:
                break
            va += 4


def test_coaster_fade_values():
    g = {n: new for n, _va, _s, new, _a in M.GROUPS['coaster end fade'][2]}
    assert g['v0 x shift'] == 0x5101B129            # sub w9, w9, #108
    assert g['v2 width a'] == 0x52802EC9            # mov w9, #374
    assert 2 * M.COASTER_FADE_W == 640 + 108        # right edge at s = 2
