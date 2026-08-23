#!/usr/bin/env python3
"""
ff7nx_marginpage.py -- give the 16:9 MARGIN its own palette-pure page.

THE PROBLEM, PROVED FROM THE USER'S OWN A/B
===========================================
A depth-1 page gets ONE palette on screen. Which one is not the one each tile
names -- MEASURED on `mds6_3`, whose slot 0 carries interior tiles at palettes
{0: 195, 1: 61} and margin tiles at palette 0 only:

    margin mean RGB if the console applied...   art OFF      art ON
        palette 0                              ( 33,33,16)  ( 37,34,16)
        palette 1                              ( 82,74,41)  (113,106,65)  <- YELLOW
        palette 2                              ( 66,57,24)  ( 45,38,22)
        palette 3                              ( 49,49,24)  ( 74,85,60)

The user reports GREY with the margin pass off and YELLOW with it on. That is
the palette-1 row, both times. So the page is drawn through palette 1 while
Cosmos's margin tiles name palette 0.

A flat filler survives the mismatch -- one index, dark under both. Cosmos's
art does not: 44 indices through a foreign colour table is a bright yellow
block. FFNx never sees this because it binds one texture per `palette_index`
(`gl_replace_texture(texture_set, palette_index, texture)`).

THE FIX
=======
Move the margin cells onto a page whose tiles ALL name one palette. Then
whatever rule the engine uses to pick a single palette for that page, it can
only pick that one, and the art renders exactly as authored.

MEASURED over the shipped archive:

    cells to move                    59,501
    fields affected                     588
    new pages needed                    613     = 1.04 per affected field
    fields with a free low slot         588     <- ALL of them
    fields that would exceed 13 pages      4     gaia_1 15, gaia_2 14,
                                                 gaiin_4 14, las0_2 14

Every affected field has a free slot, so nothing falls back. The four fields
that end up over the ceiling are handled by the no-growth pass exactly as it
already handles 53 others.

WHY A LOW SLOT SPECIFICALLY
---------------------------
`field_load_textures` picks a page's BLEND MODE from its slot index
(field_bg_native.D1_GROUPS): 0x00..0x0E opaque, 0x0F..0x17 additive,
0x18..0x19 average. Margin tiles are opaque, so the new page has to land below
0x0F or the margin would be drawn additively.

WHICH CELLS MOVE
----------------
Only a cell every one of whose tiles is (a) layer 1, (b) wholly outside the
4:3 picture, (c) free of an fx page, and (d) naming one palette. Anything else
is shared with the picture or with an animation and repointing it would break
the other user. Cells are copied, not aliased, so the original page is left
byte-identical -- a tile this pass does not touch cannot notice.

RUNS BEFORE `ff7nx_marginart`, so the fill sees pure pages and stops vetoing
them.
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
GRID = 16                      # 16x16 cells of 16x16 texels on a 256px page
SECTION9 = 8
UV_SCALE = 10_000_000          # field_bg_repack.UV_SCALE
T_SRC_X, T_SRC_Y = 10, 12
T_SRC_X_BIG, T_SRC_Y_BIG = 42, 46
T_USE_FX = 28
T_TEXID = FN.TILE_TEXTURE_ID           # 32
T_FX_PAGE = FN.TILE_TEXTURE_ID2        # 34
LOW_SLOTS = range(0x00, 0x0F)          # the opaque blend band, depth 1


def _mixed_pages(tiles, pages):
    """
    Depth-1 pages that hold margin tiles AND carry more than one palette.

    Every layer counts: on `mds6_3` slot 1 the margin is 39 layer-1 tiles at
    palette 0 and the other 208 are LAYER 2 at palettes 2, 3 and 4. A
    layer-1-only test called that page safe and left the yellow on screen.

    The test used to be "the margin names a palette the rest of the page does
    not", which let through the case where the margin itself spans every
    palette on the page -- MEASURED on `nmkin_5` slot 1, all {2, 3} with the
    margin also {2: 72, 3: 40}, and `gaiin_4` slot 5, all {0, 1, 6, 8} with the
    margin the same four. The console binds ONE palette per page, so those are
    every bit as broken; they simply looked equal to a set comparison.
    """
    marg, allp = {}, {}
    for t in tiles:
        p = pages.get(t.slot)
        if p is None or p.depth != 1:
            continue
        allp.setdefault(t.slot, set()).add(t.pal)
        if t.layer == 1 and t.outside_43:
            marg.setdefault(t.slot, set()).add(t.pal)
    # NARROW ON PURPOSE. Broadening this to "any page carrying more than one
    # palette" is more correct in principle and cost 529 fields their page
    # budget in one build. `nmkin_5` and `gaiin_4` stay unfixed until the
    # split can be made to pay for itself.
    return {s for s, mm in marg.items() if allp.get(s, mm) != mm}


def movable_cells(tiles, pages, sec9):
    """
    {(slot, sx, sy): [tile, ...]} -- cells safe to relocate.

    A cell qualifies only if EVERY tile that samples it is layer 1, outside
    the 4:3 picture, has no fx page, and names the same single palette. One
    tile failing any of those disqualifies the cell, because the copy would
    leave that tile pointing at the old page while its neighbours moved.
    """
    mixed = _mixed_pages(tiles, pages)
    users, bad = {}, set()
    for t in tiles:
        p = pages.get(t.slot)
        if p is None or p.depth != 1 or t.slot not in mixed:
            continue
        key = (t.slot, t.sx, t.sy)
        ok = (t.layer == 1 and t.outside_43
              and not struct.unpack_from('<H', sec9, t.off + T_USE_FX)[0]
              and not sec9[t.off + T_FX_PAGE])
        if not ok:
            bad.add(key)
        users.setdefault(key, []).append(t)
    out = {}
    for key, ts in users.items():
        if key in bad:
            continue
        if len({t.pal for t in ts}) != 1:
            continue
        out[key] = ts
    return out


# WHERE EVERY MOVED CELL CAME FROM.  {field: {(dst_slot, dx, dy): (slot, sx, sy)}}
#
# FINDINGS-150. This split repacks cells onto pages Cosmos never shipped -- a
# margin cell at slot 1 (sx, sy) becomes slot 3 (dx, dy) -- so ANY later pass
# that wants the mod's own art for that cell asks `art_for(3, pal)`, gets None,
# and has no way to tell "the mod ships nothing here" from "this cell was
# moved". `field_bg_dense.hue_broken` scored all 40 of mds5_5's margin sky
# cells 0.0 for exactly this reason, which silently un-did the build-60 fix.
#
# The mapping already exists in `order` below and was simply thrown away.
ORIGIN = {}


def split_section9(sec9, log=None, field=''):
    """
    (new_sec9, stats). Returns the section unchanged when there is nothing to
    do or no free low slot.

    `field` is only used to record ORIGIN; the split itself does not need it.
    """
    st = {'pages': 0, 'cells': 0, 'tiles': 0, 'nofit': 0, 'origin': {}}
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    tiles = MB.read_tiles(sec9, surv, pages)
    cells = movable_cells(tiles, pages, sec9)
    if not cells:
        return sec9, st

    free = [s for s in LOW_SLOTS if s not in pages]
    need = (len(cells) + GRID * GRID - 1) // (GRID * GRID)
    if len(free) < need:
        st['nofit'] = len(cells)
        return sec9, st

    arrays = {s: np.frombuffer(p.data, np.uint8).reshape(256, 256).copy()
              for s, p in pages.items() if p.depth == 1}
    newbuf = {}
    order = [(key, free[i // (GRID * GRID)], i % (GRID * GRID))
             for i, key in enumerate(sorted(cells))]
    step = UV_SCALE // GRID
    buf = bytearray(sec9)
    for key, dst_slot, idx in order:
        slot, sx, sy = key
        if dst_slot not in newbuf:
            newbuf[dst_slot] = np.zeros((256, 256), np.uint8)
            st['pages'] += 1
        cy, cx = divmod(idx, GRID)
        dx, dy = cx * TILE, cy * TILE
        src = arrays.get(slot)
        if src is None:
            continue
        newbuf[dst_slot][dy:dy + TILE, dx:dx + TILE] = \
            src[sy:sy + TILE, sx:sx + TILE]
        for t in cells[key]:
            off = t.off
            buf[off + T_TEXID] = dst_slot
            buf[off + T_SRC_X] = dx & 0xFF
            buf[off + T_SRC_Y] = dy & 0xFF
            struct.pack_into('<II', buf, off + T_SRC_X_BIG,
                             cx * step, cy * step)
            st['tiles'] += 1
        # See ORIGIN. Recorded per CELL, not per tile -- several tiles can
        # share one cell and they all move together.
        st['origin'][(dst_slot, dx, dy)] = (slot, sx, sy)
        st['cells'] += 1

    plist, tex_start, tex_end = FN.parse_texture_block(bytes(buf))
    for slot, arr in newbuf.items():
        plist[slot] = FN.Page(slot, 0, 1, arr.tobytes(), 256)
    out = FN.replace_texture_block(bytes(buf), plist, tex_start, tex_end)
    if field and st['origin']:
        # MERGE, DO NOT REPLACE. FINDINGS-292.
        #
        # `ff7nx_blackcell` runs BEFORE this pass and now records its own
        # copies here, so assigning the dict wholesale would throw that trail
        # away and put its cells back to having no art. Compose while merging:
        # if this split moved a cell blackcell had already copied, the origin
        # that matters is the ORIGINAL page the mod ships art for, not the
        # intermediate one.
        _o = ORIGIN.setdefault(field, {})
        for _dst, _src in st['origin'].items():
            _o[_dst] = _o.get(_src, _src)
    if log:
        log('    margin page split: %d cell(s) -> %d new page(s), %d tile(s) '
            'repointed' % (st['cells'], st['pages'], st['tiles']))
    return out, st


def apply_to_flevel(archive, payloads, encode=None, log=print, fields=None):
    """
    Same contract as `ff7nx_marginart.apply_to_flevel`. MUST run before it.
    """
    import lgp

    st = {'read': 0, 'changed': 0, 'pages': 0, 'cells': 0, 'tiles': 0,
          'nofit': 0, 'refused': []}
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
            parts = lgp.split_sections(raw)
            new9, s = split_section9(parts[SECTION9], field=name)
            st['read'] += 1
            for k in ('pages', 'cells', 'tiles', 'nofit'):
                st[k] += s[k]
            if new9 is parts[SECTION9] or not s['cells']:
                continue
            parts[SECTION9] = new9
            payloads[name] = encode(lgp.join_sections(parts))
            st['changed'] += 1
        except Exception as exc:                                # noqa: BLE001
            st['refused'].append((name, '%s: %s'
                                  % (type(exc).__name__, str(exc)[:60])))
    if st['refused'] and log:
        log('  ! margin page split: %d field(s) not changed (%s)'
            % (len(st['refused']),
               ', '.join('%s: %s' % r for r in st['refused'][:3])))
    return st


def summarise(st):
    if not st or not st.get('read'):
        return ''
    return ('margin page split: %d cell(s) moved onto %d new palette-pure '
            'page(s) across %d field(s), %d tile(s) repointed%s -- the margin '
            'no longer shares a page with tiles of another palette, which is '
            'what drew it through the wrong colour table'
            % (st['cells'], st['pages'], st['changed'], st['tiles'],
               ', %d cell(s) had no free low slot' % st['nofit']
               if st['nofit'] else ''))


if __name__ == '__main__':
    import argparse
    import lgp

    ap = argparse.ArgumentParser(
        description='give the 16:9 margin its own palette-pure page')
    ap.add_argument('flevel', help='the POST-CHUNK.9 archive (make_postchunk9)')
    ap.add_argument('--out')
    ap.add_argument('--fields', nargs='*')
    a = ap.parse_args()
    arc = lgp.Archive(a.flevel)
    pay = {}
    st = apply_to_flevel(arc, pay, fields=a.fields)
    print(summarise(st) or 'nothing to do')
    if a.out:
        arc.replace(pay)
        arc.write(a.out)
        print('wrote %s' % a.out)
