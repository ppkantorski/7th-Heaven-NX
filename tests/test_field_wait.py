#!/usr/bin/env python3
"""
test_field_wait.py -- differential test of the field script WAIT cave.

    python3 test_field_wait.py [--mult 2]

WHAT IS BEING COMPARED
======================
FFNx's replacement handler, src/ff7/field/opcode.cpp:

    else
    {
        wait_frames_ptr[id] = get_field_parameter<WORD>(0);

        if (is_fps_running_more_than_original())
            wait_frames_ptr[id] *= get_frame_multiplier();

        if (!wait_frames_ptr[id])
            field_curr_script_position[id] += 3;
        result = 1;
    }

`get_field_parameter<WORD>` reads the operand UNSIGNED, and the stock handler
assembles the same two script bytes into AX unsigned as well
(`movzx dx, [..+1]` then `or ax, bx` with bx = byte[..+2] << 8), so unlike the
camera-wait case there is no signed/unsigned disagreement to reproduce. What
there IS is a width question: the destination is a short, FFNx multiplies in a
WORD and lets it wrap, and a wrapped result is SHORTER than stock -- a scripted
pause that gets briefer when the patch is supposed to lengthen it. The cave
refuses to scale above 0x7FFF instead, so its worst case is "identical to
vanilla".

This test enumerates every one of the 65,536 operand values, executes the
ACTUAL ENCODED WORDS in arm64emu.py, and checks what landed in memory.

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
import argparse
import struct
import sys

import a64 as A
import arm64emu as E
import ff7nx_dispatch as D

CAVE = 0x1152660
DEST = 0x700CC0900                 # host address of wait_frames[entity]
FAIL = []

# Above this the doubled value would not fit the short. FFNx wraps; we decline.
GUARD = 0x7FFF


def fail(msg):
    FAIL.append(msg)
    print('  FAIL  %s' % msg)


def reference(operand, mult):
    """The 16 bits that must end up in wait_frames[entity]."""
    if operand > GUARD:
        return operand                      # left stock rather than wrapped
    return (operand * mult) & 0xFFFF


def run_case(words, val_reg, operand, cave, hook):
    mem = E.Mem()
    mem.setu(DEST, 0xA5A5, 2)               # poison, so a missing store shows
    # wait_frames is an array of shorts, so the NEXT entity's slot sits at
    # DEST+2. Poisoning it is what makes a 32-bit store visible: its low half
    # would be correct and the damage would be to the neighbouring entity's
    # wait counter, which is about as hard a bug to find in-game as exists.
    mem.setu(DEST + 2, 0x5A5A, 2)
    cpu = E.Cpu(mem, host_base=0x700000000)
    cpu.sp = 0x800000000
    for r in range(31):
        cpu.x[r] = E.GARBAGE
    cpu.x[val_reg] = operand                # `ldrh` zero-extends into the reg
    cpu.x[0] = DEST
    out = cpu.run(cave, words)
    return out, mem.u(DEST, 2), cpu, mem.u(DEST + 2, 2)


def one_site(tag, site, mult, mutate=None, quiet=False, step=1):
    shift = {2: 1, 4: 2, 8: 3}[mult]
    cave, hook = CAVE, site['hook']
    words = D.build_field_wait_cave(cave, site, shift)
    if mutate:
        words = mutate(list(words))
    v = site['val_reg']
    checked = 0
    # every value at `step`, plus every boundary regardless of step
    interesting = {0, 1, 2, 3, 0x7FFE, GUARD, GUARD + 1, 0x8000, 0x8001,
                   0xFFFE, 0xFFFF}
    for operand in sorted(set(range(0, 0x10000, step)) | interesting):
        out, got, cpu, neighbour = run_case(words, v, operand, cave, hook)
        if neighbour != 0x5A5A:
            fail('%s: operand 0x%04X wrote past wait_frames[id] into the next '
                 'entity\'s slot (0x%04X) -- the store is the wrong width'
                 % (tag, operand, neighbour))
            return
        if out != hook + 4:
            fail('%s: operand 0x%04X left the cave to 0x%X, wanted 0x%X'
                 % (tag, operand, out, hook + 4))
            return
        want = reference(operand, mult)
        if got != want:
            fail('%s: operand 0x%04X -> wait_frames 0x%04X, wanted 0x%04X'
                 % (tag, operand, got, want))
            return
        # WAIT 0 must stay 0: the handler tests it immediately afterwards and
        # advances the script pointer only when it is zero. Scaling that to a
        # nonzero value would hang the entity's script forever.
        if operand == 0 and got != 0:
            fail('%s: WAIT 0 became %d -- the fall-through test breaks'
                 % (tag, got))
            return
        if cpu.x[v] & 0xFFFF != operand:
            fail('%s: operand 0x%04X clobbered w%d (0x%X)'
                 % (tag, operand, v, cpu.x[v]))
            return
        if cpu.x[0] != DEST:
            fail('%s: operand 0x%04X clobbered x0, the destination pointer'
                 % (tag, operand))
            return
        if cpu.sp != 0x800000000:
            fail('%s: operand 0x%04X left sp at 0x%X' % (tag, operand, cpu.sp))
            return
        checked += 1

    if site['displaced'] not in words:
        fail('%s: the cave never replays the displaced instruction %08X'
             % (tag, site['displaced']))
    if not quiet:
        print('  ok    %-14s %d operand(s), %d words, guard path replays %08X'
              % (tag, checked, len(words), site['displaced']))
    return checked


def mutations(tag, site, mult):
    """Each must be caught. A test that cannot fail is not a test."""
    shift = {2: 1, 4: 2, 8: 3}[mult]
    v = site['val_reg']

    def no_scale(w):
        return [x for x in w if x != A.lsl(16, 16, shift)]

    def no_guard(w):
        # drop the b.hi that protects values which would wrap the short
        return [x for x in w if (x & 0xFF00001F) != (0x54000000 | A.HI)]

    def guard_wrong_value(w):
        # guard at 0xFFFF instead of 0x7FFF: 0x8000..0xFFFE are scaled and
        # wrap, coming back SHORTER than vanilla
        return [A.movz(17, 0xFFFF) if x == A.movz(17, 0x7FFF) else x
                for x in w]

    def scale_by_four(w):
        return [A.lsl(16, 16, 2) if x == A.lsl(16, 16, shift) else x for x in w]

    def clobber_operand(w):
        return [A.and_mask(v, v, 16) if x == A.and_mask(16, v, 16) else x
                for x in w]

    def store_wrong_width(w):
        return [A.str_(16, 0, 0) if x == A.strh(16, 0, 0) else x for x in w]

    def no_mask(w):
        # drop the 16-bit mask: the tbnz then tests bit 15 of a value that
        # may carry rubbish above bit 15
        return [x for x in w if x != A.and_mask(16, v, 16)]

    cases = [('wait never scaled', no_scale),
             ('overflow guard removed (value wraps shorter)', no_guard),
             ('guard set to 0xFFFF (value wraps shorter)', guard_wrong_value),
             ('scaled by 4 instead of %d' % mult, scale_by_four),
             ('operand register clobbered', clobber_operand),
             ('stored 32 bits over a 16-bit field', store_wrong_width),
             ('16-bit mask removed', no_mask)]
    caught = 0
    for name, fn in cases:
        before = len(FAIL)
        try:
            one_site(tag, site, mult, mutate=fn, quiet=True, step=97)
        except Exception as exc:
            del FAIL[before:]
            print('  ok    caught: %-46s (%s)' % (name, type(exc).__name__))
            caught += 1
            continue
        if len(FAIL) == before:
            print('  FAIL  NOT caught: %s' % name)
        else:
            del FAIL[before:]
            print('  ok    caught: %s' % name)
            caught += 1
    return caught, len(cases)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mult', type=int, default=2,
                    help='common_frame_multiplier (2 at 60 FPS)')
    ap.add_argument('--step', type=int, default=1,
                    help='operand stride; 1 = all 65536 values')
    a = ap.parse_args()
    try:
        from ff7nx_dispatch_sites import FIELD_WAIT_SITES
    except ImportError:
        sys.exit('ff7nx_dispatch_sites.py has no FIELD_WAIT_SITES')
    if not FIELD_WAIT_SITES:
        sys.exit('no field-wait sites')

    print('field script opcode 0x24 (WAIT), common_frame_multiplier = %d\n'
          % a.mult)
    total = 0
    for tag in sorted(FIELD_WAIT_SITES):
        total += one_site(tag, FIELD_WAIT_SITES[tag], a.mult, step=a.step) or 0

    print('\nthe values that matter:')
    for op in (0, 1, 2, 15, 30, 60, 0x7FFE, 0x7FFF, 0x8000, 0xFFFF):
        r = reference(op, a.mult)
        note = ''
        if op == 0:
            note = '   <- WAIT 0 falls through, must stay 0'
        elif op > GUARD:
            note = '   <- above the guard, left stock'
        print('    operand %6d  stock waits %6d   this build waits %6d%s'
              % (op, op, r, note))

    print('\nmutations (each must be caught):')
    tag = sorted(FIELD_WAIT_SITES)[0]
    caught, n = mutations(tag, FIELD_WAIT_SITES[tag], a.mult)

    print('\n%d cave execution(s), %d/%d mutations caught, %d failure(s)'
          % (total, caught, n, len(FAIL)))
    if FAIL or caught != n:
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
