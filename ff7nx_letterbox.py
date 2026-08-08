#!/usr/bin/env python3
r"""
ff7nx_letterbox.py -- the horizontal black bars are PAINTED, not clipped.

THE FINDING
===========
Nothing clips the field.  The driver draws two opaque black quads over the
finished frame, every frame, in field mode only.

`set_driver_mode` (+0x10F3D00) takes the mode in w0, immediately calls
+0x10FB0A0 (the screen height -- the same call `gfx_drv_init` uses), and
dispatches through the jump table at .rodata 0x11B3C74 on `mode - 1`.
Fifteen branches.  Fourteen of them store **zero** into the global at
0x12E60CC.  One does not:

    mode 2 (FIELD)                              +0x10F3DB4
      +0x10F3DB4  scvtf s0, w0                  ; s0 = (float)screen_height
      +0x10F3DBC  ldr   s1, [x8, #0x720]        ; .rodata 0x11AE720 = 0.0333333f
      +0x10F3DC8  fmul  s0, s0, s1              ; = screen_height * 16/480
      +0x10F3DDC  str   s0, [x10, #0xcc]        ; [0x12E60CC] = that      <-- THE BUG

0.0333333f is 16/480.  FF7's field viewport is (0, 0, 640, 448) in a 640x480
game space; 480-448 = 32, half of it is 16.  The port did not implement the
letterbox as a clip -- it hardcoded the *cosmetic result* as a fraction of
the screen and paints it.

    720p handheld   720 * 16/480  =  24.0 px
    1080p docked   1080 * 16/480  =  36.0 px

Measured on two captures from two different fields, 2026-08-07: content rows
24..695 in both.  Top bar 24, bottom bar 24, bit-exact, full width.

WHERE THE QUADS ARE DRAWN
=========================
`set_driver_mode`'s tail reads it back and hands it to the setter:

    +0x10F3E58  ldr s0, [x8, #0xcc]         ; x8 -> 0x12E6000
    +0x10F3E5C  bl  #0x10DF6C0              ; if (s0 >= 0.0) [[0x12CE460]] = s0

`gfx_drv_flip` (+0x10DA880) then calls +0x10E0680, which builds them:

    +0x10E07C0  ldr   s1, [x23]             ; [[0x12CE568]] = screen height H
    +0x10E07C8  ldr   s0, [x8]              ; [[0x12CE3D8]] = K, initialised 1.0f
    +0x10E07DC  ldr   s2, [x8]              ; [[0x12CE460]] = F, the letterbox px
    +0x10E07E0  fmsub s3, s0, s8, s8        ; (1 - K) / 2
    +0x10E07E8  fmadd s0, s3, s1, s2        ; top    = ((1-K)/2)*H + F
    +0x10E07F0  fdiv  s2, s0, s1            ; top / H
    +0x10E0834  fsub  s0, s1, s0
    +0x10E083C  fdiv  s9, s0, s1            ; (H - top) / H

`w24 = #-0x1000000` is 0xFF000000 -- opaque black.  Two full-width quads,
y in [0, s2] and y in [s9, 1].  K is written once, at +0x10D4EDC, as
`mov w10, #0x3f800000` = 1.0f, and never again, so the whole expression is
just F.  With F = 0 both quads are degenerate and nothing is drawn -- which
is exactly what happens in every other mode.

WHY THIS SURVIVED EIGHT ATTEMPTS
================================
Every previous patch moved something the bars do not depend on:

    the field viewport rect (0,0,640,448)   ..  verbatim, 4 readers, no offset
    gfx_drv_setviewport's device rect        ..  y1 = SY*y/480, top-aligned
    the d3dviewport matrix _22/_42           ..  3D only; 2D uses a fixed ortho
    ORIGIN_Y / [0xCFF200] 224 -> 232/240     ..  moves ART, not the quads
    field_set_mode h 448 -> 480              ..  ditto
    the per-draw scissor                     ..  (0,0,fw,fh), cannot clip
    the presentation blit                    ..  vertically full, K = 1.0
    FFNx's `y == 16 && height == 448`        ..  cannot fire; FF7 passes y = 0

The bars are drawn LAST, over the presented frame, after all of it.  That is
also why the symptom reads the way the hardware reports it: characters do not
draw *over* the bars the way they did over the old widescreen side margins --
they disappear underneath them.  And it is why battle and the settings menu
reach the top of the screen: those are modes 3 and 4, and both store zero.

THE PATCH
=========
One word.  Make mode 2 do what modes 3, 4, 5, 9..15 already do:

    +0x10F3DDC   BD 00 CD 40   str s0,  [x10, #0xcc]
              -> B9 00 CD 5F   str wzr, [x10, #0xcc]

The replacement encoding is not invented.  It is copied verbatim from
+0x10F3E18 inside the same function, which is `str wzr, [x10, #0xcc]` with
the same base register.  Byte-exactly reversible, no cave, idempotent.

[0x12E60CC] has exactly eight referencing sites in the whole module, all
inside `set_driver_mode`: seven writers and one reader.  There is no other
consumer, so this cannot move anything else.

CONFIRMED ON HARDWARE 2026-08-07, AND WHAT IT EXPOSED
=====================================================
The one word shipped and the bars went.  The capture measured **content rows
0..672, top bar 0, bottom bar 48** -- against 24..695 before it.

That is not a partial result, it is the whole diagnosis finishing itself.  `F`
did two things, not one: as well as painting the quads, `gfx_drv_flip` passes
`(0, F, A, F+B)` as the present viewport at +0x10DAEE8, so the picture was
*also* shifted down by F.  With F = 0 the frame stops moving and sits where it
always belonged, and what is left underneath is the real window:

    672 / 720  ==  448 / 480     top-aligned, exact

which is `gfx_drv_setviewport`'s device rect and nothing else:

    +0x10D67A4  mul w13, w12, w1   ; SY * y
    +0x10D67A8  mul w15, w12, w3   ; SY * h
    +0x10D67B0/B4  umull ... 0x88888889 ; / 480
    +0x10D67C8  add w15, w15, w13  ; y2 = SY*y/480 + SY*h/480
    -> field (0,0,640,448), SY = 720  =>  rows 0..672

MEASURED, NOT ASSUMED
=====================
The bars-off capture is the first clean frame this project has ever had --
no painted quads, no present shift.  Fitted against `mrkt2` rendered out of
the built flevel, edge-correlated over field identity and vertical scale:

    mrkt2   r = 0.295      next best (md8_1) 0.097      3x margin
    scale   3.00 px/tile-unit, sharp unimodal peak
            2.90 -> 0.156   3.00 -> 0.269   3.10 -> 0.159

3.00 is square pixels, matching the horizontal 1.5 px/game-unit the build log
reports.  **So the window is a genuine CLIP, not a squash** -- 48 rows of real
art are being thrown away.  mrkt2's layer 1 spans tile y -360..+360 (720 units,
three screens), so there is picture past both edges with room to spare.

THE SECOND WORD
===============
    +0x9298BC   E8 0B 1A 32   orr w8, wzr, #0x1c0     (448)
             -> 08 3C 80 52   mov w8, #0x1e0          (480)

This is the `push 448` of `field_init_viewport_values`, inside its mode-2
branch, so it is **field-only**.  `[0xCFF1EC]` has exactly five referencing
sites in the whole x86 image: one writer (`sub_60D810` @ 0x60D816) and four
readers (0x60E1A6 / 0x60E400 / 0x63A9B7 / 0x63AD21) which all push it straight
to `engine_gfx_setviewport`.  Nothing else consumes it.

Why this is safe, which is the part that needed proving.  `gfx_drv_setviewport`
also builds FFNx's `d3dviewport_matrix` from the same four arguments --
`_11 = w/gw` at +0xA8, `_22 = h/gh` at +0xBC, `_41`/`_42` at +0xD8/+0xDC,
including the `if (mode == 3) _22 = 1.0f` battle exemption.  Changing h moves
`_22` from 0.9333 to 1.0 and `_42` from +16/240 to 0, which is exactly the
"characters no longer standing on the scenery" hazard.

It cannot happen here.  Those four fields are **write-only**.  A scan of every
site in the module that reaches the driver state object through `[0x12CE668]`
finds one function touching +0xA8/+0xBC/+0xD8/+0xDC, and it is
`gfx_drv_setviewport` writing them.  There are no readers.  The matrix is a
faithful transcription of FFNx's whose consumer was never wired up -- which is
also why attempt 1 forced `_22 := 1.0` on hardware and nothing moved.

And the tile loop is already prepared for the extra 16 units.  `ff7nx_wsclamp`
has the bottom cull at bias 16 and the build log states the vertical extents as
`O=224 L=256 R=16 -> span -64..480 covers`.  HANDOFF-85 measured that bias as
"exactly right" and could not say what it was for.  This is what it was for.

    letterbox word only :  rows 0..672, 48 px dark at the bottom   (hardware)
    both words          :  rows 0..720, no bars                    (predicted)

WHAT TO LOOK FOR
================
    no bars, art to both edges              -> done
    a bar on a few maps only                -> those fields have under 240 tile
                                               units of art. 22 do (junon,
                                               lastmap). Not a bug; mrkt2 and
                                               md8_1 are the fields to judge on.
    characters float off the scenery        -> the matrix is live after all and
                                               this analysis is wrong. Run
                                               --revert-frame; the letterbox
                                               word stands on its own.
    still exactly 48 px at the bottom       -> the second word did not land.
                                               Read the log, do not re-derive.

THE THIRD STAGE, STILL OFF
==========================
`--field-center` sets the four `field_layerN_pick_tiles` origins 224 -> 232
(+0xA05AA4 / +0xA06EA8 / +0xA07878 / +0xA08728): FFNx's
`initial_pos.y = ((ff7_field_center ? 232 : 224) - bg_position.y)`, +8 tile
units = +24 device px.

With the frame open to 480 it should not be needed -- it would scroll the
picture down 24 px rather than reveal anything.  It stays available because it
is the right lever if a field turns out to be biased upward, and off because
adding it now would make the frame result unattributable.

THE THIRD LEG -- v4 PUT IT IN THE WRONG GLOBAL.  v5 CORRECTS IT.
================================================================
`ff7_field_center` moves THREE things by +16 game units.

    background tiles   origin 224 -> 232, x field_bg_multiplier(2)  = +16
    2D sprites         [0xCFF200] 224 -> 240                        = +16
    3D models          set_field_viewport y 0 -> 16                 = +16

v4 shipped the third one as `[0xCFF208] = 16` at +0x9299D0.  That is not the
viewport y; it is the field's 2D point offset, and it lands on the SAME
background tiles the first leg already moved.  Result on hardware: scenery
+32, characters +16, i.e. worse than doing nothing to leg three at all.
That is the "even more off" Patrick measured.

Read FFNx's patch against the x86 rather than against its variable names
(ff7_opengl.cpp:312):

    patch_code_byte(field_init_viewport_values + 0x35, 16)
    patch_code_int (field_init_viewport_values + 0x6E, 240)

    x86 0x60D837 +0x2A  push 0x1c0     h = 448
                 +0x2F  push 0x280     w = 640
                 +0x34  push 0         y      <-- +0x35 IS THIS IMMEDIATE
                 +0x36  push 0         x
                 +0x38  call set_field_viewport
                 +0x68  mov [0xcff200], 0xe0  <-- +0x6E is this imm32

So leg three is the viewport `y`.  In this port that is +0x9298EC, and
+0x9299D0 ([0xCFF208]) is a different variable entirely:

    x86 0x6464BA  add_field_point(x, y, ...)   x += [0xCFF204]; y += [0xCFF208]
    callers 0x640B83 0x640C32 0x640E97 0x64129D 0x641337 0x6416A2 0x64173C
            -- every one inside field_layerN_pick_tiles

WHY THE VIEWPORT Y REACHES MODELS AND NOTHING ELSE
==================================================
`gfx_drv_setviewport` (+0x10D6760) is FFNx `common_setviewport` recompiled
one for one, including the matrix (common.cpp:1468):

    _11 = w/game_width          _22 = h/game_height
    _41 = ((x+w/2)-gw/2)/(gw/2) _42 = -((y+h/2)-gh/2)/(gh/2)

and it is applied only where FFNx applies it -- gl.cpp:353,
`if(vertextype != TLVERTEX) setD3DViweport(&d3dviewport_matrix)`.  The
background and the sprites are TLVERTEX through the hardcoded
ortho(0,640,480,0) at +0x10DA018, so they never see it.  Models do:

    +0x10DA098  add x0, x27, #0x28    world_view
    +0x10DA09C  add x1, x27, #0x68    d3dprojection
    +0x10DA0A4  bl  +0x10D9AA0
    +0x10DA0B0  add x1, x27, #0xa8    THE D3DVIEWPORT MATRIX
    +0x10DA0B8  bl  +0x10D9AA0        -> the final model transform

v4 asserted this matrix was write-only.  It scanned for `ldr` and the matrix
is used by POINTER, so an `add` was invisible to it -- the same shape of miss
as FINDINGS-84's "nothing reads 0xCFF200".  `_matrix_readers` now looks for
both, and refuses if it finds neither.

THE SCALE, NOT THE OFFSET -- WORD TWO IS BACK ON, AND WHY THAT IS NOT A FLIP-FLOP
=================================================================================
h is `_22`, the vertical scale of every 3D model.  The v5 text below this
paragraph said 480 was a 1.0714x STRETCH and turned it off.  That was correct
arithmetic against a 448-unit background and it is the wrong answer now,
because the uncrop made the background 480 units and nobody re-derived the
model side.

**`_22` is not "the value FF7 authored".  It is a ratio, and it has to match
whatever the background is doing.**

    background 448 units   ->  _22 must be 448/480 = 0.93333   h = 448
    background 480 units   ->  _22 must be 1.0                 h = 480

The background is 480: the 2D ortho at +0x10DA018 was always (0,640,480,0),
and the three uncrop caves force the clip rect to (0,480) so nothing trims it.
The build log has said so on its own two lines for several builds:

    background    rows 0..719
    3D models     rows 24..696      <- 24 px short at each end

Measured, as model row minus background row, for a model standing at game y:

    y=0  h=448   _22 0.93333  _42 +0.06667    +0 .. -48 px   stock (bg 448)
    y=16 h=448   _22 0.93333  _42  0         +24 ..  -24 px   v5   (bg 480)
    y=0  h=480   _22 1.0      _42  0           0 EVERYWHERE   <- correct
    y=16 h=480   _22 1.0      _42 -0.06667   +24 everywhere

The v5 pair is zero at mid screen and wrong by up to 24 px at the top and
bottom.  That is invisible on flat ground -- every flat-ground test this
project ever ran puts the character near the middle -- and it is exactly
"characters are very slightly off on ladders", because a ladder is the one
place a character travels the full height of the frame.

Note what y does at h=480: `_42 = -y/240` is a PURE offset there, so y=16
would put every model 24 px low at every height.  The +16 that
`ff7_field_center` wants is already carried twice, by the tile origins
(224 -> 232) and the sprite origin (224 -> 240), and both of those move the
PICTURE.  The models never needed a third shift; y=16 existed only to cancel
half of the `_22` error, and with `_22` correct it has nothing left to do.
So `plan()` pairs the viewport y with h, not with `field_center`.

THE MOVIE BAND, WHICH THIS USED TO PIGGYBACK ON
------------------------------------------------
Rows 24..696 is also where `ff7nx_moviealign` puts a 4:3 movie quad, and v5
counted that as a free win: a model could not be drawn above or below the
picture during an FMV because the model band happened to be the movie band.
Opening the models to 0..720 gives that up.

It is already covered, by the module that was written to cover it.
`ff7nx_moviebars` paints four opaque quads over the finished frame while a
movie plays, and its top and bottom are y 0..16/480 and 464/480..1 -- device
rows 0..24 and 696..720, exactly the rows the model band used to exclude.
Drawn last, over everything.  So the overlap is handled by the module that
owns it rather than as a side-effect of a scale error, which is the better
arrangement anyway: one job, one owner.

    python3 ff7nx_letterbox.py <main> --verify
    python3 ff7nx_letterbox.py <main> --show
    python3 ff7nx_letterbox.py <main> --apply        # bars off + field_center
    python3 ff7nx_letterbox.py <main> --apply --open-frame   # A/B only
    python3 ff7nx_letterbox.py <main> --revert       # back out everything

--apply always drives [0xCFF208] back to 0, so it repairs a module that
already has v4 in it without a rebuild.
"""
from __future__ import annotations

