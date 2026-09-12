#!/usr/bin/env python3
r"""
ff7nx_fbresample.py -- resample the framebuffer capture horizontally, so a
1:1 capture can be BOTH correctly registered AND its authored size.

WHY THIS EXISTS -- THE THING THE RECT CANNOT DO
===============================================
The staging surface the capture reads is 640 pixels wide and, under `ws-3d`,
it holds a frame that is 854 GAME UNITS wide (FINDINGS-247 2-4).  So

    one staging pixel  =  854 / 640  =  1.334 game units

and the copy at `+0x10D71CC..+0x10D7238` is **1:1** -- one staging pixel in,
one texel out, no rescale anywhere on the path.

The effects that use these captures ask for a rect in GAME units and address
the result with UVs in the same units: Kujata's animated field takes a
256x256 rect and maps it onto its ground disc with hard-coded polar UVs that
run u = 1..255 (FINDINGS-308 2).  It needs

    one texel  =  one game unit

Those two facts cannot both hold while the copy is 1:1, and every build since
245 has been paying one of the two prices:

    fb_tex.w = w * 640/854 = 191    source region correct (256 game units),
    (builds 245-266)                registration correct... but the texture is
                                    191 texels for a 256-texel UV space, so
                                    everything past u = 191 sampled off the
                                    end:  THE BLACK TILE STAIRCASE

    fb_tex.w = w = 256              texture the right size, no black tiles...
    (builds 267-268)                but 256 staging pixels is 341 game units,
                                    so the snapshot is 1.334x too wide and
                                    slides off its anchor:  the field shows
                                    the WRONG PART OF THE SCREEN

The second one is what the TV finally named, on a flat beach stage where
there is no terrain to confuse it:

> "kujata's field is copying the ocean right of my party characters even
>  though its a distance away. its like the 'snapshot' is taken from the
>  wrong position/offset on the field, not 1:1 with the terrain below."

341 game units instead of 256 overshoots by 85 units to the right.  On a
beach, 85 units to the right of the sand is the sea.  That is the whole
symptom, and it is arithmetic, not geometry.

THE FIX
=======
Narrow the SOURCE and keep the DESTINATION, by making the copy resample:

    destination texel i  <-  staging column  x' + floor(i * 640 / 854)

    destination width    =  w        (the authored size -- no black tiles)
    source span          =  0.7494 w (the correct game-unit region)

which gives, exactly,

    texel i  <->  staging x' + 0.7494 i  <->  game x  =  rect.x + i

**one texel, one game unit, anchored on the rect's own origin** -- the
contract the effects were written against, restored under widescreen.

`ff7nx_fbcapture` keeps doing the other half: it moves the rect's ORIGIN into
staging coordinates and leaves the width alone.  The two are complements and
neither works without the other.

WHAT IS TOUCHED, AND WHAT IS NOT
================================
One word -- `+0x10D71F0  mov x14, x11`, the row-loop's source-cursor init --
becomes a branch into a cave.  The cave re-tests the SAME discriminator the
capture cave uses:

    fb_tex.w == tex_format.width   <=>  xscale == 1, a game-unit capture

and when that is false it executes the displaced `mov x14, x11` and branches
straight back into the stock loop, so **a page-scaled capture (the
battle-entry swirl) runs the original instruction sequence unchanged**, not a
reimplementation of it.

Vertical is untouched: `ws-3d` changes nothing vertical, one staging row is
already one game unit, and the row loop at `+0x10D7220` is not in the cave.

The resampled path is nearest-neighbour.  It carries exactly the information
builds 245-266 had -- 192 source columns -- spread across all 256 texels
instead of leaving 65 of them off the end of the image.
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

RESAMPLE_ENV = 'SEVENTH_NX_FB_RESAMPLE'

# The row loop's source-cursor init.  Everything the cave needs is live here:
#   x0  destination row base      x10 column count      x11 source row base + 3
#   x12 column counter (= 0)      x13 dest cursor + 3   x20 tex_header
HOOK = 0x10D71F0
HOOK_STOCK = 0xAA0B03EE            # mov x14, x11
STOCK_LOOP = 0x10D71F4             # the untouched byte-for-byte path
AFTER_LOOP = 0x10D7220             # where a finished row continues

# tex_header fields, from make_framebuffer_tex (+0x10DBC78 / +0x10DBC90).
FB_W_OFF = 0x1C                    # fb_tex.w   -- the texture's real width
TEX_W_OFF = 0x3C                   # tex_format.width -- the width asked for
FB_H_OFF = 0x20                    # fb_tex.h   -- the texture's real height
FB_X_OFF = 0x14                    # fb_tex.x   -- the capture origin
FB_Y_OFF = 0x18                    # fb_tex.y
KUJATA_TEX = 0x100                 # tex_format.width/height of Kujata's capture

COND_NE, COND_LT = 1, 11

# The whole CPU readback loop, asserted so a game update cannot silently move
# the registers this cave borrows.
ANCHORS = {
    0x10D70AC: 0xB9402688,   # ldr   w8, [x20, #0x24]     field_0
    0x10D71CC: 0x1B195E69,   # madd  w9, w19, w25, w23    surfPitch*y + x
    0x10D71D8: 0x8B2BC34B,   # add   x11, x26, w11, sxtw  source row base
    0x10D71E4: 0x91000D6B,   # add   x11, x11, #3         the +3 convention
    0x10D71E8: 0xAA1F03EC,   # mov   x12, xzr             i = 0     ROW TOP
    0x10D71EC: 0x91000C0D,   # add   x13, x0, #3          dest cursor
    0x10D71F4: 0x385FF1CF,   # ldurb w15, [x14, #-1]      the stock body
    0x10D720C: 0x384045CF,   # ldrb  w15, [x14], #4
    0x10D7214: 0x380045AF,   # strb  w15, [x13], #4
    0x10D7218: 0xEB0A019F,   # cmp   x12, x10
    0x10D721C: 0x54FFFECB,   # b.lt  #0x10D71F4
    0x10D7220: 0xB9401E8C,   # ldr   w12, [x20, #0x1c]    row advance
}


SPAN_ENV = 'SEVENTH_NX_FB_SPAN'
DEFAULT_SPAN = 100                 # per cent; 100 is the stock 640/854


def span() -> int:
    """
    How many game units the capture's SOURCE spans, as a percentage of the
    854/640 widescreen ratio.

    BUILD 293. The TV's description of what is still wrong is specific and it
    is one number:

    > "this animated field is always a pinch wider than it should be, as well
    >  as lower resolution. the visible edges dont perfectly align with the
    >  cutoff region on the floor"

    Both halves of that are the same quantity. The resample maps

        destination texel i  <-  staging column  x' + floor(i * 640 / 854)

    so 256 texels are filled from 192 staging pixels: the source span sets BOTH
    how much ground one texel covers (the alignment) and how many source pixels
    there are per texel (the sharpness). If the span is wrong, the field is
    mis-scaled AND softer than it needs to be, together, which is exactly the
    pair of symptoms.

    Raising this makes the source span WIDER, so each texel covers more ground
    and the image on the disc gets TIGHTER. Lowering it does the reverse.
    100 is stock and changes nothing.

    There is a ground truth for it in the game: the flash during Kujata's ice
    animation illuminates the exact geometry that is about to be snapshotted.
    Match the field's edges to that outline and the number is settled, rather
    than fitted.
    """
    v = os.environ.get(SPAN_ENV)
    if v is None:
        return DEFAULT_SPAN
    try:
        n = int(v, 0)
    except ValueError:
        return DEFAULT_SPAN
    return n if 50 <= n <= 200 else DEFAULT_SPAN


def _reciprocal():
    """(magic, shift) for floor(n * 640 / 854), reused from ff7nx_fbcapture.

    `SEVENTH_NX_FB_SPAN` scales the numerator, so 100 reproduces the stock
    constants bit for bit and anything else is a deliberate, single change.
    """
    import ff7nx_fbcapture as FB
    s = span()
    if s == DEFAULT_SPAN:
        return FB.reciprocal()
    num = FB.STAGING_W * s // DEFAULT_SPAN
    return FB.reciprocal(num=num)


# --------------------------------------------------------------------------
# encoders a64 does not carry
# --------------------------------------------------------------------------
def umull(rd, rn, rm):
    """UMULL Xd, Wn, Wm."""
    return 0x9BA07C00 | (rm << 16) | (rn << 5) | rd


def lsr64(rd, rn, shift):
    """LSR Xd, Xn, #shift."""
    return 0xD3400000 | (1 << 22) | (shift << 16) | (63 << 10) | (rn << 5) | rd


def _unscaled(base, rt, rn, imm9):
    return base | ((imm9 & 0x1FF) << 12) | (rn << 5) | rt


def orr_imm_ff000000(rd, rn):
    """ORR Wd, Wn, #0xFF000000 -- eight ones rotated to the top byte."""
    return 0x32000000 | (8 << 16) | (7 << 10) | (rn << 5) | rd


def tbz32(rt, bit, frm, to):
    """TBZ Wt, #bit, <to>."""
    off = (to - frm) // 4
    return 0x36000000 | ((bit & 0x1F) << 19) | ((off & 0x3FFF) << 5) | rt


