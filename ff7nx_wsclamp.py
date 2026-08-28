#!/usr/bin/env python3
"""
ff7nx_wsclamp.py -- the field background tile window, all four sides.

WHAT THIS IS
============
`field_layer1_pick_tiles` and `field_layer2_pick_tiles` each cull the field
background's tile list against a window around the camera. Stock, that
window is the 4:3 crop. Widening it is what makes Cosmos's repainted
16:9 art actually reach the edges of a 16:9 frame.

HANDOFF-48 §4 left this half-done: the LEFT side was shipped (it is a plain
immediate) and the RIGHT and BOTTOM were not, because they are bare register
compares with no immediate to edit. This module does all four.

THE LOOP, FROM THE x86
======================
Decompiled from `ff7_en_switch` at 0x640C49 (layer 1) and 0x64085C
(layer 2) -- the x86 original, not the recompiled ARM64, per HANDOFF-48 §10.1.
Both layers are the same shape:

    dst_x_base = (320 - cam_x) * mult          # mult = [0xCFF1F0], 2 in mode 2
    dst_y_base = (224 - cam_y) * mult
    for each tile:
        if (tile.y <= cam_y - 256) continue;    # TOP    -- immediate 0x100
        if (tile.y >= cam_y)        continue;   # BOTTOM -- bare register
        if (tile.x <= cam_x - 336)  continue;   # LEFT   -- immediate 0x150
        if (tile.x >= cam_x)        continue;   # RIGHT  -- bare register
        dst_x = tile.x * mult + dst_x_base;     # = (tile.x + 320 - cam_x) * 2
        dst_y = tile.y * mult + dst_y_base;

so with origin `O` and low-side extent `L`, the drawn span is

    [ (O - L) * 2 , O * 2 ]

Raising `O` TRANSLATES every tile and admits none -- which desynchronises
the background from the models that project through the mode-2 half-width,
and is what HANDOFF-48 §9 error 2 was. Only `L` (low side) and a new high-
side bias `R` admit tiles. This module never touches `O`.

THE ARITHMETIC THIS HAS TO SATISFY
==================================
With `WS_SCALE = 0.75` in the two vertex shaders and gfx_drv_init's four
words making the target 16:9, the visible game-x range is

    320 +/- 320/0.75  =  -106.67 .. 746.67

Vertically nothing is scaled, so the frame is a plain 0 .. 480.

    LEFT    (320 - L) * 2 <= -106.67   ->  L >= 374
    RIGHT   (320 + R) * 2 >=  746.67   ->  R >= 54
    TOP     (224 - L) * 2 <=    0      ->  L >= 224     (stock 256, already fine)
    BOTTOM  (224 + R) * 2 >=  480      ->  R >= 16

`required()` recomputes all four from WS_SCALE so the numbers cannot drift
away from the shader they are defined against.

...AND THE ONE THE FRAME ARITHMETIC DOES NOT CAPTURE
====================================================
Those four cover the FRAME. They do not cover the TILE, and the low sides
need one more tile than they say, because every cull tests the tile's
ORIGIN while a tile is 16 units WIDE:

    LEFT  (low)   tile.x <= cam_x - L   -- a tile whose origin is outside
                                           still reaches 16 units back in
    RIGHT (high)  tile.x >= cam_x + R   -- a tile whose origin is outside
                                           lies entirely outside

So the error is ONE-SIDED. The stock module says so itself:

    STOCK_LEFT  = 336 = required(320) + 16   <- one tile
    stock right =   0 = required(0)   +  0   <- none

`defaults()` applies that term. Shipping 376 did not -- it is 374 + 2 -- and
the missing 14 units are a black band up to one tile wide down the LEFT of
the screen, flickering as the camera crosses the tile grid, with no
counterpart on the right. See defaults() for the full derivation.

Defaults round up to a whole tile (16 field units): left=400, right=64.
Over-admitting costs a few off-screen tiles per frame and buys immunity to
the tile grid not being aligned to the camera.

HOW THE HIGH SIDE IS DONE
=========================
The high-side compares are, in both layers and on both axes:

    add   w0, w8, #8         <- x86 [ebp+8]  = cam_x   (#0xc = cam_y)
    bl    <x86 addr xlat>
    ldrsh w8, [x0]           <- w8 = the CAMERA
    ldr   w9, [x22, #N]      <- w9 = the TILE coordinate
    sub   w10, w9, w8        <- HOOK HERE
    str   w8, [x22, #M]
    eor   w8, w9, w8         \
    lsr   w11, w10, #0x1f     |  x86 CMP/JGE flag emulation
    eor   w10, w10, w9        |
    and   w8, w10, w8         |
    lsr   w8, w8, #0x1f       |
    cmp   w8, w11            /

HANDOFF-48 §4.1 marked "which of w8/w9 is the tile and which is the camera"
as **inferred and unverified**, and warned that the sign of the bias depends
on it. It is now settled, three ways:

  1. `w8` is the destination of an `ldrsh` through the x86 address translator
     from `ebp+8` / `ebp+0xc` -- the two stack arguments, i.e. cam_x / cam_y.
     The tile coordinate comes out of the emulated x86 register file at x22.
  2. The recompiler's register file is eax=+0, ecx=+4, edx=+8, ebp=+0x14.
     Every `str` in these blocks writes the camera to the register the x86
     `movsx` wrote it to, and every `ldr` reads the tile from the register
     the preceding `movsx` put it in. All four match.
  3. The flag emulation computes V for `cmp w9, w8`, which is the x86's
     `cmp tile, camera`. If the operands were the other way round the V
     term would not be `(w9^w8) & (w10^w9)`.

So the bias is `w8 += R` -- one instruction, before the displaced `sub`.
Putting it before the `sub` rather than after also keeps the flag emulation
self-consistent: N and V are then both computed against the biased value, so
the emulated flags are exactly right rather than merely right in practice.

`str w8, [x22, #M]` afterwards writes the BIASED camera into the emulated
eax/ecx. That register is dead on both the taken and not-taken paths -- the
x86 overwrites it in full at 0x640DB5/0x640D67 (fall-through) and at
0x640CE0 (loop head) before any read. Checked, both layers, both axes.

SAFETY
======
`sub w10, w9, w8` is a common word, so a bare word compare would not prove
the hook landed on the right instruction. Each site is verified against a
signature of the whole block, including the `add w0, w8, #8` vs `#0xc` that
distinguishes the horizontal cull from the vertical one. A module that does
not match is refused, not patched.
"""
import os
import struct
import sys

# ONLY this directory -- the parent shadows the real modules with any stale
# loose copies beside the project folder, and the shadowing cascades. See the
# longer note in ff7nx_fieldbuf.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                  # noqa: E402

# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------
WS_SCALE = 0.75          # must equal the #define in the two vertex shaders
GAME_W = 640.0
GAME_H = 480.0
ORIGIN_X = 320           # the `mov w9, #0x140` both layers build dst_x_base from
ORIGIN_Y = 224           # the `orr w9, wzr, #0xe0` for dst_y_base
MULT = 2                 # [0xCFF1F0] in field mode 2
TILE = 16                # field units per background tile

STOCK_LEFT = 336         # sub w9, w8, #0x150
STOCK_TOP = 256          # sub w9, w8, #0x100


def visible_x(scale=WS_SCALE):
    """(min, max) game-x actually on screen, given the shader scale."""
    half = (GAME_W / 2.0) / scale
    return GAME_W / 2.0 - half, GAME_W / 2.0 + half


def required(scale=WS_SCALE):
    """
    Minimum {left, right, top, bottom} extents that cover the frame.

    Derived, not measured -- and labelled as such, per HANDOFF-48 §10.5.
    The LEFT figure is the one hardware has confirmed: 376 reaches game-x
    -112 and the left bar closed.
    """
    lo, hi = visible_x(scale)
    import math
    return {
        'left':   int(math.ceil(ORIGIN_X - lo / MULT)),
        'right':  int(math.ceil(hi / MULT - ORIGIN_X)),
        'top':    int(math.ceil(ORIGIN_Y - 0.0 / MULT)),
        'bottom': int(math.ceil(GAME_H / MULT - ORIGIN_Y)),
    }


