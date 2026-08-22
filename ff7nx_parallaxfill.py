#!/usr/bin/env python3
"""
ff7nx_parallaxfill.py -- the parallax layers do not reach the top of the frame.

THE REPORT, from hardware after build 99, on `wcrimb_2`:

    "a black bar appears as i move up and the background parallax field moves
     down, then it fills in and its all the way at the top and as i move up
     the black bar re-appears, grows, then vanishes again"

FINDINGS-207 has the measurement. In one paragraph:

Layers 3 and 4 do not cull vertically -- FINDINGS-205 proved there is no
vertical cull in either pick loop -- so what puts a parallax tile on screen is
the WRAP in `field_layer3/4_shift_tile_position`, which repeats the layer
every `layer_height`. `layer_height` comes from the trigger header's
`bg3_height` / `bg4_height`, and that field is a PLACEHOLDER: it reads 1024 in
55 of the archive's 96 parallax layers, and matches the art in only 39. On
`wcrimb_2` it declares 1024 against 320 units of art, so a wrapped tile lands
700 units away and the layer simply runs out at the top of the picture. The
uncovered band is 39 units at one end of the camera's travel and 89 at the
other, which is the bar appearing, growing and vanishing.

THIS IS NOT A REGRESSION. The band is identical, to the unit, in vanilla,
build 98 and build 99 -- and layer 3's vertical extent is byte-for-byte
vanilla's. Cosmos widened these layers HORIZONTALLY (x +/-160 -> +/-224) and
never vertically.

WHY THIS FILLS ART INSTEAD OF CORRECTING THE HEADER
===================================================
The obvious fix is to write the measured extent into `bg3_height`. Measured,
that closes 31 of the 64 gapped layers outright and improves 18 more with no
regressions -- but `bg3_height` is not only the wrap period. FFNx
`background.cpp:889`:

    bg_layer3_pos.y = remainder(bg_layer3_pos.y, header->bg3_height);

so the same word also reduces the layer's SCROLL POSITION. Changing it moves
the layer as well as its repeat, and on a field whose camera travels far that
is a visible jump, not a fill. It also feeds `do_increase_width`.

Adding the art instead touches nothing but the tile array:

  * no header field changes, so scroll position, `remainder` and
    `do_increase_width` all keep the values they have today;
  * it works whatever the port does with `bg3_height`, which is the part
    this project cannot read off FFNx's source;
  * it fixes BOTH failure classes with one mechanism -- the wrong declared
    height AND the 16 layers whose art is shorter than the 240-unit picture,
    where no height can help because one wrap cannot fill a frame taller
    than the layer;
  * it is what FFNx does for exactly this case. `background.cpp:170-217`,
    `do_increase_height`: draw the tile set a SECOND time at an offset.

WHAT IT DOES
============
For each parallax layer, the camera's reachable travel gives the band of
`tile.y - bg.y` the picture can ever show. Where the layer's own rows do not
cover that band, rows are COPIED from the existing art at +/- the layer's
measured span, which is the same repeat the wrap would have performed if the
header had been right.

A copied record is the source record BYTE FOR BYTE with only `dst_y` changed,
so it names the same page, uv, palette and blend, and every later pass that
renumbers pages renumbers both copies identically.

THE ONE THING TO LOOK AT ON HARDWARE
====================================
This makes the backdrop genuinely TILE. Art that does not join to itself will
show a seam where it used to show black. A seam is the correct trade -- it is
what the wrap does on every field where the header is right -- but it is a
visible change and `wcrimb_2` and `mtcrl_4` are the two to photograph.

SEVENTH_NX_NO_PARALLAX_FILL=1 turns this off.
"""
from __future__ import annotations

import math
import os
import struct

TILE_SIZE = 52               # one tile record, verified across all 709 fields
PTILE = 32                   # layers 3/4 draw 32-unit tiles (FINDINGS-193)
T_DSTY = 4                   # dst_y within a tile record
T_DSTX = 2                   # dst_x -- offset 2, NOT 0. FINDINGS-256.

SECTION_TRIGGERS = 7
SECTION9 = 8

# THE PICTURE BAND, AND BUILD 100 GOT IT WRONG. FINDINGS-208.
#
# `ff7nx_uncrop` moves all four layers' tile origin 224 -> 232 (every build
# log: "layer 3 tile origin 224 -> 232 @ +0x0A07878"), so
#
#     dst_y = (232 - bg.y + tile.y) * 2    ->   picture = bg.y-232 .. bg.y+8
#
# Build 100 used -224..+16, taken from `ff7nx_wsclamp.ORIGIN_Y`, which is the
# layer-1/2 origin and stale for layers 3 and 4.
ORIGIN_Y = 232
PICTURE_TOP = -232
PICTURE_BOT = 8

# THE HORIZONTAL PICTURE. BUILD 128 GOT THIS WRONG BY 160 UNITS AND THAT IS
# WHY IT FILLED NINE COLUMNS ON THE LEFT AND NONE ON THE RIGHT. FINDINGS-281.
#
# The old constants (-459 / +107 / -427) treated `left_offset` and
# `right_offset` as if they were the picture measured from `bg.x`. They are
# not: they are the WRAP window, and the picture is a different interval on
# the same axis. FFNx `background.cpp` gives both, exactly:
#
#     initial_pos.x = (320 - bg.x) * mult          field_layer3_pick_tiles
#     bg_layer3_pos.x = remainder(bg3_pos_x/16 + bg3_speed_x*dx/256, bg3_width)
#                       + 320 - field_bg_offset->x - shake
#
# so the screen position of a tile, in field units, is
#
#     screen(tile.x) = 320 - bg.x + tile.x
#
# and the 4:3 viewport is screen 0..320. `field_bg_offset->x` is 160 in every
# field this archive ships, which the ARCHIVE ITSELF proves: 34 pinned layers
# are authored at exactly x -160..160 with `bg?_pos_x == 0`, and a 4:3 picture
# with no black bars forces `bg.x == 160` for them. So
#
#     bg.x  = remainder(pos_x/16, width) + 160        (pinned; speed_x == 0)
#
# The widescreen viewport is `wide_viewport_x = -107` px wide
# `wide_viewport_width = 854` px against a 640 px / 320 unit 4:3 frame, i.e.
# 2 px per unit, so it adds (854 - 640) / 2 / 2 = 53.5 UNITS ON EACH SIDE:
#
#     16:9 viewport = screen -53.5 .. +373.5
#                   = tile.x  bg.x - 373.5 .. bg.x + 53.5
#
# For the 34 layers at pos_x 0 that is tile.x -213.5..213.5 against art that
# stops at +/-160 -- 53.5 units of black on EACH side, which is Patrick's
# report on `fship_2` to the unit, and it is why COSMOS widened layers 1 and 2
# to exactly +/-224. Layer 3 is the layer Cosmos did not widen.
ORIGIN_X = 320                 # `initial_pos.x = (320 - bg.x) * mult`
BG_OFFSET_X = 160              # `field_bg_offset->x`, measured, see above
HALF_VIEW_43 = 160.0
HALF_VIEW_169 = 854 / 4.0      # 213.5 -- ceil() in FFNx, exact here
PICTURE_MARGIN_X = HALF_VIEW_169 - HALF_VIEW_43        # 53.5 per side

