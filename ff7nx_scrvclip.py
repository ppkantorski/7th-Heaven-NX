#!/usr/bin/env python3
"""ff7nx_scrvclip.py -- keep scripted camera rest positions inside the art
in the uncropped (240-unit) view.

BUILD 618l. trnad_1 (hardware, 10-02): after Tifa runs in, an 8-unit band
of the layer behind shows above the cave, exactly the height of the old 4:3
letterbox. The field script parks the camera with SCR2DL at y=24. With the
port's (and FFNx's) negation the camera point is p = -24. The camera range
is -136..136 and the layer-1/2 art covers -136..136 too. A 224-unit vanilla
view needs p >= -136 + 112 = -24, so the authored stop is flush in 4:3. The
uncropped 240-unit view needs p >= -136 + 120 = -16, so 8 units above the art
show.

FFNx clamps the scripted camera vertically only for fields whose config
sets `scripted_vertical_clip` (five fields in Cosmos's config), so it would
show the same band here. This port's runtime clamp (ff7nx_camclamp) is gated
the same way by ff7nx_vclip, deliberately, because a general vertical clamp
froze elevator pans.

THIS PASS IS NARROWER THAN A CLAMP. It edits only constant y targets of
SCR2D / SCR2DC / SCR2DL whose rest position:
  * is LEGAL in the vanilla 224-unit view (inside the range +-112), but
  * shows space beyond the layer-1/2 art in the 240-unit view
    (p - 120 < art top, or p + 120 > art bottom).
Each is moved by the smallest amount (at most 8 units) that brings the view
flush with the art, never past the uncropped range limit. Targets taken
from variables, pans that go further than vanilla allows (authored
reveals), fields whose art is shorter than 240 units, and fields that play
a movie (live models composited over an FMV need the authored camera) are
left alone. The op length never changes; only the int16 y
operand is rewritten.

SEVENTH_NX_NO_SCR_VCLIP=1 disables.
"""
from __future__ import annotations

import os
import struct

import diag_common as DC
import ff7nx_parallaxfill as PF

OFF_ENV = 'SEVENTH_NX_NO_SCR_VCLIP'
OPS = {0x64: 'SCR2D', 0x66: 'SCR2DC', 0x68: 'SCR2DL'}
HALF_43, HALF_UNCROP = 112, 120


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def art_extent(sec9):
    """(top, bottom) of layers 1 and 2 (static tiles), or None."""
    try:
        _pl, ts, _te, _px = DC.parse_pages(sec9)
    except Exception:                                          # noqa: BLE001
        return None
    ys = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        if layer not in (1, 2):
            continue
        for o in offs:
            if sec9[o + 26]:
                continue
            ys.append(struct.unpack_from('<h', sec9, o + 4)[0])
    if not ys:
        return None
    return min(ys), max(ys) + 16


def _operands(script, off, op):
    """(banks, y_offset_in_script) for a constant-y camera op, or None."""
    if op == 0x64:
        banks = script[off + 1]
        ybank = banks & 0x0F
        yat = off + 4
    else:
        b1, b2 = script[off + 1], script[off + 2]
        ybank = b1 & 0x0F
        banks = (b1, b2)
        yat = off + 5
    if ybank:
        return None
    return banks, yat


MOVIE_OPS = (0xF8, 0xF9, 0xFA, 0xFB)            # PMVIE MOVIE MVIEF MVCAM


def _has_movie(script):
    import echo_s_flevel as ES
    for a, b in ES._routine_blocks(script):
        stream, _ = ES._decode_block(script, a, b)
        if any(op in MOVIE_OPS for _off, op, _size in stream):
            return True
    return False


def plan(parts):
    """[(script_offset_of_y, old_y, new_y, opname)] for one field."""
    import echo_s_flevel as ES
    script, sec9 = parts[0], parts[8]
    hdr = PF.trigger_header(parts[7])
    lo = min(hdr['cam_top'], hdr['cam_bottom'])
    hi = max(hdr['cam_top'], hdr['cam_bottom'])
    if hi - lo < 2 * HALF_UNCROP:
        return []
    art = art_extent(sec9)
    if art is None:
        return []
    top, bottom = art
    if bottom - top < 2 * HALF_UNCROP:
        return []                              # the art cannot fill 240 anyway
    if _has_movie(script):
        return []                              # live models over an FMV:
                                               # the camera must stay authored
    out = []
    for a, b in ES._routine_blocks(script):
        stream, _ = ES._decode_block(script, a, b)
        for off, op, size in stream:
            if op not in OPS:
                continue
            got = _operands(script, off, op)
            if got is None:
                continue
            _banks, yat = got
            if yat + 2 > off + size:
                continue
            y = struct.unpack_from('<h', script, yat)[0]
            p = -y
            if not (lo + HALF_43 <= p <= hi - HALF_43):
                continue                       # an authored over-range pan
            q = p
            if q - HALF_UNCROP < top:
                q = min(top + HALF_UNCROP, hi - HALF_UNCROP)
            if q + HALF_UNCROP > bottom:
                q = max(bottom - HALF_UNCROP, lo + HALF_UNCROP)
            if q != p and abs(q - p) <= HALF_UNCROP - HALF_43:
                out.append((yat, y, -q, OPS[op]))
    return out


def apply_to_parts(parts):
    edits = plan(parts)
    if not edits:
        return parts, edits
    s = bytearray(parts[0])
    for yat, _old, new, _op in edits:
        struct.pack_into('<h', s, yat, new)
    parts = list(parts)
    parts[0] = bytes(s)
    return parts, edits


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    total = {'fields': [], 'ops': 0}
    if disabled():
        return total
    encode = encode or archive.encode_field
    for name in archive.names():
        entry = archive.index[name]
        try:
            if not archive.is_field(entry):
                continue
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts, edits = apply_to_parts(parts)
        except Exception:                                      # noqa: BLE001
            continue
        if not edits:
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['fields'].append(name)
        total['ops'] += len(edits)
    return total


def summarise(st):
    if not st.get('fields'):
        return ''
    return ('  SCRIPTED CAMERA VERTICAL FIT (BUILD 618l): %d camera stop(s) in '
            '%d field(s) moved <= 8 units so the 240-unit view stays on the '
            'art (%s). %s=1 disables.'
            % (st['ops'], len(st['fields']), ', '.join(st['fields'][:12])
               + (' ...' if len(st['fields']) > 12 else ''), OFF_ENV))