import argparse
import math
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '7th_heaven_nx')):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# --------------------------------------------------------------------------
# the addresses, every one read out of the module
# --------------------------------------------------------------------------
SET_DRIVER_MODE = 0x10F3D00     # set_driver_mode(int mode)
MODE_JUMP_TABLE = 0x11B3C74     # .rodata, ldrsw x8,[x9,x8,lsl#2] on (mode-1)
MODE2_BRANCH    = 0x10F3DB4     # table[1] -- FIELD
LETTERBOX_STORE = 0x10F3DDC     # str s0, [x10, #0xcc]        <- the one word
LETTERBOX_ZERO  = 0x10F3E18     # str wzr,[x10, #0xcc]        <- the donor word
FRACTION_CONST  = 0x11AE720     # .rodata float 16.0/480.0
LETTERBOX_GLOBAL = 0x12E60CC    # the letterbox height in pixels

SETTER          = 0x10DF6C0     # if (s0 >= 0.0) [[0x12CE460]] = s0
FLIP            = 0x10DA880     # gfx_drv_flip
BARS            = 0x10E0680     # draws the two black quads
SCREEN_HEIGHT   = 0x10FB0A0     # returns the screen height

WORD_STORE_S0   = 0xBD00CD40    # str s0,  [x10, #0xcc]
WORD_STORE_WZR  = 0xB900CD5F    # str wzr, [x10, #0xcc]

# Mode 5's branch (+0x10F3E24) is two instructions -- `mov w20, wzr; b tail`.
# It never writes the letterbox global, so it INHERITS whatever the previous
# mode left there. Every other mode writes: 13 write zero, mode 2 writes
# H*16/480. That makes mode 5 the one place a stale field letterbox can leak
# into another screen, and it is one more reason to zero it at the source
# rather than to try to clear it later.
MODE_INHERITS = [5]

# Word two: the field's viewport height, inside field_init_viewport_values'
# mode-2 branch. The recompiled `push 448` of x86 0x60D861.
FRAME_HEIGHT    = 0x9298BC
WORD_H_448      = 0x321A0BE8    # orr w8, wzr, #0x1c0
WORD_H_480      = 0x52803C08    # mov w8, #0x1e0
STOCK_FRAME_H   = 448
OPEN_FRAME_H    = 480

# The neighbours of FRAME_HEIGHT, as a signature -- the 640 and the x zero
# that make up the same setviewport argument block.  +0x9298EC (the y) used
# to be in here; it is a PATCH SITE now, so it is checked by name instead.
FRAME_SIG = [
    (0x9298C0, 0xB9000008),     # str w8, [x0]        -- stores the h
    (0x9298D4, 0x52805008),     # mov w8, #0x280      -- w   = 640
    (0x929900, 0xB900001F),     # str wzr, [x0]       -- x   = 0
    (0x929910, None),           # bl sub_9296C0 -- checked as "is a bl"
]

# gfx_drv_setviewport: the device rect and the (write-only) d3dviewport matrix.
SETVIEWPORT     = 0x10D6760
MATRIX_WRITES   = [0x10D6848, 0x10D6864, 0x10D6878, 0x10D68A4]   # _42 _11 _22 _41/_42
STATE_SLOT      = 0x12CE668

# FFNx's ff7_field_center -- the four field_layerN_pick_tiles origins.
STOCK_ORIGIN_Y  = 224
CENTER_ORIGIN_Y = 232

# Word four: [0xCFF200], the field SPRITE origin. FFNx patches this to 240
# (patch_code_int(field_init_viewport_values + 0x6E, 240)) in the same breath
# as the tile origin, because sprites -- steam, fire, the reactor effects --
# are placed through it and have to travel with the background.
# 224 -> 240 is +16 game units; 224 -> 232 in TILE units is the same distance.
# The stock encoding is `orr w9, wzr, #0xe0`, not a movz. Restoring by
# re-encoding as `mov w9, #224` would mean the same thing and produce a
# DIFFERENT word, so --revert would not be byte-exact. Keep both literals.
ORIGIN_WORD_224 = 0x321B0BE9    # orr w9, wzr, #0xe0
ORIGIN_WORD_232 = 0x52801D09    # mov w9, #0xe8

# Word five: the field VIEWPORT Y -- FFNx's actual third leg.  v5 CORRECTION.
#
# v4 put the +16 into [0xCFF208] (+0x9299D0) and called it "the 3D model
# lever".  That was wrong twice over, and it is why the last build was worse
# than the one before it.
#
# FFNx's ff7_field_center is exactly two code patches (ff7_opengl.cpp:312):
#
#     patch_code_byte(field_init_viewport_values + 0x35, 16)
#     patch_code_int (field_init_viewport_values + 0x6E, 240)
#
# Read them against the x86 rather than against the names:
#
#   +0x2A  push 0x1c0   h = 448
#   +0x2F  push 0x280   w = 640
#   +0x34  push 0       y   <-- +0x35 IS THE IMMEDIATE OF THIS PUSH
#   +0x36  push 0       x
#   +0x38  call set_field_viewport(x, y, w, h)
#   +0x68  mov dword [0xcff200], 0xe0    <-- +0x6E is this imm32 -> 240
#
# So leg three is `y` -- the second argument of set_field_viewport -- and in
# this port that is `str wzr, [x0]` at +0x9298EC, four words after the 640.
#
# What [0xCFF208] actually is, from the same image:
#
#     x86 0x6464BA  add_field_point(float x, float y, ...)
#                   x += [0xCFF204];  y += [0xCFF208]
#     callers: 0x640B83 0x640C32 0x640E97 0x64129D 0x641337 0x6416A2 0x64173C
#              -- all inside field_layerN_pick_tiles, the BACKGROUND emitter
#     and FFNx model.cpp:36 uses the same pair as the model CULL centre.
#
# It is a background-tile offset.  Setting it to 16 moved the tiles a SECOND
# +16 on top of the origin 224->232 that had already moved them, so the
# scenery ended up 32 game units (48 px at 720p) below the characters instead
# of 16.  "Slightly off" became "off even more", exactly as reported.
#
# The cave shape is unchanged -- both sites are the same `str wzr, [x0]` and
# w8 is dead at each -- so only the address moves.  CFF208_SITE is kept so
# --apply can back the v4 patch out of a module that already has it.
MODEL_Y_SITE    = 0x9298EC      # str wzr, [x0]  ->  set_field_viewport y = 0
CFF208_SITE     = 0x9299D0      # str wzr, [x0]  ->  [0xCFF208] = 0   (v4's site)
MODEL_Y_STOCK   = 0xB900001F
STOCK_MODEL_Y   = 0
CENTER_MODEL_Y  = 16            # game units, the same +16 as the other two

# Word six: THE UNCROP SCISSOR.  v6.
#
# v5 fixed the alignment and the bars came back, which is the measurement that
# retires "the rect does not clip the field" (HANDOFF-86 §2, four boots).
# Those four boots were confounded: h was toggled while the painted quads were
# STILL ON, so the 24 px they painted hid the 24 px the rect was clipping.
# With the quads off (+0x10F3DDC) the rect is the only thing left, and it
# clips -- bars returned the moment h went 480 -> 448.
#
# So the rect has two jobs that want different numbers, exactly as in FFNx:
#
#     the d3dviewport matrix wants (y=16, h=448)  -- _22 = 448/480, _42 = 0
#     the clip rect wants          (y=0,  h=480)  -- no bars
#
# and FFNx splits them in Renderer::setScissor (renderer.cpp:1667):
#
#     if (enable_uncrop && !ff8)
#         if (y == 16 && height == 448) { scissorOffsetY = 0; scissorHeight = 480; }
#
# That is the branch three of my dead caves keyed on.  It never fired because
# the game passed y = 0.  It fires now, because leg three makes the game pass
# 16 -- FFNx creates the condition it then branches on, and so do we.
#
# The split is done on the DEVICE values, after the divide, so w1/w3 are left
# alone and both the matrix and current_state.viewport (+0x10 / +0x18, which
# gl_load_state replays) still see the real 16/448:
#
#     +0x10D67BC  lsr x13, x13, #0x28    x13 = screen*y/480   = 24 at 720p
#     +0x10D67C0  lsr x15, x15, #0x28    x15 = screen*h/480   = 672
#     +0x10D67C8  add w15, w15, w13      y2 = y1 + h          <-- HOOK
#     +0x10D67CC  bfi x9, x13, #0x20,#0x18   packs y1
#
# 16 + 448 + 16 = 480, so y2 := h + 2*y1 and y1 := 0 is exact, not fitted:
# 720 -> 0..720, 1080 -> 0..1080, 1440 -> 0..1440, all with no remainder
# (the divisor is an exact 1/480 magic, 0x88888889 >> 40).
#
# w16 is free -- nothing in gfx_drv_setviewport touches x16/x17 and it makes
# no calls before the hook.  NZCV is dead at +0x10D67CC.
# THERE ARE THREE COPIES OF THIS ARITHMETIC.  v7.
#
# v6 patched one and the bars survived, which is the measurement that finds
# the other two. `bfi x?, x?, #0x20, #0x18` -- the y1 pack -- is distinctive
# enough to count them, and there are exactly three:
#
#   A  +0x10D67CC   gfx_drv_setviewport  +0x10D6760   args in w0-w3
#   B  +0x10D9464   gl_load_state        +0x10D9370   args from [x20+0x10/0x18]
#   C  +0x10D9E38   begin_scene          +0x10D9D70   args from [x8 +0x10/0x18]
#
# B and C are the state SAVE/RESTORE path FINDINGS-88 identified and then
# never followed: they re-derive the clip rect from the saved
# driver_state.viewport every frame, so whatever A computes is overwritten
# before anything is drawn. That is also why six earlier attempts at the live
# rect bounced -- they were all patching A.
#
# Each copy has the same shape. y1 = screen*y/480 and h = screen*h/480 land
# in two registers, an `add` makes y2 = y1 + h, and a `bfi` packs y1:
#
#   site        displaced `add`            y    h    y1   acc  scratch
#   +0x10D67C8  add w15, w15, w13          w1   w3   w13  w15  w16
#   +0x10D9458  add w0,  w0,  w17          w9   w11  w17  w0   w2
#   +0x10D9E34  add w1,  w1,  w18          w10  w12  w18  w1   w2
#
# The scratch register at each site is one whose next access is a WRITE (or
# which is never touched again), checked against the real instruction stream
# rather than assumed, and NZCV is rewritten before it is read at all three.
# THE BATTLE RECT.  v10.
#
# `battle_enter` (x86 0x41AD00) writes the battle viewport into the same four
# globals eleven other consumers read as {x, y, w, h}:
#
#     [0x9AAD4C] = 0        x        ARM +0x8D410  str w19 (wzr)
#     [0x9AAD50] = 0        y        ARM +0x8D41C  str w28 (wzr)
#     [0x9AAD5C] = 640      w        ARM +0x8D428  str w24, w24 = #0x280
#     [0x9AAD68] = 332      h        ARM +0x8D440  str w26, w26 = #0x14c
#
# battle_enter was NOT resolvable by FFNx's own anchor -- it has no address in
# its name and `get_relative_call(battle_enter, 0x17)` names 0x41B577, which
# has ZERO call sites in this build (it is switch-1.03_5, not PC 1.02). It was
# found instead from the operand pattern: FFNx patches `+0x229` to
# `&wide_viewport_width` and `+0x22F` to `&wide_viewport_x`, six bytes apart,
# and exactly ONE place in the executable has a reference to 0x9AAD5C followed
# six bytes later by one to 0x9AAD4C. All four of FFNx's offsets then land on
# the right instruction types, which is four independent confirmations.
#
# AND IT CONFIRMS THE CAPTURE. 332 of 480 game units is 0.69167 of the frame;
# at 720p that is 498 device rows, and the battle scene in the hardware
# capture ends at row ~498 with the UI below it. The rect is (0, 0, 640, 332)
# and the band is everything under row 498.
#
# WHY NOT SIMPLY WRITE 480 INTO THE HEIGHT, WHICH IS WHAT FFNx DOES
# -----------------------------------------------------------------
# Because `_42` is not carved out. Battle is driver mode 3, where
# gfx_drv_setviewport forces `_22 = 1.0` -- so the height does NOT rescale
# anything -- but `_42 = -((y + h/2) - 240)/240` still moves with it:
#
#     (0, 0, 640, 332)   _22 1.00000   _42 0.30833    rows   0..498
#     (0, 0, 640, 480)   _22 1.00000   _42 0.00000    rows   0..720
#
# 0.30833 / 2 * 720 = 111 device rows. Writing 480 opens the band and shifts
# every model down 111 px, which is the opposite of the request ("nothing
# moved, just rendering more pixels"). FFNx can do it because FFNx does not
# have this port's mode-3 carve-out sitting next to an uncarved _42.
#
# So battle takes the same shape the FIELD leg already takes: open the DEVICE
# RECT, leave the matrix alone. y1 is already 0 for this rect, so the whole
# leg is "make the extent the full target height".
BATTLE_VP_Y = 0
BATTLE_VP_H = 332               # [0x9AAD68], ARM +0x8D3CC `mov w26, #0x14c`

