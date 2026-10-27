#!/usr/bin/env python3
"""tests/test_fxcapscale.py -- Kujata's own xscale/yscale, and the claim that
it is the ONLY capture in the game that is not 1:1.

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
import re
import shutil
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu                                                # noqa: E402
import ff7nx_fxcapscale as C                                   # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
EXE = os.path.join(_HERE, 'game_data_files', 'ff7_1.02', 'ff7_en')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def _text():
    data = open(EXE, 'rb').read()
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    opt = struct.unpack_from('<H', data, pe + 20)[0]
    base = struct.unpack_from('<I', data, pe + 24 + 28)[0]
    o = pe + 24 + opt
    vsz, va, rsz, raw = struct.unpack_from('<IIII', data, o + 8)
    return base + va, data[raw:raw + rsz]


@pytest.mark.skipif(not os.path.exists(EXE), reason='needs ff7_en')
def test_kujata_is_the_only_capture_that_sets_its_scale_to_an_immediate_one():
    """The claim this module rests on, checked against the executable.

    Not "every capture is xscale 1" -- that is FALSE, and believing it is what
    broke the swirl. The swirl sets xscale from [0x9A04B0], which x86 0x401726
    sets to 2. What IS true is that Kujata's site stores an immediate 1, which
    is why raising it to 2 is a change to one effect and not to a shared global.
    """
    sva, text = _text()

    def stores(offbytes):
        imm, reg = [], []
        i = 0
        while True:
            i = text.find(offbytes, i)
            if i < 0:
                break
            if i >= 2 and text[i - 2] == 0xC7 and (text[i - 1] & 0xF8) == 0x80:
                imm.append((sva + i - 2,
                            struct.unpack_from('<I', text, i + 4)[0]))
            if i >= 2 and text[i - 2] == 0x89 and (text[i - 1] & 0xC0) == 0x80:
                reg.append(sva + i - 2)
            i += 1
        return imm, reg

    xi, xr = stores(b'\x0c\x01\x00\x00')
    yi, yr = stores(b'\x10\x01\x00\x00')
    # Kujata's pair is immediate, and it is 1
    assert (0x500A34, 1) in xi and (0x500A41, 1) in yi
    # the swirl's pair is register-sourced -- i.e. NOT an immediate 1
    assert 0x40248E in xr and 0x40249D in yr
    # Pair them up -- an xscale store followed by a yscale store within 40
    # bytes is the struc91 setup signature; a lone hit is some other struct
    # that happens to have a field at the same offset (0x4B9432 is one).
    pairs = []
    for a, v in yi:
        near = [(b, w) for b, w in xi if 0 < a - b <= 40]
        if near:
            b, w = near[-1]
            pairs.append((b, w, a, v))
    assert len(pairs) >= 25, len(pairs)
    assert (0x500A34, 1, 0x500A41, 1) in pairs
    # every immediate-scaled creator uses 1, so the 2 this module writes cannot
    # be confused with one of them
    assert {(w, v) for _, w, _, v in pairs} == {(1, 1)}


def test_the_two_sites_are_kujatas_own_and_carry_the_stock_store(img):
    assert struct.unpack_from('<I', img, C.X_SITE)[0] == C.STORE_STOCK
    assert struct.unpack_from('<I', img, C.Y_SITE)[0] == C.STORE_STOCK
    assert C.verify(img) == []
    assert C.read_state(img) == 'capture scale x1 y1 (stock)'


def test_the_cave_really_stores_the_scale():
    """Execute it: w17 must end up holding k and the store must land at [x0]."""
    for k in C.LEGAL:
        if k == 1:
            continue
        base, dest = 0x200000, 0x300000
        addrs = [base + 4 * i for i in range(C.N_WORDS)]
        body = C.body_words(k, C.X_SITE + 4, lambda i: addrs[i])
        mem = arm64emu.Mem()
        cpu = arm64emu.Cpu(mem)
        cpu.set(0, dest)
        cpu.set(19, 1)
        pc = cpu.run(base, None, code=dict(zip(addrs, body)), start_pc=base)
        assert mem.u(dest, 4) == k
        assert cpu.get(19) == 1              # the shared register is untouched
        assert pc == C.X_SITE + 4            # and it returns to the next word


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path, monkeypatch):
    for k in C.LEGAL:
        monkeypatch.setenv(C.CAPSCALE_ENV, str(k))
        out = tmp_path / ('main%d' % k)
        shutil.copy(MAIN, out)
        assert C.apply_all(out, log=lambda *_: None) == 0
        got = nxmap.Main(str(out)).img
        assert C.verify(got) == []
        assert C.installed_scale(got) == (k, k)   # BUILD 337: a pair
        assert C.apply_all(out, log=lambda *_: None) == 0        # idempotent
        assert C.apply_all(out, revert=True, log=lambda *_: None) == 0
        assert out.read_bytes() == open(MAIN, 'rb').read()


def test_switching_between_scales_never_strands_a_cave(tmp_path, monkeypatch):
    out = tmp_path / 'main_seq'
    shutil.copy(MAIN, out)
    quiet = lambda *_: None                                    # noqa: E731
    for k in (2, 4, 2, 1, 4, 2, 1):
        monkeypatch.setenv(C.CAPSCALE_ENV, str(k))
        assert C.apply_all(out, log=quiet) == 0
        got = nxmap.Main(str(out)).img
        assert C.installed_scale(got) == (k, k)   # BUILD 337: a pair
        assert C.verify(got) == []
    assert C.apply_all(out, revert=True, log=quiet) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()


def test_both_sites_always_carry_the_same_scale(tmp_path, monkeypatch):
    """A capture with xscale 2 and yscale 1 would be a shape nothing handles."""
    monkeypatch.setenv(C.CAPSCALE_ENV, '2')
    out = tmp_path / 'main_pair'
    shutil.copy(MAIN, out)
    assert C.apply_all(out, log=lambda *_: None) == 0
    got = nxmap.Main(str(out)).img
    for site in (C.X_SITE, C.Y_SITE):
        addrs, _ = C._walk(got, site)
        assert len(addrs) == C.N_WORDS
        assert [struct.unpack_from('<I', got, a)[0] for a in addrs] == \
            C.body_words(2, site + 4, lambda i: addrs[i])


def test_the_resample_gate_follows_this_module(monkeypatch):
    import ff7nx_fbresample as F
    for k, sh in ((1, 0), (2, 1), (4, 2)):
        monkeypatch.setenv(C.CAPSCALE_ENV, str(k))
        assert F.size_shift() == sh


def test_stock_scale_turns_the_module_off(monkeypatch):
    monkeypatch.setenv(C.CAPSCALE_ENV, '1')
    assert C.enabled() is False
    monkeypatch.setenv(C.CAPSCALE_ENV, '2')
    assert C.enabled() is True


def test_nonsense_falls_back_rather_than_writing_garbage(monkeypatch):
    for bad in ('0', '3', '9', 'banana', ''):
        monkeypatch.setenv(C.CAPSCALE_ENV, bad)
        assert C.scale() in C.LEGAL


def test_it_refuses_when_the_neighbourhood_moved(img):
    bad = bytearray(img)
    struct.pack_into('<I', bad, 0x473C8C, 0xD503201F)
    assert C.verify(bad) != []
