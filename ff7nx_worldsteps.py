#!/usr/bin/env python3
"""
ff7nx_worldsteps.py -- Cosmo Memory's terrain-aware world-map footsteps, on
the stock SFX channel manager.

WHAT FFNx DOES, AND WHAT THE PORT EXPOSES
=========================================
Cosmo's `WM/sfx/config.toml` is not a set of ordinary footstep replacements.
Its keys are

    wm_footsteps_<player model>_<walkmap type>_159

and FFNx reads the current world entity's walkmap type, then plays that key --
only from its own replacement for world movement. Ordinary fields are a
completely different path with no terrain material argument at all (see
`ff7nx_fieldsteps`).

The Switch recompile exposes the same three inputs without any guessed lookup:

    world entity pointer   guest 0xE3A7D0
    +0x50  byte            player model; FFNx permits 0, 1, 2 (Cloud, Tifa,
                           Cid), 4 and 19 (buggy / chocobo)
    +0x4A  word, & 0x1F    the exact FFNx walkmap type

and the recompiled `world_update_model_movement` entry still has the original
x86 `delta_x`, `delta_z` on its guest cdecl stack.

THE COLLISION RULE
==================
FFNx itself documents a long-standing limitation at this exact boundary: its
hook sees the *requested* delta before the native collision pass has clipped
it, so holding into a world-map wall can sound like walking.  The first Switch
bridge copied that predicate (`dx != 0 || dz != 0`) verbatim.  That made normal
walking audible at the port's small per-frame deltas, but it also copied the
false-positive at a wall.

This bridge keeps that requested-delta test as the cheap "is the player trying
to move?" gate, then compares the real player X/Z position recorded on the
previous call.  Collision runs after this function, so the next invocation
observes the already-clipped result.  It is deliberately one frame late, which
is inaudible at 66 Hz and is the only safe point available without inserting a
second hook after collision.  No actual position change means no footstep.

COLLAPSING THE MODEL DIMENSION -- CHECKED, NOT ASSUMED
=====================================================
The shipping Cosmo release declares 100 model/material combinations, but the
five supported models resolve to the same eight terrain sets: 68 OGG variants
over 20 walkmap materials. `prepare()` re-derives that equality from the
active config every build and REFUSES a future profile whose model-specific
sequences diverge, rather than silently playing Cloud's gravel for a chocobo.

WHY THE STOCK PLAYER AND ONE PHYSICAL SLOT
==========================================
`audio.fmt` stays at exactly 750 records. Slot 744 is empty in vanilla, so no
game route ever asks for it; reserving it gives this bridge a permanent cache
identity and a valid format template. The 68 payloads are padded to a common
MS-ADPCM block stride and appended after `audio.dat`'s ordinary contents, and
the cave computes `(length, offset)` into that tail. No synthetic id above
750, no new `audio.fmt` row, no native OGG player.

That last point is not stylistic. `NativeOggPlayer` works for one long-lived
ambient loop, but a one-shot pool freezes under traffic -- it has an
asynchronous renderer/collector lifetime and is not a substitute for the SFX
channel manager. Calling the lower `play_sfx_on_channel(0x40, 744, 5)` entry
rather than `play_sfx` is equally deliberate: its non-looping channel buffer is
released on the next step and never enters the global per-id cache.

THE CALL ABI, WHICH TOOK EIGHT ATTEMPTS
=======================================
A translated x86 call reserves FOUR guest words, not three. The complete stock
sequence at `main + 0xF33158..0xF33198` pushes the two movement arguments, then
reserves one additional UNWRITTEN return word before the host `BL`, which the
player consumes in its translated epilogue. So a direct call must lay out
`(return-unused, pan=64, id=744, channel=5)`. The three-word version shifted
every argument and returned ESP one word late.

The rest of the history, kept because each step ruled something out:

    r1          silent, no crash
    r2          used an uninitialised guest ESP -- visual instability, abort
    r3/r4       three-word stack layout; r4's repeated sound was NOT evidence
                that 5020 had been selected correctly
    r5  PASS    four-word layout; 5020 played on the world map and stopped
                cleanly on field entry
    r6  PASS    isolated the movement predicate: silent at rest, in step when
                walking. FFNx's `distance > 10` filter is too restrictive for
                this call cadence, so the bridge uses `dx != 0 || dz != 0`
                with a 0.30 s / 0.50 s cadence instead
    r7          crashed on the first step: used the 32-bit `ADD W27, W27, #..`
                form to advance a RELOCATED 64-BIT host BSS pointer, which
                zero-extended the module base away before the first cursor read
    r8  PASS    the same cave with those three increments as `ADD X27, ...`

r8 is what this file emits. The cave below is the r8 word sequence.
"""
import os
import struct