# THE ENGINE'S WRAP WINDOW, WHICH IS A DIFFERENT INTERVAL AND IS WHY THE
# RIGHT-HAND COLUMNS HAVE TO BE ENCODED RATHER THAN PLACED.
#
# `field_layer3_shift_tile_position`, and the port has no cull at all -- the
# x86 original relies on the viewport to clip, so this conditional is the ONLY
# thing that moves a parallax tile:
#
#     if (tile.x <= bg.x - left_offset || tile.x >= bg.x + right_offset)
#         tile.x += (tile.x >= bg.x - half_width) ? -width : +width;
#
# `ff7nx_fieldwide.PARALLAX_PATCHES` moves left_offset 352 -> 459 and
# half_width 160 -> 213 in place. It leaves `right_offset` at 0 -- its own
# KNOWN GAP comment says so, because a zero has no immediate to rewrite -- so
# ANY tile at `tile.x >= bg.x` is displaced by a whole `width`.
#
# The right margin starts at exactly `tile.x == bg.x`. So a right-hand column
# CANNOT simply be written where it should be drawn: the engine would move it.
# It has to be written at `x + width`, which this same conditional then brings
# back to `x`. That is not a trick -- it is the wrap doing its job, and it is
# what makes the right margin reachable with NO code patch at all.
ENGINE_LEFT_OFFSET = 459       # patched
ENGINE_RIGHT_OFFSET = 0        # NOT patched -- ff7nx_fieldwide KNOWN GAP
ENGINE_HALF_WIDTH = 213        # patched
# The stock 4:3 triple, used only as a safety assertion: an added column must
# never land inside the 4:3 picture when the widescreen words are absent.
ENGINE_43 = (352, 0, 160)
ENGINE_169 = (ENGINE_LEFT_OFFSET, ENGINE_RIGHT_OFFSET, ENGINE_HALF_WIDTH)
# ...and the same with `right_offset` patched to 107, so that closing
# ff7nx_fieldwide's KNOWN GAP later cannot silently invalidate this pass.
ENGINE_169_FULL = (ENGINE_LEFT_OFFSET, 107, ENGINE_HALF_WIDTH)

# At most this many 32-unit columns per side. 53.5 units is two; `bwhlin`'s
# off-centre `pos_x` needs three. Four is the refusal point, and a layer that
# wants more is not the defect this pass was written for.
MAX_EDGE_COLS = 4

# Refuse rather than bloat. A layer needing more than this many extra rows is
# not the defect this pass was written for, and quietly tripling a field's
# tile count is how the per-page frame cap (FINDINGS-110) gets overrun.
MAX_EXTRA_ROWS = 12
MAX_EXTRA_TILES = 1400

OFF_ENV = 'SEVENTH_NX_NO_PARALLAX_FILL'
# The horizontal twin, separately switchable so the two axes can be A/B'd
# apart -- the vertical fill has been on hardware since build 100 and the
# horizontal one has not.
# DEFAULT OFF -- BUILD 128 WAS A REGRESSION AND THIS IS WHY.
#
# Repeating a scrolling layer's columns makes the backdrop tile HORIZONTALLY,
# and on `fship_1` the sea does not join to itself: hardware showed a row of
# repeated squares marching across the water. The vertical fill got away with
# the same trick because a sky gradient tiles invisibly and a sea texture does
# not. Opt-in only until there is a seam test that can tell the two apart.
FILL_X = os.environ.get('SEVENTH_NX_PARALLAX_FILL_X') == '1'
# The PINNED-layer edge extension. Separate again, because it is a different
# mechanism with a different risk: `FILL_X` repeats a scrolling layer, this
# one extends a stationary layer's outermost column outward.
#
# DEFAULT OFF AGAIN AFTER BUILD 148 MEASURED IT ON HARDWARE. FINDINGS-283.
#
# Build 148 turned this on with the window corrected. Hardware: `fship_2` was
# UNCHANGED -- the screenshots from builds 147 and 148 are byte-identical in
# every edge column. Two things were wrong, and the second one is fatal:
#
#   1. THE MARGIN WAS ALREADY AUTHORED. Build 148's census read the VANILLA
#      dump, where `fship_2` layer 3 is x -160..160. The build does not run on
#      that: Cosmos Limit Break ships `fship_2.chunk.9` with layer 3 at
#      x -224..224, 14 columns. Cosmos widened the parallax the same way it
#      widened layers 1 and 2. There was no missing art.
#
#   2. THE ENGINE THROWS THE RIGHT HALF AWAY, AND THE ARCHIVE CANNOT STOP IT.
#      `field_layer3_pick_tiles` applies the same bound as a CULL after the
#      shift, and `right_offset` is 0 in this port. Cosmos's columns at
#      x 160 and 192 wrap to -864/-832 and are dropped. The columns build 148
#      wrote at x + width DO come back to 160/192 through the wrap -- and are
#      then dropped by the cull, which is why nothing changed.
#
#      It is not addressable. `screen(x) = 320 - bg.x + x` and the cull is
#      `x < bg.x + 0`, so `screen < 320` for every tile the engine will draw,
#      whatever `bg?_pos_x` and `dst_x` are set to. 320 is the 4:3 right edge.
#
# MEASURED on the shipped build 148: 1,069 tiles encoded for the right margin
# across 23 fields, and the engine culls 1,069 of them -- 100%. The pass is
# pure weight. It stays off until `ff7nx_fieldwide`'s KNOWN GAP is closed
# (`right_offset` 0 -> 107), and once that is closed Cosmos's own columns draw
# and this pass is very likely not needed at all.
EDGE_X = os.environ.get('SEVENTH_NX_PARALLAX_EDGE_X') == '1'

