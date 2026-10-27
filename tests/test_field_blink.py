#!/usr/bin/env python3
"""
test_field_blink.py -- run the eye-blink state machine, frame by frame.

    python3 test_field_blink.py

WHAT IS BEING COMPARED
======================
There is no FFNx reference for this one -- FFNx never scaled the blink -- so
the reference is VANILLA AT 30 FPS, simulated from the stock arithmetic:

    counter = blink_frames[model]                   ; signed 16-bit
    if (counter == 0) {
        mode = 2;                                   ; eyes shut
        counter = (jitter & 0x1F) + 0x40;           ; 64..95
    } else {
        mode = 1;                                   ; eyes open
        counter--;
    }

One shut frame per cycle, cycle length 65..96 frames, at 30 FPS
= 2.17..3.20 seconds with the eyes shut for 33 ms.

The 60 FPS build must land in the same WALL-CLOCK place: cycle 130..193
frames and 2 shut frames, at 60 FPS = 2.17..3.22 seconds with the eyes shut
for 33 ms. Both the interval constants (`field-blink`) and the two caves
(`field-blink-hold`) are needed for that, and the test drives the ACTUAL
ENCODED CAVE WORDS through arm64emu.py rather than reimplementing them.

The state machine around the caves is simulated from the stock disassembly:
the test cave decides which arm runs, the shut arm writes the counter through
the hold cave, and the open arm decrements.

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
import sys

import a64 as A
import arm64emu as E
import ff7nx_dispatch as D

TEST_CAVE = 0x1152000
HOLD_CAVE = 0x1152100
COUNTER = 0x700CC167A            # host address of blink_frames[model]
FAIL = []


def fail(msg):
    FAIL.append(msg)
    print('  FAIL  %s' % msg)


def reference_30fps(jitter):
    """(shut_frames, cycle_frames) for stock at 30 FPS."""
    return 1, ((jitter & 0x1F) + 0x40) + 1


def run(sites, jitter, frames, patched):
    """
    Simulate `frames` frames and return the list of per-frame modes
    (1 = eyes open, 2 = eyes shut).

    `patched` selects the build: False replays stock arithmetic, True runs the
    two caves and the doubled interval constants.
    """
    test, hold = sites['test'], sites['hold']
    tw = D.build_field_blink_test_cave(TEST_CAVE, test)
    hw = D.build_field_blink_hold_cave(HOLD_CAVE, hold)
    reload_val = (0x80 | ((jitter & 0x1F) << 1)) if patched \
        else ((jitter & 0x1F) + 0x40)

    mem = E.Mem()
    mem.setu(COUNTER, 0, 2)
    mem.setu(COUNTER + 2, 0x5A5A, 2)          # next model's slot -- must not move
    modes = []
    for _ in range(frames):
        raw = mem.u(COUNTER, 2)
        counter = raw - 0x10000 if raw & 0x8000 else raw     # ldrsh
        if patched:
            cpu = E.Cpu(mem, host_base=0x700000000)
            cpu.sp = 0x800000000
            for r in range(31):
                cpu.x[r] = E.GARBAGE
            cpu.x[test['val_reg']] = counter & 0xFFFFFFFF
            out = cpu.run(TEST_CAVE, tw)
            if out == test['blink_arm']:
                shut = True
            elif out == test['hook'] + 4:
                shut = False
            else:
                fail('test cave left to 0x%X, expected the shut arm 0x%X or '
                     'hook+4 0x%X' % (out, test['blink_arm'], test['hook'] + 4))
                return modes
        else:
            shut = (counter == 0)

        if shut:
            modes.append(2)
            if patched:
                cpu = E.Cpu(mem, host_base=0x700000000)
                cpu.sp = 0x800000000
                for r in range(31):
                    cpu.x[r] = E.GARBAGE
                cpu.x[0] = COUNTER
                cpu.x[hold['val_reg']] = reload_val
                out = cpu.run(HOLD_CAVE, hw)
                if out != hold['hook'] + 4:
                    fail('hold cave left to 0x%X, wanted 0x%X'
                         % (out, hold['hook'] + 4))
                    return modes
                if cpu.x[0] != COUNTER:
                    fail('hold cave clobbered x0')
                    return modes
                if cpu.x[hold['val_reg']] & 0xFFFF != reload_val:
                    fail('hold cave clobbered the reload value in w%d'
                         % hold['val_reg'])
                    return modes
            else:
                mem.setu(COUNTER, reload_val, 2)
        else:
            modes.append(1)
            mem.setu(COUNTER, (counter - 1) & 0xFFFF, 2)

        if mem.u(COUNTER + 2, 2) != 0x5A5A:
            fail('the next model\'s counter was overwritten -- wrong store width')
            return modes
    return modes


def runs_of_two(modes):
    """[(start, length)] for every maximal run of mode 2."""
    out, i = [], 0
    while i < len(modes):
        if modes[i] == 2:
            j = i
            while j < len(modes) and modes[j] == 2:
                j += 1
            out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def main():
    try:
        from ff7nx_dispatch_sites import FIELD_BLINK_SITES as S
    except ImportError:
        sys.exit('ff7nx_dispatch_sites.py has no FIELD_BLINK_SITES')
    if set(S) != {'test', 'hold'}:
        sys.exit('expected exactly the test and hold sites, got %s' % sorted(S))

    print('eye blink, every jitter value 0..255\n')
    print('  %-8s %-26s %-26s %s'
          % ('jitter', 'stock @30 (shut, cycle)', 'built @60 (shut, cycle)',
             'wall clock'))
    checked = 0
    for jitter in range(256):
        want_shut, want_cycle = reference_30fps(jitter)
        modes = run(S, jitter, 800, patched=True)
        if FAIL:
            return 1
        blinks = runs_of_two(modes)
        # ignore the first and last, which the window may clip
        mid = blinks[1:-1]
        if len(mid) < 2:
            fail('jitter 0x%02X: only %d complete blink(s) in 800 frames'
                 % (jitter, len(mid)))
            return 1
        lens = {n for _s, n in mid}
        cycles = {mid[k + 1][0] - mid[k][0] for k in range(len(mid) - 1)}
        if lens != {2}:
            fail('jitter 0x%02X: shut for %s frame(s), wanted exactly 2'
                 % (jitter, sorted(lens)))
            return 1
        if len(cycles) != 1:
            fail('jitter 0x%02X: cycle length is not constant: %s'
                 % (jitter, sorted(cycles)))
            return 1
        cycle = cycles.pop()
        # wall clock must match vanilla to within one 60 FPS frame
        stock_ms = want_cycle / 30.0
        built_ms = cycle / 60.0
        if abs(stock_ms - built_ms) > 1e-9:
            fail('jitter 0x%02X: cycle %.4f s vs vanilla %.4f s -- must be exact'
                 % (jitter, built_ms, stock_ms))
            return 1
        if jitter in (0x00, 0x01, 0x02, 0x1F, 0x20, 0xFF):
            print('  0x%02X     %-26s %-26s %.4f s == %.4f s'
                  % (jitter, '(%d, %d)' % (want_shut, want_cycle),
                     '(2, %d)' % cycle, built_ms, stock_ms))
        checked += 1

    print('\n  shut duration: 1 frame @30 = 33.3 ms   ->   2 frames @60 = 33.3 ms')

    print('\nmutations (each must be caught):')
    cases = []

    def no_widen(w):
        return [A.bcond(TEST_CAVE + 4, TEST_CAVE + 12, A.EQ) if i == 1 else x
                for i, x in enumerate(w)]

    def no_hold(w):
        return [x for x in w if x != A.movz(16, 0xFFFF)]

    caught = 0
    orig_test = D.build_field_blink_test_cave
    orig_hold = D.build_field_blink_hold_cave
    for name, which, fn in (('test never widened (b.eq, not b.le)', 'test', no_widen),
                            ('hold sentinel never written', 'hold', no_hold)):
        before = len(FAIL)
        if which == 'test':
            D.build_field_blink_test_cave = (
                lambda c, s, _=None, f=fn: f(orig_test(c, s)))
        else:
            D.build_field_blink_hold_cave = (
                lambda c, s, _=None, f=fn: f(orig_hold(c, s)))
        try:
            modes = run(S, 0x10, 600, patched=True)
            lens = {n for _s, n in runs_of_two(modes)[1:-1]}
            if lens and lens != {2}:
                raise AssertionError('shut for %s' % sorted(lens))
            if not lens:
                raise AssertionError('never blinked')
            if len(FAIL) == before:
                print('  FAIL  NOT caught: %s' % name)
            else:
                del FAIL[before:]
                print('  ok    caught: %s' % name)
                caught += 1
        except Exception as exc:
            del FAIL[before:]
            print('  ok    caught: %-38s (%s)' % (name, exc))
            caught += 1
        D.build_field_blink_test_cave = orig_test
        D.build_field_blink_hold_cave = orig_hold
        cases.append(name)

    print('\n%d jitter value(s) simulated over 800 frames each, '
          '%d/%d mutations caught, %d failure(s)'
          % (checked, caught, len(cases), len(FAIL)))
    if FAIL or caught != len(cases):
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
