#!/usr/bin/env python3
r"""
ff7nx_minipace.py -- the minigame frame limiters, on the 60 FPS dial.

    python3 ff7nx_minipace.py <ff7_en | sdout>                 (report)
    python3 ff7nx_minipace.py <ff7_en | sdout> --apply [FPS]
    python3 ff7nx_minipace.py <ff7_en | sdout> --revert

ff7_en DATA ONLY: two .rdata doubles. No code, no cave, nothing in exefs/main.
Runs inside the 60 FPS pass, right after it writes ff7_en, and follows the
same limiter dial (SEVENTH_NX_LIMITER_FPS, 66 in the shipping settings) the
field, battle and world limiters use. SEVENTH_NX_MINIGAME_60=0 leaves the
minigames stock.

WHAT EACH MINIGAME'S "LIMITER" ACTUALLY IS
==========================================
FFNx names one per mode (ff7_data.h, `fps_limiter_*`). Read on ff7_en they
are three different kinds of thing:

A. A busy-wait limiter, frame_time = cps / DIVISOR, set once at mode init.
   Same design as the field limiter the 60 FPS set already retargets.

   mode        limiter    init        divisor (.rdata)      stock   here
   condor      0x5F4CA6   0x5F47D7    0x7B7820              60.0    dial
   submarine   0x78C9E1   0x77F58A    0x7B7F60 (- 10000.0)  30.0    dial
   chocobo     0x779CC6   0x76D68D    0x7B7F18              30.0    30 (!)

   Each divisor has exactly ONE reference in .text (the fdiv at "init"), and
   each translated init reads it through guest memory (ARM +0x8BC654,
   +0xFFC8D4, +0xFBC5B0 build the address), so the double in ff7_en is what
   the Switch divides by -- the same mechanism as 0x7B7840 / 0x7C0B00 /
   0x969958.

   * CONDOR was always a 60 Hz game (FFNx runs it at 60 even at its default
     limiter setting). Its divisor is 60 with no fudge, and like the stock
     field limiter it measures from the frame start, so the post-limiter tail
     rides on top of a full 1/60 s: every frame is "late", the catch-up path
     runs, and a heavy frame presents at 30. The dial (66) is the value that
     already fixes exactly that in fields and battles.

   * SUBMARINE STAYS AT A 30 GAME (BUILD 550 -- build 540 raised it to the
     dial and was wrong; BUILD 551 sets 33 so it actually PRESENTS 30, see
     SUB_ENV below -- still one logic step per drawn frame, never the dial).
     The main loop (x86 0x77DF72) calls the limiter TWICE per pass: before
     the logic step (0x77E00A) and after it (0x77ECD7), and it runs the
     logic while the limiter reports "late" (0xE73F18 == 0) and draws once it
     reports "on time". So one logic step + one draw per limiter period:
     logic rate == limiter rate. At divisor 60 the submarine, torpedoes and
     enemies all ran ~2x (measured on hardware: 53 FPS). FFNx's "60 FPS"
     submarine replaces the limiter with a toggle that waits 1/60 s at EACH
     of the two calls -- 2/60 s per pass, i.e. 30 logic steps and 30 draws a
     second (misc.cpp ff7_limit_fps, MODE_SUBMARINE). The loop cannot draw
     more than once per logic step, so 30 is the most it can show without
     changing game speed. The divisor is still anchored and checked here, and
     a main left at 60 by build 540-549 is put back to 30.

   * CHOCOBO RACE IS NOT CHANGED. Its race step (0x76DDD6) runs once per
     limiter period with fixed per-frame speeds, so a 60 Hz limiter would run
     the race -- and every rival -- at double speed. FFNx keeps it at 30 in
     every mode for that reason (misc.cpp: CHOCOBO is absent from both 60
     lists). Documented here so nobody "finishes the set" by adding it.

B. A time-based step with NO wait at all:

   coaster     0x5E8F9B   accum += elapsed_s * 60 (0x7B7800), catch-up ticks
   snowboard   0x723960   accum += elapsed_s * 60 (0x7B7D40), catch-up ticks
   highway     0x650E88   delta  = elapsed_s * 25 (0x7B7998), clamp 16

   There is nothing to raise: these already present every frame they can
   draw, and their game speed follows the clock. If one shows 30 it is
   because a frame (logic + draw) takes longer than 16.7 ms -- the draw is
   recompiled x86 on the CPU, and model polygon count is what it scales
   with. Nothing here touches them.
"""
from __future__ import annotations

