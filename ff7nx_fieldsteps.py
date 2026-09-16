#!/usr/bin/env python3
"""
ff7nx_fieldsteps.py -- player footsteps on FIELD maps, which FFNx deliberately
does NOT route by terrain material the way the world map is.

THE BOUNDARY, KEPT INTACT
=========================
FFNx wraps the one player-only call to `field_update_single_model_position`
and, when that call reports the player moved, plays the stock logical SFX 159
-- every 0.5 seconds, or 0.3 while the run input is held. There is no walkmap
material on this path and no per-terrain family. World-map terrain routing is
a separate feature and lives in `ff7nx_worldsteps`.

This port keeps that separation rather than inventing a field terrain system
FFNx does not implement. What it ADDS is Cosmo's own per-field and
per-triangle overrides, which FFNx expresses as keys of the form

    <field>_<triangle>_159      e.g. fship_4_16_159
    <field>_159                 e.g. fship_1_159

and resolves in that order, falling back to the plain numeric SFX 159 row.
Note `fship_1` is a FIELD NAME, not "field fship, triangle 1" -- the names are
matched against real flevel filenames, which is the only way to tell those two
readings apart.

WHERE THE ROUTE TABLE LIVES, AND WHY NOT IN THE MODULE
======================================================
664 field fallbacks and 2,040 triangle overrides collapse to 148 encoded
variants. Putting an index for that in the module was never possible: there
are about 1,835 contiguous bytes for every feature combined (see
`ff7nx_tables`).

So it does not go in the module. A small `CMFS` trailer is appended after each
affected field's nine normal sections, carrying `(payload-base, count,
cursor)` records indexed directly by the same triangle value FFNx uses --
`word_CC16E8 + 136 * field_model_id`. `ff7nx_fieldsteps_data` builds those
trailers; this file is only the runtime that reads them.

Two properties fall out of that placement and both matter:

  * the trailer is in the field's DECOMPRESSED BUFFER, which is writable, so
    every triangle gets its own independent sequential cursor without
    thousands of module-BSS counters;
  * stock fields already carry an ignored trailing `FINAL FANTASY7` marker,
    so the reader recognises that first and steps over it before looking for
    its own header. A field with neither falls through to stock SFX 159.

`[...] = [0]` in Cosmo's config means deliberately silent, and is encoded as
the reserved payload base 0xFF rather than as "no record".

THE TWO HOOKS, AND THE FIVE REVISIONS IT TOOK
=============================================
    inner   ARM 0x9D81F0, right after the player movement call returns.
            It reads the returned guest EAX so NPC movement, standing still
            and blocked movement cannot trigger a step. It records a one-bit
            pending flag and DOES NOT call audio here -- that value is still
            the inner function's return to its translated caller.
    outer   ARM 0x948E4C, immediately after the enclosing
            `field_update_models_positions` call returns. This consumes the
            pending step and makes the audio call.

The history, because every entry rules something out:

    r1/r2   nested the audio call before the inner movement result had been
            consumed, corrupting the live guest EAX
    r3      deferred that, but still called in the same outer tick and
            restored only EAX/ECX/EDX -- it never established that the rest
            of the live guest frame survived
    r4      corrected the call contract; its cache-owning `play_sfx` wrapper
            still aborted on the first step
    r5      switched to the four-word direct-player ABI the world bridge
            proved, and snapshotted the recompiler's condition metadata
    r6 PASS the real cause was at the INNER hook all along: the preceding
            stock `ldp` leaves host W8 and W9 live and its continuation
            consumes them to rebuild guest ESP. Every cave r1..r5 reused W8
            without restoring the pair. r6 snapshots and restores both before
            replaying the displaced load, which makes the observation hook
            transparent to its caller.

r6 passed the full field, transition, battle-return and world-map regression,
and is what this file emits.

IT SHARES THE WORLD BRIDGE'S BSS -- DELIBERATELY, AND WITH A GUARD
==================================================================
Field and world code never execute in the same mode, so this bridge reuses the
nine BSS dwords `ff7nx_worldsteps` already owns: cursor 0 becomes the one-bit
pending flag and the cadence word becomes the field countdown. That avoids a
second module-BSS extension, which was the one operation common to the three
failing field overlays.

The hazard that creates is real and is why `apply_to_nso` takes the address
explicitly. The original implementation re-derived it as
`page_align(data_end) + bssSize - 36`, which is only correct while the world
bridge is the LAST pass to have grown BSS. Install the sequential-SFX bridge
in between -- as the normal build does, since it adds 232 bytes of its own --
and that arithmetic silently lands inside the shuffle's cursors instead. So
the caller passes `world_scratch` from the world pass's own report, and this
refuses to guess.
"""
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_cave
import nxmap
from ff7nx_audio_cave import Asm

