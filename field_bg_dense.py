#!/usr/bin/env python3
"""
field_bg_dense.py -- promote EVERY cell by repacking, instead of promoting
pages and paying for the leftovers.

WHY THIS EXISTS (FINDINGS-77)
=============================
A truecolor page holds 256 cells. Count every `(page, cell, palette)` the game
actually draws in a field and pack them densely, and full promotion costs:

    vanilla paletted pages          mean 3.7   max 12
    IDEAL fully-promoted pages      mean 3.5   max  7    <- 100% of cells
    the page-by-page promotion      mean 6.5   max 15

100% coverage needs FEWER pages than vanilla. The 16-page ceiling and the
no-growth loop never bound it; the promotion's shape did:

  * page by page, so a paletted page used at 3 palettes costs 3 truecolor
    pages -- a field averages 3.7 pages but 13.3 (page, palette) pairs, and
    `jundoc1b` has 7 pages against 48 pairs;
  * the original page has to stay alive for every cell that could not move,
    so promotion ADDS rather than REPLACES.

Those palettes mostly use DIFFERENT cells, so the union is small: `jundoc1b`
draws 1,676 distinct (cell, palette) combinations, which is 7 pages, not 48.

WHAT THIS DOES
--------------
Per field: enumerate every (page, cell, palette) any tile references, source
its pixels once, pack 256 to a page, repoint every tile, and drop every
original page.

CONSEQUENCES BEYOND COVERAGE
----------------------------
* THE PALETTE-MIXING BUG DISAPPEARS. Every cell is baked with the palette it
  names, so no page can be drawn through a foreign colour table. That is the
  Sector 6 yellow, and `ff7nx_marginpage` exists only to work around it.
* No stock-next-to-upscaled: every cell in a field comes from one pipeline at
  one depth.
* Page count falls below vanilla, so `field_load_textures` is never asked for
  more textures than it was provisioned for.

THE THREE CONSTRAINTS, AND HOW EACH IS MET
------------------------------------------
1. BLEND MODE COMES FROM THE SLOT INDEX (field_bg_native.D2_GROUPS): 0x1A-0x20
   opaque, 0x21-0x27 additive, 0x28-0x29 average. A cell must land in the band
   its tiles already draw in. MEASURED over all 709 fields: no field overflows
   a band -- worst case 7 pages, and the opaque band holds 7.

2. THE COLOUR KEY IS NOT A CUT-OUT ON LAYER 1. Proved on hardware: setting
   palette entry 0 to black removed the Sector 6 yellow AND put black speckles
   across Wall Market. If index 0 were discarded, its colour could not matter.
   Layer 1 has nothing behind it, so a "transparent" pixel there was always
   entry 0's colour; baking that colour is exactly equivalent. A cell used by
   any layer-2+ tile keeps `0x0000`, because 58% of vanilla layer-2 cells use
   the key and 33% of their pixels are index 0 -- those are real overlays.

3. AN FX TILE AND ITS BASE SHARE ONE u,v. Both cells must land at the SAME
   grid index in two different pages. Dense packing chooses placement, so this
   is a constraint to satisfy rather than a reason to refuse -- refusing is
   what costs 15% of cells today.
"""
from __future__ import annotations

import os
import struct
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC
import ff7nx_marginblack as MB
import field_bg_native as FN

TILE = 16
GRID = 16                        # 16x16 cells of 16x16 texels on a 256px page
PER_PAGE = GRID * GRID

# THE PARALLAX GRID. A page declares which of the two it uses in `size_flag`.
#
# `field_bg_pagecap._grid_step`, `field_bg_compact` and `_hole.py` have all
# read this flag for a long time; this pass is the one that did not, and
# FINDINGS-189 is the record of what that cost. A size_flag page is an 8x8
# grid of 32-texel cells, so a destination page holds SIXTY-FOUR of them
# rather than 256 -- which is the whole of the budget arithmetic that makes
# section 5.1 of HANDOFF-192 delicate.
BIG_TILE = 32
BIG_GRID = 8
BIG_PER_PAGE = BIG_GRID * BIG_GRID                 # 64

# A promoted cell keeps its grid coordinate and only changes page. See the
# long note at the placement loop. False restores the old dense packing
# (enumeration order), for A/B.
# BUILD 48, AND IT WAS A NET LOSS. MEASURED across all 20 build logs, and
# 47 -> 48 changed nothing else:
#
#     dense repack cells   300,513 -> 283,264    -17,249 cells
#     dense repack pages     1,344 ->   1,607       +263 pages
#
# 17,249 cells STOPPED being promoted to truecolor and fell back to the
# quantised paletted page -- i.e. 17,249 cells of Cosmos art traded for
# vanilla-derived art, which is the opposite of the point. The user reported
# it on the build ("this issue is new of not using some textures it feels
# like") and it was read as a perception problem.
#
# It also costs 263 EXTRA PAGES, and pages are the binding resource:
# `field_load_textures` aborts the whole loop on the first page it cannot
# allocate (FINDINGS-141 section 7), so this made the black-square ceiling
# strictly worse in every field it touched.
#
# And it did not deliver what it claimed. I reported it as taking cell
# adjacency 1% -> 89%; MEASURED on the shipped archive, all 631 fields with
# truecolor pages, it is 5.2%. The 89% was one code path measured in
# isolation and reported as the archive.
#
# True restores build 48 for A/B.
PRESERVE_CELL_COORDS = False
SECTION9 = 8
UV_SCALE = 10_000_000
STEP = UV_SCALE // GRID
BIG_STEP = UV_SCALE // BIG_GRID

T_SRC_X, T_SRC_Y = 10, 12
T_SRCX2, T_SRCY2 = 14, 16      # the fx frame's OWN source. FINDINGS-161/163.
T_PAL = FN.TILE_PALETTE_ID              # 22
T_TEXID = FN.TILE_TEXTURE_ID            # 32
T_FX_PAGE = FN.TILE_TEXTURE_ID2         # 34
T_SRC_X_BIG, T_SRC_Y_BIG = 42, 46

# The truecolor bands, taken from field_bg_native so there is ONE definition.
# Band 4 is three slots wide, not seven -- see FN.D2_OPAQUE_SLOTS for the
# measurement (vanilla puts every depth-2 page it has in 26, 27 or 28, and
# every build of ours that used 29+ produced black squares).
BANDS = {b: (lo, hi) for lo, hi, b in FN.D2_GROUPS}             # truecolor
D1_BANDS = {4: (0x00, 0x0F), 1: (0x0F, 0x18), 0: (0x18, 0x1A)}  # paletted


# HOW FAR A PALETTE-0 BORROW MAY MOVE A CELL, IN MEAN |RGB| OVER 0-255.
#
# `source_cell` falls back to palette 0's art when the mod ships nothing for
# the palette a tile names, because FFNx does (saveload.cpp:138). On a
# PALETTED page that is harmless -- the index is recoloured by the tile's own
# palette on the way to the screen. On a TRUECOLOR page the pixels ARE the
# final colour, so a borrow bakes palette 0's colours in permanently and the
# tile's own palette is never applied again.
#
# MEASURED on hardware, `nmkin_5` -- the red railing outside Reactor 1, which
# is red only because it draws through palette 7:
#
#     dst (1, 32, 112)   slot 2 -> 27   palette 5    RGB(136, 13, 13) -> (5, 15, 21)
#     dst (1, -96, 240)  slot 3 -> 28   palette 7    RGB(192, 51, 40) -> (64, 62, 53)
#
# Bright red becomes grey. The cell was promoted to truecolor and took
# palette 0's grey art with it. That is the "missing texture" -- the tile is
# present, the page is present, the art is simply the wrong variant.
#
# The comment at the borrow site already predicted this and said what to do:
# "If it is brown again, GATE THIS ON THE PALETTE DISTANCE rather than
# deleting it: distance < 8 was 0.7% of candidates and distance < 32 was
# 4.6%." It is brown again -- `mds6_3` is a yellow wash and the railing is
# grey -- so this is that gate.
#
# A refused borrow is not a lost cell: it falls through to the paletted page,
# which `ff7nx_marginart` has already filled with Cosmos art quantised
# against the tile's OWN palette. Right colours at 8 bits, instead of wrong
# colours at 16.
# OFF. `_detail_transfer` ALREADY SOLVES THIS, AND BETTER.
#
# I added this gate to stop a borrow baking palette 0's grey over the red
# railing in `nmkin_5`. It did stop it -- and it stopped 44% of Cosmos's
# detail with it, which is a regression, not a fix. The reason is three lines
# below the borrow site: when `src_pal != pal` this module already calls
# `_detail_transfer(out, pal_ref)`, which takes STRUCTURE from the borrowed
# upscale and COLOUR from the palette the cell actually names. A borrow here
# has not been able to change a cell's colour since that function landed.
#
# The grey railing was never this module's doing. `ff7nx_marginart` runs
# FIRST, borrows the same art, quantises it into the paletted page with no
# detail transfer at all, and that page is what `pal_ref` reads. The colour
# was already gone before this code saw the cell. The fix belongs there, and
# it is there now -- as a detail transfer, so nothing is refused.
#
# Set to a finite value only to A/B the borrow itself. It should stay off.
BORROW_MAX_DIST = float('inf')

# THE ATLAS-GAP ARM, and the switch that turns it off for an A/B.
#
# `SEVENTH_NX_NO_ATLASGAP=1` restores build 84's behaviour exactly -- every
# promoted cell whose paletted source is index 0 keeps the colour key, whether
# or not anything is behind it. See bare_keys() for what this buys and what it
# risks. The flag exists because this arm is the first thing in the chain that
# deliberately draws where vanilla drew nothing, and a single environment
# variable is a cheaper way to answer "was it this?" than a 40-minute rebuild
# of the previous tree.
ATLAS_GAP = os.environ.get('SEVENTH_NX_NO_ATLASGAP') != '1'

# THE WIDESCREEN OVERLAY PLACEHOLDER TAKES THE MOD'S ALPHA. FINDINGS-235.
#
# The same reasoning as the atlas-gap arm, for the one population `bare_keys`
# structurally cannot admit.
#
# Cosmos authors the 16:9 extension of an OVERLAY layer the way it authors the
# extension of layer 1: blank placeholder cells in section 9, with the real
# art shipped in the page .dds. On FFNx the .dds replaces the page and the
# overlay draws through its own alpha, which is why the green wash there runs
# the full width with no step at the 4:3 boundary. On this port the cell is a
# genuine 100%-index-0 cell, so `out[zero] = FN.EMPTY` keys the whole thing
# and the overlay is simply absent outside the picture.
#
# `bare_keys` is the existing answer to "index 0 here is not a cut-out" and it
# cannot serve these cells: it requires that NOTHING draws underneath, and in
# the widescreen margin layer 1 always does -- that art is the whole point of
# the margin. `bare` is asking "is there a hole behind this key", which is the
# right question for `mtcrl_5`'s sky and the wrong one here. The question here
# is "is this key a shape the artist cut, or a placeholder the artist left",
# and the population below answers it by construction:
#
#   * EVERY tile that draws the cell is on LAYER 2 -- a layer-1 margin cell is
#     background, keeps the quota it has always had, and is
#     `ff7nx_marginart`'s business, not this one's; a layer-3/4 cell has no
#     fixed screen position, so "outside the picture" is not decidable for it;
#   * EVERY tile that draws it is WHOLLY OUTSIDE the 4:3 picture, and no tile
#     inside the picture samples it, so there is no original to protect;
#   * the paletted source cell is ENTIRELY index 0 (`zero.all()`, enforced at
#     the arm itself) -- nothing was cut out of anything;
#   * the mod's alpha says it PAINTS there.
#
# Then `out[zero & tm] = FN.EMPTY` puts the key back at the MOD'S resolution
# and on the MOD'S alpha, and the colour is already the mod's at 512. That is
# what FFNx draws, texel for texel.
#
# WHY THIS AND NOT `ff7nx_marginart.MARGIN_LAYERS_2PLUS`. The previous attempt
# (build 114) wrote these cells in the margin-art pass, which quantises the
# .dds to 16x16 8-bit indices against the tile's palette. That put a 16x16
# 1-bit mask under a 512px colour -- `zero` is upscaled by `_up` and drives
# the key -- so the mesh came back at a quarter of the resolution the colour
# did: the speckled fence photographed on `mds7plr1`. Worse, writing indices
# there makes `zero.all()` false, which disables this arm and the atlas-gap
# arm both. The two changes are mutually exclusive and this is the one with
# the resolution.
#
# `SEVENTH_NX_NO_MARGIN_L2_ALPHA=1` restores build 113 exactly.
MARGIN_OVERLAY_ALPHA = os.environ.get('SEVENTH_NX_NO_MARGIN_L2_ALPHA') != '1'

# THE OVERLAY EDGE IS ERODED BACK TO THE UNIT GRID. FINDINGS-247.
#
# THE DEFECT, MEASURED. `_ksplitkey` classified every layer-2 depth-2 unit on
# fship_2 / mtcrl_4 / mtcrl_5 / mds7plr1 / mrkt2 against Cosmos's own alpha,
# resolved through ORIGIN:
#
#     whole-unit disagreement   3,463 units   0.4% of 799,744
#     sub-unit (MIXED) units   50,196         6.3%
#     ...of the mixed ones we KEY 42,481 and DRAW 7,715
#
# We almost never key the wrong UNIT. The entire defect is sub-unit, and the
# direction is the tell: where the mod cuts a unit partially we key 85% of
# them, i.e. we throw the WHOLE unit away. So an overlay's silhouette erodes
# back to the unit grid and reveals what is beneath it in 3-screen-pixel
# bites. That is exactly why it is an OVERLAP artefact: on layer 1 there is
# nothing behind, so erosion is invisible; on layer 2 over layer 1 it is the
# blockiness along the girders of `fship_2`, the path in `wcrimb_2` and the
# rail in `mtcrl_4`. The same population `_kedge` counts as 337,548 LOST
# texels on layer 2 across 119 fields.
#
# THE FIX IS ONE PREDICATE AND IT WAS ALREADY WRITTEN. The atlas-gap arm
# below already does `out[zero & tm] = FN.EMPTY` -- key only where the mod is
# ALSO clear -- and `tm` is already resampled to the destination shape. It was
# simply gated to `zero.all()` cells. This extends it, and ONLY it.
#
# SCOPED TO THE MIXED UNITS, WHICH IS NARROWER THAN FINDINGS-247 ASKED FOR
# AND IS THE WHOLE SAFETY ARGUMENT. A unit is refined only where the mod's
# alpha is NON-UNIFORM across it. Three consequences fall out by construction
# rather than by measurement, and each one closes a way this could go wrong:
#
#   1. NO WHOLE-UNIT CHANGE. A unit the mod paints entirely, and a unit the
#      mod leaves entirely clear, are keyed exactly as they are today. The
#      0.4% whole-unit population is untouched, so this cannot turn an
#      overlay into an opaque rectangle and cannot fabricate a hole.
#   2. NO CELL LOSES ITS KEY. `mixed` means `tm` is True SOMEWHERE in the
#      unit, so `zero & tm` keeps at least one keyed texel in every unit that
#      is keyed today. Every per-cell consumer asking "does this cell use the
#      key" reads the same answer it read before.
#   3. MONOTONE. `zero & tm` is a strict SUBSET of `zero`, so the change can
#      only ever UN-key a texel, never key one vanilla did not. Nothing that
#      draws today stops drawing.
#
# The residual risk is the mirror of 3 -- an un-keyed texel is opaque and an
# opaque texel can HIDE something -- and it is bounded to the texels of a
# boundary unit where the mod's own alpha says it PAINTS. That is the
# occlusion census in `_ksubgate`, not an argument.
#
# THE CEILING, STATED HONESTLY. At `page_px` 512 a unit is 2x2 destination
# texels against the mod's 4x4, so this captures HALF the available sub-unit
# detail and halves the step from one unit to half a unit. At 768 it is 3x3
# and three quarters. It is a real improvement, not a cure, and it is the
# reason 768 is worth re-enabling AFTER this rather than instead of it -- the
# two compound, where 768 alone just sharpens the tiles either side of a
# still-blocky boundary.
#
# AND IT KEYS ON `hmask`, NOT `tmask`. The atlas-gap arm uses `tmask`
# (alpha < 8) because it asks a per-CELL question -- "does the mod paint in
# this cell at all". Per TEXEL the paranoid threshold is a defect: it calls a
# 4%-alpha texel fully painted, and Cosmos draws a DARK OUTLINE along the
# boundary of every overlay, so drawing those opaque puts a black fringe one
# texel wide around the thing this change exists to sharpen. MEASURED on the
# newly-opaque texels: 45% of `mtcrl_4`'s, 27% of `mtcrl_5`'s and 21% of
# `wcrimb_2`'s sit at alpha 8..127. `PageArt.hmask` is the 50% rule and it is
# what this arm reads.
#
# INERT AT 256. `scale` is 1 there, a unit is one texel, no unit can be
# mixed, and the guard below refuses before the reshape.
#
# `SEVENTH_NX_NO_SUBUNIT_KEY=1` restores build 118 exactly.
SUBUNIT_KEY = os.environ.get('SEVENTH_NX_NO_SUBUNIT_KEY') != '1'

# BUILD 121 -- THE MOD'S ALPHA IS THE AUTHORITY ON ITS OWN SILHOUETTE.
# FINDINGS-253. THIS IS THE OPPOSITE DIRECTION FROM SUBUNIT_KEY ABOVE.
#
# `source_cell` writes the key from VANILLA'S INDEX (`out[zero] = FN.EMPTY`,
# `zero` being `idx == 0`) but fills mod-transparent texels from the VANILLA
# PIXEL (`out[tm] = pal_ref[tm]`). A texel where Cosmos says "nothing is here"
# and vanilla's index is NOT 0 therefore falls through both arms: not keyed,
# because `zero` is false, and painted with vanilla's own colour. On the
# Highwind's hull that colour is the 1997 art's hard black outline.
#
# THAT IS WHY ONE SILHOUETTE IS PERFECT IN PLACES AND BLACK-STEPPED IN OTHERS.
# Where the old outline pixel happened to be index 0 we key it and the edge is
# clean; where it was a dark NON-zero index we paint it and it reads as a
# stair-step. Same edge, same mod art, different vanilla index.
#
# MEASURED inside `source_cell`, keyed cells only, `fship_1`:
#
#     texels where the MOD IS CLEAR (tmask, alpha < 8)   312,897
#       ...we KEY them, correctly                        307,052   98.1%
#       ...we DRAW them from the vanilla fallback          5,845    1.9%
#          of those, NEAR-BLACK                            2,683
#
# `_kedge.py` named this years of builds ago: "extra -- our key says OPAQUE,
# the mod paints NOTHING. The FAT edge. This is the girder stair-step: we draw
# scenery over sky." Build 119's gate measured `extra` at 382,788 texels
# archive-wide and only required that it not RISE. This is the build that
# makes it FALL: 1,776,580 -> 886,009 over 681 fields, measured through
# ORIGIN by `_kmodgate.py`.
#
# AND IT KEYS ON `tmask`, NOT `hmask`, AND THAT IS THE OPPOSITE OF BUILD 119.
# Each direction wants its own conservative end of the alpha range:
#
#   SUBUNIT_KEY  UN-keys, so it must be sure the mod really PAINTS
#                -> `hmask`, alpha >= 128, the 50% rule.
#   MODCLEAR_KEY ADDS key, so it must be sure the mod really paints NOTHING
#                -> `tmask`, alpha < 8.
#
# Using `hmask` here would key everything below half alpha and eat the
# silhouette; using `tmask` there would draw a 4%-alpha texel opaque and put
# build 116's black fringe back. The two thresholds are not interchangeable
# and the asymmetry is the safety argument, not an inconsistency.
#
# SCOPE, and every term is load-bearing:
#   * layer 2+ only (`rec['l2']`) -- on layer 1 index 0 is drawn, not a
#     cut-out, and there is by definition nothing behind it;
#   * `art is not None` -- `tm` does not exist in the vanilla branch;
#   * `tm.shape == out.shape` -- a shape mismatch means the art slice missed;
#   * inside `rec['key']` already, so a cell that is not a cut-out is
#     untouched.
#
# `MODCLEAR_WHOLE` is a SEPARATE, tighter question: a cell the mod leaves
# ENTIRELY clear (`tm.all()`) would become entirely key. That is a whole cell
# of reveal rather than a boundary of it, so it is gated on its own and off.
# MEASURED: it never fires on this archive anyway -- `modclear_whole` is 0
# across all 681 fields -- so turning it on would be a change with no
# population and it is kept off for the day one appears.
#
# `SEVENTH_NX_NO_MODCLEAR_KEY=1` restores build 120 exactly.
MODCLEAR_KEY = os.environ.get('SEVENTH_NX_NO_MODCLEAR_KEY') != '1'
MODCLEAR_WHOLE = os.environ.get('SEVENTH_NX_MODCLEAR_WHOLE') == '1'

# ...AND ONLY WHERE THE TEXEL WE WOULD OTHERWISE PAINT IS BLACK.
# THIS TERM IS THE ENTIRE SAFETY ARGUMENT OF BUILD 121. DO NOT REMOVE IT
# WITHOUT A PER-TEXEL COVER MASK IN `dense_repack`.
#
# HANDOFF-254 s4.5 reported the reveal risk as measuring at ZERO on four
# fields: `rec['bare']` said 100% of the newly-keyed texels had something
# drawing behind them. `bare` IS PER CELL. s4.5 said so and asked the next
# session to build the per-TEXEL version. `_kreveal.py` is that census, and
# the per-cell answer was hiding a real population:
#
#     field       newly keyed   something behind   NOTHING behind
#     fship_1         5,845       5,845  100.0%        0    0.0%
#     fship_2        15,423      15,423  100.0%        0    0.0%
#     mtcrl_4        29,921      25,909   86.6%    4,012   13.4%
#     wcrimb_2        6,791       6,683   98.4%      108    1.6%
#
# 4,120 texels of framebuffer, in the two fields Patrick names most often.
# "Something draws at this cell's destinations" really does not prove cover
# at every texel inside the cell, and shipping on the per-cell number would
# have traded a black outline for a black hole on the Mt. Corel track --
# which is exactly the loss FINDINGS-253 s5 said would make this a clear
# regression.
#
# THE PER-TEXEL COVER MASK IS THE RIGHT FIX AND IT IS NOT AVAILABLE HERE.
# `source_cell` works in SOURCE cell space; a cell can be drawn by many
# tiles at many destinations, and the raster that answers "is this texel
# covered" lives in `dense_repack`'s caller. Building it is a real change to
# the inner loop with its own cost and its own interaction with
# `field_bg_compact`. It is the next build, not this one.
#
# SO THIS BUILD TAKES THE SUBSET WHERE THE QUESTION CANNOT ARISE.
#
#   Key only where the colour we would otherwise paint is ALREADY BLACK.
#
# and the argument is closed rather than measured:
#
#   * something behind  -> we replace a black texel with the art that
#     belongs there. That is the whole prize, and it is the defect Patrick
#     reported by name -- "black steps", "black parts".
#   * NOTHING behind    -> we replace a near-black texel with the
#     framebuffer, which is black. The picture does not change. It cannot
#     open a hole, because there was nothing there to lose.
#
# In other words the change is MONOTONE IN THE PICTURE: it can only ever
# turn black into not-black, never art into black. That is a stronger
# guarantee than any cover census can give, and it does not depend on a
# raster this function cannot see.
#
# MEASURED ARCHIVE-WIDE with the term on (`_kreveal.py --summary`, 681
# fields): 890,571 texels newly keyed, 855,565 of them revealing art and
# 35,006 revealing the framebuffer -- and the most any one of those 35,006
# loses is 33 of 255, because every one of them was black to begin with.
# `art replaced by black` is 0. Without the term the same run put 4,954
# texels of black where art is today.
#
# THE THRESHOLD is the same one every speck census in this project uses --
# max channel < 40 of 255, `_ksubgate.DARK`, `_kgapmeasure`, `_kreveal`.
# It is applied to the FINAL R5G6B5 colour, after the NEAR_BLACK lift, so a
# texel that merely rounds to the key is inside it rather than beside it.
MODCLEAR_DARK_ONLY = os.environ.get('SEVENTH_NX_MODCLEAR_ALL') != '1'
MODCLEAR_DARK = int(os.environ.get('SEVENTH_NX_MODCLEAR_DARK') or 40)

# BUILD 123 -- ...OR WHERE SOMETHING PROVABLY DRAWS BEHIND IT. FINDINGS-257.
#
# THE DEFECT THAT IS LEFT IS NOT BLACK, IT IS BLOCKY. Patrick, after 122:
# "still kind of choppy". The crops are stair-steps, not dark pixels, and
# `_kstep.py` says why -- of the boundary UNITS on `fship_1` and `fship_2`,
# 39% and 31% are still UNIFORM, i.e. the silhouette snaps to the unit grid.
# A unit is one game pixel and the screen shows it at 4 screen pixels, so a
# uniform boundary unit is a 4-pixel stair-step no matter how good the art is.
#
# WHY THEY ARE STILL UNIFORM. Build 119 refines a unit where VANILLA says
# transparent and the mod cuts it (`keep = zero & (_clear | ~mixed)`). Where
# vanilla says OPAQUE and the mod cuts the unit, `zero` is False, `keep` is
# False, and we draw the whole unit -- the fat edge, at unit resolution.
# Build 121 keys exactly that population, but only where the texel is already
# BLACK, so the non-black half of every such boundary stayed blocky.
#
# LIFTING THE DARK RESTRICTION OUTRIGHT IS NOT SAFE, AND THE SHIPPED BUILD
# IS THE PROOF. Same census, same fields, `_kreveal.py`:
#
#                            build 121 (shipped)   unrestricted
#     newly keyed                     28,805           57,980
#       static cover                   1,961            3,709
#       parallax behind it            14,429           32,600
#       NOTHING AT ALL                12,415           21,671
#         ...of those, NOT black           0            9,248
#
# Build 121 put 12,415 texels over literal framebuffer and not one of them
# was visible, because every one was already black -- which is why hardware
# came back clean. The unrestricted predicate would put **9,248 texels of
# real colour** over framebuffer on four fields. Those are holes.
#
# SO THE RULE IS THE UNION OF TWO SAFE CASES, NOT THE REMOVAL OF ONE:
#
#     key where the mod is clear AND (the texel is already black
#                                     OR something provably draws behind it)
#
# and the second arm is what `backdrop_keys` returns as `rec['cover']`. It
# INCLUDES the parallax, and that is the one place in this file where layers
# 3 and 4 are allowed to matter. The reason is that the question changes:
#
#   for COLOUR (the blend, build 122) a scrolling backdrop is useless,
#     because the pixel behind a texel is different at every camera position;
#   for the KEY it is decisive, because "does ANYTHING draw here" has the
#     same answer at every camera position for a backdrop that wraps.
#
# Keying a texel over the parallax shows the sea behind the Highwind -- which
# is what the reference renderer shows, since FFNx simply does not draw a
# texel the mod leaves clear. Keying one over nothing shows the framebuffer,
# and that is only acceptable when it was black to begin with.
#
# `SEVENTH_NX_NO_MODCLEAR_COVER=1` restores build 122 exactly.
MODCLEAR_COVER = os.environ.get('SEVENTH_NX_NO_MODCLEAR_COVER') != '1'

