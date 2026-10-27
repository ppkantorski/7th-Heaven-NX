#!/usr/bin/env python3
"""
ff7nx_gpucap.py -- the GPU capture path renders at the AUTHORED size, so the
surface scale costs nothing at battle entry.

TWO WORDS, IN PLACE, NO CAVE. (BUILD 735: TEN, FOR FULL -- SEE THE END.)

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
BUILD 735 -- FULL RESOLUTION, AND STILL NO PAUSE
================================================
> "is it possible to make this screen capture effect the same resolution as
>  the screen but with no pause / slowdown / stutter?"

The pause was never the capture's own size. Path B makes TWO textures per
capture (+0x4940): the render target, and the background scaler's filtered
copy of it, created at `w5` times its width AND height (+0x10D7270, the
literal `mov w5, #4` that `ff7nx_texscale` turns into a call). The filter pass
(+0x10DBCC0, called at +0x10D7614) then upsamples the target into the copy,
and the game draws whichever one the display filter picks (+0x10D9724: the
copy when the HD/HQ scaler is on, the target when it is off). So:

    stock, k = 4    1280 x 1920 target  +  5120 x 7680 copy   167 MB a capture
    authored (320)   160 x  240 target  +   640 x  960 copy   2.6 MB a capture

and the swirl makes four captures. 670 MB in one frame was the pause; the
authored size fixed it by making the picture 160 x 240, upsampled.

FULL renders the target at `SCALE` x the authored size -- 640 x 960 a half at
the default 4, which is the handheld screen's own half-width and two thirds of
the docked one -- straight from the capture surface. `ff7nx_texscale`'s rule
(4x up to 256 px, 2x up to 512, 1x above) then sizes the copy 1x, so it is the
same 640 x 960 and the filter pass is a same-size pass:

    full, SCALE 4    640 x  960 target  +   640 x  960 copy   4.9 MB a capture

Twice the authored size's memory, 34x less than stock, and the detail is real
in both display-filter modes. Escape (authored 256 x 256) gets 1024 x 1024.

THE WORDS (all in place, no cave)
    +0x10D7264   ldp  w2, w3, [x20, #0x3c]      tex_format, as authored mode
    +0x10D7268   lsl  w2, w2, #s                \\  the size, scaled -- BEFORE
    +0x10D726C   lsl  w3, w3, #s                /  +0x10D7270 sizes the copy
    +0x10D7274   ldr  x8, [x0, w21, sxtw #3]    \\  the two loads they
    +0x10D7278   ldr  x22, [x8]                 /  displaced, moved down
    +0x4960      cmp  w21, #0x400               \\  +0x4940 reads the slot from
    +0x497C      ldr  x20, [x0, w21, sxtw #3]   /  w21, so `mov w1, w21` goes
    +0x10D72F4   ldur x22, [x20, #0x3c]         tex_format, as authored mode
    +0x10D7300   lsl  x2, x22, #s               \\  scissor and viewport, both
    +0x10D7310   lsl  x2, x22, #s               /  halves of the packed pair

+0x4940 has exactly one caller (+0x10D727C) and no pointer to it exists, so
reading the slot from the caller's w21 (callee-saved, not written in +0x4940
before both reads) is safe; `mov x4, xzr` was dead (+0x4940 never reads x4).
The texscale cave between the shifts and the loads clobbers only w5, w16,
w17 and NZCV. The UVs (+0x10D7478) come from the rect and the surface, never
from the target, so a bigger target is the same picture with more texels.

FULL needs `ff7nx_texscale` at +0x10D7270: with the stock `mov w5, #4` the copy
would be 2560 x 3840 and the pause would be back, so this falls back to the
authored size and says so. SEVENTH_NX_GPU_CAP=authored keeps build 320's
size; SEVENTH_NX_GPU_CAP=0 is stock; SEVENTH_NX_GPU_CAP_SCALE=2 or 8 moves the
scale (8 is 1280 x 1920 a half, 19.7 MB a capture -- an experiment, not a
default).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: run directly, find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
if _nx_os.path.join(_nx_root, 'src') not in _nx_sys.path:
    _nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import argparse
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

_HERE = nxpaths.ROOT
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import nxmap                                                   # noqa: E402

GPUCAP_ENV = 'SEVENTH_NX_GPU_CAP'
SCALE_ENV = 'SEVENTH_NX_GPU_CAP_SCALE'
DEFAULT_SCALE = 4
LEGAL_SCALES = (2, 4, 8)

FB_OFF, TEX_OFF = 0x1C, 0x3C       # fb_tex.w/h  and  tex_format.width/height

ALLOC_SITE = 0x10D7264             # ldp  w2, w3, [x20, #0x1c]  -> the target
VIEW_SITE = 0x10D72F4              # ldur x22,    [x20, #0x1c]  -> scissor+vp

# BUILD 735, FULL: the words around them (see the header)
SHIFT_W, SHIFT_H = 0x10D7268, 0x10D726C
COPY_SCALE = 0x10D7270             # mov w5, #4 / ff7nx_texscale's bl -- read only
LOAD_SLOT, LOAD_OBJ = 0x10D7274, 0x10D7278
CREATE_CMP, CREATE_LDR = 0x4960, 0x497C
SCISSOR_ARG, VIEWPORT_ARG = 0x10D7300, 0x10D7310

MOV_W5_4 = 0x321E03E5              # orr w5, wzr, #0x4 -- texscale is not there


def _ldp32(t1, t2, n, off):
    """LDP Wt1, Wt2, [Xn, #off]."""
    return 0x29400000 | (((off // 4) & 0x7F) << 15) | (t2 << 10) | (n << 5) | t1


def _ldur64(t, n, off):
    """LDUR Xt, [Xn, #off]."""
    return 0xF8400000 | ((off & 0x1FF) << 12) | (n << 5) | t


def _lsl32(d, n, sh):
    """LSL Wd, Wn, #sh  (UBFM Wd, Wn, #(32-sh)%32, #31-sh)."""
    return 0x53000000 | (((32 - sh) % 32) << 16) | ((31 - sh) << 10) | (n << 5) | d


def _lsl64(d, n, sh):
    """LSL Xd, Xn, #sh  (UBFM Xd, Xn, #(64-sh)%64, #63-sh)."""
    return 0xD3400000 | (((64 - sh) % 64) << 16) | ((63 - sh) << 10) | (n << 5) | d


def _mov64(d, m):
    """MOV Xd, Xm  (ORR Xd, XZR, Xm)."""
    return 0xAA0003E0 | (m << 16) | d


ALLOC_STOCK = _ldp32(2, 3, 20, FB_OFF)
VIEW_STOCK = _ldur64(22, 20, FB_OFF)

# the stock words FULL moves or rewrites, read off the dump and asserted
STOCK = {
    SHIFT_W: 0xF875D808,           # ldr  x8, [x0, w21, sxtw #3]
    SHIFT_H: 0xF9400116,           # ldr  x22, [x8]
    LOAD_SLOT: 0x2A1503E1,         # mov  w1, w21
    LOAD_OBJ: 0xAA1F03E4,          # mov  x4, xzr
    CREATE_CMP: 0x7110003F,        # cmp  w1, #0x400
    CREATE_LDR: 0xF861D814,        # ldr  x20, [x0, w1, sxtw #3]
    SCISSOR_ARG: _mov64(2, 22),    # mov  x2, x22
    VIEWPORT_ARG: _mov64(2, 22),   # mov  x2, x22
}

# Everything that proves these are Path B's target-size reads, and that the
# source region is computed from somewhere else.
ANCHORS = {
    0x10D70B0: 0x7100051F,     # cmp  w8, #1        the path selector itself
    0x10D7254: 0x7110001F,     # cmp  w0, #0x400  \  the texture slot Path B
    0x10D725C: 0x2A0003F5,     # mov  w21, w0     /  just created
    0x10D7260: 0xF94002E0,     # ldr  x0, [x23]     the slot table, still in
                               #                       x0 at +0x10D7274
    0x10D727C: 0x97BCB5B1,     # bl   #0x4940       ... and allocates, with
                               #                       w2/w3 as its size
    0x10D7304: 0x94016B77,     # bl   #0x11320e0    scissor, x2 = the pair
    0x10D7314: 0x94016B77,     # bl   #0x11320f0    viewport, x2 = the pair
    0x10D7478: 0x2942A688,     # ldp  w8,  w9,  [x20, #0x14]  \ the UVs, from
    0x10D747C: 0x2943AE8A,     # ldp  w10, w11, [x20, #0x1c]  / rect + surface
    0x10DBC90: 0x2907DF58,     # stp  w24, w23, [x26, #0x3c]  tex_format w,h
    0x4944: 0xA9054FF4,        # stp  x20, x19, [sp, #0x50] \ +0x4940 saves
    0x4948: 0xA9067BFD,        # stp  x29, x30, [sp, #0x60] / only these
    0x496C: 0x29000FE2,        # stp  w2, w3, [sp]          its size, as given
    0x10D7614: 0x940011AB,     # bl   #0x10dbcc0    the filter pass, target
                               #                       -> copy
}

AUTHORED, FULL, STOCK_MODE = 'authored', 'full', 'stock'


def mode() -> str:
    """What SEVENTH_NX_GPU_CAP asks for: 'full' (default), 'authored' or
    'stock'."""
    v = os.environ.get(GPUCAP_ENV)
    if v is None:
        return FULL
    v = v.strip().lower()
    if v in ('0', 'off', 'false', 'no', 'stock'):
        return STOCK_MODE
    if v in ('authored', '320', 'psx', 'low'):
        return AUTHORED
    return FULL


def enabled() -> bool:
    return mode() != STOCK_MODE


def scale() -> int:
    v = os.environ.get(SCALE_ENV)
    try:
        n = int(v) if v is not None else DEFAULT_SCALE
    except ValueError:
        return DEFAULT_SCALE
    return n if n in LEGAL_SCALES else DEFAULT_SCALE


def shift_of(n):
    return {2: 1, 4: 2, 8: 3}[n]


def words_for(authored, full_shift=None):
    """The words for one state. `full_shift` set means FULL at 1 << shift
    (and implies tex_format); otherwise authored or stock as before."""
    off = TEX_OFF if authored or full_shift else FB_OFF
    w = {ALLOC_SITE: _ldp32(2, 3, 20, off),
         VIEW_SITE: _ldur64(22, 20, off)}
    if full_shift:
        s = full_shift
        w.update({
            SHIFT_W: _lsl32(2, 2, s),
            SHIFT_H: _lsl32(3, 3, s),
            LOAD_SLOT: STOCK[SHIFT_W],
            LOAD_OBJ: STOCK[SHIFT_H],
            CREATE_CMP: (STOCK[CREATE_CMP] & ~(31 << 5)) | (21 << 5),
            CREATE_LDR: (STOCK[CREATE_LDR] & ~(31 << 16)) | (21 << 16),
            SCISSOR_ARG: _lsl64(2, 22, s),
            VIEWPORT_ARG: _lsl64(2, 22, s),
        })
    else:
        w.update(STOCK)
    return w


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def installed(img):
    """('stock'|'authored'|'full', scale) or None."""
    sites = sorted(words_for(False, 2))
    got = {va: _word(img, va) for va in sites}
    if words_for(False) == got:
        return STOCK_MODE, 1
    if words_for(True) == got:
        return AUTHORED, 1
    for n in LEGAL_SCALES:
        if words_for(True, shift_of(n)) == got:
            return FULL, n
    return None


def texscale_present(img):
    """True when +0x10D7270 is no longer the stock `mov w5, #4` -- in this
    build that is ff7nx_texscale's `bl`, which caps the copy."""
    return _word(img, COPY_SCALE) != MOV_W5_4


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        have = _word(img, va)
        if have != want:
            bad.append('anchor +0x%X is %08X, expected %08X -- the GPU '
                       'capture path has moved' % (va, have, want))
    if installed(img) is None:
        bad.append('the GPU capture words (+0x%X..) are in no state this '
                   'build writes' % CREATE_CMP)
    return bad


def read_state(img) -> str:
    state = installed(img)
    if state is None:
        return 'unknown'
    m, n = state
    if m == FULL:
        return 'GPU capture target = %dx authored size' % n
    return ('GPU capture target = authored size' if m == AUTHORED
            else 'GPU capture target = scaled rect (stock)')


def wanted(img, revert=False):
    """(mode, scale, note) this build will write into `img`."""
    if revert:
        return STOCK_MODE, 1, None
    m = mode()
    if m == FULL and not texscale_present(img):
        return AUTHORED, 1, ('+0x%X is still the stock `mov w5, #4` (no '
                             'ff7nx_texscale), so a full-size target would '
                             'carry a 4x copy -- the pause. Keeping the '
                             'authored size.' % COPY_SCALE)
    return m, (scale() if m == FULL else 1), None


def plan(m_, revert=False):
    img = m_.img
    problems = verify(img)
    if problems:
        return [], [], problems
    m, n, note = wanted(img, revert)
    want = words_for(m != STOCK_MODE, shift_of(n) if m == FULL else None)
    label = {STOCK_MODE: 'scaled rect (stock)', AUTHORED: 'authored size',
             FULL: '%dx authored size' % n}[m]
    patches, notes = [], []
    if note:
        notes.append('    ! ' + note)
    for va in sorted(want):
        have = _word(img, va)
        if have == want[va]:
            continue
        patches.append({'name': 'GPU capture word +0x%X -> %s' % (va, label),
                        'va': hex(va),
                        'expect': struct.pack('<I', have).hex(),
                        'set': struct.pack('<I', want[va]).hex()})
    if patches:
        notes.append('    target -> %s (%d words)' % (label, len(patches)))
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
    m, n, _note = wanted(m_.img, revert)
    log('  GPU capture target (%s): %s'
        % (GPUCAP_ENV, {STOCK_MODE: 'scaled rect (stock)',
                        AUTHORED: 'authored size',
                        FULL: '%dx authored size, copy capped by texscale'
                              % n}[m]))
    for line in notes:
        log(line)
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
