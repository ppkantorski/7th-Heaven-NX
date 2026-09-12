#!/usr/bin/env python3
r"""
ff7nx_fbwindow.py -- slide a capture rect back onto the surface instead of
letting it hang off the edge.

THE BLACK SQUARES ON THE RIGHT, EXACTLY
=======================================
The mesh addresses the capture with UV bytes running **0..255**, because
`u_offset = 1 / tex_format.width` and `tex_format.width` is 256. The copy loop
writes

    cols = min(surface_w, x + w) - x        rows = min(surface_h, y + h) - y

columns and rows. When the rect hangs off the right edge of the surface, `cols`
comes out SHORT of the 256 the UV space addresses, and the texels past it were
never written -- on the right-hand edge of the sheet, which is where they show
up on screen.

`ff7nx_fbcapture` already clamps the origin for exactly this reason:

    x' = clamp(floor((x + 107) * 640 / 854), 0, 640 - w)

but it clamps against a **hard-coded 640**, its `STAGING_W`. The copy loop does
not use 640 -- it asks the surface for its real width at runtime, through the
vtable (`+0x28` -> w19, `+0x40` -> w0). If those two numbers disagree, the
clamp lets the rect run off the end of the surface and the shortfall is black.

THE FIX
=======
Clamp the origin against the surface's OWN runtime size, where that size is
already sitting in a register:

    if (x + w >  surface_w)   x = max(0, surface_w - w)
    if (y + h >  surface_h)   y = max(0, surface_h - h)

so the copy writes the FULL `fb_tex.w` x `fb_tex.h` the UV space expects,
sliding the window left/up rather than truncating it. No constant of mine
appears anywhere -- the code asks the surface, exactly the way vanilla's
`getInternalCoordX/Y` are driven by `framebufferWidth/Height` rather than by a
literal.

WHY IT NEEDS NO GATE
====================
A rect that already fits takes the `b.le` and nothing is touched -- not the
header, not a register. Only a rect that is currently hanging off the edge
moves, and such a rect is already producing unwritten texels. The battle swirl's
two half-rects fit; they are untouched.

This complements `ff7nx_fbfit` rather than replacing it: this one keeps the
full width by MOVING the window, and fbfit remains the backstop for a rect that
is larger than the whole surface and therefore cannot be made to fit.

THE HOOK
========
    +0x10D7124   mov  w19, w0       surface WIDTH   (vtable +0x28)
    +0x10D7134   blr  x8            -> w0 = surface HEIGHT (vtable +0x40)
    +0x10D7138   ldr  w23, [x20, #0x14]                      <-- THE HOOK
    +0x10D713C   ldr  w8,  [x20, #0x1c]

w0 must survive: it is compared at +0x10D716C. The cave reads it and never
writes it, and borrows only w15/w16/w17.
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
import ff7nx_cave                                              # noqa: E402
import nxmap                                                   # noqa: E402

WINDOW_ENV = 'SEVENTH_NX_FB_WINDOW'

HOOK = 0x10D7138                   # ldr w23, [x20, #0x14]
HOOK_STOCK = 0xB9401697
RETURN_VA = 0x10D713C

TEX = 20                           # x20  the tex header
SURF_W, SURF_H = 19, 0             # w19, w0 -- queried from the surface
S1, S2, S3 = 15, 16, 17            # the borrowed scratch
FB_X_OFF, FB_Y_OFF = 0x14, 0x18
FB_W_OFF, FB_H_OFF = 0x1C, 0x20

COND_LE, COND_LT = 13, 11
N_WORDS = 20

DEFAULT_ON = True                  # SEVENTH_NX_FB_WINDOW=0 turns it off

ANCHORS = {
    0x10D7124: 0x2A0003F3,         # mov  w19, w0     surface WIDTH
    0x10D7134: 0xD63F0100,         # blr  x8          -> w0 = surface HEIGHT
    0x10D713C: 0xB9401E88,         # ldr  w8,  [x20, #0x1c]
    0x10D7148: 0x1A88B278,         # csel w24, w19, w8, lt
    0x10D715C: 0xB9401A99,         # ldr  w25, [x20, #0x18]
    0x10D7160: 0xB9402289,         # ldr  w9,  [x20, #0x20]
    0x10D716C: 0x6B09001F,         # cmp  w0,  w9     w0 still the height
    0x10D7170: 0x1A89B016,         # csel w22, w0, w9, lt
}


def _clamp_axis(addr, first, off_origin, off_size, surf, skip_to):
    """if (origin + size > surf) origin = max(0, surf - size). Nine words."""
    return [
        A.ldr(S1, TEX, off_origin),                    # +0 origin
        A.ldr(S2, TEX, off_size),                      # +1 size
        A.add_reg(S3, S1, S2),                         # +2 origin + size
        A.cmp_reg(S3, surf),                           # +3
        A.bcond(addr(first + 4), addr(skip_to), COND_LE),   # +4 it fits
        A.sub_reg(S1, surf, S2),                       # +5 surf - size
        A.cmp_reg(S1, A.WZR),                          # +6
        A.csel(S1, A.WZR, S1, COND_LT),                # +7 max(0, ...)
        A.str_(S1, TEX, off_origin),                   # +8
    ]


def body_words(addr):
    w = _clamp_axis(addr, 0, FB_X_OFF, FB_W_OFF, SURF_W, 9)
    w += _clamp_axis(addr, 9, FB_Y_OFF, FB_H_OFF, SURF_H, 18)
    w.append(HOOK_STOCK)                               # 18 ldr w23,[x20,#0x14]
    w.append(A.b(addr(19), RETURN_VA))                 # 19
    return w


def enabled() -> bool:
    v = os.environ.get(WINDOW_ENV)
    if v is None:
        return DEFAULT_ON
    return v.strip().lower() not in ('', '0', 'off', 'false', 'no')


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _b_target(word, va):
    if (word & 0xFC000000) != 0x14000000:
        return None
    off = word & 0x03FFFFFF
    if off & 0x02000000:
        off -= 0x04000000
    return va + off * 4


def _walk(img):
    entry = _b_target(_word(img, HOOK), HOOK)
    if entry is None:
        return [], []
    logical, physical, seen, va = [], [], set(), entry
    while len(logical) < N_WORDS and va not in seen and 0 <= va <= len(img) - 4:
        seen.add(va)
        physical.append(va)
        tgt = _b_target(_word(img, va), va)
        if tgt is not None and tgt != RETURN_VA:
            va = tgt
            continue
        logical.append(va)
        va += 4
    return logical, physical


def installed(img):
    if _word(img, HOOK) == HOOK_STOCK:
        return False
    addrs, _ = _walk(img)
    if len(addrs) != N_WORDS:
        return None
    if [_word(img, a) for a in addrs] == body_words(lambda i: addrs[i]):
        return True
    return None


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X' % (va, got, want))
    if installed(img) is None:
        bad.append('+0x%X carries neither the stock `ldr w23, [x20, #0x14]` '
                   'nor a cave this build wrote' % HOOK)
    return bad


def read_state(img) -> str:
    st = installed(img)
    if st is None:
        return 'unknown'
    return ('capture window slid onto the surface' if st else
            'capture window stock (a rect may hang off the surface)')


def plan(m, revert=False):
    import cave_space
    img = m.img
    problems = verify(img)
    if problems:
        return [], [], problems
    want = False if (revert or not enabled()) else True
    have = installed(img)
    if have == want:
        return [], [], []

    patches, notes = [], []
    if have:
        for va in _walk(img)[1]:
            patches.append({'name': 'clear capture-window cave', 'va': hex(va),
                            'expect': struct.pack('<I', _word(img, va)).hex(),
                            'set': '00000000'})
        patches.append({'name': 'restore the origin load', 'va': hex(HOOK),
                        'expect': struct.pack('<I', _word(img, HOOK)).hex(),
                        'set': struct.pack('<I', HOOK_STOCK).hex()})
        return patches, ['    capture window back to stock'], []

    pool = ff7nx_cave.HolePool(
        img, starts=set(m.arm_starts),
        named=cave_space.named_targets(img[cave_space.RODATA:]))
    try:
        runs = pool.take(N_WORDS, span=0x80000)
        slots = ff7nx_cave.slots(runs, N_WORDS)
        placed = ff7nx_cave.link(runs, body_words(lambda i: slots[i]))
    except ff7nx_cave.NoRoom as exc:
        return [], [], ['capture-window cave: %s' % exc]
    placed[HOOK] = A.b(HOOK, slots[0])
    for va, wd in sorted(placed.items()):
        patches.append({'name': 'slide the capture window onto the surface',
                        'va': hex(va),
                        'expect': struct.pack('<I', _word(img, va)).hex(),
                        'set': struct.pack('<I', wd).hex()})
    notes.append('    a rect that hangs off the surface is moved back on, '
                 'against the surface\'s OWN runtime size (cave entry +0x%X); '
                 'a rect that already fits is untouched' % slots[0])
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'capture window', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fbwindow-')
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
        log('  refusing to change the capture window.')
        return 1
    log('  capture window (%s=%s):'
        % (WINDOW_ENV, 'off' if (revert or not enabled()) else 'on'))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d capture-window word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
