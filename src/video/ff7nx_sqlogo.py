"""
ff7nx_sqlogo.py -- the logo movie at startup (BUILD 640).

THE RULE (Patrick, 10-11)
=========================
  Echo-S and any SYW mod  -> Echo-S's _sqlogo.mp4
  Echo-S, no SYW mod      -> Echo-S's _eidoslogo.mp4
  no Echo-S               -> no logo movie (the Switch's own PNG logos only)

THE GAME
========
The opening module plays from its logo table at 0x99C760: entry 0
"eidoslogo.avi" (0x99C990, a 16-byte slot), entry 1 "sqlogo.avi" (0x99C9A0, a
12-byte slot). At boot the mode byte puts it in state 7 (0x409581: [0xCC0D84]
!= 0x1A), so it plays entry 0 (0x7A78A4), waits, then entry 1 (0x7A74E2), then
goes to the title; returning from the attract demo replays entry 1 only
(0x7A7464 -> 0x7A7026). Nothing else reads the table or the strings.

So the picked movie goes in entry 0 and entry 1 names a movie the player
still refuses: the logo plays once, at boot (BUILD 640c -- 640b pointed both
entries at it and it played twice).
  _sqlogo:    "_sqlogo.avi" into sqlogo's slot; entry 0 -> it, entry 1 ->
              "eidoslogo.avi" (its refusal stays)
  _eidoslogo: "_eidoslogo.avi" into eidoslogo's slot; entry 0 -> it, entry 1
              stays "sqlogo.avi" (its refusal stays)

THE SWITCH
==========
The native movie player (ARM 0x10F11E0..) turns the path into
`.../movies/<name>.mp4`, then refuses any path containing "sqlogo"
(strstr, cbnz at 0x10F1338) or "eidoslogo" (cbz at 0x10F1350 -- it plays only
when the name is NOT found). Only the refusal that matches the chosen name is
lifted: the sqlogo cbnz becomes a nop, or the eidoslogo cbz becomes a plain
branch to the play path (0x10F1394).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os
import struct

import a64 as A
import ff7nx_audio_cave as AC

ECHO_S_MOD_ID = '09e81530-3f09-46b9-831b-df431b8f319c'

TABLE = 0x99C760                           # [0] eidoslogo, [1] sqlogo
SLOTS = {'eidoslogo': (0x99C990, 16), 'sqlogo': (0x99C9A0, 12)}
STOCK = {0x99C990: b'eidoslogo.avi\0', 0x99C9A0: b'sqlogo.avi\0'}

SQ_SITE, SQ_ORIG = 0x10F1338, 0xB50000E0   # cbnz x0, 0x10F1354 (found "sqlogo" -> refuse)
EI_SITE, EI_ORIG = 0x10F1350, 0xB4000220   # cbz x0, 0x10F1394 (no "eidoslogo" -> play)
PLAY = 0x10F1394
NOP = 0xD503201F
STRSTR_CALLS = {0x10F1334: 0x94017FA7, 0x10F134C: 0x94017FA1}


def choose(mods):
    """'_sqlogo', '_eidoslogo' or None from the enabled mods (Patrick's rule)."""
    echo = any(getattr(getattr(m, 'manifest', None), 'mod_id', '').lower() == ECHO_S_MOD_ID
               or 'echo-s' in getattr(m, 'filename', '').lower() for m in mods)
    if not echo:
        return None
    syw = any('syw' in getattr(m, 'filename', '').lower() for m in mods)
    return '_sqlogo' if syw else '_eidoslogo'


def _slot_for(movie):
    return SLOTS['sqlogo' if movie == '_sqlogo' else 'eidoslogo']


def table_for(movie):
    """(entry 0, entry 1) string addresses for the picked movie."""
    if movie == '_sqlogo':
        return 0x99C9A0, 0x99C990
    return 0x99C990, 0x99C9A0


def patch_exe(data, va_to_offset, movie):
    """ff7_en with entry 0 naming `movie` and entry 1 a refused logo."""
    at, size = _slot_for(movie)
    name = movie.encode() + b'.avi\0'
    assert len(name) <= size
    out = bytearray(data)
    off = va_to_offset(at)
    cur = bytes(out[off:off + size])
    if cur != name.ljust(size, b'\0'):
        if cur != STOCK[at].ljust(size, b'\0'):
            raise ValueError('ff7_en %#x holds %r, not the stock logo name' % (at, cur))
        out[off:off + size] = name.ljust(size, b'\0')
    for k, ptr_new in enumerate(table_for(movie)):
        o = va_to_offset(TABLE + 4 * k)
        ptr = struct.unpack_from('<I', out, o)[0]
        if ptr not in (0x99C990, 0x99C9A0):
            raise ValueError('ff7_en logo table [%d] = %#x' % (k, ptr))
        struct.pack_into('<I', out, o, ptr_new)
    return bytes(out)


def apply_to_nso(src, dest, movie):
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    for va, word in STRSTR_CALLS.items():
        AC.expect_word(text, va, word, 'movie player strstr')
    if movie == '_sqlogo':
        site, orig, new = SQ_SITE, SQ_ORIG, NOP
    else:
        site, orig, new = EI_SITE, EI_ORIG, A.b(EI_SITE, PLAY)
    cur = struct.unpack_from('<I', text, site)[0]
    if cur not in (orig, new):
        raise ValueError('movie player +0x%X holds %08X, not the stock test' % (site, cur))
    struct.pack_into('<I', text, site, new)
    raw[0] = bytes(text)
    packed = AC.pack(blob, raw, 0)
    check_segs, _ = AC.segments(packed)
    assert check_segs[0][2] == segs[0][2]
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(packed)
    return {'main': dest, 'words': 1}
