#!/usr/bin/env python3
"""tests/test_fbpath.py -- routing Kujata's capture to the port's GPU path.

The gate is EXECUTED under arm64emu for every capture shape in the game, not
compared word for word against its own generator.

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
import ff7nx_fbpath as P                                       # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')

BASE = 0x900000
HDR = 0x700000


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def _code(k=1):
    addrs = [BASE + 4 * i for i in range(P.N_WORDS)]
    return dict(zip(addrs, P.body_words(lambda i: addrs[i], k=k)))


def _route(fbw, fbh, texw, x, y, k=1, w8=1):
    """Run the gate and report which path it lands on."""
    mem = E.Mem()
    for off, v in ((P.FB_W_OFF, fbw), (P.FB_H_OFF, fbh), (P.TEX_W_OFF, texw),
                   (P.FB_X_OFF, x), (P.FB_Y_OFF, y)):
        mem.setu(HDR + off, v, 4)
    cpu = E.Cpu(mem)
    cpu.set(P.TEX, HDR)
    cpu.set(8, w8)
    # poison the borrowed registers so a leak shows up
    cpu.set(P.SCRATCH_A, 0xFEEDFACE)
    cpu.set(P.SCRATCH_B, 0xDEADBEEF)
    pc = cpu.run(BASE, None, code=_code(k), start_pc=BASE)
    return ('gpu' if pc == P.GPU_PATH else
            'stock' if pc == P.RETURN_VA else 'lost:%X' % pc), cpu


# --------------------------------------------------------------- the binary

def test_the_selector_and_both_path_entries_are_where_the_module_says(img):
    assert struct.unpack_from('<I', img, P.HOOK)[0] == P.HOOK_STOCK
    assert P.verify(img) == []
    assert P.read_state(img).startswith('capture path: stock')


def test_it_refuses_when_either_path_entry_moved(img):
    for va in (P.GPU_PATH, 0x10D70B8, 0x10D7264):
        bad = bytearray(img)
        struct.pack_into('<I', bad, va, 0xD503201F)
        assert P.verify(bad) != [], hex(va)


# ------------------------------------------------------------- the routing

def test_only_kujatas_capture_shape_reaches_the_gpu_path():
    """The swirl's own xscale is 2, so 'doubled' alone is not a discriminator.
    Execute the gate against every shape the game produces."""
    assert _route(512, 512, 256, 0, 0)[0] == 'gpu'            # Kujata at 2x
    for shape in ((320, 480, 160, 0, 0),      # the battle-entry swirl, x2
                  (320, 480, 160, 80, 0),
                  (256, 256, 128, 0, 0),      # the [0x9AD1A8] x2 capture
                  (256, 256, 256, 0, 0),      # a plain 1:1 capture
                  (64, 128, 64, 0, 0),
                  (192, 256, 192, 0, 0),
                  (512, 256, 256, 0, 0),      # doubled but not square
                  (512, 512, 256, 80, 0),     # doubled, square, wrong origin
                  (512, 512, 256, 0, 60),
                  (1024, 1024, 256, 0, 0)):   # x4 while the gate is at x2
        assert _route(*shape)[0] == 'stock', shape


def test_the_gate_follows_the_capture_scale():
    """At shift 2 it is the 1024 shape that is Kujata and the 512 one that is
    not -- the gate and ff7nx_fxcapscale cannot drift apart."""
    assert _route(1024, 1024, 256, 0, 0, k=2)[0] == 'gpu'
    assert _route(512, 512, 256, 0, 0, k=2)[0] == 'stock'
    assert _route(256, 256, 256, 0, 0, k=0)[0] == 'gpu'


def test_the_stock_route_leaves_w8_and_the_flags_exactly_as_it_found_them():
    """The instruction the hook replaced is `cmp w8, #1`, and the next
    instruction in the binary is `b.ne`. If the cave returned with the wrong
    flags every other capture in the game would take the wrong path."""
    for w8 in (0, 1, 2, 100, 0xFFFFFFFF):
        where, cpu = _route(256, 256, 256, 0, 0, w8=w8)   # not Kujata at k=1
        assert where == 'stock'
        assert cpu.get(8) == w8                            # w8 survived
        assert cpu.z == (1 if w8 == 1 else 0)              # what b.ne reads


def test_the_gate_reads_the_header_and_nothing_else():
    """Every load the gate makes must come from the tex header. Run it with the
    header at two different addresses and assert the routing is unchanged."""
    global HDR
    for base in (0x700000, 0x33000):
        HDR_was, HDR = HDR, base
        try:
            assert _route(512, 512, 256, 0, 0)[0] == 'gpu'
            assert _route(320, 480, 160, 0, 0)[0] == 'stock'
        finally:
            HDR = HDR_was


# ----------------------------------------------------- the source rectangle

UVBASE = 0xA00000


def _uv(fbw, fbh, texw, x, y, surf_w, surf_h, k=1):
    """Run the source-rect cave and report the rect Path B will normalise."""
    addrs = [UVBASE + 4 * i for i in range(P.UV_WORDS)]
    code = dict(zip(addrs, P.uv_words(lambda i: addrs[i], k=k)))
    mem = E.Mem()
    for off, v in ((P.FB_W_OFF, fbw), (P.FB_H_OFF, fbh), (P.TEX_W_OFF, texw),
                   (P.FB_X_OFF, x), (P.FB_Y_OFF, y)):
        mem.setu(HDR + off, v, 4)
    cpu = E.Cpu(mem)
    cpu.set(P.TEX, HDR)
    cpu.set(P.SURF_W, surf_w)
    cpu.set(P.SURF_H, surf_h)
    for r in (8, 9, 10, 11, P.SCRATCH_A, P.SCRATCH_B):
        cpu.set(r, 0xBADF00D)
    pc = cpu.run(UVBASE, None, code=code, start_pc=UVBASE)
    assert pc == P.UV_RETURN, hex(pc)
    return (cpu.get(8), cpu.get(9), cpu.get(10), cpu.get(11))


def test_kujatas_source_rect_becomes_the_whole_surface():
    """This is the black. Path B normalises (x, y, x+w, y+h) by the SURFACE's
    own size, so a 512x512 rect on a smaller surface samples past the edge --
    and the border is black. The rect must be the surface itself."""
    for surf in ((640, 480), (320, 240), (1280, 720), (854, 480)):
        x, y, w, h = _uv(512, 512, 256, 0, 0, *surf)
        assert (x, y) == (0, 0), surf
        assert (w, h) == surf, surf
        # which is to say: u0 = v0 = 0 and u1 = v1 = 1, exactly
        assert x / surf[0] == 0.0 and (x + w) / surf[0] == 1.0
        assert y / surf[1] == 0.0 and (y + h) / surf[1] == 1.0


def test_what_the_stock_rect_would_have_sampled():
    """The same arithmetic on the stock rect, to pin the size of the bug: on a
    320x240 surface a 512x512 rect covers 29% of the texture and the other 71%
    is border -- which is what the screenshot shows."""
    def covered(surf_w, surf_h, rect=512):
        return min(1.0, surf_w / rect) * min(1.0, surf_h / rect)

    assert abs(covered(640, 480) - 0.9375) < 1e-9      # 6% black
    assert abs(covered(320, 240) - 0.29296875) < 1e-9  # 71% black  <-- observed
    # the surface is therefore 320x240 at capture time, not 640x480
    assert covered(320, 240) < 0.30


def test_everything_else_keeps_its_own_rect():
    for shape in ((320, 480, 160, 0, 0),        # the battle-entry swirl
                  (256, 256, 256, 0, 0),        # a plain 1:1 capture
                  (512, 512, 256, 80, 0),       # doubled, wrong origin
                  (512, 256, 256, 0, 0),        # doubled, not square
                  (1024, 1024, 256, 0, 0)):     # x4 while the gate is at x2
        fbw, fbh, texw, x, y = shape
        assert _uv(*shape, 640, 480) == (x, y, fbw, fbh), shape


def test_the_source_rect_gate_follows_the_capture_scale():
    assert _uv(1024, 1024, 256, 0, 0, 640, 480, k=2) == (0, 0, 640, 480)
    assert _uv(512, 512, 256, 0, 0, 640, 480, k=2) == (0, 0, 512, 512)


# ------------------------------------------------------------- the install

def _env(monkeypatch, capscale, on='1'):
    monkeypatch.setenv('SEVENTH_NX_FX_CAPSCALE', str(capscale))
    monkeypatch.setenv(P.PATH_ENV, on)


def test_it_cannot_engage_below_capscale_2(monkeypatch):
    _env(monkeypatch, 1)
    assert P.enabled() is False          # the gate could not identify Kujata
    _env(monkeypatch, 2)
    assert P.enabled() is True
    _env(monkeypatch, 4)
    assert P.enabled() is True
    _env(monkeypatch, 2, on='0')
    assert P.enabled() is False


def test_a_plain_build_leaves_the_capture_path_alone(monkeypatch):
    """BUILD 287 turned this off. The five-condition gate keys on SHAPE, and a
    battle-swirl capture satisfies it too (FINDINGS-288), so it must not run
    until it identifies Kujata by something that cannot collide."""
    import ff7nx_fxcapscale as C
    monkeypatch.delenv(P.PATH_ENV, raising=False)
    monkeypatch.delenv('SEVENTH_NX_FX_CAPSCALE', raising=False)
    assert C.scale() == 1
    assert P.enabled() is False


def test_one_variable_puts_it_back(monkeypatch):
    monkeypatch.delenv('SEVENTH_NX_FX_CAPSCALE', raising=False)
    monkeypatch.setenv(P.PATH_ENV, '0')
    assert P.enabled() is False


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path, monkeypatch):
    import ff7nx_fxcapscale as C
    for capscale in (2, 4):
        _env(monkeypatch, capscale)
        out = tmp_path / ('main_path%d' % capscale)
        shutil.copy(MAIN, out)
        assert C.apply_all(out, log=lambda *_: None) == 0
        assert P.apply_all(out, log=lambda *_: None) == 0
        got = nxmap.Main(str(out)).img
        assert P.verify(got) == []
        assert 'GPU render-to-texture' in P.read_state(got)
        assert P.installed(got) == {2: 1, 4: 2}[capscale]
        assert P.apply_all(out, log=lambda *_: None) == 0           # idempotent
        assert P.apply_all(out, revert=True, log=lambda *_: None) == 0
        assert C.apply_all(out, revert=True, log=lambda *_: None) == 0
        assert out.read_bytes() == open(MAIN, 'rb').read()


def test_it_writes_the_hook_and_its_cave_and_nothing_else(tmp_path,
                                                          monkeypatch):
    _env(monkeypatch, 2)
    out = tmp_path / 'main_scope'
    shutil.copy(MAIN, out)
    before = nxmap.Main(str(out)).img
    assert P.apply_all(out, log=lambda *_: None) == 0
    after = nxmap.Main(str(out)).img
    diff = [i for i in range(0, len(before), 4)
            if before[i:i + 4] != after[i:i + 4]]
    assert P.HOOK in diff
    # the cave is chained through reclaimed alignment padding, so it costs a
    # bridge `b` per run; every changed word must be the hook or inside it
    own = set()
    for name, hook, stock, n, build, exits in P.SITES:
        logical, physical = P._walk_site(after, hook, n, exits)
        assert len(logical) == n, name
        own |= set(physical) | {hook}
    assert set(diff) == own, sorted(set(diff) - own)
    # Path A, Path B and the selector's neighbours are untouched
    for va in (0x10D70AC, 0x10D70B4, 0x10D70B8, P.GPU_PATH, 0x10D7264,
               0x10D72F4, 0x10D71F0, 0x10D5970):
        assert struct.unpack_from('<I', after, va)[0] == \
               struct.unpack_from('<I', before, va)[0], hex(va)


def test_the_installed_cave_executes_the_same_way_as_the_generator(
        tmp_path, monkeypatch):
    """Read the cave back out of the patched binary and run THAT."""
    _env(monkeypatch, 2)
    out = tmp_path / 'main_roundtrip'
    shutil.copy(MAIN, out)
    assert P.apply_all(out, log=lambda *_: None) == 0
    img = nxmap.Main(str(out)).img
    addrs, _ = P._walk(img)
    assert len(addrs) == P.N_WORDS
    words = [struct.unpack_from('<I', img, a)[0] for a in addrs]
    assert words == P.body_words(lambda i: addrs[i], k=1)


def test_it_coexists_with_the_resampler_and_the_capture_scale(tmp_path,
                                                              monkeypatch):
    """fxcapscale, fbpath and fbresample all land in one image, in build order,
    and each still verifies and reverts."""
    import ff7nx_fbresample as R
    import ff7nx_fxcapscale as C
    _env(monkeypatch, 2)
    monkeypatch.setenv('SEVENTH_NX_FB_PROBE', '0')
    out = tmp_path / 'main_chain'
    shutil.copy(MAIN, out)
    for mod in (C, P, R):
        assert mod.apply_all(out, log=lambda *_: None) == 0
    img = nxmap.Main(str(out)).img
    for mod in (C, P, R):
        assert mod.verify(img) == [], mod.__name__
    for mod in (R, P, C):
        assert mod.apply_all(out, revert=True, log=lambda *_: None) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()
