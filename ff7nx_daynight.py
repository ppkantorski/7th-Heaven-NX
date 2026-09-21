#!/usr/bin/env python3
"""
ff7nx_daynight.py -- Echo-S's Day/Night cycle, the half FFNx supplies.

WHAT THE MOD ACTUALLY SHIPS, MEASURED
=====================================
Echo-S's Day/Night add-on is two files: an FFNx `config.toml` and a Hext patch
for the menu. In the FIELD SCRIPTS it appends one actor, literally named
`Time`, and all 703 shipped fields were decoded with `echo_s_flevel`'s own
`instruction_size` to see what is in it. Two routines, and only the first
differs between fields:

    init   256 field(s)   82 10 0A 02   BITON  bank1[0x0A], bit 2   outdoor
           444 field(s)   83 10 0A 02   BITOFF bank1[0x0A], bit 2   indoor
    main   identical in every field: the month-length calendar, twelve
           `IFUB month == n / IFUB day == <length>` pairs rolling the date

So the `Time` actor's per-field contribution is the outdoor flag, and nothing
else. But the mod DOES set the time of day -- just from ordinary actors, which
is why looking only at `Time` missed it. Instruction-aligned across all 703:

    every inn   Sleep/…   85 16 0F 08   hours += 8    sleeping advances time
    mds7_w3     Sleep     85 16 0F 07   hours += 7
    rktinn1     Sleep     85 16 0F 0B   hours += 11
    mds7pb_1    Tifa      80 10 0F 0A   hours = 10    the pillar
    nivgate     a_drct    80 10 0F 16   hours = 22    Nibelheim, at night
    nivinn_2    tabuti    80 10 0F 08   hours = 8
    elminn_2    ballet    80 10 0F 07   hours = 7
    tin_1       direct    85 10 0F 03   hours += 3

Twenty inns and a dozen story fields. None of it is in the `Time` actor, so
none of it is stripped, and this module re-reads the savemap every frame
rather than caching -- so a script write lands on the next frame. Note the
bare adds: PC relies on `Time::update`'s cascade to wrap them, which is why
`_emit_clock` carries rather than judges (see BUILD-478).

The clock and the colour are FFNx code, and this module is that code.

`echo_s_flevel` strips that actor, and keeps stripping it. The stated reason
-- "PC-specific field-bank addresses" -- turned out to be wrong: bank 1 is
ordinary scriptable game data and its base is confirmed in the binary at
`mov byte ptr [eax + 0xdc08dc], cl` (x86 0x5D97CB, one of 83 sites). But the
OTHER reason is real and unchanged: the actor count feeds the native field
object's array, and an extra actor moves that layout. So the flag does not
come from the script here. It comes from a build-time bitmap keyed by field
id, read out of flevel's own `maplist`, and the runtime sets the bit itself.

WHERE THE CLOCK LIVES
=====================
Exactly where the mod's config says, because it is the same binary:

    bank 1 base   0xDC08DC
     +0x0A  0xDC08E6  options   bit0 enabled, bit1 advance, bit2 outdoor
     +0x0C  0xDC08E8  minutes
     +0x0D  0xDC08E9  months
     +0x0E  0xDC08EA  days
     +0x0F  0xDC08EB  hours

Keeping the clock in those exact bytes is what lets Echo-S's Hext calendar
patch work unmodified if it is ever ported, and it is what makes the clock
survive a save, since bank 1 is in the savemap.

HOW THE TINT REACHES THE SCREEN
===============================
FFNx owns its renderer and multiplies in its own fragment shader:

    if (isTimeFilterEnabled) color.rgb *= TimeColor.rgb;    // FFNx.frag:162

This port's equivalent is `colortex_p.glsl`, doing `texture(Sampler0, uv) *
vColor`, and its vertex shaders declare:

    layout(binding=0) uniform BlockVertex {
        ivec4 blendMode;
        layout(column_major) mat4 projectionMatrix;
    };

`blendMode` is an ivec4 and **only `.x` is ever read**. `.y` and `.z` are two
free integers already being uploaded on every draw.

WHERE THAT BLOCK IS BUILT -- AND WHY EVERY EARLIER ATTEMPT MISSED IT
====================================================================
Builds 466-473 all tried to reach those lanes at the GL layer, by hooking the
`glBufferData(GL_UNIFORM_BUFFER, ...)` helper at +0x1132A60 and then guessing
which upload was the scene's: by size (469/470 -- wrote into hq4x/2xSaI
parameters, black screen), by a one-shot flag set near shader-program setup
(472 -- tagged an unrelated allocation, corrupted textures), and finally by
the GL buffer NAME captured at the `BlockVertex` bind (473 -- did nothing at
all, because the bind at +0x1136AD0 asks for `handles[w4]` with w4 = 0 while
the uploader at +0x1132A7C uses `handles[obj->0x34]`, its own rotating ring
index; the two names are not the same object slot).

None of that guessing is necessary, because the block is assembled in plain
sight by the port's ONE draw-submission function, `+0x10D9D70`, which every
`gfx_drv_draw_*` entry funnels into (gfx table 169-185):

    +0x10D9FF4   str  w8, [sp, #0x18]        blendMode.x
      sp+0x1C, sp+0x20, sp+0x24              blendMode.y/.z/.w -- NEVER written
    +0x10DA0B8   (matrix multiply)  ->  sp+0x28 .. sp+0x68   projectionMatrix
    +0x10DA260   add  x1, sp, #0x18          <- the 80 bytes, as one argument
    +0x10DA274   bl   +0x570                 copies them into a ring buffer
                                             and calls the uploader itself
    +0x10DA290   mov  w6, #0x50              80, the size, as a literal

`sp+0x18 .. sp+0x68` IS `BlockVertex`: 0x50 bytes, ivec4 then mat4, and every
path through the function converges on +0x10DA254 before it is handed over
(+0x10DA094, +0x10DA548 and the fall-through all land there). So:

  * `CAVE_BLOCK` sits at +0x10DA260 and writes the packed 0xRRGGBB tint into
    `sp+0x1C` and a magic into `sp+0x20`, in the caller's own stack frame,
    before the copy. No buffer identity, no size test, no timing window;
  * `custom_shaders/wide_screen/{l,tl}main_vv.glsl` multiply `vColor.rgb` by
    it, but only when the magic matches.

This also makes the corruption class structurally impossible. The 16-byte
2xSaI/hq4x `BlockVertex` and every `BlockFragment` are built and uploaded by
other code entirely; this cave cannot see them.

The magic is the safety property: the shader without the module is identity,
and the module without the shader draws nothing different. Neither half can
break a card on its own.

What travels in that lane is NOT FFNx's `TimeColor`. FFNx multiplies it in
linear light between a `toLinear` and a `toGamma`; this port's shaders
multiply in gamma space and have no transfer function at all, so the cave
converts -- see `GAMMA` below for the derivation and the measured error.

THE ON/OFF DISCIPLINE
=====================
FFNx enables the filter for the scene and turns it off again before the UI is
drawn, in a hook on the call to `field_draw_gray_quads_644E90`. Same two
points here, and for the same reason: a tinted dialogue box looks like a bug.

    FIELD    CAVE_TICK   field_draw_everything    arm
             CAVE_CLEAR  after `bl gray_quads`    white
    BATTLE   CAVE_FLIP   gfx_drv_flip             arm, if bit 2 is set
             battle_text the text-UI call         white, call, arm again
             battle_box  the box-UI call          white, call, arm again
             battle_menu the menu marker          white, and stay white
    WORLD    CAVE_FLIP   gfx_drv_flip             arm, bit 2 not consulted
             world_off1  minimap quad draw        white
             world_on    world effects draw       arm again
             world_off2  minimap points draw      white

The three modes are the three `Time::update` ticks in, and every one of those
points is FFNx's own, resolved through the port's x86 -> ARM64 map rather
than guessed -- see the block above `WORLD_DRAW_ALL`.

In FIELD `CAVE_FLIP` whitens rather than arming, which keeps the belt-and-
braces property it was added for: if `field_draw_everything` ever returns
without reaching its gray quads, the leak is bounded to one frame instead of
lasting until the next field.

HIGHWIND'S WINDOW SKY -- NOT SOMETHING FFNx DOES
================================================
The Highwind's interiors are indoor fields, so Echo-S's outdoor bitmap keeps
the ordinary filter white in them and always will. But the big window is a
LAYER 3 parallax, and layer 3 on those maps binds pages nothing else binds:
on `fship_22` the sky is pages 4..14 while the hull, the deck, the window
frame and the actors are on 2, 26, 27 and 28. So the sky can be given the
clock on its own.

`sub_640213` (arm +0x9F30F0, x86 0x640213) draws exactly one background page
per iteration and has the page number sitting in `[x19]` eight instructions
above its draw call. `CAVE_SKY_PAGE` wraps that call: it arms a private
selector for pages inside this field's layer-3 range, lets the page draw, and
clears it again the instant the draw returns. `CAVE_BLOCK` -- the one
transport everything else already uses -- reads the selector and picks the
sky's own tint instead of the field's white.

THE PAGE NUMBERS ARE MEASURED, NOT REMEMBERED. This project repacks field
background pages (`field_bg_pagecap` deals fresh slots round-robin), so a
range carried from one build could land on the hull in another. `sky_plan`
reads the flevel actually being shipped and, per field, requires layer 3's
BINDING pages -- fx page where a tile has one, texture id otherwise, which is
the distinction `ff7nx_parallaxwide` records costing it a build -- to be
contiguous and disjoint from every other layer. A field that fails is skipped
with a reason rather than guessed at. Skipping costs a sky; guessing would
tint the ship.

All five `fship_2` variants are in the shipped list, not the two whose art
looks brightest: where the art really is flat the tint is invisible anyway,
because black multiplied by a colour is still black, and covering all five
removes the one question a player cannot answer from the sofa -- which
Highwind room is this. `SEVENTH_NX_DAYNIGHT_SKY` changes the list, and
`=off` removes it.

REGISTER DISCIPLINE
===================
`CAVE_TICK` and `CAVE_CLEAR` are spliced into RECOMPILED code and obey the
rule the rest of this project does: anything that must survive the address
translator lives in x19..x28 and the cave saves and restores them itself.

`CAVE_BLOCK` and `CAVE_FLIP` are native-code hooks and need no guest
translation. `CAVE_BLOCK` uses x2 and x3, which the four instructions after
its hook site assign before anything reads them (`ldr w2, [x26]`,
`add x3, sp, #0x14`), so their liveness is proven by the code it splices into
rather than by convention. `CAVE_FLIP` sits one word into `gfx_drv_flip`'s
prologue and uses x16, which AAPCS lets any callee clobber. Neither touches NZCV:
adrp/add/ldr/orn/str/movz/movk set no flags.
"""
import os
import re
import struct
import sys

try:
    import lz4.block                                          # noqa: F401
except ImportError:                                          # pragma: no cover
    sys.exit('need lz4:  pip install lz4 --break-system-packages')

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_cave
import ff7nx_tables
import nxmap

Asm = AC.Asm
GUEST_TRANSLATE = AC.GUEST_TRANSLATE
EQ, NE, HS, LO, LE, LT, GE = A.EQ, A.NE, A.HS, 0x3, A.LE, A.LT, A.GE
HI = A.HI


# ------------------------------------------------------------------ module
# Module offsets into the 1.0.3 `exefs/main` whose build ID is
# 8CAAD5A4E142D2B8EBC1811B5AF05125, each checked against the word it displaces
# before anything is written.

TICK_HOOK = 0x9E6E80                  # x86 0x63A60B field_draw_everything
TICK_ORIG = 0xA9BB67FA                # stp x26, x25, [sp, #-0x50]!
TICK_RESUME = TICK_HOOK + 4

