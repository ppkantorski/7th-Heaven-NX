#!/usr/bin/env python3
"""
field_bg_compact.py -- pack a field's background pages into as few as they
actually need.

WHY THIS IS THE LEVER
=====================
Every present page costs one texture, and `field_load_textures`
(x86 0x640292) abandons the whole loop on the first texture it cannot
allocate -- every page after that keeps handle 0, 0x66E272 refuses a null
handle, and those tiles never reach the GPU. Scattered black squares. So the
number that breaks is the page COUNT.

MEASURED across all 709 fields of vanilla flevel.lgp: a field presents a mean
of 4.75 pages holding a mean of 923 referenced cells. A page is a 16x16 grid,
so 4.75 pages is 1,216 cell slots for 923 cells. And the distribution is much
worse than the mean:

    fship_2   12 pages   1,322 cells referenced   -> fits in 6
    del3      11 pages   1,202 cells              -> fits in 4
    ujunon2   11 pages   1,318 cells              -> fits in 5

`fship_2` is the heaviest field in the game. It is the reason the page ceiling
is 12. **It is half empty**, and so are the four other fields that reach 12.

Nothing requires a tile to stay on the page it is on. Tile records are
relocatable by exactly the mechanism field_bg_repack already uses and has
shipped:

    src_x_big (offset 42), src_y_big (offset 46)   u32, u and v times 1e7
    texture_id (offset 32), fx_page (offset 34)    one byte each

and moving a cell between two DEPTH-1 pages is strictly easier than the
truecolor promotion: same depth, same 256x256 size, the same 8-bit indices
copied verbatim, no colour conversion, no palette decision. A tile's
palette_ID lives in the TILE record (offset 22) and indexes the field's
palette array, so it keeps selecting the same palette after the move. The
picture is identical, byte for byte, by construction.

THE THREE THINGS THAT CONSTRAIN THE PACKING
===========================================
1. BLEND GROUP. A page's blend mode comes from its slot range. A cell may only
   move within its own group.

   `field_bg_native.D1_GROUPS = ((0x00,0x0F,4), (0x0F,0x18,1), (0x18,0x1A,0))`
   and FFNx's table (`common.cpp:2216`, "identical to the Direct3D driver")
   reads those codes as `0 average, 1 additive, 2 subtractive, 3 25% incoming,
   4 none`. So the bands are:

       0x00-0x0E   blend 4   OPAQUE
       0x0F-0x17   blend 1   ADDITIVE
       0x18-0x19   blend 0   AVERAGE

   THIS COMMENT USED TO SAY "0x00-0x0E average, 0x0F-0x17 additive,
   0x18-0x19 subtractive" -- the first and last were wrong, and the mistake
   matters: on an ADDITIVE page black is the identity element, which is why
   palette entry 0's stored colour is load-bearing there and nowhere else.
   MEASURED: every one of the 105,258 tiles drawing from an additive depth-1
   page, and all 2,287 on an average page, is an fx tile.

2. GRID AND DEPTH. `size_flag` makes a page an 8x8 grid of 32x32 cells
   instead of 16x16 of 16x16. That is a page property, so 8-grid and 16-grid
   cells cannot share a page, and neither can depth-1 and depth-2.

3. THE FX PAIR, and this is the subtle one. A tile that draws from an fx page
   carries ONE u,v for BOTH pages -- FFNx field/background.cpp:199 is the
   line the engine runs:

       page = tile.use_fx_page ? tile.fx_page : tile.page;
       add_page_tile(x, y, z, tile.u, tile.v, tile.palette_index, page);

   So its main cell and its fx cell must sit at the SAME grid coordinate in
   their two pages. The rule that satisfies this without any coupled packing
   is simply: **an fx-paired cell may change PAGE but not COORDINATE.** Then
   both halves keep the coordinate they already agreed on, and no constraint
   can be violated no matter which pages they land in.

WHAT IT DOES NOT DO
===================
It does not renumber anything, delete a page slot, or change the meaning of
`layer2_end_page` (0xCFFE0E). Freed pages are marked ABSENT, which the
page-range walk at x86 0x63A34A already tests for (`page->[0xC]`) before
touching a page, and so does the draw at 0x640213. That is the same freeing
the truecolor repack has been doing.
"""
from __future__ import annotations