def bfi32(rd, rn, lsb, width):
    """BFI Wd, Wn, #lsb, #width -- BFM Wd, Wn, #(-lsb mod 32), #(width-1)."""
    immr = (-lsb) % 32
    return 0x33000000 | (immr << 16) | ((width - 1) << 10) | (rn << 5) | rd


def ldur32(rt, rn, imm9):
    """LDUR Wt, [Xn, #imm9]."""
    return 0xB8400000 | ((imm9 & 0x1FF) << 12) | (rn << 5) | rt


def stur32(rt, rn, imm9):
    """STUR Wt, [Xn, #imm9] -- one whole RGBA pixel in a single store."""
    return 0xB8000000 | ((imm9 & 0x1FF) << 12) | (rn << 5) | rt


def rev32(rd, rn):
    """REV Wd, Wn -- reverse the four bytes of a word."""
    return 0x5AC00800 | (rn << 5) | rd


def ror32(rd, rn, lsb):
    """ROR Wd, Wn, #lsb -- the EXTR alias with both sources the same."""
    return 0x13800000 | (rn << 16) | ((lsb & 0x1F) << 10) | (rn << 5) | rd


def ubfx32(rd, rn, lsb, width):
    """UBFX Wd, Wn, #lsb, #width -- UBFM Wd, Wn, #lsb, #(lsb+width-1)."""
    return (0x53000000 | (lsb << 16) | ((lsb + width - 1) << 10)
            | (rn << 5) | rd)


def udiv32(rd, rn, rm):
    """UDIV Wd, Wn, Wm."""
    return 0x1AC00800 | (rm << 16) | (rn << 5) | rd


def ldurb(rt, rn, imm9):
    return _unscaled(0x38400000, rt, rn, imm9)


def sturb(rt, rn, imm9):
    return _unscaled(0x38000000, rt, rn, imm9)


# --------------------------------------------------------------------------
# the cave
# --------------------------------------------------------------------------
LOOP_TOP = 6
STOCK_ENTRY = 22
N_WORDS = 24


PROBE_ENV = 'SEVENTH_NX_FB_PROBE'

# Build 273 ran the 'texel' probe and the black band vanished (measured: the
# band's pixels all map to texels u 78..92, v 0..48, and the probe writes those
# texels too -- so the band IS texture content, not geometry).  The copy body
# and the probe body differ in exactly two things there: the probe takes its
# colour from the loop counters, and it forces ALPHA to 0xFF instead of copying
# the source's fourth byte.  'alpha' isolates the second one, in ONE word.
# BUILD 278: back to the texel probe. The 'frame' constants were fitted from
# one probe reading and then carried through two geometry changes without being
# re-measured, and the field landed somewhere new each time. Measure first.
# BUILD 281: back to 'copy' -- the build-268 behaviour the TV confirmed correct
# on flat stages. The projective remap is correct arithmetic against a correct
# measurement and still came out wrong on hardware, so one of the links between
# the two is wrong and I have not found which. Everything stays reachable by
# env; nothing speculative is the default.
# BUILD 306. 'fillhole' is the plain copy plus edge clamping: a source pixel
# that is essentially 0,0,0 -- a hole in the staging surface where the
# background is missing -- takes the colour of the texel to its left instead
# of punching black through the field. Everything else about the copy is
# unchanged, and a capture with no holes produces the identical texture.
DEFAULT_PROBE = 'punch'


def probing() -> bool:
    """
    DIAGNOSTIC. Fill the capture texture with its own texel coordinates
    instead of the frame, so one screenshot reports the ENTIRE texel -> screen
    mapping the effect actually uses:

        R = destination column  (u texel, 0..255)
        G = destination row     (v texel, 0..255)
        B = 0x40 constant, so "this pixel came from the probe" is unambiguous
        A = 0xFF

    Nothing else in the chain is touched: same rect, same texture size, same
    loop bounds, same eight bytes per pixel written.  Whatever the effect draws
    with it is a direct read-out of which texel lands on which pixel -- which
    settles alignment, scale, origin and what the rim samples, by measurement,
    in one build.
    """
    return mode() == 'texel'


def mode() -> str:
    """'copy' | 'texel' | 'alpha'."""
    v = os.environ.get(PROBE_ENV)
    if v is None:
        return DEFAULT_PROBE
    v = v.strip().lower()
    if v in ('', '0', 'off', 'false', 'no', 'copy'):
        return 'copy'
    if v in ('alpha', 'a', '2'):
        return 'alpha'
    if v in ('frame', 'f', '3'):
        return 'frame'
    if v in ('proj', 'p', '4'):
        return 'proj'
    if v in ('projtexel', 'pt', '5'):
        return 'projtexel'
    if v in ('report', 'r', '6'):
        return 'report'
    if v in ('stretch', 's', '7'):
        return 'stretch'
    if v in ('opaque', 'o', '8'):
        return 'opaque'
    if v in ('srcblack', 'black', 'b', '9'):
        return 'srcblack'
    if v in ('fillhole', 'hole', 'h', '10'):
        return 'fillhole'
    if v in ('punch', 'pun', '11'):
        return 'punch'
    if v in ('unpremul', 'unpre', 'u', '12'):
        return 'unpremul'
    return 'texel'


# --------------------------------------------------------------------------
# 'frame' mode: the capture steps across the WHOLE staging surface, mirrored
# --------------------------------------------------------------------------
# Derived from the build-273 texel probe, after the build-275 disc halving that
# the TV confirmed on hardware ("matching closer to the pixel density").
#
#   the disc, at SEVENTH_NX_FX_SCALE=6, displays texel u at screen px
#       x(u) = 1511 - 6.53 * u          (probe fit, halved about the disc
#       y(v) =  408 - 1.604 * v          centre, which does not move)
#
#   so for texel (u, v) to hold the picture at the place it is DRAWN:
#       staging column = x(u) / 2   = 755 - 3.265 * u
#       staging row    = y(v) / 1.5 = 272 - 1.069 * v
#
# Both steps are NEGATIVE because the probe showed u falling as screen x rises
# -- the texture is applied mirrored, and no capture rect can express that.
# The cave can, because it computes the source address itself.
#
# Check, at the disc centre: u = 128 -> column 338 -> frame px 676, and the
# probe puts the disc centre at 675. v = 128 -> row 136 -> frame py 204,
# probe says 203. That is the first time the two sides have agreed.
MAP_ENV = {'cx': 'SEVENTH_NX_FX_MAP_CX', 'sx': 'SEVENTH_NX_FX_MAP_SX',
           'cy': 'SEVENTH_NX_FX_MAP_CY', 'sy': 'SEVENTH_NX_FX_MAP_SY'}
#
# BUILD 277. The disc is back to STOCK size (the TV drew what the field must
# cover: the whole battlefield, running far behind the enemies), and the texel
# count is doubled by ff7nx_fbsize instead. Both doubled, so the STEPS are
# unchanged and only the ORIGINS move -- texel 0 now starts a whole disc-radius
# further out:
#
#     staging column = 1173 - 3.265 * i        i = physical texel, 0..511
#     staging row    =  409 - 1.069 * j
#
# Check, at the disc centre (physical texel 256, UV 128):
#     column 337 -> frame px 674   (probe measured the centre at 675)
#     row    136 -> frame py 204   (probe measured 203)
MAP_DEFAULT = {'cx': 1173, 'sx': 3265, 'cy': 409, 'sy': 1069}  # s* are x1000

VIRT_W, VIRT_H = 640, 480          # asserted by SURFACE_ANCHORS below
COND_GT, COND_NE2 = 12, 1


def surf_shift():
    """
    log2 of ff7nx_fbsurf's scale.

    BUILD 309. The capture surface is created from a LITERAL 640x480
    descriptor at +0x10D5970, and the rect that addresses it is computed in
    those same virtual units, so both scale together or neither does. When
    ff7nx_fbsurf raises them by k, `fb_tex.w` becomes `tex_format.width << k`
    and this body's 1:1 gate would stop matching -- the resample would
    silently disengage and the anamorphic squeeze would come straight back.

    Reading it from that module rather than repeating the number is the point:
    the two cannot drift.
    """
    try:
        import ff7nx_fbsurf
        return {1: 0, 2: 1, 4: 2}[ff7nx_fbsurf.scale()]
    except Exception:                                          # noqa: BLE001
        return 0


def surface():
    """The capture surface's real size, which follows ff7nx_fbsurf."""
    k = 1 << surf_shift()
    return VIRT_W * k, VIRT_H * k


def _surf_w():
    return surface()[0]


def _surf_h():
    return surface()[1]


# Kept as module-level names for the bodies that were written against them;
# they are the STOCK size and the frame/proj clamps use them as such.
SURF_W, SURF_H = VIRT_W, VIRT_H


def map_params():
    out = {}
    for k, env in MAP_ENV.items():
        v = os.environ.get(env)
        try:
            out[k] = int(v, 0) if v is not None else MAP_DEFAULT[k]
        except ValueError:
            out[k] = MAP_DEFAULT[k]
    # step x 65536 / 1000, as an unsigned 32-bit multiplier
    out['mx'] = max(1, min(0xFFFFFFFF, round(out['sx'] * 65536 / 1000)))
    out['my'] = max(1, min(0xFFFFFFFF, round(out['sy'] * 65536 / 1000)))
    for k in ('cx', 'cy'):
        out[k] = max(0, min(0xFFFF, out[k]))
    return out


