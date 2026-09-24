#!/usr/bin/env python3
r"""
ff7nx_fieldpace.py -- the field frame limiter: stock pacing, minus the tail.

HISTORY, BECAUSE BOTH EARLIER VERSIONS WERE MEASURED
====================================================
BUILD 525 replaced the stock field limiter with FFNx's: measure each frame
from the previous RELEASE, frame_time exactly 1/60 s, no debt path. On
hardware:

    fence jump (mds7)   fixed -- a steady 60
    3x booster          BROKEN -- the debt/catch-up tick is how game logic
                        gets ahead of the render rate; restoring that one
                        word (diag_toggle fieldpace-debt --on) fixed it
    walking camera      janky / stuttery, with debt on AND with it off

So "exactly 60, measured from release" is not a drop-in on this port. The
stock limiter, aimed at 66, never had the walking stutter. What it had was
the fence-scene drop. This version keeps the stock limiter for every frame
where the stock limiter was fine, and changes only the frames where it was
not.

WHY THE FENCE SCENE DROPS (unchanged from 525, and still true)
==============================================================
One field frame (field_main_loop, x86 0x60E5B7):

    0x60E5CA  baseline = now                     <- FRAME START
              field logic, draw lists
    0x6384E6  LIMITER: spin until now - baseline >= frame_time
              flip / present                      <- the frame's GPU cost

The limiter measures from the frame's START, so the flip is added on top:
period = max(frame_time, logic) + flip. The 66 aim leaves ~2 ms for that
flip. Five animated characters make the flip much longer than 2 ms, and the
period stretches with it although the frame's total work fits in 16.7 ms.
(The 3x booster hides it because it only scales the clock the limiter reads:
the port's rdtsc, ARM +0x6720, is a virtual clock multiplied by the booster
speed.)

THE HYBRID
==========
Two deadlines for the same frame:

    stock    frame_start  + frame_time(66)
    60 Hz    last_release + 1/60 s

and the limiter waits for the EARLIER one. With a normal flip (under ~2 ms)
the stock deadline is the earlier, so pacing is exactly what it always was --
same divisor, same fudge, same debt path, same 3x. Only when the flip grows
past the slack the 66 aim built in does the 60 Hz deadline take over, which
removes the fence-scene tail and nothing else.

It is implemented without touching the limiter's own logic:

  * the limiter's exit (+0x9E63B4) records its release time -- its own last
    clock reading, guest 0xCFF8E0 -- into 8 bytes of this module's BSS;
  * right after field_main_loop writes `baseline = now` (+0x946674, after the
    `bl 0x6720` it makes for that), a second cave lowers the baseline to
    `last_release + D` when that is earlier, where

        D = 1/60 s - frame_time(66) = cps/60 - (cps/66 - 10000)

    so `baseline + frame_time` becomes `min(stock, last_release + 1/60 s)`.

ff7_en is NOT touched: divisor and fudge stay whatever the 60 FPS pass and
the limiter-fps dial set.
"""
from __future__ import annotations

import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_cave
import ff7nx_tables
import nxmap
from ff7nx_audio_cave import Asm

ENV = 'SEVENTH_NX_FIELD_PACING'          # `stock` installs nothing

CPS = 19200000                           # the Switch system tick; see below
STOCK_FUDGE = 10000.0                    # ff7_en 0x7B7848

EXIT_HOOK = 0x9E63B4                     # limiter 0x6384E6, single exit
EXIT_ORIG = 0xB9401680                   # ldr w0, [x20, #0x14]
EXIT_RESUME = EXIT_HOOK + 4

# A frame writes `baseline = now` in two places, each followed by the
# instruction hooked here (right after its `bl 0x6720`):
#
#   +0x946674   field_main_loop (x86 0x60E5CA)          every normal frame
#   +0x94BDF4   the catch-up copy 0x60E96C (x86 0x60E97F), run as the second
#               frame of a debt call by field_sub_6388EE
#
# ONLY THE FIRST IS HOOKED. BUILD 527 hooked both, on the theory that the
# fence jump's drops came from the unhooked catch-up frame. On hardware that
# made walking feel worse AND the fence dips worse, so the theory was wrong
# and 527 is withdrawn. This is BUILD 526 again, byte for byte -- the last
# version you confirmed smooth. Do not re-add 0x94BDF4 without a measurement.
CATCH_UP_START = 0x94BDF4                # documented, deliberately NOT hooked
START_HOOKS = (0x946674,)
START_HOOK = START_HOOKS[0]
START_ORIG = 0xB9401308                  # ldr w8, [x24, #0x10] -- both sites
START_RESUME = START_HOOK + 4

