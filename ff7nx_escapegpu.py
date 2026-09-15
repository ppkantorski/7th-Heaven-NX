#!/usr/bin/env python3
r"""
ff7nx_escapegpu.py -- take Escape's two framebuffer snapshots off the CPU
readback, which is where the doubling and the hitch both come from.

FOUR WORDS, IN PLACE, NO CAVE.

WHAT ESCAPE ACTUALLY ASKS FOR -- READ, NOT INFERRED
===================================================
`escape_setup` (x86 0x5D577B, ARM +0x83B460) writes a two-rect descriptor into
guest stack slots and then copies it into two display-list objects. The
prologue sets the defaults and names every field:

    ebp-0x14 = 0      A.x        ebp-0x0C = 0x140  B.x = 320
    ebp-0x12 = 0      A.y        ebp-0x0A = 0      B.y
    ebp-0x10 = 0xA0   A.w = 160  ebp-0x08 = 0xA0   B.w = 160
    ebp-0x0E = 0xA8   A.h = 168  ebp-0x06 = 0xA8   B.h = 168
    ebp-0x04 = 2                 <-- the SCALE, xscale and yscale both

then three mutually exclusive branches on the graphics mode `[0x9ACB5C]`:

    mode 0      B.x = 160,            scale 1          +0x83B62C
    mode 1      A = (rect.x, rect.y), B.x = rect.x+160, scale 1   +0x83B59C
    otherwise   the defaults above,   scale 2          +0x83B63C

**This port runs at mode 2.** That is not a new finding: it is what
`ff7nx_fbcapture`, `ff7nx_fxsnap` and FINDINGS-362 are all built on -- Kujata's
rect is 256x256, which is the `[0x9ACB5C] == 2` value of the same switch, and
the whole black-tile chain was solved against that number on hardware.

So Escape's real descriptor is

    A = (  0, 0, 160, 168)  scale 2
    B = (320, 0, 160, 168)  scale 2

BUILD 402's PROBE WAS PATCHING A BRANCH THAT DOES NOT RUN. It set mode 1's
B.x to 320 and its scale to 2 -- i.e. it wrote, into dead code, the values
mode 2 already has. That is why the result was not any of the three outcomes
it predicted.

WHAT THAT DESCRIPTOR MEANS, END TO END
======================================
The chain is `escape_setup` -> object renderer 0x682DB2 -> 0x674418 ->
0x673F5C -> native thunk +0x10F2380 -> `make_framebuffer_tex` +0x10DBB50 ->
the loader +0x10D7050. Every link is a direct disassembly, and the two places
earlier sessions went wrong are both on it:

  * 0x682DB2 **does** reach `make_framebuffer_tex`: +0xB54248 `bl #0xb02910`
    is x86 0x674418, whose only call to +0xB015C0 is x86 0x673F5C. FINDINGS-402
    concluded the opposite from 0x673F5C's caller count and everything built
    on "Escape's capture is a path this project has never hooked" followed from
    that.
  * 0x682DB2 rounds the rect's w and h UP TO POWERS OF TWO on the way
    (+0xB53F34 / +0xB54014 call x86 0x690240 / 0x690270), so Escape's authored
    160 x 168 becomes **tex_w = tex_h = 256** before the thunk sees it.

The thunk (+0x10F2380) then unpacks struc_91 as

    field_0 = s[0x00]   x = s[0x04]   y = s[0x08]
    tex_w   = s[0x0C]   tex_h = s[0x10]
    w = s[0x14] * tex_w        h = s[0x18] * tex_h

and `make_framebuffer_tex` stores `fb_tex = (x, y, w, h)` with
`tex_format = (tex_w, tex_h)`. Only `tex_format.width` is read as a UV
divisor (`ff7nx_fbsize`, `ff7nx_fbsurf`), so a mesh UV `u` lands on

    staging column  =  fb_tex.x + u * xscale

because `u / tex_w * (xscale * tex_w)` cancels `tex_w` exactly. Escape's mesh
addresses u = 0..160 per tile, so

    A covers staging columns   0 .. 320        (   0 + 160*2 )
    B covers staging columns 320 .. 640        ( 320 + 160*2 )

and the staging surface holds the WHOLE 16:9 frame in 640 columns
(`ff7nx_fbcapture` 2-4). **The two tiles tile the entire frame exactly.** This
is the same arrangement FINDINGS-313 measured for the battle-entry swirl, and
for the same reason: the capture side was correct all along.

THE DOUBLING IS ff7nx_fbwindow, ON A RECT THAT IS MEANT TO OVERHANG
===================================================================
`fb_tex.w` is not 320. It is `xscale * tex_w` = `2 * 256` = **512**, because
0x682DB2 rounded 160 up to 256. The mesh only ever addresses 320 of those
columns -- the rest of the texture is deliberately unaddressed page space,
exactly as `ff7nx_fbcapture` describes for `xscale > 1`:

    xscale >  1   PSX page arithmetic ... the rect is deliberately larger than
                  the frame -- the effect's own UVs address only the
                  meaningful corner of the result.

`ff7nx_fbwindow` (+0x10D7138, ON by default) does not know that. Its rule is

    if (fb_tex.x + fb_tex.w > surface_w)  fb_tex.x = max(0, surface_w - fb_tex.w)

written for 1:1 captures whose UVs run the full 0..255 and would therefore read
unwritten texels off a short copy. Applied to Escape's capture B:

    320 + 512 = 832  >  640      ->      B.x := 640 - 512 = 128

so **capture B comes back holding staging columns 128..448 instead of
320..640** -- slid left by 192 staging columns = 256 game units. Capture A
fits (0 + 512 <= 640) and is untouched. The mesh then draws

    left  half   staging   0..320   =  game  -107 .. 320      correct
    right half   staging 128..448   =  game    64 .. 491      shifted

and game x 64..320 -- 30% of the screen -- is drawn TWICE, with a hard
discontinuity down the middle. That is "the same image appearing twice, left
half and right half, with a huge disconnect at the center", as arithmetic.

`fbwindow`'s own header says "the battle swirl's two half-rects fit; they are
untouched", and they do: the swirl builds its struc_91 by hand with tex_w =
160, so its `fb_tex.w` is 320 and 320 + 320 = 640 exactly. Escape is the only
capture in the game that goes through 0x682DB2's power-of-two rounding AND has
xscale > 1, so it is the only one this bites.

THE HITCH IS THE SAME ONE ff7nx_swirlgpu FIXED
==============================================
The loader branches on `field_0` at +0x10D70B0 (`cmp w8, #1`):

    field_0 == 1   CPU readback  +0x10D70B8   alloc fb_tex.w*fb_tex.h*4, then
                                              a byte-at-a-time swizzle loop
    field_0 != 1   render to texture +0x10D7240

x86 0x682D80 -- the generic battle-effect-object initialiser, shared by
Kujata, Titan, Alexander and KOTR -- stores `field_0 = 1` at +0xB52F74. With
`ff7nx_fbsurf` at k = 4 every rect field scales by four, so Escape's two
captures are 2048 x 2048 each:

    16 MB allocated, and 2048 x 1920 pixels copied a byte at a time    x2

per cast. That is "it takes a long time for this effect to kick in, like when
battle swirl had a high K value" -- it is literally the same cost on the same
loop, and `ff7nx_swirlgpu` already took the swirl off it. (It is also the most
likely reason build 402's tile came back as noise rather than as a picture: a
failed 16 MB allocation leaves +0x10D71B0 `cbz w0` skipping the copy entirely,
and an uninitialised texture is exactly what noise on one tile looks like.)

ONE CHANGE FIXES BOTH
=====================
`fbwindow`'s hook, `fbfit`'s (+0x10D71C0) and `fbresample`'s (+0x10D71F0) are
ALL inside the CPU branch. Routing Escape's two captures down the GPU path
therefore bypasses the slide, the 16 MB allocation and the byte loop together,
and touches nothing Kujata uses -- Kujata keeps `field_0 = 1`, the CPU path,
`punch`, the resample and k = 4.

The GPU path needs no clamp because it computes its source window from the
rect and the surface and stretches it into the target (+0x10D7478):

    u0 = x / surf_w        u1 = (x + w) / surf_w
    v0 = y / surf_h        v1 = (y + h) / surf_h

For capture B that is u 0.5..1.3 -- it overhangs, and the overhang is simply
never addressed, because the mesh reaches only u = 160 of tex_w = 256, i.e.
0.625 of the target, i.e. source u = 0.5 + 0.625*0.8 = **1.0 exactly**, the
right edge of the frame. With `ff7nx_gpucap` on the target is `tex_format`
rather than `fb_tex`, which the same cancellation leaves the mapping identical.

THE FOUR WORDS
==============
`field_0` is `struc_91[0x00]`, copied by 0x682DB2 from **object +0x108**
(+0xB53E40 `add w0, w8, #0x108` -> +0xB53E60). `escape_setup` never writes
+0x108, so 0x682D80's 1 survives. It does, however, write **object +0xD0
twice**, and the first store is dead:

    +0x83B7B0  add w0, w8, #0xd0     \  obj[0xD0] = 1     <-- DEAD
    +0x83B7BC  str w20, [x0]         /
    +0x83B7D0  add w0, w8, #0xd0     \  obj[0xD0] = 4     the live value
    +0x83B7DC  mov w8, #4            |
    +0x83B7E0  str w8,  [x0]         /

Nothing between the two reads +0xD0; the only intervening calls are the
address translator. So the dead store is re-pointed instead of removed:

    +0x83B7B0  add w0, w8, #0xd0  ->  add w0, w8, #0x108     object A
    +0x83B7BC  str w20, [x0]      ->  str wzr,   [x0]
    +0x83BA1C  add w0, w8, #0xd0  ->  add w0, w8, #0x108     object B
    +0x83BA28  str w20, [x0]      ->  str wzr,   [x0]

Neither encoding is invented. `add w0, w8, #0x108` is the binary's own idiom
two fields along (+0x83B8EC is `#0x10c`, +0x83B924 is `#0x110`), and
`str wzr, [x0]` is the exact word `escape_setup` already uses at +0x83B754 and
+0x83B9C0. Both are asserted as witnesses below, so a different executable
makes this module refuse rather than guess.

WHY NOT THE OTHER TWO CANDIDATE SITES
=====================================
Build 408's `escapegpu` repurposed +0x83B754 / +0x83B9C0 -- `str wzr, [x0]`,
i.e. `obj[0x00] = 0` -- into `str wzr, [x0, #0x108]`. That reaches the right
field, but it DELETES the object's own `+0x00 = 0` store and relies on
0x6831EF's memset to have left it zero. Its header also states that the
generic initialiser sets this field and that `escape_setup` never writes it;
the disassembly above shows +0x83B754 IS that write. Re-pointing a provably
dead store keeps both.

Patching `fbwindow` to skip page-scaled captures would also fix the doubling
and is arguably the more general repair -- its gate should be
`fb_tex.w == k * tex_format.width`, the same 1:1 test `ff7nx_fbcapture` and
`ff7nx_fbresample` already use. It is deliberately NOT done here: that is a
shared cave on Kujata's own path, this change is not, and this one fixes the
hitch as well. The latent bug is recorded rather than fixed.

WHAT IS NOT IN THIS MODULE
==========================
The 4:3 squeeze and the black side margins are the 2D overlay's WS_SCALE =
0.75, and `ff7nx_escapescale` fixes them -- it ships with this.

The vertical stop at game y 336 is NOT fixed, and it is not a bug of the same
kind. The mesh is 22 rows of 8 raw units at scale 2 = 336, the capture covers
v = 0..168 at yscale 2 = staging rows 0..336, and the two agree exactly: the
effect is 1:1 over the top 336 rows with its ripple origin at raw (160, 120)
= screen (320, 240), the true centre. Reaching 480 means scaling the geometry
by 10/7 while the radial origin stays at raw row 120 of 168 -- i.e. 71% down
the mesh -- so it buys coverage by moving the ripple's centre to screen y 343
or by making the rings elliptical. Build 407 shipped the first of those and
the report was "the ripple does not appear centered, the image is focused
higher". See ff7nx_escapetall, which is now OFF.
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

ESCAPEGPU_ENV = 'SEVENTH_NX_ESCAPE_GPU'

# guest -> the two capture objects' handle globals, for the record
A_HANDLE = 0x8FF0E8
B_HANDLE = 0x8FF0EC

# The dead `obj[0xD0] = 1` store in each of the two object setups.
ADD_D0 = 0x11034100                # add w0, w8, #0xd0
ADD_108 = 0x11042100               # add w0, w8, #0x108
STR_W20 = 0xB9000014               # str w20, [x0]
STR_WZR = 0xB900001F               # str wzr, [x0]

SITES = {
    # object A
    0x83B7B0: (ADD_D0, ADD_108),
    0x83B7BC: (STR_W20, STR_WZR),
    # object B
    0x83BA1C: (ADD_D0, ADD_108),
    0x83BA28: (STR_W20, STR_WZR),
}
ORDER = (0x83B7B0, 0x83B7BC, 0x83BA1C, 0x83BA28)
WHICH = {0x83B7B0: 'A', 0x83B7BC: 'A', 0x83BA1C: 'B', 0x83BA28: 'B'}

# Everything that proves these are Escape's two capture-object setups, that
# the store being re-pointed is the dead one, and that the encodings written
# are the binary's own.
ANCHORS = {
    # --- the descriptor this module's reasoning rests on -------------------
    0x83B4C4: 0x52801414,      # mov  w20, #0xa0     A.w / B.w = 160
    0x83B4D8: 0x52801513,      # mov  w19, #0xa8     A.h / B.h = 168
    0x83B4EC: 0x52802816,      # mov  w22, #0x140    B.x = 320  (mode 2)
    0x83B530: 0x321F03E8,      # mov  w8,  #2        the default scale
    0x83B568: 0xB9400009,      # ldr  w9,  [x0]      [0x9ACB5C], the mode
    0x83B58C: 0x7100051F,      # cmp  w8,  #1        mode 1 test
    0x83B6B4: 0x321F03F4,      # mov  w20, #2        mode >= 2 scale
    # --- object A: the dead store and the live one it precedes -------------
    0x83B7A0: 0xB94016A8,      # ldr  w8,  [x21, #0x14]
    0x83B7A4: 0x51006100,      # sub  w0,  w8, #0x18   the object-ptr slot
    0x83B7AC: 0xB9400008,      # ldr  w8,  [x0]        w8 = object A
    0x83B7B4: 0xB9000AA8,      # str  w8,  [x21, #8]
    0x83B7C0: 0xB94016A8,      # ldr  w8,  [x21, #0x14]
    0x83B7C4: 0x51006100,      # sub  w0,  w8, #0x18
    0x83B7CC: 0xB9400008,      # ldr  w8,  [x0]
    0x83B7D0: ADD_D0,          # add  w0,  w8, #0xd0   the LIVE +0xD0 store
    0x83B7DC: 0x321E03E8,      # mov  w8,  #4          its value
    0x83B7E0: 0xB9000008,      # str  w8,  [x0]
    # --- object B: the same shape -----------------------------------------
    0x83BA0C: 0xB94016A8,
    0x83BA10: 0x51006100,
    0x83BA18: 0xB9400008,
    0x83BA20: 0xB90002A8,      # str  w8,  [x21]
    0x83BA2C: 0xB94016A8,
    0x83BA30: 0x51006100,
    0x83BA38: 0xB9400008,
    0x83BA3C: ADD_D0,          # the LIVE +0xD0 store
    0x83BA48: 0x321E03E8,
    0x83BA4C: 0xB9000008,
    # --- where STR_WZR and the +0x108 idiom come from ---------------------
    0x83B754: STR_WZR,         # obj[0x00] = 0, object A   (build 408's site)
    0x83B9C0: STR_WZR,         # obj[0x00] = 0, object B
    0x83B8EC: 0x11043100,      # add w0, w8, #0x10c   xscale, one field along
    0x83B924: 0x11044100,      # add w0, w8, #0x110   yscale, two along
}


def enabled() -> bool:
    """ON by default. `SEVENTH_NX_ESCAPE_GPU=0` puts Escape back on the CPU
    readback, which is the A/B for both the doubling and the hitch."""
    v = os.environ.get(ESCAPEGPU_ENV)
    if v is None:
        return True
    # Same spelling as ff7nx_swirlgpu, deliberately: an empty value does NOT
    # count as "off" here. This is a default-on fix and a stray `VAR=` in a
    # shell wrapper must not silently put Escape back on the CPU readback.
    return v.strip().lower() not in ('0', 'off', 'false', 'no',
                                     'cpu', 'stock')


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def installed(img):
    """True = GPU path, False = stock CPU readback, None = neither."""
    got = tuple(_word(img, va) for va in ORDER)
    if got == tuple(SITES[va][0] for va in ORDER):
        return False
    if got == tuple(SITES[va][1] for va in ORDER):
        return True
    return None


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        have = _word(img, va)
        if have != want:
            bad.append('escape_setup anchor +0x%X is %08X, expected %08X -- '
                       'Escape\'s capture setup has moved' % (va, have, want))
    if installed(img) is None:
        bad.append('+%s are %s, which is neither path this module writes'
                   % ('/'.join('0x%X' % v for v in ORDER),
                      '/'.join('%08X' % _word(img, v) for v in ORDER)))
    return bad


def read_state(img) -> str:
    st = installed(img)
    if st is None:
        return 'unknown'
    return ('Escape captures render to texture (GPU)' if st else
            'Escape captures on the CPU readback (stock)')


def plan(m, revert=False):
    problems = verify(m.img)
    if problems:
        return [], [], problems
    gpu = enabled() and not revert
    idx = 1 if gpu else 0
    patches, notes = [], []
    for va in ORDER:
        have = _word(m.img, va)
        want = SITES[va][idx]
        if have == want:
            continue
        patches.append({
            'name': 'Escape capture %s field_0 -> %s'
                    % (WHICH[va], 'GPU' if gpu else 'CPU readback'),
            'va': hex(va),
            'expect': struct.pack('<I', have).hex(),
            'set': struct.pack('<I', want).hex(),
        })
    if patches:
        notes.append('    captures A and B -> %s'
                     % ('render-to-texture' if gpu else 'CPU readback'))
    return patches, notes, []


def apply_all(main, revert=False, log=print):
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to change Escape\'s capture path.')
        return 1
    gpu = enabled() and not revert
    log('  Escape framebuffer snapshots (%s): %s'
        % (ESCAPEGPU_ENV, 'GPU render-to-texture' if gpu
           else 'CPU readback (stock)'))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_escapegpu', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.escapegpu-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d Escape capture-path word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
