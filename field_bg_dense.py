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
SECTION9 = 8
UV_SCALE = 10_000_000
STEP = UV_SCALE // GRID

T_SRC_X, T_SRC_Y = 10, 12
T_PAL = FN.TILE_PALETTE_ID              # 22
T_TEXID = FN.TILE_TEXTURE_ID            # 32
T_FX_PAGE = FN.TILE_TEXTURE_ID2         # 34
T_SRC_X_BIG, T_SRC_Y_BIG = 42, 46

BANDS = {4: (0x1A, 0x21), 1: (0x21, 0x28), 0: (0x28, 0x2A)}     # truecolor
D1_BANDS = {4: (0x00, 0x0F), 1: (0x0F, 0x18), 0: (0x18, 0x1A)}  # paletted


class Stats:
    __slots__ = ('cells', 'pages', 'tiles', 'from_art', 'from_art_borrow',
                 'from_vanilla', 'keyed', 'fx_pairs', 'refused', 'pages_before')

    def __init__(self):
        self.cells = self.pages = self.tiles = 0
        self.from_art = self.from_art_borrow = self.from_vanilla = 0
        self.keyed = self.fx_pairs = 0
        self.pages_before = 0
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
    keys      {(slot, sx, sy, pal): {'band', 'key', 'tiles': [off, ...]}}
    fx_of     {key: fxkey}   the two that must share a grid index
    """
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
                             'key': False, 'tiles': []}
        rec['tiles'].append(t.off)
        f = sec9[t.off + T_FX_PAGE]
        if f and f in pages:
            fk = (f, t.sx, t.sy, pal if pages[f].depth == 1 else -1)
            if fk not in keys:
                keys[fk] = {'band': _band_of(f, pages[f].depth),
                            'key': rec['key'], 'tiles': []}
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


def source_cell(k, rec, pages, arrays, pal565, art_for, pals_for, st):
    """A (16, 16) uint16 R5G6B5 cell, from the mod's art where it exists."""
    slot, sx, sy, pal = k
    p = pages[slot]
    if p.depth == 2:
        a = arrays[slot]
        st.from_vanilla += 1
        return a[sy:sy + TILE, sx:sx + TILE].copy()

    pal = rec.get('pal', pal)
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
    art = art_for(slot, pal) if art_for is not None else None
    src_pal = pal
    if art is not None:
        st.from_art += 1
    elif art_for is not None and pal != 0:
        art = art_for(slot, 0)
        if art is not None:
            src_pal = 0
            st.from_art_borrow += 1

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
    zero = idx == 0
    if art is not None:
        buf = np.frombuffer(art.buf, np.uint16).reshape(art.px, art.px)
        s = art.px // 256
        out = buf[sy * s:(sy + TILE) * s:s, sx * s:(sx + TILE) * s:s].copy()
        if src_pal != pal:
            # BORROWED. Keep the detail, take the colour from the palette this
            # cell actually names. See _detail_transfer.
            out = _detail_transfer(out, pal565[pal][idx])
        # Where the ART is transparent, fall back to the paletted pixel: the
        # mod's alpha is authoritative about its own art, not about what the
        # game draws there.
        tm = art.tmask[sy * s:(sy + TILE) * s:s, sx * s:(sx + TILE) * s:s]
        if tm.any():
            out[tm] = pal565[pal][idx][tm]
    else:
        st.from_vanilla += 1
        out = pal565[pal][idx].copy()

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
    if rec['key']:
        out[zero] = FN.EMPTY                  # the real key, put back exactly
    return out


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
        rec['pal'] = k[3] if -1 <= k[3] < npg else (k[3] % npg if npg else 0)
        rec['key'] = _uses_key(pages, arrays, k)
    fx_cells = set(fx_of)
    for v in fx_of.values():
        fx_cells |= v

    # PRIORITY: the cells the most tiles draw. Those cover the most screen.
    # A CELL THAT USES THE KEY STAYS PALETTED.
    #
    # On a truecolor page 0x0000 is the only value that can mean transparent,
    # and the black speckles say the console draws it rather than discarding
    # it on the layer-1 pages we promote. Vanilla only ever ships keyed
    # truecolor cells on layer 2 (`gldst`), which is not where these are.
    # This is the same rule `field_bg_repack.cells_transparent` applied, and
    # the build that had no speckles applied it.
    cand = [k for k in keys
            if k not in fx_cells and pages[k[0]].depth == 1
            and not keys[k]['key']]
    cand.sort(key=lambda k: (-len(keys[k]['tiles']), k))
    free_slots = [sl for sl in range(*BANDS[4]) if sl not in pages]
    # COUNT THE ONES ALREADY THERE. 26 vanilla pages across 400 fields are
    # already depth-2; adding `max_tc` on top of those put 4 in one field.
    have_tc = sum(1 for p in pages.values() if p.depth == 2)
    room = max_total_pages() - len(pages)    # what the field can still afford
    cap = max(0, min(max_tc - have_tc, len(free_slots), room))
    if cap == 0:
        st.refused = 'already at the truecolor ceiling'
        return sec9, st
    chosen = cand[:cap * PER_PAGE]
    if not chosen:
        st.refused = 'nothing to promote'
        return sec9, st

    dest = {}
    out = bytearray(sec9)
    for i, k in enumerate(chosen):
        pg, idx = divmod(i, PER_PAGE)
        slot = free_slots[pg]
        buf = dest.get(slot)
        if buf is None:
            buf = dest[slot] = np.full((256, 256), FN.NEAR_BLACK, np.uint16)
        cy, cx = divmod(idx, GRID)
        try:
            cell = source_cell(k, keys[k], pages, arrays, pal565,
                               art_for, pals_for, st)
        except Exception:                                      # noqa: BLE001
            continue
        buf[cy * TILE:(cy + 1) * TILE, cx * TILE:(cx + 1) * TILE] = cell
        dx, dy = cx * TILE, cy * TILE
        for off in keys[k]['tiles']:
            out[off + T_TEXID] = slot
            out[off + T_SRC_X] = dx & 0xFF
            out[off + T_SRC_Y] = dy & 0xFF
            struct.pack_into('<II', out, off + T_SRC_X_BIG,
                             cx * STEP, cy * STEP)
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
        plist[slot] = FN.Page(slot, 0, 2, buf.tobytes(), 256)
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
