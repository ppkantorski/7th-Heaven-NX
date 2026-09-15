#!/usr/bin/env python3
r"""
ff7nx_ifritsrc.py -- the source half of Ifrit's heat warp: make the captures
reach the bottom of the frame, and give wave 1 the texels it is being magnified
by four without.

SIX WORDS, IN PLACE, NO CAVE.

WHERE THIS SITS
===============
`ff7nx_battlewide`'s three caves fix the DESTINATION -- the bands were drawn
over 640 overlay units on a path that multiplies x by WS_SCALE = 0.75, so the
whole wide picture landed squeezed into the central 4:3. That is the reported
symptom and `_ifrit_ops` has the write-up.

This module fixes the two things on the SOURCE side that the destination
transform cannot reach, and it is deliberately separate because its words go
into `ifrit_setup`'s descriptor code rather than into a cave.

WHAT THE DESCRIPTORS ARE (x86 0x595DE7, the `[0x9ACB5C] == 2` arm at 0x595EA6)
==============================================================================
    cap1 = (x=0,   y=0,   w=0x100=256, h=0xA6=166)   -> [0x8C00B4]
    cap2 = (x=512, y=0,   w=0x41 = 65, h=0x58= 88)   -> [0x8C00B8]
    cap3 = (x=512, y=176, w=0x41 = 65, h=0x58= 88)   -> [0x8C00BC]
    xscale = yscale = [0x9AD1A8] = 2

and the mapping, established for Escape in BUILD-409 and unchanged here:

    staging column = fb_tex.x + u * xscale       tex_w cancels exactly
    staging row    = fb_tex.y + v * yscale       tex_h cancels exactly

PART ONE -- THE HEIGHT (six multiply words, three yscale words, one origin)
===========================================================================
**BUILD 411b. Build 411a put the geometry half of this in the draw cave and it
was wrong in a way that only hardware could show: the picture magnified further
every frame until the coordinates overflowed their `strh`, and the screen
became vertical stripes of single texels.**

The cause is which pass writes what. `ifrit_setup`'s function is one body with
a frame-0 gate at the top:

    x86 0x595A20  movsx edx, word [state+2]      the effect's frame counter
    x86 0x595A24  test edx, edx
    x86 0x595A26  jne 0x5960FA                   -> SKIP the build pass
    ARM +0x70EAA8 cbz w8, #0x70EBB8              -> the same gate, inverted

Everything before 0x5960FA -- the 42 records, their UVs, **their y
coordinates**, and the three captures -- runs ONLY on frame 0. Every later frame
resets the display-list pointer and runs the three draw loops, which rewrite
**x alone**, fresh from the sine each time.

So a transform in the draw cave is idempotent for x and compounding for y.
332 * 1.5^n overflows int16 after about eleven frames, which is exactly where
the recording turns to stripes. The y scaling has to happen where y is written.

The vertical is EXACTLY 1:1 in stock, and that is why it stops at the UI line:

    wave 1/2   band y = v * scale         staging row = cap.y + v * yscale
    wave 3     band y = (v + 0x58) * scale             cap3.y = 0x58 * scale

Both sides are `v * 2`, so band row r shows screen row r, and v tops out at
166 -> 332 = the battle viewport height. Scaling only the geometry stretches
the picture; that is build 397, and it reported "a warped copy of the UI over
itself, plus a seam on the right".

Three numbers, each forced by the last:

    yscale 2 -> 3        so the sampling can reach staging row 498 at all
    y = v * 3            so the geometry agrees exactly -- in the BUILD pass,
                         which runs once, so it cannot compound
    cap3.y 176 -> 264    = 0x58 * 3, so wave 3 still starts where wave 2 stops
                           -- without it wave 3 is 88 rows out, which IS 397's
                           seam

The build pass finishes each y as `v * [0x9AD1A8] + rect.y`, and the multiply
is one instruction, so `v * 3` is one word with no cave at all:

    mul w8, w8, w9   ->   add w8, w8, w8, lsl #1

There are eighteen byte-identical `mul w8, w8, w9` in this function -- six for
y in the build pass and twelve for x in the draw pass -- so the site cannot be
found by its own word. They are told apart by the register the `mov w0, wN`
after them names: `ff7nx_escapescale`'s discriminator, that rect.y is reached
as rect.x + 4 and therefore lives in a different hoisted register. `find_ymul`
derives the six addresses from the binary and refuses unless it finds exactly
six with rect.y and exactly twelve with rect.x.

`yscale` is `struc_91[0x18]`, copied from object +0x110. `ifrit_setup` writes
+0x10C (xscale) and +0x110 (yscale) from two SEPARATE loads of the same global,
so the second load can become a literal and xscale is untouched:

    +0x7113E0  ldr w21, [x0]  ->  mov w21, #3      capture 1
    +0x711644  ldr w21, [x0]  ->  mov w21, #3      capture 2
    +0x7118C8  ldr w19, [x0]  ->  mov w19, #3      capture 3

In all three the register is re-loaded (`ldr` again) before it is next read --
+0x711410, +0x711674, +0x7118F8 -- so writing a literal there cannot leak.
`verify` asserts that rather than assuming it, which matters at the third site
because w19 is also the register the prologue hoisted `0x9AD1A8` into.

    +0x711168  mov w20, #0xb0  ->  mov w20, #0x108    cap3.y, the mode-2 arm

That site is in the `[0x9ACB5C] == 2` arm only; mode 0's `mov w20, #0x58` at
+0x71110C and the prologue's `mov w8, #0xb0` at +0x70F9B4 are asserted
unchanged, so a different graphics mode is left alone.

PART TWO -- THE TEXELS (two words), AND WHY BUILD 399's DIAGNOSIS WAS WRONG
===========================================================================
Build 399 removed the warp with this reasoning: "the effect replaces the battle
viewport with a 320x166-TEXEL copy of itself ... roughly 1:4 here ... That is
the effect's own texel budget, capped by 8-bit UVs, not something the geometry
work could reach."

The 4x is real. The cause is not the UVs:

    ff7nx_fbsurf   scales fb_tex with k; tex_format stays at the authored size
    ff7nx_gpucap   makes the GPU render target `tex_format`, not `fb_tex`

so wave 1 renders into 256 x 256 texels and is then magnified across 1012 x 747
screen pixels -- 3.95 px per texel. Waves 2 and 3 are 128 texels over 252
pixels, i.e. 1.97, which is why the right-hand fifth always looked sharper than
the rest.

The mapping is invariant to `tex_w`, so raising the AUTHORED size buys texels
and cannot move the picture -- `ff7nx_fbsize`'s "more texels, same UVs", per
effect instead of globally:

    +0x70F930  mov w20, #0x100  ->  mov w20, #0x200     cap1.w, tex_w 256->512
    +0x70F944  mov w8,  #0xa6   ->  mov w8,  #0x14c     cap1.h, tex_h 256->512

`tools/ifrit_verify.py` measures it: 3.95 px/texel -> 1.98, with the drawn
region, the sampled region and the 1:1 map all byte-identical.

Both sites are in the PROLOGUE, which every mode arm falls through to and only
mode 1 overrides (with `rect.x`/`rect.y`, not with a size). w20 and w8 are
re-defined before their next read at +0x70F958 and +0x70F97C, asserted below.

The render target goes 256 KB -> 1 MB. `ff7nx_gpucap` must be ON for that to
be the whole cost: with it off the target is `fb_tex`, which this makes
1024 x 1536 before the surface scale. `verify` refuses if gpucap is off.

WHAT THIS DOES NOT TOUCH
========================
xscale stays 2, so Ifrit remains outside `ff7nx_fbcapture`'s origin correction
and `ff7nx_fbresample`'s gate, the same way the battle-entry swirl and Escape
are. cap1's `field_0` is 0 whenever `[0x9ACB90]` is zero and cap2/cap3 set it
unconditionally, so these captures are render-to-texture and none of the
CPU-branch modules (`fbwindow`, `fbfit`, `fbresample`) sees them.
"""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import nxmap                                                    # noqa: E402

