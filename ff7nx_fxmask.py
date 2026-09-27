#!/usr/bin/env python3
"""Cosmos's transparency on the FX pages that stay paletted. BUILD 581.

An FX page that cannot become Cosmos truecolor (its palette animates, its
animation spans more pages than the byte budget admits, Cosmos ships it as a
runtime state sequence, ...) keeps its 1997 256px page. Cosmos's section 9,
which the build splices in, extends those effects with extra records -- and
those records sample cells where the 1997 page has ART but Cosmos's own image
is EMPTY. FFNx draws Cosmos's image, so on PC they are invisible; here they
drew the 1997 texels.

MEASURED, ujunon2 (Junon shore): the wave-foam animation spans pages 16..22.
16..19 went truecolor, 20..22 hit the byte ceiling and stayed paletted, and
Cosmos's extended layer-4 records (138 per frame against vanilla's 54) put
the 1997 foam on those pages all over the open water and the tower's base --
"the shore texture where it shouldn't be", and the white speckle.

The fix is subtractive and nothing else: an index is set to 0 (the colour
key; on these additive pages also the identity) only where EVERY Cosmos
image any sampling record resolves to (FFNx's exact-then-palette-0 rule; for
a runtime state sequence, every state) is transparent (alpha < 8). No colour,
page, slot, UV, palette or record changes; nothing can appear that was not
there. A page whose 1997 coverage and Cosmos coverage disagree over most of
its sampled texels is refused (the layout is not the same atlas).

    SEVENTH_NX_NO_FX_MASK=1    off
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import field_bg_repack as FR
import ff7nx_fxpages as XP

OFF_ENV = 'SEVENTH_NX_NO_FX_MASK'
BAND = range(0x0F, 0x1A)          # additive 15..23 and average 24..25
T_FX = FN.TILE_TEXTURE_ID2
T_USE_FX = 28
T_SRC_X_BIG = 42
UV_SCALE = 10_000_000
MIN_AGREE = 0.5


def enabled():
    return os.environ.get(OFF_ENV, '').strip().lower() not in (
        '1', 'true', 'yes', 'on')


def new_stats():
    return {'fields': 0, 'pages': 0, 'texels': 0, 'refused': 0, 'names': []}


def _alpha(provider, field, page, q, cache):
    """Union coverage (alpha >= 8) over every runtime state, at 256."""
    key = (page, q)
    if key in cache:
        return cache[key]
    import dds_decode
    recs = provider.state_slots.get((field, page, q)) or ()
    if not recs and (field, page, q) in provider.slots:
        recs = (provider.slots[(field, page, q)],)
    cov = None
    try:
        for path, entry in recs:
            reader = provider.readers.get(path)
            if reader is None:
                reader = provider.readers[path] = FR.IroReader(path)
            blob = reader.read(entry)
            if not blob:
                cov = None
                break
            rgba, w, h = dds_decode.decode_dds(blob)
            a = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)[..., 3]
            if w % 256 or h % 256 or w != h:
                cov = None
                break
            k = w // 256
            c = (a.reshape(256, k, 256, k).max((1, 3)) >= 8)
            cov = c if cov is None else (cov | c)
    except Exception:                                          # noqa: BLE001
        cov = None
    cache[key] = cov
    return cov


def mask_section9(name, sec9, art):
    st = new_stats()
    if not enabled():
        return sec9, st
    provider = getattr(art, 'provider', None)
    if provider is None:
        return sec9, st
    field = name.lower()
    try:
        surv = DC.survey(sec9)
        plist, ts, te = FN.parse_texture_block(sec9, surv['page_px'])
        pages = {p.slot: p for p in plist if p is not None}
    except Exception:                                          # noqa: BLE001
        return sec9, st
    base_slots = set()
    uses = collections.defaultdict(list)      # slot -> [(cx, cy, pal)]
    for _layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                       surv['tex_start']):
        for o in offs:
            base_slots.add(sec9[o + FN.TILE_TEXTURE_ID])
            fx = sec9[o + T_FX]
            if not fx:
                continue
            u, v = struct.unpack_from('<II', sec9, o + T_SRC_X_BIG)
            uses[fx].append((u, v, sec9[o + FN.TILE_PALETTE_ID]))
    cache = {}
    changed = {}
    for slot in BAND:
        page = pages.get(slot)
        refs = uses.get(slot)
        if (page is None or not refs or page.depth != 1 or page.px != 256
                or slot in base_slots):
            continue
        # A 32-unit (size_flag) page is an 8x8 grid of 32-texel cells.
        grid = 8 if page.size_flag else 16
        edge = 256 // grid
        arr = np.frombuffer(page.data, np.uint8).reshape(256, 256).copy()
        keep = np.zeros((256, 256), bool)      # texels some image paints
        sampled = np.zeros((256, 256), bool)
        bad = False
        for u, v, pal in refs:
            cx = int(round(u / UV_SCALE * grid))
            cy = int(round(v / UV_SCALE * grid))
            q = XP._selected_palette(provider, field, slot, pal)
            if q is None:
                bad = True
                break
            cov = _alpha(provider, field, slot, q, cache)
            if cov is None:
                bad = True
                break
            ys = slice(cy * edge, cy * edge + edge)
            xs = slice(cx * edge, cx * edge + edge)
            keep[ys, xs] |= cov[ys, xs]
            sampled[ys, xs] = True
        if bad:
            continue
        drawn = sampled & (arr != 0)
        if not drawn.any():
            continue
        agree = float((drawn & keep).sum()) / float(drawn.sum())
        if agree < MIN_AGREE:
            st['refused'] += 1
            continue
        kill = drawn & ~keep
        n = int(kill.sum())
        if not n:
            continue
        arr[kill] = 0
        changed[slot] = arr
        st['texels'] += n
    if not changed:
        return sec9, st
    for slot, arr in changed.items():
        p = pages[slot]
        plist[slot] = FN.Page(slot, p.size_flag, p.depth, arr.tobytes(), p.px)
    out = FN.replace_texture_block(sec9, plist, ts, te)
    st['fields'] = 1
    st['pages'] = len(changed)
    st['names'] = [name]
    return out, st


def merge(total, one):
    for k in ('fields', 'pages', 'texels', 'refused'):
        total[k] = total.get(k, 0) + one.get(k, 0)
    total.setdefault('names', []).extend(one.get('names', ()))
    return total


def summarise(st):
    if not st or not (st.get('pages') or st.get('refused')):
        return None
    return ('FX MASK (BUILD 581): %d texel(s) on %d paletted FX page(s) in %d '
            'field(s) keyed where every Cosmos image their records resolve to '
            'is transparent (FFNx draws nothing there; ujunon2 stray shore '
            'foam); %d page(s) refused as a different atlas. Colour, pages, '
            'slots, UVs and records unchanged. %s=1 disables.'
            % (st['texels'], st['pages'], st['fields'], st['refused'],
               OFF_ENV))
