#!/usr/bin/env python3
r"""
ff7nx_uncrop.py -- remove the black bar(s) at the top and bottom of the field.

    python3 ff7nx_uncrop.py <exefs/main> --verify     costs nothing, run it first
    python3 ff7nx_uncrop.py <exefs/main> --show
    python3 ff7nx_uncrop.py <exefs/main> --apply -o <out>

READ THIS FIRST -- THE PREVIOUS VERSION OF THIS FILE SHIPPED A CAVE THAT
COULD NOT FIRE, AND IT IS THE ONE ON PATRICK'S HARDWARE
=======================================================================
v3 hooked `gfx_drv_setviewport` and rewrote the rect when

    w1 == 16  &&  w3 == 448

**The field never passes y = 16.** MEASURED, three independent ways, all
offline, all in FINDINGS-85:

  1. `field_set_mode` (x86 0x60D837 -> +0x9297C0) has exactly THREE calls to
     `set_field_viewport` (+0x9296C0). Their pushed constants, read out of
     the image:

         +0x929910   448, 640, 0, 0      ->  (0,   0, 640, 448)   mode 2
         +0x929EAC   224, w22, w25, w24  ->  (160,120, 320, 224)  mode 1
         +0x929F58   224, 320, 0, 0      ->  (0,   0, 320, 224)   mode 0

  2. `[0xCFF1E0..0xCFF1EC]` -- the four globals those calls write -- have
     exactly ONE writer in the whole module (`set_field_viewport`) and four
     readers. `field_draw_everything` (+0x9E6E80) loads them from
     `w19 - 0x558 .. w19 - 0x54C` with `w19 = 0xCFF738` and pushes them
     straight into `engine_gfx_setviewport` at +0x9E80D8. Nothing adds 16.

  3. The x86 original says the same thing. `ff7_en_switch` file offset
     0x20CA61:

         68 C0 01 00 00   push 448
         68 80 02 00 00   push 640
         6A 00            push 0        <- y
         6A 00            push 0
         E8 ...           call set_field_viewport

     and a scan of every `push 448` in the whole exe finds no `push 16`
     beside any of them.

So v3's compare was false on every frame, the cave fell through to the
displaced word every time, and **the null result on hardware says nothing
about `gfx_drv_setviewport`**. v3's own `--verify` table contained the proof
and it was read past: the row labelled `field mode 2 (pushed)` -- the rect
the game really passes -- says `untouched`.

The `y == 16 && height == 448` constant came from FFNx `renderer.cpp:1673`.
It does not correspond to anything FF7 passes on this port OR on PC; it was
copied across without checking it against the arguments, which is the same
class of error as HANDOFF-83 2.7.

WHAT THE PORT ACTUALLY DOES WITH THE FIELD, MEASURED
====================================================
The 2D/TL path and the 3D path are separate, and the field background is 2D:

    +0x10DB384  draw_flat2D      w1 = 3   \
    +0x10DB478  draw_textured2D  w1 = 3    |  -> +0x10D9D70, `cmp w24,#3` ->
    +0x10DB6E4  draw_paletted2D  w1 = 3   /      the HARDCODED ortho below
    +0x10DB938  draw_flat3D      w1 = 2      -> d3dviewport x d3dprojection

    +0x10DA018  mov w8,#0xcccd / movk #0x3b4c   0x3B4CCCCD =  1/320
    +0x10DA024  mov w8,#0x8889 / movk #0xbb88   0xBB888889 = -1/240
                ... = ortho(0, 640, 480, 0), built on the stack, no globals

**The 2D projection is a fixed 640x480 ortho.** It cannot crop, it cannot
rescale, and it does not depend on the viewport rect. That is why attempt 1
(`_22 := 1.0`) moved nothing: `_22` is only ever applied to 3D geometry.

The field's own tile loop places the background at

    dst_y = (tile.y + 224 - cam_y) * 2          ORIGIN_Y = 224, MEASURED at
                                                +0xA06EA8 `mov w9, #0xe0`

so the 224-tile-unit field view lands on dst rows **0..448 of the 480-unit
ortho, TOP-ALIGNED**. FFNx's `ff7_field_center` option is exactly the 224 ->
232 that would centre it; this port has 224.

The only place 448 enters the render pipeline is the game's own rect. There
is no `movz #448`, no `movz #16` and no 448/480-derived float constant
anywhere in the native driver (0x10D3000..0x10E2000) -- checked by scan.
`gfx_drv_setviewport` turns the rect into exactly two things:

    device clip rect  y1 = tH*y/480, y2 = tH*(y+h)/480   -> [x13,#0x800/#0x808]
                      (0,0,640,448) at 1280x720          -> 0 .. 672
    d3dviewport       _22 = h/480 = 0.93333, _42 = +0.06667   3D ONLY

Both are TOP-ALIGNED and both predict one 48 px bar at the BOTTOM.

WHAT THE SCREEN ACTUALLY SHOWS -- AND WHY THAT KILLED THE PREDICTION
=====================================================================
Two fresh 1280x720 captures, per-row maximum luminance, four thresholds:
content occupies rows **24..695** in both. Exactly 24 top and 24 bottom, 672
of 720, CENTRED. The prediction above is wrong; FINDINGS-82's 24/24 was right.

v4 was then built, verified in the shipped module (the cave reads
`cmp w1, #0`, both retired attempts stock) and booted. **Nothing moved.**

That matters more than it looks. Forcing the rect to (0, 0, 640, 480) at the
top of the function makes ALL FOUR of its outputs full-frame at once --

    the device clip rect   [x13,#0x800/#0x808]
    the projection         _22 = 1.0, _42 = 0.0
    current_state.viewport [x8,#0x10..0x1C], which the per-draw helper
                           at +0x10D9D70 re-derives everything from
    (the +0x7F0/#0x7F8 "full" rect is computed from game_w/game_h, not the
     rect, and is unaffected)

-- and the screen did not change. Attempts 1, 2 and 4 each moved one output
and this moved three. **`gfx_drv_setviewport`'s rect has no measured path to
the field.** See `--probe` below, which is the one question left about it.

WHAT IS STILL TRUE, AND WHERE THAT POINTS
==========================================
The bars are FIELD-SPECIFIC -- menus and battle reach the top, on Patrick's
own report. Two things are field-specific and only two:

  1. the field's viewport rect (0,0,640,448), against the ENGINE's
     (0,0,640,480) that everything else uses. Read out of the x86 at
     0x7A776F, which sets `[0xF4F418..0xF4F424]` to (0,0,320,240),
     (160,120,320,240) or **(0,0,640,480)** -- the exact counterpart of
     `field_set_mode`'s (0,0,320,224) / (160,120,320,224) / (0,0,640,448).
     That pair of tables is the cleanest explanation anyone has had for why
     menus reach the top and the field does not.

  2. the LOW-RES FIELD RENDER TARGET. `gfx_drv_init` makes eight of them and
     the render-mode switch (+0x10DF6E0) points modes 0/2/3 -- the field
     modes -- at them. Menus and battle draw to the main target.

v4 tested (1) at the driver and it did nothing, so (2) is next.

THE TILE PIPELINE IS CLEAN, AND HERE IS THE WHOLE OF IT
========================================================
    field_layer1_pick_tiles  +0xA06DE0   ORIGIN_X 320 (+0xA06E60),
                                         ORIGIN_Y 224 (+0xA06EA8)
    dst = (tile + ORIGIN - cam) * mult,  mult = [0xCFF1F0] = 2 in mode 2
    culls admit dst-y -64..480           (top=256, bottom=16, both verified
                                          in the shipped module)
    add_page_tile  x86 0x6464BA  ->  tile.x += [0xCFF204]
                                     tile.y += [0xCFF208]

`[0xCFF204]`/`[0xCFF208]` are the one place FF7 could inset the field, and
they are written exactly once each, both `str wzr`, at +0x9299C4/+0x9299D0.
So the background really is emitted across the full game y 0..480. Nothing
in the game's own pipeline produces a bar.

THE PATCH, WHEN IT IS TIME
==========================
Identical machinery to v3 -- same hook, same cave shape, same anchors -- with
one immediate changed:

    cmp w1, #0x10      ->      cmp w1, #0

`(_, 0, _, 448)` is unique to field mode 2 across all 44 call sites of the
engine setviewport helper (`verify_framing.CASES`); the menus, battle, the
movie quad, the credits and the world map are byte-identically unaffected and
`verify()` asserts that by running each of them through the real words.

`apply_to_nso` upgrades v3 IN PLACE when it finds it: it follows the branch
at the hook, verifies the old cave word for word, and rewrites the single
compare. It does not need a clean base and it cannot double-apply.

WHAT IT WILL NOT FIX
====================
22 of 709 fields have less than 240 game units of layer-1/2 art (`hill`,
`junon`, `lastmap`, `zcoal_2` ...). Those keep a bar because there is no
picture behind it. See FINDINGS-84 4 and `verify_uncrop_span.py`. `md8_1`
-- Sector 8 -- has the full 240 and its top tile row is BRIGHTER than the
field average, so it is the right place to look.

THE TILE WINDOW IS ALREADY WIDE ENOUGH, MEASURED
================================================
`ff7nx_wsclamp` in Patrick's shipped module reads `top=256 bottom=16`. The
window that admits in dst-y is `((224-256)*2, (224+16)*2)` = `(-64, 480)`,
against an uncropped view of `[0, 480]`: 64 units of slack above, exact
below. `bg.y` cancels out of both sides, so that holds at every camera
position on every field. `clamp_is_present()` still checks, because stock
`bottom = 0` would clip the bottom 32 units and turn a fix into a half-fix.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A

# ==========================================================================
# THE RECT
# ==========================================================================
SETVIEWPORT = 0x10D6760

# What the field ASKS FOR. Read out of the image at +0x929910 and confirmed
# against the x86 at ff7_en_switch:0x20CA61. NOT FFNx's (16, 448).
CROP_Y, CROP_H = 0, 448
FULL_Y, FULL_H = 0, 480         # what uncrop turns it into

# v3's constant, kept only so `apply_to_nso` can recognise and repair the
# cave that is currently on hardware.
V3_CROP_Y = 16

# ==========================================================================
# --probe: THE UNMISSABLE TEST
# ==========================================================================
# v4 fired -- the cave in Patrick's booted module reads `cmp w1, #0`, both
# retired attempts are stock, and the module was read back and verified. The
# field's rect became (0, 0, 640, 480), which makes the clip rect, `_22`,
# `_42` and `current_state.viewport` ALL full-frame. Nothing moved.
#
# That is three null results on one function, and each time the response was
# to pick a different one of its outputs. The outputs are now exhausted, so
# the question is no longer "which output" but "is this function's rect read
# at all", and that is answered by asking for something that cannot be
# mistaken for a null result rather than by asking for the fix again.
#
# `--probe` writes 240 instead of 480. The field then asks for a HALF-HEIGHT
# viewport. If `gfx_drv_setviewport`'s rect reaches the screen in any form,
# the field collapses into a band and you cannot fail to see it.
#
#     field visibly halved / squashed / clipped
#         -> the rect IS honoured. Then 448 -> 480 not removing the bars
#            means the 448 band is re-established somewhere downstream, and
#            we know the mechanism is a viewport rather than a letterbox.
#     absolutely nothing changes
#         -> the rect has NO path to the screen. `gfx_drv_setviewport` is
#            dead for the field, for the third and last time, and the next
#            lever is the field render target -- which is the only other
#            thing that is field-specific (menus and battle draw to the main
#            target and they reach the top).
#
# It is one word, it reverts by re-running without --probe, and no other
# rect in the game is touched.
PROBE_H = 240

# ==========================================================================
# THE HOOK
# ==========================================================================
#
# +0x10D6760  adrp x8, #0x12ce000        <- PC-RELATIVE, cannot be displaced
# +0x10D6764  ldr  x8, [x8, #0x578]
# +0x10D6768  adrp x12, #0x12ce000       <- PC-RELATIVE
# +0x10D676C  mov  w10, #0xcccd          <- HOOK HERE. movz, position-free.
# ...
# +0x10D677C  ucvtf s5, w2               first use of an argument (w2)
# +0x10D67A0  ucvtf s0, w1               FIRST USE OF w1
# +0x10D67A8  mul   w15, w12, w3         FIRST USE OF w3
#
# So at +0x10D676C both `w1` and `w3` still hold the incoming arguments and
# nothing has consumed them. `ff7nx_cave.emit_laid_out` lays the body out
# BEFORE the displaced word, so the compare runs on the arguments as passed.
HOOK_VA = 0x10D676C
HOOK_ORIG = 0x529999AA          # mov w10, #0xcccd   -- READ from the image
RETURN_VA = HOOK_VA + 4

# NZCV. The cave leaves the flags set by its own `cmp`. The function's next
# flag consumer is its own `cmp w9, #3` at +0x10D6860, which sets them.
# `nzcv_is_dead()` asserts that against the real disassembly.

# v6: TWO INDEPENDENT REWRITES, because the band's y and the band's h have
# now been shown to come from different places.
#
#     if (y == 16)  y = 0        <- v3's test. The GAME never passes 16, but
#                                   the band starts at tH*16/480 in every
#                                   capture, so the DRIVER may still see it
#                                   from a caller that is not
#                                   field_draw_everything.
#     if (h == 448) h = 480      <- v4's test. Proven to reach the band by
#                                   --probe (h=240 gave a 360-row band).
#
# They are independent, so the cave covers (0,448), (16,448) and (16,480) --
# every combination the evidence allows. Neither 16 nor 448 appears in any
# other rect the game passes (`verify_framing.CASES`), so both are safe
# sentinels and nothing else in the game can match.
#
# v4's mistake was throwing the y test away on the grounds that the game does
# not pass 16. That was true and irrelevant: what the game passes and what the
# driver receives are different questions, and only the second one places
# pixels.
I_SET_Y = 2
I_SET_H = 5
I_SKIP_Y = 3                    # where the y test branches when it fails
I_SKIP_H = 6                    # where the h test branches when it fails
N_WORDS = 8

DISASM = [
    'cmp w1, #0x10',            # is y the 16 the band starts at?
    'b.ne #skip_y',
    'mov w1, wzr',              #   y := 0
    'cmp w3, #0x1c0',           # is h the crop?
    'b.ne #skip_h',
    'mov w3, #0x1e0',           #   h := 480
    'mov w10, #0xcccd',         # the displaced word
    'b #return',
]
NE = 1

BAND_Y = 16                     # the offset every capture starts at


def cave_words(addr, return_va=RETURN_VA, crop_y=BAND_Y, set_h=None):
    """The whole cave, laid out at addr(i). Two independent tests."""
    w = [
        A.cmp_imm(1, crop_y),                       # cmp  w1, #16
        A.bcond(addr(1), addr(I_SKIP_Y), NE),
        A.mov_reg(1, 31),                           # mov  w1, wzr
        A.cmp_imm(3, CROP_H),                       # cmp  w3, #448
        A.bcond(addr(4), addr(I_SKIP_H), NE),
        A.movz(3, FULL_H if set_h is None else set_h),
        HOOK_ORIG,                                  # the displaced word
    ]
    assert len(w) == N_WORDS - 1, 'body is %d words' % len(w)
    w.append(A.b(addr(N_WORDS - 1), return_va))
    return w


# ---------------------------------------------------------------- the anchors
#
# Every value READ out of the shipped `.text`, not derived. A first draft of
# the retired cave hand-encoded three anchors from a disassembly listing and
# all three were wrong. The anchors are the one thing in this module that must
# not be reasoned about.
ANCHORS = {
    SETVIEWPORT: 0x90000FC8,   # adrp  x8, #0x12ce000       function entry
    0x10D6764:   0xF942BD08,   # ldr   x8, [x8, #0x578]
    0x10D6768:   0x90000FCC,   # adrp  x12, #0x12ce000
    0x10D6770:   0x72B9998A,   # movk  w10, #0xcccc, lsl #16
    0x10D678C:   0xB940018C,   # ldr   w12, [x12]           tH
    0x10D67A0:   0x1E230020,   # ucvtf s0, w1               first use of w1
    0x10D67A8:   0x1B037D8F,   # mul   w15, w12, w3         first use of w3
    0x10D6860:   0x71000D3F,   # cmp   w9, #3               the battle case
}
# The hook word itself is checked separately, because a module carrying v3
# has a branch there and must still be recognised.

# Where the field's rect is BUILT. Not patched by this module -- listed so the
# next person can re-verify the premise in one command instead of re-deriving
# it. All read from the image; see FINDINGS-85.
RECT_PROVENANCE = {
    0x9296C0: 'set_field_viewport(x,y,w,h) -> [0xCFF1E0..EC]  (x86 0x60D810)',
    0x929910: 'field_set_mode mode 2 call: pushes 448, 640, 0, 0',
    0x9298BC: '  mov w8, #0x1c0   the 448',
    0x9298D4: '  mov w8, #0x280   the 640',
    0x9298EC: '  str wzr          the y -- ZERO, not 16',
    0x9E80D8: 'field_draw_everything -> engine_gfx_setviewport (x86 0x63A60B)',
    0xA06EA8: 'field_layer1_pick_tiles  mov w9, #0xe0   ORIGIN_Y = 224',
    0x10DA018: 'the 2D/TL ortho: 1/320, -1/240 = ortho(0,640,480,0)',
}

# ==========================================================================
# THE TWO RETIRED ATTEMPTS -- BOTH ARE BACKED OUT
# ==========================================================================
#
# ATTEMPT 1, +0x10D6868: NOP the `b.ne` so `_22 := 1.0` in every mode.
# Applied, booted, moved nothing -- and the reason is now measured rather than
# guessed: `_22` reaches only 3D geometry (`w1 = 2` at +0x10DB938), and the
# field background is 2D (`w1 = 3`), which goes through the fixed ortho.
VP22_VA = 0x10D6868
VP22_STOCK = 0x54000061      # b.ne #0x10d6874          READ from the image
VP22_SET = 0xD503201F        # nop

# ATTEMPT 2, +0x929964: the `224` written to `[0xCFF200]`.
# Applied, booted, moved nothing.  **NOTHING IN THE MODULE EVER READS
# 0xCFF200.**  A scan of every `movz/movk` pair forming a `0xCF____` address,
# plus every add/sub immediate applied to one within its function, finds
# exactly two references -- `+0x929950` and `+0x92995C` -- and both are the
# writes in `field_set_mode`. It is dead storage, so attempt 2's null result
# carries no information, and it is STILL IN PATRICK'S SHIPPED MODULE
# (`+0x929964 = 0xF0`). A live unintended change to a value nobody reads is a
# defect, not a neutral: it makes the next A/B unattributable.
HALF_H_VA = 0x929964
HALF_H_STOCK = 0x321B0BE8    # orr w8, wzr, #0xe0   (224)   READ from the image
HALF_H_SET = 0x52801E08      # movz w8, #0xf0       (240)


# ==========================================================================
# v5 -- THE GAME'S OWN COPY OF THE RECT. THIS IS THE ONE THE CAVE COULD NOT
#       REACH, AND THE PROBE IS WHAT PROVED IT EXISTS.
# ==========================================================================
#
# The probe measured the band as a function of the rect, on hardware:
#
#     h = 448 (stock)   content rows  24 .. 695     672 rows
#     h = 240 (probe)   content rows  24 .. 384     360 rows
#
#     band = ( 24 , 24 + tH*h/480 )
#
# The HEIGHT tracks the rect exactly -- 720*240/480 = 360, measured 360. So
# `gfx_drv_setviewport`'s `h` does reach the screen and the cave does work.
# The OFFSET does not: it stays 24 px while we force `y = 0`, and a centring
# would have put a 360-row band at (720-360)/2 = 180, not 24. **It is a
# constant 16 game units.**
#
# The cave rewrites the DRIVER's arguments at the top of `gfx_drv_setviewport`.
# It cannot reach the copy the GAME keeps, and there is exactly one:
# `engine_gfx_setviewport` (x86 0x66067A) stores the rect into
# `game_obj+0x848..0x854` and `y+h-1` into `+0x85C` BEFORE calling the driver,
# and `field_set_mode` keeps its own at `[0xCFF1E0..0xCFF1EC]`. Both still say
# 448 in every build so far, because every patch to date has been on the
# driver side of that call.
#
# So this patches the SOURCE -- one immediate in `field_set_mode`:
#
#     +0x9298BC   orr w8, wzr, #0x1c0   (448)  ->  movz w8, #0x1e0   (480)
#
# which makes the field ask for `(0, 0, 640, 480)` everywhere at once:
#
#     [0xCFF1EC]              -> field_draw_everything -> the driver
#     game_obj+0x854 / +0x85C -> whatever computes the 24 px offset
#     x86 0x63AC66            -> THE FADE QUAD, which is exactly FFNx's
#                                `height += 32` (field.cpp:162) for free
#     x86 0x60E0BD            -> its two setviewport calls
#
# FINDINGS-82 4 rejected this patch on the grounds that "the models move",
# reasoning from the NEIGHBOURING push (+0x9298D4, the width) having moved
# models on hardware. The probe retires that objection: when the band moved,
# the characters moved with it -- background and models are placed by the same
# rect and stay in register.
#
# The cave is REMOVED when this ships. With the game asking for 480 its
# compare can never be true again, and a patch that cannot fire is exactly
# what cost this project three boots.
FIELD_H_VA = 0x9298BC
FIELD_H_STOCK = 0x321A0BE8       # orr  w8, wzr, #0x1c0   (448)  READ from the image
FIELD_H_SET = A.movz(8, FULL_H)  # movz w8, #0x1e0        (480)

# The neighbouring words, checked so the immediate cannot land on a lookalike.
# `orr w8, wzr, #0x1c0` is not unique in .text; this block is.
#
# EVERY WORD BELOW WAS READ OUT OF THE IMAGE, and cross-checked between the
# fresh build and the untouched `dump/exefs/main`. The first draft of this
# block had `0x941F32BA` for the `bl`, typed from a disassembly listing --
# the real word is `0x941F4ABA`. Same trap the header already warns about,
# caught by the signature check rather than by a boot.
FIELD_H_SIG = {
    0x9298B0: 0x51001100,   # sub  w0, w8, #4        decrement the guest esp
    0x9298B4: 0xB90012A0,   # str  w0, [x21, #0x10]
    0x9298B8: 0x941F4ABA,   # bl   #0x10fc3a0        resolve the guest slot
    0x9298BC: 0x321A0BE8,   # orr  w8, wzr, #0x1c0   THE 448
    0x9298C0: 0xB9000008,   # str  w8, [x0]          push it
    0x9298D4: 0x52805008,   # mov  w8, #0x280        the 640, one push later
}


# ==========================================================================
# v7 -- THE WINDOW ITSELF. FOUND BY THE PROBE, NOT BY DISASSEMBLY.
# ==========================================================================
#
# Four boots, one formula, all measured off captures:
#
#     band = [ 24 , min( 24 + tH*h/480 , 696 ) ]
#
#        stock    h=448   predict 24..696   observed 24..695
#        --probe  h=240   predict 24..384   observed 24..384
#        v4/v5    h=480   predict 24..696   observed 24..695
#        v6       h=480   predict 24..696   observed 24..695
#
# The rect can SHRINK the band and can never GROW it past 696. So the band is
# an INTERSECTION with a fixed window at device rows 24..696 = game y 16..464
# -- 448 tall, centred in 480 -- and that window is not the rect.
#
# It is two `SetRect` calls in field code, hardcoded, in both coordinate
# spaces at once. x86 0x62F7C2, inside sub_62F3A5:
#
#     push 0x1D0 / 0x280 / 0x10 / 0 / &r1 ; call [0x7B632C]
#         -> SetRect(&r1,   0, 16, 640, 464)      the 640x480 space
#     push 0xE8  / 0x140 / 8    / 0 / &r2 ; call [0x7B632C]
#         -> SetRect(&r2,   0,  8, 320, 232)      the 320x240 space
#     ... ; call 0x642629                          the blit, r2 -> r1
#
# 464-16 = 448 and 232-8 = 224. The same window, twice, and the ONLY site in
# the whole executable that pushes 16 next to 640 -- checked by scanning every
# `push 640` in `ff7_en_switch`.
#
# This is where FFNx's `y == 16 && height == 448` really comes from. FFNx
# never had to patch it because FFNx replaces the field background path
# outright; this port runs FF7's original, so the window is still here.
#
# Opening both rects to the full frame:
#
#     +0x9C7454   mov w8, #0x1d0  (464)  ->  movz w8, #0x1e0  (480)
#     +0x9C7484   orr w8, wzr,#0x10 (16) ->  movz w8, #0        (0)
#     +0x9C74E8   mov w8, #0xe8   (232)  ->  movz w8, #0xf0   (240)
#     +0x9C7514   orr w8, wzr,#8    (8)  ->  movz w8, #0        (0)
#
# Both stay inside their surfaces (480 of 480, 240 of 240), and the port
# renders 320x240 while showing 320x224 -- so rows 0..8 and 232..240 hold real
# art, which is what the tile window was widened for.
#
# v5's `field_set_mode` push STAYS. With the window open, a driver rect of
# h=448 would clip the band to [0,672] and leave a 48 px bar at the bottom;
# 480 is needed at both layers. The driver CAVE is removed -- with the game
# asking for 480 it can never fire.
SETRECT_PATCHES = {
    #  va          stock       ->  set                what it is
    0x9C7454: (0x52803A08, A.movz(8, 480), 'r1 bottom  464 -> 480'),
    0x9C7484: (0x321C03E8, A.movz(8, 0),   'r1 top      16 -> 0'),
    0x9C74E8: (0x52801D08, A.movz(8, 240), 'r2 bottom  232 -> 240'),
    0x9C7514: (0x321D03E8, A.movz(8, 0),   'r2 top       8 -> 0'),
}

# Every neighbouring word, READ OUT OF THE IMAGE. `mov w8, #0x10` is not
# unique; this block is. The two `bl +0xA1A0` are the SetRect thunk and the
# `bl +0x9C2FC0` is the blit -- if any of those moved, this is not the site.
SETRECT_SIG = {
    0x9C7458: 0xB9000008,   # str w8, [x0]
    0x9C746C: 0x52805008,   # mov w8, #0x280      640, stays
    0x9C7488: 0xB9000008,   # str w8, [x0]
    0x9C749C: 0xB900001F,   # str wzr, [x0]       r1 left = 0
    0x9C74EC: 0xB9000008,   # str w8, [x0]
    0x9C7518: 0xB9000008,   # str w8, [x0]
    0x9C752C: 0xB900001F,   # str wzr, [x0]       r2 left = 0
}


# --------------------------------------------------------------------- model
def cave_rewrite(y, h):
    """What the cave makes of a rect. The whole of its logic, in one line."""
    return (FULL_Y, FULL_H) if (y, h) == (CROP_Y, CROP_H) else (y, h)


# Every rect the game passes, from `verify_framing.CASES` -- all 44 call sites
# of the engine setviewport helper. The three field rows are now the ones read
# out of `field_set_mode` itself (see RECT_PROVENANCE), not FFNx's constants.
CASES = [
    ('THE FIELD RECT   mode 2', 0,   0, 640, 448, True),
    ('field mode 0',            0,   0, 320, 224, False),
    ('field mode 1',          160, 120, 320, 224, False),
    ('menu / fullscreen',       0,   0, 640, 480, False),
    ('battle-ish',              0,   0, 320, 240, False),
    ("FFNx's rect (not ours)",  0,  16, 640, 448, False),
]

TARGETS = [
    (1280, 720, 'the shipping build -- field_buffer 3'),
    (1440, 1080, 'the main render target'),
    (854, 480, 'field_buffer 2'),
    (428, 240, 'field_buffer 1'),
]


def verify(log=print):
    """
    Execute the REAL words of `gfx_drv_setviewport` for every rect the game
    passes, before and after the cave's rewrite, and report the device rect.

    The check that would have caught v3 is the last one: the rect the game
    actually passes MUST be the row that changes.
    """
    ok = True
    try:
        import ws_emu
    except Exception as exc:                                    # noqa: BLE001
        log('  ! ws_emu unavailable (%s) -- the rect was NOT executed'
            % type(exc).__name__)
        return False

    log('  gfx_drv_setviewport, executed, field mode:')
    log('    %-26s %-22s %s' % ('rect', 'device rect y', 'black rows'))
    for tW, tH, what in TARGETS:
        log('    -- target %dx%d   %s' % (tW, tH, what))
        for label, x, y, w, h, is_crop in CASES:
            ny, nh = cave_rewrite(y, h)
            a = ws_emu.run(x=x, y=y, w=w, h=h, scale_x=tW, scale_y=tH,
                           game_w=640, game_h=480, mode=2)
            b = ws_emu.run(x=x, y=ny, w=w, h=nh, scale_x=tW, scale_y=tH,
                           game_w=640, game_h=480, mode=2)
            moved = a != b
            log('    %-26s %6d..%-6d -> %6d..%-6d  %3d/%-3d -> %3d/%-3d  %s'
                % (label, a['y1'], a['y2'], b['y1'], b['y2'],
                   a['y1'], tH - a['y2'], b['y1'], tH - b['y2'],
                   'CHANGED' if moved else 'untouched'))
            if moved != is_crop:
                log('    ! %s: moved=%s, expected %s' % (label, moved, is_crop))
                ok = False
        if tH == 720:
            r = ws_emu.run(x=0, y=CROP_Y, w=640, h=CROP_H, scale_x=tW,
                           scale_y=tH, game_w=640, game_h=480, mode=2)
            if (r['y1'], tH - r['y2']) != (0, 48):
                log('    ! the shipped rect no longer predicts 0 top / 48 '
                    'bottom at 720p -- the model changed, re-read FINDINGS-85')
                ok = False
            f = ws_emu.run(x=0, y=FULL_Y, w=640, h=FULL_H, scale_x=tW,
                           scale_y=tH, game_w=640, game_h=480, mode=2)
            if (f['y1'], tH - f['y2']) != (0, 0):
                log('    ! the patched rect does not fill the frame')
                ok = False
    log('  the field rect is the ONLY row the cave rewrites; every other row '
        'above is byte-identical before and after.')
    log("  NOTE the last row: FFNx's (0,16,640,448) is deliberately NOT "
        'rewritten -- nothing on this port ever passes it. That row existing '
        'and saying `untouched` is what v3 got wrong.')
    return ok


def check_encoding(log=print):
    try:
        from capstone import CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN, Cs
    except ImportError:
        log('  (capstone not installed -- encodings NOT checked)')
        return True
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    base = 0x1000
    words = cave_words(lambda i: base + 4 * i, base + 0x100)
    blob = b''.join(struct.pack('<I', x) for x in words)
    got = [(i.mnemonic + ' ' + i.op_str).strip() for i in md.disasm(blob, base)]
    ok = len(got) == len(words)
    if not ok:
        log('  ! capstone decoded %d of %d words' % (len(got), len(words)))
    for k, (g, want) in enumerate(zip(got, DISASM)):
        loose = '#skip' in want or '#return' in want
        if (g.split()[0] if loose else g) != (want.split()[0] if loose
                                              else want):
            log('  ! word %2d encodes `%s`, meant `%s`' % (k, g, want))
            ok = False
    # the two tests must be independent: neither branch may skip the other
    probe_w = cave_words(lambda i: base + 4 * i, base + 0x100, set_h=PROBE_H)
    if probe_w[I_SET_H] == words[I_SET_H]:
        log('  ! --probe and the fix encode the same height')
        ok = False
    if words[I_SET_Y] != A.mov_reg(1, 31):
        log('  ! word %d is not `mov w1, wzr`' % I_SET_Y)
        ok = False
    return ok


# ------------------------------------------------------------------- the image
def verify_anchors(text, log=print):
    ok = True
    for va, want in sorted(ANCHORS.items()):
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            log('  ! +%#x is %08X, expected %08X' % (va, got, want))
            ok = False
    return ok


def nzcv_is_dead(text, log=print):
    """
    The cave leaves NZCV set by its own `cmp`. Nothing between the return and
    the function's own `cmp w9,#3` may read the flags.
    """
    try:
        from capstone import CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN, Cs
    except ImportError:
        return True
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    bad = ('b.', 'csel', 'cset', 'csinc', 'cinc', 'ccmp', 'adc', 'sbc')
    for i in md.disasm(text[RETURN_VA:0x10D6860], RETURN_VA):
        if i.mnemonic.startswith(bad):
            log('  ! +%#x %s %s reads NZCV before it is reset'
                % (i.address, i.mnemonic, i.op_str))
            return False
    return True


def clamp_is_present(main_path, log=print):
    """
    Is `ff7nx_wsclamp`'s BOTTOM extent actually in this module?

    ASK THE MODULE, NOT `defaults()`. `wsclamp` is reached only from
    `ff7nx_ws.apply_module`, which `build.py` never calls, so `defaults()`
    reports what it WOULD ship rather than what is there.
    """
    try:
        import ff7nx_wsclamp as C
        import nxmap
    except ImportError:
        return None
    try:
        img = nxmap.Main(main_path).img
        st = C.check_all(img)
    except Exception as exc:                                    # noqa: BLE001
        log('  ? field uncrop: cannot read the tile-window state (%s) -- the '
            'LOWER bar may have no tiles to reveal' % type(exc).__name__)
        return None
    stock = [k for k in ('bottom1', 'bottom2') if st.get(k) != 'patched']
    if stock:
        log('  ! field uncrop: tile-window %s still stock, so the field emits '
            'NO background tile between dst-y 448 and 480. The BOTTOM bar '
            'cannot fill no matter what the viewport says. '
            'ff7nx_uncrop.apply_bottom_extent() fixes it.' % ', '.join(stock))
        return False
    try:
        vals = (C.read_value(img, 'bottom1'), C.read_value(img, 'top1'))
        log('  field tile window: top=%s bottom=%s -> admits dst-y %d..%d, '
            'against an uncropped view of 0..480' % (vals[1], vals[0],
                                                     (224 - vals[1]) * 2,
                                                     (224 + vals[0]) * 2))
    except Exception:                                           # noqa: BLE001
        log('  field tile window: bottom extent already patched')
    return True


def _hex(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def _branch_target(word, va):
    """VA a `b #imm26` at `va` jumps to, or None."""
    if (word & 0xFC000000) != 0x14000000:
        return None
    imm = word & 0x03FFFFFF
    if imm & 0x02000000:
        imm -= 0x04000000
    return va + imm * 4


def _legacy_words(addrs, crop_y, set_h):
    """
    The v3/v4/probe cave shape, kept ONLY so those can be recognised and
    removed. It tested `y == crop_y && h == 448` as a single conjunction,
    which is why it could not cover the (16, 448) case v6 exists for.
    """
    skip = addrs[6]
    return [A.cmp_imm(1, crop_y),
            A.bcond(addrs[1], skip, NE),
            A.cmp_imm(3, CROP_H),
            A.bcond(addrs[3], skip, NE),
            A.mov_reg(1, 31),
            A.movz(3, set_h),
            HOOK_ORIG,
            A.b(addrs[7], RETURN_VA)]


def _cave_variants(addrs, legacy=False):
    """Cave layouts this module has written, laid out at these addresses."""
    out = {}
    try:
        out['v6'] = cave_words(lambda i: addrs[i])
        out['probe'] = cave_words(lambda i: addrs[i], set_h=PROBE_H)
        if legacy:
            for nm, cy, sh in (('legacy-v3', 16, FULL_H),
                               ('legacy-v4', 0, FULL_H),
                               ('legacy-probe', 0, PROBE_H)):
                out[nm] = _legacy_words(addrs, cy, sh)
    except Exception:                                           # noqa: BLE001
        pass
    return out


def window_is_open(text):
    """Are both SetRect rects opened to the full frame?"""
    return all(struct.unpack_from('<I', text, va)[0] == new
               for va, (_old, new, _w) in SETRECT_PATCHES.items())


def state(text):
    """'stock' | 'v5' | 'v7' | 'v6' | 'probe' | 'unknown-cave'."""
    w = struct.unpack_from('<I', text, HOOK_VA)[0]
    src = struct.unpack_from('<I', text, FIELD_H_VA)[0] == FIELD_H_SET
    if w == HOOK_ORIG:
        if window_is_open(text):
            return 'v7' if src else 'unknown-cave'
        return 'v5' if src else 'stock'
    walked = _walk_cave(text)
    if walked is None:
        return 'unknown-cave'
    addrs, got = walked
    for name, want in _cave_variants(addrs, legacy=True).items():
        if got == want:
            return name
    return 'unknown-cave'


def _walk_cave(text):
    """
    ([address of logical word i], [word i]) for the cave the hook branches to.

    `ff7nx_cave.emit_laid_out` SCATTERS the cave across whatever padding holes
    it found and stitches the pieces together with unconditional branches, so
    the eight words are NOT contiguous -- v3's are at +0xE5BD4, +0xE6184, ...
    Assuming contiguity here reads the next function's prologue as cave words.

    Chain links are unambiguous: the only unconditional `b` the cave itself
    emits is its LAST word, so any `b` seen before logical index N_WORDS-1 is
    a link and is followed without consuming an index.
    """
    hook = struct.unpack_from('<I', text, HOOK_VA)[0]
    entry = _branch_target(hook, HOOK_VA)
    if entry is None:
        return None
    addrs, words = [], []
    va = entry
    for _ in range(N_WORDS * 64):           # bounded: a loop is a corruption
        if len(words) == N_WORDS:
            break
        if va + 4 > len(text):
            return None
        w = struct.unpack_from('<I', text, va)[0]
        link = _branch_target(w, va)
        # A chain link is an unconditional `b` that is NOT the cave's own tail
        # branch. Keying it on the index instead was wrong: `link()` can put a
        # hop immediately before the last word, and the walker then ate the
        # hop as if it were the return.
        if link is not None and link != RETURN_VA:
            va = link
            continue
        addrs.append(va)
        words.append(w)
        va += 4
    if len(words) != N_WORDS:
        return None
    return addrs, words


def _existing_cave(text, log=print):
    """
    (entry, words, addrs) of the cave the hook branches to, verified word for
    word against a layout this module would have produced AT THOSE ADDRESSES.
    Refuses anything else -- editing a cave you have not identified is how a
    wrong patch becomes a crash.
    """
    walked = _walk_cave(text)
    if walked is None:
        log('  ! +%#x branches somewhere this module cannot walk' % HOOK_VA)
        return None
    addrs, got = walked
    for want in _cave_variants(addrs, legacy=True).values():
        if got == want:
            return addrs[0], got, addrs
    log('  ! +%#x branches to +%#x and the cave there is not one this module '
        'wrote:' % (HOOK_VA, addrs[0]))
    ref = _cave_variants(addrs).get('v6', got)
    for i, g in enumerate(got):
        if i < len(ref) and g != ref[i]:
            log('      word %d at +%#x is %08X, v6 would be %08X'
                % (i, addrs[i], g, ref[i]))
    return None


def show(path, log=print):
    import nxmap
    m = nxmap.Main(path)
    st = state(m.text)
    log('  hook +%#x is %s' % (HOOK_VA, st))
    if st == 'v3':
        log('  ! v3 is installed. Its compare is `cmp w1, #16` and the field '
            'passes y = 0, so IT HAS NEVER FIRED. --apply repairs it in '
            'place (one word).')
    verify_anchors(m.text, log)
    nzcv_is_dead(m.text, log)
    for va, set_word, what in ((VP22_VA, VP22_SET, 'attempt 1 (_22 := 1.0)'),
                               (HALF_H_VA, HALF_H_SET, 'attempt 2 (0xCFF200)')):
        cur = struct.unpack_from('<I', m.img, va)[0]
        log('  +%#x %s: %s' % (va, what,
                               'PRESENT, will be reverted' if cur == set_word
                               else 'not present'))
    log('  the field rect, read out of this module:')
    for va, what in sorted(RECT_PROVENANCE.items()):
        log('    +%#-10x %s' % (va, what))
    clamp_is_present(path, log)
    verify(log)


def cave_patches(img, starts, log=lambda *_a: None, set_h=None):
    import ff7nx_cave
    entry, out = ff7nx_cave.emit_laid_out(
        ff7nx_cave.HolePool(img, starts=starts),
        lambda _e, addr: cave_words(addr, set_h=set_h), span=0x80000)
    out[HOOK_VA] = A.b(HOOK_VA, entry)
    log('  field uncrop cave: %d words in padding, entry +%#x'
        % (N_WORDS, entry))
    return out


def apply_to_nso(src, dest, log=lambda *_a: None, probe=False):
    try:
        import nso_patcher
        import nxmap
    except ImportError as exc:                                  # noqa: BLE001
        log('! field uncrop: cannot import %s' % exc)
        return False
    try:
        m = nxmap.Main(src)
        st = state(m.text)
        want = 'probe' if probe else 'v7'
        set_h = PROBE_H if probe else FULL_H
        if st == want:
            log('  field uncrop: already installed (%s)' % want)
            return True
        if st == 'unknown':
            log('! field uncrop: +%#x holds %08X -- neither stock nor a cave '
                'this module wrote; refusing to patch'
                % (HOOK_VA, struct.unpack_from('<I', m.text, HOOK_VA)[0]))
            return False
        if not verify_anchors(m.text, log):
            log('! field uncrop: this module is not the one the offsets were '
                'derived from; refusing to patch')
            return False
        if not nzcv_is_dead(m.text, log):
            log('! field uncrop: NZCV is live past the hook; refusing')
            return False
        clamp_is_present(src, log)

        # v5: the game's own copy. Not touched in --probe, which needs the
        # stock 448 to test against.
        field_h = struct.unpack_from('<I', m.text, FIELD_H_VA)[0]
        if not probe:
            bad = {va: struct.unpack_from('<I', m.text, va)[0]
                   for va, want in FIELD_H_SIG.items()
                   if struct.unpack_from('<I', m.text, va)[0] != want
                   and not (va == FIELD_H_VA and
                            struct.unpack_from('<I', m.text, va)[0]
                            == FIELD_H_SET)}
            if bad:
                log('! field uncrop: field_set_mode does not match its '
                    'signature (%s); refusing'
                    % ', '.join('+%#x=%08X' % kv for kv in bad.items()))
                return False

        words = {}
        cave = (None if st in ('stock', 'v5')
                else _existing_cave(m.text, log))
        if st.startswith(('v6', 'probe', 'legacy')) and cave is None:
            log('! field uncrop: a cave is hooked but it does not verify; '
                'refusing. Rebuild from the clean base.')
            return False

        if probe:
            # --probe needs the cave AND the stock 448 to test against.
            if field_h == FIELD_H_SET:
                words[FIELD_H_VA] = FIELD_H_STOCK
                log('  --probe: restoring field_set_mode to 448 so the probe '
                    'has something to test against')
            if cave is not None and st.startswith('legacy'):
                addrs = cave[2]
                words[HOOK_VA] = HOOK_ORIG
                for a in addrs:
                    words[a] = 0
                log('  removing the %s cave at +%#x' % (st, addrs[0]))
                cave = None
            if cave is None:
                words.update(cave_patches(m.img, set(m.arm_starts), log,
                                          set_h=PROBE_H))
            else:
                addrs = cave[2]
                words[addrs[I_SET_H]] = A.movz(3, PROBE_H)
                log('  field uncrop: retuning the cave at +%#x -> '
                    '`mov w3, #%d`' % (addrs[I_SET_H], PROBE_H))
        else:
            # v5 -- the game's own copy, and NO cave. See the v5 block above.
            if field_h != FIELD_H_SET:
                words[FIELD_H_VA] = FIELD_H_SET
                log("  field_set_mode: mode-2 viewport height 448 -> 480 at "
                    "+%#x. This is the GAME's copy -- the one the driver "
                    "cave could not reach." % FIELD_H_VA)
            bad = {va: struct.unpack_from('<I', m.text, va)[0]
                   for va, want in SETRECT_SIG.items()
                   if struct.unpack_from('<I', m.text, va)[0] != want}
            for va, (old_w, new_w, _what) in SETRECT_PATCHES.items():
                cur = struct.unpack_from('<I', m.text, va)[0]
                if cur not in (old_w, new_w):
                    bad[va] = cur
            if bad:
                log('! field uncrop: the field blit rects do not match their '
                    'signature (%s); refusing'
                    % ', '.join('+%#x=%08X' % kv for kv in sorted(bad.items())))
                return False
            for va, (_old, new_w, what) in sorted(SETRECT_PATCHES.items()):
                if struct.unpack_from('<I', m.text, va)[0] != new_w:
                    words[va] = new_w
                    log('  field blit window: %s   at +%#x' % (what, va))
            if cave is not None:
                addrs = cave[2]
                words[HOOK_VA] = HOOK_ORIG
                for a in addrs:
                    words[a] = 0
                log('  removing the driver cave at +%#x (%d word(s) returned '
                    'to padding) -- the window is the lever, not the rect'
                    % (addrs[0], len(addrs)))

        # Both retired attempts are backed out if present. A pass that was
        # shown not to move a pixel is a defect, not a neutral: leaving it in
        # makes the NEXT null result unattributable. HANDOFF-80 5.3.
        for va, set_word, stock_word, what in (
                (VP22_VA, VP22_SET, VP22_STOCK, 'attempt 1, _22 := 1.0'),
                (HALF_H_VA, HALF_H_SET, HALF_H_STOCK,
                 'attempt 2, 0xCFF200 (never read by anything)')):
            if struct.unpack_from('<I', m.img, va)[0] == set_word:
                words[va] = stock_word
                log('  reverting %s -- tested on hardware, moved nothing'
                    % what)

        # Report the height the CAVE ACTUALLY CARRIES, read back out of the
        # words about to be written -- never a sentence with the number typed
        # into it. The first --probe run printed "-> (0,0,640,480)" while
        # writing 240, because this line was a constant and the PROBE warning
        # only existed on the in-place path. The module was right and the log
        # was wrong, which is worse than the reverse.
        log('  field viewport (%d,%d,640,%d) -> (%d,%d,640,%d)   [%s]'
            % (0, CROP_Y, CROP_H, 0, FULL_Y, set_h,
               'driver cave, DIAGNOSTIC' if probe else
               'field_set_mode, at source'))
        if probe:
            log('  *** PROBE, NOT A FIX. h = %d is a DIAGNOSTIC: the field '
                'asks for a %s-height viewport. Visible collapse = the rect '
                'is honoured; no change at all = gfx_drv_setviewport is dead '
                'for the field. Revert with --apply.'
                % (set_h, 'half' if set_h * 2 == FULL_H else 'wrong'))
        else:
            log('  the field is drawn into a 480-unit ortho but the rect only '
                'ever admitted 448 of them.')
        # The words are about to be written; check what they SAY. This is
        # the gate the first --probe run did not have.
        if probe:
            want_h = A.movz(3, set_h)
            if want_h not in words.values():
                log('! field uncrop: the cave being written does not carry '
                    '`mov w3, #%d`; refusing' % set_h)
                return False
        elif struct.unpack_from('<I', m.text, FIELD_H_VA)[0] != FIELD_H_SET \
                and words.get(FIELD_H_VA) != FIELD_H_SET:
            log('! field uncrop: field_set_mode is not being set to 480; '
                'refusing')
            return False
        if not words:
            log('  field uncrop: nothing to do')
            return True

        nso = nso_patcher.read_nso(Path(src))
        applied = nso_patcher.apply_spec(nso, {
            'name': 'field vertical uncrop (viewport h %d -> %d)%s'
                    % (CROP_H, set_h, '  PROBE' if probe else ''),
            'patches': [
                {'name': ('hook' if va == HOOK_VA else
                          'revert retired patch' if va in (VP22_VA, HALF_H_VA)
                          else 'cave'),
                 'va': '0x%X' % va,
                 # LITTLE-ENDIAN BYTES, not the word. `nso_patcher` compares
                 # the stored bytes; '%08X' of the word is byte-reversed and
                 # fails verification on every patch.
                 'expect': _hex(struct.unpack_from('<I', m.img, va)[0]),
                 'set': _hex(word)}
                for va, word in sorted(words.items())],
        })
        Path(dest).write_bytes(nso_patcher.rebuild(nso))
        # Read the file back and say what is IN it, not what was intended.
        got = state(nxmap.Main(dest).text)
        if got != want:
            log('! field uncrop: wrote %s but the module reads back as %s'
                % (want, got))
            return False
    except Exception as exc:                                    # noqa: BLE001
        log('! field uncrop: %s: %s' % (type(exc).__name__, exc))
        return False
    log('  %d word(s) verified and applied -- module reads back as %s'
        % (len(applied), want))
    return True


UNCROP_ENV = 'SEVENTH_NX_FIELD_UNCROP'      # diagnosis only; the GUI owns this


def enabled(env=None):
    env = os.environ if env is None else env
    raw = env.get(UNCROP_ENV)
    return False if raw is None else raw.strip().lower() not in (
        '0', 'off', 'false', 'no')


# The ONE `ff7nx_wsclamp` knob this pass needs, and not one more.
#
# Three of the four must NOT move here: the horizontal widescreen in this
# build does not come from the tile window at all -- it is `ff7nx_fieldbuf`
# plus `WS_SCALE` in the shader -- and it works. Widening left/right now would
# change something already correct, on no evidence, in the build we are using
# to test one specific thing.
CLAMP_VALUES = {'bottom': 16}


def apply_bottom_extent(src, dest, log=lambda *_a: None):
    """Give the LOWER bar something to reveal. Usually already applied."""
    try:
        import ff7nx_wsclamp as C
        import nso_patcher
        import nxmap
    except ImportError as exc:                                  # noqa: BLE001
        log('! field uncrop: cannot import %s' % exc)
        return False
    try:
        m = nxmap.Main(src)
        C.check_all(m.img)
        nso = nso_patcher.read_nso(Path(src))
        applied = nso_patcher.apply_spec(
            nso, C.spec(m.img, CLAMP_VALUES, starts=set(m.arm_starts),
                        log=log))
        Path(dest).write_bytes(nso_patcher.rebuild(nso))
    except Exception as exc:                                    # noqa: BLE001
        log('! field uncrop: bottom extent: %s: %s'
            % (type(exc).__name__, exc))
        return False
    log('  field tile window: bottom extent -> 16 (%d word(s) applied)'
        % len(applied))
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0])
    ap.add_argument('module', nargs='?', help='path to exefs/main')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--probe', action='store_true',
                    help='ask for h=240 instead of 480 -- a DIAGNOSTIC, not '
                         'a fix. See the --probe block in the header.')
    ap.add_argument('-o', '--out')
    a = ap.parse_args(argv)
    if a.verify or not a.module:
        ok = check_encoding(print) and verify(print)
        print('\n  %s' % ('all checks pass' if ok else 'FAILURES ABOVE'))
        return 0 if ok else 1
    if a.show:
        show(a.module)
        return 0
    if a.apply or a.probe:
        return 0 if apply_to_nso(a.module, a.out or a.module, print,
                                 probe=a.probe) else 1
    show(a.module)
    return 0


if __name__ == '__main__':
    sys.exit(main())
