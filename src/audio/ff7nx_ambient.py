#!/usr/bin/env python3
"""
ff7nx_ambient.py -- Cosmo Memory's per-location ambience, on an independent
native OGG player.

WHY THIS CANNOT BE AN ARCHIVE SLOT
==================================
Every other Cosmo bridge routes through `audio.dat`'s fixed 750 rows. This one
cannot, and the reason is not size. An `sfx/` mapping is keyed on a GAME SOUND
ID -- a slot the engine already knows how to start and stop. An `Ambient/`
mapping is keyed on a PLACE:

    [field_66]   sequential = [ "1580" ]   fade_in = 0.50

Folding that into a sound slot would turn one continuous location loop into a
global sound effect with a global lifetime: it would start wherever that slot
happened to be played and never stop when you left the place it belongs to.

So ambience uses the port's OWN general-purpose loose-OGG player --
`NativeOggPlayer`, the same machinery `MusicStream` drives -- with one
independent instance whose entire lifetime this bridge owns. The loops are
staged as `data/music_ogg/ambient/NNNN.ogg` and the cave builds that stem on
its own stack.

One long-lived loop is exactly what that player is good at. It is NOT a
substitute for the SFX channel manager: a one-shot pool on it freezes under
traffic, which is why the footstep bridges use the stock player instead.

THE HOOK, AND WHY IT IS NOT WHERE THE UPSTREAM VERSION PUT IT
=============================================================
Upstream hooks `0x947CF0`. On this project that instruction is already taken:
`analog-360` -- 360-degree field movement -- has owned it since BUILD 383, and
it is on in the shipping preset.

The fix is not a dispatcher. The very next instruction is:

    0x947CEC  bl   #0x9f7540            the field input read
    0x947CF0  ldp  w8, w9, [x25, #0x10] <- analog-360
    0x947CF4  ldr  w22, [x25]           <- THIS BRIDGE
    0x947CF8  add  w8, w8, #8
    0x947CFC  sub  w0, w9, #0x18

`0x947CF4` is position-independent, untouched by the whole 60 FPS preset, and
runs exactly once per field frame. Because analog-360's cave replays its
displaced `ldp` and branches back to `0x947CF4`, the two chain naturally: the
360 cave runs, then this one, then the stock code continues. No trampoline, no
change to a feature that already works on hardware.

THE ONE HAZARD THAT CREATES, AND THE PROOF IT IS HANDLED
========================================================
W8 and W9 are LOADED at 0x947CF0 and CONSUMED at 0x947CF8/0x947CFC. They are
live across this hook, and this cave clobbers both -- `_emit_ambient_stem`
alone uses x6..x9.

That is the identical trap that took the field-footstep bridge five revisions:
its inner hook sat after a stock `ldp` whose continuation rebuilt guest ESP
from the pair, every revision r1..r5 reused W8 without restoring it, and r6 --
which saves and restores the pair around everything -- is the one that passed.
This cave does the same, in the two frame slots at +0x50/+0x58.

STATE
=====
Five words of BSS, shared by every cave here:

    +0x00  u64  the live NativeOggPlayer, or 0
    +0x08  u32  the last location key (field id or battle id)
    +0x0C  u32  the OGG id currently playing, or 0
    +0x10  u32  the active mode: 0 none, 1 field, 2 battle

The explicit mode is what makes returning from a battle reselect the field's
loop even when the field id never changed, and it is what the world and menu
caves test before disposing anything.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import nxpaths  # BUILD 755: project paths (src/nxpaths.py)
import os
import shutil
import struct

import a64 as A
import ambientmod
import ff7nx_audio_cave as AC
import ff7nx_cave
import ff7nx_tables
import nxmap
from ff7nx_audio_cave import Asm

# --------------------------------------------------------------- hook sites
# The field tick, taken one instruction after analog-360's -- see above.
FIELD_HOOK = 0x947CF4
FIELD_ORIG = 0xB9400336                 # ldr w22, [x25]
FIELD_RESUME = FIELD_HOOK + 4

# The entry of recompiled `battle_loop_sub_41BAB3` is 0x8FB00; this is the
# instruction just past its complete callee-save prologue, where the volatile
# registers are not live yet. Its ADRP is PC-relative, so the cave re-encodes
# it for the cave's own address rather than replaying the raw word.
BATTLE_HOOK = 0x8FB20
BATTLE_ORIG = 0xF00091FC                # adrp x28, #0x12CE000
BATTLE_RESUME = BATTLE_HOOK + 4

WORLD_HOOK = 0xF1E0EC
WORLD_ORIG = 0x90001D98                 # adrp x24, #0x12CE000
WORLD_RESUME = WORLD_HOOK + 4

# The full menu loop (x86 menu_loop_sub_6CC623). Entering it covers both the
# in-game menu and the quit-to-title transition. Ambience is an independent
# player rather than a MusicManager slot, so it has to be disposed here or its
# worker can outlive the field and hold the menu exit.
MENU_HOOK = 0xD0C3D4
MENU_ORIG = 0xD0002E16                  # adrp x22, #0x12CE000
MENU_RESUME = MENU_HOOK + 4

# THE STOP THAT COVERS EVERY OTHER MODULE  (BUILD 506)
# ====================================================
# The world and menu stops above enumerate the two places ambience was known
# to survive into. That is the wrong shape, and GAME OVER is what proved it:
#
#   * ambience kept playing over the game-over music and screen, because game
#     over is neither field, battle, world nor menu, so nothing stopped it;
#   * dismissing the game-over screen then hung the game on a black screen
#     with the loop still running -- the exact hazard the MENU hook's comment
#     already names, "its worker can outlive the field and hold the menu
#     exit", happening at a transition that had no stop.
#
# FFNx does not enumerate. `ff7_handle_ambient_playback` (ff7/misc.cpp:760)
# runs once per frame off the global loop and switches on the driver mode:
# FIELD and BATTLE play, and `default:` -- EVERY other mode -- stops. So the
# correct rule is "stop unless we are in a mode that plays", and the correct
# place for it is the one entry point every module change goes through.
#
# `set_driver_mode(w0)` is that entry point, already mapped and already
# hardware-proven by ff7nx_daynight, which hooks its `stp x29, x30` at
# +0x10F3D04. This takes the NEXT instruction:
#
#   +10F3D00  stp  x20, x19, [sp, #-0x20]!
#   +10F3D04  stp  x29, x30, [sp, #0x10]   <- ff7nx_daynight
#   +10F3D08  add  x29, sp, #0x10
#   +10F3D0C  mov  w19, w0                 <- THIS
#   +10F3D10  bl   #0x10fb0a0
#
# The two chain the same way analog-360 and the field hook do: day/night's
# cave replays its `stp` and returns to +0x10F3D08, +0x10F3D08 runs, then
# this one. `mov w19, w0` is position-independent, the frame is fully
# established by then, and x19/x20 are already saved by the function's own
# prologue -- so the cave may use them, as the other stop caves do.
#
# Being on the SETTER rather than a frame loop means it runs once per
# transition instead of once per frame, and it runs BEFORE the new module's
# loop starts, which is what actually closes the hang: the worker is disposed
# while the old module's thread is still the one running.
MODE_HOOK = 0x10F3D0C
MODE_ORIG = 0x2A0003F3                  # mov w19, w0
MODE_RESUME = MODE_HOOK + 4
# `set_driver_mode`'s numbering, shared with ff7nx_daynight: FF7's own
# `ff7_game_modes` plus one. Only these two play ambience.
MODE_DRIVER_FIELD = 2
MODE_DRIVER_BATTLE = 3
MODE_SAVE_W0 = 0x20

ADRP_PAGE = 0x12CE000                   # the page all three ADRPs name

# ------------------------------------------------------------ port routines
MALLOC = 0x1150ED0
OGG_CTOR = 0x2EB0                       # (NativeOggPlayer *, char *name)
OGG_PLAY = 0x36D0                       # (NativeOggPlayer *, bool loopish)
OGG_VOLUME = 0x3810                     # (NativeOggPlayer *, float gain)
OGG_DELETE = 0x3310                     # stop, dispose and free
# PAUSE AND RESUME  (BUILD 507)
#
# A symmetric pair, found by reading the player's own state transitions.
# Both take only `this`, both take the object's lock through the vtable at
# [this+0x58] (+0x10 acquire, +0x18 release on the tail branch), both call one
# method on the decoder at [this+0x80] and then write a state code to
# [this+0x98]:
#
#   0x3750   decoder vtable +0x48   state <- 2      RESUME
#   0x37B0   decoder vtable +0x50   state <- 4      PAUSE
#
# State 2 is what OGG_PLAY itself writes at 0x370C ("playing"), which is what
# identifies 0x3750 as the resume of the pair and 4 as paused. Using the real
# pause matters: muting with OGG_VOLUME would leave the decoder running, so
# the loop would be somewhere else when the battle unpaused.
OGG_RESUME = 0x3750
OGG_PAUSE = 0x37B0
PLAYER_BYTES = 0xA0
# NativeOggPlayer's worker keeps the supplied name and uses it later while it
# creates/names its Horizon thread.  A stack stem is therefore invalid as
# soon as this cave returns.  Keep the 13-byte ``ambient/NNNN\0`` name in a
# small tail on the same allocation the player owns; OGG_DELETE frees that
# allocation only after the worker has stopped.
PLAYER_NAME_OFF = PLAYER_BYTES
PLAYER_NAME_BYTES = 16                   # 13-byte stem, naturally aligned
PLAYER_ALLOC_BYTES = PLAYER_BYTES + PLAYER_NAME_BYTES
FMOV_S0_1_0 = 0x1E2E1000                # fmov s0, #1.0

# NativeOggPlayer separately formats its decoder pathname and its diagnostic
# Horizon-thread title.  The stock title, ``music stream thread '%s'``, makes
# a perfectly valid ``ambient/NNNN`` path exceed Horizon's short thread-name
# limit and abort.  Replacing only that title with this constant leaves the
# OGG path format and therefore every loop's actual filename unchanged.
RODATA_START = 0x1153000
MUSIC_THREAD_NAME_FORMAT = 0x11A9ABC
MUSIC_THREAD_NAME_FORMAT_STOCK = b"music stream thread '%s'\0"
MUSIC_THREAD_NAME_SAFE = b'music stream\0'

# ------------------------------------------------------------- guest state
FIELD_ID_GUEST = 0xCFF468
# FFNx calls this `modules_global_object->battle_id`. On the original game it
# is a direct guest global, not a pointer, so one translation is all it needs.
BATTLE_ID_GUEST = 0xCC0D8A
# `g_is_battle_paused`. FFNx derives it as
# `get_absolute_value(run_animation_script, 0xA)` and names the result in its
# own symbol, `g_is_battle_paused_DC0E6C`; ff7nx_locate resolves the same
# address independently and ff7nx_summonhold already reads it.
PAUSED_GUEST = 0xDC0E6C

SCRATCH_BYTES = 24
# The state block: +0 player pointer (8), +8 location id, +12 ogg id,
# +16 mode, +20 whether WE have the player paused. The last one is the whole
# reason a flag is needed at all -- pause and resume must be called on the
# TRANSITION, not once per battle frame.
PAUSE_OFF = 20
FIELD_MODE = 1
BATTLE_MODE = 2
AMBIENT_SUBDIR = 'ambient'
INDEX_BLOCK_BYTES = 12

# Stack frame: +0x10..+0x3F saved registers, then +0x50/+0x58 are the live
# W8/W9 pair this hook must preserve.  The filename is allocation-owned, not
# stack-local: NativeOggPlayer's asynchronous worker retains its pointer.
FRAME = 0x60
SAVE_W8 = 0x50
SAVE_W9 = 0x58


# ------------------------------------------------------------------- tables
class Table(tuple):
    """
    `(blob, lo, hi, oggs)` -- the 4-tuple every caller unpacks -- plus the
    BUILD 739 extras as attributes:

      groups        [(ogg, ogg, ...)], one per distinct `sequential` list of
                    two or more loops. A location whose index byte is above
                    len(oggs) names group `byte - len(oggs) - 1`.
      fade_in_ms    this half's `fade_in`, in ms (0: none)
      fade_out_ms   this half's `fade_out`, in ms
      fade_mixed    how many locations carry a fade other than those two
    """

    def __new__(cls, items, groups=(), fade_in_ms=0, fade_out_ms=0,
                fade_mixed=0):
        self = super().__new__(cls, items)
        self.groups = list(groups)
        self.fade_in_ms = fade_in_ms
        self.fade_out_ms = fade_out_ms
        self.fade_mixed = fade_mixed
        return self


def table_oggs(table):
    """Every loop a table can ask for -- its pool and every group member."""
    if table is None:
        return set()
    out = set(table[3])
    for group in getattr(table, 'groups', ()):
        out |= set(group)
    return out


def flatten(table):
    """The same table with every group reduced to its first loop (pre-739)."""
    if table is None or not getattr(table, 'groups', None):
        return table
    entries = table_entries(table)
    values = {loc: (v[0] if isinstance(v, tuple) else v)
              for loc, v in entries.items()}
    lo, hi = table[1], table[2]
    oggs = sorted(set(values.values()))
    index = {ogg: i + 1 for i, ogg in enumerate(oggs)}
    blob = bytearray(hi - lo + 1)
    for loc, ogg in values.items():
        blob[loc - lo] = index[ogg]
    blob.extend(struct.pack('<%dH' % len(oggs), *oggs))
    return Table((bytes(blob), lo, hi, oggs), (), table.fade_in_ms,
                 table.fade_out_ms, table.fade_mixed)


def tables_from_config(config):
    """
    (field_table, battle_table) from the already-layered ambient mapping.

    Either may be None: the mod's option gates decide which of FA / BA / FA+BA
    is active, and `FA=1, BA=0` selects the field-only folder. A build with no
    battle mappings installs no battle cave rather than failing.

    `field_<id>_<triangle>` is deliberately NOT collapsed into a whole-field
    loop. Cosmo's current configuration has none, and treating one as
    field-wide would be wrong rather than approximate.
    """
    # BUILD 739. A `sequential` list is a ROTATION, as FFNx plays it
    # (NxAudioEngine::playAmbient): each entry into that location takes the
    # next loop, per location key. `shuffle` is FFNx's random pick, which
    # Cosmo does not use; it keeps its first loop.
    def choice(meta):
        values = meta.get('sequential')
        if values:
            return tuple(int(v) for v in values[:MAX_GROUP])
        values = meta.get('shuffle') or ()
        return (int(values[0]),) if values else None

    fields = {int(key[6:]): (choice(meta), meta)
              for key, meta in config.items()
              if key.startswith('field_') and key.count('_') == 1
              and choice(meta) is not None}
    battles = {int(key[4:]): (choice(meta), meta)
               for key, meta in config.items()
               if key.startswith('bat_') and choice(meta) is not None}
    if not fields and not battles:
        raise ValueError('the ambient configuration has no exact field or '
                         'battle mappings')

    def fade(metas, key):
        """The half's fade in ms: the most common value, and how many differ."""
        values = [int(round(float(m.get(key, 0.0)) * 1000)) for m in metas]
        best = max(set(values), key=lambda v: (values.count(v), v))
        return best, sum(1 for v in values if v != best)

    def make_table(values, what):
        if not values:
            return None
        lo, hi = min(values), max(values)
        routes = {loc: route for loc, (route, _m) in values.items()}
        oggs = sorted({r[0] for r in routes.values() if len(r) == 1})
        groups = sorted({r for r in routes.values() if len(r) > 1})
        if len(oggs) + len(groups) > 255:
            raise ValueError('too many %s OGG ids for a one-byte map index'
                             % what)
        index = {(ogg,): i + 1 for i, ogg in enumerate(oggs)}
        index.update({g: len(oggs) + 1 + i for i, g in enumerate(groups)})
        table = bytearray(hi - lo + 1)
        for location, route in routes.items():
            table[location - lo] = index[route]
        table.extend(struct.pack('<%dH' % len(oggs), *oggs))
        metas = [m for _r, m in values.values()]
        fi, mixed_in = fade(metas, 'fade_in')
        fo, mixed_out = fade(metas, 'fade_out')
        return Table((bytes(table), lo, hi, oggs), groups, fi, fo,
                     max(mixed_in, mixed_out))

    return make_table(fields, 'field'), make_table(battles, 'battle')


