"""ff7nx_gatewall.py -- the Wall Market gate's door edge stays behind the
wall. BUILD 883b (883's 16:9-margin version replaced: the door never reaches
the margin).

HARDWARE 11-02, mds6_2 (Sector 6 park): when the gate slides open, a small
grey rectangle and a line appear on the wall just right of the hazard-stripe
pillar (screen x 1021-1045, y 42-90 at camera 0,-150 -- field x 127..135,
y -256..-240). They are not there with the gate closed.

WHY. The gate is two field models (entities drr / drl) that CMOVE apart.
The right half ends with its top edge at field x ~127..135, y ~-256, at a
camera depth of about 1062 in tile-ID units (depth/4). A model is
depth-tested against background texels only.

The 1997 wall there is layer 2. Row y = -256 is ID 1 (front-most) at
x = 112 and above, but the square at (128, -256) is ID 1086 -- and in 1997
it paints almost nothing: 244 of its 256 texels are the colour key, so the
door's edge simply showed in that gap. Cosmos paints wall across the gap,
so the same edge now shows ON the wall: the door (~1062) is nearer than the
square (1086).

FIX. That square takes the depth of its left neighbour on the same row
(ID 1, IDBig 10000), so the door edge goes behind it exactly as it already
does one square to the left. Characters are unaffected in practice: a head
reaching row -256 is already behind the wall at x = 112 in the 1997 field.
Only the depth fields of that one record change; no record is added, no
art changes.

SAFETY. Applied only when the field still has exactly the 1997 layout
there: one plain layer-2 record at each of the two squares, with ID 1086
and ID 1. Anything else refuses and leaves the field alone. The result is
re-parsed and compared: every other byte of section 9 is unchanged.

SEVENTH_NX_NO_GATEWALL=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

OFF_ENV = 'SEVENTH_NX_NO_GATEWALL'
# field -> [(square to fix, expected ID, neighbour to copy, its expected ID)]
FIELDS = {'mds6_2': [((128, -256), 1086, (112, -256), 1)]}
REC = 52
OFF_ID = 24
OFF_IDBIG = 38


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _rows(s9):
    import ff7nx_floordepth as FD
    return FD._rows(s9)


def _layer2(s9):
    """{(x, y): [record offsets]} for layer 2."""
    out = {}
    for lay, _c, first, n in _rows(s9):
        if lay != 2:
            continue
        for i in range(n):
            o = first + i * REC
            x, y = struct.unpack_from('<hh', s9, o + 2)
            out.setdefault((x, y), []).append(o)
    return out


def _plain(s9, o):
    return not (s9[o + 26] or s9[o + 27] or s9[o + 28])


def plan(name, s9, fixes=None):
    """(new section 9, records changed)."""
    fixes = fixes if fixes is not None else FIELDS[name]
    l2 = _layer2(s9)
    out = bytearray(s9)
    changed = []
    for sq, want, nb, nb_want in fixes:
        a, b = l2.get(sq, []), l2.get(nb, [])
        if len(a) != 1 or len(b) != 1:
            raise ValueError('%s / %s: %d / %d layer-2 records, expected 1'
                             % (sq, nb, len(a), len(b)))
        oa, ob = a[0], b[0]
        if not (_plain(s9, oa) and _plain(s9, ob)):
            raise ValueError('%s / %s is animated or blended' % (sq, nb))
        ida = struct.unpack_from('<h', s9, oa + OFF_ID)[0]
        idb = struct.unpack_from('<h', s9, ob + OFF_ID)[0]
        if idb != nb_want:
            raise ValueError('%s has ID %d, expected %d' % (nb, idb, nb_want))
        if ida == idb:
            continue                          # already done
        if ida != want:
            raise ValueError('%s has ID %d, expected %d' % (sq, ida, want))
        out[oa + OFF_ID:oa + OFF_ID + 2] = s9[ob + OFF_ID:ob + OFF_ID + 2]
        out[oa + OFF_IDBIG:oa + OFF_IDBIG + 4] = \
            s9[ob + OFF_IDBIG:ob + OFF_IDBIG + 4]
        changed.append(oa)
    out = bytes(out)
    _verify(s9, out, changed)
    return out, len(changed)


def _verify(old, new, changed):
    if len(old) != len(new):
        raise ValueError('section 9 changed size')
    allowed = set()
    for o in changed:
        allowed |= set(range(o + OFF_ID, o + OFF_ID + 2))
        allowed |= set(range(o + OFF_IDBIG, o + OFF_IDBIG + 4))
    import numpy as np
    diff = np.nonzero(np.frombuffer(old, np.uint8)
                      != np.frombuffer(new, np.uint8))[0]
    if any(int(i) not in allowed for i in diff):
        raise ValueError('bytes outside the depth fields changed')


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in FIELDS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], n = plan(name, parts[8])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:120]))
            continue
        if n:
            payloads[name] = encode(lgp.join_sections(parts))
            st['names'].append('%s: %d wall square(s) brought to the front '
                               'row\'s depth' % (name, n))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  GATE WALL (BUILD 883b): the gate\'s door edge stays '
                   'behind the wall -- %s. %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! gate wall %s: %s' % (name, why))
    return '\n'.join(out)
