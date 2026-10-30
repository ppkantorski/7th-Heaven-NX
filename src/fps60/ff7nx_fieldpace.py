#!/usr/bin/env python3
"""
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

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
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


# BUILD 842: THE LIMITER SLEEPS INSTEAD OF SPINNING.
#
# Both limiters (field 0x6384E6, battle 0x41B965) wait by reading the clock
# in a tight loop until the frame's time is up. A core sits at 100% doing
# nothing for most of every frame -- the 80-90% CPU readout at 1x. (At 3x the
# booster's clock runs fast, the deadline is already past when the loop
# looks, and the CPU waits on vsync instead: same frame rate, far less CPU.)
#
# Each of the four wait loops ends with one branch that sends it round again
# (the main wait and the catch-up wait, field and battle). That branch now
# goes through a stub: if the loop is leaving, it leaves as before; if it is
# going round and MORE THAN SLEEP_MARGIN_MS of the wait is left, it calls
# nn::os::SleepThread for SLEEP_QUANTUM_US first. The last stretch is still
# spun, so the release lands where it always did; only the idle part sleeps.
#
#   remaining = d0 - d8   (d0: the time waited for, d8: elapsed -- both
#                          doubles, live at all four branches, measured)
#
# The clock counts the 19.2 MHz system tick (CPS), times the booster's
# multiplier, so the margin is real time at 1x and a third of it at 3x --
# still more than one quantum. A NaN compares unordered and is taken as
# "do not sleep". The loop heads all start with a call, so the flags the
# routine's fcmp sets are dead there.
SLEEP_ENV = 'SEVENTH_NX_LIMITER_SLEEP'      # off / 0 restores the spin
SLEEP_SYMBOL = '_ZN2nn2os11SleepThreadENS_8TimeSpanE'
SLEEP_MARGIN_MS = 2.0
SLEEP_QUANTUM_US = 250
# (branch VA, stock word, kind, register, loop head, exit, what)
SLEEP_SITES = (
    (0x9E5F30, 0x34FFEFAA, 'cbz', 10, 0x9E5D24, 0x9E5F34, 'field wait'),
    (0x9E6344, 0x35FFF108, 'cbnz', 8, 0x9E6164, 0x9E6348,
     'field catch-up wait'),
    (0x8F640, 0x34FFEFEA, 'cbz', 10, 0x8F43C, 0x8F644, 'battle wait'),
    (0x8FA3C, 0x35FFF148, 'cbnz', 8, 0x8F864, 0x8FA40,
     'battle catch-up wait'),
)
# BUILD 855: the world map's limiter (x86 0x74C7E4, ARM +0xF1EE40) spins the
# same way -- its own copy of the field limiter. Its normal wait loops while
# elapsed (d8) < frame_time (d0), as the four above; its catch-up wait
# (debt) loops while (cps/22 - debt) (d8) > elapsed (d0), so the time left
# is d8 - d0 there ('swap'). Measured from the translated body; their stubs
# and the swapped routine live in the world part (world_dead_part).
# (branch VA, stock word, kind, register, loop head, exit, what, swap)
WORLD_SLEEP_SITES = (
    (0xF1F5D4, 0x35FFF128, 'cbnz', 8, 0xF1F3F8, 0xF1F5D8, 'world wait',
     False),
    (0xF1F1D0, 0x34FFEFEA, 'cbz', 10, 0xF1EFCC, 0xF1F1D4,
     'world catch-up wait', True),
)


def sleep_enabled():
    return os.environ.get(SLEEP_ENV, '').strip().lower() not in (
        '0', 'off', 'no', 'false')


def _stp_d(rt, rt2, off):
    return 0x6D000000 | ((off // 8) << 15) | (rt2 << 10) | (31 << 5) | rt


def _ldp_d(rt, rt2, off):
    return 0x6D400000 | ((off // 8) << 15) | (rt2 << 10) | (31 << 5) | rt


def build_sleep_routine(_entry, addr, sleep_fn, margin_counts, quantum_ns,
                        swap=False):
    """Sleep one quantum when more than the margin is left; else return.
    `swap` (BUILD 855, the world catch-up wait): the time left is d8 - d0."""
    a = Asm(_entry, addr)
    a.emit(0x1E603902 if swap else 0x1E683802)     # fsub d2, d8, d0 / d0, d8
    bits = struct.unpack('<Q', struct.pack('<d', float(margin_counts)))[0]
    for hw in range(4):
        imm = (bits >> (16 * hw)) & 0xFFFF
        base = 0xD2800000 if hw == 0 else 0xF2800000   # movz / movk x9
        a.emit(base | (hw << 21) | (imm << 5) | 9)
    a.emit(0x9E670123)                             # fmov d3, x9
    a.emit(0x1E632040)                             # fcmp d2, d3
    a.bcond('done', A.LE)                          # <= margin or NaN: spin
    a.emit(A.sub_imm64(31, 31, 0xE0))
    for r in range(0, 18, 2):                      # x0..x17
        a.emit(A.stp64_off(r, r + 1, 31, 8 * r))
    a.emit(A.str64(30, 31, 0x90))                  # x30
    for k, r in enumerate(range(0, 8, 2)):         # d0..d7
        a.emit(_stp_d(r, r + 1, 0xA0 + 16 * k))
    a.emit(A.movz(0, quantum_ns & 0xFFFF))
    a.emit(A.movk_hi(0, quantum_ns >> 16))         # w0 -> x0, zero-extended
    a.emit(A.bl(a.pc(), sleep_fn))                 # SleepThread(TimeSpan ns)
    for k, r in enumerate(range(0, 8, 2)):
        a.emit(_ldp_d(r, r + 1, 0xA0 + 16 * k))
    a.emit(A.ldr64(30, 31, 0x90))
    for r in range(0, 18, 2):
        a.emit(A.ldp64_off(r, r + 1, 31, 8 * r))
    a.emit(A.add_imm64(31, 31, 0xE0))
    a.label('done')
    a.emit(0xD65F03C0)                             # ret
    return a.resolve()


def build_sleep_stub(_entry, addr, kind, reg, loop, leave, routine):
    """The loop's own branch, with the routine called on the way round."""
    a = Asm(_entry, addr)
    if kind == 'cbz':
        a.cbnz(reg, 'leave')                       # loops while reg == 0
    else:
        a.cbz(reg, 'leave')                        # loops while reg != 0
    a.emit(A.bl(a.pc(), routine))
    a.emit(A.b(a.pc(), loop))
    a.label('leave')
    a.emit(A.b(a.pc(), leave))
    return a.resolve()