# Layers 3/4 normally represent a repeating backdrop, but that is not an
# invariant of the format. `junonl2` and `junonr2` layer 4 are moving
# foreground objects: two views of the train and Rufus banner.  The generic
# vertical filler treated their non-zero scroll speed as proof they tile:
#
#   junonl2  56 authored tiles -> 56 copied tiles at y=-320..-96
#   junonr2  95 authored tiles -> 46 copied tiles at y=-352..-192
#
# The `junonl2` copy's last row (-96..-64) is the duplicate-banner strip
# reported on hardware.  Rendering `junonr2` proves its copied rows are the
# lower half of the same one-off Rufus structure placed above itself.
#
# Do not infer tileability from non-zero scroll speed: this layer scrolls at
# 256 and is still an overlay, not a backdrop.  Keep the exclusion at the
# field/layer boundary, rather than changing the shared wrap/fill arithmetic;
# every other layer continues to use the existing, hardware-tested behavior.
NON_TILEABLE_OVERLAYS = frozenset({('junonl2', 4), ('junonr2', 4)})


class FillError(Exception):
    """This section is not a layout this pass understands. Skip it."""


def disabled():
    return os.environ.get(OFF_ENV) == '1'


# --------------------------------------------------------------------------
# the trigger header -- FFNx ff7.h `field_trigger_header`
# --------------------------------------------------------------------------
def trigger_header(sec7):
    cr_left, cr_bottom, cr_right, cr_top = struct.unpack_from('<4h', sec7, 0x0C)
    (bg3_w, bg3_h, bg4_w, bg4_h,
     bg3_px, bg3_py, bg4_px, bg4_py,
     bg3_sx, bg3_sy, bg4_sx, bg4_sy) = struct.unpack_from('<12h', sec7, 0x18)
    # The X pair used to be discarded (`_b3px`, `_b3sx`). It is kept now for
    # the horizontal twin -- see `plan_layer_x`.
    return {'bg3_w': bg3_w, 'bg3_h': bg3_h, 'bg4_w': bg4_w, 'bg4_h': bg4_h,
            'bg3_pos_y': bg3_py, 'bg4_pos_y': bg4_py,
            'bg3_speed_y': bg3_sy, 'bg4_speed_y': bg4_sy,
            'bg3_pos_x': bg3_px, 'bg4_pos_x': bg4_px,
            'bg3_speed_x': bg3_sx, 'bg4_speed_x': bg4_sx,
            'cam_left': cr_left, 'cam_bottom': cr_bottom,
            'cam_right': cr_right, 'cam_top': cr_top}


def bg_y_span(hdr, layer):
    """
    (lo, hi) of `bg.y` -- where this layer can ever sit relative to the camera.

    BUILD 100 COMPUTED THIS FROM THE HEADER AND IT WAS FICTION. FINDINGS-208.

    `bg3_pos_y/16 + bg3_speed_y*delta_y/256` (background.cpp:885) is the
    RESTING arithmetic only. `BGSCR` (opcode 0x2d,
    `scrollBackgroundLayer{layerId, xSpeed, ySpeed}`) lets the field script
    scroll a layer directly, and its displacement accumulates at runtime, so
    no static read can bound it.

    A CORRECTION TO WHAT WAS FIRST WRITTEN HERE. The original note claimed
    `wcrimb_2` carries 26 `BGSCR` and 33 `BGPDH`. It does not -- that came
    from counting raw bytes 0x50/0x51, which are not those opcodes. Decoded
    properly through kujata's reader:

        wcrimb_2   BGCLR 2, BGON 4, BGOFF 3      no BGSCR, no BGPDH
        onna_5     BGCLR 2, BGON 4, BGOFF 3      no BGSCR, no BGPDH
        junonl2    BGSCR 36, BGON 12, BGOFF 10, BGCLR 8

    and `BGPDH` (0x2c) is `setBackgroundZDepth`, which cannot move a layer
    vertically at all. So on `wcrimb_2` the header range IS the real range,
    and the reason build 100 changed nothing was the picture band above, not
    this.

    The camera-travel bound is kept anyway, because `junonl2` genuinely is
    script-scrolled and is one of the layers still measuring a gap. It is an
    upper bound -- a parallax layer moves at some fraction <= 1 of the camera
    -- so it over-covers rather than under-covers, which is the safe
    direction. The cost is tiles, and the frame-cap check says that cost is
    zero.

    The header-derived span is unioned in rather than replaced, so a field
    whose layer really does rest off-centre is still covered.
    """
    pos = hdr['bg3_pos_y'] if layer == 3 else hdr['bg4_pos_y']
    spd = hdr['bg3_speed_y'] if layer == 3 else hdr['bg4_speed_y']
    d_lo = min(hdr['cam_top'], hdr['cam_bottom']) + 120
    d_hi = max(hdr['cam_top'], hdr['cam_bottom']) - 120
    if d_lo > d_hi:                       # no vertical travel on this field
        d_lo = d_hi = (hdr['cam_top'] + hdr['cam_bottom']) // 2
    rest_lo = pos / 16.0 + (spd * d_lo) / 256.0
    rest_hi = pos / 16.0 + (spd * d_hi) / 256.0
    # the script bound: up to 1:1 with the camera, about the layer's rest point
    centre = pos / 16.0
    travel = abs(d_hi - d_lo) / 2.0
    return (min(rest_lo, rest_hi, centre - travel),
            max(rest_lo, rest_hi, centre + travel))


# --------------------------------------------------------------------------
# the BACK block
# --------------------------------------------------------------------------
def _layers(sec9, back, tex):
    """
    [(layer, header_count_offset, first_record_offset, n)] for every present
    layer, walked structurally exactly as `field_bg_native._layer_tile_spans`
    does. `header_count_offset` is where that layer's u16 tile count lives,
    which is the only header word this pass rewrites.
    """
    out = []
    o = back + 4                                   # "BACK"
    _w, _h, n1, _d, _b = struct.unpack_from('<HHHHH', sec9, o)
    out.append((1, o + 4, o + 10, n1))
    o += 10 + n1 * TILE_SIZE + 2
    for layer, unused in ((2, 16), (3, 10), (4, 10)):
        if o >= tex:
            break
        flag = sec9[o]
        o += 1
        if flag == 0:
            continue
        if flag != 1:
            raise FillError('layer flag %d at %d' % (flag, o - 1))
        _w, _h, n = struct.unpack_from('<HHH', sec9, o)
        count_at = o + 4
        first = o + 6 + unused + 2
        out.append((layer, count_at, first, n))
        o = first + n * TILE_SIZE + 2
    if o != tex:
        raise FillError('layer walk ended at %d, TEXTURE at %d' % (o, tex))
    return out


