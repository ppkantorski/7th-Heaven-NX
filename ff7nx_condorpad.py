#!/usr/bin/env python3
r"""
ff7nx_condorpad.py -- Fort Condor: 360-degree stick cursor, a little slower
(BUILD 569). exefs/main only; one cave in the dead-space part 'condorpad'.

    SEVENTH_NX_CONDOR_PAD=0          stock 8-way cursor at stock speed
    SEVENTH_NX_CONDOR_SPEED=0.75     cursor speed vs stock (0.25 .. 1.0)

HOW THE STOCK CURSOR MOVES
==========================
x86 0x5FE771 (ARM +0x8E6890) runs once a frame while a direction is held
(0x5FDF21; 0x5FDF34 calls it a second time when 0x80 is also held). It counts
frames held in [0xCBC7BC] and calls the ONE-UNIT step 0x5FE8CF(mask) --
ARM +0x8E71F0 -- 1 time on held-frames 1-2, 2 times on frame 3 and 4 times
from frame 4 on. `mask` is the pad word [0xC72E80]; the step (0x5FE91B)
reads only its direction bits

    0x1000 up   0x2000 right   0x4000 down   0x8000 left

and moves the cursor one unit per set bit, scrolling the map when the cursor
is near the frame edge. The port turns the left stick into those four bits,
so the cursor only ever goes 8 ways, and a diagonal is sqrt(2) faster.

WHAT THIS CHANGES
=================
Each of 0x5FE771's four `bl +0x8E71F0` is pointed at one cave instead, which
edits the step's mask argument in place on the guest stack ([esp+4]; the
port's `call` only moves esp, so esp+0 is the unwritten return slot) and then
tail-branches into the step. So the game's own step, scroll and clamps run
unchanged; only which bits a step sees changes. The step's three OTHER
callers (0x5FC503, 0x5FE0BA, 0x5FE1D6 -- the cursor snapping to a unit) pass
computed masks and are not touched.

  1. SPEED. A 1/256 accumulator lets SPEED*256 of every 256 steps through;
     the rest see the mask with its direction bits cleared (the step then
     moves nothing). Releasing every direction re-primes it so the first
     step after a press always moves.
  2. 360 DEGREES. When the left stick is out of its dead zone the direction
     bits are rebuilt from the stick's unit vector: two float accumulators
     (x, y) gain |cos| and |sin| per step and emit that axis' bit when they
     pass 1.0. Any angle comes out at its true slope, and the speed along it
     is one unit per step at every angle (a diagonal no longer runs 41%
     faster). The D-pad (stick centred) keeps its exact stock bits.

The stick is the object ff7nx_analog/ff7nx_coaster read (the port's own
halves of the left stick, 0..1).

THE PLACEMENT LINE (BUILD 570)
==============================
The red "you may place units below here" line is ONE call, x86 0x60298F
(ARM +0x8FD3C8), to the generic 2D rectangle 0x671D2A(x, y, w, h, colour,
0, z, obj) with x = [0xC6094C] (0) and w = [0xC60940] (640): the 4:3
viewport width, so it stops at the 4:3 margins. The rectangle is a TL-vertex
quad in 640-unit screen space (fild, so signed), and the wide_screen shader
maps 640 units onto 960 of 1280 pixels. The call is pointed at a small cave
that, only when w == 640, rewrites the two stack arguments to x - 107 and
854 (the 853.33-unit 16:9 span, rounded out) and tail-branches into the
rectangle. Every other caller of 0x671D2A is untouched, and a 320-wide
viewport (condor's small window mode) keeps its line.

    SEVENTH_NX_CONDOR_LINE=0         the stock 4:3 line

THE "START COMBAT." BANNER (BUILD 571)
======================================
x86 0x5FD1BB slides the banner up two units a frame, [0xC72DFC] -= 2, and
stops when it EQUALS -24 (`cmp ecx, -0x18 / jne`; ARM +0x8E389C
`cmn w8, #0x18`). The banner is drawn at camera y + [0xC72DFC] + small
offsets, in map units (3 screen px a unit at 720p -- the box is 0x100 units
and measured 759 px wide). With the 16:9 map filling the whole frame (the
stock 20-row top bar is gone) -24 parks it with its text clipped at the top
edge; measured from your clip, 89.5 px above where you want it = 30 units.
The compare word becomes `cmp w8, #6` and its `b.ne` a `b.gt`: the slide
stops at the first value <= 6. Every start the game uses (0x30..0xB0, and
0x40 after the fade-in) is even, so it lands on 6 exactly as stock landed on
-24; the `b.gt` only guards a start the stock `==` would have run past.

    SEVENTH_NX_CONDOR_BANNER_Y=6     where it stops (even, -64..64; -24 stock)

THE MESSAGE BOXES AND THE B/A BUTTONS (BUILD 575)
=================================================
The unit/status message pair ("Fighter 01" over "Arrived at the directed
position.", "Encountered enemy." ...) slides down from [0xC72DFC] = -32 one
unit a frame and stops at 0x40 -- two copies of the same step, x86 0x5FCA8E
(ARM +0x8E4E10) and 0x5FCAE6 (+0x8E4BE0), each
`inc; cmp 0x40; jl; mov 0x40`. Both boxes are drawn from that one value
(0x5F8700: camera y + [0xC72DFC] - 8 / + 6 ...), so moving the stop moves
the pair together and keeps their spacing. Measured on your clip, the name
box's top edge sits at y 105 at 720p; the "Start combat." banner (BUILD 571)
sits at y 51. 54 px is 18 units, so the stop becomes 0x40 - 18 = 46 (the
compare, the recompiler's `0x3F - x` overflow term and the clamp store, in
both copies). "Start combat." then slides up from 46 to 6 -- still even, so
BUILD 571's stop is still landed on exactly.

The blinking B and A are sprites 0x15 and 0x33, drawn ONLY by 0x5F87FF
(ARM +0x8D6E30), which "Set units." and the message boxes call. Each draw
is `push 0, 0xFFFF, id ; call 0x607CC5 ; add esp, 0xC`; the two calls
(+0x8D7074, +0x8D7138) become `nop` and the following `add esp, 0xC`
becomes `add esp, 0x10`, which pops the return slot the callee's `ret` would
have popped. The stack comes out exactly where it did; the buttons are
simply never put in the frame's sprite list.

    SEVENTH_NX_CONDOR_MSG_Y=30       where the message pairs stop (64 stock)
    SEVENTH_NX_CONDOR_BUTTONS=1      keep the B/A buttons

BUILD 576: THE REPORT THAT BUILD 575 MISSED
==========================================
575 moved the right value but the wrong slide. The pair that drops in during
a battle ("Fighter 01" / "Arrived at the directed position.") is state 0x11
(set with [0xC72DFC] = -32 at x86 0x602851). The state table at 0x5FCA0D
sends 0x11 to 0x5FCA52, which steps [0xC72DFC] += 4 until it reaches
[0xC625DC] -- and [0xC625DC] is written every frame by 0x5F86E4 from its
argument, which the report's draw (x86 0x5F8D05..0x5F8DB7: name box sprite
0x13 at [0xC72DFC] - 0x18, text at - 0x15, wide window at - 8) passes as a
constant `push 0x30`. So the report stops at 48, not 64, and 575's change
never touched it; 575 only moved the state 0xB/0xC pairs (0x5FCA8E /
0x5FCAE6, stock 64) to 46, which happened to land 2 units above the
report's 48 -- the same place to the eye, which is why nothing looked moved.
Your clip measured the report's name box at y 105 with its stop at 48; the
"Start combat." box at y 51: 18 units, so the report stops at 30 (ARM
+0x8D8804 `mov w8, #0x30` -> `mov w8, #30`; -32 + 4n passes 30 at 32 and
0x5FCA52 clamps it to exactly 30). The 0xB/0xC pairs use the same value,
so every dropdown pair now stops in the same place. The wide box is drawn
from the same [0xC72DFC] as the name box, so its distance below it is
unchanged. The "Set units." screen (state 0xA) snaps its window to its own
`push 0x30` at +0x8D9710 and does not slide; it is left alone.

REGISTERS. The cave is entered by `bl` from recompiled code, i.e. at a call
boundary: x0..x18 and every FP register are the callee's to clobber. It saves
x19..x22 and x29/x30 and restores them before the tail branch, so the step
returns straight to 0x5FE771's code with everything the caller keeps live
intact.
"""
from __future__ import annotations

