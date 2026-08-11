#!/usr/bin/env python3
"""
field_bg_pagecap.py -- no page may be named by more than 256 tiles.

THE LIMIT, AND IT IS IN THE GAME, NOT IN A GUESS
================================================
`sub_6465FB` -- the per-page submit loop `field_pick_tiles_make_vertices`
calls every frame -- walks a fixed array at guest `[0xD00050]`:

    0064676E  mov  [ebp-0x10], 0            ; page = 0
    00646780  cmp  [ebp-0x10], 0x2a         ; page < 42       <- 42 slots
    00646784  jge  done
    0064678A  imul eax, [ebp-0x10], 0x1804  ; slot stride
    00646793  mov  ecx, [0xD00050]
    00646799  cmp  [ecx+eax], 0             ; slot.count

**0x1804 = 4 + 256 * 0x18.** Four bytes of count, then exactly **256 tile
entries of 24 bytes**. Forty-two slots, back to back.

`add_page_tile` (x86 0x6464BA, signature `(x, y, z, u, v, palette, page)`)
fills them and **does not bounds-check**:

    006464C6  mov  eax, [ebp+0x20]          ; page = the LAST argument
    006464C9  imul eax, eax, 0x1804
    006464E5  mov  edx, [eax+edx]           ; count
    006464E8  imul edx, edx, 0x18
    006464EB  fstp [ecx+edx+4]              ; write x, then y z u v pal
    006465E4  add  eax, 1                   ; count++
    006465F6  mov  [edx+ecx], eax

The whole function, 0x6464BA..0x6465FA, is `imul`, loads and stores. Nothing
compares the count to anything.

So tile 257 on a page writes at `slot_base + 0x1804`, which is **exactly the
next page's count field**. A float x-coordinate lands in a `uint32_t`
counter. The submit loop then hands that counter to `draw_graphics_object`
as `n_shape`, FF7's pool allocator computes `n * element_size`, and `malloc`
is asked for hundreds of megabytes and returns NULL.

That is the Men's Hall crash, and it is why 64, 256 and 512 MB heaps all
failed with byte-identical stacks. The number was never a size.

MEASURED, from the two flevels we already have
----------------------------------------------
    vanilla mkt_mens   page 0 = 256   page 1 = 256   page 2 =  38
    ours               page 0 = 244   page 3 =   6   page 26 = 286   page 27 = 14

Vanilla splits 550 tiles as 256 + 256 + 38 -- the original tooling packed
right up to the cap and never past it. We put 286 on one page, and Men's
Hall is a single-screen room so all 286 are submitted in the same frame.

144 fields in the shipped build exceed 256 on some page AND exceed what
vanilla did. `blackbg*` and `startmap` reach 768.

WHY A POST-PASS RATHER THAN FIXING EACH PRODUCER
===============================================
Four passes move tiles between pages -- `field_bg_dense` (packs cells onto
fewer pages: 6.17 -> 2.26 per field), `field_bg_compact` (merges
byte-identical cells, so several tiles come to share one cell and a page can
pass 256 without gaining a single cell), `ff7nx_marginpage` and
`ff7nx_marginpal`. Teaching each of them the limit means four chances to get
it wrong and no single place that guarantees the invariant.

This runs last and enforces the invariant on the finished section, whatever
produced it.

HOW THE SPLIT IS PIXEL-EXACT
============================
An overloaded page is **duplicated byte for byte** into a free slot and the
excess tiles are repointed at the copy. Same size flag, same depth, same
pixels, same cell coordinates -- so every moved tile keeps its `u`, its `v`
and its palette, and samples identical texels. There is no resampling, no
requantisation and no cell relocation. The only thing that changes is which
texture handle the tile draws from.

Cost: one extra page per 256 tiles over the limit. `mkt_mens` needs one.

FX PAIRING
==========
A tile that draws from an fx page must find its fx cell at the SAME grid
coordinate (see `field_bg_compact`'s note). Copying the entire page rather
than relocating cells preserves every coordinate, so an fx pair survives a
split unchanged. The fx page itself is never touched.

The counting is done on `texture_id` only. A tile with `use_fx_page` draws
from `fx_page` (plus 14 or 18 for blend modes 2 and 3) instead, and the flag
is not one of the fields this project has located -- so `report()` counts fx
pages separately and NAMES any that are over, rather than pretending to know.
Layer 1, which is the bulk of every field and the whole of a single-screen
room, has no fx pages at all.
"""
from __future__ import annotations

