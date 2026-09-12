#!/usr/bin/env python3
r"""
ff7nx_fxscale.py -- shrink Kujata's animated-field DISC so its texture stops
being magnified nine times. TWO WORDS, IN PLACE, NO CAVE.

WHAT THE BUILD-273 PROBE MEASURED
=================================
The probe filled the capture texture with its own texel coordinates, so every
pixel the effect drew reported which texel it was showing.  Decoded (marker
channel 54.4/64, so u = B/0.85, v = G/0.85), grass stage:

    screen x      60     320     640     960    1240
    texel u      174     156     131     105      85      <- what it shows
    needed       -66     106     320     533     720      <- to match the floor

**89 texels across 1180 pixels where 786 are needed.**  13 screen pixels per
texel at mid-screen, 8.2 near the horizon, and the same 2.9x story vertically.
That is the "zoomed in like crazy", and it is also the whole of the "low
quality": no capture resolution survives a 13x magnification.

WHY IT IS 13x, AND WHAT THAT MEANS
==================================
The disc and its UVs are locked together by the mesh builder:

    R = 384 * j     (x86 0x500E3D, ARM +0x473244/+0x473248)   j = 0..32
    r = 4 * j       (the UV radius)      =>  ONE TEXEL = 96 WORLD UNITS

So the disc's full 254-texel span covers 24384 world units -- and the probe
says those 254 texels come out **2.63 screen widths** across.  The texture that
has to fill them is a capture of the frame, and a capture can never hold more
than ONE screen.  The disc is simply too big on screen for its own texture.

THE EXPERIMENT
==============
`SEVENTH_NX_FX_SCALE` is the shift that builds the ring radius.

    7   stock            R = 384*j   outer 12192   2.63 screens   13.0 px/texel
    6   half             R = 192*j   outer  6096   1.31 screens    6.6 px/texel
    5   quarter          R =  96*j   outer  3048   0.66 screens    3.3 px/texel

Both words move together -- the ring multiplier AND the j = 32 outer-ring
constant, which is 127 * (R per texel) and has to keep matching or the last
ring's geometry and its UVs disagree.

    6 is the default here.  The field should come out visibly SHARPER and
    TIGHTER, covering roughly the screen instead of overflowing it.

WHAT THIS DOES **NOT** DO
=========================
It does not align the picture.  The capture still holds the top-left 256 game
units of the frame -- 0.30 of a screen -- so after this the disc spans 1.31
screens against a texture holding 0.30, i.e. still 4.4x out instead of 8.8x.
Alignment needs the SECOND half: the capture stepping across the whole 854x480
frame instead of 256 game units, and mirrored, because the probe shows u falls
as screen x rises.  That is a change to the resample cave and it is next.

This build is the half that can be tested on its own, in one variable, and it
is the half that carries the sharpness.
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

import a64 as A                                                # noqa: E402
import nxmap                                                   # noqa: E402

SCALE_ENV = 'SEVENTH_NX_FX_SCALE'

SHIFT_SITE = 0x473248              # lsl w26, w8, #7      R = (3j) << 7 = 384j
SHIFT_STOCK = 0x5319611A
OUTER_SITE = 0x473178              # mov w23, #0x2fa0     the j = 32 ring
OUTER_STOCK = 0x5285F417

STOCK_SHIFT = 7
LEGAL = (5, 6, 7)

# BUILD 277: BACK TO STOCK. Shift 6 did exactly what it promised -- the TV
# confirmed "matching closer to the pixel density" -- but shrinking the disc
# also shrank the COVERAGE, and they then drew what the field has to cover:
# the whole battlefield, running far behind the enemies. Coverage and density
# trade off directly at a fixed texel budget, so the density now comes from
# ff7nx_fbsize (more texels) instead of from here (less disc).
DEFAULT_SHIFT = STOCK_SHIFT

# 12192 is 127 * 96, and 96 is the world units per texel that the shift sets.
# Keep the identity rather than tabulating, so the two words cannot drift.
def outer_for(shift):
    return 127 * (3 << shift) // 4


assert outer_for(STOCK_SHIFT) == 0x2FA0

# The instructions that reach these two, so the build refuses rather than
# writing into a routine a game update moved.
ANCHORS = {
    0x473244: 0x0B080508,          # add w8, w8, w8, lsl #1   w8 = 3j
    0x47324C: 0xB94016A8,          # ldr w8, [x21, #0x14]
    0x473230: 0x2A1703FA,          # mov w26, w23            the j = 32 arm
    0x473174: 0x320013F6,          # mov w22, #0x1f          ring loop bound
    0x473180: 0x128270F9,          # mov w25, #-0x1388       z = -5000
}


def _lsl(rd, rn, shift):
    """LSL Wd, Wn, #shift, via a64 so the encoder suite covers it."""
    return A.lsl(rd, rn, shift)


def _movz(rd, imm16):
    return 0x52800000 | ((imm16 & 0xFFFF) << 5) | rd


def words_for(shift):
    """(shift word, outer word). Stock keeps its own encodings exactly."""
    if shift == STOCK_SHIFT:
        return SHIFT_STOCK, OUTER_STOCK
    return _lsl(26, 8, shift), _movz(23, outer_for(shift))


def shift() -> int:
    v = os.environ.get(SCALE_ENV)
    if v is None:
        return DEFAULT_SHIFT
    try:
        n = int(v, 0)
    except ValueError:
        return DEFAULT_SHIFT
    return n if n in LEGAL else DEFAULT_SHIFT


def enabled() -> bool:
    return shift() != STOCK_SHIFT


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X' % (va, got, want))
    known = {words_for(s) for s in LEGAL}
    got = (_word(img, SHIFT_SITE), _word(img, OUTER_SITE))
    # BUILD 294: ff7nx_fxdisc owns the same two words when it is on, replacing
    # the shift with a branch into a multiply cave. Either state is legitimate;
    # the two modules are mutually exclusive and the driver applies fxscale
    # first, so this only has to recognise fxdisc's hook and stand aside.
    if got not in known and (got[0] & 0xFC000000) != 0x14000000:
        bad.append('+0x%X/+0x%X are %08X/%08X, which is neither a scale this '
                   'build writes nor a branch into ff7nx_fxdisc\'s cave'
                   % (SHIFT_SITE, OUTER_SITE, got[0], got[1]))
    return bad


def read_state(img) -> str:
    got = (_word(img, SHIFT_SITE), _word(img, OUTER_SITE))
    for s in LEGAL:
        if words_for(s) == got:
            return ('ring radius %d*j, outer %d%s'
                    % ((3 << s) // 4 * 4, outer_for(s),
                       ' (stock)' if s == STOCK_SHIFT else ''))
    return 'unknown'


def plan(m_, revert=False):
    img = m_.img
    problems = verify(img)
    if problems:
        return [], [], problems
    s = STOCK_SHIFT if revert else shift()
    want = words_for(s)
    patches, notes = [], []
    for va, wd, what in ((SHIFT_SITE, want[0], 'ring radius shift %d' % s),
                         (OUTER_SITE, want[1], 'outer ring %d' % outer_for(s))):
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
            nso, {'name': 'Kujata field disc scale', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fxscale-')
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
        log('  refusing to change the field disc scale.')
        return 1
    log('  Kujata field disc scale (%s=%d, stock is %d):'
        % (SCALE_ENV, STOCK_SHIFT if revert else shift(), STOCK_SHIFT))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d disc scale word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
