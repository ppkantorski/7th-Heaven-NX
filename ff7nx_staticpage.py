#!/usr/bin/env python3
"""
ff7nx_staticpage.py -- promote an exclusive static page in place.

The lower deck in the Highwind bridge family is assembled from 384 layer-1
tiles.  Dense packing promotes 310 of them to 768px truecolour pages, but the
last 74 remain together on slot 0 as one 256px paletted page.  The boundary
between those two populations is the pair of locally pixelated rectangles in
fship_2.

This pass replaces that one page with Cosmos's exact 768px page-0/palette-0
image in the SAME slot.  It does not add a page, consume a free slot, rewrite
a tile, move a UV, or touch the animated sky.  That distinction is important:
Build 164 allocated sky pages in slots 24/25, which this port did not load and
therefore displayed as black rectangles.  Here the page count, slot set and
all records remain byte-identical.

The mechanism is general but admission is deliberately fingerprinted to the
four Highwind states with this exact residual page.  Every structural fact is
rechecked at build time and a mismatch refuses the field instead of guessing.
fship_22 is intentionally absent: its layer 1 is already completely 768px and
its remaining paletted page belongs to layer 2.
"""
from __future__ import annotations

import hashlib
import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import lgp


SECTION9 = 8
PAGE_SLOT = 0
PAGE_PX = 768
PAGE_PAL = 0
PAGE_REFS = 74
UV_SCALE = 10_000_000
TILE_SIZE = FN.TILE_SIZE

T_SRC_X = 6
T_SRC_Y = 8
T_W = 18
T_H = 20
T_PAL = FN.TILE_PALETTE_ID
T_PARAM = 26
T_STATE = 27
T_USE_FX = 28                 # u16
T_BLEND = 30
T_BASE = FN.TILE_TEXTURE_ID
T_FX = FN.TILE_TEXTURE_ID2
T_BIG_X = 42
T_BIG_Y = 46

NO_ENV = 'SEVENTH_NX_NO_STATIC_PAGE_PROMOTION'

# The output is 35.3125 MiB per admitted field.  Keep a little integer-rounding
# room but refuse any future layout that becomes materially heavier.  The log
# for the same configuration already reports a 40.50 MiB archive worst case;
# this is therefore below a field size the current loader/heap already serves.
MAX_FIELD_MB = 35.5

# SHA-256 of slot 0 after the complete Build-174 background pipeline and before
# this pass.  The 74 referenced records are byte-identical in all four states.
TARGETS = {
    'fship_2':
        'c243be8fa2a2f891b3605da56a9e2e4307b03be79326083d976a334305766c39',
    'fship_23':
        '1fe5ceb4001fed35a5cf609db5f18ddf3b997896aa2363a526850eb1a89ae89f',
    'fship_24':
        '27d2405e924cc010eb7a91845e8a891aa37e64e24bccf060d5950f77c8e085d7',
    'fship_25':
        'b1f8ef92b768fbcd1063c18747db47da3b0971ec3507704404b8d97fcc1c033e',
}
REFS_SHA256 = (
    '14a4660580215c1ad2808648e5c2e12313c1543a0c693f2bf9e6bc39c8a9c774'
)


class StaticPageError(ValueError):
    """The field is no longer the exact layout this correction proves safe."""


def enabled():
    return os.environ.get(NO_ENV, '').strip().lower() not in (
        '1', 'true', 'yes', 'on')


def _page_bytes(px, depth):
    """Raw page plus the engine's 32-bpp runtime surface."""
    return px * px * (depth + 4)


def _field_bytes(pages):
    return sum(_page_bytes(p.px, p.depth) for p in pages if p is not None)


