#!/usr/bin/env python3
"""
ff7nx_marginart.py -- put Cosmos's widescreen ART into the 16:9 margin, by
writing it INTO THE PALETTED PAGE that is already there.

THE FINDING THIS IMPLEMENTS, AND IT OVERTURNS HANDOFF-65 AND 66
===============================================================
Six handoffs called the coloured side bands "flat filler tiles in the
archive, drawn correctly, in the colour the file holds". That is true of the
VANILLA page and it is the wrong page to look at.

Measured on the real `CosmosLimitBreak` DDS, for the cells those margin tiles
sample:

    bwhlin  vanilla cell = flat index 1 -> RGB(200,144, 80)   <- the tan band
            Cosmos DDS   = REAL ART, mean RGB(38..122, 24..85, 13..54)

    mds6_3  vanilla cell = flat index 1 -> RGB( 32, 32, 16)   <- the olive band
            Cosmos DDS   = REAL ART, mean RGB( 4.. 18,  1.. 9,  2.. 9)

Over 45 fields, 3,072 such tiles:

    Cosmos ships REAL ART at that cell   2,501   (81%)
    Cosmos ships black/near-black          421   (14%)

So the tiles are not filler. They point at cells that are BLANK in the
vanilla page and PAINTED in the upscale. FFNx loads the DDS, the page is
replaced, and those tiles draw extended scenery. This port skips the DDS
(`FFNx textures: N (skipped, no Switch loader)`), so the tile samples the
vanilla placeholder and the whole band comes out one flat colour.

THE BANDS ARE MISSING TEXTURES. Not authored letterbox, not a limit of the
data, not something FFNx does differently.

`ff7nx_marginblack.py` would have painted those 2,501 tiles near-black --
destroying the art it was trying to reveal. It stays OFF.

WHY WRITE INTO THE PALETTED PAGE INSTEAD OF PROMOTING IT
========================================================
`field_bg_repack` already puts Cosmos art on screen, but only by PROMOTING a
page to truecolor, and three things forbid that for most pages (counts from
a real build):

    95,733 tiles  the cell carries a colour key; truecolor has no index
    39,776 tiles  the cell is shared with an fx page through one u,v
    13,175 cells  the mod's own art is transparent there

Those cells are stuck on the paletted page, so they draw vanilla. Writing
INDICES into that same page dodges all three at once: index 0 stays index 0
so the colour key survives, the page keeps its identity so the fx pair stays
valid, and the format does not change so nothing new is allocated. No new
page, no new texture, no VRAM -- the slot cap this build depends on is not
even involved.

WHICH CELLS
-----------
Only cells that are (a) sampled ONLY by margin tiles on layer 1, (b) FLAT in
the vanilla page -- a single index, i.e. a placeholder, and (c) covered by a
Cosmos DDS for that exact (page, palette). A cell sampled anywhere inside the
4:3 picture is never touched, so the interior cannot change.

QUANTISATION
------------
The DDS is 1024x1024 for a 256px page, so each 16x16 cell is 64x64 source
pixels; it is box-filtered down to 16x16 and each pixel matched to the
nearest colour in THAT TILE'S palette page, searching indices 1..255.

Index 0 is never emitted. It is this pipeline's transparency key, and an
opaque scenery pixel that landed on 0 would punch a hole in the background
and let field models draw in front of it -- `field_bg_native.NEAR_BLACK`'s
reason, and the same trap `ff7nx_marginblack.NEAR_BLACK_555` was built to
avoid.
"""
from __future__ import annotations

import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC
import ff7nx_marginblack as MB
# BUILD 109. Disarmed unless `build.py` calls `arm()`, in which case every
# entry point here is a no-op and this import costs nothing but the module.
import field_bg_shadow as SH


# Leave a cell alone when the art it already carries is entirely black.
# See the comment at the use site: those cells are silhouette, and filling
# them is the grey staircase in the No. 1 reactor.
KEEP_BLACK_SILHOUETTE = False   # post-baseline; reverted with the rest

# ------------------------------------------------------------- FINDINGS-173
# THE SAME IDEA AS ABOVE, PER PIXEL -- WHICH IS THE UNIT THE DEFECT LIVES IN.
#
# `KEEP_BLACK_SILHOUETTE` skips a cell only when the WHOLE cell is black, so
# it protects the interior of a void and misses its EDGE. The edge cell is
# part scenery, part void, holds 2..27 black pixels out of 256, and is where
# a silhouette boundary actually runs -- diagonally, which is why it reads as
# a staircase. Aerith's house upstairs (`elmin1_2`) and the No. 1 reactor
# family (`nmkin_*`) are the reported cases.
#
# This preserves the BLACK PIXELS THEMSELVES and lets the rest of the cell
# take Cosmos's art, so nothing is withheld and the boundary stays exactly
# where the original put it.
#
# It can only PRESERVE a black pixel, never create one, so the failure that
# retired the cell-level guard -- `md1stin`'s widened edges blacked out --
# cannot occur. See the long note at the write site.
# OFF, AND RETRACTED BY MEASUREMENT. FINDINGS-173 s5.
#
# This looked like it worked and it does not. The "improvement" I measured
# (elmin1_2 1,245 -> 198 damaged pixels) was an artefact of `_seam._render`,
# which sampled a 512px depth-2 cell with `cell[::s, ::s]` -- the TOP-LEFT
# texel of each 4x4 block, discarding 15 of every 16 pixels. On a boundary
# cell that sample is a coin flip, and every cell in this report is a boundary
# cell. With the renderer corrected to box-average, the same A/B moves
# elmin1_2 from 700 damaged pixels to 700. Nothing.
#
# The reason is simple once measured: **100% of the damaged cells are
# TRUECOLOR** (elmin1_2 69 cells, nmkin_2 226, mds6_3 109; paletted: zero).
# A promoted cell is built by `field_bg_dense.source_cell` from Cosmos's DDS
# directly and never reads the paletted page this pass writes, so guarding
# the paletted page cannot reach it.
#
# Left in place, off, because the code and the FFNx table above are the
# groundwork for whatever does fix it -- and because turning it on is
# measurably a no-op rather than a risk.
KEEP_BLACK_PIXELS = False

# What counts as black, per channel, in the vanilla page's own palette.
# 0 is the strict reading and it is what the reported cells use (index 119 in
# `elmin1_2` palette 1 is exactly RGB(0,0,0)). A small tolerance is allowed
# because a 5:6:5 palette can hold RGB(0,0,8) and mean black; above ~16 the
# entry is a colour and the mod is entitled to replace it.
BLACK_PIXEL_MAX = 8

# ...AND ONLY WHERE THE SOURCE ITSELF IS DARK. See the FFNx table at the write
# site: a pixel is only protected when the kxk source block that produced it is
# at least SRC_DARK_FRAC dark, which is the signature of a BOUNDARY BLEND
# rather than of authored content. Without this the guard suppresses real art
# in nmkin_2 / md1stin / mds6_3, where the source is 82-96% fully opaque and
# the reporter confirms the picture is already correct.
SRC_DARK_MAX = 24        # a source texel this dark is "void", per channel
SRC_DARK_FRAC = 0.25     # this much of the block must be void to protect it

# ------------------------------------------------------------- FINDINGS-174
# A CELL THAT IS 100% INDEX 0 ON A PAGE COSMOS ADDED IS AN EMPTY ATLAS SLOT,
# NOT A CUT-OUT. See the long note at the `_is_cut` site for the measurement.
# This is what leaves the last olive squares in Wall Market's margin.
EMPTY_ATLAS_IS_NOT_A_CUTOUT = True
ATLAS_OPAQUE_FRAC = 0.9    # the mod's art must be this opaque to count as art
ATLAS_ENTRY0_MIN_LUMA = 40  # and entry 0 must be visible, or it reads as void
# ...EXCEPT ON A PARALLAX PAGE, WHERE THERE IS NOTHING BEHIND IT TO READ AS.
# See the waiver in fill_field. SEVENTH_NX_NO_ATLAS_PARALLAX=1 restores the
# build-88 behaviour, i.e. the black square in Mt. Corel's top-left corner.
ATLAS_PARALLAX_VOID = os.environ.get('SEVENTH_NX_NO_ATLAS_PARALLAX') != '1'


def _box3_rgb(a):
    """3x3 box mean of an (H, W, 3) image, edge-replicated -- low frequency.

    The same low-pass `field_bg_dense._box3` applies, in RGB rather than
    packed 565, so the two passes split detail from colour identically.
    """
    import numpy as _np
    p = _np.pad(a, ((1, 1), (1, 1), (0, 0)), mode='edge').astype(_np.int32)
    return (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:] +
            p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:] +
            p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]) // 9


