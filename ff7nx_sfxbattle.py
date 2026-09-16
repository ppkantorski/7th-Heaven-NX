#!/usr/bin/env python3
"""
ff7nx_sfxbattle.py -- FFNx's per-actor battle SFX routes, resolved at the
native player rather than at the archive loader.

THE PROBLEM, AND WHY THE OBVIOUS FIX DOES NOT WORK
==================================================
Cosmo's `config.toml` has keys like

    [battle_char_01_16]        Barret playing game SFX 16
    [battle_enemy_0030_348]    formation 0x0030 playing SFX 348

The game reuses one SFX id across unrelated attackers, so `16` alone is not
enough to know whose sound to play. FFNx resolves this in its own SFX layer by
reading the current animation queue's attacker.

The tempting Switch equivalent -- swap the archive descriptor inside
`sfx_load` once you know the actor -- was built, run on hardware, and RETIRED.
Five revisions of it are recorded in README-35, and the reason it cannot work
is structural rather than a bug:

    the native player compares and records the LOGICAL id BEFORE it calls
    sfx_load

So a loader hook can build Barret's buffer, but the channel stays labelled
"id 16", and the next request for 16 -- a machine-gun enemy, say -- reuses
that buffer without entering the loader at all. Barret's grunt leaks onto the
enemy. Event-stamping the loader calls only changed WHICH calls got remapped;
it could never repair the cache alias.

WHERE THIS HOOKS INSTEAD
========================
`play_sfx_on_channel` (x86 0x745160, ARM64 +0xEF2AB0) at the point it reads
the requested id -- `main + 0xEF2B7C`, BEFORE its cache comparison and before
it calls the loader. A physical slot chosen there becomes both the cache key
and the loader id, so the stock cache, channel, buffer lifetime, volume,
pitch and stop code all see one coherent id.

Three instructions are displaced (the guest-argument lookup: `add`, the
translator `bl`, the `ldr`), replayed in the cave, and the id is written back
to that exact caller argument before resuming at +0xEF2B88.

Hardware: `sfx-barret-player-probe-r1` PASSED. Barret's attack shuffled
correctly while every unrelated SFX stayed vanilla, and -- the point of the
test -- no Barret grunt leaked into the enemy path.

WHAT IS ENABLED HERE, AND WHAT IS DELIBERATELY NOT
==================================================
ONLY `battle_char_01_16` (Barret). That is the one route with a hardware pass
behind it, and the archive gives 26 genuinely empty physical rows to spend.

Cosmo ships 16 override keys -- 7 character, 9 enemy. The rest stay OFF and
the build logs them as pending, for two separate reasons:

  * the six other CHARACTER routes need 36 more empty rows than exist once
    every family is counted; forcing them to share would reintroduce exactly
    the stale-buffer problem this design exists to avoid. A virtual-id/cache
    scheme has to be proven separately first.
  * the ENEMY routes are worse than unproven. They key on
    `battle_context->actor_vars[actor].formationID`, and the mod author's own
    account is that the enemy identification did not match correctly and
    "it was points for some wrong enemy". A route that plays confidently and
    names the wrong monster is worse than no route.

A later revision of the upstream module added a "proxy row" layer -- one
otherwise-empty row per key family plus a second hook at the archive loader,
carrying the enemy routes. It is not ported. It needs contiguous table space
this module currently has none of, and it is built on the enemy
identification above. `PENDING_ROUTES` records the whole set so the build can
say what it is leaving on the numeric SFX.
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

GAME_MODE_GUEST = 0xCC0D89
BATTLE_MODE = 2
# FFNx's `ff7_sfx_play_layered` takes ownership from the animation queue head
# at this playback boundary. An earlier revision used the current SCRIPT event
# index instead and muted both gun exceptions on hardware; the queue head is
# what the passing probe used.
ANIM_EVENT_QUEUE_GUEST = 0x9AAD70
CHAR_INDEX_GUEST = 0x9AB0E4      # party model index, stride 0x68
FORMATION_GUEST = 0x9AB100       # actor_vars[].formationID, same stride

BARRET_KEY = ('char', 1, 16)
BARRET_SLOTS = AC.BARRET_SLOTS   # 731..736, empty in vanilla AND in this build

# (name, keys, slots) -- what this module will install.
ROUTE_LAYOUT = (
    ('Barret', (BARRET_KEY,), BARRET_SLOTS),
)
SUPPORTED_KEYS = frozenset(key for _n, keys, _s in ROUTE_LAYOUT
                           for key in keys)

# Everything Cosmo ships that is knowingly left on its plain numeric SFX.
# Named rather than counted so the build log can be specific about what is
# missing and why, instead of "some routes were skipped".
PENDING_ROUTES = {
    ('char', 7, 274): 'Vincent -- no hardware pass',
    ('char', 1, 352): 'Barret cut -- needs the proxy layer',
    ('char', 6, 290): 'character 06 -- needs the proxy layer',
    ('char', 6, 309): 'character 06 -- needs the proxy layer',
    ('char', 3, 342): 'Aerith -- needs the proxy layer',
    ('char', 3, 346): 'Aerith -- needs the proxy layer',
}
PENDING_ENEMY_REASON = ('enemy formation identification is not reliable yet '
                        '-- it resolved the wrong enemy upstream')


def actor_stride_0x68(a, dst, actor):
    """
    Wactor * 0x68, as (actor << 7) - ((actor << 4) + (actor << 3)).

    128 - 16 - 8 = 104 = 0x68. Done with shifts because the cave has no spare
    register for a multiplier constant at this point and MUL would need one.
    """
    a.emit(A.lsl(dst, actor, 7))
    a.emit(A.lsl(9, actor, 4))
    a.emit(A.lsl(10, actor, 3))
    a.emit(A.add_reg(9, 9, 10))
    a.emit(A.sub_reg(dst, dst, 9))


def prepare(entries, overrides, oggs, cache_dir=None):
    """
    Encode the supported routes into their reserved physical rows.

    `entries` is modified only at rows this module owns. Returns None when the
    active configuration names none of them, so a build without Cosmo's
    special keys installs nothing at all.
    """
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    routes = []
    for name, keys, slots in ROUTE_LAYOUT:
        active = [(key, list(overrides[key])) for key in keys
                  if overrides.get(key)]
        if not active:
            continue
        variants = sfxmod.rotation_order(active[0][1])
        if any(sfxmod.rotation_order(v) != variants for _k, v in active[1:]):
            raise ValueError('%s keys do not share one sequential family'
                             % name)
        sound_ids = {key[2] for key, _v in active}
        if len(sound_ids) != 1:
            raise ValueError('%s mixes logical SFX ids' % name)
        sound_id = next(iter(sound_ids))
        if not 2 <= len(variants) <= len(slots):
            raise ValueError('%s has %d variant(s); %d reserved row(s)'
                             % (name, len(variants), len(slots)))
        # Re-checked against THIS build's archive, not assumed from vanilla:
        # the footstep bridge already claims one of the 26 empty rows, and a
        # future feature could claim more.
        taken = [s for s in slots if not entries[s - 1].empty]
        if taken:
            raise ValueError('%s physical row(s) %s are no longer empty'
                             % (name, taken))
        template = entries[sound_id - 1]
        if template.empty or template.loop:
            raise ValueError('%s logical SFX %d is not a usable one-shot '
                             'template' % (name, sound_id))
        missing = [o for o in variants if o not in oggs]
        if missing:
            raise ValueError('%s assets missing from the enabled folders: %s'
                             % (name, missing))
        encoded = []
        for ogg_id in variants:
            _cached, wav = sfxmod._encode_cached(oggs[ogg_id], template,
                                                 cache_dir)
            entry = audio_dat.entry_from_wav(wav, like=template)
            if entry.loop:
                raise ValueError('%s encoded SFX unexpectedly loops' % name)
            encoded.append(entry)
        for slot, entry in zip(slots, encoded):
            entries[slot - 1] = entry
        routes.append({'name': name,
                       'keys': tuple(k for k, _v in active),
                       'slots': tuple(slots[:len(encoded)]),
                       'variants': len(variants),
                       'ogg_ids': tuple(variants)})
    if not routes:
        return None
    return {'routes': tuple(routes),
            'variants': sum(r['variants'] for r in routes),
            'slots': tuple(s for r in routes for s in r['slots'])}


def pending(overrides):
    """[(key, why)] for every override this build knowingly does not install."""
    out = []
    for key in sorted(overrides):
        if key in SUPPORTED_KEYS:
            continue
        why = PENDING_ROUTES.get(key)
        if why is None:
            why = (PENDING_ENEMY_REASON if key[0] == 'enemy'
                   else 'no route defined')
        out.append((key, why))
    return out


def _select_sequence(a, scratch, offset, slots, suffix):
    """Rotate W23 through `slots`, keeping this route's own cursor in BSS."""
    if len(slots) == 1:
        a.emit(A.movz(23, slots[0]))
        return
    AC.bss_ptr(a, 20, scratch + offset)
    a.emit(A.ldr(24, 20, 0))
    a.emit(A.cmp_imm(24, len(slots)))
    a.bcond('counter_%s_ok' % suffix, A.LT)
    a.emit(A.movz(24, 0))
    a.label('counter_%s_ok' % suffix)
    a.emit(A.movz(23, slots[0]))
    a.emit(A.add_reg(23, 23, 24))
    a.emit(A.add_imm(24, 24, 1))
    a.emit(A.cmp_imm(24, len(slots)))
    a.bcond('save_%s_counter' % suffix, A.LT)
    a.emit(A.movz(24, 0))
    a.label('save_%s_counter' % suffix)
    a.emit(A.str_(24, 20, 0))


