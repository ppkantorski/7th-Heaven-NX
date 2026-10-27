#!/usr/bin/env python3
"""tests/test_fxwindow.py -- the snapshot window, two immediates in place.

BUILD 369. The operator proved on hardware that an unmodified dump has the
warping, so nothing of ours causes it; FFNx's own source proves the port is
faithful to FF7 PC; and FF7 PC asks for a 256-unit window in a 640-unit frame
where PSX asked for 256 in a 320-unit one. The window was halved before this
port existed, and the disc's world size never changed to match.

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
import ff7nx_fxwindow as W                                     # noqa: E402


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
    monkeypatch.delenv(W.WIN_ENV, raising=False)
    importlib.reload(W)
    yield
    importlib.reload(W)


def test_stock_round_trips_and_the_default_is_OFF():
    """BUILD 370. 480 was executed on hardware: black field, slow game. That
    is size (1920x1920, 3.7M texels copied per capture), and it also killed
    the model the module argued from -- a 4.78x blow-up would make the field's
    grass four times coarser than the floor's, and it is not."""
    assert W.words_for(W.STOCK_HI) == {W.HI_SITE: W.HI_STOCK,
                                       W.LO_SITE: W.LO_STOCK}
    assert W.window() == W.DEFAULT_WINDOW == W.STOCK_HI
    assert W.enabled() is False


def test_the_words_really_load_those_values_on_a_cpu():
    """Execute them, do not compare them."""
    for n in (256, 320, 384, 448, 480):
        w = W.words_for(n)
        for site, reg, want in ((W.HI_SITE, W.HI_REG, n),
                                (W.LO_SITE, W.LO_REG, n // 2)):
            cpu = arm64emu.Cpu(arm64emu.Mem())
            cpu.set(reg, 0xDEADBEEF)
            cpu.run(0x1000, [w[site]])
            assert cpu.get(reg) & 0xFFFFFFFF == want, (n, site)


def test_the_encoder_agrees_with_a64():
    for n in (128, 256, 480, 512):
        assert W._movz32(W.HI_REG, n) == A.movz(W.HI_REG, n)


def test_480_is_the_largest_window_that_fits_the_frame_in_both_axes():
    """WHY this number, asserted rather than asserted-in-a-comment.

    The copy takes `min(480k, y+h) - y` rows, so any h above 480 leaves
    texels unwritten, and unwritten texels read black at the rim -- the
    artefact class that cost builds 245-266. And x must survive the clamp
    `min(x', 640k - w)` without being dragged left.
    """
    for k in (1, 2, 4):
        for n in (W.DEFAULT_WINDOW, 512):
            fbw = fbh = n * k
            x = ((0 + 107) * 640 * k) // 854          # fbcapture's origin
            rows = min(480 * k, 0 + fbh) - 0
            cols = min(640 * k, x + fbw) - x
            clamp = 640 * k - fbw
            if n <= 480:
                assert rows == fbh, (k, n, rows, fbh)   # nothing unwritten
                assert cols == fbw, (k, n, cols, fbw)
                assert x <= clamp, (k, n, x, clamp)     # origin survives
            else:
                assert rows < fbh                       # 512 overflows
    assert W.WIN_MAX == 480


def test_it_stays_square_because_four_modules_assume_it(monkeypatch):
    """fb_tex.w and fb_tex.h come from the same constant, and the resample
    gate tests both against the same shift."""
    import ff7nx_fbresample as R
    assert R.gate_shift() == R.gate_shift_h()
    for n in (256, 384, 480):
        monkeypatch.setenv(W.WIN_ENV, str(n))
        importlib.reload(W)
        assert W.window() == n


def test_out_of_band_falls_back_and_stock_turns_it_off(monkeypatch):
    for bad in ('banana', '0', '64', '1024', '-1'):
        monkeypatch.setenv(W.WIN_ENV, bad)
        importlib.reload(W)
        assert W.window() == W.DEFAULT_WINDOW, bad
    monkeypatch.setenv(W.WIN_ENV, 'stock')
    importlib.reload(W)
    assert W.window() == W.STOCK_HI and W.enabled() is False
    monkeypatch.setenv(W.WIN_ENV, '480')
    importlib.reload(W)
    assert W.window() == 480 and W.enabled() is True      # still reachable


def test_the_two_modes_keep_their_two_to_one_relationship():
    """PSX's fraction is the same in both render modes; halving the low-res
    value keeps that true rather than making mode 1 a different effect."""
    for n in (256, 384, 480):
        w = W.words_for(n)
        hi = W._decode_mov(w[W.HI_SITE], W.HI_REG)
        lo = W._decode_mov(w[W.LO_SITE], W.LO_REG)
        assert hi == n and lo == n // 2, (n, hi, lo)


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_against_the_real_image():
    import nxmap
    m = nxmap.Main(DUMP)
    assert W.verify(m.img) == []
    assert W.read_state(m.img) == 'snapshot window 256x256 (low-res mode 128) (stock)'


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_the_anchors_and_the_consumer_are_really_in_the_image():
    import nxmap
    m = nxmap.Main(DUMP)
    for table in (W.ANCHORS, W.CONSUMER):
        for va, want in sorted(table.items()):
            got = struct.unpack_from('<I', m.img, va)[0]
            assert got == want, ('+0x%X' % va, hex(got), hex(want))


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_it_shares_no_site_with_fxsnap():
    """Both modules write into the same routine, four instructions apart."""
    import ff7nx_fxsnap as S
    assert {W.HI_SITE, W.LO_SITE} & {S.HOOK_X, S.HOOK_Y} == set()
    assert S.HOOK_X in W.ANCHORS and S.HOOK_Y in W.ANCHORS


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_plan_and_revert(monkeypatch):
    import nxmap
    m = nxmap.Main(DUMP)
    patches, _, problems = W.plan(m)                  # default is stock now
    assert problems == [] and patches == []
    patches, _, problems = W.plan(m, revert=True)
    assert problems == [] and patches == []
    monkeypatch.setenv(W.WIN_ENV, '480')
    importlib.reload(W)
    patches, notes, problems = W.plan(m)
    assert problems == [] and len(patches) == 2
    got = {int(p['va'], 16): bytes.fromhex(p['set']) for p in patches}
    assert got[W.HI_SITE] == struct.pack('<I', W._movz32(W.HI_REG, 480))
    assert got[W.LO_SITE] == struct.pack('<I', W._movz32(W.LO_REG, 240))
    assert any('magnified' in n for n in notes)


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_it_refuses_when_the_consumer_moves():
    import nxmap
    m = nxmap.Main(DUMP)
    img = bytearray(m.img)
    va = sorted(W.CONSUMER)[0]
    struct.pack_into('<I', img, va, 0xDEADBEEF)
    assert W.verify(img)

    class M:
        pass
    fake = M()
    fake.img = img
    patches, _, problems = W.plan(fake)
    assert patches == [] and problems
