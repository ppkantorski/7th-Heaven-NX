"""BUILD 410 -- the Escape ripple reaches below the UI, sampling included.

    "is there a way for the image to also include below the top of the ui
     correctly? it looks like its at least using the full area above the ui now"

Build 409 made the effect one undistorted full-width snapshot that is exactly
1:1 -- and therefore exactly 336 tall. This module raises the geometry and the
SAMPLING together (yscale 2 -> 3, y' = 3y/2) and moves the ripple's own centre
with them (raw 120 -> 80) so the rings stay on screen y 240.

Build 407 did the first half of that on its own and came back as "not
centered, focused higher"; these tests encode why, so it cannot recur.

Superseded header:
BUILD 407 -- the Escape ripple reaches below the UI.

"the image should be 1:1 with the entire 16:9 screen including below the ui
boundary, no scale or stretch distortion, and the effect should ripple across
the entire thing"

escapescale does X; this does Y. Together with escapeuv the mesh covers the
whole frame and shows the capture 1:1.

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
import hashlib
import shutil
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import a64 as A                                                # noqa: E402
import arm64emu as E                                           # noqa: E402
import nxmap                                                   # noqa: E402
import ff7nx_escapetall as T                                   # noqa: E402
import ff7nx_escapescale as S                                  # noqa: E402
import ff7nx_escapeuv as U                                     # noqa: E402

MAIN = ROOT / 'exefs' / 'main'
needs_main = pytest.mark.skipif(not MAIN.exists(), reason='no exefs/main')


@pytest.fixture(scope='module')
def m():
    return nxmap.Main(str(MAIN))


# ------------------------------------------------------------- the model

def test_the_three_numbers_are_derived_not_fitted():
    """yscale, the geometry ratio and the ripple centre are not independent.
    The module asserts the relations itself; this checks it really does."""
    assert T.arithmetic_is_consistent()
    assert (T.MESH_SCALE, T.GEO_NUM, T.GEO_DEN) == (2, 3, 2)
    assert T.YSCALE_NEW == 3
    assert T.RIPPLE_C_NEW == 0x50 and T.RIPPLE_C_OLD == 0x78
    # 1:1 -- the sampling slope per raw unit equals the geometry slope
    assert T.YSCALE_NEW == T.MESH_SCALE * T.GEO_NUM / T.GEO_DEN
    # and the ring centre lands on the middle of the frame
    assert T.expected(T.RIPPLE_C_NEW * T.MESH_SCALE) == T.FRAME_H // 2


def test_the_geometry_reaches_the_bottom_and_starts_at_the_top():
    assert T.expected(0) == 0                       # top edge stays put
    assert T.expected(T.MESH_H) == 504              # past 480, which clips
    assert T.expected(T.MESH_H) >= T.FRAME_H
    # the last fully-visible grid row, and how much falls off
    assert T.expected(20 * 8 * T.MESH_SCALE) == T.FRAME_H
    assert T.expected(T.MESH_H) - T.FRAME_H == 24


def test_it_is_uniform_and_monotonic():
    """Uniform matters: a piecewise version distorts one half of the picture,
    which is what build 407's centre-anchored expansion did."""
    prev = -1
    for y in range(0, T.MESH_H + 1):
        v = T.expected(y)
        assert v >= prev
        prev = v
        # one uniform slope everywhere, to within the integer floor
        assert abs(v - y * T.GEO_NUM / T.GEO_DEN) < 1, (y, v)


def test_1_to_1_over_every_visible_grid_row():
    """The whole point: screen row == staging row, for every row on screen."""
    for row in range(T.ROWS if hasattr(T, 'ROWS') else 22):
        raw = row * 8
        screen = T.expected(raw * T.MESH_SCALE)
        staging = raw * T.YSCALE_NEW           # v = raw, staging = v * yscale
        if screen <= T.FRAME_H:
            assert screen == staging, (row, screen, staging)


