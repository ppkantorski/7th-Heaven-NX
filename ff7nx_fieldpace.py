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

BUILD 531: THE LATE GATE -- WHAT THE PROBE ACTUALLY FOUND
========================================================
Probe v3 on tin_3 (927 frames, average 24 ms, 514 frames over 25 ms): every
30 ms frame ran the limiter TWICE and the debt flag was clear at the second
call, with 16.1-16.7 ms elapsed there. So those frames were the debt path:

    frame 1 (the debt call's first frame): no draw, then the debt-path wait
                                           `until elapsed >= T - debt` ~13 ms
    frame 2 (the catch-up copy):           draw ~15 ms, elapsed ~16.3 ms
                                           > T (14.63) -> overrun -> debt AGAIN

The limiter aims at 66 Hz (T = cps/66 - 10000 = 14.63 ms; the flip after it
brings the period to ~16.7). A frame whose work is 15-17 ms is on time for
60 but LATE for 66, so the stock limiter calls it an overrun and enters its
catch-up mode: two logic ticks per presented frame, the first one padded to
the 66 Hz schedule. Draw-heavy scenes sit in that mode permanently -- 30 ms
per presented frame. The 3x booster hides it because its virtual clock makes
the padding a third as long.

The gate hooks the one word where the overrun branch raises the debt flag
(+0x9E615C, `mov w21, #1`, x86 0x6385DD). The debt amount the stock code just
stored is `elapsed - T`; the gate lets the flag rise only when that is at
least `1/60 s + LATE_MS - T`, i.e. only when the frame is late for SIXTY by
more than LATE_MS (default 2 ms). Below that the frame simply isn't padded
and isn't followed by a catch-up. A real spike (a load, a 30 ms script frame)
and the 3x booster (whose clock runs 3x) still clear the gate, so catch-up and
the booster keep working. Normal frames never reach this branch at all.
`SEVENTH_NX_FIELD_LATE_MS=off` removes only the gate.

BUILD 534: THE SAME GATE FOR BATTLE
===================================
The battle limiter (x86 0x41B965, ARM +0x8F2D0) is a copy of the field one
with its own globals -- baseline 0x9ACB70, frame_time 0x9AB090 (= cps/div,
NO fudge, x86 0x41B6D2), debt flag 0x9AD1D4, debt amount 0x9AD1D8 -- and
battle_main_loop (0x41BAB3) has the same catch-up mode: when the flag is set
it calls `66c3b2(0)` (no draw), waits `T - debt` in the limiter, re-reads the
baseline and runs the battle update a second time (0x41BC58-0x41BCD4). So a
battle whose frame work is 15.2-18.7 ms (late for the 66 aim, on time for
60) sits in the same 30 ms mode, which is the overclock you need.

The overrun raises the flag at +0x8F85C (`mov w20, #1`, x86 0x41BA3B) with
the debt amount double still in x20; the words after it translate and store
w20. Same cave, other registers: `SEVENTH_NX_BATTLE_LATE_MS` (default 2,
`off` removes it). Battle's T has no fudge, so its slack is
`1/60 + margin - cps/div`.
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

LATE_ENV = 'SEVENTH_NX_FIELD_LATE_MS'
LATE_DEFAULT_MS = 2.0
LATE_HOOK = 0x9E615C                     # overrun: debt flag := 1
LATE_ORIG = 0x320003F5                   # orr w21, wzr, #1
LATE_RESUME = LATE_HOOK + 4              # b 0x9e63ac -> store the flag

BATTLE_LATE_ENV = 'SEVENTH_NX_BATTLE_LATE_MS'
BATTLE_LATE_HOOK = 0x8F85C               # overrun: debt flag := 1 (w20)
BATTLE_LATE_ORIG = 0x320003F4            # orr w20, wzr, #1
BATTLE_LATE_RESUME = BATTLE_LATE_HOOK + 4   # b 0x8faa4 -> store the flag
GE = 10


def late_ms():
    """The gate margin in ms, or None when the gate is off."""
    v = os.environ.get(LATE_ENV, '').strip().lower()
    if v in ('off', 'no', 'none', 'stock'):
        return None
    if not v:
        return LATE_DEFAULT_MS
    try:
        return max(0.0, float(v))
    except ValueError:
        return LATE_DEFAULT_MS


def battle_late_ms():
    v = os.environ.get(BATTLE_LATE_ENV, '').strip().lower()
    if v in ('off', 'no', 'none', 'stock'):
        return None
    if not v:
        return LATE_DEFAULT_MS
    try:
        return max(0.0, float(v))
    except ValueError:
        return LATE_DEFAULT_MS


def battle_late_slack_counts(divisor, margin_ms):
    """Battle frame_time is cps/div with no fudge (x86 0x41B6D2)."""
    return CPS / 60.0 + margin_ms * CPS / 1000.0 - CPS / float(divisor)


def late_slack_counts(divisor, margin_ms):
    """`1/60 s + margin - T(divisor)` in clock counts: the smallest
    `elapsed - T` that is still allowed to raise the debt flag."""
    t = CPS / float(divisor) - STOCK_FUDGE
    return CPS / 60.0 + margin_ms * CPS / 1000.0 - t


def build_late_cave(_entry, addr, slack, reg=21, resume=None):
    """w21 = (debt amount in x22, a double) >= slack. The words after the
    hook translate and store w21 as the debt flag; nothing after it reads
    d0/d1, x9 or the flags before the translator call clobbers them."""
    a = Asm(_entry, addr)
    bits = struct.unpack('<Q', struct.pack('<d', float(slack)))[0]
    src = 22 if reg == 21 else reg               # field: amount in x22
    a.emit(0x9E670000 | (src << 5))                # fmov d0, x<amount>
    for hw in range(4):
        imm = (bits >> (16 * hw)) & 0xFFFF
        base = 0xD2800000 if hw == 0 else 0xF2800000   # movz / movk x9
        a.emit(base | (hw << 21) | (imm << 5) | 9)
    a.emit(0x9E670000 | (9 << 5) | 1)              # fmov d1, x9
    a.emit(0x1E612000)                             # fcmp d0, d1
    a.emit(0x1A9FB7E0 | reg)                       # cset w<flag>, ge (NaN->0)
    a.emit(A.b(a.pc(), LATE_RESUME if resume is None else resume))
    return a.resolve()


def mode():
    """'gate' (default): the late gate only. 'hybrid': the gate plus the 526
    release-based deadline. 'stock': nothing."""
    v = os.environ.get(ENV, '').strip().lower()
    if v in ('stock', '0', 'off', 'no'):
        return 'stock'
    if v == 'hybrid':
        return 'hybrid'
    return 'gate'


def enabled():
    return mode() != 'stock'


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
    """baseline = min(baseline, last_release + D), branch-free.

    Runs directly after a `bl`, so x0..x17 and the flags are dead; X30 was
    saved by field_main_loop's own prologue. BUILD 532: the translator is
    called FIRST, because it clobbers x8, x9, x10 and the flags (+0x10FC3A0,
    measured) -- the 526 version held `release + D` in x10 across that call.
    """
    a = Asm(_entry, addr)
    AC.mov32(a, 0, GUEST_BASELINE)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    AC.bss_ptr(a, 9, bss)
    a.emit(A.ldr64(10, 9, 0))            # last release, 0 = none yet
    a.emit(A.movz(11, slack & 0xFFFF))
    a.emit(A.movk_hi(11, slack >> 16))
    a.emit(A.add_reg64(11, 10, 11))      # last_release + D
    a.emit(A.ldr64(9, 0, 0))             # the stock frame-start baseline
    a.emit(A.cmp_reg64(11, 9))
    a.emit(A.csel64(11, 11, 9, 3))       # lo: the earlier of the two
    a.emit(A.cmp_reg64(10, 31))
    a.emit(A.csel64(11, 9, 11, 0))       # eq: no release yet -> stock
    a.emit(A.str64(11, 0, 0))
    a.emit(START_ORIG)
    a.emit(A.b(a.pc(), resume))
    return a.resolve()


def apply_to_nso(src, dest, divisor, which=None, stock=None):
    """Install the field pacing. EVERY cave is branch-free and placed with
    ANY_SPAN: BUILD 532 found that on a finished module no 1 MB window has
    room for a windowed cave, so 526's start cave and 531's gate raised
    NoRoom and the whole pass was skipped (stock limiter kept) on hardware.
    """
    which = which or mode()
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw, space = ff7nx_tables.open_module(blob)
    text = space.text
    hybrid = which == 'hybrid'
    margin = late_ms()
    checks = []
    if hybrid:
        checks.append((EXIT_HOOK, EXIT_ORIG, 'limiter exit'))
        checks += [(va, START_ORIG, 'frame-start baseline')
                   for va in START_HOOKS]
    if margin is not None:
        checks.append((LATE_HOOK, LATE_ORIG, 'overrun debt flag'))
    bmargin = battle_late_ms()
    if bmargin is not None:
        checks.append((BATTLE_LATE_HOOK, BATTLE_LATE_ORIG,
                       'battle overrun debt flag'))
    if not checks:
        raise ValueError('nothing to install (%s=%s, %s=off)'
                         % (ENV, which, LATE_ENV))
    for va, want, what in checks:
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            raise ValueError('%s at +0x%X holds %08X, expected %08X'
                             % (what, va, got, want))
    bss = AC.scratch_base(blob, segs)
    slack = slack_counts(divisor)
    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))
    placed = {}
    hooks = 0

    def put(build):
        entry, words = ff7nx_cave.emit_laid_out(pool, build,
                                                span=ff7nx_cave.ANY_SPAN)
        placed.update(words)
        return entry

    # BUILD 534: the late gates live in the dead-space 'pace' part. The
    # padding pool on a finished module has room for one of them at most
    # (measured: the battle gate raised NoRoom, 3 words short).
    dead = None

    def put_dead(build):
        nonlocal dead
        if dead is None:
            import ff7nx_deadspace as DS
            dead = DS.Bump(*DS.part(src, 'pace', stock))
        entry = dead.put(build)
        return entry

    exit_entry, start_entries = None, []
    if hybrid:
        exit_entry = put(lambda e, ad: build_exit_cave(e, ad, bss))
        placed[EXIT_HOOK] = A.b(EXIT_HOOK, exit_entry)
        hooks += 1
        for hook in START_HOOKS:
            entry = put(lambda e, ad, r=hook + 4: build_start_cave(
                e, ad, bss, slack, resume=r))
            placed[hook] = A.b(hook, entry)
            start_entries.append(entry)
            hooks += 1
    late_entry, slack_late = None, None
    if margin is not None:
        slack_late = late_slack_counts(divisor, margin)
        late_entry = put_dead(lambda e, ad: build_late_cave(e, ad, slack_late))
        placed[LATE_HOOK] = A.b(LATE_HOOK, late_entry)
        hooks += 1
    battle_entry, bslack = None, None
    if bmargin is not None:
        bslack = battle_late_slack_counts(divisor, bmargin)
        battle_entry = put_dead(lambda e, ad: build_late_cave(
            e, ad, bslack, reg=20, resume=BATTLE_LATE_RESUME))
        placed[BATTLE_LATE_HOOK] = A.b(BATTLE_LATE_HOOK, battle_entry)
        hooks += 1
    if dead is not None:
        placed.update(dead.placed)
    for va, w in placed.items():
        struct.pack_into('<I', text, va, w)
    out = AC.pack(blob, space.commit(), BSS_BYTES if hybrid else 0)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'mode': which, 'exit_entry': exit_entry,
            'start_entry': start_entries[0] if start_entries else None,
            'start_entries': start_entries,
            'words': len(placed) - hooks, 'bss': bss if hybrid else None,
            'late_entry': late_entry, 'late_ms': margin,
            'battle_entry': battle_entry, 'battle_late_ms': bmargin,
            'battle_slack_ms': (None if bslack is None
                                else bslack * 1000.0 / CPS),
            'late_slack_ms': (None if slack_late is None
                              else slack_late * 1000.0 / CPS),
            'slack': slack, 'slack_ms': slack * 1000.0 / CPS}
