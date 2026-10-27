#!/usr/bin/env python3
"""tests/test_summonhold.py -- release the summon pause guard.

BUILD 375. `effect100-throttle` forces g_is_battle_paused on 3 frames in 4,
and every routine FFNx registers begins `if (g_is_battle_paused) return`, so
on those frames the camera is not aimed and the model is not placed. The
operator's report is the diagnosis: "before we patched it, the same issue
happened with shiva" -- and Shiva stopped when summon-runtime NOP'd exactly
this branch in its camera.

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
import importlib
import os
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                # noqa: E402
import arm64emu                                                # noqa: E402
import ff7nx_summonhold as S                                   # noqa: E402


def _find_dump():
    here = os.path.join(_HERE, 'dump', 'exefs', 'main')
    if os.path.exists(here):
        return here
    sib = os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                       '7th_heaven_nx', 'dump', 'exefs', 'main')
    return sib if os.path.exists(sib) else here


DUMP = _find_dump()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(S.HOLD_ENV, raising=False)
    importlib.reload(S)
    yield
    importlib.reload(S)


def test_OFF_by_default_and_the_switch_spellings(monkeypatch):
    # BUILD 376: hardware says releasing the guard makes the camera script
    # run out four times too early and freeze at its last angle.
    assert S.mode() == 'stock' and S.enabled() is False
    monkeypatch.setenv(S.HOLD_ENV, 'default')
    assert S.mode() == 'default' and S.enabled() is True
    for off in ('0', 'stock', 'off', 'no', 'false', ''):
        monkeypatch.setenv(S.HOLD_ENV, off)
        assert S.mode() == 'stock' and S.enabled() is False, off
    monkeypatch.setenv(S.HOLD_ENV, 'all')
    assert S.mode() == 'all' and S.enabled() is True


def test_both_polarities_release_the_body():
    """cbnz jumps OVER the body, so it becomes a NOP; cbz jumps TO the body,
    so it becomes an unconditional branch to the same place. Different words,
    one meaning: the body runs whether paused or not."""
    for name, site, stock, want, _ctx in S.DEFAULT_SITES:
        kind = stock & 0xFF000000
        if kind == 0x35000000:
            assert want == S.NOP, name
        else:
            assert kind == 0x34000000, name
            assert (want & 0xFC000000) == 0x14000000, name
            # and it lands exactly where the cbz landed
            rel = want & 0x3FFFFFF
            if rel & (1 << 25):
                rel -= (1 << 26)
            assert site + rel * 4 == S._cb_target(stock, site), name


def test_the_branch_words_execute_correctly():
    """Execute the replacements rather than trusting the encoder."""
    for name, site, stock, want, _ctx in S.DEFAULT_SITES:
        if want == S.NOP:
            continue
        rel = want & 0x3FFFFFF
        if rel & (1 << 25):
            rel -= (1 << 26)
        assert want == A.b(site, site + rel * 4), name


def test_a_nop_really_is_a_nop():
    cpu = arm64emu.Cpu(arm64emu.Mem())
    cpu.set(8, 1)
    cpu.run(0x1000, [S.NOP])
    assert cpu.get(8) == 1


def test_release_refuses_anything_that_is_not_a_guard():
    with pytest.raises(ValueError):
        S._release(0xD503201F, 0x1000)
    with pytest.raises(ValueError):
        S._release(0xAA0103E0, 0x1000)


def test_default_is_the_four_summons_the_operator_named():
    names = {n for n, _s, _st, _w, _c in S.DEFAULT_SITES}
    assert 'ramuh_camera' in names           # FFNx patches this one explicitly
    assert 'phoenix_camera' in names
    assert 'chocomog_camera' in names and 'chocomog_move' in names
    # and NOT the ones reported working
    assert not any(k in n for n in names
                   for k in ('bahamut', 'kotr', 'ifrit', 'kujata'))


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_against_the_real_image():
    import nxmap
    m = nxmap.Main(DUMP)
    assert S.verify(m.img) == []
    assert S.read_state(m.img) == 'summon pause guards: 0 of 7 released'
    for name, site, stock, _w, _c in S.DEFAULT_SITES:
        assert struct.unpack_from('<I', m.img, site)[0] == stock, name


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_every_site_really_is_the_paused_test():
    """The four words before each site must be the compiled
    g_is_battle_paused test, and 0xDC0E6C must be materialised just above."""
    import nxmap
    m = nxmap.Main(DUMP)
    lo, hi = S.PAUSED_GUEST & 0xFFFF, S.PAUSED_GUEST >> 16
    for name, site, _st, _w, ctx in S.DEFAULT_SITES:
        for off, want in zip((-16, -12, -8, -4, 4), ctx):
            got = struct.unpack_from('<I', m.img, site + off)[0]
            assert got == want, (name, hex(site + off))
        found = False
        for d in range(-0x60, 0, 4):
            a = struct.unpack_from('<I', m.img, site + d)[0]
            b = struct.unpack_from('<I', m.img, site + d + 4)[0]
            if ((a & 0xFFE00000) == 0x52800000 and ((a >> 5) & 0xFFFF) == lo
                    and (b & 0xFFE00000) == 0x72A00000
                    and ((b >> 5) & 0xFFFF) == hi):
                found = True
                break
        assert found, ('%s: no g_is_battle_paused above the guard' % name)


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_it_does_not_touch_the_two_words_summon_runtime_owns():
    import nxmap
    import ff7nx_60fps as F
    m = nxmap.Main(DUMP)
    ours = {s for _n, s, _st, _w, _c in S.DEFAULT_SITES} | {
        s for _n, s in S.EXTRA_SITES}
    theirs = {va for _lbl, va, _old, _new in F.NSO_GATED['summon-runtime']}
    assert ours & theirs == set()
    for va, (stock, _who) in S.FOREIGN_GUARDS.items():
        assert va in theirs
        assert struct.unpack_from('<I', m.img, va)[0] == stock


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_plan_default_all_and_revert(monkeypatch):
    import nxmap
    m = nxmap.Main(DUMP)
    monkeypatch.setenv(S.HOLD_ENV, 'default')
    importlib.reload(S)
    patches, notes, problems = S.plan(m)
    assert problems == [] and len(patches) == len(S.DEFAULT_SITES)
    got = {int(p['va'], 16): bytes.fromhex(p['set']) for p in patches}
    for _n, site, _st, want, _c in S.DEFAULT_SITES:
        assert got[site] == struct.pack('<I', want)
    patches, _, problems = S.plan(m, revert=True)
    assert problems == [] and patches == []
    monkeypatch.setenv(S.HOLD_ENV, 'all')
    importlib.reload(S)
    patches, _, problems = S.plan(m)
    assert problems == []
    assert len(patches) == len(S.DEFAULT_SITES) + len(S.EXTRA_SITES)


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_it_refuses_when_the_test_above_a_site_moves():
    import nxmap
    m = nxmap.Main(DUMP)
    img = bytearray(m.img)
    struct.pack_into('<I', img, S.DEFAULT_SITES[0][1] - 8, 0xDEADBEEF)
    assert S.verify(img)

    class M:
        pass
    fake = M()
    fake.img = img
    patches, _, problems = S.plan(fake)
    assert patches == [] and problems