def scrolls(hdr, layer):
    """
    Does this layer MOVE ON SCREEN? If not, it must never be filled.

    THE KEYHOLE REGRESSION, AND THE INVARIANT THAT PREVENTS IT.

        bg.y  = bg_pos_y/16 + speed_y * delta_y / 256
        dst_y = (232 - bg.y + tile.y) * MULT

    With `speed_y == 0`, `bg.y` is a CONSTANT, so `dst_y` is a constant too:
    the layer is pinned to the viewport and cannot have a camera-dependent
    gap. There is nothing for a repeat to fix, and repeating it is actively
    wrong -- these are not scrolling backdrops, they are overlays and CUTOUT
    MASKS.

    Build 101 filled them anyway. `onna_5` layer 4 is the Honey Bee Inn
    keyhole mask: 46 of the archive's 96 parallax layers are speed-0 and that
    is one of them. The pass copied its rows from y 0..160 up to y -320..-160,
    which put opaque mask art -- and the keyhole's own hole -- at positions
    the artist never drew them. That is the "black flat regions" and "edge of
    a black square" reported from hardware, and it was mine.

    A speed-0 layer that does not reach the edges of the WIDER frame is a real
    problem, but it is the MARGIN problem (FINDINGS-197), and the fix there is
    to extend the mask's own edge, never to tile it.
    """
    spd = hdr['bg3_speed_y'] if layer == 3 else hdr['bg4_speed_y']
    return spd != 0


def plan_layer(sec9, first, n, hdr, layer):
    """
    [(source_record_offset, new_dst_y)] -- the rows to copy, or [] if the
    layer already covers everything the picture can show.
    """
    if not scrolls(hdr, layer):
        return []
    ys = {}
    for i in range(n):
        off = first + i * TILE_SIZE
        y = struct.unpack_from('<h', sec9, off + T_DSTY)[0]
        ys.setdefault(y, []).append(off)
    if not ys:
        return []
    rows = sorted(ys)
    span = rows[-1] + PTILE - rows[0]
    if span <= 0:
        return []

    bg_lo, bg_hi = bg_y_span(hdr, layer)
    need_top = bg_lo + PICTURE_TOP
    need_bot = bg_hi + PICTURE_BOT
    have_top, have_bot = float(rows[0]), float(rows[-1] + PTILE)

    add = []
    for k in range(1, MAX_EXTRA_ROWS + 1):
        if have_top <= need_top and have_bot >= need_bot:
            break
        moved = False
        if have_top > need_top:
            for y in rows:
                y2 = y - k * span
                if y2 + PTILE <= need_top - PTILE or y2 >= have_top:
                    continue
                add += [(o, y2) for o in ys[y]]
                moved = True
            have_top = min(have_top, rows[0] - k * span)
        if have_bot < need_bot:
            for y in rows:
                y2 = y + k * span
                if y2 >= need_bot + PTILE or y2 + PTILE <= have_bot:
                    continue
                add += [(o, y2) for o in ys[y]]
                moved = True
            have_bot = max(have_bot, rows[-1] + PTILE + k * span)
        if not moved:
            break
    return add


def scrolls_x(hdr, layer):
    """
    Does this layer move HORIZONTALLY? If not, it must never be filled in x.

    `scrolls()`' argument, on the other axis, and it is the same trap. A
    speed-0 layer is pinned to the viewport, so `dst_x` is a constant and it
    cannot have a camera-dependent gap. Build 101 tiled a speed-0 layer
    vertically and put `onna_5`'s keyhole mask -- and its hole -- where the
    artist never drew them; that is the "black flat regions" report and it is
    exactly the class of defect this project has spent this session removing.

    A speed-0 layer that does not reach the edges of the wider frame is a
    real problem and it is the MARGIN problem (FINDINGS-197). The fix there
    is to extend the art's own edge, never to tile it.
    """
    spd = hdr['bg3_speed_x'] if layer == 3 else hdr['bg4_speed_x']
    return spd != 0


def bg_x_span(hdr, layer):
    """
    (lo, hi) of `bg.x` -- where this layer can ever sit relative to the camera.

    `bg_y_span`'s twin, and it keeps that function's two bounds for the same
    reasons: the header's resting arithmetic, unioned with a camera-travel
    bound because `BGSCR` can scroll a layer from script and no static read
    can bound that. A parallax layer moves at some fraction <= 1 of the
    camera, so the travel bound OVER-covers, which is the safe direction --
    it costs tiles, and being short costs a black bar.

    THE HALF-WIDTH HERE IS 160, NOT 213, AND THAT IS DELIBERATE. `ff7nx_ws`
    has already pulled section 8's camera range in so that the stock `+/-160`
    compare produces FFNx's 16:9 bounds (see `clamp_delta`). Using 213 on the
    already-adjusted range would apply the correction twice and UNDER-state
    the camera's travel, which is the one error this function must not make.
    """
    pos = hdr['bg3_pos_x'] if layer == 3 else hdr['bg4_pos_x']
    spd = hdr['bg3_speed_x'] if layer == 3 else hdr['bg4_speed_x']
    d_lo = min(hdr['cam_left'], hdr['cam_right']) + 160
    d_hi = max(hdr['cam_left'], hdr['cam_right']) - 160
    if d_lo > d_hi:                       # no horizontal travel on this field
        d_lo = d_hi = (hdr['cam_left'] + hdr['cam_right']) // 2
    rest_lo = pos / 16.0 + (spd * d_lo) / 256.0
    rest_hi = pos / 16.0 + (spd * d_hi) / 256.0
    centre = pos / 16.0
    travel = abs(d_hi - d_lo) / 2.0
    return (min(rest_lo, rest_hi, centre - travel),
            max(rest_lo, rest_hi, centre + travel))


