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
BUILD 739 enabled only `battle_char_01_16` (Barret), the one route with a
hardware pass behind it. BUILD 740 (HANDOFF-740 step A) adds Cosmo's other
six CHARACTER routes -- Vincent 274, Barret's cut 352, Aerith 342 / 346 and
character 06 (Cait Sith) 290 / 309 -- with the same ownership test Barret
passed on: the attacker is `anim_event_queue[0]`, a party slot (< 3), and
its model index. Nothing about WHERE or HOW the id is chosen changes.

ROWS. Every route still owns its own physical rows, so a row is never shared
between two attackers and the per-channel "same id, reuse the buffer" check
can never hand one actor's sound to another. Only the nine reserved rows at
the archive's high end are used (737-739, 745-750: empty in vanilla, inside
the fixed 750, never requested by the game -- the same argument as Barret's
731-736 and the footstep row 744). The ten empty rows lower down (199, 210,
476, ...) are NOT used: nothing proves the game never asks for them, and a
row the game does ask for would start playing a grunt where it is silent
today. Three routes get two rows, three get one.

TWO-LEVEL ROTATION (HANDOFF-740 section 3). One rotation cursor per route
counts through ALL of its variants. The row is `rows[cursor % R]`, and for a
route with more variants than rows the cave also points that row's
descriptor at variant `cursor` -- appended after the archive in audio.dat
exactly the way the sequential bridge's extras are -- before the cache check.
On a miss the stock loader reads that variant; on a hit the channel replays
what that row last loaded. Either way the sound is this attacker's own, so the
extra variety is a bonus that cannot break ownership. Barret keeps his six
rows and no descriptor write: his path is the BUILD 739 one.

MEMORY. The cave and its 16-byte route records live in the dead-space part
'sfxbat' (ff7nx_deadspace), not the padding pool, which the later passes
need. The BSS stays at the 4 bytes BUILD 739 had: seven 4-bit cursors packed
into that one word (Barret's is bits 0..3 and counts 0..5 exactly as his u32
did). Echo-S voice's page has only 16 bytes of slack, so growing it was not an
option. If the dead-space part cannot be used, the bridge installs the BUILD
739 Barret-only cave in the padding pool instead and says so.

ENEMY ROUTES (BUILD 744). FFNx names an enemy's sound
`battle_enemy_%04X_<id>` from `actor_vars[attacker].formationID` (+0x24 of
the 0x68-byte actor block, guest 0x9AB100) for an attacker in slots 4..9 --
which, despite the name, is the scene.bin ENEMY id. Decoded against the
dump's scene.bin and Echo-S's own `enemy_XXXX` folders, Cosmo's nine keys are
three families: Rude on 35 (0080 Gongaga, 0113, 0119, 012A), Reno on 348
(0030 Sector 7 pillar, 0112, 0129) and Rufus on 274 (0044). The one wrong key
is `battle_enemy_0080_348`: 0080 is Gongaga RUDE, who never plays 348, while
that fight's Reno is 007F and has no key -- the "wrong enemy" the mod author
saw. It is installed as 007F_348 (`ENEMY_KEY_FIXES`).

Each family is ONE route: one record, one cursor, one level-2 rotation over
all its variants. An enemy id is turned into its family by a small map
(u16 enemy id, u16 family key) scanned only for slots 4..9; party slots, slot
3 and any enemy not in the map go down the stock path exactly as before. A
family key is 0x100 | record index and a party key is the model index
(< 0x100), so no enemy can match a character record or the reverse -- Rufus
and Vincent both play 274 and each keeps his own.

The three families share the one remaining reserved row, 739. That is safe
for the same reason one-row level-2 routes are: every routed play rewrites
the row's (length, offset) to its own variant before the player looks at the
channel, and BUILD 741 clears the channel's recorded id whenever it already
holds the row, so the player always reloads rather than replaying a buffer
another attacker left. Buffers are per channel (+0x44/+0x48), so a sound
still playing on another channel is never touched. The variants are encoded
like their own templates (all 44.1 kHz mono ADPCM) and a family whose format
differs from the row's is refused, not installed. Cursors are 3 bits now
(a cursor counts 0..7, so up to 8 variants), so ten routes fit the same 4-byte BSS
word. SEVENTH_NX_ENEMY_GRUNTS=0 leaves the enemies on their numeric sounds.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
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

# (name, keys, rows) -- what this module will install, in cursor order: route
# i's cursor is bits 4i..4i+3 of the BSS word, so Barret stays at bits 0..3.
# Rows are the archive's reserved high end only (see the module docstring).
ROUTE_LAYOUT = (
    ('Barret', (BARRET_KEY,), BARRET_SLOTS),
    ('Vincent', (('char', 7, 274),), (737, 738)),
    ('Barret cut', (('char', 1, 352),), (745, 746)),
    ('Cait Sith 290', (('char', 6, 290),), (749,)),
    ('Cait Sith 309', (('char', 6, 309),), (750,)),
)

