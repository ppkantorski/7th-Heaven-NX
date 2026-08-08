#!/usr/bin/env python3
"""
verify_analog_build.py -- read the 360-movement caves back OUT of a built
module and execute them.

    python3 verify_analog_build.py --built sdout/.../exefs/main

Everything else about this feature is checked against what the builder MEANT
to emit. This checks what actually shipped: it follows the branch at each hook
into .text, walks the cave through however many padding holes it was chained
across, and runs those words -- the real encoded ones, at their real
addresses -- through arm64emu against the reference model in ff7nx_analog.py.

That matters more here than for a contiguous cave. A chained cave's every
branch, adrp page and label was resolved against addresses chosen at build
time from whatever holes happened to be free, so "the builder's output is
correct" and "the module's contents are correct" are genuinely two claims.
"""
import argparse
import struct
import sys

import arm64emu
import nxmap
import ff7nx_analog as AN
import test_analog_cave as T


def _b_target(word, pc):
    if (word >> 26) != 0x05:
        return None
    d = word & 0x3FFFFFF
    if d & 0x2000000:
        d -= 0x4000000
    return pc + d * 4


def _cond_target(word, pc):
    """b.cond / cbz / cbnz (32- and 64-bit) target, or None."""
    if (word & 0xFF000000) == 0x54000000 or (word & 0x7E000000) == 0x34000000 \
            or (word & 0x7E000000) == 0xB4000000:
        imm = (word >> 5) & 0x7FFFF
        if imm & 0x40000:
            imm -= 0x80000
        return pc + imm * 4
    return None


def follow(img, hook):
    """
    Walk a chained cave from its hook.

    A worklist, not a straight walk: an unconditional `b` may be the chain
    link to the next padding hole OR one of the cave's own jumps, and a
    conditional branch has two successors. Both are enqueued and the run
    stops at anything that cannot fall through, so nothing past the end of a
    hole is ever read as if it were cave code.

    Returns (entry, {addr: word}).
    """
    w, = struct.unpack_from('<I', img, hook)
    entry = _b_target(w, hook)
    if entry is None:
        raise SystemExit('hook +0x%X is not an unconditional b (%08X)'
                         % (hook, w))
    code, work, seen = {}, [entry], set()
    while work:
        pc = work.pop()
        while pc not in seen:
            seen.add(pc)
            word, = struct.unpack_from('<I', img, pc)
            code[pc] = word
            tgt = _cond_target(word, pc)
            if tgt is not None:
                work.append(tgt)
                pc += 4
                continue
            tgt = _b_target(word, pc)
            if tgt is not None:
                if abs(tgt - hook) <= 8:        # the return to the game
                    break
                pc = tgt
                continue
            if (word & 0xFC000000) == 0x94000000:     # bl -- returns
                pc += 4
                continue
            pc += 4
    return entry, code


