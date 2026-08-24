#!/usr/bin/env python3
"""Restore only the tiny Cosmos-alpha details lost from ``trnad_4`` layer 2.

The dense true-colour conversion deliberately keys transparent vanilla
pixels.  Its sub-unit refinement handles a partially covered native pixel,
but an entire native pixel which is transparent in vanilla and opaque in the
Cosmos layer remains keyed.  At 3x output that is the conspicuous 3x3 square
or stair-step reported on the rock silhouettes.

This is not permission to fill every transparent pixel from the DDS.  The
same layer contains five large, intentional transparent atlas regions.  This
post-pass therefore has an exact field fingerprint and restores only opaque
Cosmos components of at most 13 native pixels which touch already-drawn art.
It changes page texels only: no tile record, UV, palette, layer, animation
byte, page slot, or page count can change.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import diag_common as DC
import field_bg_dense as FD
import field_bg_native as FN
import ff7nx_fieldbg
import ff7nx_marginblack as MB

TARGET = "trnad_4"
OFF_ENV = "SEVENTH_NX_NO_TRNAD_DETAIL"
UV_SCALE = 10_000_000
TILE = 16
SCALE = 3
MAX_COMPONENT = 13
EXPECTED = {
    "units": 867,
    "components": 31,
    "small_units": 47,
    "small_components": 26,
    "large": (36, 133, 184, 215, 252),
}


def disabled():
    return os.environ.get(OFF_ENV, "").strip().lower() in (
        "1", "true", "yes", "on"
    )


def _components(mask):
    """8-connected components in a tiny boolean array, without scipy."""
    seen = np.zeros(mask.shape, bool)
    out = []
    h, w = mask.shape
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or seen[y, x]:
                continue
            seen[y, x] = True
            todo = [(y, x)]
            comp = []
            while todo:
                cy, cx = todo.pop()
                comp.append((cy, cx))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if not (dx or dy):
                            continue
                        ny, nx = cy + dy, cx + dx
                        if (0 <= ny < h and 0 <= nx < w and mask[ny, nx]
                                and not seen[ny, nx]):
                            seen[ny, nx] = True
                            todo.append((ny, nx))
            out.append(comp)
    return out


def _touches_drawn(comp, keyed_unit):
    h, w = keyed_unit.shape
    for y, x in comp:
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not keyed_unit[ny, nx]:
                return True
    return False


def _art_for(opened, provider, slot, pal):
    art = opened(slot, pal)
    if art is None and pal:
        art = opened(slot, 0)
    if art is None:
        for q in sorted(provider.palettes(slot)):
            art = opened(slot, q)
            if art is not None:
                break
    return art


def _cell(sec9, off, pages):
    slot = sec9[off + MB.T_TEX]
    page = pages.get(slot)
    if page is None:
        return None
    grid = 8 if page.size_flag else 16
    step = page.px // grid
    u, v = struct.unpack_from("<II", sec9, off + MB.T_SRCX_BIG)
    cx = int(round(u / UV_SCALE * grid)) * step
    cy = int(round(v / UV_SCALE * grid)) * step
    return slot, cx, cy, step


def apply_to_section9(sec9, provider):
    """Return ``(section9, stats)``; refuse unchanged on any fingerprint drift."""
    st = {"fields": 0, "pages": 0, "cells": 0, "components": 0,
          "units": 0, "texels": 0, "large_units": 0, "refusal": ""}
    if disabled():
        return sec9, st
    try:
        px = ff7nx_fieldbg.page_px()
        plist, tex_start, tex_end = FN.parse_texture_block(sec9, px)
        pages = {p.slot: p for p in plist if p is not None}
        survey = DC.survey(sec9)
        tiles = MB.read_tiles(sec9, survey, pages)
        origin = FD.ORIGIN.get(TARGET, {})
        if not origin:
            raise ValueError("dense origin trail is missing")
        opened = provider.open(TARGET)
    except Exception as exc:
        st["refusal"] = "%s: %s" % (type(exc).__name__, str(exc)[:100])
        return sec9, st

    refs = {}
    for tile in tiles:
        c = _cell(sec9, tile.off, pages)
        if c is not None:
            refs[c[:3]] = refs.get(c[:3], 0) + 1

    plans = []
    sizes = []
    total_units = 0
    try:
        for tile in tiles:
            if tile.layer != 2 or sec9[tile.off + 28] != 0:
                continue
            c = _cell(sec9, tile.off, pages)
            if c is None:
                continue
            slot, cx, cy, step = c
            if step != TILE * SCALE or refs[(slot, cx, cy)] != 1:
                continue
            src = origin.get(tile.off)
            if src is None or len(src) < 4:
                continue
            art = _art_for(opened, provider, src[0], src[3])
            if art is None or art.px != 256 * SCALE:
                continue
            page = pages[slot]
            arr = np.frombuffer(page.data, "<u2").reshape(page.px, page.px)
            block = arr[cy:cy + step, cx:cx + step]
            if block.shape != (step, step):
                continue
            sy, sx = src[2] * SCALE, src[1] * SCALE
            abuf = np.frombuffer(art.buf, "<u2").reshape(art.px, art.px)
            hmask = np.asarray(art.hmask).reshape(art.px, art.px)
            art_block = abuf[sy:sy + step, sx:sx + step]
            hm = hmask[sy:sy + step, sx:sx + step]
            if art_block.shape != block.shape or hm.shape != block.shape:
                continue
            keyed_unit = (block == FN.EMPTY).reshape(
                TILE, SCALE, TILE, SCALE).all(axis=(1, 3))
            art_unit = hm.reshape(
                TILE, SCALE, TILE, SCALE).all(axis=(1, 3))
            candidate = keyed_unit & art_unit
            for comp in _components(candidate):
                sizes.append(len(comp))
                total_units += len(comp)
                if (len(comp) <= MAX_COMPONENT
                        and _touches_drawn(comp, keyed_unit)):
                    plans.append((slot, cx, cy, comp, art_block.copy()))
    except Exception as exc:
        st["refusal"] = "%s: %s" % (type(exc).__name__, str(exc)[:100])
        return sec9, st

    small = [n for n in sizes if n <= MAX_COMPONENT]
    large = tuple(sorted(n for n in sizes if n > MAX_COMPONENT))
    got = {"units": total_units, "components": len(sizes),
           "small_units": sum(small), "small_components": len(small),
           "large": large}
    if got != EXPECTED or len(plans) != EXPECTED["small_components"]:
        st["refusal"] = "unexpected lost-detail fingerprint: %s" % got
        return sec9, st

    arrays = {}
    changed_cells = set()
    for slot, cx, cy, comp, art_block in plans:
        if slot not in arrays:
            p = pages[slot]
            arrays[slot] = np.frombuffer(p.data, "<u2").reshape(
                p.px, p.px).copy()
        dst = arrays[slot][cy:cy + TILE * SCALE,
                           cx:cx + TILE * SCALE]
        for uy, ux in comp:
            ys = slice(uy * SCALE, (uy + 1) * SCALE)
            xs = slice(ux * SCALE, (ux + 1) * SCALE)
            src = art_block[ys, xs]
            if not np.all(src != FN.EMPTY):
                st["refusal"] = "opaque Cosmos unit packed as empty"
                return sec9, st
            if not np.all(dst[ys, xs] == FN.EMPTY):
                st["refusal"] = "planned destination is no longer empty"
                return sec9, st
            dst[ys, xs] = src & np.uint16(0xFFDF)
        changed_cells.add((slot, cx, cy))

    before_records = sec9[:tex_start] + sec9[tex_end:]
    for slot, arr in arrays.items():
        p = pages[slot]
        plist[slot] = FN.Page(slot, p.size_flag, p.depth, arr.tobytes(), p.px)
    out = FN.replace_texture_block(sec9, plist, tex_start, tex_end)
    _p2, out_start, out_end = FN.parse_texture_block(out, px)
    if out[:out_start] + out[out_end:] != before_records:
        st["refusal"] = "non-texture bytes changed"
        return sec9, st

    st.update(fields=1, pages=len(arrays), cells=len(changed_cells),
              components=len(plans), units=EXPECTED["small_units"],
              texels=EXPECTED["small_units"] * SCALE * SCALE,
              large_units=sum(EXPECTED["large"]))
    return out, st


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_a: None):
    import lgp

    total = {"fields": 0, "pages": 0, "cells": 0, "components": 0,
             "units": 0, "texels": 0, "large_units": 0, "refused": []}
    if disabled():
        return total
    provider = getattr(art, "provider", None)
    if provider is None:
        total["refused"].append((TARGET, "raw art provider is unavailable"))
        return total
    entry = archive.index.get(TARGET)
    if entry is None or not archive.is_field(entry):
        total["refused"].append((TARGET, "field missing"))
        return total
    encode = encode or archive.encode_field
    try:
        payload = payloads.get(TARGET)
        raw = (lgp.lzs_decompress(payload[4:]) if payload
               else archive.decompressed(entry))
        parts = list(lgp.split_sections(raw))
        parts[8], one = apply_to_section9(parts[8], provider)
    except Exception as exc:
        total["refused"].append(
            (TARGET, "%s: %s" % (type(exc).__name__, str(exc)[:100])))
        return total
    if one["refusal"]:
        total["refused"].append((TARGET, one["refusal"]))
        return total
    if not one["fields"]:
        return total
    payloads[TARGET] = encode(lgp.join_sections(parts))
    for key in ("fields", "pages", "cells", "components", "units",
                "texels", "large_units"):
        total[key] += one[key]
    return total


def summarise(st):
    if not st or not st.get("fields"):
        return ""
    return (
        "trnad_4 rock detail: restored %d tiny opaque unit(s) (%d texels, "
        "%d component(s), %d cell(s)); preserved %d units in the five large "
        "authored transparent regions. Tile records, UVs, palettes, layers, "
        "animation and page layout unchanged. Set %s=1 to disable."
        % (st["units"], st["texels"], st["components"], st["cells"],
           st["large_units"], OFF_ENV)
    )
