#!/usr/bin/env python3
"""
ff7nx_blackcell.py -- the black squares layer 1 draws out of empty page cells.

    python3 ff7nx_blackcell.py <flevel.lgp>            report, writes nothing
    python3 ff7nx_blackcell.py <flevel.lgp> trnad_3

THE BUG
=======
Layer 1 is the backmost background layer and the engine does NOT colour-key
it -- FFNx sets `color_key` only for `type == 2` (`ff7/field/field.cpp:56`).
So on layer 1 index 0 is DRAWN, as black. The same empty cell sampled by a
layer 2/3/4 tile is transparent and harmless.

A layer-1 tile pointing at an all-zero cell is therefore a solid black square
on screen. MEASURED on build 79/80, 26 fields and 1620 such tiles:

    one tile      11 fields   ancnt3 blin70_2 bugin1a colne_1 junair2 kuro_5
                              las4_2 ship_2 trnad_1 trnad_3 uttmpin4
    whole margin  14 fields   blin67_4 gaiin_6 cosmo fr_e junone22 kuro_11
                              rckt3 rckt32 trnad_52 las0_2 gaia_1 gaia_31
                              jtemplc qc

`trnad_3` is the one reported from hardware -- one black square along the top
of the Whirlwind Maze ridge, at dst (-216, -200), page 0 cell (0,0).

WHY THE MARGIN-ART PASS CANNOT DO THIS
======================================
`ff7nx_marginart.fillable_cells(..., 'margin')` returns ZERO cells for
`trnad_3`, so that pass never even considers it. A cell only enters its scope
if it is "sampled ONLY by margin tiles on layer 1 AND flat in the page".
Page 0 cell (0,0) is flat, but it is SHARED -- three tiles sample it, at three
different palettes:

    layer 1  dst(-216,-200)  palette 5     <- the black square
    layer 4  dst(-192,-128)  palette 7
    layer 4  dst( -16, -16)  palette 8

A depth-1 page is ONE array of indices and the palette only recolours it, so
art quantised for palette 5 would be read through palettes 7 and 8 by the two
layer-4 tiles. Refusing is correct. The cost is the black square.

Cosmos ships the real art as an external DDS -- `trnad_3_00_00.dds`, 1024x1024,
fully opaque, cell (0,0) = RGB(116, 211, 212), the pale cyan sky. On FFNx that
DDS replaces the page and it renders. This port has no DDS path.

WHAT THIS DOES
==============
Give the layer-1 tile its OWN copy of the cell:

  1. find a free cell on the same page -- one no tile samples and that is
     entirely empty (`trnad_3` page 0 has 96 of them);
  2. quantise the mod's DDS art for that cell against the palette THE LAYER-1
     TILE IS DRAWN WITH, and write it into the free cell;
  3. repoint ONLY that tile's u/v.

The original cell is left byte-for-byte alone, so the layer-4 tiles sharing it
are untouched. This is "Step 2 of the plan -- copying the cell so each palette
can have its own" that `fillable_cells` already describes.

Where the cell is sampled by layer-1 tiles at ONE palette and nothing else, it
is filled in place and no tile moves.

THE SAFETY PROPERTIES
=====================
* It only ever writes into a cell that is ENTIRELY EMPTY. It cannot overwrite
  art, so no cell that renders correctly today can change.
* It only ever repoints LAYER-1 tiles. Nothing on layer 2/3/4 moves.
* It re-counts the black cells after editing and REFUSES the whole field if
  the number did not go down, so a bug here costs the fix, not the field.
* No DDS art, no free cell, an unreadable page, a palette the field does not
  have -- every one of them is a skip with a reason, never a guess.

SEVENTH_NX_NO_BLACKCELL=1 turns the pass off.

DEPTH-2 PAGES ARE OUT OF SCOPE, DELIBERATELY
============================================
The 14 whole-margin fields draw from slots 26/27/28, which are depth-2
truecolor pages. Those need a 565 write rather than a palette quantisation,
and -- more to the point -- a margin that is empty across 120 tiles is a
different failure from one corner cell, and should be diagnosed before it is
patched. They are counted and named in the log, and left alone.
"""
from __future__ import annotations

import os
import struct
import sys
from collections import defaultdict

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC                                       # noqa: E402
import field_bg_native as FN                                   # noqa: E402
import field_bg_pagecap as PC                                  # noqa: E402
import field_bg_repack as RP                                   # noqa: E402
import ff7nx_marginart as MA                                   # noqa: E402
import ff7nx_marginblack as MB                                 # noqa: E402

SECTION9 = 8
SECTION_PALETTE = 3
TILE = 16
# Layers 3 and 4 set a tile's width to 32 (offsets 18/20). FINDINGS-189.
BIG_TILE = 32


def disabled():
    """SEVENTH_NX_NO_BLACKCELL=1 turns the pass off without a code edit."""
    return os.environ.get('SEVENTH_NX_NO_BLACKCELL') == '1'


# ------------------------------------------------------------------ reading
def _grid_step(page):
    grid = 8 if page.size_flag else 16
    return grid, 256 // grid


def _cell_is_empty(arr, page, sx, sy, grid):
    ys, xs = PC._cell_slice(page, sx, sy, grid)
    block = arr[ys, xs]
    if page.depth == 2:
        return not bool((block != FN.EMPTY).any())
    return not bool(block.any())


