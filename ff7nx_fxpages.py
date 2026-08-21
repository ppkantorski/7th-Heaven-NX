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


def enabled():
    """One rollback disables both the archive pages and binary blend ladder."""
    return FXM.truecolor_enabled()


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


def upgrade_section9(name, sec9, art, px, max_raw_delta=None,
                     max_runtime_delta=None):
    """Return ``(section9, stats)`` after page-neutral additive conversion.

    The optional deltas are hard remaining byte budgets. A page either fits
    in full or remains byte-identical; no cell of an atlas is copied alone.
    """
    st = {
        'fields': 0, 'pages': 0, 'tiles': 0, 'bytes': 0,
        'blend_veto': 0, 'base_veto': 0, 'art_veto': 0, 'budget_veto': 0,
        'deferred': 0, 'names': [], 'page_names': [],
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
    for slot in range(FX_LO, FX_HI):
        page = pages.get(slot)
        refs = fx_refs.get(slot, ())
        if (page is None or page.depth != 1 or page.size_flag
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
        image, _why = _page_image(art, name, slot, all_pals[slot], px)
        if image is None:
            st['art_veto'] += 1
            continue
        candidates.append((slot, page, refs, image))

    if not candidates:
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

    if not converted:
        return sec9, st
    out = FN.replace_texture_block(sec9, pages_list, tex_start, tex_end)
    st['fields'] = 1
    st['pages'] = len(converted)
    st['tiles'] = sum(len(refs) for _slot, refs in converted)
    st['bytes'] = raw_used
    st['names'] = [name]
    st['page_names'] = ['%s:%d' % (name, slot) for slot, _ in converted]
    return out, st


def merge(total, one):
    """Accumulate one field's stats into an archive summary."""
    for key in ('fields', 'pages', 'tiles', 'bytes', 'blend_veto', 'base_veto',
                'art_veto', 'budget_veto', 'deferred'):
        total[key] = total.get(key, 0) + one.get(key, 0)
    total.setdefault('names', []).extend(one.get('names', ()))
    total.setdefault('page_names', []).extend(one.get('page_names', ()))
    return total


def summarise(st):
    if not st or not st.get('pages'):
        return ''
    return ('ADDITIVE FX PAGES: %d complete Cosmos page(s), %d FX tile '
            'reference(s), in place across %d field(s); slots, page count, '
            'UVs, palettes, base pages and animation records unchanged. '
            'Vetoed: %d mixed-blend, %d also-used-as-base, %d '
            'missing/different art, %d budget.'
            % (st['pages'], st['tiles'], st['fields'], st['blend_veto'],
               st['base_veto'], st['art_veto'], st['budget_veto']))