CLEAR_HOOK = 0x9E7EF0                 # the word after `bl gray_quads`
CLEAR_ORIG = 0xB94016A8               # ldr w8, [x21, #0x14]
CLEAR_RESUME = CLEAR_HOOK + 4

# The instruction that hands the finished BlockVertex block to the uniform
# ring buffer, inside the single draw-submission helper +0x10D9D70. The block
# is the caller's own stack, sp+0x18..sp+0x68; we write two of its three
# unread ivec4 lanes and then let the stock code copy and upload it.
BLOCK_HOOK = 0x10DA260                # add x1, sp, #0x18
BLOCK_ORIG = 0x910063E1
BLOCK_RESUME = BLOCK_HOOK + 4
BLOCK_BASE = 0x18                     # sp offset of blendMode.x
BLOCK_SIZE = 0x50                     # ivec4 + mat4, as +0x10DA290 says

# Every path through +0x10D9D70 reaches the hand-over through this word --
# the fall-through plus the two `b`s at +0x10DA094 and +0x10DA548. Checked at
# install time so a future port or another pass cannot quietly split them.
BLOCK_JOINS = ((0x10DA094, 0x10DA254), (0x10DA548, 0x10DA254))
BLOCK_SIZE_SITE = 0x10DA290           # mov w6, #0x50
BLOCK_SIZE_WORD = 0x52800A06

# `sub_640213` (arm +0x9F30F0) submits one field-background page at a time:
#
#     for (i = first; i < last; i++) {
#         obj = *(0xCFFC70 + i*4);            the per-page graphics object
#         if (!obj->[0xC] || !obj->[0x14]) continue;
#         engine_draw_graphics_object(obj->[8], game_object);
#     }
#
# and `field_draw_everything` calls it for four page BANDS, which are the
# port's depth/blend groups (field_bg_native's header): depth1 opaque 0..14,
# depth2 opaque 26..32, then the models, then depth1 and depth2 blended.
# Immediately before the call it has copied the page number into [x19] -- the
# recompiler's EAX slot -- and nothing overwrites it in between, so bracketing
# this one call is PAGE-PRECISE.
#
# The Highwind's animated window sky is layer 3, and layer 3 binds its own
# pages: on fship_22 it is 4..14 while the hull and deck are 2/26/27/28. That
# is what makes this possible at all -- but the page NUMBERS are not a
# constant. This project repacks field background pages (field_bg_pagecap
# deals fresh slots round-robin), so the range is measured out of the flevel
# actually being shipped, by `sky_plan`, and carried in a table.
SKY_PAGE_HOOK = 0x9F32C4              # bl engine_draw_graphics_object
SKY_PAGE_ORIG = 0x940393B7
SKY_PAGE_RESUME = SKY_PAGE_HOOK + 4
SKY_PAGE_BODY = 0x9F30F0              # x86 0x640213
SKY_PAGE_BODY_END = 0x9F3320
SKY_PAGE_SLOT_SAVE = 0x9F3284         # str w8, [x19] -- the page number
SKY_PAGE_SLOT_SAVE_ORIG = 0xB9000268

# Which fields get it, BY NAME rather than by id, so a maplist change cannot
# silently move it onto some other field.
#
# All five fship_2 variants, not the two whose art looks brightest. Layer 3
# on these maps is the window, and it is structurally separate in every one
# of them -- `sky_plan` proves that against the shipped archive before any of
# this is installed. Where the art really is flat black the tint is invisible
# by construction, because black multiplied by a colour is still black, so
# including them costs a picture nothing and removes the one question a
# player cannot answer from the sofa: which Highwind room is this.
#
# fship_1/_12 (a different room, pages 12..14) and fship_3 also qualify
# structurally and are left out only because they are not what was asked for;
# `SEVENTH_NX_DAYNIGHT_SKY=fship_1,fship_12` adds them.
SKY_FIELD_NAMES = ('fship_2', 'fship_22', 'fship_23', 'fship_24', 'fship_25')
SKY_FIELDS_ENV = 'SEVENTH_NX_DAYNIGHT_SKY'
SKY_LAYER = 3
SKY_ROW = 4                           # u16 field id, u8 first page, u8 last
SKY_MAX_ROWS = 16

# gfx_drv_flip -- the frame is on screen, so nothing that follows belongs to
# the field draw that set the tint. The second word of its prologue rather
# than the first: the first is `str d8, [sp, #-0x40]!`, and a displaced word
# this project's tests cannot interpret is a word no test covers.
FLIP_ENTRY = 0x10DA880                # gfx_drv_flip, gfx table index 163
FLIP_HOOK = 0x10DA884                 # str x23, [sp, #8]
FLIP_ORIG = 0xF90007F7
FLIP_RESUME = FLIP_HOOK + 4
FLIP_SCRATCH = 16                     # x16: AAPCS IP0, free in any prologue

# `set_driver_mode(w0)` -- the port's own mode setter. It dispatches on
# `mode - 1` through the 15-entry table at .rodata 0x11B3C74, and index 1
# (mode 2) is the branch that computes the field letterbox, which is how
# ff7nx_letterbox identified FIELD and confirmed it on hardware. The port's
# numbering is therefore FF7's own `ff7_game_modes` plus one -- and under the
# other candidate reading, FFNx's `game_modes` plus two, which gives the same
# three numbers, so BATTLE and WORLDMAP do not depend on which is right.
MODE_HOOK = 0x10F3D04                 # stp x29, x30, [sp, #0x10]; w0 is live
MODE_ORIG = 0xA9017BFD
MODE_RESUME = MODE_HOOK + 4
MODE_SCRATCH = 16
MODE_TABLE = 0x11B3C74                # the dispatch table, checked at install
MODE_FIELD_CASE = 0x10F3DB4           # where mode 2 lands: the letterbox branch
MODE_CASES = 15

DRIVER_FIELD = 2
DRIVER_BATTLE = 3
DRIVER_WORLD = 4
DRIVER_TICKING = 3                    # FIELD..WORLDMAP, the three FFNx ticks


# ------------------------------------------------- battle and the world map
# FFNx tints all three scene modes and turns the filter off around each one's
# UI. The two it does outside the field are:
#
#   world.cpp:192-194   three calls inside world_wm0_overworld_draw_all_74C179
#                       are replaced: +0x175 minimap quad OFF, +0x1BE world
#                       effects ON, +0x208 minimap points OFF
#   ff7_opengl.cpp:438  every call to battle_draw_call_42908C is wrapped
#                       OFF / call / ON
#
# Those are x86 addresses, and this port is a recompilation -- but it carries
# its own x86 -> ARM64 function map at .rodata 0x126D3A8 (10,952 entries,
# `nxmap.Main.x86_to_arm`), and that map resolves both functions exactly:
#
#   0x74C179 world_wm0_overworld_draw_all -> 0xF1F880
#   0x42908C battle_draw_call             -> 0x93EB0
#   0x66E641 engine_draw_graphics_object  -> 0xAD81A0
#
# The three world sites are CALL sites rather than function entries, so they
# are found by position instead: x86 0x74C179 contains exactly 15 calls to
# engine_draw_graphics_object and the replaced ones are the 8th, 11th and
# 15th. The ARM64 body contains exactly 15 `bl` to 0xAD81A0, in the same
# order -- `check_scene_sites` counts them at install time and refuses if that
# ever stops being true, so this is a resolved fact and not a guessed offset.
WORLD_DRAW_ALL = 0xF1F880             # x86 0x74C179
WORLD_DRAW_OBJ = 0xAD81A0             # x86 0x66E641, engine_draw_graphics_object
WORLD_CALLS = (8, 11, 15)             # 1-based, matching x86 +0x175/+0x1BE/+0x208
WORLD_DRAW_ALL_END = 0xF20650         # the next function start

WORLD_OFF1_HOOK = 0xF200D4            # bl engine_draw_graphics_object (#8)
WORLD_ON_HOOK = 0xF20270              # (#11)
WORLD_OFF2_HOOK = 0xF20408            # (#15)
WORLD_BL_ORIG = {WORLD_OFF1_HOOK: 0x97EEE033,
                 WORLD_ON_HOOK: 0x97EEDFCC,
                 WORLD_OFF2_HOOK: 0x97EEDF66}

# Battle is three CALL SITES inside the battle main loop, and BUILD 479 is
# why they are call sites and not the function.
#
# Build 478 bracketed `battle_draw_call_42908C` itself -- entry white, exit
# armed. That is not what FFNx does and it is wrong in both directions. The
# main loop calls that function four times and FFNx wraps only two of them
# (+0x289 text, +0x2CF box), leaving +0x27D and +0x2E0 TINTED; bracketing the
# function untinted all four. And it misses the point that actually shows:
# +0x32A, where FFNx's `battle_menu_enter` disables the filter for the rest
# of the frame -- which is where the battle MENU is drawn. Without that hook
# the menu comes out tinted, which is exactly what build 478 did.
#
# All three are counted out of the x86 the same way the world sites are:
# battle_main_loop is x86 0x41BAB3 -> arm 0x8FB00, its four calls to
# 0x42908C are arm 0x907AC/0x907E4/0x90924/0x90970 in that order, and its
# two calls to 0x6CDBFC are arm 0x90ACC/0x90BD4.
BATTLE_MAIN_LOOP = 0x8FB00            # x86 0x41BAB3
BATTLE_MAIN_LOOP_END = 0x918F0        # the next function start
BATTLE_UI = 0x93EB0                   # x86 0x42908C, draw ui graphics objects
BATTLE_MENU_CALLEE = 0xD0D920         # x86 0x6CDBFC
BATTLE_WRAPPED = (2, 3)               # the two FFNx wraps: text, then box

BATTLE_TEXT_HOOK = 0x907E4            # x86 battle_main_loop + 0x289
BATTLE_BOX_HOOK = 0x90924             # x86 battle_main_loop + 0x2CF
BATTLE_MENU_HOOK = 0x90ACC            # x86 battle_main_loop + 0x32A
BATTLE_BL_ORIG = {BATTLE_TEXT_HOOK: 0x94000DB3,
                  BATTLE_BOX_HOOK: 0x94000D63,
                  BATTLE_MENU_HOOK: 0x9431F395}

UI_SCRATCH = (16, 17)                 # x16/x17, saved and restored anyway

CTX_PAGE = 0x12CE000
CTX_SLOT = 0x2B0

# ------------------------------------------------------------- guest globals
G_BANK1 = 0xDC08DC                    # the field variable bank
G_OPTIONS = G_BANK1 + 0x0A
G_MINUTES = G_BANK1 + 0x0C
G_MONTHS = G_BANK1 + 0x0D
G_DAYS = G_BANK1 + 0x0E
G_HOURS = G_BANK1 + 0x0F
G_FIELD_ID = 0xCFF468                 # u16, the field the models belong to

OPT_ENABLED = 1 << 0
OPT_ADVANCE = 1 << 1
OPT_OUTDOOR = 1 << 2

# ----------------------------------------------------------- the tint block
TINT_LANE = 4                         # blendMode.y
MAGIC_LANE = 8                        # blendMode.z
TINT_MAGIC = 0x44594E                 # 'DYN'
TINT_WHITE = 0xFFFFFF

# ----------------------------------------------------------------- our state
BSS_TINT_INV = 0x00                   # u32, packed 0xRRGGBB, COMPLEMENTED
BSS_FRAMES = 0x04                     # u32, frames since the last minute
BSS_MODE = 0x08                       # u32, the last mode set_driver_mode saw
BSS_TICKED = 0x0C                     # u32, 1 if the clock already ran this
                                      #      frame -- see build_flip_cave