def _up_to_tile(n):
    return (n + TILE - 1) // TILE * TILE


def defaults(scale=WS_SCALE):
    """
    Shipping values: the minima, PLUS a tile on the low sides, rounded up.

    THE LOW SIDES NEED ONE MORE TILE THAN `required()` SAYS, and leaving it
    out is what put a black band down the left of the screen.

    Both culls test the tile's ORIGIN, and a tile is `TILE` units wide:

        LEFT   (low)   if (tile.x <= cam_x - L) continue;
        RIGHT  (high)  if (tile.x >= cam_x + R) continue;

    On the HIGH side that is conservative -- a tile whose origin is past the
    boundary lies entirely past it, so `required()` is exactly right and no
    margin is needed. On the LOW side it is WRONG: a tile whose origin sits
    just outside still extends TILE units back INTO the frame, and culling it
    removes something that should have been drawn. The error is one-sided,
    which is why the band appears on the left and never on the right.

    THE STOCK MODULE PROVES THE RULE. At scale 1.0 the frame is dst_x
    0..640, so `required()` gives left=320, right=0 -- and the game ships:

        STOCK_LEFT  = 336 = 320 + 16   <- required + exactly one tile
        stock right =   0 =   0 +  0   <- required + nothing

    The original developers applied this term. The widescreen patch shipped
    left=376, which is `required(0.75)` = 374 plus 2 -- the tile term was
    dropped. That leaves tiles whose origin is 376..389 units left of the
    camera culled while still visible: a black band up to one tile wide
    (~42 screen px at 1280) that appears and disappears as the camera slides
    across the 16-unit tile grid.

    MEASURED, and this is why TILE is 16 rather than 32: layers 3 and 4 do
    contain 32-unit tiles (the `size_flag` pages), but the cull only exists
    in `field_layer1_pick_tiles` and `field_layer2_pick_tiles`, and across
    all 709 fields ALL 641,253 layer-1/2 tiles draw from 16-unit pages. Zero
    exceptions.

    `top` keeps STOCK_TOP: it is also a low side, but 256 already exceeds
    required(224) + 16 = 240, so stock covers it with room to spare.
    """
    r = required(scale)
    return {'left': _up_to_tile(r['left'] + TILE),
            'right': _up_to_tile(r['right']),
            'top': max(STOCK_TOP, _up_to_tile(r['top'] + TILE)),
            'bottom': _up_to_tile(r['bottom'])}


def span(origin, low, high):
    """The drawn span [(O-L)*2, (O+R)*2] for one axis."""
    return (origin - low) * MULT, (origin + high) * MULT


# --------------------------------------------------------------------------
# the sites
# --------------------------------------------------------------------------
# Every VA is a module offset into `main`, md5 c5cbcec798ab854b828a149870deb473.
# `sig` is (va, word) pairs that must ALL match before anything is written;
# `bl` words are deliberately excluded because they are PC-relative and would
# make the signature depend on where the function landed.

SUB_W10_W9_W8 = 0x4B08012A      # sub w10, w9, w8 -- the displaced instruction
CAM = 8                          # w8 holds the camera at every hook site
# NB: do NOT name a register constant `TILE` here -- `TILE` is already the
# 16-unit tile SIZE used by defaults(), and shadowing it silently narrows the
# left extent from 400 to 393. w9 is spelled out at each use instead.


def _default_body(v):
    """The cave body for a high-side site that biases the CAMERA."""
    return [A.add_imm(CAM, CAM, v)]

# name -> dict
IMMEDIATE_SITES = {
    'left1': {
        'va': 0xA07244, 'stock': 336, 'axis': 'x', 'layer': 1,
        'word': lambda v: A.sub_imm(9, 8, v),
        'sig': [(0xA0723C, 0x79C00008),      # ldrsh w8, [x0]      cam_x
                (0xA07240, 0xB94006CA),      # ldr   w10, [x22,#4] tile.x (ecx)
                (0xA07244, 0x51054109),      # sub   w9, w8, #0x150
                (0xA07248, 0x6B090148)],     # subs  w8, w10, w9
        'why': 'layer1 LEFT extent. 376 was confirmed to CLOSE THE TAN/GREEN '
               'BAR on hardware, but it is 14 units short of admitting every '
               'partially-visible tile -- see defaults(). 400 is the derived '
               'value.'},
    'left2': {
        'va': 0xA05E00, 'stock': 336, 'axis': 'x', 'layer': 2,
        'word': lambda v: A.sub_imm(9, 8, v),
        'sig': [(0xA05DF8, 0x79C00008),
                (0xA05DFC, 0xB9400AEA),      # ldr w10, [x23,#8]  tile.x (edx)
                (0xA05E00, 0x51054109),
                (0xA05E04, 0x6B090148)],
        'why': 'layer2 LEFT extent.'},
    'top1': {
        'va': 0xA07138, 'stock': 256, 'axis': 'y', 'layer': 1,
        'word': lambda v: A.sub_imm(9, 8, v),
        'sig': [(0xA07130, 0x79C00008),
                (0xA07134, 0xB9400ACA),      # ldr w10, [x22,#8]  tile.y (edx)
                (0xA07138, 0x51040109),      # sub w9, w8, #0x100
                (0xA0713C, 0x6B090148)],
        'why': 'layer1 TOP extent. Stock 256 already clears the frame; here '
               'so a sweep can prove that rather than assume it.'},
    'top2': {
        'va': 0xA05CF4, 'stock': 256, 'axis': 'y', 'layer': 2,
        'word': lambda v: A.sub_imm(9, 8, v),
        'sig': [(0xA05CEC, 0x79C00008),
                (0xA05CF0, 0xB94002EA),      # ldr w10, [x23]     tile.y (eax)
                (0xA05CF4, 0x51040109),
                (0xA05CF8, 0x6B090148)],
        'why': 'layer2 TOP extent.'},

    # ------------------------------------------------------------------
    # THE PARALLAX VERTICAL IMMEDIATES -- moved here from
    # `ff7nx_fieldwide.VERTICAL_PATCHES`. FINDINGS-205.
    # ------------------------------------------------------------------
    # These three words shipped in build 96 out of `ff7nx_fieldwide`, with
    # hand-picked constants and no signature. They are the layer-3/4 twins of
    # `top1`/`top2`, so they belong in this table -- two mechanisms owning one
    # axis is what HANDOFF-204 s3.6 said to close before building again.
    #
    # `ptop3` and `ptop4` now ship at their STOCK 256, which WITHDRAWS build
    # 96's 272. See FINDINGS-205 s4: the extra 16 is at the BOTTOM, not the
    # top, so raising top_offset admitted nothing the frame could show. It was
    # inert, not wrong -- but it is not the derived value and it would collide
    # with this entry.
    #
    # `phalf3` KEEPS build 96's 120. That word was right, and it is the one
    # value on this axis that three independent derivations agree on:
    #
    #     vanilla        (224 + 0)  / 2 = 112   <- the stock word
    #     FFNx uncrop    (232 + 8)  / 2 = 120   <- background.cpp:133
    #     this port      (224 + 16) / 2 = 120   <- ORIGIN_Y + defaults()['bottom']
    #
    # LAYER 4 HAS NO half_height AND THAT IS NOT AN OVERSIGHT. Its wrap-
    # direction test is `tile.y >= bg.y + bottom_offset`, not
    # `tile.y >= bg.y - half_height` -- FFNx background.cpp:261 against :139,
    # and confirmed word-for-word in the module at 0xA08A68, which subtracts a
    # bare register with no immediate in front of it. So layer 4's
    # discriminator moves automatically when `pbottom4b` moves, which is
    # exactly the single-constant behaviour the C has.
    'ptop3': {
        'va': 0xA07B64, 'stock': 256, 'axis': 'y', 'layer': 3,
        'word': lambda v: A.sub_imm(9, 8, v),
        'sig': [(0xA07B54, A.add_imm(0, 8, 0xc)),   # add w0, w8, #0xc  cam_y
                (0xA07B5C, 0x79C00008),             # ldrsh w8, [x0]
                (0xA07B60, 0xB940032A),             # ldr  w10, [x25]  tile.y
                (0xA07B64, 0x51040109),             # sub  w9, w8, #0x100
                (0xA07B68, 0x6B090148)],            # subs w8, w10, w9
        'why': 'layer3 parallax top_offset. Stock 256 already clears the '
               'frame (needs 224); here so the sweep can prove it and so no '
               'second mechanism can claim this word.'},
    'phalf3': {
        'va': 0xA07C1C, 'stock': 112, 'axis': 'y', 'layer': 3,
        'word': lambda v: A.sub_imm(8, 8, v),
        'sig': [(0xA07C0C, A.add_imm(0, 8, 0xc)),
                (0xA07C14, 0x79C00008),
                (0xA07C18, 0xB9400729),             # ldr w9, [x25,#4]
                (0xA07C1C, 0x5101C108),             # sub w8, w8, #0x70
                (0xA07C20, SUB_W10_W9_W8)],
        'why': 'layer3 parallax half_height -- the WRAP DIRECTION midpoint, '
               'not a bound. 112 is half a 224-unit picture; the port shows '
               '240, so 120. Same word build 96 shipped.'},
    'ptop4': {
        'va': 0xA089B0, 'stock': 256, 'axis': 'y', 'layer': 4,
        'word': lambda v: A.sub_imm(9, 8, v),
        'sig': [(0xA089A0, A.add_imm(0, 8, 0xc)),
                (0xA089A8, 0x79C00008),
                (0xA089AC, 0xB9400AEA),             # ldr w10, [x23,#8]
                (0xA089B0, 0x51040109),
                (0xA089B4, 0x6B090148)],
        'why': 'layer4 parallax top_offset. Twin of ptop3.'},
}

