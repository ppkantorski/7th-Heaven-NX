"""ff7nx_cargowait.py -- no empty dialogue box after "Cloud!" in cargoin.
BUILD 884.

HARDWARE 11-03, cargoin (the train after the first bombing mission, Cloud
jumps in): Wedge says "Cloud!", Biggs says "Cloud!!", then an EMPTY box opens
near the top of the screen before Cloud's "Late for our appointment?".

WHY. Echo-S rewrites the scene. Cloud's script starts Biggs's line without
waiting for it, then reuses the same window for his own reply:

    REQSW aval, prio 6, script 7   ; Biggs: WSIZE 0 / MESSAGE 0,"Cloud!!"
    ANIME / CANM!
    WAIT  30
    WSIZE 0, (0x50, 0x18, 0xBA, 0x29)
    MESSAGE 0, "Late for our appointment?"

Echo-S assumes its Auto Text Advance: Biggs's voiced box closes by itself
inside those 30 frames. With Auto off, Biggs's box is still open in window 0
when Cloud's WSIZE resizes that window, which leaves a box at Cloud's
position with no text that has to be dismissed before Cloud speaks. In the
1997 script Cloud waits for a character's script (REQEW) before his reply.

FIX. That one REQSW becomes REQEW (opcode 0x02 -> 0x03, same arguments, same
length): Cloud waits until Biggs's box closes -- after the voice with Auto on,
after the player's press with Auto off -- and then carries on exactly as
Echo-S wrote it. One byte of section 1 changes; nothing moves.

SAFETY. Applied only where the exact Echo-S sequence (REQSW avaf 6/6, REQSW
aval 6/7, ANIME 5, CANM! 6, WAIT 30, WSIZE 0 ... MESSAGE 0,0x0D) occurs once
in the field's script. Anything else refuses and leaves the field alone.

SEVENTH_NX_NO_CARGOWAIT=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os

OFF_ENV = 'SEVENTH_NX_NO_CARGOWAIT'
REQSW, REQEW = 0x02, 0x03
# field -> (exact byte context, index of the opcode to change)
FIELDS = {'cargoin': (bytes.fromhex(
    '020DC6'                      # REQSW avaf (13) prio 6 script 6: Wedge
    '020CC7'                      # REQSW aval (12) prio 6 script 7: Biggs
    'A30501'                      # ANIME 5
    'BC06000901'                  # CANM! 6
    '241E00'                      # WAIT 30
    '500050001800BA002900'        # WSIZE 0, 0x50, 0x18, 0xBA, 0x29
    '40000D'), 3)}                # MESSAGE 0, 0x0D: Cloud's reply


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _find(sec1, pat):
    out, i = [], sec1.find(pat)
    while i >= 0:
        out.append(i)
        i = sec1.find(pat, i + 1)
    return out


def fix_sec1(name, sec1):
    """(section 1, bytes changed). Raises if the sequence is not as expected."""
    pat, k = FIELDS[name]
    if pat[k] != REQSW:
        raise AssertionError('pattern does not have REQSW at %d' % k)
    done = pat[:k] + bytes([REQEW]) + pat[k + 1:]
    if len(_find(sec1, done)) == 1 and not _find(sec1, pat):
        return sec1, 0                                  # already applied
    hits = _find(sec1, pat)
    if len(hits) != 1:
        raise ValueError('the Echo-S sequence occurs %d time(s), expected 1'
                         % len(hits))
    buf = bytearray(sec1)
    buf[hits[0] + k] = REQEW
    return bytes(buf), 1


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
            parts[0], n = fix_sec1(name, parts[0])
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
        out.append('  CARGO WAIT (BUILD 884): Cloud waits for Biggs\'s '
                   '"Cloud!!" box before reusing its window (%s) -- no empty '
                   'box with Echo-S Auto Text Advance off. %s=1 disables.'
                   % (', '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! cargo wait %s: %s' % (name, why))
    return '\n'.join(out)