BSS_ARMED_INV = 0x10                  # u32, what this frame's gating decided,
                                      #      COMPLEMENTED like BSS_TINT_INV.
                                      #      The UI points swap BSS_TINT_INV
                                      #      between this and white.
BSS_SKY_INV = 0x14                    # u32, the sky's own tint, COMPLEMENTED
BSS_SKY_FIRST = 0x18                  # u32, first layer-3 page in this field
BSS_SKY_LAST = 0x1C                   # u32, last -- ZERO means "not a sky
                                      #      field", which is why `sky_plan`
                                      #      refuses a range starting at 0
BSS_SKY_ACTIVE = 0x20                 # u32, inside one layer-3 page draw
BSS_SCENE_MODE = 0x24                 # u32, the last mode that was a SCENE --
                                      #      FIELD or WORLDMAP, never a menu.
                                      #      A battle asks this where it came
                                      #      from; see build_flip_cave.
BSS_BYTES = 0x28

# --------------------------------------------------------- the mod's config
# Read out of Echo-S's own `DayNight/Time/config.toml`, kept here as the
# defaults so a build without the mod folder still produces the same cycle.
SUNRISE_MIN = 6 * 60
MORNING_MIN = 7 * 60
MIDDAY_MIN = 15 * 60
AFTERNOON_MIN = 19 * 60
NIGHT_MIN = 20 * 60
DAY_MINUTES = 24 * 60
FRAMES_PER_MINUTE = 20

MORNING_RGB = (191, 128, 128)         # 0.75, 0.50, 0.50
MIDDAY_RGB = (255, 255, 255)          # 1.00, 1.00, 1.00
AFTERNOON_RGB = (230, 102, 77)        # 0.90, 0.40, 0.30
NIGHT_RGB = (38, 38, 255)             # 0.15, 0.15, 1.00

# ------------------------------------------------- the colour space, BUILD 475
# FFNx does not multiply by those numbers where we do.
#
#     FFNx.frag:91    color.rgb = toLinear(color.rgb);
#     FFNx.frag:149   texture_color.rgb = toLinear(texture_color.rgb);
#     FFNx.frag:162   if (isTimeFilterEnabled) color.rgb *= TimeColor.rgb;
#     FFNx.frag:167   color.rgb = toGamma(color.rgb);
#
# The tint is a LINEAR-light multiplier, applied between a linearise and a
# re-encode. This port's `colortex_p.glsl` is plain `texture * vColor` with no
# transfer function anywhere, so multiplying by the same numbers there is a
# GAMMA-space multiply, and the two are not the same operation:
#
#     FFNx     out = toGamma(toLinear(in) * t)
#     ours     out = in * t
#
# For a power-law transfer of exponent g those agree only if we multiply by
# `t ** (1/g)` instead of by `t`:
#
#     toGamma(toLinear(in) * t) = (in**g * t)**(1/g) = in * t**(1/g)
#
# Night is where that matters. FFNx's night_color is 0.15, which as a gamma
# multiplier is 0.15**(1/2.2) = 0.42 -- and build 474 was multiplying by 0.15,
# so our night was crushing red and green to a third of what the PC build
# shows. The 4 pm warm wash was over-strong for the same reason, which is what
# the first screenshot of build 474 is.
#
# FFNx's own curve is sRGB rather than a pure power (FFNx.common.sh), and sRGB
# is NOT separable into a multiplier at all -- its linear toe means no single
# number reproduces the round trip for every input. The pure-power form is the
# separable approximation, and it is the one that makes a 256-byte table
# possible instead of a per-pixel transfer function in a shader we would then
# have to own.
#
# Measured against the exact sRGB chain, over every tint this cycle produces
# (tests/test_daynight.py pins it):
#
#     input 192..255   max 1.9/255
#     input  64..191   max 5.7/255
#     input  16..63    max 6.5/255
#
# A least-squares fit of the same table against the exact chain gets that
# worst case to 6.0, so the residual is the toe and not the exponent, and a
# fitted table would only trade a derivation for a magic number.
GAMMA = 2.2

# ------------------------------------------------------------ the dial
# `SEVENTH_NX_DAYNIGHT_STRENGTH`, 0..100, default 100 = exactly FFNx.
#
# This exists because "matches FFNx" and "looks right on my TV" are not the
# same claim. FFNx's night multiplies red and green by 0.42 in gamma, which is
# a heavy blue, and this port's picture arrives at it through a different
# scaler and a different set of custom pixel shaders than a PC build does. The
# dial lifts the multiplier toward white:
#
#     m' = 255 - (255 - m) * strength/100
#
# It is applied AFTER the transfer curve, so it is a pure dial on the finished
# multiplier rather than a second colour theory. 100 changes nothing, 0 is the
# feature off, and white stays white at every setting -- so midday, interiors
# and every untinted draw in the game are identical whatever it is set to.
STRENGTH_ENV = 'SEVENTH_NX_DAYNIGHT_STRENGTH'
FULL_STRENGTH = 100

# BUILD 478: the shipped default is 80, not FFNx's 100, and that is a
# considered choice rather than a fudge. FFNx's numbers are correct FOR FFNx,
# whose picture reaches the panel through its own renderer; this port's goes
# through a different background scaler and a different set of custom pixel
# shaders, and measured side by side against a PC capture, 100 lands heavier
# here than it does there. 80 was chosen on hardware, against that capture.
# `SEVENTH_NX_DAYNIGHT_STRENGTH=100` is still exactly FFNx for anyone who
# wants the reference rather than the match.
DEFAULT_STRENGTH = 80


def gamma_lut(strength=DEFAULT_STRENGTH):
    """256 bytes: a linear-light multiplier -> the gamma-space one."""
    out = bytearray(256)
    for v in range(256):
        curved = 255.0 * (v / 255.0) ** (1.0 / GAMMA)
        out[v] = int(round(255.0 - (255.0 - curved) * strength / 100.0))
    return bytes(out)


GAMMA_LUT = gamma_lut()


def strength_from_env():
    """`SEVENTH_NX_DAYNIGHT_STRENGTH=70` -> 70, or the shipped default."""
    raw = os.environ.get(STRENGTH_ENV, '').strip()
    if not raw:
        return DEFAULT_STRENGTH
    try:
        value = int(raw, 0)
    except ValueError:
        raise ValueError('%s=%r is not a whole percentage' % (STRENGTH_ENV, raw))
    if not 0 <= value <= 100:
        raise ValueError('%s=%d is not a percentage; 100 is FFNx exactly and '
                         '0 is the tint off' % (STRENGTH_ENV, value))
    return value


def as_the_shader_multiplies(rgb, strength=DEFAULT_STRENGTH):
    """`rgb`, which is FFNx's linear tint, as the gamma-space multiplier."""
    lut = GAMMA_LUT if strength == DEFAULT_STRENGTH else gamma_lut(strength)
    return tuple(lut[c] for c in rgb)

# The phase table the cave walks, as (upper bound in minutes, from, to).
#
# The last row is `(1440, NIGHT, NIGHT)` and it is not decoration: it turns
# FFNx's trailing `else color = nightColor` into an ordinary row, so the cave
# is a loop with no special case at either end. A minute-of-day is always
# below 1440, so the search always terminates on a row.
PHASES = (
    (SUNRISE_MIN, NIGHT_RGB, NIGHT_RGB),
    (MORNING_MIN, NIGHT_RGB, MORNING_RGB),
    (MIDDAY_MIN, MORNING_RGB, MIDDAY_RGB),
    (AFTERNOON_MIN, MIDDAY_RGB, AFTERNOON_RGB),
    (NIGHT_MIN, AFTERNOON_RGB, NIGHT_RGB),
    (DAY_MINUTES, NIGHT_RGB, NIGHT_RGB),
)
PHASE_ROW_BYTES = 8                   # u16 bound, then c0 and c1 as 3 bytes


def phase_table():
    """The 48 bytes the cave indexes. u16 bound, r0 g0 b0, r1 g1 b1."""
    out = bytearray()
    for bound, c0, c1 in PHASES:
        out += struct.pack('<H3B3B', bound, *c0, *c1)
    assert len(out) == len(PHASES) * PHASE_ROW_BYTES
    return bytes(out)

MAX_FIELD_ID = 788                    # maplist's own count on this flevel
BITMAP_BYTES = (MAX_FIELD_ID + 7) // 8


def phase_colour(minute):
    """
    The tint for a minute-of-day, in Python, for the tests to compare against.

    This is `ff7::Time::update`'s chain, in integers. The cave computes the
    same thing the same way; `tests/test_daynight.py` runs both over all 1,440
    minutes and requires them to agree exactly, which is the only way a
    fixed-point reimplementation of somebody else's float maths stays honest.

    The division TRUNCATES toward zero, because ARM64's `sdiv` does and
    Python's `//` does not. Getting that wrong is worth one bit: at 06:01 the
    blue channel is 252 floored and 253 truncated, and FFNx's float -- which
    never rounds at all, it multiplies straight into the shader -- is 252.88.
    Truncation is the closer of the two, so the cave stayed as it was and this
    followed it.
    """
    minute %= DAY_MINUTES
    low = 0
    for bound, c0, c1 in PHASES:
        if minute < bound:
            if c0 == c1:
                return c0
            span = bound - low
            step = minute - low
            return tuple(a + int((b - a) * step / span)
                         for a, b in zip(c0, c1))
        low = bound
    return NIGHT_RGB


# --------------------------------------------------- the build-time bitmap
_BITON = 0x82
_BITOFF = 0x83
_OPTIONS_BANK_BYTE = 0x10             # bank 1 for both operands
_OPTIONS_ADDR = 0x0A
_OUTDOOR_BIT = 0x02
_SECTION1_FIXED_HEADER = 32
_SECTION1_ROUTINE_TABLE_BYTES = 64


def _field_is_outdoor(script_section):
    """
    True / False / None from a field's own `Time` actor.

    None means the field has no opinion -- the two fields without the actor,
    and anything whose actor is not the plain one-instruction shape. Those
    keep whatever the previous field set, which is what the PC build does too
    when a field's script does not touch the bit.
    """
    try:
        actor_count = script_section[2]
        akao_count = struct.unpack_from('<H', script_section, 6)[0]
        string_offset = struct.unpack_from('<H', script_section, 4)[0]
    except (IndexError, struct.error):
        return None
    names_offset = _SECTION1_FIXED_HEADER
    routines_offset = names_offset + actor_count * 8 + akao_count * 4
    if routines_offset + actor_count * _SECTION1_ROUTINE_TABLE_BYTES > len(
            script_section):
        return None
    index = None
    for i in range(actor_count):
        name = script_section[names_offset + i * 8:names_offset + (i + 1) * 8]
        if name.rstrip(b'\0') == b'Time':
            index = i
            break
    if index is None:
        return None
    tables = [struct.unpack_from('<32H', script_section,
                                 routines_offset
                                 + i * _SECTION1_ROUTINE_TABLE_BYTES)
              for i in range(actor_count)]
    shared = set(o for i, t in enumerate(tables) if i != index for o in t)
    private = sorted(o for o in tables[index]
                     if o not in shared and 0 < o < string_offset)
    for start in private:
        if start + 4 > len(script_section):
            continue
        op, bank, addr, bit = script_section[start:start + 4]
        if (bank, addr, bit) != (_OPTIONS_BANK_BYTE, _OPTIONS_ADDR,
                                 _OUTDOOR_BIT):
            continue
        if op == _BITON:
            return True
        if op == _BITOFF:
            return False
    return None