import os
import struct
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import field_bg_native as FN
import field_bg_compact as FC
import ff7nx_marginblack as MB

# The game's own numbers. Both are read out of x86, not chosen.
MAX_TILES_PER_PAGE = 256          # 0x1800 / 0x18, stride 0x1804
MAX_PAGES = FN.BG_MAX_PAGES       # 42, and `cmp ..., 0x2a` agrees

T_TEXID = FN.TILE_TEXTURE_ID      # 32
T_FX_PAGE = FN.TILE_TEXTURE_ID2   # 34
T_DSTX = 2
T_DSTY = 4
TILE = 16

# ---------------------------------------------------------------- FINDINGS-122
# THE GRANDFATHERING BELOW ASSUMES A SCROLL. MOST FIELDS DO NOT SCROLL.
#
# `effective_cap` raises the limit to `max(256, vanilla's worst page)` because
# `add_page_tile` counts tiles SUBMITTED THIS FRAME, and a large scrolling
# field only ever submits a screenful. That is right, and the docstring
# already names the exception it never implemented:
#
#     "In a single-screen room like mkt_mens that is every tile, so the file
#      count IS the frame count and 256 is exact."
#
# In a single-screen room the two counts are the same number, so vanilla's
# headroom is not headroom at all -- and the 16:9 widening is exactly what
# pushes a one-screen field over. MEASURED on md8_1, the Sector 8 fire scene:
#
#     vanilla per page   {0: 671, 1: 219}                  worst 671
#     built   per page   {0: 416, 1: 269, 2: 23, 26: 256}  worst 416
#     effective_cap = max(256, 671) = 671   ->  verify() = []
#
# so a page named by 269 tiles shipped as safe. Tiles 257+ run off the end of
# the 0x1804-byte record into the NEXT page's counter, and slot 2 -- the page
# straight after slot 1 in load order -- is where 11 of the 12 black squares
# on that screen sample from. Pages 15 and 26 sit at exactly 256 because the
# cap did run on them; page 1 is the one that escaped.
#
# 25 single-screen fields in the shipped build hold a page over 256:
# ujunon5 469, sininb34 457, ujunon4 417, junair2 367, las4_0 336, blin3_1 313,
# blin68_2 307, hyou12 299, tin_3 291, hyou4 278, hyou13_2 277, junin7 275,
# mds7st32 274, mds7st33 274, junele2 273, rckt3 272, rckt32 272, md8_1 269,
# hyou8_2 266, tin_4 264, spipe_1 262, semkin_1 259, junbin5 258, hyou5_1 257,
# spipe_2 257.
#
# 103 fields are over by file count; only these 25 are one screen, so the
# other 78 keep the grandfathering and cost nothing.
SINGLE_SCREEN_HARD_CAP = True

# The 16:9 field window in game units, and the slack that still counts as one
# screen. 426x240 is the visible window, measured by aligning render_field's
# output to a 1280x720 capture (best MAD at x0 = -213, y0 = -120). The widened
# tile extent is 448x240 -- 22px wider than the window, which is the widening's
# own overshoot and not a scroll, so the slack has to cover it.
SINGLE_SCREEN_W = 426
SINGLE_SCREEN_H = 240
SINGLE_SCREEN_SLACK = 32