def apply_sleep_to_nso(src, dest, stock=None):
    """Install the limiter sleep on every wait loop still stock. A loop
    whose branch is not the stock word is left spinning and reported."""
    import ff7nx_deadspace as DS
    import nsosyms
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw, space = ff7nx_tables.open_module(blob)
    text = space.text
    _m, plt = nsosyms.imports(src)
    sleep_fn = next((va for va, name in plt.items() if name == SLEEP_SYMBOL),
                    None)
    if sleep_fn is None:
        raise ValueError('%s is not imported by this module' % SLEEP_SYMBOL)
    sites, skipped = [], []
    for site in SLEEP_SITES:
        got = struct.unpack_from('<I', text, site[0])[0]
        (sites if got == site[1] else skipped).append(site)
    wstock = [w for w in WORLD_SLEEP_SITES
              if struct.unpack_from('<I', text, w[0])[0] == w[1]]
    if not sites and not wstock:
        raise ValueError('no wait loop holds its stock branch')
    margin = int(round(SLEEP_MARGIN_MS * CPS / 1000.0))
    quantum = SLEEP_QUANTUM_US * 1000
    placed = {}
    routine = None
    if sites:
        dead = DS.Bump(*DS.part(src, 'sleep', stock))
        routine = dead.put(lambda e, ad: build_sleep_routine(
            e, ad, sleep_fn, margin, quantum))
    else:
        dead = DS.Bump(0, 0)
    for va, _w, kind, reg, loop, leave, _what in sites:
        stub = dead.put(lambda e, ad, k=kind, r=reg, lp=loop, lv=leave:
                        build_sleep_stub(e, ad, k, r, lp, lv, routine))
        placed[va] = A.b(va, stub)
    placed.update(dead.placed)
    # BUILD 855: the world map's two waits
    wsites = [w for w in WORLD_SLEEP_SITES
              if struct.unpack_from('<I', text, w[0])[0] == w[1]]
    skipped += [w for w in WORLD_SLEEP_SITES if w not in wsites]
    if wsites:
        try:
            wlo, whi = world_dead_part(src, stock, 'sleep')
        except ValueError:
            skipped += wsites
            wsites = []
    if wsites:
        wdead = DS.Bump(wlo, whi)
        swapped = wdead.put(lambda e, ad: build_sleep_routine(
            e, ad, sleep_fn, margin, quantum, swap=True))
        if routine is None:
            routine = wdead.put(lambda e, ad: build_sleep_routine(
                e, ad, sleep_fn, margin, quantum))
        for va, _w, kind, reg, loop, leave, _what, swap in wsites:
            stub = wdead.put(lambda e, ad, k=kind, r=reg, lp=loop, lv=leave,
                             rt=(swapped if swap else routine):
                             build_sleep_stub(e, ad, k, r, lp, lv, rt))
            placed[va] = A.b(va, stub)
        placed.update(wdead.placed)
    sites = list(sites) + wsites
    for va, w in placed.items():
        struct.pack_into('<I', text, va, w)
    out = AC.pack(blob, space.commit(), 0)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'routine': routine, 'sleep_fn': sleep_fn,
            'installed': [s[6] for s in sites],
            'skipped': [s[6] for s in skipped],
            'margin_ms': SLEEP_MARGIN_MS, 'quantum_us': SLEEP_QUANTUM_US,
            'bytes': 4 * len(dead.placed)}


# BUILD 847: THE 3x BOOSTER RUNS `multiplier` LOGIC TICKS PER PRESENTED
# FRAME, IN EVERY FIELD AND BATTLE FRAME, WITHOUT EXTRA DRAWS.
#
# WHY 3x WAS INCONSISTENT. The booster (L3) only multiplies the port's
# virtual clock (+0x6720, multiplier at +0x12C99B8). Presents are vsync-bound,
# so speed comes only from how many logic ticks a presented frame runs. Stock,
# the only extra tick is the limiter's catch-up ("debt") path, and since the
# late gates (BUILD 531 field, 534 battle) the debt flag rises only when the
# frame is late for 60 by more than 2 ms. On the 3x clock that is a real frame
# time of more than ~6.2 ms. So at 3x:
#
#   light frames (< ~6.2 ms real work)    no debt -> 1 tick  -> 1x, low CPU
#   medium frames                         debt    -> 2 ticks -> 2x
#   heavy field frames                    debt    -> 2 ticks AND 2 draws, which
#                                         no longer fit a vsync -> ~1x
#
# (x86 0x905AD4, the flag 0x6388EE tests before its limiter, is initialised to
# 1 in ff7_en and has no writer: that "no limiter" branch never runs. Battle
# skips the debt path only on its early branch, [ebp-0x10] == 0.)
#
# BUILD 843 repeated the catch-up iteration. It was wrong three ways: frames
# with no debt still ran 1 tick; every field repeat redrew (draw_everything,
# up to ~16 ms in busy fields) -> ~40 fps; every battle repeat ran with the
# draw flag set (66c3b2(1)) so models were submitted three times a frame ->
# flicker.
#
# WHAT THIS DOES (multiplier > 1 only; at 1x each cave is the stock call):
#
#   field  (field_sub_6388EE 0x94B820). Before the frame's draw_everything
#          (+0x94B898), `multiplier - 1` extra ticks: the stock reset/begin
#          (649770, 638C03, 646479), frame counter += 1.0, the field update
#          x86 0x60E96C (logic and anim) and its stock exit checks -- the
#          stock catch-up iteration (+0x94B93C..+0x94BA90) minus its limiter
#          wait and its first draw. The frame then draws once, as stock. A
#          logic+anim tick costs ~0.4 ms (BUILD 787 profile). A failed exit
#          check skips the draw and returns to +0x94BC10, exactly as the stock
#          catch-up does. (847 ran the extras after the draw: each began a
#          new graphics frame nothing drew, the flip skipped it -> black.) The debt flag (0xCFFA98) is cleared, so the next
#          frame takes the normal path (one draw) instead of the catch-up.
#          No extras while a movie plays (movie_object->is_playing): some
#          movie code paces itself per field tick.
#   battle (battle_loop 0x41BAB3). Before the frame's own 66c3b2/update
#          (+0x90568 normal path, +0x90500 early path), `multiplier - 1` extra
#          NO-DRAW ticks: 66c3b2(0), frame counter += 1.0, update 0x41C0BB --
#          exactly the stock catch-up's first tick, which draws nothing
#          (66c3bf gates every battle draw, the fxinterp hooks included). On
#          the normal path the debt flag (0x9AD1D4) is cleared and the frame's
#          own 66c3b2 argument is set to 1, so the frame is "N no-draw ticks,
#          one drawing tick, render": the stock catch-up shape with more
#          no-draw ticks, and no catch-up wait.
#
#   menu   (BUILD 847c) battle_loop's menu pass count (+0x90AE8) becomes
#          k x multiplier, so the battle menu -- which fills the ATB gauges --
#          runs 3 passes per drawn frame at 3x (see MENU_SITE).
#
# Extras stop early once they have used BOOST_BUDGET_MS of real time
# (cntpct_el0) in a frame, so a spike (a first-time sound load) cannot stack.
BOOST_ENV = 'SEVENTH_NX_BOOST_TICKS'        # off / 0 = stock 3x
BOOST_MS_ENV = 'SEVENTH_NX_BOOST_TICKS_MS'  # extras' real-time budget per frame
BOOST_MULT = 0x12C99B8
BOOST_MAX_EXTRA = 4
BOOST_BUDGET_MS = 4.0
MRS_CNTPCT = 0xD53BE020                     # mrs Xt, cntpct_el0 (19.2 MHz)
TRANSLATE = 0x10FC3A0
CTX_GOT = 0x12CE2B0                         # -> the emulated x86 context
MOVIE_GOT = 0x12CE7C0                       # -> -> movie_object
MOVIE_IS_PLAYING = 0x1FC
FIELD_DEBT_FLAG = 0xCFFA98
FIELD_EXIT_FLAG = 0xCFF280                  # stock check: != 0 -> stop
FIELD_COUNTER_STEP = 0x7B78A0               # 1.0, the catch-up's += [..]
BATTLE_DEBT_FLAG = 0x9AD1D4
BATTLE_COUNTER_STEP = 0x7B74B8              # 1.0
GAME_OBJ_COUNTER = 0x40                     # game_obj->field_40, a double
# the field frame's draw (BUILD 847b: was the tail +0x94BC0C, after the
# draw -- extras there began new graphics frames that nothing drew, the flip
# skipped them and the screen went black)
FIELD_SITE = 0x94B898                       # bl draw_everything (x86 0x63A60B)
FIELD_DRAW = 0x9E6E80
FIELD_EPILOGUE = 0x94BC10
FIELD_RESET = 0x946330                      # x86 0x649770
FIELD_BEGIN = 0x9E8270                      # x86 0x638C03 (arg)
FIELD_BEGIN2 = 0x9E8420                     # x86 0x646479
FIELD_UPDATE = 0x94BD30                     # x86 0x60E96C (arg) -> eax
# battle_loop's two 66c3b2 calls
SET_DRAW = 0xAD8D30                         # x86 0x66C3B2 (flag)
BATTLE_UPDATE = 0x91B10                     # x86 0x41C0BB (arg)
BATTLE_SITES = ((0x90568, True, 'battle'), (0x90500, False, 'battle early'))
# BUILD 847c: the battle MENU's per-frame pass count. battle_loop's render
# runs battle_menu_update (x86 0x6CE8B3) in a counted loop, drawing the menu
# (0x6CEE84, and 6cdbfc(1)) only on the last pass:
#     41BDE5  mov [ebp-0x18], 4     ; vanilla 4 (15 fps battle, 60 Hz menu)
# which ff7nx_60fps sets to 1 (+0x90AE8 `mov w8, #1`), ff7nx_30fps to 2. The
# menu module is what fills the ATB gauges (and the limit gauge, the reel,
# the menu's own animations), once per DRAWN frame -- so the extra battle
# updates never sped it up and the ATB bars stayed at 1x (stock 3x too). At
# 3x the count becomes k x multiplier: the vanilla multi-pass menu, draw on
# the last pass only. The word is a `bl` to a 7-word cave that leaves the
# count in w8 (x0, the translated [ebp-0x18], is live and untouched).
MENU_SITE = 0x90AE8
MENU_ORR4 = 0x321E03E8                      # orr w8, wzr, #4 (vanilla)