def survey_tiles(sec9, surv):
    """
    (tiles, by_cell) for one field.

    `tiles` is a list of dicts -- offset, layer, slot, source cell, palette,
    destination -- and `by_cell` maps (slot, sx, sy) to the indices into it.
    Source cells are in 256-space, the same units `_uv_encode` takes.
    """
    pages = {p.slot: p for p in surv['pages']}
    tiles, by_cell = [], defaultdict(list)
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        for off in offs:
            slot = sec9[off + RP.T_TEXID]
            page = pages.get(slot)
            if page is None:
                continue
            grid, step = _grid_step(page)
            u, v = struct.unpack_from('<II', sec9, off + PC.T_SRC_UV)
            sx = int(round(u / PC.UV_SCALE * grid)) * step
            sy = int(round(v / PC.UV_SCALE * grid)) * step
            dx, dy = struct.unpack_from('<hh', sec9, off + DC.TILE_DST_X)
            # `n` IS THE TILE'S OWN WIDTH, not 16. Layers 3 and 4 set offsets
            # 18/20 to 32 and five separate passes have assumed otherwise --
            # FINDINGS-189. Nothing in this module needed it until the overlay
            # margin did, and a 32-unit tile tested as 16 straddles the 4:3
            # edge invisibly.
            _w, _h = struct.unpack_from('<HH', sec9, off + 18)
            _n = max(_w, _h) if layer > 1 else TILE
            tiles.append({'off': off, 'layer': layer, 'slot': slot,
                          'sx': sx, 'sy': sy,
                          'n': _n if _n in (TILE, BIG_TILE) else TILE,
                          'pal': sec9[off + RP.T_PALETTE],
                          'fx': bool(sec9[off + RP.T_FX_PAGE]),
                          'dst': (dx, dy)})
            by_cell[(slot, sx, sy)].append(len(tiles) - 1)
    return tiles, by_cell


# EMPTY IS NOT THE QUESTION. BLACK IS. FINDINGS-189 G.
#
# This pass asked "is the cell EMPTY", because an empty cell on layer 1 draws
# entry 0, which is black 93% of the time. That is a proxy, and it misses the
# cell that is uniformly some OTHER index whose palette entry is also black.
# The player cannot tell those apart; only the file can.
#
# MEASURED on `woa_3` (Whirlwind Maze, the ridge with the wind that pushes you
# back) -- the black square reported from hardware at the top-left:
#
#   L1 dst(-160,-120)  slot 0  palette 0  cell (0,0)   every texel index 251
#   palette 0 entry 251 = (0, 0, 0)                    in VANILLA too
#   Cosmos's DDS at that cell: 100% opaque, mean RGB (146, 251, 255)
#
# Square put a flat black filler in the corner because the 4:3 frame never
# showed it. The 16:9 frame does. Cosmos paints the sky there and FFNx renders
# it; this port drew index 251.
#
# `ff7nx_marginart` cannot take it either: the cell is SHARED by a layer-3 tile
# at palette 5, and a depth-1 page is one index array, so art quantised for
# palette 0 would be read back through palette 5. `fillable_cells` refuses it,
# correctly. Giving the layer-1 tile its own copy of the cell -- which is
# exactly what this module exists to do -- is the only route.
#
# THE GATE IS WHAT MAKES THIS SAFE, and it is measured, not asserted. Over 70
# fields:
#
#   layer-1 tiles drawing a fully BLACK cell         16,463 in 27 fields
#     of those, the cell is entirely index 0 (old)        0
#     NON-zero indices AND the mod ships bright art       6
#
# 16,463 -> 6. Every dark cave, night scene and silhouette in the game draws a
# black cell on purpose and the mod agrees with it; the six are the ones where
# Cosmos explicitly paints something bright and opaque over Square's filler.
# SEVENTH_NX_NO_BLACKART=1 restores the EMPTY-only selection, i.e. build 90's
# black square in the corner of woa_3.
BLACK_ART = os.environ.get('SEVENTH_NX_NO_BLACKART') != '1'
BLACK_ART_MIN_LUMA = 25     # the mod's art must be this bright to count
BLACK_ART_MIN_COVER = 0.9   # and this opaque

# ONE CELL, ONE DESTINATION. THE BUILD-91 REGRESSION AND ITS GATE.
# FINDINGS-195.
#
# The art this pass writes is addressed by PAGE CELL -- Cosmos upscales the
# PAGE, not the field, so `art(field, slot, pal)` yields exactly one block for
# `(sx, sy)`. A tile's DESTINATION does not enter the lookup and cannot: there
# is nothing to look it up in.
#
# That is sound while a cell is drawn in one place. Square's flat FILLER cells
# are not: one 16x16 cell of a single index is named by dozens of layer-1
# tiles scattered across the field, because a flat colour is the same
# everywhere and reusing it is free. Give that cell Cosmos's art and every one
# of those destinations draws the same block -- right for at most one of them,
# wrong for the rest, and visible as a repeated square of unrelated picture.
#
# MEASURED on the shipped build-93 archive (`_bcaudit.py`), every layer-1 tile
# whose byte and uv name different cells, i.e. every repoint this pass made:
#
#     private copies                       290   in 28 field(s)
#       drawn at ONE destination           280   correct, and kept
#       drawn at MANY destinations          10   101 tile(s)  <- the defect
#
#     subin_3 36, md0 19/6/4/2, fship_3 16/8, cos_btm 4, blin60_1 4, smkin_1 2
#
# ALL TEN are `was_black` cells -- uniform, NON-empty filler, means 6, 123,
# 103, 253, 7, 26, 171, 24, 221, 5. The ORIGINAL empty-cell population
# contributes ZERO multi-destination copies, which is why builds 88-90 show
# `0 given a private copy` at the second call site and builds 91-93 show 204.
# So this gate is scoped to the WIDENED population and cannot change anything
# that shipped before build 91.
#
# WHY NOT ONE COPY PER DESTINATION INSTEAD. Because every copy would be filled
# from the same single block. Per-destination copies would spend N free cells
# to draw the identical wrong picture N times.
#
# `woa_3`, the case this widening was written for, is untouched: its cell has
# exactly one layer-1 tile at dst(-160,-120).
BLACK_ART_ONE_DST = os.environ.get('SEVENTH_NX_BLACKART_ANY_DST') != '1'


