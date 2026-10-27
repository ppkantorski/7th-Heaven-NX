#!/usr/bin/env python3
"""
test_aura_all.py -- all three scaled aura types, checked as arithmetic.

    python3 test_aura_all.py [--nso exefs/main] [--freq 4]

FF7 has four aura types, dispatched by `run_aura_effects_5C0230` on an index:

    type 0 -> 0x5C0300   magic        -- no constants; paced by effect60-throttle
    type 1 -> 0x5C0572   limit break  -- `limit-aura`
    type 2 -> 0x5C06BF   enemy skill  -- `aura-eskill`
    type 3 -> 0x5C0953   summon       -- `aura-summon`

The three scaled ones all obey the same property. Their frame counter runs four
times as far at 60 FPS, and every constant exists to keep the aura looking
identical while it does:

    patched(4 * c)  ==  stock(c)      for every c in the aura's lifetime

That is what this checks, by executing the ACTUAL ENCODED WORDS -- stock read
out of the stock NSO, patched as the build writes them -- through arm64emu.py.

WHY THESE WERE HARD TO PLACE
============================
The recompiler folds `(K - x) << N` into a shifted-register subtract with the
product baked into a register:

    x86    mov eax,0xe ; sub eax,edx ; shl eax,7
    arm64  mov w8,#0x700           ; sub w20, w8, w9, lsl #7

FFNx rewrites the x86 as `mov eax,56 ; sub ; shl 5`, and 56<<5 == 14<<7 == 0x700
-- so the constant does not move and only the shift does. A resolver searching
for an immediate equal to the x86 operand finds nothing, which is why 5 of the 9
enemy-skill patches and 1 of the 2 summon patches sat unplaced. Reading the
arithmetic places them; this file is the check on that reading.

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


def run(words, inputs, out):
    cpu = E.Cpu(E.Mem(), host_base=0x700000000)
    cpu.sp = 0x800000000
    for r in range(31):
        cpu.x[r] = E.GARBAGE
    for r, v in inputs.items():
        cpu.x[r] = v & 0xFFFFFFFF
    cpu.run(0x1000, words)
    return cpu.x[out] & 0xFFFFFFFF


def s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', default='dump/exefs/main')
    ap.add_argument('--freq', type=int, default=4)
    a = ap.parse_args()
    import ff7nx_60fps as F

    text = load_text(a.nso)
    patches = {}
    for g in ('limit-aura', 'aura-eskill', 'aura-summon'):
        for _l, off, old, new in F.NSO_GATED.get(g, ()):
            patches[off] = (old, new)
    if not patches:
        sys.exit('no aura groups in ff7nx_60fps.py')

    def word(off, patched):
        cur, = struct.unpack('<I', text[off:off + 4])
        if off in patches:
            old, new = patches[off]
            if cur != old:
                fail('+0x%X reads %08X, the group expects %08X'
                     % (off, cur, old))
            return new if patched else old
        return cur

    X3 = 0x0B080508            # add w8, w8, w8, lsl #1   (the x3 of an imul)
    SUB4096 = 0x51400515       # sub w21, w8, #1, lsl #12
    C700 = 0x32180BE8          # mov w8, #0x700
    C3800 = 0x32150BE9         # mov w9, #0x3800
    C1000 = 0x321403E9         # mov w9, #0x1000

    # (group, name, words(patched?), input regs, out reg, what the x86 computes)
    sites = [
        ('limit-aura', 'growth A  x0x600 -> x0x180',
         lambda p: [X3, word(0x007D81E4, p)], {8: None}, 24,
         lambda c: c * 0x600),
        ('limit-aura', 'growth B  x0xC00 -> x0x300',
         lambda p: [X3, word(0x007D8300, p)], {8: None}, 24,
         lambda c: c * 0xC00),
        ('limit-aura', 'offset    (c-8)<<9 -> (c-32)<<7',
         lambda p: [word(0x007D82D8, p), SUB4096], {8: None}, 21,
         lambda c: (c - 8) << 9),
        ('aura-eskill', 'ramp A    (14-c)<<7 -> (56-c)<<5',
         lambda p: [C700, word(0x007D86EC, p)], {9: None}, 20,
         lambda c: (14 - c) << 7),
        ('aura-eskill', 'ramp B    (14-c)<<10 -> (56-c)<<8',
         lambda p: [C3800, word(0x007D8764, p)], {8: None}, 21,
         lambda c: (14 - c) << 10),
        # +0x7D880C is `sub w8, w8, #8, lsl #12` -- the -0x8000 subtrahend, read
        # out of .text rather than written from memory, because getting it wrong
        # is exactly the kind of mistake this file exists to catch. It does NOT
        # move: (c'<<10) - 0x8000 == (c'-32)<<10, the same way the limit aura's
        # -0x1000 stays put.
        ('aura-eskill', 'offset    (c-8)<<12 -> (c-32)<<10',
         lambda p: [word(0x007D8808, p), word(0x007D880C, p)], {8: None}, 8,
         lambda c: (c - 8) << 12),
        ('aura-eskill', 'scale     c*-4096 -> c*-1024',
         lambda p: [word(0x007D8834, p)], {8: None}, 8,
         lambda c: -(c << 12)),
        ('aura-summon', 'ramp      (c<<12)/8 -> (c<<10)/8',
         lambda p: [word(0x007D9144, p), C1000, 0x4B080136], {8: None}, 22,
         lambda c: 0x1000 - ((c << 12) >> 3)),
    ]

    print('aura constants, battle_frame_multiplier = %d' % a.freq)
    print('\nthe property: patched(%d * c) == stock(c), on the real words\n'
          % a.freq)
    checked = 0
    for grp, name, build, inp, out, model in sites:
        reg = next(iter(inp))
        sw, pw = build(False), build(True)
        bad = False
        for c in range(0, 16):
            gs = run(sw, {reg: c}, out)
            gp = run(pw, {reg: c * a.freq}, out)
            if gs != gp:
                fail('%s %s: c=%d stock %d, patched(%d) %d'
                     % (grp, name, c, s32(gs), c * a.freq, s32(gp)))
                bad = True
                break
            if s32(gs) != model(c) & 0xFFFFFFFF and s32(gs) != model(c):
                fail('%s %s: c=%d the STOCK arm64 gives %d, the x86 says %d '
                     '-- the folding was misread'
                     % (grp, name, c, s32(gs), model(c)))
                bad = True
                break
            checked += 2
        if not bad:
            print('  ok    %-12s %-34s %08X -> %08X'
                  % (grp, name,
                     sw[-1] if len(sw) > 1 and grp != 'aura-summon' else sw[0],
                     pw[-1] if len(pw) > 1 and grp != 'aura-summon' else pw[0]))

    print('\nphase boundaries (a cmp against the counter):')
    for grp, off, label, v in (
            ('limit-aura', 0x007D8244, 'limit  phase 1', 8),
            ('limit-aura', 0x007D8284, 'limit  phase 2', 16),
            ('limit-aura', 0x007D853C, 'limit  ends', 15),
            ('aura-eskill', 0x007D87D0, 'eskill phase', 8),
            ('aura-eskill', 0x007D8AA8, 'eskill ends', 15),
            ('aura-summon', 0x007D9704, 'summon phase', 8)):
        old, new = word(off, False), word(off, True)
        oi, ni = (old >> 10) & 0xFFF, (new >> 10) & 0xFFF
        if oi != v or ni != v * a.freq:
            fail('%s +0x%X: cmp #%d -> cmp #%d, wanted #%d -> #%d'
                 % (label, off, oi, ni, v, v * a.freq))
        else:
            print('  ok    %-14s +0x%X  cmp #%-3d -> cmp #%d'
                  % (label, off, oi, ni))
            checked += 1

    print('\nmutations (each must be caught):')
    import a64 as A
    caught = 0

    def sub_sh(rd, rn, rm, sh):
        return 0x4B000000 | (rm << 16) | (sh << 10) | (rn << 5) | rd

    muts = [('eskill ramp A shift off by one', 0x007D86EC,
             sub_sh(20, 8, 9, 6)),
            ('eskill ramp B not scaled', 0x007D8764, 0x4B082935),
            ('eskill scale over-corrected', 0x007D8834, sub_sh(8, 31, 8, 8)),
            ('summon ramp off by one', 0x007D9144, A.lsl(8, 8, 8)),
            ('limit growth A not scaled', 0x007D81E4, 0x53175918)]
    for name, off, wrong in muts:
        before = len(FAIL)
        saved = patches[off]
        patches[off] = (saved[0], wrong)
        for grp, nm, build, inp, out, _m in sites:
            reg = next(iter(inp))
            sw, pw = build(False), build(True)
            for c in range(0, 16):
                if run(sw, {reg: c}, out) != run(pw, {reg: c * a.freq}, out):
                    FAIL.append('caught')
                    break
            if len(FAIL) > before:
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
