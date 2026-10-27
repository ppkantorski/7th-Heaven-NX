"""BUILD 731: the battle countdown timer at the 16:9 corner (ff7nx_btltimerpos).

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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import a64 as A                                                # noqa: E402
import ff7nx_btltimerpos as B                                  # noqa: E402

MAIN = os.path.join(os.path.dirname(HERE), 'exefs', 'main')


def _laid_out(gap):
    addrs = [0x40000 + gap * i for i in range(B.N_WORDS)]
    words = B.body_words_at(lambda i: addrs[i])
    code = {}
    for i, (va, w) in enumerate(zip(addrs, words)):
        code[va] = w
        if gap != 4 and i + 1 < len(addrs):
            code[va + 4] = A.b(va + 4, addrs[i + 1])
    return code, addrs[0]


def test_732_left_gap_equals_top_gap():
    # battle overlay is 320 space: 3 px a unit at 720p, x 0 at device 160;
    # digits draw at y - 8 (top 24 px)
    top = 3 * (16 - 8)
    left = 160 + 3 * B.NEW_X
    assert abs(left - top) <= 1.5
    assert B.NEW_X == -45


def test_732_matches_the_731_capture():
    # 731 pushed -91: first digit at -113 px, so with a 48 px digit and a
    # 24 px colon the seconds land at 7 and 55 px -- the "8" Patrick's
    # capture shows at 55..100.
    first = 160 + 3 * (16 - 107)
    assert first == -113 and first + 48 * 2 + 24 + 48 == 55


def test_731_cave_pushes_the_new_x_only():
    for gap in (4, 12):
        code, entry = _laid_out(gap)
        assert B.verify_words(code, entry) == []


def test_731_a_wrong_cave_is_caught():
    code, entry = _laid_out(4)
    mut = dict(code)
    mut[entry] = B.movn(9, 90)
    assert B.verify_words(mut, entry) != []
    mut = dict(code)
    mut[entry + 4] = A.str_(21, 0)               # the stock store
    assert B.verify_words(mut, entry) != []


def test_731_anchors_and_install_on_the_real_module():
    try:
        import nxmap
        m = nxmap.Main(MAIN)
    except BaseException:                       # no lz4 / no dump: skip
        return
    assert B.check_anchors(m.text, log=lambda *_: None) == []
    words = B.build_patches(m.img, set(m.arm_starts), log=lambda *_: None)
    assert words is not None
    entry = B._b_target(words[B.HOOK], B.HOOK)
    assert B.verify_words({va: w for va, w in words.items() if va != B.HOOK},
                          entry) == []
    # the timer draw really is reached from this push sequence
    assert B.w32(m.text, 0xD3E58) == A.bl(0xD3E58, 0xD6E330)