def mul32(rd, rn, rm):
    return A.mul(rd, rn, rm)


def _clamp_words(reg, hi, tmp, addr, i):
    """reg = min(max(reg, 0), hi) -- five words, no branches."""
    return [A.cmp_reg(reg, A.WZR),
            A.csel(reg, A.WZR, reg, 11),                # LT -> 0
            A.movz(tmp, hi),
            A.cmp_reg(reg, tmp),
            A.csel(reg, tmp, reg, COND_GT)]


FRAME_LOOP_TOP = 28
FRAME_STOCK_ENTRY = 53
FRAME_WORDS = 55


def cmp_reg_lsl(rn, rm, sh):
    """CMP Wn, Wm, LSL #sh -- SUBS WZR, Wn, Wm, LSL #sh."""
    return 0x6B000000 | (sh << 10) | (rm << 16) | (rn << 5) | A.WZR


def size_shift():
    """
    log2 of Kujata's own xscale -- the gate and the step both need it.

    BUILD 278: this used to come from ff7nx_fbsize, which scaled EVERY capture
    in the game. It now comes from ff7nx_fxcapscale, which scales exactly one,
    so `fb_tex.w == tex_format.width << shift` identifies Kujata's capture and
    nothing else. Every other capture falls through to the stock loop.
    """
    try:
        import ff7nx_fxcapscale
        return {1: 0, 2: 1, 4: 2}[ff7nx_fxcapscale.scale()]
    except Exception:                                          # noqa: BLE001
        return 0


# --------------------------------------------------------------------------
# 'proj' -- the PROJECTIVE remap. The only shape that can align a plane.
# --------------------------------------------------------------------------
# A ground plane seen through a pinhole camera maps to the screen by a
# HOMOGRAPHY. Builds 276/277 used a separable model -- source column from the
# texel column alone, source row from the texel row alone -- which cannot
# express a homography at any constants, which is why tuning them only ever
# moved the rectangle somewhere else.
#
# These nine coefficients are SOLVED, not fitted by eye, from the build-279
# texel probe: 360226 probe pixels, grouped into 7158 distinct texels by
# centroid (one texel covers many screen pixels near the camera, so the
# centroid is its position and every pixel is just its footprint), then a
# normalised DLT with outlier trimming. Residual 2.06 staging pixels median
# over the inliers -- about half a texel at mid-screen.
#
#     col = (A00*i + A01*j + A02) / (A20*i + A21*j + A22)
#     row = (A10*i + A11*j + A12) / (same denominator)
#
# at scale 2**18, which is the largest that keeps every coefficient inside
# int32 while leaving the denominator's precision at 4e-6.
PROJ_SCALE = 18
PROJ_COEF = (-5076189, 4546630, 1267468383,      # col numerator
                39616,  143742,  484422010,      # row numerator
                 2663,    7515,     262144)      # denominator
PROJ_ENV = 'SEVENTH_NX_FX_PROJ'


def gate_shift():
    """The full shift the 1:1 gate must use.

    `size_shift()` is Kujata's own xscale (ff7nx_fxcapscale) and
    `surf_shift()` is the capture surface's scale (ff7nx_fbsurf). fb_tex.w is
    `tex_format.width << (both)`, so a gate that carries only one of them
    stops matching the moment the other moves -- which is what silently
    disengaged the texel and proj bodies at k > 1.
    """
    return size_shift() + surf_shift()


def proj_coef():
    """Env override, nine comma-separated integers, for tuning without me."""
    v = os.environ.get(PROJ_ENV)
    if not v:
        return PROJ_COEF
    try:
        out = tuple(int(x, 0) for x in v.replace(' ', '').split(','))
    except ValueError:
        return PROJ_COEF
    if len(out) != 9 or not all(-2**31 <= x < 2**31 for x in out):
        return PROJ_COEF
    return out


# encoders a64 does not carry; every one is round-tripped through capstone in
# tests/test_fbresample.py before it can reach a build
def smaddl(rd, rn, rm, ra):
    """SMADDL Xd, Wn, Wm, Xa."""
    return 0x9B200000 | (rm << 16) | (ra << 10) | (rn << 5) | rd


def sdiv64(rd, rn, rm):
    """SDIV Xd, Xn, Xm."""
    return 0x9AC00C00 | (rm << 16) | (rn << 5) | rd


def madd64(rd, rn, rm, ra):
    """MADD Xd, Xn, Xm, Xa."""
    return 0x9B000000 | (rm << 16) | (ra << 10) | (rn << 5) | rd


def add_sxtw(rd, rn, rm):
    """ADD Xd, Xn, Wm, SXTW -- the increments are signed 32-bit."""
    return 0x8B20C000 | (rm << 16) | (rn << 5) | rd


def sp_sub(imm):
    return 0xD1000000 | (imm << 10) | (31 << 5) | 31


def sp_add(imm):
    return 0x91000000 | (imm << 10) | (31 << 5) | 31


def _const32(rd, val):
    """movz/movk a 32-bit constant. Negative values arrive as two's complement
    and are sign-extended by ADD ... SXTW where that matters."""
    val &= 0xFFFFFFFF
    return [A.movz(rd, val & 0xFFFF), A.movk_hi(rd, val >> 16)]


def proj_body(addr, how='proj'):
    """
    The projective remap. Returns the word list; PROJ_WORDS is its length.

    Borrowed registers, all saved and restored across the row:
        x1 col numerator   x2 row numerator   x3 denominator
        w4/w5/w6 the per-column increments   x7 spare
    Loop registers taken from the stock loop, untouched:
        w8 row index   x10 columns   x12 column counter   x13 dest cursor
        x19 surface pitch   x20 tex_header   x26 surface base
    """
    k = gate_shift()
    c = proj_coef()
    A00, A01, A02, A10, A11, A12, A20, A21, A22 = c
    w = []
    lbl = {}

    def here():
        return len(w)

    # ---- the five-condition gate (shared with every other mode) ----------
    w.append(A.ldr(15, 20, FB_W_OFF))
    w.append(A.ldr(16, 20, TEX_W_OFF))
    w.append(cmp_reg_lsl(15, 16, k))
    gate = [here()]
    w.append(0)
    w.append(A.cmp_imm(16, KUJATA_TEX))
    gate.append(here())
    w.append(0)
    w.append(A.ldr(22, 20, FB_H_OFF))
    w.append(cmp_reg_lsl(22, 16, k))
    gate.append(here())
    w.append(0)
    w.append(A.ldr(17, 20, FB_X_OFF))
    gate.append(here())
    w.append(0)
    w.append(A.ldr(17, 20, FB_Y_OFF))
    gate.append(here())
    w.append(0)
    w.append(A.mov_reg(10, 15))                    # columns = fb_tex.w
    # ---- borrow six registers for the row --------------------------------
    w.append(sp_sub(0x40))
    w.append(A.stp64_off(1, 2, A.SP, 0x00))
    w.append(A.stp64_off(3, 4, A.SP, 0x10))
    w.append(A.stp64_off(5, 6, A.SP, 0x20))
    w.append(A.str64(7, A.SP, 0x30))
    # ---- once per row: fold the j terms ----------------------------------
    for coef_j, coef_c, acc in ((A01, A02, 1), (A11, A12, 2), (A21, A22, 3)):
        w += _const32(17, coef_j)
        w += _const32(acc, coef_c)
        w.append(smaddl(acc, 17, 8, acc))          # acc = coef_j*row + coef_c
    for inc, reg in ((A00, 4), (A10, 5), (A20, 6)):
        w += _const32(reg, inc)
    # ---- per texel -------------------------------------------------------
    lbl['loop'] = here()
    w.append(sdiv64(14, 1, 3))                     # col = numc / den
    w.append(sdiv64(16, 2, 3))                     # row = numr / den
    for reg, hi in ((14, SURF_W - 1), (16, SURF_H - 1)):
        w.append(A.cmp_reg64(reg, A.XZR))
        w.append(A.csel64(reg, A.XZR, reg, COND_LT))
        w.append(A.movz(17, hi))
        w.append(A.cmp_reg64(reg, 17))
        w.append(A.csel64(reg, 17, reg, COND_GT))
    w.append(madd64(14, 16, 19, 14))               # pitch*row + col
    w.append(A.add_reg64_lsl(14, 26, 14, 2))       # surface base + that*4
    w.append(A.add_imm64(14, 14, 3))               # the loop's "+3" convention
    if how == 'projtexel':
        w.append(A.lsr(15, 12, k))
        w.append(sturb(15, 13, -3))
        w.append(A.lsr(15, 8, k))
        w.append(sturb(15, 13, -2))
        w.append(A.movz(15, 0x40))
        w.append(sturb(15, 13, -1))
        w.append(A.movz(15, 0xFF))
        w.append(sturb(15, 13, 0))
    else:
        w.append(ldurb(15, 14, -1))
        w.append(sturb(15, 13, -3))
        w.append(ldurb(15, 14, -2))
        w.append(sturb(15, 13, -2))
        w.append(ldurb(15, 14, -3))
        w.append(sturb(15, 13, -1))
        w.append(ldurb(15, 14, 0))
        w.append(sturb(15, 13, 0))
    w.append(add_sxtw(1, 1, 4))                    # numc += A00
    w.append(add_sxtw(2, 2, 5))                    # numr += A10
    w.append(add_sxtw(3, 3, 6))                    # den  += A20
    w.append(A.add_imm64(12, 12, 1))
    w.append(A.add_imm64(13, 13, 4))
    w.append(A.cmp_reg64(12, 10))
    back = here()
    w.append(0)
    # ---- give the registers back and rejoin ------------------------------
    w.append(A.ldp64_off(1, 2, A.SP, 0x00))
    w.append(A.ldp64_off(3, 4, A.SP, 0x10))
    w.append(A.ldp64_off(5, 6, A.SP, 0x20))
    w.append(A.ldr64(7, A.SP, 0x30))
    w.append(sp_add(0x40))
    done = here()
    w.append(0)
    lbl['stock'] = here()
    w.append(HOOK_STOCK)
    w.append(A.b(addr(here()), STOCK_LOOP))

    st = lbl['stock']
    for n, idx in enumerate(gate):
        if n in (3, 4):
            # the origin conditions, retired for the reason in frame_body:
            # ff7nx_fbcapture moves Kujata's origin, so `== 0` is false and
            # this body could never match.
            w[idx] = 0xD503201F
        else:
            w[idx] = A.bcond(addr(idx), addr(st), COND_NE)
    w[back] = A.bcond(addr(back), addr(lbl['loop']), COND_LT)
    w[done] = A.b(addr(done), AFTER_LOOP)
    assert all(x is not None for x in w)
    return w