import os
import struct

import a64 as A
import ff7nx_analog as AN
from ff7nx_analog_cave import Asm

ENV = 'SEVENTH_NX_CONDOR_PAD'
LINE_ENV = 'SEVENTH_NX_CONDOR_LINE'
BANNER_ENV = 'SEVENTH_NX_CONDOR_BANNER_Y'
MSG_ENV = 'SEVENTH_NX_CONDOR_MSG_Y'
MSG_STOCK_Y = 0x40
MSG_Y = 30
# BUILD 576: the unit report ("Fighter 01" / "Arrived at the directed
# position.") is state 0x11, NOT the 0x5FCA8E/0x5FCAE6 slide: 0x5FCA52 steps
# [0xC72DFC] += 4 until it reaches [0xC625DC], and that target is the `push
# 0x30` the report's draw (x86 0x5F8DB0) hands 0x5F86E4 every frame. ARM
# +0x8D8804 `mov w8, #0x30` is that push's value.
REPORT_SITE = 0x8D8804
REPORT_STOCK = 0x321C07E8                        # mov w8, #0x30
REPORT_ANCHORS = (
    (0x8D87DC, 0x52800268, 'mov w8, #0x13   name box sprite, x86 0x5F8DA6'),
    (0x8D8818, 0x97FFFA5E, 'bl 0x8D7190   x86 call 0x5F86E4 at 0x5F8DB2'),
)
# (site of `mov w10, #0x3f`, `cmp w8, #0x40`, `mov w8, #0x40`) per copy
MSG_SITES = ((0x8E4EEC, 0x8E4EF4, 0x8E4F20),     # x86 0x5FCA8E
             (0x8E4D0C, 0x8E4D14, 0x8E4D40))     # x86 0x5FCAE6
