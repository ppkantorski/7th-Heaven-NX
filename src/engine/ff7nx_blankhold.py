#!/usr/bin/env python3
"""
ff7nx_blankhold.py -- BUILD 665. A frame in which nothing was drawn shows the
last picture again instead of a black one.

WHAT WAS MEASURED
=================
PROBE-664 (664c, world map -> battle): one presented frame in the capture had
ZERO draw submissions (the port's one draw function, +0x10D9D70) -- the black
flicker right before the battle swirl. Everything else around it drew
(4 submissions per swirl frame, ~180 per world frame).

WHY A FRAME WITH NOTHING IN IT IS BLACK
=======================================
The port renders into an offscreen target. Its clear (+0x10D68D0) moves the
frame state (+0x12CE4D0 -> u32) from 2 to 3, and gfx_drv_flip (+0x10DA880)
copies that target to the screen only in state 3 (+0x10DAAC4). A frame with
no clear and no draw is never copied: the swap shows a screen nobody wrote --
black. The offscreen target, though, still holds the last frame untouched.

WHAT THIS DOES
==============
Count draws (a 3-word cave at the draw function's entry). At the flip's state
test (+0x10DAA9C): if the state is 1 or 2 (no clear), nothing was drawn, the
last real frame was copied, and fewer than HOLD_MAX frames in a row have been
held -- set the state to 3, so the flip copies the untouched target: the last
picture, one frame longer, instead of black. Anything else is stock: a
cleared frame, a frame that drew, and any blank stretch longer than HOLD_MAX
frames (a real black screen stays black).

HANDOFF HOLD (BUILD 812)
=======================
PROBE-811 (swirlprobe v3/v4): a world-map encounter stalls the game ~90 ms
on the last world frame (the world map tearing down) and ~87 ms on the
first swirl frame; the frame drawn right after the first stall is what
flashes dark for one frame (the "blink right before the swirl", old, never
caught at 30 fps capture). The present itself stays ~0.5 ms: game time.
So the flip does not present:
  - on the world map (mode 4), a frame that follows a stall over 40 ms
    since the last present, and the frame after it;
  - the first swirl frame (mode 5 right after 4), and the one after it.
Not presenting = frame state 0 into the flip's first test: the stock skip
path (+0x10DAAA8), the screen keeps the last good picture. The pause the
player sees is the game's own stall, unchanged. A present-end cave
(+0x10DAB58) stamps cntpct_el0 for the stall test. Tested on hardware with
the probe build: no blink.

SEVENTH_NX_BLANK_HOLD=0 leaves the flip as it was (both holds).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_deadspace as DS

ENV = 'SEVENTH_NX_BLANK_HOLD'
DRAW = 0x10D9D70
DRAW_WORD = 0xD10443FF           # sub sp, sp, #0x110
FLIP_TEST = 0x10DAA9C
FLIP_TEST_WORD = 0xB9400288      # ldr w8, [x20]   (x20 = &frame state)
HOLD_MAX = 2
STATE_BYTES = 40
S_DRAWS, S_VALID, S_HELD, S_TOTAL = 0, 4, 8, 12
# BUILD 812 handoff hold
S_PREVM, S_HOLD, S_HOLDS, S_T = 16, 20, 24, 32     # S_T: u64 cntpct at present end
MODE_GOT = 0x12CE1F8             # -> u32 game mode
MODE_BATTLE_SWIRL, MODE_WORLD = 5, 4
STALL_TICKS = 768000             # 40 ms of the 19.2 MHz counter
HANDOFF_HOLD = 2
PRESENT_END = 0x10DAB58
PRESENT_END_WORD = 0x321F03E8    # orr w8, wzr, #2  (after the present)
MRS_CNTPCT = 0xD53BE020          # mrs Xt, cntpct_el0 (| t)


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def draw_cave(at, state):
    """x16/x17 are free at a function entry (the AAPCS intra-call scratch)."""
    return [DRAW_WORD,
            A.adrp(16, at + 4, state & ~0xFFF),
            A.add_imm64(16, 16, state & 0xFFF),
            A.ldr(17, 16, S_DRAWS),
            A.add_imm(17, 17, 1),
            A.str_(17, 16, S_DRAWS),
            A.b(at + 24, DRAW + 4)]


def flip_cave(at, state):
    """w8 must hold the frame state at the resume point (the stock `orr`
    reads it); x9..x11 and the flags are dead there (redefined before use)."""
    a = AC.Asm(at, lambda i: at + 4 * i)
    e = a.emit
    e(FLIP_TEST_WORD)
    e(A.adrp(9, a.pc(), state & ~0xFFF))
    e(A.add_imm64(9, 9, state & 0xFFF))
    # BUILD 812 handoff hold (x12 is dead here too: both paths reach a bl)
    e(A.adrp(11, a.pc(), MODE_GOT & ~0xFFF))
    e(A.ldr64(11, 11, MODE_GOT & 0xFFF))
    a.cbz64(11, 'tick')
    e(A.ldr(10, 11, 0))
    e(A.ldr(11, 9, S_PREVM))
    e(A.str_(10, 9, S_PREVM))
    e(A.cmp_imm(10, MODE_BATTLE_SWIRL))
    a.bcond('world', A.NE)
    e(A.cmp_imm(11, MODE_WORLD))
    a.bcond('set', A.EQ)
    a.b('tick')
    a.label('world')
    e(A.cmp_imm(10, MODE_WORLD))
    a.bcond('tick', A.NE)
    e(A.ldr64(11, 9, S_T))
    a.cbz64(11, 'tick')
    e(MRS_CNTPCT | 12)
    e(A.sub_reg64(11, 12, 11))
    for w in A.movz_movk(12, STALL_TICKS):
        e(w)
    e(A.cmp_reg64(11, 12))
    a.bcond('tick', 0x9)             # ls
    a.label('set')
    e(A.movz(11, HANDOFF_HOLD))
    e(A.str_(11, 9, S_HOLD))
    a.label('tick')
    e(A.ldr(11, 9, S_HOLD))
    a.cbz(11, 'stock')
    e(A.sub_imm(11, 11, 1))
    e(A.str_(11, 9, S_HOLD))
    e(A.ldr(11, 9, S_HOLDS))
    e(A.add_imm(11, 11, 1))
    e(A.str_(11, 9, S_HOLDS))
    e(A.str_(A.WZR, 9, S_DRAWS))
    e(A.movz(8, 0))                       # not presented: the stock skip path
    a.b('resume')
    a.label('stock')
    e(A.ldr(10, 9, S_DRAWS))
    e(A.str_(A.WZR, 9, S_DRAWS))
    e(A.cmp_imm(8, 3))
    a.bcond('blank', A.NE)
    e(A.movz(11, 1))
    e(A.str_(11, 9, S_VALID))
    e(A.str_(A.WZR, 9, S_HELD))
    a.b('resume')
    a.label('blank')
    a.cbnz(10, 'resume')                  # drew without a clear: stock
    e(A.sub_imm(11, 8, 1))
    e(A.cmp_imm(11, 1))
    a.bcond('resume', A.HI)               # only states 1 and 2
    e(A.ldr(11, 9, S_VALID))
    a.cbz(11, 'resume')
    e(A.ldr(11, 9, S_HELD))
    e(A.cmp_imm(11, HOLD_MAX))
    a.bcond('resume', A.HS)
    e(A.add_imm(11, 11, 1))
    e(A.str_(11, 9, S_HELD))
    e(A.ldr(11, 9, S_TOTAL))
    e(A.add_imm(11, 11, 1))
    e(A.str_(11, 9, S_TOTAL))
    e(A.movz(8, 3))
    e(A.str_(8, 20, 0))
    a.label('resume')
    e(A.b(a.pc(), FLIP_TEST + 4))
    return a.resolve()


def present_cave(at, state):
    """BUILD 812: stamp the end of the present. x9/x10 are dead (the bl at
    +0x10DAB60 follows)."""
    return [PRESENT_END_WORD,
            A.adrp(9, at + 4, state & ~0xFFF),
            A.add_imm64(9, 9, state & 0xFFF),
            MRS_CNTPCT | 10,
            A.str64(10, 9, S_T),
            A.b(at + 20, PRESENT_END + 4)]


def check(text):
    for va, want, what in ((DRAW, DRAW_WORD, 'draw entry'),
                           (FLIP_TEST, FLIP_TEST_WORD, 'flip state test'),
                           (PRESENT_END, PRESENT_END_WORD, 'flip present end')):
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            raise ValueError('blank hold: +0x%X is %08X, not %08X (%s)'
                             % (va, got, want, what))


def apply_to_nso(src, dest, stock=None, log=lambda *_: None, region=None):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    check(text)
    lo, hi = region if region else DS.part(src, 'blank', stock)
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = (state - base) + STATE_BYTES + AC.bss_tail_slack(segs)
    d_at = (lo + 15) & ~15
    dw = draw_cave(d_at, state)
    f_at = (d_at + 4 * len(dw) + 15) & ~15
    fw = flip_cave(f_at, state)
    p_at = (f_at + 4 * len(fw) + 15) & ~15
    pw = present_cave(p_at, state)
    end = p_at + 4 * len(pw)
    if end > hi:
        raise ValueError('blank hold: %d bytes do not fit the %d-byte part'
                         % (end - lo, hi - lo))
    for at, words in ((d_at, dw), (f_at, fw), (p_at, pw)):
        for i, x in enumerate(words):
            struct.pack_into('<I', text, at + 4 * i, x)
    struct.pack_into('<I', text, DRAW, A.b(DRAW, d_at))
    struct.pack_into('<I', text, FLIP_TEST, A.b(FLIP_TEST, f_at))
    struct.pack_into('<I', text, PRESENT_END, A.b(PRESENT_END, p_at))
    raw[0] = bytes(text)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(AC.pack(blob, raw, growth))
    log('  blank-frame hold: a frame with nothing drawn shows the last one '
        '(at most %d in a row), and the world-map -> swirl handoff frames are '
        'held (BUILD 812); caves +0x%X/+0x%X/+0x%X, state +0x%X'
        % (HOLD_MAX, d_at, f_at, p_at, state))
    return {'draw': d_at, 'flip': f_at, 'present': p_at, 'state': state, 'bss': growth,
            'bytes': end - lo}
