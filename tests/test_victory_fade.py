#!/usr/bin/env python3
"""
test_victory_fade.py -- the post-battle results-screen gate, as a predicate.

    python3 test_victory_fade.py [--nso exefs/main]

WHAT IS BEING CHECKED
=====================
`battle_sub_430DD0` ends the battle with

    cmp dword [0x9AE148], 0x10
    jl  keep_going

and the recompiler open-codes the x86 flags rather than emitting a `cmp`:

    mov  w11, #0xF          ; K-1
    lsl  w10, w9, #16       ; counter & 0xFFFF0000
    sub  w9,  w8, #0x10     ; K
    sub  w8,  w11, w8
    and  w8,  w8, w10
    lsr  w9,  w9, #31       ; SF
    lsr  w8,  w8, #31       ; OF
    cmp  w8, w9             ; jl == (SF != OF)

So the threshold lives in TWO instructions four apart, and moving one without
the other leaves the overflow flag calibrated for the old value. The branch is
`SF != OF`, so a wrong OF does not make the timing slightly off -- it can invert
the branch.

This runs the ACTUAL ENCODED WORDS through arm64emu.py, stock and patched, for
every counter value that matters, and checks the one thing that has to hold:

    branch_taken(patched, a)  ==  (a < 64)        for every a
    branch_taken(stock,   a)  ==  (a < 16)        for every a

including the signed edge cases where the overflow flag is the only thing that
makes `jl` differ from `<` on the raw bits.

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

import arm64emu as E

FAIL = []


def fail(msg):
    FAIL.append(msg)
    print('  FAIL  %s' % msg)


def load_text(path):
    import lz4.block
    d = open(path, 'rb').read()
    segs = [struct.unpack('<III', d[b:b + 12]) for b in (0x10, 0x20, 0x30)]
    comp = struct.unpack('<III', d[0x60:0x6C])
    fl = struct.unpack('<I', d[0x0C:0x10])[0]
    fo, mo, ds = segs[0]
    blob = d[fo:fo + comp[0]]
    return (lz4.block.decompress(blob, uncompressed_size=ds)
            if fl & 1 else blob[:ds])


# The flag block, exactly as it sits in .text at +0x9E344..+0x9E364.
# bfi is not in the emulator's subset and is not needed -- the test feeds the
# assembled counter in directly -- so the block starts after it.
BLOCK = 0x0009E348          # mov w11, #0xF
BLOCK_END = 0x0009E368      # one past `cmp w8, w9`


def branch_taken(words, counter):
    """
    Run the flag sequence for a counter value; return whether `jl` is taken.

    `jl` is `b.ne` on (OF, SF) here, so "taken" means SF != OF -- which is the
    keep-going path.
    """
    cpu = E.Cpu(E.Mem(), host_base=0x700000000)
    cpu.sp = 0x800000000
    for r in range(31):
        cpu.x[r] = E.GARBAGE
    cpu.x[8] = counter & 0xFFFFFFFF          # w8 = the full 32-bit counter
    cpu.x[9] = (counter >> 16) & 0xFFFF      # w9 = its high half, from ldrh
    cpu.run(BLOCK, words)
    return cpu.x[8] & 0xFFFFFFFF != cpu.x[9] & 0xFFFFFFFF   # SF != OF


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', default='dump/exefs/main')
    a = ap.parse_args()
    import ff7nx_60fps as F

    text = load_text(a.nso)
    group = {off: (old, new) for _l, off, old, new in
             F.NSO_GATED.get('victory-fade', ())}
    if not group:
        sys.exit('no victory-fade group in ff7nx_60fps.py')

    n = (BLOCK_END - BLOCK) // 4
    stock, patched = [], []
    for i in range(n):
        off = BLOCK + 4 * i
        cur, = struct.unpack('<I', text[off:off + 4])
        if off in group:
            old, new = group[off]
            if cur != old:
                fail('+0x%X reads %08X, the group expects %08X'
                     % (off, cur, old))
            stock.append(old)
            patched.append(new)
        else:
            stock.append(cur)
            patched.append(cur)

    print('results-screen gate, %d word(s) at +0x%X' % (n, BLOCK))
    print('  patched words: %s'
          % ' '.join('%08X' % w for w in patched))

    # Values worth being specific about: around both thresholds, and the signed
    # extremes where the overflow flag is the only thing that makes `jl` right.
    probes = list(range(0, 80))
    probes += [255, 256, 0x7FFE, 0x7FFF, 0x8000, 0xFFFF, 0x10000,
               0x7FFFFFFE, 0x7FFFFFFF, 0x80000000, 0x80000001, 0xFFFFFFFF]
    for tag, words, k in (('stock', stock, 16), ('patched', patched, 64)):
        bad = 0
        for v in probes:
            sv = v - (1 << 32) if v & 0x80000000 else v
            want = sv < k
            got = branch_taken(words, v)
            if got != want:
                fail('%s: counter %d (0x%X) -> keep going = %s, wanted %s '
                     '(threshold %d)' % (tag, sv, v, got, want, k))
                bad += 1
                if bad > 3:
                    break
        if not bad:
            print('  ok    %-8s branch is exactly `counter < %d` over %d '
                  'value(s), signed edges included' % (tag, k, len(probes)))

    print('\n  the transition frame:')
    for tag, words, k in (('stock', stock, 16), ('patched', patched, 64)):
        first = next(v for v in range(200) if not branch_taken(words, v))
        print('    %-8s results screen at frame %3d  (%.2f s at 60 FPS)'
              % (tag, first, first / 60.0))

    # ---- which of the two words actually decides the branch? -----------
    #
    # Asked rather than assumed, because the first version of this suite
    # claimed a mutation on the K-1 word would be caught and it was not. OF is
    # `bit31(a) AND bit31((K-1) - a)`; both bits can only be set together when
    # `a < K - 2^31`, and over that region 15 and 63 agree. So the K-1 word is
    # INERT for this predicate and the K word alone is the fix. Both are still
    # patched -- a derived constant left behind at a value that merely happens
    # to be harmless is how "correct for the wrong reason" becomes a real bug --
    # but the claim is checked here rather than asserted in a comment.
    print('\n  which word does what:')
    km1_only = [patched[0]] + stock[1:]          # K-1 moved, K left at 16
    k_only = [stock[0]] + patched[1:]            # K moved, K-1 left at 15
    probes_wide = list(range(0, 1 << 12)) + [
        0x7FFFFFFE, 0x7FFFFFFF, 0x80000000, 0x80000001, 0xFFFFFFFE, 0xFFFFFFFF,
        0x8000000F, 0x8000003F, 0x80000040, 0xC0000000]

    dec = [v for v in range(200)
           if branch_taken(km1_only, v) != branch_taken(patched, v)]
    if dec != list(range(16, 64)):
        fail('the K word should decide exactly the 48 values 16..63, it '
             'decides %d' % len(dec))
    else:
        print('    ok    K word (+0x9E350) decides exactly the 48 value(s) '
              '16..63 -- that is the fix')

    # Patching only the decisive word would work for this counter, which starts
    # at 0 and is reset at K, so it is never negative. It would NOT be the same
    # program: the flag pair is only self-consistent when both halves move.
    # Recording the exact inputs where it differs is the justification for
    # patching both, and it is checked rather than asserted -- an earlier
    # version of this comment claimed the K-1 word was inert, and this probe is
    # what disproved it.
    diff = [v for v in probes_wide
            if branch_taken(k_only, v) != branch_taken(patched, v)]
    reachable = [v for v in diff if not (v & 0x80000000)]
    if reachable:
        fail('patching only the K word changes the branch at %d REACHABLE '
             'counter value(s), first %d' % (len(reachable), reachable[0]))
    elif not diff:
        print('    --    K-1 word (+0x9E348) made no difference on this probe '
              'set; widen it before trusting that')
    else:
        print('    ok    K-1 word (+0x9E348) matters only for negative '
              'counters (%d value(s), e.g. 0x%X), which this counter can '
              'never hold -- patched anyway so the pair stays consistent'
              % (len(diff), diff[0]))

    print('\nmutations (each must be caught):')
    caught = 0
    muts = [('the K word left at 16 (nothing patched)', stock),
            ('K scaled by 2 instead of 4 (sub #32)',
             [patched[0], patched[1], 0x51008109] + patched[3:]),
            ('K scaled by 8 (sub #128)',
             [patched[0], patched[1], 0x51020109] + patched[3:])]
    for name, words in muts:
        before = len(FAIL)
        for v in list(range(0, 200)) + [0x80000000, 0xFFFFFFFF]:
            sv = v - (1 << 32) if v & 0x80000000 else v
            try:
                if branch_taken(words, v) != (sv < 64):
                    FAIL.append('caught')
                    break
            except Exception:
                FAIL.append('caught')
                break
        if len(FAIL) > before:
            del FAIL[before:]
            print('  ok    caught: %s' % name)
            caught += 1
        else:
            print('  FAIL  NOT caught: %s' % name)

    print('\n%d/%d mutations caught, %d failure(s)' % (caught, len(muts),
                                                       len(FAIL)))
    if FAIL or caught != len(muts):
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