import os
import struct

import field_bg_native as FN

T_SRC_X = 10
T_SRC_Y = 12
T_SRC_X_BIG = 42
T_SRC_Y_BIG = 46
T_TEXID = FN.TILE_TEXTURE_ID
T_FX_PAGE = FN.TILE_TEXTURE_ID2
UV_SCALE = 10_000_000

COMPACT_ENV = 'SEVENTH_NX_FIELD_BG_COMPACT'


def enabled():
    """
    On by default. SEVENTH_NX_FIELD_BG_COMPACT=0 turns it off.

    Default-on is the right call because this is not a trade: the output
    holds the same cells with the same bytes drawn by the same tiles with the
    same palettes, in fewer textures. There is no quality axis for it to cost
    anything on.
    """
    if os.environ.get('SEVENTH_NX_FIELD_BG_LEGACY', '').strip().lower() in (
            '1', 'true', 'yes', 'on'):
        return False
    return os.environ.get(COMPACT_ENV, '').strip().lower() not in (
        '0', 'false', 'no', 'off')


def _group(slot):
    for lo, hi, blend in FN.D1_GROUPS:
        if lo <= slot < hi:
            return ('d1', blend)
    for lo, hi, blend in FN.D2_GROUPS:
        if lo <= slot < hi:
            return ('d2', blend)
    return None


def _slots_in_group(g):
    table = FN.D1_GROUPS if g[0] == 'd1' else FN.D2_GROUPS
    for lo, hi, blend in table:
        if blend == g[1]:
            return list(range(lo, hi))
    return []


def _cell_bytes(page, cx, cy, grid):
    """The stored bytes of one cell, exactly as they sit in the page."""
    px = page.px
    side = px // grid
    bpp = page.depth
    stride = px * bpp
    w = side * bpp
    d = page.data
    return b''.join(d[y * stride + cx * side * bpp:
                      y * stride + cx * side * bpp + w]
                    for y in range(cy * side, (cy + 1) * side))


def _write_cell(buf, px, depth, cx, cy, blk, grid):
    side = px // grid
    stride = px * depth
    w = side * depth
    for i, y in enumerate(range(cy * side, (cy + 1) * side)):
        b = y * stride + cx * side * depth
        buf[b:b + w] = blk[i * w:(i + 1) * w]


def _faults(sec9, px):
    """
    ({tile index: what is wrong}, fatal, tile count) for one section.

    `fatal` is set when the section cannot even be walked, which is a
    different thing from a tile being wrong and has to be reported as such.
    """
    try:
        pages, tex_start, _e = FN.parse_texture_block(sec9, px)
    except Exception as exc:                                     # noqa: BLE001
        return {}, 'does not parse: %s' % exc, 0
    pmap = {p.slot: p for p in pages if p is not None}
    try:
        spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    except Exception as exc:                                     # noqa: BLE001
        return {}, 'layer walk failed: %s' % exc, 0
    bad = {}
    for i, off in enumerate(spans):
        slot = sec9[off + T_TEXID]
        p = pmap.get(slot)
        if p is None:
            bad[i] = 'points at absent page %d' % slot
            continue
        grid = 8 if p.size_flag else 16
        u, v = struct.unpack_from('<II', sec9, off + T_SRC_X_BIG)
        cx = int(round(u / UV_SCALE * grid))
        cy = int(round(v / UV_SCALE * grid))
        if not (0 <= cx < grid and 0 <= cy < grid):
            bad[i] = ('resolves to cell (%d,%d) of a %dx%d grid'
                      % (cx, cy, grid, grid))
            continue
        fx = sec9[off + T_FX_PAGE]
        if not fx:
            continue
        q = pmap.get(fx)
        if q is None:
            bad[i] = 'names absent fx page %d' % fx
            continue
        fgrid = 8 if q.size_flag else 16
        if not (0 <= int(round(u / UV_SCALE * fgrid)) < fgrid
                and 0 <= int(round(v / UV_SCALE * fgrid)) < fgrid):
            bad[i] = 'resolves off its fx page %d' % fx
    return bad, None, len(spans)