def table_bytes(table):
    return 0 if table is None else len(table[0])


def index_blocks(table):
    """The fixed-size index chunks stored in verified .text padding."""
    blob, lo, hi, _oggs = table
    span = hi - lo + 1
    index = blob[:span]
    return [index[i:i + INDEX_BLOCK_BYTES].ljust(INDEX_BLOCK_BYTES, b'\0')
            for i in range(0, len(index), INDEX_BLOCK_BYTES)]


def _take_index_blocks(pool, text, blocks):
    """Put each 12-byte index chunk in one dead alignment hole."""
    vas = []
    for block in blocks:
        if len(block) != INDEX_BLOCK_BYTES:
            raise ValueError('ambient index block is not %d bytes'
                             % INDEX_BLOCK_BYTES)
        hit = None
        for va, words in pool.free:
            if words >= INDEX_BLOCK_BYTES // 4 and pool._still_zero(va, words):
                hit = va, words
                break
        if hit is None:
            raise ff7nx_cave.NoRoom('no verified 12-byte padding hole remains '
                                    'for an ambient index block')
        pool.free.remove(hit)
        pool.used.append(hit)
        va, _words = hit
        text[va:va + INDEX_BLOCK_BYTES] = block
        vas.append(va)
    return vas


def _tail_write(space, va, data):
    """Fill a previously reserved tail allocation without changing its size."""
    ro_base = space.segs[1][1]
    if va < ro_base:
        space.text[va:va + len(data)] = data
    else:
        off = va - ro_base
        space.rodata[off:off + len(data)] = data


def _set_safe_music_thread_name(space):
    """Shorten only NativeOggPlayer's diagnostic Horizon-thread title.

    Its decoder pathname is produced by a separate formatter, so this is not
    a path rewrite.  It is deliberately a checked in-place replacement of a
    stock literal, not a tail allocation, and consumes no constrained table
    budget.
    """
    off = MUSIC_THREAD_NAME_FORMAT - RODATA_START
    end = off + len(MUSIC_THREAD_NAME_FORMAT_STOCK)
    current = bytes(space.rodata[off:end])
    if current != MUSIC_THREAD_NAME_FORMAT_STOCK:
        raise ValueError('MusicStream thread format differs from compatible '
                         '1.0.3 main')
    if len(MUSIC_THREAD_NAME_SAFE) > len(MUSIC_THREAD_NAME_FORMAT_STOCK):
        raise ValueError('safe MusicStream thread title exceeds stock literal')
    space.rodata[off:end] = MUSIC_THREAD_NAME_SAFE.ljust(
        len(MUSIC_THREAD_NAME_FORMAT_STOCK), b'\0')


# THE SAMPLE RATE IS A MEMORY BUDGET  (BUILD 523)
# ===============================================
# NativeOggPlayer's PCM ring is `rate * channels * 2 * 3` bytes, carved from
# the same fixed 32 MB `g_WaveBufferAllocator` pool every music stream, sound
# effect and voice line uses; SoundBufferImpl.cpp exits the game when it
# cannot allocate from it ("g_WaveBufferAllocator alloc failed"). MEASURED over the 105 shipped loops: 42 are 96 kHz, one is
# 192 kHz and one 88.2 kHz. A 96 kHz stereo loop asks for 1,152,000 bytes, a
# 192 kHz one 2,304,000; at 48 kHz either asks for 576,000.
#
# The console mixes at 48 kHz, so nothing above it survives to the speaker
# anyway -- the renderer resamples it down on every frame. Converting at build
# time spends that once, here, and halves the loop's share of the pool.
#
# Only loops ABOVE 48 kHz are converted. 44.1 kHz loops are smaller than
# 48 kHz ones already and are copied exactly as before, byte for byte. The
# loop point stays at sample 0 (every shipped loop is tagged LOOPSTART=0),
# which is the one loop target valid in every stream -- see voice_ogg.
#
# OFF BY DEFAULT, AND WHY.  The conversion works -- 48 kHz stereo out, loop
# tag kept, 0.998+ correlation with the source -- but MEASURED at the loop
# WRAP it is not yet clean: the re-encoded end lands 7..13 ms off the
# source's length, and on 1525 and 1501 the last ~10 ms decays to near
# silence where the source does not. At LOOPSTART=0 that is a small hole every
# time the bed wraps. 96 kHz loops have played in the Sector 7 fields for
# months, so this ships as an opt-in memory-margin lever, not as a fix:
#
#     SEVENTH_NX_AMBIENT_RATE=48k     bring loops above 48 kHz down to it
MAX_RATE = 48000
RATE_ENV = 'SEVENTH_NX_AMBIENT_RATE'     # `48k` converts all above 48 kHz,
                                         # `native` none; default: above 96k
