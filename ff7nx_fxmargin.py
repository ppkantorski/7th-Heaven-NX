#!/usr/bin/env python3
"""Restore complete Cosmos animated/FX effects that cross the 4:3 boundary.

Cosmos can point many layer-2+ tiles at one base/FX cell and rely on external
textures to supply the real pixels.  On the native paletted path the FX atlas
can be shared across palettes, so an in-place write is unsafe.  This pass
copies each complete FX atlas at the SAME coordinates: mds7plr1 onto
palette-pure pages, and the two MDS5 proof effects onto one full-resolution
truecolor page each. The base page and runtime UV never move. The complete
effect is repointed, including its 4:3 cells.

Build 136 proved that a cell-by-cell admission rule is invalid for a lighting
sheet: mds5_2 moved 64 of 85 cells and mds5_3 moved 63 of 144, producing the
visible missing-square pattern.  The repair is deliberately limited to the
three hardware-observed fields and is all-or-nothing by complete field effect.
It also carries the Cosmos FX reconstruction through the 4:3 picture instead
of creating a new old/Cosmos boundary there.

Build 137 proved the complete population on hardware. Build 139 retains it,
while removing mds5_3's non-black palette-0 rectangle and mds5_2's magnified
256px indexed edge by keeping those two Cosmos atlases at 768px truecolor.
Their native partial alpha is premultiplied into additive colour rather than
rounded to a one-bit silhouette, preserving Cosmos's antialiased FX boundary.
"""
from __future__ import annotations

import collections
import os
import struct
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC
import field_bg_native as FN
import field_bg_repack as FR
import ff7nx_fieldbg as FB
import ff7nx_marginart as MA
import ff7nx_marginblack as MB


SECTION9 = 8
TILE = 16
GRID = 16
UV_SCALE = 10_000_000
T_SRC_X, T_SRC_Y = 10, 12
T_SRC_X2, T_SRC_Y2 = 14, 16
T_USE_FX = 28
T_BLEND = T_USE_FX             # compatibility name used by diagnostics/tests
T_BLEND_MODE = 30
T_PAL = 22
T_TEX = FN.TILE_TEXTURE_ID
T_FX = FN.TILE_TEXTURE_ID2
T_SRC_X_BIG, T_SRC_Y_BIG = 42, 46

MAX_QUANT_ERR = MA.MAX_QUANT_ERR
# A complete lighting sheet cannot tolerate the margin-art pass's broad
# single-cell ceiling: one badly represented tile is a visible square.  All
# three proof fields fit below 10/255 after palette selection.
COMPLETE_MAX_QUANT_ERR = 10.0

# Build 136's broad 157-field population regressed on hardware.  Do not infer
# safety archive-wide from an offline renderer that cannot reproduce the
# console's animation timing.  Expand this only after hardware proof.
TARGET_FIELDS = frozenset(('mds5_2', 'mds5_3', 'mds7plr1'))

# Build 137 proved the complete-effect selection, but these two effects still
# exposed the native 256px/8-bit destination: mds5_2 as a magnified edge grid,
# and mds5_3 as a non-black palette-0 entry drawn over nominally transparent
# additive texels. Their complete atlases are blend-mode-1 art, so alpha can
# be premultiplied into one 768px truecolor additive clone without a palette.
# mds7plr1 is hardware-correct and deliberately remains on the proven
# paletted path.
TRUECOLOR_FIELDS = frozenset(('mds5_2', 'mds5_3'))
NO_TRUECOLOR_ENV = 'SEVENTH_NX_NO_FX_TRUECOLOR'
# The MDS5_2 lighting mask in Cosmos is still native-grid coverage inside a
# 1024px DDS: single opaque/transparent steps become three-texel blocks at
# 768px and alias when the page is minified onto the 720p field. Half a 768px
# texel is the smallest Pillow Gaussian radius that removes the isolated
# checker without erasing the authored larger silhouettes. Hardware scoped;
# mds5_3 is already clean and is not filtered.
ALPHA_SOFT_FIELDS = frozenset(('mds5_2',))
ALPHA_SOFT_RADIUS_768 = 0.5


def truecolor_enabled():
    return os.environ.get(NO_TRUECOLOR_ENV, '').strip().lower() not in (
        '1', 'true', 'yes', 'on')


