#!/usr/bin/env python3
r"""
ff7nx_fxsnap.py -- move the screen window that Kujata's animated field
photocopies.  TWO WORDS, IN PLACE, NO CAVE.

THIS IS AN EXPERIMENT WITH A STATED PREDICTION, NOT A BLIND PATCH.

THE THING I HAD NEVER ACTUALLY READ
===================================
The snapshot rect is a COMPILE-TIME CONSTANT.  x86 `0x500858`:

    [0x87B430] = word[0x9AAD4C]     rect.x  = battle viewport x = 0
    [0x87B432] = word[0x9AAD50]     rect.y  = battle viewport y = 0
    [0x87B434] = [0x87B436] = 0x100 (0x80 when [0x9ACB5C] != 2)

It does not depend on the summon, the camera, the terrain, or anything that
happens in the battle.  **The effect photocopies the top-left 256x256 GAME
UNITS of the frame and smears that copy over a disc in the world**, with the
polar UVs at `0x500B78` mapping the texture's inscribed circle onto the disc:

    u = 0x80 + (r * rsin(theta) >> 12)      r = 0..127, written as BYTES
    v = 0x80 + (r * rcos(theta) >> 12)

so texture centre (128,128) sits at the disc centre, and the disc's outer
ring samples texels 1 and 255 -- the very edges of that window.

WHAT THAT MEANS FOR THE BLACK SQUARES
=====================================
After builds 267/268 the capture texture is FULLY WRITTEN and CORRECTLY
REGISTERED.  I checked the copy loop again for this note:

    +0x10D7138  w23 = tex_header+0x14      rect.x   (fbcapture's corrected x)
    +0x10D7148  w24 = min(surface_w, x+w)
    +0x10D71C0  w10 = w24 - w23            columns copied per row
    +0x10D7220  destination row stride     = fb_tex.w * 4

With x' = 80 and w = 256, `x + w = 336 <= 640`, so `w10 == fb_tex.w` and
`w22 == fb_tex.h`: every destination texel is written.  There are no
uninitialised texels left to come back black.

So the black texels are REAL.  The window is copying black off the screen.

And there is only one thing on that screen that is black on a world-map grass
battlefield and not on the beach: **the void past the edge of the battleground
model.**  ws-3d shows 854 game units of world where 4:3 showed 640.  The
battle stage is a finite model -- the TV's own words, "like a long rectangle
extending behind your party and in front", "a valley with higher edges on the
left and right", "it kind of looks like its at the artificial boundary".
FFNx does not widen it either (`src/ff7/widescreen.cpp` patches the battle
VIEWPORT and the 2D quads, and nothing about `update_3d_battleground`), so
under 16:9 you simply see past its two long edges, and past them is nothing.

Every reported property follows, with nothing left over:

    black, solid, not a darkening   POLY_FT4 code 0x2C, semi-transparency
                                    bit CLEAR (0x5006FB)
    ONE long edge, not both         the window is game x 0..256 of a frame
                                    spanning -107..747 -- left of centre, so
                                    it contains the LEFT stage edge and not
                                    the right
    parallel to the battle axis     it is the long edge of the stage model
    "curves around the uneven       it is the stage's own silhouette against
     terrain very very slightly"    the void; the silhouette follows the bumps
    squares, and lines of squares   the 16-segment x 33-ring cell grid
                                    quantising that silhouette
    warped with the geometry        it is baked into the texture, and the
                                    texture rides the warping quads
    welded to the floor through a   the snapshot is taken ONCE, at effect
     camera rotation                start, and the UVs are written once
    absent on the beach             that stage's backdrop covers 16:9
    ABSENT IN VANILLA               4:3 never shows past the stage edge

It also explains why every capture fix was correct and none of them helped:
the capture became a faithful copy of a frame that already had black in it.

THE EXPERIMENT
==============
Move the window.  `SEVENTH_NX_FX_SNAP_X` / `SEVENTH_NX_FX_SNAP_Y` replace the
two loads with immediates:

    +0x471FA0  ldrh w23, [x0]   ->   movz w23, #X      rect.x
    +0x471FBC  ldrh w20, [x0]   ->   movz w20, #Y      rect.y

The default is (192, 112): the dead centre of the 854x480 widescreen frame,
where the party and the enemies stand and the stage model always covers.

    black squares GONE           the mechanism is proved, and the remaining
                                 question is only which window looks best
    black squares MOVED          same conclusion, and the offset tells us
                                 where the stage edge is
    black squares UNCHANGED      I am wrong, and this whole line dies with a
                                 clean two-word revert

The effect will smear a different part of the picture.  That is cosmetic and
expected -- it was never a picture of the ground underneath it; it was always
a fixed screen window.

    SEVENTH_NX_FX_SNAP_X=stock   turn the module off entirely
    SEVENTH_NX_FX_SNAP_Y=stock

WHAT IT DOES NOT TOUCH
======================
The capture pipeline, the resample, the disc geometry, the UVs, the ripple,
KOTR, or any other effect's snapshot.  Kujata's two rect words, nothing else.
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

X_ENV = 'SEVENTH_NX_FX_SNAP_X'
Y_ENV = 'SEVENTH_NX_FX_SNAP_Y'

# x86 0x500858 -> ARM +0x471F20.  The recompiler merged the high-res and the
# low-res branches, so there is exactly ONE read of each rect field.
HOOK_X = 0x471FA0                  # ldrh w23, [x0]   <- word[0x9AAD4C]
HOOK_Y = 0x471FBC                  # ldrh w20, [x0]   <- word[0x9AAD50]
STOCK_X = 0x79400017
STOCK_Y = 0x79400014

# The register each load feeds, so the immediate lands in the same place.
REG_X = 23
REG_Y = 20

# The instructions either side.  The translator call before each load leaves
# its host pointer in x0, and the instruction after each load overwrites x0 --
# so the pointer is dead and the load can become a move with nothing else to
# fix up.  These anchors are what proves that, and they are read from the
# binary, never typed from a listing.
ANCHORS = {
    0x471F9C: 0x94322901,          # bl   TRANSLATE      (x0 = host 0x9AAD4C)
    0x471FA4: 0x11008260,          # add  w0, w19, #0x20 (x0 overwritten)
    0x471FA8: 0x790002D7,          # strh w23, [x22]     (guest eax)
    0x471FB8: 0x943228FA,          # bl   TRANSLATE      (x0 = host 0x9AAD50)
    0x471FC0: 0x11008A60,          # add  w0, w19, #0x22 (x0 overwritten)
    0x471FC4: 0x79000AD4,          # strh w20, [x22, #4] (guest eax again)
}

# DISPROVEN ON HARDWARE, BUILD 271.  Moving the window to (192, 112) changes
# every pixel the capture texture contains, and the black band did not move by
# a single pixel.  So the black is NOT captured content, and this module is OFF
# by default and kept only because that negative result is worth being able to
# reproduce.  Set both env vars to re-arm it.
DEFAULT_X = None
DEFAULT_Y = None


def _movz(rd, imm16):
    """MOVZ Wd, #imm16."""
    return 0x52800000 | ((imm16 & 0xFFFF) << 5) | rd