# The high side. `arg` is the `add w0, w8, #imm` that selects which stack
# argument gets loaded: #8 is cam_x, #0xc is cam_y. That word IS the proof
# that this block is the axis we think it is, so it is in every signature.
CAVE_SITES = {
    'right1': {
        'va': 0xA072D0, 'axis': 'x', 'layer': 1,
        'sig': [(0xA072C0, A.add_imm(0, 8, 8)),       # add w0, w8, #8   cam_x
                (0xA072C8, 0x79C00008),               # ldrsh w8, [x0]
                (0xA072CC, 0xB9400AC9),               # ldr  w9, [x22,#8] tile.x
                (0xA072D0, SUB_W10_W9_W8),            # HOOK
                (0xA072D4, 0xB90002C8),               # str  w8, [x22]    eax
                (0xA072D8, 0x4A080128),               # eor  w8, w9, w8
                (0xA072DC, 0x531F7D4B),
                (0xA072E0, 0x4A09014A),
                (0xA072E4, 0x0A080148),
                (0xA072E8, 0x531F7D08),
                (0xA072EC, 0x6B0B011F)],              # cmp  w8, w11
        'why': 'layer1 RIGHT extent. Bare register compare -- needs a cave.'},
    'right2': {
        'va': 0xA05E8C, 'axis': 'x', 'layer': 2,
        'sig': [(0xA05E7C, A.add_imm(0, 8, 8)),
                (0xA05E84, 0x79C00008),
                (0xA05E88, 0xB94002E9),               # ldr w9, [x23]     tile.x
                (0xA05E8C, SUB_W10_W9_W8),
                (0xA05E90, 0xB90006E8),               # str w8, [x23,#4]  ecx
                (0xA05E94, 0x4A080128),
                (0xA05E98, 0x531F7D4B),
                (0xA05E9C, 0x4A09014A),
                (0xA05EA0, 0x0A080148),
                (0xA05EA4, 0x531F7D08),
                (0xA05EA8, 0x6B0B011F)],
        'why': 'layer2 RIGHT extent.'},
    'bottom1': {
        'va': 0xA071C8, 'axis': 'y', 'layer': 1,
        'sig': [(0xA071B8, A.add_imm(0, 8, 0xc)),     # add w0, w8, #0xc cam_y
                (0xA071C0, 0x79C00008),
                (0xA071C4, 0xB94002C9),               # ldr w9, [x22]    tile.y
                (0xA071C8, SUB_W10_W9_W8),
                (0xA071CC, 0xB90006C8),               # str w8, [x22,#4] ecx
                (0xA071D0, 0x4A080128),
                (0xA071D4, 0x531F7D4B),
                (0xA071D8, 0x4A09014A),
                (0xA071DC, 0x0A080148),
                (0xA071E0, 0x531F7D08),
                (0xA071E4, 0x6B0B011F)],
        'why': 'layer1 BOTTOM extent. Bare register compare -- needs a cave.'},
    'bottom2': {
        'va': 0xA05D84, 'axis': 'y', 'layer': 2,
        'sig': [(0xA05D74, A.add_imm(0, 8, 0xc)),
                (0xA05D7C, 0x79C00008),
                (0xA05D80, 0xB94006E9),               # ldr w9, [x23,#4] tile.y
                (0xA05D84, SUB_W10_W9_W8),
                (0xA05D88, 0xB9000AE8),               # str w8, [x23,#8] edx
                (0xA05D8C, 0x4A080128),
                (0xA05D90, 0x531F7D4B),
                (0xA05D94, 0x4A09014A),
                (0xA05D98, 0x0A080148),
                (0xA05D9C, 0x531F7D08),
                (0xA05DA0, 0x6B0B011F)],
        'why': 'layer2 BOTTOM extent.'},

    # ------------------------------------------------------------------
    # THE PARALLAX RIGHT EDGE -- ff7nx_fieldwide's KNOWN GAP, closed.
    # ------------------------------------------------------------------
    # Layers 3 and 4 do not CULL past the right edge, they WRAP. FFNx,
    # `ff7/field/background.cpp:126` (layer3) and `:249` (layer4):
    #
    #     if (tile.x <= bg.x - left_offset || tile.x >= bg.x + right_offset)
    #         tile.x += (tile.x >= bg.x - half_width) ? -w : +w;
    #
    # `right_offset` is 0 at 4:3 and |wide_viewport_x| in widescreen. At 0 a
    # tile anywhere in the RIGHT-HAND EXPANDED MARGIN satisfies
    # `tile.x >= bg.x + 0`, gets shifted by a whole layer width, and lands
    # back INSIDE the 4:3 picture. Reported from hardware 2026-08 as
    # "the expanded assets are pushed inwards and are under the 4:3 field
    # portion on the parts of the map that require the expanded artwork".
    #
    # `ff7nx_fieldwide.PARALLAX_PATCHES` moves `left_offset` and
    # `half_width` in place because both are immediates. `right_offset` is a
    # bare-register compare with no immediate to rewrite, which is why that
    # module documented it as a gap needing a cave. These are that cave, and
    # the block shape is IDENTICAL to right1/right2/bottom1/bottom2 above --
    # same displaced `sub w10, w9, w8`, same flag emulation -- so they reuse
    # this module's builder rather than introducing a second mechanism.
    #
    # THESE USE THE SAME CAMERA BIAS AS right1/right2, AND THAT IS DELIBERATE
    # ----------------------------------------------------------------------
    # `tile >= cam + v` and `tile - v >= cam` are the same inequality, so an
    # earlier revision of these sites emitted `sub w9, w9, #v` -- biasing the
    # TILE -- to avoid depending on the `str w8, [reg]` after the hook being
    # dead, which this module verified for layers 1 and 2 but not for 3 and 4.
    #
    # MEASURED, through test_wsclamp's own `verdict()` emulator, against the
    # real module: the tile bias LOST 428 tile positions where the camera
    # bias GAINS them. The two are not interchangeable here, which says the
    # write-back of the biased camera through `str w8` is load-bearing in the
    # recompiled flag emulation rather than incidental.
    #
    # So: the algebra was right and the machine disagreed, and the machine
    # wins. These emit `add w8, w8, #v`, exactly like the four sites above,
    # and inherit their verification along with their one assumption.
    'pright3': {
        'va': 0xA07D60, 'axis': 'x', 'layer': 3,
        'sig': [(0xA07D50, A.add_imm(0, 8, 8)),        # add w0, w8, #8  cam_x
                (0xA07D58, 0x79C00008),                # ldrsh w8, [x0]
                (0xA07D5C, 0xB9400329),                # ldr  w9, [x25]   tile.x
                (0xA07D60, SUB_W10_W9_W8),             # HOOK
                (0xA07D64, 0xB9000728),                # str  w8, [x25,#4]
                (0xA07D68, 0x4A080128),
                (0xA07D6C, 0x531F7D4B),
                (0xA07D70, 0x4A09014A),
                (0xA07D74, 0x0A080148),
                (0xA07D78, 0x531F7D08),
                (0xA07D7C, 0x6B0B011F)],
        'why': 'layer3 parallax RIGHT wrap point, 0 -> |wide_viewport_x|.'},
    'pright4a': {
        'va': 0xA08BA8, 'axis': 'x', 'layer': 4,
        'sig': [(0xA08B98, A.add_imm(0, 8, 8)),
                (0xA08BA0, 0x79C00008),
                (0xA08BA4, 0xB9400AE9),                # ldr w9, [x23,#8]
                (0xA08BA8, SUB_W10_W9_W8),
                (0xA08BAC, 0xB90002E8),                # str w8, [x23]
                (0xA08BB0, 0x4A080128),
                (0xA08BB4, 0x531F7D4B),
                (0xA08BB8, 0x4A09014A),
                (0xA08BBC, 0x0A080148),
                (0xA08BC0, 0x531F7D08),
                (0xA08BC4, 0x6B0B011F)],
        'why': 'layer4 parallax RIGHT wrap point (shift helper).'},
    'pright4b': {
        'va': 0xA08D40, 'axis': 'x', 'layer': 4,
        'sig': [(0xA08D30, A.add_imm(0, 8, 8)),
                (0xA08D38, 0x79C00008),
                (0xA08D3C, 0xB94002E9),                # ldr w9, [x23]
                (0xA08D40, SUB_W10_W9_W8),
                (0xA08D44, 0xB90006E8),                # str w8, [x23,#4]
                (0xA08D48, 0x4A080128),
                (0xA08D4C, 0x531F7D4B),
                (0xA08D50, 0x4A09014A),
                (0xA08D54, 0x0A080148),
                (0xA08D58, 0x531F7D08),
                (0xA08D5C, 0x6B0B011F)],
        'why': 'layer4 parallax RIGHT wrap point (pick loop).'},

    # ------------------------------------------------------------------
    # THE PARALLAX BOTTOM EDGE -- the vertical twin of pright3/4a/4b.
    # FINDINGS-205. THIS IS THE FIX.
    # ------------------------------------------------------------------
    # THE REPORT, twice from hardware: the Honey Bee Inn keyhole mask stops
    # short at the BOTTOM of the frame, and on any field with a vertically
    # scrolling parallax background the tiles pop in and out as the camera
    # moves up and down -- the same defect the Mt Corel fix closed sideways.
    #
    # THE MECHANISM, and it is a WRAP, not a cull. FFNx background.cpp,
    # layer 3 at :138 and layer 4 at :260, both reproduced word-for-word in
    # this module:
    #
    #     if (tile.y <= bg.y - top_offset || tile.y >= bg.y + bottom_offset)
    #         tile.y += (...) ? -layer_height : +layer_height;
    #
    # `bottom_offset` is 0 in stock. The port's picture runs to bg.y + 16.
    # So every tile in that 16-unit band tests as OUTSIDE the window and is
    # TELEPORTED a whole layer height away -- it does not fail to draw, it
    # draws somewhere else. That is precisely "pops in and out", and it is
    # why the keyhole mask ends where it does.
    #
    # THERE IS NO VERTICAL CULL TO PAIR THESE WITH, AND THAT IS MEASURED.
    # HANDOFF-204 s3.3 and FINDINGS-203 s2.3 both stop here, refusing to ship
    # three wraps without the cull that "admits tiles", on the strength of the
    # x axis having `pright4b`. The premise was wrong. Enumerating EVERY
    # branch -- conditional and unconditional -- that reaches either pick-loop
    # head, on the stock module:
    #
    #     layer 3 head 0xA079E0   1 branch    0xA07F80  anim_group
    #     layer 4 head 0xA0882C   4 branches  0xA08D10 \ x <= bg.x - 352
    #                                         0xA08D14 /
    #                                         0xA08D68   x >= bg.x + 0  (pright4b)
    #                                         0xA08E80   anim_group
    #                                         0xA09000   palette_index
    #
    # Layer 3's pick loop has no position test at all; layer 4's culls on x
    # only. There is no y site to find because the recompiled original does
    # not test y in the pick loop -- vertical visibility on the parallax
    # layers is decided ENTIRELY by the wrap above. So the three wraps are not
    # "half the fix waiting for a cull"; they are the whole of it, exactly as
    # `pright3`/`pright4a` are two thirds of the horizontal one.
    #
    # WHY LAYER 4 HAS TWO AND LAYER 3 HAS ONE. `bottom_offset` appears once in
    # layer 3's helper (the bound; the direction test uses `half_height`) and
    # TWICE in layer 4's (the bound, then again as the direction test -- FFNx
    # :261 uses `bg->y + bottom_offset` where :139 uses `- half_height`). It
    # is ONE constant in the C, so all three move together or none do. That is
    # the same lesson `PARALLAX_RIGHT_KNOBS` records: FFNx moves all of them.
    #
    # THE VALUE IS 16 AND IT IS NOT A GUESS. `defaults()['bottom']` is 16 from
    # this file's own frame arithmetic, and `bottom1`/`bottom2` -- the same
    # axis, the same ORIGIN_Y, the same MULT, already shipping and confirmed
    # on hardware -- run it. FFNx's 8 is not a contradiction: it splits the
    # revealed 32 screen units evenly around origin 232, and this port keeps
    # origin 224 and reveals downward. Both agree the picture is 240 units
    # tall, which is why both land on half_height 120.
    #
    # MARKED OPTIONAL. `check_all` skips optional sites and `ff7nx_ws`
    # verifies them separately, so a signature that stops matching drops these
    # three knobs and leaves the 16:9 stage standing. Build 97 aborted the
    # ENTIRE framing stage -- viewport, scissor, fade quad, every camera cave
    # -- because one unproven extra was allowed to raise inside the
    # transaction. HANDOFF-204 s4(b).
    'pbottom3': {
        'va': 0xA07BC8, 'axis': 'y', 'layer': 3, 'optional': True,
        'sig': [(0xA07BB8, A.add_imm(0, 8, 0xc)),  # add w0, w8, #0xc  cam_y
                (0xA07BC0, 0x79C00008),            # ldrsh w8, [x0]
                (0xA07BC4, 0xB9400B29),            # ldr  w9, [x25,#8] tile.y
                (0xA07BC8, SUB_W10_W9_W8),         # HOOK
                (0xA07BCC, 0xB9000328),            # str  w8, [x25]
                (0xA07BD0, 0x4A080128),
                (0xA07BD4, 0x531F7D4B),
                (0xA07BD8, 0x4A09014A),
                (0xA07BDC, 0x0A080148),
                (0xA07BE0, 0x531F7D08),
                (0xA07BE4, 0x6B0B011F)],
        'why': 'layer3 parallax BOTTOM wrap point, 0 -> 16. Vertical twin of '
               'pright3, same eleven-word block.'},
    'pbottom4a': {
        'va': 0xA08A14, 'axis': 'y', 'layer': 4, 'optional': True,
        'sig': [(0xA08A04, A.add_imm(0, 8, 0xc)),
                (0xA08A0C, 0x79C00008),
                (0xA08A10, 0xB94006E9),            # ldr w9, [x23,#4]
                (0xA08A14, SUB_W10_W9_W8),
                (0xA08A18, 0xB9000AE8),            # str w8, [x23,#8]
                (0xA08A1C, 0x4A080128),
                (0xA08A20, 0x531F7D4B),
                (0xA08A24, 0x4A09014A),
                (0xA08A28, 0x0A080148),
                (0xA08A2C, 0x531F7D08),
                (0xA08A30, 0x6B0B011F)],
        'why': 'layer4 parallax BOTTOM bound. Twin of pright4a.'},
    'pbottom4b': {
        'va': 0xA08A68, 'axis': 'y', 'layer': 4, 'optional': True,
        'sig': [(0xA08A58, A.add_imm(0, 8, 0xc)),
                (0xA08A60, 0x79C00008),
                (0xA08A64, 0xB94002E9),            # ldr w9, [x23]
                (0xA08A68, SUB_W10_W9_W8),         # HOOK
                (0xA08A6C, 0xB90006E8),            # str w8, [x23,#4]
                (0xA08A70, 0x4A080128),            # eor w8, w9, w8
                (0xA08A74, 0x51290260),            # sub w0, w19, #0xa40 <- NOT
                (0xA08A78, 0x531F7D5C),            #    part of the pattern
                (0xA08A7C, 0x4A09014A),
                (0xA08A80, 0x0A080148),
                (0xA08A84, 0x531F7D14)],
        'why': 'layer4 parallax BOTTOM wrap DIRECTION test -- the second use '
               'of the same bottom_offset constant, which is why it moves '
               'with pbottom4a. Its flag emulation is scheduled differently '
               '-- w28/w20, and an unrelated sub interleaved -- so it carries '
               'its own signature rather than the shared tail.'},
}