def plan_layer_x(sec9, first, n, hdr, layer):
    """
    [(source_record_offset, new_dst_x)] -- the COLUMNS to copy.

    `plan_layer`'s twin. FINDINGS-266: `bg3_height` is a placeholder in 55 of
    96 parallax layers and `ff7nx_parallaxfill` was written to close the
    vertical gap that causes. **`bg3_width` is worse and nobody looked**:

        vanilla, 46 fields with a layer 3
          bg3_width  <= 1  (degenerate)     34
          bg3_width  <  the art's width      9      -> 43 of 46 wrong
          bg3_height <= 1                    0
          bg3_height <  the art's height     0

    MEASURED coverage gaps against what a 16:9 view can reach, worst first:
    `crater_1` 399 units each side, `mtcrl_4` 287/159, `trnad_2` 223/223,
    `fship_2` 63/63 -- and `fship_2`'s 63 each side is precisely the black
    bars reported from hardware.

    The header is NOT touched, for `plan_layer`'s reason one axis over:
    `bg3_width` also feeds `do_increase_width` and the shift arithmetic
    (`background.cpp:171`), so correcting it would move the layer as well as
    its repeat. Columns are copied at +/- the layer's own measured span,
    which is the repeat the wrap would have performed if the header were
    right, and it is what FFNx's `do_increase_width` does for the same case.
    """
    if not scrolls_x(hdr, layer):
        return []
    xs = {}
    for i in range(n):
        off = first + i * TILE_SIZE
        x = struct.unpack_from('<h', sec9, off + T_DSTX)[0]
        xs.setdefault(x, []).append(off)
    if not xs:
        return []
    cols = sorted(xs)
    span = cols[-1] + PTILE - cols[0]
    if span <= 0:
        return []

    bg_lo, bg_hi = bg_x_span(hdr, layer)
    # The PICTURE, on the corrected axis -- see `plan_layer_edge_x`. The old
    # `bg_lo - 459 .. bg_hi + 107` read the WRAP window as if it were the
    # picture and asked for eight columns that no camera position can show.
    # This arm is still OFF by default (`fship_1`'s sea does not tile), but a
    # dormant pass with a wrong window is a trap for whoever turns it on.
    need_lo = bg_lo + ORIGIN_X - BG_OFFSET_X - HALF_VIEW_43 - PICTURE_MARGIN_X
    need_hi = bg_hi + ORIGIN_X - BG_OFFSET_X + HALF_VIEW_43 + PICTURE_MARGIN_X
    have_lo, have_hi = float(cols[0]), float(cols[-1] + PTILE)

    add = []
    for k in range(1, MAX_EXTRA_ROWS + 1):
        if have_lo <= need_lo and have_hi >= need_hi:
            break
        moved = False
        if have_lo > need_lo:
            for x in cols:
                x2 = x - k * span
                if x2 + PTILE <= need_lo - PTILE or x2 >= have_lo:
                    continue
                add += [(o, x2) for o in xs[x]]
                moved = True
            have_lo = min(have_lo, cols[0] - k * span)
        if have_hi < need_hi:
            for x in cols:
                x2 = x + k * span
                if x2 >= need_hi + PTILE or x2 + PTILE <= have_hi:
                    continue
                add += [(o, x2) for o in xs[x]]
                moved = True
            have_hi = max(have_hi, cols[-1] + PTILE + k * span)
        if not moved:
            break
    return add


def layer_width(hdr, layer):
    """`layer3_width` / `layer4_width` as the ENGINE computes it.

    The port has no `do_increase_width` -- that is an FFNx addition -- so this
    is the raw header word and nothing else.
    """
    return hdr['bg3_w'] if layer == 3 else hdr['bg4_w']


def bg_x_rest(hdr, layer):
    """`bg_position.x` for a PINNED layer, in tile-x units.

    `set_world_and_background_positions`, with `speed_x == 0` so the camera
    term vanishes and `field_bg_offset->x` at its measured 160:

        bg.x = remainder(pos_x / 16, width) + 320 - 160
    """
    pos = (hdr['bg3_pos_x'] if layer == 3 else hdr['bg4_pos_x']) / 16.0
    w = layer_width(hdr, layer)
    if w:
        pos = math.remainder(pos, w)
    return pos + ORIGIN_X - BG_OFFSET_X


def engine_shift(stored_x, bg_x, width, engine):
    """Where the ENGINE actually draws a tile whose record says `stored_x`.

    `field_layer3_shift_tile_position` / `field_layer4_shift_tile_position`,
    transcribed. It fires at most once -- it is a conditional, not a modulo --
    which is the whole reason a right-hand column can be addressed through it.
    """
    left_off, right_off, half_w = engine
    if stored_x <= bg_x - left_off or stored_x >= bg_x + right_off:
        stored_x += -width if stored_x >= bg_x - half_w else width
    return stored_x


def encode_dst_x(x, bg_x, width):
    """The `dst_x` to STORE so that the engine DRAWS the tile at `x`.

    Returns None when no encoding is provably correct, in which case the
    column is refused rather than guessed at.

    THREE CONFIGURATIONS HAVE TO AGREE, and that is the whole safety argument:

      * `ENGINE_169`      what this build ships today
      * `ENGINE_169_FULL` the same with ff7nx_fieldwide's KNOWN GAP closed
                          (`right_offset` 0 -> 107), so a later build that
                          adds that cave cannot silently move these tiles
      * `ENGINE_43`       the stock words, i.e. widescreen off. Here the tile
                          is NOT required to land on `x` -- it is required to
                          land OUTSIDE the 4:3 picture, so a 4:3 player sees
                          exactly what build 147 gave them.
    """
    if width < PTILE:
        return None                   # a degenerate wrap cannot be addressed
    for stored in (x, x + width, x - width):
        if not -32768 <= stored <= 32767:
            continue
        if engine_shift(stored, bg_x, width, ENGINE_169) != x:
            continue
        if engine_shift(stored, bg_x, width, ENGINE_169_FULL) != x:
            continue
        landed = engine_shift(stored, bg_x, width, ENGINE_43)
        # the 4:3 picture is tile.x in [bg.x - 320, bg.x); a tile whose whole
        # 32-unit body is outside it cannot be seen without the widescreen
        # words applied.
        if not (landed + PTILE <= bg_x - 2 * HALF_VIEW_43 or landed >= bg_x):
            continue
        return stored
    return None


def drawn_map(xs, bg_x, width, engine=None):
    """{drawn tile-x: [source record offsets]} after the engine's wrap.

    THE AUTHORED EXTENT IS NOT THE DRAWN EXTENT, and reading one for the other
    is how `bwhlin2` and `woa_1` look covered when they are not. `bwhlin2`'s
    layer 3 is authored x -288..288 but sits at `bg.x == 16`, so every column
    from 16 rightwards is past `bg.x + right_offset` and the wrap throws it a
    thousand units off screen. Its right-hand margin is empty even though its
    art is 576 units wide.
    """
    engine = engine or ENGINE_169
    out = {}
    for x, offs in xs.items():
        out.setdefault(engine_shift(x, bg_x, width, engine), []).extend(offs)
    return out


def _covered(drawn, lo, hi):
    """Is every unit of [lo, hi) under some 32-unit tile in `drawn`?"""
    at = lo
    for d in sorted(drawn):
        if d > at:
            break
        at = max(at, d + PTILE)
        if at >= hi:
            return True
    return at >= hi