UNCROP_SITES = [
    #  hook        stock        y   h   y1  acc  T   SY  name
    #
    # SY is the register holding scale_y -- the render target height, loaded
    # from [[0x12CE580]], the same slot ws_emu feeds. Read out of each site
    # rather than assumed, and live at every hook:
    #   A  +0x10D678C ldr w12  -> used +0x10D67A4/A8, hook +0x10D67C8
    #   B  +0x10D9400 ldr w16  -> used +0x10D940C/10, hook +0x10D9458
    #   C  +0x10D9DEC ldr w17  -> used +0x10D9DF8/FC, hook +0x10D9E34
    (0x10D67C8, 0x0B0D01EF,  1,  3, 13, 15, 16, 12, 'gfx_drv_setviewport'),
    (0x10D9458, 0x0B110000,  9, 11, 17,  0,  2, 16, 'gl_load_state'),
    (0x10D9E34, 0x0B120021, 10, 12, 18,  1,  2, 17, 'begin_scene'),
]


def _uncrop_body(y, h, y1, acc, t_, sy=None):
    """
    cmp/csel only -- no branches, so it survives hole scattering.

    TWO LEGS, and they are mutually exclusive by construction: the field rect
    is (y=16, h=448) and the battle rect is (y=0, h=332), so at most one can
    match on any given call. The field leg runs first and leaves `acc` and
    `y1` untouched when it does not match, which is what lets the battle leg
    read `acc` afterwards.

    FIELD  -- open symmetrically: y1 -> 0 and the extent grows by 2*y1.
    BATTLE -- y1 is ALREADY 0, so a symmetric open is a no-op and the extent
              has to be set outright. `wSY` is the render target height, so
              "the extent becomes wSY" is exactly "draw to the bottom of the
              frame", with `y1` left at 0 and the matrix untouched.
    """
    w = [
        0x71000000 | (16 << 10) | (y << 5) | 31,      # cmp  wY, #16
        0x1A800000 | (31 << 16) | (y1 << 5) | t_,     # csel wT, wY1, wzr, eq
        0x71000000 | (448 << 10) | (h << 5) | 31,     # cmp  wH, #448
        0x1A800000 | (31 << 16) | (t_ << 5) | t_,     # csel wT, wT, wzr, eq
        0x0B000000 | (t_ << 16) | (1 << 10) | (acc << 5) | acc,   # add acc,acc,T,lsl#1
        0x4B000000 | (t_ << 16) | (y1 << 5) | y1,     # sub  wY1, wY1, wT
    ]
    if sy is not None:
        w += [
            # cmp  wY, #0             ; the battle rect's y
            0x71000000 | (BATTLE_VP_Y << 10) | (y << 5) | 31,
            # csel wT, wSY, wACC, eq  ; T = target height if y matches, else acc
            0x1A800000 | (acc << 16) | (sy << 5) | t_,
            # cmp  wH, #332           ; the battle rect's h
            0x71000000 | (BATTLE_VP_H << 10) | (h << 5) | 31,
            # csel wACC, wT, wACC, eq ; acc = T if h matches, else unchanged
            0x1A800000 | (acc << 16) | (t_ << 5) | acc,
        ]
    return w


UNCROP_HOOK     = UNCROP_SITES[0][0]        # kept for the older messages
UNCROP_STOCK    = UNCROP_SITES[0][1]
# The pack that follows each hook, as a signature. If any copy moved, refuse.
UNCROP_SIG = [
    (0x10D67BC, 0xD368FDAD),    # A  lsr x13, x13, #0x28
    (0x10D67CC, 0xB3605DA9),    # A  bfi x9,  x13, #0x20, #0x18
    (0x10D67D8, 0x1E230061),    # A  ucvtf s1, w3   -- h still reaches the matrix
    (0x10D68A8, 0x29020500),    # A  stp w0, w1, [x8, #0x10]  -- and so does y
    (0x10D9450, 0xD368FE31),    # B  lsr x17, x17, #0x28
    (0x10D9464, 0xB3605E2D),    # B  bfi x13, x17, #0x20, #0x18
    (0x10D93F0, 0x29432E8A),    # B  ldp w10, w11, [x20, #0x18]  -- w, h
    (0x10D9E28, 0xD368FE52),    # C  lsr x18, x18, #0x28
    (0x10D9E38, 0xB3605E4E),    # C  bfi x14, x18, #0x20, #0x18
    (0x10D9DDC, 0x2943310B),    # C  ldp w11, w12, [x8, #0x18]   -- w, h
    # v10: the scale_y load at each site. The battle leg reads this register
    # and a wrong one would produce a plausible-looking rect out of whatever
    # happened to be there, which is the least debuggable failure available.
    (0x10D678C, 0xB940018C),    # A  ldr w12, [x12]   scale_y
    (0x10D9400, 0xB9400210),    # B  ldr w16, [x16]   scale_y
    (0x10D9DEC, 0xB9400231),    # C  ldr w17, [x17]   scale_y
]

# The scale_y load per site, so the register the battle leg READS can be
# checked against the register the module WRITES. Without this the emulator
# happily seeds whatever register the table declares and a wrong one is
# invisible -- which it was, for two of the three sites, until this existed.
SCALE_LOADS = {
    0x10D67C8: 0x10D678C,       # A gfx_drv_setviewport
    0x10D9458: 0x10D9400,       # B gl_load_state
    0x10D9E34: 0x10D9DEC,       # C begin_scene
}

# Word seven: THE FADE QUAD.  v8.
#
# The fade to black, the fade into battle, and the red/white damage flashes
# are all ONE quad, and it is sized from the field viewport -- so it covers
# the 4:3 core and nothing else. On a 16:9 frame with the bars gone that is
# unmistakable: the picture fades and the margins do not.
#
# FFNx fixes both axes in one wrapper (ff7/field/field.cpp:161):
#
#     if (widescreen_enabled) {
#         x     -= abs(wide_viewport_x);                    // 107
#         width += (wide_viewport_width - game_width);      // 854 - 640
#     }
#     if (enable_uncrop) {
#         y      -= ff7_field_center ? 16 : 0;
#         height += 32;
#     }
#     ff7_externals.field_sub_63AC3F(x, y, width, height);
#
# `field_sub_63AC3F` has exactly ONE caller in the whole executable
# (x86 0x63AD3B, which pushes [0xCFF1EC], [0xCFF1E8], [0xCFF1E4], [0xCFF1E0]
# -- h, w, y, x -- verbatim). So patching inside the setter is equivalent to
# FFNx's wrapper and needs no cave at the call site.
#
# The setter is four stores of one register, +0x9F39D0..+0x9F3AC0:
#
#     +0x9F3A24  str w21, [x0]     -> [0xCFFADC]  x
#     +0x9F3A44  str w21, [x0]     -> [0xCFFAE0]  y
#     +0x9F3A64  str w21, [x0]     -> [0xCFFAE4]  w
#     +0x9F3A84  str w21, [x0]     -> [0xCFFAE8]  h
#
# Each is hooked and the value adjusted immediately before the store, so the
# guest's own EAX (written at +0x9F3A1C, before the translator call) keeps the
# value the game computed. The function touches ONLY x0, x8, x19, x20, x21 --
# w9 is free, and it could not have held anything live anyway because the
# function makes `bl` calls without saving it.
#
# EVERY LEG IS CONDITIONAL ON ITS STOCK VALUE. An unconditional shift that
# fires in a configuration nobody tested is the v4 defect; this refuses to
# move a rect that is not the one it was derived for.
FADE_SITES = [
    #  hook       stock word   guard  name
    (0x9F3A24, 0xB9000015,   0, 'x'),
    (0x9F3A44, 0xB9000015,  16, 'y'),
    (0x9F3A64, 0xB9000015, 640, 'w'),
    (0x9F3A84, 0xB9000015, 448, 'h'),
]
FADE_SIG = [
    (0x9F3A10, 0x529F5B93),     # mov  w19, #0xfadc
    (0x9F3A14, 0x72A019F3),     # movk w19, #0xcf, lsl #16   -> 0xCFFADC
    (0x9F3A38, 0x11001260),     # add  w0, w19, #4
    (0x9F3A58, 0x11002260),     # add  w0, w19, #8
    (0x9F3A78, 0x11003260),     # add  w0, w19, #0xc
]


def _fade_body(guard, new):
    """
    cmp/(mov)/csel -- no branches, so it survives hole scattering.

    CSEL Wd, Wn, Wm, cond is `Wd = cond ? Wn : Wm`, so the NEW value has to
    be Wn (bits 9:5) and the original Wm (bits 20:16). Getting that backwards
    is a silent inversion -- the patch applies, reads back "widened", and
    keeps the stock rect -- and it shipped once because the emulator in
    verify() had the operands the same way round. It is asserted against
    capstone below rather than against itself.
    """
    def csel(rn, rm):                                 # w21 = eq ? wRn : wRm
        return 0x1A800000 | (rm << 16) | (rn << 5) | 21
    cmp_ = 0x71000000 | (guard << 10) | (21 << 5) | 31          # cmp w21, #guard
    if new == 0:
        return [cmp_, csel(31, 21)]                             # csel w21,wzr,w21,eq
    if new < 0:
        mov = 0x12800000 | ((-new - 1) << 5) | 9                # movn w9, #-new-1
    else:
        mov = 0x52800000 | (new << 5) | 9                       # movz w9, #new
    return [cmp_, mov, csel(9, 21)]                             # csel w21,w9,w21,eq


def fade_rect(ws_scale):
    """
    FFNx's numbers, derived rather than copied.

    The half-margin is `320/S - 320`, and it is rounded OUT. This is a COVER
    quad -- the thing that paints the screen black -- so a margin one unit
    short leaves a lit sliver down the edge for the whole fade, while one
    unit long is invisible. `round` gives 106 at 0.75 and would do exactly
    that; `ceil` gives 107, which is also FFNx's `wide_viewport_x` and the
    107 this build's own log prints for the parallax edge and the 57 in
    ff7nx_modelcull (107 - 50). Same reason ff7nx_movieclip uses ceil.

        0.74766355   428 px   margin 108   wide 856
        0.74941452   854 px   margin 107   wide 854
        0.75000000  1280 px   margin 107   wide 854   <- ships, = FFNx exactly
    """
    # The epsilon is not cosmetic. WS_SCALE arrives as a decimal string out
    # of the shader, so 320/0.74766355 - 320 evaluates to 108.0000008 and a
    # bare ceil() returns 109 -- a whole unit of margin conjured out of the
    # shader's last decimal place. Shave a millionth before rounding out.
    margin = int(math.ceil(320.0 / ws_scale - 320.0 - 1e-6))
    return {'x': -margin, 'y': 0, 'w': 640 + 2 * margin, 'h': 480}


SPRITE_ORIGIN   = 0x929964
WORD_SPR_224    = 0x321B0BE8    # orr w8, wzr, #0xe0
WORD_SPR_240    = 0x52801E08    # mov w8, #0xf0
ORIGIN_SITES = [
    (0x0A05AA4, 'layer 2'),
    (0x0A06EA8, 'layer 1'),
    (0x0A07878, 'layer 3'),
    (0x0A08728, 'layer 4'),
]

# The whole mode-2 branch, as a signature. If any of this moved, refuse.
MODE2_SIG = [
    (0x10F3DB4, 0x1E220000),    # scvtf s0, w0
    (0x10F3DC8, 0x1E210800),    # fmul  s0, s0, s1
    (0x10F3DD0, 0xF0000F8A),    # adrp  x10, #0x12e6000
    (0x10F3DE0, 0x1400001A),    # b     #0x10f3e48
]


# --------------------------------------------------------------------------
# nso helpers
# --------------------------------------------------------------------------
def _text(path) -> bytes:
    import nso_tool
    return nso_tool.parse_nso(str(path))['segments']['.text']['data']