# Recompiled FF7 1.0.3. x86 0x634B82 is the only player movement call: the
# preceding x86 block compares the loop model against field_player_model_id.
# At +8, guest EAX holds field_update_single_model_position's result;
# `ldr w27, [x25]` is a position-independent, one-word hook target.
FIELD_PLAYER_RESULT_HOOK = 0x9D81F0
FIELD_PLAYER_RESULT_ORIG = 0xB940033B      # ldr w27, [x25]
FIELD_PLAYER_RESULT_RESUME = FIELD_PLAYER_RESULT_HOOK + 4

# x86 0x63C75C calls field_update_models_positions. This is the translated
# instruction immediately after that call returned but before its caller pops
# the one original cdecl argument -- the safe point for a separate SFX call,
# because the player movement return value has already been consumed.
FIELD_UPDATE_RETURN_HOOK = 0x948E4C
FIELD_UPDATE_RETURN_ORIG = 0xB9401328      # ldr w8, [x25, #0x10]
FIELD_UPDATE_RETURN_RESUME = FIELD_UPDATE_RETURN_HOOK + 4

# x86 0x745160, `play_sfx_on_channel(pan, sound_id, channel)` -- the lower,
# channel-owned one-shot player. The world bridge already calls it through its
# exact four-word translated-call layout; use the same entry rather than
# re-entering play_sfx's cache-owning wrapper from inside a field update.
DIRECT_PLAYER = 0xEF2AB0

FOOTSTEP_SFX_ID = 159
INPUT_RUN_GUEST = 0xD011C8                 # FFNx field input_run_button_status

# FFNx's exact field-layer key is the field model's current walkmesh value,
# `word_CC16E8 + 136 * modules_global_object->field_model_id`. The trailer sits
# immediately after section 9; the header's ninth section pointer at +0x26
# gives that location without a global name table.
FIELD_LEVEL_PTR_GUEST = 0xCFF594
FIELD_MODEL_ID_GUEST = 0xCC0DB2
CURRENT_TRIANGLE_GUEST = 0xCC16E8
LAST_SECTION_PTR_OFF = 6 + 8 * 4
CMFS_MAGIC = 0x53464D43                 # little-endian b'CMFS'
STOCK_TAIL_MAGIC = 0x414E4946           # little-endian b'FINA'
STOCK_TAIL_BYTES = 14
CMFS_HEADER_BYTES = 9                   # magic, u16 span, fallback record
CMFS_FALLBACK_OFF = 6
CMFS_SILENT_BASE = 0xFF

# The same physical descriptor/cache identity the world terrain bridge proved.
# Field and world modes are mutually exclusive, so sharing the row is safe.
PHYSICAL_SLOT = AC.FOOTSTEP_SLOT
PHYSICAL_META_GUEST = AC.meta_guest(PHYSICAL_SLOT)
PHYSICAL_CACHE_GUEST = AC.cache_guest(PHYSICAL_SLOT)
CHANNEL_STATE_GUEST = 0xDE0CE0 + (5 - 1) * 0x54