def boost_enabled():
    # BUILD 847: on by default (843 was off: see above). =off restores the
    # stock 3x; at 1x every cave is the stock call either way.
    return os.environ.get(BOOST_ENV, '').strip().lower() not in (
        '0', 'off', 'no', 'false')


def boost_budget_counts():
    v = os.environ.get(BOOST_MS_ENV, '').strip()
    try:
        ms = float(v) if v else BOOST_BUDGET_MS
    except ValueError:
        ms = BOOST_BUDGET_MS
    return max(1, int(round(max(0.1, min(ms, 16.0)) * CPS / 1000.0)))


def _fadd_d(rd, rn, rm):
    return 0x1E602800 | (rm << 16) | (rn << 5) | rd


def _str_d(rt, rn, imm=0):
    return 0xFD000000 | ((imm >> 3) << 10) | (rn << 5) | rt


def _boost_head(a, budget):
    """Prologue shared by the caves: frame, x20 = context, w10 = mult (goes
    to 'out' at 1x), x21 = start tick, w22 = extras, x23 = budget."""
    a.emit(A.stp64_pre(29, 30, 31, -0x40))
    a.emit(A.stp64_off(19, 20, 31, 0x10))
    a.emit(A.stp64_off(21, 22, 31, 0x20))
    a.emit(A.stp64_off(23, 24, 31, 0x30))
    a.emit(A.adrp(9, a.pc(), BOOST_MULT & ~0xFFF))
    a.emit(A.ldr(10, 9, BOOST_MULT & 0xFFF))      # multiplier
    a.emit(A.sub_imm(22, 10, 1))                  # extras = mult - 1
    a.emit(A.cmp_imm(22, 1))
    a.bcond('out', A.LT)                          # 1x (or junk <= 1): stock
    a.emit(A.cmp_imm(22, BOOST_MAX_EXTRA))
    a.bcond('capped', A.LE)
    a.emit(A.movz(22, BOOST_MAX_EXTRA))
    a.label('capped')
    a.emit(A.adrp(20, a.pc(), CTX_GOT & ~0xFFF))
    a.emit(A.ldr64(20, 20, CTX_GOT & 0xFFF))      # x86 context
    AC.mov32(a, 23, budget)


def _boost_guest_zero(a, guest):
    AC.mov32(a, 0, guest)
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.str_(31, 0, 0))


def _boost_counter(a, step):
    """game_obj->field_40 += [step] (1.0) -- the stock catch-up's x87 add,
    done natively: [ebp+8] is game_obj in both callers."""
    a.emit(A.ldr(8, 20, 0x14))                    # ebp
    a.emit(A.add_imm(0, 8, 8))
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.ldr(8, 0, 0))                        # game_obj
    a.emit(A.add_imm(0, 8, GAME_OBJ_COUNTER))
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.mov_reg64(19, 0))
    AC.mov32(a, 0, step)
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(0xFD400001)                            # ldr d1, [x0]
    a.emit(0xFD400260)                            # ldr d0, [x19]
    a.emit(_fadd_d(0, 0, 1))
    a.emit(_str_d(0, 19))


def _boost_push_arg_call(a, fn, pop=True):
    """push [ebp+8]; reserve the return slot; bl fn; (pop the argument) --
    the stock words of +0x94BA20..+0x94BA60, temp w19 for w21."""
    a.emit(A.ldr(8, 20, 0x14))
    a.emit(A.add_imm(0, 8, 8))
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.ldr(8, 20, 0x10))
    a.emit(A.ldr(19, 0, 0))                       # arg
    a.emit(A.sub_imm(0, 8, 4))
    a.emit(A.str_(19, 20, 4))                     # ecx = arg (as stock)
    a.emit(A.str_(0, 20, 0x10))                   # esp -= 4
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.str_(19, 0, 0))                      # [esp] = arg
    _boost_esp(a, -4)                             # the return slot
    a.emit(A.bl(a.pc(), fn))                      # callee pops the slot
    if pop:
        _boost_esp(a, 4)


