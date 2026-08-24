#!/usr/bin/env python3
"""
ff7nx_fxedge.py -- the lighting stops at the 4:3 edge while the art goes on.

THE REPORT, from hardware on `sinbil_1` (Shinra HQ lobby): the teal glow in
the top corners has a hard vertical edge partway out, and past it the same
surface continues unlit.

WHAT IT IS
==========
Cosmos widened the BASE art to +/-224 and the LIGHTING only part of the way.
The tile map says it plainly -- `.` is base art, `F` is a tile carrying an FX
page, and the bars are the 4:3 picture edges:

        |4:3                |4:3
 -120 FFFFFFF................F....      <- the right glow ENDS at x = 144
 -104 FFFFFFF.....................
  -88 FFFFFFF.....................      <- the left glow reaches x = -224
  -40 FFFF.......FFFFFFF..........

x = 144 is the LAST column inside the 4:3 frame. So the right-hand glow was
truncated exactly at the old picture edge and never widened, while the base
art it sits on runs out to 208. Every pixel past x = 160 shows the surface
with no light on it, and the boundary is the straight line in the screenshot.

The left glow was widened, which is what makes the field look asymmetric.

WHAT THIS DOES
==============
Per ROW, and only where the truncation signature is present:

  * the row's outermost FX tile sits at the last column inside 4:3;
  * base art continues outward past it.

Then the FX tile is repeated outward, one 16-unit column at a time, for as
long as base art is there to light. Only `dst_x` changes -- page, uv, palette,
blend mode and animation group are the source record's, byte for byte.

WHY REPEATING THE EDGE IS THE RIGHT SHAPE HERE
==============================================
This is `ff7nx_parallaxfill.plan_layer_edge_x`'s rule one layer up: extend the
edge, never tile the layer. A glow that reaches the frame edge is a gradient
running off the picture, and continuing it is what the artist would have done
had they widened it. Nothing is copied INWARD, so no interior lighting moves.

And the blend makes it safe in one direction: these are additive tiles over a
region that currently has NO light at all, so the arm can only ever brighten
what is already too dark. It cannot darken and it cannot cover.

A row whose FX stops SHORT of the 4:3 edge is a shaped feature -- a lamp, a
sign, a shaft -- that is meant to end where it ends, and it is left alone.

SEVENTH_NX_NO_FX_EDGE=1 turns this off.
"""
from __future__ import annotations

import os
import struct

import diag_common as DC
import field_bg_pagecap as PC

# ---- DEFAULT OFF. IT DID NOT FIX WHAT IT WAS WRITTEN FOR. FINDINGS-298.
#
# Build 159 shipped this on and `sinbil_1` was unchanged on hardware. The
# diagnosis was wrong: measured on the shipped section, the left glow ALREADY
# has FX tiles out to x -224, so nothing was missing in the margin. The
# strongest vertical step in the top band is at dst x -113, INSIDE the 4:3
# picture -- it is the right-hand edge of the additive wash block, not a
# margin gap.
#
# What the arm does do is repeat an edge tile outward, and every FX tile in
# this field names the SAME cell (page 15 cell 0,0) tinted by palette, so a
# repeat is a flat band of the edge colour. That is a risk on the 95 fields it
# touched and a benefit on none that has been demonstrated. Off until the real
# cause is found; `SEVENTH_NX_FX_EDGE=1` turns it back on for comparison.
ON_ENV = 'SEVENTH_NX_FX_EDGE'
OFF_ENV = 'SEVENTH_NX_NO_FX_EDGE'

TILE_SIZE = 52
T_DSTX = DC.TILE_DST_X
T_DSTY = DC.TILE_DST_Y
T_TEXID = 32
T_FX_PAGE = 34
GRID = 16