def _rodata(path) -> tuple[bytes, int]:
    import nso_tool
    seg = nso_tool.parse_nso(str(path))['segments']['.rodata']
    return seg['data'], seg['dst_off']


def w32(t: bytes, va: int) -> int:
    return struct.unpack_from('<I', t, va)[0]


def movz_imm(word: int) -> int | None:
    """imm16 of a 32-bit MOVZ/ORR-imm with shift 0, else None."""
    if (word & 0xFFE00000) == 0x52800000 and ((word >> 21) & 3) == 0:
        return (word >> 5) & 0xFFFF
    # orr wN, wzr, #imm -- the form the compiler picked for 224 and 448
    if (word & 0xFF800000) == 0x32000000:
        return _orr_imm32(word)
    return None


def _orr_imm32(word: int) -> int | None:
    """Decode ORR (immediate) 32-bit against wzr into its literal value."""
    n = (word >> 22) & 1
    immr = (word >> 16) & 0x3F
    imms = (word >> 10) & 0x3F
    if n:
        return None
    for width in (32, 16, 8, 4, 2):
        mask = (1 << width) - 1
        if (imms & ~( (width - 1) )) == (0x3F & ~(width - 1)) or width == 32:
            pass
    # generic decoder
    size = 32
    immr_ = immr
    imms_ = imms
    length = 5
    while length >= 0:
        if not (imms_ & (1 << length)):
            break
        length -= 1
    if length < 0:
        return None
    esize = 1 << (length + 1) if length + 1 <= 5 else 32
    esize = 1
    # standard ARM DecodeBitMasks
    x = (~imms_) & 0x3F
    hi = 5
    while hi >= 0 and not (x & (1 << hi)):
        hi -= 1
    if hi < 0:
        return None
    esize = 1 << hi
    if esize < 2:
        return None
    levels = esize - 1
    s = imms_ & levels
    r = immr_ & levels
    if s == levels:
        return None
    welem = (1 << (s + 1)) - 1
    # ror welem by r within esize
    welem = ((welem >> r) | (welem << (esize - r))) & ((1 << esize) - 1)
    out = 0
    for i in range(0, size, esize):
        out |= welem << i
    return out & 0xFFFFFFFF


def _decode_movz(word: int) -> tuple[int, int] | None:
    """(imm16, Rd) of a 32-bit MOVZ with shift 0, else None."""
    if (word & 0xFFE00000) != 0x52800000 or ((word >> 21) & 3) != 0:
        return None
    return (word >> 5) & 0xFFFF, word & 31


# Shipping a word whose immediate does not mean what its NAME says is the one
# mistake this module cannot detect from the module -- the anchors accept it,
# the writer reports success, and only the readback disagrees. So assert the
# whole table at import time. 0x52801C09 is `mov w9, #0xe0`; the 232 word is
# 0x52801D09. That single bit shipped once.
_WORD_TABLE = [
    (ORIGIN_WORD_232, 232, 9, 'tile origin 232'),
    (WORD_SPR_240,    240, 8, 'sprite origin 240'),
    (WORD_H_480,      480, 8, 'field frame height 480'),
]
for _w, _v, _rd, _name in _WORD_TABLE:
    _dec = _decode_movz(_w)
    assert _dec == (_v, _rd), (
        f'{_name}: word {_w:08X} decodes to {_dec}, expected ({_v}, {_rd})')
del _w, _v, _rd, _name, _dec


def _fmt(word: int) -> str:
    b = struct.pack('<I', word)
    return ' '.join(f'{x:02X}' for x in b)


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------
def state(t: bytes) -> dict:
    st = {}
    st['store'] = w32(t, LETTERBOX_STORE)
    st['bars_on'] = (st['store'] == WORD_STORE_S0)
    st['bars_off'] = (st['store'] == WORD_STORE_WZR)
    st['frame'] = w32(t, FRAME_HEIGHT)
    st['frame_h'] = {WORD_H_448: STOCK_FRAME_H, WORD_H_480: OPEN_FRAME_H}.get(st['frame'])
    st['origins'] = [(va, movz_imm(w32(t, va)), name) for va, name in ORIGIN_SITES]
    st['sprite'] = w32(t, SPRITE_ORIGIN)
    st['sprite_o'] = {WORD_SPR_224: 224, WORD_SPR_240: 240}.get(st['sprite'])
    st['model_word'] = w32(t, MODEL_Y_SITE)
    st['model_y'] = _read_model_y(t)
    st['cff208_word'] = w32(t, CFF208_SITE)
    st['cff208'] = _read_model_y(t, CFF208_SITE)
    st['fade_sites'] = [(hook, w32(t, hook) != stock, name)
                        for hook, stock, _g, name in FADE_SITES]
    st['fade'] = all(on for _, on, _ in st['fade_sites'])
    st['uncrop_sites'] = [(hook, w32(t, hook) != stock, name)
                          for hook, stock, *_ , name in UNCROP_SITES]
    st['uncrop'] = all(on for _, on, _ in st['uncrop_sites'])
    st['uncrop_part'] = (any(on for _, on, _ in st['uncrop_sites'])
                         and not st['uncrop'])
    st['centered'] = (all(v == CENTER_ORIGIN_Y for _, v, _ in st['origins'])
                      and st['sprite_o'] == 240
                      and st['model_y'] == CENTER_MODEL_Y
                      and st['cff208'] == 0)
    return st


def _read_model_y(t: bytes, site: int | None = None) -> int | None:
    """The value the store at `site` ends up writing, stock or via the cave."""
    site = MODEL_Y_SITE if site is None else site
    w = w32(t, site)
    if w == MODEL_Y_STOCK:
        return STOCK_MODEL_Y
    if (w & 0xFC000000) != 0x14000000:          # not a `b` to a cave
        return None
    imm = w & 0x03FFFFFF
    if imm & (1 << 25):
        imm -= (1 << 26)
    va = site + imm * 4
    for _ in range(64):                          # walk the chained cave
        word = w32(t, va)
        if (word & 0xFFE00000) == 0x52800000:    # movz wN, #imm16
            return (word >> 5) & 0xFFFF
        if (word & 0xFC000000) == 0x14000000:
            j = word & 0x03FFFFFF
            if j & (1 << 25):
                j -= (1 << 26)
            va += j * 4
            continue
        va += 4
    return None


def window_rows(frame_h: int, screen_h: int = 720,
                y: int = 0) -> tuple[int, int]:
    """
    Where the 3D MODELS land, in device rows.

    NOT where the background lands. Four boots settled that: with h at 448
    and again at 480 the picture's band was bit-identical, so the rect the
    game passes does not clip the 2D field. The background is TLVERTEX
    through the hardcoded ortho(0,640,480,0) at +0x10DA018 and always fills
    the frame; the rect reaches models only, through the d3dviewport matrix
    (_22 = h/480, _42 = -((y+h/2)-240)/240), which is FFNx gl.cpp:353
    `if(vertextype != TLVERTEX) setD3DViweport(...)`.

    So this is the model band, and matching it to the movie quad is what
    keeps a character inside the picture during an FMV.
    """
    return screen_h * y // 480, screen_h * (y + frame_h) // 480


def check_anchors(t: bytes, path=None) -> list[str]:
    """Every structural fact this patch depends on. Returns failures."""
    bad = []

    for va, want in MODE2_SIG:
        got = w32(t, va)
        if got != want:
            bad.append(f'mode-2 branch moved: +{va:#09x} is {got:08X}, expected {want:08X}')

    got = w32(t, LETTERBOX_STORE)
    if got not in (WORD_STORE_S0, WORD_STORE_WZR):
        bad.append(f'+{LETTERBOX_STORE:#09x} is {got:08X}, neither stock nor patched')

    donor = w32(t, LETTERBOX_ZERO)
    if donor != WORD_STORE_WZR:
        bad.append(f'donor word +{LETTERBOX_ZERO:#09x} is {donor:08X}, expected '
                   f'{WORD_STORE_WZR:08X} -- the replacement encoding is no longer '
                   f'self-evidenced')

    # the dispatch really is a 15-entry table and entry 2 really is our branch
    if path is not None:
        rod, rbase = _rodata(path)
        off = MODE_JUMP_TABLE - rbase + 4
        delta = struct.unpack_from('<i', rod, off)[0]
        if MODE_JUMP_TABLE + delta != MODE2_BRANCH:
            bad.append(f'jump table entry for mode 2 points at '
                       f'+{MODE_JUMP_TABLE + delta:#09x}, not +{MODE2_BRANCH:#09x}')
        frac = struct.unpack_from('<f', rod, FRACTION_CONST - rbase)[0]
        if abs(frac - 16.0 / 480.0) > 1e-7:
            bad.append(f'.rodata {FRACTION_CONST:#x} is {frac!r}, not 16/480')
        # every other branch of the same switch must store zero
        nonzero = []
        for i in range(15):
            d = struct.unpack_from('<i', rod, MODE_JUMP_TABLE - rbase + i * 4)[0]
            tgt = MODE_JUMP_TABLE + d
            if tgt == MODE2_BRANCH:
                continue
            # walk the branch for a store to [x?, #0xcc]
            seen_zero = False
            for k in range(0, 24):
                w = w32(t, tgt + 4 * k)
                if (w & 0xFFC00000) == 0xB9000000 and ((w >> 10) & 0xFFF) * 4 == 0xCC \
                        and (w & 31) == 31:
                    seen_zero = True
                    break
                if (w & 0xFC000000) == 0x14000000:      # unconditional b
                    break
            if not seen_zero:
                nonzero.append(i + 1)
        if nonzero != MODE_INHERITS:
            bad.append(f'modes {nonzero} neither write nor zero the letterbox; '
                       f'expected exactly {MODE_INHERITS} -- the mode map moved')

    for va, name in ORIGIN_SITES:
        got = w32(t, va)
        if got not in (ORIGIN_WORD_224, ORIGIN_WORD_232):
            bad.append(f'{name} origin +{va:#09x} is {got:08X} '
                       f'({movz_imm(got)}), not the stock 224 or the 232 word')

    for _site, _what in ((MODEL_Y_SITE, 'viewport-y'), (CFF208_SITE, 'cff208')):
        got = w32(t, _site)
        if not (got == MODEL_Y_STOCK or (got & 0xFC000000) == 0x14000000):
            bad.append(f'{_what} +{_site:#09x} is {got:08X}, neither the '
                       f'stock `str wzr,[x0]` nor a branch to a cave')
        elif _read_model_y(t, _site) is None:
            bad.append(f'{_what} +{_site:#09x} branches somewhere this '
                       f'cannot read a constant out of')

    got = w32(t, SPRITE_ORIGIN)
    if got not in (WORD_SPR_224, WORD_SPR_240):
        bad.append(f'sprite origin +{SPRITE_ORIGIN:#09x} is {got:08X}, not 224 or 240')

    # ---- word two: the field viewport height -----------------------------
    got = w32(t, FRAME_HEIGHT)
    if got not in (WORD_H_448, WORD_H_480):
        bad.append(f'+{FRAME_HEIGHT:#09x} is {got:08X}, neither 448 nor 480')
    for va, want in FRAME_SIG:
        g = w32(t, va)
        if want is None:
            if (g & 0xFC000000) != 0x94000000:
                bad.append(f'+{va:#09x} is {g:08X}, expected a bl (setviewport)')
        elif g != want:
            bad.append(f'setviewport arg block moved: +{va:#09x} is {g:08X}, '
                       f'expected {want:08X}')

    # ---- word six: the uncrop scissor -----------------------------------
    for _hook, _stock, _g, _nm in FADE_SITES:
        got = w32(t, _hook)
        if not (got == _stock or (got & 0xFC000000) == 0x14000000):
            bad.append(f'fade quad {_nm} +{_hook:#09x} is {got:08X}, neither '
                       f'the stock `str w21,[x0]` nor a branch to a cave')
    for _va, _want in FADE_SIG:
        _g = w32(t, _va)
        if _g != _want:
            bad.append(f'field_sub_63AC3F moved: +{_va:#09x} is {_g:08X}, '
                       f'expected {_want:08X} -- do not patch the fade quad')
    for _hook, _stock, *_rest in UNCROP_SITES:
        got = w32(t, _hook)
        if not (got == _stock or (got & 0xFC000000) == 0x14000000):
            bad.append(f'uncrop hook +{_hook:#09x} is {got:08X}, neither the '
                       f'stock {_stock:08X} nor a branch to a cave')
    for va, want in UNCROP_SIG:
        g = w32(t, va)
        if g != want:
            bad.append(f'gfx_drv_setviewport moved: +{va:#09x} is {g:08X}, '
                       f'expected {want:08X} -- do not patch the clip rect')

    # ---- the d3dviewport matrix is READ, and that is now the expectation --
    # v4 asserted the opposite here and was wrong (see _matrix_readers). The
    # matrix reaching model geometry is the whole reason leg three works at
    # all, so its ABSENCE is the anomaly to refuse on, not its presence.
    if not _matrix_readers(t):
        bad.append('the d3dviewport matrix has no reader in this module -- '
                   'expected the pointer form at +0x10DA0B0. Leg three (the '
                   'viewport y) cannot reach models; do not apply.')

    return bad


