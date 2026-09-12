#!/usr/bin/env python3
r"""
ff7nx_fbsurf.py -- raise the framebuffer capture's RESOLUTION CEILING.

FIVE WORDS, IN PLACE, NO CAVE.

WHERE THE SHARPNESS GOES (FINDINGS 308, PLAN 309)
=================================================
Traced out of the binary, not inferred:

    [0x12CE610]  the scene target      display resolution  (+0x10D58A4)
        |
        |   +0x10DACB0: ONE full-target quad, UVs (0,0)-(1,1)
        v
    [0x12CE628]  the capture surface   created +0x10D5970 with LITERAL 640x480
        |
        |   +0x10D71F4: CPU memcpy, 1:1, no filter
        v
      the capture texture              fb_tex.w x fb_tex.h

Two consequences, and they are the whole problem:

  * **640x480 is the ceiling.** Whatever the console renders at, this path
    resamples it to 640x480 before anything else happens. Nothing downstream
    can put the detail back.
  * **the blit is anamorphic** -- the quad covers the whole target with UVs
    0..1, so a 16:9 frame is squeezed into a 4:3 texture. That is exactly
    where ff7nx_fbresample's 640/854 step comes from. It is the inverse of
    this blit, it is a ratio, and it stays correct at any surface size.

THE FIVE WORDS
==============
The surface's size and the rect arithmetic that addresses it are both built
from literal constants, so scaling all of them by one factor is five single
word patches -- nothing to allocate, nothing to link.

    +0x10D5970   mov  x8, #0x280            -> #0x280 * k   surface width
    +0x10D5974   movk x8, #0x1e0, lsl #32   -> #0x1e0 * k   surface height
    +0x10DBBF0   orr  w13, wzr, #0x1e0      -> movz #0x1e0*k   rect y AND h
    +0x10DBC54   lsl  w11, w11, #7          -> #7 + log2(k)    rect x
    +0x10DBC80   lsl  w9,  w9,  #7          -> #7 + log2(k)    rect w

WHY THE RECT SITES ARE THE RIGHT ONES
=====================================
`make_framebuffer_tex` already computes every rect field as

    field = value * K / virtual

where the divide is a reciprocal multiply by 640 or 480 (+0x10DBC58,
+0x10DBC6C, +0x10DBC84, +0x10DBC68) and the multiply is one of the constants
above. Stock, K *is* the virtual size, so the whole thing is an identity: the
port runs a full multiply-and-divide to scale the rect by 1.0, and always has.
Raising only the multiply turns that identity into the scale a larger surface
needs. The shape is already right -- nobody ever put a number in it.

Both `lsl #7` sites are preceded by `add wD, wS, wS, lsl #2` (x5), so the pair
is x640; the 480 constant in w13 is shared by the y and the h multiply, which
is why one word covers both.

WHAT IT BUYS, AND WHAT DOES NOT MOVE
====================================
At k = 2 the surface is 1280x960 and Kujata's rect is 512x512:

  * twice the source detail -- the scene resamples to 1280x960, not 640x480;
  * four times the texels -- fb_tex.w/h scale with the rect, so the copy has
    somewhere to put them;
  * **the UVs do not change.** tex_format.width (+0x3C) comes from the
    caller's authored 256 and is untouched, and it is the only field anything
    reads as a UV scale. 512 texels addressed by the same 256-step UV space:
    same coverage, same geometry, same disc, no stretching. That is exactly
    what ff7nx_fbsize does with a shift -- obtained here as a consequence
    rather than as a second lever, so fbsize stays at 1 and is not needed;
  * the anamorphic step is unchanged, because 640/854 is a ratio and both
    terms scale together.

Cost: the capture buffer goes 256 KB -> 1 MB at k = 2, and the readback memcpy
with it. Once per summon.

BLAST RADIUS
============
[0x12CE620] and [0x12CE628] have three code sites each and all three are in
this path: the creation above, the CPU loader (+0x10D7050) and the GPU path
(+0x10D7240). Nothing else in the game reads this surface. Every capture in
the game -- the battle-entry swirl included -- scales by the same factor from
the same three instructions, so framing is preserved and the detail is gained
everywhere.

WHAT HAS TO MOVE WITH IT
========================
ff7nx_fbresample gates on `fb_tex.w == tex_format.width`, which is true only at
k = 1; at k = 2 it reads 512 == 256 and the resample would silently disengage,
bringing the anamorphic squeeze straight back. That module reads `surf_scale()`
from here for its gate shift and for SURF_W/SURF_H, so the two cannot drift.
"""
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