# WHICH OF THE THREE ARE SAFE TO SHIP, AND WHY IT IS ONLY ONE
# ===========================================================
# These three look identical -- same eleven-word signature, same displaced
# `sub w10, w9, w8` -- and they are NOT the same test. The branch after the
# flag emulation tells them apart:
#
#   pright4b  +0A08D10  b.ne +0A0882C   BACKWARD, to the loop head
#                                       -> taken means SKIP THIS TILE. A cull,
#                                          exactly like right1/right2.
#   pright3   +0A07D88  b.ne +0A07E6C   FORWARD, into the body
#   pright4a  +0A08BD0  b.ne +0A08CB4   FORWARD, into the body
#                                       -> taken means APPLY THE WRAP. Not a
#                                          cull at all.
#
# MEASURED through test_wsclamp's `verdict()` against the real module, with
# the bias at 107: pright4b GAINS 428 tile positions and its new edge is
# exactly cam+107. pright3 and pright4a LOSE 428 -- the same bias moves the
# wrap condition the wrong way, because "admit more tiles" and "wrap fewer
# tiles" are not the same transformation.
#
# So only the cull ships. The two shift-helper sites stay defined here,
# because finding and signature-verifying them is most of the work and the
# next session should not have to redo it, but they need the wrap branch
# analysed on its own terms before a bias direction can be chosen. Shipping
# them on the strength of the algebra is exactly the mistake HANDOFF-56 §4A
# and HANDOFF-57 §2 both record.
# ...AND THAT REASONING WAS WRONG. ALL THREE SHIP. FINDINGS-189 C.
#
# The paragraph above is kept because it records a real hazard, but its
# conclusion was drawn from the wrong oracle. `verdict()` models a CULL --
# "taken means skip this tile" -- so its gained/lost count is meaningless at a
# WRAP site, which this file already says two paragraphs up. Measuring a wrap
# with a cull metric and then declining to ship on the result is not caution,
# it is a category error with a safety label on it.
#
# THE RIGHT ORACLE IS FFNx'S SOURCE, AND IT IS UNAMBIGUOUS.
# `repos/FFNx-master/src/ff7/field/background.cpp`, both layer 3 and layer 4:
#
#   field_layer3_shift_tile_position:
#       const int right_offset = is_fieldmap_wide() ? abs(wide_viewport_x) : 0;
#       if (tile_position->x <= bg_position->x - left_offset ||
#           tile_position->x >= bg_position->x + right_offset)
#           tile_position->x += (...) ? -layer3_width : layer3_width;
#
#   field_layer3_pick_tiles:
#       const int right_offset = is_fieldmap_wide() ? abs(wide_viewport_x) : 0;
#       if (tile_position.x <= bg_position.x - left_offset ||
#           tile_position.x >= bg_position.x + right_offset || ...) continue;
#
# ONE `right_offset`, 0 -> 107, used identically by the wrap and by the cull.
# FFNx moves all four. Shipping only the cull is what leaves the wrap putting
# every tile of the right margin back inside the 4:3 picture -- which is the
# defect reported from hardware on Mt. Corel:
#
#   "as i move, the background that scrolls at a diff pace pops into view and
#    out of view as i move left to right to left"
#
# `ff7nx_ws.py` part E has described this exact mechanism since it was written.
#
# AND THE EMULATOR AGREES ONCE IT IS ASKED THE RIGHT QUESTION. Sweeping tile.x
# at cam = 1000 and reading where the branch flips:
#
#       site        stock boundary     with bias +107
#       pright4b    tile.x >= cam      tile.x >= cam+107     (cull)
#       pright3     tile.x <  cam      tile.x <  cam+107     (wrap)
#       pright4a    tile.x <  cam      tile.x <  cam+107     (wrap)
#
# All three move by exactly the bias, in the same direction, which is the
# transformation the C above performs. There was never a disagreement to
# resolve -- only a metric that could not express the answer.
PARALLAX_RIGHT_KNOBS = ('pright4b', 'pright3', 'pright4a')