# FFNx plays a field footstep every 0.5 s, or 0.3 s while the run input is
# held.
#
# THESE ARE SECONDS, AND THE TICK RATE IS NOT A CONSTANT -- the same trap as
# `ff7nx_worldsteps`, and the one that actually reached hardware. The outer
# hook fires once per pass through the field loop, so the tick count that
# makes 0.5 s depends on what the FIELD frame limiter is set to.
#
# Stock is 30 Hz, which is where 15 and 9 come from. The 60 FPS group rewrites
# the field limiter divisor at x86 0x7B7840 from 30.0 to 60.0, and
# `ff7nx_60fps` says so in as many words: "ff7_en's field limiter divisor went
# 30 -> 60, so field_loop runs twice per original frame". `limiter_fps` can
# raise it further, and the shipping setting is 66.
#
# At 66 Hz a 15-tick walk cooldown is 0.227 s rather than 0.50 -- FASTER than
# the 0.30 s FFNx uses for RUNNING. That is exactly what it sounds like: a walk
# that paces like a sprint and does not line up with the step animation.
#
# The bridge was developed against a build with no 60 FPS group, where 30 Hz
# held, which is why this only appeared once it met a real settings profile.
STOCK_TICK_HZ = 30.0
WALK_SECONDS = 0.50
RUN_SECONDS = 0.30
WALK_COOLDOWN = 15                     # the stock-rate values, for reference
RUN_COOLDOWN = 9


def cooldowns(tick_hz=STOCK_TICK_HZ):
    """
    (walk, run) tick counts for a field loop running at `tick_hz`.

    Clamped to at least 1: a zero countdown would request a footstep on every
    frame, which is a buffer per frame through the stock channel player.
    """
    if not tick_hz or tick_hz <= 0:
        tick_hz = STOCK_TICK_HZ
    return (max(1, int(round(WALK_SECONDS * tick_hz))),
            max(1, int(round(RUN_SECONDS * tick_hz))))

# Offsets into the world bridge's nine dwords -- see the module docstring.
WORLD_SCRATCH_BYTES = 9 * 4
PENDING_OFF = 0
COOLDOWN_OFF = 8 * 4
SCRATCH_BYTES = 0                          # this bridge grows BSS by nothing
WORLD_MOVE_HOOK = 0xF45A7C

# The outer hook calls a translated SFX routine where the original field loop
# had no call.  Its complete guest register/condition frame must survive, and
# the CMFS selector uses x21..x24 while choosing a route.  Keep the guest
# snapshot in distinct stack slots, not in those working registers: r6 used
# x21/x22 for both EAX/ECX and the condition-pair save, silently restoring
# the latter over the former on every field step.
PLAY_FRAME = 0xC0
SAVE_EAX = 0x70
SAVE_ECX = 0x74
SAVE_EDX = 0x78
SAVE_EBX = 0x7C
SAVE_ESP = 0x80
SAVE_EBP = 0x84
SAVE_ESI = 0x88
SAVE_EDI = 0x8C
SAVE_COND0 = 0x90
SAVE_COND1 = 0x98