import nxmap                                                   # noqa: E402

SURF_ENV = 'SEVENTH_NX_FB_SURFACE'

VIRT_W, VIRT_H = 640, 480

# the surface the capture reads, created from a literal 640x480 descriptor
SW_SITE = 0x10D5970                # mov  x8, #0x280
SW_STOCK = 0xD2805008
SH_SITE = 0x10D5974                # movk x8, #0x1e0, lsl #32
SH_STOCK = 0xF2C03C08

# the rect arithmetic in make_framebuffer_tex
RH_SITE = 0x10DBBF0                # orr  w13, wzr, #0x1e0   (y and h multiply)
RH_STOCK = 0x321B0FED
RX_SITE = 0x10DBC54                # lsl  w11, w11, #7       (x multiply)
RX_STOCK = 0x5319616B
RW_SITE = 0x10DBC80                # lsl  w9,  w9,  #7       (w multiply)
RW_STOCK = 0x53196129

LEGAL = (1, 2, 4)
DEFAULT_SCALE = 4

# Everything the five words feed, asserted unchanged. If the routine moves,
# these stop matching and the module refuses rather than writing into whatever
# is at those addresses now.
ANCHORS = {
    0x10D5978: 0xF90003E8,         # str  x8, [sp]        the descriptor
    0x10D5984: 0x97BCBAD7,         # bl   #0x44e0         create the texture
    0x10D5994: 0xB9000100,         # str  w0, [x8]        -> [0x12CE620]
    0x10DBC3C: 0x0B1B0B6B,         # add  w11, w27, w27, lsl #2      x * 5
    0x10DBC40: 0x1B0D7F29,         # mul  w9,  w25, w13              y * 480
    0x10DBC58: 0x9BAC7D6B,         # umull x11, w11, w12   reciprocal /640
    0x10DBC64: 0x1B0D7E6A,         # mul  w10, w19, w13              h * 480
    0x10DBC6C: 0xD368FD08,         # lsr  x8, x8, #0x28              /480
    0x10DBC7C: 0x0B150AA9,         # add  w9,  w21, w21, lsl #2      w * 5
    0x10DBC84: 0x9BAC7D29,         # umull x9, w9, w12     reciprocal /640
    0x10DBC88: 0xD369FD29,         # lsr  x9, x9, #0x29              /640
    0x10DBC78: 0x2902A74B,         # stp  w11, w9, [x26, #0x14]   x, y
    0x10DBC94: 0x2903A349,         # stp  w9,  w8, [x26, #0x1c]   w, h
}


def _movz64(rd, imm16):
    """MOVZ Xd, #imm16."""
    return 0xD2800000 | (imm16 << 5) | rd


def _movk64(rd, imm16, hw):
    """MOVK Xd, #imm16, lsl #(16*hw)."""
    return 0xF2800000 | (hw << 21) | (imm16 << 5) | rd


def _movz32(rd, imm16):
    """MOVZ Wd, #imm16."""
    return 0x52800000 | (imm16 << 5) | rd


def _lsl32(rd, rn, shift):
    """LSL Wd, Wn, #shift -- the UBFM alias, the form the stock word uses."""
    immr = (32 - shift) & 31
    imms = 31 - shift
    return 0x53000000 | (immr << 16) | (imms << 10) | (rn << 5) | rd