MSG_STOCK = (0x320017EA, 0x7101011F, 0x321A03E8)
BUTTONS_ENV = 'SEVENTH_NX_CONDOR_BUTTONS'
# (the `bl 0x607CC5`, the `add esp, 0xC` after it) for sprites 0x15 and 0x33
BUTTON_SITES = ((0x8D7074, 0x9400E51B, 0x8D707C, 0x11003108, 0x11004108),
                (0x8D7138, 0x9400E4EA, 0x8D7140, 0x11003100, 0x11004100))
BUTTON_ANCHORS = (
    (0x8D6E30, 0xF81C0FF7, 'str x23, [sp, #-0x40]!   x86 0x5F87FF'),
    (0x8D7060, 0x528002A8, 'mov w8, #0x15   sprite B'),
    (0x8D7124, 0x52800668, 'mov w8, #0x33   sprite A'),
)
NOP = 0xD503201F
BANNER_STOCK_Y = -24
BANNER_Y = 6
BANNER_SITE = 0x8E389C
BANNER_WORD = 0x3100611F             # cmn w8, #0x18   (== -24)
BANNER_BRANCH = 0x8E38AC
BANNER_BRANCH_WORD = 0x54001381      # b.ne +0x8E3B1C   not there yet
BANNER_BRANCH_GT = 0x5400138C        # b.gt +0x8E3B1C
BANNER_ANCHORS = (
    (0x8E3880, 0x51000916, 'sub w22, w8, #2   the slide step'),
    (0x8E3898, 0x79C00008, 'ldrsh w8, [x0]   [0xC72DFC]'),
    (0x8E38A0, 0x1A9F17E9, 'cset w9, eq'),
)
SPEED_ENV = 'SEVENTH_NX_CONDOR_SPEED'
DEFAULT_SPEED = 0.75
DEADZONE = 0.2
VERSION = 'condorpad-575'