def self_check(sec9, out, px):
    """
    Reject a compaction that broke the structure, whatever the reason.

    WHY THIS EXISTS. `verify_compact.py` asks "do the pixels match", which is
    the right question for a picture that draws and the WRONG one for a
    picture that does not draw at all. A tile whose `texture_id` names an
    ABSENT page gets handle 0 and 0x66E272 refuses a null handle -- the field
    comes up black, or takes the game down. Pixel identity cannot see that,
    and a build shipped on the strength of it hung on a save load.

    IT COMPARES AGAINST THE INPUT rather than judging the output alone,
    because five fields are already broken this way before anything touches
    them -- `cosmo`, `cosmo2`, `fr_e`, `gaiin_7`, `junair` all have a tile
    naming a missing page slot in VANILLA and in the mod's own chunk.9
    (HANDOFF-52 measured the same five). Failing them would switch this pass
    off for fields whose defect it neither caused nor can fix. Only a fault
    the input did not already have counts.

    Returns None if the output is no worse than the input, or a string.
    """
    a_bad, a_fatal, a_n = _faults(sec9, px)
    b_bad, b_fatal, b_n = _faults(out, px)
    if b_fatal:
        return 'output %s' % b_fatal
    if a_fatal:
        return None                   # cannot compare; do not blame the pass
    if a_n != b_n:
        return 'tile count changed %d -> %d' % (a_n, b_n)
    for i, why in sorted(b_bad.items()):
        if a_bad.get(i) != why:
            return 'tile %d %s (was: %s)' % (i, why, a_bad.get(i) or 'fine')
    return None


class CompactStats:
    def __init__(self):
        self.pages_before = 0
        self.pages_after = 0
        self.cells = 0
        self.cells_merged = 0        # byte-identical, so they share one cell
        self.cells_moved = 0
        self.cells_pinned = 0        # fx-paired: page may change, u,v may not
        self.tiles_rewritten = 0
        self.rejected = None         # self_check said no; the field was left
                                     # exactly as it came in

    @property
    def saved(self):
        return self.pages_before - self.pages_after

    def __bool__(self):
        return self.saved > 0