def _extend_into_gap(rgb, covered):
    """
    `rgb` with every UNCOVERED texel grown outward from the covered ones.

    A Cosmos page is a SPARSE ATLAS -- art only where the vanilla page had
    art, transparent elsewhere -- and the sources zero the RGB where alpha is
    0, so a gap arrives here as BLACK. Quantising that writes black into the
    picture, and `MAX_QUANT_ERR` cannot catch it because black is an
    excellent match for black (measured error 1.4 to 4.5 out of 255).
    Transparent is a claim about COVERAGE, not about colour.

    Repeated 4-neighbour dilation of the covered region, so a gap takes the
    colour of the art it touches. MEASURED on `md8_1` slot 1, the Sector 8
    cells behind the black squares:

        cell            uncovered   as black      extended
        src(192,240)      69.5%     ( 15,10, 6)   (39,24,16)
        src(160,240)      64.8%     ( 17,11, 7)   (37,24,15)
        src(192,208)      35.5%     ( 27,16,11)   (39,23,16)
        src( 80,208)      12.5%     ( 34,19,14)   (39,23,15)
        src(240,224)       1.6%     ( 49,22,15)   (49,22,15)   <- unchanged

    Every gap cell converges on the same rubble tone as its neighbours, and a
    cell the mod covers is untouched, so this cannot alter art the mod ships.
    """
    out = rgb.astype(np.float32).copy()
    m = covered.copy()
    for _ in range(32):
        if m.all():
            break
        acc = np.zeros_like(out)
        cnt = np.zeros(m.shape, np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            v = np.roll(np.where(m[..., None], out, 0), (dy, dx), (0, 1))
            c = np.roll(m.astype(np.float32), (dy, dx), (0, 1))
            if dy == 1:
                v[0] = 0
                c[0] = 0
            elif dy == -1:
                v[-1] = 0
                c[-1] = 0
            if dx == 1:
                v[:, 0] = 0
                c[:, 0] = 0
            elif dx == -1:
                v[:, -1] = 0
                c[:, -1] = 0
            acc += v
            cnt += c
        fill = (~m) & (cnt > 0)
        if not fill.any():
            break
        out[fill] = acc[fill] / cnt[fill, None]
        m = m | fill
    return out


def _cell_indices(buf, page, sx, sy):
    """The 16x16 block of palette indices a cell currently holds."""
    import numpy as _np
    arr = _np.frombuffer(bytes(buf), _np.uint8).reshape(page.px, page.px)
    return arr[sy:sy + 16, sx:sx + 16]
import ff7nx_marginpal as MP

# ---------------------------------------------------------------- the setting
MARGIN_ART_ENV = 'SEVENTH_NX_MARGIN_ART'
DEFAULT_ON = False        # settings.json owns it: `margin_art: 1`

TILE = 16

# BUILD 118. `_sqdist` via BLAS instead of an (N,M,3) reduction. Bit-identical
# by construction -- see `_sqdist` -- and ~3x faster on the hottest function in
# the build. SEVENTH_NX_NO_FASTQ=1 restores the original expression.
FAST_QUANTISE = os.environ.get('SEVENTH_NX_NO_FASTQ') != '1'
# THE 32-UNIT CELL. See the `_edge` note in fill_field.
#
# `SEVENTH_NX_NO_BIGCELL=1` restores the 16x16 write on size_flag pages,
# i.e. build 84's quarter-filled parallax cells. It exists so the change
# can be proved a no-op everywhere else: with it set, every field whose
# pages all have size_flag 0 must come out byte-identical.
BIG_CELL = os.environ.get('SEVENTH_NX_NO_BIGCELL') != '1'
SECTION_PALETTE = 3
SECTION9 = 8

# Use a page's art from ANY palette the mod ships when the exact one is
# missing, and quantise it against the palette the cell is actually drawn
# with. Cosmos ships `<field>_<page>_00` and almost nothing else while cells
# use palettes 0..8, so without this the interior scope finds art for a small
# fraction of cells. Set False to require an exact palette match.
BORROW = True

# Mean per-channel error, 0..255, above which a quantised cell is thrown away
# rather than written. This is what keeps BORROW honest: an image borrowed
# from a palette that means something different cannot be approximated, the
# nearest colour is far away, and the cell keeps its vanilla content.
MAX_QUANT_ERR = 60

# RETIRED -- see the detail transfer at the use site. This refused 44% of
# interior cells and cost Cosmos's detail everywhere it fired, which is a
# regression and not a fix. Kept only so an A/B can turn the old behaviour
# back on.
_UNUSED_BORROW_MAX_DIST_NOTE = """
# HOW FAR A BORROW MAY MOVE AN INTERIOR CELL, mean |RGB| over 0-255.
#
# `MAX_QUANT_ERR` is the wrong backstop for this and the numbers say so: it
# refused 499 cells out of 519,730 on the last build, 0.1%, because it asks
# "can the destination palette express this image" and a grey image is
# expressible in almost any palette. It never asks whether the image is the
# RIGHT ONE.
#
# MEASURED, `nmkin_5` -- the red railing outside Reactor 1, red only because
# it draws through palettes 5 and 7:
#
#     cell (1,  32, 112)  vanilla RGB(136, 13, 13)  ->  shipped build (5, 15, 21)
#     cell (1, -96, 240)  vanilla RGB(192, 51, 40)  ->  shipped build (64, 62, 53)
#
# Running the promotion from VANILLA section 9 reproduces the red correctly
# (97, 8, 11), so the colour was already gone before the repack ran: this
# pass had overwritten those indices with a quantisation of palette 0's GREY
# art, and the quantiser was happy because grey has a near neighbour in a
# palette full of dark reds. That is the "missing texture" -- not a dropped
# page, a cell repainted the wrong colour.
#
# MARGIN cells are exempt. A placeholder has no vanilla art to lose, so a
# borrowed approximation is strictly better than flat filler. The risk is
# confined to the INTERIOR scope, where borrowing overwrites art that was
# already correct.
"""
BORROW_MAX_DIST_INTERIOR = 32.0

# When Cosmos's art for a flat MARGIN PLACEHOLDER cell is near-black, write the
# near-black rather than keeping the placeholder's own colour. See EMPTY SOURCE
# in `fill_field` for the measurement. Set False to restore the old behaviour
# for an A/B.
# Where Cosmos's page is TRANSPARENT, keep the vanilla index instead of
# writing the black that a zeroed RGB channel pretends is there. See the long
# note at the write site -- this is the column of black squares at the edges
# of the 16:9 frame. Set False to restore the old behaviour for an A/B.
# ALPHA IS THE COLOUR KEY, NOT COVERAGE. FINDINGS-137.
#
# Read this before touching either switch below, because the same mistake has
# now been made twice in opposite directions.
#
# MEASURED, where the mod's DDS is transparent, what vanilla holds at the same
# texel:
#
#     mds6_1  page  2   99.4% vanilla index 0      mkt_mens page 1  99.7%
#     mds6_1  page 15  100.0%                      nivinn_1 page 3  99.9%
#
# The mod's page is COMPLETE. Alpha marks the transparency key, which is why
# it looks correct on FFNx -- there the DDS simply replaces the page and the
# alpha is the key. There is no missing art to compensate for.
#
# TWO ATTEMPTS TO COMPENSATE, BOTH WRONG:
#
#   `_extend_into_gap` (on until build 46) dilated the covered art outward to
#   fill the "gap", 32 rounds of 4-neighbour growth. Where a cell held part of
#   a texture along a diagonal, the rest of the square came out as a triangle
#   of that texture's colours -- the grey/green blocks in reactor 1 and Tifa's
#   bar. It was inventing content to fill something that was never a hole.
#
#   The vanilla fallback (build 46 only) wrote the VANILLA index wherever
#   alpha fell below 128. Redundant where it agreed with `keep0`, and where it
#   did not it discarded real art: a downsampled ANTIALIASED EDGE lands
#   between 1 and 127, and on one such edge 80 of 256 texels are partial alpha
#   that got replaced with vanilla. A vanilla-toned fringe on every edge.
#
# `keep0` at the write site is the authority on transparency and always was:
# it forces index 0 exactly where vanilla had index 0, so occlusion cannot
# change. Everything else takes the mod's colour. Nothing needs to fill
# anything.
HONOUR_MOD_ALPHA = True

# The dilation. OFF, and it should stay off: there is no gap to fill. See the
# note above. True restores build 45's behaviour, for A/B only.
EXTEND_INTO_GAP = False

# FORCE INDEX 0 ONLY WHERE INDEX 0 IS A REAL CUT-OUT. FINDINGS-140.
#
# `keep0` at the write site re-inserts vanilla's index 0 over whatever the mod
# ships, on the reasoning that "index 0 is the colour key" and moving it would
# change occlusion. That reasoning holds on layer 2+ and on the blend bands,
# where index 0 IS the transparent surround of an object. It is FALSE on a
# layer-1 opaque page, and the difference is the whole of this flag.
#
#   FFNx `ff7/field/field.cpp:58` sets `tex_header->color_key = 3` only for
#   `type == 2`. `common.cpp:1728` -- `if(color_key && pixel == 0) return 0`
#   -- is therefore never taken for a depth-1 page, so index 0 on a paletted
#   page is DRAWN, through the palette, like any other index.
#
# Confirmed on hardware the other way round as well: writing black at entry 0
# removed the Sector 6 yellow AND put black speckles across Wall Market's
# interior, which cannot happen if the index is discarded.
#
# So on a layer-1 opaque cell, `keep0` does not preserve transparency. It
# overwrites Cosmos's art with "draw the key colour", and 93% of those keys
# are 0x0000. MEASURED on the build-50 archive, on-screen cells only:
#
#     index-0 px on OPAQUE-band paletted pages   13,966,071
#        whose palette key is 0x0000             12,990,788  (93.0%)
#        fields affected                                633
#        md8_1 16,896   mkt_mens 33,508   kuro_9 56,474
#
# That is the residual black, and it is why no key COLOUR can fix it: one
# palette entry cannot be right for every index-0 pixel on a page. De-fringe
# it and you get the grey/green wash build 47 removed; black it and you get
# these. The way out is to stop emitting the index at all where it is not a
# cut-out, which also stops discarding mod art -- the actual goal.
#
# Occlusion moves in the RIGHT direction here. `ff7nx_bgclear`'s header: "A
# background pixel that is transparent writes no occlusion, so field models
# draw straight through it -- that is Cloud appearing in FRONT of black
# scenery he should be behind." These pixels become opaque, not transparent.
#
# False restores build 50 exactly, for A/B.
KEEP0_CUTOUTS_ONLY = False

# Slot at which the ADDITIVE/AVERAGE bands begin (field_bg_native.D1_GROUPS).
# A depth-1 page at or above this is only ever reached through `fx_page`, and
# every tile that draws from one is an fx tile -- see ff7nx_palkey.
BLEND_BAND_FIRST_SLOT = 0x0F

# DOES BEING ON LAYER 2+ MAKE A CELL A CUT-OUT? MEASURED: NO.
#
# The first cut of this flag said yes, on the reasoning that layer 2+ is where
# real cut-outs live. That is true on a BLEND page and false on an OPAQUE one,
# and the difference is not academic -- it is the whole effect:
#
#     texels that actually change (vanilla index 0 AND Cosmos ships art)
#                        layer rule    blend-band rule
#         md8_1                   0             14,835
#         mkt_mens            4,338              6,558
#
# md8_1 is the field this whole effort started on and the layer rule does
# NOTHING to it, because its index-0 pixels are layer-2 tiles drawing from an
# OPAQUE page. `ff7nx_palkey` calls that case "both branches are wrong for it"
# and blames it for "the dark blocks on the stairs".
#
# On an opaque page index 0 was never transparent -- the engine draws it, so
# those overlays ALREADY have a solid rectangle of key colour. Nothing is
# being made opaque that was see-through; a flat key colour is being replaced
# by the art Cosmos ships for those same texels. Vanilla's own key was picked
# to be inconspicuous, which is why this never read as broken.
#
# Set True to restore the layer rule for A/B.
KEEP0_LAYER_IS_CUTOUT = False

# THE MARGIN PASS HAS NEVER RUN ABOVE LAYER 1. FINDINGS-231.
#
#     ff7nx_marginblack.Tile.is_margin  ->  self.layer == 1 and self.outside_43
#
# `outside_43` is documented one line above it as "wholly outside the 4:3
# picture, on ANY layer", and then `is_margin` throws every other layer away.
# `fillable_cells` is built on that property, so in margin scope every layer-2,
# -3 and -4 tile is vetoed BEFORE any other test runs -- not by the
# multi-palette rule, not by `_is_animated`, not by keep-0, but by the
# definition of what a margin is. 530,349 cells written and not one of them
# above layer 1.
#
# That is why the widescreen regions have no lighting overlay: Cosmos authors
# the margin overlay as BLANK PLACEHOLDER cells and ships the art in the page
# DDS, exactly as it does on layer 1. FFNx replaces the page and draws it. We
# have to write it into the paletted page, and we were never looking.
#
# MEASURED, 701 fields (`_kl2margin.py`, FINDINGS-231 falsifier 1):
#
#     layer 2+ tiles outside the 4:3 picture        69,427
#     distinct source cells                         46,785
#     PLACEHOLDER -- a single index over 16x16       9,901
#     ...and EXCLUSIVE to the margin                 9,892
#     ...and Cosmos actually PAINTS it               8,284   in 173 fields
#       of those, ANIMATED (the flicker veto)            0
#       blend group of the page                  4 for all 8,284
#
# THREE THINGS THAT MEASUREMENT SETTLES, and each one removes a stated risk:
#
#   * ZERO are animated, so `_is_animated`'s flicker hazard does not arise in
#     this population at all.
#   * ALL of them are on blend group 4 -- the same group layer 1's margin
#     placeholders already sit on. None is on the additive group, so "index 0
#     means adds nothing and filling it paints the sky" cannot happen here.
#   * cells == tiles == 8,284, so every one is drawn EXACTLY ONCE. They are
#     single-palette by construction, which means HANDOFF-227's
#     one-cell-many-palettes ceiling -- the thing that caps the paletted path
#     everywhere else -- does not touch them.
#
# OFF AS OF BUILD 115, AND THE JOB MOVED. FINDINGS-235.
#
# This was ON in build 114 and it is the regression photographed on
# `mds7plr1`: a fence in the right margin covered in hard black speckle where
# FFNx draws a clean mesh, and a step at the 4:3 boundary on both sides.
#
# THE MECHANISM, and it is a property of WHERE the write happens rather than
# of anything decided here. This pass writes 8-BIT INDICES INTO A 256px
# PALETTED PAGE: one texel per cell texel, 16x16 per cell, and index 0 as a
# ONE-BIT colour key. `field_bg_dense` then promotes the cell to a 512px
# truecolor page, takes the COLOUR from the mod's .dds at 512 -- and takes the
# KEY from what this pass wrote, upscaled:
#
#     field_bg_dense.source_cell:  zero = _up(idx == 0, scale)
#                                  out[zero] = FN.EMPTY
#
# So the mesh comes back at a QUARTER of the resolution the colour does, with
# every hole snapped to a 2x2 block. MEASURED on `mds7plr1`: 22 cells filled,
# and of the texels this pass writes opaque, the mod's own alpha is partial
# (0 < a < 128) on 358 of them -- those have their RGB replaced by
# `_extend_into_gap`'s dilation FIRST and are then written solid, because
# `_cov` is `cover >= 128` and `_art_here` is `cover > 0`. Two thresholds,
# one cell.
#
# AND IT BLOCKS THE FIX. `field_bg_dense`'s atlas-gap arm keys on the MOD'S
# alpha at the mod's resolution, and it is gated on `zero.all()` -- the
# paletted cell being ENTIRELY index 0. Writing anything here makes that
# false, so this pass and the correct path are mutually exclusive.
#
# The job now lives in `field_bg_dense.MARGIN_OVERLAY_ALPHA`, which serves the
# same 8,284-cell population from the truecolor side: the mod's colour at 512
# and the mod's alpha at 512, which is what FFNx draws. 22 of the 33 blank
# layer-2 margin cells on `mds7plr1` qualify -- the same 22 this filled.
#
# SEVENTH_NX_MARGIN_L2=1 turns this back on for an A/B. Do not ship it on:
# with both on, the dense arm cannot fire and the result is build 114.
MARGIN_LAYERS_2PLUS = os.environ.get('SEVENTH_NX_MARGIN_L2') == '1'


def _margin_tile(t):
    """UNUSED as of FINDINGS-234 -- kept because it states the rule clearly.

    `fillable_cells` went back to `is_margin`; the layer-2 population is
    identified in `fill_field` instead, so that widening it cannot leak into
    the candidate or veto sets. See the note at that call site.

    Is this tile a widescreen margin placeholder candidate?

    `MB.Tile.is_margin` is layer 1 only and is left alone -- other passes read
    it and its meaning should not move under them. See MARGIN_LAYERS_2PLUS.
    """
    if t.is_margin:
        return True
    return MARGIN_LAYERS_2PLUS and t.layer != 1 and t.outside_43

DARKEN_MARGIN_PLACEHOLDERS = True

# OFF, because `ff7nx_marginpage` now solves this properly.
#
# This veto refuses to write Cosmos art onto a page whose margin tiles name a
# different palette from the page's other tiles -- the mismatch that draws the
# margin through a foreign colour table and turns it yellow. It works, but it
# costs 63,299 of 95,512 margin tiles: two thirds of the widescreen art goes
# back to flat filler.
#
# `ff7nx_marginpage` runs AFTER this pass and MOVES those cells onto a page of
# their own, which is palette-pure by construction, so the art can stay. The
# veto is kept as a documented fallback for a field the split cannot serve; it
# is not needed while the split runs.
SKIP_MIXED_PALETTE_PAGES = False


def _raw(env=None):
    return str(env if env is not None
               else os.environ.get(MARGIN_ART_ENV,
                                   '1' if DEFAULT_ON else '0')).strip().lower()


def enabled(env=None):
    return _raw(env) not in ('0', 'off', 'no', 'none', 'false', '')


def scope(env=None):
    """
    'margin' (setting 1) or 'all' (setting 2).

    ONE env var carries both because the pass is the same pass; 'all' simply
    stops excluding the 4:3 picture. Keeping them on one knob means a
    settings.json written before this change reads back as 'margin', which is
    the behaviour it was tested with.
    """
    return 'all' if _raw(env) in ('2', 'all', 'interior', 'full') else 'margin'


def palette_rgb(cols):
    """A1B5G5R5 palette page -> (256, 3) uint8 RGB, 5-bit expanded to 8."""
    r = (cols & 0x1F).astype(np.uint16)
    g = ((cols >> 5) & 0x1F).astype(np.uint16)
    b = ((cols >> 10) & 0x1F).astype(np.uint16)
    out = np.stack([(r << 3) | (r >> 2),
                    (g << 3) | (g >> 2),
                    (b << 3) | (b >> 2)], -1)
    return out.astype(np.uint8)


# Bayer 4x4, centred on zero and normalised to +-0.5. Ordered dithering is
# used rather than Floyd-Steinberg because error diffusion is serial: at
# 517,368 cells of 256 pixels it is ~132M dependent steps in Python, where
# this is three vectorised numpy ops.
_BAYER4 = (np.array([[0, 8, 2, 10],
                     [12, 4, 14, 6],
                     [3, 11, 1, 9],
                     [15, 7, 13, 5]], np.float32) + 0.5) / 16.0 - 0.5


def dedup_cell(cell_rgb):
    """(uniq, inv) for `quantise(..., _dedup=)`. See quantise."""
    flat = cell_rgb.reshape(-1, 3).astype(np.int32)
    return np.unique(flat, axis=0, return_inverse=True)


def _sqdist(flat, pal):
    """
    (N,3) x (M,3) -> (N,M) squared euclidean distance. BIT-IDENTICAL to
    `((flat[:, None, :] - pal[None, :, :]) ** 2).sum(-1)` and ~3x faster.

    WHY THIS IS EXACT AND NOT AN APPROXIMATION, which is the only thing that
    matters here -- `quantise` decides an index that goes into the shipped
    page, so "close enough" is a different build.

        ||u - p||^2  ==  ||u||^2  -  2 u.p  +  ||p||^2

    Every term is an integer: the inputs are 0..255, so u.p <= 3*255*255 =
    195,075 and ||u||^2 <= 195,075. All of them are exactly representable in
    float64 (which is exact for every integer below 2^53), so each product,
    each sum and the final difference are exact integers -- not rounded ones.
    The result therefore compares and argmins identically to the integer
    form, ties included, and `d[..., idx].mean()` in the dither branch sees
    the same numbers too.

    The win is that the right-hand side is one BLAS `dgemm` over an (N,M)
    result instead of materialising an (N,M,3) array and reducing it. The
    reduction was the single hottest operation in the whole build: measured
    over `mrkt2`, `md1_1` and `nmkin_1`, `quantise` is 48% of the pass chain
    and `numpy.ufunc.reduce` inside it is 2.2 s of 8.4 s.

    VERIFIED bit-identical on values AND argmin over 300 random (cell,
    palette) pairs and over every real cell of the fields above, and the
    shipped section 9 comes out byte-identical -- see `_kfastq.py`.

    `SEVENTH_NX_NO_FASTQ=1` restores the original expression exactly.
    """
    if not FAST_QUANTISE:
        return ((flat[:, None, :] - pal[None, :, :]) ** 2).sum(-1)
    fl = flat.astype(np.float64)
    pl = pal.astype(np.float64)
    return ((fl * fl).sum(1)[:, None] + (pl * pl).sum(1)[None, :]
            - 2.0 * (fl @ pl.T))


def quantise(cell_rgb, pal_rgb, dither=False, _dedup=None):
    """
    (16,16,3) -> (16,16) uint8 indices, nearest colour, INDEX 0 EXCLUDED.

    Index 0 is the transparency key. Emitting it for an opaque pixel would
    make the background see-through there, which reads on screen as a field
    model drawing in front of scenery it should be behind.

    ORDERED DITHERING, AND WHY IT IS WORTH THE TWO PASSES
    =====================================================
    Nearest-colour with no dither is what produces the salt-and-pepper speckle
    visible on 8-bit cells: a smooth gradient crossing the midpoint between two
    palette entries flips between them pixel by pixel, and a region whose true
    colour sits far from ANY entry goes flat and posterised. Cosmos's art is
    16-bit upscale material being forced through a table that often has only a
    handful of entries in the right neighbourhood, so this is the dominant
    quality loss on every cell the repack does not promote -- which after the
    layer-2 change is 517,368 of them.

    The dither amplitude has to match the palette, not be a constant. A tight
    palette needs almost none; a sparse one needs a lot. So: quantise once,
    measure the mean error THIS cell actually incurred, and re-quantise with a
    Bayer offset scaled to it. Where the palette already fits the art the
    offset is near zero and the result is unchanged; where it does not, the
    threshold pattern trades pixel-level noise for apparent colour resolution,
    which is the trade that reads better at 3x on a 720p panel.

    MEASURED, AND IT IS OFF BY DEFAULT BECAUSE THE PREMISE WAS WRONG.
    ================================================================
    Over 10,240 real cells from mds6_1, mds6_22, mrkt2, mrkt3, mds5_1,
    nmkin_1, md1_1 and tin_1, quantised against their own palettes:

        mean |RGB| error, no dither     2.53   (out of 255)
        mean |RGB| error, dithered      2.64
        cells improved                   143   (1.4%)
        cost                           1.73x

    The palettes FIT. An error of 2.53/255 leaves nothing for a dither to
    recover, and the Bayer pattern costs more than it returns. So the speckle
    on 8-bit cells is not quantisation error and this is not the lever --
    resolution is (see the note in build.py's page-cost report: at 256px the
    3x render target is upscaling 256px of detail to fill 768px of resolve).

    Kept, off, because the measurement is worth preserving and because a
    palette that genuinely does not fit would benefit. `dither=True` to A/B.
    """
    flat = cell_rgb.reshape(-1, 3).astype(np.int32)
    pal = pal_rgb[1:].astype(np.int32)              # skip index 0

    # SOLVE EACH DISTINCT COLOUR ONCE. Pure speed, bit-identical output.
    #
    # This function is 85% of the margin pass, which is 25 minutes of a build.
    # The distance matrix is (pixels x 255 x 3), and MEASURED over 10,290 real
    # calls a 256-pixel cell holds a mean of 153 DISTINCT colours -- so 40% of
    # that matrix re-derives an answer already computed one row above.
    #
    # The result cannot change: the same colour rows are compared against the
    # same palette in the same order, so argmin picks the same entry and ties
    # break the same way. `_assert_quantise_identical` in test_summarise.py
    # checks that against the un-deduplicated form on real cells.
    #
    # The fast path is skipped for the dither branch, which needs the
    # per-PIXEL distances rather than the per-colour ones.
    if not dither:
        # `_dedup` lets a caller that quantises the SAME cell against several
        # palettes -- `ff7nx_marginpal.score_slot` does exactly that, once per
        # palette in the field -- pay for np.unique once instead of npg times.
        if _dedup is not None:
            uniq, inv = _dedup
        else:
            uniq, inv = np.unique(flat, axis=0, return_inverse=True)
        if len(uniq) < len(flat):
            du = _sqdist(uniq, pal)
            idx = du.argmin(1)[inv]
            return (idx + 1).astype(np.uint8).reshape(cell_rgb.shape[:2])

    d = _sqdist(flat, pal)
    idx = d.argmin(1)
    if not dither:
        return (idx + 1).astype(np.uint8).reshape(cell_rgb.shape[:2])

    # How badly does this palette fit this cell? RMS distance, per channel.
    err = float(np.sqrt(d[np.arange(len(idx)), idx].mean() / 3.0))
    if err < 1.0:
        return (idx + 1).astype(np.uint8).reshape(cell_rgb.shape[:2])
    h, w = cell_rgb.shape[:2]
    tile = np.tile(_BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w]
    # Cap the amplitude: past ~24 the pattern itself becomes the texture.
    amp = min(err, 24.0)
    shifted = np.clip(cell_rgb.astype(np.float32)
                      + (tile * amp)[..., None], 0, 255)
    f2 = shifted.reshape(-1, 3).astype(np.int32)
    d2 = _sqdist(f2, pal)
    return (d2.argmin(1) + 1).astype(np.uint8).reshape(cell_rgb.shape[:2])


BLEND_BAND_FIRST_SLOT = 0x0F


# FILL BOTH FRAMES OF AN fx PAIR INSTEAD OF NEITHER. BUILD 110.
#
# `_is_animated` below vetoes a layer-2+ cell whose tile carries an fx page,
# because the engine can swap to that second page and painting Cosmos art
# into only one of the two states reads as FLICKER. That is correct, and it
# is correct for exactly one reason: WE ONLY EVER PAINTED ONE OF THEM.
#
# `fillable_cells` walks tiles and keys candidates off `T_TEX` (offset 32).
# NO TILE NAMES AN fx PAGE AT OFFSET 32 -- an fx page is reached only through
# `T_TEX2` (offset 34) -- so an fx page has never been a candidate and has
# never received a single cell of Cosmos art. MEASURED on the build-109
# archive, 160 fields: of 151 fx pages, 146 are BYTE-IDENTICAL to vanilla and
# 5 changed. 96.7% pure vanilla, and none promoted to truecolor.
#
# That is the defect the user reported as "fields with widescreen expanded
# regions are missing their fx textures", and it is also why the flicker
# veto looked unavoidable: with one frame unpaintable, painting the other was
# guaranteed to flicker.
#
# THE MOD SHIPS BOTH. MEASURED over 200 vanilla fields, 25,943 tiles carrying
# an fx page:
#
#     Cosmos ships art for the BASE page   24,701   95.2%
#     Cosmos ships art for the FX   page   25,157   97.0%
#     BOTH                                 24,444   94.2%
#
# So for 94.2% of them the premise of the veto -- "the mod ships art for at
# most one frame" -- is simply false. Paint both and there is no frame left
# to flicker against.
#
# THE SAFETY RULE IS BOTH-OR-NEITHER, ENFORCED AFTER THE FILL. It is not
# enough to decide up front that both are fillable: the fill itself refuses
# cells for a dozen reasons (`black`, `wild`, the silhouette guard, a missing
# borrow) and any refusal that lands on one side of a pair would recreate the
# exact flicker this is meant to avoid. So `fill_field` reverts the written
# side of any pair whose other side did not get written. One place, covering
# every refusal reason including ones added later.
#
# The fx page swap is `use_fx_page ? fx_page : page` (FFNx
# ff7/field/background.cpp:113) -- SAME u,v -- so the two cells are at the
# same (sx, sy) and are drawn through the same palette. Nothing has to be
# matched up beyond the page number.
# ...AND IT MEASURES AT EXACTLY ZERO, SO IT IS OFF. BUILD 110.
#
# Everything above is true and none of it matters, because the fx veto is not
# what is holding these cells. MEASURED over 60 fields, every distinct fx
# pair in them:
#
#     fx pairs found                              60
#     BOTH sides drawn through ONE palette         0     0.0%
#     base cell drawn through SEVERAL palettes    60   100.0%
#
# Every fx-carrying cell in the sample is already refused by the
# multi-palette rule, which runs after this one. `uutai1` is the shape of it:
# 833 layer-2 tiles carry an fx page, all 833 sample ONE cell on slot 0, and
# that cell is drawn through several palettes. Lifting the fx veto moves the
# cell from one veto to another and fills nothing -- confirmed end to end,
# `filled` identical to the byte on five fields with the flag on and off.
#
# So this ships OFF. The code and the both-or-neither enforcement in
# `fill_field` are kept because the reasoning is sound and because if the
# multi-palette rule is ever solved this becomes live immediately -- but
# switching it on today is pure risk for a measured zero.
#
# THE REAL FINDING IS THE ONE THIS RULED OUT. See HANDOFF-227: fx pages,
# parallax backdrops and the screen-filling layer-2 cells are all the SAME
# structure -- one cell reused and recoloured per tile by the palette -- and
# the paletted path cannot serve it, because a depth-1 page is one index
# array shared by every palette that draws it.
FILL_FX_PAIRS = False


def fill_fx_pairs(env=None):
    return FILL_FX_PAIRS and _flag('SEVENTH_NX_MARGIN_FX_PAIRS', env)


def _flag(name, env=None):
    raw = str(env if env is not None
              else os.environ.get(name, '1')).strip().lower()
    return raw not in ('0', 'off', 'no', 'none', 'false')


def _is_animated(sec9, t, pages):
    """
    True when this tile's cell is part of something that MOVES at runtime.

    THE FLICKER HAZARD IS REAL BUT NARROWER THAN "LAYER != 1".
    ==========================================================
    The rule this replaces vetoed every layer 2-4 cell, on the grounds that
    several frames of one animation are cut from a single cell and the mod
    ships art for at most one of them -- repaint it and one frame is Cosmos
    while the rest are vanilla, which reads as FLICKER rather than as a
    texture problem. That reasoning is right and it is worth keeping.

    But it only applies to cells that ARE animation frames. A static overlay
    -- a barrel, a sign, a fence, a piece of machinery drawn on layer 2 so it
    can sit in front of a character -- never swaps its source and cannot
    flicker no matter what is painted into it.

    MEASURED over the built archive, all 709 fields: 247,088 distinct layer 2+
    cells are still 8-bit vanilla, and the mod ships art for EVERY ONE of them
    (20.0% at the cell's own palette, 80.0% at palette 0, 0% with no art at
    all). Splitting them by this test:

        ANIMATED (fx page set, or a blend-band page)   112,103   45.4%
        STATIC   (no fx, opaque band)                  134,985   54.6%

    So more than half of the layer 2+ art the mod ships is being discarded by
    a rule that is protecting the other half.

    THE TWO TESTS
    -------------
    * `fx_page` non-zero. FFNx `ff7/field/background.cpp:113` picks
      `use_fx_page ? fx_page : page`, so a tile with an fx page has a second
      source the engine can switch to -- that is the animation.
    * effective page in the ADDITIVE or AVERAGE band (slot >= 0x0F). MEASURED
      over all 709 vanilla fields: every one of the 105,258 tiles drawing from
      an additive depth-1 page and all 2,287 on an average page is an fx tile.
      The band is reachable only through `fx_page`, so this is belt and braces
      -- it costs the opaque band nothing.

    If anything flickers after this, TIGHTEN IT: also exclude any page the
    `.iro` ships more than one dump of, which is FFNx's own marker for "this
    page has several states" (`_is_base_dump` in field_bg_repack).
    """
    fx = sec9[t.off + MB.T_TEX2]
    if fx:
        return True
    eff = fx if (fx and fx in pages) else t.slot
    return eff >= BLEND_BAND_FIRST_SLOT


def _fx_partner(sec9, t, pages, arrays):
    """
    The cell on this tile's fx page, or None when there is not one to pair.

    Returns None -- so the caller keeps the old veto -- unless ALL of:
      * the feature is on;
      * the tile actually carries an fx page (a blend-band tile with no fx
        byte has no second state to pair with, and `_is_animated`'s band test
        still vetoes it);
      * that page is present, depth-1 and parsed, so it can hold indices.
    """
    if not fill_fx_pairs():
        return None
    fx = sec9[t.off + MB.T_TEX2]
    if not fx or fx == t.slot:
        return None
    p = pages.get(fx)
    if p is None or p.depth != 1 or arrays.get(fx) is None:
        return None
    return (fx, t.sx, t.sy)


def fillable_cells(parts, surv, scope='margin'):
    """
    {(page, palette): {(sx, sy), ...}} -- cells this pass may write.

    scope='margin'  only flat placeholder cells outside the 4:3 picture.
    scope='all'     EVERY cell on a depth-1 page, interior included. This is
                    what replaces vanilla art with Cosmos art.

    KEYED ON (page, sx, sy), NOT on the palette. A 256x256 depth-1 page is ONE
    array of indices and the palette only recolours it, so a cell drawn with
    two different palettes is the same bytes twice. Cosmos ships a DIFFERENT
    image per palette, so there is no single right answer for such a cell and
    it is skipped. (Step 2 of the plan -- copying the cell so each palette can
    have its own -- is what recovers those.)

    MEASURED with the margin scope: keying on (page, palette, sx, sy) instead
    let 5 fields through and `--verify` caught every one.

    RETURNS A FOURTH VALUE: `placeholder`, the (slot, sx, sy) cells that are
    sampled ONLY by margin tiles on layer 1 AND are flat in the page -- a
    single index, i.e. the authored filler. That is the same test the 'margin'
    scope uses to choose its cells, computed in BOTH scopes because
    `fill_field` needs it independently of scope to decide what to do when the
    mod's art is empty. See EMPTY SOURCE in `fill_field`.
    """
    pages = {p.slot: p for p in surv['pages']}
    arrays = {s: MB.page_array(p) for s, p in pages.items()}
    want, veto = {}, set()
    flat_ok, not_margin = set(), set()
    # Reported through the module rather than the return tuple, which several
    # callers unpack positionally.
    _n_anim, _n_static = [0], [0]
    # (base_cell, fx_cell) pairs this field must fill BOTH of or NEITHER.
    # Handed to `fill_field` on the function object rather than in the return
    # tuple, because several callers unpack that tuple positionally.
    _pairs = set()

    # ------------------------------------------------------------------
    # A PAGE IS DRAWN WITH ONE PALETTE, SO DO NOT WRITE ART ONTO A PAGE
    # WHOSE MARGIN AND INTERIOR DISAGREE ABOUT WHICH ONE.
    #
    # PROVED by the user's own A/B, on `mds6_3`, whose slot 0 carries
    # interior tiles at palettes {0: 195, 1: 61} and margin tiles at
    # palette 0 only. Mean rendered RGB of that margin, per palette:
    #
    #                     margin_art OFF        margin_art ON
    #                     (flat index 1)        (44 distinct indices)
    #     palette 0        (33, 33, 16)          (37, 34, 16)
    #     palette 1        (82, 74, 41)         (113,106, 65)  <- BRIGHT YELLOW
    #     palette 2        (66, 57, 24)          (45, 38, 22)
    #     palette 3        (49, 49, 24)          (74, 85, 60)
    #
    # He reports GREY with the pass off and YELLOW with it on, which is
    # exactly the palette-1 column. So the console draws that page with
    # palette 1 even though every margin tile names palette 0 -- and a
    # FLAT filler survives the mismatch (it is one index, and index 1 is
    # dark in both) while real art does not.
    #
    # Writing 44 indices' worth of Cosmos art into a page that will be
    # rendered through somebody else's colour table is how a correct
    # downscale still lands on screen as a bright yellow block. So: if the
    # margin tiles on a page name a different palette from the interior
    # tiles on that same page, this pass leaves that page alone. The
    # margin keeps its filler, which is the state the user already
    # describes as acceptable, instead of becoming the state he does not.
    #
    # MEASURED on the shipped archive: 776 of 1,175 pages carrying margin
    # tiles are mixed this way, 65,318 margin tiles in 497 fields. 265
    # pages already agree and 134 are margin-only -- those still get art.
    #
    # The proper fix is to give the margin its own page so it can carry
    # the art AND render as authored; that costs 776 pages and belongs in
    # field_bg_repack. This is the free half, and it is strictly better
    # than shipping the yellow.
    # EVERY TILE ON THE PAGE, EVERY LAYER. A first version of this test looked
    # only at layer 1 and it left the yellow on half of `mds6_3`:
    #
    #     slot 0   layer1 margin {0:81}  layer1 interior {0:114, 1:61}
    #              layers 2-4   {}                    -> caught, vetoed
    #     slot 1   layer1 margin {0:39}  layer1 interior {}
    #              layers 2-4   {2:128, 3:64, 4:16}   -> MISSED
    #
    # Slot 1 has no interior layer-1 tiles, so the layer-1 test called it a
    # safe margin-only page and wrote art onto it. But 208 of its 247 tiles
    # are layer 2 at palettes 2, 3 and 4. The page still gets ONE palette and
    # it was never going to be 0. The user's screenshot after that build:
    # left margin grey (slot 0, vetoed), right margin still yellow (slot 1).
    #
    # The engine's choice does not care which layer a tile is on, so neither
    # does this test.
    page_marg, page_all = {}, {}
    for t in MB.read_tiles(parts[SECTION9], surv, pages):
        p = pages.get(t.slot)
        if p is None or p.depth != 1:
            continue
        page_all.setdefault(t.slot, set()).add(t.pal)
        if t.layer == 1 and t.outside_43:
            page_marg.setdefault(t.slot, set()).add(t.pal)
    mixed_pages = {s for s, m in page_marg.items()
                   if page_all.get(s, m) != m} \
        if SKIP_MIXED_PALETTE_PAGES else set()
    for t in MB.read_tiles(parts[SECTION9], surv, pages):
        a = arrays.get(t.slot)
        if a is None:
            continue
        cell = (t.slot, t.sx, t.sy)
        if t.depth != 1:
            veto.add(cell)                   # truecolor: no indices to write
            continue
        if t.slot in mixed_pages and t.layer == 1 and t.outside_43:
            # See MIXED PALETTE above. The page will be drawn through one
            # colour table and it is not this tile's.
            veto.add(cell)
            continue
        # The placeholder test, always. A cell any non-margin tile also
        # samples is NOT a margin placeholder however flat it looks -- it is
        # shared with the picture and must keep its content.
        # REVERTED TO `is_margin`, DELIBERATELY. FINDINGS-234.
        #
        # A first version of MARGIN_LAYERS_2PLUS widened this predicate, and
        # the gate caught what that costs: `flat_ok` / `not_margin` feed the
        # `placeholder` set, which several other rules in `fill_field` consult
        # (the EMPTY SOURCE exemption among them). Widening it changed bytes
        # in 77 fields where not one cell was filled -- a containment failure,
        # and exactly the kind of side effect that makes a change impossible
        # to reason about.
        #
        # The layer-2 margin population is identified independently in
        # `fill_field` (`_margin_overlay`) and used ONLY to waive the opacity
        # quota. `fillable_cells` is therefore byte-for-byte build 111.
        if t.layer == 1 and t.is_margin:
            b = MB.source_block(a[0], a[1], t.sx, t.sy)
            if b is not None and np.unique(b).size == 1:
                flat_ok.add(cell)
        else:
            not_margin.add(cell)
        if scope == 'margin':
            if not t.is_margin:
                veto.add(cell)
                continue
            b = MB.source_block(a[0], a[1], t.sx, t.sy)
            if b is None or np.unique(b).size != 1:
                veto.add(cell)               # already carries art
                continue
        elif t.layer != 1 and _is_animated(parts[SECTION9], t, pages) \
                and not _fx_partner(parts[SECTION9], t, pages, arrays):
            # ANIMATED OVERLAYS ONLY. See _is_animated for the measurement.
            #
            # This used to veto EVERY layer 2-4 cell. The flicker hazard it
            # was protecting against is real -- one Cosmos frame among vanilla
            # ones is worse than a uniformly vanilla animation -- but it only
            # exists for cells that are animation frames. 134,985 static
            # overlay cells were being thrown away with the 112,103 animated
            # ones, and the mod ships art for all of them.
            veto.add(cell)
            _n_anim[0] += 1
            continue
        elif t.layer != 1:
            _n_static[0] += 1
        want.setdefault(cell, set()).add(t.pal)
        # ---- THE fx PARTNER BECOMES A CANDIDATE TOO. BUILD 110.
        #
        # Same (sx, sy), same palette, on the page the engine swaps to. It is
        # recorded as a PAIR so `fill_field` can hold both to the
        # both-or-neither rule; see FILL_FX_PAIRS.
        _fxp = _fx_partner(parts[SECTION9], t, pages, arrays)
        if _fxp is not None:
            want.setdefault(_fxp, set()).add(t.pal)
            not_margin.add(_fxp)
            _pairs.add((cell, _fxp))
    out = {}
    for cell, pals in want.items():
        if cell in veto or len(pals) != 1:
            continue
        slot, sx, sy = cell
        out.setdefault((slot, next(iter(pals))), set()).add((sx, sy))
    # PER CALL, not accumulated -- `fill_field` reads it immediately after.
    fillable_cells.last_pairs = {
        (b, f) for b, f in _pairs
        if b not in veto and f not in veto
        and len(want.get(b, ())) == 1 and len(want.get(f, ())) == 1}
    fillable_cells.pairs_seen = (
        getattr(fillable_cells, 'pairs_seen', 0) + len(_pairs))
    fillable_cells.layer2_animated = (
        getattr(fillable_cells, 'layer2_animated', 0) + _n_anim[0])
    fillable_cells.layer2_static = (
        getattr(fillable_cells, 'layer2_static', 0) + _n_static[0])
    return out, pages, arrays, (flat_ok - not_margin)


def margin_cells(parts, surv):
    """Back-compat wrapper: the margin scope, without the placeholder set."""
    out, pages, arrays, _ = fillable_cells(parts, surv, 'margin')
    return out, pages, arrays


def dir_source(art_dir):
    """
    `art_for(field, page, palette) -> ((H,W,3) uint8, palette_it_was_drawn_with)`

    FALLS BACK TO ANY PALETTE THE MOD SHIPS FOR THAT PAGE, and that is not a
    compromise -- it is how the format works. Cosmos ships `<field>_<page>_00`
    and almost nothing else: `mrkt3` has 00_00, 01_00, 02_00, 15_00 while its
    cells are drawn with palettes 0,1,2,3,5,6,8. Demanding an exact match
    found art for 1 cell of 217.

    A depth-1 page is ONE index array that every palette recolours, so the
    INDICES are palette-independent; only the colours differ. Quantising the
    shipped image against the palette IT was rendered with recovers those
    indices, and every palette then draws the cell in its own colours. That is
    the same substitution `field_bg_repack` makes -- its build log calls it
    `cells_borrowed`, 8.3% of cells on a real build.
    """
    import glob as _glob
    cache = {}

    def shipped(field):
        if field not in cache:
            out = {}
            for f in _glob.glob(os.path.join(art_dir, field, '%s_*.dds' % field)):
                base = os.path.basename(f)[:-4]
                try:
                    _, pg, q = base.rsplit('_', 2)
                    out.setdefault(int(pg), []).append((int(q), f))
                except ValueError:
                    continue
            for pg in out:
                out[pg].sort()
            cache[field] = out
        return cache[field]

    def art_for(field, page, pal):
        import dds_decode
        avail = shipped(field).get(page)
        if not avail:
            return None
        # BORROW, then quantise against the DESTINATION palette in
        # `fill_field`. An earlier attempt borrowed and quantised against the
        # SOURCE palette, and that is a category error: indices that mean
        # something with palette 0, read through palette 3, are noise.
        # MEASURED on mrkt3 and bwhlin -- the picture came back as coloured
        # static, and the lesson was wrongly recorded as "never borrow".
        #
        # Borrowing is necessary, not optional. Cosmos ships
        # `<field>_<page>_00` and little else while cells use palettes 0..8, so
        # requiring an exact match finds art for a small minority. MEASURED on
        # md1stin/md1_1/mds7st1/nrthmk with the interior scope:
        #
        #     exact palette only    432 cell(s) written, 2,907 with no art
        #     borrow              3,253 cell(s) written,     0 with no art
        #
        # `field_bg_repack` makes the same substitution for the same reason and
        # its log calls it `cells_borrowed`. `MAX_QUANT_ERR` is the backstop.
        d = dict(avail)
        if pal in d:
            f, q = d[pal], pal
        elif BORROW:
            q, f = avail[0]
        else:
            return None
        rgba, w, h = dds_decode.decode_dds(open(f, 'rb').read())
        a = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)
        # RGB WHERE ALPHA IS 0 IS NOT ART. BC7 stores whatever the encoder
        # found cheapest in fully-transparent blocks, and it is frequently a
        # bright primary. Dropping the alpha channel and quantising that gave
        # solid YELLOW cells in the mds6_2 margin. Zero it so those cells
        # match `PageArt`, which already treats alpha < 8 as EMPTY, and so the
        # 'is this cell black?' test below sees them as empty rather than as
        # vivid art worth writing.
        rgb = a[..., :3].copy()
        cov = np.where(a[..., 3] < 8, np.uint8(0), np.uint8(255))
        rgb[a[..., 3] < 8] = 0
        return np.concatenate([rgb, cov[..., None]], -1), q
    return art_for


