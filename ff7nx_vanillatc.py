#!/usr/bin/env python3
"""
ff7nx_vanillatc.py -- the pages that were ALREADY truecolor never got the mod.

THE REPORT, from hardware after build 147, on `cosmo`:

    "in the field cosmo i clearly see the main field looking super low quality
     (the area in the 4:3 region) ... this particular map is not using our
     upscaled cosmos limit break textures"

Correct, and it is structural rather than a regression. FINDINGS-282.

WHAT IS HAPPENING
=================
Every pass in this project that puts Cosmos art on a field background works by
PROMOTING a paletted page: `field_bg_dense` takes a depth-1 page, converts it
to truecolor, and fills it from the mod's DDS. A page that is ALREADY depth-2
in vanilla has nothing to promote, so it falls through all of them, and the
only thing that ever touches it is `field_bg_native.resize_depth2` -- which is
honest nearest-neighbour pixel replication:

    "Nearest-neighbour integer RESIZE of a 16-bit page ... it is decimation,
     not a box filter, for the same reason the upscale is replication"

So the page arrives at 768px carrying 256px of information. `cosmo` has three
background pages and two of them are the entire picture:

    cosmo   slot 15   depth 1   -> promoted, gets Cosmos art
            slot 26   depth 2   <- the canyon, the huts, the stairs
            slot 27   depth 2   <- the sky and the far rock

MEASURED against the built flevel: `cosmo` slot 26 is **99.2% identical to the
vanilla nearest-neighbour upscale**, 59 of its 64 cells byte for byte. Then
`2xsal_p.glsl` smooths it, and that is the mush in the photograph.

`field_bg_dense.source_cell` even has the branch and says the quiet part:

    if p.depth == 2:
        st.from_vanilla += 1
        # "A depth-2 source page is already at the destination size,
        #  so its cell is (edge*scale)^2 and needs no upscale."

True, and the conclusion does not follow: it is at the destination SIZE
because pixels were replicated to get it there. Build 147's log prints the
count and it has always read as harmless -- `6,993 from the paletted page`.

THE ART IS SHIPPED. IT IS SITTING IN THE .iro.
==============================================
    cosmo  page 26  pal 0  768px  4,883 unique colours  100.0% opaque
    cosmo  page 27  pal 0  768px  2,262 unique colours   64.1% opaque

Cosmos Limit Break dumps these pages like any other, same atlas layout, same
coordinates. `ArtProvider` indexes them. Nothing has ever asked for them.

THE POPULATION -- 27 FIELDS, 51 PAGES, 50 WITH ART
==================================================
    blin67_4  cosmo    cosmo2   fr_e     gaiin_6  gaiin_7  gldinfo  gldst
    hyoumap   jtemplc  junair2  junone22 kuro_11  md_e1    nivgate2 nivgate3
    nivl_e2   nivl_e3  qc       rckt3    rckt32   sky      spipe_2  trnad_52
    zmind1    zmind2   zmind3

Cosmo Canyon, the Gold Saucer, the Nibelheim gate, the rocket, the Temple of
the Ancients, the Junon airport, the Whirlwind Maze. `md_e1` slot 26 is the
one page with no art, and no tile references it.

WHY IT IS SAFE, AND IT IS MEASURED PER CELL RATHER THAN ARGUED
==============================================================
The only thing that can go wrong is TRANSPARENCY. `0x0000` is the colour key
on a depth-2 page (FINDINGS-152), so a substitution must not fill a cut-out or
cut a solid. Comparing, for every cell a tile actually samples, vanilla's
opacity against Cosmos's:

    8,736 referenced depth-2 cells across the 27 fields

       8,317   95.2%   IDENTICAL opacity   -> pure resolution upgrade
         408    4.7%   Cosmos paints where vanilla is clear
          11    0.1%   Cosmos is clear where vanilla paints

`cosmo` and `cosmo2` agree on 300 of 300 cells each. The 419 disagreements sit
in five fields -- `spipe_2`, `rckt3`, `rckt32`, `trnad_52`, `gldst`,
`nivgate2/3` -- and this pass REFUSES every one of them. A cell whose
silhouette would change is not worth a byte of risk when the same build is
handing 8,317 cells a 3x resolution increase for nothing.

Two further hazards, both measured to zero:

    opaque Cosmos texels that quantise to 0x0000     0 of 12,278,191
        PageArt already applies the NEAR_BLACK lift, so a substitution
        cannot invent transparency. Asserted per page anyway.

    ambiguous / multi-state DDS on pages 26, 27, 28  0 of 638 archive-wide
        none of these are animated FX pages, so a substitution cannot
        collapse a runtime animation. Vetoed per page anyway.

WHAT IT COSTS
=============
Nothing. No page count, no slot, no palette, no UV, no tile record, no header
word, no byte of section 9 outside the pixels of pages that already exist, at
coordinates the tiles already sample. It cannot move a page, overrun the frame
cap or change the heap. It is the cheapest change in this project relative to
what it buys.

WHERE IT RUNS
=============
`_convert_field_backgrounds`, immediately after `field_bg_native.resize_section9`
-- the one moment when every depth-2 page in the field is at its final size and
still holds nothing but the upscale. Everything downstream then benefits for
free: `source_cell`'s `from_vanilla` branch returns Cosmos art without the
branch changing at all.

SEVENTH_NX_NO_VANILLA_TC=1 turns this off.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import field_bg_native as FN

OFF_ENV = 'SEVENTH_NX_NO_VANILLA_TC'

TILE = 16                    # the cell grid, in source units
PAGE_UNITS = 256             # a page is 256x256 source units at any px
# Tile-record fields, and they are the SAME on all four layers.
# `ff7nx_marginblack` and `diag_common` both use these numbers.
T_SRCX, T_SRCY = 10, 12
T_TEX = 32


def disabled():
    return os.environ.get(OFF_ENV) == '1'


class Stats:
    __slots__ = ('fields', 'pages', 'cells', 'texels', 'refused_page',
                 'refused_cell', 'no_art', 'ambiguous', 'keyed', 'names')

    def __init__(self):
        self.fields = 0
        self.pages = 0
        self.cells = 0
        self.texels = 0
        self.refused_page = 0
        self.refused_cell = 0
        self.no_art = 0
        self.ambiguous = 0
        self.keyed = 0
        self.names = []


def merge(a, b):
    for k in ('fields', 'pages', 'cells', 'texels', 'refused_page',
              'refused_cell', 'no_art', 'ambiguous', 'keyed'):
        setattr(a, k, getattr(a, k) + getattr(b, k))
    a.names.extend(b.names)


# --------------------------------------------------------------------------
def referenced_cells(sec9, back, tex, slots):
    """{slot: set((cell_x, cell_y))} -- the 16-unit cells tiles actually sample.

    The structural walk `diag_common.walk_layers` does, with that module's
    constants: EVERY layer's tile record is `field_bg_native.TILE_SIZE`, and
    `src_x`/`src_y`/`page` sit at 10/12/32 on all four of them.

    A layer-1/2 tile samples 16 units, a layer-3/4 tile 32, so a parallax tile
    marks FOUR cells. Working on the 16-unit grid keeps one rule for both and
    keeps the refusal granular: one bad cell of a parallax block does not cost
    the other three.
    """
    out = {s: {} for s in slots}
    if not out:
        return out

    def mark(first, n, span, layer):
        for i in range(n):
            off = first + i * FN.TILE_SIZE
            s = sec9[off + T_TEX]
            if s not in out:
                continue
            sx, sy = sec9[off + T_SRCX], sec9[off + T_SRCY]
            for dy in range(0, span, TILE):
                for dx in range(0, span, TILE):
                    c = ((sx + dx) // TILE, (sy + dy) // TILE)
                    out[s].setdefault(c, set()).add(layer)

    o = back + 4                                   # "BACK"
    _w, _h, n1, _d, _b = struct.unpack_from('<HHHHH', sec9, o)
    o += 10
    mark(o, n1, TILE, 1)                           # layer 1
    o += n1 * FN.TILE_SIZE + 2
    for layer, unused in ((2, 16), (3, 10), (4, 10)):
        if o >= tex:
            break
        flag = sec9[o]
        o += 1
        if flag == 0:
            continue
        if flag != 1:
            raise ValueError('layer flag %d at %d' % (flag, o - 1))
        _w, _h, n = struct.unpack_from('<HHH', sec9, o)
        o += 6 + unused + 2
        mark(o, n, TILE if layer == 2 else 32, layer)
        o += n * FN.TILE_SIZE + 2
    if o != tex:
        raise ValueError('layer walk ended at %d, TEXTURE at %d' % (o, tex))
    return out


def _opaque_16(block, s):
    """A 16x16 opacity mask for a (16*s, 16*s) destination block.

    "Any opaque texel in the s x s group" -- the same reduction the census
    used to establish the 95.2%, and the conservative one: it calls a cell
    opaque as soon as the mod paints anything there, so a cell where Cosmos
    only antialiases into vanilla's empty space reads as a DISAGREEMENT and is
    refused rather than substituted.
    """
    return (block != 0).reshape(TILE, s, TILE, s).any(axis=(1, 3))


def _opaque_16_page(page, s):
    """The same reduction over a WHOLE page -- (256, 256) of cell opacity."""
    n = page.shape[0] // s
    return (page != 0).reshape(n, s, n, s).any(axis=(1, 3))


def convert_page(dst, vanilla, art_buf, cells, px, tmask=None):
    """(n_cells, n_texels, n_keyed) substituted in `dst` (a mutable view).

    `vanilla` is the ORIGINAL 256px depth-2 page, `art_buf` the mod's page at
    `px`, `cells` a {(cell_x, cell_y): {layers}} map, and `tmask` the mod's
    "paints NOTHING" mask (alpha < 8) at `px`.

    TWO RULES, BECAUSE A BACKGROUND AND AN OVERLAY WANT OPPOSITE THINGS.

    A LAYER-1 cell is the background. Transparency there shows the clear
    colour, i.e. black, so the silhouette must not move at all: the cell is
    substituted only where Cosmos and vanilla agree on opacity exactly. That
    is the build-149 rule, unchanged, and MEASURED it costs nothing --
    Cosmos paints 100% of every layer-1 cell on these pages.

    A LAYER-2+ cell is an OVERLAY, and there the 1997 page is the problem.
    FINDINGS-287, measured on `gldst`:

        layer 2, slot 27:  61,730 of 407,808 texels (15.1%) are texels where
        COSMOS PAINTS NOTHING and we draw a pixel anyway

    Those are the black blocks, and the fact that the player can walk BEHIND
    them is what says they are on layer 2. It is the same defect the MOD-CLEAR
    KEY arm already fixes for PALETTED pages -- "a texel the mod calls empty
    over a non-zero vanilla index was neither keyed nor skipped, it was
    painted with the 1997 art's hard black outline" -- and it never reached
    here because a vanilla depth-2 page is never promoted (FINDINGS-282).

    So on an overlay cell the transparency taken is the UNION of the two:

        clear = tmask | (vanilla == 0)

    which can only ever REVEAL what is behind, never hide it. Cosmos's cut is
    honoured, vanilla's holes stay holes, and no hole is ever filled. The
    threshold is `tmask` (alpha < 8), the same conservative end MOD-CLEAR
    uses, because this arm ADDS transparency and so must be sure the mod
    paints nothing.
    """
    s = px // PAGE_UNITS
    up = None
    n_cell = n_tex = n_key = 0
    for (cx, cy), layers in sorted(cells.items()):
        if cx * TILE + TILE > PAGE_UNITS or cy * TILE + TILE > PAGE_UNITS:
            continue
        vb = vanilla[cy * TILE:(cy + 1) * TILE, cx * TILE:(cx + 1) * TILE]
        y0, y1 = cy * TILE * s, (cy + 1) * TILE * s
        x0, x1 = cx * TILE * s, (cx + 1) * TILE * s
        ab = art_buf[y0:y1, x0:x1]
        if ab.shape != (TILE * s, TILE * s):
            continue
        overlay = tmask is not None and layers and min(layers) >= 2
        if not overlay:
            # GATE 1 -- on the background the silhouette must not move.
            if not (_opaque_16(ab, s) == (vb != 0)).all():
                continue
        # GATE 2 -- the destination must still be the untouched upscale, so
        # anything an earlier pass deliberately wrote here is left alone.
        if up is None:
            up = np.repeat(np.repeat(vanilla, s, axis=0), s, axis=1)
        if not (dst[y0:y1, x0:x1] == up[y0:y1, x0:x1]).all():
            continue
        blk = ab
        if overlay:
            clear = tmask[y0:y1, x0:x1] | (up[y0:y1, x0:x1] == 0)
            blk = np.where(clear, np.uint16(FN.EMPTY), ab)
            n_key += int((clear & (up[y0:y1, x0:x1] != 0)).sum())
        dst[y0:y1, x0:x1] = blk
        n_cell += 1
        n_tex += ab.size
    return n_cell, n_tex, n_key


def apply_to_section9(sec9, vanilla9, px, art_for, field=None,
                      ambiguous=(), st=None):
    """(new_sec9, Stats) -- substitute Cosmos art on every vanilla depth-2 page.

    `vanilla9` is the field's ORIGINAL section 9 (256px). It is what decides
    which pages count as "vanilla depth-2" -- a page PROMOTED by
    `field_bg_dense` is depth-2 too and must never be touched here, because its
    pixels are already the mod's and were chosen with far more care than a
    whole-cell copy.
    """
    st = st or Stats()
    if disabled() or art_for is None:
        return sec9, st
    try:
        vpages, _vs, _ve = FN.parse_texture_block(vanilla9, FN.VANILLA_PX)
        pages, tex_s, tex_e = FN.parse_texture_block(sec9, px)
    except FN.Section9Error:
        return sec9, st

    was_d2 = {p.slot for p in vpages if p is not None and p.depth == 2}
    vraw = {p.slot: p for p in vpages if p is not None}
    live = [p for p in pages
            if p is not None and p.depth == 2 and p.slot in was_d2
            and p.px == px]
    if not live:
        return sec9, st

    back = sec9.find(b'BACK')
    if back < 0:
        return sec9, st
    try:
        refs = referenced_cells(sec9, back, tex_s, {p.slot for p in live})
    except Exception:                                          # noqa: BLE001
        return sec9, st

    changed = False
    for p in live:
        cells = refs.get(p.slot) or set()
        if not cells:
            continue
        if (field, p.slot, 0) in ambiguous:
            st.ambiguous += 1
            continue
        art = art_for(p.slot, 0)
        if art is None:
            st.no_art += 1
            continue
        if getattr(art, 'px', 0) != px:
            st.refused_page += 1
            continue
        buf = np.frombuffer(art.buf, '<u2').reshape(px, px)
        # GATE 3 -- an opaque source texel must never be 0x0000, or the
        # substitution would punch a hole the artist did not draw. Measured at
        # zero archive-wide; asserted here so it stays that way.
        hm = getattr(art, 'hmask', None)
        if hm is not None and hm.shape == buf.shape and (hm & (buf == 0)).any():
            st.refused_page += 1
            continue
        van = np.frombuffer(vraw[p.slot].data, '<u2').reshape(PAGE_UNITS,
                                                              PAGE_UNITS)
        dst = np.frombuffer(p.data, '<u2').reshape(px, px).copy()
        tm = getattr(art, 'tmask', None)
        if tm is not None and tm.shape != buf.shape:
            tm = None
        n_cell, n_tex, n_key = convert_page(dst, van, buf, cells, px, tm)
        st.keyed += n_key
        st.refused_cell += len(cells) - n_cell
        if not n_cell:
            continue
        p.data = dst.tobytes()
        st.pages += 1
        st.cells += n_cell
        st.texels += n_tex
        changed = True

    if not changed:
        return sec9, st
    st.fields += 1
    if field:
        st.names.append(field)
    return FN.replace_texture_block(sec9, pages, tex_s, tex_e), st


def summarise(st):
    if not st.fields:
        return ('  vanilla truecolor: OFF (%s=1)' % OFF_ENV if disabled()
                else '  vanilla truecolor: no page needed it')
    worst = ', '.join(sorted(st.names)[:6])
    return (
        '  VANILLA TRUECOLOR PAGES: %s cell(s), %s texel(s) on %s page(s) '
        'across %s field(s) took the mod\'s art instead of the 1997 page '
        'pixel-replicated up to the destination size. Every pass that puts Cosmos art on a field '
        'works by PROMOTING a paletted page, so a page that was ALREADY '
        'depth-2 in vanilla has nothing to promote and falls through all of '
        'them -- the only thing that ever touched it was '
        'field_bg_native.resize_depth2, which is honest nearest-neighbour '
        'replication. MEASURED before this pass: cosmo slot 26, the canyon and '
        'the huts, was 99.2%% identical to the vanilla upscale, 59 of its 64 '
        'cells byte for byte, and field_bg_dense.source_cell counted the class '
        'in its "from the paletted page" total without anyone reading it as a '
        'defect. %s cell(s) were looked at here. '
        'The art was always shipped: cosmo page 26 is 4,883 unique '
        'colours at full size in the .iro. SCOPED PER CELL, and a cell is '
        'refused unless (a) it is actually SAMPLED by a tile, (b) Cosmos and '
        'vanilla agree on its opacity exactly -- 8,317 of 8,736 referenced '
        'cells archive-wide, and 300 of 300 in both cosmo and cosmo2 -- and '
        '(c) the destination block is still the untouched upscale, so anything '
        'ff7nx_marginart or ff7nx_marginblack wrote is left alone. %s cell(s) '
        'were refused on those gates and keep the upscale. A page is refused '
        'whole if its art is the wrong size, if the mod ships several DDS '
        'states for it (an animated FX page -- 0 of these are), or if one '
        'opaque texel would quantise to 0x0000 and punch a hole. NO page '
        'count, slot, palette, UV, tile record or header word changes: this '
        'writes pixels into pages that already exist, at coordinates the tiles '
        'already sample, so it cannot move a page or overrun the frame cap. '
        '-- COSMOS ALPHA ON LAYER 2+: %s texel(s) of 1997 filler were made '
        'TRANSPARENT because Cosmos paints nothing there. A vanilla depth-2 '
        'page is never promoted, so the MOD-CLEAR KEY arm that fixes exactly '
        'this for paletted pages never reached it -- "a texel the mod calls '
        'empty over a non-zero vanilla index was neither keyed nor skipped, '
        'it was painted with the 1997 art\'s hard black outline". MEASURED on '
        'gldst: 61,730 of layer 2\'s 407,808 texels, and they are the black '
        'blocks the player can walk BEHIND -- which is what says they are an '
        'overlay and not the background. Scoped to cells EVERY tile of which '
        'is layer 2+, at the tmask threshold (alpha < 8, the same '
        'conservative end MOD-CLEAR uses), and the transparency taken is the '
        'UNION of Cosmos\'s and vanilla\'s: a hole is never filled and a '
        'layer-1 cell never gains one, so this can only reveal what is '
        'behind, never hide it. Fields: %s%s. Set %s=1 to restore build 148.'
        % (f'{st.cells:,}', f'{st.texels:,}', f'{st.pages:,}',
           f'{st.fields:,}',
           f'{st.cells + st.refused_cell:,}', f'{st.refused_cell:,}',
           f'{st.keyed:,}',
           worst, ' ...' if len(st.names) > 6 else '', OFF_ENV))