def _cell_is_black(arr, page, sx, sy, grid, prgb):
    """Every texel of this cell renders below the visible floor."""
    ys, xs = PC._cell_slice(page, sx, sy, grid)
    block = arr[ys, xs]
    if page.depth == 2:
        v = block.astype(np.uint32)
        lum = np.maximum(np.maximum(((v >> 11) & 31) << 3,
                                    ((v >> 5) & 63) << 2), (v & 31) << 3)
        return not bool((lum > 10).any())
    if prgb is None:
        return False
    return not bool((prgb[block].max(-1) > 10).any())


def black_cells(sec9, surv, prgbs=None):
    """
    {(slot, sx, sy): [tile, ...]} -- cells a LAYER-1 tile draws as BLACK.

    Empty cells qualify (index 0 is drawn on layer 1 and is almost always
    black). So do cells that are uniformly some other index whose palette
    entry is black -- see the note above. Cells sampled only by layer 2+ are
    excluded: there index 0 is the transparency key and draws nothing.

    `prgbs` is the field's palettes; without them only the empty test runs, so
    every existing caller keeps its exact behaviour.
    """
    pages = {p.slot: p for p in surv['pages']}
    tiles, by_cell = survey_tiles(sec9, surv)
    arrays = {}
    out = {}
    for key, idxs in by_cell.items():
        slot, sx, sy = key
        l1 = [tiles[i] for i in idxs if tiles[i]['layer'] == 1]
        if not l1:
            continue
        page = pages[slot]
        arr = arrays.get(slot)
        if arr is None:
            try:
                arr = arrays[slot] = PC._page_array(page)
            except Exception:                                  # noqa: BLE001
                continue
        grid, _ = _grid_step(page)
        if _cell_is_empty(arr, page, sx, sy, grid):
            out[key] = [tiles[i] for i in idxs]
            continue
        if prgbs is None or not BLACK_ART:
            continue
        # Black through the palette the LAYER-1 tile is actually drawn with.
        # A cell that is black through one palette and not another is not a
        # black square for every tile that names it, so all of them must agree.
        # ONLY ON THE BORDER THE 4:3 FRAME CROPPED. FINDINGS-173 IS RIGHT
        # ABOUT THE INTERIOR AND THIS MUST NOT ARGUE WITH IT.
        #
        # `ff7nx_marginart` keeps a vanilla-black pixel black inside the 4:3
        # picture, because "the original cut it to black on purpose; the
        # upscale has no standing to fill it in", and because Cosmos's DDS has
        # BLEED off its own wider canvas there. That reasoning holds here too.
        #
        # MEASURED over 90 fields, the newly-selected tiles by destination:
        #
        #     inside the 4:3 box   10,467   dominated by blackbg1..6 at 640
        #                                   each -- fields whose whole point
        #                                   is to be black
        #     on the border / outside 7,379
        #
        # What Square left black is the RING the 4:3 frame never showed --
        # `woa_3`'s tile is at dst(-160,-120), the top-left CORNER of the
        # 320x240 box, cropped by every CRT this game shipped on. A 16:9 frame
        # shows it. The interior is not this defect and is not touched.
        _edge = any(t['dst'][0] <= -160 + TILE or t['dst'][0] >= 160 - TILE
                    or t['dst'][1] <= -120 + TILE or t['dst'][1] >= 120 - TILE
                    for t in l1)
        if not _edge:
            continue
        # ONE CELL, ONE DESTINATION -- see BLACK_ART_ONE_DST above. Note that
        # the `_edge` test right above is an `any`, so a single border tile
        # admits every interior tile that happens to share its filler cell;
        # this is the test that makes that safe rather than a way of avoiding
        # it, because the ambiguity is in the ART LOOKUP and not in the edge.
        if BLACK_ART_ONE_DST and len({t['dst'] for t in l1}) > 1:
            black_cells.ambiguous_cells = \
                getattr(black_cells, 'ambiguous_cells', 0) + 1
            black_cells.ambiguous_tiles = \
                getattr(black_cells, 'ambiguous_tiles', 0) + len(l1)
            continue
        pals = {t['pal'] for t in l1}
        if any(p >= len(prgbs) for p in pals):
            continue
        if all(_cell_is_black(arr, page, sx, sy, grid, prgbs[p])
               for p in pals):
            got = [tiles[i] for i in idxs]
            for t in got:
                t['was_black'] = True     # never fill this one IN PLACE
            out[key] = got
    return out


