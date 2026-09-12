#!/usr/bin/env python3
r"""
ff7nx_summonreach.py -- give the floor-warp summons' own ground surfaces the
4:3 -> 16:9 reach their capture already has.

THE SYMPTOM, IN THE TV'S WORDS
==============================
> "it kind of looks like its at the artificial boundary"
> "in vanilla kujata on this field becomes a long rectangle ... it doesnt have
>  black squares on its side"
> "on vanilla kotr evenly fades out into black across the entire dip. not just
>  along a large column of the circle with sides missing"
> "it stays fixed on the floor layer even as the camera rotates"

Measured on the screenshot: the black band sits at game x **670..725**. The
4:3 core ends at **640**. It lies entirely inside the widescreen margin.

Welded to the floor through a camera rotation means it cannot be a clip, a
scissor or a viewport -- those are screen-space and would slide. It is a
fixed feature of the WORLD, and the world is where these effects stop.

WHAT STOPS
==========
Both effects build a finite ground surface and hard-code how much world one
captured screen unit is worth. Out of `ff7_en`, and out of the translated
bodies:

    KUJATA   x86 0x500A5E / ARM +0x472170..+0x473560
        world radius  ring * 4 * 0x60          = ring * 384
            +0x473244  add w8, w8, w8, lsl #1     3j
            +0x473248  lsl w26, w8, #7            * 128   -> 384j
        outermost ring 0x2FA0 = 12192
            +0x473178  mov w23, #0x2fa0
        UV radius     ring * 4, clamped at 0x7F   (0x500A81, +0x4722B0)
        => 384/4 = 96 world units per captured screen unit, stop at 12192

    KOTR     x86 0x47958C / ARM +0x21AF00..+0x21C0A0
        world radius  ring * 288
            +0x21BD78  add w8, w8, w8, lsl #3     9j
            +0x21BD7C  lsl w27, w8, #5            * 32    -> 288j
        outermost      0x11D0 = 4560
            +0x21BC68  mov w24, #0x11d0

Both surfaces are flat at world Y = 0 -- Kujata writes the literal zero at
x86 0x500EBD, KOTR at 0x479973 -- and only Y is animated afterwards.

`ws-3d` does not move the picture; it widens the frustum, so the frame shows
**854 game units of world where 4:3 showed 640**. The extra world at the
sides is past where these surfaces stop. There the bare battlefield shows
through: darkened for Kujata -> a black square; un-darkened for KOTR -> an
unshaded wedge. One boundary, opposite polarity, and it is a floor feature,
which is why it rotates with the floor.

It is this stage because this stage is LONG, not because it is uneven: a
long rectangle running toward the camera fans out in perspective and its
near end reaches past the stop. A compact stage (the beach) never gets
there, which is why the beach is clean.

THE CORRECTION
==============
Grow the world reach by the same 854/640 = 4/3 the frame gained, and leave
the UVs alone, so the same snapshot covers the same FRACTION of the visible
world it covered at 4:3:

    Kujata   384j -> 512j        96 -> 128 world units per screen unit
             12192 -> 16256
    KOTR     288j -> 384j        and 4560 -> 6080

Five words, all in place, no caves:

    +0x473244  add w8, w8, w8, lsl #1  ->  lsl w8, w8, #2       3j -> 4j
    +0x473178  mov w23, #0x2fa0        ->  mov w23, #0x3f80
    +0x21BD78  add w8, w8, w8, lsl #3  ->  add w8, w8, w8, lsl #1   9j -> 3j
    +0x21BD7C  lsl w27, w8, #5         ->  lsl w27, w8, #7          *32 -> *128
    +0x21BC68  mov w24, #0x11d0        ->  mov w24, #0x17c0

IT MUST BE PAIRED WITH THE CAPTURE
==================================
More world per screen unit needs more screen per texel, or the pasted
picture stops registering. The pairing is exact:

    world per texel / game units per texel  must stay at the projection's
    own 96, which `ws-3d` does not change

    96 / 1      (build 268: disc 96, capture resampled to 1 unit/texel)
    128 / 1.334 (this build: disc 128, capture NOT resampled)

so `ff7nx_fbresample` goes OFF with this on -- 256 staging pixels is 341
game units, which is exactly the 4/3 -- and `ff7nx_fbcapture` re-centres the
rect so the picture does not also slide by (854/640 - 1) * w/2 = 43 units.

Build 267 is the evidence that the reach is the live variable: it captured
341 game units by accident and the TV reported the black squares gone --
"it extended the fx farther to the other side where the black squares would
appear, and i couldnt or didnt see them" -- while the picture itself was
mis-registered, which is the half this module supplies.

WHAT WOULD FALSIFY IT
=====================
If the boundary does not move outward, the surfaces above are not the ones
on screen and this whole reading is wrong. That is visible in one build and
it is the point of shipping it.
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

REACH_ENV = 'SEVENTH_NX_SUMMON_REACH'

# (name, va, stock word, widened word)
SITES = (
    ('Kujata ring radius  384j -> 512j', 0x473244, 0x0B080508, A.lsl(8, 8, 2)),
    ('Kujata outer ring   12192 -> 16256', 0x473178, 0x5285F417, A.movz(23, 0x3F80)),
    ('KOTR   ring radius  9j -> 3j', 0x21BD78, 0x0B080D08, 0x0B080508),
    ('KOTR   ring scale   x32 -> x128', 0x21BD7C, 0x531B691B, A.lsl(27, 8, 7)),
    ('KOTR   outer ring   4560 -> 6080', 0x21BC68, 0x52823A18, A.movz(24, 0x17C0)),
)

# Neighbours that pin each site to the right routine, so a game update cannot
# move the arithmetic under us without the build refusing.
ANCHORS = {
    0x473240: 0xB9400008,   # ldr  w8, [x0]              Kujata: the ring index
    0x473248: 0x5319611A,   # lsl  w26, w8, #7
    0x47317C: 0x32000FF8,   # mov  w24, #0xf             16 segments - 1
    0x473180: 0x128270F9,   # mov  w25, #-0x1388         z centre, -5000
    0x21BD74: 0xB9400008,   # ldr  w8, [x0]              KOTR: the ring index
    0x21BD80: 0xB9401688,   # ldr  w8, [x20, #0x14]
    0x21BC64: 0x32000FF7,   # mov  w23, #0xf
    0x21BC6C: 0x320013F9,   # mov  w25, #0x1f            32 rings - 1
}


def enabled() -> bool:
    """
    OFF. HARDWARE-DISPROVEN, build 269.

    The reading this module rests on is wrong, and one sentence from the TV
    is what kills it:

        "its not a straight path, the edge curves around the uneven terrain
         very very slightly"

    A fixed world radius is a CIRCLE. It cannot bend around bumps. An edge
    that follows the ground's shape is not a reach boundary at all, so
    growing the reach cannot be the fix -- and the build confirmed it changed
    nothing. The measured constants in the header are still true and worth
    keeping; the conclusion drawn from them is not.

    SEVENTH_NX_SUMMON_REACH=1 revives it for an A/B only.
    """
    v = os.environ.get(REACH_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false', 'no')
    return False


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X' % (va, got, want))
    for name, va, stock, fixed in SITES:
        got = _word(img, va)
        if got not in (stock, fixed):
            bad.append('%s: +0x%X is %08X, expected %08X (stock) or %08X'
                       % (name, va, got, stock, fixed))
    return bad


def read_state(img) -> str:
    have = [_word(img, va) for _, va, _, _ in SITES]
    if all(h == s for h, (_, _, s, _) in zip(have, SITES)):
        return 'stock'
    if all(h == f for h, (_, _, _, f) in zip(have, SITES)):
        return 'widened'
    return 'mixed'


def plan(m, revert=False):
    img = m.img
    problems = verify(img)
    if problems:
        return [], [], problems
    state = read_state(img)
    if state == 'mixed':
        return [], [], ['summon reach sites are in mixed states; refusing']
    patches, notes = [], []
    for name, va, stock, fixed in SITES:
        have = _word(img, va)
        want = stock if revert else fixed
        if have == want:
            continue
        patches.append({'name': name, 'va': hex(va),
                        'expect': struct.pack('<I', have).hex(),
                        'set': struct.pack('<I', want).hex()})
        notes.append('    %s' % name)
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'summon floor reach', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.reach-')
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
        log('  refusing to write the summon floor reach.')
        return 1
    log('  summon floor reach -> 16:9 (4/3 more world, UVs untouched):')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d summon reach word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