PROJ_WORDS = len(proj_body(lambda i: i * 4))


def frame_body(addr, how='frame'):
    """
    45 words. Computes the source address from the loop counters instead of
    walking a cursor, so the mapping can be mirrored, scaled and clamped --
    none of which a capture rect can do.

    Live registers it borrows, all from the stock loop:
        w8  row index      w19 surface pitch     x26 surface base
        x10 column count   x12 column counter    x13 destination cursor
        x20 tex_header     x11 stock row base (stock path only)
    Scratch: w14/x14, w15, w16/x16 (row base), w17 (IP0/IP1, no calls here).
    """
    p = map_params()
    k = gate_shift()
    S = FRAME_STOCK_ENTRY
    w = [None] * FRAME_WORDS
    # ---- FIVE conditions, not one ---------------------------------------
    # Build 277 broke the battle-entry swirl because the single condition it
    # used was not a discriminator. The swirl's own xscale IS 2 (x86 0x401726
    # sets [0x9A04B0] = 2, read at 0x40248E/0x40249D), and at least one more
    # capture takes its scale from [0x9AD1A8], which is 2 in high-res. So
    # "fb_tex.w == tex_format.width << 1" catches them too.
    #
    # Kujata's capture is the only one that is ALL of: double-scaled, square,
    # 256x256 authored, and taken at the frame origin. Test all five.
    w[0] = A.ldr(15, 20, FB_W_OFF)
    w[1] = A.ldr(16, 20, TEX_W_OFF)
    w[2] = cmp_reg_lsl(15, 16, k)              # fb_tex.w == tex_w << k
    w[3] = A.bcond(addr(3), addr(S), COND_NE)
    w[4] = A.cmp_imm(16, KUJATA_TEX)           # tex_format.width == 256
    w[5] = A.bcond(addr(5), addr(S), COND_NE)
    w[6] = A.ldr(22, 20, FB_H_OFF)             # fb_tex.h
    w[7] = cmp_reg_lsl(22, 16, k)              # ... == tex_w << k too (square)
    w[8] = A.bcond(addr(8), addr(S), COND_NE)
    # BUILD 325. These two used to be `fb_tex.x == 0` and `fb_tex.y == 0`.
    # ff7nx_fbcapture MOVES Kujata's origin for widescreen -- map_x(0) = 80
    # virtual units, which make_framebuffer_tex then scales to 80*k -- so
    # "the origin is zero" has been false since that module shipped, and any
    # body carrying these conditions could never match. That is why the texel
    # probe silently ran the stock loop instead: no gradient, and the field
    # displaced because the stock loop has no 640/854 resample in it.
    #
    # Dropping them leaves w/h/tex_w, which is no less specific than the
    # SHIPPING punch gate (that one tests only fb_tex.w == tex_w << shift),
    # and the swirl no longer reaches this loop at all now that
    # ff7nx_swirlgpu puts it on the GPU path. The loads stay so the word
    # count, the register state and the walk are untouched.
    w[9] = A.ldr(17, 20, FB_X_OFF)
    w[10] = 0xD503201F                       # was: fb_tex.x == 0
    w[11] = A.ldr(17, 20, FB_Y_OFF)
    w[12] = 0xD503201F                       # was: fb_tex.y == 0
    # The stock loop bounds are min(surface, origin + size) - origin, which
    # cannot reach a texture wider than the 640x480 staging surface. Drive both
    # counts from the texture's OWN size instead, so every texel is written and
    # none is left black. w22 already holds fb_tex.h from the gate.
    w[13] = A.mov_reg(10, 15)                  # columns = fb_tex.w
    # ---- once per row: srcrow = clamp(cy - (j*my >> 16)) -----------------
    w[14] = A.movz(17, p['my'] & 0xFFFF)
    w[15] = A.movk_hi(17, p['my'] >> 16)
    w[16] = umull(17, 8, 17)
    w[17] = lsr64(17, 17, 16)
    w[18] = A.movz(15, p['cy'])
    w[19] = A.sub_reg(15, 15, 17)
    w[20], w[21], w[22], w[23], w[24] = _clamp_words(15, SURF_H - 1, 17,
                                                     addr, 20)
    w[25] = mul32(15, 15, 19)                  # pitch * srcrow
    w[26] = A.add_reg64_lsl(16, 26, 15, 2)     # surface base + that*4
    w[27] = A.add_imm64(16, 16, 3)             # the loop's "+3" convention
    # ---- per pixel: srccol = clamp(cx - (i*mx >> 16)) --------------------
    w[28] = A.movz(17, p['mx'] & 0xFFFF)
    w[29] = A.movk_hi(17, p['mx'] >> 16)
    w[30] = umull(17, 12, 17)
    w[31] = lsr64(17, 17, 16)
    w[32] = A.movz(15, p['cx'])
    w[33] = A.sub_reg(15, 15, 17)
    w[34], w[35], w[36], w[37], w[38] = _clamp_words(15, SURF_W - 1, 17,
                                                     addr, 34)
    w[39] = A.add_reg64_lsl(14, 16, 15, 2)     # row base + srccol*4
    # The ONLY thing a mode changes is these eight words. The gate, the loop
    # bounds, the clamps and the stock path are shared, so a probe can never
    # again be measuring a different code path from the fix it is measuring
    # for -- which is exactly what build 278 shipped.
    if how == 'texel':
        w[40] = A.lsr(15, 12, k)               # physical texel -> UV coordinate
        w[41] = sturb(15, 13, -3)              # B = u
        w[42] = A.lsr(15, 8, k)
        w[43] = sturb(15, 13, -2)              # G = v
        w[44] = A.movz(15, 0x40)
        w[45] = sturb(15, 13, -1)              # R = marker
        w[46] = A.movz(15, 0xFF)
        w[47] = sturb(15, 13, 0)               # A
    else:
        w[40] = ldurb(15, 14, -1)
        w[41] = sturb(15, 13, -3)
        w[42] = ldurb(15, 14, -2)
        w[43] = sturb(15, 13, -2)
        w[44] = ldurb(15, 14, -3)
        w[45] = sturb(15, 13, -1)
        w[46] = (A.movz(15, 0xFF) if how == 'alpha' else ldurb(15, 14, 0))
        w[47] = sturb(15, 13, 0)
    w[48] = A.add_imm64(12, 12, 1)
    w[49] = A.add_imm64(13, 13, 4)
    w[50] = A.cmp_reg64(12, 10)
    w[51] = A.bcond(addr(51), addr(FRAME_LOOP_TOP), COND_LT)
    w[52] = A.b(addr(52), AFTER_LOOP)
    w[53] = HOOK_STOCK
    w[54] = A.b(addr(54), STOCK_LOOP)
    assert all(x is not None for x in w)
    return w


HOLE_WORDS = 27
HOLE_LOOP_TOP = 6
HOLE_KEEP = 18
HOLE_STOCK = 25
COND_GE = 10