# BUILD 124 -- "THE MOD PAINTS NOTHING" IS A **MAX**, NOT A MEAN.
# FINDINGS-258. THIS IS THE FENCE.
#
# Patrick, on `mds7plr1` after 123: the fence is much better, but the upper
# left corner of it has BRIGHT GREEN SPECKS -- and bright green is what is
# behind the fence. We keyed the wire.
#
# `PageArt.tmask` is `alpha < 8` computed AFTER `resample_rgba`, which is an
# alpha-weighted BOX filter. So it asks "is the AVERAGE coverage of this
# texel below 3%". For a thin structure that question has the wrong answer:
# `mds7plr1`'s fence wire is about one native pixel wide, 1024 -> 768 puts
# ~1.8 native pixels in a destination texel, and a wire crossing a corner
# averages under the threshold. `tmask` then says "the mod paints nothing
# here" about a texel whose native art is FULLY OPAQUE.
#
# MEASURED against the native DDS, over the texels the mod-clear arm keys:
#
#     field       keyed at alpha<8    native art present    native OPAQUE
#     mds7plr1        396,178             8,508  2.1%        8,508  2.1%
#     fship_2         641,489             3,968  0.6%        3,968  0.6%
#
# `present` and `OPAQUE` are the SAME NUMBER. There is no soft-edge
# population in here at all, which is the signature of a thin structure lost
# to a box filter rather than of an anti-aliased boundary -- and it is why
# this is safe to fix without touching the black-edge work: the texels being
# taken back are ones where the mod paints at full strength, not ones on the
# fat edge. 98-99% of the mod-clear population is untouched.
#
# TWO CONSUMERS, AND BOTH WERE WRONG IN THE SAME DIRECTION.
#
#   1. the key. `_mck` now reads `amax < 8` -- the mod paints nothing
#      ANYWHERE in the texel's footprint -- instead of `tm`.
#   2. THE VANILLA FALLBACK, and this one matters just as much. `source_cell`
#      does `out[tm] = pal_ref[tm]`: where the mod is transparent, take the
#      VANILLA pixel. On a wire texel that overwrites the mod's own colour --
#      which the alpha-weighted resample got RIGHT, it is the wire's colour --
#      with the 1997 art. So simply un-keying those texels would hand them
#      back to vanilla and put the old fence there. Both arms have to read
#      the same predicate or the fix trades a green speck for a grey one.
#
# `SEVENTH_NX_NO_PAINT_MAXPOOL=1` restores build 123 exactly.
PAINT_MAXPOOL = os.environ.get('SEVENTH_NX_NO_PAINT_MAXPOOL') != '1'

# BUILD 122 -- BAKE THE BLEND THE 1-BIT KEY CANNOT DO. FINDINGS-255.
#
# After build 121 the black left on a silhouette boundary splits in two, and
# `_kresid.py` measured the split on the four fields Patrick reports:
#
#     the mod's alpha there    fship_1   fship_2   wcrimb_2   mtcrl_4
#     255  (opaque)              68.7%     65.4%      67.2%     77.1%
#     250..254                    3.3%      3.9%       0.3%      2.9%
#     128..249                   28.1%     30.7%      32.5%     20.1%
#
# THE FIRST TWO ROWS ARE NOT OURS AND THIS FLAG MUST NOT TOUCH THEM. Checked
# against the mod's own decoded art, the shipped texel is byte-identical in
# 100.0% of cases, and checked against the NATIVE 1024px DDS the alpha is
# 255.0 and the colour is already dark -- mean max channel 16.6 on `fship_1`,
# 18.6 on `fship_2`. Cosmos outlines the Highwind's plating with a hard,
# fully-opaque dark line. FFNx draws that line exactly as we do because there
# is nothing to decide. It is the art.
#
# THE THIRD ROW IS OURS. Those texels are ones FFNx BLENDS and we draw at full
# strength, because a truecolor page in this format has a 1-bit colour key and
# no alpha channel. `PageArt.hmask`'s 50% rule is the honest 1-bit reduction
# of an 8-bit alpha and it is already what the code uses -- but "right on
# average" and "invisible" are different claims. A hard rim at 100% where the
# reference shows 55% is spatially COHERENT: it traces the whole silhouette,
# which is exactly why the eye finds it and why the average error does not
# predict how bad it looks.
#
# AND FLIPPING THE THRESHOLD IS NOT THE ANSWER. At alpha 200 drawing is 20%
# wrong and keying is 80% wrong; keying the band would trade a rim that is too
# dark for a silhouette eaten by a texel, which is build 116 in the other
# direction. There is exactly one version of this that is right rather than
# differently wrong:
#
#     out = alpha * mod + (1 - alpha) * whatever draws underneath
#
# which is the pixel FFNx produces. We cannot ship alpha; we can ship its
# RESULT, because the destination is truecolor. **No key moves, no page moves,
# no tile moves, no byte of section 9 changes length. This is colour only.**
#
# WHAT IT NEEDS is the thing FINDINGS-254 already named as the missing piece:
# what is behind, per texel. `backdrop_keys()` computes it, and its five
# refusal conditions are the whole safety of this arm -- read them there
# before changing anything here.
#
# SCOPED TO DARK TEXELS, for build 121's reason and it is the same reason.
# A blend can only move a dark rim TOWARD the background, so the change stays
# MONOTONE IN THE PICTURE: black can become art, art cannot become black. If
# the backdrop were somehow wrong, the damage is bounded to lightening a dark
# texel toward a neighbouring colour -- which is a far smaller error than
# darkening art, and it is the asymmetry that makes this safe to ship without
# a per-camera-position proof.
#
# THE BAND. `BLEND_MIN` is 8 -- below that `tmask` says the mod paints
# nothing and build 121's arm owns the texel. `BLEND_MAX` is 250 rather than
# 255 because 250..254 is 3% of the population and rounds to the same colour
# anyway; excluding it keeps this arm off anything that is effectively opaque.
#
# `SEVENTH_NX_NO_BLEND=1` restores build 121 exactly.
BLEND_PARTIAL = os.environ.get('SEVENTH_NX_NO_BLEND') != '1'
BLEND_MIN = int(os.environ.get('SEVENTH_NX_BLEND_MIN') or 8)
BLEND_MAX = int(os.environ.get('SEVENTH_NX_BLEND_MAX') or 250)
BLEND_DARK = int(os.environ.get('SEVENTH_NX_BLEND_DARK') or 40)