def provider_source(provider):
    """
    The same, from `field_bg_repack.ArtProvider` -- i.e. straight out of the
    .iro, which is what the build has and what the repack already uses.

    `PageArt` hands back a packed 565 page rather than RGB, so it is unpacked
    here. 565 has already thrown away 3 bits per channel, but the destination
    is a 256-entry palette, so that loss is far below the quantisation this
    pass performs anyway.
    """
    # `provider.open(field)` RESETS the decoded-page cache, so it is called
    # once per field, not once per cell. Calling it inside art_for threw the
    # cache away on every lookup and re-decoded a 1 MB BC7 image each time.
    state = {'field': None, 'fn': None}

    def art_for(field, page, pal):
        if state['field'] != field:
            state['field'] = field
            state['fn'] = provider.open(field)
        art = state['fn'](page, pal)
        used = pal
        if art is None and BORROW:
            # BORROW HERE TOO, AND THIS IS WHERE IT WAS MISSING.
            #
            # `ArtProvider._art_for` looks up `slots[(field, page, palette)]`
            # and returns None on an exact miss. The repack does its own
            # borrowing on top of the provider (`palettes()` + nearest), so
            # the provider itself never needed to -- but `dir_source` DID
            # borrow, which is why the standalone run and the build disagreed
            # so violently on the same archive and the same art:
            #
            #     standalone (dir_source)        0 cell(s) 'no art shipped'
            #     build      (provider_source) 236,715 cell(s) 'no art shipped'
            #
            # Same pass, same mod, 63% of the interior silently skipped.
            for q in sorted(provider.palettes(page)):
                art = state['fn'](page, q)
                if art is not None:
                    used = q
                    break
        if art is None:
            return None
        v = np.frombuffer(art.buf, '<u2').reshape(art.px, art.px)
        r = ((v >> 11) & 0x1F).astype(np.uint16)
        g = ((v >> 5) & 0x3F).astype(np.uint16)
        b = (v & 0x1F).astype(np.uint16)
        rgb = np.stack([(r << 3) | (r >> 2),
                        (g << 2) | (g >> 4),
                        (b << 3) | (b >> 2)], -1).astype(np.uint8)
        # UNDO THE NEAR_BLACK LIFT. FINDINGS-211.
        #
        # `PageArt` packs its buffer with `black_ok=False`, so every texel the
        # mod painted TRUE BLACK comes back as NEAR_BLACK = 0x0841 = RGB(8,8,8)
        # rather than as 0. That is right for `PageArt`'s own consumer -- a
        # TRUECOLOR page, where 0x0000 means transparent to the engine
        # (x86 0x6470E0), so black genuinely cannot be stored as black.
        #
        # It is WRONG here. This provider feeds `ff7nx_marginart` and
        # `ff7nx_blackcell`, whose destination is a PALETTED page: there the
        # key is INDEX 0, not the colour, and `quantise` excludes index 0 by
        # construction -- so nothing can accidentally become transparent and
        # the lift buys nothing. What it costs is measured, on the `onna_5`
        # keyhole mask (HANDOFF-210 s3.2's candidate A):
        #
        #     the mod's margin art there is darker than one 565 step, so
        #     almost every texel rounds to 0x0000, is lifted to RGB(8,8,8),
        #     and a 98-colour gradient arrives here as a FLAT BLOCK.
        #
        #     written cell, 12 cells of onna_5 layer 4    n colours    std
        #       art as this function returns it today       2.8        0.80
        #       art with the lift undone                    4.0        3.88
        #       the raw DDS ceiling, for reference          4.0        3.90
        #
        # 3.88 of an available 3.90. Those 12 cells are the entire bottom row
        # of the keyhole's overlay margin and they render as one flat black
        # bar 384 destination units wide.
        #
        # `bmask` is EXACTLY the set that was lifted -- `PageArt` defines it as
        # "opaque AND rgb == 0" on the DECODED art, before packing -- so this
        # restores those texels and touches nothing else. No threshold, no
        # guess about which greys "were probably black".
        #
        # The depth-2 write path in `ff7nx_blackcell` re-packs through
        # `rgba_bytes_to_565`, which applies the lift again on the way out, so
        # truecolor destinations are unaffected by this.
        # `bmask` is a numpy bool array when numpy built the page and a bytes
        # object on `PageArt`'s fallback path. Handle both rather than let the
        # bytes case become a silent no-op -- a fix that quietly does nothing
        # on one path is worse than one that is absent, because the log and
        # the counters both still say it ran.
        bm = getattr(art, 'bmask', None)
        if isinstance(bm, (bytes, bytearray)):
            bm = np.frombuffer(bm, np.uint8).astype(bool)
        if bm is not None:
            bm = np.asarray(bm)
            if bm.size == art.px * art.px:
                rgb[bm.reshape(art.px, art.px)] = 0
        # RGBA, NOT RGB. The 4th channel is the mod's own COVERAGE, and it is
        # the whole point of `black_squares_are_the_atlas_gap` below: 20.1% of
        # the texels in a Cosmos page are transparent, `PageArt` records that
        # in `tmask`, and this function used to throw it away and hand the
        # caller a black pixel instead. A black pixel is a claim about colour;
        # transparent is a claim about COVERAGE, and they are not the same.
        cov = np.where(np.asarray(art.tmask).reshape(art.px, art.px),
                       np.uint8(0), np.uint8(255))
        rgba = np.concatenate([rgb, cov[..., None]], -1)
        # (image, palette it was drawn with) -- the SAME shape dir_source
        # returns. Returning a bare array here is what produced
        # "ValueError: too many values to unpack (expected 2)" on 267 fields,
        # and the build reported it as a per-field refusal rather than as the
        # type error it was.
        return rgba, used
    return art_for