def hole_body(addr):
    """'fillhole' -- never let the void reach the texture.

    BUILD 306, and the first change in this whole thread aimed at the actual
    defect rather than at the copy.

    The `srcblack` probe settled it on hardware: the texels the black squares
    occupy come back RED, i.e. **the source pixel really is black**. The
    staging surface has a hole in it where the battlefield geometry ends -- the
    background that fills that area on your screen is not in that buffer -- and
    the copy has been faithfully reproducing the hole. Everything else was
    eliminated first, by execution:

        unwritten texels    0 at any surface >= 336 wide     (_execcap.py)
        the copy inventing  no zero byte in, no black out    (_execcap.py)
          black
        the alpha channel   the `opaque` build, one word     (hardware)
        the texture size    FFNx's UV step is a hard 1/256   (animations.cpp)
        the capture origin  executed fbcapture's cave        (arm64emu)
        an undrawn margin   viewport = the target's own size (+0x10DBF40)

    The proper repair is to put the background into that buffer, and that is
    still being worked out. This is the other half, and it is not a fudge: it
    is **edge clamping**, which is what any sampler does at a texture border
    and what vanilla's capture would never have needed because its source has
    no hole.

        source pixel is (near) black   ->   reuse the texel to its left
        otherwise                      ->   copy it, opaque

    So a hole takes the colour of the real content beside it instead of
    punching through. The first column of each row has nothing to its left, so
    it is left alone rather than reading whatever precedes the row.

    The source address is computed exactly as the shipping 'copy' body does --
    same gate, same 640/854 step -- so this reads the same pixels the real
    capture reads.
    """
    magic, shift = _reciprocal()
    w = [None] * HOLE_WORDS
    w[0] = A.ldr(15, 20, FB_W_OFF)
    w[1] = A.ldr(16, 20, TEX_W_OFF)
    w[2] = cmp_reg_lsl(15, 16, surf_shift())
    w[3] = A.bcond(addr(3), addr(HOLE_STOCK), COND_NE)
    w[4] = A.movz(16, magic & 0xFFFF)
    w[5] = A.movk_hi(16, magic >> 16)
    # --- one destination pixel -------------------------------------------
    w[6] = umull(14, 12, 16)
    w[7] = lsr64(14, 14, shift)
    w[8] = A.add_reg64_lsl(14, 11, 14, 2)
    w[9] = ldurb(15, 14, -1)                   # B
    w[10] = ldurb(17, 14, -2)                  # G
    w[11] = A.orr_lsl(15, 15, 17, 8)
    w[12] = ldurb(17, 14, -3)                  # R
    w[13] = A.orr_lsl(15, 15, 17, 16)          # w15 = 0x00RRGGBB
    w[14] = A.cmp_imm(15, 8)                   # a hole is 0x000000
    w[15] = A.bcond(addr(15), addr(HOLE_KEEP), COND_GE)
    w[16] = A.cbz64(12, addr(16), addr(HOLE_KEEP))   # no texel to the left
    w[17] = ldur32(15, 13, -7)                 # the texel to the left
    # KEEP:
    w[18] = orr_imm_ff000000(15, 15)           # opaque either way
    w[19] = stur32(15, 13, -3)
    w[20] = A.add_imm64(12, 12, 1)
    w[21] = A.add_imm64(13, 13, 4)
    w[22] = A.cmp_reg64(12, 10)
    w[23] = A.bcond(addr(23), addr(HOLE_LOOP_TOP), COND_LT)
    w[24] = A.b(addr(24), AFTER_LOOP)
    w[25] = HOOK_STOCK
    w[26] = A.b(addr(26), STOCK_LOOP)
    return w


UNPREMUL_WORDS = 38
UNPREMUL_LOOP_TOP = 4
UNPREMUL_STORE = 30
UNPREMUL_STOCK = 36
COND_EQ = 0


def unpremul_body(addr):
    """'unpremul' -- undo the premultiply the render target put in.

    BUILD 311, and it is the first explanation that accounts for all three
    things the black squares do.

    WHAT THEY ARE
    -------------
    The scene target is cleared to (0,0,0,0) every frame (+0x10DAB40, s0..s3
    all zero) and the 3D layer is drawn onto it. Anything drawn src-over onto
    transparent black comes out PREMULTIPLIED, by the algebra of the blend
    itself:

        rgb = C*a + 0*(1-a) = C*a        a' = a + 0*(1-a) = a

    Then +0x10DACB0 blits that whole target into the capture surface through a
    sampler set to LINEAR (+0x10DAD94, w1 = w2 = 1), so every texel on a
    silhouette is a filtered mix of (C*a, a) and (0, 0) -- premultiplied
    again, and darker than the colour it represents.

    The readback copies those bytes verbatim, and the field draws them as if
    they were straight alpha. A texel that should be "half-covered by green"
    is drawn as "half-covered by HALF-BRIGHTNESS green". That is the dark
    fringe, and it is the textbook premultiplied-alpha black fringe.

    WHY THE EVIDENCE FITS THIS AND NOTHING ELSE
    -------------------------------------------
        one texel wide            it is the filter's footprint
        follows the silhouette    it IS the silhouette
        MOVED when the surface    the filter's footprint moved with it
          was scaled (build 310)
        SHRANK with the texels    it is measured in texels, not world units
        survived `punch`          a filtered texel is dark, not zero

    A fixed piece of model geometry could not have moved or shrunk when the
    capture surface changed size. That observation is what rules out every
    geometry explanation, including the one I gave in FINDINGS 308.

    THE REPAIR
    ----------
    Undo exactly what the blend did -- divide the colour back out by its own
    alpha:

        a == 255  ->  nothing was mixed in; copy it, byte for byte
        a == 0    ->  the whole texel is zero already; store it (this is what
                      `punch` did, and it stays true for free)
        otherwise ->  c = c * 255 / a, alpha kept

    It is the arithmetic inverse of `rgb = C*a`, not a threshold, not a fill
    and not a tint. Keeping the alpha is the point: the silhouette stays
    softened instead of gaining a hard edge, and it composites over the real
    floor at the coverage the filter actually measured.

    It is also FASTER than the body it replaces. The stock loop moves the
    pixel a byte at a time; `rev` + `ror #8` is the same byte shuffle in two
    instructions on a word, so a fully opaque texel -- which is nearly all of
    them -- costs one load, two ALU ops and one store. The divides are only
    paid on the fringe.
    """
    magic, shift = _reciprocal()
    w = [None] * UNPREMUL_WORDS
    w[0] = A.ldr(15, 20, FB_W_OFF)
    w[1] = A.ldr(16, 20, TEX_W_OFF)
    w[2] = cmp_reg_lsl(15, 16, surf_shift())
    w[3] = A.bcond(addr(3), addr(UNPREMUL_STOCK), COND_NE)
    # --- one destination pixel -------------------------------------------
    # The reciprocal is rebuilt per texel rather than hoisted, which buys w16
    # as a second scratch register for the division. Two ALU ops against
    # three divides is the right trade, and every register the stock loop
    # owns stays untouched.
    w[4] = A.movz(16, magic & 0xFFFF)
    w[5] = A.movk_hi(16, magic >> 16)
    w[6] = umull(14, 12, 16)
    w[7] = lsr64(14, 14, shift)
    w[8] = A.add_reg64_lsl(14, 11, 14, 2)
    w[9] = ldur32(15, 14, -3)                  # the whole source pixel
    w[10] = rev32(15, 15)                      # the loop's byte shuffle,
    w[11] = ror32(15, 15, 8)                   #   dest[0]<-src[2] etc, in two
    w[12] = A.lsr(17, 15, 24)                  # alpha
    w[13] = A.cmp_imm(17, 0xFF)
    w[14] = A.bcond(addr(14), addr(UNPREMUL_STORE), COND_EQ)   # nothing mixed
    w[15] = A.cmp_imm(17, 0)
    w[16] = A.bcond(addr(16), addr(UNPREMUL_STORE), COND_EQ)   # already zero
    w[17] = A.movz(16, 255)
    for n, lsb in enumerate((0, 8, 16)):       # the three colour bytes
        i = 18 + 4 * n
        w[i] = ubfx32(14, 15, lsb, 8)
        w[i + 1] = A.mul(14, 14, 16)           # c * 255
        w[i + 2] = udiv32(14, 14, 17)          # ... / a
        w[i + 3] = bfi32(15, 14, lsb, 8)
    w[UNPREMUL_STORE] = stur32(15, 13, -3)
    w[31] = A.add_imm64(12, 12, 1)
    w[32] = A.add_imm64(13, 13, 4)
    w[33] = A.cmp_reg64(12, 10)
    w[34] = A.bcond(addr(34), addr(UNPREMUL_LOOP_TOP), COND_LT)
    w[35] = A.b(addr(35), AFTER_LOOP)
    w[36] = HOOK_STOCK
    w[37] = A.b(addr(37), STOCK_LOOP)
    assert all(x is not None for x in w)
    return w


PUNCH_WORDS = 26
PUNCH_LOOP_TOP = 6
PUNCH_STOCK = 24


