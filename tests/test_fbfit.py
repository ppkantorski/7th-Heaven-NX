#!/usr/bin/env python3
"""tests/test_fbfit.py -- the copy loop fills the whole sheet.

BUILD 301. FFNx hard-codes the battle UV step at 1/256
(`animations.cpp:868`, `u_offset = field_8_float * 0.00390625`), so the mesh
spans the WHOLE sheet regardless of the texture's size. A sheet that is only
partly written therefore shows its unwritten columns -- the black edge -- and
its content sits where a fraction of the sheet is, which reads as the wrong
scale.

So the loop runs for `fb_tex.w` columns instead of stopping at what the surface
happened to hold. Two guards make that safe without any gate on identity:

    the capture must be 1:1 (fb_tex.w == tex_format.width), so
      ff7nx_fbresample's 640/854 step is the one supplying the source column
    the source must hold at least 3/4 of the sheet, since that step's furthest
      read is 0.749 * (dest_w - 1)

Both are executed here, against every capture shape in the game.

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
import shutil
import struct
import sys

import pytest

# BUILD 309. The gates in these modules follow ff7nx_fbsurf, whose default
# is 2. Everything below was written against the STOCK 640x480 capture
# surface and means to test that shape; the scaled shapes are executed in
# tests/test_fbsurf.py. Module scope, because the cave fixtures are built
# once, before any function-scoped fixture could pin it.
os.environ.setdefault('SEVENTH_NX_FB_SURFACE', '1')

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as E                                           # noqa: E402
import ff7nx_fbfit as F                                        # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')


BASE = 0xB00000
HDR = 0x700000


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def _bound(fbw, texw, x, y, surf_w):
    """Execute the cave and report the loop bound it leaves in w10."""
    avail = min(surf_w, x + fbw) - x
    mem = E.Mem()
    for off, v in ((F.FB_W_OFF, fbw), (F.TEX_W_OFF, texw)):
        mem.setu(HDR + off, v, 4)
    cpu = E.Cpu(mem)
    cpu.set(F.TEX, HDR)
    cpu.set(F.END, min(surf_w, x + fbw))
    cpu.set(F.ORG, x)
    cpu.set(F.COLS, 0xBADF00D)
    cpu.set(F.S1, 0xDEAD)
    cpu.set(F.S2, 0xBEEF)
    addrs = [BASE + 4 * i for i in range(F.N_WORDS)]
    code = dict(zip(addrs, F.body_words(lambda i: addrs[i])))
    pc = cpu.run(BASE, None, code=code, start_pc=BASE)
    assert pc == F.RETURN_VA, hex(pc)
    return cpu.get(F.COLS), avail


def test_the_stock_word_and_the_neighbourhood_are_what_the_module_claims(img):
    assert struct.unpack_from('<I', img, F.HOOK)[0] == F.HOOK_STOCK
    assert F.verify(img) == []
    assert F.read_state(img).startswith('copy loop bound stock')


def test_a_capture_that_already_fills_the_sheet_is_unchanged():
    """When the source covers the sheet, `avail == fb_tex.w` and raising the
    bound to fb_tex.w writes exactly the same value -- so the loop runs the
    same number of times it always did."""
    for surf_w in (320, 640, 854, 1280):
        for fbw, texw, x in ((256, 256, 0), (160, 160, 0), (64, 64, 8),
                             (240, 240, 40)):
            if x + fbw > surf_w:
                continue
            got, avail = _bound(fbw, texw, x, 0, surf_w)
            assert avail == fbw
            assert got == fbw, (surf_w, fbw, x)


def test_a_short_1to1_capture_is_raised_to_the_whole_sheet():
    """Kujata: a 256-wide sheet whose source only reaches 244. The loop must
    run 256 times so every texel the UVs address gets written."""
    got, avail = _bound(256, 256, 80, 0, 324)
    assert avail == 244
    assert got == 256


def test_a_page_scaled_capture_keeps_the_stock_bound():
    """fb_tex.w != tex_format.width means xscale > 1: the resample cave does
    NOT supply its source column, the stock 1:1 body does, and raising the
    bound would read past the surface. The battle swirl is this case."""
    for fbw, texw, x, surf_w in ((320, 160, 0, 300),
                                 (512, 256, 0, 400),
                                 (320, 160, 80, 320)):
        got, avail = _bound(fbw, texw, x, 0, surf_w)
        assert got == avail, (fbw, texw, surf_w)


def test_too_little_source_keeps_the_stock_bound():
    """The 640/854 step's furthest read is 0.749*(dest_w-1). If the source does
    not hold at least three quarters of the sheet, raising the bound would read
    off the end of the surface, so it must not be raised."""
    for fbw, x, surf_w in ((256, 0, 180),      # avail 180, 0.70 of the sheet
                           (256, 0, 100),
                           (256, 80, 200)):    # avail 120
        got, avail = _bound(fbw, fbw, x, 0, surf_w)
        assert avail < fbw * 3 // 4 or got == fbw
        if avail * 4 < fbw * 3:
            assert got == avail, (fbw, surf_w, avail)


def test_the_boundary_of_the_three_quarters_guard():
    """Exactly at 3/4 it is safe, just below it is not."""
    fbw = 256
    got, avail = _bound(fbw, fbw, 0, 0, 192)         # avail 192 == 3/4
    assert (avail, got) == (192, 256)
    got, avail = _bound(fbw, fbw, 0, 0, 191)         # one short
    assert (avail, got) == (191, 191)


def test_every_raised_read_stays_inside_the_surface():
    """The point of the guard, checked arithmetically for every shape it lets
    through: the resample's furthest source column must be within `avail`."""
    for surf_w in (192, 200, 244, 256, 320, 324, 640, 854, 1280):
        for fbw in (64, 128, 160, 240, 256):
            for x in (0, 40, 80):
                got, avail = _bound(fbw, fbw, x, 0, surf_w)
                if got == fbw and got != avail:
                    furthest = ((fbw - 1) * 640) // 854
                    assert furthest < avail, (surf_w, fbw, x, furthest, avail)


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv(F.FIT_ENV, '1')
    monkeypatch.setenv('SEVENTH_NX_FB_RESAMPLE', '1')
    out = tmp_path / 'main_fit'
    shutil.copy(MAIN, out)
    assert F.apply_all(out, log=lambda *_: None) == 0
    got = nxmap.Main(str(out)).img
    assert F.verify(got) == []
    assert F.installed(got) is True
    assert F.apply_all(out, log=lambda *_: None) == 0
    assert F.apply_all(out, revert=True, log=lambda *_: None) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()


