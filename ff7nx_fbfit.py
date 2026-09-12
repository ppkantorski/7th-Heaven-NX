#!/usr/bin/env python3
r"""
ff7nx_fbfit.py -- make every framebuffer capture's TEXTURE exactly the size of
the region that actually gets written into it.

THE BLACK, IN ONE SENTENCE
==========================
The CPU capture path copies

    cols = min(surface_w, x + w) - x        rows = min(surface_h, y + h) - y

pixels into the TOP-LEFT of a `fb_tex.w` x `fb_tex.h` texture, and the effect's
mesh samples the WHOLE texture. Whenever the requested rect runs past the
surface, the copy writes a smaller rectangle than the texture it is writing
into, and everything past it is never written -- that is the black. The
captured content is correct; it just ends up occupying a corner instead of the
whole sheet, so it is drawn small and the rest of the field is empty.

Patrick's description of it, which is what sent me here:

    "its like taking a screenshot that is low quality, squeezing that into a
     space so it achieves the right pixel density, but its ending up tiny
     because its original pixel density for the capture was extremely low"

And that is also why the stock build smeared a low-density image across the
whole field with black squares along one edge: at 256x256 the shortfall is
small, so most of the sheet is filled and the missing strip shows up as the
squares.

THE FIX, AND WHY IT NEEDS NO GATE
=================================
Vanilla keeps one invariant that this port dropped: the texture is created at
exactly the size of the blit (`createBlitTexture` takes the same scaled rect
that `blitTexture` copies -- FFNx `src/renderer.cpp`). Restore it:

    fb_tex.w = cols        fb_tex.h = rows

written back into the tex header after the bounds are computed and before the
copy loop runs. Then the written region IS the texture, every time.

This is safe for the other twenty-four captures **by construction**: where the
rect already fits inside the surface, `cols == fb_tex.w` and `rows ==
fb_tex.h`, so the stores write the values that are already there and the build
is byte-for-byte the same game. Only a capture that is currently being
truncated changes -- and a truncated capture is already broken.

    the battle swirl   fits    -> cols == fb_tex.w   -> no change
    a plain 1:1        fits                          -> no change
    Kujata             clamped -> texture shrinks to what was really captured

The destination row stride is re-read from `fb_tex.w` inside the loop
(+0x10D7220), so it follows. The image buffer was allocated earlier from the
LARGER size (+0x10D70B8), so shrinking can only ever under-use it, never
overrun it. `tex_format.width/height` are untouched, so `u_offset =
1/tex_format.width` is unchanged and no UV, mesh or disc geometry moves.

THE HOOK
========
    +0x10D71A0   sub  w22, w22, w25     rows are already in w22 here
    +0x10D71C0   sub  w10, w24, w23     cols                      <-- THE HOOK
    +0x10D71C4   cmp  w10, #1
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

FIT_ENV = 'SEVENTH_NX_FB_FIT'

HOOK = 0x10D71C0                   # sub w10, w24, w23
HOOK_STOCK = 0x4B17030A
RETURN_VA = 0x10D71C4              # cmp w10, #1

TEX = 20                           # x20 -- the tex header
COLS, ROWS = 10, 22                # w10 = columns, w22 = rows
END, ORG = 24, 23                  # w24 = min(surf_w, x+w), w23 = x
S1, S2 = 15, 16                    # borrowed scratch, dead here
FB_W_OFF, FB_H_OFF = 0x1C, 0x20
TEX_W_OFF = 0x3C

COND_NE, COND_GT = 1, 12
N_WORDS = 10

DEFAULT_ON = True                  # SEVENTH_NX_FB_FIT=0 turns it off

# The bounds computation either side of the hook, so a game update cannot move
# the two registers this cave writes from.
ANCHORS = {
    # +0x10D7138 is shared with ff7nx_fbwindow, which replaces it with a branch
    # into its own cave; see ORIGIN_LOAD below.
    0x10D713C: 0xB9401E88,         # ldr  w8,  [x20, #0x1c]   fb_tex.w
    0x10D7148: 0x1A88B278,         # csel w24, w19, w8, lt    min(surf_w, x+w)
    0x10D715C: 0xB9401A99,         # ldr  w25, [x20, #0x18]   y
    0x10D7160: 0xB9402289,         # ldr  w9,  [x20, #0x20]   fb_tex.h
    0x10D7170: 0x1A89B016,         # csel w22, w0, w9, lt     min(surf_h, y+h)
    0x10D71A0: 0x4B1902D6,         # sub  w22, w22, w25       rows
    0x10D71B8: 0x710006DF,         # cmp  w22, #1
    0x10D71C4: 0x7100055F,         # cmp  w10, #1
    0x10D7220: 0xB9401E8C,         # ldr  w12, [x20, #0x1c]   the row stride
}


def _cmp_reg_lsl(rn, rm, sh):
    """CMP Wn, Wm, LSL #sh."""
    return 0x6B000000 | (sh << 10) | (rm << 16) | (rn << 5) | A.WZR


def _surf_shift():
    """log2 of ff7nx_fbsurf's scale, so this gate cannot drift from it."""
    try:
        import ff7nx_fbsurf
        return {1: 0, 2: 1, 4: 2}[ff7nx_fbsurf.scale()]
    except Exception:                                          # noqa: BLE001
        return 0