def punch_body(addr):
    """'punch' -- a texel with no captured content is not DRAWN.

    BUILD 307, and it is build 306's own result that determines it.

    306 filled every hole from the texel beside it and forced the result
    opaque. On hardware that did two things:

        the RIGHT-hand squares       unchanged
        the LEFT-hand hole, which
          has looked correct in
          every build so far         became a long horizontal SMEAR

    The second half is the finding. Under the shipping `copy` body those
    left-hand texels are black in the source -- the `srcblack` map painted
    them RED -- and yet the field looks right there. The disc does cover them:
    `report` painted that whole region grey and `srcblack` painted it red, so
    the mesh samples those texels. A black texel that is sampled and does not
    show black is a texel that is **not drawn**, and the one channel that can
    do that is alpha. 306 forced alpha to 0xFF, and that is precisely when
    the region stopped being invisible.

    So the hole already resolves correctly on the left: alpha 0, nothing
    drawn, the real floor showing through. That is not a defect -- it is the
    behaviour we want everywhere, and it is better than anything the copy can
    synthesise, because the floor underneath IS the thing the capture was
    trying to reproduce, at native resolution, with no smear and no dial.

    The right-hand squares are the texels where that fails: the source is
    black but its alpha is not zero, so the void gets drawn. This makes the
    two agree:

        source pixel is (near) black   ->   write 0x00000000, alpha 0
        otherwise                      ->   copy it, source alpha and all

    Note what this is NOT: it is not a fill, not a smear, not a colour, not a
    threshold that has to be tuned to taste. It removes texels that carry no
    picture from the draw, which is what the left-hand hole already does.

    It is also safe under the opposite reading. If the blend turns out to
    ignore alpha, a punched texel stays black -- exactly what it is today --
    so this cannot regress the picture; it can only repair it. And the
    content region is untouched byte for byte, source alpha included, so 306's
    left-hand smear goes away with it.
    """
    magic, shift = _reciprocal()
    w = [None] * PUNCH_WORDS
    w[0] = A.ldr(15, 20, FB_W_OFF)
    w[1] = A.ldr(16, 20, TEX_W_OFF)
    # BUILD 309: `<< surf_shift()` follows ff7nx_fbsurf. At the stock surface
    # the shift is 0 and this is the identical word to `cmp w15, w16`.
    w[2] = cmp_reg_lsl(15, 16, surf_shift())
    w[3] = A.bcond(addr(3), addr(PUNCH_STOCK), COND_NE)
    w[4] = A.movz(16, magic & 0xFFFF)
    w[5] = A.movk_hi(16, magic >> 16)
    # --- one destination pixel, same source address as 'copy' -------------
    w[6] = umull(14, 12, 16)
    w[7] = lsr64(14, 14, shift)
    w[8] = A.add_reg64_lsl(14, 11, 14, 2)
    w[9] = ldurb(15, 14, -1)                   # src[2] -> dest byte 0
    w[10] = ldurb(17, 14, -2)                  # src[1] -> dest byte 1
    w[11] = A.orr_lsl(15, 15, 17, 8)
    w[12] = ldurb(17, 14, -3)                  # src[0] -> dest byte 2
    w[13] = A.orr_lsl(15, 15, 17, 16)          # the three colour bytes
    # Branch-free, deliberately. `_walk` treats an unconditional `b` inside the
    # cave as a run-to-run link -- that is how a body survives being scattered
    # through padding -- so a body may not contain one except its two exits. A
    # CSEL says the same thing in one word and cannot be mistaken for a link.
    w[14] = A.cmp_imm(15, 8)                   # content, or the void?
    w[15] = ldurb(17, 14, 0)                   # the SOURCE's alpha, as 'copy'
    w[16] = A.orr_lsl(17, 15, 17, 24)          # the whole pixel, unmodified
    w[17] = A.csel(15, 17, A.WZR, COND_GE)     # ... or nothing at all
    w[18] = stur32(15, 13, -3)
    w[19] = A.add_imm64(12, 12, 1)
    w[20] = A.add_imm64(13, 13, 4)
    w[21] = A.cmp_reg64(12, 10)
    w[22] = A.bcond(addr(22), addr(PUNCH_LOOP_TOP), COND_LT)
    w[23] = A.b(addr(23), AFTER_LOOP)
    w[24] = HOOK_STOCK
    w[25] = A.b(addr(25), STOCK_LOOP)
    assert all(x is not None for x in w)
    return w


BLACK_WORDS = 28
BLACK_LOOP_TOP = 6
BLACK_STOCK = 26


def black_body(addr):
    """'srcblack' -- paint each texel by WHAT IT READ, not by what it is.

    Every other candidate for the black squares has been eliminated by
    execution (see BUILD-304 and FINDINGS-303): no texel is left unwritten,
    the copy cannot manufacture black, the staging surface has no undrawn
    margin, and forcing alpha opaque changed nothing on hardware.

    What is left is that the source pixel really is black. This tests that
    directly and reports it in a colour nobody can misread:

        source R|G|B  <  8   ->   RED,   opaque
        otherwise            ->   GREEN, opaque

    So the field comes back as a two-colour map. Red exactly where the black
    squares are means the capture is faithfully copying black that is already
    in the staging surface, and the search moves out of the capture code for
    good. Green there means the source is NOT black and something after the
    copy is producing them.

    The source address is computed exactly as the shipping 'copy' body does --
    same gate, same 640/854 step, same `add x14, x11, x14, lsl #2` -- so this
    reads the same pixels the real capture reads, not a re-derivation of them.
    """
    magic, shift = _reciprocal()
    w = [None] * BLACK_WORDS
    w[0] = A.ldr(15, 20, FB_W_OFF)
    w[1] = A.ldr(16, 20, TEX_W_OFF)
    w[2] = cmp_reg_lsl(15, 16, surf_shift())
    w[3] = A.bcond(addr(3), addr(BLACK_STOCK), COND_NE)
    w[4] = A.movz(16, magic & 0xFFFF)
    w[5] = A.movk_hi(16, magic >> 16)
    # --- one destination pixel, same source address as 'copy' -------------
    w[6] = umull(14, 12, 16)
    w[7] = lsr64(14, 14, shift)
    w[8] = A.add_reg64_lsl(14, 11, 14, 2)
    w[9] = ldurb(15, 14, -1)                   # B
    w[10] = ldurb(17, 14, -2)                  # G
    w[11] = A.orr_lsl(15, 15, 17, 0)
    w[12] = ldurb(17, 14, -3)                  # R
    w[13] = A.orr_lsl(15, 15, 17, 0)           # w15 = R | G | B
    w[14] = A.cmp_imm(15, 8)                   # "is this pixel black?"
    w[15] = A.movz(17, 0xFF00)                 # movz/movk do not touch flags
    w[16] = A.movk_hi(17, 0xFF00)              # GREEN, opaque
    w[17] = A.movz(15, 0x0000)
    w[18] = A.movk_hi(15, 0xFFFF)              # RED, opaque
    w[19] = A.csel(15, 15, 17, COND_LT)        # black -> red, else green
    w[20] = stur32(15, 13, -3)
    w[21] = A.add_imm64(12, 12, 1)
    w[22] = A.add_imm64(13, 13, 4)
    w[23] = A.cmp_reg64(12, 10)
    w[24] = A.bcond(addr(24), addr(BLACK_LOOP_TOP), COND_LT)
    w[25] = A.b(addr(25), AFTER_LOOP)
    w[26] = HOOK_STOCK
    w[27] = A.b(addr(27), STOCK_LOOP)
    return w