def _stream_index(path):
    """
    ({lowername: (offset, size)}, read(name)) for an LGP, without loading it.

    Deliberately a second, tiny reader rather than a change to `lgp.Archive`:
    that class is what writes the archives and its behaviour is load-bearing
    for the whole build. This only ever reads, and only the entries it is
    asked for.
    """
    import lgp
    handle = open(path, 'rb')
    creator = handle.read(lgp.CREATOR_LEN)
    if not creator.endswith(b'SQUARESOFT'):
        handle.close()
        raise ValueError('%s is not an LGP archive' % path)
    count = struct.unpack('<i', handle.read(4))[0]
    index = {}
    for _ in range(count):
        entry = handle.read(lgp.TOC_ENTRY_LEN)
        name = entry[:20].split(b'\0')[0].decode('ascii', 'replace').lower()
        offset = struct.unpack('<I', entry[20:24])[0]
        index.setdefault(name, offset)

    def read(name):
        handle.seek(index[name.lower()])
        head = handle.read(24)
        size = struct.unpack('<I', head[20:24])[0]
        return handle.read(size)

    return index, read


def read_maplist(flevel_path):
    """
    The field id -> name table, streamed out of any flevel.

    This is what makes the bitmap indexable by the id the runtime already has
    at 0xCFF468, rather than by anything this build invented. Vanilla's copy
    does as well as the built one: Echo-S does not change maplist, and reading
    one entry out of the 131 MB archive is a seek.
    """
    entries, reader = _stream_index(flevel_path)
    if 'maplist' not in entries:
        raise ValueError('%s has no maplist; the field id table is what the '
                         'outdoor bitmap is keyed by' % flevel_path)
    blob = reader('maplist')
    count = struct.unpack_from('<H', blob, 0)[0]
    if count > MAX_FIELD_ID:
        raise ValueError('maplist has %d entries, more than the %d this '
                         'bitmap was sized for' % (count, MAX_FIELD_ID))
    out = []
    for field_id in range(count):
        name = blob[2 + field_id * 32:2 + (field_id + 1) * 32]
        out.append(name.split(b'\0')[0].decode('latin1').strip().lower())
    return out


def outdoor_bitmap(maplist, field_paths, log=lambda *_: None):
    """
    A bit per field id: 1 outdoor, 0 indoor.

    `field_paths` is `{field name: path}` -- in a build, `plan.echo_fields`,
    which is Echo-S's own loose field files and NOT the built archive. That is
    deliberate on two counts. It is the authority: the `Time` actor is the mod
    author's statement about each field, and `echo_s_flevel` strips it from
    what ships, so the built archive is the one place the answer is no longer
    written down. And it is fast: those files are stored uncompressed, where
    pulling 703 fields through this project's pure-Python LZS costs minutes,
    which is not a thing to put in a module pass.

    A field with no `Time` actor gets no bit and keeps whatever the previous
    field set, which is what the PC build does when a script does not touch it.
    """
    import lgp
    lookup = {str(k).lower(): v for k, v in dict(field_paths).items()}
    bits = bytearray(BITMAP_BYTES)
    outdoor = indoor = silent = missing = 0
    for field_id, name in enumerate(maplist):
        if not name or name == 'dummy':
            continue
        path = lookup.get(name)
        if isinstance(path, (tuple, list)):
            path = path[0]
        if not path or not os.path.isfile(path):
            missing += 1
            continue
        raw = open(path, 'rb').read()
        try:
            sections = lgp.split_sections(raw)
        except Exception:                                     # noqa: BLE001
            try:
                sections = lgp.split_sections(lgp.lzs_decompress(raw[4:]))
            except Exception:                                 # noqa: BLE001
                missing += 1
                continue
        verdict = _field_is_outdoor(sections[0])
        if verdict is None:
            silent += 1
        elif verdict:
            bits[field_id >> 3] |= 1 << (field_id & 7)
            outdoor += 1
        else:
            indoor += 1
    log('  outdoor bitmap: %d outdoor, %d indoor, %d with no Time actor, '
        '%d maplist name(s) not in the mod (%d bytes)'
        % (outdoor, indoor, silent, missing, len(bits)))
    if not outdoor:
        raise ValueError('not one field is marked outdoor -- either the Time '
                         'actor is gone from this mod or its shape changed, '
                         'and a cycle with no daylight is worse than none')
    return bytes(bits)



# ----------------------------------------------- the Highwind window sky
def sky_field_names():
    """The fields that get the sky tint. `SEVENTH_NX_DAYNIGHT_SKY` overrides."""
    raw = os.environ.get(SKY_FIELDS_ENV, '').strip()
    if not raw:
        return SKY_FIELD_NAMES
    if raw.lower() in ('off', '0', 'none'):
        return ()
    return tuple(n.strip().lower() for n in raw.split(',') if n.strip())


def sky_plan(flevel_path, maplist, names=None, log=lambda *_: None):
    """
    [(field id, first page, last page)] for the fields whose layer 3 can be
    tinted on its own.

    MEASURED, not remembered. The runtime brackets one page-draw call and
    decides by page number, so the whole thing rests on layer 3 binding pages
    that no other layer binds -- and on those page numbers being the ones in
    the flevel actually being shipped. This project repacks background pages,
    so both are checked here against the built archive rather than carried as
    constants:

      * layer 3 must exist and bind at least one page;
      * its pages must be CONTIGUOUS, because the cave tests a range;
      * they must be DISJOINT from every other layer's, or the bracket would
        tint the hull and the deck as well;
      * and the range must not start at page 0, because zero is the "not a
        sky field" mark in `BSS_SKY_LAST`.

    A field that fails any of those is skipped with a reason. Skipping costs
    the sky effect; guessing would tint the ship.
    """
    import lgp
    import diag_common
    import field_bg_pagecap as pagecap
    names = sky_field_names() if names is None else names
    if not names:
        return []
    want = {n.lower(): i for i, n in enumerate(maplist) if n.lower() in
            {x.lower() for x in names}}
    missing = [n for n in names if n.lower() not in want]
    if missing:
        log('! day/night sky: %s not in this flevel\'s maplist'
            % ', '.join(missing))
    archive = lgp.Archive(flevel_path)
    rows = []
    for name in sorted(want, key=lambda n: want[n]):
        field_id = want[name]
        if name not in archive.index:
            log('! day/night sky: %s is not in the archive' % name)
            continue
        try:
            section = lgp.split_sections(
                archive.decompressed(archive.index[name]))[8]
            pages, tex_start, _end, _px = diag_common.parse_pages(section)
            present = {slot for slot, page in enumerate(pages)
                       if page is not None}
            layers = {}
            for layer, offsets in diag_common.walk_layers(
                    section, section.find(b'BACK'), tex_start):
                bound = set()
                for off in offsets:
                    fx = section[off + pagecap.T_FX_PAGE]
                    # THE BINDING PAGE IS NOT THE TEXTURE ID -- a tile
                    # carrying an fx page binds that page instead. Getting
                    # this wrong is what ff7nx_parallaxwide records costing it
                    # a build.
                    bound.add(fx if (fx and fx in present)
                              else section[off + pagecap.T_TEXID])
                layers[layer] = bound
        except Exception as exc:                                # noqa: BLE001
            log('! day/night sky: %s: %s: %s'
                % (name, type(exc).__name__, exc))
            continue
        sky = layers.get(SKY_LAYER) or set()
        others = set()
        for layer, bound in layers.items():
            if layer != SKY_LAYER:
                others |= bound
        first, last = (min(sky), max(sky)) if sky else (0, 0)
        why = None
        if not sky:
            why = 'it has no layer %d' % SKY_LAYER
        elif sky & others:
            why = ('layer %d shares page(s) %s with the rest of the field'
                   % (SKY_LAYER, sorted(sky & others)))
        elif set(range(first, last + 1)) != sky:
            why = ('layer %d\'s pages %s are not one contiguous range'
                   % (SKY_LAYER, sorted(sky)))
        elif first == 0:
            why = 'layer %d starts at page 0, which is the off mark' % SKY_LAYER
        if why:
            log('! day/night sky: %s skipped -- %s' % (name, why))
            continue
        rows.append((field_id, first, last))
        log('  sky: %s (field %d) layer %d is pages %d..%d, and no other '
            'layer binds them -- the hull, the deck and the actors are on %s'
            % (name, field_id, SKY_LAYER, first, last,
               ', '.join(str(p) for p in sorted(others)) or 'none'))
    if len(rows) > SKY_MAX_ROWS:
        raise ValueError('day/night sky: %d fields is more than the %d the '
                         'table is sized for' % (len(rows), SKY_MAX_ROWS))
    return rows


def sky_table(rows):
    """The table the tick cave walks. Terminated by field id 0 -- `dummy`,
    which is never a field anybody stands in."""
    out = bytearray()
    for field_id, first, last in rows:
        out += struct.pack('<HBB', field_id, first, last)
    out += struct.pack('<HBB', 0, 0, 0)
    return bytes(out)


# ------------------------------------------------------------ extra encoders
# Forms a64.py does not carry. tests/test_daynight.py round-trips every one
# through capstone, for the reason the rest of this project does: an encoding
# that looks plausible in hex and decodes to the wrong register produces a
# structurally valid module that misbehaves.
def sdiv(rd, rn, rm):
    """SDIV Wd, Wn, Wm. The lerp numerator is signed: afternoon -> night
    moves red from 230 down to 38."""
    return 0x1AC00C00 | (rm << 16) | (rn << 5) | rd


def bic_reg(rd, rn, rm):
    """BIC Wd, Wn, Wm -- clear the outdoor bit without a logical immediate."""
    return 0x0A200000 | (rm << 16) | (rn << 5) | rd


def lsrv(rd, rn, rm):
    """LSRV Wd, Wn, Wm -- a shift by a register."""
    return 0x1AC02400 | (rm << 16) | (rn << 5) | rd


def ldrh_off(rt, rn, imm):
    return A.ldrh(rt, rn, imm)


# ----------------------------------------------------------------- emitters
def _translate_imm(a, guest):
    AC.mov32(a, 0, guest)
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))


def _white(a, bss_reg):
    """
    White is stored as zero, because the slot holds the COMPLEMENT of the
    tint.

    That is not a cute trick, it is the fix for build 469's black screen.
    BSS is zero-filled at load and only `field_draw_everything` ever writes
    this slot, so on the title screen -- before any field has been entered --
    a slot that meant the tint literally meant black, and the UBO cave
    faithfully multiplied the whole menu by it. Storing the complement makes
    the untouched state mean white, which is identity, which is the state
    every one of this feature's safety arguments assumes.
    """
    a.emit(A.str_(31, bss_reg, BSS_ARMED_INV))     # WZR: ~0xFFFFFF
    a.emit(A.str_(31, bss_reg, BSS_TINT_INV))


def build_block_cave(cave, addr, bss):
    """
    Park the tint in the two ivec4 lanes the shaders never read.

    Spliced into NATIVE code at the one instruction that hands the finished
    80-byte `BlockVertex` to the uniform ring buffer. `sp+0x18` is the block:
    `blendMode.x` at +0x00 (written four instructions earlier), the matrix at
    +0x10..+0x50, and `.y`/`.z`/`.w` at +0x04..+0x0F which nothing in the port
    ever writes -- they go to the GPU as whatever the stack held.

    This is the whole feature's transport and it is deliberately dumb. There
    is no buffer to identify, because we are upstream of the buffer; no size
    to test, because the literal 0x50 four instructions later is the size; and
    no window to be early or late for, because the copy happens in the next
    instruction but one.

    x2 and x3 are free by construction rather than by convention: the stock
    code assigns both (`ldr w2, [x26]`, `add x3, sp, #0x14`) before `bl +0x570`
    reads them, so nothing live can be sitting in either at this word.
    """
    a = Asm(cave, addr)
    AC.bss_ptr(a, 3, bss)
    a.emit(A.ldr(2, 3, BSS_TINT_INV))
    # The normal filter is deliberately white on all Highwind interiors.  A
    # separate, short-lived flag selects the same clock colour for a single
    # field-background page draw -- never for the ship, actor or UI paths.
    a.emit(A.ldr(3, 3, BSS_SKY_ACTIVE))
    a.cbz(3, 'normal_tint')
    AC.bss_ptr(a, 3, bss)
    a.emit(A.ldr(2, 3, BSS_SKY_INV))
    a.label('normal_tint')
    a.emit(A.mvn_reg(2, 2))                    # the slot holds the complement
    a.emit(A.str_(2, 31, BLOCK_BASE + TINT_LANE))      # blendMode.y
    AC.mov32(a, 2, TINT_MAGIC)
    a.emit(A.str_(2, 31, BLOCK_BASE + MAGIC_LANE))     # blendMode.z
    a.emit(BLOCK_ORIG)                         # add x1, sp, #0x18
    a.emit(A.b(a.pc(), BLOCK_RESUME))
    return a.resolve()