def covers_43_picture(drawn, bg_x):
    """Is this layer a full-frame BACKDROP, or is it an object?

    THE GUARD BUILD 128 DID NOT HAVE, and the reason its smear was a
    regression rather than a fix. Extending the edge of a layer that already
    fills the 4:3 frame continues sky, sea or a mask by 53.5 units. Extending
    `blin66_2`'s 96-unit layer 3 -- a lit window, not a backdrop -- would
    smear that object 165 units across the margin.

    So: what the engine already DRAWS must cover the whole 4:3 picture,
    `tile.x` in [bg.x - 320, bg.x]. Nothing narrower is touched at all.
    """
    return bool(drawn) and _covered(drawn, bg_x - 2 * HALF_VIEW_43, bg_x)


def plan_layer_edge_x(sec9, first, n, hdr, layer):
    """
    [(source_record_offset, stored_dst_x)] -- EXTEND a PINNED layer's own edge.

    THIS IS THE OTHER HALF OF THE HORIZONTAL GAP, AND IT IS NOT A TILING
    PROBLEM. `scrolls_x` refuses a speed-0 layer, and it is right to: build
    101 tiled one and put `onna_5`'s keyhole -- hole and all -- where the
    artist never drew it. But `scrolls_x`'s own docstring says what the
    remaining case needs:

        "A speed-0 layer that does not reach the edges of the WIDER frame is
         a real problem, but it is the MARGIN problem (FINDINGS-197), and the
         fix there is to extend the mask's own edge, never to tile it."

    That is this function. MEASURED over the archive, and the numbers are the
    same story in 46 places:

        fship_2   bg3 speed (0,0)  pos_x 0  art x -160..160
                  bg.x = 160, 16:9 needs -213.5..213.5
                  -> 53.5 units of black on the LEFT and 53.5 on the RIGHT

    which is Patrick's report to the unit, and it is why Cosmos widened
    layers 1 and 2 to exactly +/-224 and no further.

    TWO THINGS BUILD 128 GOT WRONG, BOTH FIXED HERE:

      1. THE WINDOW. It filled to `rest - 427 .. rest + 107`, which is the
         WRAP window read as if it were the picture. On `fship_2` that asked
         for nine columns on the left and none at all on the right -- a
         267-unit smear where 53.5 was needed, and the right-hand bar left
         exactly as it was. `bg_x_rest` and `PICTURE_MARGIN_X` above are the
         picture, derived from `initial_pos.x` rather than from the cull.

      2. THE RIGHT-HAND COLUMNS CANNOT BE PLACED, THEY MUST BE ENCODED. The
         right margin begins at `tile.x == bg.x`, and the wrap conditional
         displaces every tile at or past that point because `right_offset` is
         still 0 in this port. `encode_dst_x` writes those records at
         `x + width` so the wrap brings them back to `x` -- the same journey
         the header's `bg?_width` was always meant to send them on.

    THE COLUMN IS REPEATED, NOT THE LAYER. Only the OUTERMOST column is
    copied, and only outward. For a backdrop whose edge is sky or sea that
    continues it correctly; for a mask it extends the mask, which is what a
    mask wants at the frame edge. Nothing is copied inward, so no interior
    art can be disturbed and the keyhole cannot move.
    """
    if scrolls_x(hdr, layer):
        return []                      # tiling territory, handled above
    width = layer_width(hdr, layer)
    if width < PTILE:
        return []
    xs = {}
    for i in range(n):
        off = first + i * TILE_SIZE
        x = struct.unpack_from('<h', sec9, off + T_DSTX)[0]
        xs.setdefault(x, []).append(off)
    if not xs:
        return []
    bg_x = bg_x_rest(hdr, layer)
    drawn = drawn_map(xs, bg_x, width)
    if not covers_43_picture(drawn, bg_x):
        return []

    # THE PICTURE IN TILE-X, AND IT IS NOT CENTRED ON `bg.x`.
    #   screen(tile.x) = 320 - bg.x + tile.x,  4:3 viewport = screen 0..320
    # so the 4:3 picture is tile.x in [bg.x - 320, bg.x] and 16:9 adds
    # `PICTURE_MARGIN_X` at each end. Writing this as `bg.x -/+ 160` looks
    # symmetric and is wrong by 160 units in both directions.
    pic_lo, pic_hi = bg_x - 2 * HALF_VIEW_43, bg_x
    need_lo = pic_lo - PICTURE_MARGIN_X
    need_hi = pic_hi + PICTURE_MARGIN_X

    # The column grid this layer is authored on. Every parallax tile is 32
    # units and every layer's columns share one residue, so the margin
    # columns land flush against the art rather than half a tile off it.
    grid = min(xs) % PTILE
    lo_i = int(math.floor((need_lo - grid) / PTILE)) - 1
    hi_i = int(math.ceil((need_hi - grid) / PTILE)) + 1
    cands = [i * PTILE + grid for i in range(lo_i, hi_i + 1)]
    cands = [p for p in cands if p + PTILE > need_lo and p < need_hi]

    # ONLY THE MARGIN. A candidate that overlaps the 4:3 picture at all is
    # left alone, so this pass cannot change one 4:3 pixel by construction --
    # not by a threshold, by the geometry of what it is allowed to consider.
    left = sorted((p for p in cands if p + PTILE <= pic_lo), reverse=True)
    right = sorted(p for p in cands if p >= pic_hi)

    add = []
    for which, side in (('L', left), ('R', right)):
        for k, p in enumerate(side[:MAX_EDGE_COLS]):
            if p in drawn:
                continue               # the engine already puts art here
            # PREFER THE ARTIST'S OWN TILE. If a record exists at this
            # coordinate the engine is merely wrapping it out of the frame --
            # re-encoding it restores authored art with no smear at all.
            # `woa_1` and every 352-wide layer is this case: their art already
            # reaches the margin and the wrap is throwing it away.
            #
            # Only where nothing was ever authored does this fall back to
            # extending the nearest DRAWN column outward, which is the margin
            # fix FINDINGS-197 calls for.
            src = xs.get(p)
            if src is None:
                nearest = min(drawn, key=lambda d: (abs(d - p), d))
                src = drawn[nearest]
            stored = encode_dst_x(p, bg_x, width)
            if stored is None:
                break                  # cannot address it; nor anything past
            add += [(o, stored, (which, k)) for o in src]
            drawn[p] = src
    return add


def _vertical_plan(sec9, first, n, hdr, layer):
    """The build-127 pass, unchanged, as (offset, value, word, group) tuples."""
    return [(o, v, T_DSTY, None)
            for o, v in plan_layer(sec9, first, n, hdr, layer)]