def body_words(addr, probe=None, how=None):
    """
    The 24 words, as a function of `addr(i)` -> the real address word i lands
    on.  Two branches inside the body resolve against those addresses, so the
    cave works chained through scattered padding exactly as it does flat.

    `probe` swaps the eight sampling words for eight that write the texel's
    own coordinates.  The word COUNT, the loop, the gate and the stock path are
    identical in both, so every walk/verify/revert path is shared.
    """
    if how is None:
        how = ('texel' if probe else 'copy') if probe is not None else mode()
    if how == 'fillhole':
        return hole_body(addr)
    if how == 'punch':
        return punch_body(addr)
    if how == 'unpremul':
        return unpremul_body(addr)
    if how == 'srcblack':
        return black_body(addr)
    if how in ('proj', 'projtexel'):
        return proj_body(addr, how)
    if how in ('frame', 'texel', 'alpha'):
        return frame_body(addr, how)
    # 'opaque' is the 24-word copy body with one word changed; it must NOT be
    # dispatched to frame_body the way 'alpha' now is.
    # 'report' shares the 24-word copy body; only the eight sampling words and
    # the gate comparison differ.
    probe = False
    magic, shift = _reciprocal()
    w = [None] * N_WORDS
    w[0] = A.ldr(15, 20, FB_W_OFF)            # ldr   w15, [x20, #0x1c]
    w[1] = A.ldr(16, 20, TEX_W_OFF)           # ldr   w16, [x20, #0x3c]
    # 'report' is a diagnostic and is deliberately UNGATED: it must run on
    # every capture in the game, because the whole point is that I do not know
    # which capture the field uses. `cmp w15, w15` is always equal, so the
    # not-equal branch below can never be taken and the stock path is never
    # reached -- the word count, the walk and the revert stay identical.
    w[2] = (A.cmp_reg(15, 15) if how == 'report'
            else cmp_reg_lsl(15, 16, surf_shift()))
    w[3] = A.bcond(addr(3), addr(STOCK_ENTRY), COND_NE)
    if how == 'stretch':
        # BUILD 301 -- FILL THE SHEET, WHATEVER THE SOURCE HOLDS.
        #
        # FFNx hard-codes the UV step at 1/256 (animations.cpp:868,
        # `u_offset = field_8_float * 0.00390625`), so the mesh ALWAYS spans
        # the whole sheet. A sheet that is only partly written therefore shows
        # its unwritten columns -- the black edge -- and its content sits where
        # a fraction of the sheet is, which reads as the wrong scale.
        #
        # So stretch the source across the destination instead of copying it
        # one for one:
        #
        #     src_col = i * avail / dest_w        avail   = w24 - w23
        #                                         dest_w  = fb_tex.w
        #
        # Every destination column is written, always. No constant: both terms
        # are live registers, so it is right whatever the surface turns out to
        # be -- and when `avail == dest_w` the division is the identity and
        # this is the stock 1:1 copy, byte for byte, which is why it needs no
        # gate. w23/w24 survive the row loop (nothing between +0x10D71E8 and
        # +0x10D7238 writes them), so the body can recompute avail itself.
        w[4] = A.sub_reg(16, 24, 23)          # sub  w16, w24, w23   avail
        w[5] = A.ldr(17, 20, FB_W_OFF)        # ldr  w17, [x20, #0x1c] dest_w
        # --- one destination pixel ---------------------------------------
        w[6] = A.mul(14, 12, 16)              # mul  w14, w12, w16
        w[7] = A.udiv(14, 14, 17)             # udiv w14, w14, w17
        w[8] = A.add_reg64_lsl(14, 11, 14, 2)  # add x14, x11, x14, lsl #2
    else:
        w[4] = A.movz(16, magic & 0xFFFF)     # movz  w16, #lo
        w[5] = A.movk_hi(16, magic >> 16)     # movk  w16, #hi, lsl #16
        # --- one destination pixel ---------------------------------------
        w[6] = umull(14, 12, 16)              # umull x14, w12, w16
        w[7] = lsr64(14, 14, shift)           # lsr   x14, x14, #32   src col
        w[8] = A.add_reg64_lsl(14, 11, 14, 2)  # add x14, x11, x14, lsl #2
    if how == 'report':
        # BUILD 289 -- THE DIAGNOSTIC THAT ENDS THE GUESSING.
        #
        # Every wrong turn in this effect came from a number I inferred instead
        # of measured: the staging surface's size, the capture's tex_format,
        # which capture the field even uses. This paints every capture a FLAT
        # COLOUR that encodes the two numbers that settle all of it:
        #
        #     R = fb_tex.w >> 3        G = fb_tex.h >> 3        B = 0   A = 255
        #
        # With ff7nx_fbfit installed, fb_tex.w/h ARE the region the copy really
        # wrote -- `min(surface, origin + size) - origin` -- so reading them
        # off one screenshot gives the surface size directly:
        #
        #     Kujata's rect is 256 wide at origin 80
        #       surface 640 -> fb_tex.w = 256 -> R = 32
        #       surface 320 -> fb_tex.w = 240 -> R = 30
        #
        # Granularity is 8 px, which is far finer than the differences that
        # matter. One screenshot, no more inference.
        # BUILD 290 -- EXACT, after build 289 answered the coarse question.
        #
        # Build 289 wrote (w >> 3, h >> 3) and the field came back a perfectly
        # flat RGB(0, 21, 19), std 0.0. Two things follow. The byte I
        # deliberately zeroed is the one that arrived as RED, so the channel
        # order on screen is
        #
        #     displayed (R, G, B)  =  dest bytes (2, 1, 0)
        #
        # which is exactly how build 279's texel probe decoded (its 0x40 marker
        # went to dest byte 2 and read back as R = 64 EXACTLY). So there is no
        # colour modulation on this path and the channels are faithful.
        #
        # This reports the two numbers at FULL precision instead of in steps of
        # eight, and puts 0xFF in the red channel as its own calibration: if
        # red does not come back 255, something IS modulating and every other
        # reading is suspect.
        #
        #     B = fb_tex.w & 0xFF      G = fb_tex.h & 0xFF
        #     R = 0xFF (calibration)   A = 0xFF
        #
        # With ff7nx_fbfit installed these are the region the copy really
        # wrote, so they give the staging surface directly.
        # BUILD 291 -- GREY, BECAUSE MY TWO COLOUR READINGS CONTRADICT.
        #
        # Build 289 wrote (w >> 3, h >> 3) and read back w >> 3 = 19, h >> 3 =
        # 21. Build 290 wrote (w & 0xFF, h & 0xFF) and read back 0 and 0. Those
        # cannot both be true of the same numbers under ANY fixed byte-to-
        # channel mapping: 19 << 3 is 152..159, and x & 0xFF == 0 means a
        # multiple of 256. So one of my two decodes is wrong, and since the
        # only thing they disagree about is which byte is which channel, the
        # encoding must stop depending on that.
        #
        # This writes GREY -- the same value into all three colour bytes -- so
        # the reading is the same whatever the channel order, and it is read
        # off the screenshot as a brightness rather than a hue:
        #
        #     grey = value >> 1        exact for anything up to 511, step 2
        #
        # and it reports BOTH numbers in one screenshot by splitting on the
        # destination column, which the stock loop already counts in x12:
        #
        #     texel columns   0..127   ->  the SURFACE's pitch (w19)
        #     texel columns 128..255   ->  tex_format.width
        #
        # both as `value >> 3`, so anything up to 2040 fits in a byte:
        #
        #     grey  30 -> 240..247      grey  80 -> 640..647
        #     grey  32 -> 256..263      grey 160 -> 1280..1287
        #     grey  40 -> 320..327
        #
        # BUILD 297: the second half used to report fb_tex.h, and the answer
        # came back a single flat grey 122 -- so fb_tex.w and fb_tex.h are the
        # same number, 244. That is neither 256 (a rect that fits) nor 240 (a
        # 256-wide rect at origin 80 on a 320-wide surface), so both of my
        # surface models are wrong and height is not the interesting axis.
        #
        # What matters now is whether 244 is a SHORTFALL or the authored size:
        #
        #   244 and 256 ->  the copy writes 12 fewer columns than the UV space
        #                   addresses. Those 12 are the black edge, and the
        #                   disc dial has been cancelling 244/256 = 0.953.
        #                   Fix the width chain; the dial goes away.
        #   244 and 244 ->  nothing is short. The capture is self-consistent
        #                   and the disc's hard-coded 96 world units per texel
        #                   is what does not match it.
        #
        # Two different repairs, and one screenshot chooses.
        #
        # so the field comes back as two flat greys with a seam between them.
        # Alpha is forced opaque with a logical immediate, so no register is
        # needed for it and the whole thing still fits the eight words.
        w[9] = A.mov_reg(15, 19)              # mov  w15, w19   the row pitch
        w[10] = tbz32(12, 7, addr(10), addr(12))   # tbz w12, #7, +2
        w[11] = A.ldr(15, 20, TEX_W_OFF)      # ldr  w15, [x20, #0x3c]
        w[12] = A.lsr(17, 15, 3)              # lsr  w17, w15, #3
        w[13] = bfi32(17, 17, 8, 8)           # bfi  w17, w17, #8, #8
        w[14] = bfi32(17, 17, 16, 8)          # bfi  w17, w17, #16, #8  grey
        w[15] = orr_imm_ff000000(17, 17)      # orr  w17, w17, #0xff000000
        w[16] = stur32(17, 13, -3)            # stur w17, [x13, #-3]
    elif probe:
        # x12 is the destination column, w8 the destination row -- both are
        # the stock loop's own counters, so this reports the real indices.
        w[9] = A.and_mask(15, 12, 8)          # w15 = column & 0xFF
        w[10] = sturb(15, 13, -3)
        w[11] = A.and_mask(15, 8, 8)          # w15 = row & 0xFF
        w[12] = sturb(15, 13, -2)
        w[13] = A.movz(15, 0x40)
        w[14] = sturb(15, 13, -1)
        w[15] = A.movz(15, 0xFF)
        w[16] = sturb(15, 13, 0)
    else:
        w[9] = ldurb(15, 14, -1)              # B
        w[10] = sturb(15, 13, -3)
        w[11] = ldurb(15, 14, -2)             # G
        w[12] = sturb(15, 13, -2)
        w[13] = ldurb(15, 14, -3)             # R
        w[14] = sturb(15, 13, -1)
        # BUILD 304. 'opaque' differs from 'copy' in exactly this ONE word:
        # the destination alpha becomes 0xFF instead of a copy of the source's.
        #
        # Reasoned from the code, and from my own measurement:
        #
        #   * the copy propagates source alpha verbatim -- executed here, a
        #     source pixel with alpha 0 lands in the texture with alpha 0;
        #   * the `report` mode forces alpha opaque (`orr w17, #0xff000000`)
        #     and in EVERY report build the black squares disappeared;
        #   * the staging surface is drawn with a viewport covering its own
        #     full width and height (+0x10DBF00 GetWidth, +0x10DBF14 GetHeight,
        #     +0x10DBF40 setViewport), so it has no undrawn margin for the
        #     copy to read;
        #   * and the frame on screen at capture time has no black in it.
        #
        # Written texels, a fully drawn source, no black in the frame -- and
        # the black vanishes exactly when alpha is forced. That leaves the
        # source's ALPHA CHANNEL as what the black squares are made of.
        w[15] = (A.movz(15, 0xFF) if how in ('alpha', 'opaque')
                 else ldurb(15, 14, 0))       # A
        w[16] = sturb(15, 13, 0)
    w[17] = A.add_imm64(12, 12, 1)            # add   x12, x12, #1
    w[18] = A.add_imm64(13, 13, 4)            # add   x13, x13, #4
    w[19] = A.cmp_reg64(12, 10)               # cmp   x12, x10
    w[20] = A.bcond(addr(20), addr(LOOP_TOP), COND_LT)
    w[21] = A.b(addr(21), AFTER_LOOP)         # row done
    # --- the stock path, byte-for-byte -----------------------------------
    w[22] = HOOK_STOCK                        # mov   x14, x11
    w[23] = A.b(addr(23), STOCK_LOOP)
    return w


