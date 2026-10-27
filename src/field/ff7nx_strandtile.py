#!/usr/bin/env python3
"""ff7nx_strandtile.py -- layer-1 tiles stranded on a 256px page. BUILD 717.

mtcrl_2, hardware: "this one focus area looks a bit blocky / pixelated".
The spot is the diagonal walkway just left of the ladder top. Every layer-1
tile around it draws Cosmos art from a 768px truecolor page; the one at
field (-80, -24) still draws from slot 0, the 256px paletted page, so it is
a third of the resolution of its neighbours (and quantised to 256 colours).
A second tile, (-16, -152), is stranded the same way at the top.

WHY THEY ARE LEFT: slot 0 cannot be dropped or converted wholesale, because
it is also the dormant base page of the layer-2 FX records (params 61..63
and 2), and both tiles' atlas cells are the SAME coordinates those FX
records use. The dense repack's shared-cell arm (FILLER_EXACT) admits them
offline, but by the end of the real pipeline they are back on slot 0.

THE REPAIR is the one ff7nx_staticpage does for mds7plr1 / mtcrl_8 /
sninn_2, made structural instead of fingerprinted: copy the tile's exact
Cosmos cell (same page, same palette, the image FFNx would draw) into a cell
no record samples on an EXISTING opaque 768px page, and point only that
tile's page and UV at it. No page is added, no page changes size, no FX or
other record changes, slot 0 keeps every byte. Texture memory is identical.

Every tile must pass all of these or it is left alone:
  * layer 1, no FX page, use_fx off, blend off, param/state 0, 16-unit;
  * on a 256px depth-1 page; UV on the 16x16 cell grid;
  * Cosmos ships exactly one image for that page and palette, and its cell
    is fully opaque with no 0x0000 texel (0 is the depth-2 colour key);
  * the destination is an existing 768px depth-2 page outside the FX
    slots, every record on it sits on the cell grid, and the cell is
    sampled by no record's base or FX declaration.

BUILD 720: every field, not just mtcrl_2 -- with one more test, because a
scan of the whole flevel found the first rule far too loose. Of 525 tiles
it admitted in 104 fields, 486 were the black filler tiles the widescreen
margin passes lay outside the 1997 screen: their 256px cell is black, but
Cosmos's sheet holds unrelated art at the same cell (fship_3 would have
grown carpet in its margin). So a tile now moves only when its CURRENT
picture is the Cosmos cell: the 16x16 paletted cell, decoded, against the
48x48 Cosmos cell averaged 3x3, mean |difference| < MATCH_DIFF per channel,
and the cell not near-black (mean >= MIN_MEAN; a black cell would gain
nothing). 39 tiles in 38 fields pass (mtcrl_2's two at 3.8 and 6.8).

SEVENTH_NX_NO_STRAND_TILE=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import numpy as np

VERSION = 720
OFF_ENV = 'SEVENTH_NX_NO_STRAND_TILE'
FIELDS = None                 # BUILD 720: every field (was ('mtcrl_2',))
MATCH_DIFF = 24.0             # mean |orig - Cosmos/3| per channel, 0..255
MIN_MEAN = 12.0               # a near-black cell is filler, not art

PAGE_PX = 768
GRID = 16
UNIT = PAGE_PX // GRID
UV_SCALE = 10_000_000
STEP = UV_SCALE // GRID
FX_LO, FX_HI = 0x0F, 0x18

T_DST = 2
T_SRC = 10            # u16 x, u16 y in 1997 page pixels (cell * 16)
T_W = 18
T_PAL = 22
T_PARAM = 26
T_STATE = 27
T_USE_FX = 28         # u16
T_BLEND = 30
T_BASE = 32
T_FX = 34
T_BIG = 42            # u32 u, u32 v in 1e7 fixed point


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _cell(sec9, off):
    u, v = struct.unpack_from('<II', sec9, off + T_BIG)
    if u % STEP or v % STEP:
        return None
    cx, cy = u // STEP, v // STEP
    if not (0 <= cx < GRID and 0 <= cy < GRID):
        return None
    return cx, cy


def _static_l1(sec9, off):
    w, h = struct.unpack_from('<HH', sec9, off + T_W)
    use_fx, = struct.unpack_from('<H', sec9, off + T_USE_FX)
    return (not use_fx and not sec9[off + T_FX] and not sec9[off + T_BLEND]
            and not sec9[off + T_PARAM] and not sec9[off + T_STATE]
            and (w, h) in ((0, 0), (16, 16)))


def _art_cell(provider, getter, name, slot, pal, cell):
    key = (name.lower(), slot, pal)
    if key not in provider.slots:
        return None, 'no Cosmos image for page %d palette %d' % (slot, pal)
    if key in provider.ambiguous_slots:
        return None, 'Cosmos has more than one image'
    art = getter(slot, pal)
    if art is None or art.px != PAGE_PX:
        return None, 'Cosmos image unavailable at %dpx' % PAGE_PX
    x0, y0 = cell[0] * UNIT, cell[1] * UNIT
    tm = np.asarray(art.tmask).reshape(PAGE_PX, PAGE_PX)[
        y0:y0 + UNIT, x0:x0 + UNIT]
    hm = np.asarray(art.hmask).reshape(PAGE_PX, PAGE_PX)[
        y0:y0 + UNIT, x0:x0 + UNIT]
    if tm.any() or not hm.all():
        return None, 'Cosmos cell is not fully opaque'
    buf = np.frombuffer(art.buf, '<u2').reshape(PAGE_PX, PAGE_PX)[
        y0:y0 + UNIT, x0:x0 + UNIT]
    if (buf == 0).any():
        return None, 'Cosmos cell holds the colour key'
    return buf.copy(), None


def _matches(sec3_pal, page, pal, cell, img):
    """BUILD 720: is the tile's current 256px picture this Cosmos cell?"""
    if sec3_pal is None:
        return True, None
    a = np.frombuffer(bytes(page.data), np.uint8)[:65536].reshape(256, 256)[
        cell[1] * 16:cell[1] * 16 + 16, cell[0] * 16:cell[0] * 16 + 16]
    orig = sec3_pal[min(pal, len(sec3_pal) - 1)][a].astype(np.float64)
    v = img.astype(np.uint32)
    hd = np.stack([((v >> 11) & 31) << 3, ((v >> 5) & 63) << 2,
                   (v & 31) << 3], -1).astype(np.float64)
    hd = hd.reshape(16, 3, 16, 3, 3).mean((1, 3))
    if orig.mean() < MIN_MEAN:
        return False, 'the 256px cell is black filler'
    d = float(np.abs(orig - hd).mean())
    if d >= MATCH_DIFF:
        return False, 'the Cosmos cell is a different picture (%.0f)' % d
    return True, None


