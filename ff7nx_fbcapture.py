#!/usr/bin/env python3
r"""
ff7nx_fbcapture.py -- give the SUMMONS' framebuffer snapshots the widescreen
coordinate transform, and nothing else.

THE SYMPTOM
===========
Every summon that warps the battlefield floor -- Titan's rising slab,
Kujata's earth, Alexander, the opening of Knights of the Round -- loses part
of its floor. The missing part is welded to the geometry, its edges are
straight and hard, and it is black in places and transparent in others.
Static battle is fine. Fields are fine. FINDINGS-247 is the full write-up.

THE PATH
========
These effects do not texture themselves from `magic.lgp`. They snapshot the
frame and map the snapshot onto their mesh -- FF7's framebuffer-texture path,
`sub_673F5C` on PC, which FFNx replaces with `make_framebuffer_tex`
(`src/ff7/graphics.cpp:368`). In this port:

    guest 0x673F5C
      -> .text+0xB015C0            the recompiled body
      -> .text+0xB0166C  bl        its only call
      -> .text+0x10F2380           native thunk; unpacks struc_91 into
                                   (field_0, tex_w, tex_h, x, y,
                                    w = width*xscale, h = height*yscale,
                                    color_key != 0)
      -> .text+0x10F23B4  b        tail branch          <-- THE HOOK
      -> .text+0x10DBB50           make_framebuffer_tex; version 100 and
                                   fb_tex.x/.y/.w/.h
      -> .text+0x10D7050           the loader: `cmp w8, #0x64` on the
                                   version, CPU readback or GPU quad

`+0x10DBB50` has exactly ONE predecessor in the module (the tail branch at
+0x10F23B4, whose own only caller is +0xB0166C), so the hook below sees every
framebuffer capture in the game and nothing else.

The surface it captures from is a dedicated **640x480 staging surface**,
created in `gfx_drv_init`:

    +0x10D5970  mov  x8, #0x280            \  640 x 480
    +0x10D5974  movk x8, #0x1e0, lsl #32   /
    +0x10D5994  str  w0, [x8]                 -> [[0x12CE620]], the index the
                                                capture at +0x10D7050 uses

and end-scene (+0x10DACB0) fills it by binding it as a render target and
drawing ONE quad over all of it, so it holds the whole frame scaled to
640x480 -- not a crop.

`make_framebuffer_tex` converts the rect to staging columns with

    +0x10DBC3C  add   w11, w27, w27, lsl #2    x * 5
    +0x10DBC54  lsl   w11, w11, #7             x * 640
    +0x10DBC58  umull x11, w11, w12            * 0xCCCCCCCD
    +0x10DBC70  lsr   x11, x11, #0x29          / 640          => x

i.e. the identity: FFNx's `getInternalCoordX` with framebufferWidth and
game_width both folded to the staging surface's own 640. Under `ws-3d` the
frame is 853.33 game units wide spanning game x -106.67..746.67, so 640
staging columns are NOT 640 game units and the identity is wrong by exactly
the widescreen transform.

THE CORRECTION, AND WHO GETS IT
===============================
    x' = (x + 107) * 640 / 854          w' = w * 640 / 854

which is FFNx's `getInternalCoordX(x + abs(wide_viewport_x))` (the form it
uses for scissor rects, `src/renderer.cpp:1689`) with framebufferWidth = 640
because that is what the staging surface is. Horizontal only: `ws-3d` widens
and does nothing vertical, so y and h are never touched.

**It is applied only when `xscale == 1`, and that gate is the whole point of
this file being written twice.**

Build 244 applied the correction in `make_framebuffer_tex`, to every caller.
It fixed the summons and broke the battle-entry swirl, and the reason is a
real distinction between two kinds of caller, not a special case:

    xscale == 1   the capture rect IS the texture, 1:1, in game units. 27
                  functions build a rect this way; the floor-warp summons are
                  among them (Titan's, at guest 0x8C9698, is 192x256 scale 1).
                  For these `w` is a length in game units and scaling it is
                  the correction.

    xscale >  1   PSX page arithmetic. The width and height fields are first
                  ROUNDED UP TO POWERS OF TWO (`0x690240` tests, `0x690270`
                  rounds; see `0x682DB2`+0x2C0, which copies the object's
                  +0xDC..+0xE8 into the struc_91 through them), and the rect
                  is deliberately larger than the frame -- the effect's own
                  UVs address only the meaningful corner of the result. The
                  battle-entry swirl is this: two tiles whose width field 160
                  becomes 256, times xscale 2, so it asks for w = 512 out of a
                  640-wide surface ON PURPOSE and relies on `fb_tex.w` keeping
                  that value. Scaling `w` moves every UV constant the effect
                  was authored with, which is what made one half of the swirl
                  discontinuous with the other.

`w == width` iff `xscale == 1`, and the thunk already has both in registers
(`w1` = width, `w5` = width*xscale), so the gate costs one `cmp`.

THE PATCH
=========
`make_framebuffer_tex` is left completely STOCK. One word changes in the
module -- the thunk's tail branch -- and the arithmetic lives in a branch-free
cave in padding, the same shape `ff7nx_uiclip` uses:

    +0x10F23B4  b #0x10DBB50   ->   b <cave>

    cave:  cmp   w5, w1                 xscale == 1 ?
           movz  w9, #<magic lo>        \  640/854
           movk  w9, #<magic hi>, 16    /
           add   w8, w3, #107           x + 107
           umull x8, w8, w9
           lsr   x8, x8, #32
           csel  w3, w8, w3, eq         x' only for 1:1 captures
           umull x8, w5, w9
           lsr   x8, x8, #32
           csel  w5, w8, w5, eq         w' likewise
           b     #0x10DBB50

Eleven words of dead alignment padding, no cave-budget cost (ff7nx_cave), and
byte-exactly reversible. `w8` and `w9` are dead at the hook -- w8 was copied
to w0 at +0x10F23AC and w9 consumed by the `cset` at +0x10F23A8 -- and the
body writes no flags between the `cmp` and the two `csel`s. All three are
asserted from the disassembly by `tests/test_fbcapture.py`, not from this
paragraph.

The magic is not a constant in this file. `reciprocal()` derives it from
`ff7nx_ws.WIDE_VIEWPORT_WIDTH` / `WIDE_VIEWPORT_X` and **proves it
exhaustively** against integer `n * 640 // 854` for every n a rect can carry,
raising rather than returning an approximation.

**ON with 16:9, OFF at 4:3**, where the frame really is 640 units wide and the
stock identity is already correct -- the same gate and the same reasoning as
`ff7nx_battlewide`. `SEVENTH_NX_FB_CAPTURE=0` forces it off for an A/B.

WHAT WOULD FALSIFY IT
=====================
If the floor still tears with this on, the capture rect is not the source of
the missing region and this file is wrong -- a cheap, decisive answer that
clears the whole framebuffer-capture path in one build.

The independent check costs nothing extra: **the defect must also disappear
with `widescreen` set to off**, with or without this patch. Every line above
says the cause is the 16:9 transform. If it survives 4:3, the analysis is
wrong at the root.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

# ONLY this directory -- same note as ff7nx_fieldbuf and ff7nx_heap: a stale
# loose copy of any of these beside the project folder shadows the real one
# and the shadowing cascades.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                # noqa: E402
import ff7nx_cave                                              # noqa: E402

FBCAP_ENV = 'SEVENTH_NX_FB_CAPTURE'

# The staging surface the capture reads, asserted against the module.
STAGING_W = 640
STAGING_H = 480
STAGING_SITE = 0x10D5970          # mov x8, #0x280 ; movk x8, #0x1e0, lsl #32

# The hook: the thunk's tail branch into make_framebuffer_tex.
HOOK = 0x10F23B4
RETURN_VA = 0x10DBB50
HOOK_STOCK = 0x17FFA5E7           # b #0x10dbb50
N_BODY = 10                       # logical words before the tail branch
N_WORDS = N_BODY + 1

COND_EQ = 0


# --------------------------------------------------------------------------
# the widescreen geometry, taken from the module that owns it
# --------------------------------------------------------------------------
def geometry() -> tuple:
    """(span, offset): the frame is `span` game units wide starting at
    `-offset`. Read from ff7nx_ws so one edit moves both."""
    import ff7nx_ws
    return int(ff7nx_ws.WIDE_VIEWPORT_WIDTH), int(-ff7nx_ws.WIDE_VIEWPORT_X)


def reciprocal(num: int = STAGING_W, den: int = None, limit: int = 65536):
    """
    (magic, shift) such that `(n * magic) >> shift == n * num // den` for
    every n in [0, limit).

    Searched from the LARGEST shift down, so the magic is the most precise one
    that still fits the 32 bits a `movz`/`movk` pair can build and the 32-bit
    operand of `umull`. Raises rather than returning an approximation: a fix
    that is right to within a pixel is not what this file is for.
    """
    if den is None:
        den = geometry()[0]
    for shift in range(40, 23, -1):
        ideal = (1 << shift) * num // den
        for magic in range(max(1, ideal - 4), ideal + 6):
            if magic > 0xFFFFFFFF:
                continue
            if all(((n * magic) >> shift) == (n * num) // den
                   for n in range(limit)):
                return magic, shift
    raise ValueError('no exact 32-bit reciprocal for %d/%d' % (num, den))


def map_x(x: int) -> int:
    """The staging column a game-space x lands on, as the cave computes it."""
    magic, shift = reciprocal()
    off = geometry()[1]
    return (((x + off) & 0xFFFFFFFF) * magic) >> shift


def map_w(w: int) -> int:
    magic, shift = reciprocal()
    return ((w & 0xFFFFFFFF) * magic) >> shift


# --------------------------------------------------------------------------
# encoders a64 does not carry
# --------------------------------------------------------------------------
def umull(rd: int, rn: int, rm: int) -> int:
    """UMULL Xd, Wn, Wm -- the UMADDL alias with Xa = XZR."""
    return 0x9BA07C00 | (rm << 16) | (rn << 5) | rd


def lsr64(rd: int, rn: int, shift: int) -> int:
    """LSR Xd, Xn, #shift -- UBFM Xd, Xn, #shift, #63."""
    if not 0 <= shift <= 63:
        raise ValueError('shift %d out of range' % shift)
    return 0xD3400000 | (1 << 22) | (shift << 16) | (63 << 10) | (rn << 5) | rd