CONVERT_RECIPE = 'AMBIENT-48K-V2 ar=48000 exact-length vorbis-q6 LOOPSTART=0'
CONVERT_QUALITY = '6'                    # ~192 kbps stereo; the sources run
                                         # 128-190 kbps, so this loses nothing
CONVERTED = []                           # (ogg id, source rate) this build


# BUILD 617b. THE ONE LOOP ABOVE 96 kHz. `1525.ogg` is 192 kHz -- the only
# one of the 105 -- and it is the bed Cosmo Memory maps to field_144
# (mds7st1), field_145 (mds7st2), bat_3 and bat_392..403. Its PCM ring is
# 192000 * 2 * 6 = 2,304,000 bytes out of the 32 MB g_WaveBufferAllocator
# pool, twice any loop that has ever played on hardware ("96 kHz loops have
# played in the Sector 7 fields for months", above), on top of the station's
# music and three voice lines. The pool's failure path is SoundBufferImpl's
# exit() -- no crash report -- which is exactly the mds7st1 report: the game
# closes on entry and on loading a save there, every time. So by default a
# loop ABOVE the proven 96 kHz ceiling is brought down TO 96 kHz (617c: the
# proven rate, an exact 2:1 step, the same ring every working loop has);
# everything at or below it still ships byte for byte. `native` restores the old
# behaviour, `48k` converts every loop above 48 kHz.
SAFE_RATE = 96000


def convert_threshold():
    """Loops above this rate are converted (to convert_target()), or None."""
    v = os.environ.get(RATE_ENV, '').strip().lower()
    if v in ('48k', '48000'):
        return MAX_RATE
    if v in ('native', 'off', 'none', '0'):
        return None
    return SAFE_RATE


def convert_target():
    """The rate a converted loop is staged at.

    By default a loop above SAFE_RATE is brought down to SAFE_RATE itself --
    96 kHz, the rate 42 loops already play at on hardware -- not further:
    192 -> 96 is an exact 2:1 decimation, the ring halves to 1,152,000 bytes
    (the size every proven loop already has), and nothing else about the
    file moves. `48k` still means 48 kHz for everything above it.
    """
    limit = convert_threshold()
    return MAX_RATE if limit == MAX_RATE else SAFE_RATE


def convert_enabled():
    return convert_threshold() is not None


def _rate_channels(path):
    import voice_ogg
    with open(path, 'rb') as handle:
        head = handle.read(1 << 16)
    return voice_ogg._identification(head)


def _cache_dir():
    here = nxpaths.ROOT
    return os.path.join(here, 'cache', '_ambient_ogg')


