"""
ff7nx_echooverride.py -- the rest of EchoeS of the Planet: FFNx's per-scene
cues, per-battle themes and shuffled variants. BUILD 899.

WHAT ECHO-S SHIPS THAT A PLAIN TRACK COPY CANNOT PLAY
=====================================================
Echo-S's Music option (EchoeS of the Planet) is mostly ordinary track
replacements, which BUILD 897 copies into data/music_ogg. FFNx also looks up
three kinds of name the game itself never asks for (FFNx music.cpp):

  field_<field>_<n>.ogg   in field mode, when field <field> plays its n'th
                          music entry (the MUSIC/CMUSC "relative id")
  bat_<battle id>.ogg     when battle <battle id> starts its music
  config.toml shuffle     `[bat] shuffle = ['bat', 'bat1']`: each time the
                          track is started, one of the list at random

Echo-S ships five scene cues, five battle themes and three shuffle groups.
Without this pass the scene and battle play the generic track and the
shuffle always picks the first.

HOW THE PORT IS MADE TO PLAY THEM
=================================
Every track the game starts reaches PLAY_NATIVE(id, fade) (0x10EC490), the
host side of play_midi, which asks the guest for the track's NAME and calls
MusicPlayer::request(player, name, id, fade). The game itself refuses ids
above 0x62 (sound_operation's range check), so new tracks cannot be pushed
through the guest; they are swapped in at PLAY_NATIVE instead, exactly as
ff7nx_titlemusic plays `main_menu`: the guest keeps its own vanilla id and
state, and the player is asked for a different NAME on a player slot no game
track uses (99..126; 127 is the title track).

The cave at PLAY_NATIVE's entry decides, in FFNx's order:

  1. battle music (a flag set only around the main loop's battle-music call,
     x86 0x4093E6 -> ARM 0x3ACE8, FFNx's `play_battle_music_call`), and the
     battle id at 0xCC0D8A (`modules_global_object->battle_id`) has a theme;
  2. field mode (_mode 0xCBF9DC == 1), the loaded field (the loader's own
     name buffer 0xCC1EF0, hashed exactly as the Echo-S voice runtime hashes
     it) and the requested id match a cue. The cue's id is resolved at BUILD
     time from that field's own AKAO table -- id = AKAO[+4] - 1, which is
     what field_music_helper (x86 0x613BA3) computes -- so the runtime
     compares (field, id) rather than hooking the relative-id lookup;
  3. the id is a shuffle group's base track: the variant already playing is
     kept (FFNx does not restart a track that is already on), otherwise one
     entry is drawn with the hardware counter.

Anything else runs the stock PLAY_NATIVE untouched.

NAMES ARE ALIASES
=================
NativeOggPlayer puts the requested name into a Horizon thread title,
`music stream thread '%s'`, whose buffer aborts on a long name (the v61
abort, see ff7nx_voice). `field_canon_2_1` would overflow it, so every
override is copied to music_ogg under a four-character alias, `eo00`..
`eo27`, and the cave writes that alias into its own BSS before requesting.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os
import re
import struct

import a64 as A
import ff7nx_cave
import ff7nx_audio_cave as AC
import nxmap

# ------------------------------------------------------------- module sites
PLAY_NATIVE = 0x10EC490
PLAY_NATIVE_ORIG = 0xF81D0FF5          # str x21, [sp, #-0x30]!
PLAY_RESUME = PLAY_NATIVE + 4
REQUEST = 0x2B40                       # MusicPlayer::request(this, name, id, fade)
REQUEST_ORIG = 0xA9BD57F6              # stp x22, x21, [sp, #-0x30]!
PLAYER_PTR_PAGE = 0x12CE000
PLAYER_PTR_OFF = 0x238                 # [[0x12CE238]] -> the MusicPlayer
CURRENT_OFF = 0x420                    # MusicPlayer: current id
GUEST_TRANSLATE = AC.GUEST_TRANSLATE

# main_loop (x86 0x4090E6) +0x300: sound_operation(0x14, [0xCC0DCC], ...),
# the battle-music start FFNx wraps as ff7_battle_music.
BATTLE_MUSIC_CALL = 0x3ACE8
SOUND_OPERATION = 0xE50210

MRS_CNTPCT = 0xD53BE020                # mrs Xt, cntpct_el0
RET = 0xD65F03C0

# ------------------------------------------------------------- guest state
GAME_MODE = 0xCBF9DC                   # FF7 _mode (u16)
MODE_FIELD = 1
FIELD_FILE_NAME = 0xCC1EF0             # the field loader's name buffer
BATTLE_ID_GUEST = 0xCC0D8A             # modules_global_object->battle_id
MIDI_NAME_TABLE = 0x9684C8             # ff7_en: 99 char* NONE..ROLL
MAX_MIDI_ID = 0x62                     # sound_operation's own range check
FIELD_NAME_HASH_MULTIPLIER = 93        # = voicemod / ff7nx_voice

# ------------------------------------------------------------- allocation
SLOT_FIRST = 99
SLOT_LAST = 126                        # 127 is ff7nx_titlemusic's
ALIAS_FORMAT = 'eo%02d'

BSS_FLAG = 0x00                        # u32: inside the battle-music call
BSS_NAMES = 0x10                       # 8 bytes per alias, NUL-padded
NAME_STRIDE = 8

FIELD_RE = re.compile(r'field_([a-z0-9_]+)_(\d+)\.ogg')
BATTLE_RE = re.compile(r'bat_(\d+)\.ogg')
SHUFFLE_SECTION = re.compile(r'^\s*\[\s*([A-Za-z0-9_\-]+)\s*\]\s*$')
SHUFFLE_LINE = re.compile(r'^\s*shuffle\s*=\s*\[(.*)\]\s*$')


def field_name_hash(name):
    """The Echo-S voice runtime's 16-bit field key (voicemod.field_name_hash)."""
    value = 0
    for ch in name.lower().encode('ascii'):
        value = (value * FIELD_NAME_HASH_MULTIPLIER + ch) & 0xFFFF
    return value