def hx(word: int) -> str:
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


# --------------------------------------------------------------------------
# the cave body
# --------------------------------------------------------------------------
def body_words() -> list:
    """
    The ten position-independent words. The tail branch is added by the
    builder, which is the only word that needs to know where it landed.
    """
    magic, shift = reciprocal()
    off = geometry()[1]
    return [
        A.cmp_reg(5, 1),                    # cmp   w5, w1     xscale == 1 ?
        A.movz(9, magic & 0xFFFF),          # movz  w9, #lo
        A.movk_hi(9, magic >> 16),          # movk  w9, #hi, lsl #16
        A.add_imm(8, 3, off),               # add   w8, w3, #107
        umull(8, 8, 9),                     # umull x8, w8, w9
        lsr64(8, 8, shift),                 # lsr   x8, x8, #shift
        A.csel(3, 8, 3, COND_EQ),           # csel  w3, w8, w3, eq
        umull(8, 5, 9),                     # umull x8, w5, w9
        lsr64(8, 8, shift),                 # lsr   x8, x8, #shift
        A.csel(5, 8, 5, COND_EQ),           # csel  w5, w8, w5, eq
    ]


# --------------------------------------------------------------------------
# anchors -- everything that must still be true of this port
# --------------------------------------------------------------------------
# The thunk, whole. It is the argument unpacker, so its register assignment
# IS the contract the cave depends on: w1 = width, w3 = x, w5 = width*xscale.
THUNK = {
    0x10F2380: 0xA9BF7BFD,   # stp   x29, x30, [sp, #-0x10]!
    0x10F2384: 0x910003FD,   # mov   x29, sp
    0x10F2388: 0x94002806,   # bl    #0x10fc3a0        guest ptr -> host
    0x10F238C: 0x29410404,   # ldp   w4, w1, [x0, #8]  y_offset, width
    0x10F2390: 0x29422002,   # ldp   w2, w8, [x0, #0x10]  height, xscale
    0x10F2394: 0x1B017D05,   # mul   w5, w8, w1        w = width * xscale
    0x10F2398: 0x29432408,   # ldp   w8, w9, [x0, #0x18] yscale, color_key
    0x10F239C: 0x1B027D06,   # mul   w6, w8, w2        h = height * yscale
    0x10F23A0: 0x29400C08,   # ldp   w8, w3, [x0]      field_0, x_offset
    0x10F23A4: 0x7100013F,   # cmp   w9, #0            (consumes w9)
    0x10F23A8: 0x1A9F07E7,   # cset  w7, ne            color_key != 0
    0x10F23AC: 0x2A0803E0,   # mov   w0, w8            (frees w8)
    0x10F23B0: 0xA8C17BFD,   # ldp   x29, x30, [sp], #0x10
}

