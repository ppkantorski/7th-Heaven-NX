"""BUILD 783: routines that never read g_is_battle_paused get the
stack-balanced one-call skip (Chocobuckle "completely out of sync").

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'tests'))
import nxpaths  # noqa: E402
nxpaths.install()
import os  # noqa: E402
import struct  # noqa: E402

import pytest  # noqa: E402

import ff7nx_60fps as F  # noqa: E402
from ff7nx_dispatch_sites import DISPATCH_SITES, PAUSED_GUEST  # noqa: E402


def _thr():
    d = DISPATCH_SITES['effect100']
    thr = dict(d['throttle'])
    thr['paused_guest'] = PAUSED_GUEST
    thr['idx_off'] = d['disp_hook']['idx_off']
    return thr


def test_783_the_list_joins_the_one_call_skip(monkeypatch):
    monkeypatch.delenv(F.PAUSE_DEAF_ONE_CALL_ENV, raising=False)
    thr = _thr()
    F._pause_deaf_one_call('effect100', thr, log=lambda *_: None)
    got = {va for _n, va in thr['one_call']}
    assert 0x484A16 in got                                  # Bahamut ZERO kept
    assert {va for _n, va in F.PAUSE_DEAF_ONE_CALL} <= got
    assert 0x560C32 in got and 0x56149A in got and 0x561737 in got


def test_783_the_switch(monkeypatch):
    monkeypatch.setenv(F.PAUSE_DEAF_ONE_CALL_ENV, '0')
    thr = _thr()
    F._pause_deaf_one_call('effect100', thr, log=lambda *_: None)
    assert [va for _n, va in thr['one_call']] == [0x484A16]


def test_783_every_entry_registers_and_skips_balanced(monkeypatch):
    import test_throttle as T
    monkeypatch.delenv(F.PAUSE_DEAF_ONE_CALL_ENV, raising=False)
    thr = _thr()
    F._pause_deaf_one_call('effect100', thr, log=lambda *_: None)
    del T.FAIL[:]
    n = T.one_call_balanced(thr, 4, quiet=True)
    assert not T.FAIL, T.FAIL
    assert n >= len(thr['one_call'])


EXE = os.path.join(nxpaths.ROOT, 'dump', 'romfs', 'ff7', 'resources',
                   'ff7_1.02', 'ff7_en')


@pytest.mark.skipif(not os.path.isfile(EXE), reason='no ff7_en')
def test_783_none_of_them_reads_the_pause_flag():
    capstone = pytest.importorskip('capstone')
    d = open(EXE, 'rb').read()
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    for name, va in F.PAUSE_DEAF_ONE_CALL:
        o = va - 0x401000 + 0x200
        seen = False
        for i in md.disasm(d[o:o + 0x8000], va):
            if '0xdc0e6c' in i.op_str:
                seen = True
            if i.mnemonic == 'ret':
                break
        assert not seen, name
        # registered through add_fn_to_effect100_fn (push fn; call 0x5BEC50)
        pat = b'\x68' + struct.pack('<I', va) + b'\xe8'
        assert pat in d, name