def parse_shuffle(text):
    """{base: [names]} from FFNx's music config.toml (only `shuffle`)."""
    out, section = {}, None
    for line in text.splitlines():
        line = line.split('#', 1)[0]
        m = SHUFFLE_SECTION.match(line)
        if m:
            section = m.group(1).lower()
            continue
        m = SHUFFLE_LINE.match(line)
        if m and section:
            names = [n.strip().strip('\'"').lower()
                     for n in m.group(1).split(',')]
            names = [n[:-4] if n.endswith('.ogg') else n for n in names if n]
            if names:
                out[section] = names
    return out


def is_override_name(low, shuffle_variants=()):
    """True for a file only FFNx's lookups (or a shuffle) would play."""
    return bool(FIELD_RE.fullmatch(low) or BATTLE_RE.fullmatch(low)
                or os.path.splitext(low)[0] in shuffle_variants)


def midi_names(exe_bytes, va_to_offset):
    """The game's own id -> name list (index 0 is NONE), or None."""
    try:
        base = va_to_offset(MIDI_NAME_TABLE)
        out = []
        for k in range(MAX_MIDI_ID + 1):
            ptr = struct.unpack_from('<I', exe_bytes, base + 4 * k)[0]
            off = va_to_offset(ptr)
            out.append(exe_bytes[off:exe_bytes.index(b'\0', off)]
                       .decode('latin-1').lower())
    except Exception:                                       # noqa: BLE001
        return None
    if out[0] != 'none' or out[1] != 'oa' or out[MAX_MIDI_ID] != 'roll':
        return None
    return out


def akao_ids(section1):
    """The field's music list: [midi id, ...] by relative id, as
    field_music_helper computes it (AKAO header byte 4, minus one)."""
    n_ent = section1[2]
    n_akao = struct.unpack_from('<H', section1, 6)[0]
    base = 0x20 + 8 * n_ent
    out = []
    for k in range(n_akao):
        off = struct.unpack_from('<I', section1, base + 4 * k)[0]
        if section1[off:off + 4] == b'AKAO':
            out.append(section1[off + 4] - 1)
        else:
            out.append(None)
    return out