# BUILD 742: Omnislash, routed only while its own effect loop runs.
#
# x86 0x46BA0B (the loop, registered with add_fn_to_effect100 at 0x46B889)
# plays 316 + 27 on each of the first fourteen hits (27 = Cloud's critical
# slash, which Cosmo's Voiced Attacks turns into slash + grunt) and 645 +
# 554 on the fifteenth (the branch at 0x46C261 on the hit counter
# 0xA522F8 == 14). So, while that loop is in the effect100 list:
#   * Cloud's 27  -> a row holding the ORIGINAL game's 27, byte for byte;
#   * Cloud's 645 -> a row holding the mod's 645 with one of Cloud's voiced
#                    critical grunts mixed in, rotating through all six.
# Cloud's ordinary critical hits, every other limit and every other
# character are untouched: the records only match while Omnislash runs.
#
# The rows are the two BUILD 740 gave Aerith's 342/346 keys. Those can never
# fire: 342/346 are Cid's spear sounds (kernel weapon table: Aerith's
# weapons play 50/23, which Cosmo already voices with her grunts), her
# battle model data names neither id, and the only game code that plays
# them directly is Cid's Boost Jump / Big Brawl and an attack-effect table
# no character's weapon selects.
OMNISLASH_LOOP = 0x46BA0B
EFFECT100_FN_GUEST = 0xBF2858       # effect100_array_fn, 100 u32 slots
EFFECT100_SLOTS = 100
OMNI_FLAG = 0x8000                  # record id bit 15: "only in Omnislash"
OMNI_HIT = ('char', 0, 27)
OMNI_FINAL = ('char', 0, 645)
OMNI_HIT_ROW = 747
OMNI_FINAL_ROW = 748
SPECIAL_LAYOUT = (
    ('Omnislash hits', OMNI_HIT, OMNI_HIT_ROW),
    ('Omnislash final blow', OMNI_FINAL, OMNI_FINAL_ROW),
)
OMNI_ENV = 'SEVENTH_NX_OMNISLASH_SOUNDS'
# BUILD 744: Cosmo's enemy keys, one route per family (see the docstring).
# (name, logical id, enemy ids). The ids are scene.bin enemy ids, which is
# what FFNx's `formationID` really holds.
ENEMY_ROW = 739
ENEMY_LAYOUT = (
    ('Rude', 35, (0x0080, 0x0113, 0x0119, 0x012A)),
    ('Reno', 348, (0x0030, 0x007F, 0x0112, 0x0129)),
    ('Rufus', 274, (0x0044,)),
)
# Cosmo's typo: 0080 is Gongaga Rude (who plays 35); Gongaga Reno is 007F.
ENEMY_KEY_FIXES = {('enemy', 0x0080, 348): ('enemy', 0x007F, 348)}
ENEMY_FLAG = 0x4000                 # record id bit 14: an enemy family
ENEMY_KEY = 0x100                   # family key = ENEMY_KEY | record index
ENEMY_FIRST, ENEMY_LAST = 4, 9      # FFNx's enemy attacker slots
ENEMY_ENV = 'SEVENTH_NX_ENEMY_GRUNTS'
ENEMY_KEYS = tuple(('enemy', e, sid) for _n, sid, ids in ENEMY_LAYOUT
                   for e in ids)
SUPPORTED_KEYS = frozenset([key for _n, keys, _s in ROUTE_LAYOUT
                            for key in keys]
                           + [k for _n, k, _r in SPECIAL_LAYOUT]
                           + list(ENEMY_KEYS))
RESERVED_ROWS = tuple(sorted([s for _n, _k, rows in ROUTE_LAYOUT
                              for s in rows]
                             + [r for _n, _k, r in SPECIAL_LAYOUT]
                             + [ENEMY_ROW]))
CURSOR_BITS = 3                                  # BUILD 744: was 4
# A cursor holds 0..count-1, so 3 bits carry up to 8 variants. Echo-S's
# config (loaded after Cosmo's, so it wins) gives Reno 1110-1117 and Rufus
# 1120-1127: eight each.
MAX_VARIANTS = 1 << CURSOR_BITS                  # 8
MAX_ROUTES = 32 // CURSOR_BITS                   # 10 cursors in one u32
RECORD = 16                                      # bytes per route record
BSS_BYTES = 4                                    # unchanged from BUILD 739
# BUILD 741: the native player's channel records (x86 0x745160: 0xDE0CE0 +
# (channel - 1) * 0x54, the loaded id at +0x40). Five of them sit between
# 0xDE0CE0 and the archive metadata at 0xDE0E84.
CHANNEL_GUEST = 0xDE0CE0
CHANNEL_STRIDE = 0x54
CHANNEL_ID = 0x40
CHANNELS = (AC.META_GUEST - CHANNEL_GUEST) // CHANNEL_STRIDE      # 5
LO = 0x3                                         # b.lo / b.cc

# Everything Cosmo ships that is knowingly left on its plain numeric SFX.
# The character routes are all installed now; anything that fails to prepare
# is reported by `prepare` with its own reason instead.
PENDING_ROUTES = {
    ('char', 3, 342): 'Aerith never plays it: 342 is Cid\'s spear hit; her '
                      'own hits (50/23) already carry her Cosmo grunts',
    ('char', 3, 346): 'Aerith never plays it: 346 is Cid\'s spear critical; '
                      'her own hits (50/23) already carry her Cosmo grunts',
    ('enemy', 0x0080, 348): 'Cosmo typo: 0080 is Gongaga Rude, who never '
                            'plays 348 -- installed as 007F_348, that '
                            'fight\'s Reno',
}
PENDING_ENEMY_REASON = 'no route defined for this enemy'



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


