#!/usr/bin/env python3
"""
test_camera_wait.py -- differential test of the opcode-0xF5 camera wait cave.

    python3 test_camera_wait.py [--freq 4]

WHAT IS BEING COMPARED
======================
FFNx's camera script simulation, src/ff7/battle/camera.cpp:

    case 0xF5:
        if (scriptPtr[currentPosition] == 0xFF) {
            framesToWait = -1;
            currentPosition++;
        } else {
            executedOpCodeF5 = true;
            framesToWait = scriptPtr[currentPosition++] * battle_frame_multiplier;
        }

    ...
    if (executedOpCodeF5)
        cameraPosition[variationIndex].frames_to_wait = framesToWait;

`scriptPtr` is a `byte*`, so FFNx reads the operand UNSIGNED. The stock
interpreter reads the same byte with `movsx ax, byte ptr [edx+eax]` -- SIGNED --
and it is that sign-extended value which reaches the cave in `wV`. So for
operands 0x80..0xFE the two implementations genuinely disagree, and the cave has
to reproduce FFNx's reading, not the register's face value. That is the whole
reason this test enumerates all 256 operands rather than a handful.

When FFNx does NOT set executedOpCodeF5 -- operand 0xFF -- it never writes, so
the stock value stands. The cave's KEEP path replays the displaced instruction
verbatim to get the same result.

Every case is checked by executing the ACTUAL ENCODED WORDS in arm64emu.py, and
by checking what landed in memory rather than what ended up in a register.

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

CAVE = 0x1152E34
HOOK = 0x7E7814
DEST = 0x700BE10FA                 # host address of frames_to_wait
FAIL = []


def fail(msg):
    FAIL.append(msg)
    print('  FAIL  %s' % msg)


def reference(operand, mult):
    """
    The 16 bits FFNx leaves in `frames_to_wait` for a given script operand byte.
    """
    signed16 = (operand - 0x100 if operand & 0x80 else operand) & 0xFFFF
    if operand == 0xFF:
        return signed16                     # -1: FFNx does not overwrite
    return (operand * mult) & 0xFFFF


def guest_ax(operand):
    """What `movsx ax, byte ptr [...]` leaves in guest AX, zero-extended by the
    recompiler's `ldrh` into a 32-bit register."""
    return (operand - 0x100 if operand & 0x80 else operand) & 0xFFFF


def run_case(words, val_reg, operand, cave=CAVE, hook=HOOK):
    mem = E.Mem()
    mem.setu(DEST, 0xA5A5, 2)               # poison, so a missing store shows
    # `frames_to_wait` is a SHORT at +0xA of a 0xE-byte struct: field_C and
    # field_D live in the next two bytes. Poisoning them is what makes a store
    # of the wrong width visible -- otherwise a 32-bit store looks identical,
    # because its low half is correct and the damage is to the neighbours.
    mem.setu(DEST + 2, 0x5A5A, 2)
    cpu = E.Cpu(mem, host_base=0x700000000)
    cpu.sp = 0x800000000
    for r in range(31):
        cpu.x[r] = E.GARBAGE
    cpu.x[val_reg] = guest_ax(operand)
    cpu.x[0] = DEST
    out = cpu.run(cave, words)
    return out, mem.u(DEST, 2), cpu, mem.u(DEST + 2, 2)


def one_site(tag, site, mult, mutate=None, quiet=False, shipped=None):
    shift = {2: 1, 4: 2, 8: 3}[mult]
    if shipped:
        words, cave, hook = shipped['words'], shipped['cave'], shipped['hook']
        site = dict(site)
        site['displaced'] = words[-2]
    else:
        cave, hook = CAVE, site['hook']
        words = D.build_camera_wait_cave(cave, site, shift)
    if mutate:
        words = mutate(list(words))
    v = site['val_reg']
    checked = 0
    for operand in range(256):
        out, got, cpu, neighbour = run_case(words, v, operand, cave, hook)
        if neighbour != 0x5A5A:
            fail('%s: operand 0x%02X wrote past frames_to_wait into field_C / '
                 'field_D (0x%04X) -- the store is the wrong width'
                 % (tag, operand, neighbour))
            return
        if out != hook + 4:
            fail('%s: operand 0x%02X left the cave to 0x%X, wanted 0x%X'
                 % (tag, operand, out, hook + 4))
            return
        want = reference(operand, mult)
        if got != want:
            fail('%s: operand 0x%02X -> frames_to_wait 0x%04X, FFNx says 0x%04X'
                 % (tag, operand, got, want))
            return
        # the operand register must survive: the interpreter may still want it
        if cpu.x[v] & 0xFFFF != guest_ax(operand):
            fail('%s: operand 0x%02X clobbered w%d (0x%X)'
                 % (tag, operand, v, cpu.x[v]))
            return
        if cpu.x[0] != DEST:
            fail('%s: operand 0x%02X clobbered x0, the destination pointer'
                 % (tag, operand))
            return
        if cpu.sp != 0x800000000:
            fail('%s: operand 0x%02X left sp at 0x%X' % (tag, operand, cpu.sp))
            return
        checked += 1

    # The sentinel path must be bit-for-bit the stock instruction, not a
    # re-derivation of it: an operand of 0xFF has to behave exactly as it does
    # on an unpatched build.
    if site['displaced'] not in words:
        fail('%s: the cave never replays the displaced instruction %08X'
             % (tag, site['displaced']))
    if not quiet:
        print('  ok    %-18s %d operand(s), %d words, sentinel replays %08X'
              % (tag, checked, len(words), site['displaced']))
    return checked