STEP_FN = 0x8E71F0                   # x86 0x5FE8CF
STEP_FIRST = 0xA9BD57F6              # stp x22, x21, [sp, #-0x30]!
MOVE_FN = 0x8E6890                   # x86 0x5FE771
CALL_SITES = (                       # the four `bl +0x8E71F0` in 0x5FE771
    (0x8E6980, 0x9400021C),
    (0x8E6A0C, 0x940001F9),
    (0x8E6A98, 0x940001D6),
    (0x8E6AD8, 0x940001C6),
)
ANCHORS = (
    (MOVE_FN, 0xA9BD57F6, 'stp x22, x21, [sp, #-0x30]!   0x5FE771'),
    (0x8E68A8, 0x5298F793, 'mov w19, #0xc7bc   [0xCBC7BC] held counter'),
    (0x8E6948, 0x5285D014, 'mov w20, #0x2e80   [0xC72E80] pad word'),
    (STEP_FN, STEP_FIRST, 'stp x22, x21, [sp, #-0x30]!   0x5FE8CF'),
    (0x8E7208, 0x52999815, 'mov w21, #0xccc0   the step\'s cursor'),
)
LINE_SITE = 0x8FD3C8                 # bl x86 0x671D2A   the placement line
RECT_FN = 0xAF4730                   # x86 0x671D2A
LINE_ANCHORS = (
    (RECT_FN, 0xFC1D0FE8, 'str d8, [sp, #-0x30]!   0x671D2A'),
    (0x8FD3CC, 0x294226C8, 'ldp w8, w9, [x22, #0x10]   after the call'),
)
LINE_W = 640
WIDE_X = 107                         # (853.33 - 640) / 2, rounded out
WIDE_W = 854
REGFILE_GOT = 0x12CE2B0
TRANSLATE = 0x10FC3A0

BSS_GATE, BSS_AX, BSS_AY = 0, 4, 8
BSS_BYTES = 0x10

COND_GE, COND_LT, COND_GT, COND_LE = 10, 11, 12, 13
FSQRT = lambda rd, rn: 0x1E21C000 | (rn << 5) | rd          # noqa: E731


def _log_imm(base, rd, rn, immr, imms):
    return base | (immr << 16) | (imms << 10) | (rn << 5) | rd


def and_dirs(rd, rn):    return _log_imm(0x12000000, rd, rn, 20, 3)    # 0xF000
def clear_dirs(rd, rn):  return _log_imm(0x12000000, rd, rn, 16, 27)   # ~0xF000
BIT_IMMR = {0x1000: 20, 0x2000: 19, 0x4000: 18, 0x8000: 17}


def orr_bit(rd, rn, bit):
    return _log_imm(0x32000000, rd, rn, BIT_IMMR[bit], 0)


def enabled(env=None):
    try:
        moved = banner_y(env) != BANNER_STOCK_Y
    except ValueError:
        moved = True                  # apply_to_nso reports the bad value
    if not (pad_enabled(env) or line_enabled(env) or moved):
        return False
    try:
        import ff7nx_minifade
        return ff7nx_minifade.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def pad_enabled(env=None):
    e = os.environ if env is None else env
    return e.get(ENV, '').strip().lower() not in ('0', 'off', 'no', 'false')


def line_enabled(env=None):
    e = os.environ if env is None else env
    return e.get(LINE_ENV, '').strip().lower() not in ('0', 'off', 'no',
                                                      'false')


def banner_y(env=None):
    e = os.environ if env is None else env
    raw = e.get(BANNER_ENV, '').strip()
    v = int(raw) if raw else BANNER_Y
    if v % 2 or not -64 <= v <= 64:
        raise ValueError('%s=%d: must be even and within -64..64'
                         % (BANNER_ENV, v))
    return v


def banner_word(y):
    """`cmp w8, #y` / `cmn w8, #-y`: the slide's stop test."""
    if y >= 0:
        return 0x7100001F | (y << 10) | (8 << 5)
    return 0x3100001F | ((-y) << 10) | (8 << 5)


def msg_y(env=None):
    e = os.environ if env is None else env
    raw = e.get(MSG_ENV, '').strip()
    v = int(raw) if raw else MSG_Y
    if not 1 <= v <= 0x7F:
        raise ValueError('%s=%d: must be 1..127' % (MSG_ENV, v))
    return v


def buttons_removed(env=None):
    e = os.environ if env is None else env
    return e.get(BUTTONS_ENV, '').strip().lower() not in ('1', 'on', 'yes',
                                                          'true', 'keep')


def report_word(y):
    """`mov w8, #y`: the report's stop, pushed to 0x5F86E4."""
    return 0x52800000 | (y << 5) | 8


