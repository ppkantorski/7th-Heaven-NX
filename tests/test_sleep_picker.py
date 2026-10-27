"""BUILD 574: Echo-S inn hour pickers with no WAIT step once per press.

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
import struct
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import echo_s_flevel as E  # noqa: E402
import lgp  # noqa: E402

ECHO = os.path.expanduser('~/mnt/7th_heaven_nx/cache/Echo-s/Echo-S/flevel.lgp')
if not os.path.isdir(ECHO):
    ECHO = os.path.join(HERE, 'cache', 'Echo-s', 'Echo-S', 'flevel.lgp')


def _section1(name):
    raw = open(os.path.join(ECHO, name), 'rb').read()
    try:
        return lgp.split_sections(raw)[0]
    except Exception:                                         # noqa: BLE001
        return lgp.split_sections(lgp.lzs_decompress(raw[4:]))[0]


def test_only_the_three_unpaced_pickers_change():
    if not os.path.isdir(ECHO):
        return
    changed = {}
    for name in sorted(os.listdir(ECHO)):
        if not os.path.isfile(os.path.join(ECHO, name)):
            continue
        try:
            s = _section1(name)
        except Exception:                                     # noqa: BLE001
            continue
        out, n = E.pace_unpaced_hour_pickers(s, name)
        if n:
            diff = [i for i in range(len(s)) if s[i] != out[i]]
            assert len(out) == len(s)
            assert all(s[i] == E.OP_KEY_HELD and out[i] == E.OP_KEY_ON
                       for i in diff)
            changed[name] = len(diff)
    assert changed == {'blin64': 2, 'convil_1': 2, 'frmin': 2}


def test_a_paced_picker_is_byte_identical():
    if not os.path.isdir(ECHO):
        return
    for name in ('juninn', 'mktinn', 'gninn', 'cosin3'):
        s = _section1(name)
        assert E.pace_unpaced_hour_pickers(s, name) == (s, 0)


def test_convil_1_left_right_become_keyon_and_ok_is_untouched():
    if not os.path.isdir(ECHO):
        return
    s = _section1('convil_1')
    out, n = E.pace_unpaced_hour_pickers(s, 'convil_1')
    assert n == 2
    for off, mask in ((0x3A98, 0x2000), (0x3AA9, 0x8000)):
        assert out[off] == E.OP_KEY_ON
        assert struct.unpack_from('<H', out, off + 1)[0] == mask
    # the OK press was already `keyon`
    assert out[0x3ABA] == E.OP_KEY_ON == s[0x3ABA]


# ------------------------------------------------ BUILD 576: up to 12 hours
SLEEP_INNS = {'blin64', 'convil_1', 'cosin3', 'delinn', 'elminn_1', 'frmin',
              'gninn', 'holu_2', 'jumin', 'juninn', 'losinn', 'mds7_w3',
              'mktinn', 'ncoinn', 'nivinn_1', 'rktinn1', 'sninn_1',
              'uttmpin1'}


def test_every_sleep_picker_offers_twelve_and_nothing_else_moves():
    if not os.path.isdir(ECHO):
        return
    got = {}
    for name in sorted(os.listdir(ECHO)):
        if not os.path.isfile(os.path.join(ECHO, name)):
            continue
        try:
            s = _section1(name)
        except Exception:                                     # noqa: BLE001
            continue
        out, n = E.extend_hour_pickers(s, name)
        if not n:
            assert out == s
            continue
        diff = [i for i in range(len(s)) if s[i] != out[i]]
        assert len(out) == len(s) and len(diff) == 2 * n
        assert sorted((s[i], out[i]) for i in diff) == [(10, 12), (11, 13)] * n \
            or sorted((s[i], out[i]) for i in diff) == sorted(
                [(10, 12), (11, 13)] * n)
        got[name] = n
    assert set(got) == SLEEP_INNS and set(got.values()) == {1}


def test_juninn_wraps_exact_bytes_and_stacks_with_pacing():
    if not os.path.isdir(ECHO):
        return
    s = _section1('juninn')
    out, n = E.extend_hour_pickers(s, 'juninn')
    assert n == 1
    assert out[0xC09:0xC0F] == bytes.fromhex('1460080d0008')   # v == 13
    assert out[0xC0F:0xC13] == bytes.fromhex('80600801')       # -> 1
    assert out[0xC20:0xC26] == bytes.fromhex('146008000008')   # v == 0
    assert out[0xC26:0xC2A] == bytes.fromhex('8060080c')       # -> 12
    # convil_1: the 574 pacing and the 576 range compose
    c = _section1('convil_1')
    p, _ = E.pace_unpaced_hour_pickers(c, None)
    both, m = E.extend_hour_pickers(p, None)
    assert m == 1 and both[0x3A98] == E.OP_KEY_ON
    assert both[0x3AA2] == 13 and both[0x3AB9] == 12
    assert E.extend_hour_pickers(s, None, top=10) == (s, 0)