IFRIT_X86 = 0x595A05
SRC_ENV = 'SEVENTH_NX_FX_IFRIT_SRC'

RECT_X = 0x9AAD4C                  # rect.y is RECT_X + 4
MUL_W8 = 0x1B097D08               # mul w8, w8, w9        v * [0x9AD1A8]
ADD_W8_X3 = 0x0B080508            # add w8, w8, w8, lsl #1     v * 3
FRAME0_GATE = 0x70EAA8            # cbz w8, #0x70EBB8 -- the build-pass gate
FRAME0_GATE_WORD = 0x34000888   # cbz w8, #0x70EBB8

LDR_W21 = 0xB9400015               # ldr w21, [x0]
LDR_W19 = 0xB9400013               # ldr w19, [x0]
MOV_W21_3 = 0x52800075             # mov w21, #3     MOVZ; 3 is not a valid
MOV_W19_3 = 0x52800073             # mov w19, #3     logical immediate

# yscale: (va, stock, patched, the register, where it is next re-defined)
YSCALE_SITES = (
    (0x7113E0, LDR_W21, MOV_W21_3, 'w21', 0x711410),
    (0x711644, LDR_W21, MOV_W21_3, 'w21', 0x711674),
    (0x7118C8, LDR_W19, MOV_W19_3, 'w19', 0x7118F8),
)

