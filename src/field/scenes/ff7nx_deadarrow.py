#!/usr/bin/env python3
"""Explicit exit arrows that lead nowhere. BUILD 778.

HARDWARE 10-22, rckt2 (Rocket Town, later visit): "we see this arrow
pointing somewhere, but walking there does not change the field ... this
house has a door we can walk through, but there is no exit arrow pointing
to the door like the other houses."

ONE CAUSE FOR BOTH. rckt2's section 8 is rckt's with the north exit (rckt
gateway 0, to map 561, its own arrow off) removed -- but rckt's explicit
red arrow for that exit (arrow 2, field (-27, 2615, 0)) was left in. Nothing
in rckt2 leads there: no gateway, no MAPJUMP to 561 in its script. And
ff7nx_pointers' duplicate-arrow rule (BUILD 611) pairs every explicit red
arrow with the nearest gateway arrow on screen within 80 units -- here the
house door (gateway 1, to map 558, 64 units away on screen but 1282 field
units away), whose arrow it then hid.

FIX: the stale arrow's type becomes 0 (not drawn), checked against its
exact coordinates. The dedupe table is computed from the built flevel, so
gateway 1's arrow comes back by itself. rckt is untouched (its arrow 2 still
has its exit).

SEVENTH_NX_NO_DEADARROW=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

OFF_ENV = 'SEVENTH_NX_NO_DEADARROW'
ARROWS = 0x224              # section 8: 12 x {x, y, z, type} int32
DEAD = {'rckt2': ((2, (-27, 2615, 0)),)}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def fix_sec8(name, sec8):
    """(section 8, arrows removed). Raises if an arrow is not as expected."""
    buf = bytearray(sec8)
    n = 0
    for k, xyz in DEAD.get(name, ()):
        o = ARROWS + 16 * k
        x, y, z, t = struct.unpack_from('<4i', buf, o)
        if (_s16(x), _s16(y), _s16(z)) != xyz:
            raise ValueError('arrow %d is not at %r' % (k, xyz))
        if t == 0:
            continue
        struct.pack_into('<i', buf, o + 12, 0)
        n += 1
    return bytes(buf), n


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in DEAD:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[7], n = fix_sec8(name, parts[7])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        if n:
            payloads[name] = encode(lgp.join_sections(parts))
            st['names'].append(name)
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  DEAD ARROW (BUILD 778): %s -- an exit arrow with no exit '
                   'removed. %s=1 disables.'
                   % (', '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! dead arrow %s: %s' % (name, why))
    return '\n'.join(out)