import a64 as A
import audio_dat
import ff7nx_audio_cave as AC
import ff7nx_cave
import nxmap
import sfxmod
from ff7nx_audio_cave import Asm

# Recompiled 1.0.3: immediately after x20 has been loaded with the guest
# register context, before the translated x86 prologue mutates guest ESP.
WORLD_MOVE_HOOK = 0xF45A7C
WORLD_MOVE_ORIG = 0x52935B13           # mov w19, #0x9AD8
WORLD_MOVE_RESUME = WORLD_MOVE_HOOK + 4
DIRECT_PLAYER = 0xEF2AB0               # x86 play_sfx_on_channel (0x745160)

GAME_MODE_GUEST = 0xCC0D89
WORLD_MODE = 3
WORLD_ENTITY_PTR_GUEST = 0xE3A7D0
TERRAIN_OFF = 0x4A
MODEL_OFF = 0x50
# `world_player_pos_E04918` in FFNx's ff7_data.h.  It is a direct guest
# vector4<int>, not another pointer: x/z are the first and third dwords.
WORLD_PLAYER_POS_GUEST = 0xE04918
PLAYER_POS_X_OFF = 0
PLAYER_POS_Z_OFF = 8

FOOTSTEP_LOGICAL_ID = 159
PHYSICAL_SLOT = AC.FOOTSTEP_SLOT       # 744, vanilla-empty
META_GUEST = AC.meta_guest(PHYSICAL_SLOT)
CACHE_GUEST = AC.cache_guest(PHYSICAL_SLOT)
# play_sfx_on_channel's per-channel state: base + (channel - 1) * 0x54.
# Its +0x40 logical-ID field is the player-level cache identity.
CHANNEL_STATE_GUEST = 0xDE0CE0 + (5 - 1) * 0x54

# FFNx gates human models at 0.30 s and buggy/chocobo at 0.50 s.
#
# THESE ARE SECONDS, AND THE TICK RATE IS NOT A CONSTANT. The cave counts
# calls to `world_update_model_movement`, which the world loop makes once per
# frame -- so the number of ticks in 0.30 s depends on what the world frame
# limiter is set to.
#
# Stock is 30 Hz, which is where 9 and 15 come from and what this bridge was
# originally written against. But the 60 FPS group rewrites the WORLD limiter
# divisor at x86 0x969958 from 30.0 to 60.0 (ff7nx_60fps.LIMITER_DIVISORS),
# and `limiter_fps` can raise it further -- the shipping setting is 66. At 66
# the loop runs 2.2x per original frame, so a 9-tick cooldown is 0.136 s
# instead of 0.30, and footsteps come more than twice as fast as FFNx intends.
#
# So the tick counts are DERIVED from the rate the build is actually going to
# ship, not baked in. `cooldowns()` is the one place that conversion happens.
STOCK_TICK_HZ = 30.0
HUMAN_SECONDS = 0.30
MOUNT_SECONDS = 0.50
HUMAN_COOLDOWN = 9                     # the stock-rate values, for reference
MOUNT_COOLDOWN = 15