GUEST_BASELINE = 0xCFF8D8
GUEST_SCRATCH = 0xCFF8E0
BSS_BYTES = 8                            # u64 last release, 0 = none yet


def enabled():
    return os.environ.get(ENV, '').strip().lower() not in ('stock', '0',
                                                           'off', 'no')


def slack_counts(divisor):
    """D = 1/60 s - the stock frame_time at `divisor`, in clock counts.

    frame_time = cps/divisor - 10000 (x86 0x60E422). cps is the Switch's
    19.2 MHz system tick: build history measured frame_time at divisor 60 as
    16.146 ms, and (1/60 - 0.016146) * cps = 10000 gives exactly 19.2e6.
    """
    d = CPS / 60.0 - (CPS / float(divisor) - STOCK_FUDGE)
    return max(0, int(round(d)))


def build_exit_cave(_entry, addr, bss):
    """last_release = the limiter's last clock reading."""
    a = Asm(_entry, addr)
    AC.mov32(a, 0, GUEST_SCRATCH)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldr64(21, 0, 0))            # X21 is restored by the epilogue
    AC.bss_ptr(a, 9, bss)
    a.emit(A.str64(21, 9, 0))
    a.emit(EXIT_ORIG)
    a.emit(A.b(a.pc(), EXIT_RESUME))
    return a.resolve()


def build_start_cave(_entry, addr, bss, slack, resume=START_RESUME):
    """baseline = min(baseline, last_release + D).

    Runs directly after a `bl`, so x0..x17 and the flags are dead; X30 was
    saved by field_main_loop's own prologue.
    """
    a = Asm(_entry, addr)
    AC.bss_ptr(a, 9, bss)
    a.emit(A.ldr64(10, 9, 0))
    a.cbz64(10, 'done')                  # no release recorded yet
    a.emit(A.movz(11, slack & 0xFFFF))
    a.emit(A.movk_hi(11, slack >> 16))
    a.emit(A.add_reg64(10, 10, 11))      # last_release + D
    AC.mov32(a, 0, GUEST_BASELINE)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldr64(9, 0, 0))             # the stock frame-start baseline
    a.emit(A.cmp_reg64(10, 9))
    a.bcond('done', A.HS)                # stock deadline is the earlier
    a.emit(A.str64(10, 0, 0))
    a.label('done')
    a.emit(START_ORIG)
    a.emit(A.b(a.pc(), resume))
    return a.resolve()


def apply_to_nso(src, dest, divisor):
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw, space = ff7nx_tables.open_module(blob)
    text = space.text
    checks = [(EXIT_HOOK, EXIT_ORIG, 'limiter exit')]
    checks += [(va, START_ORIG, 'frame-start baseline') for va in START_HOOKS]
    for va, want, what in checks:
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            raise ValueError('%s at +0x%X holds %08X, expected %08X'
                             % (what, va, got, want))
    bss = AC.scratch_base(blob, segs)
    slack = slack_counts(divisor)
    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))
    exit_entry, exit_words = ff7nx_cave.emit_laid_out(
        pool, lambda e, ad: build_exit_cave(e, ad, bss),
        span=ff7nx_cave.ANY_SPAN)
    placed = dict(exit_words)
    placed[EXIT_HOOK] = A.b(EXIT_HOOK, exit_entry)
    start_entries = []
    for hook in START_HOOKS:
        entry, words = ff7nx_cave.emit_laid_out(
            pool, lambda e, ad, r=hook + 4: build_start_cave(
                e, ad, bss, slack, resume=r))
        placed.update(words)
        placed[hook] = A.b(hook, entry)
        start_entries.append(entry)
    for va, w in placed.items():
        struct.pack_into('<I', text, va, w)
    out = AC.pack(blob, space.commit(), BSS_BYTES)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'exit_entry': exit_entry, 'start_entry': start_entries[0],
            'start_entries': start_entries,
            'words': len(placed) - 1 - len(START_HOOKS), 'bss': bss,
            'slack': slack, 'slack_ms': slack * 1000.0 / CPS}