def _matrix_readers(t: bytes) -> list[int]:
    """
    Any USE of the d3dviewport matrix on a base taken from [0x12CE668].

    v4 looked only for `ldr` at +0xA8/+0xBC/+0xD8/+0xDC, found none, and
    concluded the matrix was write-only -- which is what let word two (h
    448 -> 480) ship as "cannot move models".  It is not write-only.  The
    matrix is used by POINTER, and an `add` is not a load:

        +0x10DA098  add x0, x27, #0x28    world_view_matrix
        +0x10DA09C  add x1, x27, #0x68    d3dprojection_matrix
        +0x10DA0A4  bl  +0x10D9AA0        mul -> sp+0x68
        +0x10DA0B0  add x1, x27, #0xa8    THE D3DVIEWPORT MATRIX
        +0x10DA0B8  bl  +0x10D9AA0        mul -> the final model transform

    which is FFNx `setD3DViweport(&d3dviewport_matrix)` / deferred.cpp:545,
    applied to every draw with `vertextype != TLVERTEX` -- i.e. to 3D models
    and to nothing else.  gfx_drv_setviewport computes it exactly as
    common.cpp:1468 does:

        _22 = h / game_height        _42 = -((y + h/2) - gh/2) / (gh/2)

    so at (y=0, h=448) models get 0.93333 / +0.06667, at (y=16, h=448) they
    get 0.93333 / 0 -- FFNx's field_center -- and at (y=0, h=480) they get
    1.0 / 0, which is the same offset but a 480/448 = 1.0714 VERTICAL
    STRETCH about the centre of the frame.  That stretch is zero at mid
    screen and +-16 game units (24 px at 720p) at the edges: "characters
    are slightly off, and it gets worse away from the middle".

    So this now scans for the pointer form too, and word two is refused
    unless it is asked for explicitly.
    """
    out = []
    n = len(t) // 4
    for i in range(n):
        w = struct.unpack_from('<I', t, i * 4)[0]
        if (w & 0x9F000000) != 0x90000000:              # ADRP
            continue
        rd = w & 31
        immlo = (w >> 29) & 3
        immhi = (w >> 5) & 0x7FFFF
        imm = (immhi << 2) | immlo
        if imm & (1 << 20):
            imm -= (1 << 21)
        if ((i * 4) & ~0xFFF) + (imm << 12) != (STATE_SLOT & ~0xFFF):
            continue
        # ldr xN, [xrd, #slot&0xFFF]
        base = None
        for j in range(i + 1, min(i + 16, n)):
            w2 = struct.unpack_from('<I', t, j * 4)[0]
            if (w2 & 0xFFC00000) == 0xF9400000 and ((w2 >> 5) & 31) == rd \
                    and ((w2 >> 10) & 0xFFF) * 8 == (STATE_SLOT & 0xFFF):
                base = w2 & 31
                start = j + 1
                break
        if base is None:
            continue
        for j in range(start, min(start + 80, n)):
            w2 = struct.unpack_from('<I', t, j * 4)[0]
            if ((w2 >> 5) & 31) == base:
                # a scalar/word load off one of the four matrix cells
                if ((w2 & 0xFFC00000) in (0xBD400000, 0xB9400000)
                        and ((w2 >> 10) & 0xFFF) * 4 in (0xA8, 0xBC, 0xD8, 0xDC)):
                    out.append(j * 4)
                # ADD (immediate, 64-bit) forming a POINTER into the matrix --
                # the form v4 missed. 0xA8 is _11, so the matrix is 0xA8..0xE8.
                elif ((w2 & 0xFF800000) == 0x91000000
                        and 0xA8 <= ((w2 >> 10) & 0xFFF) <= 0xE8):
                    out.append(j * 4)
            if (w2 & 0x9F000000) == 0x90000000 and (w2 & 31) == base:
                break
    return out


# --------------------------------------------------------------------------
# arithmetic -- what the bars are, without touching the module
# --------------------------------------------------------------------------
def bars_for(screen_h: int, patched: bool) -> tuple[float, float, float]:
    """(letterbox_px, top_edge, bottom_edge) for a screen height."""
    if patched:
        f = 0.0
    else:
        f = screen_h * (16.0 / 480.0)
    return f, f, screen_h - f


def predict(log=print) -> None:
    log('  the two black quads, from the module arithmetic:')
    log('')
    log('    screen        F px    top bar     bottom bar   content rows')
    for h in (720, 1080, 1440):
        f, top, bot = bars_for(h, patched=False)
        log(f'    {h:>4}p       {f:6.1f}    0..{top:<7.0f} {bot:.0f}..{h:<6} '
            f'{top:.0f}..{bot - 1:.0f}')
    log('')
    log('  patched (F forced to 0), every resolution:')
    log('    both quads are degenerate -- 0 px, nothing drawn')
    log('')
    log('  the 720p row agrees with the hardware captures: content 24..695.')


# --------------------------------------------------------------------------
# plan / apply
# --------------------------------------------------------------------------
def plan(t: bytes, revert: bool, field_center: bool,
         frame: bool = False, letterbox: bool = True,
         main=None) -> tuple[list[dict], list[str]]:
    """
    `frame` IS RETIRED.  v9.  The field viewport height stays 448 in every
    mode, and `frame=True` is accepted only so existing callers keep working.

    THE v7 REASONING WAS BACKWARDS, AND HERE IS THE MEASUREMENT
    ----------------------------------------------------------
    v7 argued "the background is 480 units tall, so _22 must be 1.0, so
    h = 480".  It checked that claim with `_align_err`, which compared the
    model row against `1.5 * gy` -- the background at origin **224**.  The
    build ships origin **232**.  The one term that makes this a bug was the
    term the check left out, so the check reported PASS on the broken state
    and FAIL on the correct one.

    Deltas from STOCK, which is known-good because the 4:3 game shipped and
    its models stand on its scenery.  Model rows are `ws_emu` on the real
    encoded words of gfx_drv_setviewport; the background row is the tile
    origin at +0xA06EA8, `dst_y = (ORIGIN - tile.y) * mult`, mult = 2, at
    1.5 device px per game unit:

        config          model delta vs stock        background delta   error
        y=0  h=448      +0                          +24 (origin 232)   -24 flat
        y=16 h=448      +24  flat                   +24                 0  <- THIS
        y=0  h=480      +0 +12 +24 +36 +48          +24                -24..+24
        y=16 h=480      +24 +36 +48 +60 +72         +24                  0..+48

    Only `y=16, h=448` is flat zero at every screen height.  That is also
    exactly what FFNx ships (ff7_opengl.cpp:312) --

        patch_code_byte(field_init_viewport_values + 0x35, 16)   <- y = 16
        patch_code_int (field_init_viewport_values + 0x6E, 240)  <- [0xCFF200]

    -- and FFNx never touches h at all.  The view is opened by the SCISSOR
    (renderer.cpp:1667), not by the viewport height.

    AND h = 480 DISARMS THE UNCROP IT WAS SUPPOSED TO MATCH
    ------------------------------------------------------
    `_uncrop_body` is `cmp wY,#16 / cmp wH,#448 / csel` -- FFNx's trigger,
    word for word.  At (0, 480) that condition is FALSE, so all three uncrop
    caves are installed and dormant.  h = 480 did not "match" the uncrop; it
    replaced it, and it replaced it with something that also moved the models.
    Restoring the pair re-arms the caves and fixes the alignment with the same
    two words.

    `frame=False` is now the only behaviour and is kept as a no-op argument.
    """
    patches, notes = [], []

    if letterbox:
        cur = w32(t, LETTERBOX_STORE)
        want = WORD_STORE_S0 if revert else WORD_STORE_WZR
        if cur == want:
            notes.append(f'  letterbox quads: already {"stock" if revert else "off"}')
        else:
            patches.append({
                'name': 'field letterbox: [0x12E60CC] = H*16/480 -> 0',
                'va': hex(LETTERBOX_STORE),
                'expect': _fmt(cur),
                'set': _fmt(want),
            })
            notes.append(f'  letterbox quads  {"restored" if revert else "OFF"} '
                         f'@ +{LETTERBOX_STORE:#09X}')

    # v9: h is ALWAYS 448 -- see the docstring. It is driven back to 448 on
    # --apply the same way [0xCFF208] is, so a module carrying v7's 480 is
    # repaired in place rather than left in a state no path produces.
    cur_f = w32(t, FRAME_HEIGHT)
    want_f = WORD_H_448
    if cur_f == want_f:
        notes.append(f'  field frame height: already '
                     f'{STOCK_FRAME_H if want_f == WORD_H_448 else OPEN_FRAME_H}')
    else:
        old = STOCK_FRAME_H if cur_f == WORD_H_448 else OPEN_FRAME_H
        new = STOCK_FRAME_H if want_f == WORD_H_448 else OPEN_FRAME_H
        patches.append({
            'name': f'field viewport height {old} -> {new}',
            'va': hex(FRAME_HEIGHT),
            'expect': _fmt(cur_f),
            'set': _fmt(want_f),
        })
        notes.append(f'  field frame h    {old} -> {new} @ +{FRAME_HEIGHT:#09X}')

    stock = revert or not field_center
    want_origin = STOCK_ORIGIN_Y if stock else CENTER_ORIGIN_Y
    new_w = ORIGIN_WORD_224 if stock else ORIGIN_WORD_232
    if movz_imm(new_w) != want_origin:
        raise AssertionError(f'origin word {new_w:08X} means {movz_imm(new_w)}, '
                             f'not {want_origin}')
    for va, name in ORIGIN_SITES:
        cur_w = w32(t, va)
        cur_v = movz_imm(cur_w)
        if cur_w == new_w:
            continue
        patches.append({
            'name': f'{name} origin_y {cur_v} -> {want_origin}',
            'va': hex(va),
            'expect': _fmt(cur_w),
            'set': _fmt(new_w),
        })
        notes.append(f'  {name} tile origin  {cur_v} -> {want_origin} @ +{va:#09X}')

    cur_s = w32(t, SPRITE_ORIGIN)
    want_s = WORD_SPR_224 if (revert or not field_center) else WORD_SPR_240
    if cur_s != want_s:
        old = 224 if cur_s == WORD_SPR_224 else 240
        new = 224 if want_s == WORD_SPR_224 else 240
        patches.append({
            'name': f'field sprite origin [0xCFF200] {old} -> {new}',
            'va': hex(SPRITE_ORIGIN),
            'expect': _fmt(cur_s),
            'set': _fmt(want_s),
        })
        notes.append(f'  sprite origin    {old} -> {new} @ +{SPRITE_ORIGIN:#09X}'
                     f'   (steam/fire travel with the background)')

    if main is not None:
        # ONE hole pool for the whole plan. Two legs can need a cave in the
        # same run, and a pool built per-leg from the on-disk image has no
        # idea what the other leg just took -- both picked +0x6DD4 and the
        # writer's own verification caught it. Share the object; HolePool
        # marks its holes used as it allocates.
        pool = _pool(main)
        # [0xCFF208] is v4's mistake and is ALWAYS driven back to 0, in every
        # mode including --apply. It is a background-tile offset, so leaving
        # it at 16 double-shifts the scenery against the origin patch above.
        patches += _plan_model_y(t, main, STOCK_MODEL_Y, notes,
                                 site=CFF208_SITE, label='[0xCFF208] (v4 defect)',
                                 pool=pool)
        # THE VIEWPORT Y IS PAIRED WITH field_center.  v9 -- v7 had this
        # inverted; see the plan() docstring for the measurement.
        #
        # ff7_field_center is +16 game units and it has THREE owners in this
        # port. They move together or not at all:
        #
        #   tile origins  224 -> 232   +8 tile units x mult 2 = +16 game units
        #   sprite origin 224 -> 240   +16 game units          [0xCFF200]
        #   viewport y      0 ->  16   +16 game units          <- this leg
        #
        # Error against the background, from ws_emu on the real words, at
        # game y 0/120/240/360/480 with the origins at 232:
        #
        #   y=0  h=448   -24 -24 -24 -24 -24    picture moved, models did not
        #   y=16 h=448     0   0   0   0   0    <- this
        #   y=0  h=480   -24 -12   0 +12 +24    v7: zero at MID SCREEN only
        #   y=16 h=480     0 +12 +24 +36 +48
        want_my = STOCK_MODEL_Y if stock else CENTER_MODEL_Y
        patches += _plan_model_y(t, main, want_my, notes,
                                 site=MODEL_Y_SITE, label='viewport y', pool=pool)
        patches += _plan_uncrop(t, main, not stock, notes, pool=pool)
        patches += _plan_fade(t, main, not stock, notes, pool=pool)
        _assert_uncrop_armed(t, patches)
        taken = [p['va'] for p in patches]
        if len(set(taken)) != len(taken):
            dup = sorted({v for v in taken if taken.count(v) > 1})
            raise RuntimeError(f'two legs claimed the same word(s): {dup}')

    return patches, notes


def _assert_uncrop_armed(t: bytes, patches: list[dict]) -> None:
    """
    v9.  The uncrop caves fire only on `y == 16 && h == 448`.

    `_uncrop_body` encodes FFNx's renderer.cpp:1667 trigger literally:

        cmp  wY, #16        csel wT, wY1, wzr, eq
        cmp  wH, #448       csel wT, wT,  wzr, eq

    v7 changed the rect to (0, 480) and left all three caves installed.  The
    condition is then never true, so 33 words of padding ran every frame and
    did nothing -- and the bars looked fixed anyway because h = 480 opens the
    rect by itself, which is what made the null result unreadable.

    This asserts the FINAL state the plan produces, not the state on disk,
    because both words are usually being written in the same run.
    """
    final = {p['va']: p for p in patches}

    def _word_after(va, cur):
        # `_fmt` emits LITTLE-ENDIAN bytes. Reading that back with int(..,16)
        # gives a byte-swapped word, which is how the first draft of this
        # check refused its own repair path. Unpack it the way it was packed.
        p = final.get(hex(va))
        if p is None:
            return cur
        return struct.unpack('<I', bytes.fromhex(p['set'].replace(' ', '')))[0]

    h_word = _word_after(FRAME_HEIGHT, w32(t, FRAME_HEIGHT))
    uncrop_on = any('uncrop' in p['name'] and 'unhook' not in p['name']
                    for p in patches) or state(t)['uncrop']
    if not uncrop_on:
        return
    if h_word != WORD_H_448:
        raise RuntimeError(
            'the uncrop caves are gated on h == 448 and the plan leaves h at '
            '480: they would be installed and dormant. This is the v7 defect.')
    y_planned = any(p['va'] == hex(MODEL_Y_SITE) for p in patches)
    if not y_planned and _read_model_y(t, MODEL_Y_SITE) != CENTER_MODEL_Y:
        raise RuntimeError(
            'the uncrop caves are gated on y == 16 and the plan leaves the '
            'viewport y at %r: they would never fire.'
            % (_read_model_y(t, MODEL_Y_SITE),))


