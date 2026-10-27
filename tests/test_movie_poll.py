#!/usr/bin/env python3
"""
test_movie_poll.py -- the MVIEF poll-counter divider.

    python3 test_movie_poll.py

WHAT IT IS FOR
==============
Field scripts time the models they composite over a playing movie with MVIEF,
and MVIEF does NOT return the movie's frame. It returns a count of how many
times the script has polled since playback started -- one per field tick:

    MOVIE   0061A339  mov  word [0xCC0B70], 0
    MVIEF   0061A446  mov  cx, word [0xCC0B70]    ; handed to the script
            0061A45A  mov  dx, word [0xCC0B70]
            0061A461  add  dx, 1                  ; then incremented
            0061A465  mov  word [0xCC0B70], dx

At 60 FPS the field ticks twice as often as the 30 the scripts were written
for, so the count hits every scripted threshold in half the wall-clock time
and the models appear halfway through the cutscene. The cave halves the value
handed over, leaving the counter itself alone.

WHAT IS CHECKED
===============
The actual encoded words run in arm64emu.py against a model of the real
instruction sequence, including the guest memory the displaced load reads and
the store that follows it. The counter's own increment path is exercised
alongside, because the one way to get this badly wrong is to disturb it.

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
import struct
import sys

import a64 as A
import arm64emu
import ff7nx_dispatch as D

CAVE = 0x1152EE0
HOST = 0x700000000
COUNTER = 0x700CC0B70      # host address of the guest counter 0xCC0B70
CTX = 0x900000000          # guest register context; CX sits at +4
STACK = 0x800000000

FAIL = []


def check(cond, msg):
    print(('  ok    ' if cond else '  FAIL  ') + msg)
    if not cond:
        FAIL.append(msg)


def run(words, counter_value):
    """
    Execute the cave with the guest counter holding `counter_value`, then run
    the two instructions that follow the hook, exactly as the module has them:

        00979874  mov  x22, x21
        00979878  strh w8, [x22, #4]!

    Returns (value the script receives, exit address, cpu, mem).
    """
    mem = arm64emu.Mem()
    mem.setu(COUNTER, counter_value, 2)
    mem.setu(COUNTER + 2, 0x5A5A, 2)        # neighbour -- must not move
    mem.setu(CTX + 4, 0xBEEF, 2)            # guest CX, overwritten below

    cpu = arm64emu.Cpu(mem, host_base=HOST)
    cpu.sp = STACK
    for r in range(31):
        cpu.x[r] = arm64emu.GARBAGE
    cpu.x[0] = COUNTER                      # translator already ran
    cpu.x[21] = CTX
    exit_pc = cpu.run(CAVE, words)

    # the two real instructions after the hook
    cpu.x[22] = cpu.x[21]
    cpu.x[22] += 4
    mem.setu(cpu.x[22], cpu.x[D.MVIEF_POLL_VALUE_REG] & 0xFFFF, 2)
    return mem.u(CTX + 4, 2), exit_pc, cpu, mem


words = D.build_movie_poll_cave(CAVE)

print('the cave\n')
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
decoded = list(md.disasm(struct.pack('<%dI' % len(words), *words), CAVE))
check(len(decoded) == len(words) == 3,
      '3 words, all decodable -- no call, no stack, no scratch register')
check(words[0] == D.MVIEF_POLL_DISPLACED,
      'the displaced load is replayed verbatim (%08X)' % words[0])
check(not any((w & 0xFC000000) == 0x94000000 for w in words),
      'no bl: nothing can clobber a caller-saved register')

print('\nwhat the script receives\n')
bad = 0
for v in [0, 1, 2, 3, 4, 5, 30, 31, 60, 61, 100, 599, 600, 601,
          0x7FFE, 0x7FFF, 0x8000, 0xFFFE, 0xFFFF]:
    got, exit_pc, cpu, mem = run(words, v)
    if got != v >> 1:
        check(False, 'counter %d -> script saw %d, wanted %d' % (v, got, v >> 1))
        bad += 1
check(bad == 0,
      'every counter value halved (0->0, 60->30, 601->300, 0xFFFF->0x7FFF)')

print('\nthe scene that was breaking\n')
got, _e, _c, _m = run(words, 600)
check(got == 300,
      'after 600 field ticks at 60 FPS the script reads 300 -- the same '
      'number it would have reached in 300 ticks at vanilla 30')
got, _e, _c, _m = run(words, 300)
check(got == 150,
      'and at the halfway point it reads 150, so a threshold of 300 does '
      'NOT fire yet')

print('\nnothing else disturbed\n')
got, exit_pc, cpu, mem = run(words, 42)
check(exit_pc == D.MVIEF_POLL_HOOK + 4,
      'control returns to hook+4 (0x%X)' % (D.MVIEF_POLL_HOOK + 4))
check(cpu.sp == STACK, 'stack pointer untouched')
check(mem.u(COUNTER, 2) == 42,
      'THE COUNTER ITSELF IS UNCHANGED (%d) -- the increment that follows '
      're-reads this global, so halving the handed-over copy cannot make the '
      'count drift' % mem.u(COUNTER, 2))
check(mem.u(COUNTER + 2, 2) == 0x5A5A,
      'and the neighbouring halfword is untouched')
check(cpu.x[0] == COUNTER, 'x0, the translated pointer, is preserved')
check(cpu.x[21] == CTX, 'x21, the guest context, is preserved')

print('\nthe counter still advances at the real rate\n')
# Simulate 8 polls: the game increments the global by 1 each time, and the
# script should see 0,0,1,1,2,2,3,3.
seen = []
counter = 0
for _ in range(8):
    got, _e, _c, _m = run(words, counter)
    seen.append(got)
    counter += 1
check(seen == [0, 0, 1, 1, 2, 2, 3, 3],
      'eight polls report %s -- one script step per two field ticks' % seen)

print('\nmutations (each must be caught)\n')


def no_divide(w):
    return [x for x in w if x != A.lsr(8, 8, 1)]


def divide_by_four(w):
    return [A.lsr(8, 8, 2) if x == A.lsr(8, 8, 1) else x for x in w]


def wrong_register(w):
    return [A.lsr(9, 9, 1) if x == A.lsr(8, 8, 1) else x for x in w]


def no_replay(w):
    return [x for x in w if x != D.MVIEF_POLL_DISPLACED]


caught = 0
muts = [('divide removed', no_divide, 60, 30),
        ('divided by 4', divide_by_four, 60, 30),
        ('divides the wrong register', wrong_register, 60, 30),
        ('displaced load not replayed', no_replay, 60, 30)]
for name, fn, feed, want in muts:
    broken = fn(list(words))
    try:
        got, _e, _c, _m = run(broken, feed)
        differs = (got != want)
    except Exception:
        differs = True
    print(('  ok    caught: ' if differs else '  FAIL  NOT caught: ') + name)
    if differs:
        caught += 1
    else:
        FAIL.append(name)
print('\n  %d/%d mutations caught' % (caught, len(muts)))

print('\nit must never turn itself on\n')
import ff7nx_60fps as F
check(F.MOVIE_POLL_GROUP not in F.RECOMMENDED_ENABLE,
      'not in the shipping preset -- a build without it is byte-identical')

print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
sys.exit(1 if FAIL else 0)