import os
import struct
import sys

ENV = 'SEVENTH_NX_MINIGAME_60'
LIMITER_FPS_ENV = 'SEVENTH_NX_LIMITER_FPS'
TITLE_ID = '0100A5B00BDC6000'
EXE_REL = ('atmosphere', 'contents', TITLE_ID, 'romfs', 'ff7', 'resources',
           'ff7_1.02', 'ff7_en')

IMAGE_BASE = 0x400000

# (name, divisor VA, stock value, the one x86 instruction that reads it,
#  its bytes, the global it stores to, the fstp bytes that follow)
DIVISORS = (
    ('condor limiter divisor',    0x7B7820, 60.0,
     0x5F47D7, bytes.fromhex('DC3520787B00'),     # fdiv qword [0x7B7820]
     0x5F47DD, bytes.fromhex('DD1DE0EDCB00')),    # fstp qword [0xCBEDE0]
    ('submarine limiter divisor', 0x7B7F60, 30.0,
     0x77F58A, bytes.fromhex('DC35607F7B00'),     # fdiv qword [0x7B7F60]
     0x77F590, bytes.fromhex('DC25687F7B00')),    # fsub qword [0x7B7F68]
)
# BUILD 550. Only these are RAISED to the dial. The submarine's divisor is
# still checked and managed here, but it is held at stock 30 -- see
# SUBMARINE_STAYS_30 below.
RAISED = frozenset(('condor limiter divisor',))
# BUILD 551. The submarine is NOT raised to the dial (that doubled its game
# speed), but its stock 30 does not give 30 either: the limiter measures the
# period from the start of the LOGIC step (0x77E01F marks 0xE74348) and the
# draw + present run after the wait, so their ~3.7 ms rides on top of every
# 33.3 ms period -- measured 27 FPS on hardware, i.e. the whole game (sub,
# torpedoes, enemies) at 90% speed. Same mechanism the condor dial of 66
# (= 2 x 33) already absorbs. 33 puts period + tail back at ~1/30 s: game
# speed and frame rate back to the 30 the minigame was written for.
SUB_ENV = 'SEVENTH_NX_SUB_FPS'
SUB_DEFAULT = 33.0
SUB_NAME = 'submarine limiter divisor'


def sub_fps(env=None) -> float:
    raw = (os.environ if env is None else env).get(SUB_ENV, '').strip()
    try:
        v = float(raw) if raw else SUB_DEFAULT
    except ValueError:
        v = SUB_DEFAULT
    return v if 30.0 <= v <= 36.0 else SUB_DEFAULT
# Not patched -- see the docstring. Listed so the report shows it.
CHOCOBO = ('chocobo race limiter divisor', 0x7B7F18, 30.0)
SUB_FUDGE = (0x7B7F68, 10000.0)


def enabled(env=None) -> bool:
    v = (os.environ if env is None else env).get(ENV, '').strip().lower()
    return v not in ('0', 'off', 'false', 'no', 'stock')


def dial(env=None) -> float:
    """The limiter dial the field/battle/world divisors use (60 if unset)."""
    raw = (os.environ if env is None else env).get(LIMITER_FPS_ENV, '').strip()
    try:
        v = float(raw) if raw else 0.0
    except ValueError:
        v = 0.0
    return v if v >= 30.0 else 60.0


