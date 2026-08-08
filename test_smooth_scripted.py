#!/usr/bin/env python3
"""
test_smooth_scripted.py -- the split-step helper for scripted field movement.

    python3 test_smooth_scripted.py

The property that matters is arithmetic, not encoding: every pair of frames
must displace the model by EXACTLY the stock step, neither half may have the
opposite sign to it, and neither may leave the model stuck. Those three are
what make this incapable of reintroducing the southmk2 freeze, so they are
checked exhaustively over the small range and by sweep over the full one.
"""
import struct
import sys

import capstone

import ff7nx_60fps as F

MD = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
FAIL = []


def ok(cond, what):
    print(('  ok  ' if cond else '  FAIL  ') + what)
    if not cond:
        FAIL.append(what)


def split(S):
    """What the helper computes: asr #1, then the complement."""
    half = S >> 1                      # arithmetic, rounds toward -inf
    return half, S - half


def main():
    print('the split is exact, same-signed and never stuck')
    bad_sum = bad_sign = bad_stuck = 0
    for S in range(-70000, 70001):
        h, o = split(S)
        if h + o != S:
            bad_sum += 1
        if S > 0 and (h < 0 or o <= 0):
            bad_sign += 1
        if S < 0 and (h > 0 or o > 0):
            bad_sign += 1
        if S and h == 0 and o == 0:
            bad_stuck += 1
    ok(bad_sum == 0, 'the two halves sum to the stock step for every S in '
                     '-70000..70000 -- the pair lands where stock lands')
    ok(bad_sign == 0, 'and neither half ever has the opposite sign to the '
                      'step, so it cannot oscillate across the target')
    ok(bad_stuck == 0, 'and never both zero, so it cannot walk in place')

    ok(split(1) == (0, 1) and split(-1) == (-1, 0),
       'the +/-1 edge carries the whole step on one frame rather than '
       'rounding both to zero')

    print()
    print('the emitted words')
    for D, name in ((28, 'X'), (27, 'Y')):
        w = F._walk_smooth_helper(D, 0x9000, 0x9DD598)
        txt = [(i.mnemonic + ' ' + i.op_str).strip()
               for i in MD.disasm(struct.pack('<%dI' % len(w), *w), 0x9000)]
        ok(len(txt) == 6, '%s helper is 6 words and all decode' % name)
        ok(txt[0] == 'asr w17, w%d, #1' % D, '%s: asr #1 gives the half' % name)
        ok(txt[2] == 'sub w%d, w%d, w17' % (D, D),
           '%s: the odd frame takes the complement' % name)
        ok(txt[4] == 'mov w%d, w17' % D, '%s: the even frame takes the half'
           % name)
        ok(all('w16' not in t or t.startswith('tbz') for t in txt),
           '%s: the tick counter in w16 is only read, never written' % name)

    print()
    print('the gate cave keeps its shape')
    base = F._walk_gate_cave(28, F.WALK_X_ORIG, F.WALK_X_RAW_RECOVER,
                             0x1000, 0x3FEE328, 0x3FEE32C, F.WALK_X_HOOK)
    smooth = F._walk_gate_cave(28, F.WALK_X_ORIG, F.WALK_X_RAW_RECOVER,
                               0x1000, 0x3FEE328, 0x3FEE32C, F.WALK_X_HOOK,
                               0x9000)
    ok(len(base) == len(smooth) == 15,
       'the tail-gap cave is 15 words with the option on or off')
    diff = [i for i in range(15) if base[i] != smooth[i]]
    ok(diff == [12],
       'and turning it on changes exactly ONE word (index %s) -- the rest of '
       'the confirmed-on-hardware cave is untouched' % diff)
    ok(F._b(0x1000 + 4 * 12, 0x9000) == smooth[12],
       'that word is the branch to the helper')

    print()
    if FAIL:
        print('%d FAILED' % len(FAIL))
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())