def _plan_fade(t, main, want, notes, pool=None):
    """The fade / battle-swirl / damage-flash quad gets the whole 16:9 frame."""
    ps = []
    if want:
        try:
            import ff7nx_movieclip
            ws = ff7nx_movieclip.shipped_ws_scale(main)
        except Exception:                                       # noqa: BLE001
            ws = 0.75
        rect = fade_rect(ws)
        notes.append(f'  fade quad        WS_SCALE {ws:.8f} -> '
                     f'x {rect["x"]}, y {rect["y"]}, w {rect["w"]}, h {rect["h"]}'
                     f'   (fade to black, battle swirl, damage flash)')
    for hook, stock, guard, name in FADE_SITES:
        cur = w32(t, hook)
        on = (cur != stock)
        if on == want:
            notes.append(f'  fade quad {name}: already {"on" if want else "off"}')
            continue
        if not want:
            ps.append({'name': f'fade quad {name}: unhook', 'va': hex(hook),
                       'expect': _fmt(cur), 'set': _fmt(stock)})
            for va in _model_y_cave(t, cur, hook):
                ps.append({'name': f'fade quad {name}: clear cave word +{va:#x}',
                           'va': hex(va), 'expect': _fmt(w32(t, va)),
                           'set': '00 00 00 00'})
            notes.append(f'  fade quad {name}   removed @ +{hook:#09X}')
            continue
        import ff7nx_cave
        out, entry = ff7nx_cave.emit_hooked(
            pool if pool is not None else _pool(main),
            hook, stock, _fade_body(guard, rect[name]))
        for va in sorted(out):
            old = w32(t, va)
            if va != hook and old != 0:
                raise RuntimeError(f'fade cave word +{va:#x} is not padding')
            ps.append({'name': f'fade quad {name} +{va:#x}', 'va': hex(va),
                       'expect': _fmt(old), 'set': _fmt(out[va])})
        notes.append(f'  fade quad {name}      {guard} -> {rect[name]} '
                     f'@ +{hook:#09X}   ({len(out) - 1} word(s) in padding)')
    return ps


def _pool(main):
    import ff7nx_cave
    import nxmap
    m = nxmap.Main(str(main))
    return ff7nx_cave.HolePool(m.img, starts=set(m.arm_starts))