def msg_words(y):
    """(mov w10, #y-1 ; cmp w8, #y ; mov w8, #y) -- the stop, in one copy."""
    return (0x52800000 | ((y - 1) << 5) | 10,
            0x7100001F | (y << 10) | (8 << 5),
            0x52800000 | (y << 5) | 8)


def speed(env=None):
    e = os.environ if env is None else env
    raw = e.get(SPEED_ENV, '').strip()
    v = float(raw) if raw else DEFAULT_SPEED
    return min(1.0, max(0.25, v))


def _fconst(a, sreg, value, wtmp=15):
    bits = struct.unpack('<I', struct.pack('<f', value))[0]
    a.emit(A.movz(wtmp, bits & 0xFFFF))
    a.emit(A.movk_hi(wtmp, bits >> 16))
    a.emit(A.fmov_s_from_w(sreg, wtmp))


def build_cave(bss, spd):
    q = max(1, min(256, int(round(spd * 256))))

    def b(at, addr):
        a = Asm(at, addr)
        a.emit(A.stp64_pre(29, 30, 31, -0x30))
        a.emit(A.stp64_off(19, 20, 31, 0x10))
        a.emit(A.stp64_off(21, 22, 31, 0x20))
        a.emit(A.adrp(21, a.pc(), REGFILE_GOT & ~0xFFF))
        a.emit(A.ldr64(21, 21, REGFILE_GOT & 0xFFF))
        a.emit(A.ldr(0, 21, 0x10))                   # guest esp
        a.emit(A.add_imm(0, 0, 4))                   # -> the mask argument
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.mov_reg64(19, 0))
        a.emit(A.ldr(20, 19, 0))                     # w20 = mask
        a.emit(A.adrp(22, a.pc(), bss & ~0xFFF))
        a.emit(A.add_imm64(22, 22, bss & 0xFFF))
        a.emit(and_dirs(9, 20))
        a.cbz(9, 'idle')
        # ---- 1. speed gate ----------------------------------------------
        a.emit(A.ldr(9, 22, BSS_GATE))
        a.emit(A.add_imm(9, 9, q))
        a.emit(A.cmp_imm(9, 256))
        a.bcond('go', COND_GE)
        a.emit(A.str_(9, 22, BSS_GATE))
        a.emit(clear_dirs(20, 20))
        a.b('store')
        a.label('go')
        a.emit(A.sub_imm(9, 9, 256))
        a.emit(A.str_(9, 22, BSS_GATE))
        # ---- 2. the stick -----------------------------------------------
        a.emit(A.adrp(9, a.pc(), AN.INPUT_GOT & ~0xFFF))
        a.emit(A.ldr64(9, 9, AN.INPUT_GOT & 0xFFF))
        a.cbz(9, 'store', wide=True)
        for off in AN.INPUT_CHAIN:
            a.emit(A.ldr64(9, 9, off))
            a.cbz(9, 'store', wide=True)
        a.emit(A.ldr_s(0, 9, AN.OBJ_UP))
        a.emit(A.ldr_s(1, 9, AN.OBJ_DOWN))
        a.emit(A.ldr_s(2, 9, AN.OBJ_RIGHT))
        a.emit(A.ldr_s(3, 9, AN.OBJ_LEFT))
        a.emit(A.fsub_s(1, 0, 1))                    # y = up - down
        a.emit(A.fsub_s(0, 2, 3))                    # x = right - left
        a.emit(A.fmul_s(2, 0, 0))
        a.emit(A.fmul_s(3, 1, 1))
        a.emit(A.fadd_s(2, 2, 3))                    # |v|^2
        _fconst(a, 4, DEADZONE * DEADZONE)
        a.emit(A.fcmp_s(2, 4))
        a.bcond('store', COND_LE)                    # D-pad / NaN: stock bits
        a.emit(FSQRT(2, 2))
        a.emit(A.fdiv_s(0, 0, 2))                    # unit x
        a.emit(A.fdiv_s(1, 1, 2))                    # unit y
        a.emit(clear_dirs(20, 20))
        a.emit(A.movz(15, 0))
        a.emit(A.fmov_s_from_w(5, 15))               # 0.0
        _fconst(a, 6, 1.0)
        for axis, (sreg, slot, pos_bit, neg_bit) in enumerate((
                (0, BSS_AX, 0x2000, 0x8000),
                (1, BSS_AY, 0x1000, 0x4000))):
            a.emit(A.fsub_s(3, 5, sreg))
            a.emit(A.fcmp_s(sreg, 5))
            a.emit(A.fcsel_s(3, sreg, 3, COND_GT))   # |component|
            a.emit(A.ldr_s(4, 22, slot))
            a.emit(A.fadd_s(4, 4, 3))
            a.emit(A.fcmp_s(4, 6))
            a.bcond('keep%d' % axis, COND_LT)
            a.emit(A.fsub_s(4, 4, 6))
            a.emit(A.fcmp_s(sreg, 5))
            a.bcond('pos%d' % axis, COND_GT)
            a.emit(orr_bit(20, 20, neg_bit))
            a.b('keep%d' % axis)
            a.label('pos%d' % axis)
            a.emit(orr_bit(20, 20, pos_bit))
            a.label('keep%d' % axis)
            a.emit(A.str_s(4, 22, slot))
        a.b('store')
        # ---- no direction held: re-prime the gate -----------------------
        a.label('idle')
        a.emit(A.movz(9, 256 - q))
        a.emit(A.str_(9, 22, BSS_GATE))
        a.label('store')
        a.emit(A.str_(20, 19, 0))
        a.emit(A.ldp64_off(21, 22, 31, 0x20))
        a.emit(A.ldp64_off(19, 20, 31, 0x10))
        a.emit(A.ldp64_post(29, 30, 31, 0x30))
        a.b('step')
        a.lab['step'] = STEP_FN
        return a.resolve()
    return b


