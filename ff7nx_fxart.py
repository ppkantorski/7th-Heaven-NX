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


def apply_to_section9(name, sec9, palettes565, art, animated=frozenset()):
    """Return ``(new_section9, stats)``; fail closed on every ambiguity.

    BUILD 581: beyond the proved FIELDS, a blank margin cell whose records
    draw with a SCRIPT-ANIMATED palette (``animated``, ff7nx_palanim) is also
    filled. Those pages now stay paletted so their glow keeps pulsing
    (ff7nx_fxpages' animation veto), and this is the only way their Cosmos
    widescreen cells get art. For them the donor is exactly the DDS FFNx
    loads -- the record's own palette, else palette 0 -- never a sibling.
    """
    stats = {
        "fields": 0,
        "cells": 0,
        "tiles": 0,
        "no_donor": 0,
        "ambiguous": 0,
        "unrepresentable": 0,
        "names": [],
    }
    proved = name.lower() in FIELDS
    live = frozenset(animated) if not proved else frozenset()
    if (disabled() or not (proved or live)
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

    # Pages some record draws through a script-animated palette: exactly the
    # pages ff7nx_fxpages now keeps paletted. Their margin records often name
    # a palette of their own (Cosmos authored past the table's end and
    # ff7nx_palrange re-seated them), so admission is by PAGE, not record.
    live_slots = {key[0] for key, uses in refs.items()
                  if any(role == "fx" and p in live
                         for role, _l, _x, _y, p, _o in uses)}
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
        if not proved and slot not in live_slots:
            continue

        # Prefer the tile's own DDS when it paints this cell.  Otherwise a
        # sibling may supply missing geometry, but only one distinct indexed
        # answer may survive.  That is what makes the donor choice factual.
        candidates = []
        donors = sorted(provider.palettes(slot), key=lambda q: (q != pal, q))
        if not proved:
            have = set(donors)
            donors = [pal] if pal in have else ([0] if 0 in have else [])
        for donor_pal in donors:
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
            import ff7nx_palanim
            new9, one = apply_to_section9(
                name, parts[8], cols, art,
                animated=ff7nx_palanim.animated_palettes(parts[0]))
        except Exception as exc:
            total["refused"].append((name, "%s: %s" % (type(exc).__name__, str(exc)[:60])))
            continue
        for key in ("fields", "cells", "tiles", "no_donor", "ambiguous", "unrepresentable"):
            total[key] += one[key]
        total["names"].extend(one["names"])
        # BUILD 617: the same repair for the margin cells the two gates above
        # cannot reach (see apply_margin_617), on the section just produced.
        try:
            import ff7nx_fxrequant as _FR
            _anim = ff7nx_palanim.animated_palettes(parts[0])
            _uni = _FR.uniform_palettes(parts[0])
            before617 = new9
            new9, ext = apply_margin_617(name, before617, cols, art,
                                         animated=_anim, uniform=_uni)
            if ext and ext.get("forced_palettes"):
                _p = list(parts)
                _p[8] = new9
                _rq, _rst = _FR.plan_field(name, _p, art)
                took = set((_rst or {}).get("palettes") or ()) if _rq else set()
                if not set(ext["forced_palettes"]) <= took:
                    new9, ext = apply_margin_617(
                        name, before617, cols, art, animated=_anim,
                        uniform=_uni, allow_force=False)
        except Exception as exc:
            total["refused"].append(
                (name, "617 %s: %s" % (type(exc).__name__, str(exc)[:60])))
            ext = None
        if ext:
            for key in ("cells", "tiles", "retargeted", "replaced_wrong",
                        "unrepresentable", "forced", "blanked"):
                total["x_" + key] = total.get("x_" + key, 0) + ext.get(key, 0)
            total.setdefault("x_names", []).extend(ext["names"])
        if new9 == parts[8]:
            continue
        parts[8] = new9
        payloads[name] = encode(lgp.join_sections(parts))
    return total


def summarise(stats):
    if not stats or not (stats.get("fields") or stats.get("x_cells")
                         or stats.get("x_blanked")):
        return ""
    names = ", ".join(stats["names"][:8])
    extra = ""
    if stats.get("x_cells") or stats.get("x_blanked"):
        extra = (
            "\n  FX MARGIN ART (BUILD 617): %d dark or wrong 16:9 margin FX "
            "cell(s) (%d replaced wrong art), used by %d margin tile(s), "
            "painted from the DDS FFNx draws into the same 256px paletted "
            "page (%d record(s) moved onto their 4:3 neighbour's palette; "
            "%d cell(s) not representable, left; %d blank cell(s) on a "
            "palette the page cannot draw moved onto a page palette) in %s. "
            "Set %s=1 to disable."
            % (stats["x_cells"], stats.get("x_replaced_wrong", 0),
               stats.get("x_tiles", 0), stats.get("x_retargeted", 0),
               stats.get("x_unrepresentable", 0), stats.get("x_blanked", 0),
               ", ".join(stats.get("x_names", [])[:16]), EXT_OFF_ENV))
    if not stats.get("fields"):
        return extra.lstrip("\n ")
    return extra and (_summary_base(stats, names) + extra) or \
        _summary_base(stats, names)


def _summary_base(stats, names):
    return (
        "FX MARGIN ART: %d blank additive cell(s), used by %d margin tile(s), "
        "restored from the one representable sibling Cosmos DDS across %d "
        "field(s) (%s). Page count, page slots, records, UVs, palettes and "
        "everything referenced inside 4:3 are unchanged. Set %s=1 to disable."
        % (stats["cells"], stats["tiles"], stats["fields"], names, OFF_ENV)
    )


# ===========================================================================
# BUILD 617 -- THE SAME REPAIR FOR EVERY PALETTED FX PAGE LEFT WITH DARK OR
# WRONG 16:9 MARGIN CELLS.
# ===========================================================================
#
# This is sinbil_1's mechanism (build 161) and build 581's extension of it,
# kept deliberately in the same shape: bytes of existing 16x16 cells on the
# existing 256px paletted page, drawn through a game palette, so the margin
# stays the same resolution and the same kind of texture as the 4:3 art it
# continues -- never an HD page beside a paletted one.
#
# WHAT THE TWO EARLIER GATES MISSED, measured on the build-616 archive:
#
#   sninn_2   29 margin cells NOT BLANK: Cosmos pointed its margin records at
#             cells that hold other 1997 art, so fxart's "only fill a blank
#             cell" rule skipped them -- they drew fragments of unrelated
#             effects (the misaligned blocks reported from hardware).
#             31 cells UNREPRESENTABLE: palrange re-seated Cosmos's
#             off-table palette 6 onto palette 0, a scenery palette that
#             cannot hold the light (error > 10/255).
#   mtcrl_1   the same palette problem (palette 9); mtcrl_8 not-blank cells.
#   las0_2, las4_0, nivl_b2, nivl_b22, sininb34
#             PAGE NOT LIVE: their FX pages normally become truecolor, but
#             fxpages' budget left the margin pages paletted, and no pass
#             fills a paletted page without an animated palette.
#
# SO, for fields other than the hardware-proven sinbil_1 / ghotel (left
# exactly as they ship):
#
#   * admission: every reference to the cell is an additive (mode 1) FX
#     record wholly outside 4:3 -- the cell cannot be seen anywhere else;
#   * a cell is rewritten only if it is blank and Cosmos visibly lights it
#     (4x4-block error >= MISSING_ERR), OR -- only for a palette that is
#     static in both the script and Cosmos -- what it draws now is far from
#     Cosmos's art (>= WRONG_ERR). Art under an animated palette is never
#     overwritten (jail2's flicker lights: the 1997 bytes are right in every
#     state, a Cosmos frame is right in one); a cell that already shows the
#     right picture is never touched;
#   * the donor is exactly the DDS FFNx loads for the record's palette (that
#     palette, else palette 0), never a sibling guess, never ambiguous;
#   * the palette: the record's own if it represents the cell within
#     MAX_ERROR; else the one the nearest 4:3 FX record on the same page and
#     param/state uses (the effect the margin continues); else another
#     palette 4:3 FX records use on that page. A record whose
#     own palette is script-animated is never moved off it; a script-
#     animated palette is chosen only if the script changes it solely by
#     whole-palette scaling (ff7nx_fxrequant.uniform_palettes), so the
#     painted cell pulses exactly with its neighbours (ghotel's rule).
#
# Records keep their page, UV, position, blend and state; only the palette
# byte of these margin-only records may change. SEVENTH_NX_NO_FX_ART_617=1
# disables this extension (the build-161/581 behaviour is unaffected).
EXT_OFF_ENV = "SEVENTH_NX_NO_FX_ART_617"
WRONG_ERR = 24.0
MISSING_ERR = 6.0         # blank cell: paint only if this much light is absent
EMPTY_MAX = 3.0           # premultiplied max channel below this -> key (fxrequant's EMPTY)
BLANK_MAX_ERROR = 20.0    # blank / invisible cell in bright art: looser fit
PROTECTED_FIELDS = frozenset({"sinbil_1", "ghotel"})


def ext_disabled():
    return os.environ.get(EXT_OFF_ENV, "").strip().lower() in (
        "1", "true", "yes", "on")


def _premul_page(art, field, slot, donor):
    """Cosmos's page at 256 with alpha premultiplied (the additive encoding
    build 139 / fxsplit / fxrequant ship), or None."""
    import ff7nx_fxmargin as _FXM
    img = _FXM._provider_rgba(art, field, slot, donor, 256)
    if img is None or img.shape != (256, 256, 4):
        return None
    return (img[..., :3].astype(np.float32)
            * (img[..., 3:4].astype(np.float32) / 255.0))


def _quantise_premul(rgb, pal):
    """16x16x3 premultiplied -> (indices, mean abs error). Nearest non-key
    entry; texels Cosmos leaves black stay the key (index 0)."""
    flat = rgb.reshape(-1, 3)
    cand = pal[1:].astype(np.float32)
    d = ((flat[:, None, :] - cand[None, :, :]) ** 2).sum(-1)
    idx = d.argmin(1) + 1
    black = flat.max(1) < EMPTY_MAX
    idx[black] = 0
    got = pal[idx].astype(np.float32)
    got[black] = 0.0
    return (idx.astype(np.uint8).reshape(TILE, TILE),
            float(np.abs(got - flat).mean()))


def _block4(a):
    return a.reshape(4, 4, 4, 4, 3).mean((1, 3))


def apply_margin_617(name, sec9, palettes565, art, animated=frozenset(),
                     uniform=None, allow_force=True):
    """Return ``(new_section9, stats)``; unchanged on any doubt."""
    stats = {"fields": 0, "cells": 0, "tiles": 0, "retargeted": 0,
             "replaced_wrong": 0, "unrepresentable": 0, "no_donor": 0,
             "relaxed": 0, "forced": 0, "forced_palettes": [],
             "names": []}
    field = name.lower()
    if (disabled() or ext_disabled() or field in PROTECTED_FIELDS
            or field in FIELDS or sec9.find(b"BACK") < 0
            or not len(palettes565)):
        return sec9, stats
    try:
        import ff7nx_fxmargin as _FXM
        import ff7nx_fxpalette as _FXPAL
        if (field in _FXM.TARGET_FIELDS
                or field in {f.lower() for f in _FXPAL.TARGET_FIELDS}):
            return sec9, stats
    except Exception:                                          # noqa: BLE001
        return sec9, stats
    provider = getattr(art, "provider", None)
    if provider is None:
        return sec9, stats
    try:
        survey = DC.survey(sec9)
        pages_list, tex_start, tex_end = FN.parse_texture_block(
            sec9, survey["page_px"])
        pages = {p.slot: p for p in pages_list if p is not None}
    except Exception:                                          # noqa: BLE001
        return sec9, stats
    animated = frozenset(animated)
    uniform = dict(uniform or {})
    npal = len(palettes565)

    refs = collections.defaultdict(list)
    inside = collections.defaultdict(list)     # slot -> [(dx, dy, par, st, pal)]
    for layer, offsets in DC.walk_layers(sec9, survey["back_start"],
                                         survey["tex_start"]):
        for off in offsets:
            dx = struct.unpack_from("<h", sec9, off + T_DSTX)[0]
            dy = struct.unpack_from("<h", sec9, off + T_DSTY)[0]
            pal = sec9[off + T_PAL]
            refs[(sec9[off + T_TEX], sec9[off + T_SRCX],
                  sec9[off + T_SRCY])].append(("base", layer, dx, dy, pal, off))
            fx = sec9[off + T_FX]
            if fx:
                refs[(fx, sec9[off + T_SRCX2], sec9[off + T_SRCY2])].append(
                    ("fx", layer, dx, dy, pal, off))
                if (sec9[off + T_USE_FX] and not _outside_43(dx)
                        and sec9[off + T_BLEND_MODE] == 1):
                    inside[fx].append((dx, dy, sec9[off + 26],
                                       sec9[off + 27], pal))
    arrays = {slot: np.frombuffer(p.data, np.uint8).reshape(256, 256).copy()
              for slot, p in pages.items()
              if p.depth == 1 and not p.size_flag and p.px == 256}
    if not arrays:
        return sec9, stats
    pal_rgb = [MA.palette_rgb(row) for row in palettes565]
    art_cache = {}
    buf = bytearray(sec9)
    changed = set()

    def _page_art(slot, q):
        donor = q if (field, slot, q) in provider.slots else (
            0 if (field, slot, 0) in provider.slots else None)
        if donor is None or (field, slot, donor) in provider.ambiguous_slots:
            return None
        key = (slot, donor)
        if key not in art_cache:
            art_cache[key] = _premul_page(art, field, slot, donor)
        return art_cache[key]

    def _usable(q, own):
        if q >= npal:
            return False
        if q == own:
            return True
        if own in animated:
            return False                 # never move an animated record
        return q not in animated or q in uniform

    for (slot, sx, sy), uses in sorted(refs.items()):
        arr = arrays.get(slot)
        if arr is None or sx % TILE or sy % TILE or sx > 240 or sy > 240:
            continue
        if any(role != "fx" or not _outside_43(dx)
               or not sec9[off + T_USE_FX]
               or sec9[off + T_BLEND_MODE] != 1
               for role, _l, dx, _dy, _p, off in uses):
            continue
        owns = {p for _r, _l, _x, _y, p, _o in uses}
        if len(owns) != 1:
            continue
        own = next(iter(owns))
        cell = arr[sy:sy + TILE, sx:sx + TILE]
        blank = not np.any(cell)
        # A cell that already holds art under an ANIMATED palette (script or
        # Cosmos states) is a light that changes: its 1997 bytes are the only
        # thing that stays right through every state (jail2's flicker).
        # Only blank cells are ever written there.
        if not blank and (own in animated or any(
                (field, slot, q) in provider.any_state_slots
                for q in (own, 0))):
            continue

        # Candidate palettes, most specific first.
        _r, _l, ux, uy, _p, uoff = uses[0]
        par, st_ = sec9[uoff + 26], sec9[uoff + 27]
        near = sorted(inside.get(slot, ()),
                      key=lambda t: ((t[2], t[3]) != (par, st_),
                                     (t[0] - ux) ** 2 + (t[1] - uy) ** 2))
        # The record's own palette first (fewest changes); the effect's 4:3
        # neighbour only when the own palette cannot hold the art.
        order = [own]
        if near:
            order.append(near[0][4])
        order += [t[4] for t in near]
        # HARDWARE RULE (FINDINGS-74, and sninn_2's upper-left rows on the
        # build-617 test): on a page that also carries 4:3 FX records, only
        # a palette those records use draws correctly. palrange's re-seat of
        # Cosmos's off-table palette onto palette 0 rendered as nothing.
        page_pals = {t[4] for t in inside.get(slot, ())}
        seen, cands = set(), []
        for q in order:
            if page_pals and q not in page_pals:
                continue
            if q not in seen and _usable(q, own):
                seen.add(q)
                cands.append(q)

        # Is the cell wrong as drawn today? Judge against the art FFNx draws
        # for the record's own palette.
        own_art = _page_art(slot, own)
        if own_art is None:
            stats["no_donor"] += 1
            continue
        ref = own_art[sy:sy + TILE, sx:sx + TILE]
        if ref.max() < EMPTY_MAX:
            # Cosmos draws nothing here either. That is only harmless when the
            # record's palette is one the page's 4:3 records use: build 383
            # showed sninn_2's blank margin cells on palette 0 drawing as
            # translucent teal blocks (FINDINGS-74). Move such a record onto
            # its 4:3 neighbour's palette and make sure its cell is blank, so
            # it draws exactly what Cosmos draws -- nothing.
            _pp = {t[4] for t in inside.get(slot, ())}
            if _pp and own not in _pp and cands:
                q = cands[0]
                arr[sy:sy + TILE, sx:sx + TILE] = 0
                changed.add(slot)
                stats["blanked"] = stats.get("blanked", 0) + 1
                stats["retargeted"] += len(uses)
                for _r, _l, _x, _y, _p, off in uses:
                    buf[off + T_PAL] = q
            continue
        if blank:
            now = np.zeros((TILE, TILE, 3), np.float32)
        else:
            now = pal_rgb[min(own, npal - 1)][cell].astype(np.float32)
            now[cell == 0] = 0.0
        gap = float(np.abs(_block4(now) - _block4(ref)).mean())
        page_pals0 = {t[4] for t in inside.get(slot, ())}
        off_page = bool(page_pals0) and own not in page_pals0
        if off_page:
            # Drawn through a palette the page's 4:3 records do not use: on
            # hardware this renders as nothing, whatever the offline render
            # says. Treat as missing.
            gap = max(gap, float(np.abs(_block4(ref)).mean()))
        elif gap < (MISSING_ERR if blank else WRONG_ERR):
            continue       # already right, or the light missing is invisible
        chosen, best = None, None
        for q in cands:
            pa_q = _page_art(slot, q)
            if pa_q is None:
                continue
            idx_q, err_q = _quantise_premul(pa_q[sy:sy + TILE, sx:sx + TILE],
                                            pal_rgb[q])
            if err_q <= MAX_ERROR:
                chosen = (q, idx_q)
                break
            if best is None or err_q < best[2]:
                best = (q, idx_q, err_q)
        # A BLANK cell inside bright art (sninn_2's glow core), or one drawn
        # through a palette the hardware renders as nothing, is a dark
        # square; the best available page palette a little over MAX_ERROR is
        # far closer than nothing. Only within BLANK_MAX_ERROR, and only when
        # it removes >= 3/4 of the gap.
        if (chosen is None and (blank or off_page) and best is not None
                and best[2] <= BLANK_MAX_ERROR and best[2] <= gap / 4.0):
            chosen = (best[0], best[1])
            stats["relaxed"] += 1
        # A page palette the ghotel pass (fxrequant) rebuilds from Cosmos:
        # move the record onto it even though its CURRENT entries cannot
        # hold the art; apply_to_flevel verifies fxrequant really takes that
        # palette and otherwise reruns without forcing.
        if (chosen is None and allow_force and page_pals
                and own not in page_pals and own not in animated
                and near and near[0][4] in uniform and near[0][4] in cands):
            fq = near[0][4]
            pa_f = _page_art(slot, fq)
            if pa_f is not None:
                chosen = (fq, _quantise_premul(
                    pa_f[sy:sy + TILE, sx:sx + TILE], pal_rgb[fq])[0])
                stats["forced"] += 1
                if fq not in stats["forced_palettes"]:
                    stats["forced_palettes"].append(fq)
        if chosen is None:
            stats["unrepresentable"] += 1
            continue
        q, idx = chosen
        arr[sy:sy + TILE, sx:sx + TILE] = idx
        changed.add(slot)
        stats["cells"] += 1
        stats["tiles"] += len(uses)
        if not blank:
            stats["replaced_wrong"] += 1
        if q != own:
            stats["retargeted"] += len(uses)
            for _r, _l, _x, _y, _p, off in uses:
                buf[off + T_PAL] = q

    if not changed:
        return sec9, stats
    plist, ts, te = FN.parse_texture_block(bytes(buf), survey["page_px"])
    for slot in changed:
        p = pages[slot]
        plist[slot] = FN.Page(slot, p.size_flag, p.depth,
                              arrays[slot].tobytes(), p.px)
    out = FN.replace_texture_block(bytes(buf), plist, ts, te)
    stats["fields"] = 1
    stats["names"] = [name]
    return out, stats