# `make_framebuffer_tex`'s identity arithmetic. This file exists BECAUSE it is
# the identity; if a game update makes it anything else, the cave would be
# correcting something already corrected, so it is an anchor, not a patch.
IDENTITY = {
    0x10DBBF0: 0x321B0FED,   # mov   w13, #0x1e0        480, the y/h scale
    0x10DBBF8: 0x529999AC,   # mov   w12, #0xcccd       \ 0xCCCCCCCD = 1/640
    0x10DBBFC: 0x72B9998C,   # movk  w12, #0xcccc, 16   /
    0x10DBC3C: 0x0B1B0B6B,   # add   w11, w27, w27, lsl #2   x * 5
    0x10DBC54: 0x5319616B,   # lsl   w11, w11, #7            x * 640
    0x10DBC58: 0x9BAC7D6B,   # umull x11, w11, w12
    0x10DBC5C: 0x52800C8A,   # mov   w10, #0x64         version 100
    0x10DBC60: 0xB900034A,   # str   w10, [x26]
    0x10DBC70: 0xD369FD6B,   # lsr   x11, x11, #0x29         / 640
    0x10DBC78: 0x2902A74B,   # stp   w11, w9, [x26, #0x14]   fb_tex.x, .y
    0x10DBC7C: 0x0B150AA9,   # add   w9, w21, w21, lsl #2    w * 5
    0x10DBC80: 0x53196129,   # lsl   w9, w9, #7              w * 640
    0x10DBC84: 0x9BAC7D29,   # umull x9, w9, w12
    0x10DBC88: 0xD369FD29,   # lsr   x9, x9, #0x29           / 640
    0x10DBC94: 0x2903A349,   # stp   w9, w8, [x26, #0x1c]    fb_tex.w, .h
}