# THE FLOOR FOR `parallax_right`. FINDINGS-272 -- see that function's
# docstring for the measurement and why raising it is the safe direction.
# 107 restores build 130.
PARALLAX_RIGHT_MIN = 107   # REVERTED, build 132. See FINDINGS-274.

# STILL LISTED, AND STILL MEANINGFUL: these two are WRAPS, not culls. The name
# is what keeps `CULL_CAVE_SITES` from asserting cull semantics against them in
# the test suite. It no longer means "not shipped".
#
# SEVENTH_NX_WS_PARALLAX_NO_SHIFT=1 drops them back out for an A/B.
PARALLAX_SHIFT_KNOBS = ('pright3', 'pright4a')

# The vertical set. All three are WRAPS -- see the block on `pbottom3` for why
# there is no cull to pair them with, and why that is a measurement and not an
# assumption. Named here so `CULL_CAVE_SITES` keeps `verdict()`'s cull oracle
# away from them, exactly as PARALLAX_SHIFT_KNOBS does on the x axis.
PARALLAX_BOTTOM_KNOBS = ('pbottom3', 'pbottom4a', 'pbottom4b')

# THE FLOOR FOR THE VERTICAL WRAP BOUND. FINDINGS-273 -- see
# `parallax_bottom_bound` for the measurement. 16 restores build 131.
#
# MEASURED over all 96 parallax layers in the archive, the bound each one
# needs so that no tile which can be on screen is teleported:
#
#     satisfied at   16   (today)     0 of 96
#     satisfied at  288              82 of 96
#     satisfied at  512              96 of 96
#
#     worst: crater_1 L4 512, mtcrl_5 L3 416, sbwy4_6 L3 392,
#            gaia_1/gaia_2 L4 384, midgal L3 376, wcrimb_1/2 L3 360
#
# 512 covers the archive and is exactly half the 1024 that `bg3/bg4_height`
# reads in almost every field, so a layer that genuinely repeats still wraps
# at its own midpoint rather than never.
PARALLAX_BOTTOM_MIN = 16   # NOT RAISED. See FINDINGS-274.

