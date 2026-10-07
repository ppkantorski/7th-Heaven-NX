#!/usr/bin/env python3
"""ff7nx_shakepad.py -- one extra row of art below (or above) a field whose
picture ends exactly at the edge of the 240-unit view, when its script
shakes the screen vertically.

BUILD 618m. zcoal_1 (the coal train), hardware 10-03: while the train
shakes (SHAKE type 2, y amplitude 4), a thin strip under the train shows
the layers behind at the bottom of the screen.
  * Layers 1/2 end at y=128, exactly the bottom of the view at the camera
    clamp (range -112..128).
  * Vanilla's 224-unit view left 8 units of art below the screen for the
    shake to move into. The uncropped 240-unit view leaves none.

FIX: the bottom row of layer-1/2 records (all of them, animated ones too)
is copied once more 16 units further down. The same goes for the top edge
when the art's top is the view's top. The copies reuse the row's own cells,
palettes, states and z; nothing else changes. Only fields that:
  * issue a SHAKE with a constant nonzero y amplitude, and
  * have that edge of the layer-1/2 art within 8 units of the view limit.
The copies are bound to ONE new depth-2 page in a free opaque slot holding
byte copies of their cells: the source pages are full (256 bindings each), so
re-binding them would break the per-page cap. FIELDS is the measured list
(the coal train); other shaking fields with flush art are reported by
`candidates()` but not changed.

SEVENTH_NX_NO_SHAKE_PAD=1 disables.
"""
from __future__ import annotations

import os
import struct

import diag_common as DC
import ff7nx_parallaxfill as PF

OFF_ENV = 'SEVENTH_NX_NO_SHAKE_PAD'
FIELDS = ()      # 618n: OFF -- the copied row drew garbage on zcoal_1; see ff7nx_trainlower
OPAQUE_SLOTS = list(range(1, 15)) + [26, 27, 28]
UV_CELL = 625000
OP_SHAKE = 0x5E
ROW = 16
SLACK = 8


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def shake_y(script):
    """Largest constant y amplitude of the field's SHAKE ops (0 = none)."""
    import echo_s_flevel as ES
    amp = 0
    for a, b in ES._routine_blocks(script):
        stream, _ = ES._decode_block(script, a, b)
        for off, op, size in stream:
            if op != OP_SHAKE or size < 8:
                continue
            if script[off + 1] or script[off + 2]:
                continue                       # amplitudes from variables
            typ = script[off + 3]
            if typ & 2:
                amp = max(amp, script[off + 6])
    return amp