def _page_art(art, name):
    provider = getattr(art, 'provider', None)
    if provider is None:
        raise StaticPageError('the exact Cosmos art provider is unavailable')
    if set(provider.by_page.get((name, PAGE_SLOT), ())) != {PAGE_PAL}:
        raise StaticPageError('slot 0 does not have exactly palette-0 art')
    if (name, PAGE_SLOT, PAGE_PAL) in provider.ambiguous_slots:
        raise StaticPageError('slot 0 has more than one Cosmos state')
    getter = provider.open(name)
    out = getter(PAGE_SLOT, PAGE_PAL)
    if out is None:
        raise StaticPageError('slot-0 palette-0 Cosmos art is missing')
    if out.px != PAGE_PX or len(out.buf) != PAGE_PX * PAGE_PX * 2:
        raise StaticPageError('Cosmos page is %dpx/%d bytes, expected %dpx'
                              % (out.px, len(out.buf), PAGE_PX))
    tmask = np.asarray(out.tmask).reshape(PAGE_PX, PAGE_PX)
    hmask = np.asarray(out.hmask).reshape(PAGE_PX, PAGE_PX)
    if tmask.any() or not hmask.all():
        raise StaticPageError('Cosmos page is not fully opaque')
    # The port uses 0x0000 as the depth-2 colour key.  PageArt normally lifts
    # opaque black to NEAR_BLACK; prove that invariant before embedding it.
    if (np.frombuffer(out.buf, '<u2') == 0).any():
        raise StaticPageError('Cosmos page contains the depth-2 colour key')
    return out


def _active_slot(sec9, off):
    use_fx, = struct.unpack_from('<H', sec9, off + T_USE_FX)
    return sec9[off + T_FX] if use_fx else sec9[off + T_BASE]


def _target_records(sec9, tex_start):
    back = sec9.find(b'BACK')
    rows = []
    for layer, offsets in DC.walk_layers(sec9, back, tex_start):
        for off in offsets:
            if _active_slot(sec9, off) == PAGE_SLOT:
                rows.append((layer, off))
    return rows


def _validate_records(sec9, rows):
    if len(rows) != PAGE_REFS:
        raise StaticPageError('slot 0 has %d active reference(s), expected %d'
                              % (len(rows), PAGE_REFS))
    if hashlib.sha256(b''.join(
            sec9[o:o + TILE_SIZE] for _layer, o in rows
    )).hexdigest() != REFS_SHA256:
        raise StaticPageError('slot-0 tile-record fingerprint changed')

    cells = set()
    for layer, off in rows:
        use_fx, = struct.unpack_from('<H', sec9, off + T_USE_FX)
        sx, sy = struct.unpack_from('<hh', sec9, off + T_SRC_X)
        w, h = struct.unpack_from('<HH', sec9, off + T_W)
        bx, by = struct.unpack_from('<ii', sec9, off + T_BIG_X)
        if (layer != 1 or use_fx or sec9[off + T_BASE] != PAGE_SLOT
                or sec9[off + T_PAL] != PAGE_PAL
                or sec9[off + T_PARAM] != 0
                or sec9[off + T_STATE] != 0
                or sec9[off + T_BLEND] != 0 or (w, h) != (0, 0)):
            raise StaticPageError('slot-0 references are not the exclusive '
                                  'opaque layer-1 population')
        # Dense records zero the legacy 16-bit UV and put the real atlas
        # coordinate in the fixed-point pair.  One cell is 16/256 of a page.
        step = UV_SCALE // 16
        if (sx, sy) != (0, 0) or bx % step or by % step \
                or not (0 <= bx <= 15 * step and 0 <= by <= 15 * step):
            raise StaticPageError('slot-0 fixed-point UV is not one 16x16 '
                                  'atlas cell')
        cells.add((bx, by))
    if len(cells) != PAGE_REFS:
        raise StaticPageError('slot-0 references do not own 74 unique cells')


