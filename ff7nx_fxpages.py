#!/usr/bin/env python3
"""Upgrade complete Cosmos additive FX pages in place, archive-wide.

This is the general counterpart to ``ff7nx_fxmargin``'s three field-specific
repairs. A qualifying page changes only its payload and depth:

* the slot stays in the native additive band (15..23);
* the page count, tile records, palettes, UVs and animation state do not move;
* the base page is never promoted;
* only blend-mode-1 FX pages with a complete Cosmos DDS are accepted.

The Switch module's seven-word FINDINGS-194 ladder makes a depth-2 page in
this same band additive. Blend modes 2 and 3 still need the port's +14/+18
aliases, which depth-2 pages do not register, so a page named by either mode
is refused rather than partially upgraded.

FFNx's external loader first asks for the tile's palette and then falls back
to palette 0 (``repos/FFNx-master/src/saveload.cpp``). We mirror exactly that
rule. If the palettes used by a page resolve to different DDS images, one
palette-free truecolor page cannot represent them and the page is left
byte-identical.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import field_bg_repack as FR
import ff7nx_fxmargin as FXM
import ff7nx_marginblack as MB


FX_LO = 0x0F
FX_HI = 0x18
T_BLEND_MODE = 30
T_FX = FN.TILE_TEXTURE_ID2
T_SRC_X_BIG = 42
UV_SCALE = 10_000_000

# These fields already have a complete-effect migration with hardware-observed
# palette/cell handling in ff7nx_fxmargin. Running both mechanisms would
# convert the source page and then clone it, so the general pass defers to the
# established one. This is a collision guard, not a map-specific admission
# rule: every other field is decided from its page data alone.
DEFER_FIELDS = FXM.TARGET_FIELDS

# The ordinary dense allowance is reserved by build.py explicitly. Two more
# prospective depth-2 pages are held back for its independent in-place
# parallax conversion and final frame-cap duplication. The final-chain gate
# found the exact otherwise-missed case: las0_2 landed at 35.31 MB against a
# 35 MB ceiling even though the FX admission projection itself was below it.
# Two pages still leave ujunon1's four smoke frames admitted (34.06 MB final)
# while keeping the general pass from spending downstream's safety margin.
DOWNSTREAM_D2_RESERVE = 2

# See the 32-unit note in `upgrade_section9`. FINDINGS-297.
BIG_FX = os.environ.get('SEVENTH_NX_NO_BIG_FX') != '1'

# Mask repair is intentionally much narrower than a DDS-alpha replacement.
# It can close one-source-texel dither holes along already-authored colour,
# but a page with broad disagreement is not an edge repair and is refused.
MAX_MASK_FILL_FRACTION = 0.10
MASK_ON_ENV = 'SEVENTH_NX_FX_MASK_REPAIR'
NO_MASK_ENV = 'SEVENTH_NX_NO_FX_MASK_REPAIR'


def enabled():
    """One rollback disables both the archive pages and binary blend ladder."""
    return FXM.truecolor_enabled()


def mask_enabled():
    """The Build-165 mask experiment is comparison-only, never default."""
    return (enabled() and os.environ.get(MASK_ON_ENV) == '1'
            and os.environ.get(NO_MASK_ENV) != '1')


def _selected_palette(provider, field, page, palette):
    """The DDS palette FFNx would load: exact first, then palette zero."""
    field = field.lower()
    if (field, page, palette) in provider.slots:
        return palette
    if (field, page, 0) in provider.slots:
        return 0
    return None


def _encode_additive(img, px):
    """Bake DDS alpha into additive RGB, then encode one R5G6B5 page."""
    enc = np.ascontiguousarray(img, np.uint8).copy()
    alpha = enc[..., 3].astype(np.uint16)
    enc[..., :3] = ((enc[..., :3].astype(np.uint16)
                     * alpha[..., None] + 127) // 255).astype(np.uint8)
    # Alpha is now represented by colour strength. Keeping it opaque stops
    # rgba_to_565_buf's generic alpha<8 key from deleting faint smoke edges;
    # additive black is already the identity element.
    enc[..., 3] = 255
    return FR.rgba_to_565_buf(enc.tobytes(), px * px, width=px,
                              black_ok=True)


def _page_image(art, name, page, palettes, px):
    """Return one exact shared Cosmos RGBA page, or (None, reason)."""
    provider = getattr(art, 'provider', None)
    if provider is None:
        return None, 'no direct Cosmos provider'
    variants = []
    chosen = set()
    for pal in sorted(palettes):
        q = _selected_palette(provider, name, page, pal)
        if q is None:
            return None, 'no exact/palette-0 DDS'
        if (name.lower(), page, q) in getattr(
                provider, 'ambiguous_slots', ()):
            return None, 'DDS has multiple runtime states'
        if q in chosen:
            continue
        chosen.add(q)
        img = FXM._provider_rgba(art, name, page, q, px)
        if (img is None or img.shape != (px, px, 4)
                or not np.any(img[..., 3] >= 8)):
            return None, 'DDS is empty or unreadable'
        variants.append(np.ascontiguousarray(img, np.uint8))
    if not variants:
        return None, 'page has no palette references'
    first = variants[0]
    if any(not np.array_equal(first, other) for other in variants[1:]):
        return None, 'palette-specific DDS images differ'
    return first, None


def _state_alpha(provider, field, page, palette, px, cache):
    """Return alpha shared byte-for-byte by every DDS runtime state.

    The RGB may animate; only an invariant alpha plane licenses an indexed
    mask repair. Returning ``None`` is a refusal, never a fallback to one
    arbitrarily resolved state.
    """
    q = _selected_palette(provider, field, page, palette)
    if q is None:
        return None
    key = (field.lower(), page, q)
    if key in cache:
        return cache[key]
    states = getattr(provider, 'state_slots', {}).get(key, ())
    if not states:
        cache[key] = None
        return None
    out = None
    try:
        import dds_decode
        for path, entry in states:
            reader = provider.readers.get(path)
            if reader is None:
                reader = provider.readers[path] = FR.IroReader(path)
            blob = reader.read(entry)
            if not blob:
                cache[key] = None
                return None
            rgba, w, h = dds_decode.decode_dds(blob)
            raw = FR.resample_rgba(rgba, w, h, px)
            alpha = np.frombuffer(raw, np.uint8).reshape(px, px, 4)[..., 3]
            alpha = np.ascontiguousarray(alpha)
            if out is None:
                out = alpha
            elif not np.array_equal(out, alpha):
                cache[key] = None
                return None
    except Exception:                                          # noqa: BLE001
        out = None
    cache[key] = out
    return out


def _fill_mask_component(cell, target):
    """Close only one-texel indexed dither holes proven by DDS alpha.

    Existing indices -- including pixels outside the DDS cutoff -- are never
    cleared. A hole inherits a cardinally adjacent ORIGINAL index, so repair
    cannot grow recursively across a missing region or invent a disconnected
    alpha island. This keeps every palette animation and limits the change to
    the pixelated edge pattern this pass is meant to remove.
    """
    out = np.ascontiguousarray(cell, np.uint8).copy()
    seeds = (cell != 0) & target
    if target.any() and not seeds.any():
        return None
    for y, x in np.argwhere((cell == 0) & target):
        for yy, xx in ((y - 1, x), (y, x - 1), (y, x + 1), (y + 1, x)):
            if (0 <= yy < out.shape[0] and 0 <= xx < out.shape[1]
                    and seeds[yy, xx]):
                out[y, x] = cell[yy, xx]
                break
    return out


def _repair_ambiguous_mask(name, sec9, page, refs, palettes, art):
    """Repair one animated paletted FX page without freezing its RGB state.

    FFNx hash variants may differ in colour while sharing one exact alpha
    plane. In that case the page's existing non-zero indices already carry
    the runtime palette animation; only its old spatial index-0 dither is
    wrong. The entire page is admitted or refused as one effect.
    """
    provider = getattr(art, 'provider', None)
    if provider is None or not getattr(provider, 'state_slots', None):
        return None, None
    alpha_cache = {}
    alpha_by_pal = {}
    for pal in sorted(palettes):
        alpha = _state_alpha(provider, name, page.slot, pal, 256, alpha_cache)
        if alpha is None:
            return None, 'runtime-state alpha differs or is unreadable'
        alpha_by_pal[pal] = alpha

    grid = 8 if page.size_flag else 16
    edge = 256 // grid
    requested = collections.defaultdict(set)
    for tile in refs:
        u, v = struct.unpack_from('<II', sec9, tile.off + T_SRC_X_BIG)
        cx = int(round(u / UV_SCALE * grid))
        cy = int(round(v / UV_SCALE * grid))
        if cx < 0 or cy < 0 or cx >= grid or cy >= grid:
            return None, 'FX UV is outside its page grid'
        sx, sy = cx * edge, cy * edge
        alpha = alpha_by_pal.get(tile.pal)
        if alpha is None:
            return None, 'tile palette has no proven alpha state'
        target = alpha[sy:sy + edge, sx:sx + edge] >= 128
        if target.shape != (edge, edge):
            return None, 'DDS alpha cell is incomplete'
        requested[(sx, sy)].add(target.tobytes())

    source = np.frombuffer(page.data, np.uint8).reshape(256, 256)
    out = source.copy()
    holes = cleared = changed_cells = 0
    for (sx, sy), masks in requested.items():
        if len(masks) != 1:
            return None, 'palette variants disagree on the cell mask'
        target = np.frombuffer(next(iter(masks)), bool).reshape(edge, edge)
        before = source[sy:sy + edge, sx:sx + edge]
        repaired = _fill_mask_component(before, target)
        if repaired is None:
            return None, 'an opaque alpha cell has no palette seed'
        if not np.array_equal(before, repaired):
            holes += int(((before == 0) & (repaired != 0)).sum())
            changed_cells += 1
            out[sy:sy + edge, sx:sx + edge] = repaired
    if not changed_cells:
        return None, 'no one-texel DDS-proven dither holes'
    covered = len(requested) * edge * edge
    if holes > covered * MAX_MASK_FILL_FRACTION:
        return None, 'mask disagreement is too broad for edge repair'
    return (out.tobytes(), {
        'cells': changed_cells, 'holes': holes, 'cleared': cleared,
        'tiles': len(refs),
    }), None


# BUILD 581: fields whose FX page stayed paletted BECAUSE a palette on it is
# animated by the field script. ff7nx_fxart fills those pages' blank
# widescreen cells in the page's own (animated) palette instead.
ANIM_KEPT = set()
KEEP_STATIC_ENV = 'SEVENTH_NX_FX_STATIC_ANIMATED'


def static_animated():
    """True: convert script-animated palettes anyway (the BUILD 579/580
    behaviour -- HD, but the glow stops)."""
    return os.environ.get(KEEP_STATIC_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


# BUILD 608: A SCRIPT-ANIMATED PALETTE STAYS PALETTED ONLY WHERE THE MOD
# ANIMATES IT TOO.
#
# FFNx replaces a paletted page with the DDS for (page, palette) -- exact
# palette first, then palette 0 (`_selected_palette`). When the field script
# rewrites that palette at runtime (LDPAL/LDPLS, see ff7nx_palanim), FFNx
# swaps to a `_<hash>` DDS for the new state IF Cosmos ships one; ghotel's fog
# (palettes 8/11: 20 and 18 frames) and sinbil_1's glow (6/8: 19 and 50) do,
# in the AA REMOVED folder. Those keep their BUILD 581 veto whether or not
# that folder is enabled: the author animated them. When the mod has no state
# frame for the palette in ANY folder, it ships one static picture for it.
#
# BUILD 581 vetoed conversion for ANY script-animated palette, so a page like
# bugin1a's 15/16 (Bugenhagen's planetarium: palettes 1/2 are dimmed by
# MPPAL, and Cosmos resolves both to the single `_15_00`/`_16_00`) kept its
# 1997 256px art and the room dropped to low resolution the moment the show
# started. The veto now applies only where Cosmos itself animates the palette
# (state frames anywhere in the .iro). The cost, stated: where the script
# animates such a palette -- bugin1a's show dims palettes 1/2 with MPPAL --
# that colour change no longer runs; the tiles show Cosmos's one picture.
# SEVENTH_NX_FX_ANIM_ALWAYS=1 restores the BUILD 581 rule.
ANIM_ALWAYS_ENV = 'SEVENTH_NX_FX_ANIM_ALWAYS'


def anim_always():
    return os.environ.get(ANIM_ALWAYS_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def cosmos_animates(provider, field, page, palette):
    """True when the mod ships runtime-state frames for this palette on this
    page -- in ANY of its folders, enabled or not -- or more than one state
    for the slot FFNx would load. False means the mod has one static picture
    for it: the script's palette animation has no HD counterpart anywhere."""
    field = field.lower()
    anyst = getattr(provider, 'any_state_slots', None)
    if anyst is None:
        return True                       # unknown provider: keep BUILD 581
    q = _selected_palette(provider, field, page, palette)
    for key in {(field, page, palette), (field, page, q)}:
        if key in anyst:
            return True
    if q is not None and len(getattr(provider, 'state_slots', {}).get(
            (field, page, q), ())) > 1:
        return True
    return False


