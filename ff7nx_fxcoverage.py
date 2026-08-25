#!/usr/bin/env python3
"""Continue demonstrably truncated animated FX into a one-cell field margin.

This is the general form of the hardware-proven nvdun1 repair.  It does not
key on a field name.  A candidate band must satisfy all of these conditions:

* layer-1 base art ends exactly one 16-unit cell beyond layer-2 FX;
* that missing cell is outside the original 4:3 picture;
* at least three consecutive rows have the same page/palette/binding shape;
* the source records are active, additive, page-0 based, and source cells are
  unique;
* both the live game palette and the complete Cosmos state set remain visibly
  non-black on the outward source edge;
* the next two inward cells prove the light centre and supply the authored
  radial counterpart, with a complete-state symmetry error below 3/255;
* the reconstructed cell uses only existing palette-index trajectories and
  stays below 3/255 maximum-state error;
* the destination is unoccupied, the paletted page has free atlas cells, and
  the 256 effective-binding cap remains satisfied.

The missing cell is reconstructed from the authored cell on the opposite
side of a proved light centre, then conditioned onto the exact outward-edge
palette indices.  This preserves the real radial falloff and makes the join
byte-identical through every palette-animation state.  Reflecting the edge
cell itself also joined exactly, but duplicated the near half of the light
and therefore invented a second pulsing lobe in the added strip.  The
base/src1 half is preserved, which is the Build-165/166 hardware distinction
that made nvdun1 render.

The Build-168 archive contains thirteen geometric near-matches. Twelve fade
to black at the outward edge and are correctly vetoed; only nvdun1's reported
seven-row band passes the live-edge proof.

SEVENTH_NX_NO_FX_COVERAGE_REPAIR=1 disables the pass. Former FX-edge and
nvdun1 rollback variables remain compatibility aliases.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import field_bg_pagecap as PC
import ff7nx_fxpalette as FP
import ff7nx_marginart as MA
import ff7nx_marginblack as MB


OFF_ENV = 'SEVENTH_NX_NO_FX_COVERAGE_REPAIR'
OLD_OFF_ENVS = ('SEVENTH_NX_NO_FX_EDGE',
                'SEVENTH_NX_NO_NVDUN1_LIGHT_EDGE')
GRID = 16
TILE_SIZE = FN.TILE_SIZE
T_DSTX = DC.TILE_DST_X
T_DSTY = DC.TILE_DST_Y
T_BASE = FN.TILE_TEXTURE_ID
T_FX = FN.TILE_TEXTURE_ID2
T_PAL = 22
T_FLAG = 28
T_BLEND = 30
T_PACKED = 42
UV_SCALE = 10_000_000
PIC_LO = -160
PIC_HI = 160
MIN_ROWS = 3
MIN_CURRENT_EDGE = 10.0
MIN_STATE_EDGE = 10.0
MIN_NONBLACK = 0.5
MAX_CENTRE_SYMMETRY = 3.0
MAX_TRAJECTORY_ERROR = 3.0


def disabled():
    return (os.environ.get(OFF_ENV) == '1'
            or any(os.environ.get(key) == '1' for key in OLD_OFF_ENVS))


def _runs(rows):
    result = []
    current = []
    for y in sorted(set(rows)):
        if current and y != current[-1] + GRID:
            result.append(tuple(current))
            current = []
        current.append(y)
    if current:
        result.append(tuple(current))
    return result


def _cell(tile, sec9):
    u, v = struct.unpack_from('<II', sec9, tile.off + T_PACKED)
    return (int(round(u / UV_SCALE * 16)),
            int(round(v / UV_SCALE * 16)))


def _write_cell(cell, dst, dx, dy):
    for row in range(GRID):
        do = (dy + row) * 256 + dx
        dst[do:do + GRID] = cell[row].tobytes()


def _block(array, coord):
    cx, cy = coord
    return array[..., cy * GRID:(cy + 1) * GRID,
                 cx * GRID:(cx + 1) * GRID, :]


def _indexed_block(array, coord):
    cx, cy = coord
    return array[cy * GRID:(cy + 1) * GRID,
                 cx * GRID:(cx + 1) * GRID]


def _conditioned_radial_cell(source, rgb, trajectories, candidates,
                             edge_coord, opposite_coord, side):
    """Return (indices, mean error, max-state error).

    `opposite_coord` is two cells inward, on the far side of the light's
    proved centre.  Reflecting it gives the authored radial counterpart of
    the missing outer cell.  A linear seam condition removes the small
    asymmetry present in the DDS, and existing palette-index trajectories
    quantise that target across every runtime state.  The join column is then
    forced to the exact source indices, which is the hardware-visible proof.
    """
    edge_rgb = _block(rgb, edge_coord)
    radial = _block(rgb, opposite_coord)[..., ::-1, :]
    edge_idx = _indexed_block(source, edge_coord)
    if side < 0:
        join_target = edge_rgb[:, :, :1]
        join_radial = radial[:, :, -1:]
        weight = np.linspace(0, 1, GRID, dtype=np.float32)
        join_column = GRID - 1
        source_column = 0
    else:
        join_target = edge_rgb[:, :, -1:]
        join_radial = radial[:, :, :1]
        weight = np.linspace(1, 0, GRID, dtype=np.float32)
        join_column = 0
        source_column = GRID - 1
    target = np.clip(
        radial + weight[None, None, :, None] * (join_target - join_radial),
        0, 255)
    flat = target.transpose(1, 2, 0, 3).reshape(-1, len(rgb), 3)
    cand = np.asarray(candidates, np.int16)
    choices = np.empty(len(flat), np.uint8)
    for start in range(0, len(flat), 256):
        want = flat[start:start + 256]
        error = np.abs(
            want[:, None] - trajectories[cand][None]).mean((2, 3))
        choices[start:start + len(want)] = cand[error.argmin(1)]
    result = choices.reshape(GRID, GRID)
    result[:, join_column] = edge_idx[:, source_column]
    rendered = trajectories[result].transpose(2, 0, 1, 3)
    per_state = np.abs(rendered - target).mean((1, 2, 3))
    return result, float(per_state.mean()), float(per_state.max())


def _candidate_groups(name, sec9, palette_sec, art, px, pages, tiles):
    """Return (admitted groups, census) without modifying the section."""
    census = collections.Counter()
    base = collections.defaultdict(set)
    fx = collections.defaultdict(lambda: collections.defaultdict(list))
    owners = collections.defaultdict(collections.Counter)
    for tile in tiles:
        page = sec9[tile.off + T_FX]
        if tile.layer == 1 and not page:
            base[tile.dy].add(tile.dx)
        if tile.layer == 2 and page:
            fx[tile.dy][tile.dx].append(tile)
        if page:
            cx, cy = _cell(tile, sec9)
            owners[(page, cx, cy)][
                (tile.pal, tile.layer, sec9[tile.off + T_BASE],
                 sec9[tile.off + T_FLAG], sec9[tile.off + T_BLEND])] += 1

    grouped = collections.defaultdict(list)
    for y, cols in fx.items():
        bcols = base.get(y, set())
        if not bcols:
            continue
        for side in (-1, 1):
            edge = min(cols) if side < 0 else max(cols)
            missing = edge + side * GRID
            if missing not in bcols or missing in cols:
                continue
            # The base itself must end at the missing cell. An interior gap
            # is a shaped lamp/shaft, not a widescreen truncation.
            if (min(bcols) if side < 0 else max(bcols)) != missing:
                continue
            # It must be in the added widescreen area, never the 4:3 picture.
            if not (missing < PIC_LO if side < 0 else missing >= PIC_HI):
                continue
            if len(cols[edge]) != 1:
                continue
            tile = cols[edge][0]
            page_id = sec9[tile.off + T_FX]
            page = pages.get(page_id)
            if (page is None or page.depth != 1 or page.size_flag
                    or page.px != 256):
                continue
            meta = (side, edge, missing, page_id, tile.pal,
                    sec9[tile.off + T_BASE], sec9[tile.off + T_FLAG],
                    sec9[tile.off + T_BLEND])
            grouped[meta].append(tile)

    provider = getattr(art, 'provider', None)
    if provider is None:
        census['provider_veto'] += len(grouped)
        return [], census
    colours, _hdr, npg, cpp = MB.palette_colours(palette_sec)
    if cpp != 256:
        census['provider_veto'] += len(grouped)
        return [], census
    state_cache = {}
    trajectory_cache = {}
    admitted = []
    for meta, population in grouped.items():
        side, edge, missing, page_id, pal, base_id, flag, blend = meta
        by_y = {tile.dy: tile for tile in population}
        for run in _runs(by_y):
            if len(run) < MIN_ROWS:
                continue
            census['geometry'] += 1
            records = [by_y[y] for y in run]
            if (base_id, flag, blend) != (0, 1, 1):
                census['record_veto'] += 1
                continue
            coords = [_cell(tile, sec9) for tile in records]
            if (len(set(coords)) != len(coords)
                    or any(not (0 <= cx < 16 and 0 <= cy < 16)
                           for cx, cy in coords)
                    or pal >= npg):
                census['record_veto'] += 1
                continue
            exact_owner = collections.Counter({(pal, 2, 0, 1, 1): 1})
            if any(owners[(page_id, cx, cy)] != exact_owner
                   for cx, cy in coords):
                census['record_veto'] += 1
                continue

            source = np.frombuffer(pages[page_id].data,
                                   np.uint8).reshape(256, 256)
            prgb = MA.palette_rgb(colours[pal])
            current_values = []
            current_nonblack = []
            for cx, cy in coords:
                cell = source[cy * GRID:(cy + 1) * GRID,
                              cx * GRID:(cx + 1) * GRID]
                edge_idx = cell[:, :1] if side < 0 else cell[:, -1:]
                edge_rgb = prgb[edge_idx]
                current_values.append(float(edge_rgb.mean()))
                current_nonblack.append(
                    float(np.any(edge_rgb != 0, axis=2).mean()))
            current_luma = sum(current_values) / len(current_values)
            current_cover = sum(current_nonblack) / len(current_nonblack)
            if (current_luma <= MIN_CURRENT_EDGE
                    or current_cover <= MIN_NONBLACK):
                census['dark_veto'] += 1
                continue

            key = (name, page_id, pal)
            states = FP._runtime_states(provider, key, px, state_cache)
            if not states:
                census['state_veto'] += 1
                continue
            scale = px // 256
            per_state_luma = []
            per_state_cover = []
            for image in states:
                values = []
                covers = []
                for cx, cy in coords:
                    cell = image[cy * GRID * scale:(cy + 1) * GRID * scale,
                                 cx * GRID * scale:(cx + 1) * GRID * scale]
                    edge_img = (cell[:, :scale] if side < 0
                                else cell[:, -scale:])
                    rgb = edge_img[..., :3]
                    values.append(float(rgb.mean()))
                    covers.append(float(np.any(rgb != 0, axis=2).mean()))
                per_state_luma.append(sum(values) / len(values))
                per_state_cover.append(sum(covers) / len(covers))
            if (sum(per_state_luma) / len(per_state_luma) <= MIN_STATE_EDGE
                    or min(per_state_cover) <= MIN_NONBLACK):
                census['state_veto'] += 1
                continue

            # A radial continuation needs the cells immediately inward and
            # two cells inward. The first proves the centre (edge ~= flipped
            # inner); the second is the authored counterpart of the missing
            # outer cell. Merely having a lit edge is not enough.
            inner_x = edge - side * GRID
            opposite_x = edge - side * GRID * 2
            inner_records = []
            opposite_records = []
            shape_ok = True
            expected_sig = (page_id, pal, 0, 1, 1)
            for y in run:
                near = fx[y].get(inner_x, ())
                far = fx[y].get(opposite_x, ())
                if len(near) != 1 or len(far) != 1:
                    shape_ok = False
                    break
                inner_records.append(near[0])
                opposite_records.append(far[0])
            if shape_ok:
                for tile in inner_records + opposite_records:
                    sig = (sec9[tile.off + T_FX], tile.pal,
                           sec9[tile.off + T_BASE],
                           sec9[tile.off + T_FLAG],
                           sec9[tile.off + T_BLEND])
                    if sig != expected_sig:
                        shape_ok = False
                        break
            if not shape_ok:
                census['shape_veto'] += 1
                continue
            inner_coords = [_cell(tile, sec9) for tile in inner_records]
            opposite_coords = [_cell(tile, sec9)
                               for tile in opposite_records]
            all_coords = coords + inner_coords + opposite_coords
            if (len(set(all_coords)) != len(all_coords)
                    or any(owners[(page_id, cx, cy)] != exact_owner
                           for cx, cy in inner_coords + opposite_coords)):
                census['shape_veto'] += 1
                continue

            scale = px // 256
            rgb = np.stack([image[..., :3].reshape(
                256, scale, 256, scale, 3).mean((1, 3))
                for image in states]).astype(np.float32)
            symmetry = float(np.mean([
                np.abs(_block(rgb, edge_coord)
                       - _block(rgb, inner_coord)[..., ::-1, :]).mean()
                for edge_coord, inner_coord in zip(coords, inner_coords)
            ]))
            if symmetry > MAX_CENTRE_SYMMETRY:
                census['shape_veto'] += 1
                continue

            trajectory_key = (page_id, pal)
            trajectory = trajectory_cache.get(trajectory_key)
            if trajectory is None:
                mask = np.zeros((256, 256), bool)
                for (owned_page, cx, cy), population in owners.items():
                    if (owned_page == page_id
                            and set(population) == {(pal, 2, 0, 1, 1)}):
                        mask[cy * GRID:(cy + 1) * GRID,
                             cx * GRID:(cx + 1) * GRID] = True
                candidates = np.unique(source[mask]).astype(np.int16)
                trajectories = np.zeros(
                    (256, len(states), 3), np.float32)
                for idx in candidates:
                    pos = mask & (source == idx)
                    trajectories[idx] = np.median(rgb[:, pos, :], axis=1)
                trajectory = (trajectories, tuple(int(x)
                                                   for x in candidates))
                trajectory_cache[trajectory_key] = trajectory
            trajectories, candidates = trajectory
            if not candidates:
                census['fit_veto'] += 1
                continue
            new_cells = []
            mean_errors = []
            max_errors = []
            for edge_coord, opposite_coord in zip(coords, opposite_coords):
                cell, mean_error, max_error = _conditioned_radial_cell(
                    source, rgb, trajectories, candidates,
                    edge_coord, opposite_coord, side)
                new_cells.append(cell)
                mean_errors.append(mean_error)
                max_errors.append(max_error)
            fit_mean = sum(mean_errors) / len(mean_errors)
            fit_max = max(max_errors)
            if fit_max > MAX_TRAJECTORY_ERROR:
                census['fit_veto'] += 1
                continue

            census['admitted'] += 1
            admitted.append({
                'side': side, 'edge': edge, 'missing': missing,
                'page': page_id, 'pal': pal, 'records': records,
                'coords': coords, 'inner_coords': inner_coords,
                'opposite_coords': opposite_coords,
                'new_cells': new_cells, 'states': len(states),
                'current_luma': current_luma,
                'state_luma': sum(per_state_luma) / len(per_state_luma),
                'symmetry': symmetry, 'fit_mean': fit_mean,
                'fit_max': fit_max,
            })
    return admitted, census


def apply_to_section9(name, sec9, palette_sec, art, px):
    st = {'fields': 0, 'bands': 0, 'tiles': 0, 'rows': 0,
          'cells': 0, 'pages': 0, 'capped': 0, 'geometry': 0,
          'dark_veto': 0, 'state_veto': 0, 'record_veto': 0,
          'provider_veto': 0, 'shape_veto': 0, 'fit_veto': 0,
          'details': [], 'refused': ''}
    if disabled() or sec9.find(b'BACK') < 0:
        return sec9, st
    try:
        plist, tex_start, tex_end = FN.parse_texture_block(sec9, px)
        pages = {page.slot: page for page in plist if page is not None}
        surv = DC.survey(sec9)
        tiles = MB.read_tiles(sec9, surv, pages)
        groups, census = _candidate_groups(
            name.lower(), sec9, palette_sec, art, px, pages, tiles)
        st.update(census)
    except Exception as exc:                                  # noqa: BLE001
        st['refused'] = 'scan: %s' % exc
        return sec9, st
    if not groups:
        return sec9, st

    used = collections.defaultdict(set)
    for tile in tiles:
        cx, cy = _cell(tile, sec9)
        for page_id in (sec9[tile.off + T_BASE], sec9[tile.off + T_FX]):
            if page_id:
                used[page_id].add((cx, cy))
    free = {}
    for page_id in {group['page'] for group in groups}:
        free[page_id] = [(cx, cy) for cy in range(16) for cx in range(16)
                         if (cx, cy) not in used[page_id]]
    counts = PC.effective_counts(sec9, px)
    needed = collections.Counter(
        group['page'] for group in groups for _tile in group['records'])
    for page_id, number in needed.items():
        if (len(free[page_id]) < number
                or counts.get(page_id, 0) + number > PC.MAX_TILES_PER_PAGE):
            st['capped'] += number
            return sec9, st

    page_data = {page_id: bytearray(pages[page_id].data)
                 for page_id in needed}
    rec_at = {}
    allocations = collections.Counter()
    for group in groups:
        page_id = group['page']
        for tile, new_cell in zip(group['records'], group['new_cells']):
            if tile.off in rec_at:
                st['refused'] = 'one source record belongs to two bands'
                return sec9, st
            index = allocations[page_id]
            cx, cy = free[page_id][index]
            allocations[page_id] += 1
            _write_cell(new_cell, page_data[page_id],
                        cx * GRID, cy * GRID)
            rec = bytearray(sec9[tile.off:tile.off + TILE_SIZE])
            struct.pack_into('<h', rec, T_DSTX, group['missing'])
            rec[14] = cx * GRID
            rec[16] = cy * GRID
            step = UV_SCALE // 16
            struct.pack_into('<II', rec, T_PACKED, cx * step, cy * step)
            rec_at[tile.off] = bytes(rec)
        st['details'].append(
            (name.lower(), group['page'], group['pal'], group['side'],
             group['missing'], tuple(tile.dy for tile in group['records']),
             group['states'], group['current_luma'], group['state_luma'],
             group['symmetry'], group['fit_mean'], group['fit_max']))

    layers = []
    import ff7nx_parallaxfill as PF
    for layer in PF._layers(sec9, surv['back_start'], surv['tex_start']):
        layers.append(layer)
    buf = bytearray(sec9)
    for _layer, count_at, first, number in sorted(layers, reverse=True):
        end = first + number * TILE_SIZE
        records = [rec_at[off] for off in sorted(rec_at)
                   if first <= off < end]
        if not records:
            continue
        buf[end:end] = b''.join(records)
        struct.pack_into('<H', buf, count_at, number + len(records))

    try:
        after_pages, after_start, after_end = FN.parse_texture_block(
            bytes(buf), px)
        for page_id, data in page_data.items():
            old = after_pages[page_id]
            after_pages[page_id] = FN.Page(
                page_id, old.size_flag, old.depth, bytes(data), old.px)
        out = FN.replace_texture_block(bytes(buf), after_pages,
                                       after_start, after_end)
        checked, _a, _b = FN.parse_texture_block(out, px)
        if (sum(page is not None for page in checked) != len(pages)
                or any(checked[page_id].data != bytes(data)
                       for page_id, data in page_data.items())):
            raise ValueError('page population or continued data changed')
    except Exception as exc:                                  # noqa: BLE001
        st['refused'] = 'post-write verification: %s' % exc
        return sec9, {**st, 'fields': 0, 'bands': 0, 'tiles': 0}

    st['fields'] = 1
    st['bands'] = len(groups)
    st['tiles'] = len(rec_at)
    st['rows'] = len(rec_at)
    st['cells'] = len(rec_at)
    return out, st


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_a: None,
                    px=None):
    import lgp
    stats = {'fields': 0, 'bands': 0, 'tiles': 0, 'rows': 0,
             'cells': 0, 'pages': 0, 'capped': 0, 'geometry': 0,
             'dark_veto': 0, 'state_veto': 0, 'record_veto': 0,
             'provider_veto': 0, 'shape_veto': 0, 'fit_veto': 0,
             'details': [], 'refused': []}
    if disabled() or art is None:
        return stats
    encode = encode or (lambda raw: archive.encode_field(raw))
    if px is None:
        import ff7nx_fieldbg
        px = ff7nx_fieldbg.page_px()
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            payload = payloads.get(name)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            new9, one = apply_to_section9(
                name, parts[8], parts[3], art, px)
        except Exception as exc:                              # noqa: BLE001
            if name.lower() not in ('blackbgb', 'blackbgb.xone'):
                stats['refused'].append((name, 'exception: %s' % exc))
            continue
        for key in ('geometry', 'dark_veto', 'state_veto', 'record_veto',
                    'provider_veto', 'shape_veto', 'fit_veto', 'capped'):
            stats[key] += one[key]
        if one['refused']:
            if name.lower() not in ('blackbgb', 'blackbgb.xone'):
                stats['refused'].append((name, one['refused']))
            continue
        if not one['fields']:
            continue
        parts[8] = new9
        payloads[name] = encode(lgp.join_sections(parts))
        for key in ('fields', 'bands', 'tiles', 'rows', 'cells', 'pages'):
            stats[key] += one[key]
        stats['details'].extend(one['details'])
    return stats


def summarise(st):
    if not st or not st.get('fields'):
        return ''
    names = ', '.join(sorted({detail[0] for detail in st['details']}))
    mean_fit = sum(detail[10] for detail in st['details']) / len(st['details'])
    max_fit = max(detail[11] for detail in st['details'])
    symmetry = sum(detail[9] for detail in st['details']) / len(st['details'])
    return (
        '  ANIMATED FX COVERAGE: continued %d live FX cell(s) across %d '
        'outside-4:3 edge band(s) in %d field(s) (%s). The archive-wide '
        'detector found %d geometric near-match(es); %d faded to black at '
        'the game-palette edge and %d more failed the complete Cosmos-state '
        'edge proof, so they remain untouched. %d more lacked a proved '
        'three-cell radial centre/counterpart and %d failed trajectory '
        'reconstruction. Each admitted band has base '
        'art exactly one cell farther out, an exclusive active additive '
        'source population, visible colour through that boundary, free atlas '
        'coordinates, and binding capacity. The missing cell comes from the '
        'authored cell opposite the proved light centre, conditioned through '
        'existing palette-index trajectories onto the exact join (centre '
        'symmetry %.2f/255, trajectory error mean %.2f, max-state %.2f/255). '
        'This continues the real outward falloff instead of reflecting the '
        'near half into a second light lobe. Base/src1 stays unchanged. No '
        'page is added '
        'and no existing record, UV, palette, blend, or animation changes. '
        'Set %s=1 to disable.'
        % (st['tiles'], st['bands'], st['fields'], names, st['geometry'],
           st['dark_veto'], st['state_veto'], st['shape_veto'],
           st['fit_veto'], symmetry, mean_fit, max_fit, OFF_ENV))