def _prepare_route(entries, name, keys, rows, overrides, oggs, cache_dir,
                   shared=(), allow_missing=False):
    """
    One route, or ValueError naming why it stays on the numeric SFX.

    `shared` (BUILD 744) names rows an earlier route of the same kind already
    holds: such a row is not required to be empty, it is not rewritten, and
    this route must be level 2 in exactly the row's own format.
    `allow_missing` (BUILD 744, enemies) rotates over the variants that are
    present, as FFNx falls back on a missing one, instead of refusing.
    """
    active = [(key, list(overrides[key])) for key in keys
              if overrides.get(key)]
    if not active:
        return None
    variants = sfxmod.rotation_order(active[0][1])
    if any(sfxmod.rotation_order(v) != variants for _k, v in active[1:]):
        raise ValueError('keys do not share one sequential family')
    sound_ids = {key[2] for key, _v in active}
    if len(sound_ids) != 1:
        raise ValueError('mixes logical SFX ids')
    sound_id = next(iter(sound_ids))
    if not 2 <= len(variants) <= MAX_VARIANTS:
        raise ValueError('%d variant(s); the cursor holds 2..%d'
                         % (len(variants), MAX_VARIANTS))
    # Re-checked against THIS build's archive, not assumed from vanilla: the
    # footstep bridge already claims one of the empty rows, and a future
    # feature could claim more.
    taken = [s for s in rows if not entries[s - 1].empty and s not in shared]
    if taken:
        raise ValueError('physical row(s) %s are no longer empty' % taken)
    template = entries[sound_id - 1]
    if template.empty or template.loop:
        raise ValueError('logical SFX %d is not a usable one-shot template'
                         % sound_id)
    missing = [o for o in variants if o not in oggs]
    if missing and allow_missing and len(variants) - len(missing) >= 2:
        variants = [o for o in variants if o in oggs]
    elif missing:
        raise ValueError('assets missing from the enabled folders: %s'
                         % missing)
    encoded = []
    for ogg_id in variants:
        _cached, wav = sfxmod._encode_cached(oggs[ogg_id], template,
                                             cache_dir)
        entry = audio_dat.entry_from_wav(wav, like=template)
        if entry.loop:
            raise ValueError('encoded SFX unexpectedly loops')
        if entry.empty or not entry.data:
            raise ValueError('encoded SFX %d is empty' % ogg_id)
        encoded.append(entry)
    count = len(encoded)
    used = tuple(rows[:min(len(rows), count)])
    stride, payloads, lens = 0, None, tuple(len(e.data) for e in encoded)
    if count > len(used):
        # Level 2: every variant is appended (route-local stride), and the
        # cave points the chosen row's descriptor at it. A descriptor carries
        # only (length, offset), so every variant must share the row's
        # format -- they were all encoded like the template, and this is
        # checked rather than assumed.
        fmts = {bytes(e.fmt) for e in encoded}
        if len(fmts) != 1:
            raise ValueError('encoded variants do not share one format')
        align = template.block_align or 1
        stride = max(lens)
        stride = (stride + align - 1) // align * align
        payloads = [bytes(e.data) + b'\0' * (stride - len(e.data))
                    for e in encoded]
    for slot in used:
        if slot in shared and (count <= len(used) or bytes(
                entries[slot - 1].fmt) != bytes(encoded[0].fmt)):
            raise ValueError('shared row %d holds another format' % slot)
    for slot, entry in zip(used, encoded):
        if slot not in shared:
            entries[slot - 1] = entry
    return {'name': name,
            'keys': tuple(k for k, _v in active),
            'slots': used,
            'variants': count,
            'ogg_ids': tuple(variants),
            'lens': lens,
            'stride': stride,
            'payloads': payloads,
            'payload_base': None,
            'missing': tuple(missing)}