def _converted_copy(source, rate=None):
    """A 48 kHz Vorbis copy of `source` in the build cache, made once.

    Keyed on the source's CONTENT, the recipe and the encoder, so an unchanged
    loop is never re-encoded and a changed one always is. `rate` defaults to
    MAX_RATE (48 kHz); BUILD 617c brings the 192 kHz loop to SAFE_RATE.
    """
    rate = MAX_RATE if rate is None else rate
    import hashlib
    import subprocess
    import tempfile
    import movies
    import voice_ogg
    chosen = voice_ogg._chosen()
    if not chosen:
        raise ValueError('ambient: no Vorbis encoder -- cannot bring a loop '
                         'above 48 kHz down (install ffmpeg with libvorbis, '
                         'or set %s=native)' % RATE_ENV)
    ffmpeg, encoder, codec_args, oggenc = chosen
    digest = hashlib.sha1()
    with open(source, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    digest.update(('|%s|%s' % (CONVERT_RECIPE, encoder)).encode())
    if rate != MAX_RATE:
        digest.update(('|ar=%d' % rate).encode())
    cache = _cache_dir()
    os.makedirs(cache, exist_ok=True)
    out = os.path.join(cache, digest.hexdigest()[:24] + '.ogg')
    if os.path.exists(out):
        try:
            if _rate_channels(out)[0] == rate:
                return out
        except Exception:                                     # noqa: BLE001
            pass
    # The loop must stay exactly as long as it was. Left to itself the
    # resampler flushes its filter tail as extra samples -- MEASURED on 1503:
    # 864 samples, 18 ms, at an RMS of 0.0014 against 0.068 for the body. At
    # LOOPSTART=0 that is an 18 ms hole in the ambience every time the loop
    # wraps. So the output is cut to the source's exact duration at the new
    # rate, taken from the source's own final granule.
    with open(source, 'rb') as handle:
        data = handle.read()
    src_rate = voice_ogg._identification(data)[0]
    want = int(round(voice_ogg._last_granule(data) * rate
                     / float(src_rate)))
    handle, tmp = tempfile.mkstemp(prefix='.amb.', suffix='.ogg', dir=cache)
    os.close(handle)
    try:
        base = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
                '-y', '-i', source, '-af',
                'aresample=%d,atrim=end_sample=%d' % (rate, want)]
        if oggenc:
            wav = subprocess.run(base + ['-map_metadata', '-1', '-f', 'wav',
                                         '-'], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            if wav.returncode:
                raise ValueError('ambient: ffmpeg could not resample %s' % source)
            run = subprocess.run([oggenc, '-Q', '-q', CONVERT_QUALITY, '-c',
                                  'LOOPSTART=0', '-o', tmp, '-'],
                                 input=wav.stdout, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        else:
            args = list(codec_args)
            if encoder == 'libvorbis':
                args += ['-q:a', CONVERT_QUALITY]
            else:
                args += ['-b:a', '192k']
            run = subprocess.run(base + args + ['-map_metadata', '-1',
                                                '-metadata', 'LOOPSTART=0',
                                                tmp],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if run.returncode:
            raise ValueError('ambient: could not encode %s: %s' % (
                source, (run.stderr or b'').decode('utf-8', 'replace')[:200]))
        got_rate, _channels = _rate_channels(tmp)
        if got_rate != rate:
            raise ValueError('ambient: %s came out at %d Hz' % (source,
                                                             got_rate))
        with open(tmp, 'rb') as handle:
            got = voice_ogg._last_granule(handle.read())
        if abs(got - want) > 1:
            raise ValueError('ambient: %s came out %d samples long, not %d -- '
                             'the loop would gap' % (source, got, want))
        os.replace(tmp, out)
        tmp = None
        return out
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def stage_loops(oggs, dest_dir, ogg_ids):
    """Stage loops once; reuse a byte-identical timestamped output on rebuild.

    The old implementation opened and rewrote every selected OGG on every
    invocation.  These are immutable source assets during a build, so
    ``copy2`` lets a subsequent run identify an unchanged staged file by its
    source size and preserved nanosecond mtime.  A new/clean output still gets
    the required copy; this merely avoids spending the same 100+ MB of I/O
    again when the output tree is retained between builds.

    Returns ``(total_bytes, file_count, copied_bytes, copied_count)``.
    """
    os.makedirs(dest_dir, exist_ok=True)
    missing, total = [], 0
    copied_bytes = copied = 0
    del CONVERTED[:]
    for ogg_id in sorted(set(ogg_ids)):
        source = oggs.get(str(ogg_id))
        if source is None:
            missing.append(ogg_id)
            continue
        target = os.path.join(dest_dir, '%04d.ogg' % ogg_id)
        limit = convert_threshold()
        if limit is not None:
            try:
                rate = _rate_channels(source)[0]
            except Exception:                                 # noqa: BLE001
                rate = 0                  # unreadable header: ship as-is
            if rate > limit:
                source = _converted_copy(source, convert_target())
                CONVERTED.append((ogg_id, rate))
        src_stat = os.stat(source)
        total += src_stat.st_size
        try:
            dst_stat = os.stat(target)
            unchanged = (dst_stat.st_size == src_stat.st_size
                         and dst_stat.st_mtime_ns == src_stat.st_mtime_ns)
        except FileNotFoundError:
            unchanged = False
        if not unchanged:
            # copy2 retains mtime, making the next run's comparison cheap;
            # shutil can use the platform's efficient copy primitive instead
            # of materialising a 100+ MB asset set in Python memory.
            shutil.copy2(source, target)
            copied += 1
            copied_bytes += src_stat.st_size
    if missing:
        raise ValueError('ambient OGG(s) missing from the enabled folders: %s'
                         % ', '.join(map(str, missing[:8])))
    return total, len(set(ogg_ids)), copied_bytes, copied


# --------------------------------------------------------------------- cave
def _emit_stem(a, pointer_reg=1, value_reg=21):
    """
    Write `ambient/NNNN\\0` into the 13-byte stack buffer at `pointer_reg`.

    The OGG constructor takes a stem relative to `data/music_ogg` and appends
    `.ogg` itself. Two 32-bit stores cover the eight-byte prefix; the decimal
    loop then writes the four digits, most significant last.
    """
    a.emit(A.movz(8, 0x6D61))
    a.emit(A.movk_hi(8, 0x6962))
    a.emit(A.str_(8, pointer_reg, 0))       # little-endian b'ambi'
    a.emit(A.movz(8, 0x6E65))
    a.emit(A.movk_hi(8, 0x2F74))
    a.emit(A.str_(8, pointer_reg, 4))       # little-endian b'ent/'
    a.emit(A.mov_reg(6, value_reg))
    a.emit(A.movz(7, 10))
    for off in (11, 10, 9, 8):
        a.emit(AC.udiv(8, 6, 7))
        a.emit(AC.msub(9, 8, 7, 6))
        a.emit(A.add_imm(9, 9, ord('0')))
        a.emit(A.strb(9, pointer_reg, off))
        a.emit(A.mov_reg(6, 8))
    a.emit(A.strb(31, pointer_reg, 12))


def _prologue(a, scratch, save_pair):
    a.emit(A.stp64_pre(29, 30, 31, -FRAME))
    a.emit(AC.stp_off(19, 20, 0x10))
    a.emit(AC.stp_off(21, 22, 0x20))
    a.emit(AC.stp_off(23, 24, 0x30))
    # The compact lookup borrows W25 for its block index. X25 is the
    # enclosing translated field routine's guest-context pointer, not a
    # disposable temporary; a W25 write zero-extends over that host pointer.
    a.emit(A.str64(25, A.SP, 0x40))
    if save_pair:
        # W8/W9 are live across the field hook -- see the module docstring.
        a.emit(A.str64(8, A.SP, SAVE_W8))
        a.emit(A.str64(9, A.SP, SAVE_W9))
    AC.bss_ptr(a, 19, scratch)
    a.emit(A.ldr64(20, 19, 0))


def _epilogue(a, save_pair):
    a.emit(A.ldr64(25, A.SP, 0x40))
    if save_pair:
        a.emit(A.ldr64(8, A.SP, SAVE_W8))
        a.emit(A.ldr64(9, A.SP, SAVE_W9))
    a.emit(AC.ldp_off(23, 24, 0x30))
    a.emit(AC.ldp_off(21, 22, 0x20))
    a.emit(AC.ldp_off(19, 20, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, FRAME))


# THE PREFLIGHT  (BUILD 524)
# ==========================
# MEASURED on hardware, one variable at a time:
#
#   ambient-mode off   still crashes        ambient-menu off   still crashes
#   ambient-field off  FIXED                1503 -> 1511 swap  still crashes
#   abort-hang         freezes (so it is one of the engine's own exit(-1)s)
#   abort-hang-file    freezes, no welcome text
#
# The last one names it: NativeOggPlayer's constructor calls
#
#     sprintf(path, "%s/data/music_ogg/%s.ogg", base, name)     +0x2FF0
#     vgmstream_open(path) -> fopen(path, "rb")                  +0x10FE9C0
#
# and when that returns NULL it prints "music file can not be loaded" and
# calls exit(-1) (+0x3108..+0x3150). That is a reasonable rule for the game's
# own music, which must exist. It is the wrong rule for an OPTIONAL ambience
# bed: a loop that cannot be opened at this instant should simply not play
# yet, not close the software.
#
# So before constructing anything the cave builds the SAME path with the SAME
# base, format and name the constructor will use, and opens it itself:
#
#     NULL -> nothing is allocated, and the location is forgotten so the next
#             frame asks again. The loop starts the first frame the open
#             succeeds.
#     ok   -> fclose, then the constructor runs exactly as it always has.
#
# The constructor's open is on this same thread, a few instructions later,
# with nothing in between that could take the file away.
#
# What this does NOT explain is WHY the open fails only on New Game -- the
# file is present, other loops open fine, and a different loop's content under
# the same name failed the same way. The preflight makes the game correct
# whatever the reason; finding the reason is a separate question.
BASE_PATH = 0x10FAEE0                    # returns the content root (char *)
SPRINTF = 0x1150FC0
FOPEN = 0x11511A0
FCLOSE = 0x11511B0
PATH_FORMAT = 0x11A89B3                  # "%s/data/music_ogg/%s.ogg"
MODE_RB = 0x11AA299                      # "rb"
PREFLIGHT_BYTES = 0x120                  # 0x100 path + a 16-byte stem slot
PREFLIGHT_STEM = 0x100
PREFLIGHT_FAILS = []                     # tests only


def _emit_preflight(a):
    """Open the loop's file ourselves; on failure, play nothing and retry."""
    a.emit(A.sub_imm64(31, 31, PREFLIGHT_BYTES))
    a.emit(A.add_imm64(1, 31, PREFLIGHT_STEM))
    _emit_stem(a, pointer_reg=1)
    a.emit(A.bl(a.pc(), BASE_PATH))
    a.emit(AC.mov64(2, 0))                       # base
    a.emit(A.add_imm64(3, 31, PREFLIGHT_STEM))   # name
    AC.bss_ptr(a, 1, PATH_FORMAT)
    a.emit(A.add_imm64(0, 31, 0))                # path buffer
    a.emit(A.bl(a.pc(), SPRINTF))
    a.emit(A.add_imm64(0, 31, 0))
    AC.bss_ptr(a, 1, MODE_RB)
    a.emit(A.bl(a.pc(), FOPEN))
    a.cbz64(0, 'preflight_fail')
    a.emit(A.bl(a.pc(), FCLOSE))
    a.emit(A.add_imm64(31, 31, PREFLIGHT_BYTES))
    a.b('preflight_ok')
    a.label('preflight_fail')
    a.emit(A.add_imm64(31, 31, PREFLIGHT_BYTES))
    # Forget this location and this loop, so the next frame comes back
    # through location_change -> change -> create and asks again.
    a.emit(A.str_(31, 19, 12))
    a.emit(A.movz(9, 0xFFFF))
    a.emit(A.movk_hi(9, 0xFFFF))
    a.emit(A.str_(9, 19, 8))
    a.b('volume')
    a.label('preflight_ok')


def _emit_swap(a, preflight=False, rt=None):
    """Shared tail: retire the old loop, start the selected one, hold gain.

    `preflight` (the FIELD cave) opens the loop's file first -- see
    _emit_preflight. The battle cave passes False: nothing has implicated it,
    the padding pool is nearly full, and the preflight is ~30 words.
    """
    a.label('change')
    a.emit(A.ldr(8, 19, 12))
    a.emit(A.cmp_reg(21, 8))
    a.bcond('volume', A.EQ)
    a.emit(A.str_(21, 19, 12))
    a.cbz64(20, 'create')
    a.emit(AC.mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_DELETE))
    a.emit(A.str64(31, 19, 0))
    a.emit(AC.mov64(20, 31))

    a.label('create')
    a.cbz64(21, 'volume')
    if preflight:
        _emit_preflight(a)
    a.emit(A.movz(0, PLAYER_ALLOC_BYTES))
    # BUILD 739: through the fade runtime, which first disposes a loop that
    # is still fading out -- never two players alive at once.
    a.emit(A.bl(a.pc(), rt['malloc'] if rt else MALLOC))
    a.cbz64(0, 'out')
    a.emit(AC.mov64(20, 0))
    # The constructor's worker keeps x1 after this call.  It must point to
    # storage lasting for the complete NativeOggPlayer lifetime, never our
    # transient cave frame (which is what caused the SDK User Break).
    a.emit(A.add_imm64(1, 20, PLAYER_NAME_OFF))
    _emit_stem(a)
    a.emit(AC.mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_CTOR))
    a.emit(A.str64(20, 19, 0))
    a.emit(AC.mov64(0, 20))
    a.emit(A.movz(1, 1))                    # retain the OGG's own loop points
    a.emit(A.bl(a.pc(), OGG_PLAY))

    # MusicManager re-applies gain every tick. An independent player has no
    # manager entry, so hold it here -- which is also what applies the gain
    # once the asynchronous voice actually exists.
    a.label('volume')
    a.cbz64(20, 'out')
    if rt:
        # BUILD 739: the gain is the fade's, not a flat 1.0 -- see
        # build_gain_helper.
        a.emit(AC.mov64(0, 20))
        a.emit(A.bl(a.pc(), rt['gain']))
        return
    a.emit(FMOV_S0_1_0)
    a.emit(AC.mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_VOLUME))


def table_entries(table):
    """Return the exact ``{location: ogg_id}`` mapping encoded by a table.

    The old runtime indexed this compact blob directly from a segment tail.
    That is normally attractive, but the two ambient maps together need
    2,002 contiguous bytes and the 60 FPS preset intentionally leaves that
    space fragmented.  The bridges themselves already live safely in the
    verified executable-padding pool, so the runtime now emits the same exact
    sparse mapping as code.  This helper keeps the on-disk/config format and
    its one-based index invariant unchanged; only its representation in the
    patched module changes.
    """
    blob, lo, hi, oggs = table
    groups = getattr(table, 'groups', ())
    span = hi - lo + 1
    index = blob[:span]
    values = struct.unpack('<%dH' % len(oggs), blob[span:])
    if list(values) != list(oggs):
        raise ValueError('ambient table pool does not match its OGG list')
    out = {}
    for offset, choice in enumerate(index):
        if choice:
            if choice > len(values) + len(groups):
                raise ValueError('ambient table index %d is outside its pool'
                                 % choice)
            out[lo + offset] = (values[choice - 1] if choice <= len(values)
                                else tuple(groups[choice - len(values) - 1]))
    return out


def _emit_lookup(a, pointer_va, pool_va, lo, hi, mode, select=None):
    """Select W21 = the OGG id for the current location, or 0.

    The index is divided into 12-byte blocks held in verified executable
    padding.  A compact u32 pointer table and the tiny OGG-id pool remain in
    the ordinary segment tails.  That takes 860 contiguous bytes for both
    maps, rather than the old 2,002 bytes, while preserving every mapping.
    """
    a.emit(A.ldr(8, 19, 16))
    a.emit(A.cmp_imm(8, mode))
    a.bcond('location_change', A.NE)
    a.emit(A.ldr(8, 19, 8))
    a.emit(A.cmp_reg(22, 8))
    a.bcond('volume', A.EQ)
    a.label('location_change')
    a.emit(A.movz(8, mode))
    a.emit(A.str_(8, 19, 16))
    a.emit(A.str_(22, 19, 8))
    a.emit(A.cmp_imm(22, lo))
    a.bcond('no_track', A.LT)
    a.emit(A.cmp_imm(22, hi))
    a.bcond('no_track', 12)                 # GT
    a.emit(A.sub_imm(21, 22, lo))
    a.emit(A.movz(24, INDEX_BLOCK_BYTES))
    a.emit(AC.udiv(25, 21, 24))             # block number
    a.emit(AC.msub(21, 25, 24, 21))         # byte within block
    AC.bss_ptr(a, 23, pointer_va)
    a.emit(AC.ldr_uxtw_scaled(23, 23, 25))
    # Pointer entries are module VAs. Materialise module VA 0 to recover the
    # loader's relocated base, then form the host address of this block.
    AC.bss_ptr(a, 24, 0)
    a.emit(A.add_reg64(23, 24, 23))
    a.emit(AC.ldrb_uxtw(21, 23, 21))
    a.cbz64(21, 'no_track')
    a.emit(A.sub_imm(21, 21, 1))
    if select:
        # BUILD 739: the pool load, or the next loop of a rotation.
        a.emit(A.bl(a.pc(), select))
    else:
        AC.bss_ptr(a, 23, pool_va)
        a.emit(AC.ldrh_uxtw_scaled(21, 23, 21))
    a.b('change')
    a.label('no_track')
    a.emit(A.movz(21, 0))