def live_animated(provider, field, page, palettes, animated):
    """The subset of `animated` (script-written palettes) that must stay
    paletted on `page`: all of them under SEVENTH_NX_FX_ANIM_ALWAYS=1, else
    only those Cosmos animates too."""
    if static_animated():
        return frozenset()
    pals = set(palettes) & set(animated)
    if anim_always():
        return frozenset(pals)
    return frozenset(q for q in pals
                     if cosmos_animates(provider, field, page, q))


def upgrade_section9(name, sec9, art, px, max_raw_delta=None,
                     max_runtime_delta=None, animated=frozenset()):
    """Return ``(section9, stats)`` after page-neutral additive conversion.

    The optional deltas are hard remaining byte budgets. A page either fits
    in full or remains byte-identical; no cell of an atlas is copied alone.
    """
    st = {
        'fields': 0, 'pages': 0, 'tiles': 0, 'bytes': 0,
        'blend_veto': 0, 'base_veto': 0, 'art_veto': 0, 'budget_veto': 0,
        'deferred': 0, 'names': [], 'page_names': [],
        'mask_fields': 0, 'mask_pages': 0, 'mask_cells': 0,
        'mask_tiles': 0, 'mask_holes': 0, 'mask_cleared': 0,
        'mask_veto': 0, 'mask_names': [], 'mask_page_names': [],
        'anim_veto': 0, 'anim_names': [],
    }
    if not enabled() or name.lower() in DEFER_FIELDS:
        st['deferred'] = int(name.lower() in DEFER_FIELDS)
        return sec9, st
    provider = getattr(art, 'provider', None)
    if provider is None:
        return sec9, st

    try:
        pages_list, tex_start, tex_end = FN.parse_texture_block(sec9, px)
        pages = {p.slot: p for p in pages_list if p is not None}
        surv = DC.survey(sec9)
        tiles = MB.read_tiles(sec9, surv, pages)
    except Exception:
        return sec9, st

    # Every declaration of an FX page matters, including a tile whose initial
    # use_fx flag is off: field scripts may toggle that frame later.
    fx_refs = collections.defaultdict(list)
    base_refs = collections.defaultdict(list)
    all_pals = collections.defaultdict(set)
    for t in tiles:
        base_refs[t.slot].append(t)
        all_pals[t.slot].add(t.pal)
        fx = sec9[t.off + T_FX]
        if fx:
            fx_refs[fx].append(t)
            all_pals[fx].add(t.pal)

    candidates = []
    mask_repairs = []
    for slot in range(FX_LO, FX_HI):
        page = pages.get(slot)
        refs = fx_refs.get(slot, ())
        # ---- 32-UNIT FX PAGES QUALIFY TOO. FINDINGS-297.
        #
        # `page.size_flag` used to be refused here. Nothing below needs it:
        # the whole page is replaced at the same resolution, the UVs never
        # move, and `FN.Page(slot, page.size_flag, 2, ...)` already carries
        # the flag through unchanged. The exclusion was caution, and it cost
        # the one place where an FX page IS the backdrop.
        #
        # A 32-unit depth-2 page is proven on this port twice over: the
        # IN-PLACE PARALLAX arm ships 35 of them across 8 fields, and build
        # 119 ships `mtcrl_4`'s on slots 12/13/14. Additive behaviour in this
        # band comes from the FINDINGS-194 ladder, which keys off the page
        # TYPE in section 9 and not off its cell size.
        #
        # MEASURED on `trnad_4`, the Whirlwind Maze: layer 3 -- the green
        # lifestream, the whole backdrop -- binds FX pages 17 and 18, both
        # 32-unit. Every other page in the field is 768px truecolor and those
        # two were still 1997 8-bit at 256px, which is why the effect banded
        # and looked blocky against FFNx's smooth gradient. Both pass every
        # other test already: FX-only (no base ref), blend mode 1 on all 165
        # records, and complete Cosmos art at 768x768.
        if (page is None or page.depth != 1
                or (page.size_flag and not BIG_FX)
                or page.px != FN.D1_PAGE_PX or not refs):
            continue
        # A page used through texture_id is a resting/base frame somewhere in
        # this field. Replacing it would make the non-FX frame move too. The
        # universal pass is intentionally narrower: FX-only pages, while the
        # existing base pages remain byte-identical by construction.
        if base_refs.get(slot):
            st['base_veto'] += 1
            continue
        # A declared blend-2/3 frame requires an alias texture that the port
        # still does not register for depth 2. Blend 0/4 are not valid in the
        # native additive page band either, so mode 1 is the complete rule.
        if any(sec9[t.off + T_BLEND_MODE] != 1 for t in refs):
            st['blend_veto'] += 1
            continue
        # BUILD 581. A palette the field script loads at runtime (LDPAL /
        # LDPLS -- see ff7nx_palanim) is a colour ANIMATION: ghotel's fog
        # pulse, sinbil_1's green glow. One truecolor page would freeze it.
        _live = live_animated(provider, name, slot,
                              {t.pal for t in refs}, animated)
        if any(t.pal in _live for t in refs):
            st['anim_veto'] += 1
            if name not in st['anim_names']:
                st['anim_names'].append(name)
            ANIM_KEPT.add(name.lower())
            continue
        image, _why = _page_image(art, name, slot, all_pals[slot], px)
        if image is None:
            # A multi-state page cannot become one frozen truecolor page. It
            # can still keep every RGB/palette state and repair only the
            # index-0 mask when all those states prove the SAME alpha plane.
            _ambiguous = any(
                (name.lower(), slot,
                 _selected_palette(provider, name, slot, pal))
                in getattr(provider, 'ambiguous_slots', ())
                for pal in all_pals[slot])
            if _ambiguous and mask_enabled():
                repaired, _mask_why = _repair_ambiguous_mask(
                    name, sec9, page, refs, all_pals[slot], art)
                if repaired is not None:
                    data, mst = repaired
                    mask_repairs.append((slot, page, data, mst))
                    continue
                st['mask_veto'] += 1
            st['art_veto'] += 1
            continue
        candidates.append((slot, page, refs, image))

    if not candidates and not mask_repairs:
        return sec9, st

    # Most-used effects first if an exceptionally large field reaches a hard
    # byte ceiling. Each admitted page is still complete; no cell or frame is
    # partially copied.
    candidates.sort(key=lambda row: (-len(row[2]), row[0]))
    raw_left = max_raw_delta if max_raw_delta is not None else 1 << 60
    runtime_left = (max_runtime_delta if max_runtime_delta is not None
                    else 1 << 60)
    converted = []
    raw_used = runtime_used = 0
    d2_runtime = FR._page_bytes(px, 2)
    d1_runtime = FR._page_bytes(FN.D1_PAGE_PX, 1)
    for slot, page, refs, image in candidates:
        raw_delta = FN.stored_bytes(px, 2) - FN.stored_bytes(page.px, 1)
        runtime_delta = d2_runtime - d1_runtime
        if (raw_used + raw_delta > raw_left
                or runtime_used + runtime_delta > runtime_left):
            st['budget_veto'] += 1
            continue
        data = _encode_additive(image, px)
        pages_list[slot] = FN.Page(slot, page.size_flag, 2, data, px)
        converted.append((slot, refs))
        raw_used += raw_delta
        runtime_used += runtime_delta

    for slot, page, data, mst in mask_repairs:
        pages_list[slot] = FN.Page(slot, page.size_flag, 1, data, page.px)

    if not converted and not mask_repairs:
        return sec9, st
    out = FN.replace_texture_block(sec9, pages_list, tex_start, tex_end)
    st['fields'] = int(bool(converted))
    st['pages'] = len(converted)
    st['tiles'] = sum(len(refs) for _slot, refs in converted)
    st['bytes'] = raw_used
    st['names'] = [name] if converted else []
    st['page_names'] = ['%s:%d' % (name, slot) for slot, _ in converted]
    if mask_repairs:
        st['mask_fields'] = 1
        st['mask_pages'] = len(mask_repairs)
        st['mask_cells'] = sum(row[3]['cells'] for row in mask_repairs)
        st['mask_tiles'] = sum(row[3]['tiles'] for row in mask_repairs)
        st['mask_holes'] = sum(row[3]['holes'] for row in mask_repairs)
        st['mask_cleared'] = sum(row[3]['cleared'] for row in mask_repairs)
        st['mask_names'] = [name]
        st['mask_page_names'] = [
            '%s:%d' % (name, slot) for slot, _page, _data, _mst
            in mask_repairs]
    return out, st