class Entry(object):
    def __init__(self, kind, slot, alias, src, source_name, **key):
        self.kind, self.slot, self.alias = kind, slot, alias
        self.src, self.source_name = src, source_name
        self.__dict__.update(key)


def plan(extra, shuffle, field_akao, names, music_files=None,
         log=lambda *_: None):
    """
    Decide every override. Returns [Entry].

    extra        {lower name.ogg: src} -- Echo-S's FFNx-only files
    shuffle      {base: [names]} from its config.toml
    field_akao   callable(field) -> [midi id by relative id] or None
    names        midi_names() list (id -> name)
    music_files  {lower name.ogg: src} already going to music_ogg, so a
                 shuffle entry that is a plain track can still be named
    """
    music_files = music_files or {}
    entries, slot = [], SLOT_FIRST
    name_to_id = {n: i for i, n in enumerate(names) if i}

    def take(kind, src, source_name, **key):
        nonlocal slot
        if slot > SLOT_LAST:
            log('  ! Echo-S music: no player slot left for %s' % source_name)
            return None
        e = Entry(kind, slot, ALIAS_FORMAT % (slot - SLOT_FIRST), src,
                  source_name, **key)
        slot += 1
        entries.append(e)
        return e

    for low in sorted(extra):
        m = FIELD_RE.fullmatch(low)
        if not m:
            continue
        field, rel = m.group(1), int(m.group(2))
        ids = field_akao(field)
        if ids is None:
            log('  Echo-S music: %s -- no field %s in this build, skipped'
                % (low, field))
            continue
        if rel >= len(ids) or ids[rel] is None or not (
                1 <= ids[rel] <= MAX_MIDI_ID):
            log('  Echo-S music: %s -- %s has no music entry %d, skipped'
                % (low, field, rel))
            continue
        mid = ids[rel]
        if ids.count(mid) > 1:
            log('  Echo-S music: %s -- note: %s lists track %s more than '
                'once; every entry of it is replaced' % (low, field,
                                                        names[mid]))
        take('field', extra[low], low, field=field, rel=rel, midi=mid,
             hash=field_name_hash(field))

    for low in sorted(extra):
        m = BATTLE_RE.fullmatch(low)
        if m:
            bid = int(m.group(1))
            if bid > 0xFFFF:
                continue
            take('battle', extra[low], low, battle=bid)

    for base in sorted(shuffle):
        if base not in name_to_id:
            log('  Echo-S music: shuffle [%s] -- not a game track, skipped'
                % base)
            continue
        options = []
        for n in shuffle[base]:
            if n == base:
                options.append(None)                 # the stock track
                continue
            src = extra.get(n + '.ogg') or music_files.get(n + '.ogg')
            if src is None:
                log('  Echo-S music: shuffle [%s] -- %s.ogg is not shipped, '
                    'left out' % (base, n))
                continue
            e = take('shuffle', src, n + '.ogg', base=base)
            if e is not None:
                options.append(e)
        if len(options) > 1 and any(o is not None for o in options):
            for o in options:
                if o is not None:
                    o.midi = name_to_id[base]
                    o.options = options
        else:
            for o in options:
                if o is not None:
                    entries.remove(o)
    return entries


# ------------------------------------------------------------------ caves
def _player(a):
    a.emit(A.adrp(0, a.pc(), PLAYER_PTR_PAGE))
    a.emit(A.ldr64(0, 0, PLAYER_PTR_OFF))
    a.emit(A.ldr64(0, 0, 0))


def _alias_word(alias):
    raw = alias.encode('ascii')
    if len(raw) != 4:
        raise ValueError('alias %r must be four characters' % alias)
    return struct.unpack('<I', raw)[0]