def mutations(tag, site, mult):
    """Each must be caught. A test that cannot fail is not a test."""
    shift = {2: 1, 4: 2, 8: 3}[mult]
    v = site['val_reg']

    def no_scale(w):
        return [x for x in w if x != A.lsl(16, 16, shift)]

    def signed_operand(w):
        # use the sign-extended 16-bit value instead of the raw byte: wrong for
        # every operand >= 0x80
        return [A.and_mask(16, v, 16) if x == A.and_mask(16, v, 8) else x
                for x in w]

    def sentinel_scaled(w):
        # drop the b.eq that protects operand 0xFF
        return [x for x in w if (x & 0xFF00001F) != (0x54000000 | A.EQ)]

    def wrong_sentinel(w):
        # compare against 0xFF instead of 0xFFFF: the operand arrives
        # zero-extended to 16 bits, so this never matches and 0xFF is scaled
        return [A.movz(17, 0xFF) if x == A.movz(17, 0xFFFF) else x for x in w]

    def scale_by_two(w):
        return [A.lsl(16, 16, 1) if x == A.lsl(16, 16, shift) else x for x in w]

    def clobber_operand(w):
        # scale in place, which is what a careless version would do
        return [A.and_mask(v, v, 8) if x == A.and_mask(16, v, 8) else x
                for x in w]

    def store_wrong_width(w):
        return [A.str_(16, 0, 0) if x == A.strh(16, 0, 0) else x for x in w]

    cases = [('wait never scaled', no_scale),
             ('operand read signed, not unsigned', signed_operand),
             ('0xFF sentinel scaled too', sentinel_scaled),
             ('sentinel compared against 0xFF, not 0xFFFF', wrong_sentinel),
             ('scaled by 2 instead of %d' % mult, scale_by_two),
             ('operand register clobbered', clobber_operand),
             ('stored 32 bits over a 16-bit field', store_wrong_width)]
    caught = 0
    for name, fn in cases:
        before = len(FAIL)
        try:
            one_site(tag, site, mult, mutate=fn, quiet=True)
        except Exception as exc:
            del FAIL[before:]
            print('  ok    caught: %-42s (%s)' % (name, type(exc).__name__))
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
    ap.add_argument('--freq', type=int, default=4)
    a = ap.parse_args()
    try:
        from ff7nx_dispatch_sites import CAMERA_WAIT_SITES
    except ImportError:
        sys.exit('ff7nx_dispatch_sites.py has no CAMERA_WAIT_SITES -- '
                 'regenerate it with ff7nx_locate.py')
    if not CAMERA_WAIT_SITES:
        sys.exit('no camera-wait sites')

    print('camera script opcode 0xF5 wait, battle_frame_multiplier = %d\n'
          % a.freq)
    print('all 256 operand values, both interpreters:')
    total = 0
    for tag in sorted(CAMERA_WAIT_SITES):
        total += one_site(tag, CAMERA_WAIT_SITES[tag], a.freq) or 0

    print('\nthe values that matter:')
    for op in (0x00, 0x01, 0x08, 0x20, 0x3F, 0x40, 0x7F, 0x80, 0xFE, 0xFF):
        print('    operand 0x%02X  stock stores %6d   this build stores %6d%s'
              % (op, _s16(guest_ax(op)), _s16(reference(op, a.freq)),
                 '   <- sentinel, left alone' if op == 0xFF else ''))

    print('\nmutations (each must be caught):')
    tag = sorted(CAMERA_WAIT_SITES)[0]
    caught, n = mutations(tag, CAMERA_WAIT_SITES[tag], a.freq)

    print('\n%d cave execution(s), %d/%d mutations caught, %d failure(s)'
          % (total, caught, n, len(FAIL)))
    if FAIL or caught != n:
        return 1
    print('PASS')
    return 0


def _s16(v):
    return v - 0x10000 if v & 0x8000 else v


if __name__ == '__main__':
    sys.exit(main())
