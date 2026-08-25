#!/usr/bin/env python3
"""
ff7nx_palettedart.py -- use the colour capacity already present in an
exclusive, opaque, animated depth-1 background page.

The Highwind bridge sky is six full-page animation states.  Five of the
pages which survive the dense repack are still depth 1, but each uses only
11--15 indices from a 256-entry palette.  Upscaling those few colours makes
the large palette islands visible, and changing animation state makes their
boundaries crawl.

This pass does not promote, allocate, move, resize, repeat, or add a page.
For an audited page whose palette is used by no other depth-1 page, it:

  * box-reduces that page's own fully opaque Cosmos image to 256x256;
  * stores every distinct A1B5G5R5 colour in entries 1..N of the page's
    existing 256-entry palette (the measured maximum is 73);
  * replaces the existing 256x256 index array with indices into that table.

Index 0 stays reserved and is never emitted.  Unused palette entries stay
byte-identical.  Section 9's page headers, slots, record array, animation
states, UVs, size, and memory footprint therefore do not change.  This is
deliberately unlike the withdrawn Build-164 experiment, which allocated new
truecolour pages and produced black rectangles on hardware.

The mechanism is general, but admission is fingerprinted.  These five
Highwind variants are one background family and were audited with Kujata's
script reader: none executes MPPAL/STPAL/LDPAL/CPPAL/RTPAL/ADPAL (or their
v2 forms), so the palettes below are not runtime data.  Every structural
claim is checked again at build time and a field is all-or-nothing across
its five pages.  A mismatch is refused, never approximated.
"""
from __future__ import annotations

import hashlib
import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import ff7nx_marginblack as MB
import lgp

SECTION_PALETTE = 3
SECTION9 = 8
PAGE_PX = 256
T_PAL = FN.TILE_PALETTE_ID
T_TEX = FN.TILE_TEXTURE_ID
T_FX = FN.TILE_TEXTURE_ID2
T_PARAM = 26
T_STATE = 27
T_USE_FX = 28                  # u16
T_BLEND = 30
T_BIG_X = 42
T_BIG_Y = 46

NO_ENV = 'SEVENTH_NX_NO_PALETTED_ART'

# sha256 of the final Build-171 replay, before this pass.  fship_2, _23 and
# _25 share pages; _22 and _24 are the earlier weather trajectory.
_A = (
    '27ea36d4c632e2a9e54933bde4cc67eecbd0641bf71e4655a29ec249f472646a',
    '6df1e5cb07b359fb9c81252486d8e7f81fb1a75531502064592be34c99a35ed5',
    'a0fd54323463acb915ddb6df2624e5609910ac501c9194bcfa6064502b83fe6c',
    '2bb625dfb302b064244112f3d0b2275d0d7e6f75d741fbf06a045e489304e110',
    '008eef6407b70d78119307fb2d691ad82edef2b2075ec48c6a56c0c96426f4e5',
)
_B = (
    '498a421bcd5bea81a8d160b63cced62456a7dc97695381afa3deba7d5dfc64dd',
    '2726226453451ff3736500806a4bd4c3b71cccb2b7352fe7983597fcb089e5e1',
    '690b425eb7859c2a50f0aafd4bc269330a3997eab76cde1d6b0c39dddf0866ef',
    '68a4646544edee60e2dbd90e38469e9c078438661f65735b6e07f5692fca44b8',
    'd49539cc862cb335e7ce0f899331b19485a68ada4ad31fe7bb187c6d5c9b8711',
)
_C = (
    '45251b9419863beefe5d28179eff0e8b51aa7fe6b813bb9a2c9cb5f7f92071a8',
    '28943701beab524286f2ca8789c4cf9df53a8a625261b420b755a072f3b19fd8',
    'f0b43217c9021a706e528f98fef70e82e555aba33d99016dad27429d02d6df6b',
    'fe52f4f50b918bdc4bca77b134753d3968455700fb41094bbd8946aa79f9a5df',
    '2f9b28bb834848216b9a86b1f870cc7a3c7b103fbf111c9f8a765aac69c69e0b',
)

