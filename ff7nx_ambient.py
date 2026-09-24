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
"""
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
    def choice(meta):
        values = meta.get('sequential') or meta.get('shuffle') or ()
        return int(values[0]) if values else None

    fields = {int(key[6:]): choice(meta)
              for key, meta in config.items()
              if key.startswith('field_') and key.count('_') == 1
              and choice(meta) is not None}
    battles = {int(key[4:]): choice(meta)
               for key, meta in config.items()
               if key.startswith('bat_') and choice(meta) is not None}
    if not fields and not battles:
        raise ValueError('the ambient configuration has no exact field or '
                         'battle mappings')

    def make_table(values, what):
        if not values:
            return None
        lo, hi = min(values), max(values)
        oggs = sorted(set(values.values()))
        if len(oggs) > 255:
            raise ValueError('too many %s OGG ids for a one-byte map index'
                             % what)
        index = {ogg: i + 1 for i, ogg in enumerate(oggs)}
        table = bytearray(hi - lo + 1)
        for location, ogg in values.items():
            table[location - lo] = index[ogg]
        table.extend(struct.pack('<%dH' % len(oggs), *oggs))
        return bytes(table), lo, hi, oggs

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
RATE_ENV = 'SEVENTH_NX_AMBIENT_RATE'     # `48k` converts; default stages as shipped
CONVERT_RECIPE = 'AMBIENT-48K-V2 ar=48000 exact-length vorbis-q6 LOOPSTART=0'
CONVERT_QUALITY = '6'                    # ~192 kbps stereo; the sources run
                                         # 128-190 kbps, so this loses nothing
CONVERTED = []                           # (ogg id, source rate) this build


def convert_enabled():
    return os.environ.get(RATE_ENV, '').strip().lower() in ('48k', '48000')


def _rate_channels(path):
    import voice_ogg
    with open(path, 'rb') as handle:
        head = handle.read(1 << 16)
    return voice_ogg._identification(head)


def _cache_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, 'cache', '_ambient_ogg')


def _converted_copy(source):
    """A 48 kHz Vorbis copy of `source` in the build cache, made once.

    Keyed on the source's CONTENT, the recipe and the encoder, so an unchanged
    loop is never re-encoded and a changed one always is.
    """
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
    cache = _cache_dir()
    os.makedirs(cache, exist_ok=True)
    out = os.path.join(cache, digest.hexdigest()[:24] + '.ogg')
    if os.path.exists(out):
        try:
            if _rate_channels(out)[0] == MAX_RATE:
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
    want = int(round(voice_ogg._last_granule(data) * MAX_RATE
                     / float(src_rate)))
    handle, tmp = tempfile.mkstemp(prefix='.amb.', suffix='.ogg', dir=cache)
    os.close(handle)
    try:
        base = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
                '-y', '-i', source, '-af',
                'aresample=%d,atrim=end_sample=%d' % (MAX_RATE, want)]
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
        rate, _channels = _rate_channels(tmp)
        if rate != MAX_RATE:
            raise ValueError('ambient: %s came out at %d Hz' % (source, rate))
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
        if convert_enabled():
            try:
                rate = _rate_channels(source)[0]
            except Exception:                                 # noqa: BLE001
                rate = 0                  # unreadable header: ship as-is
            if rate > MAX_RATE:
                source = _converted_copy(source)
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


def _emit_swap(a, preflight=False):
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
    a.emit(A.bl(a.pc(), MALLOC))
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
    span = hi - lo + 1
    index = blob[:span]
    values = struct.unpack('<%dH' % len(oggs), blob[span:])
    if list(values) != list(oggs):
        raise ValueError('ambient table pool does not match its OGG list')
    out = {}
    for offset, choice in enumerate(index):
        if choice:
            if choice > len(values):
                raise ValueError('ambient table index %d is outside its pool'
                                 % choice)
            out[lo + offset] = values[choice - 1]
    return out


def _emit_lookup(a, pointer_va, pool_va, lo, hi, mode):
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
    AC.bss_ptr(a, 23, pool_va)
    a.emit(AC.ldrh_uxtw_scaled(21, 23, 21))
    a.b('change')
    a.label('no_track')
    a.emit(A.movz(21, 0))


def build_field_cave(cave, addr, scratch, pointer_va, pool_va, lo, hi):
    """Exact field-id dispatcher, on one independent OGG player."""
    a = Asm(cave, addr)
    _prologue(a, scratch, save_pair=True)
    AC.mov32(a, 0, FIELD_ID_GUEST)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrh(22, 0, 0))
    _emit_lookup(a, pointer_va, pool_va, lo, hi, FIELD_MODE)
    _emit_swap(a, preflight=True)
    a.label('out')
    _epilogue(a, save_pair=True)
    a.emit(FIELD_ORIG)                      # ldr w22, [x25] -- not PC-relative
    a.emit(A.b(a.pc(), FIELD_RESUME))
    return a.resolve()


def _emit_battle_pause(a):
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
    a.emit(A.bl(a.pc(), OGG_PAUSE))
    a.b('pause_done')
    a.label('pause_resume')
    a.emit(A.bl(a.pc(), OGG_RESUME))
    a.label('pause_done')


def build_battle_cave(cave, addr, scratch, pointer_va, pool_va, lo, hi):
    """Exact `bat_<id>` dispatcher, sharing the field player's state."""
    a = Asm(cave, addr)
    _prologue(a, scratch, save_pair=False)
    _emit_battle_pause(a)
    AC.mov32(a, 0, BATTLE_ID_GUEST)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrh(22, 0, 0))
    _emit_lookup(a, pointer_va, pool_va, lo, hi, BATTLE_MODE)
    _emit_swap(a)
    a.label('out')
    _epilogue(a, save_pair=False)
    # NOT the raw BATTLE_ORIG word: an ADRP's page delta is relative to its
    # own PC, so replaying the stock encoding from a padding hole would point
    # x28 at a completely different page.
    a.emit(A.adrp(28, a.pc(), ADRP_PAGE))
    a.emit(A.b(a.pc(), BATTLE_RESUME))
    return a.resolve()


