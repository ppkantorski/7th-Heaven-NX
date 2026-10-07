#!/usr/bin/env python3
"""Animated FX whose Cosmos widescreen strips must match their 4:3 centre.

BUILD 582.  ghotel's fog on hardware (build 581): hard vertical seams at the
4:3 edges and boxes of brighter/darker fog, pulsing.

WHAT IS ON THE PAGES
--------------------
The fog is palette 8, and the field script pulses it: STPAL copies palette 8
to the buffer, MPPAL multiplies the whole buffer by per-channel variables and
LDPAL writes it back -- a UNIFORM brightness/tint multiply over all 256
entries.  Build 581 kept that page paletted so the pulse survives.

But the page it keeps is two different pictures:

  * the 4:3 centre -- page 15, palette 8 -- is the 1997 fog;
  * the widescreen strips Cosmos added -- page 16, authored at palette 16,
    re-seated onto palette 8 by ff7nx_palrange -- are Cosmos's fog.

Cosmos repainted the fog, so the two do not join.  On PC both halves are
Cosmos art, which is why FFNx shows one continuous bank.

WHAT THIS PASS DOES
-------------------
For a palette P that
  * the field script animates ONLY through whole-palette operations (every
    STPAL/LDPAL/LDPLS/MPPAL/ADPAL on 256 entries, no RTPAL/RTPAL2 anywhere in
    the field), so any choice of base colours pulses exactly as 1997 does;
  * is used by additive FX records only, on 256px depth-1 FX pages, in cells
    no other palette shares; and
  * has FX records outside the 4:3 picture (Cosmos extended it),
it rebuilds P's cells from the SAME Cosmos images FFNx draws (exact palette,
else palette 0 -- saveload.cpp's fallback), premultiplied because the layer
is additive, and requantises all of them jointly into P's 255 non-key
entries.  Centre and strips are then one picture again, still animated by
the unchanged script.  Records, UVs, pages, slots and sizes do not change.

Refused (field untouched) if the Cosmos art for any cell is missing or
ambiguous, if the art's brightness is far from the 1997 centre's (it would
change the look, not fix a seam), or on any structural surprise.

SEVENTH_NX_NO_FX_REQUANT=1 disables the pass.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import ff7nx_marginblack as MB

OFF_ENV = 'SEVENTH_NX_NO_FX_REQUANT'
SECTION_SCRIPT = 0
SECTION_PALETTE = 3
SECTION9 = 8
TILE = 16
PAGE = 256
HALF_43 = 160
FX_LO, FX_HI = 0x0F, 0x1A          # paletted FX band (additive + average)

T_DSTX = 2
T_DSTY = 4
T_SRCX2 = 14
T_SRCY2 = 16
T_PAL = 22
T_USE_FX = 28
T_BLEND = 30
T_TEX = 32
T_TEX2 = 34

OP_STPAL, OP_LDPAL, OP_CPPAL, OP_RTPAL = 0xE5, 0xE6, 0xE7, 0xE8
OP_ADPAL, OP_MPPAL, OP_STPLS, OP_LDPLS = 0xE9, 0xEA, 0xEB, 0xEC
OP_CPPAL2, OP_RTPAL2, OP_ADPAL2 = 0xED, 0xEE, 0xEF
# operand offset of the entry-count byte (count = byte + 1 entries)
COUNT_AT = {OP_STPAL: 4, OP_LDPAL: 4, OP_STPLS: 4, OP_LDPLS: 4,
            OP_MPPAL: 9, OP_ADPAL: 9}
# operand layout not audited: a field using it is refused outright
UNAUDITED = (OP_RTPAL, OP_RTPAL2, OP_ADPAL2)

# Cosmos art may be repainted, not relit.  Mean premultiplied brightness of
# the centre cells must stay within this ratio of the 1997 centre.
MAX_BRIGHTNESS_RATIO = 1.8
# BUILD 618u: single-source palette groups (see single_source_group) are
# requantised only for fields reviewed one by one. mtnvl3: the 4:3 mist was
# 1997 art and the widescreen strips Cosmos art (two squares at the bottom-
# left corner, hardware 10-03); both are now Cosmos art in palette 9's
# entries, still pulsed by the script. Other archive matches: blackbg2,
# gidun_1, jtempl, kuro_1, rcktbas2, semkin_8, trnad_2 (kuro_1/trnad_2 are
# hardware-confirmed and stay as they are).
GROUP_FIELDS = frozenset(('mtnvl3',))
EMPTY = 3            # premultiplied max channel below this -> index 0
# BUILD 620i. ghotel, hardware 10-07: "the light fog effects at the bottom of
# the screen glow in what look like contours, very uneven". The fog stays a
# 256px paletted page so the script's pulse keeps every frame (light2:
# MPPAL v 40..62, a uniform multiply; 23 levels x 348 cells cannot be stored
# in truecolor), and its 255 entries are 5-bit per channel. Mapping Cosmos's
# smooth fog to the NEAREST entry texel by texel turned its gradients into
# flat bands. These fields are mapped with Floyd-Steinberg error diffusion
# instead (serpentine, per 16x16 cell, never into key texels) -- the way the
# 1997 fog itself was dithered. Opt-in per field: the fields already
# confirmed on hardware keep their nearest-colour pages.
# BUILD 620j: per-cell dithering left square seams (hardware 10-07) and
# did not remove the bands. ghotel is now split by ff7nx_palstates (a static
# truecolor base + the paletted pulse at a quarter of the step), so its
# palette goes back to nearest-colour; the dither path stays for reference.
DITHER_FIELDS = frozenset()


class RequantError(Exception):
    pass


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


# --------------------------------------------------------------- the script
def uniform_palettes(script_section):
    """{palette: last entry} for palettes changed only by uniform scaling.

    A palette P qualifies when every LDPAL/LDPLS into it loads the same entry
    block 0..c, and every STPAL/MPPAL/ADPAL in the field works on a block of
    one of the loaded sizes -- so each multiply/add covers a whole loaded
    block and entries 1..c of P always move together (ghotel's fog: c = 255;
    jet's neon letters: 63 or 127).  Any rotation, unaudited op, or
    variable destination refuses the whole field ({}).
    """
    import echo_s_flevel as ES
    loads = {}
    blocks_seen = []
    stored = set()       # BUILD 618t: palettes copied INTO the buffer
    try:
        blocks = ES._routine_blocks(script_section)
    except Exception:                                          # noqa: BLE001
        return {}
    for start, end in blocks:
        stream, _trunc = ES._decode_block(script_section, start, end)
        for off, op, size in stream:
            if op in UNAUDITED:
                return {}
            at = COUNT_AT.get(op)
            if at is None:
                continue
            if size <= at:
                return {}
            count = script_section[off + at]
            if op in (OP_LDPAL, OP_LDPLS):
                if script_section[off + 1] & 0x0F:
                    return {}
                loads.setdefault(script_section[off + 3], set()).add(count)
            else:
                if op in (OP_STPAL, OP_STPLS):
                    if script_section[off + 1] & 0xF0:
                        return {}
                    stored.add(script_section[off + 2])
                blocks_seen.append(count)
    sizes = {c for cs in loads.values() for c in cs}
    if any(c not in sizes for c in blocks_seen):
        return {}
    # BUILD 618t. mtnvl3 (Mt Nibel bridge), hardware 10-03: a washed-out
    # rectangle with a ghost of the bridge over the lower screen. The script
    # stores palette 9 (STPLS 9), offsets the copy (ADPAL) and loads it into
    # palettes 9 AND 10, so at run time palette 10 IS palette 9's colours.
    # Requantising palette 10's cells into palette 10's own entries therefore
    # drew those indices through palette 9: garbage. "Any choice of base
    # colours pulses as 1997" only holds for a palette the script copies
    # FROM ITSELF, so a loaded palette must also be a stored one.
    # And palette 9's own entries feed palette 10, so changing them breaks
    # palette 10's untouched cells too: any cross-palette load refuses the
    # whole field.
    # Only whole-palette loads (255) are refused this way: a short load
    # (uutai1, 15 entries; hardware-accepted) leaves the requantised
    # entries above it alone.
    if any(pal not in stored and 255 in cs for pal, cs in loads.items()):
        return {}
    return {pal: next(iter(cs)) for pal, cs in loads.items() if len(cs) == 1}


def single_source_group(script_section):
    """BUILD 618u. (S, {palettes}) when the field stores exactly ONE palette
    S into the buffer, loads only whole palettes, and every load comes from
    that buffer -- so at run time every loaded palette IS palette S's colours
    (after the same uniform add/multiply). mtnvl3: STPLS 9 -> ADPAL ->
    LDPAL 9 and 10. Else None."""
    import echo_s_flevel as ES
    stored, loads = set(), set()
    try:
        blocks = ES._routine_blocks(script_section)
    except Exception:                                          # noqa: BLE001
        return None
    for start, end in blocks:
        stream, _trunc = ES._decode_block(script_section, start, end)
        for off, op, size in stream:
            if op in UNAUDITED:
                return None
            at = COUNT_AT.get(op)
            if at is None:
                continue
            if size <= at or script_section[off + at] != 255:
                return None
            if op in (OP_LDPAL, OP_LDPLS):
                if script_section[off + 1] & 0x0F:
                    return None
                loads.add(script_section[off + 3])
            elif op in (OP_STPAL, OP_STPLS):
                if script_section[off + 1] & 0xF0:
                    return None
                stored.add(script_section[off + 2])
    if len(stored) != 1 or not loads or not (stored <= loads):
        return None
    if loads == stored:
        return None
    return next(iter(stored)), loads


# ------------------------------------------------------------------- the art
def XP_sel(provider, name, slot, pal):
    have = set(provider.by_page.get((name, slot), ()))
    return pal if pal in have else (0 if 0 in have else None)


def _cosmos_rgba256(provider, name, slot, pal):
    """Premultiplied (256,256,3) float art for (slot, pal) as FFNx picks it."""
    have = set(provider.by_page.get((name, slot), ()))
    use = pal if pal in have else (0 if 0 in have else None)
    if use is None:
        raise RequantError('page %d has no Cosmos art for palette %d or 0'
                           % (slot, pal))
    # One DDS normally.  Several (FFNx's hashed runtime states -- it plays
    # them as the palette animates): the palette's scaling only ever dims
    # its base colours, so the BRIGHTEST state is the base the script scales.
    recs = (provider.state_slots.get((name, slot, use))
            or ((provider.slots.get((name, slot, use)),)))
    if not recs or recs[0] is None:
        raise RequantError('page %d palette %d art is missing' % (slot, use))
    import dds_decode
    import field_bg_repack as FR
    best = best_score = None
    for path, entry in recs:
        reader = provider.readers.get(path)
        if reader is None:
            reader = provider.readers[path] = FR.IroReader(path)
        rgba, w, h = dds_decode.decode_dds(reader.read(entry))
        a = np.frombuffer(rgba, np.uint8, count=w * h * 4).reshape(h, w, 4)
        if w != h or w % PAGE:
            raise RequantError('page %d art is %dx%d' % (slot, w, h))
        if len(recs) > 1:
            sub = a[::4, ::4].astype(np.uint32)
            score = int((sub[..., :3].sum(-1) * sub[..., 3]).sum())
            if best_score is not None and score <= best_score:
                continue
            best_score = score
        best = a
    k = best.shape[0] // PAGE
    f = best.astype(np.float32)
    return (f[..., :3] * (f[..., 3:4] / 255.0)).reshape(
        PAGE, k, PAGE, k, 3).mean((1, 3))


# ------------------------------------------------------------ quantisation
def _quantise(samples, n_colours):
    """k-means on premultiplied RGB samples -> (centres (n,3) float)."""
    uniq, counts = np.unique(np.round(samples).astype(np.int32), axis=0,
                             return_counts=True)
    if len(uniq) <= n_colours:
        return uniq.astype(np.float32)
    w = counts.astype(np.float64)
    pts = uniq.astype(np.float64)
    # deterministic init: sort by luminance, split by cumulative weight
    # elementwise, not `@`: macOS's Accelerate matmul raises spurious
    # divide/overflow RuntimeWarnings on finite input (numpy 2 + BLAS).
    lum = pts[:, 0] * 0.299 + pts[:, 1] * 0.587 + pts[:, 2] * 0.114
    order = np.argsort(lum, kind='stable')
    cw = np.cumsum(w[order])
    bins = np.minimum((cw / cw[-1] * n_colours).astype(int), n_colours - 1)
    centres = np.zeros((n_colours, 3))
    for b in range(n_colours):
        sel = order[bins == b]
        if len(sel):
            centres[b] = np.average(pts[sel], axis=0, weights=w[sel])
        else:
            centres[b] = pts[order[min(len(order) - 1, b)]]
    for _ in range(12):
        d = ((pts[:, None, :] - centres[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        for b in range(n_colours):
            m = lab == b
            if m.any():
                centres[b] = np.average(pts[m], axis=0, weights=w[m])
    return centres.astype(np.float32)


def _dither(t, rgb, entry0_key):
    """(TILE, TILE) palette indices (1-based) for premultiplied target `t`,
    Floyd-Steinberg, serpentine. Key texels (target below EMPTY) stay 0 and
    neither take nor pass on error."""
    work = t.astype(np.float64).copy()
    key = (t.max(-1) < EMPTY) if entry0_key else np.zeros(t.shape[:2], bool)
    out = np.zeros(t.shape[:2], np.uint8)
    pal = rgb.astype(np.float64)
    for y in range(TILE):
        xs = range(TILE) if y % 2 == 0 else range(TILE - 1, -1, -1)
        step = 1 if y % 2 == 0 else -1
        for x in xs:
            if key[y, x]:
                continue
            want = np.clip(work[y, x], 0.0, 255.0)
            j = int(((pal - want) ** 2).sum(1).argmin())
            out[y, x] = j + 1
            err = want - pal[j]
            for dx, dy, f in ((step, 0, 7 / 16.0), (-step, 1, 3 / 16.0),
                              (0, 1, 5 / 16.0), (step, 1, 1 / 16.0)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < TILE and ny < TILE and not key[ny, nx]:
                    work[ny, nx] += err * f
    return out


def _to_code(rgb, stp):
    q = np.clip(np.round(rgb / 255.0 * 31), 0, 31).astype(np.uint16)
    code = q[:, 0] | (q[:, 1] << 5) | (q[:, 2] << 10)
    if stp:
        code = code | np.uint16(0x8000)
    return code


def _code_rgb(codes):
    v = np.asarray(codes, np.uint16)
    return np.stack([(v & 31) << 3, ((v >> 5) & 31) << 3,
                     ((v >> 10) & 31) << 3], -1).astype(np.float32)


# --------------------------------------------------------------- the field
def plan_field(name, parts, art):
    """(new parts or None, stats). Raises RequantError on refusal."""
    name = name.lower()
    stats = {'palettes': [], 'cells': 0, 'tiles': 0, 'err_before': 0.0,
             'err_after': 0.0}
    uniform = uniform_palettes(parts[SECTION_SCRIPT])
    group = None
    if not uniform:
        group = (single_source_group(parts[SECTION_SCRIPT])
                 if name in GROUP_FIELDS else None)
        if group is None:
            return None, stats
        uniform = {group[0]: 255}
    sec9 = parts[SECTION9]
    surv = DC.survey(sec9)
    pages_list, tex_start, tex_end = FN.parse_texture_block(
        sec9, surv['page_px'])
    pages = {p.slot: p for p in pages_list if p is not None}
    cols, hdr, npg, cpp = MB.palette_colours(parts[SECTION_PALETTE])
    if cpp != 256:
        return None, stats

    recs = []
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        for o in offs:
            use_fx = struct.unpack_from('<H', sec9, o + T_USE_FX)[0]
            recs.append((o, layer, use_fx))
    # who uses each (page, cell)
    cell_pals = collections.defaultdict(set)
    for o, _layer, use_fx in recs:
        cell_pals[(sec9[o + T_TEX], sec9[o + 10], sec9[o + 12])].add(
            sec9[o + T_PAL])
        if sec9[o + T_TEX2]:
            cell_pals[(sec9[o + T_TEX2], sec9[o + T_SRCX2],
                       sec9[o + T_SRCY2])].add(sec9[o + T_PAL])

    provider = getattr(art, 'provider', None)
    if provider is None:
        return None, stats

    new_pages = dict()
    palbuf = bytearray(parts[SECTION_PALETTE])
    for P in sorted(uniform):
        if P >= npg:
            continue
        members = group[1] if group else {P}
        mine = [(o, l, f) for o, l, f in recs if sec9[o + T_PAL] in members
                # a non-FX record on a truecolor page never reads a palette
                and (f or getattr(pages.get(sec9[o + T_TEX]), 'depth', 1)
                     != 2)]
        if not mine:
            continue
        # every record additive FX on a 256px paletted FX page
        cells = set()
        margin = False
        for o, _l, use_fx in mine:
            slot = sec9[o + T_TEX2]
            pg = pages.get(slot)
            if (not use_fx or sec9[o + T_BLEND] != 1 or pg is None
                    or not FX_LO <= slot < FX_HI or pg.depth != 1
                    or pg.size_flag or pg.px != PAGE):
                break
            sx, sy = sec9[o + T_SRCX2], sec9[o + T_SRCY2]
            if sx % TILE or sy % TILE:
                break
            cells.add((slot, sx, sy))
            dx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
            if dx + TILE <= -HALF_43 or dx >= HALF_43:
                margin = True
        else:
            # FFNx plays HD runtime states for this palette, and it is a
            # partial block (a neon chase such as jet's sign, not a whole-
            # palette pulse): approximate those states with Cosmos's art.
            multi = uniform[P] < 255 and any(
                len(provider.state_slots.get(
                    (name, c[0], XP_sel(provider, name, c[0], P)), ())) > 1
                for c in cells)
            if not (margin or multi):
                continue
            if any(not cell_pals[c] <= members for c in cells):
                raise RequantError('palette %d shares a cell with another '
                                   'palette' % P)
            # Every P record has use_fx set (checked above), so its base
            # (texture_id) half is never sampled: use_fx is section-9 data,
            # not a script toggle -- BGON/BGOFF switch param/state only.
            _requant_palette(name, P, cells, sec9, pages, new_pages, cols,
                             hdr, cpp, palbuf, provider, stats, mine,
                             uniform[P], also=sorted(members - {P}))
            continue
        # loop broke: some record is not an additive paletted FX record
        continue

    if not new_pages:
        return None, stats
    for slot, data in new_pages.items():
        pg = pages[slot]
        pages_list[slot] = FN.Page(slot, pg.size_flag, pg.depth, data, pg.px)
    new9 = FN.replace_texture_block(sec9, pages_list, tex_start, tex_end)
    if len(new9) != len(sec9):
        raise RequantError('section 9 size changed')
    out = list(parts)
    out[SECTION9] = new9
    out[SECTION_PALETTE] = bytes(palbuf)
    MB.palette_colours(out[SECTION_PALETTE])
    DC.parse_pages(out[SECTION9])
    return out, stats


def _requant_palette(name, P, cells, sec9, pages, new_pages, cols, hdr, cpp,
                     palbuf, provider, stats, mine, last=255, also=()):
    arts = {}
    targets = {}
    for slot, sx, sy in sorted(cells):
        if slot not in arts:
            arts[slot] = _cosmos_rgba256(provider, name, slot, P)
        targets[(slot, sx, sy)] = arts[slot][sy:sy + TILE, sx:sx + TILE]

    old_rgb = _code_rgb(cols[P] & 0x7FFF)
    # brightness guard on the 4:3 centre, where the 1997 art is live
    centre = set()
    for o, _l, _f in mine:
        dx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
        if -HALF_43 <= dx and dx + TILE <= HALF_43:
            centre.add((sec9[o + T_TEX2], sec9[o + T_SRCX2],
                        sec9[o + T_SRCY2]))
    if centre:
        b_old = b_new = 0.0
        for c in centre:
            slot, sx, sy = c
            data = np.frombuffer(new_pages.get(slot, pages[slot].data),
                                 np.uint8).reshape(PAGE, PAGE)
            idx = data[sy:sy + TILE, sx:sx + TILE]
            b_old += float(np.where(idx[..., None] == 0, 0,
                                    old_rgb[idx]).mean())
            b_new += float(targets[c].mean())
        if b_old > 1.0:
            ratio = b_new / b_old
            if not 1.0 / MAX_BRIGHTNESS_RATIO <= ratio <= MAX_BRIGHTNESS_RATIO:
                raise RequantError('palette %d: Cosmos centre is %.2fx the '
                                   '1997 brightness' % (P, ratio))

    samples = np.concatenate([t.reshape(-1, 3) for t in targets.values()])
    live = samples[samples.max(1) >= EMPTY]
    if not len(live):
        raise RequantError('palette %d: Cosmos art is empty' % P)
    if last < 15:
        raise RequantError('palette %d animates only %d entries' % (P, last))
    centres = _quantise(live, last)
    used_old = cols[P][1:last + 1]
    stp_bits = (used_old[used_old != 0] & 0x8000) != 0
    stp = bool(stp_bits.mean() >= 0.5) if len(stp_bits) else False
    codes = _to_code(centres, stp)
    # a non-key entry must never encode as the 0x0000 key
    codes[(codes & 0x7FFF) == 0] = (0x8000 if stp else 0) | 0x0421
    rgb = _code_rgb(codes & 0x7FFF)
    entry0_key = int(cols[P][0]) == 0

    err_a = err_b = 0.0
    for (slot, sx, sy), t in targets.items():
        if slot not in new_pages:
            new_pages[slot] = pages[slot].data
        arr = np.frombuffer(new_pages[slot], np.uint8).reshape(
            PAGE, PAGE).copy()
        old_idx = arr[sy:sy + TILE, sx:sx + TILE]
        old_c = np.where(old_idx[..., None] == 0, 0, old_rgb[old_idx])
        flat = t.reshape(-1, 3)
        if name in DITHER_FIELDS:
            idx = _dither(t, rgb, entry0_key)
        else:
            d = ((flat[:, None, :] - rgb[None, :, :]) ** 2).sum(-1)
            idx = d.argmin(1).astype(np.uint8) + 1
            if entry0_key:
                idx[flat.max(1) < EMPTY] = 0
            idx = idx.reshape(TILE, TILE)
        new_c = np.where(idx[..., None] == 0, 0,
                         rgb[np.maximum(idx.astype(np.int32) - 1, 0)])
        err_b += float(np.abs(old_c - t).mean())
        err_a += float(np.abs(new_c - t).mean())
        arr[sy:sy + TILE, sx:sx + TILE] = idx
        new_pages[slot] = arr.tobytes()

    # a group (618u): every member palette gets the same entries -- at run
    # time they are all palette P's buffer anyway
    for Q in [P] + list(also):
        poff = hdr + 2 * Q * cpp
        for j, value in enumerate(codes, 1):
            struct.pack_into('<H', palbuf, poff + 2 * j, int(value))
    n = len(targets)
    stats['palettes'].append(P)
    stats['cells'] += n
    stats['tiles'] += len(mine)
    stats['err_before'] += err_b / n
    stats['err_after'] += err_a / n


# ----------------------------------------------------------- the archive
def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_: None):
    import lgp
    total = {'fields': 0, 'cells': 0, 'tiles': 0, 'names': [],
             'refused': []}
    if disabled() or art is None:
        return total
    encode = encode or archive.encode_field
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            payload = payloads.get(name)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            if len(parts) < 9:
                continue
            new_parts, st = plan_field(name, parts, art)
        except RequantError as exc:
            total['refused'].append((name, str(exc)))
            continue
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append(
                (name, '%s: %s' % (type(exc).__name__, str(exc)[:60])))
            continue
        if new_parts is None:
            continue
        total['fields'] += 1
        total['cells'] += st['cells']
        total['tiles'] += st['tiles']
        total['names'].append('%s(pal %s, err %.1f->%.1f)' % (
            name, ','.join(map(str, st['palettes'])),
            st['err_before'] / max(1, len(st['palettes'])),
            st['err_after'] / max(1, len(st['palettes']))))
        payloads[name] = encode(lgp.join_sections(new_parts))
    return total


def summarise(st):
    if not st.get('fields') and not st.get('refused'):
        return ''
    line = ('  animated FX requantised to Cosmos art: %d field(s), %d cell(s), '
            '%d record(s) -- %s (%s=1 disables)'
            % (st['fields'], st['cells'], st['tiles'],
               ', '.join(st['names'][:20]) or '-', OFF_ENV))
    if st.get('refused'):
        line += ('\n  ! animated FX requant: %d field(s) unchanged (%s)'
                 % (len(st['refused']), ', '.join(
                     '%s: %s' % r for r in st['refused'][:6])))
    return line