def plan(parts):
    """(new section 9, {'bottom': n, 'top': n}) or (None, reason)."""
    sec9 = parts[8]
    if not shake_y(parts[0]):
        return None, 'no vertical shake'
    hdr = PF.trigger_header(parts[7])
    lo, hi = sorted((hdr['cam_top'], hdr['cam_bottom']))
    if hi - lo < 240:
        return None, 'camera range shorter than the view'
    survey = DC.survey(sec9)
    rows = [r for r in PF._layers(sec9, survey['back_start'],
                                  survey['tex_start']) if r[0] in (1, 2)]
    recs = {}
    ys = []
    for layer, _c, first, n in rows:
        recs[layer] = [sec9[first + i * 52:first + (i + 1) * 52]
                       for i in range(n)]
        ys += [struct.unpack_from('<h', r, 4)[0] for r in recs[layer]
               if not r[26]]
    if not ys:
        return None, 'no static layer-1/2 art'
    amin, amax = min(ys), max(ys) + ROW
    add = {1: [], 2: []}
    did = {}
    if hi <= amax < hi + SLACK:
        y0 = amax - ROW
        for layer, rs in recs.items():
            for r in rs:
                if struct.unpack_from('<h', r, 4)[0] == y0:
                    b = bytearray(r)
                    struct.pack_into('<h', b, 4, y0 + ROW)
                    add[layer].append(bytes(b))
        did['bottom'] = sum(len(v) for v in add.values())
    if lo - SLACK < amin <= lo:
        n0 = sum(len(v) for v in add.values())
        for layer, rs in recs.items():
            for r in rs:
                if struct.unpack_from('<h', r, 4)[0] == amin:
                    b = bytearray(r)
                    struct.pack_into('<h', b, 4, amin - ROW)
                    add[layer].append(bytes(b))
        did['top'] = sum(len(v) for v in add.values()) - n0
    if not any(add.values()):
        return None, 'art is not flush with the view'
    import numpy as np
    import field_bg_native as FN
    pages_l, _ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pages_l if p is not None}
    free = [s for s in OPAQUE_SLOTS if s not in pm]
    if not free:
        return None, 'no free opaque slot'
    slot = free[0]
    cs = px // 16
    page = np.zeros((px, px), np.uint16)
    cells = {}
    for layer in add:
        out_recs = []
        for r in add[layer]:
            if r[28]:
                continue                       # FX tile: not copied
            src = pm.get(r[32])
            if src is None or src.depth != 2 or src.size_flag:
                return None, 'row cell on page %d is not a 16-grid HD page' \
                    % r[32]
            u, v = struct.unpack_from('<II', r, 42)
            key = (r[32], u, v)
            if key not in cells:
                n = len(cells)
                if n >= 256:
                    return None, 'more than 256 row cells'
                cx, cy = int(round(u / 1e7 * 16)), int(round(v / 1e7 * 16))
                data = np.frombuffer(src.data, '<u2').reshape(px, px)
                nx, ny = n % 16, n // 16
                page[ny * cs:(ny + 1) * cs, nx * cs:(nx + 1) * cs] = \
                    data[cy * cs:(cy + 1) * cs, cx * cs:(cx + 1) * cs]
                cells[key] = (nx, ny)
            nx, ny = cells[key]
            b = bytearray(r)
            b[32] = slot
            struct.pack_into('<hh', b, 10, nx * 16, ny * 16)
            struct.pack_into('<II', b, 42, nx * UV_CELL, ny * UV_CELL)
            out_recs.append(bytes(b))
        add[layer] = out_recs
    buf = bytearray(sec9)
    for layer, count_at, first, n in sorted(rows, key=lambda r: -r[2]):
        extra = add.get(layer)
        if not extra:
            continue
        end = first + n * 52
        buf[end:end] = b''.join(extra)
        struct.pack_into('<H', buf, count_at, n + len(extra))
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), px)
    plist[slot] = FN.Page(slot, 0, 2, page.astype('<u2').tobytes(), px)
    out = FN.replace_texture_block(bytes(buf), plist, t0, t1)
    did['slot'] = slot
    import field_bg_pagecap as PC
    counts = PC.effective_counts(out, px)
    over = {k: v for k, v in counts.items() if v > PC.MAX_TILES_PER_PAGE}
    if over:
        return None, 'binding-page cap: %s' % over
    return out, did


def apply_to_flevel(archive, payloads, encode=None, mb_cap=None,
                    raw_cap=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled():
        return total
    encode = encode or archive.encode_field
    import field_bg_repack as FR
    for name in FIELDS:
        entry = archive.index.get(name)
        try:
            if entry is None or not archive.is_field(entry):
                continue
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            if not shake_y(parts[0]):
                continue
            new9, did = plan(parts)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:80]))
            continue
        if new9 is None:
            total['refused'].append((name, did))
            continue
        pl, _ts, _te, _px = DC.parse_pages(new9)
        run = sum(FR._page_bytes(q.px, q.depth) for q in pl if q)
        if mb_cap is not None and run > mb_cap * 1048576.0:
            total['refused'].append((name, '%.1f MB over cap' % (run / 1048576.0)))
            continue
        if raw_cap is not None and len(raw) - len(parts[8]) + len(new9) > raw_cap:
            total['refused'].append((name, 'raw over cap'))
            continue
        parts[8] = new9
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s %s' % (name, did))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  SHAKE PAD (BUILD 618m): one row of art added past the '
                   'view edge on %d shaking field(s) (%s). %s=1 disables.'
                   % (len(st['names']), ', '.join(st['names'][:10])
                      + (' ...' if len(st['names']) > 10 else ''), OFF_ENV))
    for name, why in st.get('refused', ())[:5]:
        out.append('  ! shake pad %s: %s' % (name, why))
    return '\n'.join(out)