# The two in-place immediates that come with them. `ptop3`/`ptop4` ship at
# STOCK, which is what withdraws build 96's 272; `phalf3` keeps build 96's 120.
PARALLAX_TOP_KNOBS = ('ptop3', 'ptop4')
PARALLAX_HALF_KNOBS = ('phalf3',)

# Every knob the parallax VERTICAL fix touches, in one name.
PARALLAX_VERTICAL_KNOBS = (PARALLAX_BOTTOM_KNOBS + PARALLAX_TOP_KNOBS
                           + PARALLAX_HALF_KNOBS)


def parallax_right(scale):
    """
    `right_offset` for the parallax wrap, in game units, from the shader scale.

    The visible frame is `640 / scale` units wide and centred on the 4:3
    window, so the margin on each side is half the difference. At FFNx's
    0.75 that is 106.67 -> 107, which is exactly `abs(wide_viewport_x)`.
    Rounded UP, because the test is `>=`: one unit short wraps a tile that
    is still on screen, and one unit long merely draws a tile that is not.

    ...AND 107 IS TOO SHORT FOR A LAYER THAT IS NOT A REPEATING BACKDROP.
    FINDINGS-272, reported from hardware on `junonl2`.

    The rule this feeds is FFNx's, quoted in full at `PARALLAX_RIGHT_KNOBS`:

        if (tile.x <= bg.x - left_offset || tile.x >= bg.x + right_offset)
            tile.x += (...) ? -layer_width : layer_width;

    A tile outside the window is not culled, it is MOVED a whole layer width.
    For a repeating sky that is the entire point -- it is how a finite tile
    set covers an endless scroll, and the art is authored to survive it.

    **`junonl2`'s layer 4 is not a sky.** It is one object -- the train and
    the Rufus banner -- painted at a specific spot, and MEASURED it reaches
    `tile.x - bg.x = 352` while the window ends at 107. So its right-hand
    tiles are thrown a whole layer width and land back inside the picture as
    a second banner above the first, which is exactly what was photographed.

    THE DIRECTION IS THE SAFE ONE AND THIS DOCSTRING ALREADY SAID SO: "one
    unit short wraps a tile that is still on screen, and one unit long merely
    draws a tile that is not." Too small is a visible misplacement; too large
    costs a tile that is off screen anyway.

    384 AND NOT MORE. It clears `junonl2`'s measured 352 with margin, and it
    stays well under the 1024 that `bg3/bg4_width` reads in almost every
    field -- so a layer that genuinely repeats still wraps before it runs out
    of art. Going to the archive's worst case (768, `anfrst_3/4`) would
    approach that width and start disabling the wrap itself, which is a
    different change and needs its own evidence.

    `SEVENTH_NX_WS_PARALLAX_RIGHT` overrides it -- set 107 to reproduce
    build 130 exactly, or a larger value to test further.
    """
    import math
    env = os.environ.get('SEVENTH_NX_WS_PARALLAX_RIGHT', '').strip()
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    base = int(math.ceil((640.0 / scale - 640.0) / 2.0))
    return max(base, PARALLAX_RIGHT_MIN)


def parallax_bottom(scale=WS_SCALE):
    """
    `bottom_offset` for the parallax wrap, in game units.

    The same number `bottom1`/`bottom2` already ship on this axis, and for the
    same reason: the picture runs to `(ORIGIN_Y + R) * MULT >= GAME_H`, so
    R >= 16. Vertical is not scaled, so this does not depend on `scale` -- the
    argument is taken anyway so every knob in this file has one shape.
    """
    return defaults(scale)['bottom']


# THE PARALLAX LAYERS DO NOT USE ORIGIN_Y. FINDINGS-208.
#
# `ff7nx_uncrop` moves all four layers' tile origin 224 -> 232 -- it is in
# every build log as "layer 3 tile origin 224 -> 232 @ +0x0A07878" -- which is
# FFNx's `ff7_field_center ? 232 : 224`. So for layers 3 and 4:
#
#     dst_y = (232 - bg.y + tile.y) * MULT      picture = bg.y-232 .. bg.y+8
#
# not `bg.y-224 .. bg.y+16`. FINDINGS-205 s4 derived this file's parallax
# numbers against ORIGIN_Y and got `half_height` right BY COINCIDENCE --
# (224+16)/2 and (232+8)/2 are both 120 -- which is exactly why three
# "independent" derivations appeared to agree. They were the same mistake
# twice. FFNx's own constants were right from the start: top 256+8, bottom 8,
# half 120.
PARALLAX_ORIGIN_Y = 232
PARALLAX_UNCROP = PARALLAX_ORIGIN_Y - ORIGIN_Y          # 8


def parallax_top(scale=WS_SCALE):
    """
    `top_offset` for the parallax wrap: stock 256 plus the 8 units the
    centred origin moved, which is FFNx background.cpp:176 exactly.

    256 alone does cover a picture top of 232, so this is margin rather than a
    fix -- but it is the value FFNx ships and there is no reason to run 8
    units tighter than the source this whole subsystem was modelled on.
    """
    return STOCK_TOP + PARALLAX_UNCROP


def parallax_half_height(scale=WS_SCALE):
    """
    `half_height` -- the midpoint that picks which way a wrapped tile goes.

    Half the PICTURE, which is `ORIGIN_Y` above the camera plus whatever the
    bottom extent adds below it. Checks out against all three known cases:

        vanilla      (224 +  0) / 2 = 112   the stock word
        FFNx uncrop  (232 +  8) / 2 = 120   background.cpp:133
        this port    (224 + 16) / 2 = 120   and build 96 shipped 120
    """
    return (ORIGIN_Y + parallax_bottom(scale)) // 2


# Cave sites whose branch is a CULL (taken -> skip this tile). Every one of
# these answers to "bias the camera up, admit more tiles", which is what the
# test suite's `verdict()` models. The shift-helper and parallax-bottom sites
# are excluded: their branch applies a WRAP, so "gained/lost tiles" is not the
# right frame for them and asserting it would be asserting the wrong thing.
CULL_CAVE_SITES = {k: v for k, v in CAVE_SITES.items()
                   if k not in PARALLAX_SHIFT_KNOBS
                   and k not in PARALLAX_BOTTOM_KNOBS}


def parallax_bottom_bound(scale=WS_SCALE):
    """
    The parallax vertical WRAP BOUND -- which is NOT the picture's extent.

    FINDINGS-273. `parallax_bottom` is 16 because "the port's picture runs to
    bg.y + 16", and that number is correct for `parallax_half_height`, which
    is half the PICTURE. The two happened to share a value and were therefore
    the same function; they are not the same quantity.

    The bound's job is different. From the block above, in this file's own
    words: a tile outside the window *"is TELEPORTED a whole layer height
    away -- it does not fail to draw, it draws somewhere else."* So the bound
    has to cover where the ART can sit relative to `bg.y`, not where the
    picture ends. MEASURED on `junonl2`, the field reported from hardware:

        L3   art y -128..192   bg.y span -72..72   max(tile.y - bg.y) = 264
        L4   art y  -64..192   bg.y span -72..72   max(tile.y - bg.y) = 264
                                       against bottom_offset = 16  -> WRAPS

    Its layer 4 is one object -- the train and the Rufus banner -- so the
    teleported tiles are not a sky repeating, they are a second banner ABOVE
    the first, which is exactly what was photographed twice.

    512 is the archive's worst case (`crater_1` L4) and half the 1024 that
    `bg3/bg4_height` reads in almost every field, so a layer that genuinely
    repeats still wraps at its own midpoint. AT THE CURRENT 16, NONE of the
    archive's 96 parallax layers is safe -- every one has tiles that get
    teleported.

    The counter-risk of raising it is a layer that needed the wrap to fill
    the top of the frame and now runs out instead. That case is already
    served from the archive side: `ff7nx_parallaxfill` adds 6,340 tiles
    across 46 fields for exactly it (FINDINGS-207), so the wrap is not the
    only thing holding those layers up.

    RAISING IT IS THE SAFE DIRECTION, for the reason `parallax_right`'s
    docstring gives on the other axis: the test is `>=`, so one unit short
    teleports a tile that is still on screen, and one unit long merely keeps
    a tile that is off it.

    `parallax_half_height` deliberately still reads `parallax_bottom`, so the
    120 that build 96 shipped and `test_wsclamp` asserts does not move.

    `SEVENTH_NX_WS_PARALLAX_BOTTOM=16` restores build 131 exactly.
    """
    env = os.environ.get('SEVENTH_NX_WS_PARALLAX_BOTTOM', '').strip()
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return max(parallax_bottom(scale), PARALLAX_BOTTOM_MIN)


