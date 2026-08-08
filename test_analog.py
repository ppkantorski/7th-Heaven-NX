#!/usr/bin/env python3
"""
test_analog.py -- the 360 degree movement direction model.

    python3 test_analog.py

Checks the integer octant+table model in ff7nx_analog.py against real
math.atan2, and pins the properties the cave depends on. The accuracy number
this prints is the one quoted in the module docstring; if the table ever
changes, this is what says whether the change was an improvement.
"""
import math
import sys

import ff7nx_analog as A

_fail = [0]


def ok(cond, what):
    print(('  ok  ' if cond else '  FAIL  ') + what)
    if not cond:
        _fail[0] += 1


def sweep(fn, points=36000, R=4096):
    worst = 0.0
    se = 0.0
    n = 0
    exact = 0
    for k in range(points):
        th = k * 2.0 * math.pi / points
        ix = int(round(R * math.cos(th)))
        iy = int(round(R * math.sin(th)))
        if ix == 0 and iy == 0:
            continue
        got = fn(ix, iy)
        want = (math.atan2(iy, ix) * 128.0 / math.pi) % 256.0
        e = min(abs(got - want), 256 - abs(got - want))
        worst = max(worst, e)
        se += e * e
        n += 1
        if got == round(want) % 256:
            exact += 1
    return worst, math.sqrt(se / n), exact / float(n)


def main():
    print('the table is what it claims to be')
    ok(len(A.ATAN_TAB) == 65, '65 entries (one per 1/64 of the octant)')
    ok(A.ATAN_TAB[0] == 0 and A.ATAN_TAB[64] == 32,
       'atan(0)=0 and atan(1)=32 units (45 degrees)')
    ok(all(0 <= v <= 32 for v in A.ATAN_TAB), 'every entry fits in a byte, 0..32')
    ok(all(A.ATAN_TAB[i] <= A.ATAN_TAB[i + 1] for i in range(64)),
       'monotonic -- a non-monotonic table would make the stick jitter')

    print()
    print('the cardinals and diagonals are exact')
    for (x, y), want, nm in (((4096, 0), 0, 'right'), ((0, 4096), 64, 'up'),
                             ((-4096, 0), 128, 'left'), ((0, -4096), 192, 'down'),
                             ((2896, 2896), 32, 'up-right'),
                             ((-2896, 2896), 96, 'up-left'),
                             ((-2896, -2896), 160, 'down-left'),
                             ((2896, -2896), 224, 'down-right')):
        ok(A.dir256(x, y) == want, '%-10s -> %3d' % (nm, want))

    print()
    print('accuracy against math.atan2 (36000-point sweep)')
    worst, rms, exact = sweep(A.dir256)
    unit = 360.0 / 256.0
    print('      worst %.3f units (%.2f deg), rms %.3f units, exact %.1f%%'
          % (worst, worst * unit, rms, exact * 100))
    ok(worst < 1.0,
       'worst case is under ONE direction unit (%.3f) -- finer than the value '
       'FF7 stores' % worst)
    ok(rms < 0.5, 'rms is under half a unit (%.3f)' % rms)

    print()
    print('scale invariance -- a half-tilted stick points the same way')
    bad = 0
    for k in range(0, 3600):
        th = k * math.pi / 1800.0
        for R in (512, 4096, 32767):
            a = A.dir256(int(round(R * math.cos(th))), int(round(R * math.sin(th))))
            b = A.dir256(int(round(4096 * math.cos(th))), int(round(4096 * math.sin(th))))
            if a is not None and b is not None and min(abs(a - b), 256 - abs(a - b)) > 1:
                bad += 1
    ok(bad == 0, 'direction depends on angle, not magnitude (%d disagreements)' % bad)

    print()
    print('the feature is INERT unless it should not be')
    ok(A.dir256(0, 0) is None, 'a centred stick has no direction at all')
    ok(A.offset(0, 0, 0b0001) == 0, 'and produces offset 0')
    ok(A.offset(4096, 0, 0) == 0, 'no direction key held -> offset 0')
    ok(A.offset(4096, 0, 0b0101) == 0,
       'left+right together (not one of the eight) -> offset 0')
    ok(A.offset(4096, 0, 0b0001) == 0,
       'stick exactly on the snapped direction -> offset 0')

    print()
    print('the offset is the SHORT way round and stays in range')
    worst_off = 0
    for km, snap in A.SNAP.items():
        for k in range(0, 3600):
            th = k * math.pi / 1800.0
            o = A.offset(int(round(4096 * math.cos(th))),
                         int(round(4096 * math.sin(th))), km)
            worst_off = max(worst_off, abs(o))
            if not -128 <= o <= 128:
                _fail[0] += 1
    ok(worst_off <= 128, 'offset never leaves -128..128 (max seen %d)' % worst_off)
    o1 = A.offset(4096, -400, 0b0001)
    ok(o1 < 0, 'a stick just BELOW "right" gives a negative offset, not +250')

    print()
    print('the convention cancels')
    # rotating stick and snap together must not change the offset
    same = True
    for rot in (0, 32, 64, 96, 128, 160, 192, 224):
        for k in range(0, 360):
            th = math.radians(k)
            base = A.offset(int(round(4096 * math.cos(th))),
                            int(round(4096 * math.sin(th))), 0b0001)
            th2 = th + rot * 2 * math.pi / 256.0
            km2 = [m for m, s in A.SNAP.items() if s == rot][0]
            rotd = A.offset(int(round(4096 * math.cos(th2))),
                            int(round(4096 * math.sin(th2))), km2)
            if abs(base - rotd) > 1:
                same = False
                break
    ok(same, 'rotating stick and snapped direction together leaves offset '
             'unchanged -- so FF7\'s idea of north never enters the model')

    print()
    if _fail[0]:
        print('%d FAILED' % _fail[0])
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())