# cap3.y, and cap1's authored w/h.  (va, stock, patched, what)
IMM_SITES = (
    (0x711168, 0x52801614, 0x52802114, 'cap3.y 176 -> 264'),
    # `mov w20, #0x100` and `mov w20, #0x200` are ORR-IMMEDIATES, not MOVZ --
    # both values are single-bit logical patterns, so the assembler picks the
    # cheaper form. Hand-rolling a MOVZ here is exactly the bug build 406
    # shipped in escapescale's revert. The patched word is not computed: it is
    # copied verbatim from +0x711140, which is the binary's own
    # `mov w20, #0x200` on the same register.
    (0x70F930, 0x321803F4, 0x321703F4, 'cap1.w 0x100 -> 0x200'),
    (0x70F944, 0x528014C8, 0x52802988, 'cap1.h 0xA6 -> 0x14C'),
)
TALL_SITES = tuple(s[0] for s in YSCALE_SITES) + (0x711168,)
SHARP_SITES = (0x70F930, 0x70F944)

# Must not have moved. The xscale stores prove we took the second load of the
# pair; the other arms' cap3.y prove we are in the mode-2 one.
ANCHORS = {
    0x7113B0: 0x11043100,      # add w0, w8, #0x10c   cap1 xscale
    0x711614: 0x11043100,      # cap2 xscale
    0x711898: 0x11043100,      # cap3 xscale
    0x7113E4: 0x11044100,      # add w0, w8, #0x110   cap1 yscale store
    0x711648: 0x11044100,      # cap2
    0x7118CC: 0x11044100,      # cap3
    0x71110C: 0x52800B14,      # mov w20, #0x58       mode 0's cap3.y
    0x70F9B4: 0x52801608,      # mov w8,  #0xb0       the prologue default
    0x711140: 0x321703F4,      # mov w20, #0x200      cap2.x / cap3.x, and
                               #   the source of the patched word above
    0x70F958: 0x321703F5,      # mov w21, #0x200      the prologue's cap2.x
    0x70F97C: 0x52800836,      # mov w22, #0x41       cap2.w / cap3.w
    0x70F990: 0x52800B17,      # mov w23, #0x58       cap2.h / cap3.h
}


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


