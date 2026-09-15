#!/usr/bin/env python3
r"""
ff7nx_fxorigin.py -- move Kujata's field to the battlefield's own centre.

ONE WORD, IN PLACE, NO CAVE.

WHAT I HAD BEEN MOVING, AND WHY IT COULD NEVER WORK
===================================================
Build 359 moved the SNAPSHOT WINDOW to register the field. That was the wrong
object, and the operator's result said so:

    "the origin on the animated field is behind the enemies not inbetween the
     enemies and the summon"

The window decides WHAT is painted on the disc. It cannot decide WHERE the
disc is drawn. The place the field's symmetry appears is the place the disc's
own centre projects to, and that is pure geometry -- no capture rect, no UV,
no resample touches it.

WHERE THE DISC ACTUALLY IS
==========================
FINDINGS-308, and the constant has been an anchor in `ff7nx_fxdisc` and
`ff7nx_fxscale` this whole time without either of them asking what it means:

    x = (R * rsin(theta)) >> 12
    z = -(R * rcos(theta)) >> 12  -  5000        y = 0

    +0x473180  mov w25, #-0x1388        <-- THE WORD.  z = -5000
    +0x4723A0  add w9, w9, w25              applied to every ring vertex

**The disc is not centred on the battlefield. It is centred 5000 world units
behind it.** The floor underneath is a model that mirrors about its own
centre -- which is the operator's "the clean textures have symmetry along the
very center between the summon and the enemy" -- so the two symmetries are
5000 units apart, permanently, in vanilla. That is the mismatch, and it is
why fourteen builds of correcting the capture could not close it: the capture
was never the thing that was displaced.

HOW FAR 5000 IS, IN PIXELS
==========================
The disc's ring j has world radius `(3j*K) >> 8` and UV radius `4j`, so one
UV unit is `3K/1024` world units -- 90.24 at the 94 % dial. 5000 world units
is therefore 55.4 units of `v`, and `FIT_HUV` (the hardware-fitted screen<->UV
homography, validated four times) turns that into a screen position:

    z = -5000   the origin draws at  (0.680, 0.385)  =  (871, 277) px
    z =     0   the origin draws at  (0.596, 0.494)  =  (763, 356) px
                                     -108 px across, +79 px down

(871, 277) is up and behind. (763, 356) is forward and down, between. The
operator described both of those positions before this file existed, which is
the check that the model is describing the same thing he is looking at.

THE DIAL
========
`SEVENTH_NX_FX_ORIGIN_Z`, in world units, default 0 -- the battlefield's own
centre, where the floor's symmetry is. `stock` restores -5000. The whole
usable range and its screen position:

    z = -5000  (871, 277) px   stock, behind the enemies
    z = -4000  (853, 289)
    z = -3000  (834, 303)
    z = -2000  (813, 319)
    z = -1000  (790, 336)
    z =     0  (763, 356)      DEFAULT, the battlefield centre
    z = +1000  (732, 378)      in front of it, toward the party

So this is not a taste knob with an arbitrary scale. Name a screen position
and the table names the z, because the mapping is measured.

WHAT MOVES WITH IT
==================
Everything. The disc is rigid: its rings, its UVs and its ripple all travel
together, so this is a translation of the whole effect along the depth axis
and nothing about its shape, scale or texture changes. The near rim comes
5000 units closer to the camera at z = 0, which is 44 % of the outer radius
at the 94 % dial -- expect the field to cover more of the foreground and less
of the far distance.

WHAT IT DOES NOT FIX
====================
The disc's centre moves across the screen with the camera and this constant
does not. It puts the two symmetries on the same world point, which IS
camera-independent -- that is the difference between this and build 359's
window, and it is why this one can be right for every camera.
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

Z_ENV = 'SEVENTH_NX_FX_ORIGIN_Z'

Z_SITE = 0x473180                  # mov w25, #-0x1388
Z_STOCK = 0x128270F9
Z_STOCK_VALUE = -5000
Z_REG = 25

# BUILD 361: BACK TO STOCK, and the operator's own reasoning is why.
#
#   "the problem with the stretching is we dont know if the stretching is
#    causing the origin to be misplaced either"
#
# Correct, and now measured. A scale error of s displaces every feature that
# is not exactly at the origin by (s-1) * its distance from it. The field is
# drawing captured content 4.80x too wide at the disc centre (see
# FINDINGS-361), so a feature 50 px off centre is thrown 190 px. **The origin
# cannot be judged until the scale is right**, and tuning it in the meantime
# is fitting one error to another.
#
# There is a second, harder reason. `FIT_HUV` -- the hardware-fitted
# screen<->UV homography that every derivation in this project rests on -- was
# fitted with the disc at z = -5000. Moving the disc changes that relation, so
# shipping z = 0 silently invalidated the only calibrated thing we have. That
# was careless and it is undone here.
#
# The dial stays, calibrated and monotone, for when the scale is settled.
DEFAULT_Z = Z_STOCK_VALUE

# a sane band: the outer ring is ~11460 world units at the 94% dial, and
# anything beyond +-8000 moves the centre further than the field is wide
Z_MIN, Z_MAX = -8000, 8000

ANCHORS = {
    0x473178: 0x5285F417,   # mov w23, #0x2fa0    the j=32 outer ring, 127*96
    0x47317C: 0x32000FF8,   # mov w24, #0xf
    0x473184: 0x14000009,   # b   #0x4731a8       (w25 is live past here)
    0x472380: 0x0B080508,   # add w8, w8, w8, lsl #1     \  the ring's own
    0x472384: 0x53175908,   # lsl w8, w8, #9             /  z term
    0x472398: 0xB94002A9,   # ldr w9, [x21]
    0x47239C: 0x0B080129,   # add w9, w9, w8
    0x4723A0: 0x0B190129,   # add w9, w9, w25     <-- where the -5000 lands
    0x4723A4: 0x2900A6A8,   # stp w8, w9, [x21, #4]
    0x473244: 0x0B080508,   # add w8, w8, w8, lsl #1     the radius, which
    0x473248: 0x5319611A,   # lsl w26, w8, #7            ff7nx_fxdisc owns
}


def _movz32(rd, imm16):
    return 0x52800000 | ((imm16 & 0xFFFF) << 5) | rd


def _movn32(rd, imm16):
    return 0x12800000 | ((imm16 & 0xFFFF) << 5) | rd


assert _movn32(Z_REG, -Z_STOCK_VALUE - 1) == Z_STOCK


def word_for(z: int) -> int:
    """MOVZ for z >= 0, MOVN for z < 0. z = -5000 is the stock word."""
    if not Z_MIN <= z <= Z_MAX:
        raise ValueError('z out of band: %r' % (z,))
    return _movz32(Z_REG, z) if z >= 0 else _movn32(Z_REG, -z - 1)


assert word_for(Z_STOCK_VALUE) == Z_STOCK


def z() -> int:
    v = os.environ.get(Z_ENV)
    if v is None:
        return DEFAULT_Z
    if v.strip().lower() in ('stock', 'off', 'none', ''):
        return Z_STOCK_VALUE
    try:
        n = int(v, 0)
    except ValueError:
        return DEFAULT_Z
    return n if Z_MIN <= n <= Z_MAX else DEFAULT_Z


def enabled() -> bool:
    return z() != Z_STOCK_VALUE


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X'
                       % (va, got, want))
    got = _word(img, Z_SITE)
    if (got & 0x7F80001F) not in (0x52800000 | Z_REG, 0x12800000 | Z_REG):
        bad.append('+0x%X is %08X, which is not a `mov w%d, #imm`'
                   % (Z_SITE, got, Z_REG))
    return bad


def read_state(img) -> str:
    got = _word(img, Z_SITE)
    imm = (got >> 5) & 0xFFFF
    if (got & 0x7F800000) == 0x12800000:
        return 'field origin z = %d%s' % (-imm - 1,
                                          ' (stock)' if -imm - 1 ==
                                          Z_STOCK_VALUE else '')
    return 'field origin z = %d' % imm


def plan(m, revert=False):
    img = m.img
    problems = verify(img)
    if problems:
        return [], [], problems
    want = word_for(Z_STOCK_VALUE if revert else z())
    have = _word(img, Z_SITE)
    if have == want:
        return [], [], []
    return ([{'name': 'field origin z',
              'va': hex(Z_SITE),
              'expect': struct.pack('<I', have).hex(),
              'set': struct.pack('<I', want).hex()}],
            ['    field origin z = %d (stock %d), the disc centre moves to '
             'the battlefield centre' % (Z_STOCK_VALUE if revert else z(),
                                         Z_STOCK_VALUE)],
            [])


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'field origin', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fxorigin-')
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
        log('  refusing to move the field origin.')
        return 1
    log('  field origin (%s, stock is %d):' % (Z_ENV, Z_STOCK_VALUE))
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