assert _movz64(8, VIRT_W) == SW_STOCK
assert _movk64(8, VIRT_H, 2) == SH_STOCK
assert _lsl32(11, 11, 7) == RX_STOCK
assert _lsl32(9, 9, 7) == RW_STOCK


def words_for(k):
    """The five words, in site order. k = 1 reproduces the stock encodings --
    except RH_SITE, whose stock form is an ORR logical immediate rather than a
    MOVZ, so k = 1 is written as the stock word verbatim."""
    shift = {1: 7, 2: 8, 4: 9}[k]
    return {
        SW_SITE: _movz64(8, VIRT_W * k),
        SH_SITE: _movk64(8, VIRT_H * k, 2),
        RH_SITE: RH_STOCK if k == 1 else _movz32(13, VIRT_H * k),
        RX_SITE: _lsl32(11, 11, shift),
        RW_SITE: _lsl32(9, 9, shift),
    }


assert words_for(1) == {SW_SITE: SW_STOCK, SH_SITE: SH_STOCK,
                        RH_SITE: RH_STOCK, RX_SITE: RX_STOCK,
                        RW_SITE: RW_STOCK}


def scale() -> int:
    v = os.environ.get(SURF_ENV)
    if v is None:
        return DEFAULT_SCALE
    try:
        n = int(v, 0)
    except ValueError:
        return DEFAULT_SCALE
    return n if n in LEGAL else DEFAULT_SCALE


def surf_scale() -> int:
    """What ff7nx_fbresample reads, so the gate and SURF_W/H cannot drift."""
    return scale()


def surface() -> tuple:
    k = scale()
    return VIRT_W * k, VIRT_H * k


def enabled() -> bool:
    return scale() != 1


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def installed(img):
    """The k the image carries, or None if it is not a state this build
    writes."""
    got = {va: _word(img, va) for va in words_for(1)}
    for k in LEGAL:
        if words_for(k) == got:
            return k
    return None


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        have = _word(img, va)
        if have != want:
            bad.append('anchor +0x%X is %08X, expected %08X'
                       % (va, have, want))
    if installed(img) is None:
        bad.append('+0x%X..+0x%X are no capture surface this build writes: %s'
                   % (SW_SITE, RW_SITE,
                      ' '.join('%08X' % _word(img, va)
                               for va in sorted(words_for(1)))))
    return bad


def read_state(img) -> str:
    k = installed(img)
    if k is None:
        return 'unknown'
    return 'capture surface %dx%d (%dx%s)' % (VIRT_W * k, VIRT_H * k, k,
                                              ', stock' if k == 1 else '')


def plan(m_, revert=False):
    img = m_.img
    problems = verify(img)
    if problems:
        return [], [], problems
    k = 1 if revert else scale()
    want = words_for(k)
    patches, notes = [], []
    what = {SW_SITE: 'surface width %d' % (VIRT_W * k),
            SH_SITE: 'surface height %d' % (VIRT_H * k),
            RH_SITE: 'rect y and h x%d' % k,
            RX_SITE: 'rect x x%d' % k,
            RW_SITE: 'rect w x%d' % k}
    for va in sorted(want):
        have = _word(img, va)
        if have == want[va]:
            continue
        patches.append({'name': what[va], 'va': hex(va),
                        'expect': struct.pack('<I', have).hex(),
                        'set': struct.pack('<I', want[va]).hex()})
        notes.append('    %s' % what[va])
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'framebuffer capture surface', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fbsurf-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply_all(main, revert=False, log=print):
    m_ = nxmap.Main(str(main))
    patches, notes, problems = plan(m_, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to change the capture surface.')
        return 1
    k = 1 if revert else scale()
    log('  framebuffer capture surface (%s=%d, stock is 1): %dx%d'
        % (SURF_ENV, k, VIRT_W * k, VIRT_H * k))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d capture surface word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