def test_the_old_global_stretch_would_have_moved_the_origin():
    """Why build 407 read as 'not centered, focused higher': 10/7 with the
    centre left at raw 120."""
    assert 240 * 10 // 7 == 342
    # and the same 10/7 with the centre moved would have been fine too --
    # it is the pairing that was missing, not the ratio
    assert (120 * T.MESH_SCALE) * 10 // 7 == 342
    assert T.expected(T.RIPPLE_C_NEW * T.MESH_SCALE) == 240


# -------------------------------------------------------- the cave, emulated

def _run(y):
    base = 0x900000
    words = T.body('w8', 'w9')
    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=0x700000000)
    cpu.x[8] = y & 0xFFFFFFFF
    cpu.x[9] = 0xDEADBEEF
    cpu.run(base, words, max_steps=64)
    return cpu.x[8] & 0xFFFFFFFF


def test_the_emitted_words_compute_the_model():
    for y in (0, 8, 16, 80, 160, 168, 200, 300, 332, 336):
        assert _run(y) == T.expected(y), y


def test_the_body_is_three_words_and_decodes():
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    words = T.body('w8', 'w9')
    assert len(words) == 3
    txt = []
    for w in words:
        d = list(md.disasm(struct.pack('<I', w), 0))
        assert d, 'undecodable word 0x%08X' % w
        txt.append('%s %s' % (d[0].mnemonic, d[0].op_str))
    assert txt == ['lsl w9, w8, #1', 'add w8, w8, w9', 'asr w8, w8, #1'], txt


# ---------------------------------------------- the five in-place words

@needs_main
def test_the_ripple_sites_are_anchored_on_their_predecessor(m):
    """`sub w8, w8, #0x78` appears four more times as the draw block's local
    re-centring. Those must not move, so each ripple site carries the word in
    front of it as its witness."""
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    for va, (stock, wva, wword) in T.RIPPLE_Y_SITES.items():
        assert T._w(m.img, va) == stock, hex(va)
        assert T._w(m.img, wva) == wword, hex(wva)
        assert wva == va - 4, (hex(wva), hex(va))
        pred = next(md.disasm(struct.pack('<I', wword), wva))
        assert pred.mnemonic in ('lsl', 'asr'), pred.mnemonic
    # the four draw-block twins carry the same constant and a different shape
    draw = [0x83CD64, 0x83CE44, 0x83CF24, 0x83D004]
    for va in draw:
        assert va in T.GUARDS
        assert T._w(m.img, va) == 0x5101E108        # sub w8, w8, #0x78
        pred = next(md.disasm(struct.pack('<I', T._w(m.img, va - 4)), va - 4))
        assert pred.mnemonic == 'ldrsh', pred.mnemonic
    assert not set(draw) & set(T.RIPPLE_Y_SITES)


@needs_main
def test_the_x_twins_prove_the_axis(m):
    """If a future build made these Y sites, the module must refuse rather
    than move the horizontal centre."""
    for va in (0x839EE8, 0x83C02C, 0x83C190):
        assert T.GUARDS[va] == T._w(m.img, va)
        assert (T._w(m.img, va) >> 10) & 0xFFF == 0xA0     # still 160


@needs_main
def test_the_yscale_sites_are_the_second_of_two_identical_pairs(m):
    """obj+0x10C (xscale) and obj+0x110 (yscale) are written from the same
    guest slot by two separate load/store pairs, so only the second moves."""
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    for va, xoff, yoff in ((0x83B920, 0x83B8EC, 0x83B924),
                           (0x83BB8C, 0x83BB58, 0x83BB90)):
        assert va in T.YSCALE_SITES
        assert T._w(m.img, va) == T.LDR_W20
        # the store right after it targets +0x110, and the xscale one is
        # a separate, earlier `add w0, w8, #0x10c`
        for off, want in ((xoff, '#0x10c'), (yoff, '#0x110')):
            d = next(md.disasm(struct.pack('<I', T._w(m.img, off)), off))
            assert d.op_str.endswith(want), (hex(off), d.op_str)
    d = next(md.disasm(struct.pack('<I', T.MOV_W20_3), 0))
    assert (d.mnemonic, d.op_str) == ('mov', 'w20, #3')
    # MOVZ, not an ORR-immediate -- build 406's revert bug was exactly that
    assert T.MOV_W20_3 & 0x7F800000 == 0x52800000