def test_it_writes_the_hook_and_its_cave_and_nothing_else(tmp_path,
                                                          monkeypatch):
    monkeypatch.setenv(F.FIT_ENV, '1')
    monkeypatch.setenv('SEVENTH_NX_FB_RESAMPLE', '1')
    out = tmp_path / 'main_scope'
    shutil.copy(MAIN, out)
    before = nxmap.Main(str(out)).img
    assert F.apply_all(out, log=lambda *_: None) == 0
    after = nxmap.Main(str(out)).img
    diff = [i for i in range(0, len(before), 4)
            if before[i:i + 4] != after[i:i + 4]]
    logical, physical = F._walk(after)
    assert len(logical) == F.N_WORDS
    assert set(diff) == set(physical) | {F.HOOK}
    for va in sorted(F.ANCHORS):
        assert struct.unpack_from('<I', after, va)[0] == F.ANCHORS[va], hex(va)


def test_the_installed_cave_executes_the_same_way_as_the_generator(
        tmp_path, monkeypatch):
    monkeypatch.setenv(F.FIT_ENV, '1')
    monkeypatch.setenv('SEVENTH_NX_FB_RESAMPLE', '1')
    out = tmp_path / 'main_roundtrip'
    shutil.copy(MAIN, out)
    assert F.apply_all(out, log=lambda *_: None) == 0
    img = nxmap.Main(str(out)).img
    addrs, _ = F._walk(img)
    words = [struct.unpack_from('<I', img, a)[0] for a in addrs]
    assert words == F.body_words(lambda i: addrs[i])


def test_it_follows_the_resampler_and_never_runs_without_it(monkeypatch):
    """The 3/4 guard is only sufficient because the resample's step is 0.749.
    With the stock 1:1 body the furthest read is dest_w-1 and a raised bound
    would run off the surface, so this must not engage on its own."""
    import ff7nx_fbresample as R
    monkeypatch.delenv(F.FIT_ENV, raising=False)
    monkeypatch.setenv(R.RESAMPLE_ENV, '0')
    assert R.enabled() is False
    assert F.enabled() is False
    monkeypatch.setenv(R.RESAMPLE_ENV, '1')
    assert F.enabled() is True
    monkeypatch.setenv(F.FIT_ENV, '0')
    assert F.enabled() is False