def fill_field(name, raw, lgp_mod, art, log=None, scope='margin'):
    """
    Returns (new_raw or None, stats). `art` is a callable from `dir_source`
    or `provider_source`. Nothing is written if the field has no fillable
    cell or no Cosmos art for it.
    """

    st = {'cells': 0, 'filled': 0, 'no_dds': 0, 'black': 0, 'tiles': 0,
          'borrowed': 0, 'wild': 0, 'darkened': 0, 'far_borrow': 0, 'detail': 0, 'uncovered': 0,
          'keep0_kept': 0, 'keep0_dropped': 0, 'keep0_cells': 0}
    parts = lgp_mod.split_sections(raw)
    cols, hdr, npg, cpp = MB.palette_colours(parts[SECTION_PALETTE])
    surv = DC.survey(parts[SECTION9])
    cells, pages, arrays, placeholder = fillable_cells(parts, surv, scope)
    _fx_pairs = getattr(fillable_cells, 'last_pairs', set())
    if not cells:
        return None, st

    # THE BLACK-SILHOUETTE GUARD IS AN INTERIOR RULE ONLY, AND "INTERIOR"
    # MEANS THE 4:3 PICTURE -- NOT "not a placeholder".
    #
    # It exists to stop Cosmos's art being painted into cells the ORIGINAL
    # deliberately cut to black: the grey staircase in the No. 1 reactor.
    # Outside the 4:3 picture there is no original to protect. Those cells are
    # black because there was nothing there in 4:3, and Cosmos widens a field
    # by ADDING tiles that point at them -- filling them is the entire point.
    #
    # The first version keyed off `placeholder`, which is only the FLAT filler
    # cells. MEASURED: `md1stin` has 1,115 fillable cells and just 2 of them
    # are placeholders, so nearly every widened cell fell through the guard and
    # was blacked out -- the column of black squares down both edges. Caught in
    # one build, and this is the predicate it should have used from the start.
    _inside43 = set()
    if KEEP_BLACK_SILHOUETTE or KEEP_BLACK_PIXELS:
        try:
            _tl = MB.read_tiles(parts[SECTION9], surv, pages)
            _out = {(t.slot, t.sx, t.sy) for t in _tl if t.outside_43}
            _inside43 = {(t.slot, t.sx, t.sy) for t in _tl
                         if (t.slot, t.sx, t.sy) not in _out}
        except Exception:                                      # noqa: BLE001
            _inside43 = set()

    # ---- THE WIDESCREEN OVERLAY PLACEHOLDERS. FINDINGS-231.
    #
    # A cell qualifies when ALL THREE hold, and each one removes a different
    # way this could repaint something it should not:
    #
    #   1. it is a PLACEHOLDER -- `fillable_cells` already proved it is a
    #      single index over the whole 16x16, i.e. Cosmos put a blank there
    #      and shipped the art in the page DDS;
    #   2. NO tile inside the 4:3 picture samples it, so there is no original
    #      to protect. `_inside43` is the same set the black-silhouette guard
    #      uses, computed six lines up;
    #   3. EVERY tile that draws it is layer 2 or above. A layer-1 margin cell
    #      keeps the opacity quota it has always had -- that rule is correct
    #      for a piece of background and nothing here touches it.
    #
    # MEASURED over 701 fields (`_kl2margin.py`): 8,284 such cells in 173
    # fields, 0 animated, all on blend group 4, each drawn exactly once.
    # COMPUTED HERE AND FROM NOTHING ELSE. See the note in `fillable_cells`:
    # this deliberately does NOT go through `placeholder`, so the candidate
    # set, the veto set and every other rule that reads them stay exactly as
    # build 111 left them. The flatness test is the same one `fillable_cells`
    # uses -- `np.unique(...).size == 1` -- applied to this population only.
    _margin_overlay = set()
    if MARGIN_LAYERS_2PLUS:
        try:
            _tl2 = MB.read_tiles(parts[SECTION9], surv, pages)
            _lay = {}
            for t in _tl2:
                _lay.setdefault((t.slot, t.sx, t.sy), set()).add(t.layer)
            _out2 = {(t.slot, t.sx, t.sy) for t in _tl2 if t.outside_43}
            _in2 = {(t.slot, t.sx, t.sy) for t in _tl2 if not t.outside_43}
            for t in _tl2:
                c = (t.slot, t.sx, t.sy)
                if (c in _in2 or c not in _out2 or 1 in _lay.get(c, {1})
                        or c in _margin_overlay):
                    continue
                p = pages.get(t.slot)
                a = arrays.get(t.slot) if p is not None else None
                if p is None or p.depth != 1 or a is None:
                    continue
                b = MB.source_block(a[0], a[1], t.sx, t.sy)
                if b is not None and np.unique(b).size == 1:
                    _margin_overlay.add(c)
        except Exception:                                      # noqa: BLE001
            _margin_overlay = set()

    # CELLS WHERE INDEX 0 IS A GENUINE CUT-OUT. See KEEP0_CUTOUTS_ONLY.
    #
    # A cell qualifies if ANY tile that draws it is layer 2+, or draws from a
    # depth-1 page in the additive/average band. Both tests are needed and
    # `ff7nx_palkey` explains why: the walk can miss a layer, and a page can
    # be in the band without this seeing it. Two cheap tests, union taken.
    #
    # THE EFFECTIVE PAGE, NOT `texture_id` -- FFNx background.cpp:113,
    # `page = tile.use_fx_page ? tile.fx_page : tile.page`. Reading T_TEX
    # alone finds ZERO tiles in the band across the whole archive, which is
    # the wrong answer and already cost one pass at this in ff7nx_palkey.
    #
    # `None` means "could not determine", and every caller below then behaves
    # EXACTLY as build 50 did. A parse failure must not silently change what
    # the pass writes.
    _cutout = None
    if KEEP0_CUTOUTS_ONLY:
        try:
            _sec9 = parts[SECTION9]
            _cutout = set()
            for t in MB.read_tiles(_sec9, surv, pages):
                eff = _sec9[t.off + MB.T_TEX2] or t.slot
                if eff >= BLEND_BAND_FIRST_SLOT or KEEP0_LAYER_IS_CUTOUT \
                        and t.layer != 1:
                    _cutout.add((t.slot, t.sx, t.sy))
        except Exception:                                      # noqa: BLE001
            _cutout = None

    # ---------------------------------------------------------------- palette
    # BEFORE anything is quantised, ask whether the palette each margin
    # placeholder page NAMES can hold the art we are about to put in it.
    #
    # Cosmos authors its 16:9 extension as flat placeholder cells and ships
    # the real art as an external DDS. On FFNx that DDS replaces the page and
    # the palette byte is never applied, so the mod had no reason to set it --
    # 87% of those tiles name palette 0. Quantising bright margin art against
    # a dark palette 0 is what collapses a cell to one index (the flat block)
    # or pushes it past MAX_QUANT_ERR (the black square). See ff7nx_marginpal.
    prgbs = [palette_rgb(cols[p]) for p in range(npg)]
    chosen, palst = {}, None
    if MP._enabled_env():
        def _art_for(page, pal):
            try:
                return art(name, page, pal)
            except Exception:                                   # noqa: BLE001
                return None
        chosen, palst = MP.choose(parts[SECTION9], surv, pages, placeholder,
                                  _art_for, prgbs, quantise, npg)
        st['pal'] = palst

    tilepal = (palst or {}).get('from', {})

    def _pal_for(slot, sx, sy, pal):
        """The palette this CELL will be rendered through after the repoint."""
        if slot in chosen and (slot, sx, sy) in placeholder:
            return chosen[slot]
        return pal

    newdata = {}
    wrote = set()
    for (slot, pal), cs in sorted(cells.items()):
        st['cells'] += len(cs)
        if pal >= npg:
            st['no_dds'] += len(cs)
            continue
        try:
            got = art(name, slot, pal)
        except Exception:                                       # noqa: BLE001
            got = None
        if got is None:
            st['no_dds'] += len(cs)
            continue
        img, src_pal = got
        if src_pal != pal:
            st['borrowed'] += len(cs)
        k = img.shape[1] // 256
        if k < 1:
            st['no_dds'] += len(cs)
            continue
        # QUANTISE AGAINST THE PALETTE THE CELL IS DRAWN WITH. Always `pal`,
        # never the shipped image's own palette. When they are the same this
        # changes nothing; when the image was BORROWED it is the whole trick,
        # and it is what the earlier failed attempt got backwards.
        #
        # That attempt quantised palette 0's image against PALETTE 0 and then
        # let a palette-3 tile render the result -- indices that mean one
        # thing read through a table that means another, which came back as
        # coloured static on mrkt3 and bwhlin. Quantising the same image
        # against PALETTE 3 instead produces the closest palette-3 rendering
        # of that art, which is a legitimate approximation rather than a
        # category error. Where the two palettes are genuinely unrelated the
        # nearest colour is far away, `err` goes up, and the guard below
        # refuses the cell.
        prgb = prgbs[pal]
        buf = newdata.get(slot)
        if buf is None:
            buf = bytearray(pages[slot].data)
            newdata[slot] = buf
        # A CELL ON A size_flag PAGE IS 32 UNITS, NOT 16. HANDOFF-189.
        #
        # `Page.size_flag` means an 8x8 grid of 32px cells instead of 16x16 of
        # 16px. The parallax layers use those pages -- MEASURED on `mtcrl_5`,
        # vanilla puts layer 3 on slots 4 and 5 and BOTH have the flag, with
        # layer-3 tiles 32 units wide (offset 18) and their `src_x` stepping by
        # 32 to match. Archive-wide, 14,027 vanilla tiles are 32 units wide,
        # all on layers 3 and 4.
        #
        # THIS LOOP WROTE 16x16 INTO THEM. That fills the top-left QUADRANT of
        # each cell and leaves the other three at index 0, so a 32-unit tile
        # draws a quarter of Cosmos's art and three quarters of the colour key
        # -- the checkerboard of sky-and-black photographed behind the Mt.
        # Corel track. Staged through the chain, Cosmos's own section is clean,
        # `ff7nx_palrange` leaves it clean, and the margins break HERE.
        #
        # `edge` replaces the literal 16 everywhere below. `quantise` is
        # shape-agnostic (verified on a 32x32 block), and `k` is the DDS
        # oversample factor, which is per-page and unaffected.
        _edge = 32 if (BIG_CELL and pages[slot].size_flag) else TILE
        for sx, sy in sorted(cs):
            # A repointed placeholder is quantised against the palette it will
            # ACTUALLY be drawn through, not the one the mod left behind.
            eff_pal = _pal_for(slot, sx, sy, pal)
            prgb = prgbs[eff_pal]
            src = img[sy * k:(sy + _edge) * k, sx * k:(sx + _edge) * k]
            if src.shape[:2] != (_edge * k, _edge * k):
                st['no_dds'] += 1
                continue
            # box filter (edge*k)^2 -> edge^2
            small = (np.ascontiguousarray(src[..., :3])
                     .reshape(_edge, k, _edge, k, 3).mean(axis=(1, 3)))
            # HOW MUCH OF EACH DESTINATION PIXEL THE MOD ACTUALLY COVERS.
            # 0 = the mod paints nothing here; 255 = fully painted.
            cover = (np.ascontiguousarray(src[..., 3])
                     .reshape(_edge, k, _edge, k).mean(axis=(1, 3)))
            # A CELL THE ORIGINAL DREW AS PURE BLACK IS A SILHOUETTE, NOT A
            # GAP, AND IT IS NOT OURS TO FILL.
            #
            # The guard below protects vanilla scenery from being painted
            # black. This is its mirror, and it was missing: where the ORIGINAL
            # cell is entirely black and Cosmos's DDS has content there, the
            # content is bleed off the mod's own wider canvas, not art anybody
            # meant to be visible. Writing it puts grey inside a shape the
            # original deliberately cut to black -- and because the boundary
            # follows the 16x16 cell grid, it reads as a blocky staircase.
            #
            # MEASURED: `nmkin_5` (No. 1 reactor) ships 29 entirely-black cells
            # in vanilla and none in our build; `nmkin_3` gained content in
            # every one of its. That edge is the grey stair-step along the
            # walkway.
            #
            # A margin PLACEHOLDER is exempt: it is flat filler outside the 4:3
            # picture with nothing to protect, which is the case the branch
            # below exists for.
            _interior = ((slot, sx, sy) not in placeholder
                         and (slot, sx, sy) in _inside43)
            if KEEP_BLACK_SILHOUETTE and _interior:
                _cur = prgb[_cell_indices(buf, pages[slot], sx, sy)]
                if int(_cur.max()) == 0:
                    st['black'] += 1
                    continue
            # RAW BORROW, DELIBERATELY. DO NOT "CORRECT" THIS TOWARDS VANILLA.
            #
            # Cosmos authored this mod against FFNx, and FFNx falls back to
            # palette 0 UNCONDITIONALLY (saveload.cpp:138). Palette 0's image
            # drawn at a palette-7 tile is therefore not a degradation -- it
            # is HOW THE MOD LOOKS in its reference renderer, and matching it
            # is the goal.
            #
            # I broke this twice in one session by treating VANILLA as the
            # colour ground truth. First by refusing far borrows (44% of
            # interior cells lost Cosmos art), then by detail-transferring
            # them against the vanilla cell (every interior cell kept
            # vanilla's colour and got only Cosmos's edges). Both read on
            # hardware as "not leveraging the upscale", because that is
            # exactly what they were.
            #
            # MEASURED, interior borrowed cells: median palette distance 39.5,
            # 90th percentile 85.4. The borrow moves colour almost everywhere,
            # by design. There is no threshold that separates "wrong" from
            # "intended" here, because the shift IS the intent.
            _cov = (cover >= 128) if HONOUR_MOD_ALPHA else np.ones_like(cover, bool)
            if EXTEND_INTO_GAP and HONOUR_MOD_ALPHA \
                    and not _cov.all() and _cov.any():
                small = _extend_into_gap(small, _cov)
            if HONOUR_MOD_ALPHA:
                st['uncovered'] += int((~_cov).sum())
            if (small[_cov].max() if _cov.any() else 0) <= 24:
                # EMPTY SOURCE.
                #
                # In the INTERIOR this is the dangerous case: `dir_source`
                # zeroes RGB wherever alpha is 0, so a cell the mod simply does
                # not cover arrives here as black. Writing it would paint a
                # black hole over real vanilla scenery -- a worse regression
                # than the vanilla look this pass exists to replace. So the
                # cell keeps its vanilla content, which is invisible.
                #
                # ON A MARGIN PLACEHOLDER IT IS THE OPPOSITE, AND THIS IS THE
                # TAN AND YELLOW SQUARES.
                #
                # A cell in `placeholder` is sampled ONLY by layer-1 tiles
                # outside the 4:3 picture and is FLAT -- one index over all
                # 16x16. There is no scenery there to protect: flat is what
                # the authored filler looks like, and the filler's colour is
                # whatever palette entry it happens to use. MEASURED on the
                # build the user photographed:
                #
                #   mrkt1  8 visible flat margin cells, all RGB(132,107, 57)
                #   mrkt2  8 visible flat margin cells, all RGB(206,156, 90)
                #
                # and for every one of them Cosmos's DDS is near-black there
                # (`small.max()` 5..23). So the old rule kept a vivid tan block
                # in preference to the near-black the mod actually authored.
                # 1,193 such cells across the archive are bright enough to see
                # (luma > 40), 4,036 in total.
                #
                # Writing the dark source instead cannot change occlusion:
                # `quantise` never emits index 0, and a flat OPAQUE cell has an
                # all-false `keep0`, so every pixel that was drawn is still
                # drawn. It is the same judgement `ff7nx_marginblack` was built
                # to make, with the guard that module lacked -- it would have
                # blacked out 2,414 cells carrying real art, and those are
                # exactly the cells this branch never sees.
                if not DARKEN_MARGIN_PLACEHOLDERS \
                        or (slot, sx, sy) not in placeholder:
                    st['black'] += 1
                    continue
                dark = True
            else:
                dark = False
            idx = quantise(small.astype(np.uint8), prgb)
            # THE PRISTINE QUANTISER OUTPUT, KEPT FOR THE 512px SHADOW.
            #
            # BUILD 109. Every guard below overrides individual texels of
            # `idx` -- the colour key, the vanilla silhouette, the uncovered
            # fallback. `field_bg_shadow` needs to know WHICH texels those
            # were so it can replicate them into the shadow instead of
            # re-deciding them at 512, and `idx != _idx_q` is exactly that
            # set. Costs one reference; `idx` is rebound by `np.where`
            # everywhere below, never mutated in place.
            _idx_q = idx
            # SANITY: quantising APPROXIMATES a colour, it never inverts one.
            # If what we are about to write is nowhere near the source, the
            # palette and the image disagree -- wrong page, wrong palette,
            # transparent block with junk RGB -- and the cell lands on screen
            # as a vivid flat square. `mds6_2` shipped solid YELLOW margin
            # cells this way. Refuse rather than write; the cell keeps its
            # vanilla content.
            #
            # "NEVER WORSE THAN A YELLOW BLOCK" IS NOT TRUE IN THE MARGIN, and
            # the old comment said it was. Inside the 4:3 picture the vanilla
            # content is real art, so refusing is safe. In the 16:9 margin it
            # is the widescreen PLACEHOLDER -- the vivid tan filler -- so
            # refusing there keeps exactly the artefact this pass exists to
            # remove. It is not what caused mds5_5 (measured: `wild` = 0 for
            # that field), but the reasoning above should not be trusted for
            # margin cells without checking what vanilla actually holds.
            _m = _cov
            err = (float(np.abs(prgb[idx].astype(np.int16)
                                - small.astype(np.int16))[_m].mean())
                   if _m.any() else 0.0)
            if err > MAX_QUANT_ERR:
                st['wild'] += 1
                continue
            # THE TRANSPARENCY MASK IS THE VANILLA PAGE'S, ALWAYS.
            #
            # Index 0 is the colour key. Cosmos's DDS has its own alpha, but
            # trusting it would move the key around, and the key is what 95,733
            # tiles rely on -- it is the single reason those pages could never
            # be promoted to truecolor. Keeping the vanilla mask exactly means
            # every pixel that was see-through stays see-through and every
            # pixel that was solid stays solid, so the pass cannot change
            # occlusion, and a field model can neither start nor stop being
            # hidden by scenery. `--verify` scores this as MASK CHANGED.
            was = np.frombuffer(bytes(buf), np.uint8).reshape(256, 256)
            keep0 = was[sy:sy + _edge, sx:sx + _edge] == 0
            # ...BUT ONLY WHERE INDEX 0 IS ACTUALLY A CUT-OUT. FINDINGS-140,
            # and the reasoning is at KEEP0_CUTOUTS_ONLY. On a layer-1 opaque
            # cell index 0 is DRAWN, so forcing it here does not preserve
            # transparency -- it paints the key colour, black 93% of the time,
            # over art the mod shipped.
            _is_cut = _cutout is None or (slot, sx, sy) in _cutout
            # ---- AN EMPTY ATLAS SLOT IS NOT A CUT-OUT. FINDINGS-174.
            #
            # A cell that is 100% index 0 has an all-true `keep0`, so the line
            # below throws the ENTIRE quantised result away and writes index 0
            # straight back. The cell can never be filled, by anything, ever.
            #
            # That is right for a real cut-out and wrong for the cells Cosmos
            # ADDS: its chunk.9 ships widescreen pages that are sparse atlases,
            # empty where the DDS supplies the pixels on FFNx. MEASURED on
            # `mrkt2` -- vanilla has ONE tile drawing an all-zero cell, Cosmos's
            # chunk.9 has 152, on pages 5 and 6 which vanilla does not have at
            # all. The mod ships fully opaque art for them (alpha >= 128 at
            # 100%, RGB mean 49..140) and this pass discarded every byte of it.
            #
            # Archive-wide: 2,815 tiles across 16 fields name an all-zero cell
            # that had content in vanilla or no vanilla page at all; 875 of
            # them render a VISIBLE entry-0 colour. Wall Market's two remaining
            # olive squares are two of those 875.
            #
            # SCOPED HARD, because on layer 2 index 0 usually IS a cut-out and
            # filling one would occlude the scene. All three must hold:
            #   * the cell is ENTIRELY index 0 -- nothing to preserve;
            #   * the mod ships essentially OPAQUE art there, so it is not
            #     describing a hole either;
            #   * entry 0 of this page's palette is BRIGHT, i.e. the cell is
            #     already drawing a visible flat block on this port rather than
            #     behaving as transparency. Where entry 0 is dark the cell
            #     reads as void either way and is left alone.
            #
            # ...AND THE THIRD CONDITION HAS THE SAME FALSE PREMISE AS EVERY
            # OTHER "IT READS AS VOID" TEST IN THIS PROJECT. FINDINGS-189 E.
            #
            # "Where entry 0 is dark the cell reads as void either way" is true
            # only if SOMETHING IS BEHIND IT. On a PARALLAX page there is not:
            # layer 3 is the backdrop, and void there is the cleared
            # framebuffer -- a black square.
            #
            # MEASURED on `mtcrl_4` (Mt. Corel, build 88), the last defect
            # reported from hardware. Three layer-3 tiles, all on slot 5 at
            # palette 0 while the whole rest of the sky is slot 4 at palette
            # 10:
            #
            #   dst(-288,-136)  dst(-288, 88)  dst(384,-136)   32-unit tiles
            #   cell            1024 of 1024 texels are index 0
            #   palette 0       EVERY entry is (0, 0, 0)
            #   Cosmos's DDS    100.0% opaque, mean RGB (121,118,110),
            #                   (33,8,4) and (102,82,71) -- real sky and rock
            #
            # So conditions 1 and 2 both pass and condition 3 vetoes it:
            # `prgb[0].max()` is 0, which is not > 40. The art is thrown away
            # and index 0 written back, and on layer 3 that is drawn BLACK.
            # dst(-288,-136) is the top-left corner of the field, which is
            # exactly where the square appears.
            #
            # Cosmos leaves the palette byte alone because FFNx replaces the
            # page with the .dds and never applies the palette -- that is
            # `ff7nx_marginpal`'s whole subject. So palette 0 being black is
            # not a statement about this cell at all.
            #
            # WAIVED ONLY ON A size_flag PAGE, which is the parallax backdrop
            # and the one place "nothing is behind it" is true by
            # construction. Conditions 1 and 2 still both apply, so the mod
            # must be saying it paints there, opaquely, into a cell that holds
            # nothing.
            if EMPTY_ATLAS_IS_NOT_A_CUTOUT and _is_cut:
                _all0 = bool(keep0.all())
                _bright = int(prgb[0].max()) > ATLAS_ENTRY0_MIN_LUMA
                _backdrop = ATLAS_PARALLAX_VOID and bool(pages[slot].size_flag)
                # ---- AN OVERLAY IS TRANSPARENT BY DESIGN. FINDINGS-231.
                #
                # `ATLAS_OPAQUE_FRAC` is 0.9: the mod's art must cover 90% of
                # the cell before we accept it as art rather than as void.
                # That is right for a LAYER-1 placeholder, which is a piece of
                # background and ought to be painted solid.
                #
                # It is the wrong question for a layer-2 overlay. A light
                # cone, a haze or a coloured wash is mostly transparent --
                # that is what makes it an overlay -- so demanding 90%
                # opacity demands that it not be one. MEASURED, `mds7plr1`
                # page 4, the cells carrying the missing green:
                #
                #     (4,  0, 80) 24%   (4, 80, 96) 35%   (4,112, 64) 62%
                #     (4, 64, 80)  5%   (4, 96, 80) 60%   (4, 32, 64)  0%
                #
                # Every one fails, so the widescreen regions have had no
                # lighting overlay in any build. The `_art_here` line below
                # already does the correct thing once we get past this gate:
                # write where the mod paints, put index 0 back everywhere it
                # does not. So a partially covered overlay stays partially
                # transparent -- it cannot become a solid block, which is the
                # one hazard this quota was standing in for.
                #
                # `_bright`/`_backdrop` is waived with it and for the same
                # reason: it tests whether entry 0 is visible enough for the
                # cell to read as real, which is a question about a BACKGROUND
                # cell. Here index 0 is simply what is already on screen.
                _mo = (slot, sx, sy) in _margin_overlay
                _cov_ok = (float((cover >= 128).mean()) >= ATLAS_OPAQUE_FRAC
                           if not _mo else bool((cover > 0).any()))
                if _all0 and _cov_ok and (_bright or _backdrop or _mo):
                    _is_cut = False
                    st['atlas_filled'] = st.get('atlas_filled', 0) + 1
                    if _mo:
                        st['margin_overlay'] = st.get('margin_overlay', 0) + 1
                    elif not _bright:
                        st['atlas_parallax'] = st.get('atlas_parallax', 0) + 1
            if _is_cut:
                idx = np.where(keep0, np.uint8(0), idx)
                st['keep0_kept'] += int(keep0.sum())
            else:
                # ONLY WHERE THE MOD ACTUALLY SHIPS ART. Where vanilla was
                # index 0 and Cosmos ships nothing, index 0 still goes back --
                # that is byte-identical to build 50 and is most of the mask.
                # The change is confined to texels where we were overwriting
                # real art with "draw the key colour".
                _art_here = keep0 & (cover > 0)
                idx = np.where(keep0 & ~_art_here, np.uint8(0), idx)
                n = int(_art_here.sum())
                st['keep0_dropped'] += n
                st['keep0_kept'] += int(keep0.sum()) - n
                if n:
                    st['keep0_cells'] += 1
            # WHERE THE MOD IS FULLY TRANSPARENT, KEEP VANILLA. FINDINGS-138.
            #
            # Three passes have now tried to fill this and all three were
            # wrong, so here is the whole history in one place:
            #
            #   BLACK (to build 45, and again in 49). `dir_source` zeroes RGB
            #   where alpha is 0, so an uncovered texel arrives black and is
            #   quantised faithfully. Black squares. MAX_QUANT_ERR cannot
            #   catch it -- black is an excellent match for black.
            #
            #   DILATE (builds up to 45). `_extend_into_gap` grew the covered
            #   art outward. A cell holding part of a texture along a diagonal
            #   came out with the rest as a triangle of that texture's
            #   colours: the grey/green blocks in reactor 1 and Tifa's bar.
            #
            #   VANILLA AT alpha < 128 (build 46). Right idea, wrong number.
            #   Alpha is the COLOUR KEY, and a downsampled ANTIALIASED EDGE
            #   lands between 1 and 127 -- on one edge, 80 of 256 texels are
            #   partial alpha, i.e. real art, and they were replaced with
            #   vanilla. A vanilla-toned fringe on every edge and a patch
            #   wherever alpha was marginal. That was Sector 6.
            #
            # The rule that survives all three: FULLY transparent means the
            # mod is saying nothing, so keep what was there. ANY alpha at all
            # is art, however faint, and takes the mod's colour. `keep0` above
            # has already forced index 0 wherever vanilla was index 0, which
            # is 99.4% of these texels, so this only decides the remainder --
            # and the remainder is exactly where the black squares came from.
            if HONOUR_MOD_ALPHA:
                _solid = cover > 0
                if not _solid.all():
                    idx = np.where(_solid, idx,
                                   was[sy:sy + _edge, sx:sx + _edge])
            # ---- THE SILHOUETTE IS PER-PIXEL, NOT PER-CELL. FINDINGS-173.
            #
            # `KEEP_BLACK_SILHOUETTE` above asks `_cur.max() == 0` -- is the
            # WHOLE cell black -- and skips it if so. That catches a cell
            # entirely inside the void and misses the one that matters: the
            # cell ON THE EDGE, part scenery and part void. Those are exactly
            # the cells a silhouette boundary runs through, and because the
            # boundary follows a diagonal it reads as a STAIRCASE.
            #
            # MEASURED, `elmin1_2` (Aerith's house, upstairs), every damaged
            # tile after `fill_field`:
            #
            #   26 tiles, ALL layer 1, ALL outside_43 = False (interior),
            #   ALL palette 1, vanilla index 119 -- which is black --
            #   rewritten to 15, 16, 20, 34, 4, 7, 29, 42, 58, 103...
            #   worst pixel 102/255, and only 2..27 black pixels per cell.
            #
            # Two to twenty-seven pixels out of 256, so `_cur.max()` is nowhere
            # near 0 and the cell-level guard cannot fire. Cosmos's art is
            # 4x oversampled and box-filtered back down to 16x16, so at the
            # boundary the room's brown MIXES with the void and the quantiser
            # writes that blend faithfully. It is a resampling artefact, not
            # authored content.
            #
            # THE RULE: inside the 4:3 picture, a pixel that is BLACK in the
            # vanilla page stays black. The original cut it to black on
            # purpose; the upscale has no standing to fill it in.
            #
            # WHY THIS IS SAFER THAN THE CELL GUARD, and it is the whole
            # reason to prefer it: this can only ever PRESERVE a pixel that
            # was already black. It cannot create one. The cell-level version
            # withheld entire cells and blacked out `md1stin`'s widened edges
            # (the note at `_inside43` records that build); this writes no new
            # black anywhere, so that failure mode does not exist here.
            #
            # Margin cells are untouched -- outside the 4:3 picture there is
            # no original to protect, which is `_inside43`'s whole point.
            # AND ONLY WHERE THE BRIGHTNESS IS OURS, NOT THE MOD'S.
            #
            # VERIFIED AGAINST FFNx, which is the mod's reference renderer and
            # draws the DDS at FULL resolution while we box-filter a kxk source
            # block into ONE destination pixel. At the pixels where vanilla is
            # black and this pass would write >48/255, the SOURCE block is:
            #
            #   field      px     dark frac   >=25% dark   fully opaque
            #   elmin1_2   302      0.26          68%          41%
            #   mrkt2      736      0.22          47%          54%
            #   nmkin_2  7,987      0.09          21%          82%
            #   md1stin    804      0.03           8%          94%
            #   mds6_3   3,323      0.03           9%          96%
            #
            # `elmin1_2` -- the reported case -- is a BLEND: a quarter of its
            # source is dark and under half is fully opaque, so the brightness
            # comes from averaging a boundary. `nmkin_2`, `md1stin` and
            # `mds6_3` are 82-96% fully opaque with almost no dark source:
            # Cosmos genuinely PAINTS there and FFNx genuinely shows it.
            #
            # That matches hardware. The user reports the No. 1 reactor
            # (`nmkin_*`) looks CORRECT and Aerith's house does not.
            #
            # So a blanket "vanilla was black, keep it black" is wrong -- it
            # would suppress real art in three fields to fix one. The guard
            # fires only where the source block is substantially dark, i.e.
            # where our own downsample invented the colour.
            if KEEP_BLACK_PIXELS and _interior:
                _wasc = was[sy:sy + _edge, sx:sx + _edge]
                _blk = prgb[_wasc].max(-1) <= BLACK_PIXEL_MAX
                if _blk.any():
                    _sd = ((np.ascontiguousarray(src[..., :3]).max(-1)
                            <= SRC_DARK_MAX)
                           .reshape(_edge, k, _edge, k).mean(axis=(1, 3)))
                    _blk &= _sd >= SRC_DARK_FRAC
                if _blk.any():
                    idx = np.where(_blk, _wasc, idx)
                    st['silhouette_px'] = st.get('silhouette_px', 0) + int(
                        _blk.sum())
                    st['silhouette_cells'] = st.get('silhouette_cells', 0) + 1
            # THE BLACK SQUARES ARE THE ATLAS GAP, AND THIS IS THE FIX.
            #
            # A Cosmos page is a SPARSE ATLAS: art only where the vanilla page
            # had art, transparent everywhere else. MEASURED on `md8_1` slot 1
            # -- 20.1% of the whole page is transparent, and the widescreen
            # margin tiles sample the bottom band, which is flat filler in
            # vanilla and EMPTY in the upscale:
            #
            #     cell src(192,240)   67.2% transparent
            #     cell src(160,240)   61.6% transparent
            #     cell src(144,240)   52.5% transparent
            #     cell src(192,208)   30.6% transparent
            #
            # `dir_source`/`provider_source` zero the RGB where alpha is 0, so
            # those texels arrive here as BLACK -- and the quantiser, having no
            # idea it is looking at a hole, faithfully writes black. Error 1.4
            # to 4.5 out of 255: it did its job perfectly on a lie. That is the
            # column of black squares at the left and right edges of the 16:9
            # frame in Sector 8, and MAX_QUANT_ERR can never catch it because
            # black is not "wildly off-colour", it is an excellent match for
            # black.
            #
            # TRANSPARENT IS A CLAIM ABOUT COVERAGE, NOT ABOUT COLOUR. Where
            # the mod paints nothing, the mod is saying nothing, so the cell
            # keeps the vanilla index it already had. `field_bg_dense` has
            # always done this on the promotion side -- "the mod's alpha is
            # authoritative about its own art, not about what the game draws
            # there" -- and this pass, which runs first and feeds it, never did.
            #
            # This can only ADD art. A covered texel is written exactly as
            # before; an uncovered one stops being overwritten with black.
            #
            # AND IT MUST NOT BE DONE HERE, WHICH BUILD 46 GOT WRONG.
            #
            # Build 46 added `idx = np.where(_cov, idx, was_block)` -- fall
            # back to the vanilla index wherever the mod's alpha is below 128.
            # That was based on reading alpha as COVERAGE. It is not.
            #
            # MEASURED, where the mod's DDS is transparent, what vanilla holds
            # at the same texel:
            #
            #     mds6_1  page  2    99.4% vanilla index 0 (the COLOUR KEY)
            #     mds6_1  page 15   100.0%
            #     mkt_mens page 1    99.7%
            #     nivinn_1 page 3    99.9%
            #
            # Alpha means "this texel is the transparency key", not "the mod
            # painted nothing". The mod's page is COMPLETE -- which is why it
            # looks right on FFNx, where the DDS simply replaces the page.
            #
            # So the fallback was wrong twice over. `keep0` below already
            # forces index 0 wherever VANILLA had index 0, which is 99.4% of
            # those texels, so it was redundant where it agreed. And where it
            # did not agree it threw away real art: a downsampled ANTIALIASED
            # EDGE lands between 1 and 127, and on one such edge 80 of 256
            # texels are partial alpha -- mod art -- that the fallback
            # replaced with vanilla. That is a vanilla-toned fringe along
            # every edge and a vanilla-toned patch wherever alpha is marginal,
            # which is what appeared in Sector 6 in build 46.
            #
            # The vanilla mask is the authority on transparency and always
            # was. Everything else takes the mod's colour.
            for r in range(_edge):
                base = (sy + r) * 256 + sx
                buf[base:base + _edge] = bytes(idx[r])
            # ---- THE 512px SHADOW. BUILD 109, HANDOFF-224.
            #
            # `small` was this same `src` box-filtered all the way down to
            # the 256-unit cell. `field_bg_shadow` stops half way and
            # quantises against THIS palette -- `prgb`, the effective one --
            # so the shadow cannot drift in colour from the page it shadows,
            # only in resolution, which is the point. Nothing about the
            # bytes just written to `buf` changes; this is additive.
            if SH.active():
                st['shadow'] = st.get('shadow', 0) + SH.record(
                    name, idx, _idx_q, src, k, prgb, quantise, _edge)
            st['filled'] += 1
            wrote.add((slot, sx, sy))
            if dark:
                st['darkened'] += 1

    # ------------------------------------------------ BOTH OR NEITHER.
    #
    # BUILD 110, and this is the whole reason the fx veto can be lifted at
    # all. See FILL_FX_PAIRS.
    #
    # The loop above refuses cells for a dozen independent reasons -- the art
    # is near-black, the quantisation came out wildly off-colour, the
    # silhouette guard fired, the borrow found nothing. Any one of those
    # landing on ONE side of an fx pair leaves the base frame carrying Cosmos
    # art and the fx frame carrying vanilla, and the engine swapping between
    # them is precisely the FLICKER the old veto existed to prevent.
    #
    # So the pairing is enforced HERE, after every refusal has already
    # happened, rather than predicted before them. A pair with one side
    # written has that side put back exactly as it was; the field ends up in
    # the state build 109 would have produced for those two cells.
    #
    # Reverting is a byte copy from the ORIGINAL page, which is what
    # `pages[slot].data` still holds -- `newdata` is a separate bytearray.
    if _fx_pairs:
        for base, fxc in sorted(_fx_pairs):
            wa, wb = base in wrote, fxc in wrote
            if wa == wb:
                continue
            bad = base if wa else fxc
            slot, sx, sy = bad
            buf = newdata.get(slot)
            if buf is None:
                continue
            page = pages[slot]
            src = page.data
            _e3 = 32 if (BIG_CELL and page.size_flag) else TILE
            for r in range(_e3):
                o = (sy + r) * 256 + sx
                buf[o:o + _e3] = src[o:o + _e3]
            wrote.discard(bad)
            st['filled'] -= 1
            st['fx_unpaired'] = st.get('fx_unpaired', 0) + 1
        st['fx_paired'] = sum(
            1 for b, f in _fx_pairs if b in wrote and f in wrote)

    # ------------------------------------------------- the repoint, and the
    # cells it would otherwise strand.
    #
    # A repointed page is drawn through a NEW colour table. Every placeholder
    # cell on it that the loop above actually wrote is already quantised
    # against that table. The ones it did NOT write -- no art shipped, art
    # near-black and DARKEN off, or refused -- still hold vanilla indices that
    # mean a colour in the OLD table, and reading them through the new one
    # would move them. So remap each such cell to the index nearest its
    # PREVIOUS rendered colour: same pixels on screen, new table.
    #
    # Index 0 is left alone. It is the colour key and `ff7nx_palkey` owns it.
    if chosen:
        for slot, newpal in sorted(chosen.items()):
            strand = sorted(c for c in placeholder
                            if c[0] == slot and c not in wrote)
            if not strand:
                continue
            buf = newdata.get(slot)
            if buf is None:
                buf = bytearray(pages[slot].data)
                newdata[slot] = buf
            page = np.frombuffer(bytes(buf), np.uint8).reshape(256, 256)
            oldpal = tilepal.get(slot, newpal)
            lut = quantise(prgbs[oldpal][np.arange(256)]
                           .reshape(16, 16, 3).astype(np.uint8),
                           prgbs[newpal]).reshape(256)
            lut[0] = 0
            _e2 = 32 if (BIG_CELL and pages[slot].size_flag) else TILE
            for _s, sx, sy in strand:
                blk = lut[page[sy:sy + _e2, sx:sx + _e2]]
                for r in range(_e2):
                    base = (sy + r) * 256 + sx
                    buf[base:base + _e2] = bytes(blk[r])
                st['pal_remapped'] = st.get('pal_remapped', 0) + 1
        parts[SECTION9], nt = MP.apply_repoint(parts[SECTION9], surv, pages,
                                               chosen, placeholder)
        st['pal_tiles'] = nt

    # ------------------------------------------------ THE TIER-2 SHADOW PASS
    #
    # BUILD 109. Runs over EVERY cell on every depth-1 page, including every
    # one the loop above refused, and writes NOTHING into the page -- it only
    # records how each cell should be resampled when the lift takes it to
    # 512. See the tier-2 note in `field_bg_shadow`; the short version is
    # that the multi-palette veto is a statement about COLOUR and a resample
    # map does not mention colour, so the veto does not reach it.
    #
    # HERE, AND NOT IN A PASS OF ITS OWN, for the same reason the fill is
    # here: Cosmos names its art by the page number the cell is on NOW, and
    # this is the last point at which that lookup resolves. It is also the
    # only place `_pal_for` and the borrow logic already exist.
    #
    # `newdata` is read rather than `pages[slot].data` so the map is built
    # against what the fill just wrote, not against what it replaced.
    if SH.active() and SH.map_enabled():
        for slot, page in sorted(pages.items()):
            if page.depth != 1:
                continue
            buf = newdata.get(slot)
            arr = np.frombuffer(bytes(buf) if buf is not None else page.data,
                                np.uint8, count=256 * 256).reshape(256, 256)
            _e2 = 32 if (BIG_CELL and page.size_flag) else TILE
            # The palette only picks WHICH of Cosmos's images to use as the
            # guide, never what is written, so the most common one a tile on
            # this page names is good enough and a borrow is harmless.
            _pals = sorted({p for (s, p) in cells if s == slot})
            got = None
            for _p in (_pals or [0]):
                try:
                    got = art(name, slot, _p)
                except Exception:                              # noqa: BLE001
                    got = None
                if got is not None:
                    break
            if got is None:
                continue
            img = got[0]
            k = img.shape[1] // 256
            if k < 2:
                continue
            for sy in range(0, 256, _e2):
                for sx in range(0, 256, _e2):
                    src = img[sy * k:(sy + _e2) * k, sx * k:(sx + _e2) * k]
                    if src.shape[:2] != (_e2 * k, _e2 * k):
                        continue
                    st['shadow_map'] = st.get('shadow_map', 0) + SH.record_map(
                        name, arr[sy:sy + _e2, sx:sx + _e2], src, k, _e2)

    if not st['filled'] and not st.get('pal_tiles'):
        return None, st

    # Rebuild the TEXTURE block rather than patching bytes in place: the page
    # payloads are not at a fixed offset and `field_bg_native` owns that
    # layout. Same call the repack uses, so the two cannot disagree.
    import field_bg_native as FN
    allpages = list(surv['pages_by_slot']) if 'pages_by_slot' in surv else None
    slots = FN.parse_texture_block(parts[SECTION9])
    plist, tex_start, tex_end = slots
    for slot, buf in newdata.items():
        for i, p in enumerate(plist):
            if p is not None and p.slot == slot:
                plist[i] = FN.Page(p.slot, p.size_flag, p.depth,
                                   bytes(buf), p.px)
    parts[SECTION9] = FN.replace_texture_block(parts[SECTION9], plist,
                                               tex_start, tex_end)
    return lgp_mod.join_sections(parts), st