@needs_main
def test_word_plan_round_trips_and_mutations_are_caught(m):
    ps, pr = T.plan_words(m.img)
    assert pr == [] and len(ps) == 5
    img = bytearray(m.img)
    for p in ps:
        struct.pack_into('<I', img, int(p['va'], 16),
                         int.from_bytes(bytes.fromhex(p['set']), 'little'))
    assert T.words_installed(bytes(img)) is True
    assert T.verify_words(bytes(img)) == []
    ps2, pr2 = T.plan_words(bytes(img), revert=True)
    assert pr2 == [] and len(ps2) == 5
    for p in ps2:
        struct.pack_into('<I', img, int(p['va'], 16),
                         int.from_bytes(bytes.fromhex(p['set']), 'little'))
    assert bytes(img) == m.img
    # every guard is load-bearing
    for va in sorted(T.GUARDS):
        orig = struct.unpack_from('<I', img, va)[0]
        struct.pack_into('<I', img, va, orig ^ 0x400)
        assert T.verify_words(bytes(img)), hex(va)
        struct.pack_into('<I', img, va, orig)
    assert T.verify_words(bytes(img)) == []


# --------------------------------------------------------------- the site

@needs_main
def test_the_anchor_is_unique_and_disjoint_from_the_x_anchor():
    m = nxmap.Main(str(MAIN))
    found, why = T.find_hook(m)
    assert found is not None, why
    yhook, yval, yscratch = found
    xfound, xwhy = S.find_hook(m)
    assert xfound is not None, xwhy
    xhook, _xv, _xs = xfound
    assert yhook != xhook, 'the two hooks are the same word'


@needs_main
def test_the_hook_is_the_store_that_finishes_y():
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    m = nxmap.Main(str(MAIN))
    hook, val, _s = T.find_hook(m)[0]
    w = struct.unpack_from('<I', m.img, hook)[0]
    i = next(md.disasm(struct.pack('<I', w), hook))
    assert i.mnemonic == 'str'
    assert i.op_str.startswith(val + ',')


@needs_main
def test_apply_revert_is_byte_exact_and_idempotent(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    h0 = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert T.apply(dst, log=lambda *a: None) == 0
    h1 = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert h1 != h0
    assert T.apply(dst, log=lambda *a: None) == 0          # no-op
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == h1
    assert T.apply(dst, revert=True, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == h0


@needs_main
def test_all_three_escape_patches_coexist(tmp_path):
    """The whole fix, applied and reverted in the order the build uses."""
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    h0 = hashlib.sha256(dst.read_bytes()).hexdigest()
    quiet = lambda *a: None                                 # noqa: E731
    assert S.apply(dst, log=quiet) == 0
    assert T.apply(dst, log=quiet) == 0
    assert U.apply(dst, log=quiet) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() != h0
    assert U.apply(dst, revert=True, log=quiet) == 0
    assert T.apply(dst, revert=True, log=quiet) == 0
    assert S.apply(dst, revert=True, log=quiet) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == h0


@needs_main
def test_the_three_caves_do_not_overlap(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    quiet = lambda *a: None                                 # noqa: E731
    seen = set()
    for mod in (S, T, U):
        m = nxmap.Main(str(dst))
        ps, _n, errs = mod.plan(m)
        assert errs == [], (mod.__name__, errs)
        vas = {int(p['va'], 16) for p in ps}
        assert not (vas & seen), (mod.__name__, sorted(vas & seen))
        seen |= vas
        assert mod.apply(dst, log=quiet) == 0
