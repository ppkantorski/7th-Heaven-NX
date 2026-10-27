"""
tests/test_effectwide.py -- Ultima and KOTR full-frame effect geometry.

Nothing here trusts a capstone mnemonic. `movz`, `movn` and `orr Wd,wzr,#imm`
all print as `mov`, and that alias has produced three wrong answers in this
project's history, so every assertion compares the literal 32-bit word or the
value an emulated execution actually produces.

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
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import arm64emu                      # noqa: E402
import ff7nx_effectwide as EW        # noqa: E402
import nxmap                         # noqa: E402

STOCK = ROOT / 'game_data_files' / 'exefs' / 'main'
pytestmark = pytest.mark.skipif(not STOCK.exists(),
                                reason='no stock exefs/main in this tree')


def word(img, va):
    return struct.unpack('<I', img[va:va + 4])[0]


@pytest.fixture(scope='module')
def stock_img():
    return nxmap.Main(str(STOCK)).img


@pytest.fixture(scope='module')
def patched(tmp_path_factory):
    """The shipping build."""
    d = tmp_path_factory.mktemp('effectwide')
    p = d / 'main'
    shutil.copy(STOCK, p)
    assert EW.apply_all(p, log=lambda *_: None) == 0
    return p


ALL_SITES = ({EW.ULT_A_LEFT, EW.ULT_A_RIGHT, EW.ULT_B_BASE,
              EW.ULT_STEP_A, EW.ULT_STEP_B, EW.KOTR_X_START,
              EW.KOTR_ROW2_HEIGHT, EW.KOTR_EXTRA_HOOK,
              EW.POL_W, EW.POL_H, EW.POL_DOT_X, EW.POL_BG_X}
             | set(EW.ULT_B_SPAN))


# --- encoders -------------------------------------------------------------

def test_enc_const_round_trips_through_the_emulator():
    for v in (-107, -1, 0, 1, 332, 427, 480, 854, 0xFFFF - 1):
        cpu = arm64emu.Cpu(arm64emu.Mem())
        cpu.run(0x1000, [EW.enc_const(19, v), 0xD503201F], max_steps=8)
        got = cpu.get(19, w=True)
        got = got - (1 << 32) if got & 0x80000000 else got
        assert got == v, 'enc_const(19, %d) produced %d' % (v, got)


def test_span_immediate_is_right_minus_left():
    # add w8, w22, #imm must carry the full left..right distance.
    assert EW.ULT_B_SPAN_WIDE == EW.ULT_B_SPAN_STOCK - (0x13F << 10) + (534 << 10)
    assert 534 == EW.WIDE_W // 2 - EW.WIDE_X


# --- the stock words are what the module claims ---------------------------

def test_every_stock_word_matches_the_image(stock_img):
    assert word(stock_img, EW.ULT_A_LEFT) == EW.ULT_A_LEFT_STOCK
    assert word(stock_img, EW.ULT_A_RIGHT) == EW.ULT_A_RIGHT_STOCK
    assert word(stock_img, EW.ULT_B_BASE) == EW.ULT_B_BASE_STOCK
    for va in EW.ULT_B_SPAN:
        assert word(stock_img, va) == EW.ULT_B_SPAN_STOCK
    assert word(stock_img, EW.ULT_STEP_A) == EW.ULT_STEP_A_STOCK
    assert word(stock_img, EW.ULT_STEP_B) == EW.ULT_STEP_B_STOCK


def test_every_anchor_matches_the_image(stock_img):
    for va, expected in EW.ULT_ANCHORS.items():
        assert word(stock_img, va) == expected, '+0x%X' % va


def test_w22_redefinition_anchor_is_not_a_movz(stock_img):
    """The site that ends w22's live range is ORR-immediate, not MOVZ.

    If this ever became a real MOVZ the live-range argument would still hold,
    but the assertion exists to keep the alias trap documented in code.
    """
    w = word(stock_img, 0x00692840)
    assert (w >> 23) & 0x1FF != 0b010100101, 'that word is a MOVZ after all'
    assert w == 0x320823F6


# --- what the patched image actually computes -----------------------------

def _exec(words, pre=None):
    cpu = arm64emu.Cpu(arm64emu.Mem())
    if pre:
        pre(cpu)
    cpu.run(0x1000, list(words) + [0xD503201F], max_steps=64)
    return cpu


def _cave_body(img, hook):
    return [word(img, va) for va in EW._walk_cave(img, hook)[0]
            if EW._branch_target(va, word(img, va)) is None]


def _halves(dword):
    """The x86 body doubles both 16-bit halves before submitting."""
    def s16(v):
        v &= 0xFFFF
        return v - 0x10000 if v & 0x8000 else v
    x, y = s16(dword), s16(dword >> 16)
    return s16(x * 2), s16(y * 2)


def test_ultima_expanding_phase_spans_the_whole_frame(patched):
    img = nxmap.Main(str(patched)).img
    left = _exec(_cave_body(img, EW.ULT_A_LEFT)).get(8, w=True)
    right = _exec([word(img, EW.ULT_A_RIGHT)]).get(8, w=True)
    lx, _ = _halves(left)
    rx, _ = _halves(right)
    assert lx <= EW.WIDE_X, 'left edge %d does not reach %d' % (lx, EW.WIDE_X)
    assert rx >= EW.WIDE_W + EW.WIDE_X, 'right edge %d short of %d' % (
        rx, EW.WIDE_W + EW.WIDE_X)


def test_ultima_iris_phase_spans_the_whole_frame_and_keeps_its_y(patched):
    img = nxmap.Main(str(patched)).img
    base = _exec(_cave_body(img, EW.ULT_B_BASE)).get(22, w=True)
    span = _exec([word(img, EW.ULT_B_SPAN[0])],
                 pre=lambda c: c.set(22, base, w=True)).get(8, w=True)
    lx, ly = _halves(base)
    rx, ry = _halves(span)
    assert lx <= EW.WIDE_X
    assert rx >= EW.WIDE_W + EW.WIDE_X
    # The iris now starts at the centre of the full frame, not of the 4:3 box.
    assert ly == EW.WIDE_H // 2, 'the iris is not centred: %d' % ly
    # FFNx's own form of this patch shears the band by one pre-doubling unit.
    assert 0 <= ry - ly <= 2, 'unexpected vertical shear %d' % (ry - ly)


def test_both_halves_of_the_iris_use_the_same_span_word(patched):
    img = nxmap.Main(str(patched)).img
    a, b = (word(img, va) for va in EW.ULT_B_SPAN)
    assert a == b == EW.ULT_B_SPAN_WIDE


def test_the_shared_battle_rect_globals_are_never_written(patched, stock_img):
    """0x9AAD4C/0x9AAD5C feed the viewport and the 0x5953FD bounds test.

    Widening them froze world-map battle entry (HANDOFF-273).  This module
    materialises its values at the two LOADS instead, so every word outside
    the declared sites must be untouched.
    """
    img = nxmap.Main(str(patched)).img
    declared = ALL_SITES
    changed = set()
    for off in range(0, min(len(img), len(stock_img)) - 3, 4):
        if img[off:off + 4] != stock_img[off:off + 4]:
            changed.add(off)
    # Everything else that changed must be zero padding we turned into a cave.
    for va in changed - declared:
        assert word(stock_img, va) == 0, (
            'non-padding word +0x%X was modified' % va)


def test_revert_is_byte_exact(tmp_path):
    p = tmp_path / 'main'
    shutil.copy(STOCK, p)
    assert EW.apply_all(p, log=lambda *_: None) == 0
    assert p.read_bytes() != STOCK.read_bytes()
    assert EW.apply_all(p, revert=True, log=lambda *_: None) == 0
    assert p.read_bytes() == STOCK.read_bytes()


def test_apply_is_idempotent(patched):
    before = Path(patched).read_bytes()
    assert EW.apply_all(patched, log=lambda *_: None) == 0
    assert Path(patched).read_bytes() == before


def test_module_self_verify_passes(patched):
    assert EW._verify(patched, log=lambda *_: None) == 0


def test_cli_verify_exits_clean(patched):
    r = subprocess.run([sys.executable, str(__import__('pathlib').Path(nxpaths.src_file('ff7nx_effectwide.py'))),
                        str(patched), '--verify'],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_vertical_walk_reaches_the_bottom_of_the_frame(patched):
    """step * count, doubled, must cover the full 480 -- not 360."""
    img = nxmap.Main(str(patched)).img
    step_a = _exec([word(img, EW.ULT_STEP_A)]).get(8, w=True)
    step_b = _exec([word(img, EW.ULT_STEP_B)]).get(8, w=True)
    count = 30                      # obj+0x40, deliberately left stock
    assert step_a * count * 2 == EW.WIDE_H, (
        '0x57A20A walks to %d, frame is %d' % (step_a * count * 2, EW.WIDE_H))
    assert step_b * count * 2 == EW.WIDE_H // 2, (
        '0x57A38C reaches %d from centre' % (step_b * count * 2))


def test_the_band_count_is_never_touched(patched, stock_img):
    """0x57A38C compares the band index against the phase argument, so the
    count is animation timing.  Only the step may move."""
    img = nxmap.Main(str(patched)).img
    for va in (0x00690D1C, 0x00690BBC):
        assert word(img, va) == word(stock_img, va) == 0x321F0FE8


def _bl_target(va, insn):
    return EW._bl_target(va, insn)


def test_kotr_stock_sites_and_recorded_submission_block_match(stock_img):
    assert word(stock_img, EW.KOTR_X_START) == EW.KOTR_X_START_STOCK
    assert (word(stock_img, EW.KOTR_ROW2_HEIGHT)
            == EW.KOTR_ROW2_HEIGHT_STOCK)
    assert word(stock_img, EW.KOTR_EXTRA_HOOK) == EW.KOTR_EXTRA_HOOK_STOCK
    got = tuple(word(stock_img, EW.KOTR_TEMPLATE_START + 4 * i)
                for i in range(len(EW.KOTR_TEMPLATE_STOCK)))
    assert got == EW.KOTR_TEMPLATE_STOCK
    for va, expected in EW.KOTR_ANCHORS.items():
        assert word(stock_img, va) == expected


def test_kotr_cave_has_one_looped_real_bg1_column_call(patched):
    img = nxmap.Main(str(patched)).img
    cave, why = EW._walk_cave(img, EW.KOTR_EXTRA_HOOK,
                              limit=EW.KOTR_CAVE_WALK_LIMIT)
    assert cave is not None, why
    calls = [_bl_target(va, word(img, va)) for va in cave]
    assert calls.count(EW.KOTR_DRAW_ARM) == 1
    assert sum((word(img, va) & 0x7F000000) == 0x35000000
               for va in cave) == 1


def test_kotr_extra_columns_execute_with_balanced_stack_and_threaded_tail(patched):
    """Run the installed scattered cave, including every translated push.

    The native callback models x86 RET by popping its translated return slot,
    returns the incoming draw-list tail + 1 through guest EAX, and destroys
    every caller-saved register.  The second call must still receive the first
    result, and the caller's guest ESP must finish exactly where it started.
    """
    img = nxmap.Main(str(patched)).img
    cave, why = EW._walk_cave(img, EW.KOTR_EXTRA_HOOK,
                              limit=EW.KOTR_CAVE_WALK_LIMIT)
    assert cave is not None, why
    code = {va: word(img, va) for va in [EW.KOTR_EXTRA_HOOK] + cave}

    mem = arm64emu.Mem()
    ctx = 0x600000000
    calls = []

    def draw(cpu):
        esp = mem.u(ctx + 0x10, 4)
        # The translated CALL owns one four-byte return slot below arg 1.
        args = [mem.u(cpu.guest_to_host(esp + 4 + 4 * i), 4)
                for i in range(3)]
        x = mem.u(cpu.guest_to_host(0x7F5CE0), 2)
        x = x - 0x10000 if x & 0x8000 else x
        calls.append((x, args))
        mem.setu(ctx, args[2] + 1, 4)       # guest EAX / returned list tail
        mem.setu(ctx + 0x10, esp + 4, 4)    # translated RET
        for reg in range(19):
            cpu.x[reg] = arm64emu.GARBAGE
        cpu.x[30] = arm64emu.GARBAGE

    cpu = arm64emu.Cpu(mem, native={EW.KOTR_DRAW_ARM: draw}, paged=True)
    cpu.set(23, ctx)
    cpu.set(19, 0xBFB2E0, w=True)
    cpu.set(21, EW.KOTR_W21_STOCK, w=True)
    esp0 = 0x900100
    mem.setu(ctx + 0x10, esp0, 4)
    mem.setu(cpu.guest_to_host(0x7F5CE0), 100, 2)
    mem.setu(cpu.guest_to_host(0xBFB2E0), 1000, 4)
    mem.setu(cpu.guest_to_host(0xBE1128), 0x200000, 4)

    exit_pc = cpu.run(EW.KOTR_EXTRA_HOOK, code, max_steps=1000)
    assert exit_pc == EW.KOTR_EXTRA_HOOK + 4
    assert [x for x, _args in calls] == [356, 612]
    assert [args for _x, args in calls] == [
        [0x7F5CE0, 0x20406C, 1000],
        [0x7F5CE0, 0x20406C, 1001],
    ]
    assert mem.u(cpu.guest_to_host(0xBFB2E0), 4) == 1002
    assert mem.u(ctx + 0x10, 4) == esp0
    assert cpu.get(21, w=True) == EW.KOTR_W21_STOCK


def test_kotr_five_columns_cover_every_scroll_phase(patched):
    img = nxmap.Main(str(patched)).img
    cpu = _exec([word(img, EW.KOTR_X_START)],
                pre=lambda c: c.set(8, 0x345, w=True))
    delta = 0x345 - cpu.get(22, w=True)
    cave, why = EW._walk_cave(img, EW.KOTR_EXTRA_HOOK,
                              limit=EW.KOTR_CAVE_WALK_LIMIT)
    assert cave is not None, why
    count = 3 + EW.KOTR_EXTRA_COLUMNS
    assert delta == 512
    assert count == 5
    for scroll in range(256):
        columns = [scroll - delta + 256 * i for i in range(count)]
        assert columns[0] <= EW.WIDE_X
        assert columns[-1] + 256 >= EW.WIDE_X + EW.WIDE_W
        assert all(b == a + 256 for a, b in zip(columns, columns[1:]))


def test_kotr_second_row_ends_exactly_at_frame_bottom(patched):
    img = nxmap.Main(str(patched)).img
    height = _exec([word(img, EW.KOTR_ROW2_HEIGHT)]).get(8, w=True)
    assert height == 224
    assert 256 + height == EW.WIDE_H


# --- BUILD 704: Pollensalta's Cold Breath ------------------------------------

def test_cold_breath_stock_words_and_anchors_match(stock_img):
    assert word(stock_img, EW.POL_W) == EW.POL_W_STOCK
    assert word(stock_img, EW.POL_H) == EW.POL_H_STOCK
    assert word(stock_img, EW.POL_DOT_X) == EW.POL_DOT_X_STOCK
    assert word(stock_img, EW.POL_BG_X) == EW.POL_BG_X_STOCK
    for va, expected in EW.POL_ANCHORS.items():
        assert word(stock_img, va) == expected, '+0x%X' % va


def test_cold_breath_dot_field_is_the_whole_frame(patched):
    """The game seeds and steps the dots modulo W and H (x86 0x547670,
    0x547D56); the drawer shifts x by -107.  W x H must be the frame."""
    img = nxmap.Main(str(patched)).img
    w = _exec([word(img, EW.POL_W)]).get(8, w=True)
    h = _exec([word(img, EW.POL_H)]).get(20, w=True)
    assert (w, h) == (EW.WIDE_W, EW.WIDE_H)
    body = _cave_body(img, EW.POL_DOT_X)
    assert body[0] == EW.POL_DOT_X_STOCK          # the displaced load first
    xs = []
    for stored in (0, w - 1):
        cpu = _exec(body[1:], pre=lambda c, v=stored: c.set(22, v))
        x = cpu.get(22, w=True) & 0xFFFF
        xs.append(x - 0x10000 if x & 0x8000 else x)
    assert xs == [EW.WIDE_X, EW.WIDE_X + EW.WIDE_W - 1]


def test_cold_breath_billboard_takes_ffnx_x_and_keeps_its_y(patched, stock_img):
    img = nxmap.Main(str(patched)).img
    body = _cave_body(img, EW.POL_BG_X)
    v = _exec(body[:1]).get(8, w=True)
    assert v - (1 << 32) == EW.WIDE_X - 200 == -307
    assert body[1] == EW.STRH_W8_X0
    # y still stores the shared -100 in w19
    assert word(img, 0x005A9960) == word(stock_img, 0x005A9960) == 0x529FF393
    assert word(img, 0x005A9984) == 0x79000013
