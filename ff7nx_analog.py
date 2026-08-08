#!/usr/bin/env python3
"""
ff7nx_analog.py -- 360 degree field movement: the direction model.

WHAT THE FEATURE IS
===================
FF7 fields move the player in one of EIGHT directions. Which way "up" points
in world space is decided by a per-field `control direction`, a signed short
living inside the field's own level data:

    level_data      = *field_level_data_pointer          (0xCFF594)
    triggers_offset = *(uint32_t*)(level_data + 0x22)
    control_dir     = (signed short*)(level_data + triggers_offset + 4 + 9)

FFNx's 360 degree movement does not touch the movement engine at all. It
leaves the game believing you pressed one of its eight directions and instead
rewrites that short every frame:

    offset      = angle between the snapped 8-direction and the true stick
    *control_dir = base_control_direction + offset

so the eight-way input, rotated by a nudged world rotation, comes out pointing
exactly where the stick does. `base_control_direction` is the field's own
value, re-read whenever `field_id` (0xCFF468) changes.

WHY THIS MODULE EXISTS
======================
That formula needs `atan2`, and the cave that will run it is hand-assembled
ARM64 with no libm to call. This module is the integer model the cave
implements, kept here so it can be tested against real `math.atan2` on the
host before any of it is encoded -- and so the cave can be diffed against a
reference rather than eyeballed.

THE MODEL
=========
Octant fold plus a small table. For |y| <= |x| the angle is `atan(|y|/|x|)`,
which is 0..32 units; everything else is that value reflected into one of the
eight octants. So the whole of `atan2` reduces to ONE table of 65 bytes and a
handful of integer ops -- no floats in the angle path at all, which matters
because it makes the cave exactly reproducible and lets `arm64emu` check it.

    ATAN_TAB[i] = round(atan(i/64) * 128/pi)      i = 0..64,  values 0..32

ACCURACY, MEASURED (see test_analog.py, 36000-point sweep)
==========================================================
    table size   worst error            rms error
    17 bytes     1.727 units (2.43 deg) 0.678 units
    33 bytes     1.090 units (1.53 deg) 0.417 units
    65 bytes     0.771 units (1.08 deg) 0.322 units   <- chosen

One FF7 direction unit is 1.406 degrees. At 65 bytes the worst case is
**0.771 units -- less than one unit**, so the model is never further from the
true angle than the granularity of the value the game stores. Going bigger
cannot buy anything the game can represent.

UNITS AND SIGN
==============
`dir256` returns 0..255 with 0 = +x (stick right) and 64 = +y (stick up).
That convention is arbitrary and it CANCELS: the cave only ever uses the
DIFFERENCE between the stick's direction and the snapped direction, and both
are measured the same way. Nothing here needs to know which way FF7 thinks
north is, which removes the one assumption that would have been hard to check.
"""
import math

N = 64
ATAN_TAB = [int(round(math.atan(i / float(N)) * 128.0 / math.pi))
            for i in range(N + 1)]
assert len(ATAN_TAB) == 65 and ATAN_TAB[0] == 0 and ATAN_TAB[N] == 32
assert all(0 <= v <= 32 for v in ATAN_TAB)

# The eight snapped directions, in the same units. The port maps the stick to
# FF7's four direction keys (DIK_NUMPAD 8/2/4/6); a diagonal is two of them.
#   bit 0 right, bit 1 up, bit 2 left, bit 3 down
SNAP = {
    0b0001: 0,      # right
    0b0011: 32,     # up-right
    0b0010: 64,     # up
    0b0110: 96,     # up-left
    0b0100: 128,    # left
    0b1100: 160,    # down-left
    0b1000: 192,    # down
    0b1001: 224,    # down-right
}


def dir256(ix, iy):
    """
    Stick vector (fixed-point ints) -> 0..255, or None if centred.

    Integer only, and deliberately written the way the cave will execute it:
    one absolute value each, one compare, one divide, one table load.
    """
    ax, ay = abs(ix), abs(iy)
    if ax == 0 and ay == 0:
        return None
    if ax >= ay:
        idx = (ay * N * 2 + ax) // (ax * 2)          # round(ay/ax * N)
        a = ATAN_TAB[idx]
        if iy >= 0:
            d = a if ix >= 0 else 128 - a
        else:
            # NOTE the sign: reflecting the SECOND quadrant through the x axis
            # gives a - 128, not -128 - a. Writing the latter puts the whole
            # third quadrant 64 units out and it is invisible on the cardinals
            # -- test_analog.py's down-left case is what catches it.
            d = -a if ix >= 0 else a - 128
    else:
        idx = (ax * N * 2 + ay) // (ay * 2)          # round(ax/ay * N)
        a = ATAN_TAB[idx]
        d = (64 - a) if ix >= 0 else (64 + a)
        if iy < 0:
            d = -d
    return d % 256


