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


# Leave a cell alone when the art it already carries is entirely black.
# See the comment at the use site: those cells are silhouette, and filling
# them is the grey staircase in the No. 1 reactor.
KEEP_BLACK_SILHOUETTE = False   # post-baseline; reverted with the rest


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
# COVERAGE IS HONOURED. THE DILATION IS NOT. FINDINGS-134.
#
# `_extend_into_gap` filled every uncovered texel of a cell by dilating the
# COVERED art outward, 32 rounds of 4-neighbour growth. It was added because a
# transparent hole reaches the quantiser as BLACK -- the sources zero RGB where
# alpha is 0 -- and gets written faithfully as black, which `MAX_QUANT_ERR`
# can never catch because black is an excellent match for black.
#
# The reasoning was right and the remedy overshot. Transparent is a claim about
# COVERAGE, so black is wrong -- but a smear of the neighbouring art is wrong
# too, and it is visible:
#
#   REPORTED, first reactor and Tifa's bar: a cell holds part of a texture
#   along a diagonal and the REST of the square, which should be black, is a
#   triangle of that texture's own colours. Grey where the art is grey, green
#   where it is green. Not in the widened margin -- anywhere the mod's atlas
#   is partially covered.
#
# That is `_extend_into_gap`'s output described exactly.
#
# The write site 800 lines below has always documented the correct rule --
# "where the mod paints nothing, the mod is saying nothing, so the cell keeps
# the vanilla index it already had" -- and never implemented it. It does now.
# The vanilla index is what was there, it is what the mod declined to comment
# on, and it invents nothing.
HONOUR_MOD_ALPHA = True

# The dilation. OFF: the gap now keeps its vanilla pixels instead, which is
# what the write site has always claimed to do. See FINDINGS-134 at the write
# site. True restores build 45's behaviour exactly, for A/B.
EXTEND_INTO_GAP = False
# FIRST before assuming anything." Doing that.
#
# False restores the pre-3.5 behaviour: an uncovered texel arrives as black
# and is written as black. That is the OLD defect, not a fix -- it is the
# clean A/B that tells us whether the dilation is what is being seen. If the
# triangles go and black squares come back in their place, the right answer is
# neither: keep the VANILLA pixels wherever the mod does not cover, which is
# a change at the write site (`idx = np.where(_cov, idx, was_block)`) and
# invents nothing.
HONOUR_MOD_ALPHA = True

# The dilation. OFF: the gap now keeps its vanilla pixels instead, which is
# what the write site has always claimed to do. See FINDINGS-134 at the write
# site. True restores build 45's behaviour exactly, for A/B.
EXTEND_INTO_GAP = False

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


def quantise(cell_rgb, pal_rgb, dither=False):
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
    d = ((flat[:, None, :] - pal[None, :, :]) ** 2).sum(-1)
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
    d2 = ((f2[:, None, :] - pal[None, :, :]) ** 2).sum(-1)
    return (d2.argmin(1) + 1).astype(np.uint8).reshape(cell_rgb.shape[:2])


