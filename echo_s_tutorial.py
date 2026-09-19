"""Echo-S's field-scoped opening tutorial movie compatibility.

Echo-S changes the stock ``mds7pb_1`` button tutorial from ``TUTOR 3`` to
``PMVIE 20; MOVIE``. On PC, 7th Heaven supplies a temporary replacement for
movie slot 20 while that field is active. A LayeredFS build cannot make that
runtime selection: replacing ``mkup`` statically would also replace the real
materia tutorial later in the game.

The Switch PMVIE handler has already loaded the script's movie index at the
hook below. This tiny, field-gated bridge changes 20 to the unused slot 1
only for ``mds7pb_1``. The build routes Echo-S's authored MP4 as
``fship2n.mp4`` through the ordinary movie pipeline, so its container, frame
rate, and cache policy stay identical to every other movie in the output.
"""
from __future__ import annotations

import os
import struct

import a64 as A
import ff7nx_cave
import nxmap
import ff7nx_audio_cave as AC


# All native audio/voice bridges use this same NSO container implementation.
# Keeping the tutorial redirect here means it composes on the finished module
# without importing the old fork's developer-only probe harness.
Asm = AC.Asm
_pack = AC.pack
_segments = AC.segments


PMVIE_INDEX_HOOK = 0x9794B8
PMVIE_INDEX_ORIG = A.ldrb(8, 0, 0)
PMVIE_INDEX_RESUME = PMVIE_INDEX_HOOK + 4
RESOLVE_PAGED = 0x10FC3A0

CURRENT_FIELD_ID = 0xCC15D0
MDS7PB_1_FIELD_ID = 0x09A

REQUESTED_INDEX = 0x14
REDIRECT_INDEX = 0x01
REQUESTED_STEM = 'mkup'
REDIRECT_STEM = 'fship2n'


def _guest(a, address):
    """Materialise a 32-bit guest address in W0."""
    a.emit(A.movz(0, address & 0xFFFF))
    a.emit(A.movk_hi(0, address >> 16))


def _build_redirect_cave(_entry, addr):
    """Return the laid-out ``PMVIE 20`` -> ``1`` redirect cave."""
    a = Asm(_entry, addr)
    a.emit(PMVIE_INDEX_ORIG)
    a.emit(A.cmp_imm(8, REQUESTED_INDEX))
    a.bcond('resume', A.NE)
    a.emit(A.mov_reg(22, 8))
    _guest(a, CURRENT_FIELD_ID)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrh(9, 0, 0))
    a.emit(A.mov_reg(8, 22))
    a.emit(A.cmp_imm(9, MDS7PB_1_FIELD_ID))
    a.bcond('resume', A.NE)
    a.emit(A.movz(8, REDIRECT_INDEX))
    a.label('resume')
    a.emit(A.b(a.pc(), PMVIE_INDEX_RESUME))
    return a.resolve()


def _verified_hole_pool(src, text):
    module = nxmap.Main(src)
    image = bytearray(module.img)
    image[:len(text)] = text
    return ff7nx_cave.HolePool(image, starts=set(module.arm_starts))


def patch_main(src, dest):
    """Install the narrow field-scoped PMVIE movie redirect."""
    with open(src, 'rb') as handle:
        blob = handle.read()
    _segs, raw = _segments(blob)
    text = bytearray(raw[0])
    current, = struct.unpack_from('<I', text, PMVIE_INDEX_HOOK)
    if current != PMVIE_INDEX_ORIG:
        raise ValueError(
            'Echo-S tutorial hook +0x%X is %08X, expected %08X; input is '
            'not the compatible 1.0.3 main or another patch owns the site'
            % (PMVIE_INDEX_HOOK, current, PMVIE_INDEX_ORIG))

    pool = _verified_hole_pool(src, text)
    entry, placed = ff7nx_cave.emit_laid_out(pool, _build_redirect_cave)
    placed[PMVIE_INDEX_HOOK] = A.b(PMVIE_INDEX_HOOK, entry)
    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)
    raw[0] = bytes(text)
    out = _pack(blob, raw, 0)

    _check, check_raw = _segments(out)
    assert struct.unpack_from('<I', check_raw[0], PMVIE_INDEX_HOOK)[0] == \
        A.b(PMVIE_INDEX_HOOK, entry)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {
        'main': dest,
        'entry': entry,
        'words': len(placed) - 1,
        'requested': REQUESTED_STEM,
        'redirect': REDIRECT_STEM,
        'requested_index': REQUESTED_INDEX,
        'redirect_index': REDIRECT_INDEX,
        'field_id': MDS7PB_1_FIELD_ID,
        'field': 'mds7pb_1',
    }