# The six build-pass y multiplies. Eighteen `mul w8, w8, w9` in this function
# are byte-identical -- six finish a y, twelve finish an x -- and the registers
# that hold rect.x and rect.y are REUSED with different meanings across the
# body (w21 is rect.y at +0x70FA60 and rect.x at +0x70FBB4, w22 likewise), so
# pattern matching on them is unsafe. Each site therefore carries the four
# words after it as its witness. The three "first of a wave" sites are followed
# by a fresh 0x9AAD4C materialisation and `add w22, w8, #4` -- rect.y, which is
# ff7nx_escapescale's discriminator -- and the three "second of a wave" sites
# reuse that w22 through `mov w0, w22`.
YMUL_SITES = {
    0x70EDE4: (0xB9000B08, 0x5295A988, 0x72A01348, 0x11001116),
    0x70EE94: (0x2A1603E0, 0xB9000708, 0x9427B540, 0xB9400008),
    0x70F0E0: (0xB9000708, 0x5295A988, 0x72A01348, 0x11001116),
    0x70F190: (0x2A1603E0, 0xB9000308, 0x9427B481, 0xB9400008),
    0x70F514: (0xB9000708, 0x5295A988, 0x72A01348, 0x11001116),
    0x70F5C8: (0x2A1603E0, 0xB9000308, 0x9427B373, 0xB9400008),
}

# The twelve draw-pass x multiplies. They must stay `mul` -- they are the ones
# the cave's own transform is layered on, and they are what makes x idempotent
# per frame. Listed so that a build which hit one of these instead fails here.
XMUL_GUARD = {va: 0x2A1503E0 for va in (          # mov w0, w21   (rect.x)
    0x70FEB8, 0x70FF28, 0x70FF98, 0x710008,
    0x7105A0, 0x710610, 0x710680, 0x7106F0,
    0x710BDC, 0x710C4C, 0x710CBC, 0x710D2C)}


def check_ymul(img) -> list:
    """The six sites, their witnesses, the twelve x muls, and the frame-0 gate
    that makes the distinction matter at all."""
    bad = []
    if _w(img, FRAME0_GATE) != FRAME0_GATE_WORD:
        bad.append('the frame-0 gate at +0x%X is %08X, expected %08X -- the '
                   'build/draw split this module depends on has moved'
                   % (FRAME0_GATE, _w(img, FRAME0_GATE), FRAME0_GATE_WORD))
    for va, wit in sorted(YMUL_SITES.items()):
        have = _w(img, va)
        if have not in (MUL_W8, ADD_W8_X3):
            bad.append('y multiply +0x%X is %08X, neither `mul w8, w8, w9` '
                       'nor `add w8, w8, w8, lsl #1`' % (va, have))
        for k, want in enumerate(wit, start=1):
            if _w(img, va + 4 * k) != want:
                bad.append('y multiply +0x%X witness +%d is %08X, expected '
                           '%08X' % (va, 4 * k, _w(img, va + 4 * k), want))
    for va, want in sorted(XMUL_GUARD.items()):
        if _w(img, va) != MUL_W8:
            bad.append('x multiply +0x%X is %08X, not `mul w8, w8, w9` -- the '
                       'draw pass no longer rebuilds x, so the cave\'s '
                       'transform would compound' % (va, _w(img, va)))
        if _w(img, va + 4) != want:
            bad.append('x multiply +0x%X is not followed by `mov w0, w21`'
                       % va)
    if set(YMUL_SITES) & set(XMUL_GUARD):
        bad.append('a multiply is listed as both x and y')
    return bad


def enabled() -> bool:
    """ON, following ff7nx_battlewide's ifrit mode.

    `SEVENTH_NX_FX_IFRIT_SRC=0` leaves the captures stock, which -- because
    the height half is three quarters of this module -- also means the warp
    must not be told to reach 480. That pairing is enforced in `verify`, not
    left to the reader.
    """
    v = os.environ.get(SRC_ENV)
    if v is None:
        return True
    return v.strip().lower() not in ('0', 'off', 'false', 'no')


def wanted():
    """(tall, sharp) -- what should be installed."""
    import ff7nx_battlewide as BW
    if not enabled():
        return False, False
    return BW.ifrit_tall(), BW.ifrit_enabled()


def installed(img):
    """(tall, sharp) or None for a mixed state."""
    t = [_w(img, va) == pat for va, _st, pat, _r, _d in YSCALE_SITES]
    t.append(_w(img, 0x711168) == 0x52802114)
    t += [_w(img, va) == ADD_W8_X3 for va in YMUL_SITES]
    s = [_w(img, va) == pat for va, _st, pat, _what in IMM_SITES
         if va in SHARP_SITES]
    if not (all(t) or not any(t)):
        return None
    if not (all(s) or not any(s)):
        return None
    return all(t), all(s)


