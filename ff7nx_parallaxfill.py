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

# THE HORIZONTAL PICTURE, AND IT IS THE PATCHED CULL WINDOW.
#
# `field_layer3_pick_tiles` drops a tile unless
#     bg.x - left_offset  <  tile.x  <  bg.x + right_offset
# and `ff7nx_fieldwide.parallax_spec()` already widens those from the stock
# 352 / 0 to 459 / 107 -- log 127 records all five words applied. So the
# window the ENGINE will accept is [-459, +107] about `bg.x`, and that is the
# bound this pass fills to.
#
# It is deliberately the CULL window rather than a measured picture, which
# over-covers: the cost is tiles, and being one tile short is a black bar.
PICTURE_LEFT = -459
PICTURE_RIGHT = 107
# What can actually be SEEN, as opposed to what the cull will accept. The
# tiling fill may over-cover cheaply (it repeats art that exists); the edge
# extension may not, because it copies a whole column per step.
PICTURE_SEEN_LEFT = -427

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
# DEFAULT OFF, same build, same reason. Extending a pinned layer's outermost
# column smears that column across the margin, which is a streak wherever the
# edge is not flat colour.
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
    need_lo = bg_lo + PICTURE_LEFT
    need_hi = bg_hi + PICTURE_RIGHT
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


def plan_layer_edge_x(sec9, first, n, hdr, layer):
    """
    [(source_record_offset, new_dst_x)] -- EXTEND a PINNED layer's own edge.

    THIS IS THE OTHER HALF OF THE HORIZONTAL GAP, AND IT IS NOT A TILING
    PROBLEM. `scrolls_x` refuses a speed-0 layer, and it is right to: build
    101 tiled one and put `onna_5`'s keyhole -- hole and all -- where the
    artist never drew it. But `scrolls_x`'s own docstring says what the
    remaining case needs:

        "A speed-0 layer that does not reach the edges of the WIDER frame is
         a real problem, but it is the MARGIN problem (FINDINGS-197), and the
         fix there is to extend the mask's own edge, never to tile it."

    That is this function. MEASURED, and it is exactly Patrick's report:

        fship_2   bg3 speed (0, 0)   art x -160..160   16:9 needs -223..223
                  -> 63 units of black on the LEFT and 63 on the RIGHT

    A pinned layer's `dst_x` is a constant, so the gap is the same at every
    camera position -- a fixed black bar down each side, which is precisely
    what a widened frame does to art drawn for a 320-unit one.

    THE COLUMN IS REPEATED, NOT THE LAYER. Only the OUTERMOST column is
    copied, and only outward, one tile width at a time. For a backdrop whose
    edge is sky or sea that continues it correctly; for a mask it extends the
    mask, which is what a mask wants at the frame edge. Nothing is copied
    inward, so no interior art can be disturbed and the keyhole cannot move.
    """
    if scrolls_x(hdr, layer):
        return []                      # tiling territory, handled above
    xs = {}
    for i in range(n):
        off = first + i * TILE_SIZE
        x = struct.unpack_from('<h', sec9, off + T_DSTX)[0]
        xs.setdefault(x, []).append(off)
    if not xs:
        return []
    cols = sorted(xs)
    lo, hi = float(cols[0]), float(cols[-1] + PTILE)

    # A PINNED layer sits at a fixed `bg.x`, so the window is its resting
    # position -- no camera travel term, which is the whole point of pinned.
    rest = (hdr['bg3_pos_x'] if layer == 3 else hdr['bg4_pos_x']) / 16.0
    # THE VISIBLE PICTURE, NOT THE CULL WINDOW. The cull accepts
    # [-459, +107] about `bg.x` but only [-427, +107] can ever be SEEN
    # (`initial_pos.x = (320 - bg.x) * mult` plus the 107 units widescreen
    # adds on the left). Filling to the cull would copy ~4 extra columns per
    # side that no camera position can show, and every one of them is a tile
    # record against the per-page frame cap. FINDINGS-110.
    need_lo = rest + PICTURE_SEEN_LEFT
    need_hi = rest + PICTURE_RIGHT

    add = []
    for k in range(1, MAX_EXTRA_ROWS + 1):
        if lo <= need_lo and hi >= need_hi:
            break
        moved = False
        if lo > need_lo:
            x2 = cols[0] - k * PTILE
            add += [(o, x2) for o in xs[cols[0]]]
            lo = float(x2)
            moved = True
        if hi < need_hi:
            x2 = cols[-1] + k * PTILE
            add += [(o, x2) for o in xs[cols[-1]]]
            hi = float(x2 + PTILE)
            moved = True
        if not moved:
            break
    return add