def cooldowns(tick_hz=STOCK_TICK_HZ):
    """
    (human, mount) tick counts for a world loop running at `tick_hz`.

    Clamped to at least 1 so a misconfigured rate cannot produce a zero
    countdown, which would play a footstep on every single frame.
    """
    if not tick_hz or tick_hz <= 0:
        tick_hz = STOCK_TICK_HZ
    return (max(1, int(round(HUMAN_SECONDS * tick_hz))),
            max(1, int(round(MOUNT_SECONDS * tick_hz))))


# Eight group cursors, cadence, and the previous collision-resolved X/Z plus
# a valid bit.  Field footsteps deliberately share offsets 0 and 32 only, so
# growing this block leaves their pending/cadence contract intact.
GROUP_CURSOR_BYTES = 8 * 4
CADENCE_OFF = GROUP_CURSOR_BYTES
LAST_POS_X_OFF = CADENCE_OFF + 4
LAST_POS_Z_OFF = CADENCE_OFF + 8
POS_VALID_OFF = CADENCE_OFF + 12
SCRATCH_BYTES = POS_VALID_OFF + 4

# The five player models FFNx treats as walkers. Anything else -- the Highwind
# is model 3 -- is not a walker and must never reach the material selector; an
# earlier full-profile path skipped this gate and then forced the model to
# zero, which made flying produce Cloud's footsteps.
WALKER_MODELS = (0, 1, 2, 4, 19)

# The eight terrain groups, in the order `_material_selector` emits them and
# `prepare()` orders its payloads. The walkmap values in each group are the
# ones Cosmo gives an identical sequence.
MATERIAL_GROUPS = (
    ((0, 1, 16, 25), 0),
    ((2, 9, 11, 12, 19, 20, 28), 1),
    ((4,), 2),
    ((8, 17, 24), 3),
    ((10,), 4),
    ((13, 14), 5),
    ((27,), 6),
    ((29,), 7),
)
# The walkmap value that names each group, used to order the payload sets.
GROUP_KEYS = (0, 2, 4, 8, 10, 13, 27, 29)
EXPECTED_MATERIALS = frozenset(
    value for values, _g in MATERIAL_GROUPS for value in values)


# ------------------------------------------------------------------- routes
def routes_from_configs(texts):
    """
    The eight verified terrain sequences, in `GROUP_KEYS` order.

    Raises if the active configuration does not have the shape this bridge's
    collapsed model dimension depends on -- that refusal is the point.
    """
    merged = sfxmod.merge_world_footsteps(texts, FOOTSTEP_LOGICAL_ID)
    if not merged:
        return None
    materials = {}
    for material in range(32):
        values = [merged.get((model, material)) for model in WALKER_MODELS]
        values = [item for item in values if item]
        if not values:
            continue
        if any(item != values[0] for item in values[1:]):
            raise ValueError(
                'world footsteps differ by player model for walkmap type %d; '
                'this bridge collapses the model dimension and must not be '
                'used with a profile that does not' % material)
        materials[material] = values[0]
    if set(materials) != EXPECTED_MATERIALS:
        raise ValueError('unexpected world terrain coverage %s; expected %s'
                         % (sorted(materials), sorted(EXPECTED_MATERIALS)))
    unique = []
    for values in materials.values():
        if values not in unique:
            unique.append(values)
    if len(unique) != len(MATERIAL_GROUPS):
        raise ValueError('expected %d world terrain sets, got %d'
                         % (len(MATERIAL_GROUPS), len(unique)))
    return tuple(materials[key] for key in GROUP_KEYS)


def collect_configs(files):
    """The active `WM/sfx/config.toml` texts, in application order."""
    out = []
    for rel, full in files:
        norm = rel.replace('\\', '/').lower().strip('/')
        if norm.endswith('wm/sfx/config.toml'):
            try:
                with open(full, encoding='utf-8', errors='replace') as handle:
                    out.append(handle.read())
            except OSError as exc:
                raise ValueError('could not read %s: %s' % (rel, exc)) from exc
    return out