def _boost_esp(a, delta):
    a.emit(A.ldr(8, 20, 0x10))
    a.emit(A.add_imm(8, 8, delta) if delta > 0 else A.sub_imm(8, 8, -delta))
    a.emit(A.str_(8, 20, 0x10))


def _boost_tail(a):
    """Stop when the extras have used the budget; else count down."""
    a.emit(MRS_CNTPCT | 8)
    a.emit(A.sub_reg64(8, 8, 21))
    a.emit(A.cmp_reg64(8, 23))
    a.bcond('out', A.HS)
    a.emit(A.sub_imm(22, 22, 1))
    a.cbnz(22, 'loop')


def _boost_foot(a):
    a.label('out')
    a.emit(A.ldp64_off(23, 24, 31, 0x30))
    a.emit(A.ldp64_off(21, 22, 31, 0x20))
    a.emit(A.ldp64_off(19, 20, 31, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, 0x40))


def build_field_boost_cave(_entry, addr, budget):
    """Replaces the frame's `bl draw_everything` at +0x94B898. The extras,
    then the stock draw as a tail call (x30 = +0x94B89C). A failed exit
    check skips the draw and returns to the epilogue +0x94BC10, as the
    stock catch-up does."""
    a = Asm(_entry, addr)
    _boost_head(a, budget)
    _boost_guest_zero(a, FIELD_DEBT_FLAG)         # this frame: normal path
    a.emit(A.adrp(9, a.pc(), MOVIE_GOT & ~0xFFF))
    a.emit(A.ldr64(9, 9, MOVIE_GOT & 0xFFF))
    a.cbz64(9, 'go')
    a.emit(A.ldr64(9, 9, 0))
    a.cbz64(9, 'go')
    a.emit(A.ldr(9, 9, MOVIE_IS_PLAYING))
    a.cbnz(9, 'out')                              # a movie plays: stock
    a.label('go')
    a.emit(MRS_CNTPCT | 21)
    a.label('loop')
    _boost_esp(a, -4)                             # +0x94B93C..: reset/begin
    a.emit(A.bl(a.pc(), FIELD_RESET))
    _boost_push_arg_call(a, FIELD_BEGIN, pop=False)
    a.emit(A.bl(a.pc(), FIELD_BEGIN2))            # takes the arg's slot
    _boost_counter(a, FIELD_COUNTER_STEP)
    _boost_push_arg_call(a, FIELD_UPDATE)         # eax = update(arg)
    a.emit(A.ldr(8, 20, 0))                       # the stock exit checks
    a.cbz(8, 'fail')
    AC.mov32(a, 0, FIELD_EXIT_FLAG)
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.ldr(8, 0, 0))
    a.cbnz(8, 'fail')
    _boost_tail(a)
    _boost_foot(a)
    a.emit(A.b(a.pc(), FIELD_DRAW))               # the stock draw
    a.label('fail')
    a.emit(A.ldp64_off(23, 24, 31, 0x30))
    a.emit(A.ldp64_off(21, 22, 31, 0x20))
    a.emit(A.ldp64_off(19, 20, 31, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, 0x40))
    a.emit(A.adrp(30, a.pc(), FIELD_EPILOGUE & ~0xFFF))
    a.emit(A.add_imm64(30, 30, FIELD_EPILOGUE & 0xFFF))
    a.emit(A.ret())
    return a.resolve()


def build_battle_boost_cave(_entry, addr, budget):
    """One cave, two entries, each replacing a `bl 66c3b2` in battle_loop:
    word 0 for the early path (+0x90500), word 2 for the normal path
    (+0x90568). The extras, then the stock call as a tail call with the
    original x30."""
    a = Asm(_entry, addr)
    a.emit(A.movz(16, 0))                         # +0: early path
    a.b('common')
    a.emit(A.movz(16, 1))                         # +8: normal path
    a.label('common')
    _boost_head(a, budget)
    a.cbz(16, 'go')                               # w16 is intact here
    _boost_guest_zero(a, BATTLE_DEBT_FLAG)        # +0x905B0 re-reads it
    a.emit(A.ldr(8, 20, 0x10))                    # the frame's own flag:
    a.emit(A.add_imm(0, 8, 4))                    # [esp+4] := 1 (it was 0
    a.emit(A.bl(a.pc(), TRANSLATE))               # when the debt was set)
    a.emit(A.movz(8, 1))
    a.emit(A.str_(8, 0, 0))
    a.label('go')
    a.emit(MRS_CNTPCT | 21)
    a.label('loop')
    a.emit(A.ldr(8, 20, 0x10))                    # push 0
    a.emit(A.sub_imm(0, 8, 4))
    a.emit(A.str_(0, 20, 0x10))
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.str_(31, 0, 0))
    _boost_esp(a, -4)
    a.emit(A.bl(a.pc(), SET_DRAW))                # 66c3b2(0): no draw
    _boost_esp(a, 4)
    _boost_counter(a, BATTLE_COUNTER_STEP)
    _boost_push_arg_call(a, BATTLE_UPDATE)
    _boost_tail(a)
    _boost_foot(a)
    a.emit(A.b(a.pc(), SET_DRAW))                 # the stock call
    return a.resolve()


BATTLE_ENTRY = {False: 0, True: 8}               # cave offset per site