def build_line():
    """x, w of 0x671D2A's stack arguments, widened when w is the full 640."""
    def b(at, addr):
        a = Asm(at, addr)
        a.emit(A.stp64_pre(29, 30, 31, -0x20))
        a.emit(A.stp64_off(19, 20, 31, 0x10))
        a.emit(A.adrp(19, a.pc(), REGFILE_GOT & ~0xFFF))
        a.emit(A.ldr64(19, 19, REGFILE_GOT & 0xFFF))
        a.emit(A.ldr(0, 19, 0x10))                   # guest esp
        a.emit(A.add_imm(0, 0, 0xC))                 # w
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldr(9, 0, 0))
        a.emit(A.movz(10, LINE_W))
        a.emit(A.cmp_reg(9, 10))
        a.bcond('out', 1)                            # b.ne
        a.emit(A.movz(9, WIDE_W))
        a.emit(A.str_(9, 0, 0))
        a.emit(A.ldr(0, 19, 0x10))
        a.emit(A.add_imm(0, 0, 4))                   # x
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldr(9, 0, 0))
        a.emit(A.sub_imm(9, 9, WIDE_X))
        a.emit(A.str_(9, 0, 0))
        a.label('out')
        a.emit(A.ldp64_off(19, 20, 31, 0x10))
        a.emit(A.ldp64_post(29, 30, 31, 0x20))
        a.b('rect')
        a.lab['rect'] = RECT_FN
        return a.resolve()
    return b


def _w(t, va):
    return struct.unpack_from('<I', t, va)[0]