class CapStats:
    __slots__ = ('pages_before', 'pages_added', 'tiles_moved', 'over',
                 'refused', 'fx_over', 'single_screen', 'ss_pages',
                 'ss_tiles')

    def __init__(self):
        self.pages_before = 0
        self.pages_added = 0
        self.tiles_moved = 0
        self.over = {}            # slot -> tile count before the split
        self.refused = []         # (slot, tiles) we could not split
        self.fx_over = {}         # fx slot -> tile count, reported only
        self.single_screen = {}   # FINDINGS-122: slot -> binding tiles, the
        #                           pages the hard 256 caught that the
        #                           grandfathered cap let through
        # ATTRIBUTABLE to the single-screen rule alone -- what this field
        # would NOT have cost without it. `pages_added` is the field's total
        # and counts work the grandfathered cap was already doing; reporting
        # that as the new rule's cost overstated it 2.5x in the first build
        # (46 pages / 3,851 tiles claimed against 12 / 537 actually added).
        self.ss_pages = 0
        self.ss_tiles = 0

    def __bool__(self):
        return bool(self.pages_added or self.refused)


def counts(sec9, src_px=None):
    """{page slot: tiles naming it} plus {fx slot: tiles naming it}."""
    d2px = src_px if src_px is not None else FN.VANILLA_PX
    pages, tex_start, _tex_end = FN.parse_texture_block(sec9, d2px)
    spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    main = defaultdict(int)
    fx = defaultdict(int)
    for off in spans:
        main[sec9[off + T_TEXID]] += 1
        f = sec9[off + T_FX_PAGE]
        if f:
            fx[f] += 1
    return dict(main), dict(fx)


def worst(sec9, src_px=None):
    """The largest number of tiles on any one page. 0 for an empty section."""
    main, _ = counts(sec9, src_px)
    return max(main.values()) if main else 0


def effective_counts(sec9, src_px=None):
    """
    {page slot: tiles that BIND it} -- the fx page when a tile carries one,
    the texture id otherwise.

    THIS IS THE COUNT THE CONSOLE MAKES, AND `counts()` IS NOT.

    `counts()` returns the raw `T_TEXID` histogram, which is what the
    grandfathered cap has always used. That number is not what `add_page_tile`
    sees: a tile that names an fx page binds the fx page, so the texture id it
    also carries never becomes a call. MEASURED, over the 200 single-screen
    fields of Switch vanilla:

        by raw T_TEXID          many fields over 256 -- blue_2 739, bugin1a
                                668, hyou5_2 953, and they have shipped since
                                1997 without overrunning anything
        by effective page       ZERO fields over 256

    Vanilla's own tooling never puts more than 256 binding tiles on a page in
    a room where every tile is submitted every frame. That is the invariant,
    and it is the one our build breaks: 25 single-screen fields, 1,280 tiles
    over. See FINDINGS-122.
    """
    d2px = src_px if src_px is not None else FN.VANILLA_PX
    pages, tex_start, _tex_end = FN.parse_texture_block(sec9, d2px)
    present = {s for s, p in enumerate(pages) if p is not None}
    spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    out = defaultdict(int)
    for off in spans:
        fx = sec9[off + T_FX_PAGE]
        out[fx if (fx and fx in present) else sec9[off + T_TEXID]] += 1
    return dict(out)


def tile_extent(sec9, src_px=None):
    """(width, height) in game units of the field's whole layer-1..n tile grid.

    Returns (0, 0) for a section with no tiles.
    """
    d2px = src_px if src_px is not None else FN.VANILLA_PX
    pages, tex_start, _tex_end = FN.parse_texture_block(sec9, d2px)
    spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    xs = []
    ys = []
    for off in spans:
        xs.append(struct.unpack_from('<h', sec9, off + T_DSTX)[0])
        ys.append(struct.unpack_from('<h', sec9, off + T_DSTY)[0])
    if not xs:
        return 0, 0
    return max(xs) + TILE - min(xs), max(ys) + TILE - min(ys)


def is_single_screen(sec9, src_px=None,
                     win_w=None, win_h=None, slack=None):
    """
    True when every tile in the field can be on screen at once, so the file
    count IS the frame count and vanilla's page counts prove nothing.

    See the FINDINGS-122 note at the top of this module.
    """
    win_w = SINGLE_SCREEN_W if win_w is None else win_w
    win_h = SINGLE_SCREEN_H if win_h is None else win_h
    slack = SINGLE_SCREEN_SLACK if slack is None else slack
    try:
        w, h = tile_extent(sec9, src_px)
    except Exception:                                          # noqa: BLE001
        return False
    if not w:
        return False
    return w <= win_w + slack and h <= win_h + slack