def offset(ix, iy, keymask):
    """
    The value FFNx adds to the field's base control direction.

    `keymask` is the four direction keys the port is actually asserting, so
    the snapped direction is READ rather than re-derived -- the port's own
    deadzone decides it, and guessing that threshold would be the easiest way
    to get this subtly wrong.

    Returns 0 when the stick is centred or the keys are not one of the eight
    shapes, which is what makes the feature inert rather than merely quiet.
    """
    if keymask not in SNAP:
        return 0
    d = dir256(ix, iy)
    if d is None:
        return 0
    o = (d - SNAP[keymask]) % 256
    if o > 128:
        o -= 256
    return max(-128, min(128, o))


# ==========================================================================
# THE CAVE
# ==========================================================================
# ONE hook: `0x947CF0`, inside `field_loop_sub_63C17F`, the instruction right
# after the single `bl` to the field's input read (`sub_6499F7`). Once per
# field frame, with the key state and the stick both current, and -- proven
# below -- before anything reads the control direction.
#
# HOW THE FIELD ACTUALLY USES THE CONTROL DIRECTION -- PROVEN
# ==========================================================
# This was never traced before and it is worth writing down, because it is
# what makes the whole approach valid on this port.
#
#   0x63C17F+0x10C  call sub_6499F7          ; -> EAX = the input mask
#   0x63C17F+0x111  <-- OUR HOOK
#   0x63C17F+0x114  mov [ebp-0x18], eax
#   0x63C17F+0x5DD  call field_update_models_positions(0x6342C6) with that mask
#
# and inside 0x6342C6, for the player model:
#
#   0x634A3A  test the mask's 0xF000 bits; none set -> skip
#   0x634A61.. eight `mov byte [i*0x88 + 0xCC16A6], <const>` with the
#             constants 0x00 0x20 0xE0 0x80 0x60 0xA0 0xC0 0x40 -- the
#             eight-way snap, one of the 8 multiples of 32
#   0x634B48  mov  eax, [0xCFF454]           ; the triggers header
#   0x634B4D  movsx ecx, byte [eax+9]        ; THE CONTROL DIRECTION
#   0x634B63  add  ecx, model.field_35
#   0x634B6B  add  dl, cl                    ; snapped + control direction
#   0x634B77  mov  byte [i*0x88+0xCC16A6], dl ; model.rotation_value
#   0x634B82  call 0x636C41                  ; move the model
#
# and the movement itself, in 0x636C41:
#
#   0x636F73  al = model.rotation_value
#   0x636F7A  call 0x6364EB  ->  ax = tab[(dir & 0xFF) * 4]      at 0x908E30
#   0x636FAC  call 0x636500  ->  ax = tab[(dir & 0xFF) * 4 + 2]
#
# A **256-entry** sin/cos table indexed by the full byte. So the direction the
# player walks in has full 1/256-of-a-circle resolution, and adding an offset
# to the control direction moves it by exactly that much. `0x634B4D` is the
# ONLY read of +9 anywhere in the executable, and nothing writes it, so this
# cave is its sole author.
#
# WHERE THE STICK COMES FROM -- and why this is no longer an assumption
# ====================================================================
# The first version of this feature hooked the port's input poll (0x111BFC0)
# and recorded its `this` pointer, on the assumption that the object being
# polled was the object the game reads. That assumption was never verified,
# it is the one thing the feature rested on that was not measured, and the
# feature did not work on hardware.
#
# It is not needed. The port's own axis getters resolve the object themselves,
# and we can do exactly what they do. From `GetAxis` (0x1D80) and
# `IsButtonHeld` (0x1AD0), both at their common tail 0x1DC0:
#
#   adrp x8, #0x12ce000
#   ldr  x8, [x8, #0x1d0]      ; -> the singleton holder in bss
#   ldr  x8, [x8]
#   ldr  x8, [x8, #8]
#   ldr  x8, [x8]
#   ldr  x0, [x8, #0x88]       ; the input object
#   cbz  x0, ...               ; the game's own null check
#
# Every route through those getters -- including all four of the
# region/config variants at 0x1DF0..0x1EA4 -- funnels through that same tail,
# so there is exactly one such object and this is it.
#
# That matters because it is the object the DirectInput emulation reads to
# decide which direction scancodes to assert:
#
#   IsHeld(obj, 0x10..0x13)  ==  *(float*)(obj + 0x30..0x3C) > 0.4f
#   0x10D3A5C: KEYBUF[dik] = 0x80  for  0x10->UP 0x11->DOWN 0x12->RIGHT
#                                        0x13->LEFT
#
# So the four floats this cave reads are, by construction, the same four the
# port thresholded into the four scancodes this cave reads. If the character
# moves at all, those floats are live and this object is the right one --
# which is a proof, where the poll hook was a guess.
#
# THE INPUT OBJECT LAYOUT -- MEASURED
# ===================================
#   0x111BFC0  the poll. Calls the port's nn::hid read, then at +0x1A4..+0x204
#              stores the LEFT stick as four SPLIT, NORMALISED floats:
#                  str s5, [x19, #0x30]   max(Ly, 0)   "up"
#                  str s0, [x19, #0x34]  -min(Ly, 0)   "down"
#                  str s5, [x19, #0x38]   max(Lx, 0)   "right"
#                  str s1, [x19, #0x3C]  -min(Lx, 0)   "left"
#   0x111BF60  GetAxis, vtable+0x50: for idx >= 0x10, `ldur s0, [x0, w1*4-0x10]`
#              -- so axis 0x10 IS +0x30, 0x11 is +0x34, 0x12 +0x38, 0x13 +0x3C.
#   0x111BE00  IsHeld, vtable+0x38: for idx >= 0x10, that float > 0.4f.
#   0x10D3820  the DirectInput `GetDeviceState` emulation: zeroes 0x100 bytes
#              at 0x12CF2AC and writes 0x80 per pressed key, and its jump
#              table (0x11B2A98) maps ids 0x10..0x13 to DIK 0x48/0x50/0x4D/0x4B.
#
# THE CONTROL DIRECTION IS A BYTE
# ===============================
# FFNx reads and writes it as a `signed short`. The game does not -- 0x634B4D
# is a `movsx byte`, and the byte after it is `focus_height`, read as a word
# at 0x63C02C. A 16-bit store corrupts focus_height for the sake of a value
# the game only ever reads 8 bits of. This cave stores a byte.
#
# THE TRANSLATOR IS A PAGE TABLE
# ==============================
# `TRANSLATE` (0x10FC3A0) is not a base + offset:
#
#     lsr w9, w0, #0xc                 ; guest page number
#     ldr x8, [x8, w9, uxtw #3]        ; host base of THAT page
#     and w9, w0, #0xfff
#     add x9, x8, x9
#
# Guest memory is mapped in 4 KB pages and consecutive guest pages are not
# consecutive host pages, so `TRANSLATE(p) + n` is the address of `p + n` only
# while `n` stays inside the same page. An earlier version did
# `TRANSLATE(level_data) + triggers_offset`, and `triggers_offset` measured
# across flevel.lgp is 25-55 KB -- six to thirteen pages. The rule, which the
# recompiled code follows everywhere, is: do the arithmetic in GUEST space and
# translate the finished address.
#
# It also no longer walks the section table at all: the game caches the
# triggers pointer for us.
#
#     0x6211C3  push 7 ; call get_field_section ; mov [0xCFF440], eax
#                                                mov [0xCFF454], eax
#     0x6308CA  get_field_section(i):
#                   return [0xCFF594] ? [0xCFF594] + [0xCFF570 + 4*i] + 4 : 0
#
# `[0xCFF594]` is still read first, as the "is a field actually loaded" guard:
# between fields the level buffer is freed and 0xCFF594 zeroed (0x6308F0), but
# 0xCFF454 keeps the stale pointer.
#
# SCRATCH (module offsets, in the BSS the build grows)
#     +0x00  w  the field_id the base was captured for
#     +0x04  w  captured flag
#     +0x08  w  base control direction (sign-extended), valid iff +0x04 != 0
ANALOG_BASE = 0x3FEC4A8          # after THROTTLE_BASE (0x3FEC3C8) + 0xE0
ANALOG_GROW = 0x1A0              # total bss growth when this group is on

