#!/usr/bin/env python3
"""ff7nx_shake60.py -- the field SHAKE at its 1997 pace at 60 fps. BUILD 711.

Hardware (ztruck, zcoal_1/2/3): "some of the bounces jitter instead of
tracing out smooth paths ... it almost feels like it could trace out more
steps while maintaining the same movement over the same duration."

WHAT THE ENGINE DOES. SHAKE (opcode 0x5E, x86 0x61E529) stores per axis an
amplitude and a period N in field ticks (struct 0xCC0D88 +0x8A x, +0x98 y).
field_loop (0x63C17F) runs 0x6431BB once per tick for each axis: every N
ticks it picks a new target (table 0x9059A0 x amplitude / 256, the sign
alternating) and eases from the last target to it over the N ticks with the
cosine interpolator 0x6430F2. The offset is a whole unit (a byte, +3).

WHY IT JITTERS. N counts field ticks and the port runs 60 of them a second.
ff7nx_60fps scales the frame counts of JUMP, SCRLC, SCRLA, SCR2DC, SCR2DL,
SCRLP, OFST and VWOFT, not SHAKE, so every shake runs at twice its 1997
speed: zcoal's N = 2 gives a new target every 2 ticks (1/30 s) with one
step between, a buzz rather than a bounce.

FIX. Every constant period operand of every SHAKE is doubled (capped at
255, the byte), so a shake takes as long as in 1997 and the easing has
twice the steps. Amplitudes, flags and the ops that stop a shake (flags 0)
are not touched; a period read from a variable is left alone. Script
operands only, op lengths unchanged.

Operands: b1 b2 banks; b3 flags (1 x, 2 y); b4 x amplitude, b5 x period
(bank 2: b1 low nibble), b6 y amplitude, b7 y period (bank 4: b2 low
nibble).

SEVENTH_NX_NO_SHAKE60=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os

OFF_ENV = 'SEVENTH_NX_NO_SHAKE60'
OP_SHAKE = 0x5E
CAP = 255


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def plan_field(sec):
    """(new script section, [(offset, axis, old, new)]) -- [] when the
    field has no constant SHAKE period to scale."""
    import echo_s_flevel as ES
    sec = bytearray(sec)
    edits = []
    for a, b in ES._routine_blocks(bytes(sec)):
        stream, _ = ES._decode_block(bytes(sec), a, b)
        for off, op, size in stream:
            if op != OP_SHAKE or size < 8:
                continue
            flags = sec[off + 3]
            for axis, bit, at, bank in (('x', 1, off + 5, sec[off + 1] & 0x0F),
                                        ('y', 2, off + 7, sec[off + 2] & 0x0F)):
                if not flags & bit or bank:
                    continue
                old = sec[at]
                if not old:
                    continue
                new = min(CAP, old * 2)
                if new != old:
                    sec[at] = new
                    edits.append((off, axis, old, new))
    return bytes(sec), edits


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'ops': 0, 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            if OP_SHAKE not in parts[0]:
                continue
            parts[0], edits = plan_field(parts[0])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:90]))
            continue
        if not edits:
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append(name)
        st['ops'] += len(edits)
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  SHAKE 60 (BUILD 711): %d SHAKE period(s) doubled in %d '
                   'field(s), so a shake runs at its 1997 pace with twice the '
                   'easing steps (%s). %s=1 disables.'
                   % (st['ops'], len(st['names']),
                      ', '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! shake60 %s: %s' % (name, why))
    return '\n'.join(out)