def field_terrain_select(a, payload_base, stride):
    """Set W22 to stock 159, physical 744, or branch to ``out`` for silence.

    The trailer table lives in the current field's decompressed buffer.  It
    is indexed directly by the same triangle number FFNx puts in a
    ``<field>_<triangle>_159`` key.  The selected record's cursor byte is
    mutable field memory, giving independent sequential state per route
    without allocating thousands of executable BSS counters.
    """
    # guest ``*field_level_data_pointer``
    AC.guest_ptr(a, FIELD_LEVEL_PTR_GUEST)
    a.emit(A.ldr(21, 0, 0))
    a.emit(A.cmp_imm(21, 0))
    a.bcond('terrain_stock', A.EQ)

    # The ninth section's (offset,length) identifies the ignored tail. All
    # arithmetic remains guest-side until the final pointer is translated.
    a.emit(A.add_imm(0, 21, LAST_SECTION_PTR_OFF))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldr(22, 0, 0))              # section-9 offset
    a.emit(A.add_reg(0, 21, 22))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldr(23, 0, 0))              # section-9 length
    a.emit(A.add_reg(21, 21, 22))
    a.emit(A.add_imm(21, 21, 4))
    a.emit(A.add_reg(21, 21, 23))        # guest CMFS header
    a.emit(A.mov_reg(0, 21))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldr(22, 0, 0))
    AC.mov32(a, 23, CMFS_MAGIC)
    a.emit(A.cmp_reg(22, 23))
    a.bcond('terrain_header', A.EQ)
    AC.mov32(a, 23, STOCK_TAIL_MAGIC)
    a.emit(A.cmp_reg(22, 23))
    a.bcond('terrain_stock', A.NE)
    a.emit(A.add_imm(21, 21, STOCK_TAIL_BYTES))
    a.emit(A.mov_reg(0, 21))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldr(22, 0, 0))
    AC.mov32(a, 23, CMFS_MAGIC)
    a.emit(A.cmp_reg(22, 23))
    a.bcond('terrain_stock', A.NE)
    a.label('terrain_header')
    a.emit(A.ldrh(23, 0, 4))             # triangle span

    # FFNx's source uses this exact model-id/136-byte calculation, rather
    # than the tempting but different module-global destination triangle.
    AC.guest_ptr(a, FIELD_MODEL_ID_GUEST)
    a.emit(A.ldrh(22, 0, 0))
    a.emit(A.add_reg_lsl(24, 22, 22, 4)) # model * 17
    a.emit(A.lsl(24, 24, 3))             # model * 136
    AC.mov32(a, 0, CURRENT_TRIANGLE_GUEST)
    a.emit(A.add_reg(0, 0, 24))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrsh(24, 0, 0))
    a.emit(A.cmp_reg(24, 23))
    a.bcond('terrain_fallback', A.HS)    # also handles a negative triangle

    # record = header + 9 + triangle * 3, with each final address translated
    # independently so a field map never assumes host-contiguous guest pages.
    a.emit(A.add_reg_lsl(24, 24, 24, 1))
    a.emit(A.add_reg(0, 21, 24))
    a.emit(A.add_imm(0, 0, CMFS_HEADER_BYTES))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrb(22, 0, 0))             # payload base
    a.emit(A.ldrb(23, 0, 1))             # sequence count
    a.emit(A.ldrb(24, 0, 2))             # mutable cursor
    a.emit(A.cmp_imm(22, CMFS_SILENT_BASE))
    a.bcond('out', A.EQ)
    a.emit(A.cmp_imm(23, 0))
    a.bcond('terrain_fallback', A.EQ)
    a.b('terrain_choose')

    a.label('terrain_fallback')
    a.emit(A.add_imm(0, 21, CMFS_FALLBACK_OFF))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrb(22, 0, 0))
    a.emit(A.ldrb(23, 0, 1))
    a.emit(A.ldrb(24, 0, 2))
    a.emit(A.cmp_imm(22, CMFS_SILENT_BASE))
    a.bcond('out', A.EQ)
    a.emit(A.cmp_imm(23, 0))
    a.bcond('terrain_stock', A.EQ)

    a.label('terrain_choose')
    a.emit(A.cmp_reg(24, 23))
    a.bcond('terrain_cursor_ok', A.LT)
    a.emit(A.movz(24, 0))
    a.label('terrain_cursor_ok')
    a.emit(A.add_reg(22, 22, 24))        # flat padded-payload index
    a.emit(A.add_imm(24, 24, 1))
    a.emit(A.cmp_reg(24, 23))
    a.bcond('terrain_cursor_save', A.LT)
    a.emit(A.movz(24, 0))
    a.label('terrain_cursor_save')
    a.emit(A.strb(24, 0, 2))

    # Rewrite physical slot 744's descriptor for this one call, then clear
    # both cache identities exactly as the successful world route does.
    AC.mov32(a, 24, stride)
    AC.mov32(a, 23, payload_base)
    a.emit(A.mul(22, 22, 24))
    a.emit(A.add_reg(23, 23, 22))
    AC.guest_ptr(a, PHYSICAL_META_GUEST)
    a.emit(A.str_(24, 0, 0))
    a.emit(A.str_(23, 0, 4))
    AC.guest_ptr(a, PHYSICAL_CACHE_GUEST)
    a.emit(A.str_(A.WZR, 0, 0))
    AC.guest_ptr(a, CHANNEL_STATE_GUEST)
    a.emit(A.str_(A.WZR, 0, 0x40))
    a.emit(A.movz(22, PHYSICAL_SLOT))
    a.b('terrain_done')

    a.label('terrain_stock')
    a.emit(A.movz(22, FOOTSTEP_SFX_ID))
    a.label('terrain_done')