FIELD_HOOK = 0x947CF0
FIELD_ORIG = 0x29422728          # ldp w8, w9, [x25, #0x10]

# The port's input-object resolution, copied instruction for instruction from
# the tail every one of its axis and button getters shares (0x1DC0).
INPUT_GOT = 0x12CE1D0            # holder of the singleton pointer
INPUT_CHAIN = (0, 8, 0, 0x88)    # dereference offsets after the first load

TRANSLATE = 0x10FC3A0            # guest VA in w0 -> host pointer in x0
KEYBUF = 0x12CF2AC               # the port's DirectInput key state
LEVEL_PTR_GUEST = 0xCFF594       # field_level_data_pointer -- the loaded guard
TRIGGERS_PTR_GUEST = 0xCFF454    # the game's own cached triggers-section ptr
CONTROL_DIR_OFF = 9              # control direction, one signed byte, at +9
FIELD_ID_GUEST = 0xCFF468        # field_id

# The four floats on the port's input object, pinned to axis ids by 0x111BF60.
# Split positive/negative pairs: the cave takes the DIFFERENCE of each pair, so
# a stick the port has already deadzoned to zero cancels.
OBJ_UP, OBJ_DOWN, OBJ_RIGHT, OBJ_LEFT = 0x30, 0x34, 0x38, 0x3C

# DirectInput scancodes the port asserts for the four directions.
DIK_UP, DIK_DOWN, DIK_LEFT, DIK_RIGHT = 0x48, 0x50, 0x4B, 0x4D

# SNAP as a 16-entry table indexed by the 4-bit key mask; 0xFF = "not one of
# the eight", which makes the cave write the base unchanged.
SNAP_TAB = [SNAP.get(i, 0xFF) for i in range(16)]
