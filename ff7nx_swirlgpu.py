#!/usr/bin/env python3
r"""
ff7nx_swirlgpu.py -- take the battle-entry swirl off the CPU readback.

TWO WORDS, IN PLACE, NO CAVE.

THE PROBLEM
===========
`ff7nx_fbsurf` raises the capture surface so Kujata's field has the detail to
match the floor, and the cost lands on the CPU readback, which copies
`fb_tex.w * fb_tex.h` pixels a byte at a time (+0x10D71F4). The summon pays it
once. The battle-entry swirl pays it for **two** full half-frame captures, and
its rects scale with the surface:

    fb_tex per swirl capture = 320k x 480k
      k = 2    640 x  960  =   614k pixels    x2 captures
      k = 4   1280 x 1920  = 2,458k pixels    x2 captures    <- the delay

Kujata's own capture is only 256k x 256k -- 1M pixels at k = 4 -- so the
summon is not what is slow. The swirl is, and it is slow because it is four
times the pixels on a path that runs on the CPU.

THE PORT ALREADY HAS THE OTHER PATH
===================================
The capture loader branches on `field_0` at +0x10D70AC:

    cmp w8, #1
    b.ne #0x10D7240        -> Path B: render-to-texture, on the GPU

and the swirl **already uses both**. Its creator submits four captures (x86
0x4023F8), each half twice, differing in exactly that field:

```
             field_0   path            ARM site
sub 1  left     1      CPU readback    +0x123E4
sub 2  left     0      GPU             +0x12640
sub 3  right    1      CPU readback    +0x128A4
sub 4  right    0      GPU             +0x12B00
```

The draw picks one texture per quad from the four (x86 0x402CB5 / 0x402CE7 /
0x402D20 / 0x402D53) and does not care which path produced it -- both fill the
same handle at struc91 +0x114.

So the two CPU submissions can be sent down the GPU path as well, and the
byte-wise readback disappears from the swirl entirely -- **at any surface
scale**.

THE TWO WORDS
=============
    +0x123E4   str w8, [x0]   ->   str wzr, [x0]
    +0x128A4   str w8, [x0]   ->   str wzr, [x0]

`str wzr, [x0]` is not a spelling I invented: it is the **exact encoding the
game already uses** at +0x12640 and +0x12B00 to write `field_0 = 0` for the
other two submissions of the same two rects. Both are asserted as witnesses
below, so if a different executable wrote that field some other way this
module refuses rather than guessing.

The `mov w8, #1` that fed the store is left in place. It is dead afterwards,
and leaving it means the patch is exactly the two stores.

WHAT CHANGES ON SCREEN
======================
Path B renders the region into a texture with a LINEAR sampler (+0x10D73B8)
instead of copying it with POINT, so the swirl's picture is filtered on the way
in rather than point-sampled. There is no silhouette against void in a
full-frame capture, so the premultiplied fringe that `ff7nx_fbfilter` exists to
prevent cannot appear here -- that was a Kujata problem, and Kujata keeps
`field_0 = 1` and stays on the CPU path with `punch` and the resample.

WHAT IT DOES NOT CHANGE
=======================
Nothing about Kujata. Nothing about the surface. Nothing about the swirl's
geometry, its UVs, or `ff7nx_swirlseam`. The rects are untouched, so both
halves still cover exactly half the surface each: at k = 4 that is x = 1280,
w = 1280 on a 2560-wide surface, which fits without a clamp -- Path B does not
get `ff7nx_fbwindow`'s origin slide, so that mattering is checked rather than
assumed.
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

GPU_ENV = 'SEVENTH_NX_SWIRL_GPU'

# The two stores that set field_0 = 1, i.e. "take the CPU readback".
SITES = (0x123E4, 0x128A4)
STORE_W8 = 0xB9000008              # str w8,  [x0]     field_0 = 1
STORE_WZR = 0xB900001F             # str wzr, [x0]     field_0 = 0

# The game's own `field_0 = 0` stores, for the other two submissions of the
# same two rects. These are where STORE_WZR comes from -- it is the binary's
# encoding, not one of mine.
WITNESS = {0x12640: STORE_WZR, 0x12B00: STORE_WZR}

# Everything that proves these four stores are the swirl's four submissions.
ANCHORS = {
    0x123D4: 0x11032100,       # add w0, w8, #0xc8   struc91 field_0, sub 1
    0x123E0: 0x320003E8,       # mov w8, #1          the value being stored
    0x12634: 0x11032100,       # add w0, w8, #0xc8   sub 2
    0x12894: 0x11032100,       # add w0, w8, #0xc8   sub 3
    0x128A0: 0x320003E8,       # mov w8, #1
    0x12AF4: 0x11032100,       # add w0, w8, #0xc8   sub 4
    0x122F8: 0x52809614,       # mov  w20, #0x4b0   \  the swirl's own
    0x122FC: 0x72A01354,       # movk w20, #0x9a    /  struct, 0x9A04B0
}
ANCHORS.update(WITNESS)


def enabled() -> bool:
    v = os.environ.get(GPU_ENV)
    if v is None:
        return True
    return v.strip().lower() not in ('0', 'off', 'false', 'no', 'cpu', 'stock')


def words_for(gpu):
    return {va: (STORE_WZR if gpu else STORE_W8) for va in SITES}


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def installed(img):
    got = {va: _word(img, va) for va in SITES}
    for gpu in (True, False):
        if words_for(gpu) == got:
            return gpu
    return None


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        have = _word(img, va)
        if have != want:
            bad.append('anchor +0x%X is %08X, expected %08X -- the swirl\'s '
                       'capture creator has moved' % (va, have, want))
    if installed(img) is None:
        bad.append('+0x%X/+0x%X are %08X/%08X, which is neither path this '
                   'build writes'
                   % (SITES[0], SITES[1], _word(img, SITES[0]),
                      _word(img, SITES[1])))
    return bad


def read_state(img) -> str:
    state = installed(img)
    if state is None:
        return 'unknown'
    return ('swirl captures on the GPU path' if state
            else 'swirl captures on the CPU readback (stock)')


def plan(m_, revert=False):
    img = m_.img
    problems = verify(img)
    if problems:
        return [], [], problems
    gpu = not revert and enabled()
    want = words_for(gpu)
    patches, notes = [], []
    for n, va in enumerate(SITES):
        have = _word(img, va)
        if have == want[va]:
            continue
        what = ('%s half -> %s path' % ('left' if n == 0 else 'right',
                                        'GPU' if gpu else 'CPU'))
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
            nso, {'name': 'battle swirl capture path', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.swirlgpu-')
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
        log('  refusing to change the swirl capture path.')
        return 1
    gpu = not revert and enabled()
    log('  battle swirl capture path (%s): %s'
        % (GPU_ENV, 'GPU' if gpu else 'CPU readback (stock)'))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d swirl capture path word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