def parallax_vertical_values(scale=WS_SCALE):
    """The parallax VERTICAL knobs and their derived values. FINDINGS-205."""
    out = {}
    for knob in PARALLAX_BOTTOM_KNOBS:
        out[knob] = parallax_bottom_bound(scale)
    for knob in PARALLAX_TOP_KNOBS:
        out[knob] = parallax_top(scale)
    for knob in PARALLAX_HALF_KNOBS:
        out[knob] = parallax_half_height(scale)
    return out


def shipped_values(scale=WS_SCALE, shift_helpers=False, vertical=True):
    """
    Every knob a real build sets: the four axis extents, the parallax right
    edge, and the parallax bottom edge. This is what ff7nx_ws passes to
    spec(), so a test that uses it is testing the shipping configuration
    rather than a subset of it.
    """
    out = defaults(scale)
    pr = parallax_right(scale)
    for knob in PARALLAX_RIGHT_KNOBS:
        out[knob] = pr
    if shift_helpers:
        for knob in PARALLAX_SHIFT_KNOBS:
            out[knob] = pr
    if vertical:
        out.update(parallax_vertical_values(scale))
    return out

ALL_SITES = dict(IMMEDIATE_SITES, **CAVE_SITES)


class SiteMismatch(Exception):
    """A signature did not match -- refuse rather than patch blind."""


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _hex(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def check_site(img, name, patched_ok=True):
    """
    Verify one site's signature. Returns 'stock', or 'patched' if the hook
    word has already been replaced by a branch and everything else matches.
    """
    site = ALL_SITES[name]
    hook_va = site['va']
    bad = []
    for va, want in site['sig']:
        got = _word(img, va)
        if got == want:
            continue
        if va == hook_va and patched_ok and (got & 0xFC000000) == 0x14000000:
            continue                                   # already hooked
        if va == hook_va and patched_ok and name in IMMEDIATE_SITES:
            # an immediate site legitimately holds a different imm12
            if (got & 0xFFC003FF) == (want & 0xFFC003FF):
                continue
        bad.append((va, want, got))
    if bad:
        raise SiteMismatch(
            '%s: %s' % (name, '; '.join(
                '+0x%07X expected %08X got %08X' % (va, w, g)
                for va, w, g in bad)))
    return ('patched' if _word(img, hook_va) != dict(site['sig'])[hook_va]
            else 'stock')


def check_all(img):
    """
    Verify every LOAD-BEARING site. Raises on the first mismatch.

    Sites carrying `optional` are skipped on purpose. `check_all` is called
    from inside `ff7nx_ws.apply_module`'s one transaction, whose `except`
    logs a line and writes NO module at all -- so anything raising here takes
    the viewport, the uncrop scissor, the fade quad and every camera cave down
    with it. That is build 97, exactly: one unproven extra inside the `try`
    aborted the whole 16:9 stage (HANDOFF-204 s4b). Optional sites are checked
    by `verified_optional()` instead, which returns names rather than raising.
    """
    return {n: check_site(img, n) for n, s in ALL_SITES.items()
            if not s.get('optional')}


def verified_optional(img, names, log=lambda *_: None):
    """
    The subset of `names` whose signature matches -- never raises.

    This is how an optional knob is allowed to fail: the caller drops it from
    `values` and ships everything else, instead of the build losing its 16:9.
    """
    out = []
    for name in names:
        if name not in ALL_SITES:
            log('  ! parallax vertical: no such site %r; skipped' % name)
            continue
        try:
            check_site(img, name)
        except SiteMismatch as exc:                            # noqa: PERF203
            log('  ! parallax vertical: %s -- knob dropped, the rest of the '
                '16:9 stage is unaffected' % exc)
            continue
        out.append(name)
    return out


# --------------------------------------------------------------------------
# reading the current values back out of a module
# --------------------------------------------------------------------------
def read_value(img, name):
    """
    The extent currently encoded at `name`, or None if it cannot be decoded.

    For a cave site this follows the hook branch and reads the `add w8, w8,
    #imm` at the far end, so a readback proves what the module will actually
    do rather than what the build log said it did.
    """
    site = ALL_SITES[name]
    w = _word(img, site['va'])
    if name in IMMEDIATE_SITES:
        for v in range(0, 4096):
            if w == site['word'](v):
                return v
        return None
    if w == SUB_W10_W9_W8:
        return 0
    if (w & 0xFC000000) != 0x14000000:
        return None
    off = w & 0x03FFFFFF
    if off & 0x02000000:
        off -= 0x04000000
    entry = site['va'] + off * 4
    body = _word(img, entry)
    make = site.get('body', _default_body)
    for v in range(0, 4096):
        if body == make(v)[0]:
            return v
    return None


def report(img):
    """[(name, value, stock, site)] for every knob, decoded from the module."""
    out = []
    for name, site in ALL_SITES.items():
        stock = site.get('stock', 0)
        out.append((name, read_value(img, name), stock, site))
    return out


# --------------------------------------------------------------------------
# building the patches
# --------------------------------------------------------------------------
def immediate_patches(img, values):
    """
    {va: word} for the low-side extents. `values` maps knob name -> extent.
    """
    out = {}
    for name, v in values.items():
        if name not in IMMEDIATE_SITES:
            continue
        site = IMMEDIATE_SITES[name]
        if not 0 <= v < 4096:
            raise ValueError('%s = %d is not encodable in a sub imm12'
                             % (name, v))
        check_site(img, name)
        want = site['word'](v)
        if _word(img, site['va']) != want:
            out[site['va']] = want
    return out


def cave_patches(img, values, starts=None, pool=None, log=lambda *_: None):
    """
    {va: word} for the high-side biases, including the `b cave` hook words.

    One cave per site, three words each:  add w8, w8, #R / the displaced
    sub / b back. `ff7nx_cave` re-checks that every hole is still zero in
    THIS module at allocation time, so a cave never lands on another patch.
    """
    import ff7nx_cave
    if pool is None:
        pool = ff7nx_cave.HolePool(img, starts=starts)
    out = {}
    for name, v in sorted(values.items()):
        if name not in CAVE_SITES:
            continue
        if v == 0:
            continue
        if not 0 <= v < 4096:
            raise ValueError('%s = %d is not encodable in an add imm12'
                             % (name, v))
        site = CAVE_SITES[name]
        state = check_site(img, name)
        if state == 'patched':
            raise SiteMismatch(
                '%s is already hooked. Caves are not idempotent -- re-hooking '
                'a hooked site chains a second bias onto the first. Start '
                'from a module where this site is stock.' % name)
        body = site.get('body', _default_body)(v)
        words, entry = ff7nx_cave.emit_hooked(
            pool, site['va'], SUB_W10_W9_W8, body)
        log('  %-8s +%07X  bias %s %+d  -> cave at +%07X (%d words)'
            % (name, site['va'],
               'tile' if 'body' in site else 'camera',
               -v if 'body' in site else v,
               entry, len(words) - 1))
        overlap = set(out) & set(words)
        if overlap:
            raise SiteMismatch('two caves want +0x%07X' % min(overlap))
        out.update(words)
    return out, pool


AXES = {'left': ('left1', 'left2'), 'right': ('right1', 'right2'),
        'top': ('top1', 'top2'), 'bottom': ('bottom1', 'bottom2')}


def expand(values):
    """
    Turn per-AXIS values into the eight per-KNOB values the patcher takes.

    Both layers must always move together -- layer 1 is the background and
    layer 2 the overlay that sits on top of it, so widening one and not the
    other shows the seam. Per-knob names are still accepted so a sweep can
    deliberately break that symmetry to isolate which layer an artefact is
    coming from.
    """
    out = {}
    for name, v in values.items():
        if name in AXES:
            for knob in AXES[name]:
                out[knob] = v
        else:
            out[name] = v
    return out


def build(img, values, starts=None, log=lambda *_: None, pool=None):
    """
    Every word this module wants to change, as {va: word}.

    `pool` lets a caller that is emitting OTHER caves into the same module
    share one allocator with this one. Passing None builds a private pool,
    which is correct only when nothing else in the same transaction takes
    holes -- see the note on the shared pool in ff7nx_ws.apply_module.

    `values` maps knob name -> extent; anything absent is left alone. Nothing
    is written here -- the caller hands the result to nso_patcher, which
    verifies each original byte first.
    """
    values = expand(values)
    unknown = set(values) - set(ALL_SITES)
    if unknown:
        raise ValueError('unknown knob(s): %s' % ', '.join(sorted(unknown)))
    out = dict(immediate_patches(img, values))
    caves, _pool = cave_patches(img, values, starts=starts, pool=pool,
                                log=log)
    clash = set(out) & set(caves)
    if clash:
        raise SiteMismatch('immediate and cave both want +0x%07X'
                           % min(clash))
    out.update(caves)
    return out


def revert_patches(img):
    """
    {va: word} that puts all eight sites back to stock.

    The cave BODIES are left where they are. Restoring the hook word makes
    them unreachable, which is the whole of the behaviour change; zeroing
    them as well would mean re-deriving which padding words were ours, and a
    mistake there would corrupt a neighbouring patch's cave. Dead words in
    padding cost nothing and the pool will not reuse them, so this is the
    conservative direction.
    """
    out = {}
    for name, site in IMMEDIATE_SITES.items():
        stock = site['word'](site['stock'])
        if _word(img, site['va']) != stock:
            out[site['va']] = stock
    for name, site in CAVE_SITES.items():
        if _word(img, site['va']) != SUB_W10_W9_W8:
            out[site['va']] = SUB_W10_W9_W8
    return out


def revert_spec(img):
    """An nso_patcher spec that turns the tile-window widening off."""
    return {
        'name': 'field tile window -> stock 4:3',
        'patches': [{'name': 'restore', 'va': va,
                     'expect': _hex(_word(img, va)), 'set': _hex(w)}
                    for va, w in sorted(revert_patches(img).items())],
    }


def spec(img, values, starts=None, log=lambda *_: None, pool=None):
    """An nso_patcher spec for `build`'s output."""
    words = build(img, values, starts=starts, log=log, pool=pool)
    hooks = {s['va'] for s in CAVE_SITES.values()}
    return {
        'name': 'field background tile window, 16:9',
        'patches': [
            {'name': ('hook -> cave' if va in hooks else
                      'extent' if va in {s['va'] for s in
                                         IMMEDIATE_SITES.values()}
                      else 'cave word'),
             'va': va,
             'expect': _hex(_word(img, va)),
             'set': _hex(word)}
            for va, word in sorted(words.items())],
    }


def collapse(values):
    """Per-knob values folded back to per-axis, for the geometry report."""
    out = {}
    for name, v in (values or {}).items():
        for axis, knobs in AXES.items():
            if name == axis or name in knobs:
                out[axis] = v
    return out


def describe(values=None, scale=WS_SCALE):
    """Human-readable geometry for the log. Pure arithmetic, no module."""
    v = dict(defaults(scale)); v.update(collapse(values))
    req = required(scale)
    lo, hi = visible_x(scale)
    lines = ['  frame:  game-x %.2f .. %.2f  (WS_SCALE %.6f), game-y 0 .. 480'
             % (lo, hi, scale)]
    for axis, origin, low, high, flo, fhi in (
            ('horizontal', ORIGIN_X, v['left'], v['right'], lo, hi),
            ('vertical', ORIGIN_Y, v['top'], v['bottom'], 0.0, GAME_H)):
        s0, s1 = span(origin, low, high)
        ok = 'covers' if (s0 <= flo and s1 >= fhi) else 'SHORT'
        lines.append('  %-10s O=%d L=%d R=%d -> span %d .. %d   %s'
                     % (axis, origin, low, high, s0, s1, ok))
    lines.append('  minimum extents for this scale: L>=%d R>=%d (x), '
                 'L>=%d R>=%d (y)'
                 % (req['left'], req['right'], req['top'], req['bottom']))
    return lines


# --------------------------------------------------------------------------
# the fast loop -- edit a built module in place, no rebuild
# --------------------------------------------------------------------------
def main(argv=None):
    """
    HANDOFF-48 §5's loop, for these four sides.

        python3 ff7nx_wsclamp.py <main> --show
        python3 ff7nx_wsclamp.py <main> --wide          # the shipping set
        python3 ff7nx_wsclamp.py <main> --set right=64
        python3 ff7nx_wsclamp.py <main> --set right1=64 # one layer, to isolate

    A full build is ~20 minutes and rebuilds 380 MB of archives none of this
    touches. Edit, copy `exefs/main`, reboot.
    """
    import argparse
    from pathlib import Path
    import nso_patcher
    import nxmap

    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('main')
    ap.add_argument('--set', action='append', default=[], metavar='NAME=VAL')
    ap.add_argument('--wide', action='store_true',
                    help='apply the shipping extents for WS_SCALE=%.2f'
                         % WS_SCALE)
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    m = nxmap.Main(a.main)
    print('module %s' % a.main)
    try:
        check_all(m.img)
    except SiteMismatch as exc:
        print('! this is not a module this tool understands:\n    %s' % exc)
        return 2

    values = {}
    if a.wide:
        values.update(defaults())
    for spec_str in a.set:
        name, _, val = spec_str.partition('=')
        name = name.strip()
        if name not in ALL_SITES and name not in AXES:
            print('! unknown knob %r; known: %s'
                  % (name, ', '.join(sorted(set(ALL_SITES) | set(AXES)))))
            return 2
        values[name] = int(val)

    if a.show or not values:
        for line in describe():
            print(line)
        print()
        for name, val, stock, site in report(m.img):
            print('  %-8s +%07X  = %-8s (stock %d)  %s'
                  % (name, site['va'],
                     val if val is not None else 'UNKNOWN', stock,
                     site['why']))
        return 0

    for line in describe(values):
        print(line)
    print()
    try:
        sp = spec(m.img, values, starts=set(m.arm_starts), log=print)
    except (SiteMismatch, ValueError) as exc:
        print('! %s' % exc)
        return 2
    if not sp['patches']:
        print('  nothing to do -- already at these values')
        return 0
    print('  %d word(s)' % len(sp['patches']))
    if a.dry_run:
        print('  --dry-run: nothing written')
        return 0
    nso = nso_patcher.read_nso(Path(a.main))
    nso_patcher.apply_spec(nso, sp)
    tmp = a.main + '.tmp'
    Path(tmp).write_bytes(nso_patcher.rebuild(nso))
    os.replace(tmp, a.main)
    print('  written. Copy exefs/main to the SD card and reboot.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