def apply_to_section9(sec9, sec7, field_name=None):
    """
    (new_sec9, {layer: tiles_added}) -- or (sec9, {}) if nothing was needed.

    Records are copied byte for byte with only `dst_y` rewritten, so they keep
    the page, uv, palette and blend of the row they repeat.
    """
    back = sec9.find(b'BACK')
    tex = sec9.find(b'TEXTURE')
    if back < 0 or tex < 0 or tex < back:
        raise FillError('no BACK/TEXTURE marker')
    hdr = trigger_header(sec7)
    layers = _layers(sec9, back, tex)

    # TWO AXES, TWO PLANS, AND THE RECORDS THEY REWRITE ARE DIFFERENT WORDS.
    # A vertical copy rewrites `dst_y` and a horizontal one `dst_x`, so they
    # are carried as (offset, value, field) rather than merged -- writing the
    # wrong word would move a tile sideways instead of down and the result is
    # a backdrop that tiles into itself at a right angle.
    plans = {}
    for layer, _count_at, first, n in layers:
        if layer not in (3, 4) or n == 0:
            continue
        if (field_name, layer) in NON_TILEABLE_OVERLAYS:
            continue
        p = [(o, v, T_DSTY) for o, v in plan_layer(sec9, first, n, hdr, layer)]
        if FILL_X:
            p += [(o, v, T_DSTX)
                  for o, v in plan_layer_x(sec9, first, n, hdr, layer)]
        if EDGE_X:
            p += [(o, v, T_DSTX)
                  for o, v in plan_layer_edge_x(sec9, first, n, hdr, layer)]
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
    # the cap is dropped, nearest-to-the-picture first -- a slightly short
    # fill is a thinner black bar, and overrunning the cap is a page the
    # engine will not draw at all.
    if not (FILL_X or EDGE_X):
        # No horizontal work -> no budget pressure, and the vertical fill must
        # come out byte-for-byte as build 127 shipped it. Applying the budget
        # unconditionally would let it drop VERTICAL tiles that build 127 kept,
        # which is a silent behaviour change in a pass that is meant to be
        # untouched.
        counts = None
    else:
        counts = {}
    for _layer, _ca, first, n in (layers if counts is not None else ()):
        for i in range(n):
            off = first + i * TILE_SIZE
            counts[sec9[off + 32]] = counts.get(sec9[off + 32], 0) + 1
    if counts is None:
        kept = plans
    else:
      cap = max(256, max(counts.values()) if counts else 256)
      budget = {slot: cap - k for slot, k in counts.items()}
      kept, dropped = {}, 0
      for layer, p in plans.items():
        out_p = []
        for off, val, word in p:
            slot = sec9[off + 32]
            if budget.get(slot, cap) <= 0:
                dropped += 1
                continue
            budget[slot] = budget.get(slot, cap) - 1
            out_p.append((off, val, word))
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
        for off, val, word in p:
            rec = bytearray(sec9[off:off + TILE_SIZE])
            struct.pack_into('<h', rec, word, int(val))
            blob += rec
        end = first + n * TILE_SIZE
        buf[end:end] = blob
        struct.pack_into('<H', buf, count_at, n + len(p))
        added[layer] = len(p)
    return bytes(buf), added


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
            ' -- HORIZONTAL, BUILD 128: bg3_WIDTH is worse than the height '
            'ever was. MEASURED over the 46 fields with a layer 3: it is <= 1 '
            'in 34 of them and smaller than the art in 9 more, so 43 of 46 '
            'are wrong, while bg3_height is degenerate in NONE. Two '
            'mechanisms, because a layer that SCROLLS and a layer that is '
            'PINNED need opposite treatment: a scrolling layer has its '
            'columns repeated at +/- its own span, exactly as the rows are '
            'above; a PINNED layer (bg speed_x == 0) is never tiled -- build '
            '101 tiled one and put onna_5\'s keyhole where the artist never '
            'drew it -- and instead has its OUTERMOST COLUMN extended '
            'outward, which is the margin fix FINDINGS-197 calls for. '
            'fship_2 is pinned and 267 units short on the left; its bars are '
            'this. Additions are BUDGETED against the per-page frame cap '
            '(max(256, vanilla\'s worst page)) and dropped rather than '
            'allowed to overrun it -- junonl2 would otherwise have gone 308 '
            '-> 703 tiles on one page. Off with %s=1 (tiling) or %s=1 '
            '(pinned edge).'
            % (stats['tiles'], stats['layers'], stats['fields'], worst,
               OFF_ENV, 'SEVENTH_NX_NO_PARALLAX_FILL_X',
               'SEVENTH_NX_NO_PARALLAX_EDGE_X'))