# The 4:3 picture in tile-x. `screen(tile.x) = tile.x + 160` for a pinned
# layer, and the 4:3 viewport is screen 0..320, so the picture is
# tile.x -160..160 and the last column INSIDE it starts at 144.
PIC_LO = -160
PIC_HI = 160
LAST_IN = PIC_HI - GRID          # 144
FIRST_IN = PIC_LO                # -160

# One column of glow is 16 units; the widescreen margin is 53.5 each side, so
# four columns covers it with one to spare. Bounded so a field with a strange
# base extent cannot run away.
MAX_COLS = 6


def disabled():
    if os.environ.get(OFF_ENV) == '1':
        return True
    return os.environ.get(ON_ENV) != '1'


def _layers(sec9, back, tex):
    import ff7nx_parallaxfill as PF
    return PF._layers(sec9, back, tex)


def plan(sec9):
    """[(source_offset, new_dst_x)] -- the FX tiles to repeat outward."""
    surv = DC.survey(sec9)
    layers = _layers(sec9, surv['back_start'], surv['tex_start'])
    base = {}
    fx = {}
    meta = {}
    for layer, _count_at, first, n in layers:
        if layer > 2:
            continue
        for i in range(n):
            o = first + i * TILE_SIZE
            x = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
            y = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
            if abs(x) > 1000:
                continue
            if sec9[o + T_FX_PAGE]:
                fx.setdefault(y, {})[x] = o
                meta[(x, y)] = o
            else:
                base.setdefault(y, set()).add(x)
    add = []
    for y, cols in fx.items():
        b = base.get(y)
        if not b:
            continue
        xs = sorted(cols)
        # ---- RIGHT. The signature is "ends exactly at the 4:3 edge".
        if xs[-1] == LAST_IN:
            src = cols[xs[-1]]
            for k in range(1, MAX_COLS + 1):
                x2 = LAST_IN + k * GRID
                if x2 not in b or x2 in cols:
                    break
                add.append((src, x2))
        # ---- LEFT, the same test mirrored.
        if xs[0] == FIRST_IN:
            src = cols[xs[0]]
            for k in range(1, MAX_COLS + 1):
                x2 = FIRST_IN - k * GRID
                if x2 not in b or x2 in cols:
                    break
                add.append((src, x2))
    return add