def compact_section9(sec9, src_px=None, page_px=None):
    """
    Repack one section 9's background pages into the fewest that hold it.

    Returns (new_sec9, CompactStats). The section comes back unchanged when
    nothing can be saved.

    `src_px` is the size DEPTH-2 pages already have (field_bg_native and
    field_bg_repack may have resized them); depth-1 is always 256.
    """
    st = CompactStats()
    d2px = src_px if src_px is not None else (page_px or FN.VANILLA_PX)
    pages, tex_start, tex_end = FN.parse_texture_block(sec9, d2px)
    spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    pmap = {p.slot: p for p in pages if p is not None}
    st.pages_before = len(pmap)
    if not pmap:
        return sec9, st

    # ---- read every tile once
    #
    # `parsed` keeps what was found so the freeze fixpoint below can run
    # without re-reading the section.
    parsed = []                   # (off, slot, cx|None, cy|None, fx_slot)
    frozen_pages = set()
    margin_only = {}              # slot -> {palette, ...} while every tile on
                                  # it is outside the 4:3 picture
    for off in spans:
        slot = sec9[off + T_TEXID]
        fx_slot = sec9[off + T_FX_PAGE]
        # A DEDICATED MARGIN PAGE MUST NOT BE MERGED INTO ANYTHING.
        #
        # `ff7nx_marginpage` moves the 16:9 margin cells onto a page of their
        # own precisely so that page names ONE palette -- a depth-1 page is
        # drawn through a single palette and Cosmos's margin tiles all name 0
        # while their old page-mates named something else, which is what drew
        # the margin through a foreign colour table and turned it yellow.
        #
        # This pass then packed it straight back in. MEASURED on `mds6_3`:
        #
        #     after the split      slot 2  pals {0:120}          PURE
        #     after compaction     slot 1  pals {0:120, 2:75, 3:37}  MIXED AGAIN
        #
        # It buckets by `(group, depth, size_flag, grid)` and palette is not in
        # that tuple, so it merged a palette-0 margin page into a page carrying
        # palettes 2 and 3 and undid the fix one pass earlier in the build.
        #
        # Freezing is the narrow answer: a page every one of whose tiles sits
        # outside the 4:3 picture under ONE palette is a dedicated margin page.
        # There is nothing to gain by merging it -- it is already full of the
        # cells it exists for -- and everything to lose.
        dx = struct.unpack_from('<h', sec9, off + 2)[0]
        pal = sec9[off + 22]
        if -160 < dx + 16 and dx < 160:            # touches the 4:3 picture
            margin_only[slot] = None
        elif margin_only.get(slot, ()) is not None:
            margin_only.setdefault(slot, set()).add(pal)
        p = pmap.get(slot)
        if p is None:
            # Nothing to relocate, but this tile still names an fx page and
            # its u,v is not ours to change -- so that page must not move.
            parsed.append((off, slot, None, None, fx_slot))
            frozen_pages.add(slot)
            continue
        grid = 8 if p.size_flag else 16
        u, v = struct.unpack_from('<II', sec9, off + T_SRC_X_BIG)
        cx = int(round(u / UV_SCALE * grid))
        cy = int(round(v / UV_SCALE * grid))
        if not (0 <= cx < grid and 0 <= cy < grid):
            # A u,v that does not land on a cell is not something this
            # understands, so the page it points at is frozen.
            frozen_pages.add(slot)
            parsed.append((off, slot, None, None, fx_slot))
            continue
        if fx_slot and fx_slot in pmap:
            fq = pmap[fx_slot]
            if (8 if fq.size_flag else 16) != grid:
                # Two pages disagreeing about the grid under one u,v is not a
                # thing this models. Freeze both.
                frozen_pages.add(slot)
                frozen_pages.add(fx_slot)
        if _group(slot) is None:
            frozen_pages.add(slot)
        parsed.append((off, slot, cx, cy, fx_slot))

    # Every tile outside the 4:3 picture, all naming one palette -> dedicated
    # margin page, frozen. See the comment in the parse loop.
    for slot, pals in margin_only.items():
        if pals and len(pals) == 1 and slot in pmap and pmap[slot].depth == 1:
            frozen_pages.add(slot)
            st.margin_pages_frozen = getattr(st, 'margin_pages_frozen', 0) + 1

    # ---- FREEZE FIXPOINT
    #
    # A tile whose MAIN page is frozen keeps its u,v and its texture_id, so
    # the fx_page byte in that record is never rewritten either. If the fx
    # page had moved, the tile would be left pointing at the wrong slot. So
    # freezing propagates main -> fx, and because freezing a page freezes the
    # tiles that draw from it, it has to run to a fixpoint rather than once.
    while True:
        grow = set()
        for off, slot, cx, cy, fx_slot in parsed:
            if fx_slot and fx_slot in pmap and (
                    slot in frozen_pages or slot not in pmap):
                if fx_slot not in frozen_pages:
                    grow.add(fx_slot)
        if not grow:
            break
        frozen_pages |= grow

    # ---- what is referenced, and what is pinned to its coordinate
    refs = {}                     # (slot, cx, cy) -> grid
    pinned = set()
    tiles = []                    # (off, main_ref, fx_ref or None)
    for off, slot, cx, cy, fx_slot in parsed:
        if cx is None or slot in frozen_pages:
            continue
        p = pmap[slot]
        grid = 8 if p.size_flag else 16
        main = (slot, cx, cy)
        refs[main] = grid
        fxr = None
        if fx_slot and fx_slot in pmap and fx_slot not in frozen_pages:
            fxr = (fx_slot, cx, cy)
            refs[fxr] = 8 if pmap[fx_slot].size_flag else 16
            # THE FX PAIR: one u,v, two pages. Keep the coordinate and the
            # constraint cannot be broken -- see the module docstring.
            pinned.add(fxr)
            pinned.add(main)
        elif fx_slot and fx_slot in pmap:
            # fx frozen: it keeps its page AND its coordinate, so the main
            # cell must keep the coordinate too. It may still change page.
            pinned.add(main)
        tiles.append((off, main, fxr))
    st.cells = len(refs)
    st.cells_pinned = len(pinned & set(refs))

    # A present page that no tile references is left exactly where it is.
    # Dropping it would probably be free -- the page-range walk at 0x63A34A
    # tests `present` before touching a page -- but "probably" is not the
    # standard for deleting something, and this pass is supposed to be a
    # relocation, not a garbage collector. Freezing it also stops its slot
    # being reused underneath whatever does reference it.
    touched = {r[0] for r in refs}
    for s in pmap:
        if s not in touched and s not in frozen_pages:
            frozen_pages.add(s)

    # ---- bucket by everything that makes two cells incompatible
    #
    # The grid and the depth are page-wide properties, so a 16x16-grid cell
    # and an 8x8-grid cell cannot share a destination even when their blend
    # group is the same -- and a blend group DOES hold both. That is why
    # `claimed` below is global: two buckets in one group must not both start
    # filling at the group's first slot.
    buckets = {}                  # (group, depth, size_flag, grid) -> [ref]
    for ref, grid in refs.items():
        p = pmap[ref[0]]
        buckets.setdefault((_group(ref[0]), p.depth, p.size_flag, grid),
                           []).append(ref)

    # ---- plan, one bucket at a time
    #
    # Destination slots are the group's own slots, taken in order, so a page
    # that does not move keeps its number wherever possible and the diff
    # stays small. Identical cells merge; pinned cells keep their coordinate;
    # everything else fills what is left.
    remap = {}                    # ref -> (new_slot, ncx, ncy)
    newbuf = {}                   # new_slot -> (bytearray, px, depth, grid)
    claimed = set()               # destination slots already spoken for
    # Tightest bucket first: an 8x8-grid page holds 64 cells against a
    # 16x16's 256, so it has the least room to absorb a slot another bucket
    # borrowed. Neither can actually run out -- every cell came from a page
    # in its own bucket, so `here` is always enough -- but ordering costs
    # nothing and removes the need to rely on that argument.
    for key, group_refs in sorted(buckets.items(),
                                  key=lambda kv: (kv[0][3], repr(kv[0]))):
        (g, depth, size_flag, grid) = key
        cap = grid * grid
        # keep the bucket's own existing pages first, so a page that does not
        # need to move keeps its slot number and the diff stays small
        # ONLY the bucket's OWN slots. Never borrow one the field was not
        # already using.
        #
        # Borrowing looked free -- an unused slot in the same blend group is
        # an unused slot -- but the layers are bounded by page RANGES in the
        # section header (`layer2_end_page`, 0xCFFE0E, bounding the walk at
        # x86 0x63A34A), and this pass does not update them. Moving a cell to
        # a slot outside the range its layer walks would silently stop it
        # being drawn. Staying inside the field's own slots cannot cross a
        # boundary, because every one of those slots is already inside the
        # range that reaches it.
        #
        # It costs nothing: every cell in the bucket came from a page in the
        # bucket, so the bucket's own slots are always enough to hold them.
        here = sorted({r[0] for r in group_refs})
        avail = [s for s in here if s not in claimed]
        if not avail:
            return sec9, CompactStats()
        px = FN.VANILLA_PX if depth == 1 else pmap[group_refs[0][0]].px

        # merge byte-identical cells
        canon = {}
        content = {}
        for ref in sorted(group_refs):
            blk = _cell_bytes(pmap[ref[0]], ref[1], ref[2], grid)
            # A pinned cell can only merge with another cell that wants the
            # same coordinate, so the identity has to include it.
            ident = (blk, (ref[1], ref[2]) if ref in pinned else None)
            first = content.setdefault(ident, ref)
            canon[ref] = first
        st.cells_merged += len(group_refs) - len(content)

        # place: pinned cells first (they have no freedom), then the rest
        order = sorted(content.values(),
                       key=lambda r: (r not in pinned, r))
        # occupancy[slot] = set of taken (cx, cy)
        occupancy = {}
        place = {}
        for ref in order:
            if ref in pinned:
                want = (ref[1], ref[2])
                for s in avail:
                    occ = occupancy.setdefault(s, set())
                    if want not in occ and len(occ) < cap:
                        occ.add(want)
                        place[ref] = (s, want[0], want[1])
                        break
                else:                                  # pragma: no cover
                    return sec9, CompactStats()        # no room: give up
            else:
                for s in avail:
                    occ = occupancy.setdefault(s, set())
                    if len(occ) >= cap:
                        continue
                    spot = next(((x, y) for y in range(grid)
                                 for x in range(grid) if (x, y) not in occ),
                                None)
                    if spot is None:
                        continue
                    occ.add(spot)
                    place[ref] = (s, spot[0], spot[1])
                    break
                else:                                  # pragma: no cover
                    return sec9, CompactStats()

        for ref in group_refs:
            remap[ref] = place[canon[ref]]

        for ref, (s, ncx, ncy) in place.items():
            ent = newbuf.get(s)
            if ent is None:
                ent = newbuf[s] = (bytearray(px * px * depth), px, depth, grid)
            _write_cell(ent[0], px, depth,
                        ncx, ncy, _cell_bytes(pmap[ref[0]], ref[1], ref[2],
                                              grid), grid)
            claimed.add(s)

    if not remap:
        return sec9, st

    # ---- rewrite the tiles
    buf = bytearray(sec9)
    for off, main, fxr in tiles:
        tgt = remap.get(main)
        if tgt is None:
            continue
        new_slot, ncx, ncy = tgt
        grid = refs[main]
        step = UV_SCALE // grid
        if (new_slot, ncx, ncy) != main:
            st.cells_moved += 1
        buf[off + T_TEXID] = new_slot
        struct.pack_into('<II', buf, off + T_SRC_X_BIG, ncx * step, ncy * step)
        buf[off + T_SRC_X] = (ncx * (256 // grid)) & 0xFF
        buf[off + T_SRC_Y] = (ncy * (256 // grid)) & 0xFF
        if fxr is not None:
            ftgt = remap.get(fxr)
            if ftgt is not None:
                fs, fcx, fcy = ftgt
                # The pin guarantees this; assert it rather than trust it,
                # because a violation is invisible until it is on screen.
                if (fcx, fcy) != (ncx, ncy):
                    return sec9, CompactStats()
                buf[off + T_FX_PAGE] = fs
        st.tiles_rewritten += 1

    # ---- emit: every page that received cells, and nothing else
    out_pages = [None] * FN.BG_MAX_PAGES
    for s, (data, px, depth, grid) in newbuf.items():
        old = pmap.get(s)
        size_flag = 1 if grid == 8 else 0
        out_pages[s] = FN.Page(s, size_flag, depth, bytes(data), px)
    for s in frozen_pages:
        if s in pmap and out_pages[s] is None:
            out_pages[s] = pmap[s]
    st.pages_after = sum(1 for p in out_pages if p is not None)
    if st.pages_after >= st.pages_before:
        return sec9, st
    try:
        out = FN.replace_texture_block(bytes(buf), out_pages,
                                       tex_start, tex_end)
    except Exception:                                            # noqa: BLE001
        return sec9, CompactStats()
    why = self_check(sec9, out, d2px)
    if why is not None:
        st.rejected = why
        st.pages_after = st.pages_before
        return sec9, st
    return out, st