_STATES_A = ({4, 8}, {8, 16}, {16}, {32}, {32, 64})
_STATES_B = ({2, 4}, {4, 8}, {8}, {16}, {16, 32})

TARGETS = {
    'fship_2': (_A, _STATES_A),
    'fship_22': (_B, _STATES_B),
    'fship_23': (_A, _STATES_A),
    'fship_24': (_C, _STATES_B),
    'fship_25': (_A, _STATES_A),
}


class PalettedArtError(ValueError):
    pass


def enabled():
    return os.environ.get(NO_ENV, '').strip().lower() not in (
        '1', 'true', 'yes', 'on')


def _rgb565(buf, px):
    v = np.frombuffer(buf, '<u2').reshape(px, px)
    r = ((v >> 11) & 31).astype(np.uint16)
    g = ((v >> 5) & 63).astype(np.uint16)
    b = (v & 31).astype(np.uint16)
    return np.stack(((r << 3) | (r >> 2),
                     (g << 2) | (g >> 4),
                     (b << 3) | (b >> 2)), axis=-1).astype(np.uint8)


def _rgb555(codes):
    v = np.asarray(codes, dtype=np.uint16)
    r = v & 31
    g = (v >> 5) & 31
    b = (v >> 10) & 31
    return np.stack(((r << 3) | (r >> 2),
                     (g << 3) | (g >> 2),
                     (b << 3) | (b >> 2)), axis=-1).astype(np.uint8)