def _horizontal_plan(sec9, first, n, hdr, layer):
    """TWO AXES, TWO PLANS, AND THEY REWRITE DIFFERENT WORDS.

    A vertical copy rewrites `dst_y` and a horizontal one `dst_x`, so they are
    carried as (offset, value, field) rather than merged -- writing the wrong
    word would move a tile sideways instead of down and the result is a
    backdrop that tiles into itself at a right angle.

    The fourth element is the GROUP: ('L'|'R', k), the margin column this
    record belongs to, counted outward from the picture. The budget below
    accepts or refuses a whole column at a time and stops at the first one it
    cannot afford, so a short fill is always a narrower black bar and never a
    ragged half-column.
    """
    p = []
    if FILL_X:
        p += [(o, v, T_DSTX, ('X', i))
              for i, (o, v) in enumerate(
                  plan_layer_x(sec9, first, n, hdr, layer))]
    if EDGE_X:
        p += [(o, v, T_DSTX, g)
              for o, v, g in plan_layer_edge_x(sec9, first, n, hdr, layer)]
    return p


def _apply_plan(sec9, hdr, field_name, planner, budgeted, cap_sec9=None):
    """(new_sec9, {layer: n_added}) for ONE axis."""
    back = sec9.find(b'BACK')
    tex = sec9.find(b'TEXTURE')
    if back < 0 or tex < 0 or tex < back:
        raise FillError('no BACK/TEXTURE marker')
    layers = _layers(sec9, back, tex)

    plans = {}
    for layer, _count_at, first, n in layers:
        if layer not in (3, 4) or n == 0:
            continue
        if (field_name, layer) in NON_TILEABLE_OVERLAYS:
            continue
        p = planner(sec9, first, n, hdr, layer)
        if p:
            plans[layer] = p
    if not plans:
        return sec9, {}

    # THE PER-PAGE FRAME CAP, AND THE HORIZONTAL FILL IS THE FIRST THING IN
    # THIS PASS BIG ENOUGH TO HIT IT. FINDINGS-110, FINDINGS-122.
    #
    # `field_bg_pagecap.MAX_TILES_PER_PAGE` is 256, raised per field to
    # `max(256, vanilla's worst page)` because vanilla itself ships pages over
    # the limit and they demonstrably render. MEASURED before this guard:
    # `junonl2`'s worst page went 308 -> 703, which is not a grandfathered
    # excess, it is three times one.
    #
    # So additions are BUDGETED per page. A tile that would push its page past
    # the cap is dropped -- a slightly short fill is a thinner black bar, and
    # overrunning the cap is a page the engine will not draw at all.
    #
    # ONLY THE HORIZONTAL PASS IS BUDGETED. The vertical fill has been on
    # hardware since build 100 and must come out byte-for-byte as build 127
    # shipped it; budgeting it too would silently drop rows that build 127
    # kept, in a pass that is meant to be untouched.
    if budgeted:
        def _slot_counts(s):
            c = {}
            b, t = s.find(b'BACK'), s.find(b'TEXTURE')
            for _l, _ca, f, m in _layers(s, b, t):
                for i in range(m):
                    k = s[f + i * TILE_SIZE + 32]
                    c[k] = c.get(k, 0) + 1
            return c

        # THE CAP IS VANILLA'S, NOT THIS SECTION'S. `field_bg_pagecap`'s rule
        # is `ours <= max(256, VANILLA's worst page)`; reading the ceiling off
        # the section the vertical fill has just grown lets that fill raise
        # its own limit on some fields and exhaust it on others.
        vc = _slot_counts(cap_sec9 if cap_sec9 is not None else sec9)
        counts = _slot_counts(sec9)
        cap = max(256, max(vc.values()) if vc else 256)
        budget = {slot: cap - k for slot, k in counts.items()}
        kept = {}
        for layer, p in plans.items():
            groups, order = {}, []
            for item in p:
                key = item[3]
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(item)
            out_p, stopped = [], set()
            for key in order:
                side = key[0] if isinstance(key, tuple) else key
                if side in stopped:
                    continue
                need = {}
                for off, _v, _w, _g in groups[key]:
                    slot = sec9[off + 32]
                    need[slot] = need.get(slot, 0) + 1
                if any(budget.get(s, cap) < c for s, c in need.items()):
                    stopped.add(side)          # and nothing further out
                    continue
                for s, c in need.items():
                    budget[s] = budget.get(s, cap) - c
                out_p += groups[key]
            if out_p:
                kept[layer] = out_p
        plans = kept
        if not plans:
            return sec9, {}

    total = sum(len(p) for p in plans.values())
    if total > MAX_EXTRA_TILES:
        raise FillError('%d extra tiles is out of scope for this pass' % total)

    # Rebuild back to front so the offsets computed above stay valid.
    buf = bytearray(sec9)
    added = {}
    for layer, count_at, first, n in sorted(layers, reverse=True):
        p = plans.get(layer)
        if not p:
            continue
        blob = bytearray()
        for off, val, word, _group in p:
            rec = bytearray(sec9[off:off + TILE_SIZE])
            struct.pack_into('<h', rec, word, int(val))
            blob += rec
        end = first + n * TILE_SIZE
        buf[end:end] = blob
        struct.pack_into('<H', buf, count_at, n + len(p))
        added[layer] = len(p)
    return bytes(buf), added


def apply_to_section9(sec9, sec7, field_name=None):
    """
    (new_sec9, {layer: tiles_added}) -- or (sec9, {}) if nothing was needed.

    Records are copied byte for byte with only ONE destination word rewritten,
    so they keep the page, uv, palette, blend and animation group of the row or
    column they repeat.

    THE TWO AXES RUN IN SEQUENCE, NOT TOGETHER, AND THE ORDER MATTERS.
    `wcrimb_2`'s layer 3 is pinned horizontally and scrolls vertically, so both
    passes fire on it. Planning them both against the ORIGINAL section -- which
    is what build 128 did -- makes the new edge column exactly as tall as the
    art was BEFORE the vertical fill extended it, and the margin then shows the
    black band the vertical fill had just removed. Running vertical first and
    planning horizontal against the result copies the filled column entire.
    """
    if sec9.find(b'BACK') < 0 or sec9.find(b'TEXTURE') < 0:
        raise FillError('no BACK/TEXTURE marker')
    hdr = trigger_header(sec7)
    vanilla = sec9
    added = {}
    sec9, a = _apply_plan(sec9, hdr, field_name, _vertical_plan,
                          budgeted=False)
    for k, v in a.items():
        added[k] = added.get(k, 0) + v
    if FILL_X or EDGE_X:
        sec9, a = _apply_plan(sec9, hdr, field_name, _horizontal_plan,
                              budgeted=True, cap_sec9=vanilla)
        for k, v in a.items():
            added[k] = added.get(k, 0) + v
    return sec9, added