# ------------------------------------------------------------------ writing
# ------------------------------------------------- THE OVERLAY MARGIN. F-197
# LAYER 1 GOT ITS MARGIN IN BUILD 30. LAYERS 2/3/4 NEVER DID.
#
# `ff7nx_marginart` fills Cosmos's widescreen placeholder cells from the DDS,
# and its own scope line says how far it reaches: "Cosmos art replaces vanilla
# inside the 4:3 picture too, ON LAYER 1". Cosmos widened the OVERLAYS as well,
# and nothing has ever filled those.
#
# `onna_5` -- the Honey Bee Inn keyhole -- is the clean demonstration:
#
#     vanilla   layer 4   96 tiles   opaque x -160..159
#     Cosmos    layer 4  156 tiles   opaque x -160..159, tiles out to +/-224
#
# The 60 extra tiles are 100% TRANSPARENT in the paletted page, and Cosmos's
# own DDS is FULLY OPAQUE and near-black at exactly those cells -- mean RGB
# (8.7, 8.6, 7.9) at alpha 255. That black is the keyhole mask. On layer 1 an
# empty cell is a black square; on layers 2+ index 0 is the colour key, so an
# empty cell is a HOLE, and the hole is the room showing through the mask in
# the 16:9 reveal.
#
# WHY THIS IS SAFE TO DO HERE AND NOT SOMEWHERE ELSE. This module already
# writes art into an empty cell, picks a palette, and refuses when the cell is
# shared -- and `_art_block` was corrected for 32-unit cells in anticipation of
# exactly this (HANDOFF-188 3.1). The selection below adds no new machinery.
#
# MEASURED, 220 fields, `_ovmargin.py`:
#
#     layer>=2 tiles outside the 4:3 box            41,574
#       already carry art                           35,820
#       cell entirely keyed                          5,754
#         the mod ships no art for it                    0
#         the art is transparent there too           2,070   left alone
#         FILLABLE -- art opaque at that cell        3,684
#           and dark (a mask)                          773
#           and bright (real widened scenery)        2,911
#
# And the ambiguity that cost build 91 does not exist in this population.
# MEASURED on `onna_5`, all 908 outside-box overlay cells:
#
#     shared by more than one destination     0
#     also sampled by a tile INSIDE the box   0
#     sampled at more than one palette        0
#
# Every palette they name carries at least two non-zero entries at or below
# luma 12, so black is expressible without index 0 -- which `MA.quantise`
# guarantees anyway ("INDEX 0 EXCLUDED", and it says why).
#
# THE ORDERING CONSTRAINT, AND IT IS THE SHARP EDGE HERE. Cosmos names its art
# `<field>_<page>_<pal>.dds` against the page the cell is on NOW, so this may
# only run at the FIRST call site -- after `marginart`, before `marginpage` and
# the repack. At the second call site the pages have been renumbered and
# compacted, and `art(field, slot, pal)` would return a different page's
# picture. That is not hypothetical: it is exactly how FINDINGS-195's olive
# blocks were filled. `apply_to_flevel(..., overlay=True)` is therefore opt-in
# and build.py sets it on the first call only.
OVERLAY_MARGIN = os.environ.get('SEVENTH_NX_NO_OVERLAY_MARGIN') != '1'
OVERLAY_MIN_COVER = 0.90    # the art must be this opaque to be a mask at all
BOX_X, BOX_Y = 160, 120     # the 4:3 picture, in destination units


def _outside_43(t):
    """True when a tile is not wholly inside the 4:3 picture."""
    x, y = t['dst']
    n = t.get('n', TILE)
    return x < -BOX_X or x + n > BOX_X or y < -BOX_Y or y + n > BOX_Y


def overlay_cells(sec9, surv):
    """
    {(slot, sx, sy): [tile, ...]} -- EMPTY cells that only widescreen overlay
    placeholders sample.

    Deliberately narrow, and every clause is a refusal this pass has paid for
    before:

      * layer >= 2 only              layer 1 is `black_cells`' population and
                                     is already handled
      * the cell is entirely keyed   so writing into it cannot overwrite art
      * NOTHING inside the 4:3 box   samples it -- the interior is not this
                                     defect (FINDINGS-173) and must not move
      * ONE destination              FINDINGS-195: the art is addressed by page
                                     cell, so a cell drawn in two places has no
                                     unambiguous art
      * ONE palette                  a depth-1 page is one index array
      * no fx tile                   an fx tile draws its SECOND texture, so
                                     this cell is not what it samples
    """
    if not OVERLAY_MARGIN:
        return {}
    pages = {p.slot: p for p in surv['pages']}
    tiles, by_cell = survey_tiles(sec9, surv)
    arrays = {}
    out = {}
    for key, idxs in by_cell.items():
        here = [tiles[i] for i in idxs]
        if any(t['layer'] < 2 or t['fx'] for t in here):
            continue
        if not all(_outside_43(t) for t in here):
            continue
        if len({t['dst'] for t in here}) > 1:
            continue
        if len({t['pal'] for t in here}) > 1:
            continue
        slot = key[0]
        page = pages.get(slot)
        if page is None or page.depth != 1:
            # A truecolor page has no index channel and its key is 0x0000.
            # Refusing is one line; getting it wrong is a transparent mask.
            continue
        arr = arrays.get(slot)
        if arr is None:
            try:
                arr = arrays[slot] = PC._page_array(page)
            except Exception:                                  # noqa: BLE001
                continue
        grid, _ = _grid_step(page)
        if not _cell_is_empty(arr, page, key[1], key[2], grid):
            continue
        for t in here:
            t['overlay'] = True
        out[key] = here
    return out


def _free_cells(page, by_cell, arr, grid, step):
    """Cells of `page` that NO tile samples and that are entirely empty."""
    taken = {(sx, sy) for (slot, sx, sy) in by_cell if slot == page.slot}
    out = []
    for gy in range(grid):
        for gx in range(grid):
            sx, sy = gx * step, gy * step
            if (sx, sy) in taken:
                continue
            if _cell_is_empty(arr, page, sx, sy, grid):
                out.append((sx, sy))
    return out