def build_flag_cave(cave, address, scratch):
    """Record the player's result without calling another guest function."""
    a = Asm(cave, address)
    AC.save_host(a)
    # This hook follows `ldp w8, w9, [guest + ESP]`.  Unlike a normal call
    # boundary, the next stock instructions still consume both host values to
    # rebuild the guest stack.  The cave's movement test borrows W8, so save
    # and restore the live pair in the spare tail of the native frame before
    # returning to `add w8, w8, #4` / `sub w0, w9, #4`.
    a.emit(A.str64(8, A.SP, 0x60))
    a.emit(A.str64(9, A.SP, 0x68))
    # The displaced instruction loads this guest EAX into W27.  It must remain
    # untouched: the enclosing translated routine writes it to `[ebp-4]` just
    # after this hook. r1/r2 incorrectly nested an SFX guest call here, whose
    # own return value replaced this movement result.
    a.emit(A.ldr(8, 25, 0))
    a.emit(A.cmp_imm(8, 0))
    a.bcond('out', A.EQ)
    AC.bss_ptr(a, 27, scratch)
    a.emit(A.movz(8, 1))
    a.emit(A.str_(8, 27, PENDING_OFF))

    a.label('out')
    a.emit(A.ldr64(8, A.SP, 0x60))
    a.emit(A.ldr64(9, A.SP, 0x68))
    AC.restore_host(a)
    a.emit(FIELD_PLAYER_RESULT_ORIG)
    a.emit(A.b(a.pc(), FIELD_PLAYER_RESULT_RESUME))
    return a.resolve()


