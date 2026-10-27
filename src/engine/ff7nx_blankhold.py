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

SEVENTH_NX_BLANK_HOLD=0 leaves the flip as it was.

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
STATE_BYTES = 16
S_DRAWS, S_VALID, S_HELD, S_TOTAL = 0, 4, 8, 12


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


def check(text):
    for va, want, what in ((DRAW, DRAW_WORD, 'draw entry'),
                           (FLIP_TEST, FLIP_TEST_WORD, 'flip state test')):
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
    end = f_at + 4 * len(fw)
    if end > hi:
        raise ValueError('blank hold: %d bytes do not fit the %d-byte part'
                         % (end - lo, hi - lo))
    for at, words in ((d_at, dw), (f_at, fw)):
        for i, x in enumerate(words):
            struct.pack_into('<I', text, at + 4 * i, x)
    struct.pack_into('<I', text, DRAW, A.b(DRAW, d_at))
    struct.pack_into('<I', text, FLIP_TEST, A.b(FLIP_TEST, f_at))
    raw[0] = bytes(text)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(AC.pack(blob, raw, growth))
    log('  blank-frame hold: a frame with nothing drawn shows the last one '
        '(at most %d in a row); caves +0x%X/+0x%X, state +0x%X'
        % (HOLD_MAX, d_at, f_at, state))
    return {'draw': d_at, 'flip': f_at, 'state': state, 'bss': growth,
            'bytes': end - lo}