# --------------------------------------------------------------------------
def enabled():
    """
    ON with 16:9, OFF at 4:3.

    This is build 268's capture and it is the only one the TV has confirmed
    correct on a flat stage -- one texel, one game unit, anchored on the
    rect's own origin. It was briefly turned off in build 269 to pair with
    `ff7nx_summonreach`; that pairing is disproven and both halves are back
    where they were.
    """
    v = os.environ.get(RESAMPLE_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false', 'no')
    try:
        import ff7nx_ws
        return ff7nx_ws.enabled()
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


def _walk(img, n=None):
    """
    (logical, physical) addresses of the cave.

    `logical` is the 24 instruction words; `physical` additionally holds the
    run-to-run link branches. revert needs the physical set: zeroing only the
    logical words would leave stray `b`s live in someone else's padding, the
    next allocator would skip those holes, and apply -> revert would stop
    being byte-identical.

    The rule -- a `b` is a link unless it targets one of the cave's own two
    exits -- is exact, because those are the only unconditional branches the
    body contains.
    """
    entry = _b_target(_word(img, HOOK), HOOK)
    if entry is None:
        return [], []
    logical, physical, seen, va = [], [], set(), entry
    n = N_WORDS if n is None else n
    while len(logical) < n and va not in seen and 0 <= va <= len(img) - 4:
        seen.add(va)
        physical.append(va)
        tgt = _b_target(_word(img, va), va)
        if tgt is not None and tgt not in (AFTER_LOOP, STOCK_LOOP):
            va = tgt          # a run-to-run link, never logic
            continue
        logical.append(va)
        va += 4
    return logical, physical


def walk(img, n=None):
    """The cave's logical addresses, chaining branches removed."""
    return _walk(img, n)[0]


def walk_physical(img, n=None):
    """Every address the cave occupies, link branches included."""
    return _walk(img, n)[1]


MODES = ('copy', 'texel', 'alpha', 'frame', 'proj', 'projtexel',
         'report', 'stretch', 'opaque', 'srcblack', 'fillhole', 'punch',
         'unpremul')


def n_words(how):
    """'copy' is the stock 1:1 resample; 'proj'/'projtexel' are the projective
    body; everything else is a frame body."""
    if how == 'unpremul':
        return UNPREMUL_WORDS
    if how == 'punch':
        return PUNCH_WORDS
    if how == 'fillhole':
        return HOLE_WORDS
    if how == 'srcblack':
        return BLACK_WORDS
    if how in ('copy', 'report', 'stretch', 'opaque'):
        return N_WORDS
    if how in ('proj', 'projtexel'):
        return PROJ_WORDS
    return FRAME_WORDS


def installed_mode(img):
    """Which body the live cave carries, or None."""
    for how in MODES:
        n = n_words(how)
        addrs = walk(img, n)
        if len(addrs) != n:
            continue
        got = [_word(img, va) for va in addrs]
        if got == body_words(lambda i: addrs[i], how=how):
            return how
    return None


def installed(img):
    """Installed AND in the mode this build is asking for."""
    return installed_mode(img) == mode()


def read_state(img):
    if _word(img, HOOK) == HOOK_STOCK:
        return 'stock'
    how = installed_mode(img)
    if how is None:
        return 'unknown'
    return 'resampling' if how == 'copy' else 'resampling+%s' % how


SELECTOR = 0x10D70B0             # cmp w8, #1  -- the CPU/GPU path selector
SELECTOR_STOCK = 0x7100051F
COLUMNS = 0x10D71C0              # sub w10, w24, w23  -- the column count
COLUMNS_STOCK = 0x4B17030A


def verify(img):
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('+0x%X is %08X, expected %08X' % (va, got, want))
    # The selector one word above the loop is shared with ff7nx_fbpath, which
    # replaces it with a branch into its own gate. Either state is legitimate;
    # anything else means the routine moved.
    sel = _word(img, SELECTOR)
    if sel != SELECTOR_STOCK and (sel & 0xFC000000) != 0x14000000:
        bad.append('+0x%X is %08X -- neither the stock `cmp w8, #1` nor a '
                   'branch into ff7nx_fbpath\'s gate' % (SELECTOR, sel))
    # Likewise the column count, which ff7nx_fbfit hooks to fit the texture to
    # the region the loop actually writes. This module never touches either.
    col = _word(img, COLUMNS)
    if col != COLUMNS_STOCK and (col & 0xFC000000) != 0x14000000:
        bad.append('+0x%X is %08X -- neither the stock `sub w10, w24, w23` nor '
                   'a branch into ff7nx_fbfit\'s cave' % (COLUMNS, col))
    st = read_state(img)
    if st == 'unknown':
        bad.append('hook +0x%X is %08X -- neither stock nor a cave this build '
                   'recognises' % (HOOK, _word(img, HOOK)))
    return bad


def plan(m, revert=False):
    import cave_space
    img = m.img
    problems = [b for b in verify(img)]
    if problems:
        return [], [], problems
    state = read_state(img)
    have_any = installed_mode(img)
    if revert:
        if state == 'stock':
            return [], [], []
        addrs = walk_physical(img, n_words(have_any) if have_any else None)
        patches = [{'name': 'restore capture row loop', 'va': hex(HOOK),
                    'expect': struct.pack('<I', _word(img, HOOK)).hex(),
                    'set': struct.pack('<I', HOOK_STOCK).hex()}]
        for va in addrs:
            patches.append({'name': 'clear resample cave', 'va': hex(va),
                            'expect': struct.pack('<I', _word(img, va)).hex(),
                            'set': '00000000'})
        return patches, ['    capture resample removed'], []
    want_mode = mode()
    have_mode = installed_mode(img)
    if have_mode == want_mode:
        return [], ['    capture resample already installed (%s)'
                    % want_mode], []
    if have_mode is not None:
        # Same length, same layout, same branches -- only the eight sampling
        # words differ, so switch them in place rather than re-allocating a
        # cave (which would leave the old one behind and move the entry).
        if n_words(have_mode) != n_words(want_mode):
            # Different cave sizes cannot be switched in place. apply_all
            # reverts first and comes back here on a clean image; saying so
            # here keeps plan() a pure function of the image it was handed.
            return [], [], ['RESIZE']
        addrs = walk(img, n_words(have_mode))
        want = body_words(lambda i: addrs[i], how=want_mode)
        patches = []
        for va, wd in zip(addrs, want):
            have = _word(img, va)
            if have != wd:
                patches.append({'name': 'capture resample -> %s' % want_mode,
                                'va': hex(va),
                                'expect': struct.pack('<I', have).hex(),
                                'set': struct.pack('<I', wd).hex()})
        return patches, ['    capture resample switched to %s' % want_mode], []

    pool = ff7nx_cave.HolePool(
        img, starts=set(m.arm_starts),
        named=cave_space.named_targets(img[cave_space.RODATA:]))
    try:
        n = n_words(mode())
        runs = pool.take(n, span=0x80000)
        slots = ff7nx_cave.slots(runs, n)
        words = body_words(lambda i: slots[i])
        placed = ff7nx_cave.link(runs, words)
    except ff7nx_cave.NoRoom as exc:
        return [], [], ['capture resample cave: %s' % exc]
    placed[HOOK] = A.b(HOOK, slots[0])
    patches = []
    for va, want in sorted(placed.items()):
        patches.append({'name': 'capture resample'
                                + (' hook' if va == HOOK else ''),
                        'va': hex(va),
                        'expect': struct.pack('<I', _word(img, va)).hex(),
                        'set': struct.pack('<I', want).hex()})
    magic, shift = _reciprocal()
    notes = ['    1:1 captures: destination texel i <- staging column '
             'x + floor(i * 640 / 854) (magic 0x%08X >> %d)' % (magic, shift),
             '    page-scaled captures execute the stock loop unchanged',
             '    resample cave entry +0x%X' % slots[0]]
    if mode() in ('proj', 'projtexel'):
        c = proj_coef()
        notes.insert(0, '    PROJECTIVE remap, solved from the build-279 texel '
                        'probe (residual 2.06 staging px):')
        notes.insert(1, '      col = (%d*i %+d*j %+d) / (%d*i %+d*j %+d)'
                     % (c[0], c[1], c[2], c[6], c[7], c[8]))
        notes.insert(2, '      row = (%d*i %+d*j %+d) / (same denominator)'
                     % (c[3], c[4], c[5]))
        if mode() == 'projtexel':
            notes.insert(3, '      (texel read-out, not the picture)')
    elif mode() == 'texel':
        notes.insert(0, '    PROBE(texel): the texture is filled with its own '
                        'texel coordinates (B = u, G = v, R = 0x40), not the '
                        'frame')
    elif mode() == 'alpha':
        notes.insert(0, '    PROBE(alpha): the capture is copied as normal but '
                        'the texel ALPHA is forced to 0xFF instead of being '
                        'taken from the staging surface -- ONE word')
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'framebuffer capture resample', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fbresample-')
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
    if problems == ['RESIZE']:
        # The live cave is a different size from the one being asked for.
        # Take it out completely first, then install the new one on a clean
        # image -- never leave two caves, and never move an entry under a hook
        # that still points at the old one.
        log('  framebuffer capture resample: resizing the cave '
            '(%s -> %s), reverting first' % (installed_mode(m.img), mode()))
        if apply_all(main, revert=True, log=log) != 0:
            return 1
        m = nxmap.Main(str(main))
        patches, notes, problems = plan(m, revert=False)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write the framebuffer capture resample.')
        return 1
    log('  framebuffer capture resample:')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d capture resample word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