def effective_cap(vanilla_sec9=None, src_px=None,
                  max_tiles=MAX_TILES_PER_PAGE, sec9=None):
    """
    The per-field limit to enforce: `max(256, whatever vanilla already did)`.

    THE 256 IS ABOUT SIMULTANEOUSLY VISIBLE TILES, NOT TOTAL ONES, and this
    is the whole reason a flat 256 is the wrong rule.

    `add_page_tile` is called once per tile that is actually submitted this
    frame. In a single-screen room like `mkt_mens` that is every tile, so the
    file count IS the frame count and 256 is exact. In a large scrolling
    field it is a window: MEASURED, vanilla `crater_2` names page 0 from
    **1912** tiles and has shipped since 1997 without overrunning anything,
    because only a screenful is ever submitted.

    A flat 256 would "fix" 413 fields, add 704 pages and leave 17 still over
    -- most of that work protecting fields that were never at risk.

    Vanilla is the ground truth we have. If vanilla puts N tiles on its worst
    page and the game is fine, then N is demonstrably survivable for that
    field's geometry and scrolling. So the rule is:

        ours_max  <=  max(256, vanilla_max)

    which triggers only where WE made a field worse than the shipping game
    did, and never asks a scrolling field to do something vanilla did not.

    With no vanilla section to compare against, the answer is a flat 256 --
    the conservative choice.

    FINDINGS-122: this scalar is the GRANDFATHERED cap and is unchanged. The
    single-screen rule is applied per page by `single_screen_over`, not here,
    because dropping this scalar to 256 for every single-screen field would
    split 81 fields and add 150 pages -- almost all of it on pages where the
    raw `T_TEXID` count is enormous but the binding count is one, i.e. pages
    vanilla itself "exceeds" and has always been fine on.
    """
    if vanilla_sec9 is None:
        return max_tiles
    try:
        main, _fx = counts(vanilla_sec9, src_px)
    except Exception:                                          # noqa: BLE001
        return max_tiles
    return max(max_tiles, max(main.values()) if main else 0)


def single_screen_over(sec9, src_px=None, max_tiles=MAX_TILES_PER_PAGE):
    """
    {slot: binding tiles} for the pages a SINGLE-SCREEN field puts over the
    hard 256, or {} when the rule does not apply.

    This is the whole of FINDINGS-122's change. It is additive: it can only
    ever add pages to the `over` set that `effective_cap`'s grandfathering
    let through, and only in a field where every tile is submitted every
    frame, and only for pages vanilla's own tooling never overfills.
    """
    if not SINGLE_SCREEN_HARD_CAP:
        return {}
    if not is_single_screen(sec9, src_px):
        return {}
    try:
        eff = effective_counts(sec9, src_px)
    except Exception:                                          # noqa: BLE001
        return {}
    return {s: n for s, n in eff.items() if n > max_tiles}


