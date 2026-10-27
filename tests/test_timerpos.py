"""BUILD 715: the field countdown timer at the 16:9 corner (ff7nx_timerpos).

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
import ff7nx_timerpos as T                                     # noqa: E402

MAIN = os.path.join(os.path.dirname(HERE), 'exefs', 'main')


def _laid_out(gap):
    """The cave at real addresses `gap` bytes apart, as {va: word}, entry."""
    addrs = [0x40000 + gap * i for i in range(T.N_WORDS)]
    words = T.body_words_at(lambda i: addrs[i])
    code = {}
    for i, (va, w) in enumerate(zip(addrs, words)):
        code[va] = w
        if gap != 4 and i + 1 < len(addrs):
            code[va + 4] = A.b(va + 4, addrs[i + 1])
    return code, addrs[0]


def test_715_margin_is_the_shader_margin():
    assert T.MARGIN == 107                     # 320 / 0.75 - 320 = 106.67
    # game x 24 (the 4:3 corner, 36 px at 720p) lands 36 px from the left
    assert abs(1.5 * (24 - T.MARGIN) + 160 - 1.5 * 24) <= 1


def test_715_timer_window_moves_and_its_clip_goes_full_width():
    for gap in (4, 12):                         # contiguous and chained
        code, entry = _laid_out(gap)
        assert T.verify_words(code, entry) == []


def test_715_other_windows_untouched_flat_and_paged():
    code, entry = _laid_out(4)
    for paged in (False, True):
        r = T._run_cave(code, entry, special=0, x1=16, width=506, left=16,
                        paged=paged)
        assert (r['x1'], r['width'], r['left']) == (16, 506, 16)
        assert r['exit'] == T.RETURN_VA and r['w8'] == T.ESP


def test_715_reads_the_right_window():
    # only window 5 is a timer; the cave must index with the 0x30 stride
    code, entry = _laid_out(4)
    r = T._run_cave(code, entry, special=1, x1=20, width=320, left=20, index=5)
    assert (r['x1'], r['width'], r['left']) == (0, T.FULL_W, 20 - T.MARGIN)


def test_715_a_wrong_cave_is_caught():
    code, entry = _laid_out(4)
    # mutate the margin and the width: verify_words must notice both
    for i, bad in ((28, A.sub_imm(9, 9, 106)), (22, A.movz(9, 639))):
        mut = dict(code)
        mut[entry + 4 * i] = bad
        assert T.verify_words(mut, entry) != []


def test_715_anchors_and_install_on_the_real_module():
    try:
        import nxmap
        m = nxmap.Main(MAIN)
    except BaseException:                       # no lz4 / no dump: skip
        return
    assert T.check_anchors(m.text, log=lambda *_: None) == []
    words = T.build_patches(m.img, set(m.arm_starts), log=lambda *_: None)
    assert words[T.HOOK] == A.b(T.HOOK, T._b_target(words[T.HOOK], T.HOOK))
    code = {va: w for va, w in words.items() if va != T.HOOK}
    assert T.verify_words(code, T._b_target(words[T.HOOK], T.HOOK)) == []


def test_715_follows_16_9():
    assert T.enabled('1') and not T.enabled('0') and not T.enabled('')