def _plan_uncrop(t, main, want, notes, pool=None):
    """
    The clip rect gets (0, 480) while the matrix keeps (16, 448).

    ALL THREE copies, every time. Patching a subset is what made v6 read back
    "ON" and change nothing on screen: gl_load_state and begin_scene re-derive
    the rect from the saved state every frame and overwrite whatever
    gfx_drv_setviewport computed.
    """
    ps = []
    for hook, stock, y, h, y1, acc, t_, sy, name in UNCROP_SITES:
        cur = w32(t, hook)
        on = (cur != stock)
        if on and want:
            # v10: "ALREADY ON" IS NOT THE SAME AS "ALREADY CURRENT".
            #
            # The body grew from 6 words to 10 when the battle leg went in. A
            # module carrying the OLD cave answers `on == want` and would be
            # left exactly as it is -- installed, field leg working, battle
            # leg absent, and the log cheerfully saying "already on". That is
            # the same defect `ff7nx_camclamp`'s LEGACY_LENGTHS fixed one
            # module over, and it is worth catching in the same build rather
            # than on hardware.
            body = _uncrop_body(y, h, y1, acc, t_, sy)
            chain = _model_y_cave(t, cur, hook)
            live = [w32(t, va) for va in chain if va != hook]
            live = [w for w in live if (w & 0xFC000000) != 0x14000000]
            # `emit_hooked` lays out body + the DISPLACED word, so a correct
            # chain reads one longer than the body. Getting that +1 wrong made
            # the check reject the very cave it had just written -- which is
            # why the assertion below runs on a freshly applied module too.
            got = len(live) - 1
            if got != len(body):
                raise RuntimeError(
                    'uncrop %s carries a %d-word body and this revision emits '
                    '%d. Run --revert then --apply; refusing to leave a stale '
                    'cave installed.' % (name, got, len(body)))
            notes.append(f'  uncrop {name}: already on ({got}-word body)')
            continue
        if on == want:
            notes.append(f'  uncrop {name}: already {"on" if want else "off"}')
            continue
        if not want:
            ps.append({'name': f'uncrop {name}: unhook',
                       'va': hex(hook), 'expect': _fmt(cur), 'set': _fmt(stock)})
            for va in _model_y_cave(t, cur, hook):
                ps.append({'name': f'uncrop {name}: clear cave word +{va:#x}',
                           'va': hex(va), 'expect': _fmt(w32(t, va)),
                           'set': '00 00 00 00'})
            notes.append(f'  uncrop {name}  removed @ +{hook:#09X}')
            continue
        import ff7nx_cave
        out, entry = ff7nx_cave.emit_hooked(
            pool if pool is not None else _pool(main),
            hook, stock, _uncrop_body(y, h, y1, acc, t_, sy))
        for va in sorted(out):
            old = w32(t, va)
            if va != hook and old != 0:
                raise RuntimeError(f'uncrop cave word +{va:#x} is not padding')
            ps.append({'name': f'uncrop {name} +{va:#x}', 'va': hex(va),
                       'expect': _fmt(old), 'set': _fmt(out[va])})
        notes.append(f'  uncrop {name:<20} ON @ +{hook:#09X}   '
                     f'({len(out) - 1} word(s) in padding, entry +{entry:#x})')
    if want and ps:
        # This line used to end "the matrix still sees (16,448)", stated as a
        # reassurance. It was the defect: a 480-unit clip rect over a 448-unit
        # model matrix is a scale mismatch, and it went out in several builds
        # with the two numbers printed next to each other. The rect and the
        # matrix have to agree, so the height word now moves with the caves.
        notes.append('    -> FIELD  clip rect (0,480) on ALL THREE copies; '
                     'the matrix keeps (16,448), which is both what the '
                     'caves trigger on and what puts models on the scenery')
        notes.append('    -> BATTLE clip rect (0,%d) -> the full target '
                     'height on the same three copies. The matrix is NOT '
                     'touched (_42 stays %.5f), so the scene does not move '
                     '-- it just keeps drawing below the UI band instead of '
                     'stopping at device row %d of 720.'
                     % (BATTLE_VP_H,
                        -((BATTLE_VP_Y + BATTLE_VP_H / 2.0) - 240.0) / 240.0,
                        720 * BATTLE_VP_H // 480))
    return ps


def _model_y_cave(t: bytes, hook_word: int, site: int) -> list[int]:
    """Every word of the chained cave hooked at `site`, from the hook branch."""
    imm = hook_word & 0x03FFFFFF
    if imm & (1 << 25):
        imm -= (1 << 26)
    va, seen, guard = site + imm * 4, [], 0
    while guard < 64:
        guard += 1
        w = w32(t, va)
        seen.append(va)
        if (w & 0xFC000000) == 0x14000000:
            j = w & 0x03FFFFFF
            if j & (1 << 25):
                j -= (1 << 26)
            tgt = va + j * 4
            if tgt == site + 4:                  # the return branch
                return seen
            va = tgt
            continue
        va += 4
    raise RuntimeError(f'cave chain at +{site:#x} did not terminate')


def _plan_model_y(t, main, want, notes, site=None, label=None, pool=None):
    """
    Make the `str wzr, [x0]` at `site` write `want`.

    Both sites this is used for are the identical instruction inside
    field_init_viewport_values, and w8 is dead at each -- at +0x9298EC the
    next use is +0x9298F0 `ldr w8,[x21,#0x10]`, at +0x9299D0 it is +0x9299DC
    `mov w8,#0x244`. x0 is the translated address the `bl` just returned.
    """
    site = MODEL_Y_SITE if site is None else site
    label = label or ('viewport y' if site == MODEL_Y_SITE else '[0xCFF208]')
    cur_word = w32(t, site)
    cur = _read_model_y(t, site)
    if cur == want:
        notes.append(f'  {label}: already {want}')
        return []
    if want == STOCK_MODEL_Y:
        # Unhooking is not enough: the cave words stay behind as live code in
        # padding the next allocator would refuse to reuse, and --revert would
        # not be byte-exact. Clear them too.
        ps = [{'name': f'{label}: unhook, back to 0',
               'va': hex(site), 'expect': _fmt(cur_word),
               'set': _fmt(MODEL_Y_STOCK)}]
        for va in _model_y_cave(t, cur_word, site):
            ps.append({'name': f'{label}: clear cave word +{va:#x}',
                       'va': hex(va), 'expect': _fmt(w32(t, va)),
                       'set': '00 00 00 00'})
        notes.append(f'  {label}  restored to 0 @ +{site:#09X}, '
                     f'{len(ps) - 1} cave word(s) cleared')
        return ps
    if cur_word != MODEL_Y_STOCK:
        raise RuntimeError(f'{label} is already caved; revert before changing it')
    import ff7nx_cave
    # body = `mov w8, #want`; displaced = `str w8, [x0]` -- deliberately NOT
    # the original `str wzr, [x0]`, which would zero it straight back.
    out, entry = ff7nx_cave.emit_hooked(
        pool if pool is not None else _pool(main),
        site, 0xB9000008, [0x52800000 | (want << 5) | 8])
    ps = []
    for va in sorted(out):
        old = w32(t, va)
        if va != site and old != 0:
            raise RuntimeError(f'{label} cave word +{va:#x} is not padding')
        ps.append({'name': f'{label} +{va:#x}', 'va': hex(va),
                   'expect': _fmt(old), 'set': _fmt(out[va])})
    notes.append(f'  {label}  0 -> {want} @ +{site:#09X}   '
                 f'({len(out) - 1} word(s) in padding, entry +{entry:#x})'
                 f' -- 3D models travel with the background')
    return ps


# field_center defaults ON so the library call and the CLI agree. They did not
# once: build.py called apply() with the default and silently shipped the frame
# without the centring, which is the exact half-applied state FINDINGS-88 8d
# describes. One default, one meaning.
def apply(main, revert=False, field_center=True, frame=True, letterbox=True,
          log=print) -> int:
    import nso_patcher

    main = Path(main)
    t = _text(main)

    bad = check_anchors(t, main)
    if bad:
        for b in bad:
            log('  ! ' + b)
        log('  refusing to write.')
        return 1

    patches, notes = plan(t, revert, field_center, frame=frame,
                          letterbox=letterbox, main=main)
    for n in notes:
        log(n)
    if not patches:
        log('  nothing to do -- module already in the requested state')
        return 0

    spec = {'name': 'ff7nx_letterbox', 'patches': patches}
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, spec):
        log('    ' + line)

    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.letterbox-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    t2 = _text(main)
    log('  read back from the written module:')
    st = state(t2)
    log(f'    +{LETTERBOX_STORE:#09X}  letterbox store   '
        f'{"str s0  (bars ON)" if st["bars_on"] else "str wzr (bars OFF)"}')
    log(f'    +{FRAME_HEIGHT:#09X}  field frame h     {st["frame_h"]}')
    for va, v, name in st['origins']:
        log(f'    +{va:#09X}  {name} tile origin  {v}')
    log(f'    +{SPRITE_ORIGIN:#09X}  sprite origin    {st["sprite_o"]}')
    log(f'    +{MODEL_Y_SITE:#09X}  viewport y       {st["model_y"]}   '
        f'(3D models, via d3dviewport _42)')
    log(f'    +{CFF208_SITE:#09X}  [0xCFF208]       {st["cff208"]}   '
        f'(background tile offset -- must be 0)')
    for _hk, _on, _nm in st['uncrop_sites']:
        log(f'    +{_hk:#09X}  uncrop {_nm:<20} '
            f'{"ON" if _on else "off  <- THIS COPY STILL CLIPS"}')
    for _hk, _on, _nm in st['fade_sites']:
        log(f'    +{_hk:#09X}  fade quad {_nm:<17} '
            f'{"widened" if _on else "off  <- fades stay 4:3"}')
    log('')
    _report_frame(st, log)
    return 0


def _report_frame(st: dict, log=print) -> None:
    f = bars_for(720, st['bars_off'])[0]
    h = st['frame_h'] or STOCK_FRAME_H
    vy = st['model_y'] if st['model_y'] is not None else 0
    y1, y2 = window_rows(h, 720, vy)
    scale = h / 480.0
    log(f'  predicted 720p frame:')
    log(f'    painted bars  {f:.0f} px top, {f:.0f} px bottom')
    if st.get('uncrop'):
        log('    clip rect     rows 0..719   (uncrop: all 3 copies forced open)')
    elif st.get('uncrop_part'):
        log('    clip rect     PARTIAL -- a copy still clips; bars WILL show')
    else:
        r1, r2 = window_rows(h, 720, vy)
        log(f'    clip rect     rows {r1}..{r2}   '
            f'-> {r1} px BAR top, {720 - r2} px BAR bottom')
    log(f'    background    rows 0..719   (2D ortho, clipped only by the rect)')
    log(f'    3D models     rows {y1}..{y2}   '
        f'(viewport y={vy} h={h}: _42 -> centre, _22 = {scale:.5f})')

    # MODEL-TO-BACKGROUND ALIGNMENT, stated as a number rather than left for
    # someone to notice on a ladder.
    #
    # v9: THE BACKGROUND TERM WAS MISSING, and it is the whole bug.
    #
    # This block used to compare the model row against `1.5 * gy`, i.e. the
    # background at ORIGIN 224. Every shipped build puts it at 232. So the
    # check was blind to exactly the defect it was written to catch, and it
    # printed "+0 at every screen height: ladders included" over a build in
    # which a ladder is 24 px out at the top of the screen and 24 px the
    # other way at the bottom.
    #
    # The origin is disassembled, not assumed: +0xA06EA8 is `mov w9, #0xe0`
    # feeding `sub w8, w9, w8` -- dst_y = (ORIGIN - tile.y) * mult, mult = 2,
    # and 720 device rows over 480 game units is 1.5 px per unit. So moving
    # the origin by one unit moves the picture by 3 device rows, flat.
    # Both sides are DELTAS AGAINST STOCK. The absolute correspondence
    # between a model's frame y and the background's game y is not known
    # from this module -- but it does not have to be. Stock is aligned
    # (the 4:3 game shipped), so preserving the relationship is sufficient
    # and is the only claim the numbers here can actually support.
    origin = st['origins'][0][1] if st.get('origins') else STOCK_ORIGIN_Y
    bg_shift = (origin - STOCK_ORIGIN_Y) * 2 * 1.5

    def _row(_y, _h, gy):
        _42 = -((_y + _h / 2.0) - 240.0) / 240.0
        return (1.0 - ((_h / 480.0) * (1.0 - gy / 240.0) + _42)) / 2.0 * 720.0

    errs = [(gy, (_row(vy, h, gy) - _row(0, STOCK_FRAME_H, gy)) - bg_shift)
            for gy in (0, 120, 240, 360, 480)]
    worst = max(abs(e) for _gy, e in errs)
    log(f'    background    tile origin {origin} -> the picture sits '
        f'{bg_shift:+.0f} device rows vs stock, flat at every height')
    log('    model vs background, in device rows, at game y '
        '0 / 120 / 240 / 360 / 480:')
    log('      %s' % '  '.join('%+.0f' % e for _gy, e in errs))
    if worst < 0.5:
        log('      -> 0 at every screen height: models sit ON the scenery '
            'wherever a character stands, ladders included')
    else:
        log(f'      ! up to {worst:.0f} px out -- models do NOT stand on the '
            f'scenery. With the tile origins at {origin}, the only pair that '
            f'is flat zero at every height is viewport y=16 with h=448, '
            f'which is also FFNx\'s (ff7_opengl.cpp:312) and is the pair the '
            f'uncrop caves trigger on. h=480 is zero at MID SCREEN ONLY: '
            f'flat ground cannot see it and a LADDER can.')
    if (y1, y2) == (24, 696):
        log('    -> the model band is the movie band (24..696); note '
            'ff7nx_moviebars covers rows 0..24 and 696..720 during an FMV '
            'anyway, so this is not a reason to keep it')
    o = st['origins'][0][1]
    log(f'    tile window   [bg.y-{o}, bg.y+{240 - o + 224 - 224 if False else 240 - o}]'
        f'   vs the camera clamp bg.y <= range.bottom-8')
    if o == CENTER_ORIGIN_Y:
        log('    -> [bg.y-232, bg.y+8] == the clamp exactly: cannot run past the art')
    else:
        log('    -> [bg.y-224, bg.y+16] runs 8 tile units (24 px) past the art '
            'at the clamp')


def show(main, log=print) -> int:
    t = _text(main)
    st = state(t)
    log(f'  {main}')
    log(f'    +{LETTERBOX_STORE:#09X}  {_fmt(st["store"])}  '
        f'{"str s0, [x10,#0xcc]   BARS ON  (stock)" if st["bars_on"] else ""}'
        f'{"str wzr,[x10,#0xcc]   BARS OFF (patched)" if st["bars_off"] else ""}')
    log(f'    +{FRAME_HEIGHT:#09X}  {_fmt(st["frame"])}  field viewport h = '
        f'{st["frame_h"]}'
        f'{"   (stock)" if st["frame_h"] == STOCK_FRAME_H else "   <- opened"}')
    for va, v, name in st['origins']:
        tag = '' if v == STOCK_ORIGIN_Y else '   <- ff7_field_center'
        log(f'    +{va:#09X}  {name} tile origin  {v}{tag}')
    log(f'    +{SPRITE_ORIGIN:#09X}  {_fmt(st["sprite"])}  sprite origin [0xCFF200] = '
        f'{st["sprite_o"]}'
        f'{"" if st["sprite_o"] == 224 else "   <- ff7_field_center"}')
    log(f'    +{MODEL_Y_SITE:#09X}  {_fmt(st["model_word"])}  set_field_viewport y = '
        f'{st["model_y"]}'
        f'{"" if st["model_y"] == STOCK_MODEL_Y else "   <- ff7_field_center"}')
    log(f'    +{CFF208_SITE:#09X}  {_fmt(st["cff208_word"])}  [0xCFF208] = '
        f'{st["cff208"]}'
        f'{"" if st["cff208"] == 0 else "   <- v4 DEFECT, tiles shifted twice"}')
    for _hk, _on, _nm in st['uncrop_sites']:
        log(f'    +{_hk:#09X}  {_fmt(w32(t, _hk))}  uncrop {_nm:<20} '
            f'{"ON" if _on else "off  <- this copy clips to 16..464"}')
    for _hk, _on, _nm in st['fade_sites']:
        log(f'    +{_hk:#09X}  {_fmt(w32(t, _hk))}  fade quad {_nm:<17} '
            f'{"widened" if _on else "off  <- fade/swirl/flash stay 4:3"}')
    log('')
    _report_frame(st, log)
    log('')
    bad = check_anchors(t, main)
    for b in bad:
        log('    ! ' + b)
    log('    anchors: ' + ('OK' if not bad else f'{len(bad)} FAILED'))
    return 1 if bad else 0


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------
LETTERBOX_ENV = 'SEVENTH_NX_FIELD_FRAME'


def enabled() -> bool:
    """
    ON with 16:9, OFF at 4:3, overridable with SEVENTH_NX_FIELD_FRAME.

    At 4:3 the 448-of-480 letterbox is the framing FF7 was authored in, and
    the port paints it deliberately. Opening it there would show 32 game units
    the composition never expected to be seen, on every field, with no
    widescreen mod to have provided art for it. FFNx makes the same call: its
    uncrop helpers are all reached through `is_fieldmap_wide()`, which is
    `enable_uncrop && widescreen.getMode() != WM_DISABLED`.
    """
    v = os.environ.get(LETTERBOX_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false')
    try:
        import ff7nx_ws
        return ff7nx_ws.enabled()
    except Exception:
        return False


def verify(main=None, log=print) -> int:
    fails = []

    def ck(cond, what):
        log(f'    {"ok  " if cond else "FAIL"}  {what}')
        if not cond:
            fails.append(what)

    log('  arithmetic (no module needed):')
    ck(abs(720 * 16 / 480 - 24.0) < 1e-9, '720p  -> 24.0 px  (matches both captures)')
    ck(abs(1080 * 16 / 480 - 36.0) < 1e-9, '1080p -> 36.0 px')
    f, top, bot = bars_for(720, patched=False)
    ck((round(top), round(bot) - 1) == (24, 695),
       'stock 720p content rows 24..695  (hardware: 24..695)')
    f2, top2, bot2 = bars_for(720, patched=True)
    ck((round(top2), round(bot2)) == (0, 720), 'patched 720p content rows 0..719')
    ck(WORD_STORE_WZR != WORD_STORE_S0, 'the replacement word differs from stock')
    ck(window_rows(448) == (0, 672),
       'clip window at h=448 is rows 0..672   (hardware, bars off: 0..672)')
    ck(window_rows(480) == (0, 720), 'clip window at h=480 is rows 0..720')
    ck(window_rows(448, 1080) == (0, 1008), 'and 0..1008 of 1080 docked')
    ck(240 - CENTER_ORIGIN_Y == 8 and CENTER_ORIGIN_Y - 224 == 8,
       'origin 232 splits the extra 16 units as 8 above / 8 below')
    ck(240 - STOCK_ORIGIN_Y == 16, 'origin 224 puts all 16 extra units below')
    ck((CENTER_ORIGIN_Y - STOCK_ORIGIN_Y) * 2 == 16 and 240 - 224 == 16,
       'both PICTURE legs move the same +16 game units: tiles +8x2, '
       'sprites +16')

    # THE INVARIANT, WITH THE TERM v7 LEFT OUT.
    #
    # v7's version of this compared the model row against `1.5 * gy` -- the
    # background at ORIGIN 224 -- while every build ships 232. So the one
    # term that makes this a bug was the term the invariant omitted, and it
    # certified the broken pair and condemned the correct one. It is not
    # enough for a check to sample five heights if it samples them against
    # the wrong picture.
    #
    # `origin` is a REQUIRED argument now. There is no default, deliberately:
    # a default is how the term went missing in the first place.
    #
    # AND IT IS A DELTA AGAINST STOCK, NOT AN ABSOLUTE.  The first draft of
    # this fix compared the model row against `1.5 * gy + bg_shift` -- the
    # absolute form, just with the missing term restored -- and the very
    # last assertion below caught it: STOCK does not satisfy it. Stock puts
    # models on 1.4*gy and the background on 1.5*gy, and stock is aligned,
    # because a model's `gy` and the background's game y are NOT the same
    # coordinate. The viewport is what converts one into the other.
    #
    # So the only thing that can be asserted without re-deriving the whole
    # projection is that the RELATIONSHIP is unchanged from the 4:3 game,
    # which shipped and whose characters stand on its scenery. Both sides
    # are deltas; the unknown absolute cancels.
    def _model_row(vy, h, gy):
        s = h / 480.0
        m42 = -((vy + h / 2.0) - 240.0) / 240.0
        return (1.0 - (s * (1.0 - gy / 240.0) + m42)) / 2.0 * 720.0

    def _align_err(vy, h, origin):
        bg = (origin - STOCK_ORIGIN_Y) * 2 * 1.5      # flat, from +0xA06EA8
        return [(_model_row(vy, h, gy) - _model_row(0, STOCK_FRAME_H, gy)) - bg
                for gy in (0, 120, 240, 360, 480)]

    # Stated through the CONSTANTS the planner uses, not through literals,
    # so that changing a constant and leaving the reasoning behind -- which
    # is what v7 did -- fails here instead of on hardware.
    ck(max(abs(e) for e in
           _align_err(CENTER_MODEL_Y, STOCK_FRAME_H, CENTER_ORIGIN_Y)) < 1e-9,
       f'the shipped triple (origin {CENTER_ORIGIN_Y}, viewport y '
       f'{CENTER_MODEL_Y}, h {STOCK_FRAME_H}): models land on the background '
       f'at all five screen heights -- and it is FFNx\'s pair too, '
       f'ff7_opengl.cpp:312')
    ck(OPEN_FRAME_H not in (STOCK_FRAME_H,)
       and max(abs(e) for e in
               _align_err(CENTER_MODEL_Y, OPEN_FRAME_H, CENTER_ORIGIN_Y)) > 1.0
       and max(abs(e) for e in
               _align_err(STOCK_MODEL_Y, OPEN_FRAME_H, CENTER_ORIGIN_Y)) > 1.0,
       f'and NEITHER viewport y is right at h={OPEN_FRAME_H} -- h is not a '
       f'free knob that y can be tuned against, which is the mistake v7 made')
    for vy, h, worst in ((0, 448, 24.0), (0, 480, 24.0), (16, 480, 48.0)):  # noqa: E501
        got = max(abs(e) for e in _align_err(vy, h, CENTER_ORIGIN_Y))
        ck(abs(got - worst) < 1e-9,
           f'origin 232, y={vy} h={h} is {got:.0f} px out at worst -- the '
           f'check can fail, so passing above means something')
    e480 = _align_err(0, 480, CENTER_ORIGIN_Y)
    ck(abs(e480[2]) < 1e-9 and abs(e480[0] + 24.0) < 1e-9
       and abs(e480[4] - 24.0) < 1e-9,
       'v7\'s pair (y=0 h=480) is EXACTLY 0 at mid screen and 24 px out at '
       'each end -- flat ground cannot see it, a LADDER can, and that is the '
       'reported symptom')
    ck(max(abs(e) for e in _align_err(0, 448, STOCK_ORIGIN_Y)) < 1e-9,
       'and the wholly stock pair is zero too, which is why the 4:3 game '
       'shipped looking right -- the invariant reproduces known-good')
    for w, v, rd, name in _WORD_TABLE:
        ck(_decode_movz(w) == (v, rd),
           f'{name}: {w:08X} really is mov w{rd}, #{v}')
    ck(movz_imm(ORIGIN_WORD_224) == 224 and movz_imm(ORIGIN_WORD_232) == 232,
       'the two origin words decode to 224 and 232')

    if main is None:
        log('')
        log(f'  {len(fails)} failure(s)' if fails else '  all checks pass')
        return 1 if fails else 0

    t = _text(main)
    log('')
    log('  against the module:')
    for b in check_anchors(t, main):
        ck(False, b)
    if not fails:
        ck(True, 'mode-2 branch signature intact (4 words)')
        ck(True, 'jump table entry 2 -> the letterbox branch')
        ck(True, '.rodata constant is exactly 16/480')
        ck(True, 'all other modes store zero (mode 5 inherits -- documented)')
        ck(True, 'donor word +0x10F3E18 is str wzr,[x10,#0xcc]')
        ck(True, 'all four layer origins are 224 or 232')
        ck(True, 'setviewport arg block intact (h store, 640, x=0, bl)')
        ck(True, 'sprite origin [0xCFF200] is 224 or 240')
        ck(True, 'viewport y and [0xCFF208] are both 0 or a readable cave')

    readers = _matrix_readers(t)
    ck(readers != [],
       f'd3dviewport matrix is USED ({len(readers)} site(s): '
       f'{[hex(r) for r in readers]}) -- leg three can reach models')
    ck(0x10DA0B0 in readers,
       'the pointer form +0x10DA0B0 add x1,x27,#0xa8 is present '
       '(v4 missed this and shipped h=480 as "safe")')
    ck(w32(t, MODEL_Y_SITE + 4) == 0xB94012A8,
       'w8 is dead at +0x9298EC: next use is +0x9298F0 ldr w8,[x21,#0x10]')
    ck(w32(t, 0x9298E8) & 0xFC000000 == 0x94000000,
       '+0x9298E8 is the bl that leaves the target address in x0')

    # the patch is a pure state toggle -- prove plan() is an involution
    st = state(t)
    p_off, _ = plan(t, revert=False, field_center=False)
    p_on, _ = plan(t, revert=True, field_center=False)
    ck(st['bars_on'] != st['bars_off'], 'module is in exactly one known state')
    ck(st['frame_h'] in (STOCK_FRAME_H, OPEN_FRAME_H),
       f'field frame height is {st["frame_h"]}')
    ck(bool(p_off) != bool(p_on) or (p_off and p_on),
       'the two directions are not both no-ops')

    # mutation: a wrong signature must be refused
    for name, va, word in (
            ('mutated mode-2 signature', MODE2_SIG[0][0], 0xD503201F),
            ('missing donor word', LETTERBOX_ZERO, 0xD503201F),
            ('origin of 240 (only 224/232)', ORIGIN_SITES[0][0], 0x52801E08),
            ('moved setviewport arg block', FRAME_SIG[0][0], 0xD503201F),
            ('unknown frame height', FRAME_HEIGHT, 0x52801E08),
            ('unknown sprite origin', SPRITE_ORIGIN, 0x52800008),
            # v5: the two sites that swapped roles, and the matrix pointer.
            ('viewport y neither str wzr nor a branch', MODEL_Y_SITE, 0xD503201F),
            ('[0xCFF208] neither str wzr nor a branch', CFF208_SITE, 0xD503201F),
            ('the d3dviewport pointer erased', 0x10DA0B0, 0xD503201F),
            ('uncrop hook is neither stock nor a branch', UNCROP_HOOK, 0xD503201F),
            ('the device-y divide moved', UNCROP_SIG[0][0], 0xD503201F),
            ('the y1 pack moved', UNCROP_SIG[2][0], 0xD503201F),
            ('h no longer reaches the matrix', UNCROP_SIG[3][0], 0xD503201F),
    ):
        mut = bytearray(t)
        struct.pack_into('<I', mut, va, word)
        ck(bool(check_anchors(bytes(mut), main)), f'{name} is refused')

    # v5: the three legs must land on ONE distance, and the model band must
    # land on the movie band. This is the check that would have caught v4:
    # it put +16 into a leg that was already carrying +16 from the origins.
    ck(window_rows(448, 720, 16) == (24, 696),
       'viewport (y=16, h=448) puts models on device rows 24..696')
    ck(window_rows(448, 720, 0) == (0, 672),
       'and stock (y=0, h=448) puts them on 0..672, top-aligned')
    ck(window_rows(480, 720, 0) == (0, 720),
       'h=480 fills the frame -- but at _22 = 1.0, a 1.0714x stretch')
    ck(abs(448 / 480 - 0.93333333) < 1e-6,
       '_22 at h=448 is 448/480 = 0.93333, which is what FF7 authored')
    ck(window_rows(448, 720, 16) == (24, 696) and 720 * 16 // 480 == 24,
       'the model band and the shifted movie quad share the same 24 px inset')

    # v10: THE BATTLE RECT, and why the height is not simply raised to 480.
    def _m42(y, h):
        return -((y + h / 2.0) - 240.0) / 240.0
    ck(abs(_m42(BATTLE_VP_Y, BATTLE_VP_H) - 0.3083333) < 1e-6,
       f'battle ({BATTLE_VP_Y},{BATTLE_VP_H}) has _42 = '
       f'{_m42(BATTLE_VP_Y, BATTLE_VP_H):.5f}, NOT zero -- the rect is not '
       f'centred, so h is not free to move')
    ck(abs(_m42(0, 480)) < 1e-9,
       'and h=480 would make _42 = 0, i.e. FFNx\'s fix MOVES this port\'s '
       'battle scene by %.0f device rows'
       % (_m42(BATTLE_VP_Y, BATTLE_VP_H) / 2 * 720))
    ck(720 * BATTLE_VP_H // 480 == 498,
       f'battle draws {720 * BATTLE_VP_H // 480} of 720 rows at 720p, which '
       f'is where the hardware capture\'s UI band starts')
    ck(BATTLE_VP_Y == 0,
       'the battle rect starts at y=0, so the leg only has to extend the '
       'BOTTOM -- a symmetric open would be a no-op')
    for _hk, _st, _y, _h, _y1, _acc, _t, _sy, _nm in UNCROP_SITES:
        _ld = dict(UNCROP_SIG)[SCALE_LOADS[_hk]]
        ck((_ld & 0x1F) == _sy,
           f'{_nm}: the battle leg reads w{_sy} and the module loads scale_y '
           f'into w{_ld & 0x1F} -- declared register matches the image')
        ck(_sy not in (_y, _h, _y1, _acc, _t),
           f'{_nm}: scale_y w{_sy} does not collide with y/h/y1/acc/tmp')

    # v7: execute EVERY copy's cave body as the hardware would, on every rect
    # the driver is handed. Each must open (16,448) to the full frame and be
    # byte-identical on everything else. Patching a subset is the v6 defect.
    def _run(body, displaced, screen, y, h, ymap):
        R = dict(ymap)
        R[R['_y']] = y
        R[R['_h']] = h
        R[R['_y1']] = screen * y // 480
        R[R['_acc']] = screen * h // 480
        Z = False
        for wrd in body + [displaced]:
            if (wrd & 0xFF000000) == 0x71000000:                  # cmp wN,#imm
                Z = (R[(wrd >> 5) & 31] == ((wrd >> 10) & 0xFFF))
            elif (wrd & 0xFFE00000) == 0x1A800000:                # csel wD,wN,wM,eq
                # v10: the ELSE OPERAND IS A REGISTER, not always wzr. It was
                # hardcoded to 0 here, which was correct for the field leg and
                # silently wrong for the battle one -- an emulator that cannot
                # express the instruction cannot test it.
                rn, rm = (wrd >> 5) & 31, (wrd >> 16) & 31
                pick = rn if Z else rm
                R[wrd & 31] = 0 if pick == 31 else R[pick]
            elif (wrd & 0xFF000000) == 0x0B000000:                # add wD,wN,wM,lsl#s
                R[wrd & 31] = R[(wrd >> 5) & 31] + (R[(wrd >> 16) & 31] << ((wrd >> 10) & 63))
            elif (wrd & 0xFF000000) == 0x4B000000:                # sub wD,wN,wM
                R[wrd & 31] = R[(wrd >> 5) & 31] - R[(wrd >> 16) & 31]
            else:
                raise AssertionError(f'unmodelled word {wrd:08X}')
        return R[R['_y1']], R[R['_acc']]

    for _hook, _stock, _y, _h, _y1, _acc, _t, _sy, _name in UNCROP_SITES:
        body = _uncrop_body(_y, _h, _y1, _acc, _t, _sy)
        for screen in (720, 1080, 1440):
            # `_sy` really is the render target height at every hook, so the
            # emulator seeds it with the screen and the battle leg is tested
            # at three resolutions rather than at 720p only.
            ymap = {'_y': _y, '_h': _h, '_y1': _y1, '_acc': _acc,
                    _t: 0, _sy: screen}
            ck(_run(body, _stock, screen, 16, 448, ymap) == (0, screen),
               f'{_name} @ {screen}p: FIELD (16,448) -> 0..{screen}, no bars')
            ck(_run(body, _stock, screen, BATTLE_VP_Y, BATTLE_VP_H, ymap)
               == (0, screen),
               f'{_name} @ {screen}p: BATTLE ({BATTLE_VP_Y},{BATTLE_VP_H}) '
               f'-> 0..{screen}, drawn to the bottom')
            for yy, hh in ((0, 480), (0, 448), (24, 432), (16, 480), (0, 224),
                           (0, 333), (1, 332), (16, 332), (0, 331)):
                want = (screen * yy // 480,
                        screen * hh // 480 + screen * yy // 480)
                ck(_run(body, _stock, screen, yy, hh, ymap) == want,
                   f'{_name} @ {screen}p: ({yy},{hh}) byte-identical')
        for wrd in body:
            ck((wrd >> 26) not in (0x05, 0x25) and (wrd & 0x1F000000) != 0x10000000,
               f'{_name}: no branch or adrp in the body (hole-scatter safe)')
    # v8: the fade quad. Execute each site's body the way the hardware would.
    def _fade_run(body, guard, val):
        R = {21: val, 9: 0}
        Z = False
        for wrd in body:
            if (wrd & 0xFF000000) == 0x71000000:
                Z = (R[(wrd >> 5) & 31] == ((wrd >> 10) & 0xFFF))
            elif (wrd & 0xFFE00000) == 0x52800000:
                R[wrd & 31] = (wrd >> 5) & 0xFFFF
            elif (wrd & 0xFFE00000) == 0x12800000:
                R[wrd & 31] = -(((wrd >> 5) & 0xFFFF) + 1)
            elif (wrd & 0xFFE00000) == 0x1A800000:
                # Wd = cond ? Wn : Wm   -- Wn is 9:5, Wm is 20:16, wzr == 31
                rn, rm = (wrd >> 5) & 31, (wrd >> 16) & 31
                pick = rn if Z else rm
                R[wrd & 31] = 0 if pick == 31 else R[pick]
            else:
                raise AssertionError(f'unmodelled fade word {wrd:08X}')
        return R[21]

    for _s, _want in ((0.75, {'x': -107, 'y': 0, 'w': 854, 'h': 480}),
                      (0.74941452, {'x': -107, 'y': 0, 'w': 854, 'h': 480}),
                      (0.74766355, {'x': -108, 'y': 0, 'w': 856, 'h': 480})):
        ck(fade_rect(_s) == _want,
           f'WS_SCALE {_s:.8f} -> fade rect {_want} (rounded OUT, = FFNx at 0.75)')
    _rect = fade_rect(0.75)
    for _hook, _stock, _guard, _nm in FADE_SITES:
        _body = _fade_body(_guard, _rect[_nm])
        ck(_fade_run(_body, _guard, _guard) == _rect[_nm],
           f'fade quad {_nm}: the field rect {_guard} -> {_rect[_nm]}')
        for _other in (0, 16, 224, 240, 448, 480, 640, 854, 1234):
            if _other == _guard:
                continue
            ck(_fade_run(_body, _guard, _other) == _other,
               f'fade quad {_nm}: {_other} is left alone (guarded)')
        for _wrd in _body:
            ck((_wrd >> 26) not in (0x05, 0x25) and (_wrd & 0x1F000000) != 0x10000000,
               f'fade quad {_nm}: no branch or adrp in the body')
    try:
        import capstone as _cs
        _md = _cs.Cs(_cs.CS_ARCH_ARM64, _cs.CS_MODE_LITTLE_ENDIAN)
        for _hook, _stock, _guard, _nm in FADE_SITES:
            _txt = [f'{i.mnemonic} {i.op_str}' for i in _md.disasm(
                b''.join(struct.pack('<I', x) for x in _fade_body(_guard, _rect[_nm])), 0)]
            _sel = [x for x in _txt if x.startswith('csel')]
            ck(len(_sel) == 1 and _sel[0].split(',')[1].strip() != 'w21',
               f'fade quad {_nm}: csel takes the NEW value on match '
               f'({_sel[0] if _sel else "no csel"})')
    except ImportError:
        ck(True, 'capstone absent -- csel operand order not cross-checked')
    ck(len(FADE_SITES) == 4 and len({h for h, *_ in FADE_SITES}) == 4,
       'all four fade-quad fields patched, at four distinct sites')

    ck(len(UNCROP_SITES) == 3,
       'all three copies of the rect arithmetic are covered, not just the '
       'live one -- gl_load_state and begin_scene overwrite it every frame')
    log('')
    log(f'  {len(fails)} failure(s)' if fails else '  all checks pass')
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('main', nargs='?', help='path to exefs/main')
    ap.add_argument('--apply', action='store_true',
                    help='bars off + ff7_field_center + h 480 (models match '
                         'the 480-unit background)')
    ap.add_argument('--revert', action='store_true', help='back out everything')
    ap.add_argument('--revert-frame', action='store_true',
                    help='back out the frame height only, keep the bars off')
    ap.add_argument('--no-frame', action='store_true',
                    help='keep h at 448 and put the viewport y back to 16 -- '
                         'the v5 pair. MEASURED to misalign models by +24 px '
                         'at the top of the screen through 0 in the middle to '
                         '-24 px at the bottom. A/B only.')
    ap.add_argument('--open-frame', action='store_true',
                    help=argparse.SUPPRESS)      # now the default
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--no-center', action='store_true',
                    help='leave the origins at 224 (bar returns at the camera clamp)')
    ap.add_argument('--field-center', action='store_true',
                    help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    if a.verify or not (a.apply or a.revert or a.show or a.revert_frame):
        print('ff7nx_letterbox -- painted bars, then a 448-of-480 clip')
        print('')
        rc = verify(a.main, log=print)
        print('')
        predict()
        return rc
    if a.show:
        return show(a.main)
    if not a.main:
        ap.error('need a path to exefs/main')
    centered = not a.no_center
    if a.revert_frame:
        return apply(a.main, revert=False, field_center=centered,
                     frame=False, letterbox=False)
    return apply(a.main, revert=a.revert, field_center=centered,
                 frame=not a.no_frame)


if __name__ == '__main__':
    raise SystemExit(main())
