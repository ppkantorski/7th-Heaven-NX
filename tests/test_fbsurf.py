#!/usr/bin/env python3
"""tests/test_fbsurf.py -- the capture's resolution ceiling.

BUILD 309. The surface the framebuffer capture reads is created from a LITERAL
640x480 descriptor at +0x10D5970, and the whole display frame is blitted into
it by one full-target quad (+0x10DACB0), so 640x480 is the ceiling for every
capture in the game.

The rect that addresses that surface is computed by `make_framebuffer_tex` as
`value * K / virtual`, where K is currently the virtual size -- an identity.
Raising the surface and K together by one factor is five single-word patches.

Nothing here is asserted from the algebra: the five encodings are round-tripped
through capstone, and the rect arithmetic is EXECUTED under arm64emu at every
scale against every capture shape in the game.

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
import ff7nx_fbresample as R                                   # noqa: E402
import ff7nx_fbsurf as S                                       # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')

BASE = 0xE00000

# the capture shapes the game actually creates, as (x, y, w, h) in virtual
# 640x480 units -- Kujata's, the battle-entry swirl's halves, and the corners
SHAPES = ((0, 0, 256, 256), (80, 0, 256, 256), (0, 0, 320, 240),
          (320, 240, 320, 240), (0, 0, 640, 480), (160, 120, 160, 120),
          (1, 1, 3, 3), (639, 479, 1, 1))


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


# make_framebuffer_tex's rect arithmetic, in address order, with the struct
# copy and the unrelated stores between them left out. Every one of these is
# lifted from the image at test time -- only the three words under test are
# substituted -- so this executes the port's own multiply-and-divide.
RECT_VAS = (0x10DBBF0,                       # mov  w13, #0x1e0      (RH_SITE)
            0x10DBBF8, 0x10DBBFC,            # w12 = 0xCCCCCCCD
            0x10DBC3C,                       # add  w11, w27, w27, lsl #2
            0x10DBC40,                       # mul  w9,  w25, w13
            0x10DBC48, 0x10DBC4C,            # w8  = 0x88888889
            0x10DBC50,                       # umull x9, w9, w8
            0x10DBC54,                       # lsl  w11, w11, #7     (RX_SITE)
            0x10DBC58,                       # umull x11, w11, w12
            0x10DBC64,                       # mul  w10, w19, w13
            0x10DBC68,                       # umull x8, w10, w8
            0x10DBC6C,                       # lsr  x8, x8, #0x28    -> h
            0x10DBC70,                       # lsr  x11, x11, #0x29  -> x
            0x10DBC74,                       # lsr  x9, x9, #0x28    -> y
            0x10DBC78,                       # stp  w11, w9, [x26, #0x14]
            0x10DBC7C,                       # add  w9, w21, w21, lsl #2
            0x10DBC80,                       # lsl  w9, w9, #7       (RW_SITE)
            0x10DBC84,                       # umull x9, w9, w12
            0x10DBC88,                       # lsr  x9, x9, #0x29    -> w
            0x10DBC94)                       # stp  w9, w8, [x26, #0x1c]


def _rect(k, x, y, w, h):
    """Execute that arithmetic and report the (x, y, w, h) it stores."""
    want = S.words_for(k)
    image = nxmap.Main(MAIN).img
    code, addrs = {}, []
    for n, va in enumerate(RECT_VAS):
        at = BASE + 4 * n
        addrs.append(at)
        code[at] = want.get(va, struct.unpack_from('<I', image, va)[0])
    HDR = 0x700000
    mem = E.Mem()
    cpu = E.Cpu(mem)
    cpu.set(26, HDR)                       # the tex_header being filled
    cpu.set(27, x)                         # arg3 = x
    cpu.set(25, y)                         # arg4 = y
    cpu.set(21, w)                         # arg5 = w
    cpu.set(19, h)                         # arg6 = h
    cpu.run(BASE, None, code=code, start_pc=BASE)
    return (mem.u(HDR + 0x14, 4), mem.u(HDR + 0x18, 4),
            mem.u(HDR + 0x1c, 4), mem.u(HDR + 0x20, 4))


def test_the_five_stock_words_are_what_the_module_claims(img):
    """If any of them moved, every number in this module is about some other
    routine and it must refuse rather than write."""
    for va, want in sorted(S.words_for(1).items()):
        got = struct.unpack_from('<I', img, va)[0]
        assert got == want, '+0x%X is %08X, expected %08X' % (va, got, want)
    assert S.verify(img) == []
    assert S.read_state(img) == 'capture surface 640x480 (1x, stock)'


def test_every_anchor_the_five_words_feed_is_intact(img):
    for va, want in sorted(S.ANCHORS.items()):
        got = struct.unpack_from('<I', img, va)[0]
        assert got == want, '+0x%X is %08X, expected %08X' % (va, got, want)


def test_the_encodings_round_trip_through_capstone():
    """Every word this module can write, disassembled back. A hand-built
    encoding that assembles to something else is the one mistake that a test
    against my own generator cannot catch."""
    capstone = pytest.importorskip('capstone')
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    expect = {
        1: {S.SW_SITE: 'mov x8, #0x280', S.SH_SITE: 'movk x8, #0x1e0, lsl #32',
            S.RX_SITE: 'lsl w11, w11, #7', S.RW_SITE: 'lsl w9, w9, #7'},
        2: {S.SW_SITE: 'mov x8, #0x500', S.SH_SITE: 'movk x8, #0x3c0, lsl #32',
            S.RH_SITE: 'mov w13, #0x3c0',
            S.RX_SITE: 'lsl w11, w11, #8', S.RW_SITE: 'lsl w9, w9, #8'},
        4: {S.SW_SITE: 'mov x8, #0xa00', S.SH_SITE: 'movk x8, #0x780, lsl #32',
            S.RH_SITE: 'mov w13, #0x780',
            S.RX_SITE: 'lsl w11, w11, #9', S.RW_SITE: 'lsl w9, w9, #9'},
    }
    for k, want in expect.items():
        got = S.words_for(k)
        for va, text in want.items():
            ins = next(md.disasm(struct.pack('<I', got[va]), va))
            assert '%s %s' % (ins.mnemonic, ins.op_str) == text, (k, hex(va))


def test_scale_one_is_the_stock_image_word_for_word():
    assert S.words_for(1) == {S.SW_SITE: S.SW_STOCK, S.SH_SITE: S.SH_STOCK,
                              S.RH_SITE: S.RH_STOCK, S.RX_SITE: S.RX_STOCK,
                              S.RW_SITE: S.RW_STOCK}


def test_the_surface_and_the_rect_scale_by_the_same_factor(monkeypatch):
    """The whole correctness argument in one assertion: if the rect scaled and
    the surface did not, the capture would read off the end of it; if the
    surface scaled and the rect did not, it would read a corner."""
    for k in S.LEGAL:
        monkeypatch.setenv(S.SURF_ENV, str(k))
        assert S.surface() == (640 * k, 480 * k)
        shift = {1: 7, 2: 8, 4: 9, 8: 10}[k]
        w = S.words_for(k)
        assert w[S.RX_SITE] == S._lsl32(11, 11, shift)
        assert w[S.RW_SITE] == S._lsl32(9, 9, shift)
        assert w[S.SW_SITE] == S._movz64(8, 640 * k)
        assert w[S.SH_SITE] == S._movk64(8, 480 * k, 2)


@pytest.mark.parametrize('k', S.LEGAL)
def test_the_rect_arithmetic_executed_at_every_scale(k):
    """Run the binary's own multiply-and-divide with the patched constants,
    over every capture shape in the game, and require exactly `value * k`.

    This is the claim the whole module rests on -- that the port's identity
    scaling becomes a real scale when only the multiply moves -- and it is
    executed rather than argued."""
    for (x, y, w, h) in SHAPES:
        got = _rect(k, x, y, w, h)
        assert got == (x * k, y * k, w * k, h * k), (k, x, y, w, h, got)


def test_a_scaled_rect_still_fits_inside_the_scaled_surface():
    """A rect that fitted 640x480 must fit k*640 x k*480, for every shape and
    every scale -- the arithmetic is linear, and this is the check that it
    really is."""
    for k in S.LEGAL:
        sw, sh = 640 * k, 480 * k
        for (x, y, w, h) in SHAPES:
            gx, gy, gw, gh = _rect(k, x, y, w, h)
            assert gx + gw <= sw and gy + gh <= sh, (k, x, y, w, h)


def test_nonsense_falls_back_rather_than_writing_garbage(monkeypatch):
    # BUILD 335: 8 is legal now; 16 takes its place as the rejected one
    for bad in ('0', '3', '16', 'banana', '', '-2'):
        monkeypatch.setenv(S.SURF_ENV, bad)
        assert S.scale() == S.DEFAULT_SCALE


def test_the_resample_gate_follows_this_module(monkeypatch):
    """At the stock surface the gate word is IDENTICAL to `cmp w15, w16`, so
    turning this module off cannot change the resample by one byte. At k it
    becomes `cmp w15, w16, lsl #log2(k)`, which is what keeps the resample
    engaged instead of silently falling through to the stock loop."""
    import a64 as A
    # BUILD 337: the gate now carries the CAPTURE scale too, so this test --
    # which is about the SURFACE scale -- has to pin the other one.
    monkeypatch.setenv('SEVENTH_NX_FX_CAPSCALE', '1')
    monkeypatch.delenv('SEVENTH_NX_FX_CAPSCALE_Y', raising=False)
    monkeypatch.setenv(S.SURF_ENV, '1')
    assert R.surf_shift() == 0
    assert R.body_words(lambda i: 0x180000 + 4 * i,
                        how='punch')[2] == A.cmp_reg(15, 16)
    assert R.surface() == (640, 480)
    for k, shift in ((2, 1), (4, 2)):
        monkeypatch.setenv(S.SURF_ENV, str(k))
        assert R.surf_shift() == shift
        assert R.surface() == (640 * k, 480 * k)
        for how in ('punch', 'copy', 'fillhole', 'srcblack'):
            got = R.body_words(lambda i: 0x180000 + 4 * i, how=how)[2]
            assert got == R.cmp_reg_lsl(15, 16, shift), (how, k)


def test_the_gate_executed_keeps_a_scaled_capture_and_drops_the_others(
        monkeypatch):
    """Executed, not read: at k = 2 a 512-wide capture of a 256-wide sheet must
    enter the body, and a 256/256 one must land on the stock loop."""
    monkeypatch.setenv('SEVENTH_NX_FX_CAPSCALE', '1')
    monkeypatch.delenv('SEVENTH_NX_FX_CAPSCALE_Y', raising=False)
    monkeypatch.setenv(S.SURF_ENV, '2')
    monkeypatch.setenv(R.PROBE_ENV, 'punch')
    n = R.n_words('punch')
    addrs = [BASE + 4 * i for i in range(n)]
    code = dict(zip(addrs, R.body_words(lambda i: addrs[i], how='punch')))
    HDR = 0x700000
    for fbw, texw, expect_stock in ((512, 256, False), (256, 256, True),
                                    (1024, 256, True), (512, 512, True)):
        mem = E.Mem()
        mem.setu(HDR + R.FB_W_OFF, fbw, 4)
        mem.setu(HDR + R.TEX_W_OFF, texw, 4)
        cpu = E.Cpu(mem)
        for r, v in ((20, HDR), (10, 0), (12, 0), (11, 0x900003),
                     (13, 0x800003), (0, 0x800000), (19, 1280)):
            cpu.set(r, v)
        pc = cpu.run(BASE, None, code=code, start_pc=BASE)
        if expect_stock:
            assert pc == R.STOCK_LOOP, (fbw, texw, hex(pc))
        else:
            assert pc != R.STOCK_LOOP, (fbw, texw, hex(pc))


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path, monkeypatch):
    for k in (2, 4):
        monkeypatch.setenv(S.SURF_ENV, str(k))
        out = tmp_path / ('main_surf%d' % k)
        shutil.copy(MAIN, out)
        assert S.apply_all(out, log=lambda *_: None) == 0
        got = nxmap.Main(str(out)).img
        assert S.verify(got) == []
        assert S.installed(got) == k
        assert S.read_state(got).startswith('capture surface %d' % (640 * k))
        assert S.apply_all(out, log=lambda *_: None) == 0
        assert S.apply_all(out, revert=True, log=lambda *_: None) == 0
        assert out.read_bytes() == open(MAIN, 'rb').read()


def test_it_writes_those_five_words_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setenv(S.SURF_ENV, '2')
    out = tmp_path / 'main_scope'
    shutil.copy(MAIN, out)
    before = nxmap.Main(str(out)).img
    assert S.apply_all(out, log=lambda *_: None) == 0
    after = nxmap.Main(str(out)).img
    diff = {i for i in range(0, len(before), 4)
            if before[i:i + 4] != after[i:i + 4]}
    assert diff == set(S.words_for(1)), [hex(x) for x in sorted(diff)]
    for va, want in sorted(S.ANCHORS.items()):
        assert struct.unpack_from('<I', after, va)[0] == want, hex(va)


def test_it_refuses_when_the_routine_it_targets_has_moved(img):
    for va in sorted(S.ANCHORS)[:4] + [S.SW_SITE, S.RX_SITE]:
        bad = bytearray(img)
        struct.pack_into('<I', bad, va, 0xD503201F)          # nop
        assert S.verify(bad) != [], hex(va)