def _target_codes(page_art):
    """(256x256 A1B5G5R5 codes, 256x256x3 RGB target)."""
    if page_art.px < PAGE_PX or page_art.px % PAGE_PX:
        raise PalettedArtError('Cosmos page is %dpx, not an integer 256x '
                               'source' % page_art.px)
    # The sky is an opaque backdrop.  Refuse an alpha-bearing page rather
    # than collapsing its coverage into a palette that cannot represent it.
    # PageArt's BOX path can round a handful of 255 samples to 254.  Coverage
    # is nevertheless exactly opaque: no texel is below either its 8/255
    # transparency threshold or the honest 128/255 one-bit threshold.
    if np.asarray(page_art.tmask).any() \
            or not np.asarray(page_art.hmask).all():
        raise PalettedArtError('Cosmos page is not fully opaque')

    rgb = _rgb565(page_art.buf, page_art.px).astype(np.uint32)
    scale = page_art.px // PAGE_PX
    rgb = rgb.reshape(PAGE_PX, scale, PAGE_PX, scale, 3).sum((1, 3))
    rgb = ((rgb + scale * scale // 2) // (scale * scale)).astype(np.uint8)
    q = ((rgb.astype(np.uint16) * 31 + 127) // 255).astype(np.uint16)
    codes = q[..., 0] | (q[..., 1] << 5) | (q[..., 2] << 10)
    return codes, rgb


def _requantise(page_art):
    """(new indices, palette entries, target RGB, represented RGB)."""
    codes, target_rgb = _target_codes(page_art)
    unique = np.unique(codes)
    if len(unique) > 255:
        raise PalettedArtError('%d A1B5G5R5 colours need more than the 255 '
                               'non-key indices' % len(unique))
    indices = (np.searchsorted(unique, codes) + 1).astype(np.uint8)
    if not indices.min() or indices.max() > 255:
        raise PalettedArtError('index 0 escaped the reserved-key guard')
    represented = _rgb555(unique[np.asarray(indices, np.uint16) - 1])
    return indices, unique.astype(np.uint16), target_rgb, represented


def _records(sec9, surv):
    rows = []
    for layer, offsets in DC.walk_layers(sec9, surv['back_start'],
                                         surv['tex_start']):
        for off in offsets:
            use_fx, = struct.unpack_from('<H', sec9, off + T_USE_FX)
            base = sec9[off + T_TEX]
            fx = sec9[off + T_FX]
            rows.append({
                'off': off, 'layer': layer, 'base': base, 'fx': fx,
                'slot': fx if use_fx else base, 'use_fx': bool(use_fx),
                'pal': sec9[off + T_PAL], 'param': sec9[off + T_PARAM],
                'state': sec9[off + T_STATE], 'blend': sec9[off + T_BLEND],
                'x': struct.unpack_from('<i', sec9, off + T_BIG_X)[0],
                'y': struct.unpack_from('<i', sec9, off + T_BIG_Y)[0],
            })
    return rows


def _page_art(art, name, slot):
    provider = getattr(art, 'provider', None)
    if provider is None:
        raise PalettedArtError('the exact Cosmos art provider is unavailable')
    if set(provider.by_page.get((name, slot), ())) != {0}:
        raise PalettedArtError('slot %d does not have exactly palette-0 art'
                               % slot)
    if (name, slot, 0) in provider.ambiguous_slots:
        raise PalettedArtError('slot %d has more than one Cosmos state' % slot)
    getter = provider.open(name)
    out = getter(slot, 0)
    if out is None:
        raise PalettedArtError('slot %d palette-0 Cosmos art is missing' % slot)
    return out


def improve_field(name, parts, art):
    """Return (new parts or None, per-field measurements)."""
    hashes, expected_states = TARGETS[name]
    cols, hdr, npg, cpp = MB.palette_colours(parts[SECTION_PALETTE])
    if cpp != 256 or npg <= 14:
        raise PalettedArtError('palette is %d page(s) x %d colours' %
                               (npg, cpp))
    pages, tex_start, tex_end, px = DC.parse_pages(parts[SECTION9])
    surv = {'back_start': parts[SECTION9].find(b'BACK'),
            'tex_start': tex_start}
    rows = _records(parts[SECTION9], surv)

    plans = []
    for i, slot in enumerate(range(6, 11)):
        pal = slot + 4
        page = pages[slot]
        if page is None or page.depth != 1 or page.size_flag != 1 \
                or page.px != PAGE_PX or len(page.data) != PAGE_PX ** 2:
            raise PalettedArtError('slot %d is not one 256px size-1 depth-1 '
                                   'page' % slot)
        refs = [r for r in rows if r['slot'] == slot]
        cells = {(r['x'], r['y']) for r in refs}
        # Oversized-tile UVs are fixed point over the whole page: 32/256 of
        # 10,000,000 per atlas cell, not native pixel coordinates.
        step = 10_000_000 // 8
        want_cells = {(x * step, y * step) for y in range(8)
                      for x in range(8)}
        if len(refs) != 64 or cells != want_cells:
            raise PalettedArtError('slot %d is not the complete 8x8 sky '
                                   'atlas (%d record(s), %d cell(s))'
                                   % (slot, len(refs), len(cells)))
        if any(r['layer'] != 3 or r['base'] != slot or r['use_fx']
               or r['pal'] != pal or r['param'] != 1 or r['blend'] != 0
               for r in refs):
            raise PalettedArtError('slot %d bindings no longer describe the '
                                   'opaque layer-3 animation' % slot)
        states = {r['state'] for r in refs}
        if states != expected_states[i]:
            raise PalettedArtError('slot %d states are %r, expected %r'
                                   % (slot, sorted(states),
                                      sorted(expected_states[i])))

        # The palette may also be named by a truecolour page; that page does
        # not consult section 3.  It must not colour any other depth-1 page.
        foreign = []
        for r in rows:
            p = pages[r['slot']] if 0 <= r['slot'] < len(pages) else None
            if p is not None and p.depth == 1 and r['pal'] == pal \
                    and r['slot'] != slot:
                foreign.append((r['slot'], r['off']))
        if foreign:
            raise PalettedArtError('palette %d also colours depth-1 slot(s) %r'
                                   % (pal, sorted({x for x, _ in foreign})))

        old_indices = np.frombuffer(page.data, np.uint8)
        used = np.unique(old_indices)
        page_art = _page_art(art, name, slot)
        new_indices, new_colours, target_rgb, represented = _requantise(page_art)
        new_data = new_indices.tobytes()
        digest = hashlib.sha256(page.data).hexdigest()
        if digest == hashes[i]:
            if used.min() == 0 or not 11 <= len(used) <= 15:
                raise PalettedArtError('slot %d uses unexpected indices %r'
                                       % (slot, used.tolist()))
        elif page.data == new_data:
            if used.min() == 0:
                raise PalettedArtError('slot %d reintroduced reserved index 0'
                                       % slot)
        else:
            raise PalettedArtError('slot %d content fingerprint changed (%s)'
                                   % (slot, digest[:12]))

        old_rgb = _rgb555(cols[pal][old_indices]).reshape(PAGE_PX, PAGE_PX, 3)
        old_err = float(np.abs(old_rgb.astype(np.int16) -
                               target_rgb.astype(np.int16)).mean())
        new_err = float(np.abs(represented.astype(np.int16) -
                               target_rgb.astype(np.int16)).mean())
        plans.append((slot, pal, page, new_data, new_colours, len(used),
                      old_err, new_err))

    # All five frames passed.  Only now mutate copies, so one bad page cannot
    # leave a field with a mixed animation family.
    new_pages = list(pages)
    palbuf = bytearray(parts[SECTION_PALETTE])
    changed = False
    old_colours = new_colours_count = 0
    old_error = new_error = 0.0
    for slot, pal, page, data, colours, old_n, e0, e1 in plans:
        old_colours += old_n
        new_colours_count += len(colours)
        old_error += e0
        new_error += e1
        if page.data != data:
            new_pages[slot] = FN.Page(slot, page.size_flag, page.depth,
                                      data, page.px)
            changed = True
        poff = hdr + 2 * pal * cpp
        for j, value in enumerate(colours, 1):
            off = poff + 2 * j
            value = int(value)
            if struct.unpack_from('<H', palbuf, off)[0] != value:
                struct.pack_into('<H', palbuf, off, value)
                changed = True

    stats = {'pages': 5, 'pixels': 5 * PAGE_PX ** 2,
             'old_colours': old_colours, 'new_colours': new_colours_count,
             'old_error': old_error / 5, 'new_error': new_error / 5}
    if not changed:
        stats['already'] = 1
        return None, stats
    new9 = FN.replace_texture_block(parts[SECTION9], new_pages,
                                    tex_start, tex_end)
    if len(new9) != len(parts[SECTION9]):
        raise PalettedArtError('section 9 size changed')
    out = list(parts)
    out[SECTION_PALETTE] = bytes(palbuf)
    out[SECTION9] = new9
    # Reparse both edited structures before handing them back to the build.
    MB.palette_colours(out[SECTION_PALETTE])
    DC.parse_pages(out[SECTION9])
    return out, stats


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_: None):
    stats = {'fields': 0, 'pages': 0, 'pixels': 0, 'old_colours': 0,
             'new_colours': 0, 'old_error': 0.0, 'new_error': 0.0,
             'already': 0, 'refused': []}
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
            if out is not None:
                payloads[name] = encode(lgp.join_sections(out))
                stats['fields'] += 1
                stats['pages'] += st['pages']
                stats['pixels'] += st['pixels']
                stats['old_colours'] += st['old_colours']
                stats['new_colours'] += st['new_colours']
                stats['old_error'] += st['old_error']
                stats['new_error'] += st['new_error']
            else:
                stats['already'] += st.get('already', 0)
        except Exception as exc:                               # noqa: BLE001
            stats['refused'].append((name, '%s: %s' %
                                     (type(exc).__name__, exc)))
    return stats


def summarise(stats):
    n = stats.get('fields', 0)
    if not n:
        return ''
    return ('  PALETTED ART: %d animated sky page(s) in %d Highwind field(s) '
            'requantised in their existing slots and palettes (%d -> %d '
            'live colours; mean RGB error %.2f -> %.2f/255). No page, record, '
            'animation state, UV, byte count, or memory allocation changed.'
            % (stats['pages'], n, stats['old_colours'], stats['new_colours'],
               stats['old_error'] / n, stats['new_error'] / n))