def improve_field(name, parts, art):
    """Return (new parts or None, measurements) for one admitted field."""
    if name not in TARGETS:
        return None, {'already': 0}
    sec9 = parts[SECTION9]
    pages, tex_start, tex_end, page_px = DC.parse_pages(sec9)
    if page_px != PAGE_PX:
        raise StaticPageError('truecolour page size is %dpx, expected %dpx'
                              % (page_px, PAGE_PX))
    page = pages[PAGE_SLOT]
    if page is None or page.slot != PAGE_SLOT or page.size_flag != 0:
        raise StaticPageError('slot 0 is not the expected size-0 page')

    page_art = _page_art(art, name)
    rows = _target_records(sec9, tex_start)
    _validate_records(sec9, rows)

    if page.depth == 2 and page.px == PAGE_PX and page.data == page_art.buf:
        return None, {'already': 1, 'tiles': len(rows),
                      'before_bytes': _field_bytes(pages),
                      'after_bytes': _field_bytes(pages)}
    if page.depth != 1 or page.px != 256 or len(page.data) != 256 * 256:
        raise StaticPageError('slot 0 is neither the audited 256px page nor '
                              'the exact promoted page')
    digest = hashlib.sha256(page.data).hexdigest()
    if digest != TARGETS[name]:
        raise StaticPageError('slot-0 content fingerprint changed (%s)'
                              % digest[:12])

    before = _field_bytes(pages)
    new_pages = list(pages)
    new_pages[PAGE_SLOT] = FN.Page(PAGE_SLOT, page.size_flag, 2,
                                   page_art.buf, PAGE_PX)
    after = _field_bytes(new_pages)
    if after > int(MAX_FIELD_MB * 1048576):
        raise StaticPageError('promotion would use %.4f MiB, over %.1f MiB'
                              % (after / 1048576.0, MAX_FIELD_MB))

    prefix = sec9[:tex_start]
    old_slots = tuple(i for i, p in enumerate(pages) if p is not None)
    new9 = FN.replace_texture_block(sec9, new_pages, tex_start, tex_end)
    parsed, new_start, _new_end, new_px = DC.parse_pages(new9)
    new_slots = tuple(i for i, p in enumerate(parsed) if p is not None)
    if new9[:new_start] != prefix:
        raise StaticPageError('tile records changed while replacing page 0')
    if new_slots != old_slots or new_px != PAGE_PX:
        raise StaticPageError('page slots or truecolour size changed')
    check = parsed[PAGE_SLOT]
    if (check is None or check.depth != 2 or check.size_flag != 0
            or check.px != PAGE_PX or check.data != page_art.buf):
        raise StaticPageError('promoted page did not round-trip exactly')

    out = list(parts)
    out[SECTION9] = new9
    return out, {'already': 0, 'tiles': len(rows),
                 'before_bytes': before, 'after_bytes': after}


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_: None):
    stats = {'fields': 0, 'pages': 0, 'tiles': 0, 'bytes_added': 0,
             'max_before': 0, 'max_after': 0, 'already': 0, 'refused': []}
    if not enabled() or art is None:
        return stats
    encode = encode or archive.encode_field
    for name in TARGETS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            payload = payloads.get(name)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = lgp.split_sections(raw)
            out, st = improve_field(name, parts, art)
            if out is None:
                stats['already'] += st.get('already', 0)
                continue
            payloads[name] = encode(lgp.join_sections(out))
            stats['fields'] += 1
            stats['pages'] += 1
            stats['tiles'] += st['tiles']
            stats['bytes_added'] += st['after_bytes'] - st['before_bytes']
            stats['max_before'] = max(stats['max_before'], st['before_bytes'])
            stats['max_after'] = max(stats['max_after'], st['after_bytes'])
        except Exception as exc:                               # noqa: BLE001
            stats['refused'].append((name, '%s: %s' %
                                     (type(exc).__name__, exc)))
    return stats


def summarise(stats):
    if not stats.get('fields'):
        return ''
    return ('  STATIC PAGE PROMOTION: %d lower-deck layer-1 cell(s) across '
            '%d Highwind field(s) moved 256 -> 768 in existing slot 0. '
            'Page count, slot set, tile records, UVs, palettes and animation '
            'are unchanged; worst field %.2f -> %.2f MiB. Set %s=1 to '
            'disable.'
            % (stats['tiles'], stats['fields'],
               stats['max_before'] / 1048576.0,
               stats['max_after'] / 1048576.0, NO_ENV))