# BUILD 855: THE WORLD MAP. world_main_loop (x86 0x74BE49, ARM +0xF1E0D0) is
# the field's shape: limiter (0x74C7E4, a spin -- see WORLD_SLEEP_SITES),
# input/texture housekeeping, setbg + clear, begin_scene, then one pass:
#     74c973(debt ? 0 : 1)            the draw flag [0x96994C]; every world
#                                     draw (the [0xDE68FC] callbacks 0x74C179
#                                     /0x74C3F0/0x74C589, the model and
#                                     effect draws) returns at once on 0
#     [0xDE6940] = 74db8c(0xCC1644, 0xCC1648, 0xCC0D80, 0)   the update
#     [0xDE68FC](); 666df4; 6656d1; 660e95(1); 66e641([0xE2C3F4])
# and, when the limiter left debt ([0xDE69EC]), the catch-up: debt off,
# [0xDE69F8] = 1, draw flag 1, counter += [0x7B7EC0] (1.0), the
# housekeeping (666ddd 6656a4 666da3 666dc0, 660370, 41a21e(0)) and the pass
# again -- so a debt frame is one no-draw tick and one drawing tick inside
# one scene, and every other frame one tick. The booster only speeds the
# clock, presents are vsync-bound: the world map stayed at 1x.
#
# BUILD 855b (hardware 10-28: at 3x, sprite pieces and texture-sheet
# fragments stuck in the top-left corner over everything). The stock
# catch-up's no-draw tick still SUBMITS: 666df4 (the poly sets at
# obj+0x938, 660d96), 6656d1 (obj+0x920 -> 66562b, 660e95/660c3a),
# 660e95(1) (driver+0x84) and 66e641 draw whatever the update queued, and
# with the draw flag 0 the world view was never set up for it -- so the
# effect sprites queued by the update landed at the screen origin. Rare at
# 1x (debt frames); every frame at 3x. The extras now submit nothing: input,
# update, then the housekeeping (666ddd 6656a4 666da3 666dc0), which resets
# the render lists -- dropping the extra tick's queued polygons and readying
# the next tick, as the frame's own top-of-loop call does for the first.
#
# The cave replaces the pass's `bl 74db8c` (+0xF1E598, arguments pushed).
# At multiplier > 1 it first turns this frame's debt off (debt 0, limiter on
# next frame) and runs `multiplier - 1` extra NO-DRAW ticks -- the stock
# catch-up tick with the draw flag 0, minus its 660370 (which restarts the
# limiter's clock: the next frame would wait for the extras' time again) and
# minus the [0xDE68FC] call (a no-op with the flag 0) -- then sets the flag to
# 1 and tail-calls the stock update: "N no-draw ticks, one drawing tick", the
# battle cave's shape. Same budget and cap. At 1x it is the stock call.
# (855b: the extra ticks submit nothing at all -- see above.)
WORLD_SITE = 0xF1E598                       # bl 74db8c (x86 0x74BF5D)
WORLD_UPDATE = 0xF29100                     # x86 0x74DB8C (4 args) -> eax
WORLD_DRAW_FLAG = 0x96994C
WORLD_DEBT_FLAG = 0xDE69EC
WORLD_LIMIT_FLAG = 0xDE69F8                 # 1 = run the limiter next frame
WORLD_RESULT = 0xDE6940
WORLD_COUNTER_STEP = 0x7B7EC0               # 1.0
WORLD_UPDATE_ARGS = (0xCC1644, 0xCC1648, 0xCC0D80, 0)
WORLD_HOUSE = (0xAB06B0, 0xAA7650, 0xAB0210, 0xAB0410)   # 666ddd 6656a4
                                                         # 666da3 666dc0
WORLD_INPUT = 0x29FD0                       # x86 0x41A21E (0)
WORLD_AFTER = (0xAB0770, 0xAA7780)          # 666df4 6656d1 (obj)
WORLD_FLUSH = 0xA87860                      # x86 0x660E95 (1, obj)
WORLD_SUBMIT = 0xAD81A0                     # x86 0x66E641 ([0xE2C3F4], obj)
WORLD_SUBMIT_ARG = 0xE2C3F4
WORLD_SLEEP_BYTES = 0x180                   # the world part's first 384 B
WORLD_DIVISOR = 0x969958                    # the world limiter's fps (double)
# BUILD 855c: THE EXTRAS FIT THE FRAME. A world tick is far heavier than a
# field one (hardware 10-28: at 3x the frame rate dropped and the CPU rose
# with the Highwind just hovering), so a fixed 4 ms budget either costs
# frames or wastes speed. 32 bytes of module BSS hold a controller: the
# frame period P (cave entry to cave entry, cntpct), the extras' budget, its
# ceiling, and the last extra tick's cost. A frame that came late (P >
# T + T/8, T = CPS / the world fps) cuts the ceiling and the budget by a
# quarter each; an on-time one grows the budget by T/128 up to the ceiling,
# which itself regrows by T/16384 a frame (~0.06 ms a second) up to 5T/8.
# An extra runs only when the time already used plus the last tick's cost
# fits the budget. A pause or mode change (P > 5T) restarts at T/2 under
# 5T/8. (855c capped it all at T/4 = 4.2 ms and started at T/8: a world tick
# costs more than that on the Switch, so no extra ever started -- hardware
# 10-28, "3x does nothing now". The frame's own feedback is the limit now.)
# So the world map runs as many ticks as the frame has room for -- 3x where
# it fits, less where a tick does not fit -- and keeps its frame rate.
WORLD_STATE_PREV = 0
WORLD_STATE_BUDGET = 8
WORLD_STATE_CEIL = 16                       # 855c/d only; unused since 855e
WORLD_STATE_TAP = 16                        # 855f: the tap count before an extra
# 855f: 0x7667B2 lands / leaves a vehicle on a TAP of button 0x40: it counts
# the ticks the button is held in [0xE3A870] and acts on release only when
# the count is 1..14. At 3x that is 3 ticks a frame, so a normal tap (5+
# frames) counted past 14 and was ignored -- "click B enough times and it
# eventually lands". Extra ticks never advance the count past 1, so it counts
# frames as at 1x.
WORLD_TAP_COUNT = 0xE3A870
WORLD_STATE_SKIP = 40                       # 855g: 1 = this extra skips 0x75079D
# 855g: 0x75079D is not only the terrain: it also runs the landing / exit
# step. With [0xE3A86C] (0x7667B2's land state) at 2 it calls 0x766417, which
# sets the party's step-out position; the next 0x7667B2 lands only if that
# position moved, else the Highwind climbs again ("touches the ground, then
# goes back up"). An extra that skipped the call lost the step. So an extra
# runs the full call while a land / exit is under way ([0xE3A86C] != 0, or
# 0x75079D's other 0x766417 caller [0xE28CC0] == 1).
WORLD_LAND_STATE = 0xE3A86C
WORLD_EXIT_FLAG = 0xE28CC0
WORLD_STATE_TICK = 24
WORLD_STATE_EXTRA = 32                      # 855e: 1/2 while extras run
WORLD_STATE_BYTES = 48
# BUILD 855e (hardware 10-28 of 855d: "it speeds up briefly, then stops and
# stays at 1x"). Why a world tick is so heavy: world_mode_loop's update
# (x86 0x74DB8C) DRAWS THE TERRAIN itself -- its call to 0x75079D (+0xF2A390)
# builds the 5x5 window (world_sub_751EFC, and worldfar's whole far field on
# top) on every tick, with no draw-flag test -- so each extra tick rebuilt
# and threw away the whole landscape. The extras now skip that one call
# (WORLD_TERRAIN_SITE, its own 8-word cave: it pops the call's return slot as
# the callee would); the frame's own tick draws it as before. The block
# window it keeps follows the player once a frame, which a tick's movement
# never outruns (the window is 5x5 around the last drawn position).
# The controller is simpler too: one budget, -1/4 on a late frame (not
# under T/16), +T/64 on an on-time one, up to 5T/8; and a frame with no extra
# lets the remembered tick cost fade by 1/8, so a probe follows.
WORLD_TERRAIN_SITE = 0xF2A390               # bl 0x75079D in 0x74DB8C
WORLD_TERRAIN = 0xF35A40                    # x86 0x75079D
WORLD_PART_BYTES = 0x600                    # = ff7nx_fxinterp.WORLD_TOP