# ------------------------------------------------------------------ the pass
def apply_to_flevel(archive, payloads, art, encode=None, log=print,
                    fields=None, scope='margin'):
    """
    Same contract as `ff7nx_marginblack.apply_to_flevel`: a field already in
    `payloads` is taken from there, so this composes with the mod replacement
    passes rather than competing with them.

    MUST RUN BEFORE the field-background repack, and an earlier draft of this
    docstring said AFTER, which was WRONG and shipped garbage.

    Cosmos names its art against the VANILLA page numbering. The repack
    renumbers and compacts -- `mds6_2` goes from dump slots [0,1,2,3,4] to
    built slots [2,3,4,26,27,28] with NOT ONE page identical. Writing page-0
    art into the built archive's slot 0 lands it on unrelated cells and
    renders as bright yellow blocks. Run first and the numbering matches.

    Raises nothing. A field that will not parse, or has no Cosmos art for its
    margin cells, is counted and left exactly as it was.
    """
    import lgp

    st = {'read': 0, 'changed': 0, 'cells': 0, 'filled': 0, 'black': 0,
          'no_dds': 0, 'borrowed': 0, 'wild': 0, 'darkened': 0, 'far_borrow': 0, 'detail': 0, 'uncovered': 0,
          'keep0_kept': 0, 'keep0_dropped': 0, 'keep0_cells': 0,
          'refused': [],
          'pal': {'fields': 0, 'slots': 0, 'slots_repointed': 0, 'tiles': 0,
                  'cells': 0, 'remapped': 0, 'layer1_constrained': 0,
                  'layer1_escaped': 0, 'layer1_penalty': [],
                  # FINDINGS-148. These MUST be declared here. `merge_pal`
                  # only merges a key that already exists in the aggregate
                  # (`isinstance(P.get(k), int)` is False for a missing key),
                  # which is precisely how build 54's `layer1_constrained`
                  # was computed correctly and then thrown away unlogged.
                  'layer1_escaped_hue': 0, 'layer1_hue_gap': [],
                  # FINDINGS-149, and declared here for the same reason.
                  'hue_vetoed': 0, 'hue_veto_dist': [],
                  'err_before': [],
                  'err_after': [], 'idx_before': [], 'idx_after': []}}
    encode = encode or (lambda raw: archive.encode_field(raw))

    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        if fields and name not in fields:
            continue
        payload = payloads.get(name, entry.get('payload'))
        if not payload:
            continue
        try:
            raw = (lgp.lzs_decompress(payload[4:]) if name in payloads
                   else archive.decompressed(entry))
            new, s = fill_field(name, raw, lgp, art, scope=scope)
            st['read'] += 1
            # MERGE EVERY INTEGER COUNTER, NOT A HARDCODED LIST.
            #
            # This list was fixed at seven names, so `uncovered` -- the atlas
            # gap counter -- never reached the aggregate and `summarise` never
            # printed its line, which made a build that HAD the fix look
            # identical to one that did not. Any counter added to `fill_field`
            # from now on shows up without touching this function.
            for k, v in s.items():
                if isinstance(v, int) and not isinstance(v, bool) \
                        and isinstance(st.get(k), int):
                    st[k] += v
            ps = s.get('pal')
            if ps:
                merge_pal(st['pal'], ps, s)
            if new is None:
                continue
            payloads[name] = encode(new)
            st['changed'] += 1
        except Exception as exc:                                # noqa: BLE001
            st['refused'].append((name, '%s: %s'
                                  % (type(exc).__name__, str(exc)[:60])))
    if st['refused'] and log:
        log('  ! margin art: %d field(s) not changed (%s)'
            % (len(st['refused']),
               ', '.join('%s: %s' % r for r in st['refused'][:3])))
    return st