def build_sky_page_cave(cave, addr, bss):
    """
    Tint exactly the Highwind's layer-3 pages, while they are being submitted.

    `sub_640213` draws one background page per iteration and has just copied
    that page's number into `[x19]` -- the recompiler's EAX slot -- eight
    instructions above this call, with nothing writing it in between (the
    stores there go to +4, +8 and +0x10). So the page number is simply
    readable, and the bracket is page-precise rather than layer-guessing.

    The flag is cleared BEFORE the test as well as after the call, so a page
    outside the range, a field that is not in the sky table, or an exception
    unwinding out of the draw all leave it off. The general block hook then
    sees a plain white field for everything except those pages.

    x15, x16 and x17 are pushed and popped: this is recompiled code and this
    project's rule is that x19..x28 carry what must survive the address
    translator, so no liveness is assumed for anything here. The displaced
    `bl` is re-encoded for the cave's own address because it is PC-relative.
    """
    a = Asm(cave, addr)
    a.emit(A.stp64_pre(16, 17, 31, -32))
    a.emit(A.str64(15, 31, 16))
    AC.bss_ptr(a, 16, bss)
    a.emit(A.str_(31, 16, BSS_SKY_ACTIVE))
    a.emit(A.ldr(17, 16, BSS_SKY_LAST))
    a.cbz(17, 'sky_page_out')                  # not a sky field at all
    a.emit(A.ldr(15, 19, 0))                   # the page, from +0x9F3284
    a.emit(A.cmp_reg(15, 17))
    a.bcond('sky_page_out', HI)                # past the last sky page
    a.emit(A.ldr(17, 16, BSS_SKY_FIRST))
    a.emit(A.cmp_reg(15, 17))
    a.bcond('sky_page_out', LO)                # before the first
    a.emit(A.movz(17, 1))
    a.emit(A.str_(17, 16, BSS_SKY_ACTIVE))
    a.label('sky_page_out')
    a.emit(A.ldr64(15, 31, 16))
    a.emit(A.ldp64_post(16, 17, 31, 32))

    a.emit(A.bl(a.pc(), WORLD_DRAW_OBJ))       # the displaced, re-encoded call

    # The page is submitted by the time that returns, so the selector is put
    # back immediately: it can never reach the hull, the actors or the UI.
    a.emit(A.stp64_pre(16, 17, 31, -16))
    AC.bss_ptr(a, 16, bss)
    a.emit(A.str_(31, 16, BSS_SKY_ACTIVE))
    a.emit(A.ldp64_post(16, 17, 31, 16))
    a.emit(A.b(a.pc(), SKY_PAGE_RESUME))
    return a.resolve()


def build_mode_cave(cave, addr, bss):
    """
    Remember which driver mode the game last asked for.

    `set_driver_mode` is the port's own single entry point for it -- every
    module change goes through the 15-way dispatch two instructions below
    this, so a copy taken here is the mode, not a guess at it. The flip cave
    uses it to tick the clock in exactly the three modes FFNx ticks in.

    One store, in a function prologue, through x16 (AAPCS IP0). The displaced
    `stp x29, x30, [sp, #0x10]` is re-emitted unchanged and w0 -- the mode,
    which the stock code copies to w19 four instructions later -- is not
    touched.
    """
    a = Asm(cave, addr)
    AC.bss_ptr(a, MODE_SCRATCH, bss)
    a.emit(A.str_(0, MODE_SCRATCH, BSS_MODE))
    # ... and separately, the last mode that was a SCENE. Menus, movies, the
    # swirl and the title screen come through here too and must not count:
    # what a battle needs to know is whether it was entered from a field or
    # from the world map, and nothing else changes that answer.
    a.emit(A.cmp_imm(0, DRIVER_FIELD))
    a.bcond('scene', EQ)
    a.emit(A.cmp_imm(0, DRIVER_WORLD))
    a.bcond('not_scene', NE)
    a.label('scene')
    a.emit(A.str_(0, MODE_SCRATCH, BSS_SCENE_MODE))
    a.label('not_scene')
    a.emit(MODE_ORIG)                          # stp x29, x30, [sp, #0x10]
    a.emit(A.b(a.pc(), MODE_RESUME))
    return a.resolve()


def _emit_clock(a, frames_per_minute, seed_hour, force_on, bss_reg=20):
    """
    Load the clock, seed it if it has never run, advance it, store it back.

    Emitted into BOTH the field tick and the once-a-frame flip tick, from here
    rather than twice, because two copies of a cascade like this drift. The
    register assignment is the tick cave's and both callers hold to it:

        w21  the options byte     w22 minutes   w23 hours
        w25  the frame counter    w27 months    w28 days
        x26  the host pointer to the four clock bytes
        x<bss_reg>  our BSS

    Leaves w21 holding the options byte, which the field tick needs afterwards
    for the outdoor bit. Branches to the caller's `white` label when the cycle
    is switched off in the savemap, and falls through otherwise.
    """
    # the options byte: bit 0 enables the whole cycle, bit 1 advances it
    _translate_imm(a, G_OPTIONS)
    a.emit(A.ldrb(21, 0, 0))
    if force_on:
        # ARM THE CYCLE OURSELVES, AND THIS IS A DELIBERATE DIVERGENCE.
        #
        # On PC those two bits are set by ordinary field scripts -- `init` in
        # `blackbgh` sets bit 0 at the opening black screen, `dir` in
        # `md1stin` sets bit 1 at the Sector 1 station -- and both survive
        # into the shipped flevel, because neither lives in the `Time` actor
        # `echo_s_flevel` strips.
        #
        # But they are SAVEMAP bits. A save made before this feature existed
        # has never run either script, so the cycle would stay dead on every
        # save anybody already has, and the setting would look broken. This
        # port has no in-game toggle for it either -- the Hext menu that owns
        # one is not ported -- so nothing is being overridden: the build only
        # installs this pass when the mod's own "Day Night" option is on, and
        # that option IS the user's answer.
        a.emit(A.movz(9, OPT_ENABLED | OPT_ADVANCE))
        a.emit(A.orr_lsl(21, 21, 9, 0))
    a.emit(A.and_mask(9, 21, 1))
    a.cbz(9, 'white')

    # minutes, months, days and hours are four adjacent bytes at a four-
    # aligned address, so one translation reaches all of them.
    _translate_imm(a, G_MINUTES)
    a.emit(AC.mov64(26, 0))
    a.emit(A.ldrb(22, 26, 0))                  # minutes
    a.emit(A.ldrb(27, 26, 1))                  # months
    a.emit(A.ldrb(28, 26, 2))                  # days
    a.emit(A.ldrb(23, 26, 3))                  # hours
    a.emit(A.ldr(25, bss_reg, BSS_FRAMES))

    if seed_hour is not None:
        # IS THIS CLOCK OURS AT ALL? BUILD 477.
        #
        # These are five ordinary field-script variables -- savemap +0x0BAE,
        # inside the script variable banks -- so a save made before this
        # feature existed has whatever the game left in them, and that is very
        # unlikely to be a valid date. Day 0 alone was the old test, and it
        # missed every vanilla save whose byte there happened to be non-zero:
        # the cycle then started from a nonsense hour, which is exactly the
        # "my old saves have the wrong time" report.
        #
        # So the test is the whole date, not one byte of it. A clock this
        # feature has been running is always inside these ranges, because the
        # cascade below is what keeps it there. Anything outside them was
        # written by something else and is not a time.
        # THE DATE decides this, not the time, and BUILD 478 is why.
        #
        # 477 tested the hour and the minute too, and that broke sleeping.
        # Every inn's `Sleep` actor does `PLUS! bank1[0x0F], +8` -- a bare add
        # with no wrap, because on PC `Time::update`'s own cascade does the
        # wrapping. Sleeping from 16:00 therefore hands us hour 24, which 477
        # read as "not a time" and reset to 08:00. You could sleep all night
        # and never reach night.
        #
        # So the hour and the minute are normalised below rather than judged,
        # and only the DATE -- which no script in the mod writes except one
        # `days += 1` -- decides whether these bytes were ever ours. The
        # thresholds allow exactly one legitimate overshoot.
        a.cbz(28, 'seed')                      # day 0: never initialised
        a.emit(A.cmp_imm(28, 33))              # days are 1..31, +1 to carry
        a.bcond('seed', HS)
        a.emit(A.cmp_imm(27, 13))              # months are 0..11, +1 to carry
        a.bcond('seed', HS)
        a.b('seeded')
        a.label('seed')
        a.emit(A.movz(28, 1))
        a.emit(A.movz(27, 0))
        a.emit(A.movz(22, 0))
        a.emit(A.movz(23, seed_hour))
        a.emit(A.movz(25, 0))                  # and the part-minute with it
        a.label('seeded')

    # Carry whatever a script left over the top, smallest unit upwards. One
    # step each: that covers every legitimate write (the biggest is
    # `rktinn1`'s +11 hours, and 23+11 = 34 needs one carry), and anything
    # wilder than that walks itself back into range over the next few frames
    # rather than being thrown away.
    a.emit(A.cmp_imm(22, 60))
    a.bcond('carried_minutes', LO)
    a.emit(A.sub_imm(22, 22, 60))
    a.emit(A.add_imm(23, 23, 1))
    a.label('carried_minutes')
    a.emit(A.cmp_imm(23, 24))
    a.bcond('carried_hours', LO)
    a.emit(A.sub_imm(23, 23, 24))
    a.emit(A.add_imm(28, 28, 1))
    a.label('carried_hours')
    a.emit(A.cmp_imm(28, 32))
    a.bcond('carried_days', LO)
    a.emit(A.sub_imm(28, 28, 31))              # days are 1-based
    a.emit(A.add_imm(27, 27, 1))
    a.label('carried_days')
    a.emit(A.cmp_imm(27, 12))
    a.bcond('carried_months', LO)
    a.emit(A.movz(27, 0))
    a.label('carried_months')

    # bit 1 is "advance"; the mod's menu clears it to pause the clock
    a.emit(A.lsr(9, 21, 1))
    a.emit(A.and_mask(9, 9, 1))
    a.cbz(9, 'store')
    a.emit(A.add_imm(25, 25, 1))
    a.emit(A.cmp_imm(25, frames_per_minute))
    a.bcond('store', LO)
    a.emit(A.movz(25, 0))
    a.emit(A.add_imm(22, 22, 1))
    a.emit(A.cmp_imm(22, 60))
    a.bcond('store', LO)
    a.emit(A.movz(22, 0))
    a.emit(A.add_imm(23, 23, 1))
    a.emit(A.cmp_imm(23, 24))
    a.bcond('store', LO)
    a.emit(A.movz(23, 0))
    a.emit(A.add_imm(28, 28, 1))
    a.emit(A.cmp_imm(28, 32))
    a.bcond('store', LO)
    a.emit(A.movz(28, 1))                      # days are 1-based
    a.emit(A.add_imm(27, 27, 1))
    a.emit(A.cmp_imm(27, 12))
    a.bcond('store', LO)
    a.emit(A.movz(27, 0))

    a.label('store')
    a.emit(A.str_(25, bss_reg, BSS_FRAMES))
    a.emit(A.strb(22, 26, 0))
    a.emit(A.strb(27, 26, 1))
    a.emit(A.strb(28, 26, 2))
    a.emit(A.strb(23, 26, 3))