def build_play_cave(cave, address, scratch, terrain=None,
                    tick_hz=STOCK_TICK_HZ):
    """
    Consume one recorded step after field_update_models_positions returns.

    `tick_hz` is the rate the field loop really runs at in this build -- see
    `cooldowns`. Only the two cadence immediates change with it; every other
    word is the hardware-proven r6 sequence.
    """
    walk_cd, run_cd = cooldowns(tick_hz)
    a = Asm(cave, address)
    AC.save_host(a, frame=PLAY_FRAME)

    AC.bss_ptr(a, 27, scratch)
    a.emit(A.ldr(8, 27, PENDING_OFF))
    a.emit(A.cmp_imm(8, 0))
    a.bcond('out', A.EQ)
    a.emit(A.str_(A.WZR, 27, PENDING_OFF))

    # Cadence applies after a genuine player movement result. The outer hook
    # runs at the same original 30 Hz field-update rate.
    a.emit(A.ldr(8, 27, COOLDOWN_OFF))
    a.emit(A.cmp_imm(8, 0))
    a.bcond('play', A.EQ)
    a.emit(A.sub_imm(8, 8, 1))
    a.emit(A.str_(8, 27, COOLDOWN_OFF))
    a.b('out')

    a.label('play')
    # FFNx picks 0.3s whenever input_run_button_status is nonzero; this is
    # the same guest value resolved by its ``get_absolute_value(... +0x55)``.
    AC.guest_ptr(a, INPUT_RUN_GUEST)
    a.emit(A.ldr(8, 0, 0))
    a.emit(A.cmp_imm(8, 0))
    a.bcond('running', A.NE)
    a.emit(A.movz(8, walk_cd))
    a.b('save_pace')
    a.label('running')
    a.emit(A.movz(8, run_cd))
    a.label('save_pace')
    AC.bss_ptr(a, 27, scratch)
    a.emit(A.str_(8, 27, COOLDOWN_OFF))

    # The cache-owning one-argument play_sfx wrapper is callable with a valid
    # stack frame, but r3/r4 show it is not safely re-entrant at this point in
    # the field update.  The proven world bridge instead enters the lower,
    # channel-owned player with the original 64/ID/5 cdecl ABI.  This creates
    # and releases the ordinary channel buffer without sharing a global cache
    # object with the field loop.
    #
    # A translated guest call writes its return and scratch values into the
    # shared guest context. We are inserting one where the original x86 had
    # none, so snapshot every live general register and both adjacent
    # condition words.  These stack slots cannot overlap the callee-saved
    # host frame or CMFS's x21..x24 scratch registers.
    for guest_off, save_off in ((0, SAVE_EAX), (4, SAVE_ECX), (8, SAVE_EDX),
                                (0xC, SAVE_EBX), (0x10, SAVE_ESP),
                                (0x14, SAVE_EBP), (0x18, SAVE_ESI),
                                (0x1C, SAVE_EDI)):
        a.emit(A.ldr(21, 25, guest_off))
        a.emit(A.str_(21, A.SP, save_off))
    a.emit(A.ldr64(21, 25, 0x20))
    a.emit(A.str64(21, A.SP, SAVE_COND0))
    a.emit(A.ldr64(21, 25, 0x28))
    a.emit(A.str64(21, A.SP, SAVE_COND1))

    # With no CMFS route this is the r6 hardware-proven SFX-159 call. A field
    # route rewrites physical slot 744 just before this exact same ABI.
    if terrain:
        field_terrain_select(a, terrain['payload_base'], terrain['stride'])
    else:
        a.emit(A.movz(22, FOOTSTEP_SFX_ID))

    # Exact layout used by the hardware-proven world bridge:
    # (virtual-return-unused, pan=64, id, channel=5).  The direct
    # player's translated epilogue consumes the first word; the caller
    # removes the three cdecl arguments afterwards.
    a.emit(A.ldr(19, A.SP, SAVE_ESP))
    a.emit(A.sub_imm(0, 19, 16))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.str_(A.WZR, 0, 0))
    a.emit(A.movz(8, 64))
    a.emit(A.str_(8, 0, 4))
    a.emit(A.str_(22, 0, 8))
    a.emit(A.movz(8, 5))
    a.emit(A.str_(8, 0, 12))
    a.emit(A.sub_imm(8, 19, 16))
    a.emit(A.str_(8, 25, 0x10))
    a.emit(A.bl(a.pc(), DIRECT_PLAYER))
    a.emit(A.ldr(8, 25, 0x10))
    a.emit(A.add_imm(8, 8, 12))
    a.emit(A.str_(8, 25, 0x10))
    # Restore the exact field-loop frame.  In particular, do not reuse
    # x21/x22 here: CMFS deliberately clobbers them while it selects a
    # trailer record, and the old cave restored those clobbered values as
    # guest EAX/ECX.
    for guest_off, save_off in ((0, SAVE_EAX), (4, SAVE_ECX), (8, SAVE_EDX),
                                (0xC, SAVE_EBX), (0x10, SAVE_ESP),
                                (0x14, SAVE_EBP), (0x18, SAVE_ESI),
                                (0x1C, SAVE_EDI)):
        a.emit(A.ldr(21, A.SP, save_off))
        a.emit(A.str_(21, 25, guest_off))
    a.emit(A.ldr64(21, A.SP, SAVE_COND0))
    a.emit(A.str64(21, 25, 0x20))
    a.emit(A.ldr64(21, A.SP, SAVE_COND1))
    a.emit(A.str64(21, 25, 0x28))

    a.label('out')
    AC.restore_host(a, frame=PLAY_FRAME)
    a.emit(FIELD_UPDATE_RETURN_ORIG)
    a.emit(A.b(a.pc(), FIELD_UPDATE_RETURN_RESUME))
    return a.resolve()