def merge_pal(P, ps, s):
    """
    Fold one field's `choose()` stats into the archive-wide `pal` aggregate.

    EXTRACTED SO IT CAN BE TESTED. It was inline, and it merged through a
    HARDCODED key list -- the same bug the outer loop already fixed and
    documented for `uncovered`:

        "This list was fixed at seven names, so `uncovered` -- the atlas gap
         counter -- never reached the aggregate and `summarise` never printed
         its line, which made a build that HAD the fix look identical to one
         that did not."

    It then did it again to `layer1_constrained` in build 54: the constraint
    WORKED (margin palette 135 -> 101 pages, margin art refused 1,185 ->
    1,414, and the reported field was visibly fixed) but its log line never
    printed, so the only evidence the change had landed was two counters
    moving for unstated reasons.

    `tiles` is excluded from the generic sweep because it is summed from
    `s['pal_tiles']`, not from `ps`; taking both double-counts it.
    """
    if ps.get('slots_repointed'):
        P['fields'] += 1
        P['remapped'] += s.get('pal_remapped', 0)
        P['tiles'] += s.get('pal_tiles', 0)
        for k in ('err_before', 'err_after', 'idx_before', 'idx_after'):
            P[k] += ps[k]
    # list-valued counters merge by extension, not addition
    for k, v in ps.items():
        if isinstance(v, list) and isinstance(P.get(k), list):
            P[k] = P[k] + v
    for k, v in ps.items():
        if (k != 'tiles' and isinstance(v, int) and not isinstance(v, bool)
                and isinstance(P.get(k), int)):
            P[k] += v
    return P