def build_flip_cave(cave, addr, bss, frames_per_minute, phase_table_addr,
                    gamma_table_addr, force_on=True, seed_hour=8):
    """
    The once-a-frame half: tick the clock outside the field, and arm the tint
    for the two modes that have no draw entry of their own.

    FFNx runs `ff7::Time::update` from the main loop, so in MODE_BATTLE and
    MODE_WORLDMAP the clock advances and the filter is armed with no help from
    any draw function -- the scene is simply tinted until one of the UI points
    turns it off. `gfx_drv_flip` is this port's once-a-frame point, in every
    mode, and arming at the end of a frame arms the next one, which is the
    same thing a frame later and invisible.

    THE THREE MODES DIVERGE HERE, EXACTLY AS THEY DO IN `Time::update`:

        FIELD    the field tick has already run, so this puts the tint back
                 to WHITE. That is the belt-and-braces half this cave was
                 originally for: if `field_draw_everything` ever returns
                 without reaching its gray quads, the leak is bounded to one
                 frame instead of lasting until the next field.
        BATTLE   armed, but only if bit 2 says the last field was outdoors --
                 `Time::update` gates battle on the same savemap bit, and
                 nothing in a battle writes it.
        WORLDMAP armed unconditionally. FFNx does not test bit 2 for the
                 world map and neither does this (time.cpp:230-240).

    `GUEST_TRANSLATE` is safe to call from here: it is a pure page-table
    lookup on w0 (+0x10FC3A0), and `gfx_drv_flip` itself calls it eleven
    instructions later. The stock function reads none of its incoming
    arguments, so `AC.save_host` covers the cave completely.
    """
    a = Asm(cave, addr)
    AC.save_host(a, 0x80)
    AC.bss_ptr(a, 20, bss)

    # only the three modes FFNx ticks in. FIELD is 2, so this is
    # `2 <= mode <= 4` as one unsigned compare.
    a.emit(A.ldr(9, 20, BSS_MODE))
    a.emit(A.sub_imm(9, 9, DRIVER_FIELD))
    a.emit(A.cmp_imm(9, DRIVER_TICKING))
    a.bcond('white', HS)
    # a field frame has already ticked and already armed; white is all this
    # owes it.
    a.emit(A.ldr(9, 20, BSS_TICKED))
    a.cbnz(9, 'white')

    _emit_clock(a, frames_per_minute, seed_hour, force_on)

    # WHICH SCENE'S RULE APPLIES -- and BUILD 481 is why this is not just
    # `mode == WORLDMAP`.
    #
    # Bit 2 is the OUTDOOR flag, and it describes a FIELD: the field tick
    # writes it from the build-time bitmap every frame you are standing in
    # one. FFNx gates both field and battle on it (time.cpp:230-240), which
    # means a battle inherits whatever the last field left behind -- and
    # after any interior, the Highwind included, that is "indoor". So a
    # random encounter on the world map at midnight came out looking like
    # noon until you next walked through somewhere outdoors.
    #
    # The bit is simply not about a world-map encounter. `BSS_SCENE_MODE` is
    # the last mode that was a scene at all, so a battle can ask where it was
    # entered from and follow that scene's rule: from the world map, tint
    # like the world map; from a field, honour that field's bit.
    a.emit(A.ldr(9, 20, BSS_MODE))
    a.emit(A.cmp_imm(9, DRIVER_FIELD))
    a.bcond('gate_outdoor', EQ)                # a field always asks the bit
    a.emit(A.cmp_imm(9, DRIVER_WORLD))
    a.bcond('colour', EQ)                      # the world map never does
    a.emit(A.ldr(9, 20, BSS_SCENE_MODE))       # a battle asks where it began
    a.emit(A.cmp_imm(9, DRIVER_WORLD))
    a.bcond('colour', EQ)
    a.label('gate_outdoor')
    a.emit(A.lsr(9, 21, 2))
    a.emit(A.and_mask(9, 9, 1))
    a.cbz(9, 'white')
    a.label('colour')
    _emit_colour(a, phase_table_addr, gamma_table_addr)
    a.b('done')

    a.label('white')
    _white(a, 20)

    a.label('done')
    a.emit(A.str_(31, 20, BSS_TICKED))
    AC.restore_host(a, 0x80)
    a.emit(FLIP_ORIG)                          # str x23, [sp, #8]
    a.emit(A.b(a.pc(), FLIP_RESUME))
    return a.resolve()


def build_ui_cave(cave, addr, bss, hook, orig, before, after=None):
    """
    FFNx's `setTimeFilterEnabled` calls, at FFNx's own points.

    `before` and `after` are False for its `(false)`, True for its `(true)`
    and None for "leave it alone". The battle pair use both, which makes this
    cave literally `draw_ui_graphics_objects_wrapper`: the displaced `bl` is
    re-emitted INSIDE the cave, so control comes back here after the call and
    the second half can run.

    Nothing recomputes a colour -- the frame's decision stays in
    `BSS_ARMED_INV` and only `BSS_TINT_INV` moves, so these points can toggle
    as often as the scene needs, and a mode that never reaches them is simply
    tinted throughout.

    Spliced into RECOMPILED code, so nothing is assumed about which registers
    are free: x16 and x17 are pushed and popped around each half, and the
    displaced word runs with the machine exactly as it was. A `bl` is
    RE-ENCODED for the cave's own address rather than copied, because it is
    PC-relative -- `test_a_displaced_call_still_calls_the_same_function`
    walks each cave and requires the call to land where the stock one did.
    """
    lo, hi = UI_SCRATCH

    def put(a, armed):
        a.emit(A.stp64_pre(lo, hi, 31, -16))
        AC.bss_ptr(a, lo, bss)
        if armed:
            a.emit(A.ldr(hi, lo, BSS_ARMED_INV))
            a.emit(A.str_(hi, lo, BSS_TINT_INV))
        else:
            a.emit(A.str_(31, lo, BSS_TINT_INV))
        a.emit(A.ldp64_post(lo, hi, 31, 16))

    a = Asm(cave, addr)
    if before is not None:
        put(a, before)
    if (orig >> 26) == 0b100101:               # a `bl`: PC-relative
        imm = orig & 0x3FFFFFF
        if imm & (1 << 25):
            imm -= 1 << 26
        a.emit(A.bl(a.pc(), hook + (imm << 2)))
    else:
        a.emit(orig)
    if after is not None:
        put(a, after)
    a.emit(A.b(a.pc(), hook + 4))
    return a.resolve()


def build_clear_cave(cave, addr, bss):
    """
    Put the tint back to white before the UI is drawn.

    Same point FFNx uses -- immediately after the call to
    `field_draw_gray_quads_644E90` -- and for the same reason: the field and
    its models are behind us, the dialogue box and the menu are not, and a
    tinted text window reads as a bug rather than as nightfall.

    Saves the host frame rather than borrowing w8/w9. The displaced word here
    is `ldr w8, [x21, #0x14]`, and README-35 records what reusing that pair in
    recompiler output cost the field-footstep caves.
    """
    a = Asm(cave, addr)
    AC.save_host(a, 0x80)
    AC.bss_ptr(a, 19, bss)
    _white(a, 19)
    AC.restore_host(a, 0x80)
    a.emit(CLEAR_ORIG)
    a.emit(A.b(a.pc(), CLEAR_RESUME))
    return a.resolve()


def _emit_sky_target(a, sky_table_addr, bss_reg=20):
    """
    Look this field up in the sky table and leave its page range in BSS.

    Runs once a frame at the top of the tick, before anything else can
    branch, so the page bracket below always has a fresh answer. A field that
    is not in the table stores zero in `BSS_SKY_LAST`, which is the off mark
    the bracket tests first.
    """
    _translate_imm(a, G_FIELD_ID)
    a.emit(A.ldrh(9, 0, 0))                    # this field's id
    AC.bss_ptr(a, 10, sky_table_addr)
    a.label('sky_row')
    a.emit(A.ldrh(13, 10, 0))
    a.cbz(13, 'sky_no')                        # id 0 terminates the table
    a.emit(A.cmp_reg(9, 13))
    a.bcond('sky_hit', EQ)
    a.emit(A.add_imm64(10, 10, SKY_ROW))
    a.b('sky_row')
    a.label('sky_hit')
    a.emit(A.ldrb(13, 10, 2))                  # first page
    a.emit(A.ldrb(14, 10, 3))                  # last page
    a.emit(A.str_(13, bss_reg, BSS_SKY_FIRST))
    a.emit(A.str_(14, bss_reg, BSS_SKY_LAST))
    a.b('sky_done')
    a.label('sky_no')
    a.emit(A.str_(31, bss_reg, BSS_SKY_LAST))
    a.label('sky_done')


def _emit_outdoor(a, outdoor_table, false_label='white'):
    """
    Set bit 2 of the options byte from the build-time bitmap, and leave the
    verdict in w21.

    Only the FIELD knows which field it is in, so only the field tick calls
    this. Battle reads the bit the last field left -- which is exactly what
    PC does, because there the bit is a savemap flag a field script set and
    nothing in a battle touches it.
    """
    # the outdoor bit, from the bitmap rather than from the script
    _translate_imm(a, G_FIELD_ID)
    a.emit(A.ldrh(24, 0, 0))
    a.emit(A.movz(11, 0))
    AC.mov32(a, 9, MAX_FIELD_ID)
    a.emit(A.cmp_reg(24, 9))
    a.bcond('have_flag', HS)
    AC.bss_ptr(a, 10, outdoor_table)
    a.emit(A.lsr(12, 24, 3))
    a.emit(AC.ldrb_uxtw(11, 10, 12))
    a.emit(A.and_mask(12, 24, 3))
    a.emit(lsrv(11, 11, 12))
    a.emit(A.and_mask(11, 11, 1))
    a.label('have_flag')
    a.emit(A.movz(12, OPT_OUTDOOR))
    a.emit(bic_reg(21, 21, 12))
    a.emit(A.lsl(12, 11, 2))
    a.emit(A.orr_lsl(21, 21, 12, 0))
    _translate_imm(a, G_OPTIONS)
    a.emit(A.strb(21, 0, 0))
    # Re-read the decision out of W21 rather than trusting W11. The
    # translator clobbers x0..x18 and x30 -- that is its documented contract,
    # not a conservative guess -- so the flag computed above is gone by here.
    # W21 is callee-saved and holds the byte we just wrote.
    a.emit(A.lsr(9, 21, 2))
    a.emit(A.and_mask(9, 9, 1))
    a.cbz(9, false_label)



