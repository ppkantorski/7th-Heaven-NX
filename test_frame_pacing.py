#!/usr/bin/env python3
"""
test_frame_pacing.py -- the limiter divisor retarget.

    python3 test_frame_pacing.py [--exe path/to/ff7_en]

Checks the arithmetic and, if the exe is available, every claim the patch
rests on: that both divisors are where they are said to be, that each is
referenced only from its own limiter's setup, that the field setup subtracts
the early-exit margin and the battle setup does not, and that retargeting
rewrites exactly two patches and leaves the rest alone.
"""
import argparse
import struct
import sys

import ff7nx_60fps as F

FAIL = []


def ok(cond, what):
    print(('  ok  ' if cond else '  FAIL  ') + what)
    if not cond:
        FAIL.append(what)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', default='dump/ff7_1.02/ff7_en')
    a = ap.parse_args(argv)

    print('retargeting rewrites, never appends')
    base = list(F.EXE_CONFIRMED)
    out = F.retarget_limiters(base, 70)
    ok(len(out) == len(base), 'the patch list keeps its length')
    addrs = [va for _l, va, _o, _n in out]
    ok(len(addrs) == len(set(addrs)),
       'and no address is patched twice (which would fail its own old-bytes '
       'check)')
    by = {va: (o, n) for _l, va, o, n in out}
    for _w, va, _b in F.LIMITER_DIVISORS:
        ok(struct.unpack('<d', by[va][1])[0] == 70.0,
           'divisor at 0x%X now aims for 70' % va)
    orig = {va: (o, n) for _l, va, o, n in base}
    for va in orig:
        if va not in {x[1] for x in F.LIMITER_DIVISORS}:
            ok(by[va] == orig[va], 'the patch at 0x%X is untouched' % va)
    ok(struct.unpack('<d', by[0x7B7840][0])[0] == 30.0
       and struct.unpack('<d', by[0x7C0B00][0])[0] == 15.0,
       'and both still expect the STOCK bytes, not the 60 FPS ones')

    for bad in (10, 2000):
        try:
            F.retarget_limiters(base, bad)
            ok(False, 'refuses %g FPS' % bad)
        except SystemExit:
            ok(True, 'refuses %g FPS' % bad)
    try:
        F.retarget_limiters([p for p in base if p[1] != 0x7C0B00], 70)
        ok(False, 'refuses a patch list missing a divisor')
    except SystemExit:
        ok(True, 'refuses a patch list missing a divisor')

    try:
        d = open(a.exe, 'rb').read()
    except OSError as exc:
        print('\nexe tests SKIPPED -- pass --exe path/to/ff7_en (%s)' % exc)
        return 1 if FAIL else 0

    pe = struct.unpack('<I', d[0x3C:0x40])[0]
    nsec = struct.unpack('<H', d[pe + 6:pe + 8])[0]
    opt = struct.unpack('<H', d[pe + 20:pe + 22])[0]
    imgbase = struct.unpack('<I', d[pe + 52:pe + 56])[0]
    secs = []
    for i in range(nsec):
        o = pe + 24 + opt + i * 40
        vs, va, rs, ro = struct.unpack('<IIII', d[o + 8:o + 24])
        secs.append((imgbase + va, vs, ro, rs))

    def rd(va, n):
        for sva, vs, ro, _rs in secs:
            if sva <= va < sva + vs:
                return d[ro + (va - sva):ro + (va - sva) + n]
        return b''

    def dbl(va):
        return struct.unpack('<d', rd(va, 8))[0]

    print()
    print('the stock exe says what the patch assumes')
    ok(dbl(0x7B7840) == 30.0, 'field divisor 0x7B7840 is 30.0')
    ok(dbl(0x7C0B00) == 15.0, 'battle divisor 0x7C0B00 is 15.0')
    ok(dbl(0x7B7848) == 10000.0, 'the field early-exit margin is 10000 counts')
    ok(dbl(0x7B7898) == 2000000.0, 'and the debt bail-out is 2000000 counts')

    # each divisor is referenced only from its own limiter setup
    text = [s for s in secs if s[0] == 0x401000][0]
    blob = d[text[2]:text[2] + text[3]]

    def refs(va):
        n = struct.pack('<I', va)
        out, i = [], 0
        while True:
            j = blob.find(n, i)
            if j < 0:
                break
            out.append(0x401000 + j)
            i = j + 1
        return out

    ok(refs(0x7B7840) == [0x60E42A],
       'the field divisor has exactly one reference, in its own setup')
    ok(refs(0x7B7848) == [0x60E430],
       'so does the margin, four bytes later in the same expression')
    ok(len(refs(0x7C0B00)) == 2 and all(0x41B6A0 < r < 0x41B6E0
                                        for r in refs(0x7C0B00)),
       'the battle divisor has two, both inside the battle limiter setup')

    # field subtracts the margin, battle does not
    ok(rd(0x60E428, 12)[:2] == b'\xdc\x35' and rd(0x60E42E, 2) == b'\xdc\x25',
       'the field setup is fdiv then fsub -- it has the margin')
    ok(rd(0x41B6D2, 2) == b'\xdc\x35' and rd(0x41B6D8, 2) != b'\xdc\x25',
       'the battle setup is fdiv with NO fsub -- zero headroom at 60, which '
       'is why battle is the worse case')

    print()
    if FAIL:
        print('%d FAILED' % len(FAIL))
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())