def build_cave(cave, address, scratch, routes):
    """
    Choose a physical id at the player boundary, before the cache check.

    X21 carries live guest context at this site and is deliberately untouched.
    Every other register the cave uses is saved and restored -- the Barret
    loader probe aborted on hardware twice for using W20 without preserving
    the enclosing X20, and this site has the same discipline.
    """
    a = Asm(cave, address)
    a.emit(A.stp64_pre(20, 22, 31, -0x30))
    a.emit(AC.stp_off(23, 24, 0x10))
    a.emit(AC.stp_off(25, 30, 0x20))
    a.emit(AC.PLAYER_ORIG)               # replay: add w0, w8, #0xc
    a.emit(A.mov_reg(25, 8))             # keep the caller's argument pointer
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldr(23, 0, 0))              # W23 = the requested logical SFX id

    AC.mov32(a, 0, GAME_MODE_GUEST)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrb(8, 0, 0))
    a.emit(A.cmp_imm(8, BATTLE_MODE))
    a.bcond('write_id', A.NE)

    # Ownership: anim_event_queue[0].attackerID, which is what FFNx reads at
    # this same boundary. Party slots are 0..2.
    AC.mov32(a, 0, ANIM_EVENT_QUEUE_GUEST)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrb(24, 0, 0))
    a.emit(A.cmp_imm(24, 3))
    a.bcond('write_id', A.HS)            # enemy actor: no route installed
    actor_stride_0x68(a, 8, 24)
    AC.mov32(a, 0, CHAR_INDEX_GUEST)
    a.emit(A.add_reg(0, 0, 8))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrb(8, 0, 0))              # W8 = that attacker's model index

    for route_index, route in enumerate(routes):
        for key_index, key in enumerate(route['keys']):
            if key[0] != 'char':
                continue
            suffix = 'c_%d_%d' % (route_index, key_index)
            a.emit(A.cmp_imm(8, key[1]))
            a.bcond('next_%s' % suffix, A.NE)
            a.emit(A.cmp_imm(23, key[2]))
            a.bcond('next_%s' % suffix, A.NE)
            _select_sequence(a, scratch, route_index * 4, route['slots'],
                             suffix)
            a.b('write_id')
            a.label('next_%s' % suffix)

    # Write the chosen id back into the caller's own argument, so the stock
    # cache lookup, loader and channel all see ONE coherent id.
    a.label('write_id')
    a.emit(A.add_imm(0, 25, 12))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.str_(23, 0, 0))
    a.emit(A.mov_reg(8, 23))
    a.emit(AC.ldp_off(25, 30, 0x20))
    a.emit(AC.ldp_off(23, 24, 0x10))
    a.emit(A.ldp64_post(20, 22, 31, 0x30))
    a.emit(A.b(a.pc(), AC.PLAYER_RESUME))
    return a.resolve()