def cap_section9(sec9, src_px=None, max_tiles=MAX_TILES_PER_PAGE,
                 vanilla_sec9=None):
    """
    Split every page named by more than the effective cap.

    Returns (new_sec9, CapStats). The section comes back unchanged, and
    `stats.pages_added == 0`, when nothing is over.

    `vanilla_sec9` is the same field's Switch-vanilla section 9; passing it
    raises the limit to whatever vanilla already survives. See
    `effective_cap`.
    """
    max_tiles = effective_cap(vanilla_sec9, src_px, max_tiles, sec9=sec9)
    st = CapStats()
    d2px = src_px if src_px is not None else FN.VANILLA_PX
    # A section this module cannot parse is LEFT ALONE and named, never
    # guessed at. Two of the 711 fields do not parse as a background at all
    # (blackbgb / blackbgb.xone) and every other pass already skips them.
    try:
        pages, tex_start, tex_end = FN.parse_texture_block(sec9, d2px)
        spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    except Exception as exc:                                   # noqa: BLE001
        st.refused.append(('unparsed', str(exc)))
        return sec9, st
    st.pages_before = sum(1 for p in pages if p is not None)

    by_page = defaultdict(list)
    for off in spans:
        by_page[sec9[off + T_TEXID]].append(off)
    _main, fxc = counts(sec9, d2px)
    st.fx_over = {s: n for s, n in fxc.items() if n > max_tiles}

    # FINDINGS-122. A single-screen field submits every tile every frame, so
    # its binding count IS its frame count and vanilla's headroom is not
    # headroom. Those pages get the hard 256; every other page in the field
    # keeps the grandfathered cap, so this can only ever ADD to `over`.
    ss_over = single_screen_over(sec9, d2px, MAX_TILES_PER_PAGE)
    st.single_screen = dict(ss_over)

    def _cap_for(slot):
        return MAX_TILES_PER_PAGE if slot in ss_over else max_tiles

    over = {s: offs for s, offs in by_page.items()
            if len(offs) > _cap_for(s)}

    # A page that is over only through the FX byte cannot be split here: the
    # splitter repoints `T_TEXID`, and these tiles do not bind through it.
    # NAME them rather than reporting the field as safe. MEASURED: 5 of the
    # 25 -- las4_0, sininb34, spipe_1, ujunon4, ujunon5.
    for s, n in ss_over.items():
        if len(by_page.get(s, ())) <= MAX_TILES_PER_PAGE:
            st.refused.append((s, n))

    if not over:
        return sec9, st
    st.over = {s: len(o) for s, o in over.items()}

    # THE COPY MUST LAND IN THE SAME BAND AS THE PAGE IT COPIES.
    #
    # THIS WAS THE BLACK-SQUARE BUG AND IT WAS MINE. The first version took
    # the lowest free slot, `range(MAX_PAGES)`, which for most fields is slot
    # 0, 1, 2... But the slot INDEX is what tells the engine a page's depth
    # and blend mode -- `field_bg_native.D1_GROUPS` / `D2_GROUPS`, and
    # `field_load_textures` branches on the index at x86 0x6402EB
    # (`cmp ecx, 0xF`, `cmp edx, 0x18`, `cmp eax, 0x21`, `cmp ecx, 0x28`).
    # Slots 0..0x19 are the PALETTED band; 0x1A..0x29 are the truecolor one.
    #
    # So a duplicated truecolor page parked in slot 0 was handed the paletted
    # path, `_load_texture` (x86 0x6710AC) refused it, and
    # `field_load_textures` abandoned every remaining page of the field --
    # black squares in clumps.
    #
    # MEASURED, our own shipped archive against vanilla:
    #
    #     vanilla depth-2 pages   51, in slots {26, 27, 28} ONLY
    #     ours (300-field sample) 658, in slots {0,1,2,3,4, 26..31}
    #                                          ^^^^^^^^^^^ 65 pages, all wrong
    #
    # A split now draws from the free slots of the source page's own group and
    # REFUSES rather than moving a page across bands.
    free_by_group = {}
    for s in range(MAX_PAGES):
        if pages[s] is None:
            g = FC._group(s)
            if g is not None:
                free_by_group.setdefault(g, []).append(s)
    buf = bytearray(sec9)
    for slot in sorted(over):
        src = pages[slot] if slot < len(pages) else None
        if src is None:
            # Tiles naming a page that is not present. That is a different
            # defect (audit_dangling.py) and splitting cannot help it, so it
            # is named rather than silently "handled".
            st.refused.append((slot, len(over[slot])))
            continue
        grp = FC._group(slot)
        free = free_by_group.get(grp) if grp is not None else None
        # PER-PAGE CAP, NOT THE FIELD SCALAR. A single-screen page gets the
        # hard 256 while the rest of the field keeps the grandfathered limit;
        # slicing with the scalar here left `rest` empty and split nothing.
        _cap = _cap_for(slot)
        rest = over[slot][_cap:]
        while rest:
            if not free:
                # No free slot in this page's OWN band. Refusing is the only
                # safe answer: a copy in another band is drawn by the wrong
                # path and takes the whole field's texture load down with it.
                st.refused.append((slot, len(rest)))
                break
            dst = free.pop(0)
            chunk, rest = rest[:_cap], rest[_cap:]
            # BYTE-FOR-BYTE duplicate. Same size flag, same depth, same
            # pixels, so every repointed tile keeps its u, v and palette and
            # samples identical texels.
            pages[dst] = FN.Page(dst, src.size_flag, src.depth,
                                 src.data, src.px)
            for off in chunk:
                buf[off + T_TEXID] = dst
            st.pages_added += 1
            st.tiles_moved += len(chunk)
            # Attribute this page to the single-screen rule only for the part
            # the grandfathered cap would NOT have split anyway.
            if slot in ss_over and _cap < max_tiles:
                _would = max(0, len(over[slot]) - max_tiles)
                st.ss_pages += 1
                st.ss_tiles += max(0, len(chunk) - _would)

    out = FN.replace_texture_block(bytes(buf), pages, tex_start, tex_end)
    return out, st


