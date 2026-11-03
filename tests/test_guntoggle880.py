"""BUILD 880: the battle gun-effect toggle (x86 0x6CE81E) runs once per
battle-multiplier frames, as FFNx does, so enemy gunfire keeps its muzzle
flash and bullets at 60 FPS.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import os  # noqa: E402
import struct  # noqa: E402

import pytest  # noqa: E402

import ff7nx_60fps as F  # noqa: E402

MAIN = _nx_os.path.join(_nx_root, 'dump', 'exefs', 'main')


def test_caves_store_only_on_phase_zero():
    a, b = F._gun_toggle_caves(0x1000000, 0x1000020, 0x3FEF528, 2)
    assert a[6] == F.GUN_TOGGLE_A_ORIG and b[3] == F.GUN_TOGGLE_B_ORIG
    # A: adrp, ldr, add #1, and #3, str, cbnz -> past the store, store, b back
    assert a[2] == 0x11000631 and a[3] == 0x12000631
    assert (a[5] & 0xFF00001F) == 0x35000011 and (b[2] & 0xFF00001F) == 0x35000011
    assert (a[7] >> 26) == 0x05 and (b[4] >> 26) == 0x05


@pytest.mark.skipif(not os.path.exists(MAIN), reason='needs the dump')
def test_hooks_are_the_two_guest_stores_of_6CE81E():
    import nxmap
    m = nxmap.Main(MAIN)
    a, e = m.extent(0x6CE81E)
    assert a == F.GUN_TOGGLE_FN
    t = m.img
    for hook, orig in ((F.GUN_TOGGLE_A_HOOK, F.GUN_TOGGLE_A_ORIG),
                       (F.GUN_TOGGLE_B_HOOK, F.GUN_TOGGLE_B_ORIG)):
        assert a < hook < e
        assert struct.unpack_from('<I', t, hook)[0] == orig
        # right after `bl 0x10FC3A0`, so x16/x17 are dead
        prev = struct.unpack_from('<I', t, hook - 4)[0]
        assert (prev >> 26) == 0x25
        assert hook - 4 + ((prev & 0x3FFFFFF) << 2) == 0x10FC3A0


def test_off_switch(monkeypatch):
    monkeypatch.setenv(F.GUN_TOGGLE_OFF_ENV, '1')
    assert F.gun_toggle_disabled()