def apply_to_nso(src, dest, plan, space=None):
    """Install the character routes, patching `src` into `dest`."""
    routes = tuple(plan.get('routes') or ())
    if not routes:
        return None
    for route in routes:
        if not route.get('keys') or not route.get('slots'):
            raise ValueError('invalid battle route %r' % (route,))
        bad = [k for k in route['keys'] if k not in SUPPORTED_KEYS]
        if bad:
            raise ValueError('battle route names unsupported key(s) %r' % bad)
    with open(src, 'rb') as handle:
        blob = handle.read()
    import ff7nx_tables
    segs, raw, own = ff7nx_tables.open_module(blob)
    space = own if space is None else space
    text = space.text
    AC.expect_word(text, AC.PLAYER_HOOK, AC.PLAYER_ORIG, 'battle SFX player')
    scratch = AC.scratch_base(blob, segs)
    bss_bytes = 4 * len(routes)          # one rotation cursor per route

    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))
    entry, placed = ff7nx_cave.emit_laid_out(
        pool, lambda cave, address: build_cave(cave, address, scratch, routes))
    placed[AC.PLAYER_HOOK] = A.b(AC.PLAYER_HOOK, entry)
    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)

    out = AC.pack(blob, space.commit(), bss_bytes)
    _check_segs, check_raw = AC.segments(out)
    if struct.unpack_from('<I', check_raw[0], AC.PLAYER_HOOK)[0] != \
            A.b(AC.PLAYER_HOOK, entry):
        raise ValueError('battle player hook did not survive the repack')
    old_bss, = struct.unpack_from('<I', blob, 0x3C)
    if struct.unpack_from('<I', out, 0x3C)[0] != old_bss + bss_bytes:
        raise ValueError('BSS did not grow by the rotation cursors')

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'cave_entry': entry, 'cave_words': len(placed) - 1,
            'bss_bytes': bss_bytes, 'routes': len(routes),
            'variants': sum(r['variants'] for r in routes),
            'slots': tuple(s for r in routes for s in r['slots']),
            'table_bytes': 0}