def apply_to_section9(sec9, px=None, field_name=None):
    """(new_sec9, stats). Unchanged when no row shows the signature."""
    st = {'fields': 0, 'tiles': 0, 'rows': 0, 'capped': 0}
    if disabled() or sec9.find(b'BACK') < 0:
        return sec9, st
    try:
        add = plan(sec9)
    except Exception:                                          # noqa: BLE001
        return sec9, st
    if not add:
        return sec9, st

    # ---- THE BINDING CAP, WHICH IS THE ONE THE CONSOLE MAKES.
    # `field_bg_pagecap.effective_counts`: a tile carrying an fx page binds the
    # FX page. Every record this pass adds carries one, so the whole cost
    # lands on that page and nowhere else.
    try:
        counts = PC.effective_counts(sec9, px)
    except Exception:                                          # noqa: BLE001
        counts = {}
    # ---- A ROW IS EXTENDED IN FULL OR NOT AT ALL.
    #
    # Budgeting tile by tile spends the last of a page on half a row, and half
    # a row does not close the discontinuity -- it MOVES it to an arbitrary
    # column, which is worse to look at than the honest edge. Same discipline
    # as `plan_layer_edge_x`, which accepts or refuses a whole column.
    by_row = {}
    for o, x2 in add:
        y = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
        by_row.setdefault((y, x2 > 0), []).append((o, x2))
    room = {}
    keep = []
    for _key, group in sorted(by_row.items()):
        need = {}
        for o, _x in group:
            b = sec9[o + T_FX_PAGE] or sec9[o + T_TEXID]
            need[b] = need.get(b, 0) + 1
        if any(room.get(b, counts.get(b, 0)) + n > PC.MAX_TILES_PER_PAGE
               for b, n in need.items()):
            st['capped'] += len(group)
            continue
        for b, n in need.items():
            room[b] = room.get(b, counts.get(b, 0)) + n
        keep += group
    if not keep:
        return sec9, st

    surv = DC.survey(sec9)
    layers = _layers(sec9, surv['back_start'], surv['tex_start'])
    buf = bytearray(sec9)
    # Rebuild back to front so the offsets stay valid.
    for layer, count_at, first, n in sorted(layers, reverse=True):
        end = first + n * TILE_SIZE
        mine = [(o, x2) for o, x2 in keep if first <= o < end]
        if not mine:
            continue
        blob = bytearray()
        for o, x2 in mine:
            rec = bytearray(sec9[o:o + TILE_SIZE])
            struct.pack_into('<h', rec, T_DSTX, int(x2))
            blob += rec
        buf[end:end] = blob
        struct.pack_into('<H', buf, count_at, n + len(mine))
    st['fields'] = 1
    st['tiles'] = len(keep)
    st['rows'] = len({struct.unpack_from('<h', sec9, o + T_DSTY)[0]
                      for o, _x in keep})
    return bytes(buf), st


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_a: None,
                    px=None):
    import lgp
    encode = encode or (lambda raw: archive.encode_field(raw))
    stats = {'fields': 0, 'tiles': 0, 'rows': 0, 'capped': 0, 'worst': []}
    if disabled():
        return stats
    if px is None:
        import ff7nx_fieldbg
        px = ff7nx_fieldbg.page_px()
    for nm in archive.names():
        e = archive.index.get(nm)
        if e is None or not archive.is_field(e):
            continue
        try:
            payload = payloads.get(nm)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(e))
            parts = list(lgp.split_sections(raw))
        except Exception:                                      # noqa: BLE001
            continue
        try:
            new9, st = apply_to_section9(parts[8], px, nm)
        except Exception:                                      # noqa: BLE001
            continue
        if not st['tiles']:
            continue
        parts[8] = new9
        payloads[nm] = encode(lgp.join_sections(parts))
        for k in ('fields', 'tiles', 'rows', 'capped'):
            stats[k] += st[k]
        stats['worst'].append((st['tiles'], nm))
    stats['worst'].sort(reverse=True)
    return stats


def summarise(stats):
    if not stats.get('fields'):
        return ''
    worst = ', '.join('%s +%d' % (n, c) for c, n in stats['worst'][:4])
    return (
        '  FX EDGE: %s lighting tile(s) on %d row(s) across %d field(s) were '
        'repeated outward into the widescreen margin. Cosmos widened the BASE '
        'art to +/-224 on these fields and the LIGHTING only part of the way, '
        'so the surface continues past the old 4:3 edge with no light on it '
        'and the boundary is a straight vertical line. MEASURED on sinbil_1, '
        'the Shinra lobby: its left glow reaches x -224 and its right glow '
        'ends at x 144 -- the last column INSIDE the 4:3 frame -- against '
        'base art that runs to 208. Scoped by that signature per ROW: the '
        'row\'s outermost FX tile sits exactly at the 4:3 edge AND base art '
        'continues past it. A row whose lighting stops short of the edge is '
        'a lamp or a shaft that is meant to end there and is left alone. '
        'Only dst_x changes; page, uv, palette, blend and animation group are '
        'the source record byte for byte, and nothing is copied inward so no '
        'interior lighting moves. SAFE IN ONE DIRECTION: these are additive '
        'tiles over a region with no light at all, so the arm can only '
        'brighten what is already too dark -- it cannot darken and it cannot '
        'cover. Budgeted against the 256 BINDING tiles per page that '
        'field_bg_pagecap calls the count the console makes. Biggest: %s. '
        'Set %s=1 to disable.'
        % (f"{stats['tiles']:,}", stats['rows'], stats['fields'], worst,
           OFF_ENV))