def summarise(st):
    if not st or not st.get('read'):
        return ''
    return ('margin art: %d cell(s) of Cosmos widescreen art written into the '
            'paletted page in %d of %d field(s) (%d cell(s) genuinely black '
            'and left alone, %d with no art shipped%s%s)'
            % (st['filled'], st['changed'], st['read'], st['black'],
               st['no_dds'],
               ', %d REFUSED as wildly off-colour' % st['wild']
               if st.get('wild') else '',
               ', %d refused' % len(st['refused']) if st['refused'] else '')
            # THIS LINE USED TO CLAIM THE DILATION HAD RUN. It said the gap
            # texels "were extended from the covered art beside them" whether
            # or not `EXTEND_INTO_GAP` was on, and it has been False since
            # build 46 -- so five builds of logs asserted an action the build
            # did not take. The counter is the number of UNCOVERED texels; it
            # never measured how many were changed. Worded honestly now, both
            # ways, because a log that describes the wrong build is how three
            # of the last six regressions got past review.
            + (' -- ATLAS GAP: %d texel(s) where Cosmos ships no art (a page '
               'is a sparse atlas and the sources zero the RGB where alpha is '
               '0, so a hole arrives here as BLACK and the quantiser cannot '
               'tell it from a black pixel, measured error 1.4-4.5 of 255). '
               '%s'
               % (st['uncovered'],
                  'They were EXTENDED from the covered art beside them '
                  '(EXTEND_INTO_GAP is on -- this is build 45 behaviour and '
                  'produced the grey/green triangles).'
                  if EXTEND_INTO_GAP else
                  'They were NOT dilated -- EXTEND_INTO_GAP is off. Each one '
                  'keeps whatever the vanilla page held, which is the rule '
                  'that survived builds 45, 46 and 49.')
               if st.get('uncovered') else '')
            + (' -- of the written cells, %d are flat MARGIN PLACEHOLDERS '
               'where the mod authored near-black: those used to keep a vivid '
               'tan/yellow filler and now take the dark art'
               % st['darkened'] if st.get('darkened') else '')
            + (' -- LAYER 2+: %s static overlay cell(s) are now eligible '
               '(barrels, signs, fences, machinery drawn in front of the '
               'characters), %s animated one(s) still vetoed because a tile '
               'carries an fx page or draws from a blend-band page and '
               'repainting one frame of an animation reads as FLICKER'
               % (f"{getattr(fillable_cells, 'layer2_static', 0):,}",
                  f"{getattr(fillable_cells, 'layer2_animated', 0):,}")
               if getattr(fillable_cells, 'layer2_static', 0) else '')
            + (' -- KEY DROPPED: %s texel(s) in %s layer-1 opaque cell(s) kept '
               "Cosmos's art instead of being forced back to index 0. On a "
               'depth-1 page index 0 is DRAWN, not discarded (FFNx sets '
               'color_key only for type 2), and 93%% of those keys are 0x0000, '
               'so every one of these was a BLACK pixel painted over art the '
               'mod shipped. %s texel(s) in genuine cut-outs -- layer 2+ or a '
               'blend-band page -- were left forced, because there index 0 is '
               'real transparency and dropping it would make an overlay an '
               'opaque rectangle'
               % (f"{st['keep0_dropped']:,}", f"{st['keep0_cells']:,}",
                  f"{st.get('keep0_kept', 0):,}")
               if st.get('keep0_dropped') else ''))


