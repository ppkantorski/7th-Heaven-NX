"""ff7nx_lighttile.py -- a light-animation tile whose HD art does not match.
BUILD 885.

HARDWARE 11-03, cargoin (the train, as Cloud jumps in through the door): a
square of the doorway's light layer looks missing while Cloud jumps and is
back when he lands.

WHY. The tunnel light is background param 8, frames 0/1/2 (BGON 8,0..2).
Frame 0 has an opaque layer-2 tile at (-64,-56) (ID 34). In the 1997 field
that tile adds no light: with the door open, frame 0 and frame 1 render the
same pixels there (0 texels differ by more than 24). In this build its HD
art does not match what is beneath it (650 of 2304 texels differ), so while
frame 0 is up -- the jump -- that square shows as a hard-edged block. Frame 1
has no tile there, so on landing the square looks right again.

FIX. The tile is moved to frame 7 (state bit 0x80), which the field never
switches on, so it is never drawn. With the door open that is pixel-for-pixel
what 1997 shows; with the door closed it no longer pastes open-door pixels
over the door either (1997 does, at 1x). One byte of one record changes.

BUILD 885b, the same scene (hardware 11-03, video): the doorway's yellow
light bar loses whole squares in most frames. The bar is background param 1
(frames 0..4, BGON 1,0..4 in a loop). In 1997 frames 0..3 draw nothing over
the base picture (0 texels differ) and frame 4 adds a 153-texel flash.
Cosmos's base picture already paints the full, lit HD bar; the param-1
records (12, 85-100 % colour key) only cut it: frames 0..3 hide its right end
at a tile edge, frame 4 leaves a notch. Every param-1 record goes to frame 7
too, so the bar shows whole and steady, as Cosmos painted it. What is lost
is the 1997 frame-4 flash, which the HD art has no frames for.

BUILD 885c, the same scene (hardware 11-03, video + two stills): with the
door open, cells of the light around the doorway still come and go. They are
the 45 frame-0 records of param 8 (the tunnel light; 885 had hidden one). The
user's "bad" still matches the field with frame 0 up (mean error 20.6 vs
41.3 without it, over the 491 px frame 0 changes) and the "good" still the
field without it (25.7 vs 31.0). Frames 1 and 2 already draw nothing over
the HD picture (0 and 1 px differ). So all frame-0 records go to frame 7 and
the doorway holds the "good" look for as long as the door is open. In 1997
frame 0 adds a 100-texel flicker the HD art has no frames for.

SAFETY. A listed record must be the only layer-2 record at that square with
that param, state and ID; a listed param must have records only on layer 2,
none blended; and the field's script must never turn frame 7 on. Anything
else refuses and leaves the field alone. Idempotent.

SEVENTH_NX_NO_LIGHTTILE=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

OFF_ENV = 'SEVENTH_NX_NO_LIGHTTILE'
REC = 52
NEVER = 0x80                       # state bit 7: no BGON in the field uses it
# field -> [(layer, x, y, param, state bit, ID)]
FIELDS = {'cargoin': [(2, -64, -56, 8, 0x01, 34)]}
# field -> {param: (state bit or None for every state, expected record count)}
PARAMS = {'cargoin': {1: (None, 12), 8: (0x01, 45)}}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _rows(s9):
    import ff7nx_floordepth as FD
    return FD._rows(s9)


def _frame_used(sec1, param, bit):
    """True if any BGON/BGOFF in section 1 names `param` / state `bit`."""
    import field_dis as FD
    meta, _names, entries = FD.parse(sec1)
    state = bit.bit_length() - 1
    for ins in FD.walk(sec1, meta['code_start'], meta['string_table']):
        _off, n, _size, raw, err = ins
        if err:
            raise ValueError('section 1 does not disassemble cleanly')
        if n in ('bgon', 'bgoff') and len(raw) >= 4:
            if raw[1] == 0 and raw[2] == param and raw[3] == state:
                return True
            if raw[1] != 0 and raw[2] == param:
                return True          # a variable state: assume it may be
    return False


def fix(name, sec1, sec9):
    """(section 9, records changed)."""
    out = bytearray(sec9)
    n = 0
    for lay, x, y, param, bit, idv in FIELDS[name]:
        if _frame_used(sec1, param, NEVER):
            raise ValueError('param %d frame 7 is used by the script' % param)
        found, done = [], []
        for layer, _c, first, cnt in _rows(sec9):
            if layer != lay:
                continue
            for i in range(cnt):
                o = first + i * REC
                tx, ty = struct.unpack_from('<hh', sec9, o + 2)
                tid = struct.unpack_from('<H', sec9, o + 24)[0]
                if (tx, ty, sec9[o + 26], tid) != (x, y, param, idv):
                    continue
                if sec9[o + 27] == bit:
                    found.append(o)
                elif sec9[o + 27] == NEVER:
                    done.append(o)
        if done and not found:
            continue
        if len(found) != 1:
            raise ValueError('%d records at (%d,%d) param %d state %#x ID %d'
                             % (len(found), x, y, param, bit, idv))
        out[found[0] + 27] = NEVER
        n += 1
    for param, (bit, want) in PARAMS.get(name, {}).items():
        if _frame_used(sec1, param, NEVER):
            raise ValueError('param %d frame 7 is used by the script' % param)
        recs = []
        for layer, _c, first, cnt in _rows(sec9):
            for i in range(cnt):
                o = first + i * REC
                if sec9[o + 26] != param:
                    continue
                if layer != 2 or sec9[o + 28]:
                    raise ValueError('param %d has a layer-%d or blended record'
                                     % (param, layer))
                if bit is None or sec9[o + 27] in (bit, NEVER):
                    recs.append(o)
        if len(recs) != want:
            raise ValueError('param %d has %d records, expected %d'
                             % (param, len(recs), want))
        for o in recs:
            if out[o + 27] != NEVER:
                out[o + 27] = NEVER
                n += 1
    return bytes(out), n


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
            parts[8], n = fix(name, parts[0], parts[8])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:120]))
            continue
        if n:
            payloads[name] = encode(lgp.join_sections(parts))
            st['names'].append(name)
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  LIGHT TILE (BUILD 885c): 1997 light-animation tiles that '
                   'only cut Cosmos\'s lit HD art are no longer drawn (%s). '
                   '%s=1 disables.'
                   % (', '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! light tile %s: %s' % (name, why))
    return '\n'.join(out)