def _band(slot):
    for i, (lo, hi, _blend) in enumerate(FN.D1_GROUPS):
        if lo <= slot < hi:
            return i
    return None


def _cell(arr, sx, sy):
    b = arr[sy:sy + TILE, sx:sx + TILE]
    return b if b.shape == (TILE, TILE) else None


def _art_cell(art, name, slot, source_pal, sx, sy, prgb):
    """Return quantised Cosmos cell plus measurements, or None."""
    try:
        got = art(name, slot, source_pal)
    except Exception:                                          # noqa: BLE001
        got = None
    if got is None:
        return None
    img, _used = got
    if img.ndim != 3 or img.shape[2] < 4 or img.shape[0] != img.shape[1]:
        return None
    k = img.shape[0] // 256
    if k < 1 or img.shape[0] != 256 * k:
        return None
    src = img[sy * k:(sy + TILE) * k, sx * k:(sx + TILE) * k]
    if src.shape != (TILE * k, TILE * k, 4):
        return None
    rgb = (np.ascontiguousarray(src[..., :3])
           .reshape(TILE, k, TILE, k, 3).mean((1, 3)))
    cover = (np.ascontiguousarray(src[..., 3])
             .reshape(TILE, k, TILE, k).mean((1, 3)))
    cov = cover >= 128
    if not cov.any():
        return None
    idx = MA.quantise(rgb.astype(np.uint8), prgb)
    err = float(np.abs(prgb[idx].astype(np.int16)
                       - rgb.astype(np.int16))[cov].mean())
    if err > MAX_QUANT_ERR:
        return None
    # The blend page has a real colour key.  Cosmos alpha is the only faithful
    # coverage source for a cell the local page left blank.
    idx = np.where(cov, idx, np.uint8(0)).astype(np.uint8)
    lum = float(rgb.max(-1)[cov].mean())
    return idx, lum, err, float(cov.mean())


def _provider_rgba(art, name, page, palette, px):
    """Return Cosmos's resampled RGBA before PageArt's 565 round trip.

    `provider_source` is intentionally a paletted-destination adapter: it
    converts PageArt's R5G6B5 buffer back to RGB and reduces alpha to a key.
    Feeding that into another R5G6B5 conversion would quantise/dither the FX
    twice and is not "the full-resolution Cosmos atlas".  This narrowly
    scoped truecolor path can read the already-indexed IRO record directly,
    resample once with the same production filter, and quantise once at the
    final write.  Return None for directory/test providers and use their
    supplied RGBA unchanged.
    """
    provider = getattr(art, 'provider', None)
    if provider is None:
        return None
    rec = provider.slots.get((name.lower(), page, palette))
    if rec is None:
        return None
    try:
        import dds_decode
        path, entry = rec
        reader = provider.readers.get(path)
        if reader is None:
            reader = provider.readers[path] = FR.IroReader(path)
        blob = reader.read(entry)
        if not blob:
            return None
        rgba, w, h = dds_decode.decode_dds(blob)
        rgba = FR.resample_rgba(rgba, w, h, px)
        out = np.frombuffer(rgba, np.uint8).reshape(px, px, 4)
        return np.ascontiguousarray(out)
    except Exception:                                          # noqa: BLE001
        return None