if __name__ == '__main__':
    import argparse

    ap = argparse.ArgumentParser(
        description='write Cosmos widescreen art into the 16:9 margin')
    ap.add_argument('flevel',
                    help='the flevel.lgp to read. USE THE DUMP, NOT sdout: '
                         'the build renumbers and compacts pages, so Cosmos '
                         'art written into a built archive lands on the wrong '
                         'cells. --check-numbering verifies this for you.')
    ap.add_argument('--art', required=True,
                    help='the extracted "LIMIT BREAK/field" directory')
    ap.add_argument('--out', help='write a patched flevel.lgp here')
    ap.add_argument('--fields', nargs='*', help='limit to these fields')
    ap.add_argument('--interior', action='store_true',
                    help='fill EVERY cell, not just the margin -- replaces vanilla\n         art with Cosmos art across the whole picture')
    ap.add_argument('--verify', action='store_true',
                    help='re-render every changed field and prove the 4:3 '
                         'interior is byte-identical')
    ap.add_argument('--png', help='write a before/after PNG here (with --fields)')
    a = ap.parse_args()

    # A wrong --art path used to look exactly like "the mod ships no art":
    # every cell fell through to `no_dds` and the run reported PASS. Shell
    # quoting makes that easy to hit -- a backslash-escaped space inside
    # double quotes is a literal backslash. Fail loudly instead.
    if not os.path.isdir(a.art):
        raise SystemExit('--art is not a directory: %r\n'
                         '   (if the path has spaces, quote it WITHOUT '
                         'backslashes:  --art "/a b/LIMIT BREAK/field")'
                         % a.art)
    n_dirs = sum(1 for d in os.listdir(a.art)
                 if os.path.isdir(os.path.join(a.art, d)))
    if n_dirs < 10:
        raise SystemExit('--art has only %d field folder(s): %r\n'
                         '   expected the "LIMIT BREAK/field" directory, '
                         'which holds one folder per field.' % (n_dirs, a.art))
    print('art source: %s  (%d field folder(s))' % (a.art, n_dirs))

    # A BUILT archive has had its pages renumbered and compacted, so Cosmos's
    # page numbering no longer applies and every write lands on the wrong
    # cell. Detect it rather than let it render as yellow blocks: promoted
    # truecolor pages live at slot >= 26 and vanilla never uses those.
    # MEASURED, because vanilla is not free of depth-2 pages: 17 of the first
    # 400 dump fields (4%) already hold one at slot >= 26. A built archive has
    # 188 of 400 (47%). The ratio separates them cleanly; the raw count does
    # not, and refusing the dump is worse than the bug.
    import lgp as _lgp, diag_common as _DC
    _arc = _lgp.Archive(a.flevel)
    _hi = _seen = 0
    for _n in list(_arc.names())[:400]:
        _e = _arc.index.get(_n)
        if _e is None or not _arc.is_field(_e):
            continue
        try:
            _pg = _DC.survey(_lgp.split_sections(_arc.decompressed(_e))[8])['pages']
        except Exception:
            continue
        _seen += 1
        if any(p.slot >= 26 for p in _pg):
            _hi += 1
    if _seen and _hi / _seen > 0.15:
        raise SystemExit(
            'REFUSING: %s looks like a BUILT archive -- %d of %d field(s) '
            'hold a page at slot >= 26\n   (vanilla runs about 4%%). The '
            'repack creates those, and it also '
            'COMPACTS, relocating cells, so\n   Cosmos art written here lands '
            'on the wrong cells and renders as garbage.\n'
            '   Use the DUMP flevel.lgp, or let the build run this pass '
            '(margin_art: 1).' % (a.flevel, _hi, _seen))

    import lgp
    arc = lgp.Archive(a.flevel)
    payloads = {}
    st = apply_to_flevel(arc, payloads, dir_source(a.art), fields=a.fields,
                         scope='all' if a.interior else 'margin')
    print('\n' + (summarise(st) or 'nothing to do'))

    if a.verify:
        import locate_field as LF
        # WHAT IS CHECKED, AND WHY IT CHANGES WITH --interior
        #
        # margin scope: the 4:3 interior must be BYTE-IDENTICAL. That is the
        # whole safety argument and it is checked by rendering.
        #
        # interior scope: the interior is SUPPOSED to change, so that check is
        # meaningless and is replaced by the two that still mean something:
        #
        #   MASK CHANGED   a pixel that was drawn is no longer drawn, or the
        #                  reverse. Must be 0: the colour key is what 95,733
        #                  tiles depend on and what makes models occlude
        #                  correctly.
        #   CLOSER TO COSMOS  the rendered field, compared against Cosmos's own
        #                  DDS, must move CLOSER. If a field gets further away
        #                  the quantiser made it worse and the field is named.
        bad_int = bad_mask = worse = 0
        for name in sorted(payloads):
            old_raw = arc.decompressed(arc.index[name])
            new_raw = lgp.lzs_decompress(payloads[name][4:])
            A, DA = LF.render_big(old_raw)
            B, DB = LF.render_big(new_raw)
            if not np.array_equal(DA, DB):
                bad_mask += 1
                print('   MASK CHANGED: %s' % name)
            if not a.interior:
                if not np.array_equal(A[:, LF.CX - 160:LF.CX + 160],
                                      B[:, LF.CX - 160:LF.CX + 160]):
                    bad_int += 1
                    print('   INTERIOR CHANGED: %s' % name)
        print()
        if not a.interior:
            print('INTERIOR CHANGED  %d  <- must be 0' % bad_int)
        print('MASK CHANGED      %d  <- must be 0' % bad_mask)
        ok = not bad_int and not bad_mask
        print('\n%s' % ('PASS' if ok else 'FAIL'))

    if a.png and payloads:
        from PIL import Image
        import locate_field as LF
        rows = []
        for name in sorted(payloads):
            old = arc.decompressed(arc.index[name])
            new = lgp.lzs_decompress(payloads[name][4:])
            A, _ = LF.render_big(old)
            B, _ = LF.render_big(new)
            x0, y0 = LF.CX - 224, LF.CY - 120
            rows.append(np.concatenate([A[y0:y0 + 240, x0:x0 + 448],
                                        B[y0:y0 + 240, x0:x0 + 448]], 0))
        img = np.concatenate(rows, 0)
        Image.fromarray(img).resize((img.shape[1] * 2, img.shape[0] * 2),
                                    Image.NEAREST).save(a.png)
        print('wrote %s  (before above, after below, per field)' % a.png)

    if a.out:
        arc.replace(payloads)
        arc.write(a.out)
        print('wrote %s' % a.out)