def next_executed(code, va):
    """The address the cave runs after `va`, hopping any chain branch."""
    nxt = va + 4
    w = code.get(nxt)
    if w is not None:
        t = _b_target(w, nxt)
        if t is not None and t in code:
            return t
    return nxt


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--built', required=True)
    a = ap.parse_args(argv)
    m = nxmap.Main(a.built)
    img = m.img

    fails = []

    # Which revision is this? The hook at 0x111BFC0 only exists in the FIRST
    # version of the feature -- the one whose input-object cave was the bug.
    # A module still carrying it has not been rebuilt with the current source,
    # and everything below would be describing code that is not there.
    old, = struct.unpack_from('<I', img, 0x111BFC0)
    if (old >> 26) == 0x05:
        print('!! THIS IS THE OLD BUILD.')
        print('   +0x111BFC0 is still hooked -- that is the input-poll cave,')
        print('   which the current source does not emit at all. The module')
        print('   was built from a tree that does not have the fix in it.')
        print()
        print('   Check that ff7nx_analog.py, ff7nx_analog_cave.py and')
        print('   ff7nx_60fps.py in your project are the current ones, then')
        print('   rebuild. The build log should say')
        print('       360 movement: field control direction  [rev2 ...]')
        print('   and must NOT say')
        print('       360 movement: remember the port input object')
        return 1
    print('rev2: one cave, object resolved through the port\'s own chain')
    print()

    def ok(cond, what):
        print(('  ok  ' if cond else '  FAIL  ') + what)
        if not cond:
            fails.append(what)

    field_entry, field = follow(img, AN.FIELD_HOOK)
    print('field cave : entry 0x%X, %d word(s), 0x%X span'
          % (field_entry, len(field), max(field) - min(field)))
    print()

    # every internal branch must be in range for the form that encodes it
    bad = []
    for va, w in field.items():
        if (w & 0xFF000000) == 0x54000000 or (w & 0x7E000000) in (0x34000000,):
            imm = (w >> 5) & 0x7FFFF
            if imm & 0x40000:
                imm -= 0x80000
            if not -(1 << 18) <= imm < (1 << 18):
                bad.append(va)
    ok(not bad, 'every b.cond/cbz in the shipped cave is inside +/-1 MB')

    # the tables the cave points at must be the tables we meant.
    # adrp and its add are consecutive in EXECUTION order, which in a chained
    # cave is not the same as consecutive in address order -- a chain branch
    # can sit between them.
    found = []
    for va, w in sorted(field.items()):
        if (w & 0x9F000000) != 0x90000000 or (w & 31) != 9:       # adrp x9
            continue
        immlo, immhi = (w >> 29) & 3, (w >> 5) & 0x7FFFF
        imm = (immhi << 2) | immlo
        if imm & (1 << 20):
            imm -= (1 << 21)
        page = (va & ~0xFFF) + imm * 0x1000
        nxt = field.get(next_executed(field, va))
        if nxt is not None and (nxt & 0xFFC00000) == 0x91000000 \
                and (nxt & 31) == 9 and ((nxt >> 5) & 31) == 9:
            found.append(page + ((nxt >> 10) & 0xFFF))
    tabs = bytes(AN.SNAP_TAB) + bytes(AN.ATAN_TAB)
    at = img.find(tabs, m.segs[1][1])
    ok(at > 0, 'both tables are in .rodata, contiguous, at 0x%X' % at)
    snap, atan = at, at + len(AN.SNAP_TAB)
    ok(snap in found, 'the cave points at SNAP_TAB (0x%X)' % snap)
    ok(atan in found, 'the cave points at ATAN_TAB (0x%X)' % atan)
    ok(len(found) == 3, 'and at exactly one other adrp+add pair (the key '
                        'buffer), not some address nobody asked for: %s'
                        % ['0x%X' % f for f in found])

    # the object resolution must be the port's own chain, offset for offset
    got = [w for _, w in sorted(field.items())
           if (w & 0xFFC003E0) == 0xF9400120 and (w & 31) == 9]
    offs = [((w >> 10) & 0xFFF) * 8 for w in got]
    ok(offs[:5] == [AN.INPUT_GOT & 0xFFF] + list(AN.INPUT_CHAIN),
       'it walks the port\'s own chain: [0x%X] then %s'
       % (AN.INPUT_GOT, list(AN.INPUT_CHAIN)))

    # ---- execute the shipped words ------------------------------------
    print()
    print('executing the SHIPPED words, 8 key masks x 360 degrees')
    import math
    bad = 0
    for km in sorted(AN.SNAP):
        for deg in range(0, 360):
            th = math.radians(deg)
            sx, sy = math.cos(th), math.sin(th)
            want = AN.offset(int(sx * 4096), int(sy * 4096), km)
            mem, cpu = T.setup(sx, sy, km, base=100, captured=True)
            mem.write(snap, bytes(AN.SNAP_TAB))
            mem.write(atan, bytes(AN.ATAN_TAB))
            exit_pc = cpu.run(field_entry, None, code=field)
            got = T.written(mem, cpu)
            if (got & 0xFF) != ((100 + want) & 0xFF) or exit_pc != AN.FIELD_HOOK + 4:
                bad += 1
                if bad <= 3:
                    print('      km=%s deg=%3d  cave %+4d  model %+4d  exit 0x%X'
                          % (format(km, '04b'), deg, got - 100, want, exit_pc))
    ok(bad == 0, '%d of 2880 exact against the reference model' % (2880 - bad))

    print()
    if fails:
        print('%d FAILED' % len(fails))
        return 1
    print('the module that shipped does what the model says')
    return 0


if __name__ == '__main__':
    sys.exit(main())
