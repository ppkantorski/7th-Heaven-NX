#!/usr/bin/env python3
"""tests/test_fbwindow.py -- a capture rect is slid back onto the surface.

The point of the module is that after it runs, the copy writes the FULL
fb_tex.w x fb_tex.h the UV space addresses -- so there are no unwritten texels
for the mesh to sample. That is what these execute.

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

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as E                                           # noqa: E402
import ff7nx_fbwindow as W                                     # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')

BASE = 0xC00000
HDR = 0x700000


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def _run(fbw, fbh, x, y, surf_w, surf_h):
    """Execute the cave, then compute what the stock loop would go on to do."""
    mem = E.Mem()
    for off, v in ((W.FB_X_OFF, x), (W.FB_Y_OFF, y),
                   (W.FB_W_OFF, fbw), (W.FB_H_OFF, fbh)):
        mem.setu(HDR + off, v, 4)
    cpu = E.Cpu(mem)
    cpu.set(W.TEX, HDR)
    cpu.set(W.SURF_W, surf_w)
    cpu.set(W.SURF_H, surf_h)
    cpu.set(23, 0xDEAD)                       # w23, the stock load's target
    addrs = [BASE + 4 * i for i in range(W.N_WORDS)]
    code = dict(zip(addrs, W.body_words(lambda i: addrs[i])))
    pc = cpu.run(BASE, None, code=code, start_pc=BASE)
    assert pc == W.RETURN_VA, hex(pc)
    nx = mem.u(HDR + W.FB_X_OFF, 4)
    ny = mem.u(HDR + W.FB_Y_OFF, 4)
    cols = min(surf_w, nx + fbw) - nx
    rows = min(surf_h, ny + fbh) - ny
    return dict(x=nx, y=ny, cols=cols, rows=rows,
                w23=cpu.get(23), surf_h_reg=cpu.get(W.SURF_H))


def test_the_stock_word_and_the_neighbourhood_are_what_the_module_claims(img):
    assert struct.unpack_from('<I', img, W.HOOK)[0] == W.HOOK_STOCK
    assert W.verify(img) == []
    assert W.read_state(img).startswith('capture window stock')


def test_a_rect_that_fits_is_not_touched_at_all():
    """The safety argument, executed. Nothing moves, and the stock load still
    happens, so a capture that fits runs the original instruction sequence."""
    for surf in ((640, 480), (320, 240), (1280, 720)):
        for fbw, fbh, x, y in ((320, 480, 0, 0),       # a swirl half
                               (160, 240, 0, 0),
                               (256, 256, 0, 0),
                               (64, 64, 8, 8),
                               (192, 200, 100, 20)):
            if x + fbw > surf[0] or y + fbh > surf[1]:
                continue
            r = _run(fbw, fbh, x, y, *surf)
            assert (r['x'], r['y']) == (x, y), (surf, fbw, fbh, x, y)
            assert r['w23'] == x                   # the stock load ran
            assert (r['cols'], r['rows']) == (fbw, fbh)


def test_a_rect_hanging_off_the_edge_is_slid_back_and_written_in_full():
    """This is the black squares. Kujata's rect is 256 wide at origin 80; on a
    320-wide surface the stock code writes 240 columns and the UV space
    addresses 256, so sixteen columns on the right are never written."""
    before = min(320, 80 + 256) - 80
    assert before == 240                       # what you have been seeing

    r = _run(256, 256, 80, 0, 320, 240)
    assert r['x'] == 320 - 256                 # slid left, not truncated
    assert r['cols'] == 256                    # the FULL UV width now written
    assert r['rows'] == 240                    # height still cannot fit
    assert r['y'] == 0


def test_the_full_uv_width_is_written_whenever_the_surface_can_hold_it():
    """The invariant: if the surface is at least as wide as the rect, the copy
    writes every column the UV space addresses -- at any origin."""
    for surf_w, surf_h in ((320, 240), (640, 480), (854, 480), (1280, 720)):
        for fbw, fbh in ((64, 64), (160, 176), (240, 240), (256, 256),
                         (320, 240)):
            for x, y in ((0, 0), (40, 0), (80, 0), (300, 10), (600, 200)):
                r = _run(fbw, fbh, x, y, surf_w, surf_h)
                if fbw <= surf_w:
                    assert r['cols'] == fbw, (surf_w, fbw, x)
                if fbh <= surf_h:
                    assert r['rows'] == fbh, (surf_h, fbh, y)
                assert r['x'] >= 0 and r['y'] >= 0


def test_a_rect_bigger_than_the_whole_surface_pins_to_zero():
    """It cannot be made to fit, so the origin goes to 0 and ff7nx_fbfit takes
    over -- the texture shrinks to what was written rather than the rect
    hanging off one side."""
    r = _run(512, 512, 0, 0, 320, 240)
    assert (r['x'], r['y']) == (0, 0)
    assert (r['cols'], r['rows']) == (320, 240)


def test_the_surface_height_register_survives():
    """w0 holds the surface height and is compared again at +0x10D716C. If the
    cave clobbered it the row count would be garbage for every capture."""
    for surf_h in (240, 480, 720):
        r = _run(256, 256, 80, 0, 320, surf_h)
        assert r['surf_h_reg'] == surf_h


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv(W.WINDOW_ENV, '1')
    out = tmp_path / 'main_window'
    shutil.copy(MAIN, out)
    assert W.apply_all(out, log=lambda *_: None) == 0
    got = nxmap.Main(str(out)).img
    assert W.verify(got) == []
    assert W.installed(got) is True
    assert W.apply_all(out, log=lambda *_: None) == 0
    assert W.apply_all(out, revert=True, log=lambda *_: None) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()


def test_it_writes_the_hook_and_its_cave_and_nothing_else(tmp_path,
                                                          monkeypatch):
    monkeypatch.setenv(W.WINDOW_ENV, '1')
    out = tmp_path / 'main_scope'
    shutil.copy(MAIN, out)
    before = nxmap.Main(str(out)).img
    assert W.apply_all(out, log=lambda *_: None) == 0
    after = nxmap.Main(str(out)).img
    diff = [i for i in range(0, len(before), 4)
            if before[i:i + 4] != after[i:i + 4]]
    logical, physical = W._walk(after)
    assert len(logical) == W.N_WORDS
    assert set(diff) == set(physical) | {W.HOOK}
    for va in sorted(W.ANCHORS):
        assert struct.unpack_from('<I', after, va)[0] == W.ANCHORS[va], hex(va)


def test_the_installed_cave_executes_the_same_way_as_the_generator(
        tmp_path, monkeypatch):
    monkeypatch.setenv(W.WINDOW_ENV, '1')
    out = tmp_path / 'main_roundtrip'
    shutil.copy(MAIN, out)
    assert W.apply_all(out, log=lambda *_: None) == 0
    img = nxmap.Main(str(out)).img
    addrs, _ = W._walk(img)
    words = [struct.unpack_from('<I', img, a)[0] for a in addrs]
    assert words == W.body_words(lambda i: addrs[i])


def test_it_coexists_with_the_fit_and_the_resampler(tmp_path, monkeypatch):
    import ff7nx_fbfit as FT
    import ff7nx_fbresample as R
    monkeypatch.setenv(W.WINDOW_ENV, '1')
    monkeypatch.setenv(FT.FIT_ENV, '1')
    monkeypatch.setenv('SEVENTH_NX_FB_PROBE', '0')
    out = tmp_path / 'main_chain'
    shutil.copy(MAIN, out)
    for mod in (R, FT, W):
        assert mod.apply_all(out, log=lambda *_: None) == 0
    img = nxmap.Main(str(out)).img
    for mod in (R, FT, W):
        assert mod.verify(img) == [], mod.__name__
    for mod in (W, FT, R):
        assert mod.apply_all(out, revert=True, log=lambda *_: None) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()


def test_off_puts_it_back(monkeypatch):
    monkeypatch.setenv(W.WINDOW_ENV, '0')
    assert W.enabled() is False
    monkeypatch.delenv(W.WINDOW_ENV, raising=False)
    assert W.enabled() is True