def merge(total, one):
    """Accumulate one field's stats into an archive summary."""
    for key in ('fields', 'pages', 'tiles', 'bytes', 'blend_veto', 'base_veto',
                'art_veto', 'budget_veto', 'deferred', 'mask_fields',
                'anim_veto',
                'mask_pages', 'mask_cells', 'mask_tiles', 'mask_holes',
                'mask_cleared', 'mask_veto'):
        total[key] = total.get(key, 0) + one.get(key, 0)
    total.setdefault('names', []).extend(one.get('names', ()))
    total.setdefault('page_names', []).extend(one.get('page_names', ()))
    total.setdefault('mask_names', []).extend(one.get('mask_names', ()))
    total.setdefault('anim_names', []).extend(one.get('anim_names', ()))
    total.setdefault('mask_page_names', []).extend(
        one.get('mask_page_names', ()))
    return total


def summarise(st):
    if not st or not (st.get('pages') or st.get('mask_pages')
                      or st.get('anim_veto')):
        return ''
    line = ('ADDITIVE FX PAGES: %d complete Cosmos page(s), %d FX tile '
            'reference(s), in place across %d field(s); slots, page count, '
            'UVs, palettes, base pages and animation records unchanged. '
            'Vetoed: %d mixed-blend, %d also-used-as-base, %d '
            'missing/different art, %d budget.'
            % (st['pages'], st['tiles'], st['fields'], st['blend_veto'],
               st['base_veto'], st['art_veto'], st['budget_veto']))
    if st.get('anim_veto'):
        line += (' -- %d page(s) KEPT PALETTED because the field script '
                 'animates a palette on them (glow/pulse; BUILD 581, %s=1 '
                 'converts them anyway and freezes the effect): %s'
                 % (st['anim_veto'], KEEP_STATIC_ENV,
                    ', '.join(st.get('anim_names', [])[:40])))
    if st.get('mask_pages'):
        line += (' -- ANIMATED FX MASK: %d paletted page(s), %d cell(s), %d '
                 'tile reference(s) in %d field(s) kept every RGB/palette '
                 'runtime state while closing %d one-source-texel index-0 '
                 'dither hole(s); %d existing texel(s) were cleared. '
                 'Admission requires byte-identical alpha across all runtime '
                 'DDS states and palettes, FX-only use, blend mode 1, '
                 'per-cell agreement, an adjacent existing animated palette '
                 'index, and at most %.0f%% page-cell coverage; %d ambiguous '
                 'page(s) were refused. No page, depth, slot, UV, palette, '
                 'animation record or byte budget changes. Set %s=1 to '
                 'disable only this mask repair.'
                 % (st['mask_pages'], st['mask_cells'], st['mask_tiles'],
                    st['mask_fields'], st['mask_holes'], st['mask_cleared'],
                    MAX_MASK_FILL_FRACTION * 100, st['mask_veto'],
                    NO_MASK_ENV))
    return line