# --------------------------------------------------------------------------
# the archive pass
# --------------------------------------------------------------------------
def apply_to_field(raw, split, join, field_name=None):
    parts = split(raw)
    if len(parts) < 9:
        return raw, {}
    new9, added = apply_to_section9(parts[SECTION9], parts[SECTION_TRIGGERS],
                                    field_name=field_name)
    if not added:
        return raw, {}
    parts = list(parts)
    parts[SECTION9] = new9
    return join(parts), added


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    """Fill every parallax layer that cannot reach the frame. Stats for the log."""
    import lgp
    if disabled():
        log('  parallax fill: OFF (%s=1)' % OFF_ENV)
        return {'fields': 0, 'tiles': 0, 'layers': 0, 'refused': []}
    encode = encode or archive.encode_field
    protected = ', '.join('%s L%d' % pair for pair in
                          sorted(NON_TILEABLE_OVERLAYS))
    if protected:
        log('  parallax fill: protected non-tileable overlay(s): %s'
            % protected)
    stats = {'fields': 0, 'tiles': 0, 'layers': 0, 'refused': [], 'worst': []}
    for name in archive.names():
        entry = archive.index[name]
        try:
            raw = (lgp.lzs_decompress(payloads[name][4:]) if name in payloads
                   else archive.decompressed(entry))
        except Exception:                                      # noqa: BLE001
            continue
        try:
            if not archive.is_field(entry):
                continue
            new_raw, added = apply_to_field(raw, lgp.split_sections,
                                            lgp.join_sections,
                                            field_name=name)
        except FillError as exc:
            stats['refused'].append((name, str(exc)))
            continue
        except Exception as exc:                               # noqa: BLE001
            stats['refused'].append((name, str(exc)))
            continue
        if not added:
            continue
        stats['fields'] += 1
        stats['layers'] += len(added)
        n = sum(added.values())
        stats['tiles'] += n
        stats['worst'].append((n, name))
        payloads[name] = encode(new_raw)
    stats['worst'].sort(reverse=True)
    return stats


def summarise(stats):
    if not stats['fields']:
        return ('  parallax fill: no layer needed extra rows'
                if not stats['refused'] else
                '  parallax fill: nothing filled, %d refused'
                % len(stats['refused']))
    worst = ', '.join('%s +%d' % (n, c) for c, n in stats['worst'][:4])
    return ('  parallax fill: %d tile(s) added to %d parallax layer(s) in %d '
            'field(s) -- layers 3/4 do not cull vertically, so what puts a '
            'tile on screen is the WRAP, and the wrap period is the trigger '
            'header\'s bg3/bg4_height, which reads 1024 in 55 of the 96 '
            'parallax layers and matches the art in only 39. Where it is '
            'wrong the layer runs out at the top of the picture instead of '
            'repeating: measured 39..89 units of black on wcrimb_2, '
            'identical in vanilla and in builds 98 and 99, because Cosmos '
            'widened these layers horizontally and never vertically. Rows '
            'are copied at +/- the layer\'s own measured span -- the repeat '
            'the wrap would have done if the header were right, and what '
            'FFNx\'s do_increase_height does for the same case. The header '
            'is NOT touched: bg3_height also reduces the layer\'s scroll '
            'position through remainder(), so correcting it would move the '
            'layer as well as its repeat. WATCH FOR: the backdrop now '
            'genuinely tiles, so art that does not join to itself shows a '
            'seam where it used to show black. Biggest: %s. Off with %s=1.'
            ' -- HORIZONTAL MARGIN, BUILD 148, AND BUILD 128 WAS WRONG BY 160 '
            'UNITS: it read the engine\'s WRAP window as if it were the '
            'picture and asked fship_2 for nine extra columns on the LEFT and '
            'none at all on the RIGHT. FFNx background.cpp gives both numbers '
            'exactly: initial_pos.x = (320 - bg.x) * mult, so a tile is on '
            'screen at 320 - bg.x + tile.x, the 4:3 viewport is 0..320 of '
            'that, and wide_viewport_width 854 against 640 adds 53.5 UNITS ON '
            'EACH SIDE. bg.x for a pinned layer is remainder(pos_x/16, width) '
            '+ 160, which the archive proves: 34 pinned layers are authored '
            'at exactly x -160..160 with pos_x 0, and no black bars in 4:3 '
            'forces bg.x = 160 for them. So the margin is 53.5 units per '
            'side, both sides -- which is why COSMOS widened layers 1 and 2 '
            'to exactly +/-224 and is fship_2\'s two bars to the unit. THE '
            'RIGHT-HAND COLUMNS ARE ENCODED, NOT PLACED: this port never '
            'patched right_offset (ff7nx_fieldwide KNOWN GAP), so the wrap '
            'displaces every tile at or past bg.x, and a right margin column '
            'is written at x + bg?_width for the wrap to bring back to x -- '
            'no code patch, no cave, the header\'s own period doing its job. '
            'Where a record already exists at that coordinate it is that '
            'record that is re-encoded, so no smear at all; only where the '
            'artist drew nothing is the nearest DRAWN column extended '
            'outward, which is the margin fix FINDINGS-197 calls for. SCOPED: '
            'pinned layers only (a scrolling layer is FILL_X, still off -- '
            'fship_1\'s sea does not tile), and only where what the engine '
            'ALREADY DRAWS covers the whole 4:3 picture, so blin66_2\'s '
            '96-unit lit window is not smeared 165 units across the margin. '
            'A candidate column that overlaps 4:3 at all is never considered, '
            'so the 4:3 picture cannot change by construction; GATED '
            '(_kpx.py) over all 96 parallax layers -- 29 margins closed, zero '
            '4:3 tiles gained or lost, and identical whether right_offset is '
            '0 or 107. Additions are BUDGETED against the per-page frame cap '
            '(max(256, VANILLA\'s worst page)) a WHOLE COLUMN at a time and '
            'stop at the first one that does not fit, so a short fill is a '
            'narrower bar and never a ragged half-column: MEASURED, zero '
            'fields had their worst per-page tile count rise. Off with %s=1 '
            '(tiling, already off) or %s=1 (pinned margin, restores build '
            '147).'
            % (stats['tiles'], stats['layers'], stats['fields'], worst,
               OFF_ENV, 'SEVENTH_NX_PARALLAX_FILL_X',
               'SEVENTH_NX_NO_PARALLAX_EDGE_X'))