def _spare_cell(slot, pages, by_cell, arrays):
    """
    (slot, sx, sy) for a free cell, preferring the tile's own page.

    Falls back to any other page of the SAME depth, resolution and size flag,
    which is what `field_bg_pagecap.relocate_cells` does for the same reason:
    "the slot was never the problem" -- the pages already present are packed
    unevenly and there is nearly always room somewhere in the group.

    MEASURED: restricting this to the tile's own page left 6 of the 11
    single-tile fields with `no_room` -- `trnad_1`, `ship_2`, `blin70_2`,
    `bugin1a`, `junair2`, `kuro_5`. Their page 0 is fully referenced; other
    depth-1 pages in the same field are not.
    """
    src = pages[slot]
    order = [slot] + sorted(s for s, p in pages.items()
                            if s != slot
                            and (p.depth, p.px, p.size_flag)
                            == (src.depth, src.px, src.size_flag))
    for s in order:
        page = pages[s]
        grid, step = _grid_step(page)
        arr = arrays.get(s)
        if arr is None:
            try:
                arr = arrays[s] = PC._page_array(page)
            except Exception:                                  # noqa: BLE001
                continue
        free = _free_cells(page, by_cell, arr, grid, step)
        if free:
            return s, free[0]
    return None


def _best_palette(small, prgbs, npg):
    """
    (palette id, indices, error) -- the palette that renders this art best.

    THE MOD'S PALETTE BYTE IS NOT A COLOUR DECISION. Cosmos leaves it at
    whatever it was, because on FFNx the DDS replaces the page and the palette
    is never applied -- `ff7nx_marginpal`'s whole reason for existing.

    MEASURED on `trnad_3`: the layer-1 tile names palette 5, and quantising
    the pale cyan sky (116, 211, 212) against palette 5 collapses to ONE index
    whose colour is (0, 0, 0). Honouring that byte turns a black square into a
    different black square.

    Choosing is only safe because the cell is PRIVATE to this tile -- the
    palette byte is per-tile, and no other tile reads the cell we write.
    """
    best = None
    for pal in range(npg):
        prgb = prgbs[pal]
        try:
            idx = MA.quantise(small.astype(np.float32), prgb)
        except Exception:                                      # noqa: BLE001
            continue
        err = float(np.abs(prgb[idx].astype(np.float32)
                           - small.astype(np.float32)).mean())
        if best is None or err < best[2]:
            best = (pal, idx, err)
    return best


def _art_block(img, page, sx, sy, grid):
    """
    The mod's art for one cell, box-filtered to the page's own resolution.

    Returns (side, side, 3) float or None. Mirrors `ff7nx_marginart`'s filter
    exactly -- a k-times block reduced by mean -- so a cell written here and a
    cell written there cannot disagree along their shared edge.
    """
    k = img.shape[1] // 256
    if k < 1:
        return None
    # THE CELL IS NOT ALWAYS 16 UNITS. HANDOFF-189.
    #
    # `grid` is 8 on a `size_flag` page -- an 8x8 grid of 32-unit cells, which
    # is what the parallax layers use. Taking a TILE-wide window there reads
    # the top-left QUADRANT of the cell and fills only that, leaving three
    # quarters at index 0. That is the checkerboard `ff7nx_marginart` was
    # producing on Mt. Corel's mountain until the same literal was replaced
    # there.
    #
    # It cannot fire today, because `black_cells()` selects layer-1 tiles only
    # and layer 1 never uses a 32-unit tile (346,161 of 346,175 vanilla
    # layer-1 records leave width at 0). It is corrected here anyway, because
    # HANDOFF-188 3.1 plans to widen this pass to layers 2/3/4 -- which is
    # precisely where the 32-unit tiles are.
    #
    # `edge == TILE` on every non-size_flag page, so this is a no-op there.
    edge = 256 // grid
    src = img[sy * k:(sy + edge) * k, sx * k:(sx + edge) * k]
    if src.shape[:2] != (edge * k, edge * k):
        return None
    side = page.px // grid
    f = (edge * k) // side
    if f < 1 or side * f != edge * k:
        return None
    rgb = np.ascontiguousarray(src[..., :3])
    return rgb.reshape(side, f, side, f, 3).mean(axis=(1, 3))


def _art_cover(img, page, sx, sy, grid):
    """`_art_block`'s alpha twin -- which texels of the cell the mod PAINTS.

    Same window and same reduction, so a texel counted here is the texel whose
    colour `_art_block` returned. `provider_source` puts `PageArt.tmask` in
    channel 3, so 255 means the mod paints and 0 means it does not.
    """
    k = img.shape[1] // 256
    if k < 1 or img.shape[-1] < 4:
        return None
    edge = 256 // grid
    src = img[sy * k:(sy + edge) * k, sx * k:(sx + edge) * k]
    if src.shape[:2] != (edge * k, edge * k):
        return None
    side = page.px // grid
    f = (edge * k) // side
    if f < 1 or side * f != edge * k:
        return None
    a = np.ascontiguousarray(src[..., 3]).reshape(side, f, side, f)
    return a.mean(axis=(1, 3)) >= 128