def _rgb8_565(a):
    """R5G6B5 -> (r, g, b) uint16 planes, by the engine's bit replication."""
    u = a.astype(np.uint16)
    r = (u >> 11) & 31
    g = (u >> 5) & 63
    b = u & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _565_rgb8(r, g, b):
    """(r, g, b) 8-bit -> R5G6B5, `field_bg_native.rgb_to_565`'s arithmetic.

    Rounds onto the level*8 grid the engine reconstructs (`c * 0.125 + 0.5`,
    i.e. `(c + 4) // 8`), keeps GREEN'S LOW BIT ZERO by building green as a
    5-bit value shifted up -- x86 0x63F350 ORs that bit onto BLUE on this
    port, which is `_pal_rgb`'s light-purple Sector 6 patches -- and lifts a
    result of 0x0000 off the colour key.
    """
    q = lambda c: np.minimum(np.uint16(31),                     # noqa: E731
                             (c.astype(np.uint16) + 4) // 8)
    v = ((q(r) << 11) | ((q(g) << 1) << 5) | q(b)).astype(np.uint16)
    v[v == FN.EMPTY] = FN.NEAR_BLACK
    return v


def _maxchan565(a):
    """Max 8-bit channel of an R5G6B5 array, by the engine's own expansion.

    Bit replication, not a shift -- `_seam._rgb565` and `field_bg_native`
    both spell this out, and it is what makes NEAR_BLACK's R1 G2 B1 land on
    exactly (8, 8, 8) instead of (8, 8, 8)-ish. The exactness matters here
    because NEAR_BLACK is precisely the population this predicate is about.
    """
    u = a.astype(np.uint32)
    r = (u >> 11) & 31
    g = (u >> 5) & 63
    b = u & 31
    r8 = (r << 3) | (r >> 2)
    g8 = (g << 2) | (g >> 4)
    b8 = (b << 3) | (b >> 2)
    return np.maximum(np.maximum(r8, g8), b8)


# THE 32-UNIT TILE VETO -- NOW OFF BY DEFAULT, BECAUSE THE HANDLING EXISTS.
#
# HISTORY, because the default flipping is the whole of HANDOFF-192 5.1.
#
# Build 87 added this veto after build 84 promoted 32-unit parallax cells as
# if they were 16-unit ones: a 16-aligned destination, a 16x16 copy, and a
# destination page emitted with `size_flag = 0`. Wrong three ways at once, and
# the result was a checkerboard of art and colour key on the backdrop of 84
# fields -- Mt. Corel, the Highwind, Honey Bee Inn, the Midgar overlook.
# Vetoing was the right emergency fix and it cost colour DEPTH and nothing
# else, because those cells keep Cosmos's art on their own paletted page,
# which `ff7nx_marginart` has already written.
#
# But it left the port a long way from the goal. MEASURED on build 91:
#
#     32-unit tiles              16,917  in 83 field(s)
#       still on a depth-1 page  16,829  in 82 field(s)     99.5%
#     16-unit tiles             695,693
#       still on a depth-1 page 139,817                     20.1%
#
# Essentially every parallax tile in the game was 8-bit while four fifths of
# the rest of the screen was truecolor -- and truecolor promotion is this
# archive's only equivalent of FFNx's per-(page, palette) DDS replacement, so
# the veto was the single biggest remaining gap.
#
# All three of build 84's mistakes are now fixed rather than avoided: a
# 32-unit cell gets a destination page of its own with `size_flag = 1`, an
# 8x8 grid, a 32x32 copy, and a uv computed at that grid. `test_bigtile.py`
# asserts every one of those on the archive and passes on vanilla with zero
# exceptions, so a regression to build 84's behaviour is now a failing test
# rather than a photograph from hardware.
#
# `SEVENTH_NX_BIGTILE_VETO=1` puts the veto back, i.e. reproduces build 91.
# That is the A/B, and it is the switch to reach for first if the backdrops
# come back wrong.
BIG_TILE_VETO = os.environ.get('SEVENTH_NX_BIGTILE_VETO') == '1'


# THE PROMOTION MAP. `{field: {tile_offset: (slot, sx, sy, pal)}}`
#
# Exactly the same problem `ff7nx_marginpage.ORIGIN` solves, one level down and
# for a bigger population. This function promotes a cell from its paletted page
# onto a dense truecolor page: slot 1 (sx, sy) becomes slot 26 (dx, dy). Cosmos
# names its art `<field>_<page>_<pal>.dds` against the ORIGINAL page, so every
# later pass that asks `art_for(26, pal)` gets None and cannot tell "the mod
# ships nothing here" from "this cell moved".
#
# MEASURED (HANDOFF-188 3.2): `trnad_3`'s 7 remaining black tiles are on slot
# 26 and `marginpage.ORIGIN` records nothing for them -- marginpage creates a
# LOW slot, and slot 26 is reached through this promotion. Same for all of
# `mtcrl_5`'s visible holes, which sit on slots 13/14.
#
# KEYED ON THE TILE, NOT ON THE DESTINATION CELL, and that is the whole reason
# this map is trustworthy where a cell-keyed one would not be:
#
#   * `field_bg_compact` runs AFTER this function, merges byte-identical cells
#     and renumbers pages -- so (dst_slot, dx, dy) is not stable, but the tile
#     record it repoints is;
#   * build.py retries this function with a falling truecolor ceiling and can
#     DISCARD the result (`raw_capped`) -- in which case the tile still points
#     at its original page, and a per-tile entry saying "your art is at the
#     original page" is then simply true rather than stale;
#   * a tile record's byte offset inside section 9 does not move: the layer
#     block is before the texture block and neither pass adds or removes
#     tiles.
#
# The entries are CHAINED THROUGH marginpage: if the cell this promoted was
# itself already moved by the margin split, what is recorded here is the page
# Cosmos actually shipped, not the intermediate one. A consumer therefore never
# has to know how many hops happened.
ORIGIN = {}


class Stats:
    __slots__ = ('hue_kept_art',
                 'cells', 'pages', 'tiles', 'from_art', 'from_art_borrow',
                 'from_vanilla', 'keyed', 'fx_pairs', 'refused',
                 'pages_before',
                 'borrow_refused', 'origin', 'atlas_gap', 'bare',
                 'margin_l2', 'margin_l2_filled',
                 'subunit_cells', 'subunit_units', 'subunit_texels',
                 'modclear_cells', 'modclear_texels', 'modclear_whole',
                 'blend_cells', 'blend_texels', 'backdrop_cells',
                 'wire_texels')

    def __init__(self):
        self.cells = self.pages = self.tiles = 0
        self.from_art = self.from_art_borrow = self.from_vanilla = 0
        self.keyed = self.fx_pairs = 0
        self.pages_before = 0
        self.borrow_refused = 0
        self.hue_kept_art = 0
        self.refused = None
        self.origin = {}
        self.atlas_gap = 0        # cells whose atlas gap took the mod's art
        self.bare = 0             # cells with nothing behind them at all
        self.margin_l2 = 0        # layer-2+ widescreen margin placeholders
        self.margin_l2_filled = 0     # ...of those, served the mod's alpha
        self.subunit_cells = 0    # layer-2 cut-outs refined below unit size
        self.subunit_units = 0    # ...units in them the mod cuts partially
        self.subunit_texels = 0   # ...texels that stopped being keyed
        self.modclear_cells = 0   # cut-out cells where the mod paints nothing
        self.modclear_texels = 0  # ...texels that STARTED being keyed
        self.modclear_whole = 0   # ...cells the mod leaves entirely clear
        self.blend_cells = 0      # cells with a baked partial-alpha blend
        self.blend_texels = 0     # ...texels blended toward the backdrop
        self.backdrop_cells = 0   # cells backdrop_keys() could judge
        self.wire_texels = 0      # thin-structure texels the box filter lost


def _pal_rgb(sec3):
    """
    Section-3 palettes as R5G6B5, one entry per index.

    THE GREEN LSB MUST BE ZERO -- see field_bg_native line 147. When the
    display surface is not 5:6:5, and on this port it is not, the engine
    converts every depth-2 pixel with x86 0x63F350:

        out = ((in & 0xF800) >> 1) | ((in & 0x07E0) >> 1) | (in & 0x1F)

    Six bits of green shifted into a five-bit field puts green's low bit on
    bit 4 -- the top bit of BLUE -- ORed onto the real blue. Blue gains +16 of
    31 whenever green is odd, at random, per pixel. MEASURED in that file:
    RGB(160,140,90) arrives with blue 27 instead of 11.

    This function used to expand 5-bit green with `(g << 1) | (g >> 4)`, which
    SETS that bit for every green >= 16 -- most of a khaki or olive scene. The
    result is a heavy blue cast with per-pixel noise: the light purple patches
    in Sector 6. `FN.rgb_to_565` masks it with GREEN_LSB; this hand-rolled
    conversion did not.
    """
    cols, hdr, npg, cpp = MB.palette_colours(sec3)
    v = cols.astype(np.uint32)
    r = (v & 31); g = (v >> 5) & 31; b = (v >> 10) & 31
    r5 = r.astype(np.uint16)
    g6 = (((g << 1) | (g >> 4)) & FN.GREEN_LSB).astype(np.uint16)
    b5 = b.astype(np.uint16)
    return ((r5 << 11) | (g6 << 5) | b5).astype(np.uint16), npg, cpp


# PROMOTE THE CELLS QUANTISATION IS PROVABLY FAILING, FIRST. FINDINGS-149.
#
# The candidate order was `-len(tiles)` alone -- how many tiles reuse a cell.
# That is a density heuristic and it never asks whether the paletted version
# is any good. MEASURED across 38 fields (diag_huebudget.py): 40.5% of margin
# layer-1 cells are quantisation failures, and 15.8% are ORPHANED -- no
# palette in the field is within 0.10 of their hue, so no palette choice and
# no page split can ever fix them. Only a truecolor page can, because it has
# no palette at all. That is why FFNx has none of these defects: it never
# applies one (FINDINGS-141).
#
# The two routes were priced on the same cells:
#     split into more paletted pages   5,439 extra page(s) archive-wide
#     promote to truecolor               538 extra page(s), <= 1 per field
#
# And it may cost nothing: the repack already promotes 363,503 cells, and the
# broken cells are ~40,000 of them, so ordering them first largely fixes the
# defect inside the budget already being spent.
HUE_FIRST = True
# Same units and calibration as ff7nx_marginpal's gates (FINDINGS-148): the
# known-answer cases sit at 0.000 (right) and 0.048 (wrong).
HUE_BROKEN_DIST = 0.030


def _chroma(rgb):
    v = np.asarray(rgb, float)
    s = float(v.sum())
    return v / s if s > 1e-6 else np.zeros(3)


def hue_broken(k, arrays, pal565, art_for, _cache=None, origin=None):
    """
    Chromaticity distance between Cosmos's ART for this cell and what the
    PALETTED page actually renders. 0.0 when it cannot be measured.

    THE DECODE IS R5G6B5 AND THAT IS NOT SECTION 3's LAYOUT. `pal565` packs
    (v>>11)&31 / (v>>5)&63 / v&31 -- see `_pal_distance`, which is where this
    is established. Section 3 is BGR555, (v&31)/((v>>5)&31)/((v>>10)&31).
    Decoding one with the other silently yields a plausible-looking wrong
    colour, which is exactly the kind of error this project keeps paying for.
    """
    if art_for is None:
        return 0.0
    slot, sx, sy, pal = k
    # Clamp, do not bail: an out-of-range palette byte is common (see
    # black_fraction) and returning 0.0 would score the cell "sound" for a
    # reason that has nothing to do with its colour.
    if pal >= len(pal565):
        pal = len(pal565) - 1
    if pal < 0:
        pal = 0
    if _cache is None:
        _cache = {}
    # ASK THE PAGE THE CELL CAME FROM. FINDINGS-150.
    #
    # `ff7nx_marginpage` moves margin cells onto pages Cosmos never shipped
    # and REPACKS their coordinates -- slot 1 (sx, sy) becomes slot 3
    # (dx, dy). Asking `art_for(3, pal)` returns None, and the first version
    # of this scored that 0.0, i.e. "sound". It was measured: all 40 of
    # mds5_5's margin sky cells went from 40/40 BROKEN before the split to
    # 0/40 after it, which silently un-did the entire build-60 fix.
    #
    # The art still exists -- at the ORIGIN page and the ORIGIN coordinates.
    # The rendered side keeps using the destination, because that is what the
    # screen actually draws.
    aslot, asx, asy = slot, sx, sy
    if origin:
        _o = origin.get((slot, sx, sy))
        if _o:
            aslot, asx, asy = _o
    ck = (aslot, pal)
    if ck not in _cache:
        # FALL BACK TO PALETTE 0 WHEN THE EXACT PALETTE IS NOT SHIPPED.
        #
        # THIS IS WHY THE FIRST VERSION MEASURED ZERO ON mds5_5. `marginpal`
        # had repointed that page to palette 1; Cosmos ships only `_00`, so
        # `art_for(slot, 1)` returned None and every cell scored 0.0 -- the
        # detector reported "nothing broken" about the very page whose sky
        # the user photographed. A missing dump is not evidence of a sound
        # cell.
        #
        # `source_cell` already does this, and quotes FFNx's own rule for it
        # (saveload.cpp:138, load_normal_texture falls back to palette 0).
        got = None
        for _p in (pal, 0) if pal != 0 else (pal,):
            try:
                got = art_for(aslot, _p)
            except Exception:                                  # noqa: BLE001
                got = None
            if got is not None:
                break
        img = None
        if got is not None:
            img = got[0] if isinstance(got, tuple) else got
        _cache[ck] = img
    img = _cache[ck]
    if img is None:
        # UNMEASURABLE, WHICH IS NOT THE SAME AS SOUND. Counted so the log
        # can say how often the detector was blind rather than satisfied --
        # three separate bugs this session were "returned 0.0 because it
        # could not look" reading as "this cell is fine".
        hue_broken.unmeasured = getattr(hue_broken, 'unmeasured', 0) + 1
        return 0.0
    # `art_for` hands back a PageArt, NOT an ndarray -- its `.buf` is the page
    # already packed as 565 at `.px`, which is the SAME encoding as `pal565`,
    # so both sides decode identically below. (The first version of this
    # indexed `.shape` and died in the harness; that is what the harness is
    # for.)
    try:
        f = img.px // 256
        page = np.frombuffer(img.buf, '<u2').reshape(img.px, img.px)
    except Exception:                                          # noqa: BLE001
        return 0.0
    if f < 1:
        return 0.0
    av = page[asy * f:(asy + TILE) * f, asx * f:(asx + TILE) * f].reshape(-1)
    av = av.astype(np.int64)
    b = np.stack([((av >> 11) & 31) << 3,
                  ((av >> 5) & 63) << 2,
                  (av & 31) << 3], -1).astype(float)
    b = b[b.max(1) > 24]
    if not b.size:
        return 0.0
    idx = arrays[slot][sy:sy + TILE, sx:sx + TILE]
    v = pal565[pal][idx].astype(np.int64).reshape(-1)
    col = np.stack([((v >> 11) & 31) << 3,
                    ((v >> 5) & 63) << 2,
                    (v & 31) << 3], -1).astype(float)
    col = col[(idx.reshape(-1) != 0) & (col.max(1) > 24)]
    if not col.size:
        return 0.0
    return float(np.linalg.norm(_chroma(b.mean(0)) - _chroma(col.mean(0))))


def _pal_distance(pal565, pal, idx):
    """
    Mean |RGB| over 0-255 between palette 0 and palette `pal`, measured over
    the PIXELS of `idx` -- every pixel counted once, so an index that covers
    the cell weighs more than one that touches a corner.

    PER PIXEL, NOT PER UNIQUE INDEX. Weighing unique indices equally let
    `nmkin_5` dst (1, 16, 112) through at 30.4 when its rendered colour moves
    RGB(136,13,13) -> (5,15,21): the red dominates the cell but is only one
    entry of many, so averaging over entries buried it. Counting pixels puts
    that cell at 44.5 and refuses it, which is what the screen says should
    happen.

    Index 0 is excluded: it is the colour key, not art, and `ff7nx_palkey`
    rewrites it independently of either palette.

    Returns 0.0 when the cell draws nothing but the key -- there is no colour
    to get wrong, so a borrow is free.
    """
    u = idx.reshape(-1)
    u = u[u != 0]
    if u.size == 0:
        return 0.0
    a = pal565[0][u].astype(np.int32)
    b = pal565[pal][u].astype(np.int32)
    # R5G6B5 -> 0-255 per channel, matching the numbers in BORROW_MAX_DIST.
    def chans(v):
        return np.stack([((v >> 11) & 31) << 3,
                         ((v >> 5) & 63) << 2,
                         (v & 31) << 3], -1).astype(np.int32)
    return float(np.abs(chans(a) - chans(b)).mean())


def _band_of(slot, depth):
    g = FN._group_of(slot, FN.D1_GROUPS if depth == 1 else FN.D2_GROUPS)
    return 4 if g is None else g


def _uses_key(pages, arrays, k):
    """
    Does this cell use the colour key anywhere?

    THE CELL IS NOT ALWAYS 16 UNITS -- FINDINGS-189, AND THIS IS THE THIRD
    PLACE THE SAME LITERAL HAS BEEN WRONG. `source_cell` takes an explicit
    `edge` for exactly this reason and documents it; `ff7nx_blackcell._art_block`
    was corrected for it in anticipation of the overlay work. This function was
    missed, and because it decides whether the key is RESTORED after promotion
    (line ~1209, `if rec['key'] and ...`), getting it wrong does not blur a
    cell -- it DELETES A CUT-OUT.

    A `size_flag` page is an 8x8 grid of 32-unit cells. Reading a TILE-wide
    window there inspects only the cell's TOP-LEFT QUADRANT, so a cell whose
    keyed texels all lie outside that quadrant reports "no key", the promotion
    does not put the key back, and the cell lands fully opaque.

    MEASURED on `onna_5` layer 4 -- the Honey Bee Inn keyhole mask, and the
    defect the user reported as a flattened circle with a rectangle cut into
    its upper right:

        dst          keyed in top-left 16x16   keyed in the full 32x32
        (-64,-32)          0 / 256                 142 / 1024   KEY THROWN AWAY
        (-64,-64)          0 / 256                  12 / 1024   KEY THROWN AWAY
        ( 32,-64)          0 / 256                   6 / 1024   KEY THROWN AWAY
        (-64, 64)          0 / 256                   5 / 1024   KEY THROWN AWAY
        ( 32,-32)         70 / 256                 111 / 1024   kept
        (-32,-32)        256 / 256                1024 / 1024   kept

    Every cell that lost its key has NOTHING keyed in the quadrant and
    something keyed outside it. That is the whole rule, and it is why three
    cells of one page lost the key while their neighbours on the same page
    kept it.

    The edge is taken from the ARRAY'S OWN SIZE rather than from a constant,
    so it is right at 256px and at 512px without a second literal to keep in
    step. On a non-`size_flag` page it evaluates to 16 and this is a no-op --
    which is what bounds the change.
    """
    slot, sx, sy, pal = k
    p = pages[slot]
    arr = arrays[slot]
    grid = 8 if p.size_flag else 16
    edge = arr.shape[0] // grid or TILE
    return bool((arr[sy:sy + edge, sx:sx + edge] == 0).any())


# ---------------------------------------------------------------- coverage
# ATLAS GAPS, AND WHY "WHICH LAYER" WAS THE WRONG QUESTION. HANDOFF-188 3.1.
#
# `out[zero] = FN.EMPTY` below puts the colour key back on any promoted cell
# whose paletted source is index 0, on the reasoning that a layer-2+ key is a
# real cut-out meant to show what is behind it. That reasoning has a premise
# nobody checked: THAT SOMETHING IS BEHIND IT.
#
# MEASURED on `mtcrl_5` (Mt. Corel, the roller coaster) at build 84:
#
#     layer 1     22 tiles          <- the scenery is NOT on layer 1
#     layer 2   1238 tiles, 358 empty
#     308 of those empties sit where NO other tile draws anything
#
# The camera there scrolls the track past a sky, so Cosmos put the whole
# background on layer 2 and layer 1 holds almost nothing. An empty layer-2
# cell is transparent, and transparent over nothing is the cleared framebuffer
# -- black. That is the grid of black squares reported from hardware, and it
# is the same defect on the Highwind and everywhere else "the background moves
# at a different pace", because those are exactly the fields that put their
# scenery on 2/3/4.
#
# AND THE ART EXISTS. Following the promotion map back to Cosmos's own page
# (see ORIGIN) and asking the mod what it holds at each of those 308 cells:
#
#     mod is PARTIAL here -- fillable, honour alpha    302
#     mod is TRANSPARENT here -- leave alone             6
#     mean coverage 0.529
#
# So this is FINDINGS-174's lesson -- "AN EMPTY ATLAS SLOT IS NOT A CUT-OUT"
# -- one pass later than where it was learned. Cosmos ships sparse atlas pages
# whose empty cells are supplied by the .dds on FFNx; this port read the empty
# 8-bit cell, called it a cut-out, and threw the mod's art away.
#
# THE PREDICATE IS COVERAGE, NOT LAYER, and it is computed here.
def _draws(arr, page, sx, sy):
    """True if this cell has ANY texel that is not the key / not empty."""
    if page.depth == 2:
        s = page.px // 256
        b = arr[sy * s:(sy + TILE) * s, sx * s:(sx + TILE) * s]
        return bool((b != FN.EMPTY).any())
    b = arr[sy:sy + TILE, sx:sx + TILE]
    return bool(b.any())


def bare_keys(pages, arrays, tiles, keys):
    """
    The subset of `keys` whose tiles ALL sit where nothing else draws.

    LAYERS 3 AND 4 DO NOT COUNT AS COVER. They are the parallax backdrops --
    `ff7nx_ws` moves their clip and wrap points precisely because they scroll
    at their own rate -- so a layer-3 tile that happens to share a destination
    with a layer-2 hole is only over that hole at one camera position. Trusting
    it would mean the square is filled when the camera is still and black when
    it moves, which is exactly what "squares pop into existence as I move
    right" describes.

    Layer 1 is counted as cover only where it DRAWS: index 0 on layer 1 is
    rendered, but rendered BLACK, and a black square behind a hole is still a
    black square.
    """
    covered = set()
    for t in tiles:
        if t.layer > 2:
            continue
        p = pages.get(t.slot)
        a = arrays.get(t.slot)
        if p is None or a is None:
            continue
        try:
            if _draws(a, p, t.sx, t.sy):
                covered.add((t.dx, t.dy))
        except Exception:                                      # noqa: BLE001
            covered.add((t.dx, t.dy))          # unreadable -- assume covered
    where = {}
    for t in tiles:
        where.setdefault(t.off, (t.dx, t.dy, t.layer))
    out = set()
    for k, rec in keys.items():
        got = [where.get(off) for off in rec['tiles']]
        if not got or any(g is None for g in got):
            continue
        # A parallax tile's own destination is meaningless as a fixed screen
        # position, so a cell any of whose tiles is on layer 3/4 is not judged
        # here at all -- it keeps today's behaviour.
        if any(g[2] > 2 for g in got):
            continue
        if all((g[0], g[1]) not in covered for g in got):
            out.add(k)
    return out


def backdrop_keys(pages, arrays, tiles, keys, pal565, px):
    """
    `{cell_key: uint16 (n, n)}` -- WHAT DRAWS UNDERNEATH A LAYER-2 CELL.

    BUILD 122. `BLEND_PARTIAL` needs the pixel that is behind a partial-alpha
    texel so it can bake `alpha*mod + (1-alpha)*behind`, which is the pixel
    FFNx produces. `source_cell` works in SOURCE cell space and cannot see a
    destination, so the answer is computed here -- once per field, over tiles
    this function has already read, exactly as `bare_keys` is -- and handed
    down on `rec`.

    FIVE CONDITIONS, AND EVERY ONE OF THEM IS A THING THAT WOULD OTHERWISE
    BAKE THE WRONG COLOUR INTO THE ARCHIVE PERMANENTLY.

      1. EVERY tile that draws the cell is on LAYER 2. Not layer 1, which has
         nothing behind it; and not layers 3/4, whose destination is not a
         fixed screen position at all -- `ff7nx_ws` moves their clip and wrap
         points because they scroll at their own rate, so "what is behind" is
         a different answer at every camera position. `bare_keys` has refused
         to judge them since it was written and this refuses for the same
         reason. A baked blend is worse than a refused one: it is wrong at
         every camera position except the one it was baked for.
      2. SOMETHING DRAWS UNDERNEATH, AND IT IS RECORDED PER TEXEL. Where
         nothing does, the backdrop is the framebuffer and there is nothing
         to blend toward, so the mask comes back False there and the texel
         keeps the colour it has today. This is a mask, not a veto: a cell
         that is covered in part is still worth blending in that part.
      3. THE BACKDROP IS THE COMPOSITE IN DRAW ORDER, AND EACH LAYER-2 TILE
         IS SAMPLED BEFORE IT IS COMPOSITED. Layer 1 is NOT the only thing
         under an overlay and assuming it was is what the first two versions
         of this function got wrong -- `mtcrl_4` has ONE layer-1 tile and
         715 layer-2 tiles, so its backdrop is almost entirely other
         overlays. Sampling after compositing would read the tile's own
         pixels back and report full cover for free.
      4. THE CELL IS REUSED CONSISTENTLY. A cell can be drawn by many tiles
         at many destinations -- that is the entire point of an atlas -- and
         there is ONE copy of its texels. Baking the backdrop of one
         destination corrupts every other. So all of the cell's destinations
         must show the SAME thing underneath, compared on the bytes.
      5. 16-UNIT CELLS ONLY. A 32-unit `size_flag` cell is parallax by
         construction and condition 1 has already excluded it; the guard is
         kept because HANDOFF-189's whole lesson is that assuming 16 is how
         this codebase produces checkerboards.

    ONE APPROXIMATION, STATED RATHER THAN HIDDEN. The raster is built from
    the cells as they are BEFORE this pass blends them, so an overlay that is
    itself blended and then serves as the backdrop for a second overlay above
    it contributes its pre-blend colour. The error is second order -- only
    dark rim texels move, and only toward the thing behind them -- and
    resolving it properly would mean ordering the whole promotion by draw
    order, which no other pass in this file does.

    LAYER 1 COVERS ITS WHOLE TILE. On layer 1 index 0 is not a cut-out -- it
    is rendered, in entry 0's colour -- which this project has proved on
    hardware twice (the Sector 6 yellow, and `PROMOTE_LAYER1_KEY`'s note). So
    the backdrop of a layer-1 tile is its full 16x16, and where the vanilla
    index is 0 the backdrop is entry 0's colour, which is what the screen
    shows there.

    LAST TILE WINS where two layer-1 tiles share a destination, which is
    `dense_repack`'s own `_l1_last` rule and is measured there: 69 of 346,666
    positions carry more than one layer-1 tile. Painting the raster in tile
    order gives that for free.

    AND IT IS A RASTER, NOT A DICTIONARY OF POSITIONS. The first version of
    this function looked the backdrop up by `l1[(t.dx, t.dy)]` -- exact tile
    coordinates -- and refused 174 of 217 candidate cells on `fship_1`, 395
    of 458 on `fship_2` and **715 of 715 on `mtcrl_4`**, all reported as "no
    layer 1 underneath". That was the measurement, not the game: layer-2
    destinations are NOT on layer 1's 16-grid, so an overlay at dx = 8 sits
    across two layer-1 tiles and matches neither of them. A raster is the
    only structure that can answer a question about a rectangle that spans
    tiles.
    """
    scale = max(1, px // 256)
    n = TILE * scale
    lay1 = [t for t in tiles if t.layer == 1]
    lay2 = [t for t in tiles if t.layer == 2]
    par = [t for t in tiles if t.layer >= 3]
    if not lay2:
        return {}
    xs = [t.dx for t in tiles]
    ys = [t.dy for t in tiles]
    span = 32                                   # the widest tile the format has
    x0, y0 = min(xs), min(ys)
    w = (max(xs) + span - x0) * scale
    h = (max(ys) + span - y0) * scale
    if w <= 0 or h <= 0 or w * h > 64_000_000:
        return {}
    cov = np.zeros((h, w), bool)
    rgb = np.zeros((h, w), np.uint16)
    anyc = np.zeros((h, w), bool)          # cover INCLUDING the parallax

    def _fit(blk, want):
        s0 = blk.shape[0]
        if s0 == want:
            return blk
        if want > s0 and want % s0 == 0:
            f = want // s0
            return np.repeat(np.repeat(blk, f, 0), f, 1)
        if s0 > want and s0 % want == 0:
            return blk[::s0 // want, ::s0 // want]
        return None

    def _block(t, nu=TILE):
        """(colour, drawn) at destination resolution, or (None, None)."""
        p = pages.get(t.slot)
        a = arrays.get(t.slot)
        if p is None or a is None:
            return None, None
        if getattr(p, 'size_flag', 0) and nu == TILE:
            return None, None
        try:
            if p.depth == 2:
                s = p.px // 256
                blk = a[t.sy * s:(t.sy + nu) * s,
                        t.sx * s:(t.sx + nu) * s]
                if blk.shape[0] != nu * s:
                    return None, None
                drawn = blk != FN.EMPTY
            else:
                pal = t.pal if t.pal < len(pal565) else len(pal565) - 1
                idx = a[t.sy:t.sy + nu, t.sx:t.sx + nu]
                if idx.shape != (nu, nu):
                    return None, None
                blk = pal565[pal][idx]
                # On layer 1 index 0 IS drawn -- see the docstring -- and on
                # layer 2 it is the cut-out. That asymmetry is this module's
                # oldest rule and it decides cover here as it does there.
                drawn = (np.ones(idx.shape, bool) if t.layer == 1
                         else (idx != 0))
            want = nu * scale
            blk = _fit(np.ascontiguousarray(blk), want)
            drawn = _fit(drawn, want)
        except Exception:                                      # noqa: BLE001
            return None, None
        if blk is None or drawn is None or blk.shape != (want, want):
            return None, None
        return blk, drawn

    # THE PARALLAX GOES DOWN FIRST, AND IT IS COVER FOR THE KEY ONLY.
    #
    # Layers 3 and 4 draw BEHIND 1 and 2 even though `walk_layers` yields
    # them last. For the COLOUR question they are useless -- they scroll, so
    # the pixel behind a given texel is different at every camera position,
    # which is why `rgb` never sees them and `MODCLEAR_COVER_PAR` is not
    # allowed to feed the blend. For the KEY question they are decisive:
    # "does ANYTHING draw here" has the same answer at every camera position
    # for a backdrop that wraps, and the difference between "the sea is
    # behind this texel" and "the framebuffer is behind this texel" is the
    # difference between a fixed edge and a black hole.
    #
    # A LOWER BOUND, deliberately: a parallax tile wraps, so its static
    # extent understates where it actually draws. Under-counting cover can
    # only make this arm refuse work it could have done.
    for t in par:
        nu = 32 if getattr(pages.get(t.slot), 'size_flag', 0) else TILE
        m = nu * scale
        ry = (t.dy - y0) * scale
        rx = (t.dx - x0) * scale
        if ry < 0 or rx < 0 or ry + m > h or rx + m > w:
            continue
        blk, drawn = _block(t, nu)
        if drawn is None:
            continue
        # ONLY A PARALLAX CELL THAT IS OPAQUE THROUGHOUT COUNTS AS COVER.
        #
        # The same self-reference as the layer-2 exclusion above, one layer
        # down and subtler. A parallax cell is on layer 3/4, so `rec['l2']`
        # is true for it and the mod-clear arm CAN key it -- which means a
        # partially transparent parallax cell is not a fixed point either,
        # and cover taken from it may evaporate in the same build that
        # relied on it.
        #
        # `drawn.all()` is the exact test rather than a margin: the key arm
        # only ever runs inside `if rec['key']`, and `rec['key']` is
        # `_uses_key`, which is true iff the cell contains index 0. A cell
        # with none cannot be keyed by any arm in this file, now or later.
        #
        # MEASURED: without this, `_kreveal` found 9 non-black texels keyed
        # over literal framebuffer archive-wide, ALL NINE in `md8_b2` --
        # whose parallax is 57.5% keyed, i.e. a sparse cut-out rather than a
        # backdrop. Nine texels in 1.2 million is the size of the hole this
        # closes; the reason to close it is that it is a CLASS, and the next
        # field like `md8_b2` might not be so cheap.
        if not drawn.all():
            continue
        anyc[ry:ry + m, rx:rx + m] |= drawn

    # DRAW ORDER. `read_tiles` walks layer 1 then 2 then 3 then 4 and, within
    # a layer, the tile records in file order -- which is the order the engine
    # consumes them. Layers 3 and 4 are never composited: see condition 1.
    back = {}
    for t in lay1 + lay2:
        ry = (t.dy - y0) * scale
        rx = (t.dx - x0) * scale
        if ry < 0 or rx < 0 or ry + n > h or rx + n > w:
            continue
        if t.layer == 2:                       # SAMPLE FIRST...
            # THE KEY'S COVER MASK IS LAYER 1 AND THE PARALLAX ONLY -- IT
            # DELIBERATELY EXCLUDES OTHER LAYER-2 CELLS, AND THAT IS NOT
            # CONSERVATISM FOR ITS OWN SAKE.
            #
            # `MODCLEAR_COVER` keys layer-2 cells. If one layer-2 cell were
            # allowed to be the cover for another, this arm would be reading
            # a raster it is itself about to punch holes in: cell B is
            # licensed by cell A, and then A gets keyed too and B's licence
            # was never true. MEASURED with layer 2 included, `_kreveal` on
            # the four fields found 15 non-black texels keyed over literal
            # framebuffer, and every one was that self-reference.
            #
            # Layer 1 cannot be keyed by this arm (`rec['l2']` excludes it)
            # and on layer 1 index 0 DRAWS, so its cover is a fixed point.
            # The parallax is one for the same reason at the granularity
            # that matters here. Excluding layer 2 makes the mask true
            # independently of anything this build does, which is the only
            # version of "provably draws behind it" worth the word.
            back[t.off] = (np.ascontiguousarray(rgb[ry:ry + n, rx:rx + n]),
                           np.ascontiguousarray(cov[ry:ry + n, rx:rx + n]),
                           np.ascontiguousarray(anyc[ry:ry + n, rx:rx + n]))
        blk, drawn = _block(t)                 # ...COMPOSITE SECOND
        if blk is None:
            continue
        sub_r = rgb[ry:ry + n, rx:rx + n]
        sub_c = cov[ry:ry + n, rx:rx + n]
        sub_r[drawn] = blk[drawn]
        sub_c |= drawn
        if t.layer == 1:
            anyc[ry:ry + n, rx:rx + n] |= drawn

    where = {}
    for t in tiles:
        where.setdefault(t.off, t)
    out, covers = {}, {}
    for k, rec in keys.items():
        if not rec.get('l2'):
            continue
        p = pages.get(k[0])
        if p is None or getattr(p, 'size_flag', 0):
            continue                                            # 5
        got = [where.get(off) for off in rec['tiles']]
        if not got or any(g is None for g in got):
            continue
        if any(g.layer != 2 for g in got):
            continue                                            # 1
        pair0 = None
        ok = True
        agree = True
        anyk = None
        for g in got:
            pr = back.get(g.off)
            if pr is None:
                ok = False
                break
            # THE KEY MASK IS THE **AND** ACROSS DESTINATIONS, ALWAYS.
            # A cell has one copy of its texels, so a texel may only be
            # keyed if it is safe to key at EVERY place the cell is drawn.
            # This is a separate reduction from the colour agreement below
            # because it can succeed where that one fails -- two
            # destinations can both be covered and still be covered by
            # different colours, and the key does not care which.
            anyk = pr[2] if anyk is None else (anyk & pr[2])
            if pair0 is None:
                pair0 = pr
            elif agree and not (np.array_equal(pr[0], pair0[0])   # 4
                                and np.array_equal(pr[1], pair0[1])):
                # THE COLOUR DISAGREES ACROSS DESTINATIONS, SO THE BLEND IS
                # OFF FOR THIS CELL -- BUT THE KEY IS NOT. A separate flag
                # rather than poisoning `pair0`: the first version set
                # `pair0 = False` here and the next iteration then indexed a
                # bool, which the caller's `except` swallowed by disabling
                # BOTH arms for the whole field. A guard that silently turns
                # a pass off is worse than no guard.
                agree = False
        if not ok or anyk is None:
            continue
        if anyk.any():
            covers[k] = anyk
        if agree and pair0 is not None and pair0[1].any():        # 2
            out[k] = pair0
    return out, covers


def margin_overlay_keys(pages, arrays, tiles, keys):
    """
    The subset of `keys` that are LAYER-2 WIDESCREEN MARGIN PLACEHOLDERS.

    See MARGIN_OVERLAY_ALPHA for what this is for. Three conditions, all of
    them about the TILES, so this can be decided before a single texel is
    read:

      1. EVERY tile that draws the cell is on layer 2 -- not layer 1, and
         not layers 3/4 either;
      2. every one of them is WHOLLY outside the 4:3 picture;
      3. no tile inside the 4:3 picture samples the cell.

    2 and 3 are not the same test. A cell can be sampled by one margin tile
    and one interior tile, and then it is shared with the picture and is not
    ours -- that is what 3 refuses. `Tile.outside_43` is the property this
    reads and it is deliberately the LAYER-AGNOSTIC one; `Tile.is_margin`
    means layer 1 as well and several other passes depend on it meaning that,
    so it is left alone. FINDINGS-234 is the record of what widening a shared
    predicate costs.

    LAYERS 3 AND 4 ARE EXCLUDED FOR `bare_keys`'S REASON, WHICH IS THE ONLY
    ONE THAT MATTERS HERE. A parallax tile's destination is not a fixed screen
    position -- `ff7nx_ws` moves its clip and wrap points precisely because it
    scrolls at its own rate -- so "wholly outside the 4:3 picture" is a
    statement about one camera position and not about the tile. Condition 2 is
    therefore undecidable for them and the cell is left alone. MEASURED over
    ten of the largest fields: 398 of 2,852 otherwise-qualifying cells have a
    layer-3/4 tile, so this costs 14% of the population and removes the whole
    class. The parallax void has its own arm already -- ATLAS_PARALLAX_VOID.

    The flatness test is NOT here. `source_cell` already has the destination
    cell in hand and asks `zero.all()` on the real bytes, which is stricter
    than anything this could compute and is the same test the atlas-gap arm
    uses. Deciding it twice is how the two arms drift apart.
    """
    lay, out43, in43 = {}, set(), set()
    for t in tiles:
        c = (t.slot, t.sx, t.sy)
        lay.setdefault(c, set()).add(t.layer)
        (out43 if t.outside_43 else in43).add(c)
    out = set()
    for k in keys:
        c = (k[0], k[1], k[2])
        if c in in43 or c not in out43:
            continue
        if lay.get(c) != {2}:
            continue
        out.add(k)
    return out


def collect(sec9, pages, tiles):
    """
    keys      {(slot, sx, sy, pal): {'band', 'key', 'l2', 'tiles': [...]}}
    fx_of     {key: fxkey}   the two that must share a grid index

    `l2` is True when ANY tile drawing this cell is on layer 2 or above. That
    is the difference between a colour key that is a real cut-out and one that
    is just entry 0's colour -- see the module docstring, constraint 2.

    `l1_over` is True when ANY tile drawing this cell is a LAYER-1 tile that
    sits ON TOP of another layer-1 tile at the same screen position. That is
    the one case where the layer-1 colour key is a real cut-out, and it is
    what `PROMOTE_LAYER1_KEY`'s scope turns on. See FINDINGS-171.
    """
    # WHICH LAYER-1 TILES ARE ON TOP OF ANOTHER ONE. FINDINGS-171.
    #
    # Layer 1 is a single ordered list and later entries draw over earlier
    # ones, so the LAST tile at a position is the one the player sees. Only
    # that tile's key can reveal anything, and only when something is under
    # it.
    #
    # MEASURED over all 709 vanilla fields: 346,735 layer-1 tiles at 346,666
    # distinct positions -- 69 positions (0.02%) carry more than one.
    _l1_last, _l1_n = {}, {}
    for t in tiles:
        if t.layer != 1:
            continue
        pos = (t.dx, t.dy)
        _l1_n[pos] = _l1_n.get(pos, 0) + 1
        _l1_last[pos] = t.off
    _on_top = {off for pos, off in _l1_last.items() if _l1_n[pos] > 1}

    keys, fx_of = {}, {}
    for t in tiles:
        p = pages.get(t.slot)
        if p is None:
            continue
        pal = t.pal if p.depth == 1 else -1
        k = (t.slot, t.sx, t.sy, pal)
        rec = keys.get(k)
        if rec is None:
            rec = keys[k] = {'band': _band_of(t.slot, p.depth),
                             'key': False, 'l2': False, 'l1_over': False,
                             'tiles': []}
        if t.layer >= 2:
            rec['l2'] = True
        if t.layer == 1 and t.off in _on_top:
            rec['l1_over'] = True
        rec['tiles'].append(t.off)
        # A 32-UNIT TILE IS NOT FOUR 16-UNIT CELLS. HANDOFF-189.
        #
        # Offsets 18 and 20 of a tile record are its WIDTH and HEIGHT, and on
        # the parallax layers they are 32. MEASURED over all 709 VANILLA
        # fields, which is the format's own ground truth:
        #
        #     (layer, width)      tiles        aligned to width?
        #     (1, 0)            346,161        n/a -- layer 1 never sets it
        #     (2, 16)           294,518        yes
        #     (3, 32)             7,062        yes
        #     (4, 32)             6,965        yes, bar 2 tiles
        #
        # So 14,027 of Square's own tiles are 32 units wide and 14,025 of them
        # sit on a 32 grid. Alignment to the tile's own width is an invariant
        # of the format, not an accident of authoring.
        #
        # Every relocation in this file works in 16-unit cells, so promoting
        # one of these writes its top-left QUADRANT to a 16-aligned
        # destination and the tile then samples 32x32 from there -- one
        # quarter its own art, three quarters whatever the neighbouring grid
        # slots hold.
        #
        # That is the black-and-sky checkerboard photographed behind the Mt.
        # Corel track, and the archive-wide count is exactly the fields that
        # were reported: 4,186 tiles in 40 fields whose source is no longer on
        # a 32 grid -- `mtcrl_5`, `mtcrl_4`, `fship_1`, `fship_12` (the
        # Highwind), `onna_5` (Honey Bee Inn), `hill`, `hill2`, `midgal`,
        # `zcoal_1..3`, `bwhlin`. Staged through the chain, Cosmos's own
        # section is aligned and palrange, marginart and marginpage all leave
        # it aligned; `dense_repack` misaligns 125 of `mtcrl_5`'s 168.
        #
        # VETOED RATHER THAN HANDLED, deliberately: promoting these correctly
        # needs a 32-aligned destination and a 32x32 copy through the whole
        # placement path, and the cost of getting that wrong is a checkerboard
        # on the backdrop of 84 fields. Vetoing costs them truecolor DEPTH and
        # nothing else -- they keep Cosmos's art at their own page, which
        # `ff7nx_marginart` has already written, exactly as an unpromoted cell
        # does everywhere else in this pipeline.
        #
        # SCOPED TO LAYER 2 AND ABOVE. Layer 1 leaves width at 0 on 346,161 of
        # its 346,175 vanilla tiles, so the engine cannot be reading it there
        # -- and the 14 layer-1 tiles that hold something else hold garbage
        # (2839, 6664, 31080, 257, 17). Testing width alone would veto those
        # 14 for no reason; testing the layer as well keeps the rule to the
        # tiles the field format actually applies it to.
        #
        # AND THE PAGE SAYS IT TOO, WHICH IS THE MECHANISM RATHER THAN THE
        # SYMPTOM. `Page.size_flag` means an 8x8 grid of 32px cells instead of
        # 16x16 of 16px -- `field_bg_pagecap._grid_step` and this file's own
        # `_grid_step` both already read it. MEASURED on `mtcrl_5`: vanilla
        # puts layer 3 on slots 4 and 5, BOTH size_flag=1, and Cosmos keeps
        # that on its slots 5/6/7. Our build ships every page size_flag=0,
        # because the promotion writes `FN.Page(slot, 0, 2, ...)` -- a literal
        # zero. So a promoted parallax cell is wrong twice over: placed on a
        # 16 grid, and landed on a page that no longer declares 32px cells.
        #
        # Both tests are kept. The tile's width is what the reported artefact
        # is measured in; the page's flag is what makes the copy wrong.
        #
        # ---- AND NOW THEY ARE KEPT SEPARATELY, BECAUSE THEY ANSWER DIFFERENT
        # QUESTIONS. HANDOFF-192 5.1.
        #
        # The page's flag says HOW BIG THIS CELL IS -- 32 texels square, and
        # therefore how much `source_cell` must copy and what grid the
        # destination needs. The tile's width says WHAT THE ENGINE WILL READ
        # from it. When they agree, which is every non-fx tile in the archive
        # (see below), the cell is simply a 32-unit cell and can be promoted
        # onto a 32-unit destination page.
        #
        # When they DISAGREE the cell is still refused, because a 32-unit read
        # of a 16-unit cell is the checkerboard from the other side and there
        # is nothing sensible to place.
        #
        # MEASURED on the unmodified archive, over the first 201 fields, using
        # THE CELL THE ENGINE ACTUALLY SAMPLES -- `(texture_id2, src2)` on a
        # tile that has a second texture and `(texture_id, src1)` otherwise,
        # which `test_bigtile.py` establishes at 100.0% of 95,779 non-fx tiles:
        #
        #     32-unit tiles                      5,974
        #       misaligned to a 32 grid              0
        #       on a page without size_flag          0
        #
        # So the disagreement population is EMPTY and this arm cannot fire on
        # Square's data. It is kept because it is cheap and because the thing
        # it guards against is a checkerboard on 84 fields.
        #
        # This also retires FINDINGS-189 6's note that `las1_1` (299 tiles)
        # and `onna_5` (16) have 32-unit tiles on pages without the flag, "and
        # that is Square's arrangement, present in vanilla". They do not.
        # Those are fx tiles: their BASE page has no flag and is not the page
        # they draw from, and the fx page they do draw from has the flag and
        # is 32-aligned. The old note was reading src1 on a tile that samples
        # src2. There is no exception population.
        _w, _h = struct.unpack_from('<HH', sec9, t.off + 18)
        _w32 = t.layer >= 2 and (_w > TILE or _h > TILE)
        if p.size_flag:
            rec['edge'] = BIG_TILE
        if _w32 and not p.size_flag:
            rec['nogrid'] = True
        if _w32 or p.size_flag:
            rec['big'] = True
        f = sec9[t.off + T_FX_PAGE]
        if f and f in pages:
            fk = (f, t.sx, t.sy, pal if pages[f].depth == 1 else -1)
            if fk not in keys:
                keys[fk] = {'band': _band_of(f, pages[f].depth),
                            'key': rec['key'], 'l2': rec['l2'],
                            'l1_over': rec['l1_over'], 'tiles': []}
            elif rec['l2']:
                keys[fk]['l2'] = True
            fx_of.setdefault(k, set()).add(fk)
    return keys, fx_of


def _unpack565(v):
    v = v.astype(np.int32)
    return ((v >> 11) & 31) << 3, ((v >> 5) & 63) << 2, (v & 31) << 3


def _recolour(art, was, now):
    """
    `art` recoloured from the palette it was authored for to the one the tile
    names, keeping the upscale's detail as a residual.

        out = now + (art - was)      per channel, clamped

    Where the upscale added nothing the result is exactly `now`, so this can
    only be as wrong as the palette itself. Where it added detail, that detail
    survives the change of palette.
    """
    ar, ag, ab = _unpack565(art)
    wr, wg, wb = _unpack565(was)
    nr, ng, nb = _unpack565(now)
    r = np.clip(nr + (ar - wr), 0, 255) >> 3
    g = np.clip(ng + (ag - wg), 0, 255) >> 2
    b = np.clip(nb + (ab - wb), 0, 255) >> 3
    return ((r << 11) | (g << 5) | b).astype(np.uint16)


def _box3(a):
    """3x3 box mean with edge replication -- the low-frequency part."""
    p = np.pad(a, 1, mode='edge').astype(np.int32)
    return (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:] +
            p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:] +
            p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]) // 9