def body_words(addr):
    """BUILD 301 -- the opposite direction, and the right one.

    This used to SHRINK the texture to the region that was written. That kept
    the sheet fully covered but changed `fb_tex.w` after the buffer had already
    been allocated from the old value, so the allocation, the row stride and
    whatever uploads the texture no longer agreed on one width.

    FFNx hard-codes the UV step at 1/256 (animations.cpp:868), so the mesh
    spans the whole sheet no matter what. The sheet therefore has to be
    completely written at its AUTHORED size, and the way to do that is to run
    the copy loop for `fb_tex.w` columns and let `ff7nx_fbresample`'s stretch
    body supply each one:

        loop bound  =  fb_tex.w        (the whole sheet, always)
        src_col     =  i * avail / fb_tex.w

    When `avail == fb_tex.w` the division is the identity and the pair is the
    stock 1:1 copy byte for byte -- which is why neither half needs a gate.
    """
    keep = 9
    return [
        HOOK_STOCK,                                # 0 sub w10, w24, w23 avail
        A.ldr(S1, TEX, FB_W_OFF),                  # 1 dest_w
        A.ldr(S2, TEX, TEX_W_OFF),                 # 2 tex_format.width
        # BUILD 309: `<< surf_shift()` follows ff7nx_fbsurf, which scales the
        # capture surface and the rect together. At the stock surface the shift
        # is 0 and this is the identical word to `cmp S1, S2`.
        _cmp_reg_lsl(S1, S2, _surf_shift()),       # 3
        A.bcond(addr(3 + 1), addr(keep), COND_NE),  # 4 not a 1:1 capture
        A.add_reg_lsl(S2, S1, S1, 1),              # 5 3 * dest_w
        _cmp_reg_lsl(S2, COLS, 2),                 # 6 3*dest_w vs 4*avail
        A.bcond(addr(6 + 1), addr(keep), COND_GT),  # 7 too little source
        A.mov_reg(COLS, S1),                       # 8 fill the whole sheet
        A.b(addr(keep), RETURN_VA)]                # 9


def enabled() -> bool:
    """Never on its own.

    Raising the loop bound is only safe because ff7nx_fbresample's 0.749 step
    is what supplies the source column -- its furthest read is
    0.749*(dest_w-1), which the 3/4 guard keeps inside the surface. With the
    resample OFF the stock 1:1 body runs instead, its furthest read is
    dest_w-1, and a raised bound would read straight off the end. So this
    follows the resample.
    """
    v = os.environ.get(FIT_ENV)
    want = DEFAULT_ON if v is None else (
        v.strip().lower() not in ('', '0', 'off', 'false', 'no'))
    if not want:
        return False
    try:
        import ff7nx_fbresample
        return ff7nx_fbresample.enabled()
    except Exception:                                          # noqa: BLE001
        return False


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


def installed(img) -> bool | None:
    """True when the cave is in, False when stock, None when unrecognised."""
    if _word(img, HOOK) == HOOK_STOCK:
        return False
    addrs, _ = _walk(img)
    if len(addrs) != N_WORDS:
        return None
    if [_word(img, a) for a in addrs] == body_words(lambda i: addrs[i]):
        return True
    return None


ORIGIN_LOAD = 0x10D7138          # ldr w23, [x20, #0x14] -- ff7nx_fbwindow's hook
ORIGIN_LOAD_STOCK = 0xB9401697


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X' % (va, got, want))
    # Shared with ff7nx_fbwindow: either the stock load or a branch into its
    # gate is legitimate, anything else means the routine moved.
    org = _word(img, ORIGIN_LOAD)
    if org != ORIGIN_LOAD_STOCK and (org & 0xFC000000) != 0x14000000:
        bad.append('+0x%X is %08X -- neither the stock `ldr w23, [x20, #0x14]` '
                   'nor a branch into ff7nx_fbwindow\'s cave'
                   % (ORIGIN_LOAD, org))
    if installed(img) is None:
        bad.append('+0x%X carries neither the stock `sub w10, w24, w23` nor a '
                   'cave this build wrote' % HOOK)
    return bad


def read_state(img) -> str:
    st = installed(img)
    if st is None:
        return 'unknown'
    return ('copy loop fills the whole sheet (fb_tex.w columns)' if st else
            'copy loop bound stock (stops at what the surface holds)')


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
            patches.append({'name': 'clear capture-fit cave', 'va': hex(va),
                            'expect': struct.pack('<I', _word(img, va)).hex(),
                            'set': '00000000'})
        patches.append({'name': 'restore the column count', 'va': hex(HOOK),
                        'expect': struct.pack('<I', _word(img, HOOK)).hex(),
                        'set': struct.pack('<I', HOOK_STOCK).hex()})
        return patches, ['    capture texture size back to stock'], []

    pool = ff7nx_cave.HolePool(
        img, starts=set(m.arm_starts),
        named=cave_space.named_targets(img[cave_space.RODATA:]))
    try:
        runs = pool.take(N_WORDS, span=0x80000)
        slots = ff7nx_cave.slots(runs, N_WORDS)
        placed = ff7nx_cave.link(runs, body_words(lambda i: slots[i]))
    except ff7nx_cave.NoRoom as exc:
        return [], [], ['capture-fit cave: %s' % exc]
    placed[HOOK] = A.b(HOOK, slots[0])
    for va, wd in sorted(placed.items()):
        patches.append({'name': 'fit capture texture to what is written',
                        'va': hex(va),
                        'expect': struct.pack('<I', _word(img, va)).hex(),
                        'set': struct.pack('<I', wd).hex()})
    notes.append('    the copy loop runs for fb_tex.w columns, so every texel '
                 'the UVs address is written (cave entry +0x%X); paired with '
                 'ff7nx_fbresample=stretch, and identical to stock whenever '
                 'the source already fills the sheet' % slots[0])
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'capture texture fit', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fbfit-')
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
        log('  refusing to change the capture texture size.')
        return 1
    log('  capture texture fit (%s=%s):'
        % (FIT_ENV, 'off' if (revert or not enabled()) else 'on'))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d capture-fit word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