def build_field_cave(cave, addr, scratch, pointer_va, pool_va, lo, hi,
                     rt=None):
    """Exact field-id dispatcher, on one independent OGG player."""
    a = Asm(cave, addr)
    _prologue(a, scratch, save_pair=True)
    AC.mov32(a, 0, FIELD_ID_GUEST)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrh(22, 0, 0))
    _emit_lookup(a, pointer_va, pool_va, lo, hi, FIELD_MODE,
                 rt and rt.get('select_field'))
    _emit_swap(a, preflight=True, rt=rt)
    a.label('out')
    _epilogue(a, save_pair=True)
    a.emit(FIELD_ORIG)                      # ldr w22, [x25] -- not PC-relative
    a.emit(A.b(a.pc(), FIELD_RESUME))
    return a.resolve()


def _emit_battle_pause(a, rt=None):
    """Follow `g_is_battle_paused` with the player's own pause/resume.

    FFNx does this by polling every frame
    (`ff7_handle_ambient_playback`, ff7/misc.cpp):

        if ( *is_battle_paused && isAmbientPlaying())  pauseAmbient();
        else if (!*is_battle_paused && !isAmbientPlaying()) resumeAmbient();

    which is a TRANSITION, expressed as a comparison against what the engine
    is already doing. We have no `isAmbientPlaying`, so the transition is
    remembered in the state block instead -- and it has to be a transition:
    calling pause on every paused frame would take the player's lock sixty
    times a second for nothing.

    This runs FIRST, before the location lookup, so it happens on every
    battle frame whatever the lookup decides -- including the frames where
    there is no track for this battle and the lookup leaves early.
    """
    AC.mov32(a, 0, PAUSED_GUEST)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrb(23, 0, 0))
    # Normalise to 0/1: the engine writes a byte, and only "is it zero"
    # is meaningful. Comparing the raw byte against our flag would toggle
    # on any change of a non-zero value.
    a.emit(A.cmp_imm(23, 0))
    a.bcond('pause_none', A.EQ)
    a.emit(A.movz(23, 1))
    a.b('pause_have')
    a.label('pause_none')
    a.emit(A.mov_reg(23, A.WZR))
    a.label('pause_have')
    a.emit(A.ldr(24, 19, PAUSE_OFF))
    a.emit(A.cmp_reg(23, 24))
    a.bcond('pause_done', A.EQ)
    a.emit(A.str_(23, 19, PAUSE_OFF))
    # The player pointer, re-read rather than trusting x20: this runs before
    # the lookup, so x20 is whatever the prologue loaded, and a battle with
    # no ambience at all has none.
    a.emit(A.ldr64(20, 19, 0))
    a.cbz64(20, 'pause_done')
    a.emit(AC.mov64(0, 20))
    a.cbz(23, 'pause_resume')
    # BUILD 739: FFNx's pauseAmbient / resumeAmbient fade first; the runtime
    # calls OGG_PAUSE once the fade-out has reached zero (build_gain_helper).
    a.emit(A.bl(a.pc(), rt['pause'] if rt else OGG_PAUSE))
    a.b('pause_done')
    a.label('pause_resume')
    a.emit(A.bl(a.pc(), rt['resume'] if rt else OGG_RESUME))
    a.label('pause_done')


def build_battle_cave(cave, addr, scratch, pointer_va, pool_va, lo, hi,
                      rt=None):
    """Exact `bat_<id>` dispatcher, sharing the field player's state."""
    a = Asm(cave, addr)
    _prologue(a, scratch, save_pair=False)
    _emit_battle_pause(a, rt)
    AC.mov32(a, 0, BATTLE_ID_GUEST)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrh(22, 0, 0))
    _emit_lookup(a, pointer_va, pool_va, lo, hi, BATTLE_MODE,
                 rt and rt.get('select_battle'))
    _emit_swap(a, rt=rt)
    a.label('out')
    _epilogue(a, save_pair=False)
    # NOT the raw BATTLE_ORIG word: an ADRP's page delta is relative to its
    # own PC, so replaying the stock encoding from a padding hole would point
    # x28 at a completely different page.
    a.emit(A.adrp(28, a.pc(), ADRP_PAGE))
    a.emit(A.b(a.pc(), BATTLE_RESUME))
    return a.resolve()


def _build_stop_cave(cave, addr, scratch, reg, resume, rt=None):
    """Dispose any live ambience once, on entering world map or menu."""
    a = Asm(cave, addr)
    a.emit(A.stp64_pre(29, 30, 31, -0x30))
    a.emit(AC.stp_off(19, 20, 0x10))
    AC.bss_ptr(a, 19, scratch)
    a.emit(A.ldr(8, 19, 16))
    a.emit(A.cmp_imm(8, 0))
    a.bcond('out', A.EQ)
    a.emit(A.ldr64(20, 19, 0))
    a.cbz64(20, 'clear')
    a.emit(AC.mov64(0, 20))
    # BUILD 739: retired, not deleted -- it fades out over `fade_out` and the
    # flip tick disposes it (build_retire_helper / build_flip_cave).
    a.emit(A.bl(a.pc(), rt['retire'] if rt else OGG_DELETE))
    a.label('clear')
    a.emit(A.str64(31, 19, 0))
    a.emit(A.str_(31, 19, 8))
    a.emit(A.str_(31, 19, 12))
    a.emit(A.str_(31, 19, 16))
    # ... and OUR pause flag. A disposed player is not paused, and leaving a
    # 1 here would make the next battle's first frame call resume on a
    # player that was never paused.
    a.emit(A.str_(31, 19, PAUSE_OFF))
    a.label('out')
    a.emit(AC.ldp_off(19, 20, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, 0x30))
    a.emit(A.adrp(reg, a.pc(), ADRP_PAGE))  # re-encoded, as above
    a.emit(A.b(a.pc(), resume))
    return a.resolve()


def build_mode_stop_cave(cave, addr, scratch, rt=None):
    """
    Dispose any live ambience on entering a mode that does not play it.

    This is FFNx's `default: stopAmbient()` (ff7/misc.cpp:760) expressed as an
    event rather than a poll: FIELD and BATTLE are the only modes that play,
    so every other one stops. It covers game over, the title screen, the
    credits, the swirl and anything else, instead of enumerating them.

    `w0` is the incoming mode and must reach the displaced `mov w19, w0`
    intact, so it is kept in the frame across OGG_DELETE -- which is a
    blocking dispose, and blocking here is the point: the worker has to be
    gone before the new module's loop starts.
    """
    a = Asm(cave, addr)
    a.emit(A.stp64_pre(29, 30, 31, -0x30))
    a.emit(AC.stp_off(19, 20, 0x10))
    a.emit(A.str_(0, A.SP, MODE_SAVE_W0))
    a.emit(A.cmp_imm(0, MODE_DRIVER_FIELD))
    a.bcond('out', A.EQ)
    a.emit(A.cmp_imm(0, MODE_DRIVER_BATTLE))
    a.bcond('out', A.EQ)
    AC.bss_ptr(a, 19, scratch)
    a.emit(A.ldr(8, 19, 16))
    a.emit(A.cmp_imm(8, 0))
    a.bcond('out', A.EQ)
    a.emit(A.ldr64(20, 19, 0))
    a.cbz64(20, 'clear')
    a.emit(AC.mov64(0, 20))
    # BUILD 739: retired, not deleted -- it fades out over `fade_out` and the
    # flip tick disposes it (build_retire_helper / build_flip_cave).
    a.emit(A.bl(a.pc(), rt['retire'] if rt else OGG_DELETE))
    a.label('clear')
    a.emit(A.str64(31, 19, 0))
    a.emit(A.str_(31, 19, 8))
    a.emit(A.str_(31, 19, 12))
    a.emit(A.str_(31, 19, 16))
    # ... and OUR pause flag. A disposed player is not paused, and leaving a
    # 1 here would make the next battle's first frame call resume on a
    # player that was never paused.
    a.emit(A.str_(31, 19, PAUSE_OFF))
    a.label('out')
    a.emit(A.ldr(0, A.SP, MODE_SAVE_W0))
    a.emit(AC.ldp_off(19, 20, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, 0x30))
    a.emit(MODE_ORIG)                       # mov w19, w0 -- after the restore
    a.emit(A.b(a.pc(), MODE_RESUME))
    return a.resolve()


def build_world_stop_cave(cave, addr, scratch, rt=None):
    return _build_stop_cave(cave, addr, scratch, 24, WORLD_RESUME, rt)


def build_menu_stop_cave(cave, addr, scratch, rt=None):
    return _build_stop_cave(cave, addr, scratch, 22, MENU_RESUME, rt)