def _detail_transfer(art565, tgt565):
    """
    The borrowed art's DETAIL on the correct palette's COLOUR.

    THE PROBLEM THIS SOLVES
    =======================
    Cosmos ships `<field>_<page>_<palette>.dds` almost entirely for palette 0
    (3,537 files across 691 fields; per-palette art exists for only ~207).
    Handing palette 0's image to a cell that names palette 3 is the right art
    in the wrong colours -- MEASURED in Sector 6, where 1 page/palette pair in
    20 has exact art and the other 19 borrow at a mean per-pixel distance of
    44-52 out of 255. That is the brown.

    Turning borrowing off instead costs the upscale: the cell falls back to the
    paletted page, which `ff7nx_marginart` has already filled with Cosmos art
    at the correct palette, but only in 8 bits through that palette's table.

    Neither is necessary. The two sources disagree about COLOUR and agree about
    STRUCTURE, so take one from each:

        out = blur(target) + (art - blur(art))

    `target` is the cell as the game would draw it at its own palette, so its
    low frequency is the correct colour by construction. `art - blur(art)` is
    the upscale's high-frequency detail, which carries no hue of its own.

    BOTH HALVES ARE BLURRED, and that matters. `ff7nx_marginart` has already
    written Cosmos art into the paletted page, so `target` ALREADY carries the
    upscale's structure at 8 bits. Adding the art's detail to the unblurred
    target double-counts every edge. MEASURED over 5,765 real borrowed cells,
    horizontal detail (mean |d/dx|):

        the paletted cell            6.60      <- the structure we want
        the raw borrowed art         6.28
        target + detail(art)        10.48      <- over-sharpened, haloed
        blur(target) + detail(art)   6.95      <- matches the source

    and colour error against the cell drawn at its own palette:

        raw borrowed art            16.92      <- this is the brown
        target + detail(art)         3.61
        blur(target) + detail(art)   4.79

    WHY THIS IS NOT THE RECOLOUR THAT BROKE
    =======================================
    The disabled `_recolour` computed `now + (art - palette_q[i])` and depended
    on `i` being the index the art was derived from. After `ff7nx_marginart`
    rewrites 335,457 cells of page indices, it is not, so `palette_q[i]` was an
    unrelated colour and the residual was arbitrary -- the pastel wash.

    This uses no index lookup and no second palette. The residual is the art
    against ITSELF, so it is bounded by the art's own local contrast and cannot
    run away no matter what marginart did to the indices.
    """
    ar, ag, ab = _unpack565(art565)
    tr, tg, tb = _unpack565(tgt565)
    # HOW MUCH OF THE TARGET'S OWN COLOUR VARIATION TO KEEP.
    #
    # `blur(target)` alone is what "the colours lose detail" is: a 3x3 box on a
    # 16x16 cell is a real low-pass, and it throws away every colour gradient
    # the paletted cell had, keeping only luminance detail from the art.
    #
    # MEASURED over 5,765 borrowed cells -- colour error against the cell drawn
    # at its own palette, and horizontal detail:
    #
    #     target + detail(art)          err 3.61   detail 10.48  (haloed)
    #     blur(target) + detail(art)    err 4.79   detail  6.95  (flat colour)
    #
    # The first double-counts edges because `marginart` has already put the
    # upscale's structure into the paletted page; the second erases the
    # target's own gradients. TARGET_KEEP mixes them, so the cell keeps the
    # part of its colour variation that is really there and the art still
    # supplies the fine structure. 0.0 is the old flat behaviour, 1.0 is the
    # haloed one.
    # BAND-LIMITED. Taking the target's FULL high frequency put the 8-bit
    # page's quantisation speckle straight back into the truecolor output --
    # a fine per-pixel grain over every promoted cell, which is most of the
    # screen. That was reported from hardware as "the entire screen has a
    # grainy look", and it was this line.
    #
    # The target's useful colour information is at the 3-5 pixel scale: the
    # gradient across a surface. Its 1-pixel content is nearest-colour noise
    # and nothing else. So take the mid band and drop the top one.
    TARGET_KEEP = 0.6
    _t3 = np.stack([_box3(tr), _box3(tg), _box3(tb)], -1).astype(np.float32)
    _t5 = np.stack([_box3(_box3(tr)), _box3(_box3(tg)), _box3(_box3(tb))],
                   -1).astype(np.float32)
    base = _t5 + (_t3 - _t5) * TARGET_KEEP
    det = np.stack([ar - _box3(ar), ag - _box3(ag), ab - _box3(ab)],
                   -1).astype(np.float32)

    # DO NOT CLIP THE CHANNELS INDEPENDENTLY.
    #
    # `np.clip(base + det, 0, 255)` per channel is what "colours clipping
    # detail" looks like: a highlight whose red saturates while green and blue
    # do not comes out hue-shifted, and every pixel past the limit collapses to
    # the same value, so the detail in the brightest and darkest parts of a
    # cell is simply gone -- exactly where an upscale has the most to say.
    #
    # Instead, scale the DETAIL down per pixel by the largest factor that
    # keeps every channel in range. `base` is a blur of a real colour so it is
    # always in range, which means s = 0 is always available and the solve
    # cannot fail. Hue is preserved because all three channels are scaled
    # together, and detail is only reduced on the pixels that actually needed
    # it rather than across the cell.
    with np.errstate(divide='ignore', invalid='ignore'):
        room = np.where(det > 0, (255.0 - base) / det,
                        np.where(det < 0, base / (-det), np.inf))
    s = np.clip(np.nanmin(room, axis=-1), 0.0, 1.0)[..., None]
    out = np.clip(base + det * s, 0, 255).astype(np.int32)
    r = (out[..., 0] >> 3).astype(np.uint16)
    g = (out[..., 1] >> 2).astype(np.uint16)
    b = (out[..., 2] >> 3).astype(np.uint16)
    # 2.10: THE GREEN LSB MUST BE ZERO. The engine's non-565 display path
    # (x86 0x63F350) shifts six bits of green into a five-bit field and ORs
    # green's low bit onto the top bit of BLUE. Masked here as well as at the
    # end of the pass, because this function builds a 565 word by hand and
    # that is exactly what 2.10 says not to leave unguarded.
    return (((r << 11) | (g << 5) | b) & ~np.uint16(0x0020)).astype(np.uint16)


def black_fraction(pages, arrays, pal565, k):
    """How much of this cell is OPAQUE BLACK on its paletted page.

    Index 0 is the colour key and is excluded: it is not black, it is the
    key. Any other index whose palette colour is 0x0000 is real, opaque,
    dead black.

    THE CELL IS NOT ALWAYS 16 UNITS -- FINDINGS-215, and this is the SAME
    LITERAL `_uses_key` carried until build 103 (FINDINGS-213). HANDOFF-214
    s6 listed this site as known-bad and untested; it is neither now.

    A `size_flag` page is an 8x8 grid of 32-unit cells, so a TILE-wide window
    reads only the cell's TOP-LEFT QUADRANT. A cell whose quadrant is
    entirely black but whose other three quadrants carry art scores 1.0 and
    is vetoed by the `TRUE_BLACK` filter as "wholly black" when it is not.

    MEASURED on `mtcrl_4` -- the Mt. Corel coaster, whose railroad track the
    user reported as jagged against its parallax backdrop:

        source slot   vetoed by the 16x16 quadrant   by the true 32x32 cell
            3               20 / 64                        0 / 64
            5                2 / 48                        0 / 48
            4                0 / 64                        0 / 64

    Those false vetoes are not merely 22 lost cells. The parallax half admits
    a source page only when EVERY key on it promotes ("a slot that cannot be
    afforded WHOLE is not promoted at all", see the seat split), so 20 false
    vetoes on slot 3 and 2 on slot 5 REFUSE BOTH PAGES ENTIRELY -- 112 of
    `mtcrl_4`'s 176 parallax cells, left at 256px 8-bit directly against the
    64 that promoted to 512px truecolor. That resolution seam is the jagged
    track.

    WHY THIS CANNOT COST A CELL, STRUCTURALLY AND NOT JUST EMPIRICALLY.
    `TRUE_BLACK` is 1.0, and the filter keeps a cell when the fraction is
    STRICTLY BELOW it -- so the veto fires only when EVERY texel in the
    window is opaque black. The corrected window is a SUPERSET of the
    quadrant, and "every texel is black" over a superset is strictly harder
    to satisfy. The corrected function can therefore veto FEWER cells and
    never more, so no field can lose a promotion because of this.
    THAT GUARANTEE IS TIED TO `TRUE_BLACK == 1.0`: at any lower threshold the
    mean over a wider window may rise as well as fall, and this argument does
    not hold. Re-derive it before changing that constant.

    On a non-`size_flag` page `edge` evaluates to 16 and this is a literal
    no-op, which is what bounds the change.
    """
    slot, sx, sy, pal = k
    # CLAMP, FOR THE REASON source_cell ALREADY DOCUMENTS AT LENGTH.
    #
    # A tile may name a palette the field does not have -- Cosmos leaves the
    # palette byte of its widescreen tiles at whatever it was, because FFNx
    # replaces the page with a DDS and never applies it. `source_cell` clamps
    # on the read side for exactly this. THIS FUNCTION DID NOT, and it did not
    # matter while keyed layer-2 cells were vetoed out of `cand` before they
    # ever reached it.
    #
    # PROMOTE_L2_KEY let them in and this raised IndexError on 29+ fields --
    # "index 8 is out of bounds for axis 0 with size 8". build.py catches that
    # per field and logs "not repacked", so each one lost its ENTIRE promotion:
    # a colour-depth change turned into a total loss of truecolor for those
    # fields. Clamping changes no bytes; it only decides which colour we read
    # for a cell already rendering off the end of its palette table.
    if pal >= len(pal565):
        pal = len(pal565) - 1
    if pal < 0:
        pal = 0
    # The edge comes from the ARRAY'S OWN SIZE, exactly as `_uses_key` takes
    # it, so it is right at 256px and at 512px with no second literal to keep
    # in step.
    p = pages[slot]
    arr = arrays[slot]
    grid = 8 if p.size_flag else 16
    edge = arr.shape[0] // grid or TILE
    idx = arr[sy:sy + edge, sx:sx + edge]
    col = pal565[pal][idx]
    return float(((col == 0) & (idx != 0)).mean())


def _up(a, s):
    """Nearest-neighbour block upscale by an integer factor. s == 1 is free."""
    if s == 1:
        return a
    return np.repeat(np.repeat(a, s, axis=0), s, axis=1)


