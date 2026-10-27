"""BUILD 540: the minigame limiter divisors (ff7_en data only).

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
import ff7nx_minipace as M  # noqa: E402

STOCK = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'resources', 'ff7_1.02',
                     'ff7_en')


def _stock():
    with open(STOCK, 'rb') as fh:
        return fh.read()


def test_anchors_hold_on_stock():
    assert M.check(_stock()) == []


def test_stock_values():
    st = M.state(_stock())
    assert st == {'condor limiter divisor': 60.0,
                  'submarine limiter divisor': 30.0}


def test_apply_changes_only_the_two_divisors():
    data = _stock()
    new, ch = M.patch(data, 66)
    assert len(ch) == 2          # condor to the dial, sub to 33 (551)
    diff = [i for i in range(len(data)) if data[i] != new[i]]
    pe = M._Pe(data)
    allowed = set()
    for _n, va, *_ in M.DIVISORS:
        allowed |= set(range(pe.off(va), pe.off(va) + 8))
    assert diff and set(diff) <= allowed
    assert M.state(new) == {'condor limiter divisor': 66.0,
                            'submarine limiter divisor': 33.0}
    # chocobo race untouched
    assert struct.unpack_from('<d', new, pe.off(M.CHOCOBO[1]))[0] == 30.0


def test_idempotent_and_revert():
    data = _stock()
    once, _ = M.patch(data, 66)
    twice, ch = M.patch(once, 66)
    assert twice == once and ch == []
    back, _ = M.patch(once, revert=True)
    assert back == data


def test_refuses_foreign_value():
    data = bytearray(_stock())
    pe = M._Pe(bytes(data))
    struct.pack_into('<d', data, pe.off(0x7B7820), 5.0)
    try:
        M.patch(bytes(data), 66)
    except ValueError:
        return
    raise AssertionError('foreign divisor accepted')


def test_dial_and_switch():
    assert M.dial({}) == 60.0
    assert M.dial({'SEVENTH_NX_LIMITER_FPS': '66'}) == 66.0
    assert M.dial({'SEVENTH_NX_LIMITER_FPS': '0'}) == 60.0
    assert M.enabled({})
    assert not M.enabled({'SEVENTH_NX_MINIGAME_60': '0'})


def test_submarine_left_at_60_is_put_back():
    # BUILD 550: builds 540-549 wrote the dial into the submarine divisor,
    # which doubled its game speed. A main carrying that is restored.
    data = bytearray(_stock())
    pe = M._Pe(bytes(data))
    struct.pack_into('<d', data, pe.off(0x7B7F60), 66.0)
    new, ch = M.patch(bytes(data), 66)
    assert M.state(new)['submarine limiter divisor'] == 33.0
    assert ('submarine limiter divisor', 66.0, 33.0) in ch


def test_sub_fps_env():
    assert M.sub_fps({}) == 33.0
    assert M.sub_fps({'SEVENTH_NX_SUB_FPS': '30'}) == 30.0
    assert M.sub_fps({'SEVENTH_NX_SUB_FPS': '60'}) == 33.0   # never the dial