def _setting(env, default):
    """
    None  -> leave this field stock.
    int   -> write it.  Negative values are taken modulo 2^16, because the
             destination is a `strh` and the consumer at x86 0x5009F5 does a
             `movsx`, so 0xFF95 really is -107 by the time it is used.
    """
    v = os.environ.get(env)
    if v is None:
        return default
    if v.strip().lower() in ('stock', 'off', 'none', ''):
        return None
    try:
        n = int(v, 0)
    except ValueError:
        return default
    if not -0x8000 <= n <= 0xFFFF:
        return default
    return n


def snap_x():
    return _setting(X_ENV, DEFAULT_X)


def snap_y():
    return _setting(Y_ENV, DEFAULT_Y)


def enabled() -> bool:
    return snap_x() is not None or snap_y() is not None


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _is_movz(word, rd):
    return (word & 0xFFE0001F) == (0x52800000 | rd)


def _movz_imm(word):
    return (word >> 5) & 0xFFFF


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X' % (va, got, want))
    for va, stock, rd, name in ((HOOK_X, STOCK_X, REG_X, 'rect.x'),
                                (HOOK_Y, STOCK_Y, REG_Y, 'rect.y')):
        got = _word(img, va)
        if got != stock and not _is_movz(got, rd):
            bad.append('+0x%X (%s) is %08X -- neither the stock load nor a '
                       'movz this build wrote' % (va, name, got))
    return bad


def read_state(img) -> str:
    out = []
    for va, stock, rd, name in ((HOOK_X, STOCK_X, REG_X, 'x'),
                                (HOOK_Y, STOCK_Y, REG_Y, 'y')):
        got = _word(img, va)
        out.append('%s=stock' % name if got == stock
                   else '%s=%d' % (name, _movz_imm(got)))
    return ' '.join(out)


def plan(m_, revert=False):
    img = m_.img
    problems = verify(img)
    if problems:
        return [], [], problems
    want_x = None if revert else snap_x()
    want_y = None if revert else snap_y()
    patches, notes = [], []
    for va, stock, rd, name, want in (
            (HOOK_X, STOCK_X, REG_X, 'rect.x', want_x),
            (HOOK_Y, STOCK_Y, REG_Y, 'rect.y', want_y)):
        target = stock if want is None else _movz(rd, want)
        have = _word(img, va)
        if have == target:
            continue
        label = ('restore stock %s (battle viewport)' % name if want is None
                 else 'snapshot %s = %d' % (name, want & 0xFFFF))
        patches.append({'name': label, 'va': hex(va),
                        'expect': struct.pack('<I', have).hex(),
                        'set': struct.pack('<I', target).hex()})
        notes.append('    %s' % label)
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'Kujata field snapshot window',
                  'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fxsnap-')
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
        log('  refusing to move the field snapshot window.')
        return 1
    if revert:
        log('  Kujata field snapshot window: stock (battle viewport origin)')
    else:
        log('  Kujata field snapshot window (DIAGNOSTIC, %s/%s; stock is the '
            'battle viewport origin 0,0):' % (X_ENV, Y_ENV))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d snapshot-window word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