def verify(img) -> list:
    bad = list(check_ymul(img))
    for va, want in sorted(ANCHORS.items()):
        if _w(img, va) != want:
            bad.append('ifrit src anchor +0x%X is %08X, expected %08X -- the '
                       'capture setup has moved' % (va, _w(img, va), want))
    for va, stock, pat, reg, redef in YSCALE_SITES:
        if _w(img, va) not in (stock, pat):
            bad.append('yscale site +0x%X is %08X, neither `ldr %s, [x0]` nor '
                       '`mov %s, #3`' % (va, _w(img, va), reg, reg))
        if _w(img, redef) != stock:
            bad.append('+0x%X is %08X: %s is no longer re-loaded before its '
                       'next read, so a literal at +0x%X could leak'
                       % (redef, _w(img, redef), reg, va))
    for va, stock, pat, what in IMM_SITES:
        if _w(img, va) not in (stock, pat):
            bad.append('%s site +0x%X is %08X, neither value this module '
                       'writes' % (what, va, _w(img, va)))
    if installed(img) is None:
        bad.append('the height and texel words are in a mixed state')
    tall, sharp = wanted()
    if tall and not enabled():
        bad.append('the warp is set to reach 480 but this module is off; the '
                   'geometry would stretch without the sampling')
    if sharp:
        try:
            import ff7nx_gpucap as GC
            if not GC.enabled():
                bad.append('ff7nx_gpucap is off, so the GPU target is fb_tex '
                           'and raising the authored size would make it '
                           '1024x1536 before the surface scale')
        except ImportError:
            pass
    return bad


def read_state(img) -> str:
    st = installed(img)
    if st is None:
        return 'mixed'
    tall, sharp = st
    return '%s, %s' % ('yscale 3 (reaches 480)' if tall else 'yscale stock',
                       '512x512 texels' if sharp else '256x256 texels')


def plan(m, revert=False):
    problems = verify(m.img)
    if problems:
        return [], [], problems
    tall, sharp = (False, False) if revert else wanted()
    patches, notes = [], []
    for va, stock, pat, reg, _redef in YSCALE_SITES:
        want = pat if tall else stock
        have = _w(m.img, va)
        if have != want:
            patches.append({'name': 'ifrit capture yscale -> %s'
                                    % ('3' if tall else 'stock'),
                            'va': hex(va),
                            'expect': struct.pack('<I', have).hex(),
                            'set': struct.pack('<I', want).hex()})
    for va in sorted(YMUL_SITES):
        want = ADD_W8_X3 if tall else MUL_W8
        have = _w(m.img, va)
        if have != want:
            patches.append({'name': 'ifrit build-pass y = v * %d'
                                    % (3 if tall else 2),
                            'va': hex(va),
                            'expect': struct.pack('<I', have).hex(),
                            'set': struct.pack('<I', want).hex()})
    for va, stock, pat, what in IMM_SITES:
        on = tall if va not in SHARP_SITES else sharp
        want = pat if on else stock
        have = _w(m.img, va)
        if have != want:
            patches.append({'name': 'ifrit %s' % what, 'va': hex(va),
                            'expect': struct.pack('<I', have).hex(),
                            'set': struct.pack('<I', want).hex()})
    if patches:
        notes.append('  Ifrit captures: %s'
                     % (('yscale 3, cap3.y 264 and build-pass y = v*3 -- '
                         'geometry and sampling both reach staging row 498'
                         if tall else 'yscale stock')
                        + (', wave 1 authored 0x200x0x14C -- 512x512 target '
                           'texels instead of 256x256' if sharp else '')))
    return patches, notes, []


def apply(main, revert=False, log=print) -> int:
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ifrit src: %s' % p)
        log('  refusing to touch Ifrit\'s capture descriptors.')
        return 1
    for n in notes:
        log(n)
    if not patches:
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_ifritsrc', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.ifritsrc-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d Ifrit capture word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply(a.main, revert=a.revert))
