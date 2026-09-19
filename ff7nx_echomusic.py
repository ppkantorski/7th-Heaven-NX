"""
ff7nx_echomusic.py -- the two FFNx music-compatibility patches Echo-S's field
scripts are authored against.

WHY THIS IS NOT OPTIONAL WHEN ECHO-S IS ON
==========================================
Echo-S ships complete PC field scripts and they are written for FFNx, which
applies two unconditional compatibility patches to the PC exe before any mod
runs. Both are about the music lock, and both change what a field script's
MUSIC/BMUSC opcodes actually do:

  1. `field_initialize_variables` clears a byte on every field load. Vanilla
     clears guest 0xCBF9C0; FFNx rewrites that address immediate to 0xCC195C,
     the music lock itself. Without it a field can finish loading with the
     lock still set, so the script's own MUSIC opcode is swallowed -- the
     field comes up silent and the next battle inherits whatever was playing.

  2. `field_music_helper + 0x106` (x86 0x613CA9) calls the legacy AKAO
     interpreter. FF7 only initialises the LOW BYTE of the command type
     there, so the upper three bytes are whatever the last caller left.
     FFNx masks them off, and additionally maps the stray 0xDA command onto
     the ordinary 0xF0 stop.

Neither is a voice feature and neither has anything to do with the Echo-S
runtime, which is exactly why they live in their own file: `build.py` installs
them the moment Echo-S's field scripts are merged into flevel, whether or not
any voice layer is present. A build that takes Echo-S's scripts without these
is the "towns have no music" bug, and it looks like a field-art problem.

WHAT THIS DOES NOT DO
=====================
No BSS. No table. No audio path of its own. Patch 1 resolves one guest
address through the game's own `ResolvePaged` and stores a zero byte through
it; patch 2 is a call-site wrapper that sanitises W0 and then branches into
the untouched stock AKAO interpreter. Both are two-digit numbers of bytes out
of the padding pool and both fail closed: the site must still hold the exact
word this was written against or nothing is written at all.

COLLISION STATUS (measured, not assumed)
========================================
Both sites were read out of the real shipping module -- the one this project
builds with the 60 FPS preset, widescreen, analog-360 and all five Cosmo
Memory audio bridges already installed -- and both still hold their stock
word:

    0x93AED8   510072A0   sub w0, w21, #0x1c      (the lock-clear site)
    0x95E1B4   9413C817   bl  0xE50210            (the AKAO call site)

So neither contends with anything this project ships. `test_echomusic.py`
asserts that against the dump rather than trusting this comment.
"""
import os
import struct

import a64 as A
import ff7nx_cave
import ff7nx_audio_cave as AC
import nxmap


# ARM64 translation of the original x86 store at guest 0x63C060:
#   mov byte ptr [0xCBF9C0], 0
# FFNx's unconditional compatibility patch rewrites its address immediate to
# 0xCC195C. ResolvePaged is the game's regular guest-address translator, the
# same one the Cosmo bridges and the Echo-S runtime read guest state through.
LOCK_SITE = 0x93AED8
LOCK_SITE_ORIGINAL = 0x510072A0            # sub w0, w21, #0x1c
LOCK_CONTINUE = 0x93AEE4
RESOLVE_PAGED = 0x10FC3A0
FFNX_MUSIC_LOCK_GUEST = 0xCC195C
STORE_ZERO_BYTE = 0x3900001F               # strb wzr, [x0]

# ARM64 counterpart of FFNx's other unconditional music patch. The original
# branch target is the translated AKAO interpreter, which this wrapper calls
# rather than replaces.
FIELD_SOUND_CALL = 0x95E1B4
FIELD_SOUND_TARGET = 0xE50210
FIELD_SOUND_CALL_ORIGINAL = A.bl(FIELD_SOUND_CALL, FIELD_SOUND_TARGET)
RET = 0xD65F03C0


def _build_lock_clear_cave(_entry, address):
    """Resolve FFNx's lock global, clear it, then continue translated code."""
    return [
        A.movz(0, FFNX_MUSIC_LOCK_GUEST & 0xFFFF),
        A.movk_hi(0, FFNX_MUSIC_LOCK_GUEST >> 16),
        A.bl(address(2), RESOLVE_PAGED),
        STORE_ZERO_BYTE,
        A.b(address(4), LOCK_CONTINUE),
    ]


def _build_field_sound_cave(cave, address):
    """Exact FFNx command-type sanitisation before the stock AKAO call."""
    a = AC.Asm(cave, address)
    # The hook site is itself a BL, so X30 already points at
    # field_music_helper + 4. Save it across the nested BL or the stock
    # interpreter's RET would come back into this cave forever.
    a.emit(A.stp64_pre(29, 30, 31, -0x10))
    a.emit(A.and_mask(0, 0, 8))            # FFNx: type &= 0xFF
    # FFNx also treats the stray 0xDA command as a normal music stop.
    a.emit(A.cmp_imm(0, 0xDA))
    a.bcond('call', A.NE)
    a.emit(A.movz(0, 0xF0))
    a.label('call')
    a.emit(A.bl(a.pc(), FIELD_SOUND_TARGET))
    a.emit(A.ldp64_post(29, 30, 31, 0x10))
    a.emit(RET)
    return a.resolve()


def _install(src, dest, site, original, what, builder, link):
    """Shared body: verify the site, lay out one cave, repack, re-verify."""
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    AC.expect_word(text, site, original, what)

    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))
    entry, placed = ff7nx_cave.emit_laid_out(pool, builder)
    placed[site] = link(site, entry)
    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, 0)

    # Decode the result again. This proves the cave changed no segment
    # geometry and no BSS -- which is what makes the pass composable after
    # every other module patch -- and that the one replaced game instruction
    # really points at the cave.
    check_segs, check_raw = AC.segments(out)
    assert check_segs[0][2] == segs[0][2]
    assert struct.unpack_from('<I', check_raw[0], site)[0] == link(site, entry)
    assert struct.unpack_from('<I', out, 0x3C)[0] == \
        struct.unpack_from('<I', blob, 0x3C)[0]

    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'site': site, 'cave_entry': entry, 'cave_words': len(placed) - 1}


def apply_to_nso(src, dest):
    """
    Install both FFNx music-compatibility patches, `src` -> `dest`.

    Returns a report, or raises if either site no longer holds its stock
    word. Both are installed together on purpose: Echo-S's scripts need the
    pair, and half of it is a subtler failure than none of it.
    """
    tmp = dest + '.echomusic-lock'
    lock = _install(src, tmp, LOCK_SITE, LOCK_SITE_ORIGINAL,
                    'Echo-S field music-lock clear', _build_lock_clear_cave,
                    A.b)
    try:
        sound = _install(tmp, dest, FIELD_SOUND_CALL, FIELD_SOUND_CALL_ORIGINAL,
                         'Echo-S field music-command sanitiser',
                         _build_field_sound_cave, A.bl)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {
        'main': dest,
        'lock': lock,
        'sound': sound,
        'guest_lock': FFNX_MUSIC_LOCK_GUEST,
        'bss_bytes': 0,
        'table_bytes': 0,
    }
