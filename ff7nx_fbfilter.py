#!/usr/bin/env python3
r"""
ff7nx_fbfilter.py -- stop the capture blit INVENTING texels at the silhouette.

TWO WORDS, IN PLACE, NO CAVE.

WHAT MAKES THE BLACK SQUARES
============================
The scene target is cleared to (0,0,0,0) every frame (+0x10DAB40, s0..s3 all
zero) and the 3D layer is drawn onto it. It has hard edges -- no multisampling
-- so every pixel in it is either fully drawn or untouched:

    drawn      (C, 255)
    untouched  (0, 0, 0, 0)

Then +0x10DACB0 blits that whole target into the capture surface, and the
sampler it sets is LINEAR:

    +0x10DAD94   ldr x8, [x8, #0x30]     the filter setter
    +0x10DAD98   mov w1, #1              min    = LINEAR
    +0x10DAD9C   mov w2, #1              mag    = LINEAR
    +0x10DADA4   mov w3, wzr             mip    = POINT

That same vtable slot is called with (0, 0, 0) at +0x10DBEE8 in the other blit
in this file, which is what fixes 0 = POINT and 1 = LINEAR.

A LINEAR blit at a silhouette averages drawn pixels with untouched ones, so it
manufactures texels that exist nowhere in the scene: dark, partly covered, and
*between* the two categories the capture path knows how to handle. The readback
copies them verbatim and the field draws them. That is the fringe, and it is
what the black squares have always been made of.

WHY THIS IS THE RIGHT PLACE TO FIX IT
=====================================
Everything the artifact does follows from the filter's FOOTPRINT, which is the
one thing that changes when the capture surface is resized:

    one texel wide        it is the footprint
    follows the edge      it is the silhouette
    MOVED when build 310  the footprint moved with the surface
      scaled the surface
    SHRANK with the        it is measured in texels, not world units
      texels
    survived `punch`      a filtered texel is dark, not exactly zero, so the
                            exactly-zero rule does not see it

Repairing it downstream means guessing which dark texels were invented and
which are real, which is a threshold -- a dial, and the wrong kind of fix.
Turning the filter off removes the invention instead:

    **with POINT sampling every capture texel is exactly one scene pixel**,
    so it is either real content or the cleared void, and nothing in between.

That is also what makes `ff7nx_fbresample`'s `punch` exactly right rather than
lucky: once no texel is a blend, "colour is zero" identifies the void with no
threshold in it at all. The two are a pair.

WHAT IT COSTS
=============
The blit becomes a point resample of the scene into the capture surface. At
SEVENTH_NX_FB_SURFACE=2 that is 1280x960 from a 1280x720 frame -- horizontally
1:1 -- so there is very little to lose and the capture gets crisper. At the
stock 640x480 it drops pixels rather than averaging them, which aliases
slightly; the mesh magnifies the result either way, and the fringe is the
larger of the two evils by a distance.

It touches ONLY the blit that feeds [0x12CE628], the readback texture. That
texture has three code sites in the whole binary -- its creation, the CPU
loader and the GPU capture path -- and nothing displayed on screen reads it.
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

FILTER_ENV = 'SEVENTH_NX_FB_FILTER'

MIN_SITE = 0x10DAD98               # mov w1, #1     minification filter
MIN_STOCK = 0x320003E1             # orr w1, wzr, #1  -- the logical-immediate
MAG_SITE = 0x10DAD9C               # mov w2, #1        form, not a movz
MAG_STOCK = 0x320003E2

# The call these two are arguments to, the address-mode call just above it, and
# the render-target switch just below -- so "this is the scene -> capture blit"
# is checked rather than assumed.
ANCHORS = {
    0x10DAD90: 0xF9400288,         # ldr  x8, [x20]
    0x10DAD94: 0xF9401908,         # ldr  x8, [x8, #0x30]    the filter setter
    0x10DADA0: 0xAA1403E0,         # mov  x0, x20
    0x10DADA4: 0x2A1F03E3,         # mov  w3, wzr            mip stays POINT
    0x10DADA8: 0xD63F0100,         # blr  x8
    0x10DAD7C: 0x321F03E1,         # mov  w1, #2  \
    0x10DAD80: 0x321F03E2,         # mov  w2, #2   >  address mode = CLAMP
    0x10DAD84: 0x321F03E3,         # mov  w3, #2  /
    0x10DAE1C: 0xAA1303E2,         # mov  x2, x19   the readback texture, as
    0x10DAE24: 0xD63F0100,         # blr  x8        SetRenderTargets' argument
}

# The same vtable slot, called with POINT in the sibling blit. This is the
# evidence that 0 is POINT and 1 is LINEAR, and it is asserted so that a
# different build of the executable cannot quietly invert the meaning.
POINT_WITNESS = {
    0x10DBEE0: 0xF9401908,         # ldr  x8, [x8, #0x30]   the same setter
    0x10DBEE8: 0x2A1F03E1,         # mov  w1, wzr
    0x10DBEEC: 0x2A1F03E2,         # mov  w2, wzr
    0x10DBEF0: 0x2A1F03E3,         # mov  w3, wzr
}

POINT, LINEAR = 0, 1
DEFAULT_FILTER = POINT


def _mov_wzr(rd):
    """MOV Wd, WZR -- ORR Wd, WZR, WZR. The exact form the sibling blit uses
    to ask for POINT at +0x10DBEE8, so this writes the encoding the binary
    itself already carries rather than a second spelling of zero."""
    return 0x2A1F03E0 | rd


# LINEAR is the stock pair, verbatim; POINT is the witness pair, verbatim.
WORDS = {
    LINEAR: {MIN_SITE: MIN_STOCK, MAG_SITE: MAG_STOCK},
    POINT: {MIN_SITE: _mov_wzr(1), MAG_SITE: _mov_wzr(2)},
}

assert WORDS[POINT][MIN_SITE] == POINT_WITNESS[0x10DBEE8]
assert WORDS[POINT][MAG_SITE] == POINT_WITNESS[0x10DBEEC]


def words_for(mode):
    """{site: word}. LINEAR reproduces the stock image exactly."""
    return dict(WORDS[mode])


def filter_mode() -> int:
    v = os.environ.get(FILTER_ENV)
    if v is None:
        return DEFAULT_FILTER
    v = v.strip().lower()
    if v in ('point', 'nearest', 'off', '0'):
        return POINT
    if v in ('linear', 'stock', 'on', '1'):
        return LINEAR
    return DEFAULT_FILTER


def enabled() -> bool:
    return filter_mode() != LINEAR


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def installed(img):
    got = {va: _word(img, va) for va in words_for(LINEAR)}
    for mode in (POINT, LINEAR):
        if words_for(mode) == got:
            return mode
    return None


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        have = _word(img, va)
        if have != want:
            bad.append('anchor +0x%X is %08X, expected %08X'
                       % (va, have, want))
    for va, want in sorted(POINT_WITNESS.items()):
        have = _word(img, va)
        if have != want:
            bad.append('the POINT witness at +0x%X is %08X, expected %08X -- '
                       'without it, 0 vs 1 is a guess' % (va, have, want))
    if installed(img) is None:
        bad.append('+0x%X/+0x%X are %08X/%08X, which is neither filter this '
                   'build writes'
                   % (MIN_SITE, MAG_SITE, _word(img, MIN_SITE),
                      _word(img, MAG_SITE)))
    return bad


def read_state(img) -> str:
    mode = installed(img)
    if mode is None:
        return 'unknown'
    return ('capture blit POINT sampled' if mode == POINT
            else 'capture blit LINEAR (stock)')


def plan(m_, revert=False):
    img = m_.img
    problems = verify(img)
    if problems:
        return [], [], problems
    mode = LINEAR if revert else filter_mode()
    want = words_for(mode)
    name = 'POINT' if mode == POINT else 'LINEAR'
    patches, notes = [], []
    for va in sorted(want):
        have = _word(img, va)
        if have == want[va]:
            continue
        what = '%s filter -> %s' % ('min' if va == MIN_SITE else 'mag', name)
        patches.append({'name': what, 'va': hex(va),
                        'expect': struct.pack('<I', have).hex(),
                        'set': struct.pack('<I', want[va]).hex()})
        notes.append('    %s' % what)
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'capture blit filter', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fbfilter-')
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
        log('  refusing to change the capture blit filter.')
        return 1
    mode = LINEAR if revert else filter_mode()
    log('  capture blit filter (%s): %s'
        % (FILTER_ENV, 'POINT' if mode == POINT else 'LINEAR (stock)'))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d filter word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