def _build_stop_cave(cave, addr, scratch, reg, resume):
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
    a.emit(A.bl(a.pc(), OGG_DELETE))
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


def build_mode_stop_cave(cave, addr, scratch):
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
    a.emit(A.bl(a.pc(), OGG_DELETE))
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


def build_world_stop_cave(cave, addr, scratch):
    return _build_stop_cave(cave, addr, scratch, 24, WORLD_RESUME)


def build_menu_stop_cave(cave, addr, scratch):
    return _build_stop_cave(cave, addr, scratch, 22, MENU_RESUME)


# -------------------------------------------------------------------- apply
def apply_to_nso(src, dest, field_table, battle_table=None, space=None):
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
              'short': []}

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
        entry, words = ff7nx_cave.emit_laid_out(
            pool, lambda cave, addr, b=builder, p=pointer_va, q=pool_va,
            l=lo, h=hi: b(cave, addr, scratch, p, q, l, h))
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
            pool, lambda cave, addr, b=builder: b(cave, addr, scratch))
        placed.update(words)
        placed[hook] = A.b(hook, entry)
        report[key + '_entry'] = entry

    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)

    _set_safe_music_thread_name(space)

    out = AC.pack(blob, space.commit(), SCRATCH_BYTES)
    check_segs, check_raw = AC.segments(out)
    for va, _want, what in sites:
        if struct.unpack_from('<I', check_raw[0], va)[0] & 0xFC000000 \
                != 0x14000000:
            raise ValueError('%s hook did not survive the repack' % what)
    old_bss, = struct.unpack_from('<I', blob, 0x3C)
    if struct.unpack_from('<I', out, 0x3C)[0] != old_bss + SCRATCH_BYTES:
        raise ValueError('BSS did not grow by the ambient state block')

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    report['cave_words'] = len(placed) - len(sites)
    return report
