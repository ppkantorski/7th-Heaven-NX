#!/usr/bin/env python3
"""ff7nx_l4offset.py -- FFNx's widescreen h_offset on layer 4, baked into the data.

BUILD 618l. zcoal_1 and zcoal_3 (the Corel coal train), hardware 10-02:
  * zcoal_1: the trestle starts in mid-air beside the locomotive, as if out
    of nowhere.
  * zcoal_3: the rock wall the train runs along stops at the old 4:3 edge,
    leaving the 16:9 side empty.

FFNx, background.cpp `field_layer4_shift_tile_position`:

    if (widescreen_enabled && is_fieldmap_wide())
        tile_position->x -= widescreen.getHorizontalOffset();

Cosmos's widescreen config gives exactly two fields an h_offset:
zcoal_1 (+56) and zcoal_3 (-54). FFNx adds it to the camera point (the port
gets the same camera from ff7nx_ws's range bake) and subtracts it from every
layer-4 tile, so a layer-4 object stays aligned with the shifted view.
Nothing in the port did the second half, so both layer-4 objects sat
h_offset units away from where FFNx draws them:
  * zcoal_1's trestle (x -40..120) belongs at -96..64, starting under the
    locomotive and ending at the wagon.
  * zcoal_3's wall (x -32..160) belongs at 22..214, reaching the right 16:9
    edge.

FIX: every layer-4 record's stored x moves by -h_offset. The port then draws
it where FFNx does, provided the wrap and cull make the same decisions. That
is PROVED per field: at every integer bg.x in the layer's reachable range
±64, the port's shift + cull of the new x must equal FFNx's shift of the old
x followed by the offset and cull. Otherwise the field is refused and left
alone.

SEVENTH_NX_NO_L4_OFFSET=1 disables.
"""
from __future__ import annotations

import os
import struct

import diag_common as DC
import ff7nx_paraudit as PA
import ff7nx_parallaxfill as PF

OFF_ENV = 'SEVENTH_NX_NO_L4_OFFSET'
L, R, HW = PA.PORT['L'], PA.PORT['R'], PA.PORT['HW']


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def offsets(config):
    """{field: h_offset} from a parsed widescreen config."""
    out = {}
    for name, entry in (config or {}).items():
        if isinstance(entry, dict) and int(entry.get('h_offset', 0) or 0):
            out[name] = int(entry['h_offset'])
    return out


def _port(x, bg, w):
    if x <= bg - L or x >= bg + R:
        x += -w if x >= bg - HW else w
    return x if bg - L < x < bg + R else None


def _ffnx(x, bg, w, h):
    if x <= bg - L or x >= bg + R:
        x += -w if x >= bg - HW else w
    x -= h
    return x if bg - L < x < bg + R else None


def plan_field(parts, h, script=None):
    """(new section 9, records moved) -- raises unless proved."""
    sec9 = parts[8]
    hdr = PF.trigger_header(parts[7])
    w = hdr['bg4_w']
    survey = DC.survey(sec9)
    rows = [r for r in PF._layers(sec9, survey['back_start'],
                                  survey['tex_start']) if r[0] == 4]
    if len(rows) != 1 or not rows[0][3]:
        raise ValueError('no layer 4')
    _l, _c, first, count = rows[0]
    ops = PA.bgscr_ops(script).get(4, []) if script else [(None, None)]
    rx, _ry, _mv = PA.reach(hdr, 4, ops)
    xs = [struct.unpack_from('<h', sec9, first + i * 52 + 2)[0]
          for i in range(count)]
    for bg in range(int(rx[0]) - 64, int(rx[1]) + 65):
        for x in set(xs):
            if _port(x - h, bg, w) != _ffnx(x, bg, w, h):
                raise ValueError('wrap/cull differs at bg.x %d, x %d' % (bg, x))
    buf = bytearray(sec9)
    for i, x in enumerate(xs):
        struct.pack_into('<h', buf, first + i * 52 + 2, x - h)
    return bytes(buf), count


def apply_to_flevel(archive, payloads, config, encode=None,
                    log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled():
        return total
    encode = encode or archive.encode_field
    for name, h in sorted(offsets(config).items()):
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], n = plan_field(parts, h, parts[0])
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %d layer-4 record(s) x %+d'
                              % (name, n, -h))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  LAYER-4 H_OFFSET (BUILD 618l): FFNx\'s widescreen '
                   'layer-4 offset baked in (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! layer-4 h_offset %s: %s' % (name, why))
    return '\n'.join(out)