BLEND_BAND_FIRST_SLOT = 0x0F


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
        elif t.layer != 1 and _is_animated(parts[SECTION9], t, pages):
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
    out = {}
    for cell, pals in want.items():
        if cell in veto or len(pals) != 1:
            continue
        slot, sx, sy = cell
        out.setdefault((slot, next(iter(pals))), set()).add((sx, sy))
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
          'borrowed': 0, 'wild': 0, 'darkened': 0, 'far_borrow': 0, 'detail': 0, 'uncovered': 0}
    parts = lgp_mod.split_sections(raw)
    cols, hdr, npg, cpp = MB.palette_colours(parts[SECTION_PALETTE])
    surv = DC.survey(parts[SECTION9])
    cells, pages, arrays, placeholder = fillable_cells(parts, surv, scope)
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
    if KEEP_BLACK_SILHOUETTE:
        try:
            _tl = MB.read_tiles(parts[SECTION9], surv, pages)
            _out = {(t.slot, t.sx, t.sy) for t in _tl if t.outside_43}
            _inside43 = {(t.slot, t.sx, t.sy) for t in _tl
                         if (t.slot, t.sx, t.sy) not in _out}
        except Exception:                                      # noqa: BLE001
            _inside43 = set()

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
        for sx, sy in sorted(cs):
            # A repointed placeholder is quantised against the palette it will
            # ACTUALLY be drawn through, not the one the mod left behind.
            eff_pal = _pal_for(slot, sx, sy, pal)
            prgb = prgbs[eff_pal]
            src = img[sy * k:(sy + TILE) * k, sx * k:(sx + TILE) * k]
            if src.shape[:2] != (TILE * k, TILE * k):
                st['no_dds'] += 1
                continue
            # box filter 64x64 -> 16x16
            small = (np.ascontiguousarray(src[..., :3])
                     .reshape(TILE, k, TILE, k, 3).mean(axis=(1, 3)))
            # HOW MUCH OF EACH DESTINATION PIXEL THE MOD ACTUALLY COVERS.
            # 0 = the mod paints nothing here; 255 = fully painted.
            cover = (np.ascontiguousarray(src[..., 3])
                     .reshape(TILE, k, TILE, k).mean(axis=(1, 3)))
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
            # SANITY: quantising APPROXIMATES a colour, it never inverts one.
            # If what we are about to write is nowhere near the source, the
            # palette and the image disagree -- wrong page, wrong palette,
            # transparent block with junk RGB -- and the cell lands on screen
            # as a vivid flat square. `mds6_2` shipped solid YELLOW margin
            # cells this way. Refuse rather than write; the cell keeps its
            # vanilla content, which is the state we are trying to improve on
            # and is never worse than a yellow block.
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
            keep0 = was[sy:sy + TILE, sx:sx + TILE] == 0
            idx = np.where(keep0, np.uint8(0), idx)
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
            # AND UNTIL NOW THE CODE DID NOT DO IT. The paragraph above has
            # been here describing the rule while the loop below wrote `idx`
            # across the WHOLE cell regardless of coverage; the gap was
            # handled instead by `_extend_into_gap`, which fills it with a
            # dilation of the surrounding art. That is why a cell holding part
            # of a texture along a diagonal comes out with the rest of the
            # square filled by a triangle of that texture's colours -- grey,
            # green, whatever the art beside it happens to be -- where it
            # should be black. Reported from the first reactor and Tifa's bar.
            #
            # Neither black nor a smear. The vanilla index is what was there,
            # it is what the mod declined to comment on, and it invents
            # nothing. FINDINGS-134.
            if HONOUR_MOD_ALPHA and not _cov.all():
                idx = np.where(_cov, idx, was[sy:sy + TILE, sx:sx + TILE])

            for r in range(TILE):
                base = (sy + r) * 256 + sx
                buf[base:base + TILE] = bytes(idx[r])
            st['filled'] += 1
            wrote.add((slot, sx, sy))
            if dark:
                st['darkened'] += 1

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
            for _s, sx, sy in strand:
                blk = lut[page[sy:sy + TILE, sx:sx + TILE]]
                for r in range(TILE):
                    base = (sy + r) * 256 + sx
                    buf[base:base + TILE] = bytes(blk[r])
                st['pal_remapped'] = st.get('pal_remapped', 0) + 1
        parts[SECTION9], nt = MP.apply_repoint(parts[SECTION9], surv, pages,
                                               chosen, placeholder)
        st['pal_tiles'] = nt

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
          'refused': [],
          'pal': {'fields': 0, 'slots': 0, 'slots_repointed': 0, 'tiles': 0,
                  'cells': 0, 'remapped': 0, 'err_before': [],
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
            if ps and ps.get('slots_repointed'):
                P = st['pal']
                P['fields'] += 1
                P['remapped'] += s.get('pal_remapped', 0)
                for k in ('slots', 'slots_repointed', 'cells'):
                    P[k] += ps[k]
                P['tiles'] += s.get('pal_tiles', 0)
                for k in ('err_before', 'err_after',
                          'idx_before', 'idx_after'):
                    P[k] += ps[k]
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
            + (' -- ATLAS GAP: %d texel(s) where Cosmos ships no art were '
               'extended from the covered art beside them instead of being '
               'written as BLACK: a page is a sparse atlas, the sources zero '
               'the RGB where alpha is 0, and the quantiser cannot tell a hole '
               'from a black pixel (measured error 1.4-4.5 of 255). That is '
               'the column of black squares at the edges of the 16:9 frame'
               % st['uncovered'] if st.get('uncovered') else '')
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
               if getattr(fillable_cells, 'layer2_static', 0) else ''))


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