def prepare(entries, files, oggs, cache_dir=None):
    """
    Reserve slot 744 and encode the 68 padded payloads for the archive tail.

    `entries` is modified ONLY at the dedicated empty physical slot. The
    returned payloads are appended by `_emplace_sfx` after every generic
    sequential payload, so their final offsets are known before the late ExeFS
    pass runs.
    """
    groups = routes_from_configs(collect_configs(files))
    if groups is None:
        return None
    template = entries[FOOTSTEP_LOGICAL_ID - 1]
    if template.empty or template.loop:
        raise ValueError('stock SFX %d is not a usable one-shot template'
                         % FOOTSTEP_LOGICAL_ID)
    if not entries[PHYSICAL_SLOT - 1].empty:
        raise ValueError('physical footstep slot %d is no longer empty -- '
                         'another feature has claimed it' % PHYSICAL_SLOT)
    selected = [ogg_id for values in groups for ogg_id in values]
    missing = sorted(set(selected) - set(oggs))
    if missing:
        raise ValueError('active world footsteps are missing OGG(s): %s'
                         % ', '.join(map(str, missing)))
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)

    payloads, encoded_fmt = [], None
    for ogg_id in selected:
        _cached, wav = sfxmod._encode_cached(oggs[ogg_id], template, cache_dir)
        item = audio_dat.entry_from_wav(wav, like=template)
        if (item.loop or item.sample_rate != template.sample_rate
                or item.block_align != template.block_align
                or item.channels != template.channels):
            raise ValueError('footstep %d does not match SFX %d\'s format'
                             % (ogg_id, FOOTSTEP_LOGICAL_ID))
        # Normalise only the advisory byte-rate field: ffmpeg builds disagree
        # about nAvgBytesPerSec even when they emit byte-identical ADPCM, and
        # the hardware-stable slot 744 build used the geometry-derived value.
        item_fmt = audio_dat.canonical_adpcm_fmt(item.fmt)
        if encoded_fmt is None:
            encoded_fmt = item_fmt
        elif item_fmt != encoded_fmt:
            raise ValueError('footstep %d has a different ADPCM format block'
                             % ogg_id)
        payloads.append(item.data)

    stride = max(len(item) for item in payloads)
    stride = ((stride + template.block_align - 1) // template.block_align
              * template.block_align)
    payloads = [item + b'\0' * (stride - len(item)) for item in payloads]
    entries[PHYSICAL_SLOT - 1] = audio_dat.Entry(
        encoded_fmt, payloads[0], loop=0, count=template.count)
    return {'groups': groups, 'payloads': payloads, 'stride': stride,
            'variants': len(selected), 'materials': len(EXPECTED_MATERIALS)}


# --------------------------------------------------------------------- cave
def _material_selector(a, material):
    """Set W24 to the terrain-set index, or branch to `out` if unmapped."""
    for values, group in MATERIAL_GROUPS:
        for value in values:
            a.emit(A.cmp_imm(material, value))
            a.bcond('material_%d' % group, A.EQ)
    a.b('out')
    for _values, group in MATERIAL_GROUPS:
        a.label('material_%d' % group)
        a.emit(A.movz(24, group))
        a.b('selected')


