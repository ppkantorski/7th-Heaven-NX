#!/usr/bin/env python3
"""Make exact 352x256 scrolling grids repeat exactly as FFNx does.

The authored lifestream is one seamless 352x256 period (11x8 32-unit tiles).
The generic vertical filler grew it to 352x480 while the engine still wrapped
at 256, creating 77 duplicate residues that collide and form a horizontal
band which moves vertically.

FFNx handles both axes by drawing the original set again at y+256 for uncrop,
at x+352 for widescreen, and at both offsets for the corner. It shifts that
2x2 population against local dimensions 704x512. The port has only the header
dimensions for both scrolling and shifting, so this field encodes those four
identical quadrants as one 704x512 period. Because the art itself repeats every
352x256, the wider remainders select identical pixels; they only prevent the
copies from folding onto each other.

The same authored grid and the same generic-fill collision occur on the Great
Glacier ``hyou*`` set and the six ``move_*`` fields. The correction is limited
to those named fields and still proves the exact authored 11x8 population plus
every generic-fill record byte-for-byte. Irregular grids with intentional
animation duplicates are not candidates.
"""
from __future__ import annotations

import os
import struct

import diag_common as DC
import field_bg_pagecap as PC
import ff7nx_parallaxfill as PF

TARGET = "trnad_4"
PF_T_PARAM = 26
OFF_ENV = "SEVENTH_NX_NO_TRNAD4_REPEAT"
OFF_ENV_ALL = "SEVENTH_NX_NO_EXACT_REPEAT"
TILE = 32
WIDTH = 352
HEIGHT = 256
WIDE_WIDTH = WIDTH * 2
WIDE_HEIGHT = HEIGHT * 2
X0 = -176
Y0 = -128

TARGETS = {
    **{name: 4 for name in (
        "hyou1", "hyou2", "hyou3", "hyou4", "hyou5_1", "hyou5_2",
        "hyou5_4", "hyou6", "hyou7", "hyou8_1", "hyou9", "hyou10",
        "hyou11", "hyou13_1")},
    **{name: 4 for name in (
        "move_d", "move_f", "move_i", "move_r", "move_s", "move_u")},
    "kuro_1": 4,
    "woa_2": 3,
    "woa_3": 3,
    TARGET: 3,
}

# BUILD 618g. kuro_1 (Temple of the Ancients, the purple light drifting over
# the maze) is the same authored 11x8 grid on layer 4 plus TWO animated
# overlay records (param 63 at (-16,-16), palettes 9/10). The overlay made
# the exact-repeat proof refuse it, so ff7nx_parallaxwide tiled it instead
# (808 records over 44 columns, header still 352x256): copies one and two
# periods away fold onto each other through the 352 wrap and whole regions
# of light popped in and out as the layer drifted (hardware video, 10-02).
# The overlay records are carried through the 2x2 repeat like the grid.
# BUILD 618h. woa_2 / woa_3 (the Weapon over the cliff -- the white wind
# haze on layer 3): the same 11x8 grid and two param-63 overlay records, a
# PINNED layer (header speed 0) scrolled right-to-left by BGSCR (+640 x).
# Never treated (speed 0 failed the header test, the overlay the proof), so
# the 427-unit view ran past the 352-unit period on the incoming (right)
# side: haze with empty gaps opening on the right (hardware, 10-02). Exact
# repeat like trnad_4 (also layer 3, also positive speed, confirmed good):
# 704x512, no edge relocation (that is for the negative-speed Glacier).
OVERLAY_TARGETS = frozenset(("kuro_1", "woa_2", "woa_3"))
# kuro_1's camera also travels vertically (range -256..256 => bg.y spans
# about -416..368, against -288..240 on the Glacier): its two highest rows of
# the 512 period are moved one period up, proved like the columns below.
EDGE_MOVE_ROWS = {"kuro_1": 2}

