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
    TARGET: 3,
}


def disabled():
    values = (os.environ.get(OFF_ENV, ""), os.environ.get(OFF_ENV_ALL, ""))
    return any(v.strip().lower() in ("1", "true", "yes", "on")
               for v in values)


def _same_except_y(record, canonical):
    a, b = bytearray(record), bytearray(canonical)
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
        if (hdr["bg%d_w" % layer], hdr["bg%d_h" % layer],
                hdr["bg%d_speed_x" % layer],
                hdr["bg%d_speed_y" % layer]) != (WIDTH, HEIGHT, 256, 256):
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

    xs = [X0 + i * TILE for i in range(WIDTH // TILE)]
    ys = [Y0 + i * TILE for i in range(HEIGHT // TILE)]
    expected = {(x, y) for y in ys for x in xs}
    canonical = {}
    canonical_order = []
    decoded = []
    for rec in records:
        x = struct.unpack_from("<h", rec, PF.T_DSTX)[0]
        y = struct.unpack_from("<h", rec, PF.T_DSTY)[0]
        decoded.append((x, y, rec))
        if x in xs and y in ys:
            if (x, y) in canonical:
                st["refusal"] = "duplicate authored destination"
                return sec7, sec9, st
            canonical[(x, y)] = rec
            canonical_order.append((x, y))
    if set(canonical) != expected or len(canonical_order) != 88:
        st["refusal"] = "authored 11x8 period not found exactly"
        return sec7, sec9, st

    # The only tolerated extras are the generic vertical fill: exact copies
    # of an authored record at the same x and y modulo the 256-unit period.
    for x, y, rec in decoded:
        if x not in xs:
            st["refusal"] = "unexpected horizontal record"
            return sec7, sec9, st
        cy = Y0 + ((y - Y0) % HEIGHT)
        base = canonical.get((x, cy))
        if base is None or not _same_except_y(rec, base):
            st["refusal"] = "non-identical vertical residue"
            return sec7, sec9, st

    blob = bytearray()
    # FFNx order: original, vertical repeat, horizontal repeat, corner.
    for add_x, add_y in ((0, 0), (0, HEIGHT),
                         (WIDTH, 0), (WIDTH, HEIGHT)):
        for key in canonical_order:
            rec = bytearray(canonical[key])
            x = struct.unpack_from("<h", rec, PF.T_DSTX)[0]
            y = struct.unpack_from("<h", rec, PF.T_DSTY)[0]
            struct.pack_into("<h", rec, PF.T_DSTX, x + add_x)
            struct.pack_into("<h", rec, PF.T_DSTY, y + add_y)
            blob += rec

    out9 = bytearray(sec9)
    out9[first:first + count * PF.TILE_SIZE] = blob
    struct.pack_into("<H", out9, count_at, len(canonical_order) * 4)
    out7 = bytearray(sec7)
    header_at = 0x18 if layer == 3 else 0x1C
    struct.pack_into("<h", out7, header_at, WIDE_WIDTH)
    struct.pack_into("<h", out7, header_at + 2, WIDE_HEIGHT)

    # The new population must stay below the console's binding-page ceiling.
    try:
        import ff7nx_fieldbg
        counts = PC.effective_counts(bytes(out9), ff7nx_fieldbg.page_px())
    except Exception as exc:
        st["refusal"] = "binding census failed: %s" % str(exc)[:80]
        return sec7, sec9, st
    over = {slot: n for slot, n in counts.items()
            if n > PC.MAX_TILES_PER_PAGE}
    if over:
        st["refusal"] = "binding-page cap: %s" % over
        return sec7, sec9, st

    st.update(fields=1, removed=count - len(canonical_order),
              added=len(canonical_order) * 3, tiles=len(canonical_order) * 4,
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
