#!/usr/bin/env python3
r"""
ff7nx_gpucap.py -- the GPU capture path renders at the AUTHORED size, so the
surface scale costs nothing at battle entry.

TWO WORDS, IN PLACE, NO CAVE.

WHAT IS BEING SEPARATED
=======================
> "im trying to isolate it to in battle effects vs just entering a battle"

The port already draws that line, and it draws it exactly there. The capture
loader branches on `field_0` (+0x10D70B0, `cmp w8, #1`):

    field_0 == 1   ->  CPU readback (+0x10D70B8..)   every battle effect
                       object, set by x86 0x682D80 -- Kujata, Titan,
                       Alexander, KOTR, all of them
    field_0 != 1   ->  Path B, render-to-texture (+0x10D7240..)   the
                       battle-entry swirl

So "in-battle effects" and "entering a battle" are already two different code
paths. `ff7nx_fbsurf` scales the surface and both paths inherit it, and it is
the second one that makes entry expensive:

    Path B target = fb_tex.w x fb_tex.h = 320k x 480k per swirl capture
      k = 1    320 x  480    x4 =  2.5 MB
      k = 2    640 x  960    x4 =  9.8 MB
      k = 4   1280 x 1920    x4 = 39.3 MB   allocated in one frame

39 MB of render target created at battle entry is the freeze.

WHY THE TARGET CAN SHRINK WITHOUT MOVING THE PICTURE
====================================================
Path B's source region does NOT come from its target size. It is computed from
the RECT and the SURFACE, at +0x10D7478:

    ldp w8,  w9,  [x20, #0x14]      fb_tex.x, fb_tex.y
    ldp w10, w11, [x20, #0x1c]      fb_tex.w, fb_tex.h
    w22 = surface width             (GetWidth  at +0x10D744C)
    w0  = surface height            (GetHeight at +0x10D7474)
    ...
    u0 = x / surf_w                 u1 = (x + w) / surf_w
    v0 = y / surf_h                 v1 = (y + h) / surf_h

so the quad always samples the same region of the surface whatever size the
target is. Making the target smaller is therefore a **GPU downscale of the
same picture**, which is the one thing a render-to-texture path is good at.

THE TWO WORDS
=============
Read the target's size from `tex_format` (+0x3C/+0x40) instead of `fb_tex`
(+0x1C/+0x20):

    +0x10D7264   ldp  w2, w3, [x20, #0x1c]  ->  [x20, #0x3c]   the allocation
    +0x10D72F4   ldur x22,    [x20, #0x1c]  ->  [x20, #0x3c]   scissor+viewport

`tex_format` is the effect's **authored** size -- the same number its UVs are
divided by (`u / tex_format.width`), so the UV space maps onto the target
exactly, with no rescaling anywhere. For the swirl that is 160 x 240 per half,
which is the PlayStation's own framebuffer resolution for that effect.

Both fields are stored by the same instruction pair as `fb_tex`
(+0x10DBC90, `stp w24, w23, [x26, #0x3c]`), so they are always present; and
being adjacent 32-bit fields, the 64-bit `ldur` at +0x3C packs (w | h << 32)
the same way the stock one does at +0x1C.

WHAT IT COSTS AND WHAT IT DOES NOT
==================================
    every in-battle effect      CPU readback, untouched, full surface scale
    the battle-entry swirl      4 x 160x240 = 154k pixels, 2.5 MB, at ANY k

That is less than the stock build spends at k = 1, because the stock path sizes
the target from the scaled rect and this one never does. Battle entry stops
caring about `SEVENTH_NX_FB_SURFACE` altogether.

PAIRS WITH ff7nx_swirlgpu
=========================
The swirl submits four captures, two on each path (FINDINGS 316). This module
only reaches the two already on Path B unless `ff7nx_swirlgpu` has moved the
other two there as well, so the pair is what makes battle entry fully
scale-independent. Both are on by default.

One cost this does not touch: the scene -> surface blit (+0x10DACB0) runs every
frame everywhere in the game, and at k = 4 that is 4.9M pixels of GPU fill per
frame rather than 1.2M at k = 2. That is a steady framerate cost, not an entry
freeze, and it is the remaining reason to prefer k = 2 if the game ever feels
less smooth generally.
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

GPUCAP_ENV = 'SEVENTH_NX_GPU_CAP'

FB_OFF, TEX_OFF = 0x1C, 0x3C       # fb_tex.w/h  and  tex_format.width/height

ALLOC_SITE = 0x10D7264             # ldp  w2, w3, [x20, #0x1c]  -> the target
VIEW_SITE = 0x10D72F4              # ldur x22,    [x20, #0x1c]  -> scissor+vp


def _ldp32(t1, t2, n, off):
    """LDP Wt1, Wt2, [Xn, #off]."""
    return 0x29400000 | (((off // 4) & 0x7F) << 15) | (t2 << 10) | (n << 5) | t1


def _ldur64(t, n, off):
    """LDUR Xt, [Xn, #off]."""
    return 0xF8400000 | ((off & 0x1FF) << 12) | (n << 5) | t


ALLOC_STOCK = _ldp32(2, 3, 20, FB_OFF)
VIEW_STOCK = _ldur64(22, 20, FB_OFF)

# Everything that proves these are Path B's target-size reads, and that the
# source region is computed from somewhere else.
ANCHORS = {
    0x10D70B0: 0x7100051F,     # cmp  w8, #1        the path selector itself
    0x10D7254: 0x7110001F,     # cmp  w0, #0x400  \  the texture slot Path B
    0x10D725C: 0x2A0003F5,     # mov  w21, w0     /  just created
    0x10D727C: 0x97BCB5B1,     # bl   #0x4940       ... and allocates, with
                               #                       w2/w3 as its size
    0x10D7304: 0x94016B77,     # bl   #0x11320e0    scissor, x2 = the pair
    0x10D7314: 0x94016B77,     # bl   #0x11320f0    viewport, x2 = the pair
    0x10D7478: 0x2942A688,     # ldp  w8,  w9,  [x20, #0x14]  \ the UVs, from
    0x10D747C: 0x2943AE8A,     # ldp  w10, w11, [x20, #0x1c]  / rect + surface
    0x10DBC90: 0x2907DF58,     # stp  w24, w23, [x26, #0x3c]  tex_format w,h
}


def enabled() -> bool:
    v = os.environ.get(GPUCAP_ENV)
    if v is None:
        return True
    return v.strip().lower() not in ('0', 'off', 'false', 'no', 'stock')


def words_for(authored):
    off = TEX_OFF if authored else FB_OFF
    return {ALLOC_SITE: _ldp32(2, 3, 20, off),
            VIEW_SITE: _ldur64(22, 20, off)}


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def installed(img):
    got = {va: _word(img, va) for va in (ALLOC_SITE, VIEW_SITE)}
    for authored in (True, False):
        if words_for(authored) == got:
            return authored
    return None


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        have = _word(img, va)
        if have != want:
            bad.append('anchor +0x%X is %08X, expected %08X -- the GPU '
                       'capture path has moved' % (va, have, want))
    if installed(img) is None:
        bad.append('+0x%X/+0x%X are %08X/%08X, which is neither size this '
                   'build writes'
                   % (ALLOC_SITE, VIEW_SITE, _word(img, ALLOC_SITE),
                      _word(img, VIEW_SITE)))
    return bad


def read_state(img) -> str:
    state = installed(img)
    if state is None:
        return 'unknown'
    return ('GPU capture target = authored size' if state
            else 'GPU capture target = scaled rect (stock)')


def plan(m_, revert=False):
    img = m_.img
    problems = verify(img)
    if problems:
        return [], [], problems
    authored = not revert and enabled()
    want = words_for(authored)
    what = {ALLOC_SITE: 'target allocation', VIEW_SITE: 'target viewport'}
    patches, notes = [], []
    for va in sorted(want):
        have = _word(img, va)
        if have == want[va]:
            continue
        line = '%s -> %s' % (what[va],
                             'authored size' if authored else 'scaled rect')
        patches.append({'name': line, 'va': hex(va),
                        'expect': struct.pack('<I', have).hex(),
                        'set': struct.pack('<I', want[va]).hex()})
        notes.append('    %s' % line)
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'GPU capture target size', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.gpucap-')
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
        log('  refusing to change the GPU capture target size.')
        return 1
    authored = not revert and enabled()
    log('  GPU capture target (%s): %s'
        % (GPUCAP_ENV,
           'authored size' if authored else 'scaled rect (stock)'))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d GPU capture size word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