def world_dead_part(src, stock=None, which='sleep'):
    """(lo, hi) of one half of the world part -- the top WORLD_PART_BYTES of
    the dead body ff7nx_fxinterp and ff7nx_facial share (x86 0x4079B8;
    facial keeps 4,880 of its 6,416 bytes and uses at most 3,552): 'sleep'
    is its first WORLD_SLEEP_BYTES, 'boost' the rest. Refused when that half
    is not stock bytes."""
    import ff7nx_fxinterp as FX
    lo, hi = FX.dead_region(src, None)
    hi &= ~15
    lo = hi - WORLD_PART_BYTES
    if which == 'sleep':
        hi = lo + WORLD_SLEEP_BYTES
    elif which == 'boost':
        lo += WORLD_SLEEP_BYTES
    else:
        raise ValueError(which)
    if stock is not None and nxmap.Main(stock).text[lo:hi] != \
            nxmap.Main(src).text[lo:hi]:
        raise ValueError('the world %s part was already written' % which)
    return lo, hi


# 855c: shifted-register forms the controller needs (a64 has the 32-bit
# ones only); every one is decoded through capstone in test_boostticks.
def _add_lsl64(rd, rn, rm, sh):
    return 0x8B000000 | (rm << 16) | (sh << 10) | (rn << 5) | rd


def _add_lsr64(rd, rn, rm, sh):
    return 0x8B400000 | (rm << 16) | (sh << 10) | (rn << 5) | rd


def _sub_lsr64(rd, rn, rm, sh):
    return 0xCB400000 | (rm << 16) | (sh << 10) | (rn << 5) | rd


def _lsr64(rd, rn, sh):
    """LSR Xd, Xn, #sh -- UBFM Xd, Xn, #sh, #63."""
    return 0xD340FC00 | (sh << 16) | (rn << 5) | rd


def _g_store(a, guest, value=None, reg=None):
    """[guest] = value (a literal) or w<reg> (callee-saved)."""
    AC.mov32(a, 0, guest)
    a.emit(A.bl(a.pc(), TRANSLATE))
    if reg is None:
        AC.mov32(a, 8, value)
        reg = 8
    a.emit(A.str_(reg, 0, 0))


def _wbl(a, name):
    a.fixups.append((len(a.words), name, 'bl'))
    a.emit(0)


def _wpush(a, value=None, reg=None):
    if reg is None:
        AC.mov32(a, 1, value)
    else:
        a.emit(A.mov_reg(1, reg))
    _wbl(a, 'push')


def _wcall(a, fn, nargs):
    _boost_esp(a, -4)                              # the return slot
    a.emit(A.bl(a.pc(), fn))                       # the callee pops it
    _boost_esp(a, 4 * nargs)


def build_world_terrain_cave(_entry, addr, state):
    """855e: the terrain call skipped while extras run; 855g: only an extra
    the boost cave marked ([state+SKIP], no land / exit under way)."""
    a = Asm(_entry, addr)
    AC.bss_ptr(a, 16, state)
    a.emit(A.ldr64(17, 16, WORLD_STATE_SKIP))
    a.fixups.append((len(a.words), 'skip', 'cbnz64x', 17))   # cbnz x17
    a.emit(0)
    a.emit(A.b(a.pc(), WORLD_TERRAIN))             # the stock call
    a.label('skip')
    a.emit(A.adrp(16, a.pc(), CTX_GOT & ~0xFFF))
    a.emit(A.ldr64(16, 16, CTX_GOT & 0xFFF))
    a.emit(A.ldr(17, 16, 0x10))
    a.emit(A.add_imm(17, 17, 4))                   # the callee's ret pops
    a.emit(A.str_(17, 16, 0x10))                   # its return slot
    a.emit(A.ret())
    cb = [f for f in a.fixups if f[2] == 'cbnz64x']
    a.fixups = [f for f in a.fixups if f[2] != 'cbnz64x']
    words = a.resolve()
    for i, name, _k, reg in cb:
        off = (a.labels[name] - a.addr(i)) >> 2
        words[i] = 0xB5000000 | ((off & 0x7FFFF) << 5) | reg
    return words


