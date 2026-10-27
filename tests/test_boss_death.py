#!/usr/bin/env python3
"""
test_boss_death.py -- the boss vanquish countdown, checked as arithmetic.

    python3 test_boss_death.py [--nso exefs/main] [--freq 4]

`battle_boss_death_sub_5BC5EC` is an effect60 slot FFNx runs at full rate:

    n_frames = 64
    each frame:
        if (n_frames == 0) retire
        if (n_frames == 58 || n_frames == 64) boss_death_call(0xFA,0xFA,0xFA)
        modelPosition.z = field_A + ((n_frames & 1) ? +64 : -64)
        n_frames--

Scaling n_frames to 256 makes the countdown last the right wall time at 60 FPS.
Everything else in the group exists so the rest of the function still means the
same thing once it does. That is what this checks, over the WHOLE countdown:

  1. the flash fires at the same two points of the countdown, as a fraction;
  2. the wobble has the same period and the same phase -- `bit2(4c) == bit0(c)`
     for every c, which is the whole reason one word replaces FFNx's table;
  3. the wobble instruction still yields 0-or-1, because the recompiler feeds it
     to `eor w8,w8,#1` to build the guest zero flag. `and #4` would give 0-or-4
     and silently invert that flag -- the reason the patch is a `ubfx` and not
     the obvious mask change;
  4. the two per-frame position steps are divided by the same factor the
     countdown was multiplied by.

The encoded words are executed by arm64emu.py, stock and patched, rather than
modelled.

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


def const_of(w):
    """
    The immediate a `movz Wd,#imm` or `orr Wd,WZR,#imm` materialises.

    The stock countdown is `orr w22,wzr,#64` and the patched one is
    `movz w22,#256` -- the recompiler picks whichever encoding fits, so reading
    the field positionally gives 53279 for one of them and looks like a
    catastrophic mismatch.
    """
    if (w & 0xFFE00000) == 0x52800000:                 # movz Wd,#imm16
        return (w >> 5) & 0xFFFF
    if (w & 0xFF800000) == 0x32000000:                 # orr Wd,WZR,#bitmask
        from ff7nx_resolve import decode_bitmask
        return decode_bitmask(0, (w >> 22) & 1, (w >> 16) & 0x3F,
                              (w >> 10) & 0x3F)
    raise SystemExit('%08X is neither movz nor orr-immediate' % w)


def imm12_of(w):
    """The imm12 of an add/sub/cmp immediate."""
    return (w >> 10) & 0xFFF


def run(words, inputs, out):
    cpu = E.Cpu(E.Mem(), host_base=0x700000000)
    cpu.sp = 0x800000000
    for r in range(31):
        cpu.x[r] = E.GARBAGE
    for r, v in inputs.items():
        cpu.x[r] = v & 0xFFFFFFFF
    cpu.run(0x1000, words)
    return cpu.x[out] & 0xFFFFFFFF


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', default='dump/exefs/main')
    ap.add_argument('--freq', type=int, default=4)
    a = ap.parse_args()
    import ff7nx_60fps as F

    text = load_text(a.nso)
    grp = {off: (old, new) for _l, off, old, new in
           F.NSO_GATED.get('boss-death', ())}
    if not grp:
        sys.exit('no boss-death group in ff7nx_60fps.py')

    def word(off, patched):
        cur, = struct.unpack('<I', text[off:off + 4])
        old, new = grp[off]
        if cur != old:
            fail('+0x%X reads %08X, the group expects %08X' % (off, cur, old))
        return new if patched else old

    N_INIT, TRIG_A, TRIG_B = 0x007C9130, 0x007C9CD0, 0x007C9D08
    WOBBLE, STEP_A, STEP_B = 0x007C9DB0, 0x007C9AAC, 0x007C9B04
    print('boss vanquish countdown, battle_frame_multiplier = %d\n' % a.freq)

    # ---- 1. the countdown length ---------------------------------------
    s_n = const_of(word(N_INIT, False))
    p_n = const_of(word(N_INIT, True))
    if p_n != s_n * a.freq:
        fail('n_frames %d -> %d, wanted x%d' % (s_n, p_n, a.freq))
    else:
        print('  ok    countdown       %3d -> %4d frames  (%.2fs -> %.2fs at '
              '60 FPS)' % (s_n, p_n, s_n / 60.0, p_n / 60.0))

    # ---- 2. the flash triggers, as fractions of the countdown ----------
    for off, label in ((TRIG_A, 'flash trigger A'), (TRIG_B, 'flash trigger B')):
        so = imm12_of(word(off, False))
        po = imm12_of(word(off, True))
        if po != so * a.freq:
            fail('%s %d -> %d, wanted x%d' % (label, so, po, a.freq))
        elif abs(so / s_n - po / p_n) > 1e-9:
            fail('%s fires at %.4f of the stock countdown but %.4f of the '
                 'patched one' % (label, so / s_n, po / p_n))
        else:
            print('  ok    %-15s %3d -> %4d   (%.1f%% through the countdown, '
                  'unchanged)' % (label, so, po, 100.0 * so / s_n))

    # ---- 3. the wobble: same period AND same phase ---------------------
    #
    # The real words: stock `and w8,w19,#1`, patched `ubfx w8,w19,#2,#1`.
    # The claim is bit2(freq*c) == bit0(c) for every counter value, which is
    # what makes one word equivalent to FFNx's eight-entry table for a
    # two-position toggle.
    sw, pw = [word(WOBBLE, False)], [word(WOBBLE, True)]
    bad = 0
    for c in range(0, s_n + 1):
        s_bit = run(sw, {19: c}, 8)
        p_bit = run(pw, {19: c * a.freq}, 8)
        if s_bit not in (0, 1) or p_bit not in (0, 1):
            fail('wobble c=%d yields %d / %d -- must be 0 or 1, the guest ZF '
                 'is built from it with eor #1' % (c, s_bit, p_bit))
            bad = 1
            break
        if s_bit != p_bit:
            fail('wobble phase differs at c=%d: stock %d, patched(%d) %d'
                 % (c, s_bit, c * a.freq, p_bit))
            bad = 1
            break
    if not bad:
        print('  ok    wobble          bit0(c) == bit%d(%dc) for all %d counter '
              'value(s); result stays 0/1'
              % (a.freq.bit_length() - 1, a.freq, s_n + 1))
        # and the period in wall time
        flips = sum(1 for c in range(s_n) if run(sw, {19: c}, 8) !=
                    run(sw, {19: c + 1}, 8))
        pflips = sum(1 for c in range(p_n) if run(pw, {19: c}, 8) !=
                     run(pw, {19: c + 1}, 8))
        if flips != pflips:
            fail('wobble flips %d times stock, %d patched' % (flips, pflips))
        else:
            print('  ok    wobble period  %d flip(s) over the whole countdown, '
                  'both' % flips)

    # ---- 4. the per-frame position steps -------------------------------
    for off, label in ((STEP_A, 'position step A'), (STEP_B, 'position step B')):
        so = imm12_of(word(off, False))
        po = imm12_of(word(off, True))
        if po * a.freq != so:
            fail('%s %d -> %d, wanted /%d' % (label, so, po, a.freq))
        else:
            print('  ok    %-15s %3d -> %4d   (/%d, so the total travel over '
                  'the countdown is unchanged)' % (label, so, po, a.freq))

    # ---- mutations -----------------------------------------------------
    print('\nmutations (each must be caught):')
    caught = 0
    muts = [('wobble left on bit 0 (vibrates every frame)',
             WOBBLE, 0x12000268),
            ('wobble on bit 1 (twice too fast)', WOBBLE, 0x53010668),
            ('wobble as `and #4` -- 0-or-4 breaks the guest ZF',
             WOBBLE, 0x12180268),
            ('countdown not scaled', N_INIT, 0x321A03F6),
            ('flash trigger A not scaled', TRIG_A, 0x7100E91F)]
    for name, off, wrong in muts:
        before = len(FAIL)
        saved = grp[off]
        grp[off] = (saved[0], wrong)
        try:
            if off == WOBBLE:
                w = [wrong]
                for c in range(0, 65):
                    r = run(w, {19: c * a.freq}, 8)
                    if r not in (0, 1) or r != (c & 1):
                        FAIL.append('caught')
                        break
            else:
                so = const_of(saved[0]) if off == N_INIT else imm12_of(saved[0])
                po = const_of(wrong) if off == N_INIT else imm12_of(wrong)
                if po != so * a.freq:
                    FAIL.append('caught')
        except Exception:
            FAIL.append('caught')
        grp[off] = saved
        if len(FAIL) > before:
            del FAIL[before:]
            print('  ok    caught: %s' % name)
            caught += 1
        else:
            print('  FAIL  NOT caught: %s' % name)

    print('\n%d/%d mutations caught, %d failure(s)'
          % (caught, len(muts), len(FAIL)))
    if FAIL or caught != len(muts):
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