def plan_field(name, sec9, art, sec3=None):
    """(new sec9, moved [(x, y, slot, cell)], refused [(x, y, why)]).
    `sec3` (section 3, the palettes) turns on the BUILD 720 picture match;
    without it every exact Cosmos cell is taken, as in 717."""
    import diag_common as DC
    import field_bg_native as FN
    provider = getattr(art, 'provider', None)
    if provider is None:
        raise ValueError('no Cosmos provider')
    pal3 = None
    if sec3 is not None:
        import render_field as RF
        pal3 = RF._pal_rgb(sec3)
    getter = provider.open(name)
    pages, ts, te, px = DC.parse_pages(sec9)
    if px != PAGE_PX:
        raise ValueError('truecolor pages are %dpx' % px)
    pm = {p.slot: p for p in pages if p is not None}
    back = sec9.find(b'BACK')
    rows = [(layer, o) for layer, offs in DC.walk_layers(sec9, back, ts)
            for o in offs]
    used = {}                       # slot -> cells any record can sample
    for _layer, o in rows:
        c = _cell(sec9, o)
        slots = {sec9[o + T_BASE]} | ({sec9[o + T_FX]} if sec9[o + T_FX]
                                      else set())
        for s in slots:
            if c is None:
                used[s] = None      # an off-grid sampler: page not usable
            elif used.get(s, set()) is not None:
                used.setdefault(s, set()).add(c)
    dests = sorted(
        s for s, p in pm.items()
        if p.depth == 2 and p.px == PAGE_PX and not p.size_flag
        and not FX_LO <= s < FX_HI and s in used and used[s] is not None)
    free = {s: [(cx, cy) for cy in range(GRID) for cx in range(GRID)
                if (cx, cy) not in used[s]] for s in dests}
    moved, refused, writes = [], [], {}
    work = bytearray(sec9)
    for layer, o in rows:
        src = pm.get(sec9[o + T_BASE])
        if (layer != 1 or src is None or src.depth != 1 or src.px != 256
                or src.size_flag or not _static_l1(sec9, o)):
            continue
        x, y = struct.unpack_from('<hh', sec9, o + T_DST)
        cell = _cell(sec9, o)
        if cell is None:
            refused.append((x, y, 'UV off the cell grid'))
            continue
        img, why = _art_cell(provider, getter, name, src.slot,
                             sec9[o + T_PAL], cell)
        if img is None:
            refused.append((x, y, why))
            continue
        ok, why = _matches(pal3, src, sec9[o + T_PAL], cell, img)
        if not ok:
            refused.append((x, y, why))
            continue
        dest = next((s for s in dests if free[s]), None)
        if dest is None:
            refused.append((x, y, 'no free cell on an opaque 768px page'))
            continue
        dc = free[dest].pop(0)
        writes.setdefault(dest, []).append((dc, img))
        work[o + T_BASE] = dest
        struct.pack_into('<HH', work, o + T_SRC, dc[0] * 16, dc[1] * 16)
        struct.pack_into('<II', work, o + T_BIG, dc[0] * STEP, dc[1] * STEP)
        moved.append((x, y, dest, dc))
    if not moved:
        return sec9, moved, refused
    new_pages = list(pages)
    for s, items in writes.items():
        p = pm[s]
        a = np.frombuffer(p.data, '<u2', count=PAGE_PX * PAGE_PX).reshape(
            PAGE_PX, PAGE_PX).copy()
        for (cx, cy), img in items:
            a[cy * UNIT:(cy + 1) * UNIT, cx * UNIT:(cx + 1) * UNIT] = img
        data = a.astype('<u2').tobytes() + bytes(p.data[PAGE_PX * PAGE_PX * 2:])
        new_pages[s] = FN.Page(s, p.size_flag, p.depth, data, p.px)
    new9 = FN.replace_texture_block(bytes(work), new_pages, ts, te)
    # the page table, sizes and every unrelated byte must round-trip
    parsed, nts, _nte, npx = DC.parse_pages(new9)
    if (npx != px or nts != ts
            or [(p.slot, p.depth, p.px, p.size_flag) for p in parsed
                if p is not None]
            != [(p.slot, p.depth, p.px, p.size_flag) for p in pages
                if p is not None]):
        raise ValueError('page layout changed')
    if len(new9) != len(sec9):
        raise ValueError('section size changed')
    return new9, moved, refused


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled() or art is None or getattr(art, 'provider', None) is None:
        return total
    encode = encode or archive.encode_field
    for name in archive.names():
        if FIELDS is not None and name.lower() not in FIELDS:
            continue
        entry = archive.index.get(name)
        try:
            if entry is None or not archive.is_field(entry):
                continue
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            new9, moved, refused = plan_field(name, parts[8], art, parts[3])
        except Exception as exc:                               # noqa: BLE001
            if not str(exc).startswith('truecolor pages are'):
                total['refused'].append((name, str(exc)[:80]))
            continue                        # 256px fields: nothing stranded
        for x, y, why in refused:
            if why.startswith(('no Cosmos image', 'the 256px cell is black')):
                continue                    # nothing to restore: not news
            total['refused'].append((name, '(%d,%d) %s' % (x, y, why)))
        if not moved:
            continue
        parts[8] = new9
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s %s' % (name, ' '.join(
            '(%d,%d)->slot %d' % (x, y, s) for x, y, s, _c in moved)))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  STRANDED LAYER-1 TILES (BUILD 720): moved onto an '
                   'existing 768px page with their exact Cosmos cell, no page '
                   'added: %s. %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ())[:6]:
        out.append('  ! stranded tile %s: %s' % (name, why))
    return '\n'.join(out)