def _emit_field_hash(a):
    """x0 = host pointer to the loader's name buffer -> w24 = 16-bit key.
    The same normalisation as ff7nx_voice: last backslash within 31 bytes,
    then at most eight characters, stopping at NUL or '.'."""
    a.emit(AC.mov64(11, 0))
    a.emit(A.movz(9, 0))
    a.label('h_sep')
    a.emit(A.ldrb(8, 0, 0))
    a.cbz(8, 'h_sep_done')
    a.emit(A.cmp_imm(8, ord('\\')))
    a.bcond('h_sep_next', A.NE)
    a.emit(A.add_imm64(11, 0, 1))
    a.label('h_sep_next')
    a.emit(A.add_imm64(0, 0, 1))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.cmp_imm(9, 31))
    a.bcond('h_sep', A.LT)
    a.label('h_sep_done')
    a.emit(AC.mov64(0, 11))
    a.emit(A.movz(24, 0))
    a.emit(A.movz(12, FIELD_NAME_HASH_MULTIPLIER))
    a.emit(A.movz(9, 0))
    a.label('h_char')
    a.emit(A.ldrb(8, 0, 0))
    a.cbz(8, 'h_done')
    a.emit(A.cmp_imm(8, ord('.')))
    a.bcond('h_done', A.EQ)
    a.emit(A.mul(24, 24, 12))
    a.emit(A.add_reg(24, 24, 8))
    a.emit(A.add_imm64(0, 0, 1))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.cmp_imm(9, 8))
    a.bcond('h_char', A.LT)
    a.label('h_done')
    a.emit(A.and_mask(24, 24, 16))


# The PLAY_NATIVE logic is SEVERAL small caves joined by unconditional `b`
# (+-128 MB, no window needed), not one big one. A cave with internal
# conditional branches has to sit inside one +-1 MB window, and with the
# full mod set the widest window left in the module holds ~260 words --
# a single 449-word cave did not fit there, and later passes need room too.
#
# Register contract across all of them (set by the entry cave, which has
# already run save_host): w20 = id, w21 = fade, x22 = our BSS,
# w25 = the slot to play (set right before branching to the play cave).
#
# Each builder takes `at`, the addresses of caves already placed; they are
# placed exits-first so every target is known.

def _far(a, cond, target):
    """b.<cond> to an address in ANOTHER cave: a local skip plus a `b`."""
    inv = cond ^ 1
    skip = 'far_%d' % len(a.words)
    a.bcond(skip, inv)
    a.emit(A.b(a.pc(), target))
    a.label(skip)


def _to_play(a, slot, at):
    a.emit(A.movz(25, slot))
    a.emit(A.b(a.pc(), at['play']))


def build_stock(resume=PLAY_RESUME):
    """Stock PLAY_NATIVE with the arguments as they came in."""
    def build(cave, address):
        a = AC.Asm(cave, address)
        a.emit(A.mov_reg(0, 20))
        a.emit(A.mov_reg(1, 21))
        AC.restore_host(a)
        a.emit(PLAY_NATIVE_ORIG)
        a.emit(A.b(a.pc(), resume))
        return a.resolve()
    return build


def build_play_slot():
    """request(player, "eoNN", w25, fade); returns its result to
    PLAY_NATIVE's caller, as PLAY_NATIVE's own tail call does. The alias
    is derived from the slot, so one routine serves every override."""
    def build(cave, address):
        a = AC.Asm(cave, address)
        a.emit(A.sub_imm(9, 25, SLOT_FIRST))          # w9 = alias index
        a.emit(A.movz(10, 10))
        a.emit(AC.udiv(11, 9, 10))                    # tens
        a.emit(AC.msub(12, 11, 10, 9))                # ones
        a.emit(A.add_imm(11, 11, ord('0')))
        a.emit(A.add_imm(12, 12, ord('0')))
        a.emit(A.movz(13, ord('e') | (ord('o') << 8)))
        a.emit(A.orr_lsl(13, 13, 11, 16))
        a.emit(A.orr_lsl(13, 13, 12, 24))             # "eoNN", little-endian
        a.emit(A.add_imm64(1, 22, BSS_NAMES))
        a.emit(A.add_reg64_lsl(1, 1, 9, 3))           # 8 bytes per alias
        a.emit(A.str_(13, 1, 0))                      # byte 4 is BSS zero
        a.emit(A.mov_reg64(19, 1))
        _player(a)
        a.emit(A.mov_reg64(1, 19))
        a.emit(A.mov_reg(2, 25))
        a.emit(A.mov_reg(3, 21))
        a.emit(A.bl(a.pc(), REQUEST))
        AC.restore_host(a)
        a.emit(RET)
        return a.resolve()
    return build


