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

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
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


# BUILD 845: THE TUTORIAL MOVIE FILLS THE SCREEN -- THIS MOVIE ONLY.
#
# The tutorial is 4:3 (1280x960); every other FMV is 10:7. During a field FMV
# two of our passes frame the picture so it meets the field art:
# ff7nx_moviealign moves the movie quad down 16 game units, and
# ff7nx_moviebars paints black bars over the frame (sides, top 0..16, bottom
# 464..480) so models vanish under them at the FMV <-> field cuts. A 4:3
# movie is 480 units tall, so it is pushed down and its bottom is painted
# over. For the tutorial both passes now stand aside -- no shift, side bars
# only -- and the picture spans the full height, as in FFNx.
#
# "The tutorial" is: the current field is mds7pb_1 (where this module's
# redirect plays it) AND the playing movie is 4:3 or taller (h*4 >= w*3),
# read from the port's movie renderer [[0x12CE280]] +0x1C / +0x20 -- the
# same two words +0x5C90 hands to the quad. Every 10:7 FMV fails the size
# test; every other field fails the field test.
MOVIE_RENDERER_GOT = 0x12CE280
RENDERER_W, RENDERER_H = 0x1C, 0x20
FULLVIEW_ENV = 'SEVENTH_NX_TUTORIAL_FULLVIEW'


def fullview_enabled():
    return os.environ.get(FULLVIEW_ENV, '').strip().lower() not in (
        '0', 'off', 'no', 'false')


def tutorial_test(addr, base, miss):
    """Words that branch to `miss` unless the tutorial movie is playing.

    `addr(i)` is the real address of word i (ff7nx_cave layouts), `base` the
    index of the first word emitted here. Clobbers x0-x18 and x30 (the guest
    translator), nothing else. No `b`: every branch is conditional, so the
    callers' cave walkers (which treat a `b` as a link) are not disturbed.
    """
    w = []

    def at():
        return addr(base + len(w))
    w += A.movz_movk(0, CURRENT_FIELD_ID)
    w.append(A.bl(at(), RESOLVE_PAGED))
    w.append(A.ldrh(9, 0, 0))
    w.append(A.cmp_imm(9, MDS7PB_1_FIELD_ID))
    w.append(A.bcond(at(), miss, A.NE))
    w.append(A.adrp(10, at(), MOVIE_RENDERER_GOT & ~0xFFF))
    w.append(A.ldr64(10, 10, MOVIE_RENDERER_GOT & 0xFFF))
    w.append(A.cbz64(10, at(), miss))
    w.append(A.ldr64(10, 10, 0))
    w.append(A.cbz64(10, at(), miss))
    w.append(A.ldr(11, 10, RENDERER_W))
    w.append(A.ldr(12, 10, RENDERER_H))
    w.append(A.cbz(11, at(), miss))
    w.append(A.add_reg_lsl(11, 11, 11, 1))       # w * 3
    w.append(A.lsl(12, 12, 2))                   # h * 4
    w.append(A.cmp_reg(12, 11))
    w.append(A.bcond(at(), miss, A.LT))
    return w


TUTORIAL_TEST_WORDS = 18


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
