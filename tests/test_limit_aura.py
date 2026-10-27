#!/usr/bin/env python3
"""
test_limit_aura.py -- the limit break aura constants, checked as arithmetic.

    python3 test_limit_aura.py [--nso exefs/main] [--freq 4]

WHAT IS BEING CHECKED
=====================
`limit_break_aura_effects_5C0572` grows the charge-up glow from a per-slot frame
counter. At 15 FPS that counter reaches 15 in 15 frames; at 60 FPS it reaches 60
in the same wall-clock second. FFNx's seven patches exist to keep the aura
looking identical while the counter counts four times as far.

So there is one property that has to hold, and it is checkable rather than
eyeballable:

    patched(4 * c)  ==  stock(c)        for every c in the aura's lifetime

That is what this tests, by executing the ACTUAL ENCODED WORDS -- the stock ones
read out of the stock NSO, and the patched ones the build writes -- through
arm64emu.py. Three arithmetic sites and three phase boundaries.

Three of the six words could not be placed by ff7nx_resolve.py, because the
recompiler folded the arithmetic and there is no longer an immediate equal to
the x86 operand to search for:

    x86  imul ecx,ecx,0x600      arm64  add w8,w8,w8,lsl #1  ;  lsl w24,w8,#9
    x86  imul eax,eax,0xC00      arm64  add w8,w8,w8,lsl #1  ;  lsl w24,w8,#10
    x86  sub ecx,8 ; shl ecx,9   arm64  lsl w8,w8,#9 ; sub w21,w8,#0x1000

They were placed by reading the arithmetic instead. This file is the check on
that reading: if the folding was misread, `patched(4c) == stock(c)` fails.

The third case is the one worth being suspicious of, because FFNx patches it as
TWO edits and the ARM64 needs ONE. `(x-8)<<9` folds to `(x<<9) - 0x1000`, and
FFNx's patched `(x-32)<<7` folds to `(x<<7) - 0x1000` -- the same subtrahend, so
only the shift moves. The test does not take that on trust; it runs both forms.

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


def run(words, inputs, out_reg):
    """Execute a short straight-line sequence; return the 32-bit out_reg."""
    cpu = E.Cpu(E.Mem(), host_base=0x700000000)
    cpu.sp = 0x800000000
    for r in range(31):
        cpu.x[r] = E.GARBAGE
    for r, v in inputs.items():
        cpu.x[r] = v & 0xFFFFFFFF
    cpu.run(0x1000, words)
    return cpu.x[out_reg] & 0xFFFFFFFF


def s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', default='dump/exefs/main')
    ap.add_argument('--freq', type=int, default=4)
    a = ap.parse_args()
    import ff7nx_60fps as F

    text = load_text(a.nso)
    patches = {off: (old, new) for _l, off, old, new in
               F.NSO_GATED.get('limit-aura', ())}
    if not patches:
        sys.exit('no limit-aura group in ff7nx_60fps.py')

    def word(off, patched):
        cur, = struct.unpack('<I', text[off:off + 4])
        if off in patches:
            old, new = patches[off]
            if cur != old:
                fail('+0x%X reads %08X, the group expects %08X' % (off, cur, old))
            return new if patched else old
        return cur

    X3 = 0x0B080508          # add w8, w8, w8, lsl #1
    SUB4096 = 0x51400515     # sub w21, w8, #1, lsl #12

    # (name, words-builder, out reg, what FFNx says the value is)
    sites = [
        ('growth A  x0x600 -> x0x180',
         lambda p: [X3, word(0x007D81E4, p)], 24,
         lambda c: c * 0x600),
        ('growth B  x0xC00 -> x0x300',
         lambda p: [X3, word(0x007D8300, p)], 24,
         lambda c: c * 0xC00),
        ('offset    (x-8)<<9 -> (x-32)<<7',
         lambda p: [word(0x007D82D8, p), SUB4096], 21,
         lambda c: ((c - 8) << 9)),
    ]

    print('limit break aura, battle_frame_multiplier = %d' % a.freq)
    print('\nthe property: patched(%d * c) == stock(c), on the real words\n'
          % a.freq)
    checked = 0
    for name, build, out, model in sites:
        stock_w, patch_w = build(False), build(True)
        bad = 0
        for c in range(0, 16):
            got_stock = run(stock_w, {8: c}, out)
            got_patch = run(patch_w, {8: c * a.freq}, out)
            if got_stock != got_patch:
                fail('%s: c=%d stock gives %d, patched(%d) gives %d'
                     % (name, c, s32(got_stock), c * a.freq, s32(got_patch)))
                bad += 1
                break
            # and the stock words must match FFNx's own reading of the x86
            if s32(got_stock) != model(c):
                fail('%s: c=%d the STOCK arm64 gives %d, the x86 says %d -- the '
                     'folding was misread' % (name, c, s32(got_stock), model(c)))
                bad += 1
                break
            checked += 2
        if not bad:
            print('  ok    %-34s %08X -> %08X   16 counter value(s)'
                  % (name, stock_w[-1] if name.startswith('growth')
                     else stock_w[0],
                     patch_w[-1] if name.startswith('growth') else patch_w[0]))

    print('\nphase boundaries (a cmp against the counter):')
    for off, label, stock_v in ((0x007D8244, 'phase 1', 8),
                                (0x007D8284, 'phase 2', 16),
                                (0x007D853C, 'aura ends', 15)):
        old = word(off, False)
        new = word(off, True)
        old_imm = (old >> 10) & 0xFFF
        new_imm = (new >> 10) & 0xFFF
        okk = old_imm == stock_v and new_imm == stock_v * a.freq
        (print if okk else fail)(
            '  ok    %-12s +0x%X  cmp #%d -> cmp #%d' % (label, off, old_imm,
                                                         new_imm)
            if okk else
            '%s +0x%X: cmp #%d -> cmp #%d, wanted #%d -> #%d'
            % (label, off, old_imm, new_imm, stock_v, stock_v * a.freq))
        checked += 1

    print('\nmutations (each must be caught):')
    caught = 0
    import a64 as A
    muts = [('growth A off by one shift (lsl #8, not #7)',
             0x007D81E4, A.lsl(24, 8, 8)),
            ('growth B not scaled at all (left at lsl #10)',
             0x007D8300, A.lsl(24, 8, 10)),
            ('offset over-corrected (lsl #6, not #7)',
             0x007D82D8, A.lsl(8, 8, 6))]
    for name, off, wrong in muts:
        before = len(FAIL)
        saved = patches[off]
        patches[off] = (saved[0], wrong)
        for nm, build, out, _m in sites:
            stock_w, patch_w = build(False), build(True)
            for c in range(0, 16):
                if run(stock_w, {8: c}, out) != run(patch_w, {8: c * a.freq},
                                                    out):
                    FAIL.append('caught')
                    break
        patches[off] = saved
        if len(FAIL) > before:
            del FAIL[before:]
            print('  ok    caught: %s' % name)
            caught += 1
        else:
            print('  FAIL  NOT caught: %s' % name)

    print('\n%d check(s), %d/%d mutations caught, %d failure(s)'
          % (checked, caught, len(muts), len(FAIL)))
    if FAIL or caught != len(muts):
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