def build_world_boost_cave(_entry, addr, budget, state=None):
    """Replaces world_main_loop's pass `bl 74db8c` at +0xF1E598.

    855c: `state` is 32 bytes of module BSS for the frame-time controller
    (WORLD_STATE_*). None builds the 855b fixed-budget cave."""
    a = Asm(_entry, addr)
    a.emit(A.stp64_pre(29, 30, 31, -0x50))
    a.emit(A.stp64_off(19, 20, 31, 0x10))
    a.emit(A.stp64_off(21, 22, 31, 0x20))
    a.emit(A.stp64_off(23, 24, 31, 0x30))
    a.emit(A.stp64_off(25, 26, 31, 0x40))
    a.emit(A.adrp(9, a.pc(), BOOST_MULT & ~0xFFF))
    a.emit(A.ldr(10, 9, BOOST_MULT & 0xFFF))
    a.emit(A.sub_imm(22, 10, 1))                  # extras = mult - 1
    a.emit(A.cmp_imm(22, 1))
    a.bcond('out', A.LT)                          # 1x: the stock call
    a.emit(A.cmp_imm(22, BOOST_MAX_EXTRA))
    a.bcond('capped', A.LE)
    a.emit(A.movz(22, BOOST_MAX_EXTRA))
    a.label('capped')
    a.emit(A.adrp(20, a.pc(), CTX_GOT & ~0xFFF))
    a.emit(A.ldr64(20, 20, CTX_GOT & 0xFFF))
    a.emit(A.ldr(8, 20, 0x14))                     # obj = [ebp + 8]
    a.emit(A.add_imm(0, 8, 8))
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.ldr(24, 0, 0))
    _g_store(a, WORLD_DEBT_FLAG, 0)                # no catch-up this frame
    _g_store(a, WORLD_LIMIT_FLAG, 1)               # the limiter runs next
    if state is None:
        AC.mov32(a, 23, budget)
        a.emit(A.movz(26, 0))                      # no tick-cost estimate
    else:
        # T = CPS / [0x969958], the world frame in clock counts
        AC.mov32(a, 0, WORLD_DIVISOR)
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(0xFD400000)                         # ldr d0, [x0]
        bits = struct.unpack('<Q', struct.pack('<d', float(CPS)))[0]
        for hw in range(4):
            imm = (bits >> (16 * hw)) & 0xFFFF
            base = 0xD2800000 if hw == 0 else 0xF2800000
            a.emit(base | (hw << 21) | (imm << 5) | 9)
        a.emit(0x9E670121)                         # fmov d1, x9
        a.emit(A.fdiv_d(2, 1, 0))                  # d2 = CPS / divisor
        a.emit(0x9E790059)                         # fcvtzu x25, d2
        AC.mov32(a, 9, CPS // 60)
        a.emit(A.cmp_reg64(25, 31))                # junk divisor: 60
        a.emit(A.csel64(25, 9, 25, A.EQ))
        AC.mov32(a, 10, CPS)
        a.emit(A.cmp_reg64(25, 10))                # (0 / NaN / < 1 fps)
        a.emit(A.csel64(25, 9, 25, A.HI))
        a.emit(MRS_CNTPCT | 21)                    # now
        AC.bss_ptr(a, 9, state)
        a.emit(A.ldr64(10, 9, WORLD_STATE_PREV))
        a.emit(A.str64(21, 9, WORLD_STATE_PREV))
        a.emit(A.ldr64(23, 9, WORLD_STATE_BUDGET))
        a.emit(A.sub_reg64(12, 21, 10))            # P = now - prev
        a.cbz64(10, 'init')
        a.cbz64(23, 'init')
        a.emit(_add_lsl64(13, 25, 25, 2))          # 5T
        a.emit(A.cmp_reg64(12, 13))
        a.bcond('init', A.HI)                      # a pause, a mode change
        a.emit(_add_lsr64(13, 25, 25, 3))          # T + T/8
        a.emit(A.cmp_reg64(12, 13))
        a.bcond('hit', 0x9)                        # ls
        a.emit(_sub_lsr64(23, 23, 23, 2))          # late: budget -= 1/4
        a.emit(_lsr64(13, 25, 4))                  # ... not under T/16
        a.emit(A.cmp_reg64(23, 13))
        a.emit(A.csel64(23, 13, 23, 0x3))          # lo
        a.b('store')
        a.label('hit')
        a.emit(_add_lsr64(23, 23, 25, 6))          # on time: += T/64
        a.emit(_lsr64(13, 25, 1))                  # ... up to 5T/8
        a.emit(_add_lsr64(13, 13, 25, 3))
        a.emit(A.cmp_reg64(23, 13))
        a.emit(A.csel64(23, 13, 23, A.HI))
        a.b('store')
        a.label('init')
        a.emit(_lsr64(23, 25, 1))                  # budget T/2
        a.label('store')
        a.emit(A.str64(23, 9, WORLD_STATE_BUDGET))
        a.emit(A.ldr64(26, 9, WORLD_STATE_TICK))   # last tick's cost
        a.emit(A.movz(10, 1))
        a.emit(A.str64(10, 9, WORLD_STATE_EXTRA))  # 1: extras, none ran yet
    if state is None:
        a.emit(MRS_CNTPCT | 21)
    a.label('loop')
    # 855c: an extra starts only when it is expected to finish in budget
    a.emit(MRS_CNTPCT | 8)
    a.emit(A.sub_reg64(8, 8, 21))
    a.emit(A.add_reg64(8, 8, 26))
    a.emit(A.cmp_reg64(8, 23))
    a.bcond('done', A.HI)
    a.emit(MRS_CNTPCT | 26)                        # this tick's start
    _g_store(a, WORLD_DRAW_FLAG, 0)                # a no-draw tick
    _boost_counter(a, WORLD_COUNTER_STEP)          # (uses x19)
    if state is not None:
        AC.mov32(a, 0, WORLD_TAP_COUNT)            # 855f: remember the count
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldr(8, 0, 0))
        AC.bss_ptr(a, 9, state)
        a.emit(A.str_(8, 9, WORLD_STATE_TAP))
        # 855g: skip 0x75079D unless a land / exit is under way
        a.emit(A.movz(19, 1))
        AC.mov32(a, 0, WORLD_LAND_STATE)
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldr(8, 0, 0))
        a.cbz(8, 'noland')
        a.emit(A.movz(19, 0))
        a.label('noland')
        AC.mov32(a, 0, WORLD_EXIT_FLAG)
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldr(8, 0, 0))
        a.emit(A.cmp_imm(8, 1))
        a.bcond('noexit', A.NE)
        a.emit(A.movz(19, 0))
        a.label('noexit')
        AC.bss_ptr(a, 9, state)
        a.emit(A.str64(19, 9, WORLD_STATE_SKIP))
    for v in reversed(WORLD_UPDATE_ARGS):
        _wpush(a, v)
    _wcall(a, WORLD_UPDATE, 4)
    a.emit(A.ldr(19, 20, 0))                       # eax
    _g_store(a, WORLD_RESULT, reg=19)
    if state is not None:
        # 855f: an extra may start a tap (0 -> 1) but not lengthen it
        AC.mov32(a, 0, WORLD_TAP_COUNT)
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldr(8, 0, 0))
        AC.bss_ptr(a, 9, state)
        a.emit(A.ldr(10, 9, WORLD_STATE_TAP))
        a.cbz(10, 'tapped')
        a.emit(A.add_imm(11, 10, 1))
        a.emit(A.cmp_reg(8, 11))
        a.bcond('tapped', A.NE)
        a.emit(A.str_(10, 0, 0))
        a.label('tapped')
    # 855b: nothing of this tick is submitted. The housekeeping resets the
    # render lists (6a28c0 / 68f839 / 670f4f -> 6a5686), so running it HERE
    # drops whatever the no-draw update still queued and readies the next
    # tick -- the frame's own top-of-loop call did the same for the first.
    for fn in WORLD_HOUSE:
        _wpush(a, reg=24)
        _wcall(a, fn, 1)
    # 855c: the input is read AFTER the tick, for the next one -- the
    # frame's own read (x86 0x74BE9C) is the first tick's, so a press is
    # seen by exactly one tick (855b read it again before each extra, which
    # cleared the press before any tick saw it: "rapidly click land")
    _wpush(a, 0)
    _wcall(a, WORLD_INPUT, 1)
    a.emit(MRS_CNTPCT | 8)
    a.emit(A.sub_reg64(26, 8, 26))                 # this tick's cost
    if state is not None:
        AC.bss_ptr(a, 9, state)
        a.emit(A.str64(26, 9, WORLD_STATE_TICK))
        a.emit(A.movz(10, 2))
        a.emit(A.str64(10, 9, WORLD_STATE_EXTRA))  # 2: an extra ran
    else:
        a.emit(A.movz(26, 0))
    a.emit(A.sub_imm(22, 22, 1))
    a.cbnz(22, 'loop')
    a.label('done')
    if state is not None:
        # 855e: a frame that ran no extra lets the remembered tick cost
        # fade (1/8 a frame), so one heavy tick cannot stop the boost for
        # good -- the next probe measures it again
        AC.bss_ptr(a, 9, state)
        a.emit(A.ldr64(10, 9, WORLD_STATE_EXTRA))
        a.emit(A.cmp_imm(10, 1))
        a.bcond('ran', A.NE)
        a.emit(A.ldr64(11, 9, WORLD_STATE_TICK))
        a.emit(_sub_lsr64(11, 11, 11, 3))
        a.emit(A.str64(11, 9, WORLD_STATE_TICK))
        a.label('ran')
        a.emit(A.str64(31, 9, WORLD_STATE_EXTRA))  # 0: the frame's own tick
        a.emit(A.str64(31, 9, WORLD_STATE_SKIP))   # 855g: it runs 0x75079D
    _g_store(a, WORLD_DRAW_FLAG, 1)                # the frame's own tick draws
    a.label('out')
    a.emit(A.ldp64_off(25, 26, 31, 0x40))
    a.emit(A.ldp64_off(23, 24, 31, 0x30))
    a.emit(A.ldp64_off(21, 22, 31, 0x20))
    a.emit(A.ldp64_off(19, 20, 31, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, 0x50))
    a.emit(A.b(a.pc(), WORLD_UPDATE))              # the stock call
    # push w1 onto the guest stack
    a.label('push')
    a.emit(A.stp64_pre(29, 30, 31, -0x20))
    a.emit(A.str_(1, 31, 0x10))
    a.emit(A.ldr(8, 20, 0x10))
    a.emit(A.sub_imm(0, 8, 4))
    a.emit(A.str_(0, 20, 0x10))
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.ldr(9, 31, 0x10))
    a.emit(A.str_(9, 0, 0))
    a.emit(A.ldp64_post(29, 30, 31, 0x20))
    a.emit(A.ret())
    bls = [f for f in a.fixups if f[2] == 'bl']
    a.fixups = [f for f in a.fixups if f[2] != 'bl']
    words = a.resolve()
    for i, name, _k in bls:
        words[i] = A.bl(a.addr(i), a.labels[name])
    return words


