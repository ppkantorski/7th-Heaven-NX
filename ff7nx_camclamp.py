#!/usr/bin/env python3
r"""
ff7nx_camclamp.py -- clamp the SCRIPTED camera to the camera range, which
vanilla FF7 never does.

    python3 ff7nx_camclamp.py <exefs/main | sdout> --verify
    python3 ff7nx_camclamp.py <exefs/main | sdout> --show
    python3 ff7nx_camclamp.py <exefs/main | sdout> --apply
    python3 ff7nx_camclamp.py <exefs/main | sdout> --revert

=============================================================================
0. THE SYMPTOM, AND THE NUMBER THAT IDENTIFIES IT
=============================================================================
Sector 1 station, the Biggs conversation. The camera pans right and a black
band appears down the right-hand side of the frame. Measured off the capture
rather than eyeballed:

    content columns 0..1119 of 1280      ->  a black band exactly 160 px wide

160 px at 720p is 53.4 field units (the 16:9 field view is 427 units across
1280 px). **53 is FFNx's number**, and it appears in exactly one place:

    background.cpp:436   half_width = 160 + std::min(53, cameraRangeSize/2 - 160)

That is not a coincidence and it is not field-specific. For any field whose
camera range is at least 426 units wide, a camera sitting at the stock 4:3
bound shows exactly `213 - 160 = 53` units past the edge of the art in 16:9.
The band width alone identifies the mechanism: **the camera reached a
position the widescreen clamp forbids.**

=============================================================================
1. WHY THE EXISTING COMPENSATION DOES NOT COVER IT
=============================================================================
`ff7nx_ws` already implements FFNx's clamp, as an identity on the data rather
than as code: it pulls each field's camera range in by `half_width - 160`, so
the stock `+/-160` compare produces FFNx's bounds. The build log says so --

    camera ranges written: 444 (config 41, clamp 432)

-- and the shipped archive agrees; `nmkin_1` reads `-258 .. 258` where vanilla
has `-312 .. 312`, i.e. pulled in by 54 on each side, giving camera travel
+/-98 instead of +/-152. At +/-98 the 16:9 view lands flush on the art.

So the compensation is correct, and it is in the build. It just never runs on
the path that produced this frame.

    x86 field_update_background_positions +0x126
        movsx edx, [0xCC15E4]        the "a script is moving the camera" flag
        jne   0x644674               -> SKIPS the entire normal camera block,
                                        including the +0x2B7 call to
                                        field_clip_with_camera_range

and inside `field_update_scripted_bg_movement` (x86 0x643D22), disassembled
branch by branch:

    mode 1  0x643D5B   call 0x643C86 ; call 0x6438F6     CLAMPED
    mode 2  0x643D91   call 0x643C86 ; call 0x6438F6     CLAMPED
    mode 3  0x643E4F   call 0x643C86 ; call 0x6438F6     CLAMPED
    mode 4  0x643F0D   interpolate only                  NOT CLAMPED
    mode 5  0x643FB5   interpolate only                  NOT CLAMPED
    mode 6  0x643FB5   interpolate only                  NOT CLAMPED

and `field_init_scripted_bg_movement` (0x64341C) clamps in no mode at all --
it copies the script's numbers into `[0xCC15F0]` verbatim.

That is fine in 4:3: the script author aimed at a legal 4:3 camera position,
so the 4:3 view is inside the art by construction. In 16:9 the view is 107
units wider and the same target runs off the end.

FFNx hit exactly this and fixed it in exactly this place -- it adds
`field_widescreen_width_clip_with_camera_range` to both functions, eight call
sites in all, including one **unconditional** clamp of `field_curr_delta_world_pos`
at the very end of the update (background.cpp:855). This module is that last
one, and only that one.

=============================================================================
2. WHY THE PATCH IS THE STOCK +/-160 AND NOT FFNx's half_width
=============================================================================
FFNx computes `half_width = 160 + min(53, size/2 - 160)` at runtime because
its camera range is the vanilla one. Ours is not: `ff7nx_ws` already baked the
same adjustment into section 8. Re-deriving `half_width` here would apply the
correction twice -- `nmkin_1` would clamp to 258-213 = +/-45 instead of +/-98
and the camera would stop dead short of the art.

So the cave uses the stock bounds, `left + 160` and `right - 160`, read from
the live `field_triggers_header`. One compensation, one owner, and this module
is arithmetically identical to `field_clip_with_camera_range`'s x-half:

    x86 0x6438F6
        if (p->x > hdr[0x10] - 0xA0) p->x = hdr[0x10] - 0xA0     right - 160
        if (p->x < hdr[0x0C] + 0xA0) p->x = hdr[0x0C] + 0xA0     left  + 160

`field_trigger_header` is section 8's body verbatim -- `field_name[9]`,
`control_direction`, `focus_height`, then `camera_range` at +0x0C -- which is
the offset `ff7nx_ws` writes, so the bake and this clamp read the same four
shorts by construction.

=============================================================================
3. THE HOOK -- THE ONE POINT EVERY MODE CONVERGES ON
=============================================================================
    +0x9F874C   ldr w0, [x22, #0x14]      <- HOOK  (x86 0x644055, `mov esp,ebp`)

`field_update_scripted_bg_movement` recompiles to +0x9F7DB0..+0x9F8790, and
every one of its seven mode branches ends with `b #0x9f874c`. Hooking the
epilogue rather than each branch means one cave instead of six, and it also
catches `field_init_scripted_bg_movement`'s unclamped writes, because those
land in `[0xCC15F0]` and the next frame's update passes through here.

REGISTERS, READ OUT OF THE PROLOGUE RATHER THAN ASSUMED
-------------------------------------------------------
    +0x9F7DC4  adrp x22, #0x12ce000  \  the guest CPU context; still live at
    +0x9F7DC8  ldr  x22, [x22, #0x2b0] /  the hook (used by the next word)
    +0x9F7DCC  mov  w19, #0x15ec     \  0xCC15EC. w19 is written ONCE, in the
    +0x9F7DD0  movk w19, #0xcc, lsl #16 /  prologue, and never again -- so
                                          w19 + 4 is field_curr_delta_world_pos_x

The function's prologue saves x23, x22, x21, x20, x19, x29, x30 and the
epilogue restores them AFTER the hook, so w20 and w21 are free scratch here
and x30 may be clobbered by `bl`. x24-x28 are NOT saved by this function and
are therefore the caller's -- the cave does not touch them.

    guest globals are reached the way the recompiler reaches them:
    materialise the 32-bit guest address in w0, `bl #0x10fc3a0`, use x0.
    That helper is AAPCS, so x19-x23 survive it; the recompiled code relies
    on the same thing four instructions above the hook.

=============================================================================
4. THE CAVE
=============================================================================
    mov   w0, #0xf454
    movk  w0, #0xcf, lsl #16          0xCFF454, field_triggers_header
    bl    #0x10fc3a0
    ldr   w0, [x0]                    the guest header pointer
    cbz   w0, skip                    no field loaded -> do nothing
    bl    #0x10fc3a0
    ldrsh w20, [x0, #0x10]            camera_range.right
    ldrsh w21, [x0, #0xc]             camera_range.left
    sub   w20, w20, #0xa0             hi = right - 160
    add   w21, w21, #0xa0             lo = left  + 160
    cmp   w21, w20
    b.gt  skip                        degenerate range -> do nothing
    add   w0, w19, #4                 0xCC15F0
    bl    #0x10fc3a0
    ldrsh w8, [x0]
    neg   w8, w8                      p = -delta        (FFNx's negation)
    cmp   w8, w20
    csel  w8, w20, w8, gt             p = p > hi ? hi : p
    cmp   w8, w21
    csel  w8, w21, w8, lt             p = p < lo ? lo : p
    neg   w8, w8
    strh  w8, [x0]                    delta = -p
  skip:
    ldr   w0, [x22, #0x14]            <- displaced
    b     #0x9f8750

IDEMPOTENT BY CONSTRUCTION, WHICH IS THE POINT
----------------------------------------------
On every frame that did go through `field_clip_with_camera_range`, the value
is already inside [lo, hi] and both `csel`s take the no-op arm. So this cannot
change the normal camera, cannot change a 4:3 build (where the baked range is
the vanilla one and the bounds are the stock ones), and cannot change a field
whose script never moves the camera. The only frames it can move are the ones
that are already outside the bounds -- which is the definition of the bug.

Y, WHICH THIS MODULE NOW ALSO DOES  (was "WHY NOT Y")
-----------------------------------------------------
It used to say: FFNx's unconditional tail clamp is
`field_widescreen_width_clip_with_camera_range`, which is horizontal; the
vertical twin `field_uncropped_height_clip_with_camera_range` is gated behind
`isScriptedVerticalClipEnabled()` and its own per-field config key; vanilla
never clamped a scripted camera vertically either; so adding it would be a
behaviour change with no measured symptom behind it.

**There is a measured symptom now.** Sector 8, the camera panning down from
the LOVELESS billboards onto Aerith: a black band along the top at the
extreme of the pan. HANDOFF-93 proposed that the port was clamping to the
stock +/-112 while the uncropped view needs +/-120, and that the missing 8
range units (24 device rows at 720p) were the band. `diag_camhalf.py`
disassembled the clamp and that is not true -- see §2.1. The port is already
on 120, the normal path is flush with the art on `md8_3`, and what is left is
the path that does not clamp at all. Which is this one.

Cosmos's config asks for the vertical clip on ten fields explicitly
(`scripted_vertical_clip = true`). This module does not read that key: it
clamps every field, for the same reason the x leg does. The clamp is
idempotent, so on a field whose script stays legal it is a no-op, and the
per-field gate is already baked into the range data. Following FFNx's opt-in
here would mean shipping a config reader to reproduce a default that FFNx only
has because it cannot edit the archive.

The shape is exactly what this docstring predicted before there was any
evidence for it -- `_clamp_block` + `_clamp_tail` with RANGE_TOP/RANGE_BOTTOM
and HALF_H -- which is the one thing in HANDOFF-93 that survived contact with
the measurement.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '7th_heaven_nx')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import a64 as A                                                # noqa: E402
import ff7nx_cave                                              # noqa: E402

CAMCLAMP_ENV = 'SEVENTH_NX_CAM_CLAMP'
SDOUT_MAIN = os.path.join('atmosphere', 'contents',
                          '0100A5B00BDC6000', 'exefs', 'main')

# --------------------------------------------------------------- the site
FUNC_LO = 0x9F7DB0              # field_update_scripted_bg_movement, x86 0x643D22
FUNC_HI = 0x9F8790

HOOK_VA = 0x9F874C              # ldr w0, [x22, #0x14]   -- x86 0x644055
HOOK_ORIG = 0xB94016C0
RETURN_VA = 0x9F8750

XLAT = 0x10FC3A0                # guest address -> host pointer

HDR_PTR = 0xCFF454              # &field_triggers_header
RANGE_LEFT = 0x0C               # field_trigger_header.camera_range.left
RANGE_TOP = 0x0E
RANGE_RIGHT = 0x10
RANGE_BOTTOM = 0x12

W19_BASE = 0xCC15EC             # scripted_world_move_step_index
DELTA_X_OFF = 4                 # -> 0xCC15F0  field_curr_delta_world_pos_x
DELTA_Y_OFF = 8                 # -> 0xCC15F4

HALF_W = 0xA0                   # 160, the stock half-view; see §2
HALF_H = 0x78                   # 120, and MEASURED -- see §2.1

CTX = 22                        # x22, the guest CPU context
BASE = 19                       # w19 = 0xCC15EC

COND_GT = 12
COND_LT = 11

# +0x30 in field_trigger_header -- FFNx calls it `short field_30[4]` and
# never touches it. MEASURED zero in all 711 fields of the built archive, all
# eight bytes, which is why it can carry the per-field vertical-clip flag.
# `field_14[4]` at +0x14 was the obvious candidate and is NOT free: 86 fields
# use it.
VCLIP_FLAG_OFF = 0x30
VCLIP_FLAG_SITES = 8            # bytes at +0x30..+0x37, only the first used

N_WORDS_X = 24                  # horizontal only -- the shipped v1
N_WORDS_XY = 47                 # both axes, y gated per field  (v3)

# EVERY LENGTH THIS MODULE HAS EVER WRITTEN, newest first.
#
# `cave_length` probes these and only these, so a module carrying an OLDER
# cave can still be reverted -- which is the only way v3 can replace v2 on a
# module that is already on hardware. v3 shipped without this list and could
# not revert its own predecessor: `--revert` reported "does not end in the
# displaced word plus a return branch" and refused, correctly, because 45 was
# no longer a length it knew about. Removing a length from this list strands
# every module built with it.
#
#   24  v1  x only
#   45  v2  x and y, ungated -- the one that froze 198 cameras
#   47  v3  x and y, gated on field_trigger_header + 0x30
LEGACY_LENGTHS = (45,)
KNOWN_LENGTHS = (N_WORDS_XY,) + LEGACY_LENGTHS + (N_WORDS_X,)
N_WORDS = N_WORDS_X             # kept so old callers still read something


def n_words(vertical=True):
    return N_WORDS_XY if vertical else N_WORDS_X

ANCHORS = [
    (0x9F7DC4, 0xF00046B6, 'adrp x22, #0x12ce000  \\ the guest context,'),
    (0x9F7DC8, 0xF9415AD6, 'ldr  x22, [x22, #0x2b0] / live at the hook'),
    (0x9F7DCC, 0x5282BD93, 'mov  w19, #0x15ec     \\ 0xCC15EC, written once'),
    (0x9F7DD0, 0x72A01993, 'movk w19, #0xcc,16    / in the prologue'),
    (0x9F8750, 0xB90012C0, 'str w0, [x22, #0x10]  the word after the hook'),
    # These four are the whole safety argument for using w20/w21/x30 as
    # scratch, so they are READ OUT OF THE MODULE, not encoded from the
    # listing. The first two were hand-encoded on the first pass and both
    # were wrong -- --verify caught it against the shipped file, which is
    # the only reason this comment is not a bug report.
    (0x9F877C, 0xA9437BFD, 'ldp x29, x30, [sp, #0x30]  x30 restored AFTER'),
    (0x9F8780, 0xA9424FF4, 'ldp x20, x19, [sp, #0x20]  w20/w19 restored after'),
    (0x9F8784, 0xA94157F6, 'ldp x22, x21, [sp, #0x10]  w21/w22 restored after'),
    (0x9F8788, 0xF84407F7, 'ldr x23, [sp], #0x40       the frame is torn down'),
    (0x10FC3A0, 0x34000180, 'the guest->host address helper, first word'),
    # DELTA_Y_OFF was defined in this file from the first revision and never
    # used, so it was never checked. It is checked now, because the vertical
    # leg writes through it and an unverified constant is how HANDOFF-89's
    # third leg went into the wrong global.
    #
    # The normal path's write-back, immediately after the clip call, does both
    # axes in the same shape one after the other:
    #
    #   +0x9F8510  ldrsh w8, [x0]        the clipped x
    #   +0x9F8514  add   w0, w19, #4     &0xCC15F0
    #   +0x9F8518  neg   w20, w8
    #   +0x9F8520  bl    xlat
    #   +0x9F8524  strh  w20, [x0]       delta_x = -x
    #   +0x9F852C  sub   w0, w8, #6      the point's SECOND short
    #   +0x9F8534  ldrsh w8, [x0]        the clipped y
    #   +0x9F8538  add   w0, w19, #8     &0xCC15F4
    #
    # so +4 is x and +8 is y, and the negation the cave applies is the game's
    # own convention on both axes rather than a guess carried over from x.
    (0x9F8514, 0x11001260, 'add w0, w19, #4   &delta_x -- DELTA_X_OFF'),
    (0x9F8538, 0x11002260, 'add w0, w19, #8   &delta_y -- DELTA_Y_OFF'),
]


# -------------------------------------------------- the measured half-height
#
# §2.1 -- WHY HALF_H IS 120 AND NOT 112, AND WHY THAT IS NOT A CHOICE
#
# HANDOFF-93 predicted that the port clamps the camera vertically to the stock
# +/-112 while the uncropped view needs +/-120, and that the missing 8 units
# were the black band. `diag_camhalf.py` disassembled both clamps and that is
# not what the port does:
#
#     field_clip_with_camera_range         +0xA11530 .. +0xA11898
#       +0xA115A8  sub w8, w8, #160     right - 160     camera_range +0x10
#       +0xA11668  add w8, w8, #160     left  + 160     camera_range +0x0C
#       +0xA11720  sub w9, w8, #120     bottom - 120    camera_range +0x12
#       +0xA117E8  add w8, w8, #120     top   + 120     camera_range +0x0E
#       ... and the second copy of each at +0xA11600/+0xA116B4/
#           +0xA11778/+0xA11834
#
#     field_layer3_clip_with_camera_range  +0xA108A0
#       19 immediates of 160, 17 of 120, and again no 112.
#
# ZERO immediates of 112 in either function. The port's vertical half-view has
# always been 120 -- it matches FFNx's `background.cpp:447/449`, which are also
# a bare 120 with no `enable_uncrop` term. (HANDOFF-93 cited
# `background.cpp:133`'s `enable_uncrop ? 120 : 112`; that constant is the
# layer-3/4 tile WRAP, not the camera clamp. Right number, wrong line, and the
# wrong conclusion followed from it.)
#
# So the normal path was never broken. Checked against the built archive:
# md8_3, the LOVELESS pan, has camera_range top -200 bottom 200, and at 120 the
# camera travels y in [-80, +80], putting the visible span at exactly
# [-200, +200] -- flush with the art, no overshoot to find.
#
# That eliminates HANDOFF-93 §3 and §4 outright and leaves §5: the SCRIPTED
# path, which does not clamp at all. Which is this module, and it only ever
# did x.
CLIP_LO = 0xA11530              # field_clip_with_camera_range
CLIP_HI = 0xA11898              # its `ret`
VERTICAL_HALF_SITES = (0xA11720, 0xA11778, 0xA117E8, 0xA11834)
STOCK_HALF_H = 0x70             # 112 -- what the port does NOT use


def _find_imm(t, lo, hi, imm):
    """Every add/sub-immediate in [lo, hi) whose immediate is `imm`."""
    out = []
    for va in range(lo, hi, 4):
        w = struct.unpack_from('<I', t, va)[0]
        if (w & 0x7F000000) in (0x11000000, 0x51000000) \
                and ((w >> 10) & 0xFFF) == imm and not ((w >> 22) & 3):
            out.append(va)
    return out


def _fmt(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def w32(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _text(path):
    import nso_tool
    return nso_tool.parse_nso(str(path))['segments']['.text']['data']


# ------------------------------------------------------------------ the cave
def _clamp_block(addr, i0, skip, guest_off, lo_off, hi_off, half):
    """
    Ten words: read one short through the translator, clamp it into
    [range[lo_off] + half, range[hi_off] - half], write it back.

    Split out so the vertical variant is the SAME instructions with different
    offsets rather than a second hand-written copy that can drift.
    """
    return [
        A.ldrsh(20, 0, hi_off),                   # ldrsh w20, [x0, #hi]
        A.ldrsh(21, 0, lo_off),                   # ldrsh w21, [x0, #lo]
        A.sub_imm(20, 20, half),                  # hi = right - half
        A.add_imm(21, 21, half),                  # lo = left  + half
        A.cmp_reg(21, 20),
        A.bcond(addr(i0 + 5), skip, COND_GT),     # degenerate -> skip
        A.add_imm(0, BASE, guest_off),            # guest &delta
        A.bl(addr(i0 + 7), XLAT),
        A.ldrsh(8, 0, 0),
        A.sub_reg(8, 31, 8),                      # neg w8, w8
    ]


def _clamp_tail(addr, i0):
    """The six words that finish a clamp: two csel, negate, store."""
    return [
        A.cmp_reg(8, 20),
        A.csel(8, 20, 8, COND_GT),
        A.cmp_reg(8, 21),
        A.csel(8, 21, 8, COND_LT),
        A.sub_reg(8, 31, 8),                      # neg w8, w8
        A.strh(8, 0, 0),
    ]


def _header_ptr(addr, i0, skip=None):
    """
    The host pointer to `field_triggers_header` in x0.

    Six words with the null check, five without. The vertical leg needs a
    SECOND copy because `_clamp_block` leaves x0 pointing at the delta it just
    wrote, not at the header.

    Five words rather than stashing the pointer in a callee-saved register.
    x23 is in fact free here -- the prologue saves it, nothing between the hook
    and `ldr x23, [sp], #0x40` at +0x9F8788 reads it -- but that is one more
    unproven claim about register liveness in a cave that runs on every field
    frame, and re-deriving costs five words out of a padding hole. The idiom is
    the same one the first leg already proves.
    """
    w = [
        A.movz(0, HDR_PTR & 0xFFFF),
        A.movk_hi(0, HDR_PTR >> 16),
        A.bl(addr(i0 + 2), XLAT),
        A.ldr(0, 0, 0),                           # guest header pointer
    ]
    if skip is not None:
        w.append(A.cbz(0, addr(i0 + 4), skip))    # no field loaded -> nothing
    w.append(A.bl(addr(i0 + len(w)), XLAT))       # -> host header
    return w


def _vclip_gate(addr, i0, skip):
    """
    Two words: the PER-FIELD opt-in for the vertical clamp.

    WHY THIS EXISTS, AND WHY v2 WAS WRONG TO LEAVE IT OUT
    ----------------------------------------------------
    FFNx gates the vertical scripted clip and does not gate the horizontal
    one (`ff7/field/background.cpp:559`):

        void field_uncropped_height_clip_with_camera_range(vector2<short>* p)
        {
            if(!widescreen.isScriptedClipEnabled()) return;
            p->y += widescreen.getVerticalOffset();
            if(widescreen.isScriptedVerticalClipEnabled()) {
                if (p->y > camera_range.bottom - 120) p->y = ...bottom - 120;
                if (p->y < camera_range.top    + 120) p->y = ...top    + 120;
            }
        }

    `scripted_clip` defaults TRUE (one field in Cosmos's config turns it off),
    `scripted_vertical_clip` defaults FALSE and five fields opt in. v2 read
    that asymmetry, decided to ignore it, and wrote down two reasons:

        "The clamp is idempotent, so on a field whose script stays legal it
         is a no-op, and the per-field gate is already baked into the range
         data."

    Both are false, and each has a symptom.

      * NOT A NO-OP. Field scripts pan the camera to positions the range
        forbids -- that is what an elevator does. Vanilla allowed it. Clamped,
        the camera stops at `top + 120`, holds while the elevator keeps
        moving, and releases when the script brings it back inside. Patrick
        reported exactly that on the reactor elevator, in those words.
      * NOTHING IS BAKED. `ff7nx_ws.clamped_range()` copies `top` and
        `bottom` through UNTOUCHED -- HANDOFF-93 0.2 says so. The x leg's gate
        really is baked into the data, which is why the same sentence is true
        for x and false for y. The reasoning was carried across an axis it
        does not survive.

    Measured over the built archive, the ungated clamp:

        198 of 711 fields have a range EXACTLY 240 units tall, so
            `top + 120 == bottom - 120` and the camera is frozen at one y --
            `blinele` (the Shinra HQ elevator), `elminn_1`, `elmtow`,
            `junele2` among them
         10 more invert and are skipped by `_clamp_block`'s guard
        503 keep real travel, and stick at the last few units of it

    So the gate is not caution, it is the difference between clamping five
    fields and clamping seven hundred.

    THE FLAG
    --------
    One byte at `field_trigger_header + 0x30`, written into section 8 by
    `ff7nx_vclip` from the same `scripted_vertical_clip` key FFNx reads. Zero
    means leave the scripted camera alone, which is both FFNx's default and
    vanilla's behaviour, so an archive built without that pass gets the
    horizontal clamp and nothing else -- the safe direction.

    `w20` is the scratch because it is dead here and `_clamp_block`'s first
    instruction overwrites it. `w22` would have been the obvious pick and is
    NOT free: `_clamp_tail`'s `ldr w0, [x22, #0x14]` reads it.
    """
    return [
        A.ldrb(20, 0, VCLIP_FLAG_OFF),            # ldrb w20, [x0, #0x30]
        A.cbz(20, addr(i0 + 1), skip),            # not opted in -> skip
    ]


def cave_words(addr, return_va, vertical=True):
    """
    The whole cave, laid out at addr(i).

    `vertical=False` reproduces the shipped horizontal-only v1 byte for byte,
    which is what makes `--revert` of an older module and the A/B possible.
    """
    n = n_words(vertical)
    skip = addr(n - 2)                            # the displaced instruction
    w = _header_ptr(addr, 0, skip)                            # 6
    w += _clamp_block(addr, 6, skip, DELTA_X_OFF,
                      RANGE_LEFT, RANGE_RIGHT, HALF_W)        # 10 -> 16
    w += _clamp_tail(addr, 16)                                # 6  -> 22
    if vertical:
        w += _header_ptr(addr, 22)                            # 5  -> 27
        w += _vclip_gate(addr, 27, skip)                      # 2  -> 29
        w += _clamp_block(addr, 29, skip, DELTA_Y_OFF,
                          RANGE_TOP, RANGE_BOTTOM, HALF_H)    # 10 -> 39
        w += _clamp_tail(addr, 39)                            # 6  -> 45
    w += [HOOK_ORIG, A.b(addr(len(w) + 1), return_va)]        # 2
    assert len(w) == n, 'n_words is %d, body is %d' % (n, len(w))
    return w


_DISASM_X = [
    'mov w0, #0xf454', 'movk w0, #0xcf, lsl #16', 'bl #0x10fc3a0',
    'ldr w0, [x0]', 'cbz w0, #skip', 'bl #0x10fc3a0',
    'ldrsh w20, [x0, #0x10]', 'ldrsh w21, [x0, #0xc]',
    'sub w20, w20, #0xa0', 'add w21, w21, #0xa0',
    'cmp w21, w20', 'b.gt #skip',
    'add w0, w19, #4', 'bl #0x10fc3a0', 'ldrsh w8, [x0]', 'neg w8, w8',
    'cmp w8, w20', 'csel w8, w20, w8, gt',
    'cmp w8, w21', 'csel w8, w21, w8, lt',
    'neg w8, w8', 'strh w8, [x0]',
]

_DISASM_Y = [
    'mov w0, #0xf454', 'movk w0, #0xcf, lsl #16', 'bl #0x10fc3a0',
    'ldr w0, [x0]', 'bl #0x10fc3a0',
    'ldrb w20, [x0, #0x30]', 'cbz w20, #skip',
    'ldrsh w20, [x0, #0x12]', 'ldrsh w21, [x0, #0xe]',
    'sub w20, w20, #0x78', 'add w21, w21, #0x78',
    'cmp w21, w20', 'b.gt #skip',
    'add w0, w19, #8', 'bl #0x10fc3a0', 'ldrsh w8, [x0]', 'neg w8, w8',
    'cmp w8, w20', 'csel w8, w20, w8, gt',
    'cmp w8, w21', 'csel w8, w21, w8, lt',
    'neg w8, w8', 'strh w8, [x0]',
]

_DISASM_TAIL = ['ldr w0, [x22, #0x14]', 'b #0x9f8750']


def disasm(vertical=True):
    return _DISASM_X + (_DISASM_Y if vertical else []) + _DISASM_TAIL


DISASM = disasm(False)          # the v1 listing, for anything that imported it


# --------------------------------------------------------------- the model
def clamp(delta_x, left, right, half=HALF_W):
    """
    What the cave computes, in Python, for a camera range and a delta.

    Written from the x86 of `field_clip_with_camera_range` (0x6438F6), not
    from the cave -- so `--verify` compares two independent statements of the
    rule instead of the cave against itself.
    """
    lo, hi = left + half, right - half
    if lo > hi:
        return delta_x
    p = -delta_x
    if p > hi:
        p = hi
    if p < lo:
        p = lo
    return -p


def travel(left, right, half=HALF_W):
    """The camera's reachable x span for a range, as a sanity number."""
    return max(0, (right - half) - (left + half))


# ------------------------------------------------------------------- state
def state(t):
    got = w32(t, HOOK_VA)
    if got == HOOK_ORIG:
        return 'stock'
    if (got & 0xFC000000) == 0x14000000:
        return 'patched'
    return 'unknown'


def check_anchors(t, log=lambda *_: None):
    bad = []
    for va, want, what in ANCHORS:
        got = w32(t, va)
        if got != want:
            bad.append('+%#09x is %08X, expected %08X -- %s'
                       % (va, got, want, what))
    if state(t) == 'unknown':
        bad.append('+%#09x is %08X -- neither the stock `ldr w0, [x22, #0x14]` '
                   'nor a branch; refusing' % (HOOK_VA, w32(t, HOOK_VA)))
    # w19 must be written exactly once in the whole function, or `add w0, w19,
    # #4` is not field_curr_delta_world_pos_x at the hook.
    writes = _w19_writes(t)
    if writes != [0x9F7DCC, 0x9F7DD0]:
        bad.append('w19 is written at %s, not only in the prologue -- the '
                   'base register may not still be 0xCC15EC at the hook'
                   % ', '.join('+%#x' % v for v in writes))
    for b in bad:
        log('  ! ' + b)
    return bad


def _w19_writes(t):
    """Every instruction in the function whose destination register is 19."""
    out = []
    for va in range(FUNC_LO, FUNC_HI, 4):
        w = w32(t, va)
        if (w & 0x1F) != 19:
            continue
        top = w & 0x7F800000
        if top in (0x52800000, 0x72800000, 0x52A00000, 0x72A00000):   # movz/movk
            out.append(va)
        elif top in (0x11000000, 0x51000000, 0x91000000, 0xD1000000):  # add/sub imm
            out.append(va)
        elif (w & 0xBFC00000) == 0xB9400000:                           # ldr imm
            out.append(va)
        elif (w & 0x7FE00000) in (0x0B000000, 0x4B000000, 0x2A000000):  # add/sub/orr reg
            out.append(va)
    return out


# ------------------------------------------------------------------ walking
def _b_target(word, va):
    if (word & 0xFC000000) != 0x14000000:
        return None
    imm = word & 0x03FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return va + imm * 4


def walk(t, hook=HOOK_VA, n=None):
    """The cave's logical words: chain links followed, not recorded."""
    n = N_WORDS if n is None else n
    tgt = _b_target(w32(t, hook), hook)
    if tgt is None:
        return None
    va, out, seen = tgt, [], set()
    while len(out) < n:
        if va in seen:
            break
        seen.add(va)
        x = w32(t, va)
        b = _b_target(x, va)
        if b is not None and b != RETURN_VA:
            va = b
            continue
        out.append((va, x))
        va += 4
    return out


def walk_physical(t, hook=HOOK_VA, n=None):
    """Every address the cave occupies, chain links included."""
    n = N_WORDS if n is None else n
    tgt = _b_target(w32(t, hook), hook)
    if tgt is None:
        return []
    va, out, logical = tgt, [], 0
    while logical < n and va not in out:
        x = w32(t, va)
        out.append(va)
        b = _b_target(x, va)
        if b is not None and b != RETURN_VA:
            va = b
            continue
        logical += 1
        va += 4
    return out


def cave_length(t):
    """
    The installed cave's word count, or None.

    EVERY length this module has ever written is probed -- see
    `KNOWN_LENGTHS`. `--revert` has to remove whichever is actually there, and
    a wrong count would leave live words behind or zero words that are not
    ours. The end marker -- the displaced instruction followed by a branch to
    RETURN_VA -- is what distinguishes them, so this is a measurement and not
    a preference.
    """
    for n in KNOWN_LENGTHS:
        got = walk(t, n=n)
        if got and len(got) == n \
                and _b_target(got[-1][1], got[-1][0]) == RETURN_VA \
                and got[-2][1] == HOOK_ORIG:
            return n
    return None


def installed_vertical(t):
    """
    True/False for which variant is in the module, None if none is.

    A LEGACY vertical cave (45 words, ungated) counts as vertical, so
    `--apply` sees "x and y is installed, x and y is wanted" and would call it
    a no-op. `installed_current` is what distinguishes "has a y leg" from "has
    THIS y leg", and `apply` uses it so a v2 module is upgraded rather than
    left alone.
    """
    n = cave_length(t)
    if n is None:
        return None
    return n != N_WORDS_X


def installed_current(t):
    """True only for the cave THIS revision writes."""
    return cave_length(t) == N_WORDS_XY


# ------------------------------------------------------------------ patches
def build_patches(img, starts, log=print, vertical=True):
    def build(_entry, addr):
        return cave_words(addr, RETURN_VA, vertical)

    entry, out = ff7nx_cave.emit_laid_out(
        ff7nx_cave.HolePool(bytearray(img), starts=starts), build, span=0x80000)
    out[HOOK_VA] = A.b(HOOK_VA, entry)
    log('  scripted camera clamp cave: %d words in padding, entry +%#x  (%s)'
        % (n_words(vertical), entry,
           'x and y' if vertical else 'x only'))
    if vertical:
        log('    x -> [left + %d, right - %d]   the bounds '
            'field_clip_with_camera_range uses' % (HALF_W, HALF_W))
        log('    y -> [top  + %d, bottom - %d]  MEASURED in that function at '
            '+0xA11720/+0xA117E8' % (HALF_H, HALF_H))
    log('  (the 60 FPS cave region is not touched)')
    return out


def revert_patches(t, log=print):
    n = cave_length(t)
    if n is None:
        log('  ! the cave at +%#x does not end in the displaced word plus a '
            'return branch; refusing to guess what to restore' % HOOK_VA)
        return None
    out = {HOOK_VA: HOOK_ORIG}
    for va in walk_physical(t, n=n):
        if va != HOOK_VA:
            out[va] = 0
    log('  scripted camera clamp removed (%d word(s) of padding returned)'
        % (len(out) - 1))
    return out


# ------------------------------------------------------------------ emulation
def _emu():
    import ws_emu
    import arm64emu
    return ws_emu.Cpu, arm64emu


def emulate(delta_x, left, right, hdr=0x2200000,
            delta_y=0, top=0, bottom=0, no_field=False, vertical=True,
            vclip=1):
    """
    Execute the cave's real words against a fake guest memory.

    The translator at 0x10FC3A0 is stubbed with identity -- the cave's
    correctness does not depend on what the mapping IS, only on the fact that
    it calls it before every guest access, which the disassembly check covers.
    """
    Cpu, arm64emu = _emu()
    mem = arm64emu.Mem()
    base = 0x3000000
    n = n_words(vertical)
    mem.setu(HDR_PTR, 0 if no_field else hdr, 4)
    mem.setu(hdr + RANGE_LEFT, left & 0xFFFF, 2)
    mem.setu(hdr + RANGE_RIGHT, right & 0xFFFF, 2)
    mem.setu(hdr + RANGE_TOP, top & 0xFFFF, 2)
    mem.setu(hdr + RANGE_BOTTOM, bottom & 0xFFFF, 2)
    # The per-field opt-in `ff7nx_vclip` writes into section 8. `vclip=1` is
    # the default HERE and only here, so the existing y cases keep testing the
    # clamp itself; the archive's default is 0 and `vclip=0` below is what
    # tests that.
    mem.setu(hdr + VCLIP_FLAG_OFF, vclip & 0xFF, 1)
    mem.setu(W19_BASE + DELTA_X_OFF, delta_x & 0xFFFF, 2)
    mem.setu(W19_BASE + DELTA_Y_OFF, delta_y & 0xFFFF, 2)

    words = cave_words(lambda i: base + 4 * i, base + 4 * n, vertical)

    class Stub(Cpu):
        def step(self, w, pc):
            if (w & 0xFC000000) == 0x94000000:        # bl -> identity xlat
                return None                            # x0 already holds it
            return Cpu.step(self, w, pc)

    cpu = Stub(mem)
    cpu.set(BASE, W19_BASE, True)
    cpu.set(CTX, 0x2400000)
    mem.setu(0x2400000 + 0x14, 0xC0FFEE, 4)
    cpu.run(base, words, stop_at=base + 4 * n)

    def s16(v):
        v &= 0xFFFF
        return v - 0x10000 if v & 0x8000 else v

    return {'x': s16(mem.u(W19_BASE + DELTA_X_OFF, 2)),
            'y': s16(mem.u(W19_BASE + DELTA_Y_OFF, 2)),
            'w0': cpu.get(0, True) & 0xFFFFFFFF}


# ------------------------------------------------------------------ verify
def check_encoding(log=print, vertical=True):
    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    except ImportError:
        log('  (capstone not installed -- encodings NOT checked)')
        return True
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    base = 0x1000
    n = n_words(vertical)
    words = cave_words(lambda i: base + 4 * i, base + 4 * n, vertical)
    blob = b''.join(struct.pack('<I', x) for x in words)
    got = [(i.mnemonic + ' ' + i.op_str).strip() for i in md.disasm(blob, base)]
    ok = len(got) == len(words)
    if not ok:
        log('  ! capstone decoded %d of %d words' % (len(got), len(words)))
        return False
    for k, (g, want) in enumerate(zip(got, disasm(vertical))):
        loose = '#skip' in want or want.startswith('b #')
        if (g.split()[0] if loose else g) != (want.split()[0] if loose
                                              else want):
            log('  ! word %2d encodes `%s`, meant `%s`' % (k, g, want))
            ok = False
    return ok


def verify(main=None, log=print):
    fails = []

    def ck(cond, what):
        log('    %s  %s' % ('ok  ' if cond else 'FAIL', what))
        if not cond:
            fails.append(what)

    log('  encodings, cross-checked against capstone rather than the encoder:')
    ck(check_encoding(log), 'every word disassembles to what it is '
                                      'named in DISASM')

    log('')
    log('  the cave, executed, against clamp() derived from the x86:')
    log('')
    log('    range            delta_x    cave      model    travel')
    cases = [
        (-258, 258, 152, 'nmkin_1 as SHIPPED, script at the 4:3 bound'),
        (-258, 258, 98, 'nmkin_1, already legal -- must not move'),
        (-258, 258, -152, 'nmkin_1, the other end'),
        (-258, 258, 0, 'centred -- must not move'),
        (-312, 312, 152, 'nmkin_1 VANILLA range (no bake): 152 is legal'),
        (-160, 160, 0, 'a fixed-camera field: lo == hi, pinned'),
        (-160, 160, 40, 'a fixed-camera field, script off-centre'),
        (-120, 120, 30, 'range narrower than the view: refused, left alone'),
        (-1000, 1000, 900, 'a big field, far right'),
    ]
    for left, right, dx, what in cases:
        got = emulate(dx, left, right)
        want = clamp(dx, left, right)
        good = got['x'] == want
        ck(good, '%5d..%-5d  %6d -> %6d  (model %6d, travel %4d)  %s'
           % (left, right, dx, got['x'], want, travel(left, right), what))

    log('')
    log('  the VERTICAL leg, executed, against the same model at half = %d:'
        % HALF_H)
    log('    the constant is not FFNx\'s and not assumed -- it is READ OUT OF')
    log('    the port at +0xA11720 / +0xA11778 / +0xA117E8 / +0xA11834, the')
    log('    four vertical immediates in field_clip_with_camera_range. There')
    log('    is no 112 anywhere in that function or in the layer-3 one.')
    log('')
    log('    (all rows below have the per-field opt-in flag SET; the rows')
    log('     after them are the same fields with it clear, which is the')
    log('     archive default and vanilla behaviour)')
    log('')
    log('    range            delta_y    cave      model    travel')
    ycases = [
        (-200, 200, 100, 'md8_3 (Sector 8, the LOVELESS pan) -- script high'),
        (-200, 200, -100, 'md8_3, the other end'),
        (-200, 200, 80, 'md8_3 at the bound exactly -- must not move'),
        (-200, 200, 0, 'md8_3 centred -- must not move'),
        (-120, 120, 40, 'a 240-unit range: pinned to 0, no vertical travel'),
        (-256, 256, 300, 'a tall field, script far below'),
        (-120, 112, 40, 'md8_5: 232 units, lo > hi -- refused, left alone'),
    ]
    for top, bottom, dy, what in ycases:
        got = emulate(0, -2000, 2000, delta_y=dy, top=top, bottom=bottom)
        want = clamp(dy, top, bottom, HALF_H)
        ck(got['y'] == want,
           '%5d..%-5d  %6d -> %6d  (model %6d, travel %4d)  %s'
           % (top, bottom, dy, got['y'], want,
              travel(top, bottom, HALF_H), what))

    log('')
    log('  THE PER-FIELD GATE -- flag clear means the scripted camera is left')
    log('  exactly as the script wrote it, which is what an elevator needs:')
    for top, bottom, dy, what in ycases:
        got = emulate(0, -2000, 2000, delta_y=dy, top=top, bottom=bottom,
                      vclip=0)
        ck(got['y'] == dy,
           '%5d..%-5d  %6d -> %6d  UNTOUCHED   %s'
           % (top, bottom, dy, got['y'], what))
    got = emulate(0, -2000, 2000, delta_y=300, top=-120, bottom=120, vclip=0)
    ck(got['y'] == 300,
       'blinele-shaped (240 units, would be PINNED): the script gets its '
       'camera back')
    got = emulate(152, -258, 258, delta_y=300, top=-256, bottom=256, vclip=0)
    ck(got['x'] == clamp(152, -258, 258) and got['y'] == 300,
       'and the flag gates ONLY y -- x still clamps (x %d, y %d), which is '
       'FFNx: scripted_clip defaults true, scripted_vertical_clip false'
       % (got['x'], got['y']))

    log('')
    log('  the two legs do not interfere:')
    got = emulate(152, -258, 258, delta_y=100, top=-200, bottom=200)
    ck(got['x'] == clamp(152, -258, 258)
       and got['y'] == clamp(100, -200, 200, HALF_H),
       'x and y clamp to their own axes in one pass (x %d, y %d)'
       % (got['x'], got['y']))
    got = emulate(0, -2000, 2000, delta_y=100, top=-200, bottom=200)
    ck(got['x'] == 0, 'a legal x is untouched while y is clamped')
    got = emulate(152, -258, 258, delta_y=0, top=-2000, bottom=2000)
    ck(got['y'] == 0, 'a legal y is untouched while x is clamped')

    log('')
    log('  BACKWARD COMPATIBILITY -- the check that v3 shipped without:')
    ck(24 in KNOWN_LENGTHS and 45 in KNOWN_LENGTHS
       and N_WORDS_XY in KNOWN_LENGTHS,
       'KNOWN_LENGTHS carries every cave this module has written '
       '(v1 24, v2 45, v3 %d) -- a module built with any of them can still '
       'be reverted' % N_WORDS_XY)
    ck(KNOWN_LENGTHS[0] == N_WORDS_XY,
       'the current length is probed first, so a fresh cave is never '
       'mistaken for a legacy one')
    ck(len(set(KNOWN_LENGTHS)) == len(KNOWN_LENGTHS),
       'no length is listed twice')
    ck(N_WORDS_XY not in LEGACY_LENGTHS,
       'the current length is not also marked legacy -- if it were, --apply '
       'would try to upgrade a module that is already correct, forever')

    log('')
    log('  the guards:')
    got = emulate(9999, -258, 258, no_field=True)
    ck(got['x'] == 9999, 'no field loaded (header pointer 0) -> nothing '
                         'is written')
    got = emulate(9999, -258, 258, delta_y=9999, top=0, bottom=0,
                  no_field=True)
    ck(got['y'] == 9999, 'no field loaded -> the VERTICAL leg is skipped too '
                         '(one cbz covers both)')
    got = emulate(9999, 100, 120)
    ck(got['x'] == 9999, 'a degenerate range (lo > hi) -> nothing is written')
    got = emulate(0, -2000, 2000, delta_y=9999, top=-120, bottom=112)
    ck(got['y'] == 9999, 'md8_5\'s 232-unit range (lo > hi at 120) -> the y '
                         'leg is skipped, x still runs')
    # The displaced word is `ldr w0, [x22, #0x14]` and the instruction the
    # cave returns to is `str w0, [x22, #0x10]` -- the guest's `mov esp, ebp`.
    # If the cave ever returned without running the displaced word, or left
    # its own pointer in w0, the guest stack pointer would be set to a host
    # address. So the invariant is the OPPOSITE of "x0 survives": w0 must
    # come back holding [x22+0x14] on EVERY path, including the guarded ones.
    #
    # This check was first written the other way round, asserting the cave's
    # pointer survived, and it failed -- correctly. HANDOFF-90 §2.5's rule
    # again: a test that agrees with a wrong mental model is not a test.
    SENTINEL = 0xC0FFEE
    for args, what in (((0, -258, 258), 'the clamping path'),
                       ((9999, -258, 258), 'the clamping path, out of range'),
                       ((9999, 100, 120), 'the degenerate-range skip'),
                       (dict(delta_x=9999, left=-258, right=258,
                             no_field=True), 'the no-field skip')):
        got = emulate(**args) if isinstance(args, dict) else emulate(*args)
        ck(got['w0'] == SENTINEL,
           'w0 comes back holding [x22+0x14] on %s -- the displaced '
           '`ldr w0, [x22, #0x14]` ran' % what)

    log('')
    log('  the invariant that makes this safe to ship:')
    import random
    rnd = random.Random(20260807)
    idem = True
    for _ in range(400):
        left = rnd.randint(-2000, 0)
        right = rnd.randint(0, 2000)
        dx = rnd.randint(-3000, 3000)
        once = clamp(dx, left, right)
        if clamp(once, left, right) != once:
            idem = False
    ck(idem, 'clamp() is idempotent over 400 random ranges -- a value the '
             'normal path already clamped cannot be moved')
    inside = all(clamp(d, -258, 258) == d
                 for d in range(-98, 99))
    ck(inside, 'every legal camera position for the shipped nmkin_1 range '
               'is a fixed point')

    if main is None:
        log('')
        log('  %d failure(s)' % len(fails) if fails else '  all checks pass')
        return 1 if fails else 0

    t = _text(resolve_main(main))
    log('')
    log('  against %s:' % main)
    for b in check_anchors(t):
        ck(False, b)
    if not fails:
        ck(True, 'all %d anchors match, and w19 is written only in the '
                 'prologue' % len(ANCHORS))
    log('')
    log('  the vertical half-view, read out of THIS module rather than '
        'assumed:')
    for va in VERTICAL_HALF_SITES:
        got = w32(t, va)
        imm = (got >> 10) & 0xFFF
        shape = got & 0x7F000000
        ck(shape in (0x11000000, 0x51000000) and imm == HALF_H,
           '+%#09x is %s -> %s #%d  (want add/sub #%d)'
           % (va, _fmt(got),
              'sub' if shape == 0x51000000 else
              ('add' if shape == 0x11000000 else '??'), imm, HALF_H))
    ck(not _find_imm(t, CLIP_LO, CLIP_HI, STOCK_HALF_H),
       'no 112 immediate anywhere in field_clip_with_camera_range -- the '
       'port was never on the stock half-height, so there is nothing to '
       'compensate and HANDOFF-93 §4 is eliminated')

    st = state(t)
    log('    +%#09X  %s   %s' % (HOOK_VA, _fmt(w32(t, HOOK_VA)), st))
    if st == 'patched':
        n = cave_length(t)
        ck(n is not None, 'the installed cave ends in the displaced word '
                          'plus a return to +%#x' % RETURN_VA)
        if n:
            got = walk(t, n=n)
            want = cave_words(lambda i: got[i][0], RETURN_VA,
                              n == N_WORDS_XY)
            ck([x for _, x in got] == want,
               'every word in the WRITTEN module matches what cave_words() '
               'lays out at those exact addresses')

    # Does the vertical evidence actually bite? Rewrite the module's own
    # `sub w9, w8, #120` as `#112` -- i.e. manufacture the world HANDOFF-93
    # assumed -- and the site check must notice. A check that passes on both
    # the real module and the counterfactual is not a check.
    log('')
    log('  mutation -- the vertical evidence must be falsifiable:')
    mut = bytearray(t)
    got = struct.unpack_from('<I', mut, VERTICAL_HALF_SITES[0])[0]
    struct.pack_into('<I', mut, VERTICAL_HALF_SITES[0],
                     (got & ~(0xFFF << 10)) | (STOCK_HALF_H << 10))
    imm = (struct.unpack_from('<I', bytes(mut),
                              VERTICAL_HALF_SITES[0])[0] >> 10) & 0xFFF
    ck(imm == STOCK_HALF_H and _find_imm(bytes(mut), CLIP_LO, CLIP_HI,
                                         STOCK_HALF_H)
       == [VERTICAL_HALF_SITES[0]],
       'a module whose +%#x carried #112 IS detected -- so "no 112 anywhere" '
       'is a measurement, not a tautology' % VERTICAL_HALF_SITES[0])

    for name, va, word in (
            ('a moved w19 prologue', 0x9F7DCC, 0xD503201F),
            ('a moved guest context', 0x9F7DC4, 0xD503201F),
            ('an extra write to w19 inside the function', FUNC_LO + 0x100,
             0x52800013),
            ('a hook that is neither the stock word nor a branch',
             HOOK_VA, 0xD503201F)):
        mut = bytearray(t)
        struct.pack_into('<I', mut, va, word)
        ck(bool(check_anchors(bytes(mut))), '%s is refused' % name)

    log('')
    log('  %d failure(s)' % len(fails) if fails else '  all checks pass')
    return 1 if fails else 0


# ------------------------------------------------------------------ plumbing
def enabled(env=None):
    """
    ON with widescreen. The clamp only matters because the view is wider than
    the one the scripts were authored for; at 4:3 the baked range is the
    vanilla range and this is a no-op, but there is no reason to write it.
    """
    raw = env if env is not None else os.environ.get(CAMCLAMP_ENV)
    if raw is not None:
        return str(raw).strip().lower() not in ('', '0', 'off', 'no', 'false')
    try:
        import ff7nx_ws
        return ff7nx_ws.enabled()
    except Exception:
        return False


def resolve_main(path):
    if os.path.isdir(path):
        cand = os.path.join(path, SDOUT_MAIN)
        if os.path.exists(cand):
            return cand
        raise SystemExit('no %s under %s' % (SDOUT_MAIN, path))
    return path


def show(main, log=print):
    main = resolve_main(main)
    t = _text(main)
    log('  %s' % main)
    log('    +%#09X  %s  %s' % (HOOK_VA, _fmt(w32(t, HOOK_VA)), state(t)))
    if state(t) == 'patched':
        n = cave_length(t)
        vert = installed_vertical(t)
        log('    cave length %s word(s)  (%s)'
            % (n, 'unrecognised -- neither %d nor %d' % (N_WORDS_X, N_WORDS_XY)
               if vert is None else ('x and y' if vert else 'x only')))
    bad = check_anchors(t, log)
    log('    anchors: %s' % ('OK' if not bad else '%d FAILED' % len(bad)))
    return 1 if bad else 0


def apply(main, revert=False, log=print, vertical=True):
    import nso_patcher
    import nxmap
    main = Path(resolve_main(main))
    m = nxmap.Main(str(main))
    t = m.text

    if check_anchors(t, log):
        log('  refusing to write.')
        return 1

    if revert:
        if state(t) == 'stock':
            log('  scripted camera clamp: not installed')
            return 0
        words = revert_patches(t, log)
        if words is None:
            return 1
    else:
        if state(t) == 'patched':
            got = installed_vertical(t)
            n_now = cave_length(t)
            stale = (vertical and n_now in LEGACY_LENGTHS)
            if stale:
                log('  scripted camera clamp: a LEGACY %d-word cave is '
                    'installed (ungated vertical leg) -- removing it first'
                    % n_now)
                if revert_patches(t, log) is None:
                    return 1
                log('  ! run --revert, then --apply. Refusing to write two '
                    'caves in one pass.')
                return 1
            if got == vertical:
                log('  scripted camera clamp: already installed (%s)'
                    % ('x and y' if vertical else 'x only'))
                return 0
            # An x-only cave is in the module and the y leg is wanted, or the
            # reverse. Upgrading in place would leave the old words live, so
            # take the old one out first rather than layering. This is the
            # "two owners" failure from FINDINGS-91 §2.3 in cave form.
            log('  scripted camera clamp: a %s cave is installed and %s is '
                'wanted -- removing the old one first'
                % ('x and y' if got else 'x only',
                   'x and y' if vertical else 'x only'))
            old = revert_patches(t, log)
            if old is None:
                return 1
            log('  ! run --revert, rebuild, then apply. Refusing to write two '
                'caves in one pass.')
            return 1
        if not check_encoding(log, vertical):
            log('! scripted camera clamp: an encoder disagrees with capstone; '
                'refusing')
            return 1
        words = build_patches(m.img, set(m.arm_starts), log, vertical)

    patches = [{'name': ('hook -> cave' if va == HOOK_VA else 'cave word'),
                'va': '0x%X' % va,
                'expect': _fmt(w32(t, va)),
                'set': _fmt(word)}
               for va, word in sorted(words.items())]
    nso = nso_patcher.read_nso(main)
    applied = nso_patcher.apply_spec(nso, {'name': 'ff7nx_camclamp',
                                           'patches': patches})
    main.write_bytes(nso_patcher.rebuild(nso))
    log('  %d word(s) verified and applied' % len(applied))

    t2 = _text(main)
    log('  read back: +%#09X is %s' % (HOOK_VA, state(t2)))
    if not revert:
        n = cave_length(t2)
        if n is None:
            log('  ! the written cave does not walk back to the displaced '
                'word. DO NOT BOOT THIS.')
            return 1
        got = walk(t2, n=n)
        want = cave_words(lambda i: got[i][0], RETURN_VA, vertical)
        if [x for _, x in got] != want:
            log('  ! the written cave differs from cave_words(). '
                'DO NOT BOOT THIS.')
            return 1
        log('  the scripted camera is now clamped to '
            '[range.left + %d, range.right - %d]%s, the same bounds '
            'field_clip_with_camera_range uses on the normal path'
            % (HALF_W, HALF_W,
               ' and [range.top + %d, range.bottom - %d]' % (HALF_H, HALF_H)
               if vertical else ''))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split('\n')[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('main', nargs='?')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--horizontal-only', action='store_true',
                    help='ship the v1 cave (x only). For an A/B against the '
                         'build that is already on hardware -- the vertical '
                         'leg is what fixes the band at the top of a pan.')
    a = ap.parse_args(argv)
    vertical = not a.horizontal_only

    if a.show:
        if not a.main:
            ap.error('need a path to exefs/main')
        return show(a.main)
    if a.apply or a.revert:
        if not a.main:
            ap.error('need a path to exefs/main')
        return apply(a.main, revert=a.revert, vertical=vertical)
    print('ff7nx_camclamp -- vanilla never clamps a scripted camera')
    print('')
    return verify(a.main, log=print)


if __name__ == '__main__':
    raise SystemExit(main())