def build_group(options, nxt, at):
    """One shuffle group: keep a variant already on, else draw one."""
    mid = next(o.midi for o in options if o is not None)
    slots = [o.slot for o in options if o is not None]

    def build(cave, address):
        a = AC.Asm(cave, address)
        a.emit(A.cmp_imm(20, mid))
        _far(a, A.NE, nxt)
        _player(a)
        a.emit(A.ldr(8, 0, CURRENT_OFF))
        for s in slots:                               # a variant is on
            a.emit(A.cmp_imm(8, s))
            a.bcond('keep_%d' % s, A.EQ)
        a.emit(A.cmp_reg(8, 20))                      # the stock one is on
        _far(a, A.EQ, at['stock'])
        a.emit(MRS_CNTPCT | 9)
        a.emit(A.lsr(9, 9, 4))
        a.emit(A.movz(10, len(options)))
        a.emit(AC.udiv(11, 9, 10))
        a.emit(AC.msub(9, 11, 10, 9))                 # w9 = counter % n
        for i, o in enumerate(options):
            if o is None:
                a.emit(A.cmp_imm(9, i))
                _far(a, A.EQ, at['stock'])
            else:
                a.emit(A.cmp_imm(9, i))
                a.bcond('keep_%d' % o.slot, A.EQ)
        a.emit(A.b(a.pc(), at['stock']))
        for s in slots:
            a.label('keep_%d' % s)
            _to_play(a, s, at)
        return a.resolve()
    return build


def build_field(field, nxt, at):
    """A field's own cue: field mode, the loaded map and the track."""
    def build(cave, address):
        a = AC.Asm(cave, address)
        for mid in sorted({e.midi for e in field}):
            a.emit(A.cmp_imm(20, mid))
            a.bcond('candidate', A.EQ)
        a.emit(A.b(a.pc(), nxt))
        a.label('candidate')
        AC.guest_ptr(a, GAME_MODE)
        a.emit(A.ldrh(8, 0, 0))
        a.emit(A.cmp_imm(8, MODE_FIELD))
        _far(a, A.NE, nxt)
        AC.guest_ptr(a, FIELD_FILE_NAME)
        _emit_field_hash(a)
        for k, e in enumerate(field):
            AC.mov32(a, 9, e.hash)
            a.emit(A.cmp_reg(24, 9))
            a.bcond('next_%d' % k, A.NE)
            a.emit(A.cmp_imm(20, e.midi))
            a.bcond('hit_%d' % k, A.EQ)
            a.label('next_%d' % k)
        a.emit(A.b(a.pc(), nxt))
        for k, e in enumerate(field):
            a.label('hit_%d' % k)
            _to_play(a, e.slot, at)
        return a.resolve()
    return build


def build_battle(battle, nxt, at):
    """Inside the battle-music call: the battle's own theme."""
    def build(cave, address):
        a = AC.Asm(cave, address)
        AC.guest_ptr(a, BATTLE_ID_GUEST)
        a.emit(A.ldrh(23, 0, 0))
        for k, e in enumerate(battle):
            AC.mov32(a, 9, e.battle)
            a.emit(A.cmp_reg(23, 9))
            a.bcond('hit_%d' % k, A.EQ)
        a.emit(A.b(a.pc(), nxt))
        for k, e in enumerate(battle):
            a.label('hit_%d' % k)
            _to_play(a, e.slot, at)
        return a.resolve()
    return build


def build_entry(bss, battle_target, field_target):
    """PLAY_NATIVE's entry (w0 = id, w1 = fade)."""
    def build(cave, address):
        a = AC.Asm(cave, address)
        AC.save_host(a)
        a.emit(A.mov_reg(20, 0))
        a.emit(A.mov_reg(21, 1))
        AC.bss_ptr(a, 22, bss)
        a.emit(A.ldr(8, 22, BSS_FLAG))
        a.cbz(8, 'field')
        a.emit(A.b(a.pc(), battle_target))            # battle-music call
        a.label('field')
        a.emit(A.b(a.pc(), field_target))
        return a.resolve()
    return build