def source_cell(k, rec, pages, arrays, pal565, art_for, pals_for, st,
                scale=1, origin=None, hue_broken_cell=False, edge=TILE,
                force_transfer=False):
    """An (edge*scale, edge*scale) uint16 R5G6B5 cell, from the mod's art.

    `scale` is `page_px // 256`. AT 256 IT IS 1 AND NOTHING BELOW CHANGES.

    `edge` IS 32 ON A size_flag PAGE AND 16 EVERYWHERE ELSE, and it defaults
    to 16 so that every existing caller is unchanged. It is a parameter rather
    than a lookup because this function is called by diagnostics as well as by
    the placement loop, and a silent 16 in one of those is exactly the failure
    FINDINGS-189 5 describes: `render_field.py` drew 16x16 for a 32-unit tile
    and therefore showed the same picture for a fixed archive and a broken
    one. Note `scale = px // 256`, so at 512px a 32-unit cell is 64x64 texels.

    THIS FUNCTION USED TO THROW THE EXTRA RESOLUTION AWAY. `ArtProvider` is
    built at the page size, so at 512px `art.px` is 512 -- and the art path
    then read it with `buf[sy*s:(sy+TILE)*s:s]`, a stride of `s`, which is
    NEAREST-NEIGHBOUR POINT SAMPLING back down to a 16x16 cell. The DDS went
    1024 -> BOX -> 512 -> every-other-texel -> 256. That is both why a 512px
    build produced 256px pages and why the downsampling looked bad: the
    careful filter in PageArt was undone one line later by a stride.
    """
    slot, sx, sy, pal = k
    # A TILE MAY NAME A PALETTE THE FIELD DOES NOT HAVE, AND THAT MUST NOT
    # TAKE THE WHOLE FIELD DOWN WITH IT.
    #
    # Cosmos leaves the palette byte of its widescreen tiles at whatever it
    # was, because FFNx replaces the page with a DDS and never applies it --
    # `ff7nx_marginpal` documents this. So `pal` can be >= the palette count,
    # `pal565[pal]` raises IndexError, `dense_repack` aborts, and build.py
    # logs "not repacked -- index 5 is out of bounds for axis 0 with size 4".
    # THE FIELD THEN GETS NO PROMOTION AT ALL: not one truecolor page, for one
    # bad byte on one tile.
    #
    # `render_field.py` already handles this the same way and says so
    # (HANDOFF-78 3.4: CLAMP). Clamping on the READ side changes no bytes in
    # the archive -- it only decides which colour we bake for a cell that is
    # currently rendering out of the end of the palette table anyway.
    if pal >= len(pal565):
        pal = len(pal565) - 1
    p = pages[slot]
    if p.depth == 2:
        a = arrays[slot]
        st.from_vanilla += 1
        # A depth-2 source page is already at the destination size, so its
        # cell is (edge*scale)^2 and needs no upscale.
        t = edge * scale
        return a[sy * scale:sy * scale + t, sx * scale:sx * scale + t].copy()

    pal = rec.get('pal', pal)
    if pal >= len(pal565):                    # see the clamp note above
        pal = len(pal565) - 1
    # BORROW PALETTE 0 WHEN THE EXACT PALETTE IS NOT SHIPPED.
    # THIS IS FFNx'S OWN RULE, AND IT IS ONLY VALID ON A TRUECOLOR DESTINATION.
    #
    # `repos/FFNx-master/src/saveload.cpp:138`, `load_normal_texture`:
    #
    #     _snprintf(filename, ..., "%s/%s/%s_%02i.%s", basedir, tex_path,
    #               name, palette_index, mod_ext[idx]);
    #     ...
    #     if (!ret) {
    #         if (palette_index != -1 && (palette_index & 0x3FFFFFFF) != 0) {
    #             ffnx_info("No external texture found [%s], falling back to
    #                        palette 0\n", ...);
    #             return load_normal_texture(..., palette_index & 0x40000000,
    #                                        ...);      // -> palette 0, FF7
    #         }
    #         return 0;                       // -> the game's own texture
    #     }
    #
    # It borrows, it does not recolour, and it renders this mod correctly. The
    # previous rule here ("NEVER BORROW", written after the Sector 6 brown side)
    # missed the distinction that makes both observations true:
    #
    #   depth 2  pixels are FINAL COLOUR, the palette is never applied
    #            -> borrowed art draws exactly as FFNx draws it.   CORRECT.
    #   depth 1  pixels are INDICES, recoloured by the tile's palette
    #            -> borrowed art is the right indices through the wrong table.
    #            That is `ff7nx_marginart`'s job and it must stay exact-only.
    #
    # The build that produced the brown side was doing BOTH at once, so the two
    # cases were never separated. This function only ever writes depth-2 cells.
    #
    # MEASURED, why it is worth having: the mod ships 3,537 palette-0 field
    # textures across 691 fields and per-palette art for only ~207 of them, so
    # the exact-palette ceiling is 21.1% of drawn (cell, palette) pairs
    # (HANDOFF-78 3.2). The last build logged `71,410 exact, 0 borrowed,
    # 262,237 from the paletted page` -- a quarter of a million cells taking an
    # 8-bit page for art the mod ships and the reference renderer would use.
    # ...AND IT IS ON, ON HARDWARE EVIDENCE, OVER MY OWN OBJECTION.
    #
    # I implemented this, measured the numbers below, reverted it, and put it
    # back when the build that had it ON came back from hardware with no crash
    # and "looking better". The measurement is real and is kept here as the
    # known risk; it is not a reason to remove a change the console likes.
    #
    # The case FOR leaving it on, which the numbers below do not capture:
    # Cosmos authored this mod against FFNx, and FFNx falls back to palette 0
    # unconditionally (saveload.cpp:138). The palette-0 fallback is therefore
    # part of how the mod is MEANT to look, not a degradation of it.
    #
    # Enabling it took art coverage on 19 real fields from 21.1% to 100.0% of
    # promoted cells.
    #
    # MEASURED, over the palettes a borrow would actually cross -- 284
    # (field, page, palette) borrow candidates, mean |RGB| distance between
    # palette 0 and the palette the tile names, 0-255:
    #
    #        0-7      2   ( 0.7%)      <- the only ones where borrowing is free
    #        8-31     11  ( 3.9%)
    #       32-63    235  ( 82.7%)
    #        64+      36  ( 12.7%)
    #
    # 82.7% of borrows would shift the cell by more than 32/255 per pixel.
    # Independently: over the 340 pages where Cosmos ships BOTH palette 0 and
    # another palette, the two images differ by mean 36.5, 99th pct 156.9, and
    # ZERO are byte-identical. The palettes are genuinely different colours.
    # That is the brown right-hand side, quantified.
    #
    # THE COUNTER-ARGUMENT, so it is not lost: `ff7nx_marginart` gives this
    # pipeline an option FFNx does not have -- 335,457 cells of Cosmos art
    # written INTO the paletted page, quantised through the palette the tile
    # names. Right art, right colours, 8 bits. On that reading a borrowed cell
    # trades correct colour for colour DEPTH, and 82.7% of borrows move the
    # pixel by more than 32/255.
    #
    # WHICH IS RIGHT IS AN EYES QUESTION, AND THE FIELDS THAT ANSWER IT ARE
    # `mds6_2`, `mds6_3` AND WALL MARKET -- the right-hand side that went brown
    # in the build HANDOFF-78 2.7 was written about. If it is brown again, gate
    # this on the palette distance rather than deleting it: distance < 8 was
    # 0.7% of candidates and distance < 32 was 4.6%.
    # FOLLOW ff7nx_marginpage's SPLIT TO THE ART. FINDINGS-151.
    #
    # THE SAME BLINDNESS AS hue_broken, ONE STEP LATER AND MUCH MORE COSTLY.
    # `marginpage` moved this cell onto a page Cosmos never shipped, so
    # `art_for(slot, pal)` returns None, the whole art path is skipped, and
    # the cell is taken FROM THE PALETTED PAGE -- which already holds the
    # sky quantised through a palette whose bluest entry is 41. Promoting it
    # then bakes that olive into truecolor permanently.
    #
    # MEASURED, mds5_5 build 61: the margin sky reached the truecolor page
    # (40/40 cells) and its PIXELS were still (79.5, 67.8, 27.8) against the
    # interior sky's (65.4, 65.4, 58.0). Right depth, wrong colour -- which is
    # why build 61 looked identical on hardware despite every counter moving.
    # `_wire` MUST EXIST ON EVERY PATH THROUGH THIS FUNCTION.
    # It is written only in the `art is not None` branch, and the key block
    # below subtracts it unconditionally -- so the vanilla branch would raise
    # NameError, which `dense_repack` would swallow as "field not repacked"
    # and cost that field every truecolor page it was going to get.
    _wire = None
    _asl, _asx, _asy = slot, sx, sy
    if origin:
        _o = origin.get((slot, sx, sy))
        if _o:
            _asl, _asx, _asy = _o
    art = art_for(_asl, pal) if art_for is not None else None
    src_pal = pal
    if art is not None:
        st.from_art += 1
    elif art_for is not None and pal != 0:
        # ONLY BORROW WHEN PALETTE 0 IS THE SAME COLOUR. See BORROW_MAX_DIST.
        # Measured over the indices THIS CELL actually draws, not the whole
        # table -- a palette can differ wildly in entries the cell never uses.
        if BORROW_MAX_DIST == float('inf') or _pal_distance(
                pal565, pal,
                arrays[slot][sy:sy + edge, sx:sx + edge]) <= BORROW_MAX_DIST:
            art = art_for(_asl, 0)
            if art is not None:
                src_pal = 0
                st.from_art_borrow += 1
        else:
            st.borrow_refused += 1

    # THE RECOLOUR IS DISABLED. IT IS BROKEN, AND HERE IS WHY.
    #
    # It computed `out = palette_target[i] + (art - palette_q[i])`, which is
    # only meaningful if `i` is the index the ART WAS DERIVED FROM. It is not.
    # `ff7nx_marginart` runs FIRST and rewrites 335,457 cells of page indices
    # to Cosmos art quantised against the tile's own palette, so by the time
    # this pass reads `arrays[slot]`, `i` is no longer the vanilla index the
    # `.dds` was rendered from. `palette_q[i]` is then an unrelated colour, the
    # residual is arbitrary and large, and the per-channel clamp turns it into
    # a pastel wash -- the light purple patches.
    #
    # Fixing it properly needs the PRE-marginart indices, i.e. the vanilla
    # section 9 carried alongside the rewritten one. Until then a cell with no
    # art at its own palette takes the paletted page, which already holds
    # Cosmos art at the correct palette -- lower colour depth, right colours.
    #
    # Do not re-enable this without the vanilla indices. It has cost one build.
    idx = arrays[slot][sy:sy + edge, sx:sx + edge]
    zero = _up(idx == 0, scale)
    if art is not None:
        buf = np.frombuffer(art.buf, np.uint16).reshape(art.px, art.px)
        s = art.px // 256
        # Take every texel the destination cell can hold. `step` is only >1
        # when the art is BIGGER than the page -- e.g. 512px art into a 256px
        # page -- and even then PageArt has already box-filtered the DDS down
        # to `art.px`, so the stride is a last resort rather than the filter.
        step = max(1, s // scale)
        t = edge * scale
        out = buf[_asy * s:_asy * s + t * step:step,
                  _asx * s:_asx * s + t * step:step].copy()
        if out.shape != (t, t):                       # art smaller than asked
            out = _up(buf[sy * s:(sy + edge) * s, sx * s:(sx + edge) * s],
                      scale // max(1, s)).copy()
        pal_ref = _up(pal565[pal][idx], scale)
        # `force_transfer` IS THE MULTI-PALETTE ARM AND IT IS DELIBERATELY
        # NARROW. See MULTIPAL_RECOLOUR. It defaults False, so it changes
        # nothing for any existing caller, and `dense_repack` sets it only for
        # cells its admission test has already proved recolour rather than
        # collapse. `hue_broken_cell` still wins: a cell with no chromaticity
        # has no colour for the transfer to take, and `_multipal_admit`
        # refuses those before they reach here.
        if (src_pal != pal and not hue_broken_cell
                and (force_transfer or not KEEP_ART_ON_BORROW)):
            # BORROWED. Keep the detail, take the colour from the palette this
            # cell actually names. See _detail_transfer.
            out = _detail_transfer(out, pal_ref)
        elif src_pal != pal or (KEEP_ART_ON_BORROW and src_pal != pal):
            # ...EXCEPT WHERE THAT PALETTE PROVABLY CANNOT HOLD THE ART.
            # FINDINGS-151, and this is the last link in mds5_5's yellow sky.
            #
            # The cell is borrowed (art at palette 0, tile names palette 1) and
            # `_detail_transfer` then takes its COLOUR from pal_ref -- the
            # paletted page through palette 1, whose bluest entry is 41. So
            # Cosmos's cool sky (74.8, 78.2, 74.6) was re-dyed olive on the way
            # onto the truecolor page, which is why promoting it changed
            # nothing on hardware across builds 60 and 61.
            #
            # The module already argues the right rule two hundred lines up:
            # "depth 2 pixels are FINAL COLOUR, the palette is never applied
            # -> borrowed art draws exactly as FFNx draws it. CORRECT." It was
            # simply never applied here.
            #
            # SCOPED to hue-broken cells on purpose. The detail transfer exists
            # because 82.7% of borrows move a pixel by more than 32/255, and
            # the field that punished removing it was mds6_2/mds6_3/Wall
            # Market's brown right-hand side. Those cells are not hue-broken,
            # so they keep the transfer and that risk is untouched.
            st.hue_kept_art += 1
            dense_repack.hue_kept_art = (
                getattr(dense_repack, 'hue_kept_art', 0) + 1)
        # Where the ART is transparent, fall back to the paletted pixel: the
        # mod's alpha is authoritative about its own art, not about what the
        # game draws there.
        tm = art.tmask[_asy * s:_asy * s + t * step:step,
                       _asx * s:_asx * s + t * step:step]
        # `tpaint` IS `tm` NARROWED BY THE MOD'S TRUE COVERAGE. See
        # PAINT_MAXPOOL. Where the native art paints and only the box filter
        # says otherwise, the mod's own colour -- which the alpha-weighted
        # resample got right -- must NOT be replaced by the 1997 pixel, and
        # the key arms below must not key it either. One predicate, both
        # places, or the fix trades a green speck for a grey one.
        tpaint = tm
        _am = getattr(art, 'amax', None) if PAINT_MAXPOOL else None
        _cm = getattr(art, 'cmax', None) if PAINT_MAXPOOL else None
        _wire = None
        if _am is not None and _cm is not None:
            _a2 = _am[_asy * s:_asy * s + t * step:step,
                      _asx * s:_asx * s + t * step:step]
            _c2 = _cm[_asy * s:_asy * s + t * step:step,
                      _asx * s:_asx * s + t * step:step]
            if _a2.shape == tm.shape and _c2.shape == tm.shape:
                # ...AND ONLY WHERE THE RECOVERED WIRE IS **BRIGHT**.
                #
                # Same rule as MODCLEAR_DARK_ONLY and the blend, for the same
                # reason: this arm may only ever make a texel lighter.
                #
                # A thin structure the box filter lost can be dark -- a
                # girder edge rather than a fence wire -- and recovering
                # those would put Cosmos's own dark outline back over the
                # background, which is the black edge builds 121 and 123
                # spent themselves removing. MEASURED without this term, the
                # texels handed back from the key rendered near-black 3,875
                # times on `mtcrl_4`, 318 on `fship_1` and 304 on `fship_2`.
                #
                # With it the arm is monotone in the picture in the same
                # sense as everything else here: a keyed texel can only be
                # replaced by a BRIGHT one, never by a dark one. The fence
                # wire is bright, which is why it is the thing that comes
                # back; Cosmos's dark outline stays keyed, exactly as it is
                # today. Patrick's two constraints are the two halves of
                # this one predicate.
                _bright = _maxchan565(_c2) >= MODCLEAR_DARK
                tpaint = tm & ((_a2 < 8) | ~_bright)
                _wire = tm & ~tpaint
                if not _wire.any():
                    _wire = None
        if tpaint.shape == out.shape and tpaint.any():
            out[tpaint] = pal_ref[tpaint]
        # AND THE THIN STRUCTURE GETS ITS OWN COLOUR BACK.
        #
        # This is not optional and leaving it out is measurably worse than
        # doing nothing. `rgb_to_565` returns EMPTY below `alpha_cut`, so
        # `art.buf` is ZERO at every texel the box filter pushed under alpha
        # 8 -- the mod's colour is not merely dim there, it was never
        # written. Narrowing the vanilla fallback without supplying a
        # replacement therefore leaves the texel at 0x0000, which the
        # NEAR_BLACK lift turns into (8, 8, 8): MEASURED at exactly mean
        # luminance 8.0 across all four test fields, i.e. a black speck
        # traded for a green one.
        #
        # `cmax` is the colour of the highest-alpha NATIVE pixel in the
        # texel's footprint -- the wire itself, at full strength, from the
        # art before any of this pipeline touched it.
        if _wire is not None and _wire.shape == out.shape:
            out[_wire] = _c2[_wire]
            st.wire_texels += int(_wire.sum())
            dense_repack.wire_texels = (
                getattr(dense_repack, 'wire_texels', 0) + int(_wire.sum()))
    else:
        st.from_vanilla += 1
        out = _up(pal565[pal][idx], scale).copy()

    # THE KEY SURVIVES THE MOVE. 0x0000 is the key in truecolor too --
    # MEASURED in the UNMODIFIED archive, which ships 435 truecolor cells
    # containing 0x0000 across `gldst` and six other fields. If the console
    # drew those opaque the stock game would have black rectangles there.
    #
    # Baking entry 0's colour instead, on the theory that layer 1 has nothing
    # behind it, is what I tried first: layer-1 tiles OVERLAP and the key is
    # how an earlier one shows through a later one, so single pixels moved by
    # up to 248 over 26 fields. Preserving the key is both faithful and free.
    # THE GREEN LSB MUST BE ZERO ON EVERY PIXEL THIS PASS WRITES, whatever the
    # source. The palette path is masked in `_pal_rgb`, but a depth-2 page
    # copied verbatim and the mod's own decoded art both arrive from code this
    # pass does not own, so the invariant is enforced here as well. Clearing a
    # bit the engine is going to smear onto blue can only be correct, and it
    # costs one bit of green -- below the 8-bit quantisation step.
    out = (out & ~np.uint16(0x0020)).astype(np.uint16)

    out[out == FN.EMPTY] = FN.NEAR_BLACK      # colour that merely rounds to 0
    if rec['key'] and (rec.get('l2') or not PROMOTE_LAYER1_KEY
                       or rec.get('l1_over')):
        # A REAL CUT-OUT: a layer-2+ overlay whose index 0 is meant to show
        # what is behind it -- or, with PROMOTE_LAYER1_KEY on, a layer-1 tile
        # that sits ON TOP of another layer-1 tile, where the key is how the
        # lower one shows through (FINDINGS-171). Put the key back exactly.
        #
        # The `l1_over` arm is belt-and-braces: the candidate filter already
        # vetoes those cells, so this cannot fire today. It is here so that a
        # future change which lets them through cannot silently bake a key
        # that reveals something.
        #
        # ...UNLESS THE CELL IS AN ATLAS GAP WITH NOTHING BEHIND IT. See
        # bare_keys() above for the measurement. All FOUR conditions hold
        # together or this arm does not run:
        #
        #   1. the paletted source cell is ENTIRELY index 0. A cell with any
        #      real index is a shape cut out of art, and that shape is not
        #      ours to close. `zero.all()` -- not "mostly", not "flat".
        #   2. nothing else draws at any destination its tiles occupy, judged
        #      over layers 1 and 2 only, because 3 and 4 move (`rec['bare']`).
        #   3. the mod's own alpha says it PAINTS here. `art.tmask` is the
        #      mod's coverage and it is authoritative about the mod's art.
        #   4. we have art at all -- `art is not None`.
        #
        # And even then the key is put back everywhere the mod's alpha is
        # transparent, so the 6 of `mtcrl_5`'s 308 that Cosmos genuinely
        # leaves empty stay empty. The tile currently draws NOTHING -- it is
        # a fully-keyed cell over bare framebuffer -- so this can only add
        # pixels the reference renderer already shows, never hide any.
        #
        # ...OR THE CELL IS A WIDESCREEN OVERLAY PLACEHOLDER. FINDINGS-235.
        #
        # Condition 2 above -- "nothing else draws underneath" -- is what a
        # margin overlay cell can never satisfy, because layer 1 draws the
        # margin art it sits on top of. It is also not the question: `bare`
        # asks whether closing the key would hide something, and outside the
        # 4:3 picture the mod is the only authority on what belongs there.
        # 1, 3 and 4 all still apply, unchanged, and `margin_overlay_keys`
        # adds the scope: layer 2+, wholly outside the picture, and sampled by
        # no interior tile. See MARGIN_OVERLAY_ALPHA.
        _mo = MARGIN_OVERLAY_ALPHA and bool(rec.get('margin_l2'))
        _atlas = (ATLAS_GAP and art is not None
                  and (rec.get('bare') or _mo)
                  and bool(zero.all()))
        if _atlas and tpaint.shape == out.shape and not tpaint.all():
            # The atlas arm may not re-key a recovered wire either.
            _ak = zero & tpaint
            if _wire is not None and _wire.shape == _ak.shape:
                _ak = _ak & ~_wire
            out[_ak] = FN.EMPTY
            if _mo and not rec.get('bare'):
                st.margin_l2_filled += 1
                dense_repack.margin_l2_filled = (
                    getattr(dense_repack, 'margin_l2_filled', 0) + 1)
            else:
                st.atlas_gap += 1
                dense_repack.atlas_gap = (
                    getattr(dense_repack, 'atlas_gap', 0) + 1)
        else:
            # ...OR THE MOD CUTS SOME OF THESE UNITS IN HALF. FINDINGS-247.
            #
            # See SUBUNIT_KEY for the measurement and the safety argument.
            # `zero` is uniform per unit by construction (`_up` of a 16x16
            # index block); `tm` is at the DESTINATION resolution and is the
            # mod's own coverage. Where the two disagree WITHIN a unit, the
            # unit is a silhouette boundary and today the whole of it is
            # thrown away.
            #
            # Refine those units and only those: `keep` is `zero` everywhere
            # else, so a unit the mod paints whole and a unit the mod leaves
            # whole are keyed byte-for-byte as before. A field with no mixed
            # unit comes out identical without needing a special case.
            #
            # The `art is not None` guard is not optional -- `tm` does not
            # exist in the `st.from_vanilla` branch above.
            # THE THRESHOLD IS `hmask`, NOT `tmask`, AND THAT IS DELIBERATE.
            # See PageArt.hmask in field_bg_repack. `tmask` answers "does
            # this cell contain any transparency at all" at alpha < 8, which
            # would call a 4%-alpha texel painted and draw Cosmos's dark
            # boundary outline at full strength -- build 116's black fringe,
            # reintroduced one texel wide around every overlay. `hmask` is
            # the 50% rule, which is the honest 1-bit reduction of an 8-bit
            # alpha. `_clear` is its complement: the mod covers LESS than
            # half of this texel, so the key stays.
            _hm = getattr(art, 'hmask', None) if art is not None else None
            _clear = None
            if _hm is not None:
                _c = _hm[_asy * s:_asy * s + t * step:step,
                         _asx * s:_asx * s + t * step:step]
                if _c.shape == out.shape:
                    _clear = ~_c
            _sub = (SUBUNIT_KEY and art is not None and scale > 1
                    and bool(rec.get('l2'))
                    and _clear is not None
                    and _clear.shape == (edge * scale, edge * scale)
                    and zero.shape == _clear.shape
                    and _clear.any() and not _clear.all())
            # ...AND WHERE THE MOD PAINTS NOTHING AT ALL, KEY IT. FINDINGS-253.
            #
            # See MODCLEAR_KEY. This is the mirror of the arm above and it is
            # deliberately computed as a SEPARATE mask rather than folded into
            # `_clear`, because the two read different alpha thresholds -- this
            # one `tmask` (alpha < 8), that one `hmask` (alpha >= 128) -- and
            # collapsing them would be the bug each guard exists to prevent.
            #
            # `_mck` is unioned into whichever key mask the arms above compute,
            # so this can only ever ADD key. `subunit_texels` is still counted
            # against `zero` alone, so build 119's counter keeps meaning what
            # it meant, and with MODCLEAR_KEY off the three branches below
            # collapse to exactly build 120's `out[zero]` / `out[keep]`.
            _mck = None
            if (MODCLEAR_KEY and art is not None and bool(rec.get('l2'))
                    and tpaint.shape == out.shape and tpaint.any()
                    and (MODCLEAR_WHOLE or not tpaint.all())):
                _mck = tpaint
                _cvr = rec.get('cover') if MODCLEAR_COVER else None
                if _cvr is not None and _cvr.shape != out.shape:
                    _cvr = None
                if MODCLEAR_DARK_ONLY and _cvr is not None:
                    # BUILD 123. See MODCLEAR_COVER. The dark test still runs
                    # -- it is what makes a texel with nothing behind it safe
                    # -- and the cover test is a second, independent licence
                    # for the same texel. A texel needs one of the two, not
                    # both, and the union is strictly larger than build 122's
                    # population, so this arm can only ever key MORE.
                    _mck = _mck & ((_maxchan565(out) < MODCLEAR_DARK) | _cvr)
                    if not _mck.any():
                        _mck = None
                elif MODCLEAR_DARK_ONLY:
                    # See MODCLEAR_DARK_ONLY. This is the term that makes the
                    # change monotone IN THE PICTURE -- black can become art,
                    # art can never become black -- and it is what stands in
                    # for the per-texel cover mask `source_cell` cannot see.
                    _mck = _mck & (_maxchan565(out) < MODCLEAR_DARK)
                    if not _mck.any():
                        _mck = None
            if _sub:
                _tu = _clear.reshape(edge, scale, edge, scale)
                _mixed = _tu.any(axis=(1, 3)) & ~_tu.all(axis=(1, 3))
                if _mixed.any():
                    keep = zero & (_clear | ~_up(_mixed, scale))
                    st.subunit_cells += 1
                    st.subunit_units += int(_mixed.sum())
                    st.subunit_texels += int(zero.sum() - keep.sum())
                    dense_repack.subunit_cells = (
                        getattr(dense_repack, 'subunit_cells', 0) + 1)
                    dense_repack.subunit_units = (
                        getattr(dense_repack, 'subunit_units', 0)
                        + int(_mixed.sum()))
                    dense_repack.subunit_texels = (
                        getattr(dense_repack, 'subunit_texels', 0)
                        + int(zero.sum() - keep.sum()))
                else:
                    keep = zero
            else:
                keep = zero
            # A RECOVERED WIRE MUST SURVIVE THE KEY BLOCK, AND IN BUILD 124
            # IT DID NOT. THIS IS THE BUG THAT MADE THAT BUILD "MARGINAL".
            #
            # `_wire` is written up in the art branch, which runs BEFORE this
            # one. `keep` is built from `zero` (vanilla's index) and `_clear`
            # (`hmask`) and knows nothing about `tpaint`, so
            # `out[keep] = FN.EMPTY` keyed every recovered wire texel straight
            # back wherever vanilla's index happened to be 0 -- which on a
            # fence is most of them, because vanilla's fence is transparent
            # between its own wires.
            #
            # MEASURED after build 124 on `mds7plr1`: 3,663 texels still keyed
            # over art the mod paints OPAQUELY, 1,283 of them with a mean
            # alpha under 8 -- i.e. exactly the population `_wire` had already
            # rescued and this block then threw away again. Patrick's word for
            # the result was "marginally" better.
            #
            # The subtraction is unconditional and last: nothing else in this
            # function may re-key a texel the mod paints at full strength.
            if _wire is not None and _wire.shape == keep.shape:
                keep = keep & ~_wire
            if _mck is not None:
                _final = keep | _mck
                _added = int(_final.sum() - keep.sum())
                if _added:
                    st.modclear_cells += 1
                    st.modclear_texels += _added
                    dense_repack.modclear_cells = (
                        getattr(dense_repack, 'modclear_cells', 0) + 1)
                    dense_repack.modclear_texels = (
                        getattr(dense_repack, 'modclear_texels', 0) + _added)
                    if bool(_mck.all()):
                        st.modclear_whole += 1
                        dense_repack.modclear_whole = (
                            getattr(dense_repack, 'modclear_whole', 0) + 1)
                keep = _final
            out[keep] = FN.EMPTY
    # On layer 1 index 0 is NOT a cut-out -- it is drawn, and its colour
    # matters. Proved on hardware: setting entry 0 to black removed the
    # Sector 6 yellow and put black speckles across Wall Market, which cannot
    # happen if the index is discarded. So the colour above is kept as it is,
    # lifted off 0x0000 by the line before this one so it cannot be mistaken
    # for a key.
    #
    # ...AND LAST OF ALL, BAKE THE BLEND THE 1-BIT KEY CANNOT DO.
    # BUILD 122, FINDINGS-255. See BLEND_PARTIAL for the measurement and
    # `backdrop_keys` for the five conditions `rec['under']` had to pass.
    #
    # DELIBERATELY THE LAST THING THIS FUNCTION DOES, AND AFTER THE KEY.
    # `out != FN.EMPTY` is then the real, final answer to "does this texel
    # draw", so this arm structurally cannot blend a texel that is keyed and
    # structurally cannot key one that is not. Running it earlier would mean
    # re-deriving that answer, and the two derivations would drift.
    _und = rec.get('under') if BLEND_PARTIAL else None
    _alp = getattr(art, 'alpha', None) if art is not None else None
    if (_und is not None and _alp is not None
            and _und[0].shape == out.shape):
        _a = _alp[_asy * s:_asy * s + t * step:step,
                  _asx * s:_asx * s + t * step:step]
        if _a.shape == out.shape:
            # `_und[1]` IS THE PER-TEXEL COVER MASK AND IT IS NOT OPTIONAL.
            # Without it a texel with nothing behind it would blend toward
            # `rgb`'s zero fill, i.e. toward BLACK -- which would make this
            # arm darken a rim instead of lightening it and destroy the
            # monotone-in-the-picture property the whole build rests on.
            # ...AND ONLY WHERE THE BACKDROP IS LIGHTER THAN THE RIM.
            #
            # MEASURED, and it is the one thing the design note above got
            # wrong: a blend is not monotone by itself. `_kblendgate` on
            # eight fields found 79 of 2,279 changed texels getting DARKER,
            # because the thing behind a dark rim is sometimes darker still
            # -- a shadow under an overlay, or another dark overlay. Those
            # blends are arithmetically CORRECT and they are given up
            # anyway, because 3.5% of the population is not worth trading
            # the property the whole build is argued from:
            #
            #     black can become art; art can never become black.
            #
            # With this term the result is bounded below by the texel we
            # already ship and above by the backdrop, so the arm cannot
            # darken anything even if `backdrop_keys` were wrong about what
            # is behind. That is a structural guarantee rather than a
            # measured one, and it is what makes this safe to bake into the
            # archive permanently.
            # AND THE TEST IS PER CHANNEL, NOT ON THE MAXIMUM.
            # Comparing brightness alone is not sufficient and the gate
            # caught it: `out` = (0, 0, 39) against a backdrop of
            # (40, 0, 0) passes a max-channel test and still blends DOWN to
            # (20, 0, 19). One texel in 2,003 did exactly that. Requiring
            # every channel to be at least as bright makes the result
            # bounded below by `out` in every channel as a matter of
            # arithmetic, so nothing can darken -- and it keeps the value
            # written a TRUE blend rather than a clamped one, which is the
            # whole justification for writing it.
            _or, _og, _ob = _rgb8_565(out)
            _ur, _ug, _ub = _rgb8_565(_und[0])
            _bm = (_und[1] & (_a >= BLEND_MIN) & (_a < BLEND_MAX)
                   & (out != FN.EMPTY)
                   & (_maxchan565(out) < BLEND_DARK)
                   & (_ur >= _or) & (_ug >= _og) & (_ub >= _ob)
                   & ((_ur > _or) | (_ug > _og) | (_ub > _ob)))
            if _bm.any():
                _w = _a[_bm].astype(np.uint16)
                sr, sg, sb = _rgb8_565(out[_bm])
                dr, dg, db = _rgb8_565(_und[0][_bm])
                _iw = np.uint16(255) - _w
                # Integer, rounded, and never in float: this writes archive
                # bytes and a float path would make the output depend on the
                # numpy build. `+ 127` is the round-half-up the engine's own
                # reconstruction expects.
                _mix = lambda a, b: (                          # noqa: E731
                    (a.astype(np.uint32) * _w + b.astype(np.uint32) * _iw
                     + 127) // 255).astype(np.uint16)
                out[_bm] = _565_rgb8(_mix(sr, dr), _mix(sg, dg),
                                     _mix(sb, db))
                _n = int(_bm.sum())
                st.blend_cells += 1
                st.blend_texels += _n
                dense_repack.blend_cells = (
                    getattr(dense_repack, 'blend_cells', 0) + 1)
                dense_repack.blend_texels = (
                    getattr(dense_repack, 'blend_texels', 0) + _n)
    return out


# PROMOTE A LAYER-1 CELL THAT CONTAINS INDEX 0? DEFAULT OFF, AND HERE IS WHY
# THE ANSWER IS NOT OBVIOUS.
#
# This module's constraint 2 says the layer-1 colour key is not a cut-out:
# "Layer 1 has nothing behind it, so a 'transparent' pixel there was always
# entry 0's colour; baking that colour is exactly equivalent." On that
# reading, vetoing layer-1 keyed cells is a bug, and it costs a lot --
# MEASURED over 265 vanilla fields, 22,378 of 62,646 keyed cells are layer-1
# only, and allowing them lifts promotable cells 133,630 -> 155,456 (+16.3%).
# At 512px over 187 fields it is +15.4% cells for +11% pages.
#
# BUT `field_bg_repack` RECORDS A MEASUREMENT AGAINST IT, in its own words:
#
#     "Baking entry 0's colour instead, on the theory that layer 1 has
#      nothing behind it, is what I tried first: layer-1 tiles OVERLAP and
#      the key is how an earlier one shows through a later one, so single
#      pixels moved by up to 248 over 26 fields."
#
# Two claims in this codebase, opposite conclusions, both written as measured.
# The difference may be real -- that note is about baking a colour into a
# PALETTED page, this is about promoting a cell to truecolor -- or it may be
# the same mistake twice. Nobody has run the A/B.
#
# So the code is here, gated, and off. Turning it on is a deliberate visual
# experiment with a named prediction: if it is wrong, the fields to look at
# are the 26 that note is about, and the symptom is a layer-1 tile losing the
# pixels an overlapping neighbour used to show through.
# A CELL THIS BLACK KEEPS ITS PALETTED PAGE. 0 disables the rule.
#
# THIS WAS DESIGNED, MEASURED, DOCUMENTED -- AND NEVER RAN. It lives in
# `field_bg_repack.black_cell_threshold()`, which is read by
# `PageArt.cell_opaque`, which is called by `field_bg_repack.upgrade()` --
# the pass this module replaced. So `SEVENTH_NX_FIELD_BG_TRUE_BLACK` joined
# the budget, the promotion flag and the partial flag as a control the build
# log describes on every run while nothing acts on it.
#
# WHY IT MATTERS, and it is the cause of a visible artifact.
#
# A truecolor page has no index channel, so 0x0000 must mean transparent
# (x86 0x6470E0). A black pixel therefore cannot be stored as black -- it is
# lifted to NEAR_BLACK, which is now 0x0841 = RGB(8,8,8). On a promoted cell that
# is invisible. At the BOUNDARY between a promoted cell and one that stayed
# paletted it is not: true black meets 8/255 blue along a cell edge, and the
# eye finds it immediately. That is the blue line in Men's Hall and the
# patchy near-black squares in the reactor.
#
# The fix is the rule that was always meant to be here: a cell that is mostly
# black has no detail to lose by staying paletted, so let it stay, and the
# lift only ever lands on cells with a few stray black pixels among real
# detail -- where it cannot be seen.
#
# The threshold's own measurements (field_bg_repack.black_cell_threshold):
#
#     reject cells that are   cells kept    black made TRUE black
#          100% black           98.8%              22.1%
#           25% black           92.5%              85.5%   <- default
#            5% black           87.2%              98.1%
#
# 25% takes 85% of the benefit for 7.5% of the cells. Rejection is exactly
# vanilla behaviour for that cell, so it cannot break anything: the cell keeps
# the page it already had.
#
# ------------------------------------------------------------- FINDINGS-169
# 0.25 -> 1.0. THE SEAM THIS PREVENTS IS CANCELLED BY THE SHIPPED SHADER.
#
# Everything above is correct FOR THE BUILD IT WAS WRITTEN AGAINST, where
# NEAR_BLACK was 0x0001 -- pure blue, 0.9/255 -- and the HD shaders had no
# black point. Two things changed since and nobody revisited this number:
#
#   field_bg_native.NEAR_BLACK       0x0001 (blue)  ->  0x0841 = RGB(8,8,8)
#   custom_shaders/hd/*.glsl         HD_BLACK_POINT ->  0.03137 = 8/255
#
# and the second was SIZED TO CANCEL THE FIRST. Both shipped background
# scalers (2xsal_p.glsl, hq4x_p.glsl) end in
#
#     rgb = max(rgb - 0.03137, 0.0) / (1.0 - 0.03137)
#
# so a promoted cell's lifted black, exactly 8/255, arrives on screen at
# 0.00067/255 -- zero in an 8-bit framebuffer -- and its unpromoted
# neighbour's true black arrives at 0. Every value between them is crushed
# too. FINDINGS-132 said "the grey lift and the 8/255 black point have NEVER
# been in the same build"; they are both in this one, and have been for
# several.
#
# MEASURED, not reasoned. `_seam.py` renders both sides of every 16-px tile
# boundary that promotion changes and reports step_AFTER - step_BEFORE, which
# is the only quantity that matters -- a boundary always has a step, the
# question is whether promotion made it worse. 18 real fields, 1,112 cells
# newly promoted, 2,820 boundaries changed:
#
#     boundaries worse by     RAW surface        AS SHIPPED (graded)
#       > 2/255                 649  (23.0%)        8  (0.28%)
#       > 8/255                   4                 2
#       worst delta            17.333             10.324
#
# The raw column is the artifact the rule was written to stop -- and in
# mkt_mens its worst boundary is exactly 8.000, the lift's own signature, on
# 41 of 108 boundaries. Graded, mkt_mens has ZERO over 2/255 and its mean
# delta is -3.679: promotion makes Men's Hall BETTER. So do sbwy4_3 (-9.29),
# jun_w (-8.96) and junpb_3 (-6.09).
#
# 1.0 AND NOT 0.0 DELIBERATELY. At 1.0 only a 100% opaque black cell keeps
# its paletted page, and such a cell has no detail to gain from promotion --
# it is black either way. Keeping it paletted costs nothing visually and
# leaves the truecolor page space for cells that use it. 0.0 measured +31
# further tiles over 8 fields and spends pages on solid black.
#
# THE TWO THAT DID GET WORSE, so the next reader does not have to find them:
#   blin59    (2, -160, -64)|(2, -160, -48)   +10.324
#   blin63_1  (2, 128, 160)|(2, 128, 176)     + 8.754
# Both are layer-2 boundaries and neither is the black lift (8.000 is that
# signature and it is gone). They are ordinary art difference at one spot.
#
# IF THE BLACK POINT IS EVER TURNED OFF, PUT THIS BACK TO 0.25. The two move
# together, exactly as NEAR_BLACK and HD_BLACK_POINT do.
TRUE_BLACK = 1.0

# ------------------------------------------------------------- FINDINGS-171
# SETTLED. BOTH NOTES ARE RIGHT, AND THE DISPUTED SET IS FIVE TILES.
#
# The two claims above disagree in exactly ONE case: a layer-1 tile drawn at a
# position where ANOTHER layer-1 tile already drew, where the upper one
# contains index 0. There, the key IS a cut-out and baking entry 0's colour
# hides what is underneath. Everywhere else layer 1 has nothing behind it and
# baking is exactly equivalent, as constraint 2 says.
#
# Nobody had measured how big that case is. `_l1key.py` does, per POSITION --
# the pair unit, not the cell (HANDOFF-167 s0.5). All 709 vanilla fields:
#
#     layer-1 tiles                   346,735
#     distinct positions              346,666
#     positions with >1 layer-1 tile       69   (0.02%)
#
#     KEYED layer-1 tiles              57,599   <- what this flag vetoes
#       SAFE      nothing else there   57,588   (99.98%)
#       COVERED   drawn over by another     6
#       DISPUTED  on top, and keyed         5   (0.009%)
#
# FIVE TILES IN THE WHOLE GAME: delpb (192,-144), niv_ti1 x3, nivinn_3
# (-96,112). Their worst pixel moves 255 -- so the "up to 248 over 26 fields"
# note is real and I am not overriding it, I am SCOPING it. The 26 fields it
# names were counting cells that CONTAIN a key, not cells whose key reveals
# anything.
#
# Re-measured on build 71's SHIPPED archive, because the margin passes add
# layer-1 tiles beyond the 4:3 picture and could have created new overlaps:
# 123,068 layer-1 tiles, still 5 overlapped positions, still 1 disputed. They
# do not.
#
# So the flag is ON, and `l1_over` (see `collect`) is the scope: a keyed
# layer-1 cell is promoted unless some tile drawing it is on top of another
# layer-1 tile. That is 99.98% of the bucket at provably zero cost, and the
# 0.02% keeps exactly today's behaviour.
PROMOTE_LAYER1_KEY = True

# PROMOTE A LAYER-2+ CUT-OUT. FINDINGS-152, and this is the actual ceiling.
#
# THE VETO IT REPLACES WAS BUILT ON AN UNTESTED PREMISE. `field_bg_dense`'s own
# note says so: "Whether a truecolor page can carry a working cut-out at all is
# the open question (0x0000 on depth 2 -- this project has claims both ways and
# neither is settled)". It is settled now, from the stock game:
#
#     VANILLA, UNMODIFIED: 1,091,741 truecolor texel(s) equal to 0x0000
#     across 26 field(s) -- cosmo, cosmo2, fr_e, gaiin_6, gaiin_7, blin67_4...
#
# If 0x0000 drew opaque on a depth-2 page, the stock game would have black
# rectangles in all 26. It does not. 0x0000 means TRANSPARENT there, which is
# exactly what a cut-out needs, so a layer-2 keyed cell means the SAME THING on
# both page depths and promoting it preserves it byte for byte.
#
# WHY THIS AND NOT THE PAGE CAPS. MEASURED over 34 fields at the real pipeline
# point, every capacity limit is slack: free page slots never run out (~37
# spare per field), the 16-page ceiling binds 9% of fields, worst-field memory
# is 11.19 MB, and raising LOW_SLOT_MAX_TC from 7 to 16 changes NOTHING. The
# candidate filter throws out 69% of all still-paletted cells before any of
# those numbers is consulted:
#
#     key + layer 2 (this flag)   169,706 cells   51% of what is left
#     key, layer 1 only            60,397 cells   18%   -- HARDER, see below
#     no key, held by cap/black   102,873 cells   31%
#
# LAYER 1 IS NOT THE SAME CASE and stays off. There index 0 is DRAWN as a
# colour, so preserving 0x0000 turns 23% of the texels in 2,763 cells
# see-through, and baking entry 0's colour instead breaks the overlap
# show-through a previous attempt hit. That one needs the overlap test first.
PROMOTE_L2_KEY = True

# KEEP COSMOS'S OWN COLOUR ON A BORROWED TRUECOLOR CELL. FINDINGS-157.
#
# Cosmos ships a DDS per (field, page, palette) but usually only `_00`, so
# only ~21% of drawn (cell, palette) pairs have EXACT art. The rest BORROW
# palette 0's art, and `_detail_transfer` then takes the DETAIL from Cosmos
# and the COLOUR from the palette the tile names -- i.e. it pulls the cell
# back toward VANILLA's colour table.
#
# On a DEPTH-2 page there is no palette. The engine never applies one, and
# neither does FFNx -- which is what the mod was authored against. This
# module already argued the right rule 200 lines up: "depth 2 pixels are
# FINAL COLOUR, the palette is never applied -> borrowed art draws exactly as
# FFNx draws it. CORRECT." It was applied only to hue-broken cells.
#
# MEASURED against Cosmos's own DDS as ground truth (`_fidelity.py`, mean
# |RGB| per drawn texel, atlas gap excluded, weighted by tiles), 18 fields:
#
#     ALL          13.96 -> 11.79
#     mkt_mens     12.32 ->  3.00     md8_2     4.45 -> 1.07
#     mds6_2        5.28 ->  2.31     mrkt1    14.49 -> 11.76
#     mds6_3        3.36 ->  1.40     desert1  36.47 -> 33.81
#
# 18 of 18 improved, none worse -- INCLUDING mds6_2/mds6_3/Wall Market, the
# fields whose brown right-hand side was the stated reason for keeping the
# transfer. That objection was measured against VANILLA's colour intent; the
# user's standing rule is the opposite ("do not fix colour by moving it
# toward vanilla -- the mod's art is the target").
#
# This changes PIXELS ONLY. Not one cell changes page, depth or slot.
KEEP_ART_ON_BORROW = True

# PROMOTE THE BASE CELL OF AN ANIMATED TILE. FINDINGS-161.
#
# `collect()` returns `fx_of = {base_key: {partner_key, ...}}`. Until now
# `fx_cells` was the UNION of both sides and every one of them was vetoed, on
# this premise from FINDINGS-157 s5:
#
#     "A tile and its fx page share ONE (sx,sy), so a pair must move together
#      AND land on the SAME GRID INDEX of two different pages."
#
# THAT PREMISE IS FALSE, and it is the reason the biggest bucket in the
# archive sat untouched. The tile record carries SEPARATE source coordinates
# for the second texture -- `src_x2/src_y2` at offsets 14/16, which
# `ff7nx_marginblack` has named since it was written and which nothing in this
# module writes. MEASURED on the vanilla archive:
#
#     tiles with texture_id2 != 0        107,677
#       src2 == src1                         707   ( 0.66%)
#       src2 != src1                     106,970   (99.34%)
#
# The two sources are independent in the format and independent in the data.
# Moving the base cell does not move the fx cell and cannot desynchronise it.
#
# THE BUILD ALREADY DOES THIS AND IT SHIPS. In `md_e1`, 850 fx tiles have had
# their base relocated (slot 0 -> slots 2/6/7/8, new sx,sy) by marginpage
# while `fx_page` and `(sx2,sy2)` stayed byte-identical -- 0 of 850 changed.
# That is in build 68, on the SD card, working.
#
# WHAT IS ACTUALLY NEW is only the base page's DEPTH. Two things bound that
# risk:
#   * UV is NORMALISED. `T_SRC_X_BIG` is `cx * (UV_SCALE//GRID)` -- a fraction
#     of the page, not a texel count -- so 256px and 512px pages produce the
#     same UV. Page size was already decoupled; that is why 512px works.
#   * MIXED DEPTHS ALREADY SHIP IN THE STOCK GAME. Vanilla `md_e1` draws 128
#     tiles whose base is DEPTH 1 and whose fx page (slot 26) is DEPTH 2. The
#     engine resolves each page's type from the file independently.
#
# INFERRED, and say so: the mirror case -- base depth 2 at 512px with a
# depth-1 256px fx page -- has never been observed on hardware. Vanilla proves
# the two are not coupled; it does not prove this direction. That is what the
# scoped test is for.
#
# THE PARTNER SIDE STAYS VETOED. An fx frame lives in the additive/average
# band (MEASURED: 47,363 of 47,653 partner references in 0x0F-0x17, 290 in
# 0x18-0x19, ZERO opaque) and depth-2 additive needs slots 33-39, which do not
# become textures on this port (s2.3). Promoting a partner would silently turn
# an additive frame opaque. Do not.
#
# MEASURED COST of the base side alone, on the build-68 archive:
#
#     promotable cells      24,938   carrying 68,282 tiles
#     coverage              68.8% -> 78.3%   (+9.6 points)
#     new depth-2 pages       +382   depth-1 pages emptied  -61
#     memory                +0.78 MB per affected field, mean
#     fields over 16 pages       0   (max after = 16; md_e1 17 -> 14)
#
# 18,250 further base cells are still held by TRUE_BLACK. Separate question.
#
# ===================================================================
# BUILD 69 SHIPPED THIS AND IT BROKE OVERLAY ANIMATIONS ON HARDWARE.
# TURNED OFF PENDING THE REAL FIX. FINDINGS-162.
# ===================================================================
#
# Reported: rectangular blocks of wrong content wherever an animated overlay
# draws -- Wall Market (mrkt2, confirmed by match, corr 0.552) and Aerith's
# house. Coverage went 68.8% -> 84.0% and fidelity improved everywhere, so the
# RESTING frame is right; what broke is the ANIMATED frame.
#
# WHAT IS NOW MEASURED, and it narrows the cause to one thing:
#   * src2 IS a real runtime coordinate. In vanilla `md_e1`, many tiles share
#     ONE base cell (0,0) while carrying DISTINCT src2 values on the SAME fx
#     page -- (32,240), (48,240), (64,240)... If the engine sampled the fx
#     page with the base UV those tiles would all draw the same cell and the
#     distinct values would be dead data. So s2 of FINDINGS-161 is right that
#     the two sources are independent.
#   * What s4 got WRONG is that it checked DEPTH and never checked SIZE.
#     Vanilla's one mixed pair (`md_e1` base d1@256 + fx d2@256) matches in
#     SIZE. Our depth-2 pages are 512px and every fx page in the archive is
#     depth-1 at 256px, so promoting a base creates a 512/256 pair that
#     vanilla NEVER ships and that nothing has ever exercised.
#   * INFERRED, and it is the leading candidate: the engine scales the fx
#     source by a page width it takes once per tile. With a 512px base the fx
#     UV lands on a fraction of the intended cell -- which is exactly
#     "rectangles of flat, wrong content".
#
# BUILD 70. The pair now MOVES TOGETHER onto pages of the SAME SIZE, each
# keeping its own grid coordinate -- so src_x2/src_y2 stay valid as written
# and only the fx PAGE byte is repointed. A base whose partners cannot all be
# seated is WITHDRAWN, so the build-69 half-moved pair cannot be constructed.
#
# MEASURED, 160 fields, offline chain:
#     coverage 61.11% -> 69.23%   (+8.11 points, +11,728 tiles)
#     half-moved (base d2 + fx d1) ....... 0
#     size-mismatched fx tiles ........... 0
#     dangling fx references ............. 0
#     paired fx tiles, both sides 512px .. 11,729
#     max pages after 16, none over
#     ANIMATED FRAME RENDERED AND COMPARED: md_e1 mean|d| 0.01, no wrong-
#     content rectangles, no new black. That is the check build 69 lacked.
#
# ONE KNOWN EXCEPTION: `uutai1` 1024 -> 1023 tiles. Understood, not mysterious
# -- an fx base is seated by tile-count order, then withdrawn when a partner
# will not fit, and the ordinary cell it displaced does not share its grid
# coordinate so the freed seat cannot be handed back. The proper fix is to
# decide seatability BEFORE the main seating pass. See FINDINGS-163 s7.
# THE MULTI-PALETTE VETO. FINDINGS-165. Leave this ON.
# A cell drawn through more than one palette carries its variation IN the
# palette; a depth-2 page has none, so promoting it collapses every tile that
# shares it to one colour. Only safe when the mod ships exact art per palette.
MULTI_PALETTE_VETO = True

# THE VETO'S ONE EXCEPTION, MEASURED. FINDINGS-228, HANDOFF-227 s5.6.
#
# The veto refuses a multi-palette cell on this stated ground:
#
#     "Keying by (slot,sx,sy,PAL) does not save it: Cosmos ships only `_00`
#      for these pages, so every variant BORROWS palette 0's art and they all
#      come out identical anyway."
#
# THAT CLAIM IS TRUE AS BUILD 109 SHIPS, and `_kmpal.py` measured exactly why:
# `KEEP_ART_ON_BORROW` is True, so the `_detail_transfer` arm at the top of
# `source_cell`'s borrow branch is DEAD CODE and a borrowed cell keeps
# Cosmos's palette-0 pixels whatever palette the tile names. 393 of 458
# multi-palette cells come back BYTE-IDENTICAL across their palettes.
#
# It stops being true when the transfer is applied, because
# `_detail_transfer(art565, tgt565)` takes COLOUR from `tgt565` -- the paletted
# page rendered through THIS tile's palette -- so two palettes give two
# different textures. MEASURED over the whole archive (701 fields):
#
#     multi-palette cells                458    113,780 tiles   17.9% of drawn
#       admitted today (exact art)         0                    <- see below
#       hue-broken, skip the transfer    393    103,725 tiles
#       CLEAN                             65     10,055 tiles
#         variants differ with transfer   30      3,156 tiles    0.50% of drawn
#
#     on those 30: per-palette mean RGB spread / VANILLA's
#                  median 0.83, p10 0.58, p90 1.22
#                  correlation with vanilla's per-palette mean, median 1.000
#
# So the gradient SURVIVES on that population -- and it is 0.50% of the drawn
# tiles, not the 29% the coverage gap is made of. This is a small, real win and
# it is not the ceiling moving.
#
# TWO THINGS THIS MEASUREMENT ALSO SETTLES, both of which were assumed:
#   * ZERO cells in the archive have exact art at every palette that draws
#     them, so the `set(pals) <= have` arm below has never once admitted a
#     cell. The "20% at the cell's own palette" figure in build 109's log is
#     over ALL layer-2+ cells, and none of that 20% is multi-palette.
#   * 85.8% of multi-palette cells are HUE-BROKEN. They skip the transfer, so
#     they are exactly as identical with it on as with it off. A near-black
#     cell has no chromaticity, which is what makes it hue-broken and what
#     makes it unfixable here -- HANDOFF-227 s3.2 predicted this and it holds.
#
# ADMISSION RUNS THE FALSIFIER. `_multipal_admit` builds the real textures for
# every palette the cell is drawn through and refuses the cell unless they
# actually differ, so a cell that would collapse to one colour CANNOT be
# admitted by construction rather than by argument. False restores build 109.
MULTIPAL_RECOLOUR = True
MULTIPAL_MIN_FRAC = 0.10       # texels that must differ between the closest
MULTIPAL_MIN_MEAN = 2.0        # pair of palettes, and by how much -- 2/255 is
                               # below the 5-bit step, so rounding cannot
                               # reach it
MULTIPAL_MIN_CORR = 0.0        # the promoted per-palette means must move the
                               # SAME WAY as vanilla's, not merely differ.
                               # 12% of the passing cells came back
                               # anti-correlated; this is what excludes them.

# ON AS OF BUILD 110, AND ONLY BECAUSE `MULTIPAL_RECOLOUR` GIVES IT SOMETHING
# TO SEAT. FINDINGS-228.
#
# These two flags are each downstream of the other, which is why both measured
# at zero on their own:
#
#   * the cells this flag admits are one cell drawn through many palettes --
#     that IS what an animated beam or waterfall is -- so `MULTI_PALETTE_VETO`
#     refused every one of them;
#   * and the cells `MULTIPAL_RECOLOUR` admits are fx BASES, so `cand` dropped
#     them as `fx_cells` before the veto ever ran.
#
# MEASURED through the real pass chain, three arms, 471 fields:
#
#     off  = build 109                         fx = this flag alone
#     on   = both
#
#     fx  vs off   BYTE-IDENTICAL in 471 of 471 fields
#     on  vs off   26 fields change, 26 cells admitted, +2,531 tiles
#                  promoted to truecolor
#                  truecolor pages  +0        total pages  -33
#                  texture bytes    -2,112 KB (the emptied paletted pages
#                                    are compacted away, so it is CHEAPER)
#                  tiles lost 0, new slots >= 29: 0, page budget: not reached
#     fx pairs 3,976: px mismatches 0, depth mismatches 0, dangling 1 -> 1
#                  (that one is pre-existing and identical in both arms)
#
# AND IT WAS A CRASH, NOT A VETO, THAT HID THIS. See `_edge_of` below:
# with this flag on, seating a pair raised KeyError on the PARTNER key and
# build.py logged the whole field as "not repacked". HANDOFF-227 s3.1's A/B
# was reading that.
# ...AND IT IS OFF AGAIN. `wcrimb_2` CRASHES ON HARDWARE. FINDINGS-232.
#
# Atmosphere report `01786995986`, resolved through `nxmap`:
#
#     winmain 0x67DB30 +0xC34
#       field_main_loop 0x60E5B7 +0xB30
#         field_sub_6388EE +0x7C
#           field_draw_everything 0x63A60B +0x3E8
#             0x640213 +0x1D8          <- the TEXTURE LOADER
#               0x66E641 +0x364        <- engine texture create
#                 [thunk] -> native shim -> nn::diag::detail::Abort
#
# This is the LOAD path, not the draw path, and it is our data. What build 110
# changed in that field is exactly one thing:
#
#     build 109   fx pairs (base slot 0, fx slot 15)   BOTH depth-1 paletted
#                 pages [0(d1), 11,12,13,14(d2), 15(d1), 26,27,28(d2)]
#     build 110   fx pairs (base slot 27, fx slot 28)  BOTH depth-2 512px
#                 pages [11,12,13,14,26,27,28] -- all truecolor, no paletted
#
# So `PROMOTE_FX_BASE` does not only promote the BASE. The seating loop's
# column allocator moves the PARTNER with it (`todo`/`fx_slot_of`), and an fx
# frame ends up on a 512px truecolor page. FINDINGS-161 flagged the mirror of
# this as never observed on hardware and said so explicitly; this direction is
# just as untested and it is what the console is refusing.
#
# 26 fields now ship that configuration and `wcrimb_2` is the heaviest of them
# -- 7 truecolor pages, 3.5 MB, and NO paletted page left at all. Whether the
# trigger is the pair, the size or the last paletted page going away is not
# settled, and a crash is not the place to keep guessing.
#
# WHAT THIS COSTS: build 110's +3,064 truecolor tiles across 26 fields, since
# `MULTIPAL_RECOLOUR` only ever admits fx base cells. Build 111's palette work
# is independent and unaffected.
# ...AND VANILLA SETTLES WHY. MEASURED over the STOCK archive, 701 fields,
# every fx pair in the game:
#
#     base d1 + fx d1     104,797 tiles   422 fields    the normal case
#     base d1 + fx d2         128 tiles     1 field     md_e1, slot 26
#     base d2 + fx d1               0
#     base d2 + fx d2               0       <- what build 110 made, 26 fields
#
# An fx BASE is on a paletted page in 104,925 of 104,925 cases. The stock game
# has never once asked this console to draw an animated tile whose base page is
# truecolor, in either the mixed or the matched form. That is not "untested",
# it is "never shipped", and the crash is the console saying so.
#
# THE ONE PRECEDENT RUNS THE OTHER WAY. `md_e1` proves the FX page may be
# depth 2 while the base stays paletted. So promoting the PARTNER and leaving
# the base alone reproduces a configuration the stock game already ships --
# which is the safe half of this idea and is where the fx-page sharpness the
# user has been asking about actually lives. It buys no truecolor TILES (a
# tile counts by its base page) but it is the only part of this with a
# hardware precedent.
PROMOTE_FX_BASE = False

# PER-FIELD OPT-IN, SO THE HARDWARE QUESTION COSTS ONE FIELD AND NOT 26.
#
# The 3,064 tiles build 110 gained are not recoverable by argument: vanilla
# says the base is always paletted, and the only way to learn whether this
# console tolerates otherwise is to ask it. This makes that ask cheap.
#
# Put ONE field in here, build, and open it. If it renders, the configuration
# is survivable and the flag can widen a few fields at a time; if it crashes,
# exactly one field is affected and the answer is final.
#
#     PROMOTE_FX_BASE_FIELDS = frozenset(('las3_2',))
#
# `las3_2` is the right first candidate: it is the largest single gain (+731
# tiles, 58.8% -> 99.8% truecolor), it ends with a paletted page still present
# (6 pages, 1 paletted) unlike `wcrimb_2`, and it is easy to reach in game.
# An empty set means the flag is off everywhere, which is build 109 behaviour.
PROMOTE_FX_BASE_FIELDS = frozenset()

# LOW-SLOT PROBE -- put truecolor pages in free slots 0..25 instead of the
# 29+ range that does not render on this port. Rationale, disassembly and
# measured headroom are at the use site in `dense_repack`. False restores
# build 54 exactly.
LOW_SLOT_PROBE = True
LOW_SLOT_MAX_TC = 7            # 7 covers the whole archive: pages a field
                               # needs to be 100% truecolor, MEASURED --
                               # 1p:46 2p:246 3p:166 4p:179 5p:49 6p:11 7p:4
# EVERY FIELD. Proven on 18 Wall Market fields in build 56.
#
# Those fields ran 4-5 truecolor pages with pages living in slots 6, 8, 9 and
# 10 and rendered clean on hardware, where build 55 gave the SAME fields the
# same page count in slots 29/30 and they went black. Same count, different
# slots, opposite result -- so the ceiling was PLACEMENT, not capacity, and
# the engine's own rule (type from section 9, any type-2 page below slot 33
# drawn opaque) is what makes a low slot work.
#
#     mrkt1   [9, 10, 26, 27, 28]      mkt_ia  0 -> 99 cells truecolor
#     mrkt2   [8,  9, 26, 27, 28]      mkt_s1  0 -> 129
#     onna_2  [6,     26, 27, 28]      mkt_s3  0 -> 91
#     Wall Market overall: 73.2% -> 76.4% of cells truecolor,
#     and ZERO pages at slot >= 29.
#
# An empty set means every field, which the guard below already handles.
LOW_SLOT_FIELDS = frozenset()

# PLACEMENT A/B, FINDINGS-156.  'asc' is build 64 exactly (lowest free slot
# first).  'desc' hands out the HIGHEST free low slot first, which changes
# only WHICH slot each truecolor page lands in -- same pages, same cells,
# same coverage, same bytes.  It is a probe for whether the Wall Market
# black tiles follow the SLOT or follow the CONTENT, and it costs nothing.
LOW_SLOT_ORDER = 'desc'   # FINDINGS-156 placement probe. 'asc' = build 64.
# Highest low slot the probe may use.  25 is build 64.  14 keeps every
# truecolor page inside the depth-1 OPAQUE band (0x00-0x0E), so the probe
# cannot accidentally test blend mode at the same time as placement --
# FINDINGS-141 s4 says the depth-2 blend selection was never verified in
# the ARM64 image, and slots 15-23 are ADDITIVE / 24-25 AVERAGE for depth 1.
LOW_SLOT_TOP = 14         # FINDINGS-156 placement probe. 25 = build 64.

# RAISED 3 -> 7 WITH `field_bg_native.D2_OPAQUE_SLOTS`, BUILD 106.
#
# THESE TWO MUST MOVE TOGETHER AND NEITHER IS SUFFICIENT ALONE.
# `D2_OPAQUE_SLOTS` decides how many free truecolor SLOTS exist (it is what
# `BANDS[4]` is built from); this decides how many of them a field may SPEND.
# Raising only the slot count leaves `cap_big = min(max_tc - have_tc, ...)`
# at 3 and the change is inert; raising only this leaves `free_slots` at 3
# and it is equally inert.
#
# AND THE SETTINGS FILE OVERRIDES THIS CONSTANT. Both build paths do
#     MAX_TRUECOLOR_PAGES = saved['__global__']['field_bg_truecolor_pages']
# (7th_heaven_nx.py:2943 and :3149), so `settings.json` must say 7 as well or
# this edit does nothing at all. That is not hypothetical -- it is the same
# shape as the dead "Field background budget (MB)" control this module's
# byte-budget note describes.
MAX_TRUECOLOR_PAGES = 3   # REVERTED with D2_OPAQUE_SLOTS, FINDINGS-218

# CONVERT A WHOLE 32-UNIT PARALLAX PAGE INTO A TRUECOLOR ONE IN ITS OWN SLOT.
# FINDINGS-249. See the arm in `dense_repack` for the measurement and the
# legality argument. `SEVENTH_NX_NO_INPLACE_BIG=1` restores build 119.
INPLACE_BIG = os.environ.get('SEVENTH_NX_NO_INPLACE_BIG') != '1'

# THE PER-FIELD RUNTIME MEMORY CEILING, IN MB, AND IT BOUNDS `INPLACE_BIG`
# ALONE.
#
# A conversion is free in PAGES and costs 3.07 MB at 768px, and
# `field_bg_budget_mb` ships at 0.0 (UNLIMITED), so without this the arm has
# no bound at all -- and `field_load_textures` abandons its whole loop on the
# first texture it cannot allocate, which is what black squares are.
#
# 27.5 is not a guess. `mrkt4` is the archive's heaviest field at 27.31 MB and
# it ships in build 119, so this is the largest figure with evidence behind
# it. Nothing else consults it, and no field reaches it today, so it cannot
# take anything away from build 119.
FIELD_MB_CAP = float(os.environ.get('SEVENTH_NX_FIELD_MB_CAP') or 27.5)
_MAX_TOTAL_PAGES_DEFAULT = 12

# HOW MUCH OF A FIELD'S TRUECOLOR BUDGET THE 32-UNIT POPULATION MAY TAKE.
#
# See the long note at the seat split. A 32-unit page holds 64 cells against a
# 16-unit page's 256, so the parallax population is expensive per page, and
# left unbounded a field like `hill` (347 parallax cells, 308 interior ones)
# would spend six pages on the backdrop and two on everything else.
#
# This is a CEILING on the share, not a reservation: a field whose parallax
# population needs less takes less, and a field with none is untouched. The
# floor of one page (in the seat split) exists so that rounding cannot refuse
# an entire backdrop on a field with a small cap.
#
# 1.0 -- i.e. NOT A CEILING AT ALL by default, because the two hard
# constraints at the seat split (the 16-unit half is served first and in full;
# the 32-unit half is paid for out of the pages it frees) already bound this
# tightly, and a second overlapping limiter would only make the result harder
# to attribute. It is kept as an A/B lever: 0.0 keeps the whole classification
# and the separate grids in place while promoting nothing big, which is a
# cheaper and more informative comparison than the veto because it isolates
# the BUDGET from the MECHANISM.
BIG_PAGE_SHARE = float(os.environ.get('SEVENTH_NX_BIG_PAGE_SHARE', '1'))


def max_total_pages():
    """
    The total page ceiling, from the 7th Heaven GUI.

    THIS WAS WIRED TO NOTHING. The GUI writes
    `SEVENTH_NX_FIELD_BG_MAX_TOTAL_PAGES` and only `field_bg_repack` read it
    -- and `field_bg_repack` is no longer called. This pass used a hardcoded
    12 and never looked at the setting, so a build with the GUI showing 16
    was enforcing 12, the log PRINTED 16, and the field that came out held
    15. Three numbers, none of which agreed.

    Read through `field_bg_repack.max_total_pages()` so there is exactly one
    parser for the setting, and fall back to the constant if that module is
    not importable for some reason.
    """
    try:
        import field_bg_repack as FR
        v = FR.max_total_pages()
    except Exception:                                          # noqa: BLE001
        return _MAX_TOTAL_PAGES_DEFAULT
    if not v:                       # 0 == no cap, per DEFAULT_MAX_TOTAL_PAGES
        return 1 << 30
    return int(v)


# The TOTAL page ceiling, and it is not optional. The truecolor cap alone is
# meaningless because this pass is ADDITIVE: the originals stay for every cell
# that did not promote, and `ff7nx_marginpage` has already added ~1 page per
# field before this runs. MEASURED on hardware with only the truecolor cap:
# mean 7.4 pages, max 17, 595 fields grown, and purple patches where
# `field_load_textures` gave up. Vanilla's worst field is 12.
# MEASURED against the build that runs on hardware: over 110 fields repacked
# with the real .iro, the working promotion never put more than THREE
# truecolor pages in one field (mean 1.41). Both frozen builds averaged 4.7
# with every page truecolor, at a LOWER total page count -- so the truecolor
# count is the one quantity that separates them. Vanilla itself ships 26
# truecolor pages across 400 fields; this path is a rarity in the stock game
# and does not survive being made the rule.


def _multipal_admit(ks, keys, pages, arrays, pal565, art_for, pals_for,
                    hc, org, scale):
    """
    MULTIPAL_RECOLOUR's falsifier, run at ADMISSION TIME. See the constant.

    `ks` is every (slot,sx,sy,pal) key for ONE cell. Returns True only when
    the promoted textures -- built by `source_cell` itself, with the detail
    transfer forced on, so this is the picture the build would actually seat
    and not a model of it -- differ from each other AND move the same way
    vanilla's do.

    A cell that fails here is refused exactly as before. That is the whole
    safety argument: the collapse the veto protects against is detected on
    the real bytes rather than predicted from a rule.
    """
    if not ks or len(ks) < 2:
        return False
    # FALSIFIER 3 (HANDOFF-227 s5.6): a hue-broken cell SKIPS the transfer, so
    # its variants are identical whatever this flag says. Refuse the cell if
    # ANY palette is hue-broken -- not just the broken ones -- because a cell
    # half-recoloured and half-not is a seam, and 85.8% of this population is
    # hue-broken anyway.
    if art_for is None:
        return False
    for k in ks:
        if hue_broken(k, arrays, pal565, art_for, hc, org) > HUE_BROKEN_DIST:
            return False
    # A THROWAWAY `Stats`. These `source_cell` calls are a TEST, and a cell
    # this function refuses is never seated -- counting its borrow in the
    # field's own numbers would make `from_art_borrow` a count of cells
    # considered rather than cells drawn, which is the reporting error
    # HANDOFF-227 s6 records twice.
    st = Stats()
    vs, vans = [], []
    for k in ks:
        rec = keys.get(k)
        if rec is None:
            return False
        edge = BIG_TILE if rec.get('edge') == BIG_TILE else TILE
        try:
            c = source_cell(k, rec, pages, arrays, pal565, art_for, pals_for,
                            st, scale, org, False, edge, force_transfer=True)
        except Exception:                                      # noqa: BLE001
            return False
        vs.append(c)
        pl = k[3] if 0 <= k[3] < len(pal565) else len(pal565) - 1
        idx = arrays[k[0]][k[2]:k[2] + edge, k[1]:k[1] + edge]
        vans.append(pal565[pl][idx])
    if any(c.shape != vs[0].shape for c in vs):
        return False

    def _mean(a):
        r, g, b = _unpack565(a)
        return np.array([r.mean(), g.mean(), b.mean()], np.float64)

    # THE CLOSEST PAIR, not the mean pair. One palette that collapses onto
    # another is the defect; an average over pairs would hide it.
    for i in range(len(vs)):
        for j in range(i + 1, len(vs)):
            ri, rj = _unpack565(vs[i]), _unpack565(vs[j])
            d = (np.abs(ri[0].astype(np.int32) - rj[0]) +
                 np.abs(ri[1].astype(np.int32) - rj[1]) +
                 np.abs(ri[2].astype(np.int32) - rj[2])) / 3.0
            if (d > 0.5).mean() < MULTIPAL_MIN_FRAC:
                return False
            if d.mean() < MULTIPAL_MIN_MEAN:
                return False
    m = np.array([_mean(c).mean() for c in vs])
    v = np.array([_mean(a).mean() for a in vans])
    if m.std() > 0.5 and v.std() > 0.5:
        if float(np.corrcoef(v, m)[0, 1]) < MULTIPAL_MIN_CORR:
            return False
    return True


def dense_repack(sec3, sec9, field='', art_for=None, pals_for=None, px=256,
                 max_tc=MAX_TRUECOLOR_PAGES):
    """
    Promote as many cells as `max_tc` truecolor pages will hold, densely.

    Cells that do not fit KEEP THEIR ORIGINAL PAGE, which stays present. Those
    pages already carry Cosmos art -- `ff7nx_marginart` writes 335,457 cells of
    it into the paletted pages -- so nothing falls back to vanilla pixels and
    the widescreen alignment holds. The difference is colour depth, not art.

    fx cells are never promoted: a tile and its fx page share one u,v, so the
    pair has to move together or not at all, and "not at all" is free.
    """
    st = Stats()
    # build.py RETRIES this function with a falling truecolor ceiling, so drop
    # any map an earlier attempt on this same field left behind before we start
    # writing a new one. Without this a field that promoted 200 cells at
    # max_tc=3 and 40 at max_tc=1 would keep 160 entries describing cells that
    # the accepted section never moved.
    if field:
        ORIGIN.pop(field, None)
    try:
        surv = DC.survey(sec9)
    except Exception as exc:                                   # noqa: BLE001
        st.refused = '%s' % str(exc)[:60]
        return sec9, st
    pages = {p.slot: p for p in surv['pages']}
    st.pages_before = len(pages)
    tiles = MB.read_tiles(sec9, surv, pages)
    if not tiles:
        st.refused = 'no tiles'
        return sec9, st

    pal565, npg, cpp = _pal_rgb(sec3)
    arrays = {}
    for sl, p in pages.items():
        if p.depth == 1:
            arrays[sl] = np.frombuffer(p.data, np.uint8).reshape(256, 256)
        else:
            arrays[sl] = np.frombuffer(p.data, '<u2').reshape(p.px, p.px)

    keys, fx_of = collect(sec9, pages, tiles)
    for k, rec in keys.items():
        # BOUND BY THE ARRAY, NOT BY THE HEADER.
        # `npg` is section 3's DECLARED palette count; `len(pal565)` is how
        # many rows were actually built. When the two disagree -- and after
        # the margin passes have rewritten section 3, they can -- `k[3] % npg`
        # yields an index past the end of `pal565`, `source_cell` raises
        # IndexError, and build.py logs the whole field as "not repacked".
        _np = len(pal565)
        rec['pal'] = k[3] if -1 <= k[3] < _np else (k[3] % _np if _np else 0)
        rec['key'] = _uses_key(pages, arrays, k)
    # See bare_keys() and the atlas-gap arm in source_cell. Computed once per
    # field over tiles already read, not per cell.
    if ATLAS_GAP:
        try:
            _bare = bare_keys(pages, arrays, tiles, keys)
        except Exception:                                      # noqa: BLE001
            _bare = set()
        for k in _bare:
            keys[k]['bare'] = True
        st.bare = len(_bare)
    # See MARGIN_OVERLAY_ALPHA. Computed once per field, from the tiles this
    # function has already read, and used for ONE decision -- whether the
    # atlas-gap arm may take the mod's alpha for this cell. It sets no other
    # field on `rec` and nothing else consults it.
    if ATLAS_GAP and MARGIN_OVERLAY_ALPHA:
        try:
            _mo = margin_overlay_keys(pages, arrays, tiles, keys)
        except Exception:                                      # noqa: BLE001
            _mo = set()
        for k in _mo:
            keys[k]['margin_l2'] = True
        st.margin_l2 = len(_mo)
    # See BLEND_PARTIAL and backdrop_keys(). Computed once per field from the
    # tiles already read, exactly as the two blocks above are, and used for
    # ONE decision -- what colour a partial-alpha texel blends toward. It sets
    # no other field on `rec`, nothing else consults it, and a field where it
    # returns nothing comes out byte-identical.
    #
    # The `except` is not laziness. This walks tile destinations and page
    # arrays that four earlier passes have rewritten; a field where that walk
    # cannot be completed must lose the BLEND, not the whole repack. That is
    # the same failure mode `bare_keys` guards above, and losing a repack
    # costs the field every truecolor page it was going to get.
    if BLEND_PARTIAL or MODCLEAR_COVER:
        try:
            _bd, _cv = backdrop_keys(pages, arrays, tiles, keys, pal565, px)
        except Exception:                                      # noqa: BLE001
            _bd, _cv = {}, {}
        if BLEND_PARTIAL:
            for k, blk in _bd.items():
                keys[k]['under'] = blk
        if MODCLEAR_COVER:
            for k, m in _cv.items():
                keys[k]['cover'] = m
        st.backdrop_cells = len(_bd)
        dense_repack.backdrop_cells = (
            getattr(dense_repack, 'backdrop_cells', 0) + len(_bd))
        dense_repack.cover_cells = (
            getattr(dense_repack, 'cover_cells', 0) + len(_cv))
    # See PROMOTE_FX_BASE. `fx_partners` is the side that must stay paletted
    # (an fx frame is drawn through the additive/average band, which has no
    # depth-2 equivalent that renders on this port). `fx_cells` is what the
    # candidate filter vetoes: both sides when the flag is off, partners only
    # when it is on. A cell that is BOTH a base and someone else's partner
    # stays vetoed either way -- it is in `fx_partners`.
    fx_partners = set()
    for v in fx_of.values():
        fx_partners |= v
    # See PROMOTE_FX_BASE_FIELDS. Resolved ONCE per field and used by both
    # sites, so the candidate filter and the seating loop can never disagree
    # about whether this field promotes fx bases.
    _fxbase = PROMOTE_FX_BASE or (field or '').lower() in PROMOTE_FX_BASE_FIELDS
    fx_cells = fx_partners if _fxbase else (set(fx_of) | fx_partners)

    # PRIORITY: the cells the most tiles draw. Those cover the most screen.
    # A CELL THAT USES THE KEY STAYS PALETTED.
    #
    # On a truecolor page 0x0000 is the only value that can mean transparent,
    # and the black speckles say the console draws it rather than discarding
    # it on the layer-1 pages we promote. Vanilla only ever ships keyed
    # truecolor cells on layer 2 (`gldst`), which is not where these are.
    # This is the same rule `field_bg_repack.cells_transparent` applied, and
    # the build that had no speckles applied it.
    # THE COLOUR-KEY VETO, NARROWED TO WHAT THE DOCSTRING ALWAYS SAID.
    #
    # Constraint 2 at the top of this module: "THE COLOUR KEY IS NOT A CUT-OUT
    # ON LAYER 1 ... Layer 1 has nothing behind it, so a 'transparent' pixel
    # there was always entry 0's colour; baking that colour is exactly
    # equivalent. A cell used by any layer-2+ tile keeps 0x0000."
    #
    # The code did not do that. `_uses_key` asks only "does this cell contain
    # index 0" and the filter vetoed every cell that does, layer 1 included --
    # so the case the docstring explicitly calls safe was the one being
    # refused. MEASURED over 265 vanilla fields:
    #
    #     depth-1 cells                     196,699
    #     containing index 0                 62,646   (31.8%)
    #        also drawn by a layer-2+ tile   40,268   <- real cut-outs, veto
    #        LAYER 1 ONLY                    22,378   <- were vetoed anyway
    #     promotable before                 133,630
    #     promotable after                  155,456   (+16.3%)
    #
    # Layer-2+ keyed cells stay vetoed. Whether a truecolor page can carry a
    # working cut-out at all is the open question (0x0000 on depth 2 -- this
    # project has claims both ways and neither is settled), and answering it
    # is not a prerequisite for the 22,378 cells above.
    # `l1_over` is the SCOPE on PROMOTE_LAYER1_KEY, not a second switch --
    # FINDINGS-171. With the flag off this reads exactly as it did before
    # (`not PROMOTE_LAYER1_KEY` vetoes every keyed layer-1 cell); with it on,
    # only the cells whose key can actually reveal something are vetoed.
    cand = [k for k in keys
            if k not in fx_cells and pages[k[0]].depth == 1
            and not (BIG_TILE_VETO and keys[k].get('big'))
            and not keys[k].get('nogrid')
            and not (keys[k]['key']
                     and ((keys[k]['l2'] and not PROMOTE_L2_KEY)
                          or (not keys[k]['l2']
                              and (not PROMOTE_LAYER1_KEY
                                   or keys[k].get('l1_over')))))]
    if PROMOTE_LAYER1_KEY:
        _l1o = sum(1 for k in keys
                   if keys[k]['key'] and not keys[k]['l2']
                   and keys[k].get('l1_over'))
        if _l1o:
            dense_repack.l1key_overlap_vetoed = (
                getattr(dense_repack, 'l1key_overlap_vetoed', 0) + _l1o)
    # ---- ONE CELL, MANY PALETTES. FINDINGS-165.
    #
    # A light beam, a waterfall or a column of smoke is ONE 16x16 source cell
    # drawn hundreds of times across the screen, and the PALETTE is what makes
    # each instance different. MEASURED:
    #
    #   field     group      cells  tiles  tiles/cell  pals/cell  multi-pal
    #   mrkt2     fx base        1    406       406.0       4.00     100.0%
    #   mrkt2     ordinary   1777   1777         1.0       1.00       0.0%
    #   nivl_b22  fx base        1   2199      2199.0      10.00     100.0%
    #   ancnt2    fx base        1   1783      1783.0       9.00     100.0%
    #
    # A depth-2 page has NO palette. Promote that cell and all those tiles
    # collapse to ONE colour -- a grid of identical patches where a graded
    # beam used to be. Reported from hardware as "it repeats the same texture
    # in various locations", with the beams gone.
    #
    # Keying by (slot,sx,sy,PAL) does not save it: Cosmos ships only `_00` for
    # these pages, so every variant BORROWS palette 0's art and they all come
    # out identical anyway.
    #
    # THIS IS NOT AN FX PROBLEM. Build 68 -- with PROMOTE_FX_BASE off --
    # already promotes 87 such cells across 351 fields, carrying 23,417 tiles,
    # and NOT ONE has exact art for every palette it is drawn through. That is
    # this defect, already shipping.
    #
    # The veto is exact: a cell may be promoted if only one palette draws it,
    # or if the mod ships EXACT art for every palette that does.
    _bypal = {}
    for t in tiles:
        p = pages.get(t.slot)
        if p is not None and p.depth == 1:
            _bypal.setdefault((t.slot, t.sx, t.sy), set()).add(t.pal)
    # RESOLVED HERE RATHER THAN BELOW. `_multipal_admit` calls `source_cell`,
    # which needs marginpage's origin map to find Cosmos's art at all -- ask
    # with post-split slot numbers and the answer is "the mod ships nothing",
    # which is the renumbering trap of HANDOFF-222 s8 and would silently make
    # every admission fail. The block that used to compute this a few lines
    # down now reuses `_org`.
    _hc = {}
    try:
        import ff7nx_marginpage as _MPG
        _org = _MPG.ORIGIN.get(field) or None
    except Exception:                                          # noqa: BLE001
        _org = None
    _mp_force = set()
    if MULTI_PALETTE_VETO:
        _kept = []
        _mp_seen = {}
        _cand_set = set(cand)
        _scale = max(1, px // 256)
        for k in cand:
            pals = _bypal.get((k[0], k[1], k[2]), ())
            if len(pals) > 1:
                # `pals_for` IS None FOR ANY FIELD THE MOD SHIPS NO ART FOR.
                # FINDINGS-170.
                #
                # build.py:2960 --
                #     _af = _pf = None
                #     if art is not None and name.lower() in art.fields():
                #         _af, _pf = art.open(name), art.palettes
                #
                # so a field outside the mod reaches here with `pals_for`
                # None, and calling it raised TypeError: 'NoneType' object is
                # not callable. build.py catches that per field and logs
                # "not repacked", which means the field lost its ENTIRE
                # truecolor promotion -- not just the multi-palette cells.
                # Four fields in builds 70 and 71: crcin_2.xone, games_2.xone,
                # md1_1.xone, nmkin_3.xone. Builds 66-69 have zero such lines;
                # this veto introduced it.
                #
                # The rest of this function already guards the same way
                # (`if HUE_FIRST and art_for is not None`). This one did not.
                #
                # THE SEMANTICS ARE UNCHANGED BY THE GUARD. The rule is
                # "promote a multi-palette cell only when the mod ships exact
                # art for EVERY palette it is drawn through". No art at all is
                # emphatically not that, so the answer is veto -- which is
                # exactly what an empty `have` already produces. The guard
                # only stops the crash on the way to the same decision.
                have = set((pals_for(k[0]) if pals_for is not None else None)
                           or ())
                if not set(pals) <= have:
                    # THE ONE EXCEPTION, AND IT RUNS ITS OWN FALSIFIER.
                    # See MULTIPAL_RECOLOUR. Decided once per CELL and cached,
                    # because the test costs one `source_cell` per palette and
                    # `cand` holds one key per palette of the same cell.
                    _c = (k[0], k[1], k[2])
                    if MULTIPAL_RECOLOUR:
                        ok = _mp_seen.get(_c)
                        if ok is None:
                            _ks = [(k[0], k[1], k[2], p) for p in sorted(pals)]
                            # EVERY palette of the cell must still be a
                            # candidate. A cell promoted at two palettes and
                            # left paletted at a third is a resolution seam
                            # between tiles of the same surface, which is the
                            # defect FINDINGS-213 spent a build on.
                            ok = (all(kk in _cand_set for kk in _ks)
                                  and _multipal_admit(
                                      _ks, keys, pages, arrays, pal565,
                                      art_for, pals_for, _hc, _org, _scale))
                            _mp_seen[_c] = ok
                            if ok:
                                dense_repack.multipal_admitted = (
                                    getattr(dense_repack,
                                            'multipal_admitted', 0) + 1)
                        if ok:
                            _mp_force.add(k)
                            _kept.append(k)
                            continue
                    dense_repack.multipal_vetoed = (
                        getattr(dense_repack, 'multipal_vetoed', 0) + 1)
                    continue
            _kept.append(k)
        if len(_kept) != len(cand):
            dense_repack.multipal_fields = (
                getattr(dense_repack, 'multipal_fields', 0) + 1)
        cand = _kept

    # Measured BEFORE the TRUE_BLACK filter, because it is what exempts a
    # cell from it. See below.
    #
    # `_hc` and `_org` are RESOLVED ABOVE, before the multi-palette veto, which
    # needs them. `_hc` is a cache keyed by cell, so the admission test's
    # entries are reused here rather than recomputed. The comment that used to
    # sit here still holds: `_org` is resolved unconditionally because
    # `source_cell` needs it whether or not HUE_FIRST is on.
    _hb = {}
    if HUE_FIRST and art_for is not None:
        _hb = {k: hue_broken(k, arrays, pal565, art_for, _hc, _org)
               for k in cand}
    if TRUE_BLACK > 0.0:
        # See TRUE_BLACK. A mostly-black cell keeps its paletted page so that
        # its black stays exactly black, instead of being lifted to 0x0001 and
        # drawing a blue seam against its unpromoted neighbours.
        #
        # ...UNLESS THE PALETTE CANNOT EXPRESS THE CELL AT ALL. FINDINGS-149.
        #
        # This filter is why mds5_5's sky is olive, and the counts are exact:
        #
        #     mds5_5   13 of 40 sky cells vetoed  ->  27/40 promoted
        #     mds6_3   35 of 40 sky cells vetoed  ->   5/40 promoted
        #
        # A dark sky cell is >=25% opaque black, so it is held on the paletted
        # page to keep that black exact -- and the page's palette has a bluest
        # entry of 41, so the cell renders olive. The trade is upside down
        # here: promoting costs a 0.9/255 lift on black, which is invisible,
        # and refusing costs the entire hue, which is what the user
        # photographed. So blackness only wins the argument when the paletted
        # version is otherwise faithful.
        cand = [k for k in cand
                if black_fraction(pages, arrays, pal565, k) < TRUE_BLACK
                or _hb.get(k, 0.0) > HUE_BROKEN_DIST]
    # HUE-BROKEN CELLS GO FIRST. FINDINGS-149, and see HUE_FIRST above.
    # Tile reuse remains the tie-breaker inside each group, so within the
    # broken set and within the sound set the old ordering is unchanged.
    # ---- A NEW CANDIDATE MUST NOT EVICT AN OLD ONE. FINDINGS-172.
    #
    # The truecolor budget is fixed (`cap` below is min(max_tc - have_tc,
    # free slots, page room)), so `cand` is a QUEUE and everything past the
    # cut-off stays paletted. Widening the eligibility rule therefore does not
    # only add cells -- it PUSHES CELLS OUT, and the ones pushed out are
    # whatever sorted last.
    #
    # Build 72 turned PROMOTE_LAYER1_KEY on and Wall Market grew flat tan and
    # olive blocks in the widescreen margin. MEASURED on mrkt2, same 1,536
    # cells promoted and the same 6 pages both ways:
    #
    #     flag OFF   layer 1: 831 tc / 224 pal    layer 2: 637 tc / 105 pal
    #     flag ON    layer 1: 958 tc /  97 pal    layer 2: 512 tc / 230 pal
    #
    # Layer 1 gained 127. **Layer 2 lost 125.** 75 of them in the 16:9 margin,
    # 78 of them rendering as ONE flat colour once evicted -- RGB(231,170,107),
    # the vivid tan of FINDINGS-68. A margin cell that falls back to its
    # paletted page falls back to the authored FILLER, not to softer art, so
    # eviction there is not a loss of sharpness. It is a hole in the picture.
    #
    # `DARKEN_MARGIN_PLACEHOLDERS` (FINDINGS-68 s3) does not cover these: it
    # only reaches cells sampled by layer-1 margin tiles, and every one of
    # these 125 is layer 2.
    #
    # So NEWLY-ELIGIBLE CELLS GO TO THE BACK. A cell that only became a
    # candidate because PROMOTE_LAYER1_KEY was switched on is a bonus; it may
    # take space nothing else wants and must never take space something else
    # already had. That makes this flag monotonic -- no cell can lose
    # truecolor because of it -- which is the cell-level form of checklist
    # item 2, "no field's truecolor tile count goes DOWN".
    _newly = ({k for k in cand
               if keys[k]['key'] and not keys[k]['l2']} if PROMOTE_LAYER1_KEY
              else set())

    def _rank(k):
        return (1 if k in _newly else 0,
                0 if _hb.get(k, 0.0) > HUE_BROKEN_DIST else 1,
                -len(keys[k]['tiles']), k)

    if HUE_FIRST and art_for is not None:
        _nb = sum(1 for k in cand if _hb.get(k, 0.0) > HUE_BROKEN_DIST)
        if _nb:
            dense_repack.hue_first_cells = (
                getattr(dense_repack, 'hue_first_cells', 0) + _nb)
            dense_repack.hue_first_fields = (
                getattr(dense_repack, 'hue_first_fields', 0) + 1)
        cand.sort(key=_rank)
    else:
        cand.sort(key=lambda k: (1 if k in _newly else 0,
                                 -len(keys[k]['tiles']), k))
    if _newly:
        dense_repack.l1key_deferred = (
            getattr(dense_repack, 'l1key_deferred', 0) + len(_newly))
    free_slots = [sl for sl in range(*BANDS[4]) if sl not in pages]
    # ---- LOW-SLOT PROBE. FINDINGS-145.
    #
    # Slots 29+ DO NOT RENDER on this port. Measured twice: build 52 used slot
    # 29 alone, build 55 used 29/30/31 across 124 fields; both gave black
    # squares with NO CRASH, so the page never becomes a texture rather than
    # failing to allocate. The archive is not at fault -- black-cell rate was
    # 4.41% on slots 26-28 and 4.85% on 29+, identical, all genuine dark art.
    #
    # A truecolor page does not have to live at 26+. From the ORIGINAL x86
    # this port recompiles (md5 ca7284c3.., byte-identical to ff7_en_switch):
    #
    #   read_field_background_data 0x62B6F1
    #     0062D13C  add  ecx, 0x1a
    #     0062D147  call 0x62b5e1     ; ->type is READ FROM THE FILE
    #     0062D162  cmp  edx, 1
    #     0062D165  jne  depth2_path  ; allocates a depth-2 buffer instead
    #
    #   field_load_textures 0x640292, type-2 path at 0x6403B8
    #     006403C0  cmp  eax, 0x21    ; 33
    #     006403C3  jl   0x64042b     ; -> blend 4, OPAQUE
    #
    # The type comes from section 9, NOT from the slot index, and any type-2
    # page below slot 33 draws opaque -- including slots 0..25. So a truecolor
    # page can sit in a free LOW slot and never touch the 29+ range hardware
    # has now rejected twice.
    #
    # MEASURED headroom over all 701 fields: the tightest has 12 free low
    # slots, the median 23.
    #
    # `_band_of` already returns 4 when `_group_of` finds no band, which is
    # exactly the engine's rule for a type-2 page below 33 -- so downstream
    # classification of these pages is already correct.
    #
    # SCOPED to Wall Market: the worst area on hardware and the heaviest user
    # of the broken slots (mrkt1/mrkt2/mrkt4 each took FIVE pages in build 55,
    # so two per field landed at 29/30 and went black). An empty set would
    # mean every field; deliberately not that until this is proven.
    if LOW_SLOT_PROBE and (not LOW_SLOT_FIELDS or field in LOW_SLOT_FIELDS):
        _low = [sl for sl in range(0, min(LOW_SLOT_TOP + 1, BANDS[4][0]))
                if sl not in pages]
        if LOW_SLOT_ORDER == 'desc':
            _low = list(reversed(_low))
        free_slots = free_slots + _low
        # AND LIFT THE CEILING FOR THESE FIELDS ONLY.
        #
        # `cap` below is min(max_tc - have_tc, len(free_slots), room), so more
        # slots alone change nothing while max_tc is 3. Raising it HERE rather
        # than globally keeps `field_bg_truecolor_pages` honest: a field that
        # is not in the probe still sees free_slots == the 26..28 range, so
        # its cap is 3 no matter what the global says. Wall Market's heaviest
        # field needs 5.
        max_tc = max(max_tc, LOW_SLOT_MAX_TC)
        dense_repack.low_slots_offered = (
            getattr(dense_repack, 'low_slots_offered', 0) + len(_low))
    # HIGH SLOTS LAST. FINDINGS-217.
    #
    # Slots 29..32 only became available at all when D2_OPAQUE_SLOTS went to
    # 7, and they are the ONLY slots on this port with no runtime evidence
    # behind them -- builds 52 and 55 drew black squares there, and the fix
    # for that (FINDINGS-168's loader bound) has never been on hardware.
    #
    # Left in their natural position they are handed out FIRST, because
    # `BANDS[4]` is the head of this list. MEASURED at 7 slots: 345 of 699
    # fields put a page in 29..32, and 339 of them gain NOTHING by it -- the
    # low-slot probe already gave them the pages they needed, in slots that
    # are known to render. That is 339 fields taking an unproven risk for no
    # benefit, which is the opposite of a probe.
    #
    # Moving them behind the low slots makes the exposure match the benefit:
    # a field only reaches 29..32 after it has exhausted 26..28 AND every
    # free slot below 15, which is exactly the condition `fship_2` is in (it
    # occupies all of 0..14) and exactly why it was stuck at 3 pages.
    _HIGH_FIRST = 0x1D            # the STOCK loader bound, from FINDINGS-168
    free_slots = ([s for s in free_slots if s < _HIGH_FIRST]
                  + [s for s in free_slots if s >= _HIGH_FIRST])
    # COUNT THE ONES ALREADY THERE. 26 vanilla pages across 400 fields are
    # already depth-2; adding `max_tc` on top of those put 4 in one field.
    have_tc = sum(1 for p in pages.values() if p.depth == 2)
    room = max_total_pages() - len(pages)    # what the field can still afford
    cap = max(0, min(max_tc - have_tc, len(free_slots), room))
    # THE PAGE-COUNT TERM DOES NOT APPLY TO THE PARALLAX HALF, AND CHARGING IT
    # THERE WAS COSTING FOUR FIFTHS OF THE POPULATION.
    #
    # `room` bounds how many pages the field may GAIN. The 32-unit half is
    # admitted one whole source page at a time and frees that page, so it does
    # not gain any -- it CONVERTS a depth-1 page into a depth-2 one. Charging
    # it against `room` anyway made a conversion look like an addition.
    #
    # MEASURED on the shipped build 92, replaying the admission over 18
    # parallax fields: 55 source slots admitted, 4 refused for having a key
    # that is not promotable, and TWELVE refused purely for want of budget --
    # and in every one of those twelve the binding term was this one. `onna_5`
    # is the clearest: 11 pages already, so `room` is 5, its 16-unit half
    # needs all 5, and its three parallax slots were left nothing at all. That
    # is why Honey Bee Inn gained zero tiles in build 92.
    #
    # `max_tc` and the byte budget DO still apply and are not relaxed: a
    # truecolor page is 1.50 MB at runtime against a paletted page's 0.31 MB,
    # so a conversion is free in pages and costs 1.19 MB in memory. That is
    # the real price and it is the one still being paid.
    cap_big = max(0, min(max_tc - have_tc, len(free_slots)))
    # THE PER-FIELD BYTE BUDGET, AND IT WAS DEAD UNTIL NOW.
    #
    # `field_bg_repack.budget_bytes()` was read only by `upgrade()`, which
    # stopped being called when this pass replaced it -- so the GUI's
    # "Field background budget (MB)" control changed nothing, exactly like
    # "Field background promotion" did. MEASURED: zero references to `budget`
    # in this module before this line.
    #
    # A page COUNT is the wrong unit once the page size moves. The same 12
    # pages cost 4.56 MB at 256px and 18.00 MB at 512px, so a ceiling
    # expressed in pages silently means something four times bigger the
    # moment you change the size above it. Bytes do not do that.
    #
    # This bounds the TRUECOLOR half, which is the half that scales: at 512px
    # a truecolor page is 1.50 MB against a paletted page's 0.31 MB, and
    # build 20 measured mean 4.72 MB and a heaviest field of 12.31 MB where
    # the last clean build was mean 1.87 MB and heaviest 4.75 MB.
    try:
        import field_bg_repack as _FR
        _bud = _FR.budget_bytes()
        if _bud < _FR.UNLIMITED:
            # THE RUNTIME COST, not the stored size. A 512px truecolor page
            # is 0.50 MB in the file and 1.50 MB once the engine builds its
            # 32bpp surface from it (6*px^2), and it is the runtime figure
            # the loader has to find. `_page_bytes` is the same function the
            # build's cost report uses, so the budget and the report cannot
            # disagree.
            _page = _FR._page_bytes(px, 2)
            cap = max(0, min(cap, int(_bud) // max(1, _page)))
            cap_big = max(0, min(cap_big, int(_bud) // max(1, _page)))
    except Exception:                                          # noqa: BLE001
        pass
    if cap == 0:
        st.refused = 'already at the truecolor ceiling'
        return sec9, st
    # PLACEMENT: A CELL KEEPS ITS COORDINATE AND ONLY CHANGES PAGE.
    # ------------------------------------------------------------------
    # This used to be `pg, idx = divmod(i, PER_PAGE)` -- cells packed into the
    # destination page in ENUMERATION ORDER, with no reference to where they
    # came from. Dense, and it destroys neighbourhood.
    #
    # MEASURED on the shipped build, asking whether a cell's neighbour ON THE
    # PAGE is also its neighbour ON SCREEN:
    #
    #     mkt_mens slot 26 (truecolor)     3 / 240    1%
    #     nivinn_1 slot 26                13 / 240    5%
    #     nivinn_1 slot 27                 0 / 102    0%
    #     mkt_mens slot  2 (paletted)     35 /  35  100%
    #
    # So on a promoted page 99% of cells sit beside a cell from an unrelated
    # part of the screen. Any filter that samples one texel past a cell edge
    # pulls that stranger's colour in, which is a one-pixel fringe whose hue is
    # whatever happens to be packed next door -- BLUE in Men's Hall, GREEN in
    # Cloud's past. Reported as "thin aliasing pixels", and the per-field
    # colour is the tell.
    #
    # Keeping the coordinate fixes it BY CONSTRUCTION: two cells that were
    # adjacent either stay adjacent, or land on different pages and never
    # share a boundary. It is the same rule `field_bg_compact` already applies
    # to fx-paired cells -- "may change PAGE but not COORDINATE".
    #
    # AND IT COSTS NOTHING. MEASURED, pages needed if every cell keeps its
    # coordinate, against pages actually used now:
    #
    #     mkt_mens  d1 needs 3, uses 4      nivinn_1  d1 needs 2, uses 3
    #     md8_1     d1 needs 3, uses 4      fship_2   d1 needs 11, uses 11
    #
    # The arbitrary packing was not even buying density.
    # ---- TWO GRIDS, TWO SETS OF PAGES. HANDOFF-192 5.1.
    #
    # `size_flag` is a property of the PAGE, not of the cell, so a 16-unit and
    # a 32-unit cell cannot share a destination however much room is left.
    # The populations are therefore seated separately, out of disjoint slices
    # of the same free-slot list, and each page is emitted with the flag its
    # own population needs.
    #
    # HOW THE BUDGET IS SPLIT, AND IT IS NOT A SHARE OF THE CAP.
    #
    # THE FIRST ATTEMPT WAS A SHARE, AND THE A/B KILLED IT. Giving the 32-unit
    # population half the cap took pages away from the 16-unit one, and
    # `_bigpages.py` reported exactly the build-72 Wall Market shape:
    #
    #     mtcrl_5   32-unit +168 tiles     16-UNIT -236 tiles     +1 page
    #     mtcrl_4   32-unit +154 tiles     16-unit    0           +2 pages
    #
    # `cand` is a QUEUE. Widening eligibility does not only add cells, it
    # PUSHES CELLS OUT, and the ones pushed out are whatever sorted last --
    # which on a widescreen field is the margin, where a cell that falls back
    # to its paletted page falls back to authored FILLER rather than to
    # softer art. That is a hole in the picture, not a loss of sharpness.
    #
    # I also claimed in the first draft of this comment that the parallax
    # pages were "close to page-neutral, because a 32-unit source page holds
    # 64 cells and its whole population moves together". MEASURED, they were
    # not: mtcrl_5 went 8 -> 9 pages and mtcrl_4 7 -> 9. A source page is
    # dropped by the dead-page sweep below only when EVERY key on it has
    # promoted, and a partly-promoted page stays -- so paying for three
    # destination pages while freeing two is a net cost, and the claim was an
    # assumption wearing a measurement's clothes.
    #
    # SO THE RULE IS TWO HARD CONSTRAINTS AND NO TUNING:
    #
    #   1. THE 16-UNIT POPULATION IS SERVED FIRST AND IN FULL. It gets the
    #      same `cap` seats it had with the veto on, so no cell that was
    #      truecolor in build 91 can stop being truecolor because of this
    #      change. That is the cell-level form of "no field's truecolor tile
    #      count goes DOWN", and `_bigpages.py` asserts it per field.
    #
    #   2. THE 32-UNIT POPULATION IS PAID FOR OUT OF THE PAGES IT FREES. A
    #      source page is counted as freeable only when every one of its keys
    #      is in this promotion, which is precisely the dead-page sweep's own
    #      condition -- so `n_big_pages <= freeable` makes the parallax half
    #      page-neutral BY CONSTRUCTION rather than by hope. Any slack left in
    #      `cap` after the 16-unit half is added on top, because that slack was
    #      already affordable.
    #
    # `field_load_textures` (x86 0x640292) ABANDONS THE WHOLE LOOP on the
    # first page it cannot allocate, so every page after it draws nothing.
    # That is why this is a constraint and not a preference.
    # OVER `keys`, NOT `cand`, AND THAT IS A BUG FIX. FINDINGS-228.
    #
    # An fx PARTNER is never a candidate -- `fx_cells` holds it whether or not
    # `PROMOTE_FX_BASE` is on -- but it IS appended to `chosen` when its base
    # is seated, and the seating loop then reads `_edge_of[fk]`. Built over
    # `cand` that is a KeyError, which `build.py` catches per field and logs
    # as "not repacked": the field loses its ENTIRE truecolor promotion, not
    # just the pair.
    #
    #     PROMOTE_FX_BASE=True, las3_2:  KeyError: (15, 0, 0, 8)
    #
    # So `PROMOTE_FX_BASE` has never seated one pair in this tree, and
    # HANDOFF-227 s3.1's "A/B measures +0 tiles, +0 pages, +0 bytes" was
    # reading a crash, not a downstream veto. `_edge_of` is a pure lookup, so
    # widening its domain cannot change a decision -- `_big_cand` and
    # `_small_cand` below still filter `cand` explicitly.
    _edge_of = {k: (BIG_TILE if keys[k].get('edge') == BIG_TILE else TILE)
                for k in keys}
    _big_cand = [k for k in cand if _edge_of[k] == BIG_TILE]
    _small_cand = [k for k in cand if _edge_of[k] == TILE]

    # THE PARALLAX POPULATION IS ADMITTED ONE WHOLE SOURCE PAGE AT A TIME.
    #
    # THE FIRST VERSION OF THIS ADMITTED CELLS AND COUNTED FREEABLE PAGES
    # SEPARATELY, AND IT DID NOT ADD UP. MEASURED on `mtcrl_4`, whose three
    # parallax slots hold 64 + 64 + 48 cells and are all fully promotable:
    #
    #     3 destination pages added, 1 source page freed, 7 -> 9 pages
    #
    # because a page is dropped by the dead-page sweep below only when EVERY
    # key on it has gone, and a handful of cells left behind on two of the
    # three slots kept both alive. Nearly-freeing a page is worth nothing;
    # the sweep does not deal in fractions.
    #
    # So the unit of admission is THE SOURCE PAGE, not the cell:
    #
    #   * a slot is a candidate only if EVERY key on it is promotable, which
    #     is the sweep's own condition rather than an approximation of it;
    #   * admitting it costs `ceil(cells / 64)` destination pages and frees
    #     exactly ONE, so its NET cost is `ceil(cells / 64) - 1` -- zero for
    #     the ordinary one-palette parallax page, which is the common case;
    #   * that net cost is paid out of `_slack`, the part of the truecolor cap
    #     the 16-unit population does not need, so the field cannot grow.
    #
    # A slot that cannot be afforded WHOLE is not promoted at all. Half a
    # backdrop at two colour depths is worse than one at eight bits, and it is
    # also the arrangement that costs pages for nothing.
    #
    # Biggest first, because a full 64-cell page frees as much as a 3-cell one
    # and covers twenty times the screen.
    _keys_on = {}
    for k in keys:
        _keys_on[k[0]] = _keys_on.get(k[0], 0) + 1
    # A PAGE IS ALSO HELD ALIVE BY BEING SOMEONE'S FX FRAME, and the dead-page
    # sweep below says so in one line: `live.add(f)` for every non-zero
    # `T_FX_PAGE`. MEASURED on `onna_5`, which is exactly this case -- its
    # layer-4 population is 80 base tiles and 16 fx ones, the fx tiles name a
    # parallax slot as their SECOND texture, and promoting every base key on
    # that slot therefore freed nothing. The field grew a page and the
    # neutrality guarantee, which is the whole basis for spending `room` on
    # this half, was quietly false.
    #
    # Counting keys was never the right test on its own; the right test is the
    # sweep's own, which is what this is.
    _fx_live = set()
    for t in tiles:
        f = sec9[t.off + T_FX_PAGE]
        if f:
            _fx_live.add(f)
    _groups = {}
    for k in _big_cand:
        _groups.setdefault(k[0], []).append(k)
    _whole = sorted((sl for sl, ks in _groups.items()
                     if sl in pages and _keys_on.get(sl) == len(ks)
                     and sl not in _fx_live),
                    key=lambda sl: -len(_groups[sl]))

    # THE 16-UNIT HALF RESERVES ONLY WHAT IT CAN FILL, NOT THE WHOLE CAP.
    #
    # `seats` used to be `free_slots[:cap]` and the flat cursor then walked
    # those pages in order and stopped when the candidates ran out -- so a
    # field with 308 promotable 16-unit cells reserved SIX pages and used TWO.
    # That is invisible while there is only one population; with two it is
    # four pages of budget held by a population that provably cannot fill
    # them. MEASURED on `hill`: cap 6, 308 small cells needing 2 pages, and
    # the parallax half was left 2 slots for a backdrop that wanted 6.
    #
    # Reserving `_small_need` instead is free by construction -- the cursor
    # could never have reached page `_small_need + 1` -- and it is what makes
    # the parallax promotion affordable on the fields that have the most of
    # it.
    _small_need = -(-len(_small_cand) // PER_PAGE) if _small_cand else 0
    _small_take = min(cap, _small_need)
    # Two independent ceilings, and they are not the same quantity:
    #   `_tc_left`   what is left of the TRUECOLOR budget -- `max_tc`, the free
    #                slots, `max_total_pages()` and the GUI's byte budget, all
    #                already folded into `cap`. A truecolor page is 1.50 MB at
    #                512px against a paletted page's 0.31 MB, so this is a
    #                memory ceiling and not a bookkeeping one.
    #   `_room_slots` how many free SLOTS are actually left to put them in.
    _tc_left = max(0, cap_big - _small_take)
    _room_slots = min(_tc_left, max(0, len(free_slots) - _small_take))

    # The admission arithmetic, kept for diagnostics. `_bigpages.py` reads it;
    # a refusal that cannot be attributed to a number is a refusal nobody can
    # argue with, and this pass has enough of those already.
    dense_repack.last_split = {
        'field': field, 'cap': cap, 'free': len(free_slots),
        'small_cells': len(_small_cand), 'small_need': _small_need,
        'small_take': _small_take, 'tc_left': _tc_left,
        'room_slots': _room_slots,
        'big_cells': len(_big_cand), 'groups': {sl: len(v) for sl, v
                                                in _groups.items()},
        'whole': list(_whole),
        'keys_on': {sl: _keys_on.get(sl) for sl in _groups},
    }

    # `_net` is the page delta this half would cost the field: `need` pages
    # added, one freed. Holding it at or below zero is the whole guarantee --
    # not "roughly neutral", not "within budget", but a field that cannot come
    # out of this function with more pages than it went in with because of the
    # parallax half.
    _admit, n_big_pages, _net = set(), 0, 0
    for sl in _whole:
        need = -(-len(_groups[sl]) // BIG_PER_PAGE)
        if _net + need - 1 > 0 or n_big_pages + need > _room_slots:
            continue
        if BIG_PAGE_SHARE < 1.0:              # A/B lever, see the constant
            if n_big_pages + need > int(round(len(_whole) * BIG_PAGE_SHARE)):
                continue
        _net += need - 1
        n_big_pages += need
        _admit.add(sl)

    # ---- CONVERT A WHOLE PARALLAX PAGE **IN PLACE**. FINDINGS-249.
    #
    # THE LOOP ABOVE NEEDS A FREE DESTINATION SLOT AND THAT IS THE ONLY THING
    # STOPPING `fship_2`.
    #
    # MEASURED at the shipping settings (768px, 20 pages), 260 fields:
    #
    #     fship_2   15 pages, slots 4..14 are ELEVEN 32-unit PALETTED pages,
    #               every one of them a `_whole` group needing exactly ONE
    #               destination page -- 672 parallax cells, the sky and the
    #               girders -- and `_room_slots` is **0**, so all eleven are
    #               refused. Its only free slots are 26/27/28 and the 16-unit
    #               half takes all three.
    #
    #     archive   65 convertible groups refused across 9 fields, 4,423
    #               parallax cells. `fship_2`/`fship_22`..`_25` are 55 of them.
    #
    # A whole group that needs exactly one page frees exactly one page -- its
    # own -- so it does not need a free slot at all. It can be written back
    # into the slot it came from. The page emission below already replaces
    # `plist[slot]` unconditionally and the dead-page sweep already spares any
    # slot in `dest`, so nothing downstream has to change.
    #
    # WHY THIS IS LEGAL, AND IT IS ALREADY ON HARDWARE. The engine reads a
    # page's TYPE from section 9 and not from its slot index (x86 0x62D147),
    # and draws any type-2 page below slot 33 opaque (x86 0x6403C0). Build
    # 119 ships 32-unit TRUECOLOR pages in LOW slots today -- `mtcrl_4` at
    # 12/13/14, `mtcrl_5` at 11/12, `wcrimb_2` at 11/12/13 -- and those are
    # the fields Patrick judges as improved. This arm changes WHICH low slot,
    # not whether a low slot can hold one.
    #
    # PAGE-NEUTRAL BY CONSTRUCTION: one freed, one added, `need == 1` enforced.
    # `_net` is unchanged, so the no-growth loop sees exactly what it saw.
    #
    # BUT NOT MEMORY-NEUTRAL, AND THAT IS THE REAL BUDGET. At 768 a paletted
    # page is 0.31 MB and a truecolor one is 3.38 MB, so each conversion costs
    # +3.07 MB of the loader's heap -- and `field_bg_budget_mb` is 0.0, i.e.
    # UNLIMITED, so nothing bounds it today. `field_load_textures` (x86
    # 0x640292) abandons the whole loop on the first texture it cannot
    # allocate and every page after it draws nothing, which is what scattered
    # black squares are. So this arm carries its own ceiling.
    #
    # THE CEILING IS THE HIGHEST FIGURE ALREADY PROVEN ON HARDWARE, not a
    # guess: `mrkt4` ships at 27.31 MB in build 119. `fship_2` is at 13.88 MB
    # and therefore affords 4 conversions, not 11. Deliberately a measured
    # bound rather than an optimistic one -- builds 52 and 55 bought black
    # squares with exactly this kind of optimism.
    #
    # IT CANNOT REGRESS ANYTHING. The cap is consulted ONLY by this arm, and
    # no field exceeds it today (27.31 is the archive maximum), so with the
    # arm off it is unreachable. `SEVENTH_NX_NO_INPLACE_BIG=1` restores 119.
    _inplace = []
    if INPLACE_BIG and _whole:
        try:
            import field_bg_repack as _FR2
            _d2 = _FR2._page_bytes(px, 2)
            _cap_b = FIELD_MB_CAP * 1048576.0
            # THE BASELINE IS THE PROJECTED FIELD, NOT THE SOURCE FIELD, AND
            # GETTING THAT WRONG IS WORTH A BLACK SQUARE.
            #
            # The first version summed the SOURCE pages only. On `fship_2`
            # that is 15 paletted pages = 4.65 MB, so the cap appeared to
            # allow SEVEN conversions -- and the field came out at 35.35 MB
            # against a 27.5 MB ceiling, because the 16-unit half's three
            # truecolor pages and the conversions themselves were never
            # counted. A budget that does not include what the rest of the
            # pass is about to allocate is not a budget.
            #
            # So: every source page, PLUS the 16-unit half's new pages, PLUS
            # the free-slot parallax groups already admitted above (each of
            # which adds `need` pages and frees its own source page).
            _proj_b = sum(_FR2._page_bytes(p.px, p.depth)
                          for p in pages.values())
            _proj_b += _small_take * _d2
            for _sl in _admit:
                _need = -(-len(_groups[_sl]) // BIG_PER_PAGE)
                _proj_b += _need * _d2
                if _sl in pages:
                    _proj_b -= _FR2._page_bytes(pages[_sl].px,
                                                pages[_sl].depth)
            _cur_b = _proj_b
            for sl in _whole:
                if sl in _admit or sl not in pages:
                    continue
                if len(_groups[sl]) > BIG_PER_PAGE:     # need == 1, exactly
                    continue
                _p = pages[sl]
                if _p.depth != 2 and not _p.size_flag:
                    # A 32-unit population must land on a size_flag page, and
                    # reusing a slot whose source is NOT 32-unit would change
                    # the grid the engine reads it on. Refuse rather than
                    # reinterpret -- that mistake is the build-84 checkerboard.
                    continue
                _delta = _d2 - _FR2._page_bytes(_p.px, _p.depth)
                if _cur_b + _delta > _cap_b:
                    continue
                _cur_b += _delta
                _admit.add(sl)
                _inplace.append(sl)
            if _inplace:
                dense_repack.inplace_big = (
                    getattr(dense_repack, 'inplace_big', 0) + len(_inplace))
                dense_repack.inplace_fields = (
                    getattr(dense_repack, 'inplace_fields', 0) + 1)
                dense_repack.inplace_cells = (
                    getattr(dense_repack, 'inplace_cells', 0)
                    + sum(len(_groups[s]) for s in _inplace))
        except Exception:                                      # noqa: BLE001
            _inplace = []
    freeable = len(_admit)

    if len(_admit) < len(_groups):
        _big_cand = [k for k in _big_cand if k[0] in _admit]
        _drop = {k for k in cand
                 if _edge_of[k] == BIG_TILE and k[0] not in _admit}
        if _drop:
            cand = [k for k in cand if k not in _drop]
            dense_repack.big_slots_refused = (
                getattr(dense_repack, 'big_slots_refused', 0)
                + len(_groups) - len(_admit))

    # The 16-unit half keeps the LOW slots it has always had -- `LOW_SLOT_ORDER`
    # and `LOW_SLOT_TOP` are a measured placement probe (FINDINGS-156) and
    # reordering that population would confound this change with that one. The
    # parallax pages are taken from the TAIL instead.
    # The in-place seats are the groups' OWN slots, appended AFTER the free
    # ones so a field that has free slots fills them exactly as build 119 did
    # and the flat cursor's order is unchanged for everything else.
    seats_big = ((free_slots[_small_take:_small_take + n_big_pages]
                  if n_big_pages else []) + _inplace)
    n_big_pages += len(_inplace)
    seats = free_slots[:_small_take]
    _seats_of = {BIG_TILE: seats_big, TILE: seats}
    _big_slots = set(seats_big)
    if n_big_pages:
        dense_repack.big_pages = getattr(dense_repack, 'big_pages', 0) + n_big_pages
        dense_repack.big_fields = getattr(dense_repack, 'big_fields', 0) + 1

    chosen = []
    occupancy = {}
    fx_slot_of = {}
    _placed_at = {}
    _grid_order = [(i % GRID, i // GRID) for i in range(PER_PAGE)]
    _grid_order_big = [(i % BIG_GRID, i // BIG_GRID)
                       for i in range(BIG_PER_PAGE)]

    # ---- FX PAIRS SHARE ONE u,v AND TWO PAGES. FINDINGS-164.
    #
    # SETTLED, and it was already settled inside this project -- I just had
    # not read it. `field_bg_compact` builds the fx reference as
    # `fxr = (fx_slot, cx, cy)` from the BASE's cx,cy, validates the fx page
    # with `u,v` out of the BASE's T_SRC_X_BIG, and REFUSES to compact a pair
    # whose two cells would not land on the same grid index:
    #
    #     if (fcx, fcy) != (ncx, ncy):
    #         return sec9, CompactStats()
    #     # "The pin guarantees this; assert it rather than trust it,
    #     #  because a violation is invisible until it is on screen."
    #
    # So the engine samples the fx page with the BASE's uv. `src_x2/src_y2`
    # exist in the record but are NOT the runtime sampling coordinate, and the
    # "99.34% have src2 != src1" argument in FINDINGS-161 s2 proved nothing
    # about the runtime. FINDINGS-157 s5 was right the whole time:
    #
    #     a pair must move together AND land on the SAME GRID INDEX of two
    #     different pages.
    #
    # Build 69 dense-packed the base to a new index and left the fx page
    # alone, so the animated frame sampled an arbitrary wrong cell. That is
    # the blocky smoke, and it is not a size problem at all.
    #
    # So: allocate a COLUMN. A group of width w takes one grid index (cx,cy)
    # on w different pages. If no index has w seats free, the base is not
    # promoted at all -- a half-placed group is the defect itself.
    def _col_free(cx, cy, need, avoid=(), edge=TILE):
        got = []
        for sl in _seats_of[edge]:
            if sl in avoid:
                continue
            if (cx, cy) not in occupancy.setdefault(sl, set()):
                got.append(sl)
                if len(got) == need:
                    return got
        return None

    # SINGLES KEEP THE ORIGINAL PACKING. With the flag OFF there are no
    # groups, nothing is pre-occupied, and this cursor reproduces the old
    # `divmod` fill exactly -- verified by the flag-off column of the A/B
    # being identical to build 68. Only a seated GROUP perturbs it.
    _cursor = [0]
    _cursor_big = [0]

    def _next_flat(edge=TILE):
        # ONE CURSOR PER POPULATION, over that population's OWN seats and its
        # OWN grid. The 16-unit arm is unchanged apart from reading `seats`
        # instead of `free_slots`, and those are the same list when there is
        # no parallax population -- which is what keeps a field with no
        # 32-unit cells byte-identical.
        if edge == BIG_TILE:
            cur, per, grid, pool = _cursor_big, BIG_PER_PAGE, BIG_GRID, seats_big
        else:
            cur, per, grid, pool = _cursor, PER_PAGE, GRID, seats
        while cur[0] < len(pool) * per:
            pg, idx = divmod(cur[0], per)
            cur[0] += 1
            cy, cx = divmod(idx, grid)
            sl = pool[pg]
            if (cx, cy) not in occupancy.setdefault(sl, set()):
                return sl, cx, cy
        return None

    # DO NOT TRUNCATE THE CANDIDATE LIST. The old `cand[:cap * PER_PAGE]`
    # window assumed one seat per candidate; an fx group takes several and a
    # FAILED group takes none, so truncating pushed ordinary cells out of the
    # window for no gain -- las0_2 lost 8 tiles and gained a page while seating
    # zero pairs. `_next_flat` already stops at the real capacity.
    order = cand
    for k in order:
        _edge = _edge_of[k]
        partners = [fk for fk in (fx_of.get(k) or ())
                    if fk in keys and fk[0] in pages]
        if partners and not _fxbase:
            continue
        done = [fk for fk in partners if fk in fx_slot_of]
        todo = [fk for fk in partners if fk not in fx_slot_of]
        if not partners and not PRESERVE_CELL_COORDS:
            spot = _next_flat(_edge)
            if spot is None:
                continue
            sl, cx, cy = spot
            occupancy[sl].add((cx, cy))
            chosen.append((k, sl, cx, cy))
            continue
        if PRESERVE_CELL_COORDS:
            _g = BIG_GRID if _edge == BIG_TILE else GRID
            spots = [((k[1] // _edge) % _g, (k[2] // _edge) % _g)]
        elif done:
            # A partner already seated fixes the column for the whole group.
            spots = [_placed_at[done[0]]]
        elif _edge == BIG_TILE:
            spots = _grid_order_big
        else:
            spots = _grid_order
        for cx, cy in spots:
            if done and _placed_at[done[0]] != (cx, cy):
                continue
            avoid = {fx_slot_of[fk] for fk in done}
            got = _col_free(cx, cy, 1 + len(todo), avoid, _edge)
            if got is None:
                continue
            occupancy[got[0]].add((cx, cy))
            chosen.append((k, got[0], cx, cy))
            for fk, sl in zip(todo, got[1:]):
                occupancy[sl].add((cx, cy))
                chosen.append((fk, sl, cx, cy))
                fx_slot_of[fk] = sl
                _placed_at[fk] = (cx, cy)
            break
    if fx_slot_of:
        dense_repack.fx_pairs = getattr(dense_repack, 'fx_pairs', 0) + len(fx_slot_of)
        dense_repack.fx_pair_fields = getattr(dense_repack, 'fx_pair_fields', 0) + 1

    if not chosen:
        st.refused = 'nothing to promote'
        return sec9, st

    dest = {}
    out = bytearray(sec9)
    # THE DESTINATION PAGE IS `px` WIDE, NOT 256.
    #
    # This was hardcoded to 256 in three places -- the buffer, the write, and
    # the Page it built -- so a 512px build produced 256px pages and every
    # later pass, which is told `px`, then failed to parse the section.
    # MEASURED: at 512px `field_bg_compact` raised "truncated pixels at slot
    # 26" for essentially every field, i.e. COMPACTION WAS OFF for the whole
    # archive, page counts grew ~2 per field, and that is what the black
    # squares were.
    #
    # The tile COORDINATES stay in 256-space: a page holding the same layout
    # at 2x carries identical u, v and extent (README-field-bg-512-MEASURED),
    # so only the pixel buffer scales.
    scale = max(1, px // 256)
    side = GRID * TILE * scale
    for k, slot, cx, cy in chosen:
        # THE THREE THINGS BUILD 84 GOT WRONG, ALL DERIVED FROM ONE NUMBER.
        # `_edge` is 32 on a size_flag cell and 16 otherwise, and it drives
        # the copy size, the destination coordinate and the uv step together.
        # Deriving all three from one place is the point: build 84 fixed the
        # copy at 16, the coordinate at a 16 grid and the page flag at a
        # literal 0, and any one of those alone reproduces the checkerboard.
        _edge = _edge_of[k]
        _step = BIG_STEP if _edge == BIG_TILE else STEP
        buf = dest.get(slot)
        if buf is None:
            buf = dest[slot] = np.full((side, side), FN.NEAR_BLACK, np.uint16)
        try:
            cell = source_cell(k, keys[k], pages, arrays, pal565,
                               art_for, pals_for, st, scale, _org,
                               _hb.get(k, 0.0) > HUE_BROKEN_DIST, _edge,
                               force_transfer=(k in _mp_force))
        except Exception:                                      # noqa: BLE001
            continue
        t = _edge * scale
        if cell.shape != (t, t):
            # A cell that did not come back at the size this seat holds would
            # otherwise raise inside the assignment and lose the whole field
            # through build.py's "not repacked" path. Refusing the one cell is
            # strictly better, and it can only happen if a source page is
            # short -- which `test_bigtile.py` invariant D also checks for.
            continue
        buf[cy * t:(cy + 1) * t, cx * t:(cx + 1) * t] = cell
        dx, dy = cx * _edge, cy * _edge
        # See ORIGIN. `_org` is marginpage's map, so chaining through it here
        # means what we store is Cosmos's own page whether the cell moved once
        # or twice, and no consumer has to walk the chain itself.
        _src = (k[0], k[1], k[2])
        if _org:
            _src = _org.get(_src, _src)
        _src = (_src[0], _src[1], _src[2], k[3])
        for off in keys[k]['tiles']:
            st.origin[off] = _src
        for off in keys[k]['tiles']:
            out[off + T_TEXID] = slot
            out[off + T_SRC_X] = dx & 0xFF
            out[off + T_SRC_Y] = dy & 0xFF
            struct.pack_into('<II', out, off + T_SRC_X_BIG,
                             cx * _step, cy * _step)
            # Repoint this tile's fx frame at the partner's new page. The
            # coordinate is preserved, so src_x2/src_y2 stay correct as
            # written and only the page byte moves. See FINDINGS-163.
            if fx_slot_of:
                # The pair shares this tile's u,v -- only the PAGE moves. The
                # partner key is collect()'s: (fx page, BASE sx, BASE sy, pal).
                f = out[off + T_FX_PAGE]
                if f and f in pages:
                    ns = fx_slot_of.get(
                        (f, k[1], k[2], k[3] if pages[f].depth == 1 else -1))
                    if ns is not None:
                        out[off + T_FX_PAGE] = ns
            st.tiles += 1
        st.cells += 1

    # Original pages nothing points at any more cost a texture for nothing.
    live = set()
    for t in MB.read_tiles(bytes(out), surv, pages):
        live.add(t.slot)
        f = out[t.off + T_FX_PAGE]
        if f:
            live.add(f)
    plist, tex_start, tex_end = FN.parse_texture_block(bytes(out), px)
    for sl in list(range(len(plist))):
        if plist[sl] is not None and sl not in live and sl not in dest:
            plist[sl] = None
    for slot, buf in dest.items():
        # THE size_flag WAS A LITERAL ZERO HERE, AND THAT IS HALF OF WHY THE
        # BUILD-84 CHECKERBOARD EXISTED EVEN WHERE THE PLACEMENT WAS RIGHT.
        # A page that holds 32-unit cells must SAY SO, or the engine reads it
        # on a 16 grid and every tile samples a quarter of its own cell.
        # `field_bg_compact` has derived this from the grid since it was
        # written (`size_flag = 1 if grid == 8 else 0`); this pass now does
        # the same.
        plist[slot] = FN.Page(slot, 1 if slot in _big_slots else 0, 2,
                              buf.tobytes(), side)
    st.pages = len(dest)
    # PUBLISH AFTER THE PAGES ARE FINAL, and only for cells that survived the
    # loop -- a `source_cell` exception `continue`s above without writing the
    # cell, and an entry for a cell that was never promoted would point a
    # consumer at art for a page the tile no longer names.
    if field and st.origin:
        ORIGIN[field] = dict(st.origin)
    return FN.replace_texture_block(bytes(out), plist, tex_start, tex_end), st


def summarise(t):
    if not t or not t.get('fields'):
        return ''
    return ('field background DENSE REPACK: %d field(s), %s cell(s) packed onto '
            '%d truecolor page(s) -- %.1f per field against %.1f paletted '
            'before. %s from the mod exactly, %s borrowed a neighbouring '
            'palette, %s baked from vanilla. Every cell carries the palette it '
            'names, so no page is drawn through a foreign colour table.%s'
            % (t['fields'], f"{t['cells']:,}", t['pages'],
               t['pages'] / max(t['fields'], 1),
               t['pages_before'] / max(t['fields'], 1),
               f"{t['from_art']:,}", f"{t['from_art_borrow']:,}",
               f"{t['from_vanilla']:,}",
               '  %d field(s) refused: %s' % (len(t['refused']),
                                              ', '.join(t['refused'][:3]))
               if t.get('refused') else ''))