def _emit_colour(a, phase_table_addr, gamma_table_addr, bss_reg=20,
                 slots=(BSS_ARMED_INV, BSS_TINT_INV), label_prefix=''):
    """
    Minute of day -> phase -> interpolate -> transfer curve -> selected slots.

    Branches to the caller's `white` label when there is no colour to show.
    `BSS_ARMED_INV` is what the frame decided and `BSS_TINT_INV` is what the
    draw helper reads right now; the UI points below move the second one
    without disturbing the first.
    """
    def label(name):
        return '%s_%s' % (label_prefix, name) if label_prefix else name

    # minute of day, and the phase it lands in
    a.emit(A.movz(9, 60))
    a.emit(A.mul(24, 23, 9))
    a.emit(A.add_reg(24, 24, 22))
    AC.bss_ptr(a, 28, phase_table_addr)
    a.emit(A.movz(25, 0))                      # row index
    a.emit(A.movz(26, 0))                      # the row below this one
    a.label(label('phase'))
    a.emit(A.cmp_imm(25, len(PHASES)))
    a.bcond('white', HS)                       # unreachable: 1440 is a row
    a.emit(A.lsl(9, 25, 3))
    a.emit(AC.add_x_uxtw(10, 28, 9))
    a.emit(A.ldrh(11, 10, 0))
    a.emit(A.cmp_reg(24, 11))
    a.bcond(label('found'), LO)
    a.emit(A.mov_reg(26, 11))
    a.emit(A.add_imm(25, 25, 1))
    a.b(label('phase'))

    a.label(label('found'))
    # w10 = row, w26 = low bound, w11 = high bound, w24 = minute
    a.emit(A.sub_reg(11, 11, 26))              # span
    a.emit(A.sub_reg(24, 24, 26))              # step
    a.emit(A.movz(27, 0))                      # the packed result
    # The interpolation happens in FFNx's own linear-light numbers, and the
    # transfer curve is applied AFTER it, per channel -- that order is the one
    # FFNx uses (it lerps `TimeColor`, then the shader linearises, multiplies
    # and re-encodes). Baking the curve into the phase table instead would
    # interpolate between two already-curved endpoints, which is a different
    # and slightly darker answer through the middle of every transition.
    AC.bss_ptr(a, 9, gamma_table_addr)
    for channel in range(3):
        a.emit(A.ldrb(12, 10, 2 + channel))    # c0
        a.emit(A.ldrb(13, 10, 5 + channel))    # c1
        a.cbz(11, label('flat_%d' % channel))
        a.emit(A.sub_reg(13, 13, 12))
        a.emit(A.mul(13, 13, 24))
        a.emit(sdiv(13, 13, 11))
        a.emit(A.add_reg(12, 12, 13))
        a.label(label('flat_%d' % channel))
        a.emit(AC.ldrb_uxtw(12, 9, 12))        # linear tint -> gamma multiply
        a.emit(A.lsl(27, 27, 8))
        a.emit(A.orr_lsl(27, 27, 12, 0))
    a.emit(A.mvn_reg(27, 27))                  # the slot holds the complement
    for slot in slots:
        a.emit(A.str_(27, bss_reg, slot))


def build_tick_cave(cave, addr, bss, outdoor_table, phase_table_addr,
                    gamma_table_addr, sky_table_addr, frames_per_minute,
                    force_on=True, seed_hour=8, freeze_hour=None):
    """
    The clock and the colour, once per field frame.

    A transcription of `ff7::Time::update` with two deliberate differences.

    The arithmetic is integer. FFNx works in floats and normalised time; this
    works in minutes-of-day and 0..255 channels, because the alternative is
    floating point in a hand-assembled cave for a value that ends up quantised
    to a byte anyway. `phase_colour` in this module is the same computation in
    Python and `tests/test_daynight.py` requires the two to agree on all 1,440
    minutes.

    The outdoor flag comes from the build-time bitmap rather than from the
    script, because `echo_s_flevel` strips the actor that used to set it --
    see this module's header for why that stays stripped.
    """
    a = Asm(cave, addr)
    AC.save_host(a, 0x80)
    AC.bss_ptr(a, 20, bss)
    # Tell the flip cave this frame's clock is already counted. Set before
    # anything can branch away, so a frame that exits early through `white`
    # still cannot be ticked twice.
    a.emit(A.movz(9, 1))
    a.emit(A.str_(9, 20, BSS_TICKED))
    _emit_sky_target(a, sky_table_addr)

    if freeze_hour is not None:
        # SEVENTH_NX_DAYNIGHT_HOUR. Not a feature, an instrument.
        #
        # A day is 32 real minutes and 10 of its 24 hours are night, so
        # answering "does night look like the PC build" from a save whose
        # clock is at four in the afternoon means waiting, in a field, for a
        # colour you are not sure is coming. This pins the clock so one build
        # answers it in ten seconds. The clock still writes nothing to the
        # savemap while pinned, so dropping the variable puts the real one
        # back exactly where it was.
        _translate_imm(a, G_OPTIONS)
        a.emit(A.ldrb(21, 0, 0))
        if force_on:
            a.emit(A.movz(9, OPT_ENABLED | OPT_ADVANCE))
            a.emit(A.orr_lsl(21, 21, 9, 0))
        a.emit(A.and_mask(9, 21, 1))
        a.cbz(9, 'white')
        a.emit(A.movz(22, 0))                  # minutes
        a.emit(A.movz(23, freeze_hour))        # hours
    else:
        _emit_clock(a, frames_per_minute, seed_hour, force_on)

    # Highwind's interiors are intentionally NOT outdoor scenes.  Keep the
    # normal slot white there, but retain this frame's clock colour in the
    # private sky slot for the page-4..14 wrapper alone.
    _emit_colour(a, phase_table_addr, gamma_table_addr,
                 slots=(BSS_SKY_INV,), label_prefix='sky')
    _emit_outdoor(a, outdoor_table, false_label='normal_white')
    _emit_colour(a, phase_table_addr, gamma_table_addr,
                 label_prefix='field')
    a.b('out')

    a.label('normal_white')
    _white(a, 20)
    a.b('out')

    a.label('white')
    _white(a, 20)
    # This is the cycle-disabled/error exit.  Do not let an old sky colour
    # survive into a later fship field if the option bit is now off.
    a.emit(A.str_(31, 20, BSS_SKY_INV))
    a.emit(A.str_(31, 20, BSS_SKY_LAST))
    a.emit(A.str_(31, 20, BSS_SKY_ACTIVE))

    a.label('out')
    AC.restore_host(a, 0x80)
    a.emit(TICK_ORIG)
    a.emit(A.b(a.pc(), TICK_RESUME))
    return a.resolve()


# ------------------------------------------------------------- installation
SITES = (
    ('tick', TICK_HOOK, TICK_ORIG, 'field_draw_everything entry'),
    ('sky_page', SKY_PAGE_HOOK, SKY_PAGE_ORIG,
     'the per-field-page graphics-object draw'),
    ('clear', CLEAR_HOOK, CLEAR_ORIG, 'the word after the gray-quads call'),
    ('block', BLOCK_HOOK, BLOCK_ORIG, 'the BlockVertex hand-over in the draw '
                                      'submission helper'),
    ('flip', FLIP_HOOK, FLIP_ORIG, 'gfx_drv_flip prologue'),
    ('mode', MODE_HOOK, MODE_ORIG, 'set_driver_mode prologue'),
    ('world_off1', WORLD_OFF1_HOOK, WORLD_BL_ORIG[WORLD_OFF1_HOOK],
     'the world map\'s minimap-quad draw'),
    ('world_on', WORLD_ON_HOOK, WORLD_BL_ORIG[WORLD_ON_HOOK],
     'the world map\'s world-effects draw'),
    ('world_off2', WORLD_OFF2_HOOK, WORLD_BL_ORIG[WORLD_OFF2_HOOK],
     'the world map\'s minimap-points draw'),
    ('battle_text', BATTLE_TEXT_HOOK, BATTLE_BL_ORIG[BATTLE_TEXT_HOOK],
     'the battle text-UI draw'),
    ('battle_box', BATTLE_BOX_HOOK, BATTLE_BL_ORIG[BATTLE_BOX_HOOK],
     'the battle box-UI draw'),
    ('battle_menu', BATTLE_MENU_HOOK, BATTLE_BL_ORIG[BATTLE_MENU_HOOK],
     'the battle menu marker'),
)

# (name, hook, displaced word, before the call, after it). `False` is FFNx's
# setTimeFilterEnabled(false), `True` its (true), `None` leave it alone.
# The battle pair are WRAPPERS -- off, call, on -- which is literally FFNx's
# `draw_ui_graphics_objects_wrapper`; the rest only act before the call.
UI_SITES = (
    ('world_off1', WORLD_OFF1_HOOK, WORLD_BL_ORIG[WORLD_OFF1_HOOK],
     False, None),
    ('world_on', WORLD_ON_HOOK, WORLD_BL_ORIG[WORLD_ON_HOOK], True, None),
    ('world_off2', WORLD_OFF2_HOOK, WORLD_BL_ORIG[WORLD_OFF2_HOOK],
     False, None),
    ('battle_text', BATTLE_TEXT_HOOK, BATTLE_BL_ORIG[BATTLE_TEXT_HOOK],
     False, True),
    ('battle_box', BATTLE_BOX_HOOK, BATTLE_BL_ORIG[BATTLE_BOX_HOOK],
     False, True),
    # `battle_menu_enter` has no matching (true): FFNx leaves the filter off
    # for the rest of the frame and lets `Time::update` arm the next one, so
    # everything the battle menu draws after this point is untinted.
    ('battle_menu', BATTLE_MENU_HOOK, BATTLE_BL_ORIG[BATTLE_MENU_HOOK],
     False, None),
)

# `tick` is not here: it is the only cave that needs the two tables, so
# `build_all` places it by hand. These three take nothing but the BSS base.
STATE_ONLY_CAVES = (
    ('clear', build_clear_cave),
    ('block', build_block_cave),
    ('mode', build_mode_cave),
    ('sky_page', build_sky_page_cave),
)

SHADER_MARK = '0x44594E'


def shaders_carry_the_tint(sdout, title_id):
    """
    Is the vertex half of this feature on the card?

    Both halves are individually harmless, which is the design -- but a build
    that installs one and not the other is a build whose day/night silently
    does nothing, and that is worth a line in the log rather than a puzzled
    evening. The shaders come from `ff7nx_ws`, so the realistic way to get
    here is 16:9 turned off.
    """
    shaders = os.path.join(sdout, 'atmosphere', 'contents', title_id,
                           'romfs', 'ff7', 'shaders')
    for name in ('lmain_vv.glsl', 'tlmain_vv.glsl'):
        path = os.path.join(shaders, name)
        try:
            if SHADER_MARK not in open(path).read():
                return False
        except OSError:
            return False
    return True


def check_block_shape(text):
    """
    Refuse to install unless the draw helper still has the shape the tint
    assumes: every path converging on the hand-over, and 0x50 as the size.

    `expect_word` already pins the one instruction we displace. This pins the
    two facts AROUND it that make writing into the caller's stack correct --
    that the block really is 80 bytes, and that no draw reaches the uploader
    by a route that skips our word. Both are one `struct.unpack_from` and both
    are what a future port revision, or another pass widening its own patch in
    +0x10D9D70, would break silently.
    """
    size, = struct.unpack_from('<I', text, BLOCK_SIZE_SITE)
    if size != BLOCK_SIZE_WORD:
        raise ValueError('daynight: +0x%X holds %08X, not `mov w6, #0x%X` -- '
                         'the uniform block this tint rides in is no longer '
                         'the size it was derived against'
                         % (BLOCK_SIZE_SITE, size, BLOCK_SIZE))
    for site, target in BLOCK_JOINS:
        word, = struct.unpack_from('<I', text, site)
        if word != A.b(site, target):
            raise ValueError('daynight: +0x%X no longer branches to +0x%X, so '
                             'a draw can reach the uniform upload without '
                             'passing the tint' % (site, target))


