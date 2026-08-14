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


class Stats:
    __slots__ = ('hue_kept_art',
                 'cells', 'pages', 'tiles', 'from_art', 'from_art_borrow',
                 'from_vanilla', 'keyed', 'fx_pairs', 'refused', 'pages_before',
                 'borrow_refused')

    def __init__(self):
        self.cells = self.pages = self.tiles = 0
        self.from_art = self.from_art_borrow = self.from_vanilla = 0
        self.keyed = self.fx_pairs = 0
        self.pages_before = 0
        self.borrow_refused = 0
        self.hue_kept_art = 0
        self.refused = None


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
    slot, sx, sy, pal = k
    p = pages[slot]
    if p.depth == 2:
        return bool((arrays[slot][sy:sy + TILE, sx:sx + TILE] == 0).any())
    return bool((arrays[slot][sy:sy + TILE, sx:sx + TILE] == 0).any())


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


def black_fraction(arrays, pal565, k):
    """How much of this cell is OPAQUE BLACK on its paletted page.

    Index 0 is the colour key and is excluded: it is not black, it is the
    key. Any other index whose palette colour is 0x0000 is real, opaque,
    dead black.
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
    idx = arrays[slot][sy:sy + TILE, sx:sx + TILE]
    col = pal565[pal][idx]
    return float(((col == 0) & (idx != 0)).mean())


def _up(a, s):
    """Nearest-neighbour block upscale by an integer factor. s == 1 is free."""
    if s == 1:
        return a
    return np.repeat(np.repeat(a, s, axis=0), s, axis=1)


def source_cell(k, rec, pages, arrays, pal565, art_for, pals_for, st,
                scale=1, origin=None, hue_broken_cell=False):
    """A (16*scale, 16*scale) uint16 R5G6B5 cell, from the mod's art.

    `scale` is `page_px // 256`. AT 256 IT IS 1 AND NOTHING BELOW CHANGES.

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
        # cell is (TILE*scale)^2 and needs no upscale.
        t = TILE * scale
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
                arrays[slot][sy:sy + TILE, sx:sx + TILE]) <= BORROW_MAX_DIST:
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
    idx = arrays[slot][sy:sy + TILE, sx:sx + TILE]
    zero = _up(idx == 0, scale)
    if art is not None:
        buf = np.frombuffer(art.buf, np.uint16).reshape(art.px, art.px)
        s = art.px // 256
        # Take every texel the destination cell can hold. `step` is only >1
        # when the art is BIGGER than the page -- e.g. 512px art into a 256px
        # page -- and even then PageArt has already box-filtered the DDS down
        # to `art.px`, so the stride is a last resort rather than the filter.
        step = max(1, s // scale)
        t = TILE * scale
        out = buf[_asy * s:_asy * s + t * step:step,
                  _asx * s:_asx * s + t * step:step].copy()
        if out.shape != (t, t):                       # art smaller than asked
            out = _up(buf[sy * s:(sy + TILE) * s, sx * s:(sx + TILE) * s],
                      scale // max(1, s)).copy()
        pal_ref = _up(pal565[pal][idx], scale)
        if src_pal != pal and not hue_broken_cell and not KEEP_ART_ON_BORROW:
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
        if tm.shape == out.shape and tm.any():
            out[tm] = pal_ref[tm]
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
        out[zero] = FN.EMPTY
    # On layer 1 index 0 is NOT a cut-out -- it is drawn, and its colour
    # matters. Proved on hardware: setting entry 0 to black removed the
    # Sector 6 yellow and put black speckles across Wall Market, which cannot
    # happen if the index is discarded. So the colour above is kept as it is,
    # lifted off 0x0000 by the line before this one so it cannot be mistaken
    # for a key.
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

PROMOTE_FX_BASE = False

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

MAX_TRUECOLOR_PAGES = 3
_MAX_TOTAL_PAGES_DEFAULT = 12


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
    # See PROMOTE_FX_BASE. `fx_partners` is the side that must stay paletted
    # (an fx frame is drawn through the additive/average band, which has no
    # depth-2 equivalent that renders on this port). `fx_cells` is what the
    # candidate filter vetoes: both sides when the flag is off, partners only
    # when it is on. A cell that is BOTH a base and someone else's partner
    # stays vetoed either way -- it is in `fx_partners`.
    fx_partners = set()
    for v in fx_of.values():
        fx_partners |= v
    fx_cells = fx_partners if PROMOTE_FX_BASE else (set(fx_of) | fx_partners)

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
    if MULTI_PALETTE_VETO:
        _kept = []
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
    _hc = {}
    _hb = {}
    # RESOLVED UNCONDITIONALLY. `source_cell` needs it whether or not
    # HUE_FIRST is on, and scoping it inside that branch made it a NameError
    # the moment the flag was turned off.
    try:
        import ff7nx_marginpage as _MPG
        _org = _MPG.ORIGIN.get(field) or None
    except Exception:                                          # noqa: BLE001
        _org = None
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
                if black_fraction(arrays, pal565, k) < TRUE_BLACK
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
    # COUNT THE ONES ALREADY THERE. 26 vanilla pages across 400 fields are
    # already depth-2; adding `max_tc` on top of those put 4 in one field.
    have_tc = sum(1 for p in pages.values() if p.depth == 2)
    room = max_total_pages() - len(pages)    # what the field can still afford
    cap = max(0, min(max_tc - have_tc, len(free_slots), room))
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
    seats = free_slots[:cap]
    chosen = []
    occupancy = {}
    fx_slot_of = {}
    _placed_at = {}
    _grid_order = [(i % GRID, i // GRID) for i in range(PER_PAGE)]

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
    def _col_free(cx, cy, need, avoid=()):
        got = []
        for sl in seats:
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

    def _next_flat():
        while _cursor[0] < cap * PER_PAGE:
            pg, idx = divmod(_cursor[0], PER_PAGE)
            _cursor[0] += 1
            cy, cx = divmod(idx, GRID)
            sl = free_slots[pg]
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
        partners = [fk for fk in (fx_of.get(k) or ())
                    if fk in keys and fk[0] in pages]
        if partners and not PROMOTE_FX_BASE:
            continue
        done = [fk for fk in partners if fk in fx_slot_of]
        todo = [fk for fk in partners if fk not in fx_slot_of]
        if not partners and not PRESERVE_CELL_COORDS:
            spot = _next_flat()
            if spot is None:
                continue
            sl, cx, cy = spot
            occupancy[sl].add((cx, cy))
            chosen.append((k, sl, cx, cy))
            continue
        if PRESERVE_CELL_COORDS:
            spots = [((k[1] // TILE) % GRID, (k[2] // TILE) % GRID)]
        elif done:
            # A partner already seated fixes the column for the whole group.
            spots = [_placed_at[done[0]]]
        else:
            spots = _grid_order
        for cx, cy in spots:
            if done and _placed_at[done[0]] != (cx, cy):
                continue
            avoid = {fx_slot_of[fk] for fk in done}
            got = _col_free(cx, cy, 1 + len(todo), avoid)
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
        buf = dest.get(slot)
        if buf is None:
            buf = dest[slot] = np.full((side, side), FN.NEAR_BLACK, np.uint16)
        try:
            cell = source_cell(k, keys[k], pages, arrays, pal565,
                               art_for, pals_for, st, scale, _org,
                               _hb.get(k, 0.0) > HUE_BROKEN_DIST)
        except Exception:                                      # noqa: BLE001
            continue
        t = TILE * scale
        buf[cy * t:(cy + 1) * t, cx * t:(cx + 1) * t] = cell
        dx, dy = cx * TILE, cy * TILE
        for off in keys[k]['tiles']:
            out[off + T_TEXID] = slot
            out[off + T_SRC_X] = dx & 0xFF
            out[off + T_SRC_Y] = dy & 0xFF
            struct.pack_into('<II', out, off + T_SRC_X_BIG,
                             cx * STEP, cy * STEP)
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
        plist[slot] = FN.Page(slot, 0, 2, buf.tobytes(), side)
    st.pages = len(dest)
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