# ------------------------------------------------- fades and rotation (739)
# BUILD 739. FADES AND ROTATION, AS FFNx PLAYS THEM
# =================================================
# Cosmo gives every location `fade_in = 0.20` and `fade_out = 0.20`, and 34
# of them a three-loop `sequential` list. FFNx (NxAudioEngine::playAmbient,
# stopAmbient, pauseAmbient, resumeAmbient):
#
#   * a new loop starts at volume 0 and fades to full over fade_in;
#   * leaving for a mode that plays no ambience (FFNx's `default:` --
#     world map, menu, game over, ...) fades the loop out over fade_out and
#     then stops it;
#   * pausing a battle fades out over fade_out and THEN pauses; unpausing
#     resumes and fades back in over fade_in, from wherever the gain was;
#   * a loop replaced by another location's loop stops at once, and the new
#     one fades in;
#   * a `sequential` list rotates per location: each entry into that location
#     plays the next loop, wrapping.
#
# The time base is the port's own timeGetTime (gfx_drv_table index 0, ms),
# so a fade lasts its configured time at any frame rate.
#
# WHERE IT LIVES. The padding pool is spent by the end of a full build (the
# finished BUILD 738 module has 55 two-word holes left), so the runtime is in
# its own 1.25 KB dead-space part, 'amb' (ff7nx_deadspace). The pool caves
# keep their hooks and their logic and get SMALLER: the pool load and the
# flat 1.0 gain become one `bl` each.
#
# THE DEFERRED DISPOSE. A retired loop has to be disposed after its fade,
# from somewhere that runs in every mode. That is gfx_drv_flip, which
# ff7nx_daynight already hooks at +0x10DA884; this takes the next word,
# +0x10DA888 (`stp x22, x21, [sp, #0x10]`, position-independent), and the
# two chain the way analog-360 and the field hook do. Its fast path is four
# words. A retiring loop lives at most fade_out (200 ms) -- BUILD 506's
# game-over hang was a loop left running for the whole screen -- and any new
# loop disposes it first, so two players are never alive at once (the 32 MB
# wave pool, BUILD 523). `SEVENTH_NX_AMBIENT_FADE=0` puts every fade at zero,
# which is the pre-739 behaviour exactly: immediate pause, immediate dispose.
FADE_ENV = 'SEVENTH_NX_AMBIENT_FADE'
TIME_MS = 0x10ECB90                      # timeGetTime: w0 = milliseconds
FLIP_HOOK = 0x10DA888                    # gfx_drv_flip + 8, after daynight's
FLIP_ORIG = 0xA90157F6                   # stp x22, x21, [sp, #0x10]
FLIP_RESUME = FLIP_HOOK + 4
MAX_GROUP = 7                            # a group row is 8 u16: count + 7
GROUP_ROW = 16

# WHERE ITS STATE LIVES. Not in BSS. Echo-S (ff7nx_voice) needs its 0x990-byte
# block inside the BSS page the earlier passes end in -- an empirical rule from
# a hardware abort, build 325 -- and on the composed module that page has 16
# bytes to spare. The pre-739 block is 24 bytes and ends at +0x3FEF658,
# which Echo-S aligns up to +0x3FEF660. So the block
# grows by exactly those 8 alignment bytes, to hold one pointer, and Echo-S
# lands where it always has. The state itself (88 bytes plus a rotation cursor
# per location, about 1.8 KB) is allocated once, zeroed, from the game heap,
# by the flip tick on the first presented frame. Until then -- or if the
# allocation ever failed -- every helper does exactly what the pre-739 cave
# did: first loop, full gain, immediate pause, immediate dispose.
STATE_PTR = 24         # u64 in BSS: the heap state below, or 0 (+28 when
BSS_BYTES = 32         # the block starts 4 mod 8; see state_ptr_off)

# The heap state, BUILD 739.
FADE_PLAYER = 0        # u64  the player the fade-in below belongs to
FADE_T0 = 8            # u32  ms the fade-in started
FADE_G0 = 12           # f32  the gain it started from
LAST_GAIN = 16         # f32  the gain last applied to the live player
PAUSE_T0 = 20          # u32  ms the pause fade-out started
PAUSE_G0 = 24          # f32  the gain it started from
HARD_PAUSED = 28       # u32  OGG_PAUSE has actually been called
RETIRE_PLAYER = 32     # u64  a retired player still fading out, or 0
RETIRE_T0 = 40         # u32  ms its fade-out started
RETIRE_G0 = 44         # f32  the gain it started from
RETIRE_MS = 48         # u32  its fade-out length
CURSORS = 56           # u8[] rotation cursors, one per location in each span
STATE_BYTES = 56

EQ_, NE_, HS_, GT_ = 0, 1, 2, 12


def fades_enabled():
    return os.environ.get(FADE_ENV, '1').strip().lower() not in (
        '0', 'off', 'false', 'no')


def state_ptr_off(scratch):
    """Block offset of the state pointer: +24, or +28 to keep it 8-aligned."""
    if scratch % 4:
        raise ValueError('ambient BSS at +0x%X is not word-aligned' % scratch)
    return STATE_PTR + (-(scratch + STATE_PTR)) % 8


def scratch_bytes(field_table, battle_table, runtime=True, scratch=0):
    """BSS the ambient block needs: pre-739 24 bytes, or 32 with the pointer."""
    return state_ptr_off(scratch) + 8 if runtime else SCRATCH_BYTES


def state_bytes(field_table, battle_table):
    """Heap bytes of the 739 state: the fields plus the cursor rows."""
    n = STATE_BYTES
    for t in (field_table, battle_table):
        if t is not None and getattr(t, 'groups', None):
            n += t[2] - t[1] + 1
    return (n + 15) & ~15


def _state(a, dst, scratch):
    """X<dst> = the heap state pointer (0 until the first flip)."""
    at = scratch + state_ptr_off(scratch)
    a.emit(A.adrp(dst, a.pc(), at & ~0xFFF))
    a.emit(A.ldr64(dst, dst, at & 0xFFF))


def cursor_offsets(field_table, battle_table):
    """{mode: BSS offset of its cursor row}, for halves that rotate."""
    out, at = {}, CURSORS
    for mode, t in ((FIELD_MODE, field_table), (BATTLE_MODE, battle_table)):
        if t is not None and getattr(t, 'groups', None):
            out[mode] = at
            at += t[2] - t[1] + 1
    return out


def group_rows(table):
    """The group table: one 16-byte row per group, count then its loops."""
    out = bytearray()
    for group in table.groups:
        if not 2 <= len(group) <= MAX_GROUP:
            raise ValueError('ambient rotation of %d loops' % len(group))
        out += struct.pack('<8H', len(group), *(list(group)
                                                + [0] * (7 - len(group))))
    return bytes(out)


def _fade_ms(a, dst, which, ms, scratch):
    """W<dst> = this mode's fade (which: 0 in, 1 out). Uses W13, W14."""
    f, b = ms[FIELD_MODE][which], ms[BATTLE_MODE][which]
    if f == b:
        a.emit(A.movz(dst, f))
        return
    AC.bss_ptr(a, 13, scratch)
    a.emit(A.ldr(13, 13, 16))                # the mode, pre-739 block
    a.emit(A.movz(dst, f))
    a.emit(A.movz(14, b))
    a.emit(A.cmp_imm(13, BATTLE_MODE))
    a.emit(A.csel(dst, 14, dst, EQ_))


def _frame(a, size=0x30):
    a.emit(A.stp64_pre(29, 30, 31, -size))
    a.emit(AC.stp_off(19, 20, 0x10))
    a.emit(AC.stp_off(21, 22, 0x20))


def _unframe(a, size=0x30):
    a.emit(AC.ldp_off(21, 22, 0x20))
    a.emit(AC.ldp_off(19, 20, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, size))
    a.emit(A.ret())


def _ramp(a, t_reg, ms_reg):
    """S0 = W<t> / W<ms> as a float (callers have checked t < ms, ms > 0)."""
    a.emit(A.ucvtf_s(0, t_reg))
    a.emit(A.ucvtf_s(1, ms_reg))
    a.emit(A.fdiv_s(0, 0, 1))


def build_select_helper(addr, scratch, pool_va, npool, groups_va, lo,
                        cursor_off):
    """
    W21 (0-based index) -> W21 = the OGG id to play.  Leaf; uses x9..x14.

    Below `npool` it is the plain pool load the cave used to do inline. At or
    above it, it is group `index - npool`: take the loop this location's
    cursor names, then advance the cursor, wrapping -- FFNx's per-name
    `_ambientSequentialIndexes`.
    """
    a = Asm(addr(0), addr)
    a.emit(A.cmp_imm(21, npool))
    a.bcond('group', HS_)
    AC.bss_ptr(a, 9, pool_va)
    a.emit(AC.ldrh_uxtw_scaled(21, 9, 21))
    a.emit(A.ret())
    a.label('group')
    if groups_va is None:
        a.emit(A.movz(21, 0))
        a.emit(A.ret())
        return a.resolve()
    a.emit(A.sub_imm(10, 21, npool))
    AC.bss_ptr(a, 9, groups_va)
    a.emit(A.lsl(10, 10, 4))
    a.emit(AC.add_x_uxtw(9, 9, 10))          # this group's row
    a.emit(A.ldrh(10, 9, 0))                 # its loop count
    _state(a, 11, scratch)
    a.cbz64(11, 'first')
    a.emit(A.add_imm64(11, 11, cursor_off))
    a.emit(A.sub_imm(12, 22, lo))
    a.emit(AC.add_x_uxtw(11, 11, 12))        # this location's cursor
    a.emit(A.ldrb(13, 11, 0))
    a.emit(A.cmp_reg(13, 10))
    a.emit(A.csel(13, 13, 31, 3))            # LO: keep it, else wrap to 0
    a.emit(A.add_imm(14, 13, 1))
    a.emit(AC.ldrh_uxtw_scaled(21, 9, 14))   # row[1 + cursor]
    a.emit(A.cmp_reg(14, 10))
    a.emit(A.csel(14, 14, 31, 3))
    a.emit(A.strb(14, 11, 0))
    a.emit(A.ret())
    a.label('first')                         # no state yet: the first loop
    a.emit(A.ldrh(21, 9, 2))
    a.emit(A.ret())
    return a.resolve()


