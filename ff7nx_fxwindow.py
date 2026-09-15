#!/usr/bin/env python3
r"""
ff7nx_fxwindow.py -- the snapshot window is half the size the effect was
authored for. TWO WORDS, IN PLACE, NO CAVE.

THIS IS THE PC-ERA BUG THE PORT INHERITED
=========================================
The operator's test settled the biggest question in this investigation: an
UNMODIFIED dump has the warping, the stretching and the black edges. Nothing
of ours causes it. But footage of the PSX/Steam original does not show it, so
the defect entered somewhere between the two -- and this is where.

**The PSX original.** Kujata's disc is textured from a PSX TEXTURE PAGE, and
its UVs are bytes 0..255. A 16-bit page is 256 VRAM pixels wide, and the PSX
screen is 320x240 in that same VRAM. So the window the effect photographs is

    256 / 320  =  80 % of the screen width
    256 / 240  = 107 % of the height

**FF7 PC, which is what this port recompiled.** `game_width` is the window
width -- 640 -- and the effect still asks for 256:

    x86 0x500858    [0x9ACB5C] == 2  ->  rect 256 x 256
                    otherwise        ->  rect 128 x 128

    +0x471F88   mov  w9, #0x100          256      <-- THE WORD (live)
    +0x471F94   mov  w8, #0x80           128      <-- the other mode
    +0x471F98   csel w21, w9, w8, eq
    +0x471FDC   strh w21, [0x87B436]     rect.h
    +0x471FF8   strh w20, [0x87B434]     rect.w, copied from it

    256 / 640 = 40 %      128 / 320 = 40 %

**Both PC modes are 40 %. PSX was 80 %. The window was halved.** That is not
this port's doing and it is not FFNx's -- I read the reference implementation
to be sure it was not being corrected downstream, and it is not:

    make_framebuffer_tex()          common.cpp:2699   stores x,y,w,h RAW
    createBlitTexture(x,y,w,h)      renderer.cpp      newW = x*fbW/854 ...
    blitTexture(...)                renderer.cpp      ... = w * scalingFactor

`getInternalCoordX` divides by `wide_viewport_width` = 854 while
`framebufferWidth` = `wide_game_width` * S = 854 * S, so it is `w * S` --
an identity in game units, exactly like this port's `w * 640 / 640`. FFNx and
the port agree with each other and with FF7 PC. All three are half of PSX.

WHY HALVING THE WINDOW LOOKS LIKE EVERYTHING THE OPERATOR DESCRIBED
===================================================================
The disc's world size is a constant -- `R = 96 * r`, read out of ff7_en -- so
its screen footprint does not change when the window does. Only the amount of
picture spread over it does. Measured against the operator's own texel probe:

    the disc's footprint            1835 x 465 device px
    the window, at 256              384 x 384 device px    -> 4.78x across
    the window, at 480              720 x 720 device px    -> 2.55x across

A 4.78x magnification of a screen photograph, pasted on the ground, is:

    blurry                     four texels of screen per texel of picture
    stretched                  and by different amounts in the two axes
    centre off                 a scale error of s throws every off-centre
                               feature by (s-1) * its distance
    running onto ground it     the disc reaches to the horizon; the picture
        is not an image of     it holds stops 4.78x short

All four of the operator's symptoms, from one number. And it is why no dial
ever reached them: `fxdisc`, `fxscale`, `fxdepth` and `fxorigin` all move the
disc, and the disc was never the thing that was wrong.

WHY 480 AND NOT 512
===================
512 is PSX's fraction exactly (512/640 = 80 %), but 512 game units of a
480-unit-tall frame overflows the bottom: the copy takes
`min(480k, y + h) - y = 480k` rows into a 512k-tall texture and the remaining
6.25 % is never written, which reads black at the disc's rim -- the exact
class of artefact builds 245-266 spent twenty builds on.

480 is the largest window that fits the frame in BOTH axes:

    width    480 / 640 = 75 %   (PSX 80 %)     the clamp is 80k of 160k, fits
    height   480 / 480 = 100 %  (PSX 107 %)    exactly full, nothing unwritten
    square   fb_tex.w == fb_tex.h              the resample gate's assumption
                                               and fxcapscale's both hold

and it keeps the texture square, which four modules in this project depend on.

COST
====
`fb_tex` becomes 480k x 480k. At k = 4 that is 1920 x 1920 x 4 = 14.7 MB
against 4.2 MB today. k = 8 died at 78 MB, so 14.7 is inside what has worked,
but it has not been executed on hardware and k = 2 (3.7 MB, the same as
today) is the safe home if it misbehaves.
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

WIN_ENV = 'SEVENTH_NX_FX_WINDOW'

HI_SITE = 0x471F88                 # mov w9, #0x100    the live, mode-2 value
HI_STOCK = 0x321803E9
HI_REG = 9
LO_SITE = 0x471F94                 # mov w8, #0x80     the other render mode
LO_STOCK = 0x321903E8
LO_REG = 8

STOCK_HI, STOCK_LO = 256, 128

# BUILD 370: OFF. Executed on hardware at 480 and the field came back BLACK
# and the game ran slow. Both are size, not geometry: fb_tex becomes
# 1920x1920 at k=4 and the CPU readback copies 3.7M texels per capture instead
# of 1.0M -- 3.5x the per-frame work, and an allocation the loader's
# `cbz w0` path treats as failure. So the window is not reachable at k=4 even
# if the reasoning about it were right.
#
# AND THE REASONING IS PROBABLY NOT RIGHT. The model that produced "4.78x
# magnified" predicts the field is a four-times blow-up of a screen
# photograph. The operator's own screenshots do not look like that -- the
# grass on the field is close to the grass on the floor, not four times
# coarser. A prediction that visibly fails is a dead model, and every number
# in this thread that came out of composing FIT_HUV with an ASSUMED capture
# layout inherits it. That includes 4.78, 1835x465, and this module's whole
# argument.
#
# Kept, dialable, and off, because the PSX-vs-PC window fraction is a real
# finding even though acting on it was not a fix.
DEFAULT_WINDOW = 256

# below 256 is smaller than stock and pointless; above 480 overflows the frame
WIN_MIN, WIN_MAX = 256, 480

ANCHORS = {
    0x471F78: 0x7100091F,   # cmp  w8, #2             the render-mode test
    0x471F7C: 0x1A9F17E8,   # cset w8, eq
    0x471F98: 0x1A880135,   # csel w21, w9, w8, eq    <-- picks between them
    0x471FD0: 0x11009A74,   # add  w20, w19, #0x26    &rect.h
    0x471FDC: 0x79000015,   # strh w21, [x0]          rect.h = the window
    0x471FE8: 0x79400014,   # ldrh w20, [x0]          read it back ...
    0x471FEC: 0x11009260,   # add  w0, w19, #0x24     ... &rect.w
    0x471FF8: 0x79000014,   # strh w20, [x0]          rect.w = the same value
    # and the two sites ff7nx_fxsnap owns, so the two modules cannot collide
    0x471FA0: 0x79400017,   # ldrh w23, [x0]          rect.x
    0x471FBC: 0x79400014,   # ldrh w20, [x0]          rect.y
}

# The consumer, so a game update that changes how the rect becomes a texture
# stops this module rather than letting it write into different arithmetic.
CONSUMER = {
    0x10DBC7C: 0x0B150AA9,   # add  w9, w21, w21, lsl #2    w * 5
    0x10DBC94: 0x2903A349,   # stp  w9, w8, [x26, #0x1c]    fb_tex.w, .h
    0x10D6E6C: 0x9A8A0128,   # csel x8, x9, x10, eq  &fb_tex.w : &tex_format.w
}


def _movz32(rd, imm16):
    """MOVZ Wd, #imm16.

    Note the stock words are the ORR-with-bitmask-immediate form of `mov`
    (0x32...), not MOVZ -- 256 and 128 are both expressible as logical
    immediates and the compiler chose that. MOVZ says the same thing in one
    word for any 16-bit value, so `words_for` emits MOVZ and the stock
    encodings are kept verbatim for the revert. Both forms are recognised.
    """
    return 0x52800000 | ((imm16 & 0xFFFF) << 5) | rd


def _decode_mov(word, rd):
    """The immediate a `mov Wd, #imm` at this site carries, or None.

    Accepts the two stock ORR words and any MOVZ to the right register --
    which is exactly the set this module can ever have written.
    """
    if word == HI_STOCK and rd == HI_REG:
        return STOCK_HI
    if word == LO_STOCK and rd == LO_REG:
        return STOCK_LO
    if (word & 0xFFE0001F) == (0x52800000 | rd):
        return (word >> 5) & 0xFFFF
    return None


assert _decode_mov(HI_STOCK, HI_REG) == STOCK_HI
assert _decode_mov(LO_STOCK, LO_REG) == STOCK_LO
assert _decode_mov(_movz32(HI_REG, 480), HI_REG) == 480


def window() -> int:
    v = os.environ.get(WIN_ENV)
    if v is None:
        return DEFAULT_WINDOW
    if v.strip().lower() in ('stock', 'off', 'none', ''):
        return STOCK_HI
    try:
        n = int(v, 0)
    except ValueError:
        return DEFAULT_WINDOW
    return n if WIN_MIN <= n <= WIN_MAX else DEFAULT_WINDOW


def enabled() -> bool:
    return window() != STOCK_HI


def words_for(hi: int) -> dict:
    """Both render modes keep their 2:1 relationship, so the low-resolution
    path stays the same fraction of its own frame. `hi == 256` returns the
    stock encodings byte for byte, so a revert restores the image exactly."""
    if hi == STOCK_HI:
        return {HI_SITE: HI_STOCK, LO_SITE: LO_STOCK}
    return {HI_SITE: _movz32(HI_REG, hi),
            LO_SITE: _movz32(LO_REG, hi // 2)}


assert words_for(STOCK_HI) == {HI_SITE: HI_STOCK, LO_SITE: LO_STOCK}
assert _decode_mov(words_for(480)[HI_SITE], HI_REG) == 480


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def verify(img) -> list:
    bad = []
    for name, table in (('anchor', ANCHORS), ('consumer', CONSUMER)):
        for va, want in sorted(table.items()):
            got = _word(img, va)
            if got != want:
                bad.append('%s +0x%X is %08X, expected %08X'
                           % (name, va, got, want))
    for va, rd in ((HI_SITE, HI_REG), (LO_SITE, LO_REG)):
        got = _word(img, va)
        if _decode_mov(got, rd) is None:
            bad.append('+0x%X is %08X, which is not a `mov w%d, #imm` this '
                       'module could have written' % (va, got, rd))
    return bad


def read_state(img) -> str:
    hi = _decode_mov(_word(img, HI_SITE), HI_REG)
    lo = _decode_mov(_word(img, LO_SITE), LO_REG)
    return ('snapshot window %dx%d (low-res mode %d)%s'
            % (hi, hi, lo, ' (stock)' if (hi, lo) == (STOCK_HI, STOCK_LO)
               else ''))


def plan(m, revert=False):
    img = m.img
    problems = verify(img)
    if problems:
        return [], [], problems
    want = words_for(STOCK_HI if revert else window())
    patches = []
    for va in sorted(want):
        have = _word(img, va)
        if have == want[va]:
            continue
        patches.append({'name': 'snapshot window (%s)'
                        % ('mode 2' if va == HI_SITE else 'low-res mode'),
                        'va': hex(va),
                        'expect': struct.pack('<I', have).hex(),
                        'set': struct.pack('<I', want[va]).hex()})
    notes = []
    if patches:
        n = STOCK_HI if revert else window()
        notes.append('    window %d -> %d game units: the capture holds '
                     '%.0f%% of the frame instead of %.0f%%, so the field is '
                     'magnified %.2fx instead of %.2fx'
                     % (STOCK_HI, n, 100.0 * n / 640, 100.0 * STOCK_HI / 640,
                        4.78 * STOCK_HI / n, 4.78))
        notes.append('    fb_tex becomes %d x %d at k=4 (%.1f MB)'
                     % (n * 4, n * 4, (n * 4) ** 2 * 4 / 1e6))
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'snapshot window', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fxwindow-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply_all(main, revert=False, log=print):
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to change the snapshot window.')
        return 1
    log('  snapshot window (%s, stock is %d):' % (WIN_ENV, STOCK_HI))
    for n in notes:
        log(n)
    if not patches:
        log('    already %s' % read_state(m.img))
        return 0
    _write(main, patches, log)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--state', action='store_true')
    a = ap.parse_args(argv)
    if a.state:
        m = nxmap.Main(a.main)
        print(read_state(m.img))
        for p in verify(m.img):
            print('  ! ' + p)
        return 0
    return apply_all(a.main, revert=a.revert)


if __name__ == '__main__':
    sys.exit(main())
