#!/usr/bin/env python3
"""Repair mtnvl6's animated page-16 index-255 RGB stipple.

Hardware showed bright square dots around the lower-left fog contour. They
are not missing alpha and not index-0 transparency. Exact reconstruction of
the shipped field against every Cosmos runtime state proves one fingerprint:
page 16 has 159 unique cells (palette 8 on 46 records, palette 9 on 113);
after matching the current palette to its Cosmos hash state, exactly 2,869
source units differ by more than 64/255 RGB; every one is palette index 255
in a palette-9-only cell, and every index-255 unit has the error. Page 15 has
zero units over 24/255 and is not part of this defect.

Build 166 changed that population from index 255 to index 0. Hardware correctly
showed no change: palette 9 stores both entries as black (0x0000/0x8000), so
the two indices are the same additive identity. Its depth-2 companion also did
not draw for this animated population and is retained below only to reproduce
that failed build.

Build 167 repaired those 2,869 palette-9 units and hardware confirmed the fog,
then exposed one last tiny square.  The archive-wide audit had already found
that exact outlier: source (47,47), destination (-225,207), in a disjoint
palette-8 cell on the same page.  It is another fully-opaque index 255, and
palette-8 index 3 follows its eight-state Cosmos trajectory with mean error
0.87/255 (maximum 1.56).

The active repair learns each usable palette index's colour trajectory from
all eight Cosmos runtime states, independently per palette, then replaces each
bad source unit with the live index whose trajectory best matches Cosmos at
that exact position. The original page, records, UVs, palette animation and
blend remain in place; no page or record is added. The complete fingerprint is
required or the field remains byte-identical.

SEVENTH_NX_NO_MTNVL6_FX_REPAIR=1 disables the arm.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import field_bg_pagecap as PC
import field_bg_repack as FR
import ff7nx_marginart as MA
import ff7nx_marginblack as MB


TARGET = 'mtnvl6'
OFF_ENV = 'SEVENTH_NX_NO_MTNVL6_FX_REPAIR'
SOURCE_SLOT = 16
TARGET_PAL = 9
TOTAL_REFS = 159
TARGET_REFS = 113
OTHER_REFS = 46
BAD_INDEX = 255
BAD_UNITS = 2869
ERROR_FLOOR = 64.0
BASE_COMPANION = 17
FX_COMPANION = 18
TILE_SIZE = FN.TILE_SIZE
T_TEX = FN.TILE_TEXTURE_ID
T_FX = FN.TILE_TEXTURE_ID2
T_BLEND = 30
T_PACKED = 42
UV_SCALE = 10_000_000


def disabled():
    return os.environ.get(OFF_ENV) == '1'


def _layers(sec9, back, tex):
    import ff7nx_parallaxfill as PF
    return PF._layers(sec9, back, tex)


def _runtime_states(provider, name, page, pal, px, cache):
    key = ((name, page, pal) if (name, page, pal) in provider.state_slots
           else (name, page, 0))
    if key not in provider.state_slots:
        return None
    if key in cache:
        return cache[key]
    recs = provider.state_slots.get(key, ())
    if not recs:
        cache[key] = None
        return None
    out = []
    try:
        import dds_decode
        for path, entry in recs:
            reader = provider.readers.get(path)
            if reader is None:
                reader = provider.readers[path] = FR.IroReader(path)
            blob = reader.read(entry)
            if not blob:
                cache[key] = None
                return None
            rgba, w, h = dds_decode.decode_dds(blob)
            raw = FR.resample_rgba(rgba, w, h, px)
            out.append(np.frombuffer(raw, np.uint8).reshape(px, px, 4).copy())
    except Exception:                                          # noqa: BLE001
        cache[key] = None
        return None
    alpha = out[0][..., 3]
    if any(not np.array_equal(alpha, img[..., 3]) for img in out[1:]):
        cache[key] = None
        return None
    cache[key] = tuple(out)
    return cache[key]


def _encode_rgb(rgb, px):
    rgba = np.empty((px, px, 4), np.uint8)
    rgba[..., :3] = rgb
    rgba[..., 3] = 255
    return FR.rgba_to_565_buf(rgba.tobytes(), px * px, width=px,
                              black_ok=True)


def _append_records(sec9, layers, duplicates):
    buf = bytearray(sec9)
    for _layer, count_at, first, n in sorted(layers, reverse=True):
        end = first + n * TILE_SIZE
        mine = [duplicates[o] for o in sorted(duplicates)
                if first <= o < end]
        if not mine:
            continue
        buf[end:end] = b''.join(mine)
        struct.pack_into('<H', buf, count_at, n + len(mine))
    return bytes(buf)


def _withdrawn_build166_apply_to_section9(name, sec9, palette_sec, art, px):
    st = {'fields': 0, 'tiles': 0, 'pages': 0, 'cells': 0,
          'cleared': 0, 'pixels': 0, 'state': -1, 'refused': ''}
    if disabled() or name.lower() != TARGET:
        return sec9, st
    provider = getattr(art, 'provider', None)
    if provider is None or not getattr(provider, 'state_slots', None):
        st['refused'] = 'no runtime-state DDS provider'
        return sec9, st
    try:
        plist, _ts, _te = FN.parse_texture_block(sec9, px)
        pages = {p.slot: p for p in plist if p is not None}
        surv = DC.survey(sec9)
        tiles = MB.read_tiles(sec9, surv, pages)
        layers = _layers(sec9, surv['back_start'], surv['tex_start'])
        colours, _hdr, npg, cpp = MB.palette_colours(palette_sec)
    except Exception as exc:                                  # noqa: BLE001
        st['refused'] = 'parse: %s' % exc
        return sec9, st

    page = pages.get(SOURCE_SLOT)
    if (page is None or page.depth != 1 or page.size_flag or page.px != 256
            or TARGET_PAL >= npg or cpp != 256
            or BASE_COMPANION in pages or FX_COMPANION in pages):
        st['refused'] = 'page/palette/slot signature changed'
        return sec9, st
    cap = FR.max_total_pages()
    if cap and len(pages) + 2 > cap:
        st['refused'] = 'two companion pages exceed total-page cap'
        return sec9, st

    all_refs = [t for t in tiles if sec9[t.off + T_FX] == SOURCE_SLOT]
    target = [t for t in all_refs if t.pal == TARGET_PAL]
    other = [t for t in all_refs if t.pal == 8]
    if (len(all_refs) != TOTAL_REFS or len(target) != TARGET_REFS
            or len(other) != OTHER_REFS
            or {t.pal for t in all_refs} != {8, TARGET_PAL}
            or any(t.layer != 2 or sec9[t.off + T_TEX] != 0
                   or sec9[t.off + T_BLEND] != 1
                   or sec9[t.off + 28] != 1 for t in all_refs)):
        st['refused'] = 'page-16 record signature changed'
        return sec9, st
    if len(target) > PC.MAX_TILES_PER_PAGE:
        st['refused'] = 'companion binding population exceeds cap'
        return sec9, st

    scale = px // 256
    if scale < 2 or scale * 256 != px:
        st['refused'] = 'truecolor page is not an integer 256px multiple'
        return sec9, st
    cell_hi = 16 * scale
    states = _runtime_states(provider, TARGET, SOURCE_SLOT, TARGET_PAL, px, {})
    if states is None or len(states) < 2:
        st['refused'] = 'palette-9 runtime states missing or alpha-inconsistent'
        return sec9, st
    prgb = MA.palette_rgb(colours[TARGET_PAL])
    source = np.frombuffer(page.data, np.uint8).reshape(256, 256)
    entries, seen = [], set()
    scores = np.zeros(len(states), np.float64)
    samples = 0
    for t in target:
        u, v = struct.unpack_from('<II', sec9, t.off + T_PACKED)
        cx = int(round(u / UV_SCALE * 16))
        cy = int(round(v / UV_SCALE * 16))
        if not (0 <= cx < 16 and 0 <= cy < 16) or (cx, cy) in seen:
            st['refused'] = 'palette-9 cell map is not unique'
            return sec9, st
        seen.add((cx, cy))
        sx, sy = cx * 16, cy * 16
        hx, hy = cx * cell_hi, cy * cell_hi
        idx = source[sy:sy + 16, sx:sx + 16].copy()
        crops = np.stack([q[hy:hy + cell_hi, hx:hx + cell_hi]
                          for q in states], 0)
        if idx.shape != (16, 16) or crops.shape[1:] != (cell_hi, cell_hi, 4):
            st['refused'] = 'incomplete source/state cell'
            return sec9, st
        src_rgb = prgb[idx].astype(np.int16)
        for i, crop in enumerate(crops):
            tgt = crop[..., :3].reshape(
                16, scale, 16, scale, 3).mean((1, 3))
            scores[i] += np.abs(src_rgb - tgt.astype(np.int16)).sum()
        samples += src_rgb.size
        entries.append((t, cx, cy, sx, sy, idx, crops))
    if len(seen) != TARGET_REFS or not samples:
        st['refused'] = 'palette-9 atlas population changed'
        return sec9, st
    best = int(scores.argmin())
    st['state'] = best

    bad_total, checked = 0, []
    for row in entries:
        _t, _cx, _cy, _sx, _sy, idx, crops = row
        tgt = crops[best, ..., :3].reshape(
            16, scale, 16, scale, 3).mean((1, 3))
        err = np.abs(prgb[idx].astype(np.int16)
                     - tgt.astype(np.int16)).mean(-1)
        high = err > ERROR_FLOOR
        bad = idx == BAD_INDEX
        if not np.array_equal(high, bad):
            st['refused'] = 'large RGB errors no longer equal index 255'
            return sec9, st
        bad_total += int(bad.sum())
        checked.append(row + (bad,))
    if bad_total != BAD_UNITS:
        st['refused'] = 'index-255 count changed (%d)' % bad_total
        return sec9, st

    masked = bytearray(page.data)
    correction = np.zeros((px, px, 3), np.uint8)
    duplicates = {}
    for t, cx, cy, sx, sy, _idx, crops, bad in checked:
        dst = np.frombuffer(masked, np.uint8).reshape(256, 256)[
            sy:sy + 16, sx:sx + 16]
        dst[bad] = 0
        bad_hi = np.repeat(np.repeat(bad, scale, 0), scale, 1)
        premul = ((crops[..., :3].astype(np.uint16)
                   * crops[..., 3:4].astype(np.uint16) + 127) // 255)
        floor = premul.min(axis=0).astype(np.uint8)
        block = correction[cy * cell_hi:(cy + 1) * cell_hi,
                           cx * cell_hi:(cx + 1) * cell_hi]
        mask = bad_hi & np.any(floor != 0, axis=-1)
        block[mask] = floor[mask]
        st['pixels'] += int(mask.sum())
        rec = bytearray(sec9[t.off:t.off + TILE_SIZE])
        rec[T_TEX] = BASE_COMPANION
        rec[T_FX] = FX_COMPANION
        duplicates[t.off] = bytes(rec)

    buf = _append_records(sec9, layers, duplicates)
    try:
        out_pages, tex_start, tex_end = FN.parse_texture_block(buf, px)
        out_pages[SOURCE_SLOT] = FN.Page(SOURCE_SLOT, 0, 1, bytes(masked), 256)
        out_pages[BASE_COMPANION] = FN.Page(
            BASE_COMPANION, 0, 2, bytes(px * px * 2), px)
        out_pages[FX_COMPANION] = FN.Page(
            FX_COMPANION, 0, 2, _encode_rgb(correction, px), px)
        out = FN.replace_texture_block(buf, out_pages, tex_start, tex_end)
        chk, _a, _b = FN.parse_texture_block(out, px)
        cmap = {p.slot: p for p in chk if p is not None}
        counts = PC.effective_counts(out, px)
        if (cmap[BASE_COMPANION].depth != 2
                or cmap[FX_COMPANION].depth != 2
                or counts.get(FX_COMPANION, 0) != TARGET_REFS):
            raise ValueError('companion verification failed')
        before = np.frombuffer(page.data, np.uint8)
        after = np.frombuffer(cmap[SOURCE_SLOT].data, np.uint8)
        changed = before != after
        if (int(changed.sum()) != BAD_UNITS
                or not np.all((~changed) | ((before == BAD_INDEX)
                                            & (after == 0)))):
            raise ValueError('page-16 edit population mismatch')
    except Exception as exc:                                  # noqa: BLE001
        st['refused'] = 'post-write verification: %s' % exc
        return sec9, st

    st.update(fields=1, tiles=TARGET_REFS, pages=2, cells=TARGET_REFS,
              cleared=BAD_UNITS)
    return out, st


def _withdrawn_build166_apply_to_flevel(
        archive, payloads, art, encode=None, log=lambda *_a: None, px=None):
    import lgp
    stats = {'fields': 0, 'tiles': 0, 'pages': 0, 'cells': 0,
             'cleared': 0, 'pixels': 0, 'state': -1, 'refused': []}
    if disabled() or art is None:
        return stats
    encode = encode or (lambda raw: archive.encode_field(raw))
    if px is None:
        import ff7nx_fieldbg
        px = ff7nx_fieldbg.page_px()
    nm = next((q for q in archive.names() if q.lower() == TARGET), None)
    if nm is None:
        stats['refused'].append((TARGET, 'field absent'))
        return stats
    try:
        e = archive.index[nm]
        payload = payloads.get(nm)
        raw = (lgp.lzs_decompress(payload[4:]) if payload
               else archive.decompressed(e))
        parts = list(lgp.split_sections(raw))
        new9, st = apply_to_section9(nm, parts[8], parts[3], art, px)
    except Exception as exc:                                  # noqa: BLE001
        stats['refused'].append((nm, 'exception: %s' % exc))
        return stats
    if st['refused']:
        stats['refused'].append((nm, st['refused']))
        return stats
    if not st['fields']:
        return stats
    parts[8] = new9
    payloads[nm] = encode(lgp.join_sections(parts))
    for key in ('fields', 'tiles', 'pages', 'cells', 'cleared', 'pixels'):
        stats[key] += st[key]
    stats['state'] = st['state']
    return stats


def _withdrawn_build166_summarise(st):
    if not st or not st.get('fields'):
        return ''
    return (
        '  MTNVL6 ANIMATED FOG: matched palette 9 to Cosmos runtime state '
        '%d and proved all %d source unit(s) over %.0f/255 RGB error are '
        'exactly index 255 (and every index-255 unit is in that population). '
        'Only those units were keyed on page 16. Added %d paired records on '
        'one black-identity base page and one 768px additive companion, '
        'restoring %d high-resolution Cosmos pixel(s). Page 15, palette-8 '
        'page-16 cells, all other indices, and all original records/palettes/'
        'UVs/states are unchanged. Total pages 8 -> 10; companion bindings '
        '%d/256. Set %s=1 to disable.'
        % (st['state'], st['cleared'], ERROR_FLOOR, st['tiles'], st['pixels'],
           st['tiles'], OFF_ENV))


# ---------------------------------------------------------------- BUILD 167
# Build 166 changed index 255 to index 0.  Both palette entries are black, so
# hardware was guaranteed to show no change.  Keep the failed implementation
# above only to document/reproduce that build; these definitions are the live
# module API.
VALID_TRAJECTORIES = 194
PRIMARY_EXPECTED_PICKS = {1: 38, 2: 2782, 3: 45, 4: 4}
SECONDARY_PAL = 8
SECONDARY_BAD_UNITS = 1
SECONDARY_VALID_TRAJECTORIES = 172
SECONDARY_EXPECTED_PICKS = {3: 1}
SECONDARY_MAX_STATE_ERROR = 1.6
TOTAL_BAD_UNITS = BAD_UNITS + SECONDARY_BAD_UNITS
EXPECTED_PICKS = {1: 38, 2: 2782, 3: 46, 4: 4}
MAX_STATE_ERROR = 3.1


def apply_to_section9(name, sec9, palette_sec, art, px):
    """Replace black 255 units with their best live eight-state palette index."""
    st = {'fields': 0, 'tiles': 0, 'pages': 0, 'cells': 0,
          'replaced': 0, 'state': -1, 'old_error': 0.0,
          'new_error': 0.0, 'picks': {}, 'refused': ''}
    if disabled() or name.lower() != TARGET:
        return sec9, st
    provider = getattr(art, 'provider', None)
    if provider is None or not getattr(provider, 'state_slots', None):
        st['refused'] = 'no runtime-state DDS provider'
        return sec9, st
    try:
        plist, tex_start, tex_end = FN.parse_texture_block(sec9, px)
        pages = {p.slot: p for p in plist if p is not None}
        surv = DC.survey(sec9)
        tiles = MB.read_tiles(sec9, surv, pages)
        colours, _hdr, npg, cpp = MB.palette_colours(palette_sec)
    except Exception as exc:                                  # noqa: BLE001
        st['refused'] = 'parse: %s' % exc
        return sec9, st

    page = pages.get(SOURCE_SLOT)
    if (page is None or page.depth != 1 or page.size_flag or page.px != 256
            or TARGET_PAL >= npg or cpp != 256):
        st['refused'] = 'page/palette signature changed'
        return sec9, st
    # This is the fact Build 166 failed to test.  If it changes, refuse rather
    # than assume the two indices still have identical visual meaning.
    signatures = {
        pal: (int(colours[pal, 0]), int(colours[pal, BAD_INDEX]))
        for pal in (TARGET_PAL, SECONDARY_PAL)
    }
    if signatures != {TARGET_PAL: (0x8000, 0x0000),
                      SECONDARY_PAL: (0x8000, 0x0000)}:
        st['refused'] = 'palette 8/9 entries 0/255 are no longer both black'
        return sec9, st

    all_refs = [t for t in tiles if sec9[t.off + T_FX] == SOURCE_SLOT]
    target = [t for t in all_refs if t.pal == TARGET_PAL]
    other = [t for t in all_refs if t.pal == 8]
    if (len(all_refs) != TOTAL_REFS or len(target) != TARGET_REFS
            or len(other) != OTHER_REFS
            or {t.pal for t in all_refs} != {8, TARGET_PAL}
            or any(t.layer != 2 or sec9[t.off + T_TEX] != 0
                   or sec9[t.off + T_BLEND] != 1
                   or sec9[t.off + 28] != 1 for t in all_refs)):
        st['refused'] = 'page-16 record signature changed'
        return sec9, st

    scale = px // 256
    if scale < 2 or scale * 256 != px:
        st['refused'] = 'truecolor page is not an integer 256px multiple'
        return sec9, st
    states = _runtime_states(provider, TARGET, SOURCE_SLOT, TARGET_PAL,
                             px, {})
    if states is None or len(states) != 8:
        st['refused'] = 'the exact eight runtime DDS states are unavailable'
        return sec9, st

    source = np.frombuffer(page.data, np.uint8).reshape(256, 256)
    used = np.zeros((256, 256), bool)
    cells = set()
    scores = np.zeros(len(states), np.float64)
    samples = 0
    prgb = MA.palette_rgb(colours[TARGET_PAL]).astype(np.float32)
    for t in target:
        u, v = struct.unpack_from('<II', sec9, t.off + T_PACKED)
        cx = int(round(u / UV_SCALE * 16))
        cy = int(round(v / UV_SCALE * 16))
        if not (0 <= cx < 16 and 0 <= cy < 16) or (cx, cy) in cells:
            st['refused'] = 'palette-9 cell map is not unique'
            return sec9, st
        cells.add((cx, cy))
        ys = slice(cy * 16, cy * 16 + 16)
        xs = slice(cx * 16, cx * 16 + 16)
        used[ys, xs] = True
        idx = source[ys, xs]
        for si, rgba in enumerate(states):
            crop = rgba[cy * 16 * scale:(cy + 1) * 16 * scale,
                        cx * 16 * scale:(cx + 1) * 16 * scale, :3]
            low = crop.reshape(16, scale, 16, scale, 3).mean((1, 3))
            scores[si] += np.abs(prgb[idx] - low).sum()
        samples += idx.size * 3
    if len(cells) != TARGET_REFS or not samples:
        st['refused'] = 'palette-9 atlas population changed'
        return sec9, st
    other_cells = set()
    for t in other:
        u, v = struct.unpack_from('<II', sec9, t.off + T_PACKED)
        cx = int(round(u / UV_SCALE * 16))
        cy = int(round(v / UV_SCALE * 16))
        if not (0 <= cx < 16 and 0 <= cy < 16):
            st['refused'] = 'palette-8 cell map is invalid'
            return sec9, st
        other_cells.add((cx, cy))
    if len(other_cells) != OTHER_REFS or cells & other_cells:
        st['refused'] = 'palette-9 cells are no longer palette-exclusive'
        return sec9, st
    best_state = int(scores.argmin())
    st['state'] = best_state
    if best_state != 6:
        st['refused'] = 'current palette no longer matches runtime state 6'
        return sec9, st

    rgb = np.stack([q[..., :3].reshape(
        256, scale, 256, scale, 3).mean((1, 3)) for q in states])
    alpha = np.stack([q[..., 3].reshape(
        256, scale, 256, scale).mean((1, 3)) for q in states])
    bad = used & (source == BAD_INDEX)
    if int(bad.sum()) != BAD_UNITS or not np.all(alpha[:, bad] == 255):
        st['refused'] = 'index-255 count/opacity fingerprint changed'
        return sec9, st

    # Each trajectory is measured from all occurrences of that same palette
    # index in the same 113 cells.  Median removes spatial DDS detail while
    # retaining the palette-driven colour movement between animation states.
    trajectories = np.zeros((256, len(states), 3), np.float32)
    candidates = []
    for idx in range(1, BAD_INDEX):
        pos = used & (source == idx)
        if not pos.any():
            continue
        trajectories[idx] = np.median(rgb[:, pos, :], axis=1)
        candidates.append(idx)
    if len(candidates) != VALID_TRAJECTORIES:
        st['refused'] = 'live palette-trajectory population changed'
        return sec9, st

    cand = np.asarray(candidates, np.int16)
    fixed = source.copy()
    picked = collections.Counter()
    errs_old = np.zeros(len(states), np.float64)
    errs_new = np.zeros(len(states), np.float64)
    yy, xx = np.nonzero(bad)
    for y, x in zip(yy, xx):
        want = rgb[:, y, x]
        err = np.abs(trajectories[cand] - want[None]).mean((1, 2))
        choice = int(cand[int(err.argmin())])
        fixed[y, x] = choice
        picked[choice] += 1
        errs_old += np.abs(want).mean(1)       # index 255 is black
        errs_new += np.abs(trajectories[choice] - want).mean(1)
    old_mean = errs_old / BAD_UNITS
    new_mean = errs_new / BAD_UNITS
    if (dict(picked) != PRIMARY_EXPECTED_PICKS
            or float(new_mean.max()) > MAX_STATE_ERROR):
        st['refused'] = 'replacement trajectory/error fingerprint changed'
        return sec9, st

    # Hardware Build 167 exposed the final isolated speck.  The archive-wide
    # audit predicted it exactly: the disjoint palette-8 population on this
    # page contains one additional fully-opaque index-255 unit.  Reconstruct
    # it independently from palette 8's own eight-state trajectories; never
    # borrow palette 9's indices or colour model.
    states8 = _runtime_states(provider, TARGET, SOURCE_SLOT, SECONDARY_PAL,
                              px, {})
    if states8 is None or len(states8) != 8:
        st['refused'] = 'the exact palette-8 runtime DDS states are unavailable'
        return sec9, st
    used8 = np.zeros((256, 256), bool)
    for cx, cy in other_cells:
        used8[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16] = True
    rgb8 = np.stack([q[..., :3].reshape(
        256, scale, 256, scale, 3).mean((1, 3)) for q in states8])
    alpha8 = np.stack([q[..., 3].reshape(
        256, scale, 256, scale).mean((1, 3)) for q in states8])
    bad8 = used8 & (source == BAD_INDEX)
    if (int(bad8.sum()) != SECONDARY_BAD_UNITS
            or not np.all(alpha8[:, bad8] == 255)):
        st['refused'] = 'palette-8 singleton count/opacity fingerprint changed'
        return sec9, st
    trajectories8 = np.zeros((256, len(states8), 3), np.float32)
    candidates8 = []
    for idx in range(1, BAD_INDEX):
        pos = used8 & (source == idx)
        if not pos.any():
            continue
        trajectories8[idx] = np.median(rgb8[:, pos, :], axis=1)
        candidates8.append(idx)
    if len(candidates8) != SECONDARY_VALID_TRAJECTORIES:
        st['refused'] = 'palette-8 trajectory population changed'
        return sec9, st
    cand8 = np.asarray(candidates8, np.int16)
    picked8 = collections.Counter()
    errs_old8 = np.zeros(len(states8), np.float64)
    errs_new8 = np.zeros(len(states8), np.float64)
    yy8, xx8 = np.nonzero(bad8)
    for y, x in zip(yy8, xx8):
        want = rgb8[:, y, x]
        err = np.abs(trajectories8[cand8] - want[None]).mean((1, 2))
        choice = int(cand8[int(err.argmin())])
        fixed[y, x] = choice
        picked8[choice] += 1
        errs_old8 += np.abs(want).mean(1)
        errs_new8 += np.abs(trajectories8[choice] - want).mean(1)
    if (dict(picked8) != SECONDARY_EXPECTED_PICKS
            or float(errs_new8.max()) > SECONDARY_MAX_STATE_ERROR):
        st['refused'] = 'palette-8 singleton trajectory/error changed'
        return sec9, st
    picked.update(picked8)
    old_mean = (errs_old + errs_old8) / TOTAL_BAD_UNITS
    new_mean = (errs_new + errs_new8) / TOTAL_BAD_UNITS

    try:
        plist[SOURCE_SLOT] = FN.Page(SOURCE_SLOT, 0, 1,
                                     fixed.tobytes(), 256)
        out = FN.replace_texture_block(sec9, plist, tex_start, tex_end)
        chk, _a, _b = FN.parse_texture_block(out, px)
        before = np.frombuffer(page.data, np.uint8)
        after = np.frombuffer(chk[SOURCE_SLOT].data, np.uint8)
        changed = before != after
        if (int(changed.sum()) != TOTAL_BAD_UNITS
                or not np.all((~changed) | (before == BAD_INDEX))
                or collections.Counter(after[changed]) != picked
                or sum(p is not None for p in chk) != len(pages)):
            raise ValueError('post-write population mismatch')
    except Exception as exc:                                  # noqa: BLE001
        st['refused'] = 'post-write verification: %s' % exc
        return sec9, st

    affected = sum(bool(bad[cy * 16:cy * 16 + 16,
                            cx * 16:cx * 16 + 16].any())
                   for cx, cy in cells)
    affected += sum(bool(bad8[cy * 16:cy * 16 + 16,
                              cx * 16:cx * 16 + 16].any())
                    for cx, cy in other_cells)
    st.update(fields=1, tiles=TOTAL_REFS, cells=affected,
              replaced=TOTAL_BAD_UNITS, old_error=float(old_mean.mean()),
              new_error=float(new_mean.mean()),
              picks=dict(sorted(picked.items())))
    return out, st


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_a: None,
                    px=None):
    import lgp
    stats = {'fields': 0, 'tiles': 0, 'pages': 0, 'cells': 0,
             'replaced': 0, 'state': -1, 'old_error': 0.0,
             'new_error': 0.0, 'picks': {}, 'refused': []}
    if disabled() or art is None:
        return stats
    encode = encode or (lambda raw: archive.encode_field(raw))
    if px is None:
        import ff7nx_fieldbg
        px = ff7nx_fieldbg.page_px()
    nm = next((q for q in archive.names() if q.lower() == TARGET), None)
    if nm is None:
        stats['refused'].append((TARGET, 'field absent'))
        return stats
    try:
        entry = archive.index[nm]
        payload = payloads.get(nm)
        raw = (lgp.lzs_decompress(payload[4:]) if payload
               else archive.decompressed(entry))
        parts = list(lgp.split_sections(raw))
        new9, st = apply_to_section9(nm, parts[8], parts[3], art, px)
    except Exception as exc:                                  # noqa: BLE001
        stats['refused'].append((nm, 'exception: %s' % exc))
        return stats
    if st['refused']:
        stats['refused'].append((nm, st['refused']))
        return stats
    if not st['fields']:
        return stats
    parts[8] = new9
    payloads[nm] = encode(lgp.join_sections(parts))
    for key in ('fields', 'tiles', 'pages', 'cells', 'replaced'):
        stats[key] += st[key]
    for key in ('state', 'old_error', 'new_error', 'picks'):
        stats[key] = st[key]
    return stats


def summarise(st):
    if not st or not st.get('fields'):
        return ''
    return (
        '  MTNVL6 ANIMATED FOG: replaced the %d fully-opaque index-255 '
        'black source unit(s) on page 16 with live palette indices %s. '
        'Build 166 changed 255 to index 0, but palette 9 stores both as black '
        '(0x0000/0x8000), so that edit was visually identical. This version '
        'learns each usable index\'s colour trajectory from all eight Cosmos '
        'runtime states and selects the closest trajectory per source unit. '
        'That includes Build 167\'s last one-pixel palette-8 speck as an '
        'independently fingerprinted 255 -> 3 repair: '
        'mean RGB error %.2f -> %.2f/255 across the eight states. The original '
        'paletted page, %d records, UVs, palettes, blend and animation remain; '
        'no page or record is added. Set %s=1 to disable.'
        % (st['replaced'], st['picks'], st['old_error'], st['new_error'],
           st['tiles'], OFF_ENV))