def check_driver_modes(text, rodata, rodata_base):
    """
    Refuse to install unless FIELD is still driver mode 2.

    The clock ticks in modes 2..4 because `ff7nx_letterbox` identified FIELD
    as 2 -- the dispatch index that computes the field letterbox -- and
    confirmed it on hardware. That is the one number BATTLE and WORLDMAP hang
    off, so it is checked rather than remembered: the 15-entry table at
    .rodata 0x11B3C74 must still send `mode - 1 == 1` to the branch that loads
    the letterbox fraction.
    """
    table = MODE_TABLE - rodata_base
    if table < 0 or table + MODE_CASES * 4 > len(rodata):
        raise ValueError('daynight: the driver-mode dispatch table is not in '
                         'this module\'s .rodata')
    field, = struct.unpack_from('<i', rodata, table + (DRIVER_FIELD - 1) * 4)
    if MODE_TABLE + field != MODE_FIELD_CASE:
        raise ValueError('daynight: driver mode %d no longer dispatches to '
                         '+0x%X, so FIELD is not %d in this module and the '
                         'clock would tick in the wrong three modes'
                         % (DRIVER_FIELD, MODE_FIELD_CASE, DRIVER_FIELD))


def _count_calls(text, body, end, callee, total, pick, want, what):
    """
    Count every `bl` to `callee` inside `body`..`end` and require the `pick`th
    ones to be exactly `want`.

    This is the whole reason the battle, world and sky points are
    trustworthy. They are CALL SITES, which the port's x86 -> ARM64 map does
    not carry -- it only maps function entries. But the recompiler preserved
    call order exactly, so a site is identified by position, and the position
    is re-counted here at install time rather than remembered from a session.
    The total is checked too: a port revision that inserts or drops a draw
    stops the build instead of quietly moving the tint onto the wrong one.
    """
    found = []
    for va in range(body, end, 4):
        word, = struct.unpack_from('<I', text, va)
        if (word >> 26) != 0b100101:
            continue
        imm = word & 0x3FFFFFF
        if imm & (1 << 25):
            imm -= 1 << 26
        if va + (imm << 2) == callee:
            found.append(va)
    got = [found[n - 1] for n in pick if n <= len(found)]
    if len(found) != total or got != want:
        raise ValueError('daynight: the %s calls moved -- %d of them in '
                         '+0x%X (expected %d), and %s are %s, not %s'
                         % (what, len(found), body, total, list(pick),
                            ['+0x%X' % v for v in got],
                            ['+0x%X' % v for v in want]))


def check_scene_sites(text):
    """
    Refuse to install unless battle and the world map still have the shape
    the UI points were resolved from. Six call sites in two functions, every
    one of them counted rather than remembered.
    """
    _count_calls(text, WORLD_DRAW_ALL, WORLD_DRAW_ALL_END, WORLD_DRAW_OBJ,
                 15, WORLD_CALLS,
                 [WORLD_OFF1_HOOK, WORLD_ON_HOOK, WORLD_OFF2_HOOK],
                 'world map draw')
    _count_calls(text, BATTLE_MAIN_LOOP, BATTLE_MAIN_LOOP_END, BATTLE_UI,
                 4, BATTLE_WRAPPED, [BATTLE_TEXT_HOOK, BATTLE_BOX_HOOK],
                 'battle UI draw')
    _count_calls(text, BATTLE_MAIN_LOOP, BATTLE_MAIN_LOOP_END,
                 BATTLE_MENU_CALLEE, 2, (1,), [BATTLE_MENU_HOOK],
                 'battle menu marker')


def check_sky_page_site(text):
    """
    Prove the word the sky bracket reads is still the page number, and that
    the call it wraps is still the only draw in that loop.
    """
    saved, = struct.unpack_from('<I', text, SKY_PAGE_SLOT_SAVE)
    if saved != SKY_PAGE_SLOT_SAVE_ORIG:
        raise ValueError('daynight: +0x%X no longer stores the page number '
                         'to [x19], so which page is sky cannot be read'
                         % SKY_PAGE_SLOT_SAVE)
    if not SKY_PAGE_SLOT_SAVE < SKY_PAGE_HOOK < SKY_PAGE_BODY_END:
        raise ValueError('daynight: the sky bracket is not between the page '
                         'number and the end of its loop')
    # ... and nothing between them writes [x19] again
    for va in range(SKY_PAGE_SLOT_SAVE + 4, SKY_PAGE_HOOK, 4):
        word, = struct.unpack_from('<I', text, va)
        if word == SKY_PAGE_SLOT_SAVE_ORIG:
            raise ValueError('daynight: +0x%X overwrites the page number '
                             'before the draw' % va)
    _count_calls(text, SKY_PAGE_BODY, SKY_PAGE_BODY_END, WORLD_DRAW_OBJ,
                 1, (1,), [SKY_PAGE_HOOK], 'field background page draw')


def build_all(pool, space, bss, bitmap, frames_per_minute, force_on=True,
              freeze_hour=None, strength=DEFAULT_STRENGTH, sky_rows=()):
    """Place the three tables and runtime caves. Returns entries and patches."""
    outdoor_at = space.place('daynight-outdoor', bitmap, align=4)
    phase_at = space.place('daynight-phase', phase_table(), align=4)
    gamma_at = space.place('daynight-gamma', gamma_lut(strength), align=4)
    sky_at = space.place('daynight-sky', sky_table(sky_rows), align=4)
    entries, placed = {}, {}
    entries['tick'], words = ff7nx_cave.emit_laid_out(
        pool, lambda cave, addr: build_tick_cave(
            cave, addr, bss, outdoor_at, phase_at, gamma_at, sky_at,
            frames_per_minute, force_on=force_on, freeze_hour=freeze_hour))
    placed.update(words)
    entries['flip'], words = ff7nx_cave.emit_laid_out(
        pool, lambda cave, addr: build_flip_cave(
            cave, addr, bss, frames_per_minute, phase_at, gamma_at,
            force_on=force_on,
            seed_hour=None if freeze_hour is not None else 8))
    placed.update(words)
    entries['sky_page'], words = ff7nx_cave.emit_laid_out(
        pool, lambda cave, addr: build_sky_page_cave(cave, addr, bss))
    placed.update(words)
    for name, builder in STATE_ONLY_CAVES:
        entries[name], words = ff7nx_cave.emit_laid_out(
            pool, lambda cave, addr, _b=builder: _b(cave, addr, bss))
        placed.update(words)
    for name, hook, orig, before, after in UI_SITES:
        entries[name], words = ff7nx_cave.emit_laid_out(
            pool, lambda cave, addr, _h=hook, _o=orig, _b=before, _a=after:
            build_ui_cave(cave, addr, bss, _h, _o, _b, _a))
        placed.update(words)
    for name, site, _orig, _what in SITES:
        placed[site] = A.b(site, entries[name])
    return entries, placed, outdoor_at, phase_at, gamma_at, sky_at


FREEZE_ENV = 'SEVENTH_NX_DAYNIGHT_HOUR'


def freeze_hour_from_env():
    """
    `SEVENTH_NX_DAYNIGHT_HOUR=22` -> pin the clock at 22:00, or None.

    Registered in `build.MAIN_ONLY_ENV`, so setting it cannot invalidate an
    archive cache -- this pass writes into `exefs/main` and nowhere else, and
    build 467 is the record of what happens when a module-only variable is
    left in the generic sweep.
    """
    raw = os.environ.get(FREEZE_ENV, '').strip()
    if not raw:
        return None
    try:
        hour = int(raw, 0)
    except ValueError:
        raise ValueError('%s=%r is not a whole hour' % (FREEZE_ENV, raw))
    if not 0 <= hour <= 23:
        raise ValueError('%s=%d is not an hour of the day' % (FREEZE_ENV, hour))
    return hour


def apply_to_nso(src, dest, bitmap, space=None, fps=60,
                 force_on=True, freeze_hour=None, strength=DEFAULT_STRENGTH,
                 sky_rows=()):
    """
    Install the day/night runtime, patching `src` into `dest`.

    `bitmap` is what `outdoor_bitmap` produced; it is a required argument
    rather than something this function goes and derives, because deriving it
    means reading 703 field scripts and that belongs to the caller who already
    knows whether Echo-S is even in the load order.

    `fps` scales the minute. FFNx multiplies `frames_per_minute` by its own
    frame multiplier so a cycle takes the same wall-clock time at 30 and at
    60; the same correction here keeps a day eight minutes long whatever the
    limiter is set to.
    """
    if len(bitmap) != BITMAP_BYTES:
        raise ValueError('the outdoor bitmap is %d bytes, not %d'
                         % (len(bitmap), BITMAP_BYTES))
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw, own = ff7nx_tables.open_module(blob)
    space = own if space is None else space
    text = space.text
    for _name, site, orig, what in SITES:
        AC.expect_word(text, site, orig, 'daynight: %s' % what)
    check_block_shape(text)
    check_sky_page_site(text)
    check_driver_modes(text, space.rodata, segs[1][1])
    check_scene_sites(text)
    bss = AC.scratch_base(blob, segs)
    # FFNx: `modeFramesPerMinute = framesPerMinute * 2 * common_frame_multiplier`
    # for field and world, where the multiplier is 1 at 30 FPS. The factor of
    # two is NOT part of the frame-rate correction -- it is in the constant
    # FFNx compares against at 30 as well, and dropping it ran the day at
    # double speed, sixteen real minutes instead of the thirty-two the mod's
    # own config documents for frames_per_minute = 20.
    frames = max(1, int(round(FRAMES_PER_MINUTE * 2 * (fps / 30.0))))

    # KNOWN, DELIBERATELY NOT FIXED IN THIS BUILD -- see FINDINGS-484.
    # `cave_space.find_holes_in` rejects a run of zeros that a word in
    # `.rodata`/`.data` points at; passing `text` alone makes that scan's input
    # empty, so on this main it admits 128 extra runs (288 words) and 88 of
    # this module's 387 runs sit in them. `ff7nx_voice._verified_hole_pool` is
    # the correct construction. Changing it here moves every day/night cave,
    # and this build is carrying one variable, not two.
    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))
    entries, placed, outdoor_at, phase_at, gamma_at, sky_at = build_all(
        pool, space, bss, bitmap, frames, force_on=force_on,
        freeze_hour=freeze_hour, strength=strength, sky_rows=sky_rows)
    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)

    out = AC.pack(blob, space.commit(), BSS_BYTES)
    _segs, check_raw = AC.segments(out)
    for name, site, _orig, _what in SITES:
        got = struct.unpack_from('<I', check_raw[0], site)[0]
        if got != A.b(site, entries[name]):
            raise ValueError('daynight: the %s hook did not survive the '
                             'repack' % name)
    old_bss, = struct.unpack_from('<I', blob, 0x3C)
    if struct.unpack_from('<I', out, 0x3C)[0] != old_bss + BSS_BYTES:
        raise ValueError('daynight: BSS did not grow by the state block')

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    minutes_per_day = DAY_MINUTES * frames / float(fps) / 60.0
    return {'bss_bytes': BSS_BYTES, 'bss_base': bss, 'entries': entries,
            'cave_words': len(placed) - len(SITES),
            'table_bytes': (len(bitmap) + len(phase_table()) + len(GAMMA_LUT)
                            + len(sky_table(sky_rows))),
            'strength': strength,
            'outdoor_table': outdoor_at, 'phase_table': phase_at,
            'gamma_table': gamma_at, 'gamma': GAMMA,
            'sky_table': sky_at, 'sky_rows': tuple(sky_rows),
            'frames_per_minute': frames, 'fps': fps,
            'day_minutes': minutes_per_day, 'force_on': force_on,
            'freeze_hour': freeze_hour,
            'outdoor_fields': sum(bin(b).count('1') for b in bitmap)}