T_PAL = FN.TILE_PALETTE_ID        # 22

# THE PALETTE CLAMP IS OFF, AND IT IS OFF BECAUSE IT CAUSED A CRASH.
#
# The observation behind it is real and reproducible: `md8_1` ships fourteen
# layer-2 tiles at dx -224/-208 and 192/208 naming palette 13 when the field
# has thirteen, and that coordinate set is exactly the column of black
# rectangles down both edges of the screen.
#
# Repointing them did not fix it. Build 27 turned that field from "black
# squares" into a HARD CRASH -- and the stack is `draw_graphics_object` at
# +0xADF24C, the same signature as the tile-counter overrun. Worse, a second
# reading of the same archive with the same call reported 0 tiles repointed
# where the first reported 14, which means my own measurement of this is not
# stable and I do not understand the tile enumeration well enough to be
# writing bytes based on it.
#
# So it is gated and off. The observation stays written down because it is
# almost certainly still the cause of those particular squares -- what is
# wrong is the fix, not the diagnosis.
CLAMP_PALETTES = False


def clamp_palettes(sec9, sec3, src_px=None):
    """Repoint every tile that names a palette the field does not have.

    Returns (new_sec9, n_fixed, npg).

    WHY THIS IS NEEDED, AND IT IS THE BLACK SQUARES IN SECTOR 8.
    -----------------------------------------------------------
    MEASURED on `md8_1` -- the fire scene before Aerith:

        vanilla   13 palettes, 890 tiles, every one names 0..12
        ours      13 palettes, 964 tiles, and FOURTEEN name palette 13

    Those fourteen are Cosmos's widescreen tiles: all layer 2, all wholly
    outside the 4:3 picture (dx -224, -208 on the left; 192, 208 on the
    right), all sampling the same cell, and all naming a palette that does
    not exist. Their dx/dy set is exactly the column of black rectangles down
    both edges of the screenshot.

    This is not something our passes wrote. It is how the mod ships, and it
    works on PC for the reason `ff7nx_marginpal` already documents: "Cosmos
    leaves the palette byte of its 16:9 tiles at whatever it was because FFNx
    replaces the page with the DDS and never applies it." On the Switch there
    is no DDS replacement, so the index IS applied -- and an index past the
    end of the palette table reads whatever follows it.

    `ff7nx_marginpal` fixes this class already, but only for LAYER 1 tiles
    that sample a placeholder cell. These are layer 2, so nothing caught them.

    The replacement palette is the one most used by the other tiles sampling
    the SAME cell, because that is the table the cell's indices were authored
    against; failing that, the modal palette of the page; failing that, 0.
    Anything valid beats reading off the end of the table.
    """
    if not CLAMP_PALETTES:
        return sec9, 0, None
    try:
        _cols, _hdr, npg, _cpp = MB.palette_colours(sec3)
    except Exception:                                          # noqa: BLE001
        return sec9, 0, None
    if not npg:
        return sec9, 0, npg
    try:
        d2px = src_px if src_px is not None else FN.VANILLA_PX
        pages, tex_start, _e = FN.parse_texture_block(sec9, d2px)
        spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    except Exception:                                          # noqa: BLE001
        return sec9, 0, npg
    by_cell = defaultdict(lambda: defaultdict(int))
    by_page = defaultdict(lambda: defaultdict(int))
    for off in spans:
        pal = sec9[off + T_PAL]
        if pal >= npg:
            continue
        slot = sec9[off + T_TEXID]
        by_cell[(slot, sec9[off + 10], sec9[off + 12])][pal] += 1
        by_page[slot][pal] += 1
    buf = bytearray(sec9)
    n = 0
    for off in spans:
        if buf[off + T_PAL] < npg:
            continue
        slot = buf[off + T_TEXID]
        cell = by_cell.get((slot, buf[off + 10], buf[off + 12]))
        page = by_page.get(slot)
        if cell:
            new = max(cell.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        elif page:
            new = max(page.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        else:
            new = 0
        buf[off + T_PAL] = new
        n += 1
    return (bytes(buf) if n else sec9), n, npg


def verify(sec9, src_px=None, max_tiles=MAX_TILES_PER_PAGE,
           vanilla_sec9=None):
    """[] when the section is safe, else a list of complaints."""
    max_tiles = effective_cap(vanilla_sec9, src_px, max_tiles, sec9=sec9)
    bad = []
    try:
        main, fx = counts(sec9, src_px)
    except Exception:                                          # noqa: BLE001
        # Not a background this module understands. Not a violation -- the
        # two such sections in the game are skipped by every other pass too,
        # and reporting them here would make the build cry wolf.
        return []
    ss_over = single_screen_over(sec9, src_px, MAX_TILES_PER_PAGE)
    for slot, n in sorted(main.items()):
        if n > max_tiles:
            bad.append('page %d is named by %d tiles (limit %d) -- '
                       'add_page_tile will overrun into page %d\'s counter'
                       % (slot, n, max_tiles, slot + 1))
    # FINDINGS-122: the field is one screen, so every tile is submitted every
    # frame and the binding count is the frame count. Vanilla never exceeds
    # 256 here, in any of its 200 single-screen fields.
    for slot, n in sorted(ss_over.items()):
        if n > max_tiles:
            continue                      # already reported above
        bad.append('page %d BINDS %d tiles (hard limit %d) in a single-screen '
                   'field -- add_page_tile will overrun into page %d\'s '
                   'counter' % (slot, n, MAX_TILES_PER_PAGE, slot + 1))
    for slot, n in sorted(fx.items()):
        if n > max_tiles:
            bad.append('fx page %d is named by %d tiles (limit %d)'
                       % (slot, n, max_tiles))
    if main and max(main) >= MAX_PAGES:
        bad.append('page index %d is at or past the game\'s %d-slot array'
                   % (max(main), MAX_PAGES))
    return bad


# ------------------------------------------------------------------- main
def main(argv=None):
    import argparse
    import lgp
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('flevel')
    ap.add_argument('--px', type=int, default=256,
                    help='the size depth-2 pages have in this archive')
    ap.add_argument('--fix', metavar='OUT',
                    help='write a capped flevel here')
    a = ap.parse_args(argv)

    arc = lgp.Archive(a.flevel)
    bad_fields = []
    total_over = 0
    fixed = 0
    for e in arc.entries:
        if not arc.is_field(e):
            continue
        try:
            raw = arc.decompressed(e)
            parts = lgp.split_sections(raw)
            sec9 = parts[8]
        except Exception:                                      # noqa: BLE001
            continue
        problems = verify(sec9, a.px)
        if problems:
            bad_fields.append((e['name'], problems))
            total_over += len(problems)
        if a.fix and problems:
            new9, st = cap_section9(sec9, a.px)
            if st.pages_added:
                parts[8] = new9
                arc.replace(e['name'],
                            arc.encode_field(lgp.join_sections(parts)))
                fixed += 1
    print('%d field(s) exceed the limit' % len(bad_fields))
    for name, problems in bad_fields[:25]:
        print('  %-12s %s' % (name, problems[0]))
    if len(bad_fields) > 25:
        print('  ... and %d more' % (len(bad_fields) - 25))
    if a.fix:
        arc.write(a.fix)
        print('wrote %s (%d field(s) split)' % (a.fix, fixed))
    return 1 if bad_fields and not a.fix else 0


if __name__ == '__main__':
    raise SystemExit(main())
