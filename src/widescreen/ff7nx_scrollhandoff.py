#!/usr/bin/env python3
"""ff7nx_scrollhandoff.py -- the last scripted pan of a scene ends where the
player camera picks up, so control is handed back without a jump.

BUILD 671. mds7, first arrival (hardware, 10-14): the opening pan is three
SCR2DL moves; the script then gives the camera back with SCRCC:

    produce:  SCR2D  (104, 136)
              SCR2DL (5, 50)    200 ; SCRLW
              SCR2DL (-56, 59)  100 ; SCRLW
              SCR2DL (-96, 92)   80 ; SCRLW
              SCRCC                       <- the camera follows Cloud again

The third pan stops at y = 92. SCRCC (x86 0x61A7A9) sets mode 0, i.e. the
plain follow camera (0x644075, flag 0xCC15E4 = 0), which puts the camera on
Cloud: y = 98. The picture jumps 6 units (18 px at 720p) on one frame --
measured in the video: the pan moves 0.6 px a frame and ends, then one 18.0
px step in the same direction. It is in the vanilla script too (PC jumps
the same way; FFNx does not touch SCRCC).

THE EDIT (script data only, this field only). The last pan is aimed at the
player instead of at a fixed point:

    SCR2DL b1 b2 x x y y s s      (9 bytes)
 -> SCRLC  00 s s 02  NOP NOP NOP NOP   (5 + 4 = 9 bytes)

SCRLC (x86 0x61A58A) scrolls to the CURRENT PLAYABLE CHARACTER -- the same
model SCRCC hands the camera to (both read global +0x2A) -- with type 2 =
mode 2, linear, the same easing as SCR2DL. Mode 2 (0x643D22) aims at
0x643C86 + the camera-range clip 0x6438F6: the very projection and clip the
follow camera uses (mds7 has no rail, field_14 = 0, and camera pan 0, so the
follow path's 0x643628 and the scripted path's camdat pan are both no-ops).
SCRLW then turns mode 2 into mode 1 (scripted follow, same target) and SCRCC
into mode 0: the position is the same integer on every one of those frames,
so the hand-over cannot jump. Same speed (80 steps; the 60 FPS preset
doubles SCRLC like SCR2DL, 610), same start; x is clipped at the range edge
before and after, so only the vertical leg grows (33 -> 39 units over the
same 160 frames). ff7nx_subcam smooths mode 2 like the SCR2DL it replaces.

The length never changes, so no offset or branch moves: the four NOPs
(x86 0x6107EB, one frame each) run while the pan is already moving, before
SCRLW waits for it. Matched by shape, not by offset: in the named field, the
one constant-banked SCR2DL / SCR2DC that is followed directly by SCRLW and
SCRCC. Anything else (no match, two matches, banked operands) leaves the
field unchanged and says so.

SEVENTH_NX_SCROLL_HANDOFF=0 disables (an flevel input: VERSION and the switch
are in flevel.lgp's key).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os

VERSION = 671
ENV = 'SEVENTH_NX_SCROLL_HANDOFF'

OP_SCRLC, OP_SCR2DC, OP_SCRLW, OP_SCR2DL, OP_SCRCC, OP_NOP = (
    0x62, 0x66, 0x67, 0x68, 0x65, 0x5F)
PAN_TYPE = {OP_SCR2DL: 2, OP_SCR2DC: 3}          # linear -> mode 2, smooth -> 3

# field -> why. Only scenes checked on hardware/video are listed.
FIELDS = {
    'mds7': 'first arrival: the 3-pan opening ends 6 units short of Cloud',
}


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'false', 'no', 'off')


def _stream(script):
    import echo_s_flevel as ES
    out = []
    for a, b in ES._routine_blocks(script):
        st, _trunc = ES._decode_block(script, a, b)
        out.append(st)
    return out


def find(script):
    """[(offset, opcode)] of each constant pan directly followed by SCRLW,
    SCRCC."""
    hits = []
    for st in _stream(script):
        for i in range(len(st) - 2):
            off, op, size = st[i]
            if op not in PAN_TYPE or size != 9:
                continue
            if st[i + 1][1] != OP_SCRLW or st[i + 2][1] != OP_SCRCC:
                continue
            if script[off + 1] or script[off + 2]:
                continue                       # an operand from a variable
            hits.append((off, op))
    return sorted(set(hits))


def replacement(script, off):
    """The 9 bytes that aim the pan at `off` at the current character."""
    op = script[off]
    speed = script[off + 7:off + 9]
    return bytes([OP_SCRLC, 0x00]) + speed + bytes([PAN_TYPE[op]]) + \
        bytes([OP_NOP] * 4)


def apply_to_script(script):
    """(new_script or None, report)."""
    hits = find(script)
    if len(hits) != 1:
        return None, 'expected one pan before SCRLW/SCRCC, found %d' % len(hits)
    off, _op = hits[0]
    new = bytearray(script)
    old = bytes(new[off:off + 9])
    new[off:off + 9] = replacement(script, off)
    return bytes(new), '+%04X %s -> %s' % (off, old.hex(' '),
                                            bytes(new[off:off + 9]).hex(' '))


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    total = {'fields': [], 'refused': [], 'edits': []}
    if not enabled():
        return total
    encode = encode or archive.encode_field
    for name in sorted(FIELDS):
        entry = archive.index.get(name)
        if entry is None:
            total['refused'].append((name, 'not in flevel'))
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            new, report = apply_to_script(bytes(parts[0]))
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, 'unreadable: %s' % exc))
            continue
        if new is None:
            total['refused'].append((name, report))
            continue
        parts[0] = new
        payloads[name] = encode(lgp.join_sections(parts))
        total['fields'].append(name)
        total['edits'].append((name, report))
    return total


def summarise(st):
    lines = []
    if st.get('fields'):
        lines.append('  SCROLL HAND-OFF (BUILD 671): the last pan ends on the '
                     'player, no jump at SCRCC: %s. %s=0 disables.'
                     % ('; '.join('%s %s' % e for e in st['edits']), ENV))
    for name, why in st.get('refused', ()):
        lines.append('  ! scroll hand-off: %s unchanged (%s)' % (name, why))
    return '\n'.join(lines)
