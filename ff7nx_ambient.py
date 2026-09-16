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

ADRP_PAGE = 0x12CE000                   # the page all three ADRPs name

# ------------------------------------------------------------ port routines
MALLOC = 0x1150ED0
OGG_CTOR = 0x2EB0                       # (NativeOggPlayer *, char *name)
OGG_PLAY = 0x36D0                       # (NativeOggPlayer *, bool loopish)
OGG_VOLUME = 0x3810                     # (NativeOggPlayer *, float gain)
OGG_DELETE = 0x3310                     # stop, dispose and free
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

SCRATCH_BYTES = 24
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
    for ogg_id in sorted(set(ogg_ids)):
        source = oggs.get(str(ogg_id))
        if source is None:
            missing.append(ogg_id)
            continue
        target = os.path.join(dest_dir, '%04d.ogg' % ogg_id)
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


def _emit_swap(a):
    """Shared tail: retire the old loop, start the selected one, hold gain."""
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
    _emit_swap(a)
    a.label('out')
    _epilogue(a, save_pair=True)
    a.emit(FIELD_ORIG)                      # ldr w22, [x25] -- not PC-relative
    a.emit(A.b(a.pc(), FIELD_RESUME))
    return a.resolve()


def build_battle_cave(cave, addr, scratch, pointer_va, pool_va, lo, hi):
    """Exact `bat_<id>` dispatcher, sharing the field player's state."""
    a = Asm(cave, addr)
    _prologue(a, scratch, save_pair=False)
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
    a.label('out')
    a.emit(AC.ldp_off(19, 20, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, 0x30))
    a.emit(A.adrp(reg, a.pc(), ADRP_PAGE))  # re-encoded, as above
    a.emit(A.b(a.pc(), resume))
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
                             (MENU_HOOK, MENU_ORIG, 'ambient menu stop')):
        AC.expect_word(text, hook, orig, what)
        sites.append((hook, orig, what))

    for builder, hook, key in ((build_world_stop_cave, WORLD_HOOK, 'world'),
                               (build_menu_stop_cave, MENU_HOOK, 'menu')):
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