# BUILD 618k. THE SAME GRID, FOUND BY AUDIT INSTEAD OF BY HARDWARE.
# trnad_1, sky, zcoal_1 and zcoal_3 came back from hardware together: black
# bars, gaps and repeated strips in their scrolling layer-3 backdrop. Every
# one is this module's case -- an exact seamless grid that BGSCR scrolls (the
# header speed is 0) -- left out only because TARGETS was a hand-made list.
# FFNx needs no list: it applies the 2x2 repeat to every layer 3/4 of every
# field (background.cpp). ff7nx_paraudit now models every layer 3/4 of the
# archive against the port's wrap and finds exactly seven exact moving grids
# that were untreated, all of which fail 16:9 coverage in the built archive:
# the four reported plus trnad_2 (layer 3, 77% doubled cells), trnad_3
# (layer 4, 384x256) and woa_1 (layer 3, 384x256). All seven join here. Their
# edge relocation is PLANNED per field (`plan_auto_edges`) from the same
# executed model over the field's own reachable bg range, both axes, instead
# of the Glacier's fixed five columns.
AUTO_TARGETS = {"sky": 3, "trnad_1": 3, "trnad_2": 3, "trnad_3": 4,
                "woa_1": 3, "zcoal_1": 3, "zcoal_3": 3}
GRID = {"trnad_3": (384, 256, -192, -128), "woa_1": (384, 256, -192, -128)}
TARGETS.update(AUTO_TARGETS)
OVERLAY_TARGETS = OVERLAY_TARGETS | frozenset(
    ("trnad_1", "trnad_2", "trnad_3", "woa_1"))


def grid_of(field):
    """(W, H, X0, Y0) of the authored period."""
    return GRID.get(field, (WIDTH, HEIGHT, X0, Y0))


def disabled():
    values = (os.environ.get(OFF_ENV, ""), os.environ.get(OFF_ENV_ALL, ""))
    return any(v.strip().lower() in ("1", "true", "yes", "on")
               for v in values)


def _same_except_y(record, canonical):
    a, b = bytearray(record), bytearray(canonical)
    a[PF.T_DSTY:PF.T_DSTY + 2] = b[PF.T_DSTY:PF.T_DSTY + 2]
    return a == b


def _same_except_xy(record, canonical):
    a, b = bytearray(record), bytearray(canonical)
    a[PF.T_DSTX:PF.T_DSTX + 2] = b[PF.T_DSTX:PF.T_DSTX + 2]
    a[PF.T_DSTY:PF.T_DSTY + 2] = b[PF.T_DSTY:PF.T_DSTY + 2]
    return a == b