def _soften_additive_alpha(name, img, rows, px):
    """Antialias used FX cells without bleeding between atlas cells.

    Cosmos's page is an atlas, and neighbouring 48px blocks are not
    necessarily neighbours on screen. Filtering the full page would leak one
    cell's mask into another. Blur only each *used* source cell independently
    and only in the one hardware-observed field whose native-grid alpha is
    visible. RGB is untouched; the caller premultiplies it afterward.
    """
    if name.lower() not in ALPHA_SOFT_FIELDS or px <= 0:
        return img
    from PIL import Image, ImageFilter
    out = np.ascontiguousarray(img, np.uint8).copy()
    alpha = out[..., 3]
    cell_px = px // GRID
    if cell_px <= 0 or cell_px * GRID != px:
        return img
    radius = ALPHA_SOFT_RADIUS_768 * px / 768.0
    positions = set()
    for key, _tiles in rows:
        _base, _fx, _sx, _sy, sx2, sy2, _pal = key
        positions.add((sx2 * px // 256, sy2 * px // 256))
    for x, y in positions:
        if x < 0 or y < 0 or x + cell_px > px or y + cell_px > px:
            continue
        cell = Image.fromarray(alpha[y:y + cell_px, x:x + cell_px], 'L')
        alpha[y:y + cell_px, x:x + cell_px] = np.asarray(
            cell.filter(ImageFilter.GaussianBlur(radius)), np.uint8)
    return out


def _truecolor_effect(name, parts, sec9, pages, by_group, art, st, log,
                      lgp_mod):
    """Emit one full-resolution FX clone per source atlas.

    These pages intentionally remain in the depth-1 additive SLOT band
    (15..23) while declaring depth 2.  `ff7nx_fieldbg` supplies the matching
    seven-word loader ladder: depth 2 in 15..23 becomes additive, while every
    depth-1 slot and every other depth-2 slot keeps its stock blend.  Keeping
    the slot band is what lets the existing tile page byte and packed UV do
    all the addressing with no new coordinate path.
    """
    rows_by_fx = collections.defaultdict(list)
    for (_base, fx, _pal), rows in by_group.items():
        rows_by_fx[fx].extend(rows)
    if not rows_by_fx:
        return None, st

    # This path does not implement FFNx's +14/+18 blend aliases. Refuse the
    # complete field unless every admitted tile is the directly registered
    # blend-mode-1 texture. Both proof fields satisfy this exactly.
    for rows in rows_by_fx.values():
        for _key, tiles in rows:
            if any(sec9[t.off + T_BLEND_MODE] != 1 for t in tiles):
                st['no_art'] += len(rows)
                return None, st

    configured_px = FB.page_px()
    px = None
    atlas = {}
    for fx, rows in sorted(rows_by_fx.items()):
        variants = []
        for _key, tiles in rows:
            source_pal = _key[6]
            try:
                got = art(name, fx, source_pal)
            except Exception:                                  # noqa: BLE001
                got = None
            if got is None:
                st['no_art'] += len(rows)
                return None, st
            img, used = got
            if (img.ndim != 3 or img.shape[2] != 4
                    or img.shape[0] != img.shape[1]
                    or not np.any(img[..., 3] >= 8)):
                st['no_art'] += len(rows)
                return None, st
            # `ff7nx_marginart.provider_source` intentionally reduces alpha
            # to a binary coverage mask and round-trips RGB through 565 for
            # paletted destinations.  That is wrong for this additive
            # truecolor path: Cosmos's native mds5_2 page has 4,072 partially
            # transparent edge texels, and a second 565 quantisation needlessly
            # damages its gradients. Read/resample the indexed DDS once when
            # the provider is available. Keep the supplied image as the safe
            # fallback for directory/test providers.
            direct = _provider_rgba(art, name, fx, used, img.shape[0])
            if direct is not None:
                img = direct
            if px is None:
                px = img.shape[0]
                if configured_px > 0 and configured_px != px:
                    st['no_art'] += len(rows)
                    return None, st
            elif img.shape[0] != px:
                st['no_art'] += len(rows)
                return None, st
            variants.append(np.ascontiguousarray(img, np.uint8))
        first = variants[0]
        if any(not np.array_equal(first, other) for other in variants[1:]):
            # Palette-specific DDS variants cannot share one truecolor page.
            # Refuse instead of silently choosing one.
            st['no_art'] += len(rows)
            return None, st
        atlas[fx] = first

    present = len(pages)
    if present + len(atlas) > FR.max_total_pages():
        st['nofit'] = sum(len(x) for x in rows_by_fx.values())
        return None, st
    free = [s for s in range(0x0F, 0x18) if s not in pages]
    if len(free) < len(atlas):
        st['nofit'] = sum(len(x) for x in rows_by_fx.values())
        return None, st

    buf = bytearray(sec9)
    new_pages = {}
    moved = 0
    for fx in sorted(atlas):
        slot = free.pop(0)
        img = _soften_additive_alpha(name, atlas[fx], rows_by_fx[fx], px)
        # The console page has no 8-bit alpha channel, but this is additive
        # blend mode 1: alpha*colour can be baked into the stored colour and
        # then added normally.  Fully opaque texels remain byte-for-byte the
        # same; transparent texels remain zero; only Cosmos's own soft edge
        # is attenuated instead of being promoted to a hard opaque stair.
        enc = np.ascontiguousarray(img, np.uint8).copy()
        aa = enc[..., 3].astype(np.uint16)
        enc[..., :3] = ((enc[..., :3].astype(np.uint16)
                         * aa[..., None] + 127) // 255).astype(np.uint8)
        # Alpha has now been represented in colour. Do not run the generic
        # alpha<8 colour-key threshold afterward: additive zero already means
        # no contribution, while a faint nonzero premultiplied texel is the
        # antialiasing signal this path exists to retain.
        enc[..., 3] = 255
        data = FR.rgba_to_565_buf(enc.tobytes(), px * px, width=px,
                                  black_ok=True)
        new_pages[slot] = FN.Page(slot, 0, 2, data, px)
        for _key, tiles in rows_by_fx[fx]:
            for t in tiles:
                buf[t.off + T_FX] = slot
                moved += 1

    plist, tex_start, tex_end = FN.parse_texture_block(bytes(buf), px)
    for slot, page in new_pages.items():
        plist[slot] = page
    parts[SECTION9] = FN.replace_texture_block(bytes(buf), plist,
                                               tex_start, tex_end)
    st['units'] = sum(len(rows) for rows in rows_by_fx.values())
    st['tiles'] = moved
    st['pages'] = len(new_pages)
    st['truecolor_pages'] = len(new_pages)
    if log:
        log('    %s: complete FX %d cell(s), %d tile(s) -> %d truecolor '
            'page(s) at %dpx' % (name, st['units'], st['tiles'],
                                  st['pages'], px))
    return lgp_mod.join_sections(parts), st


def split_field(name, raw, lgp_mod, art, log=None):
    """Return ``(new_raw_or_None, stats)`` for one field."""
    st = {'units': 0, 'tiles': 0, 'pages': 0, 'truecolor_pages': 0, 'dark': 0,
          'no_art': 0, 'partial': 0, 'nofit': 0}
    if name.lower() not in TARGET_FIELDS:
        return None, st
    parts = lgp_mod.split_sections(raw)
    sec9 = parts[SECTION9]
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    arrays = {s: np.frombuffer(p.data, np.uint8).reshape(256, 256)
              for s, p in pages.items()
              if p.depth == 1 and not p.size_flag and p.px == 256}
    cols, _hdr, npg, _cpp = MB.palette_colours(parts[3])
    prgbs = [MA.palette_rgb(cols[p]) for p in range(npg)]

    # First identify the complete active layer-2+ effect in this proof field.
    # Once any part of that coherent sheet crosses 4:3, every active tile in
    # the field is part of the admission decision.  That includes page/palette
    # groups which happen to occur only inside 4:3: leaving those behind is
    # exactly how mds7plr1 retained its boundary.  This field-wide rule is safe
    # here because TARGET_FIELDS is deliberately limited to the three observed
    # single-effect cases; it is not an archive-wide inference.
    units = collections.defaultdict(list)
    effect_reaches_margin = False
    all_tiles = MB.read_tiles(sec9, surv, pages)
    for t in all_tiles:
        fx = sec9[t.off + T_FX]
        if (t.layer < 2 or not fx
                or not sec9[t.off + T_BLEND] or t.pal >= npg):
            continue
        bp, fp = pages.get(t.slot), pages.get(fx)
        if (bp is None or fp is None or t.slot not in arrays or fx not in arrays
                or _band(t.slot) is None or _band(fx) is None):
            continue
        # ONE runtime u,v selects a cell on whichever page is active.  These
        # records currently have use_fx_page set, so the loader chose src2
        # when it built this packed UV; the base page must nevertheless be
        # copied from that SAME UV for a later frame toggle.  Raw src1 is not
        # a second live sampler here (FINDINGS-164).
        u, v = struct.unpack_from('<II', sec9, t.off + T_SRC_X_BIG)
        grid = GRID
        sx2 = int(round(u / UV_SCALE * grid)) * TILE
        sy2 = int(round(v / UV_SCALE * grid)) * TILE
        if (_cell(arrays[t.slot], sx2, sy2) is None
                or _cell(arrays[fx], sx2, sy2) is None):
            continue
        if t.outside_43:
            effect_reaches_margin = True
        units[(t.slot, fx, sx2, sy2, sx2, sy2, t.pal)].append(t)

    by_group = collections.defaultdict(list)
    if effect_reaches_margin:
        for key, tiles in units.items():
            group = (key[0], key[1], key[6])
            by_group[group].append((key, tiles))

    if name.lower() in TRUECOLOR_FIELDS and truecolor_enabled():
        return _truecolor_effect(name, parts, sec9, pages, by_group, art,
                                 st, log, lgp_mod)

    # mds5_3's Cosmos page-15 yellow cannot be represented by palette 0
    # (worst error ~49/255), while palette 6 represents it below 2/255.  The
    # tile palette cannot simply change: its base frame then moves by as much
    # as 128/255.  Instead, use palette-0 entries that no currently sampled
    # base cell references.  This preserves every existing base texel exactly
    # and adds only the colours needed by the replacement FX page.
    if name.lower() == 'mds5_3' and (0, 15, 0) in by_group and npg > 6:
        used = {0}
        for t in all_tiles:
            if t.pal != 0 or t.slot not in arrays:
                continue
            u, v = struct.unpack_from('<II', sec9, t.off + T_SRC_X_BIG)
            sx = int(round(u / UV_SCALE * GRID)) * TILE
            sy = int(round(v / UV_SCALE * GRID)) * TILE
            cell = _cell(arrays[t.slot], sx, sy)
            if cell is not None:
                used.update(int(x) for x in cell.flat)
        free_idx = [i for i in range(1, min(256, cols.shape[1]))
                    if i not in used]
        donor_freq = collections.Counter()
        donor_rgb = prgbs[6]
        for key, _tiles in by_group[(0, 15, 0)]:
            _base, fx, _sx, _sy, sx2, sy2, source_pal = key
            a1 = _art_cell(art, name, fx, source_pal, sx2, sy2,
                           donor_rgb)
            if a1 is not None:
                donor_freq.update(int(x) for x in a1[0].flat if x)
        donor_idx = [i for i, _n in donor_freq.most_common(len(free_idx))]
        if donor_idx:
            rows = cols.copy()
            for dst, src in zip(free_idx, donor_idx):
                rows[0, dst] = rows[6, src]
            pbuf = bytearray(parts[3])
            start = _hdr + 0 * _cpp * 2
            pbuf[start:start + _cpp * 2] = rows[0].astype('<u2').tobytes()
            parts[3] = bytes(pbuf)
            cols = rows
            prgbs[0] = MA.palette_rgb(rows[0])

    # Every group must fit its ORIGINAL palette after the safe enrichment
    # above. Changing a tile's palette would also recolour its untouched base
    # page and can change layer-2 sorting, so it is not an admission option.
    accepted = []
    for (base, fx, source_pal), rows in sorted(by_group.items()):
        choices = []
        for target_pal in (source_pal,):
            prgb = prgbs[target_pal]
            made, errs, partial = [], [], 0
            for key, tiles in rows:
                _base, _fx, sx, sy, sx2, sy2, _pal = key
                b0 = _cell(arrays[base], sx, sy)
                if b0 is None:
                    made = []
                    break
                base_rgb = prgbs[source_pal][b0]
                qbase = MA.quantise(base_rgb, prgb)
                base_cov = b0 != 0
                berr = (float(np.abs(prgb[qbase].astype(np.int16)
                                     - base_rgb.astype(np.int16))[base_cov].mean())
                        if base_cov.any() else 0.0)
                qbase = np.where(base_cov, qbase, np.uint8(0)).astype(np.uint8)
                a1 = _art_cell(art, name, fx, source_pal, sx2, sy2, prgb)
                if a1 is None:
                    made = []
                    break
                errs.append(max(berr, a1[2]))
                partial += int(a1[3] < 1.0)
                made.append((key, tiles, a1[0], target_pal))
            if made and max(errs) <= COMPLETE_MAX_QUANT_ERR:
                choices.append((max(errs), float(np.mean(errs)), target_pal,
                                partial, made))
        if not choices:
            st['no_art'] += len(rows)
            # One incomplete group refuses the complete field below.
            return None, st
        _worst, _mean, _target, partial, made = min(choices)
        st['partial'] += partial
        accepted.extend(made)

    if not accepted:
        return None, st

    grouped = collections.defaultdict(list)
    for rec in accepted:
        base, fx, _sx, _sy, _sx2, _sy2, _pal = rec[0]
        # Preserve the runtime UV and base page.  One new FX page per source
        # atlas/palette keeps every authored cell at the coordinate the tile
        # already samples and makes the resting frame structurally unable to
        # drift through downstream page seating.
        grouped[(fx, _band(fx), rec[3])].append(rec)

    chunks = []
    for gkey in sorted(grouped):
        rows = grouped[gkey]
        chunks.append((gkey, rows))

    present = len(pages)
    need_pages = len(chunks)
    if present + need_pages > FR.max_total_pages():
        st['nofit'] = len(accepted)
        return None, st

    free = {}
    for bi, (lo, hi, _blend) in enumerate(FN.D1_GROUPS):
        free[bi] = [s for s in range(lo, hi) if s not in pages]
    demand = collections.Counter()
    for (_srcfx, fb, _pal), _rows in chunks:
        demand[fb] += 1
    if any(len(free[b]) < n for b, n in demand.items()):
        st['nofit'] = len(accepted)
        return None, st

    buf = bytearray(sec9)
    new_pages = {}
    moved = 0
    for (_srcfx, fb, _pal), rows in chunks:
        fslot = free[fb].pop(0)
        fa = np.zeros((256, 256), np.uint8)
        new_pages[fslot] = fa
        occupied = set()
        for _key, tiles, qfx, target_pal in rows:
            _base, _fx, _sx, _sy, dx, dy, _source_pal = _key
            pos = (dx, dy)
            old = fa[dy:dy + TILE, dx:dx + TILE]
            if pos in occupied and not np.array_equal(old, qfx):
                st['nofit'] = len(accepted)
                return None, st
            occupied.add(pos)
            fa[dy:dy + TILE, dx:dx + TILE] = qfx
            for t in tiles:
                o = t.off
                buf[o + T_FX] = fslot
                buf[o + T_PAL] = target_pal
                moved += 1

    plist, tex_start, tex_end = FN.parse_texture_block(bytes(buf),
                                                       FB.page_px())
    for slot, arr in new_pages.items():
        plist[slot] = FN.Page(slot, 0, 1, arr.tobytes(), 256)
    parts[SECTION9] = FN.replace_texture_block(bytes(buf), plist,
                                               tex_start, tex_end)
    st['units'] = len(accepted)
    st['tiles'] = moved
    st['pages'] = len(new_pages)
    if log:
        log('    %s: complete FX %d cell(s), %d tile(s) -> %d page(s)'
            % (name, st['units'], st['tiles'], st['pages']))
    return lgp_mod.join_sections(parts), st


def apply_to_flevel(archive, payloads, art, encode=None, log=print,
                    fields=None):
    import lgp

    encode = encode or (lambda raw: archive.encode_field(raw))
    st = {'read': 0, 'changed': 0, 'units': 0, 'tiles': 0, 'pages': 0,
          'truecolor_pages': 0,
          'dark': 0, 'no_art': 0, 'nofit': 0, 'refused': []}
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        if fields and name not in fields:
            continue
        payload = payloads.get(name, entry.get('payload'))
        if not payload:
            continue
        try:
            raw = (lgp.lzs_decompress(payload[4:]) if name in payloads
                   else archive.decompressed(entry))
            new, one = split_field(name, raw, lgp, art, log=log)
            st['read'] += 1
            for k in ('units', 'tiles', 'pages', 'truecolor_pages', 'dark',
                      'no_art', 'nofit'):
                st[k] += one[k]
            if new is None:
                continue
            payloads[name] = encode(new)
            st['changed'] += 1
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, '%s: %s'
                                  % (type(exc).__name__, str(exc)[:60])))
    if st['refused'] and log:
        log('  ! FX margin: %d field(s) not changed (%s)'
            % (len(st['refused']), ', '.join('%s: %s' % r
                                              for r in st['refused'][:3])))
    return st


def summarise(st):
    if not st or not st.get('changed'):
        return ''
    return ('COMPLETE FX ATLAS: %d Cosmos FX cell(s) copied onto %d page(s) '
            '(%d full-resolution truecolor) across %d hardware-scoped '
            'field(s); %d '
            'layer-2+ tile(s) repointed across 4:3 and margin. Base pages, '
            'runtime UVs, and original texture pages stay unchanged.%s'
            % (st['units'], st['pages'], st.get('truecolor_pages', 0),
               st['changed'], st['tiles'],
               ' %d eligible cell(s) were left byte-identical because the '
               'complete field had no safe slots.' % st['nofit']
               if st.get('nofit') else ''))