def apply_to_nso(src, dest, world_scratch, terrain=None, space=None,
                 tick_hz=STOCK_TICK_HZ):
    """
    Install the player-only field-footstep bridge.

    `world_scratch` is the module address `ff7nx_worldsteps.apply_to_nso`
    reported. It is REQUIRED rather than re-derived: see the module docstring
    for why subtracting from the current bssSize is only correct when nothing
    else has grown BSS since.
    """
    if not world_scratch:
        raise ValueError('field footsteps need the world bridge\'s scratch '
                         'address; pass the value its report returned')
    with open(src, 'rb') as handle:
        blob = handle.read()
    import ff7nx_tables
    segs, raw, own = ff7nx_tables.open_module(blob)
    space = own if space is None else space
    text = space.text
    AC.expect_word(text, FIELD_PLAYER_RESULT_HOOK, FIELD_PLAYER_RESULT_ORIG,
                   'field player movement')
    AC.expect_word(text, FIELD_UPDATE_RETURN_HOOK, FIELD_UPDATE_RETURN_ORIG,
                   'field update return')

    # The world bridge must already be installed: this shares its BSS block
    # and its physical archive row, and without it the cursors it reads are
    # whatever the module had there.
    world_word, = struct.unpack_from('<I', text, WORLD_MOVE_HOOK)
    if (world_word & 0xFC000000) != 0x14000000:
        raise ValueError('field footsteps require the world footstep bridge '
                         'to be installed first; its movement hook holds '
                         '%08X, which is not a branch' % world_word)
    old_bss, = struct.unpack_from('<I', blob, 0x3C)
    if old_bss < WORLD_SCRATCH_BYTES:
        raise ValueError('module BSS is too small to hold the world scratch')

    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))
    flag_entry, placed = ff7nx_cave.emit_laid_out(
        pool, lambda cave, addr: build_flag_cave(cave, addr, world_scratch))
    play_entry, play_placed = ff7nx_cave.emit_laid_out(
        pool, lambda cave, addr: build_play_cave(cave, addr, world_scratch,
                                                 terrain=terrain,
                                                 tick_hz=tick_hz))
    placed.update(play_placed)
    placed[FIELD_PLAYER_RESULT_HOOK] = A.b(FIELD_PLAYER_RESULT_HOOK, flag_entry)
    placed[FIELD_UPDATE_RETURN_HOOK] = A.b(FIELD_UPDATE_RETURN_HOOK, play_entry)
    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)

    out = AC.pack(blob, space.commit(), SCRATCH_BYTES)
    _check_segs, check_raw = AC.segments(out)
    for site, entry, what in ((FIELD_PLAYER_RESULT_HOOK, flag_entry, 'inner'),
                              (FIELD_UPDATE_RETURN_HOOK, play_entry, 'outer')):
        if struct.unpack_from('<I', check_raw[0], site)[0] != A.b(site, entry):
            raise ValueError('%s field hook did not survive the repack' % what)
    if struct.unpack_from('<I', out, 0x3C)[0] != old_bss + SCRATCH_BYTES:
        raise ValueError('field footsteps must not grow BSS')

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'main': dest, 'bss_bytes': SCRATCH_BYTES, 'scratch': world_scratch,
            'shared_world_scratch': True, 'terrain': bool(terrain),
            'flag_entry': flag_entry, 'play_entry': play_entry,
            'cave_words': len(placed) - 2, 'table_bytes': 0,
            'tick_hz': tick_hz, 'walk_cooldown': cooldowns(tick_hz)[0],
            'run_cooldown': cooldowns(tick_hz)[1]}