def _decode_pcm(path, rate, channels):
    """Signed 16-bit samples of any file ffmpeg reads, at `rate`."""
    import array
    import shutil
    import subprocess
    import audio_dat
    # BUILD 808: the ffmpeg the program resolved (SEVENTH_NX_FFMPEG -- in a
    # release, the copy fetched into <folder>/modules/ffmpeg), the way
    # audio_dat and sfxmod already do. A bare 'ffmpeg' only worked where it
    # happened to be on PATH; an .app opened from Finder has no Homebrew
    # PATH, so the Omnislash final blow fell back to numeric SFX.
    ffmpeg = (os.environ.get(audio_dat.FFMPEG_ENV) or shutil.which('ffmpeg')
              or 'ffmpeg')
    raw = subprocess.run([ffmpeg, '-v', 'error', '-i', path, '-ac',
                          str(channels), '-ar', str(rate), '-f', 's16le', '-'],
                         capture_output=True, check=True).stdout
    out = array.array('h')
    out.frombytes(raw[:len(raw) // 2 * 2])
    return out


def _entry_wav(entry):
    """A RIFF file around one archive entry, exactly as it is stored."""
    fmt = bytes(entry.fmt)
    data = bytes(entry.data)
    return (b'RIFF' + struct.pack('<I', 4 + 8 + len(fmt) + 8 + len(data))
            + b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt
            + b'data' + struct.pack('<I', len(data)) + data)


def _pcm_wav(samples, rate, channels):
    data = samples.tobytes()
    return (b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVE'
            + b'fmt ' + struct.pack('<IHHIIHH', 16, 1, channels, rate,
                                    rate * channels * 2, channels * 2, 16)
            + b'data' + struct.pack('<I', len(data)) + data)


def _nv_sibling(path):
    """Cosmo's non-voiced twin of a V_Attacks file, or None."""
    norm = path.replace('\\', '/')
    marker = '/V_Attacks/'
    if marker not in norm:
        return None
    twin = norm.replace(marker, '/NV_Attacks/')
    return twin if os.path.isfile(twin) else None


def _free_row(entries, row):
    if not entries[row - 1].empty:
        raise ValueError('physical row %d is no longer empty' % row)


def _prepare_omni_hit(entries, stock_entries):
    """Row 747 = the ORIGINAL game's sound 27, byte for byte."""
    _free_row(entries, OMNI_HIT_ROW)
    if stock_entries is None:
        raise ValueError('the vanilla archive is not available')
    stock = stock_entries[OMNI_HIT[2] - 1]
    if stock.empty or stock.loop or not stock.data:
        raise ValueError('the vanilla SFX 27 is not a usable one-shot')
    entries[OMNI_HIT_ROW - 1] = audio_dat.Entry(
        fmt=bytes(stock.fmt), data=bytes(stock.data), loop=stock.loop,
        count=stock.count, start=stock.start, end=stock.end)
    return {'name': 'Omnislash hits', 'keys': (OMNI_HIT,), 'omni': True,
            'slots': (OMNI_HIT_ROW,), 'variants': 1, 'ogg_ids': (),
            'lens': (len(stock.data),), 'stride': 0, 'payloads': None,
            'payload_base': None}


def _prepare_omni_final(entries, config, oggs, cache_dir):
    """
    Row 748 + level 2 = the mod's 645 with each of Cloud's six voiced
    critical grunts mixed in.

    The grunt is Cosmo's own: its V_Attacks file minus the NV_Attacks twin
    (the same slash without the voice) leaves the voice alone, still at the
    moment the mod placed it. It is laid over the final-blow sound from its
    start, and the mix is scaled down as a whole if it would clip.
    """
    _free_row(entries, OMNI_FINAL_ROW)
    base = entries[OMNI_FINAL[2] - 1]
    if base.empty or base.loop or not base.data:
        raise ValueError('SFX 645 is not a usable one-shot')
    sources = list((config or {}).get(OMNI_HIT[2]) or ())
    pairs = []
    for ogg_id in sources:
        v = oggs.get(ogg_id)
        nv = _nv_sibling(v) if v else None
        if v and nv:
            pairs.append((ogg_id, v, nv))
    if len(pairs) < 2:
        raise ValueError('needs Cosmo\'s voiced critical set (Voiced '
                         'Attacks on) and its NV_Attacks twins')
    pairs = pairs[:MAX_VARIANTS]
    import array
    import hashlib
    rate, channels = base.sample_rate, base.channels or 1
    tmp_dir = cache_dir or '.'
    base_path = os.path.join(tmp_dir, 'omni645-base.wav')
    with open(base_path, 'wb') as f:
        f.write(_entry_wav(base))
    base_pcm = _decode_pcm(base_path, rate, channels)
    encoded, ogg_ids = [], []
    for ogg_id, v, nv in pairs:
        voiced = _decode_pcm(v, rate, channels)
        plain = _decode_pcm(nv, rate, channels)
        nv = min(len(voiced), len(plain))
        voice = [voiced[i] - plain[i] for i in range(nv)]
        n = max(len(base_pcm), nv)
        under = [base_pcm[i] if i < len(base_pcm) else 0 for i in range(n)]

        def peak_with(gain):
            return max(abs(under[i] + gain * voice[i]) for i in range(nv))
        # The final blow keeps its full level: only the grunt is turned down,
        # and only as far as it takes for the sum to fit 16 bits.
        gain = 1.0
        if nv and peak_with(1.0) > 32767:
            lo_g, hi_g = 0.0, 1.0
            for _ in range(24):
                mid = (lo_g + hi_g) / 2
                if peak_with(mid) > 32767:
                    hi_g = mid
                else:
                    lo_g = mid
            gain = lo_g
        mix = list(under)
        for i in range(nv):
            mix[i] = int(round(under[i] + gain * voice[i]))
        mix = [max(-32768, min(32767, x)) for x in mix]
        pcm = array.array('h', mix)
        blob = _pcm_wav(pcm, rate, channels)
        tag = hashlib.md5(blob).hexdigest()[:12]
        path = os.path.join(tmp_dir, 'omni645-%d-%s.wav' % (ogg_id, tag))
        if not os.path.exists(path):
            with open(path, 'wb') as f:
                f.write(blob)
        _cached, wav = sfxmod._encode_cached(path, base, cache_dir)
        entry = audio_dat.entry_from_wav(wav, like=base)
        if entry.loop or entry.empty or not entry.data:
            raise ValueError('the mixed final blow did not encode cleanly')
        encoded.append(entry)
        ogg_ids.append(ogg_id)
    fmts = {bytes(e.fmt) for e in encoded}
    if len(fmts) != 1:
        raise ValueError('mixed variants do not share one format')
    lens = tuple(len(e.data) for e in encoded)
    align = base.block_align or 1
    stride = (max(lens) + align - 1) // align * align
    entries[OMNI_FINAL_ROW - 1] = encoded[0]
    return {'name': 'Omnislash final blow', 'keys': (OMNI_FINAL,),
            'omni': True, 'slots': (OMNI_FINAL_ROW,),
            'variants': len(encoded), 'ogg_ids': tuple(ogg_ids),
            'lens': lens, 'stride': stride,
            'payloads': [bytes(e.data) + b'\0' * (stride - len(e.data))
                         for e in encoded],
            'payload_base': None}


def prepare(entries, overrides, oggs, cache_dir=None, config=None,
            stock_entries=None):
    """
    Encode the supported routes into their reserved physical rows.

    `entries` is modified only at rows this module owns. A route that cannot
    be prepared is left on its numeric SFX and listed under 'skipped' -- it
    no longer takes the others down with it. Returns None when the active
    configuration names none of the routes, so a build without Cosmo's
    special keys installs nothing at all.

    A level-2 route carries 'payloads' (its padded variants) and a
    'payload_base' of None: `_emplace_sfx` appends the payloads after every
    other bank in audio.dat and fills the base in. A route whose base never
    gets filled in is installed on its own rows only.
    """
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    routes, skipped = [], []
    for name, keys, rows in ROUTE_LAYOUT:
        try:
            route = _prepare_route(entries, name, keys, rows, overrides,
                                   oggs, cache_dir)
        except ValueError as exc:
            skipped.append((name, tuple(k for k in keys if overrides.get(k)),
                            str(exc)))
            continue
        if route:
            routes.append(route)
    # BUILD 742: Omnislash. Only when the mod replaces Cloud's critical slash
    # (27) -- otherwise Omnislash already plays the original -- and only
    # alongside the mod's own battle keys. SEVENTH_NX_OMNISLASH_SOUNDS=0
    # leaves Omnislash exactly as the mod has it.
    if (overrides and (config or {}).get(OMNI_HIT[2])
            and os.environ.get(OMNI_ENV, '1') != '0'):
        for name, builder in (
                ('Omnislash hits',
                 lambda: _prepare_omni_hit(entries, stock_entries)),
                ('Omnislash final blow',
                 lambda: _prepare_omni_final(entries, config, oggs,
                                             cache_dir))):
            try:
                routes.append(builder())
            except (ValueError, OSError) as exc:
                skipped.append((name, (), str(exc)))
    # BUILD 744: the enemy families, last, so their cursors follow every
    # character's (Barret is still bits 0..2).
    if overrides and os.environ.get(ENEMY_ENV, '1') != '0':
        for name, route in _prepare_enemies(entries, overrides, oggs,
                                            cache_dir):
            if isinstance(route, str):
                skipped.append((name, (), route))
            else:
                routes.append(route)
    if not routes and not skipped:
        return None
    return {'routes': tuple(routes),
            'variants': sum(r['variants'] for r in routes),
            'slots': tuple(s for r in routes for s in r['slots']),
            'skipped': tuple(skipped)}


def enemy_overrides(overrides):
    """The enemy keys as installed: Cosmo's 0080_348 typo moved to 007F."""
    out = {k: v for k, v in overrides.items() if k[0] == 'enemy'}
    for bad, good in ENEMY_KEY_FIXES.items():
        if bad in out:
            out.setdefault(good, out[bad])
            del out[bad]
    return out


def _prepare_enemies(entries, overrides, oggs, cache_dir):
    """[(name, route or why-not)] for each enemy family the config names."""
    found = enemy_overrides(overrides)
    out, shared = [], set()
    for name, sid, ids in ENEMY_LAYOUT:
        keys = tuple(('enemy', e, sid) for e in ids)
        if not any(found.get(k) for k in keys):
            continue
        try:
            route = _prepare_route(entries, name, keys, (ENEMY_ROW,), found,
                                   oggs, cache_dir, shared=frozenset(shared),
                                   allow_missing=True)
        except ValueError as exc:
            out.append((name, str(exc)))
            continue
        route['enemy'] = True
        shared.add(ENEMY_ROW)
        out.append((name, route))
    return out


def pending(overrides):
    """[(key, why)] for every override this build knowingly does not install."""
    out = []
    for key in sorted(overrides):
        if key in SUPPORTED_KEYS:
            continue
        why = PENDING_ROUTES.get(key)
        if why is None:
            why = (PENDING_ENEMY_REASON if key[0] == 'enemy'
                   else 'no route defined for this character')
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


def build_legacy_cave(cave, address, scratch, routes):
    """
    BUILD 739's cave, kept word for word: the padding-pool fallback, for
    Barret alone, when the dead-space part cannot be used.

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


# ------------------------------------------------------- BUILD 740 cave
def lslv(rd, rn, rm):
    """LSLV Wd, Wn, Wm."""
    return 0x1AC02000 | (rm << 16) | (rn << 5) | rd


def bic(rd, rn, rm):
    """BIC Wd, Wn, Wm."""
    return 0x0A200000 | (rm << 16) | (rn << 5) | rd


def madd(rd, rn, rm, ra):
    """MADD Wd, Wn, Wm, Wa -- Wd = Wa + Wn * Wm."""
    return 0x1B000000 | (rm << 16) | (ra << 10) | (rn << 5) | rd


def msub(rd, rn, rm, ra):
    """MSUB Wd, Wn, Wm, Wa -- Wd = Wa - Wn * Wm."""
    return 0x1B008000 | (rm << 16) | (ra << 10) | (rn << 5) | rd


def ldr_uxtw2(rt, rn, rm):
    """LDR Wt, [Xn, Wm, UXTW #2]."""
    return 0xB8605800 | (rm << 16) | (rn << 5) | rt


def route_records(routes):
    """
    (records, lens) exactly as the cave reads them.

    record, 16 bytes:  +0 u16 first row    +2 u8 rows    +3 u8 variants
                       +4 u8 first length index      +5 u8 model index
                       +6 u16 logical SFX id (bit 15 Omnislash, bit 14 enemy)
                       +8 u32 audio.dat payload base +12 u32 stride
    Record i owns cursor bits 3i..3i+2. One key per character route: the cave
    scans the records for (model index, id) rather than inlining a compare
    per key. An enemy family's record holds its own index at +5 and bit 14,
    so its key reads back as ENEMY_KEY | index; `enemy_map` maps each enemy
    id onto that.
    A stride of 0 means "rows only" (no descriptor write), which is Barret's
    BUILD 739 behaviour and the fallback for a level-2 route whose payloads
    never reached audio.dat.
    """
    if len(routes) > MAX_ROUTES:
        raise ValueError('%d battle routes; one cursor word holds %d'
                         % (len(routes), MAX_ROUTES))
    records, lens = b'', []
    for index, route in enumerate(routes):
        rows = tuple(route['slots'])
        count = int(route['variants'])
        keys = tuple(route['keys'])
        enemy = bool(route.get('enemy'))
        if enemy:
            if not keys or route.get('omni') or any(
                    k[0] != 'enemy' or not 0 <= k[1] <= 0xFFFF
                    or k[2] != keys[0][2] for k in keys) \
                    or not 1 <= keys[0][2] <= 750:
                raise ValueError('route %s must name enemy keys of one id'
                                 % route['name'])
        elif len(keys) != 1 or keys[0][0] != 'char' or \
                not 0 <= keys[0][1] < 256 or not 1 <= keys[0][2] <= 750:
            raise ValueError('route %s must name exactly one character key'
                             % route['name'])
        if not rows or list(rows) != list(range(rows[0], rows[0] + len(rows))):
            raise ValueError('route %s rows %r are not one consecutive run'
                             % (route['name'], rows))
        if route.get('omni') and keys[0][1] != 0:
            raise ValueError('an Omnislash route must name Cloud')
        if not all(731 <= s <= 750 for s in rows):
            raise ValueError('route %s uses a row outside the reserved '
                             '731..750' % route['name'])
        if not (1 if len(rows) == 1 else 2) <= count <= MAX_VARIANTS \
                or len(rows) > count:
            raise ValueError('route %s: %d variant(s) over %d row(s)'
                             % (route['name'], count, len(rows)))
        stride = int(route.get('stride') or 0)
        base = route.get('payload_base')
        if count > len(rows) and stride and base is not None:
            route_lens = list(route['lens'])
            if len(route_lens) != count or not all(
                    0 < n <= stride for n in route_lens):
                raise ValueError('route %s lengths do not fit its stride'
                                 % route['name'])
            if base + stride * count > 0xFFFFFFFF:
                raise ValueError('route %s payload is beyond a u32 offset'
                                 % route['name'])
        elif enemy:
            # a shared row is only ever safe with its descriptor rewritten
            raise ValueError('enemy route %s has no level-2 payload'
                             % route['name'])
        else:
            # rows only: the rotation is over the rows themselves
            stride, base, count, route_lens = 0, 0, len(rows), []
        if len(lens) + len(route_lens) > 255:
            raise ValueError('too many level-2 variants for the length index')
        sid = (keys[0][2] | (OMNI_FLAG if route.get('omni') else 0)
               | (ENEMY_FLAG if enemy else 0))
        records += struct.pack('<HBBBBHII', rows[0], len(rows), count,
                               len(lens), index if enemy else keys[0][1],
                               sid, base, stride)
        lens += route_lens
    return records, struct.pack('<%dI' % len(lens), *lens)


def enemy_map(routes):
    """
    BUILD 744. u16 enemy id, u16 family key (ENEMY_KEY | record index) for
    every enemy key, in record order. Empty when no enemy route is installed.
    """
    out, seen = b'', set()
    for index, route in enumerate(routes):
        if not route.get('enemy'):
            continue
        for _kind, eid, _sid in route['keys']:
            if eid in seen:
                raise ValueError('enemy id %04X is in two families' % eid)
            seen.add(eid)
            out += struct.pack('<HH', eid, ENEMY_KEY | index)
    return out


def effective_routes(routes):
    """What each route will actually do, as the log reports it."""
    out = []
    for route in routes:
        level2 = (route['variants'] > len(route['slots'])
                  and route.get('stride')
                  and route.get('payload_base') is not None)
        out.append(dict(route, level2=bool(level2),
                        rotation=(route['variants'] if level2
                                  else len(route['slots']))))
    return out


def build_cave(cave, address, scratch, routes, table_va, lens_va,
               shuffle=None, emap=None):
    """
    BUILD 740. The BUILD 739 cave with a route TABLE instead of one inlined
    route, plus the level-2 descriptor write.

    Identical up to the attacker lookup: same prologue and saved registers
    (x20/x22, x23/x24, x25/x30), same three replayed instructions, same mode
    test, same anim_event_queue[0] < 3 ownership, same model-index read. X21
    carries live guest context here and is still untouched. The key match is
    a scan of the route records (13 words for any number of routes); the
    selection and the descriptor write are shared.
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

    AC.mov32(a, 0, ANIM_EVENT_QUEUE_GUEST)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrb(24, 0, 0))
    a.emit(A.cmp_imm(24, 3))
    # BUILD 744: slots 4..9 look up their enemy family; slot 3 stays stock.
    a.bcond('enemy' if emap else 'rot_check', A.HS)
    actor_stride_0x68(a, 8, 24)
    AC.mov32(a, 0, CHAR_INDEX_GUEST)
    a.emit(A.add_reg(0, 0, 8))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrb(24, 0, 0))             # W24 = that attacker's model index
                                         # (callee-saved: the Omnislash test
                                         # below calls the translator)

    # Find this (key, logical id) among the route records. X20 walks the
    # records, W22 counts them; the record index is the cursor. The key is
    # the model index for a character, ENEMY_KEY | index for an enemy family.
    a.label('records')
    AC.bss_ptr(a, 20, table_va)          # adrp + add: any distance
    a.emit(A.movz(22, 0))
    a.label('scan')
    a.emit(A.ldrh(9, 20, 6))
    a.emit(A.ldrb(10, 20, 5))
    a.emit(A.lsr(11, 9, 14))
    a.emit(A.and_mask(11, 11, 1))        # the enemy bit ...
    a.emit(A.orr_lsl(10, 10, 11, 8))     # ... as ENEMY_KEY
    a.emit(A.cmp_reg(10, 24))
    a.bcond('scan_next', A.NE)
    a.emit(A.and_mask(10, 9, 10))        # the id, without the flag bits
    a.emit(A.cmp_reg(10, 23))
    a.bcond('scan_next', A.NE)
    a.emit(A.lsr(10, 9, 15))
    a.cbz(10, 'select')                  # an ordinary route: matched
    # BUILD 742: an Omnislash record matches only while the Omnislash loop
    # sits in the effect100 function list (one page, translated once).
    AC.mov32(a, 0, EFFECT100_FN_GUEST)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    AC.mov32(a, 11, OMNISLASH_LOOP)
    a.emit(A.movz(12, EFFECT100_SLOTS))
    a.label('omni_scan')
    a.emit(A.ldr_post(9, 0, 4))
    a.emit(A.cmp_reg(9, 11))
    a.bcond('select', A.EQ)
    a.emit(A.sub_imm(12, 12, 1))
    a.cbnz(12, 'omni_scan')
    a.label('scan_next')
    a.emit(A.add_imm64(20, 20, RECORD))
    a.emit(A.add_imm(22, 22, 1))
    a.emit(A.cmp_imm(22, len(routes)))
    a.bcond('scan', LO)
    a.b('rot_check')                     # not a routed attacker/id: stock

    if emap:
        # ---- BUILD 744: an enemy attacker (slot 4..9). Its scene.bin enemy
        # id (FFNx's `formationID`, +0x24) is looked up in the family map;
        # a match becomes W24 = ENEMY_KEY | record index and the record scan
        # above does the rest. Anything else is stock.
        emap_va, emap_count = emap
        a.label('enemy')
        a.emit(A.cmp_imm(24, ENEMY_LAST + 1))
        a.bcond('rot_check', A.HS)
        a.emit(A.cmp_imm(24, ENEMY_FIRST))
        a.bcond('rot_check', LO)
        actor_stride_0x68(a, 8, 24)
        AC.mov32(a, 0, FORMATION_GUEST)
        a.emit(A.add_reg(0, 0, 8))
        a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
        a.emit(A.ldrh(9, 0, 0))          # W9 = the enemy id
        AC.bss_ptr(a, 20, emap_va)
        a.emit(A.movz(22, emap_count))
        a.label('emap_scan')
        a.emit(A.ldrh(10, 20, 0))
        a.emit(A.cmp_reg(10, 9))
        a.bcond('emap_hit', A.EQ)
        a.emit(A.add_imm64(20, 20, 4))
        a.emit(A.sub_imm(22, 22, 1))
        a.cbnz(22, 'emap_scan')
        a.b('rot_check')
        a.label('emap_hit')
        a.emit(A.ldrh(24, 20, 2))        # W24 = this enemy's family key
        a.b('records')

    # ---- shared selection. x20 = this route's record, W22 its index.
    a.label('select')
    a.emit(A.add_reg_lsl(11, 22, 22, 1))  # W11 = cursor shift (3 * index)
    AC.bss_ptr(a, 12, scratch)
    a.emit(A.ldr(13, 12, 0))             # W13 = the packed cursor word
    a.emit(A.lsr_reg(14, 13, 11))
    a.emit(A.and_mask(14, 14, CURSOR_BITS))   # W14 = this turn's variant
    a.emit(A.ldrb(10, 20, 3))            # W10 = variants
    a.emit(A.cmp_reg(14, 10))
    a.bcond('cursor_ok', LO)
    a.emit(A.movz(14, 0))
    a.label('cursor_ok')
    a.emit(A.add_imm(15, 14, 1))
    a.emit(A.cmp_reg(15, 10))
    a.bcond('next_ok', LO)
    a.emit(A.movz(15, 0))
    a.label('next_ok')
    a.emit(A.movz(16, (1 << CURSOR_BITS) - 1))
    a.emit(lslv(16, 16, 11))
    a.emit(bic(13, 13, 16))
    a.emit(lslv(15, 15, 11))
    a.emit(A.orr_lsl(13, 13, 15, 0))
    a.emit(A.str_(13, 12, 0))            # only this route's nibble changed
    a.emit(A.ldrb(9, 20, 2))             # W9 = rows
    a.emit(A.udiv(15, 14, 9))
    a.emit(msub(15, 15, 9, 14))          # W15 = variant % rows
    a.emit(A.ldrh(23, 20, 0))
    a.emit(A.add_reg(23, 23, 15))        # W23 = this route's physical row
    a.emit(A.cmp_imm(10, 1))
    a.bcond('write_id', A.EQ)            # one sound: the stock rewind is it
    a.emit(A.ldr(22, 20, 12))            # W22 = stride; 0 = rows only
    a.cbz(22, 'force')                   # rows only: still a fresh load

    # ---- level 2: point the row's descriptor at variant W14, exactly as
    # the sequential bridge does at the loader (length, then offset).
    a.emit(A.ldr(24, 20, 8))
    a.emit(madd(24, 14, 22, 24))         # W24 = base + variant * stride
    a.emit(A.ldrb(15, 20, 4))
    a.emit(A.add_reg(15, 15, 14))
    AC.bss_ptr(a, 17, lens_va)
    a.emit(ldr_uxtw2(22, 17, 15))        # W22 = this variant's exact length
    a.emit(A.sub_imm(8, 23, 1))
    a.emit(A.lsl(9, 8, 5))
    a.emit(A.lsl(10, 8, 2))
    a.emit(A.sub_reg(8, 9, 10))          # (row - 1) * 0x1C
    AC.mov32(a, 0, AC.META_GUEST)
    a.emit(A.add_reg(0, 0, 8))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.str_(22, 0, 0))             # length
    a.emit(A.str_(24, 0, 4))             # offset
    a.b('force')

    # ---- BUILD 741: is the logical id one the sequential bridge rotates?
    # Its own route table (slot index in bits 0..9 of word 0, 8-byte rows)
    # is scanned in place; nothing is copied.
    a.label('rot_check')
    if shuffle and shuffle[1]:
        shuffle_va, shuffle_count = shuffle
        AC.bss_ptr(a, 20, shuffle_va)
        a.emit(A.movz(22, shuffle_count))
        a.emit(A.sub_imm(10, 23, 1))         # slot index = id - 1
        a.label('rot_scan')
        a.emit(A.ldr(9, 20, 0))
        a.emit(A.and_mask(9, 9, 10))
        a.emit(A.cmp_reg(9, 10))
        a.bcond('force', A.EQ)
        a.emit(A.add_imm64(20, 20, 8))
        a.emit(A.sub_imm(22, 22, 1))
        a.cbnz(22, 'rot_scan')
    a.b('write_id')

    # ---- BUILD 741: make a repeat of a ROTATING id on the same channel take
    # the player's ordinary "different sound" path. play_sfx_on_channel
    # (x86 0x745160) rewinds and replays the channel's loaded buffer when
    # channel.id (+0x40 of the 0x54-byte record at 0xDE0CE0) equals the
    # request, so the loader -- where every rotation advances -- is never
    # entered: Omnislash replayed one grunt for every hit. Clearing the
    # recorded id when it matches sends the request down the miss path,
    # which releases the old buffers with the stock release call and loads
    # afresh -- exactly what happens whenever the channel changes sound.
    a.label('force')
    a.emit(A.add_imm(0, 25, 0x10))       # the channel argument
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldr(24, 0, 0))
    a.emit(A.sub_imm(24, 24, 1))         # channel - 1
    a.emit(A.cmp_imm(24, CHANNELS))
    a.bcond('write_id', A.HS)            # unsigned: also rejects channel 0
    a.emit(A.lsl(9, 24, 6))
    a.emit(A.lsl(10, 24, 4))
    a.emit(A.add_reg(9, 9, 10))
    a.emit(A.lsl(10, 24, 2))
    a.emit(A.add_reg(9, 9, 10))          # (channel - 1) * 0x54
    AC.mov32(a, 0, CHANNEL_GUEST + CHANNEL_ID)
    a.emit(A.add_reg(0, 0, 9))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldr(9, 0, 0))
    a.emit(A.cmp_reg(9, 23))
    a.bcond('write_id', A.NE)            # a different sound: already a miss
    a.emit(A.str_(31, 0, 0))             # wzr: "this channel holds nothing"

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


def layout(bump, scratch, routes, shuffle=None, tables=None):
    """
    Place the records and lengths (in `tables`, BUILD 742's own part, or in
    `bump` when none is given) and the cave (in `bump`).
    """
    records, lens = route_records(routes)
    emap = enemy_map(routes)
    tables = bump if tables is None else tables
    table_va = tables.put_bytes(records)
    lens_va = tables.put_bytes(lens) if lens else table_va
    emap_at = (tables.put_bytes(emap), len(emap) // 4) if emap else None
    entry = bump.put(lambda cave, address: build_cave(
        cave, address, scratch, routes, table_va, lens_va, shuffle, emap_at))
    return entry, table_va, len(records) + len(lens) + len(emap)


def _checked_shuffle(segs, raw, shuffle):
    """
    (table_va, count) of the sequential bridge's route table, read back out of
    the module being patched and checked, or None. A table that does not look
    like 8-byte rows of in-range slot indices is not scanned.
    """
    if not shuffle or not shuffle[1]:
        return None
    va, count = int(shuffle[0]), int(shuffle[1])
    if not 0 < count < 750:
        raise ValueError('sequential table count %d is implausible' % count)
    slots = set()
    for i in range(count):
        word = AC.read_word(segs, raw, va + 8 * i)
        slot = word & 0x3FF
        if slot >= 750 or slot in slots or not (word >> 10) & 7:
            raise ValueError('the sequential route table at +0x%X does not '
                             'read back as routes' % va)
        slots.add(slot)
    return (va, count)


def _legacy_routes(routes):
    """The BUILD 739 fallback: Barret alone, on his own rows."""
    return tuple(dict(r, slots=tuple(r['slots'])) for r in routes
                 if r['keys'] == (BARRET_KEY,)
                 and r['variants'] == len(r['slots']))


def apply_to_nso(src, dest, plan, space=None, stock=None, shuffle=None):
    """
    Install the character routes, patching `src` into `dest`.

    BUILD 740: every route goes in the dead-space part 'sfxbat'. If that
    part cannot be used, the BUILD 739 Barret-only cave goes in the padding
    pool as before and the report says what was left out.
    """
    planned = tuple(plan.get('routes') or ())
    # BUILD 744: an enemy family shares row 739, which is only safe with its
    # descriptor rewritten per play -- one whose payloads never reached
    # audio.dat stays on its numeric SFX on its own.
    routes = tuple(r for r in planned if not r.get('enemy')
                   or (r.get('stride') and r.get('payload_base') is not None
                       and r['variants'] > len(r['slots'])))
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
    import ff7nx_deadspace as DS
    import ff7nx_tables
    segs, raw, own = ff7nx_tables.open_module(blob)
    space = own if space is None else space
    text = space.text
    AC.expect_word(text, AC.PLAYER_HOOK, AC.PLAYER_ORIG, 'battle SFX player')
    scratch = AC.scratch_base(blob, segs)

    # A sequential table that does not read back cleanly only costs the
    # BUILD 741 repeat fix; it never costs the routes.
    shuffle_note = None
    try:
        shuffle = _checked_shuffle(segs, raw, shuffle)
    except ValueError as exc:
        shuffle, shuffle_note = None, str(exc)
    fallback = None
    table_bytes = 0
    try:
        lo, hi = DS.part(src, 'sfxbat', stock)
        tlo, thi = DS.part(src, 'sfxtab', stock)
        bump = DS.Bump(lo, hi)
        tabs = DS.Bump(tlo, thi)
        entry, _table_va, table_bytes = layout(bump, scratch, routes, shuffle,
                                               tabs)
        placed = dict(bump.placed)
        placed.update(tabs.placed)
        where = ('dead space: cave +0x%X..+0x%X, %d of %d bytes; tables '
                 '+0x%X..+0x%X, %d of %d bytes'
                 % (lo, hi, bump.at - bump.lo, hi - lo, tlo, thi,
                    tabs.at - tabs.lo, thi - tlo))
        installed = routes
    except ValueError as exc:
        installed = _legacy_routes(routes)
        if not installed:
            raise
        fallback = str(exc)
        pool = ff7nx_cave.HolePool(text,
                                   starts=set(nxmap.Main(src).arm_starts))
        entry, placed = ff7nx_cave.emit_laid_out(
            pool, lambda cave, address: build_legacy_cave(
                cave, address, scratch, installed))
        where = 'the padding pool (BUILD 739 Barret-only cave)'
    if 4 * len(installed) > BSS_BYTES and fallback:
        raise ValueError('the fallback cave would grow BSS past %d bytes'
                         % BSS_BYTES)
    hook_word = A.b(AC.PLAYER_HOOK, entry)
    placed[AC.PLAYER_HOOK] = hook_word
    for at, word in placed.items():
        struct.pack_into('<I', text, at, word)

    out = AC.pack(blob, space.commit(), BSS_BYTES)
    _check_segs, check_raw = AC.segments(out)
    if struct.unpack_from('<I', check_raw[0], AC.PLAYER_HOOK)[0] != hook_word:
        raise ValueError('battle player hook did not survive the repack')
    for at, word in placed.items():
        if struct.unpack_from('<I', check_raw[0], at)[0] != word:
            raise ValueError('battle SFX cave word +0x%X did not survive the '
                             'repack' % at)
    old_bss, = struct.unpack_from('<I', blob, 0x3C)
    if struct.unpack_from('<I', out, 0x3C)[0] != old_bss + BSS_BYTES:
        raise ValueError('BSS did not grow by the one cursor word')

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    effective = effective_routes(installed)
    return {'cave_entry': entry, 'cave_words': len(placed) - 1,
            'bss_bytes': BSS_BYTES, 'scratch': scratch,
            'routes': len(installed),
            'variants': sum(r['variants'] for r in installed),
            'slots': tuple(s for r in installed for s in r['slots']),
            'effective': tuple(effective),
            'left_out': tuple(r['name'] for r in planned
                              if r not in installed),
            'fallback': fallback, 'where': where,
            'rotating_repeats': (shuffle[1] if (shuffle and not fallback)
                                 else 0),
            'shuffle_note': shuffle_note,
            'table_bytes': 0, 'deadspace_table_bytes': table_bytes}
