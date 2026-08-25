#!/usr/bin/env python3
"""Repair proven opaque-black units in live paletted field FX.

Cosmos/FFNx replaces an animated paletted page with a sequence of DDS images.
On this port the original 8-bit page remains live.  A source unit whose index
is 255 therefore draws palette entry 255; in the affected FX tables that entry
is black, even though every Cosmos state paints opaque colour at that exact
position.  On an additive layer black contributes nothing and appears as a
square hole or stipple.

This module is general machinery with an audited fingerprint manifest.  For
each admitted (field, page, palette), it learns every usable palette index's
colour trajectory from that slot's own Cosmos runtime states and substitutes
only index-255 units whose exact count, ownership, opacity, trajectory
population, chosen indices, and error bound match the audit.  One mismatch
refuses the whole field.  No page, record, UV, palette, blend, or animation
state is added or moved.

The archive-wide Build-168 audit examined 638 multi-state slots in 196 fields.
Only the eleven slots below passed every safe reconstruction gate.  mtnvl4
page 16/palette 10 has the same black-source symptom, but its best available
live trajectory reaches 67/255 error in one state; it is deliberately deferred.

SEVENTH_NX_NO_FX_PALETTE_REPAIR=1 disables this pass.  The former mtnvl6
rollback is accepted as a compatibility alias.
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


OFF_ENV = 'SEVENTH_NX_NO_FX_PALETTE_REPAIR'
OLD_OFF_ENV = 'SEVENTH_NX_NO_MTNVL6_FX_REPAIR'
BAD_INDEX = 255
T_FX = FN.TILE_TEXTURE_ID2
T_BASE = FN.TILE_TEXTURE_ID
T_PAL = 22
T_FLAG = 28
T_BLEND = 30
T_PACKED = 42
UV_SCALE = 10_000_000


def _spec(states, records, bad, live, picks, max_error):
    return {'states': states, 'records': records, 'cells': records,
            'bad': bad, 'live': live, 'picks': picks,
            'max_error': max_error}


# Exact Build-168 input fingerprints.  This is an audited allow-list, not a
# field-name implementation: all reconstruction and safety logic below is
# shared, and each field supplies its own runtime-state proof.
TARGETS = {
    ('gidun_1', 15, 7): _spec(8, 104, 168, 163, {2: 168}, 10.5),
    ('gidun_1', 16, 6): _spec(8, 97, 40, 175, {1: 40}, 3.3),
    ('mtnvl2', 15, 13): _spec(8, 255, 101, 195, {1: 101}, 4.1),
    ('mtnvl2', 16, 12): _spec(8, 254, 57, 244, {1: 57}, 3.7),
    ('mtnvl2', 17, 11): _spec(8, 49, 109, 216, {3: 109}, 8.3),
    ('mtnvl3', 16, 10): _spec(
        9, 212, 2716, 206, {1: 168, 2: 2533, 4: 2, 5: 12, 6: 1}, 4.1),
    ('mtnvl5', 16, 7): _spec(8, 196, 84, 180, {1: 84}, 5.3),
    ('mtnvl6', 16, 8): _spec(8, 46, 1, 172, {3: 1}, 1.7),
    ('mtnvl6', 16, 9): _spec(
        8, 113, 2869, 194, {1: 38, 2: 2782, 3: 45, 4: 4}, 3.2),
    ('psdun_4', 15, 4): _spec(8, 84, 129, 203, {1: 129}, 5.5),
    ('psdun_4', 15, 5): _spec(8, 86, 21, 215, {1: 21}, 5.1),
    ('psdun_4', 15, 6): _spec(8, 78, 85, 198, {1: 85}, 5.8),
}

TARGET_FIELDS = tuple(sorted({key[0] for key in TARGETS}))
DEFERRED = {('mtnvl4', 16, 10):
            '50 units; best live trajectory reaches 67.05/255 error'}
EXPECTED_FIELDS = 6
EXPECTED_SLOTS = 12
EXPECTED_RECORDS = 1574
EXPECTED_UNITS = 6380


def disabled():
    return os.environ.get(OFF_ENV) == '1' or os.environ.get(OLD_OFF_ENV) == '1'


def _runtime_states(provider, key, px, cache):
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
    cache[key] = tuple(out)
    return cache[key]


def _cell(tile, sec9):
    u, v = struct.unpack_from('<II', sec9, tile.off + T_PACKED)
    return (int(round(u / UV_SCALE * 16)),
            int(round(v / UV_SCALE * 16)))


def apply_to_section9(name, sec9, palette_sec, art, px):
    """Return (section9, stats); refuse the complete field on any mismatch."""
    name = name.lower()
    specs = [(key, value) for key, value in TARGETS.items() if key[0] == name]
    st = {'fields': 0, 'slots': 0, 'records': 0, 'pages': 0,
          'cells': 0, 'replaced': 0, 'old_error': 0.0,
          'new_error': 0.0, 'details': [], 'refused': ''}
    if disabled() or not specs:
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
    scale = px // 256
    if scale < 2 or scale * 256 != px or cpp != 256:
        st['refused'] = 'page/palette scale signature changed'
        return sec9, st

    owners = collections.defaultdict(set)
    for tile in tiles:
        page = sec9[tile.off + T_FX]
        if not page:
            continue
        cx, cy = _cell(tile, sec9)
        if not (0 <= cx < 16 and 0 <= cy < 16):
            st['refused'] = 'FX cell coordinate is invalid'
            return sec9, st
        owners[(page, cx, cy)].add(
            (tile.pal, tile.layer, sec9[tile.off + T_BASE],
             sec9[tile.off + T_FLAG], sec9[tile.off + T_BLEND]))

    originals = {}
    fixed = {}
    err_old_sum = 0.0
    err_new_sum = 0.0
    state_cache = {}
    all_changed_masks = collections.defaultdict(
        lambda: np.zeros((256, 256), bool))

    for key, spec in sorted(specs):
        _field, page_id, pal = key
        page = pages.get(page_id)
        if (page is None or page.depth != 1 or page.size_flag or page.px != 256
                or pal >= npg):
            st['refused'] = '%s page/palette signature changed' % (key,)
            return sec9, st
        if (int(colours[pal, 0]), int(colours[pal, BAD_INDEX])) \
                != (0x8000, 0x0000):
            st['refused'] = '%s black-entry signature changed' % (key,)
            return sec9, st
        matches = [tile for tile in tiles
                   if sec9[tile.off + T_FX] == page_id and tile.pal == pal]
        cells = {_cell(tile, sec9) for tile in matches}
        if (len(matches) != spec['records'] or len(cells) != spec['cells']
                or any(tile.layer != 2
                       or sec9[tile.off + T_BASE] != 0
                       or sec9[tile.off + T_FLAG] != 1
                       or sec9[tile.off + T_BLEND] != 1
                       for tile in matches)):
            st['refused'] = '%s record/cell ownership changed' % (key,)
            return sec9, st

        source = originals.setdefault(
            page_id, np.frombuffer(page.data, np.uint8).reshape(256, 256).copy())
        target = fixed.setdefault(page_id, source.copy())
        used = np.zeros((256, 256), bool)
        for cx, cy in cells:
            used[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16] = True
        bad = used & (source == BAD_INDEX)
        if int(bad.sum()) != spec['bad']:
            st['refused'] = '%s index-255 population changed' % (key,)
            return sec9, st
        bad_cells = {(x // 16, y // 16) for y, x in zip(*np.nonzero(bad))}
        expected_owner = {(pal, 2, 0, 1, 1)}
        if any(owners[(page_id, cx, cy)] != expected_owner
               for cx, cy in bad_cells):
            st['refused'] = '%s bad cell is shared or changed' % (key,)
            return sec9, st

        states = _runtime_states(provider, key, px, state_cache)
        if states is None or len(states) != spec['states']:
            st['refused'] = '%s runtime-state population changed' % (key,)
            return sec9, st
        rgb = np.stack([img[..., :3].reshape(
            256, scale, 256, scale, 3).mean((1, 3)) for img in states])
        alpha = np.stack([img[..., 3].reshape(
            256, scale, 256, scale).mean((1, 3)) for img in states])
        if not np.all(alpha[:, bad] == 255):
            st['refused'] = '%s target is not fully opaque' % (key,)
            return sec9, st
        if min(float(rgb[i][bad].mean()) for i in range(len(states))) <= 64.0:
            st['refused'] = '%s target is not coloured in every state' % (key,)
            return sec9, st

        trajectories = np.zeros((256, len(states), 3), np.float32)
        candidates = []
        for idx in range(1, BAD_INDEX):
            pos = used & (source == idx)
            if not pos.any():
                continue
            trajectories[idx] = np.median(rgb[:, pos, :], axis=1)
            candidates.append(idx)
        if len(candidates) != spec['live']:
            st['refused'] = '%s live trajectory population changed' % (key,)
            return sec9, st

        cand = np.asarray(candidates, np.int16)
        picked = collections.Counter()
        old = np.zeros(len(states), np.float64)
        new = np.zeros(len(states), np.float64)
        yy, xx = np.nonzero(bad)
        for y, x in zip(yy, xx):
            want = rgb[:, y, x]
            error = np.abs(trajectories[cand] - want[None]).mean((1, 2))
            choice = int(cand[int(error.argmin())])
            target[y, x] = choice
            picked[choice] += 1
            old += np.abs(want).mean(1)
            new += np.abs(trajectories[choice] - want).mean(1)
        old /= spec['bad']
        new /= spec['bad']
        if (dict(picked) != spec['picks']
                or float(new.max()) > spec['max_error']):
            st['refused'] = '%s chosen trajectory/error changed' % (key,)
            return sec9, st
        if np.any(all_changed_masks[page_id] & bad):
            st['refused'] = '%s overlaps another repaired population' % (key,)
            return sec9, st
        all_changed_masks[page_id] |= bad
        err_old_sum += float(old.mean()) * spec['bad']
        err_new_sum += float(new.mean()) * spec['bad']
        st['slots'] += 1
        st['records'] += spec['records']
        st['cells'] += len(cells)
        st['replaced'] += spec['bad']
        st['details'].append((key, spec['bad'], dict(sorted(picked.items())),
                              float(old.mean()), float(new.mean()),
                              float(new.max())))

    try:
        for page_id, arr in fixed.items():
            old_page = pages[page_id]
            plist[page_id] = FN.Page(page_id, old_page.size_flag,
                                     old_page.depth, arr.tobytes(), old_page.px)
        out = FN.replace_texture_block(sec9, plist, tex_start, tex_end)
        check, check_start, check_end = FN.parse_texture_block(out, px)
        if (sec9[:tex_start] != out[:check_start]
                or sec9[tex_end:] != out[check_end:]
                or sum(p is not None for p in check) != len(pages)):
            raise ValueError('records, suffix, or page population changed')
        for page_id, before in originals.items():
            after = np.frombuffer(check[page_id].data, np.uint8).reshape(256, 256)
            changed = before != after
            expected = all_changed_masks[page_id]
            if (not np.array_equal(changed, expected)
                    or not np.all(before[changed] == BAD_INDEX)):
                raise ValueError('post-write changed-unit population mismatch')
        for page_id, old_page in pages.items():
            if page_id not in originals and check[page_id].data != old_page.data:
                raise ValueError('unrelated page changed')
    except Exception as exc:                                  # noqa: BLE001
        st['refused'] = 'post-write verification: %s' % exc
        return sec9, {**st, 'fields': 0}

    st['fields'] = 1
    st['old_error'] = err_old_sum / st['replaced']
    st['new_error'] = err_new_sum / st['replaced']
    return out, st


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_a: None,
                    px=None):
    import lgp
    stats = {'fields': 0, 'slots': 0, 'records': 0, 'pages': 0,
             'cells': 0, 'replaced': 0, 'old_error': 0.0,
             'new_error': 0.0, 'details': [], 'refused': []}
    if disabled() or art is None:
        return stats
    encode = encode or (lambda raw: archive.encode_field(raw))
    if px is None:
        import ff7nx_fieldbg
        px = ff7nx_fieldbg.page_px()
    weighted_old = weighted_new = 0.0
    names = {name.lower(): name for name in archive.names()}
    for lower in TARGET_FIELDS:
        name = names.get(lower)
        if name is None:
            stats['refused'].append((lower, 'field absent'))
            continue
        try:
            entry = archive.index[name]
            payload = payloads.get(name)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            new9, one = apply_to_section9(lower, parts[8], parts[3], art, px)
        except Exception as exc:                              # noqa: BLE001
            stats['refused'].append((lower, 'exception: %s' % exc))
            continue
        if one['refused']:
            stats['refused'].append((lower, one['refused']))
            continue
        if not one['fields']:
            continue
        parts[8] = new9
        payloads[name] = encode(lgp.join_sections(parts))
        for key in ('fields', 'slots', 'records', 'pages', 'cells', 'replaced'):
            stats[key] += one[key]
        weighted_old += one['old_error'] * one['replaced']
        weighted_new += one['new_error'] * one['replaced']
        stats['details'].extend(one['details'])
    if stats['replaced']:
        stats['old_error'] = weighted_old / stats['replaced']
        stats['new_error'] = weighted_new / stats['replaced']
    return stats


def summarise(st):
    if not st or not st.get('fields'):
        return ''
    names = ', '.join(sorted({detail[0][0] for detail in st['details']}))
    return (
        '  ANIMATED FX PALETTE: reconstructed %s fully-opaque black source '
        'unit(s) across %d live page/palette slot(s) in %d field(s) (%s). '
        'Each slot uses its own complete Cosmos runtime-state set and only '
        'existing palette-index trajectories; weighted mean RGB error '
        '%.2f -> %.2f/255. Every affected cell is exclusively owned by its '
        'proved layer-2 additive record population. No page, record, UV, '
        'palette, blend, or animation state was added or moved. mtnvl4 '
        'page16/palette10 remains unchanged because its best live trajectory '
        'reaches 67.05/255 error. Set %s=1 to disable.'
        % (f"{st['replaced']:,}", st['slots'], st['fields'], names,
           st['old_error'], st['new_error'], OFF_ENV))