def build_cave(cave, address, scratch, payload_base, stride, groups,
               tick_hz=STOCK_TICK_HZ):
    """The r8 word sequence: motion gate, model gate, terrain, cadence, play.

    `tick_hz` is the rate the world loop will really run at in the module
    being built -- see `cooldowns`. Only the two cadence immediates change
    with it; every other word is r8's.
    """
    human_cd, mount_cd = cooldowns(tick_hz)
    a = Asm(cave, address)
    # The original function has saved its own callee-saved registers already;
    # the cave nevertheless restores every register it borrows before replaying
    # the displaced guest instruction.
    #
    # This is a 0x60 frame written out longhand rather than `AC.save_host`,
    # which uses 0x80 to leave room for the field bridge's condition-metadata
    # snapshot. This cave needs no such snapshot, and the 0x60 layout is the
    # one that passed on hardware -- so it stays exactly as r8 emitted it.
    a.emit(A.stp64_pre(19, 20, 31, -0x60))
    a.emit(AC.stp_off(21, 22, 0x10))
    a.emit(AC.stp_off(23, 24, 0x20))
    a.emit(AC.stp_off(25, 26, 0x30))
    a.emit(AC.stp_off(27, 28, 0x40))
    a.emit(AC.stp_off(29, 30, 0x50))

    # The hook IS the world-map movement routine, so a separate main-loop mode
    # check would only introduce a false negative. Read the original cdecl
    # deltas as a requested-motion gate -- FFNx's `> 10` heuristic suppresses
    # ordinary walking at this port's call cadence.
    a.emit(A.ldr(19, 20, 0x10))
    a.emit(A.add_imm(0, 19, 4))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldr(23, 0, 0))
    a.emit(A.cmp_imm(23, 0))
    a.bcond('moving', A.NE)
    a.emit(A.add_imm(0, 19, 8))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldr(23, 0, 0))
    a.emit(A.cmp_imm(23, 0))
    a.bcond('out', A.EQ)
    a.label('moving')

    # Requested motion alone is not enough: these arguments are read before
    # collision, so they remain non-zero while the player presses into a wall.
    # Snapshot the collision-resolved player position from the previous tick.
    # The world loop calls collision immediately after this function, making
    # the next tick the first safe place to observe its result.
    AC.guest_ptr(a, WORLD_PLAYER_POS_GUEST)
    a.emit(A.ldr(23, 0, PLAYER_POS_X_OFF))
    a.emit(A.ldr(24, 0, PLAYER_POS_Z_OFF))
    AC.bss_ptr(a, 27, scratch)
    a.emit(A.ldr(8, 27, POS_VALID_OFF))
    a.cbz64(8, 'remember_position')
    a.emit(A.ldr(8, 27, LAST_POS_X_OFF))
    a.emit(A.cmp_reg(23, 8))
    a.bcond('actual_motion', A.NE)
    a.emit(A.ldr(8, 27, LAST_POS_Z_OFF))
    a.emit(A.cmp_reg(24, 8))
    a.bcond('out', A.EQ)
    a.label('actual_motion')
    a.emit(A.str_(23, 27, LAST_POS_X_OFF))
    a.emit(A.str_(24, 27, LAST_POS_Z_OFF))
    a.b('position_checked')
    a.label('remember_position')
    a.emit(A.str_(23, 27, LAST_POS_X_OFF))
    a.emit(A.str_(24, 27, LAST_POS_Z_OFF))
    a.emit(A.movz(8, 1))
    a.emit(A.str_(8, 27, POS_VALID_OFF))
    a.b('out')
    a.label('position_checked')

    # FFNx's walkmap helper reads E3A7D0 + 0x4A.
    AC.guest_ptr(a, WORLD_ENTITY_PTR_GUEST)
    a.emit(A.ldr(21, 0, 0))
    a.cbz64(21, 'out')
    a.emit(A.add_imm(0, 21, MODEL_OFF))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrb(22, 0, 0))
    # Match FFNx's world ownership exactly: models 0..2, 4 and 19 only.
    a.emit(A.cmp_imm(22, 2))
    a.bcond('model_ok', A.LE)
    a.emit(A.cmp_imm(22, 4))
    a.bcond('model_ok', A.EQ)
    a.emit(A.cmp_imm(22, 19))
    a.bcond('out', A.NE)
    a.label('model_ok')
    a.emit(A.add_imm(0, 21, TERRAIN_OFF))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrh(23, 0, 0))
    a.emit(A.and_mask(23, 23, 5))
    _material_selector(a, 23)
    a.label('selected')

    # Each terrain group owns an independent sequential cursor. Its variants
    # are contiguous in the padded tail, so the physical descriptor is
    # base + (group_start + cursor) * stride.
    starts, total = [], 0
    for values in groups:
        starts.append(total)
        total += len(values)

    # Words 0..7 are the group cursors; word 8 is the shared cadence counter,
    # so a moving player cannot create one buffer per logic tick.
    AC.bss_ptr(a, 27, scratch)
    # X27 is a RELOCATED HOST ADDRESS. Never the W-form here: it zero-extends
    # the module base away and faults on the first step. That was r7.
    a.emit(A.add_imm64(27, 27, CADENCE_OFF))
    a.emit(A.ldr(8, 27, 0))
    a.cbz64(8, 'select')
    a.emit(A.sub_imm(8, 8, 1))
    a.emit(A.str_(8, 27, 0))
    a.b('out')

    a.label('select')
    AC.bss_ptr(a, 27, scratch)
    for _start, group in zip(starts, range(len(groups))):
        a.emit(A.cmp_imm(24, group))
        a.bcond('group_%d' % group, A.EQ)
    a.b('out')                            # the selector result must be 0..7
    for group, (start, values) in enumerate(zip(starts, groups)):
        a.label('group_%d' % group)
        if group:
            a.emit(A.add_imm64(27, 27, group * 4))
        a.emit(A.ldr(8, 27, 0))
        a.emit(A.cmp_imm(8, len(values)))
        a.bcond('reset_%d' % group, A.HS)
        a.b('choose_%d' % group)
        a.label('reset_%d' % group)
        a.emit(A.movz(8, 0))
        a.label('choose_%d' % group)
        # W24 becomes the payload's flat padded-tail index. Every start is
        # below 64, so the compact immediate form is sufficient.
        if start:
            a.emit(A.add_imm(24, 8, start))
        else:
            a.emit(A.mov_reg(24, 8))
        a.emit(A.add_imm(8, 8, 1))
        a.emit(A.cmp_imm(8, len(values)))
        a.bcond('save_%d' % group, A.LT)
        a.emit(A.movz(8, 0))
        a.label('save_%d' % group)
        a.emit(A.str_(8, 27, 0))
        a.b('play')

    a.label('play')
    AC.bss_ptr(a, 27, scratch)
    a.emit(A.add_imm64(27, 27, CADENCE_OFF))
    a.emit(A.cmp_imm(22, 4))
    a.bcond('mount_pace', A.EQ)
    a.emit(A.cmp_imm(22, 19))
    a.bcond('human_pace', A.NE)
    a.label('mount_pace')
    a.emit(A.movz(8, mount_cd))
    a.b('save_pace')
    a.label('human_pace')
    a.emit(A.movz(8, human_cd))
    a.label('save_pace')
    a.emit(A.str_(8, 27, 0))

    # The physical archive row is only a format template; the descriptor write
    # happens immediately before a channel-owned stock load.
    AC.mov32(a, 25, stride)
    AC.mov32(a, 26, payload_base)
    a.emit(A.mul(24, 24, 25))
    a.emit(A.add_reg(26, 26, 24))
    AC.guest_ptr(a, META_GUEST)
    a.emit(A.str_(25, 0, 0))
    a.emit(A.str_(26, 0, 4))

    # All 68 payloads share one physical slot, so clear BOTH identities before
    # the stock player compares them -- otherwise channel 5 replays the
    # previous 744 buffer without ever reading the new tail descriptor.
    AC.guest_ptr(a, CACHE_GUEST)
    a.emit(A.str_(A.WZR, 0, 0))
    AC.guest_ptr(a, CHANNEL_STATE_GUEST)
    a.emit(A.str_(A.WZR, 0, 0x40))

    # cdecl play_sfx_on_channel(0x40, 744, 5), FOUR guest words: the
    # translated call still reserves its virtual return word immediately
    # before the host BL, and the player consumes it in its epilogue. This is
    # byte-for-byte the layout at the stock world call site (main+0xF3318C).
    a.emit(A.sub_imm(0, 19, 16))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.str_(31, 0, 0))              # the unused virtual return word
    a.emit(A.movz(8, 0x40))
    a.emit(A.str_(8, 0, 4))
    a.emit(A.movz(8, PHYSICAL_SLOT))
    a.emit(A.str_(8, 0, 8))
    a.emit(A.movz(8, 5))
    a.emit(A.str_(8, 0, 12))
    a.emit(A.sub_imm(8, 19, 16))
    a.emit(A.str_(8, 20, 0x10))
    a.emit(A.bl(a.pc(), DIRECT_PLAYER))
    # The callee consumed its virtual return word and restored its own frame;
    # discard the three cdecl arguments so the world callee sees its exact
    # caller stack again.
    a.emit(A.ldr(8, 20, 0x10))
    a.emit(A.add_imm(8, 8, 12))
    a.emit(A.str_(8, 20, 0x10))

    a.label('out')
    a.emit(AC.ldp_off(29, 30, 0x50))
    a.emit(AC.ldp_off(27, 28, 0x40))
    a.emit(AC.ldp_off(25, 26, 0x30))
    a.emit(AC.ldp_off(23, 24, 0x20))
    a.emit(AC.ldp_off(21, 22, 0x10))
    a.emit(A.ldp64_post(19, 20, 31, 0x60))
    a.emit(WORLD_MOVE_ORIG)
    a.emit(A.b(a.pc(), WORLD_MOVE_RESUME))
    return a.resolve()


