"""BUILD 782b: Bahamut ZERO's main loop MUST keep FFNx's one-call skip.

782 pause-throttled it so its scene matrix 0xAA66C0 (= view x scene, built
at the top of every call, x86 0x484A5E) would follow the camera on every
frame. Hardware 10-22 (2026-10-22_10-20-21.mp4): the space scene went black
on every 4th frame (733, 737, ... 989) and then for 380 frames solid.

The real call does not leave 0xAA66C0 as the top built it: during the
approach (script step 0x14 .. 0xC7) it calls 0x48B98D, which ends with

    48BBDC  661e85([0xAA6544] + 8, 0xAA66C0, 0xAA66C0)

-- a script rotation multiplied INTO the matrix. A paused call rebuilt the
matrix without it, so three frames in four drew a different scene. The skip
keeps the tick's matrix, rotation included, for all four frames.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()

import os  # noqa: E402

import pytest  # noqa: E402

import ff7nx_60fps as F  # noqa: E402

BZ_MAIN = 0x484A16


def test_782b_the_main_loop_keeps_the_one_call_skip():
    thr = F.DISPATCH_SITES['effect100']['throttle']
    assert any(va == BZ_MAIN for _n, va in thr['one_call'])
    assert not hasattr(F, '_bz_main_refresh')


def test_782b_the_real_call_rotates_the_matrix_in_place():
    exe = os.path.join(nxpaths.ROOT, 'dump', 'romfs', 'ff7', 'resources',
                       'ff7_1.02', 'ff7_en')
    if not os.path.isfile(exe):
        pytest.skip('no ff7_en')
    d = open(exe, 'rb').read()

    def at(va, n):
        o = va - 0x401000 + 0x200
        return d[o:o + n]
    # 48BBDC push 0xAA66C0 ; push 0xAA66C0 ; ... ; call 661e85
    assert at(0x48BBDC, 10) == bytes.fromhex('68c066aa00' '68c066aa00')
    # 484D33 call 48B98D -- from the main loop's real (unpaused) path
    rel = int.from_bytes(at(0x484D34, 4), 'little', signed=True)
    assert at(0x484D33, 1) == b'\xe8' and 0x484D38 + rel == 0x48B98D