def emit_play_caves(pool, entries, bss):
    """Lay out every PLAY_NATIVE cave. Returns (entry address, words)."""
    battle = [e for e in entries if e.kind == 'battle']
    field = [e for e in entries if e.kind == 'field']
    groups = []
    for e in entries:
        if e.kind == 'shuffle' and not any(e.options is g for g in groups):
            groups.append(e.options)
    at, words = {}, {}

    def place(name, builder):
        addr, placed = ff7nx_cave.emit_laid_out(pool, builder)
        words.update(placed)
        at[name] = addr
        return addr

    place('stock', build_stock())
    place('play', build_play_slot())
    nxt = at['stock']
    for g in reversed(range(len(groups))):
        nxt = place('group_%d' % g, build_group(groups[g], nxt, at))
    shuffle = nxt
    field_at = (place('field', build_field(field, shuffle, at))
                if field else shuffle)
    battle_at = (place('battle', build_battle(battle, shuffle, at))
                 if battle else shuffle)
    entry = place('entry', build_entry(bss, battle_at, field_at))
    return entry, words


def build_battle_call(bss):
    """Wrapper for the main loop's battle-music sound_operation call: the
    flag is up for exactly that call. Only x16/x17 (IP0/IP1) are used, so
    every argument register reaches sound_operation as the game set it."""
    def build(cave, address):
        a = AC.Asm(cave, address)
        a.emit(A.stp64_pre(29, 30, 31, -0x10))
        AC.bss_ptr(a, 16, bss)
        a.emit(A.movz(17, 1))
        a.emit(A.str_(17, 16, BSS_FLAG))
        a.emit(A.bl(a.pc(), SOUND_OPERATION))
        AC.bss_ptr(a, 16, bss)
        a.emit(A.str_(31, 16, BSS_FLAG))              # wzr
        a.emit(A.ldp64_post(29, 30, 31, 0x10))
        a.emit(RET)
        return a.resolve()
    return build


def bss_bytes(entries):
    n = (BSS_NAMES + NAME_STRIDE * (SLOT_LAST - SLOT_FIRST + 1))
    return (n + 15) & ~15


def apply_to_nso(src, dest, entries):
    """Install the PLAY_NATIVE cave (and, with battle themes, the battle
    wrapper). Refuses, writing nothing, if any site is not stock."""
    if not entries:
        raise ValueError('no overrides to install')
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    AC.expect_word(text, PLAY_NATIVE, PLAY_NATIVE_ORIG, 'PLAY_NATIVE entry')
    AC.expect_word(text, REQUEST, REQUEST_ORIG, 'MusicPlayer::request')
    battle = any(e.kind == 'battle' for e in entries)
    if battle:
        AC.expect_word(text, BATTLE_MUSIC_CALL,
                       A.bl(BATTLE_MUSIC_CALL, SOUND_OPERATION),
                       'main loop battle-music call')
    bss = AC.scratch_base(blob, segs)
    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))
    out = {}
    entry, placed = emit_play_caves(pool, entries, bss)
    placed[PLAY_NATIVE] = A.b(PLAY_NATIVE, entry)
    out.update(placed)
    if battle:
        entry2, placed2 = ff7nx_cave.emit_laid_out(pool,
                                                   build_battle_call(bss))
        placed2[BATTLE_MUSIC_CALL] = A.bl(BATTLE_MUSIC_CALL, entry2)
        out.update(placed2)
    for where, word in out.items():
        struct.pack_into('<I', text, where, word)
    raw[0] = bytes(text)
    growth = bss_bytes(entries) + AC.bss_tail_slack(segs)
    packed = AC.pack(blob, raw, growth)
    check_segs, check_raw = AC.segments(packed)
    assert check_segs[0][2] == segs[0][2]
    assert struct.unpack_from('<I', check_raw[0], PLAY_NATIVE)[0] == \
        A.b(PLAY_NATIVE, entry)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(packed)
    return {'main': dest, 'words': len(out), 'bss': bss,
            'bss_bytes': growth, 'battle_hook': battle}