# The 640x480 staging surface and the consumer's version gate.
SURFACE = {
    STAGING_SITE:     0xD2805008,   # mov  x8, #0x280
    STAGING_SITE + 4: 0xF2C03C08,   # movk x8, #0x1e0, lsl #32
    0x10D5994:        0xB9000100,   # str  w0, [x8]     -> [[0x12CE620]]
    0x10D598C:        0xF9431108,   # ldr  x8, [x8, #0x620]
    0x10D70DC:        0xF9431339,   # ldr  x25, [x25, #0x620]  the consumer
    0x10D7070:        0xB9400028,   # ldr  w8, [x1]     tex_header->version
    0x10D7074:        0x7101911F,   # cmp  w8, #0x64
}

ANCHORS = {}
ANCHORS.update(THUNK)
ANCHORS.update(IDENTITY)
ANCHORS.update(SURFACE)


# --------------------------------------------------------------------------
# gate
# --------------------------------------------------------------------------
def enabled() -> bool:
    """
    ON with 16:9, OFF at 4:3, overridable for an A/B.

    Every number this module writes is a widescreen number. At 4:3 the frame
    really is 640 units wide, the stock identity is already right, and this
    would shrink every 1:1 capture to 75%. Same gate and same reasoning as
    ff7nx_battlewide.
    """
    v = os.environ.get(FBCAP_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false', 'no')
    try:
        import ff7nx_ws
        return ff7nx_ws.enabled()
    except Exception:                                          # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# module inspection
# --------------------------------------------------------------------------
def _word(img, va: int) -> int:
    return struct.unpack_from('<I', img, va)[0]


def _b_target(word: int, va: int):
    """The target of an unconditional `b`, or None."""
    if (word & 0xFC000000) != 0x14000000:
        return None
    off = word & 0x03FFFFFF
    if off & (1 << 25):
        off -= 1 << 26
    return va + off * 4


def cave_state(img) -> str:
    """'stock', 'patched' or 'unknown', from the hook word alone."""
    w = _word(img, HOOK)
    if w == HOOK_STOCK:
        return 'stock'
    return 'patched' if _b_target(w, HOOK) is not None else 'unknown'


def walk_physical(img) -> list:
    """
    Every ADDRESS the cave occupies, chaining branches included.

    revert needs the footprint, not the logic: zeroing only the logical words
    leaves link branches behind as live code in someone else's padding, which
    makes the next allocator skip a usable hole and the next apply->revert
    fail its byte-identity check. The rule -- a `b` that is not to RETURN_VA
    is a run-to-run link, never logic -- is exact because the body is
    branch-free by construction.
    """
    entry = _b_target(_word(img, HOOK), HOOK)
    if entry is None or entry == RETURN_VA:
        return []
    va, out = entry, []
    while len(out) < 64 and va not in out:
        out.append(va)
        b = _b_target(_word(img, va), va)
        if b == RETURN_VA:
            return out
        if b is not None:
            va = b
            continue
        va += 4
    return []


def walk(img) -> list:
    """The cave's LOGICAL [(va, word), ...], chaining branches removed."""
    entry = _b_target(_word(img, HOOK), HOOK)
    if entry is None or entry == RETURN_VA:
        return []
    va, out, seen = entry, [], set()
    while len(out) < 64 and va not in seen:
        seen.add(va)
        w = _word(img, va)
        b = _b_target(w, va)
        if b is not None and b != RETURN_VA:
            va = b
            continue
        out.append((va, w))
        if b == RETURN_VA:
            return out
        va += 4
    return []


def installed(img) -> bool:
    """True when the cave present is exactly the one this file builds."""
    if cave_state(img) != 'patched':
        return False
    wk = walk(img)
    if len(wk) != N_WORDS:
        return False
    body = body_words()
    if [w for _, w in wk[:N_BODY]] != body:
        return False
    tail_va, tail = wk[N_BODY]
    return tail == A.b(tail_va, RETURN_VA)


def read_state(img) -> str:
    if cave_state(img) == 'stock':
        return 'stock'
    return 'wide' if installed(img) else 'unknown'


def verify(img) -> list:
    """Everything that must hold before a word is written."""
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('+0x%X is %08X, expected %08X' % (va, got, want))
    st = cave_state(img)
    if st == 'unknown':
        bad.append('hook +0x%X is %08X -- neither the stock branch nor a '
                   'branch into a cave' % (HOOK, _word(img, HOOK)))
    elif st == 'patched' and not installed(img):
        bad.append('hook +0x%X branches to a cave this build does not '
                   'recognise' % HOOK)
    return bad


def describe() -> str:
    magic, shift = reciprocal()
    span, off = geometry()
    return ("1:1 captures only: x' = (x + %d) * %d / %d, w' = w * %d / %d "
            "(magic 0x%08X >> %d); page-scaled captures (xscale > 1, the "
            "battle swirl) left stock"
            % (off, STAGING_W, span, STAGING_W, span, magic, shift))


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------
def build_patches(img, starts=None, log=lambda *_: None):
    """{va: word} for the cave and the hook, or None if it cannot be done."""
    if _word(img, HOOK) != HOOK_STOCK:
        log('  ! hook +0x%X is %08X, not the stock tail branch'
            % (HOOK, _word(img, HOOK)))
        return None
    pool = ff7nx_cave.HolePool(bytearray(img), starts=starts)

    def builder(_entry, addr):
        return body_words() + [A.b(addr(N_BODY), RETURN_VA)]

    entry, words = ff7nx_cave.emit_laid_out(pool, builder)
    words[HOOK] = A.b(HOOK, entry)
    log('  fb capture cave: %d words in verified padding, entry +0x%X'
        % (N_WORDS, entry))
    return words


def revert_patches(img, log=lambda *_: None):
    """{va: word} that puts the hook back and returns the padding."""
    if cave_state(img) == 'stock':
        return {}
    phys = walk_physical(img)
    if not phys:
        log('  ! the hook is a branch but the cave cannot be walked')
        return None
    if not installed(img):
        log('  ! the cave is not the one this build makes; refusing to guess '
            'what to restore')
        return None
    out = {HOOK: HOOK_STOCK}
    for va in phys:
        out[va] = 0
    log('  fb capture cave removed (%d word(s) of padding returned)'
        % len(phys))
    return out


def spec(img, starts=None, revert: bool = False, log=lambda *_: None):
    """An nso_patcher spec, or None when there is nothing to write."""
    state = read_state(img)
    want = 'stock' if revert else 'wide'
    if state == want:
        return None
    if state == 'unknown':
        raise ValueError('the module carries an unrecognised cave; refusing')
    words = (revert_patches(img, log) if revert
             else build_patches(img, starts, log))
    if not words:
        return None
    out = []
    for va in sorted(words):
        out.append({'name': '+0x%X' % va,
                    'va': hex(va),
                    'expect': hx(_word(img, va)),
                    'set': hx(words[va])})
    return {'name': 'framebuffer capture rect -> %s' % want, 'patches': out}


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------
def apply_to_nso(src, dest, log=lambda *_: None, revert: bool = False) -> bool:
    """Write `dest` from `src`. False when there was nothing to change."""
    from pathlib import Path as _P
    import nxmap
    try:
        import nso_patcher
    except ImportError as exc:                             # pragma: no cover
        log('! fb capture: cannot import nso_patcher (%s)' % exc)
        return False

    module = nxmap.Main(str(src))
    bad = verify(module.img)
    if bad:
        log('! fb capture: module does not match this port; skipped')
        for b in bad:
            log('    %s' % b)
        log('  nothing was written; the module is unchanged')
        return False

    try:
        s = spec(module.img, set(module.arm_starts), revert, log)
    except Exception as exc:                                   # noqa: BLE001
        log('! fb capture: %s' % exc)
        log('  nothing was written; the module is unchanged')
        return False
    if s is None:
        log('  fb capture: already %s; nothing to write'
            % ('stock' if revert else 'corrected'))
        return False

    try:
        nso = nso_patcher.read_nso(_P(str(src)))
        nso_patcher.apply_spec(nso, s)
        data = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        log('! fb capture: %s' % exc)
        log('  nothing was written; the module is unchanged')
        return False

    os.makedirs(os.path.dirname(os.path.abspath(str(dest))), exist_ok=True)
    with open(str(dest), 'wb') as f:
        f.write(data)
    log('  fb capture: %s' % ('reverted to stock' if revert else describe()))
    return True


# --------------------------------------------------------------------------
# the cave, EXECUTED rather than read
# --------------------------------------------------------------------------
def run_cave(words, x: int, w: int, width: int) -> tuple:
    """
    Interpret the body against (w3 = x, w5 = w, w1 = width) and return the
    (x, w) it leaves behind. A tiny interpreter is worth more than a word
    comparison: it checks the SEMANTICS, so a wrong register or a wrong
    condition fails here instead of on hardware.
    """
    r = {1: width & 0xFFFFFFFF, 3: x & 0xFFFFFFFF, 5: w & 0xFFFFFFFF,
         8: 0, 9: 0}
    x64 = {8: 0, 9: 0}
    eq = None
    for word in words:
        if (word & 0xFFE0FC1F) == (0x6B000000 | 31):            # cmp Wn, Wm
            rm, rn = (word >> 16) & 31, (word >> 5) & 31
            eq = (r[rn] == r[rm])
        elif (word & 0xFFE00000) == 0x52800000:                 # movz Wd
            r[word & 31] = (word >> 5) & 0xFFFF
        elif (word & 0xFFE00000) == 0x72A00000:                 # movk Wd, hi
            rd = word & 31
            r[rd] = (r[rd] & 0xFFFF) | ((((word >> 5) & 0xFFFF)) << 16)
        elif (word & 0xFFC00000) == 0x11000000:                 # add Wd,Wn,#i
            rd, rn = word & 31, (word >> 5) & 31
            r[rd] = (r[rn] + ((word >> 10) & 0xFFF)) & 0xFFFFFFFF
        elif (word & 0xFFE0FC00) == 0x9BA07C00:                 # umull Xd,Wn,Wm
            rd, rn, rm = word & 31, (word >> 5) & 31, (word >> 16) & 31
            x64[rd] = r[rn] * r[rm]
            r[rd] = x64[rd] & 0xFFFFFFFF
        elif (word & 0xFFC0FC00) == 0xD3400000 | (1 << 22) | (63 << 10):
            rd, rn = word & 31, (word >> 5) & 31   # lsr Xd, Xn, #sh
            x64[rd] = x64[rn] >> ((word >> 16) & 63)
            r[rd] = x64[rd] & 0xFFFFFFFF
        elif (word & 0xFFE00C00) == 0x1A800000:                 # csel Wd,Wn,Wm
            rd, rn, rm = word & 31, (word >> 5) & 31, (word >> 16) & 31
            cond = (word >> 12) & 15
            if cond != COND_EQ:
                raise ValueError('unexpected csel condition %d' % cond)
            r[rd] = r[rn] if eq else r[rm]
        else:
            raise ValueError('the interpreter does not know %08X' % word)
    return r[3], r[5]


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------
def selftest(log=print) -> bool:
    """Offline. No module needed."""
    ok = True
    magic, shift = reciprocal()
    span, off = geometry()
    body = body_words()

    log('  geometry: frame spans game x %d..%d (%d units) in %d staging px'
        % (-off, span - off, span, STAGING_W))
    log('  reciprocal: 0x%08X >> %d' % (magic, shift))

    for n in range(0, 4096):
        if map_w(n) != (n * STAGING_W) // span:
            ok = False
            log('  FAIL map_w(%d) = %d, expected %d'
                % (n, map_w(n), (n * STAGING_W) // span))
            break
    else:
        log('  map_w exact against (n*%d)//%d for n in 0..4095'
            % (STAGING_W, span))

    # The cave, executed. `width` is what decides the gate.
    cases = [
        # (x, w, width, want_x, want_w, what)
        (0, 640, 640, 80, 479, 'a 1:1 4:3 effect'),
        (-off, span, span, 0, 640, 'a battlewide-widened 1:1 effect'),
        (200, 256, 256, 230, 191, "Titan's 256 sub-rect"),
        (0, 512, 256, 0, 512, 'the swirl tile A (xscale 2)'),
        (320, 512, 256, 320, 512, 'the swirl tile B (xscale 2)'),
    ]
    for x, w, width, wx, ww, what in cases:
        gx, gw = run_cave(body, x, w, width)
        mark = 'ok  ' if (gx, gw) == (wx, ww) else 'FAIL'
        if mark == 'FAIL':
            ok = False
        log('  %s %-34s (%5d,%4d) -> (%4d,%4d), want (%4d,%4d)'
            % (mark, what, x, w, gx, gw, wx, ww))

    # A page-scaled capture must come out BIT-IDENTICAL, which is the whole
    # promise this rewrite makes.
    for x, w, width in ((0, 512, 256), (320, 512, 256), (0, 1024, 512),
                        (64, 256, 128)):
        if run_cave(body, x, w, width) != (x & 0xFFFFFFFF, w):
            ok = False
            log('  FAIL xscale>1 capture (%d,%d,width %d) was modified'
                % (x, w, width))
    log('  every xscale>1 capture passes through unchanged')

    # no 1:1 rect may leave the staging surface
    for x, w in ((0, 640), (-off, span), (0, 320), (160, 320)):
        gx, gw = run_cave(body, x, w, w)
        if not 0 <= gx <= gx + gw <= STAGING_W:
            ok = False
            log('  FAIL (%d,%d) -> (%d,%d) leaves the %dpx surface'
                % (x, w, gx, gw, STAGING_W))
    log('  no 1:1 rect leaves the %dx%d staging surface'
        % (STAGING_W, STAGING_H))

    if len(body) != N_BODY:
        ok = False
        log('  FAIL the body is %d words, not %d' % (len(body), N_BODY))
    log('  selftest: %s' % ('PASS' if ok else 'FAIL'))
    return ok


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main_(argv=None):
    ap = argparse.ArgumentParser(description='framebuffer capture rect -> 16:9')
    ap.add_argument('main', nargs='?', help='exefs/main')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--apply', metavar='DEST')
    ap.add_argument('--revert', metavar='DEST')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)

    if a.selftest or not a.main:
        return 0 if selftest() else 1

    import nxmap
    module = nxmap.Main(a.main)
    bad = verify(module.img)
    if bad:
        for b in bad:
            print('!', b)
        return 1
    print('state:', read_state(module.img))
    print(describe())
    if a.show:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
        md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
        print('  hook +0x%X = %08X' % (HOOK, _word(module.img, HOOK)))
        for va, w in walk(module.img) or []:
            d = list(md.disasm(struct.pack('<I', w), va))
            print('    +0x%-8X %08X  %s' % (va, w, (
                '%s %s' % (d[0].mnemonic, d[0].op_str)) if d else '???'))
    if a.apply:
        return 0 if apply_to_nso(a.main, a.apply, print) else 1
    if a.revert:
        return 0 if apply_to_nso(a.main, a.revert, print, revert=True) else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main_())