def build_gain_helper(addr, scratch, ms):
    """
    X0 = the live player.  Apply this frame's gain; finish a pause fade.

    Replaces the cave's flat `OGG_VOLUME(player, 1.0)`. A player it has not
    seen before starts a fade-in from 0. While the battle is paused it ramps
    the gain from where the pause found it to 0 over fade_out, and then makes
    the real OGG_PAUSE call, once.
    """
    a = Asm(addr(0), addr)
    _frame(a)
    a.emit(AC.mov64(20, 0))
    _state(a, 19, scratch)
    a.cbz64(19, 'full')
    a.emit(A.bl(a.pc(), TIME_MS))
    a.emit(A.mov_reg(21, 0))                 # now
    a.emit(A.ldr64(10, 19, FADE_PLAYER))
    a.emit(A.cmp_reg64(10, 20))
    a.bcond('seen', EQ_)
    a.emit(A.str64(20, 19, FADE_PLAYER))
    a.emit(A.str_(21, 19, FADE_T0))
    a.emit(A.str_(31, 19, FADE_G0))          # 0.0f
    a.label('seen')
    AC.bss_ptr(a, 10, scratch)
    a.emit(A.ldr(10, 10, PAUSE_OFF))
    a.cbz(10, 'playing')
    a.emit(A.ldr(10, 19, HARD_PAUSED))
    a.cbnz(10, 'out')
    _fade_ms(a, 11, 1, ms, scratch)
    a.cbz(11, 'hard_pause')
    a.emit(A.ldr(12, 19, PAUSE_T0))
    a.emit(A.sub_reg(12, 21, 12))
    a.emit(A.cmp_reg(12, 11))
    a.bcond('hard_pause', HS_)
    _ramp(a, 12, 11)
    a.emit(A.fmov_s_imm(1, 1.0))
    a.emit(A.fsub_s(0, 1, 0))
    a.emit(A.ldr_s(1, 19, PAUSE_G0))
    a.emit(A.fmul_s(0, 0, 1))
    a.b('apply')
    a.label('hard_pause')
    a.emit(A.movz(10, 1))
    a.emit(A.str_(10, 19, HARD_PAUSED))
    a.emit(A.str_(31, 19, LAST_GAIN))
    a.emit(A.fmov_s_from_w(0, 31))
    a.emit(AC.mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_VOLUME))
    a.emit(AC.mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_PAUSE))
    a.b('out')
    a.label('playing')
    _fade_ms(a, 11, 0, ms, scratch)
    a.cbz(11, 'full')
    a.emit(A.ldr(12, 19, FADE_T0))
    a.emit(A.sub_reg(12, 21, 12))
    a.emit(A.cmp_reg(12, 11))
    a.bcond('full', HS_)
    _ramp(a, 12, 11)
    a.emit(A.ldr_s(1, 19, FADE_G0))
    a.emit(A.fmov_s_imm(2, 1.0))
    a.emit(A.fsub_s(2, 2, 1))
    a.emit(A.fmul_s(0, 0, 2))
    a.emit(A.fadd_s(0, 0, 1))                # g0 + (1 - g0) * t / fade_in
    a.b('apply')
    a.label('full')
    a.emit(A.fmov_s_imm(0, 1.0))
    a.cbz64(19, 'volume')                    # no state: the pre-739 flat 1.0
    a.label('apply')
    a.emit(A.str_s(0, 19, LAST_GAIN))
    a.label('volume')
    a.emit(AC.mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_VOLUME))
    a.label('out')
    _unframe(a)
    return a.resolve()


def build_pause_helper(addr, scratch):
    """X0 = player. The battle just paused: start the fade-out, from here."""
    a = Asm(addr(0), addr)
    _frame(a)
    a.emit(AC.mov64(20, 0))
    _state(a, 19, scratch)
    a.cbz64(19, 'hard')
    a.emit(A.bl(a.pc(), TIME_MS))
    a.emit(A.str_(0, 19, PAUSE_T0))
    a.emit(A.ldr(9, 19, LAST_GAIN))
    a.emit(A.str_(9, 19, PAUSE_G0))
    a.b('out')
    a.label('hard')                          # no state: pause now, as pre-739
    a.emit(AC.mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_PAUSE))
    a.label('out')
    _unframe(a)
    return a.resolve()


def build_resume_helper(addr, scratch):
    """X0 = player. The battle unpaused: un-pause it, fade in from here."""
    a = Asm(addr(0), addr)
    _frame(a)
    a.emit(AC.mov64(20, 0))
    _state(a, 19, scratch)
    a.cbz64(19, 'hard')
    a.emit(A.ldr(9, 19, HARD_PAUSED))
    a.cbz(9, 'soft')
    a.emit(A.str_(31, 19, HARD_PAUSED))
    a.label('hard')
    a.emit(AC.mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_RESUME))
    a.cbz64(19, 'out')
    a.label('soft')
    a.emit(A.bl(a.pc(), TIME_MS))
    a.emit(A.str_(0, 19, FADE_T0))
    a.emit(A.str64(20, 19, FADE_PLAYER))
    a.emit(A.ldr(9, 19, LAST_GAIN))
    a.emit(A.str_(9, 19, FADE_G0))
    a.label('out')
    _unframe(a)
    return a.resolve()


def build_retire_helper(addr, scratch, ms):
    """
    X0 = the live player, which a stop cave is about to forget.

    With a fade-out it moves to the retire slot (disposing any older one
    first) and the flip tick fades and disposes it. With none, or if it was
    really paused, it is disposed here and now -- the pre-739 behaviour.
    """
    a = Asm(addr(0), addr)
    _frame(a)
    a.emit(AC.mov64(20, 0))
    _state(a, 19, scratch)
    a.cbz64(19, 'now')
    _fade_ms(a, 22, 1, ms, scratch)
    a.emit(A.ldr64(0, 19, RETIRE_PLAYER))
    a.cbz64(0, 'no_older')
    a.emit(A.str64(31, 19, RETIRE_PLAYER))
    a.emit(A.bl(a.pc(), OGG_DELETE))
    a.label('no_older')
    a.emit(A.ldr(9, 19, HARD_PAUSED))
    a.emit(A.str_(31, 19, HARD_PAUSED))
    a.emit(A.str64(31, 19, FADE_PLAYER))
    a.cbnz(9, 'now')
    a.cbz(22, 'now')
    a.emit(A.bl(a.pc(), TIME_MS))
    a.emit(A.str_(0, 19, RETIRE_T0))
    a.emit(A.ldr(9, 19, LAST_GAIN))
    a.emit(A.str_(9, 19, RETIRE_G0))
    a.emit(A.str_(22, 19, RETIRE_MS))
    a.emit(A.str64(20, 19, RETIRE_PLAYER))
    a.b('out')
    a.label('now')
    a.emit(AC.mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_DELETE))
    a.label('out')
    _unframe(a)
    return a.resolve()


def build_malloc_helper(addr, scratch):
    """
    W0 = size. Dispose a still-fading retired loop, then call MALLOC.

    The create path's `bl MALLOC`; a new player never coexists with an old
    one, and a new player is never paused.
    """
    a = Asm(addr(0), addr)
    a.emit(A.stp64_pre(29, 30, 31, -0x20))
    a.emit(AC.stp_off(19, 20, 0x10))
    a.emit(AC.mov64(20, 0))
    _state(a, 19, scratch)
    a.cbz64(19, 'go')
    a.emit(A.str_(31, 19, HARD_PAUSED))
    a.emit(A.ldr64(0, 19, RETIRE_PLAYER))
    a.cbz64(0, 'go')
    a.emit(A.str64(31, 19, RETIRE_PLAYER))
    a.emit(A.bl(a.pc(), OGG_DELETE))
    a.label('go')
    a.emit(AC.mov64(0, 20))
    a.emit(A.bl(a.pc(), MALLOC))
    a.emit(AC.ldp_off(19, 20, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, 0x20))
    a.emit(A.ret())
    return a.resolve()


FLIP_FRAME = 0xE0


def build_flip_cave(addr, scratch, heap_bytes):
    """
    gfx_drv_flip + 8, once a presented frame in every mode: fade a retired
    loop and dispose it when its fade-out is over. On the first frame it
    allocates and zeroes the heap state instead.

    Fast path, five words: x16/x17 (IP0/IP1, free at a prologue) find the
    state and its retire slot and fall straight through to the displaced
    word. The slow path keeps flip's arguments -- x0..x7 and q0..q7 --
    across the calls.
    """
    a = Asm(addr(0), addr)
    AC.bss_ptr(a, 16, scratch)
    a.emit(A.ldr64(17, 16, state_ptr_off(scratch)))
    a.cbz64(17, 'slow')
    a.emit(A.ldr64(17, 17, RETIRE_PLAYER))
    a.cbz64(17, 'replay')
    a.label('slow')
    a.emit(A.stp64_pre(29, 30, 31, -FLIP_FRAME))
    for i, r in enumerate((0, 2, 4, 6)):
        a.emit(AC.stp_off(r, r + 1, 0x10 + 0x10 * i))
    a.emit(AC.stp_off(19, 20, 0x50))
    for i, q in enumerate((0, 2, 4, 6)):
        a.emit(A.stp_q_off(q, q + 1, 31, 0x60 + 0x20 * i))
    a.emit(A.ldr64(19, 16, state_ptr_off(scratch)))
    a.cbz64(19, 'alloc')
    a.emit(A.ldr64(20, 19, RETIRE_PLAYER))
    a.emit(A.bl(a.pc(), TIME_MS))
    a.emit(A.ldr(9, 19, RETIRE_T0))
    a.emit(A.sub_reg(9, 0, 9))
    a.emit(A.ldr(10, 19, RETIRE_MS))
    a.emit(A.cmp_reg(9, 10))
    a.bcond('dispose', HS_)
    _ramp(a, 9, 10)
    a.emit(A.fmov_s_imm(1, 1.0))
    a.emit(A.fsub_s(0, 1, 0))
    a.emit(A.ldr_s(1, 19, RETIRE_G0))
    a.emit(A.fmul_s(0, 0, 1))
    a.emit(AC.mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_VOLUME))
    a.b('restore')
    a.label('dispose')
    a.emit(A.str64(31, 19, RETIRE_PLAYER))
    a.emit(AC.mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_DELETE))
    a.b('restore')
    a.label('alloc')                         # first frame: the heap state
    a.emit(AC.mov64(20, 16))
    a.emit(A.movz(0, heap_bytes))
    a.emit(A.bl(a.pc(), MALLOC))
    a.cbz64(0, 'restore')                    # retried next frame
    a.emit(AC.mov64(9, 0))
    a.emit(A.movz(10, heap_bytes))
    a.label('zero')
    a.emit(A.str64(31, 9, 0))
    a.emit(A.add_imm64(9, 9, 8))
    a.emit(A.sub_imm(10, 10, 8))
    a.cbnz(10, 'zero')
    a.emit(A.str64(0, 20, state_ptr_off(scratch)))
    a.label('restore')
    for i, q in enumerate((0, 2, 4, 6)):
        a.emit(A.ldp_q_off(q, q + 1, 31, 0x60 + 0x20 * i))
    a.emit(AC.ldp_off(19, 20, 0x50))
    for i, r in enumerate((0, 2, 4, 6)):
        a.emit(AC.ldp_off(r, r + 1, 0x10 + 0x10 * i))
    a.emit(A.ldp64_post(29, 30, 31, FLIP_FRAME))
    a.label('replay')
    a.emit(FLIP_ORIG)                        # stp x22, x21, [sp, #0x10]
    a.emit(A.b(a.pc(), FLIP_RESUME))
    return a.resolve()