def patch_text(text, place, bss, spd, pad=True, line=True, banner=None,
               msg=None, buttons=False):
    t = bytes(text)
    bad = []
    if msg is not None and msg != MSG_STOCK_Y:
        for sites in MSG_SITES:
            for va, want in zip(sites, MSG_STOCK):
                if _w(t, va) != want:
                    bad.append('condor msg +%#x holds %08X' % (va, _w(t, va)))
        bad += ['condor report anchor +%#x (%s) holds %08X'
                % (va, n, _w(t, va))
                for va, want, n in REPORT_ANCHORS if _w(t, va) != want]
        if _w(t, REPORT_SITE) != REPORT_STOCK:
            bad.append('condor report +%#x holds %08X'
                       % (REPORT_SITE, _w(t, REPORT_SITE)))
    if buttons:
        bad += ['condor buttons anchor +%#x (%s) holds %08X'
                % (va, n, _w(t, va))
                for va, want, n in BUTTON_ANCHORS if _w(t, va) != want]
        for bl_va, bl_w, add_va, add_w, _new in BUTTON_SITES:
            for va, want in ((bl_va, bl_w), (add_va, add_w)):
                if _w(t, va) != want:
                    bad.append('condor buttons +%#x holds %08X'
                               % (va, _w(t, va)))
    if banner is not None and banner != BANNER_STOCK_Y:
        bad += ['condor banner anchor +%#x (%s) holds %08X'
                % (va, n, _w(t, va))
                for va, want, n in BANNER_ANCHORS if _w(t, va) != want]
        for va, want in ((BANNER_SITE, BANNER_WORD),
                         (BANNER_BRANCH, BANNER_BRANCH_WORD)):
            if _w(t, va) != want:
                bad.append('condor banner +%#x holds %08X' % (va, _w(t, va)))
    if pad:
        bad += ['condor pad anchor +%#x (%s) holds %08X' % (va, n, _w(t, va))
                for va, want, n in ANCHORS if _w(t, va) != want]
        for site, stock in CALL_SITES:
            if _w(t, site) != stock or stock != A.bl(site, STEP_FN):
                bad.append('condor pad call +%#x holds %08X'
                           % (site, _w(t, site)))
    if line:
        bad += ['condor line anchor +%#x (%s) holds %08X' % (va, n, _w(t, va))
                for va, want, n in LINE_ANCHORS if _w(t, va) != want]
        if _w(t, LINE_SITE) != A.bl(LINE_SITE, RECT_FN):
            bad.append('condor line call +%#x holds %08X'
                       % (LINE_SITE, _w(t, LINE_SITE)))
    if bad:
        raise ValueError('; '.join(bad))
    written = {}
    if msg is not None and msg != MSG_STOCK_Y:
        for sites in MSG_SITES:
            for va, word in zip(sites, msg_words(msg)):
                struct.pack_into('<I', text, va, word)
                written[va] = word
        struct.pack_into('<I', text, REPORT_SITE, report_word(msg))
        written[REPORT_SITE] = report_word(msg)
    if buttons:
        for bl_va, _bl_w, add_va, _add_w, new in BUTTON_SITES:
            for va, word in ((bl_va, NOP), (add_va, new)):
                struct.pack_into('<I', text, va, word)
                written[va] = word
    if banner is not None and banner != BANNER_STOCK_Y:
        for va, word in ((BANNER_SITE, banner_word(banner)),
                         (BANNER_BRANCH, BANNER_BRANCH_GT)):
            struct.pack_into('<I', text, va, word)
            written[va] = word
    if pad:
        entry, placed = place(build_cave(bss, spd))
        written.update(placed)
        for va, word in placed.items():
            struct.pack_into('<I', text, va, word)
        for site, _stock in CALL_SITES:
            word = A.bl(site, entry)
            struct.pack_into('<I', text, site, word)
            written[site] = word
    if line:
        entry, placed = place(build_line())
        written.update(placed)
        for va, word in placed.items():
            struct.pack_into('<I', text, va, word)
        word = A.bl(LINE_SITE, entry)
        struct.pack_into('<I', text, LINE_SITE, word)
        written[LINE_SITE] = word
    return written


def apply_to_nso(src, dest, log=lambda *_: None, stock=None, spd=None,
                 pad=None, line=None, banner=None, msg=None, buttons=None):
    import ff7nx_audio_cave as AC
    import ff7nx_deadspace as DS
    spd = speed() if spd is None else spd
    pad = pad_enabled() if pad is None else pad
    line = line_enabled() if line is None else line
    banner = banner_y() if banner is None else banner
    msg = msg_y() if msg is None else msg
    buttons = buttons_removed() if buttons is None else buttons
    with open(src, 'rb') as fh:
        blob = fh.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    lo, hi = DS.part(src, 'condorpad', stock)
    bump = DS.Bump(lo, hi)

    def place(bld):
        before = dict(bump.placed)
        e = bump.put(bld)
        return e, {k: v for k, v in bump.placed.items() if k not in before}
    bss = AC.scratch_base(blob, segs)
    written = patch_text(text, place, bss, spd, pad, line, banner, msg,
                         buttons)
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, BSS_BYTES if pad else 0)
    assert AC.segments(out)[0][0][2] == segs[0][2]
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as fh:
        fh.write(out)
    return {'words': len(written), 'bss': bss, 'speed': spd, 'pad': pad,
            'line': line, 'banner': banner, 'msg': msg, 'buttons': buttons}