def menu_site_count(word):
    """k of the menu pass count word, or None when it is not a plain
    `mov w8, #k` (1..8) / the vanilla `orr w8, wzr, #4`."""
    if word == MENU_ORR4:
        return 4
    if (word & 0xFFE0001F) == 0x52800008:
        k = (word >> 5) & 0xFFFF
        if 1 <= k <= 8:
            return k
    return None


def build_menu_boost_cave(_entry, addr, k):
    """w8 = k * clamp(multiplier, 1, 1 + BOOST_MAX_EXTRA); only x9 and the
    flags besides. Returns to +0x90AEC."""
    a = Asm(_entry, addr)
    a.emit(A.adrp(9, a.pc(), BOOST_MULT & ~0xFFF))
    a.emit(A.ldr(8, 9, BOOST_MULT & 0xFFF))
    a.emit(A.cmp_imm(8, 1))
    a.bcond('lo', A.LT)
    a.emit(A.cmp_imm(8, 1 + BOOST_MAX_EXTRA))
    a.bcond('ok', A.LE)
    a.emit(A.movz(8, 1 + BOOST_MAX_EXTRA))
    a.b('ok')
    a.label('lo')
    a.emit(A.movz(8, 1))
    a.label('ok')
    if k != 1:
        a.emit(A.movz(9, k))
        a.emit(A.mul(8, 8, 9))
    a.emit(A.ret())
    return a.resolve()


def apply_boost_to_nso(src, dest, stock=None, budget=None, region=None):
    """Install the field tail cave and the battle cave on every site still
    holding its stock call. `region` overrides the dead-space part (a test
    main patched from an earlier test main that used it)."""
    import ff7nx_deadspace as DS
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw, space = ff7nx_tables.open_module(blob)
    text = space.text
    budget = boost_budget_counts() if budget is None else budget
    bss_grow = 0
    wstate = None
    sites = [(FIELD_SITE, FIELD_DRAW, 'field', None)]
    sites += [(va, SET_DRAW, what, normal)
              for va, normal, what in BATTLE_SITES]
    todo, skipped = [], []
    for site in sites:
        ok = struct.unpack_from('<I', text, site[0])[0] == A.bl(site[0],
                                                                site[1])
        (todo if ok else skipped).append(site)
    menu_k = menu_site_count(struct.unpack_from('<I', text, MENU_SITE)[0])
    world_stock = struct.unpack_from('<I', text, WORLD_SITE)[0] == \
        A.bl(WORLD_SITE, WORLD_UPDATE)
    if not todo and not world_stock:
        raise ValueError('no booster site holds its stock call')
    if todo or menu_k is not None:
        lo, hi = region if region else DS.part(src, 'boost', stock)
    else:
        lo = hi = 0                     # only the world map is left to do
    dead = DS.Bump(lo, hi)
    caves = {}
    battle = None
    for va, _callee, what, normal in todo:
        if normal is None:
            cave = dead.put(lambda e, ad: build_field_boost_cave(e, ad,
                                                                 budget))
        else:
            if battle is None:
                battle = dead.put(lambda e, ad: build_battle_boost_cave(
                    e, ad, budget))
            cave = battle + BATTLE_ENTRY[normal]
        caves[what] = cave
        dead.placed[va] = A.bl(va, cave)
    # BUILD 855: the world map, in its own part
    if world_stock:
        try:
            wlo, whi = world_dead_part(src, stock, 'boost')
            wdead = DS.Bump(wlo, whi)
            wstate = (AC.scratch_base(blob, segs) + 15) & ~15
            bss_grow = (wstate - AC.scratch_base(blob, segs)
                        + WORLD_STATE_BYTES + AC.bss_tail_slack(segs))
            cave = wdead.put(lambda e, ad: build_world_boost_cave(
                e, ad, budget, state=wstate))
            wdead.placed[WORLD_SITE] = A.bl(WORLD_SITE, cave)
            if struct.unpack_from('<I', text, WORLD_TERRAIN_SITE)[0] == \
                    A.bl(WORLD_TERRAIN_SITE, WORLD_TERRAIN):
                tcave = wdead.put(lambda e, ad: build_world_terrain_cave(
                    e, ad, wstate))
                wdead.placed[WORLD_TERRAIN_SITE] = A.bl(WORLD_TERRAIN_SITE,
                                                        tcave)
                caves['world terrain'] = tcave
            dead.placed.update(wdead.placed)
            caves['world'] = cave
            todo.append((WORLD_SITE, WORLD_UPDATE, 'world', None))
        except ValueError:
            skipped.append((WORLD_SITE, WORLD_UPDATE, 'world', None))
    else:
        skipped.append((WORLD_SITE, WORLD_UPDATE, 'world', None))
    if menu_k is None:
        skipped.append((MENU_SITE, None, 'battle menu', None))
    else:
        cave = dead.put(lambda e, ad: build_menu_boost_cave(e, ad, menu_k))
        dead.placed[MENU_SITE] = A.bl(MENU_SITE, cave)
        caves['battle menu'] = cave
        todo.append((MENU_SITE, None, 'battle menu', None))
    for va, w in dead.placed.items():
        struct.pack_into('<I', text, va, w)
    out = AC.pack(blob, space.commit(), bss_grow)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'installed': [s[2] for s in todo], 'world_state': wstate,
            'skipped': [s[2] for s in skipped], 'caves': caves,
            'max_extra': BOOST_MAX_EXTRA,
            'budget_ms': budget * 1000.0 / CPS,
            'bytes': dead.at - lo}


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