def apply_to_nso(src, dest, route, space=None, tick_hz=STOCK_TICK_HZ):
    """
    Install the world-footstep bridge, patching `src` into `dest`.

    `tick_hz` must be the rate the WORLD frame limiter is set to in this
    build -- 30 stock, 60 with the 60 FPS group, or whatever `limiter_fps`
    raised it to. Passing the wrong one does not fail the build; it just
    makes footsteps play at the wrong speed, which is why `build.py` derives
    it from the same setting that writes the limiter divisor.
    """
    with open(src, 'rb') as handle:
        blob = handle.read()
    import ff7nx_tables
    segs, raw, own = ff7nx_tables.open_module(blob)
    space = own if space is None else space
    text = space.text
    AC.expect_word(text, WORLD_MOVE_HOOK, WORLD_MOVE_ORIG, 'world movement')
    scratch = AC.scratch_base(blob, segs)

    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))
    entry, placed = ff7nx_cave.emit_laid_out(
        pool, lambda cave, address: build_cave(
            cave, address, scratch, route['payload_base'], route['stride'],
            route['groups'], tick_hz=tick_hz))
    placed[WORLD_MOVE_HOOK] = A.b(WORLD_MOVE_HOOK, entry)
    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)

    out = AC.pack(blob, space.commit(), SCRATCH_BYTES)
    _check_segs, check_raw = AC.segments(out)
    if struct.unpack_from('<I', check_raw[0], WORLD_MOVE_HOOK)[0] != \
            A.b(WORLD_MOVE_HOOK, entry):
        raise ValueError('world movement hook did not survive the repack')
    old_bss, = struct.unpack_from('<I', blob, 0x3C)
    if struct.unpack_from('<I', out, 0x3C)[0] != old_bss + SCRATCH_BYTES:
        raise ValueError('BSS did not grow by the cursor block')

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    human_cd, mount_cd = cooldowns(tick_hz)
    return {'bss_bytes': SCRATCH_BYTES, 'cave_entry': entry,
            'cave_words': len(placed) - 1, 'scratch': scratch,
            'variants': route['variants'], 'materials': route['materials'],
            'table_bytes': 0, 'tick_hz': tick_hz,
            'human_cooldown': human_cd, 'mount_cooldown': mount_cd}