class _Pe:
    def __init__(self, data):
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        if data[pe:pe + 4] != b'PE\0\0':
            raise ValueError('not a PE image')
        nsec = struct.unpack_from('<H', data, pe + 6)[0]
        optsz = struct.unpack_from('<H', data, pe + 20)[0]
        off = pe + 24 + optsz
        self.secs = []
        for i in range(nsec):
            vsize, va, rsize, raw = struct.unpack_from('<IIII', data,
                                                       off + 40 * i + 8)
            self.secs.append((va + IMAGE_BASE, raw, rsize))

    def off(self, va):
        for base, raw, rsize in self.secs:
            if base <= va < base + rsize:
                return raw + va - base
        raise ValueError('VA 0x%X is not in the file' % va)


def _exe_path(target):
    if os.path.isdir(target):
        return os.path.join(target, *EXE_REL)
    return target


def _d(data, pe, va):
    return struct.unpack_from('<d', data, pe.off(va))[0]


def check(data):
    """Every anchor this patch relies on. Returns a list of problems."""
    pe = _Pe(data)
    bad = []
    for name, va, _stock, ins, ins_b, nxt, nxt_b in DIVISORS:
        for where, want in ((ins, ins_b), (nxt, nxt_b)):
            got = data[pe.off(where):pe.off(where) + len(want)]
            if got != want:
                bad.append('%s: x86 0x%X is %s, expected %s'
                           % (name, where, got.hex(), want.hex()))
    if _d(data, pe, SUB_FUDGE[0]) != SUB_FUDGE[1]:
        bad.append('submarine fudge 0x%X is not %g' % SUB_FUDGE)
    return bad


def state(data):
    pe = _Pe(data)
    return {name: _d(data, pe, va) for name, va, *_ in DIVISORS}


def patch(data, fps=None, revert=False):
    """Return (new bytes, [(name, old, new)]). Raises on a foreign value."""
    fps = dial() if fps is None else float(fps)
    if not 30.0 <= fps <= 240.0:
        raise ValueError('minigame limiter fps %g is outside 30..240' % fps)
    bad = check(data)
    if bad:
        raise ValueError('; '.join(bad))
    pe = _Pe(data)
    out = bytearray(data)
    changes = []
    for name, va, stock, *_ in DIVISORS:
        cur = _d(data, pe, va)
        if revert:
            want = stock
        elif name in RAISED:
            want = fps
        elif name == SUB_NAME:
            want = sub_fps()
        else:
            want = stock
        # A fresh ff7_en is stock. Anything that is neither stock nor our
        # own 30..240 dial value was written by something else: refuse.
        if cur != stock and not (30.0 <= cur <= 240.0):
            raise ValueError('%s at 0x%X holds %r, not stock %g'
                             % (name, va, cur, stock))
        if cur != want:
            struct.pack_into('<d', out, pe.off(va), want)
            changes.append((name, cur, want))
    return bytes(out), changes


def apply(target, fps=None, revert=False, log=print):
    """Patch ff7_en in place. Returns 0 on success, 1 on refusal."""
    path = _exe_path(target)
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
        new, changes = patch(data, fps, revert)
    except (OSError, ValueError) as exc:
        log('  ! minigame limiters: %s -- NOT CHANGED' % exc)
        return 1
    if new != data:
        tmp = path + '.minipace-tmp'
        with open(tmp, 'wb') as fh:
            fh.write(new)
        os.replace(tmp, path)
    for name, old, now in changes:
        log('  %s %g -> %g' % (name, old, now))
    if not changes:
        log('  minigame limiters already at %s'
            % ', '.join('%g' % v for v in state(new).values()))
    return 0


def main(argv):
    if not argv:
        print(__doc__.split('\n\n')[1])
        return 2
    target = argv[0]
    path = _exe_path(target)
    with open(path, 'rb') as fh:
        data = fh.read()
    if '--apply' in argv:
        i = argv.index('--apply')
        fps = float(argv[i + 1]) if len(argv) > i + 1 else None
        return apply(target, fps)
    if '--revert' in argv:
        return apply(target, revert=True)
    bad = check(data)
    for b in bad:
        print('! ' + b)
    pe = _Pe(data)
    for name, v in state(data).items():
        print('%-28s %g' % (name, v))
    print('%-28s %g (never changed)' % (CHOCOBO[0], _d(data, pe, CHOCOBO[1])))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