def apply_to_sections(sec7, sec9, field=TARGET):
    """Return ``(new7, new9, stats)`` or both inputs unchanged on refusal."""
    st = {"fields": 0, "removed": 0, "added": 0, "tiles": 0,
          "refusal": "", "names": []}
    if disabled():
        return sec7, sec9, st
    layer = TARGETS.get(field)
    if layer is None:
        st["refusal"] = "field is not an exact-repeat candidate"
        return sec7, sec9, st
    try:
        hdr = PF.trigger_header(sec7)
        gw, gh, gx0, gy0 = grid_of(field)
        if (hdr["bg%d_w" % layer], hdr["bg%d_h" % layer],
                hdr["bg%d_speed_x" % layer],
                hdr["bg%d_speed_y" % layer]) not in (
                    (gw, gh, 256, 256), (gw, gh, 0, 0)):
            st["refusal"] = "unexpected layer-%d header" % layer
            return sec7, sec9, st
        survey = DC.survey(sec9)
        layers = PF._layers(sec9, survey["back_start"], survey["tex_start"])
        hits = [row for row in layers if row[0] == layer]
        if len(hits) != 1:
            st["refusal"] = "expected one layer-%d array" % layer
            return sec7, sec9, st
        _layer, count_at, first, count = hits[0]
        records = [sec9[first + i * PF.TILE_SIZE:
                        first + (i + 1) * PF.TILE_SIZE] for i in range(count)]
    except Exception as exc:
        st["refusal"] = "%s: %s" % (type(exc).__name__, str(exc)[:80])
        return sec7, sec9, st

    xs = [gx0 + i * TILE for i in range(gw // TILE)]
    ys = [gy0 + i * TILE for i in range(gh // TILE)]
    expected = {(x, y) for y in ys for x in xs}
    canonical = {}
    canonical_order = []
    decoded = []
    overlays = []
    for rec in records:
        x = struct.unpack_from("<h", rec, PF.T_DSTX)[0]
        y = struct.unpack_from("<h", rec, PF.T_DSTY)[0]
        if field in OVERLAY_TARGETS and rec[PF_T_PARAM]:
            if gx0 <= x < gx0 + gw and gy0 <= y < gy0 + gh:
                overlays.append(rec)
            continue
        decoded.append((x, y, rec))
        if x in xs and y in ys:
            if (x, y) in canonical:
                st["refusal"] = "duplicate authored destination"
                return sec7, sec9, st
            canonical[(x, y)] = rec
            canonical_order.append((x, y))
    if set(canonical) != expected or len(canonical_order) != len(expected):
        st["refusal"] = "authored %dx%d period not found exactly" % (
            gw // TILE, gh // TILE)
        return sec7, sec9, st

    # The only tolerated extras are the generic vertical fill: exact copies
    # of an authored record at the same x and y modulo the 256-unit period.
    for x, y, rec in decoded:
        if field in AUTO_TARGETS and not (gx0 <= x < gx0 + gw
                                          and gy0 <= y < gy0 + gh):
            # Fill-pass output outside the authored period (vertical copies,
            # or ff7nx_parallaxfill's pinned-layer edge cells, which sample
            # other cells and are wrong once BGSCR moves the layer). The 2x2
            # repeat replaces all of it.
            continue
        if x not in xs and field not in AUTO_TARGETS:
            st["refusal"] = "unexpected horizontal record"
            return sec7, sec9, st
        cx = gx0 + ((x - gx0) % gw)
        cy = gy0 + ((y - gy0) % gh)
        base = canonical.get((cx, cy))
        if base is None or not _same_except_xy(rec, base):
            st["refusal"] = "non-identical vertical residue"
            return sec7, sec9, st

    if field in OVERLAY_TARGETS:
        if not overlays or len(overlays) > 8:
            st["refusal"] = "overlay records not found"
            return sec7, sec9, st
        ov_keys = [bytes(r) for r in overlays]
        if len(set(ov_keys)) != len(ov_keys):
            st["refusal"] = "duplicate overlay record"
            return sec7, sec9, st
    blob = bytearray()
    sources = [canonical[key] for key in canonical_order] + overlays
    # FFNx order: original, vertical repeat, horizontal repeat, corner.
    for add_x, add_y in ((0, 0), (0, gh), (gw, 0), (gw, gh)):
        for src in sources:
            rec = bytearray(src)
            x = struct.unpack_from("<h", rec, PF.T_DSTX)[0]
            y = struct.unpack_from("<h", rec, PF.T_DSTY)[0]
            struct.pack_into("<h", rec, PF.T_DSTX, x + add_x)
            struct.pack_into("<h", rec, PF.T_DSTY, y + add_y)
            blob += rec

    out9 = bytearray(sec9)
    out9[first:first + count * PF.TILE_SIZE] = blob
    struct.pack_into("<H", out9, count_at, len(sources) * 4)
    out7 = bytearray(sec7)
    header_at = 0x18 if layer == 3 else 0x1C
    struct.pack_into("<h", out7, header_at, 2 * gw)
    struct.pack_into("<h", out7, header_at + 2, 2 * gh)

    # The new population must stay below the console's binding-page ceiling.
    # BUILD 618y: only pages this pass CHANGES are judged. trnad_3 was
    # refused because an unrelated base page (13) already binds 275 tiles in
    # total -- within the per-screen limit (window_over is empty there) --
    # so its scrolling layer never got the repeat and showed gaps on
    # hardware. The page size is read from the section itself.
    try:
        _px = DC.parse_pages(bytes(sec9))[3]
        before = PC.effective_counts(bytes(sec9), _px)
        counts = PC.effective_counts(bytes(out9), _px)
        wover = PC.window_over(bytes(out9), _px)
    except Exception as exc:
        st["refusal"] = "binding census failed: %s" % str(exc)[:80]
        return sec7, sec9, st
    over = {slot: n for slot, n in counts.items()
            if n > PC.MAX_TILES_PER_PAGE and n > before.get(slot, 0)}
    over.update({slot: n for slot, n in wover.items()
                 if counts.get(slot, 0) != before.get(slot, 0)})
    if over:
        st["refusal"] = "binding-page cap: %s" % over
        return sec7, sec9, st

    st.update(fields=1, removed=count - len(sources),
              added=len(sources) * 3, tiles=len(sources) * 4,
              names=[field])
    return bytes(out7), bytes(out9), st


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_a: None):
    import lgp

    total = {"fields": 0, "removed": 0, "added": 0, "tiles": 0,
             "refused": [], "names": []}
    if disabled():
        return total
    encode = encode or archive.encode_field
    for field in sorted(TARGETS):
        entry = archive.index.get(field)
        if entry is None or not archive.is_field(entry):
            total["refused"].append((field, "field missing"))
            continue
        try:
            payload = payloads.get(field)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            new7, new9, one = apply_to_sections(parts[7], parts[8], field)
        except Exception as exc:
            total["refused"].append(
                (field, "%s: %s" % (type(exc).__name__, str(exc)[:80])))
            continue
        if one["refusal"]:
            total["refused"].append((field, one["refusal"]))
            continue
        if not one["fields"]:
            continue
        parts[7], parts[8] = new7, new9
        payloads[field] = encode(lgp.join_sections(parts))
        for key in ("fields", "removed", "added", "tiles"):
            total[key] += one[key]
        total["names"].extend(one["names"])
    return total


def summarise(st):
    if not st or not st.get("fields"):
        return ""
    return (
        "  exact scrolling-grid repeat: %d field(s) (%s), removed %d "
        "colliding generic-fill tile(s), encoded FFNx's 2x2 repeats as "
        "collision-free 704x512 periods (%d tiles total). Pages, UVs, "
        "palettes, animation state, other layers and scroll speeds "
        "unchanged. Set %s=1 to disable."
        % (st["fields"], ", ".join(st["names"]), st["removed"],
           st["tiles"], OFF_ENV_ALL)
    )


# ===========================================================================
# BUILD 617 -- THE GREAT GLACIER LEFT-EDGE BAND.
# ===========================================================================
#
# THE CAUSE, READ OFF THE PORT'S OWN CODE (x86 field_update_background_positions
# as recompiled, ARM +0x9F9114 / +0x9FA92C):
#
#     bg4_pos_x   %= bg4_width * 16                 sdiv + msub  (truncating)
#     t            = bg4_pos_x/16 + bg4_speed_x*dx/256
#     bg.x         = (t % bg4_width) + 320 - field_bg_offset.x - shake
#                                                   sdiv + msub  (truncating)
#
# C's `%` truncates toward zero, so the layer position spans (-W, +W): TWO
# periods, not one. FFNx uses remainder() (+-W/2) and keeps the 352 header
# width for the position while doubling only its local shift width; the port
# has one header word for both. This module wrote 704 into that word, so the
# snow/wind position now sweeps bg.x in (-544, +864) -- and with BGSCR's
# -320 x speed every Glacier cycle spends its last ~37 units at bg.x < -506,
# where the authored stored-x range [-176, 496] leaves the left 16:9 margin
# uncovered after the single wrap. That is the band that scoots in from the
# left once per cycle and vanishes on the wrap (hyou3 and every move_* field).
#
# THE FIX MOVES FIVE EXISTING COLUMNS BY EXACTLY ONE PERIOD. The five highest
# stored columns (x 368..496, 80 records) are written at x - 704. That is the
# same residue modulo the 704 wrap, so the shift helper draws them at the same
# screen positions it did before wherever those were already correct, and the
# art is 352-periodic so every pixel is identical. Executed against the
# layer-4 shift + cull (left 459, right 107, half 213): covered with zero gap
# and zero doubled tile for bg.x in [-666, 1018], which contains the whole
# truncating range (-544, 864) with >= 120 units of margin each side. Before:
# [-506, 1178], i.e. the band.
#
# No tile is added, no page, UV, palette, blend, depth, header or other layer
# changes -- which is what build 616's added columns got wrong (they were put
# on new pages outside the additive band and drew opaque).
#
# Runs as the LAST background pass (after stackorder) so no later pass sees a
# changed dst_x. SEVENTH_NX_NO_GLACIER_EDGE=1 disables it.
EDGE_OFF_ENV = "SEVENTH_NX_NO_GLACIER_EDGE"
EDGE_MOVE_COLS = 5
EDGE_COLS = tuple(X0 + i * TILE for i in range(WIDE_WIDTH // TILE))  # -176..496
EDGE_ROWS = tuple(Y0 + i * TILE for i in range(WIDE_HEIGHT // TILE))  # -128..352
EDGE_MOVE_FROM = frozenset(EDGE_COLS[-EDGE_MOVE_COLS:])               # 368..496
EDGE_MOVED_COLS = tuple(sorted(
    (x - WIDE_WIDTH if x in EDGE_MOVE_FROM else x) for x in EDGE_COLS))
EDGE_TARGETS = frozenset(n for n, layer in TARGETS.items()
                         if layer == 4 and n not in AUTO_TARGETS)
# The port's layer-4 x shift and pick-loop cull, as patched in this build
# (ff7nx_fieldwide PARALLAX_PATCHES + ff7nx_wsclamp pright4a/pright4b).
L4_LEFT, L4_RIGHT, L4_HALF = 459, 107, 213
PIC_LEFT, PIC_RIGHT = -373.5, 53.5          # 16:9 picture, tile.x - bg.x
TRUNC_BG_RANGE = (160 - WIDE_WIDTH + 1, 160 + WIDE_WIDTH - 1)


EDGE_COLS_ENV = "SEVENTH_NX_GLACIER_EDGE_COLS"


def edge_move_cols(field, env=None):
    """Columns moved one period left for `field` (default EDGE_MOVE_COLS).
    BUILD 618h DIAGNOSTIC: SEVENTH_NX_GLACIER_EDGE_COLS=hyou5_2=9,hyou5_1=0
    sets it per field, so one probe build can compare layouts on hardware
    (hyou3 still flashes at the left once per wrap with 5)."""
    raw = (os.environ if env is None else env).get(EDGE_COLS_ENV, "")
    for item in raw.replace(";", ",").split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            if k.strip().lower() == field.lower():
                try:
                    n = int(v)
                except ValueError:
                    break
                if 0 <= n <= 10:
                    return n
    return EDGE_MOVE_COLS


def moved_cols_for(n):
    frm = frozenset(EDGE_COLS[-n:]) if n else frozenset()
    return frm, tuple(sorted((x - WIDE_WIDTH if x in frm else x)
                             for x in EDGE_COLS))


def edge_disabled():
    return (os.environ.get(EDGE_OFF_ENV, "").strip().lower()
            in ("1", "true", "yes", "on"))


def l4_drawn_x(stored, bg, width=WIDE_WIDTH):
    """Where the port draws a layer-4 tile, or None when the cull drops it."""
    x = stored
    if x <= bg - L4_LEFT or x >= bg + L4_RIGHT:
        x += -width if x >= bg - L4_HALF else width
    return x if bg - L4_LEFT < x < bg + L4_RIGHT else None


def edge_coverage(cols, bg):
    """(uncovered units of the 16:9 picture, doubled draws) at one bg.x."""
    drawn = sorted(d for d in (l4_drawn_x(c, bg) for c in cols)
                   if d is not None)
    dup = len(drawn) - len(set(drawn))
    lo, hi = bg + PIC_LEFT, bg + PIC_RIGHT
    cur, gap = lo, 0.0
    for a in drawn:
        if a + TILE <= lo or a >= hi:
            continue
        if a > cur:
            gap += a - cur
        cur = max(cur, a + TILE)
    return gap + max(0.0, hi - cur), dup


def edge_proof(cols, lo=TRUNC_BG_RANGE[0] - 64, hi=TRUNC_BG_RANGE[1] + 64):
    """First failing bg.x in [lo, hi] as (bg, gap, dup), or None."""
    for bg in range(lo, hi + 1):
        gap, dup = edge_coverage(cols, bg)
        if gap or dup:
            return bg, gap, dup
    return None


# Vertical twin of the model above (BUILD 618g, kuro_1 only): the port's
# layer-4 y shift/cull as shipped (ff7nx_wsclamp ptop4 264, pbottom4 16) and
# a 240-unit picture at tile.y - bg.y in [-232, 8]. bg.y = (t % 512) + 232 -
# camera.y; kuro_1's camera range -256..256 lets camera.y reach +-136, and
# its scroll is downward-only (BGSCR y < 0), so bg.y spans about -416..368.
L4_TOP, L4_BOTTOM = 264, 16
PIC_TOP, PIC_BOTTOM = -232, 8
VERT_BG_RANGE = {"kuro_1": (-416 - 32, 368 + 32)}


def l4_drawn_y(stored, bg, height=WIDE_HEIGHT):
    y = stored
    if y <= bg - L4_TOP or y >= bg + L4_BOTTOM:
        y += -height if y >= bg + L4_BOTTOM else height
    return y if bg - L4_TOP < y < bg + L4_BOTTOM else None


def edge_coverage_y(rows, bg):
    drawn = sorted(d for d in (l4_drawn_y(r, bg) for r in rows)
                   if d is not None)
    dup = len(drawn) - len(set(drawn))
    lo, hi = bg + PIC_TOP, bg + PIC_BOTTOM
    cur, gap = lo, 0.0
    for a in drawn:
        if a + TILE <= lo or a >= hi:
            continue
        if a > cur:
            gap += a - cur
        cur = max(cur, a + TILE)
    return gap + max(0.0, hi - cur), dup


def edge_proof_y(rows, lo, hi):
    for bg in range(lo, hi + 1):
        gap, dup = edge_coverage_y(rows, bg)
        if gap or dup:
            return bg, gap, dup
    return None


def relocate_edge_sections(sec7, sec9, field):
    """Return ``(new9, stats)``; the section is unchanged on any refusal."""
    st = {"fields": 0, "tiles": 0, "refusal": "", "names": []}
    if edge_disabled() or field not in EDGE_TARGETS:
        return sec9, st
    try:
        hdr = PF.trigger_header(sec7)
        if (hdr["bg4_w"], hdr["bg4_h"], hdr["bg4_speed_x"]) != (
                WIDE_WIDTH, WIDE_HEIGHT, 256):
            st["refusal"] = "layer-4 header is not the 704x512 exact repeat"
            return sec9, st
        survey = DC.survey(sec9)
        hits = [row for row in PF._layers(sec9, survey["back_start"],
                                          survey["tex_start"]) if row[0] == 4]
        if len(hits) != 1:
            st["refusal"] = "expected one layer-4 array"
            return sec9, st
        _layer, _count_at, first, count = hits[0]
    except Exception as exc:                                   # noqa: BLE001
        st["refusal"] = "%s: %s" % (type(exc).__name__, str(exc)[:80])
        return sec9, st
    offs_all = [first + i * PF.TILE_SIZE for i in range(count)]
    offs = [o for o in offs_all
            if not (field in OVERLAY_TARGETS and sec9[o + PF_T_PARAM])]
    xy = [struct.unpack_from("<hh", sec9, o + PF.T_DSTX) for o in offs]
    cols = tuple(sorted({x for x, _y in xy}))
    ncols = edge_move_cols(field)
    move_from, moved_cols = moved_cols_for(ncols)
    if ncols and cols == moved_cols:
        return sec9, st                          # already relocated
    if ncols != EDGE_MOVE_COLS:
        bad = edge_proof(moved_cols)
        if ncols and bad is not None:
            st["refusal"] = ("coverage proof failed for %d columns at bg.x "
                             "%d" % (ncols, bad[0]))
            return sec9, st
    if cols != EDGE_COLS:
        st["refusal"] = "layer-4 columns are not the exact 22-column grid"
        return sec9, st
    if (len(xy) != len(EDGE_COLS) * len(EDGE_ROWS)
            or set(xy) != {(x, y) for x in EDGE_COLS for y in EDGE_ROWS}):
        st["refusal"] = "layer-4 grid is not complete and unique"
        return sec9, st
    nrows = EDGE_MOVE_ROWS.get(field, 0)
    rows_from = frozenset(EDGE_ROWS[-nrows:]) if nrows else frozenset()
    if nrows:
        moved_rows = tuple(sorted((y - WIDE_HEIGHT if y in rows_from else y)
                                  for y in EDGE_ROWS))
        bad = edge_proof_y(moved_rows, *VERT_BG_RANGE.get(field, (0, 0)))
        if bad is not None:
            st["refusal"] = ("vertical coverage proof failed at bg.y %d "
                             "(gap %.1f, dup %d)" % bad)
            return sec9, st
    out = bytearray(sec9)
    moved = 0
    for o in offs_all:
        x, y = struct.unpack_from("<hh", sec9, o + PF.T_DSTX)
        if x in move_from:
            struct.pack_into("<h", out, o + PF.T_DSTX, x - WIDE_WIDTH)
            moved += 1
        if y in rows_from:
            struct.pack_into("<h", out, o + PF.T_DSTY, y - WIDE_HEIGHT)
            moved += 1
    st.update(fields=1, tiles=moved, names=[field])
    return bytes(out), st


def apply_edge_to_flevel(archive, payloads, encode=None,
                         log=lambda *_a: None):
    import lgp

    total = {"fields": 0, "tiles": 0, "refused": [], "names": []}
    if edge_disabled():
        return total
    bad = edge_proof(EDGE_MOVED_COLS)
    if bad is not None:                 # the layout itself must prove out
        total["refused"].append(("*", "coverage proof failed at bg.x %d "
                                 "(gap %.1f, dup %d)" % bad))
        return total
    encode = encode or archive.encode_field
    for field in sorted(EDGE_TARGETS | frozenset(AUTO_TARGETS)):
        entry = archive.index.get(field)
        if entry is None or not archive.is_field(entry):
            total["refused"].append((field, "field missing"))
            continue
        try:
            payload = payloads.get(field)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            if field in AUTO_TARGETS:
                new9, one = relocate_auto_sections(parts[7], parts[8], field,
                                                   parts[0])
            else:
                new9, one = relocate_edge_sections(parts[7], parts[8], field)
        except Exception as exc:                               # noqa: BLE001
            total["refused"].append(
                (field, "%s: %s" % (type(exc).__name__, str(exc)[:80])))
            continue
        if one["refusal"]:
            total["refused"].append((field, one["refusal"]))
            continue
        if not one["fields"]:
            continue
        parts[8] = new9
        payloads[field] = encode(lgp.join_sections(parts))
        total["fields"] += 1
        total["tiles"] += one["tiles"]
        total["names"].extend(one["names"])
    return total


def summarise_edge(st):
    if not st or not st.get("fields"):
        return ""
    return ("  Great Glacier snow/wind left edge (BUILD 617): %d field(s), "
            "%d record(s) of the 5 highest layer-4 columns moved by exactly "
            "one 704-unit period; the truncating layer position (-544..864) "
            "is now covered with no gap and no doubled tile. No tile, page, "
            "UV, palette or header added or changed. Set %s=1 to disable."
            % (st["fields"], st["tiles"], EDGE_OFF_ENV))


# ===========================================================================
# BUILD 618k -- PLANNED EDGE RELOCATION FOR THE AUTO TARGETS (both layers).
# ===========================================================================
# The port's shift, as shipped (ff7nx_fieldwide + ff7nx_wsclamp, the same
# constants ff7nx_paraudit models): x -- once, by -+P if x <= bg-459 or
# x >= bg+107, direction x >= bg-213; layer 4 then culls to bg-459 < x <
# bg+107, layer 3 has no cull. y -- once, by -+P if y <= bg-264 or y >=
# bg+16; direction y >= bg-120 on layer 3, y >= bg+16 on layer 4.
# Picture: tile - bg in [-373.5, 53.5] x [-232, 8]. For each axis the planner
# moves the n highest (or lowest) stored columns/rows by exactly one period
# -- the same residue, identical pixels -- and keeps the smallest n whose
# coverage has no gap and no doubled tile at EVERY integer bg in the field's
# reachable range (header speed x camera range, every BGSCR the scripts
# issue, the truncating modulo) widened by AUTO_MARGIN.
AUTO_MARGIN = 24
L3_HALF_Y = 120


def _drawn_axis(v, bg, period, layer, axis):
    if axis == "x":
        if v <= bg - L4_LEFT or v >= bg + L4_RIGHT:
            v += -period if v >= bg - L4_HALF else period
        if layer == 4 and not (bg - L4_LEFT < v < bg + L4_RIGHT):
            return None
        return v
    if v <= bg - L4_TOP or v >= bg + L4_BOTTOM:
        down = (v >= bg - L3_HALF_Y) if layer == 3 else (v >= bg + L4_BOTTOM)
        v += -period if down else period
    return v


def axis_fail(vals, period, layer, axis, lo, hi):
    """First failing bg in [lo, hi] as (bg, gap, dup), or None."""
    plo, phi = (PIC_LEFT, PIC_RIGHT) if axis == "x" else (PIC_TOP, PIC_BOTTOM)
    for bg in range(int(lo), int(hi) + 1):
        drawn = sorted(d for d in (_drawn_axis(v, bg, period, layer, axis)
                                   for v in vals) if d is not None)
        dup = len(drawn) - len(set(drawn))
        a0, a1 = bg + plo, bg + phi
        cur, gap = a0, 0.0
        for a in drawn:
            if a + TILE <= a0 or a >= a1:
                continue
            if a > cur:
                gap += a - cur
            cur = max(cur, a + TILE)
        gap += max(0.0, a1 - cur)
        if gap or dup:
            return bg, gap, dup
    return None


def plan_axis(vals, period, layer, axis, lo, hi):
    """(moved_from, delta, new_vals) with the fewest moves, or None."""
    vals = tuple(sorted(vals))
    for n in range(0, len(vals) // 2 + 1):
        for delta in ((-period, period) if n else (0,)):
            frm = (frozenset(vals[-n:]) if delta < 0 else
                   frozenset(vals[:n])) if n else frozenset()
            new = tuple(sorted(v + delta if v in frm else v for v in vals))
            if axis_fail(new, period, layer, axis, lo, hi) is None:
                return frm, delta, new
    return None


def auto_ranges(sec7, field, script):
    """((bgx_lo, bgx_hi), (bgy_lo, bgy_hi)) after the 2x2 header change."""
    import ff7nx_paraudit as PA
    layer = AUTO_TARGETS[field]
    hdr = PF.trigger_header(sec7)
    ops = PA.bgscr_ops(script).get(layer, []) if script else [(None, None)]
    rx, ry, _moves = PA.reach(hdr, layer, ops)
    return ((rx[0] - AUTO_MARGIN, rx[1] + AUTO_MARGIN),
            (ry[0] - AUTO_MARGIN, ry[1] + AUTO_MARGIN))


def plan_auto_edges(sec7, sec9, field, script=None):
    """Plan for one AUTO target: dict, or raise ValueError."""
    layer = AUTO_TARGETS[field]
    gw, gh, gx0, gy0 = grid_of(field)
    hdr = PF.trigger_header(sec7)
    if (hdr["bg%d_w" % layer], hdr["bg%d_h" % layer]) != (2 * gw, 2 * gh):
        raise ValueError("layer-%d header is not the %dx%d exact repeat"
                         % (layer, 2 * gw, 2 * gh))
    survey = DC.survey(sec9)
    hits = [row for row in PF._layers(sec9, survey["back_start"],
                                      survey["tex_start"]) if row[0] == layer]
    if len(hits) != 1:
        raise ValueError("expected one layer-%d array" % layer)
    _layer, _count_at, first, count = hits[0]
    offs_all = [first + i * PF.TILE_SIZE for i in range(count)]
    offs = [o for o in offs_all
            if not (field in OVERLAY_TARGETS and sec9[o + PF_T_PARAM])]
    xy = [struct.unpack_from("<hh", sec9, o + PF.T_DSTX) for o in offs]
    cols = tuple(gx0 + i * TILE for i in range(2 * gw // TILE))
    rows = tuple(gy0 + i * TILE for i in range(2 * gh // TILE))
    if (len(xy) != len(cols) * len(rows)
            or set(xy) != {(x, y) for x in cols for y in rows}):
        raise ValueError("layer-%d grid is not the complete %dx%d repeat"
                         % (layer, len(cols), len(rows)))
    rx, ry = auto_ranges(sec7, field, script)
    px = plan_axis(cols, 2 * gw, layer, "x", *rx)
    py = plan_axis(rows, 2 * gh, layer, "y", *ry)
    if px is None or py is None:
        raise ValueError("no relocation covers bg %s" % (
            ("x %d..%d" % rx) if px is None else ("y %d..%d" % ry)))
    return {"offs": offs_all, "x": px, "y": py, "rx": rx, "ry": ry}


def relocate_auto_sections(sec7, sec9, field, script=None):
    """Return ``(new9, stats)``; unchanged on refusal or when nothing moves."""
    st = {"fields": 0, "tiles": 0, "refusal": "", "names": []}
    if edge_disabled() or field not in AUTO_TARGETS:
        return sec9, st
    try:
        plan = plan_auto_edges(sec7, sec9, field, script)
    except Exception as exc:                                   # noqa: BLE001
        st["refusal"] = str(exc)[:100]
        return sec9, st
    (fx, dx, _), (fy, dy, _) = plan["x"], plan["y"]
    out = bytearray(sec9)
    moved = 0
    for o in plan["offs"]:
        x, y = struct.unpack_from("<hh", sec9, o + PF.T_DSTX)
        if x in fx:
            struct.pack_into("<h", out, o + PF.T_DSTX, x + dx)
            moved += 1
        if y in fy:
            struct.pack_into("<h", out, o + PF.T_DSTY, y + dy)
            moved += 1
    if moved:
        st.update(fields=1, tiles=moved, names=[field])
    return bytes(out), st