def fade_ms(field_table, battle_table):
    """{mode: (fade_in_ms, fade_out_ms)}, zero everywhere when disabled."""
    out = {}
    for mode, t in ((FIELD_MODE, field_table), (BATTLE_MODE, battle_table)):
        if t is None or not fades_enabled():
            out[mode] = (0, 0)
        else:
            out[mode] = (min(getattr(t, 'fade_in_ms', 0), 0xFFFF),
                         min(getattr(t, 'fade_out_ms', 0), 0xFFFF))
    return out


def layout_runtime(pool, scratch, field_table, battle_table):
    """
    Lay the 739 runtime into `pool` (a ff7nx_deadspace.Bump). Returns `rt`,
    {helper: entry}; each half's 'select_*' stays (table, groups VA, cursor
    offset) until finish_select places it.
    """
    ms = fade_ms(field_table, battle_table)
    cursors = cursor_offsets(field_table, battle_table)
    rt = {}
    for mode, t, key in ((FIELD_MODE, field_table, 'select_field'),
                         (BATTLE_MODE, battle_table, 'select_battle')):
        if t is None:
            continue
        groups_va = pool.put_bytes(group_rows(t)) if t.groups else None
        rt[key] = (t, groups_va, cursors.get(mode))
    rt['gain'] = pool.put(lambda e, ad: build_gain_helper(ad, scratch, ms))
    rt['pause'] = pool.put(lambda e, ad: build_pause_helper(ad, scratch))
    rt['resume'] = pool.put(lambda e, ad: build_resume_helper(ad, scratch))
    rt['retire'] = pool.put(lambda e, ad: build_retire_helper(ad, scratch, ms))
    rt['malloc'] = pool.put(lambda e, ad: build_malloc_helper(ad, scratch))
    heap = state_bytes(field_table, battle_table)
    rt['flip'] = pool.put(lambda e, ad: build_flip_cave(ad, scratch, heap))
    return rt


def install_runtime(src, text, scratch, field_table, battle_table, stock=None):
    """
    The 739 runtime in the 'amb' dead-space part of `src`.

    Returns (rt, pool, report); `pool.placed` is {va: word} to write.
    """
    import ff7nx_deadspace as DS
    lo, hi = DS.part(src, 'amb', stock)
    pool = DS.Bump(lo, hi)
    rt = layout_runtime(pool, scratch, field_table, battle_table)
    report = {'amb_part': (lo, hi),
              'fade_ms': fade_ms(field_table, battle_table)}
    return rt, pool, report


def finish_select(rt, pool, scratch, key, pool_va):
    """Emit one half's select helper, now that its pool VA is known."""
    t, groups_va, cur = rt[key]
    rt[key] = pool.put(lambda e, ad: build_select_helper(
        ad, scratch, pool_va, len(t[3]), groups_va, t[1], cur or 0))
    return rt[key]


# -------------------------------------------------------------------- apply
def apply_to_nso(src, dest, field_table, battle_table=None, space=None,
                 stock=None, runtime=True):
    """
    Install the ambient runtime, patching `src` into `dest`.

    Either table may be None. With the shipping `FA=1, BA=0` option set only
    the field map exists, and the battle cave is simply not installed -- the
    world and menu stop caves still are, because a field loop still has to be
    disposed when you leave.
    """
    if field_table is None and battle_table is None:
        return None
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw, own = ff7nx_tables.open_module(blob)
    space = own if space is None else space
    text = space.text

    scratch = AC.scratch_base(blob, segs)
    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))
    placed = {}
    report = {'bss_bytes': SCRATCH_BYTES, 'scratch': scratch,
              'table_bytes': 0, 'field': None, 'battle': None,
              'short': [], 'runtime': None}

    # BUILD 739: the fade and rotation runtime, in its own dead-space part.
    # If that part cannot be had, everything below is exactly pre-739: one
    # loop per location (each rotation's first) and no fades.
    rt = amb = None
    if runtime:
        try:
            rt, amb, info = install_runtime(src, text, scratch, field_table,
                                            battle_table, stock)
            report['runtime'] = info
        except Exception as exc:                               # noqa: BLE001
            report['runtime_error'] = '%s: %s' % (type(exc).__name__, exc)
            rt = amb = None
    if rt is None:
        field_table, battle_table = flatten(field_table), flatten(battle_table)
    bss_bytes = scratch_bytes(field_table, battle_table, rt is not None,
                              scratch)
    report['bss_bytes'] = bss_bytes

    # The full maps use 860 tail bytes for block pointers + OGG pools; their
    # 1,718 one-byte index values live in safe 12-byte padding holes.
    halves = []
    if field_table:
        halves.append(('field', field_table, FIELD_HOOK, FIELD_ORIG,
                       'ambient field tick', build_field_cave))
    if battle_table:
        halves.append(('battle', battle_table, BATTLE_HOOK, BATTLE_ORIG,
                       'ambient battle entry', build_battle_cave))

    sites = []
    for name, table, hook, orig, what, builder in halves:
        blob_t, lo, hi, oggs = table
        AC.expect_word(text, hook, orig, what)
        blocks = index_blocks(table)
        pointers_size = 4 * len(blocks)
        pool_blob = struct.pack('<%dH' % len(oggs), *oggs)
        # Reserve tail space before consuming padding.  A map that cannot fit
        # leaves the other half intact, as before.
        tail_before = (bytes(space.text), bytes(space.rodata),
                       list(space.placed))
        holes_before = (list(pool.free), list(pool.used))
        try:
            pointer_va = space.place('ambient %s block pointers' % name,
                                     b'\0' * pointers_size, align=4)
            pool_va = space.place('ambient %s OGG pool' % name, pool_blob,
                                  align=2)
            block_vas = _take_index_blocks(pool, text, blocks)
        except (ff7nx_tables.NoSpace, ff7nx_cave.NoRoom) as exc:
            # A rejected optional half must leave no dead tail allocation or
            # consumed padding behind for the remaining bridges.
            space.text[:] = tail_before[0]
            space.rodata[:] = tail_before[1]
            space.placed[:] = tail_before[2]
            pool.free[:], pool.used[:] = holes_before
            report['short'].append((name, len(blob_t), str(exc)))
            continue
        _tail_write(space, pointer_va,
                    struct.pack('<%dI' % len(block_vas), *block_vas))
        if rt is not None:
            finish_select(rt, amb, scratch, 'select_' + name, pool_va)
        entry, words = ff7nx_cave.emit_laid_out(
            pool, lambda cave, addr, b=builder, p=pointer_va, q=pool_va,
            l=lo, h=hi: b(cave, addr, scratch, p, q, l, h, rt=rt))
        placed.update(words)
        placed[hook] = A.b(hook, entry)
        sites.append((hook, orig, what))
        used = pointers_size + len(pool_blob)
        report[name] = {'entry': entry, 'pointer_va': pointer_va,
                        'pool_va': pool_va, 'table_bytes': used,
                        'locations': hi - lo + 1,
                        'oggs': len(oggs)}
        report['table_bytes'] += used

    # Neither half fitted: install nothing at all rather than leaving the two
    # stop caves hooked to state no dispatcher ever writes.
    if not sites:
        return None

    for hook, orig, what in ((WORLD_HOOK, WORLD_ORIG, 'ambient world stop'),
                             (MENU_HOOK, MENU_ORIG, 'ambient menu stop'),
                             (MODE_HOOK, MODE_ORIG, 'ambient mode stop')):
        AC.expect_word(text, hook, orig, what)
        sites.append((hook, orig, what))

    for builder, hook, key in ((build_world_stop_cave, WORLD_HOOK, 'world'),
                               (build_menu_stop_cave, MENU_HOOK, 'menu'),
                               (build_mode_stop_cave, MODE_HOOK, 'mode')):
        entry, words = ff7nx_cave.emit_laid_out(
            pool, lambda cave, addr, b=builder: b(cave, addr, scratch, rt=rt))
        placed.update(words)
        placed[hook] = A.b(hook, entry)
        report[key + '_entry'] = entry

    if rt is not None:
        AC.expect_word(text, FLIP_HOOK, FLIP_ORIG, 'ambient flip tick')
        sites.append((FLIP_HOOK, FLIP_ORIG, 'ambient flip tick'))
        placed.update(amb.placed)
        placed[FLIP_HOOK] = A.b(FLIP_HOOK, rt['flip'])
        report['runtime']['amb_used'] = amb.at - amb.lo
        report['runtime']['entries'] = {k: v for k, v in rt.items()
                                        if isinstance(v, int)}
        report['runtime']['groups'] = {
            name: len(t.groups) for name, t in (('field', field_table),
                                                ('battle', battle_table))
            if t is not None}

    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)

    _set_safe_music_thread_name(space)

    out = AC.pack(blob, space.commit(), bss_bytes)
    check_segs, check_raw = AC.segments(out)
    for va, _want, what in sites:
        if struct.unpack_from('<I', check_raw[0], va)[0] & 0xFC000000 \
                != 0x14000000:
            raise ValueError('%s hook did not survive the repack' % what)
    old_bss, = struct.unpack_from('<I', blob, 0x3C)
    if struct.unpack_from('<I', out, 0x3C)[0] != old_bss + bss_bytes:
        raise ValueError('BSS did not grow by the ambient state block')

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    report['cave_words'] = len(placed) - len(sites) - (
        len(amb.placed) if rt is not None else 0)
    return report
