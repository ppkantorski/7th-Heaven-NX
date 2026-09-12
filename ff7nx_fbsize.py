#!/usr/bin/env python3
r"""
ff7nx_fbsize.py -- give the framebuffer capture MORE TEXELS without changing
what its UVs mean. TWO WORDS, IN PLACE, NO CAVE.

THE THING THE TV DREW
=====================
> "this field should be long and extending... with matching pixel density,
>  extending far behind the enemies"

Build 275 shrank the disc, which fixed the density and wrecked the coverage.
Build 276's remap then made the coverage worse still. Both were the wrong
lever, because coverage and density trade off directly when the texel budget
is fixed:

    254 texels over 2.63 screens  =  13.0 px per texel   (stock coverage)
    254 texels over 1.31 screens  =   6.6 px per texel   (build 275)

You cannot have a long field AND fine texels out of 254 texels. You need MORE
TEXELS.

WHY MORE TEXELS IS FREE
=======================
The UV bytes are 0..255 and the port turns them into texture coordinates with

    u_offset = 1 / tex_format.width          (tex_header +0x3C)

so `u * u_offset` spans [0, 1) of the texture **whatever the texture's real
pixel size is**. `fb_tex.w` / `fb_tex.h` (+0x1C / +0x20) are the real size:
the allocation, the row stride, the copy and the texture object all come from
them, and NOTHING reads them as a UV scale.

So doubling fb_tex.w/h while leaving tex_format.width alone gives the effect a
512x512 texture addressed by the same 256-step UV space: **twice the texels,
same coverage, same UVs, no stretching.**

This is exactly what FFNx does. `Renderer::createBlitTexture` builds the
capture at `getInternalCoordX(w) x getInternalCoordY(h)` -- the internal
resolution -- while `make_framebuffer_tex` keeps `tex_format.width` at the
authored 256. This port is the only one that ties the two together.

THE TWO WORDS
=============
Both are shifts in the port's own fb_tex.w/h arithmetic, so the size travels
everywhere it needs to with nothing else to keep in step:

    ARM +0x10DBC6C   lsr x8, x8, #0x28    fb_tex.h = h * 480 / 480
    ARM +0x10DBC88   lsr x9, x9, #0x29    fb_tex.w = w * 640 / 640

Taking one off each shift doubles the result.

    1   stock     256 x 256    13.0 px per texel at stock disc coverage
    2   double    512 x 512     6.6 px per texel      <- the default
    4   quadruple 1024 x 1024   3.3 px per texel

At 2 the capture is 1 MB and the density is build 275's, which the TV called
"matching closer", but with the disc back at full size so the field is long
again.

WHAT HAS TO MOVE WITH IT
========================
`ff7nx_fbresample`'s 'frame' mode, because:

  * its gate is `fb_tex.w == tex_format.width`, which is now `== k *
    tex_format.width`;
  * the copy's own loop bounds are `min(surface, x + w) - x`, which cannot
    reach 512 out of a 640-wide surface -- the cave sets the counts from
    fb_tex.w / fb_tex.h instead, so every texel is written and none is left
    black;
  * its source step halves, because there are twice as many texels covering
    the same screen.

The cave does all three, gated, so a page-scaled capture (the battle-entry
swirl) still runs the stock loop untouched.
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

import nxmap                                                   # noqa: E402

SIZE_ENV = 'SEVENTH_NX_FB_SIZE'

H_SITE = 0x10DBC6C                 # lsr x8, x8, #0x28   -> fb_tex.h
H_STOCK = 0xD368FD08
H_SHIFT = 0x28
W_SITE = 0x10DBC88                 # lsr x9, x9, #0x29   -> fb_tex.w
W_STOCK = 0xD369FD29
W_SHIFT = 0x29

LEGAL = (1, 2, 4)

# RETRACTED, BUILD 278. This module changes fb_tex.w/h for EVERY framebuffer
# capture in the game, because the "1:1 vs page-scaled" distinction it relied on
# does not exist: all twenty-five capture creators set xscale = yscale = 1.
# It doubled the battle-entry swirl's texture while the stock loop that fills
# that texture still used min(surface, origin+size) - origin bounds, so most of
# the swirl's texture was never written. THAT IS WHAT BROKE THE SWIRL.
# ff7nx_fxcapscale does the same job through Kujata's own xscale/yscale, which
# touches one effect. This is OFF and stays off.
DEFAULT_SIZE = 1

# The stores these two feed, and the x/y shifts right next to them that must
# NOT move -- the origin stays in staging coordinates.
ANCHORS = {
    0x10DBC70: 0xD369FD6B,         # lsr x11, x11, #0x29   fb_tex.x  (untouched)
    0x10DBC74: 0xD368FD29,         # lsr x9,  x9,  #0x28   fb_tex.y  (untouched)
    0x10DBC78: 0x2902A74B,         # stp w11, w9, [x26, #0x14]   x, y
    0x10DBC90: 0x2907DF58,         # stp w24, w23, [x26, #0x3c]  tex_format w,h
    0x10DBC94: 0x2903A349,         # stp w9,  w8,  [x26, #0x1c]  fb_tex w, h
}


def _lsr64(rd, rn, shift):
    """LSR Xd, Xn, #shift -- the UBFM alias, same form as the stock words."""
    return 0xD3400000 | (1 << 22) | (shift << 16) | (63 << 10) | (rn << 5) | rd


assert _lsr64(8, 8, H_SHIFT) == H_STOCK
assert _lsr64(9, 9, W_SHIFT) == W_STOCK


def words_for(k):
    """(height word, width word). k = 1 keeps the stock encodings exactly."""
    drop = {1: 0, 2: 1, 4: 2}[k]
    return _lsr64(8, 8, H_SHIFT - drop), _lsr64(9, 9, W_SHIFT - drop)


def size() -> int:
    v = os.environ.get(SIZE_ENV)
    if v is None:
        return DEFAULT_SIZE
    try:
        n = int(v, 0)
    except ValueError:
        return DEFAULT_SIZE
    return n if n in LEGAL else DEFAULT_SIZE


def enabled() -> bool:
    return size() != 1


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X' % (va, got, want))
    known = {words_for(k) for k in LEGAL}
    got = (_word(img, H_SITE), _word(img, W_SITE))
    if got not in known:
        bad.append('+0x%X/+0x%X are %08X/%08X, which is no capture size this '
                   'build writes' % (H_SITE, W_SITE, got[0], got[1]))
    return bad


def read_state(img) -> str:
    got = (_word(img, H_SITE), _word(img, W_SITE))
    for k in LEGAL:
        if words_for(k) == got:
            return 'capture %dx%s' % (k, ' (stock)' if k == 1 else '')
    return 'unknown'


def plan(m_, revert=False):
    img = m_.img
    problems = verify(img)
    if problems:
        return [], [], problems
    k = 1 if revert else size()
    want = words_for(k)
    patches, notes = [], []
    for va, wd, what in ((H_SITE, want[0], 'capture height x%d' % k),
                         (W_SITE, want[1], 'capture width x%d' % k)):
        have = _word(img, va)
        if have == wd:
            continue
        patches.append({'name': what, 'va': hex(va),
                        'expect': struct.pack('<I', have).hex(),
                        'set': struct.pack('<I', wd).hex()})
        notes.append('    %s' % what)
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'framebuffer capture texel count',
                  'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fbsize-')
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
        log('  refusing to change the capture texel count.')
        return 1
    log('  framebuffer capture texel count (%s=%d, stock is 1):'
        % (SIZE_ENV, 1 if revert else size()))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d capture size word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
