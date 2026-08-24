#!/usr/bin/env python3
"""Restore authored Cosmos art in blank widescreen FX atlas cells.

FFNx/Cosmos can ship multiple DDS images for one paletted FX page.  Some
palette-specific dumps contain only the cells used in the original 4:3
picture, while the page-0/base dump contains the complete atlas.  Widescreen
tile records then legitimately point at cells that are blank in both vanilla
and their palette-specific DDS even though the complete effect exists in a
sibling DDS.

This pass fills only a depth-1 FX cell which is currently all index 0 and is
referenced exclusively by wholly-outside-4:3, blend-1 FX records using one
palette.  A sibling DDS is accepted only when exactly one representable cell
image exists after quantising through that record's own game palette.  The
shipping allowlist contains only fields whose donor meaning has also been
visually proved; build 161 contains ``sinbil_1`` only.  Page slots, records,
UVs, palettes, blend modes, page count, and everything visible inside 4:3
remain byte-identical.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import field_bg_repack as FR
import ff7nx_marginart as MA
import ff7nx_marginblack as MB

OFF_ENV = "SEVENTH_NX_NO_FX_ART"
TILE = 16
HALF_43 = 160
T_DSTX, T_DSTY = 2, 4
T_SRCX, T_SRCY = 10, 12
T_SRCX2, T_SRCY2 = 14, 16
T_PAL = 22
T_USE_FX = 28
T_BLEND_MODE = 30
T_TEX = FN.TILE_TEXTURE_ID
T_FX = FN.TILE_TEXTURE_ID2
MAX_ERROR = 10.0
FIELDS = frozenset({"sinbil_1"})


def disabled():
    return os.environ.get(OFF_ENV, "").strip().lower() in (
        "1", "true", "yes", "on"
    )


def _outside_43(dx):
    return dx + TILE <= -HALF_43 or dx >= HALF_43


def _rgb(page_art):
    value = np.frombuffer(page_art.buf, "<u2").reshape(page_art.px, page_art.px)
    r = ((value >> 11) & 31).astype(np.uint16)
    g = ((value >> 5) & 63).astype(np.uint16)
    b = (value & 31).astype(np.uint16)
    return np.stack(
        ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)),
        -1,
    ).astype(np.uint8)


def _donor_cell(page_art, sx, sy, palette_rgb, rgb_cache=None):
    """A 16x16 indexed cell and its mean error, or None when unpainted."""
    if page_art.px < 256 or page_art.px % 256:
        return None
    scale = page_art.px // 256
    y0, y1 = sy * scale, (sy + TILE) * scale
    x0, x1 = sx * scale, (sx + TILE) * scale
    cache_key = id(page_art)
    if rgb_cache is not None and cache_key in rgb_cache:
        page_rgb = rgb_cache[cache_key]
    else:
        page_rgb = _rgb(page_art)
        if rgb_cache is not None:
            rgb_cache[cache_key] = page_rgb
    rgb = page_rgb[y0:y1, x0:x1]
    if rgb.shape != (TILE * scale, TILE * scale, 3):
        return None
    rgb = np.ascontiguousarray(rgb).reshape(
        TILE, scale, TILE, scale, 3
    ).mean((1, 3))

    alpha = getattr(page_art, "alpha", None)
    if alpha is not None:
        alpha = np.asarray(alpha)[y0:y1, x0:x1]
        cover = np.ascontiguousarray(alpha).reshape(
            TILE, scale, TILE, scale
        ).mean((1, 3)) >= 128
    else:
        mask = np.asarray(page_art.tmask)[y0:y1, x0:x1]
        cover = (~np.ascontiguousarray(mask)).reshape(
            TILE, scale, TILE, scale
        ).mean((1, 3)) >= 0.5
    if not cover.any():
        return None

    idx = MA.quantise(rgb.astype(np.uint8), palette_rgb)
    error = float(
        np.abs(palette_rgb[idx].astype(np.int16) - rgb.astype(np.int16))[cover].mean()
    )
    if error > MAX_ERROR:
        return None
    return np.where(cover, idx, np.uint8(0)).astype(np.uint8), error


def apply_to_section9(name, sec9, palettes565, art):
    """Return ``(new_section9, stats)``; fail closed on every ambiguity."""
    stats = {
        "fields": 0,
        "cells": 0,
        "tiles": 0,
        "no_donor": 0,
        "ambiguous": 0,
        "unrepresentable": 0,
        "names": [],
    }
    if (disabled() or name.lower() not in FIELDS
            or sec9.find(b"BACK") < 0 or not len(palettes565)):
        return sec9, stats
    provider = getattr(art, "provider", None)
    if provider is None:
        return sec9, stats
    try:
        survey = DC.survey(sec9)
        pages_list, tex_start, tex_end = FN.parse_texture_block(
            sec9, survey["page_px"]
        )
        pages = {page.slot: page for page in pages_list if page is not None}
    except Exception:
        return sec9, stats

    # Every reference to a cell participates in the gate, including a page
    # reached as a base texture and an FX record whose initial state is off.
    refs = collections.defaultdict(list)
    for layer, offsets in DC.walk_layers(
        sec9, survey["back_start"], survey["tex_start"]
    ):
        for off in offsets:
            dx = struct.unpack_from("<h", sec9, off + T_DSTX)[0]
            dy = struct.unpack_from("<h", sec9, off + T_DSTY)[0]
            pal = sec9[off + T_PAL]
            base = sec9[off + T_TEX]
            refs[(base, sec9[off + T_SRCX], sec9[off + T_SRCY])].append(
                ("base", layer, dx, dy, pal, off)
            )
            fx = sec9[off + T_FX]
            if fx:
                refs[(fx, sec9[off + T_SRCX2], sec9[off + T_SRCY2])].append(
                    ("fx", layer, dx, dy, pal, off)
                )

    arrays = {
        slot: np.frombuffer(page.data, np.uint8).reshape(256, 256).copy()
        for slot, page in pages.items()
        if page.depth == 1 and not page.size_flag and page.px == 256
    }
    if not arrays:
        return sec9, stats
    palette_rgb = [MA.palette_rgb(row) for row in palettes565]
    opened = provider.open(name)
    changed_slots = set()
    rgb_cache = {}

    for (slot, sx, sy), uses in sorted(refs.items()):
        arr = arrays.get(slot)
        if arr is None or sx % TILE or sy % TILE or sx > 240 or sy > 240:
            continue
        cell = arr[sy : sy + TILE, sx : sx + TILE]
        if np.any(cell):
            continue
        # This write must be structurally incapable of changing 4:3 or a base
        # frame.  The record population, not a renderer, proves that.
        if any(role != "fx" or not _outside_43(dx) for role, _l, dx, _dy, _p, _o in uses):
            continue
        if any(sec9[off + T_BLEND_MODE] != 1 for _r, _l, _x, _y, _p, off in uses):
            continue
        pals = {pal for _r, _l, _x, _y, pal, _o in uses}
        if len(pals) != 1:
            stats["ambiguous"] += 1
            continue
        pal = next(iter(pals))
        if pal >= len(palette_rgb):
            continue

        # Prefer the tile's own DDS when it paints this cell.  Otherwise a
        # sibling may supply missing geometry, but only one distinct indexed
        # answer may survive.  That is what makes the donor choice factual.
        candidates = []
        for donor_pal in sorted(provider.palettes(slot), key=lambda q: (q != pal, q)):
            key = (name.lower(), slot, donor_pal)
            if key in provider.ambiguous_slots:
                continue
            page_art = opened(slot, donor_pal)
            if page_art is None:
                continue
            made = _donor_cell(page_art, sx, sy, palette_rgb[pal], rgb_cache)
            if made is not None:
                candidates.append((donor_pal, made[0], made[1]))
        if not candidates:
            stats["no_donor"] += 1
            continue
        own = [row for row in candidates if row[0] == pal]
        if own:
            chosen = own[0]
        else:
            distinct = {}
            for row in candidates:
                distinct.setdefault(row[1].tobytes(), row)
            if len(distinct) != 1:
                stats["ambiguous"] += 1
                continue
            chosen = next(iter(distinct.values()))
        arr[sy : sy + TILE, sx : sx + TILE] = chosen[1]
        changed_slots.add(slot)
        stats["cells"] += 1
        stats["tiles"] += len(uses)

    if not changed_slots:
        return sec9, stats
    for slot in changed_slots:
        page = pages[slot]
        pages_list[slot] = FN.Page(slot, page.size_flag, page.depth,
                                   arrays[slot].tobytes(), page.px)
    out = FN.replace_texture_block(sec9, pages_list, tex_start, tex_end)
    stats["fields"] = 1
    stats["names"] = [name]
    return out, stats


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_a: None):
    import lgp

    total = {
        "fields": 0,
        "cells": 0,
        "tiles": 0,
        "no_donor": 0,
        "ambiguous": 0,
        "unrepresentable": 0,
        "names": [],
        "refused": [],
    }
    if disabled():
        return total
    encode = encode or archive.encode_field
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            payload = payloads.get(name)
            raw = lgp.lzs_decompress(payload[4:]) if payload else archive.decompressed(entry)
            parts = list(lgp.split_sections(raw))
            cols, _h, _n, _c = MB.palette_colours(parts[3])
            new9, one = apply_to_section9(name, parts[8], cols, art)
        except Exception as exc:
            total["refused"].append((name, "%s: %s" % (type(exc).__name__, str(exc)[:60])))
            continue
        for key in ("fields", "cells", "tiles", "no_donor", "ambiguous", "unrepresentable"):
            total[key] += one[key]
        total["names"].extend(one["names"])
        if new9 == parts[8]:
            continue
        parts[8] = new9
        payloads[name] = encode(lgp.join_sections(parts))
    return total


def summarise(stats):
    if not stats or not stats.get("fields"):
        return ""
    names = ", ".join(stats["names"][:8])
    return (
        "FX MARGIN ART: %d blank additive cell(s), used by %d margin tile(s), "
        "restored from the one representable sibling Cosmos DDS across %d "
        "field(s) (%s). Page count, page slots, records, UVs, palettes and "
        "everything referenced inside 4:3 are unchanged. Set %s=1 to disable."
        % (stats["cells"], stats["tiles"], stats["fields"], names, OFF_ENV)
    )