def fill_field(name, raw, art, log=lambda *_: None, overlay=False):
    """
    (new raw bytes, stats) for one field, or (None, stats) if nothing changed.

    `art(field, page, palette) -> (image, src_pal) | None` is the same provider
    `ff7nx_marginart` is given.

    `overlay=True` also fills the WIDESCREEN OVERLAY MARGIN -- see
    `overlay_cells`. It is off by default because it is only correct while the
    page numbering is still Cosmos's, i.e. at the first of build.py's two call
    sites.
    """
    import lgp

    st = {'black': 0, 'fixed': 0, 'in_place': 0, 'copied': 0,
          'no_art': 0, 'no_room': 0, 'depth2': 0, 'reverted': 0,
          'ov_cells': 0, 'ov_filled': 0, 'ov_no_art': 0}
    parts = lgp.split_sections(raw)
    sec9 = parts[SECTION9]
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    cols, _hdr, npg, _cpp = MB.palette_colours(parts[SECTION_PALETTE])
    prgbs = [MA.palette_rgb(cols[i]) for i in range(npg)]
    bad = black_cells(sec9, surv, prgbs)
    st['black'] = sum(len([t for t in v if t['layer'] == 1])
                      for v in bad.values())
    if overlay:
        # DISJOINT BY CONSTRUCTION -- `black_cells` selects cells with a
        # layer-1 tile, `overlay_cells` refuses any cell that has one. The
        # assert is cheap and this is the kind of thing that silently
        # double-writes a cell two builds from now.
        _ov = overlay_cells(sec9, surv)
        assert not (set(_ov) & set(bad)), 'overlay and black populations met'
        st['ov_cells'] = len(_ov)
        bad.update(_ov)
    if not bad:
        return None, st

    buf = bytearray(sec9)
    arrays, touched = {}, set()
    _tiles, by_cell = survey_tiles(sec9, surv)

    for key in sorted(bad):
        slot, sx, sy = key
        page = pages[slot]
        grid, step = _grid_step(page)
        if slot not in arrays:
            arrays[slot] = PC._page_array(page)
        arr = arrays[slot]
        here = bad[key]
        # THE OVERLAY MARGIN IS THE SAME WRITE WITH A DIFFERENT POPULATION.
        # `overlay_cells` has already guaranteed one destination, one palette,
        # no layer-1 tile, no fx tile and an empty cell -- so `others` is empty
        # and the fill is in place, by construction rather than by test.
        _ov = bool(here) and all(t.get('overlay') for t in here)
        if _ov:
            l1 = [t for t in here if not t['fx']]
        else:
            l1 = [t for t in here if t['layer'] == 1 and not t['fx']]
        if not l1:
            continue
        others = [] if _ov else [t for t in here if t['layer'] != 1]
        by_pal = defaultdict(list)
        for t in l1:
            by_pal[t['pal']].append(t)
        # In place only when nothing else reads this cell and every layer-1
        # tile agrees on the palette -- then no index can be misread.
        # A CELL THAT MERELY RENDERS BLACK IS NOT EMPTY, SO IT IS NEVER
        # FILLED IN PLACE. The module's first safety property -- "it only ever
        # writes into a cell that is ENTIRELY EMPTY, so it cannot overwrite
        # art" -- is what makes this pass safe to run on 709 fields, and the
        # new population would break it. A private copy costs one free cell
        # and keeps the original bytes untouched.
        _was_black = any(t.get('was_black') for t in here)
        in_place = not others and len(by_pal) == 1 and not _was_black
        for pal, group in sorted(by_pal.items()):
            got = None
            for want in (pal, 0):
                if want >= npg:
                    continue
                try:
                    got = art(name, slot, want)
                except Exception:                              # noqa: BLE001
                    got = None
                if got is not None:
                    break
            if got is None:
                st['no_art'] += len(group)
                continue
            block = _art_block(got[0], page, sx, sy, grid)
            if block is None:
                st['no_art'] += len(group)
                continue
            # THE MOD HAS TO DISAGREE WITH THE BLACK, OPAQUELY. See
            # BLACK_ART_MIN_LUMA. This is the whole safety of the widened
            # selection: 16,463 layer-1 tiles across 70 fields draw a black
            # cell, and all but SIX of them are dark caves, night scenes and
            # silhouettes that Cosmos draws dark as well. Where the mod agrees
            # the cell is black, it stays black -- the filler is only replaced
            # where Cosmos explicitly paints over it.
            #
            # Only the newly-selected cells are gated. A cell that is EMPTY is
            # this pass's original population and keeps its original rule.
            if _ov:
                # OPACITY ONLY, AND NO LUMA TEST. The whole point of this
                # population is that the art IS black -- it is a mask. Asking
                # it to be bright, as the `was_black` gate does, would refuse
                # exactly the cells worth filling. What must be true is that
                # Cosmos PAINTED there rather than leaving the placeholder
                # transparent, and that is what the alpha says.
                _cov = _art_cover(got[0], page, sx, sy, grid)
                if _cov is None or float(_cov.mean()) < OVERLAY_MIN_COVER:
                    st['ov_no_art'] += len(group)
                    st['no_art'] += len(group)
                    continue
            elif _was_black:
                _cov = _art_cover(got[0], page, sx, sy, grid)
                if _cov is None or float(_cov.mean()) < BLACK_ART_MIN_COVER \
                        or float(block[_cov].mean() if _cov.any()
                                 else 0.0) < BLACK_ART_MIN_LUMA:
                    st['no_art'] += len(group)
                    continue
            side = page.px // grid
            if block.shape[0] != TILE:
                # quantise() is written for a 16x16 cell. A larger page cell
                # is reduced to 16x16 for the palette decision and written
                # back at full size, which is what the paletted path does
                # everywhere else -- a depth-1 page carries no more colour
                # than its table regardless of resolution.
                r = block.reshape(TILE, side // TILE, TILE, side // TILE, 3)
                small = r.mean(axis=(1, 3))
            else:
                small = block
            if page.depth == 2:
                # A TRUECOLOR PAGE HAS NO INDEX CHANNEL, so there is no
                # palette to choose and nothing to quantise -- the art goes in
                # as 565. `black_ok=False` keeps a fully black source off
                # 0x0000, which means TRANSPARENT on a depth-2 page
                # (x86 0x6470E0); it takes NEAR_BLACK's 0.9/255 lift instead,
                # exactly as the promotion does.
                rgba = np.empty(small.shape[:2] + (4,), np.uint8)
                rgba[..., :3] = np.clip(small, 0, 255).astype(np.uint8)
                rgba[..., 3] = 255
                packed = FN.rgba_bytes_to_565(rgba.tobytes(),
                                              small.shape[0] * small.shape[1])
                chosen = np.frombuffer(packed, '<u2').reshape(small.shape[:2])
                use_pal, err = pal, None
                st['depth2'] += len(group)
            elif in_place:
                # No repoint, so the palette byte has to stay as it is: other
                # tiles may name this cell later even if none does now.
                chosen = MA.quantise(small.astype(np.float32), prgbs[pal])
                use_pal, err = pal, None
            else:
                pick = _best_palette(small, prgbs, npg)
                if pick is None:
                    st['no_art'] += len(group)
                    continue
                use_pal, chosen, err = pick
            if in_place:
                tgt = (slot, sx, sy)
            else:
                spot = _spare_cell(slot, pages, by_cell, arrays)
                if spot is None:
                    st['no_room'] += len(group)
                    continue
                tslot_, (tx_, ty_) = spot
                tgt = (tslot_, tx_, ty_)
            tslot, tx, ty = tgt
            tpage = pages[tslot]
            tgrid, _tstep = _grid_step(tpage)
            tside = tpage.px // tgrid
            idx = chosen
            if tside != TILE:
                idx = np.repeat(np.repeat(idx, tside // TILE, axis=0),
                                tside // TILE, axis=1)
            tarr = arrays[tslot]
            ys, xs = PC._cell_slice(tpage, tx, ty, tgrid)
            if idx.shape != tarr[ys, xs].shape:
                st['no_art'] += len(group)
                continue
            tarr[ys, xs] = idx.astype(tarr.dtype)
            touched.add(tslot)
            if in_place:
                st['in_place'] += len(group)
                if _ov:
                    st['ov_filled'] += len(group)
            else:
                eu, ev = PC._uv_encode(tx, ty)
                for t in group:
                    buf[t['off'] + RP.T_TEXID] = tslot
                    if pages[tslot].depth == 1:
                        buf[t['off'] + RP.T_PALETTE] = use_pal
                    struct.pack_into('<II', buf, t['off'] + PC.T_SRC_UV,
                                     eu, ev)
                    # WRITE THE BYTE TOO. FINDINGS-193 4.1, which found this
                    # pass leaving offsets 10/12 stale and measured 162 uv
                    # disagreements in the shipped build-91 archive -- every
                    # one of them ours, every one on layer 1.
                    #
                    # The ENGINE reads the uv, so the stale byte drew nothing
                    # wrong. `ff7nx_marginblack.read_tiles` reads the BYTE,
                    # and `collect()` and most of this pipeline are built on
                    # it, so the disagreement is a trap for any pass ordered
                    # after this one -- and `_bcaudit.py` only found the
                    # build-91 regression BECAUSE the two disagreed.
                    #
                    # `tx`/`ty` are already in 256-space (survey_tiles: "the
                    # same units `_uv_encode` takes"), which is what offsets
                    # 10/12 hold, so this is the identical coordinate in the
                    # other representation and cannot move a tile.
                    buf[t['off'] + RP.T_SRC_X] = tx & 0xFF
                    buf[t['off'] + RP.T_SRC_Y] = ty & 0xFF
                # the copy is now occupied; do not hand it out twice
                by_cell[(tslot, tx, ty)] = []
                st['copied'] += len(group)
                if err is not None:
                    st.setdefault('err', []).append(err)
            st['fixed'] += len(group)

    if not st['fixed']:
        return None, st

    # Same rebuild path `ff7nx_marginart` ends on: re-parse the texture block,
    # swap the changed Page objects, write it back. Sizes are unchanged, so
    # nothing else in the section moves.
    # PASS THE DETECTED PAGE SIZE. `parse_texture_block` defaults to 256 and
    # this build ships 512px pages in 691 fields, where the default raises
    # "slot 13 has depth 32735" -- it is reading page headers at the wrong
    # stride. `DC.survey` already detected the real size; use it.
    plist, tex_start, tex_end = FN.parse_texture_block(bytes(buf),
                                                       surv['page_px'])
    for slot in sorted(touched):
        for i, p in enumerate(plist):
            if p is not None and p.slot == slot:
                plist[i] = FN.Page(p.slot, p.size_flag, p.depth,
                                   arrays[slot].tobytes(), p.px)
    new9 = FN.replace_texture_block(bytes(buf), plist, tex_start, tex_end)
    parts[SECTION9] = new9

    # REFUSE OUR OWN WORK IF IT DID NOT HELP. Cheap, and it turns any bug in
    # the arithmetic above into "the field is unchanged" instead of "the
    # field is now wrong in a new way".
    # THE REFUSAL COUNTS EMPTY CELLS, NOT BLACK ONES, AND THAT IS DELIBERATE.
    #
    # "No improvement" was a valid inference while the population was EMPTY
    # cells: fill one and it stops being empty. It is NOT valid for a cell that
    # merely renders black, because the mod's art for it can itself be dark --
    # `bugin1a` (Bugenhagen's observatory) is exactly that, and the check
    # refused a fill that had worked:
    #
    #     before  2 layer-1 tiles, 0 of them the black-cell kind
    #     after   2 layer-1 tiles, 2 of them the black-cell kind
    #
    # The art landed; it is dark art. Measuring "is it still dark" and calling
    # that failure loses the fix for the whole field.
    #
    # So the guard keeps build 90's exact test -- the EMPTY-cell count, no
    # palettes -- and asks only that it did not get WORSE. That is the property
    # it was written to protect: "a bug here costs the fix, not the field." A
    # copy that lands somewhere it should not still shows up as a new empty
    # cell and is still caught.
    try:
        _before = black_cells(sec9, surv)
        n_before = sum(len([t for t in v if t['layer'] == 1])
                       for v in _before.values())
    except Exception:                                          # noqa: BLE001
        n_before = 0
    try:
        after = black_cells(new9, DC.survey(new9))
        n_after = sum(len([t for t in v if t['layer'] == 1])
                      for v in after.values())
    except Exception:                                          # noqa: BLE001
        n_after = n_before + 1
    if n_after > n_before:
        st['reverted'] = 1
        log('  ! blackcell: %s went %d -> %d EMPTY cell(s); left unchanged'
            % (name, n_before, n_after))
        return None, st
    st['after'] = n_after
    return lgp.join_sections(parts), st


# --------------------------------------------------------------- the build
def apply_to_flevel(archive, payloads, art, encode=None, log=print,
                    fields=None, overlay=False):
    """Fill the black cells of every field in `archive`. Returns stats."""
    import lgp

    encode = encode or (lambda raw: archive.encode_field(raw))
    st = {'fields': 0, 'black': 0, 'fixed': 0, 'in_place': 0, 'copied': 0,
          'no_art': 0, 'no_room': 0, 'depth2': 0, 'reverted': 0,
          'ov_cells': 0, 'ov_filled': 0, 'ov_no_art': 0, 'ov_fields': 0,
          'depth2_fields': [], 'done': []}
    for nm in archive.names():
        if fields and nm not in fields:
            continue
        e = archive.index.get(nm)
        if e is None or not archive.is_field(e):
            continue
        try:
            payload = payloads.get(nm)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(e))
        except Exception:                                      # noqa: BLE001
            continue
        try:
            new, one = fill_field(nm, raw, art, log=log, overlay=overlay)
        except Exception as exc:                               # noqa: BLE001
            log('  ! blackcell: %s skipped (%s)' % (nm, exc))
            continue
        for k in ('black', 'fixed', 'in_place', 'copied', 'no_art',
                  'no_room', 'depth2', 'reverted',
                  'ov_cells', 'ov_filled', 'ov_no_art'):
            st[k] += one.get(k, 0)
        if one.get('ov_filled'):
            st['ov_fields'] += 1
        if one.get('depth2'):
            st['depth2_fields'].append(nm)
        if new is not None:
            payloads[nm] = encode(new)
            st['fields'] += 1
            st['done'].append((nm, one['fixed']))
    return st


def _summarise_overlay(st):
    if not st.get('ov_filled'):
        return []
    return ['  field background OVERLAY MARGIN: %d cell(s) on layers 2/3/4 in '
            '%d field(s) were Cosmos widescreen placeholders sampled ONLY '
            'outside the 4:3 picture and entirely COLOUR-KEYED -- on those '
            'layers index 0 is transparency, so an empty placeholder is a '
            'HOLE, not a black square. Filled from the mod\'s own art, which '
            'is opaque there. %d refused because the art is transparent too '
            '(a placeholder Cosmos left genuinely empty). onna_5, the Honey '
            'Bee Inn keyhole, is the case this was built from: its mask '
            'stopped at x +/-160 and the room showed through the 16:9 reveal '
            'around it. Turn off with SEVENTH_NX_NO_OVERLAY_MARGIN=1.'
            % (st['ov_filled'], st['ov_fields'], st['ov_no_art'])]


def summarise(st):
    if st and st.get('ov_filled') and not st.get('black'):
        return '\n'.join(_summarise_overlay(st))
    if not st or not st.get('black'):
        return '  field background: no layer-1 tile draws an empty cell'
    out = ['  field background: %d layer-1 tile(s) in %d field(s) were drawing '
           'an EMPTY page cell, which layer 1 renders as a BLACK SQUARE '
           '(index 0 is not colour-keyed there). %d fixed in %d field(s) '
           '-- %d cell(s) filled in place, %d given a private copy of the '
           'cell so the layer-2+ tiles sharing it are untouched'
           % (st['black'], st['fields'] + len(st['depth2_fields']),
              st['fixed'], st['fields'], st['in_place'], st['copied'])]
    if st['no_art']:
        out.append('      %d left alone: the mod ships no art for that cell'
                   % st['no_art'])
    if st['no_room']:
        out.append('      %d left alone: no free cell on the page' %
                   st['no_room'])
    if st['depth2']:
        out.append('      %d of them were on TRUECOLOR pages and went in as '
                   '565 rather than through a palette' % st['depth2'])
    if st['reverted']:
        out.append('      %d field(s) refused their own edit (no improvement)'
                   % st['reverted'])
    out.extend(_summarise_overlay(st))
    return '\n'.join(out)


# --------------------------------------------------------------------- CLI
def main(argv=None):
    import lgp

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.exit(__doc__)
    a = lgp.Archive(argv[0])
    wanted = set(argv[1:])
    tot = 0
    for nm in a.names():
        if wanted and nm not in wanted:
            continue
        e = a.index.get(nm)
        if e is None or not a.is_field(e):
            continue
        try:
            raw = a.decompressed(e)
            sec9 = lgp.split_sections(raw)[SECTION9]
            bad = black_cells(sec9, DC.survey(sec9))
        except Exception:                                      # noqa: BLE001
            continue
        if not bad:
            continue
        n = sum(len([t for t in v if t['layer'] == 1]) for v in bad.values())
        tot += n
        slots = sorted({k[0] for k in bad})
        print('  %-14s %4d layer-1 tile(s) black   page slot(s) %s' %
              (nm, n, slots))
        for key in sorted(bad)[:3]:
            sh = bad[key]
            print('      cell %s sampled by %s' %
                  (key, [(t['layer'], t['dst'], t['pal']) for t in sh]))
    print('\n%d layer-1 tile(s) drawing black' % tot)
    return 0


if __name__ == '__main__':
    sys.exit(main())
