"""
echo_s_battletext.py -- Echo-S's in-battle conversations, matched to the
lines they belong to.

WHAT THIS IS FOR
================
The first boss says "Don't attack while his tail is up" and one of the party
warns you about the laser. Those are battle DIALOGUE, and they are a third
voice system, separate from field lines and from battle barks:

    field lines   <field>/<dialog><page>        addressed by script state
    battle barks  battle/bc<char><cmd><action>  addressed by the action
    battle text   battle/bd<hash>               addressed by the LINE ITSELF

`ff7nx_voice._build_battle_text_command_cave` reads the string the battle text
queue is displaying and hashes it -- FNV-1a over the raw encoded bytes, the
0xFF terminator included -- because reimplementing FFNx's text expansion and
tokenizer in injected ARM64 was not worth it. So staging has to produce the
same hash from the same bytes, which means reading them out of `scene.bin`.

WHY THE CLIP NAMES ARE THE INDEX
================================
FFNx names a battle dialogue clip after the line: `_battle/enemy_<formation
id:04X>/<slug>.ogg`, where the slug is `tokenize_text(decode_ff7_text(...))`
-- lower-cased, `[a-z0-9]` kept, space to `_`, `{}` kept, everything else
dropped (`FFNx/src/voice.cpp`). Echo-S ships 32 of them.

So the match runs in that direction: decode every 0xFF-terminated run in every
scene, slug it, and see whether Echo-S shipped a clip by that name. A slug is
a whole sentence, so a false match is not a practical concern.

THE SPEAKER-NAME PREFIX
=======================
Three clips are named for a speaker the text does not contain:

    cloud_barret_be_careful      "!, be careful!"
    cloud_its_gonna_fire_that_laser
    barret_i_dunno_whats_goin_on_but

Their lines begin with byte 0xEA plus a three-byte operand -- a character-name
variable. FFNx's `decode_ff7_text` has no case for 0xEA (its escape table runs
0xEB..0xF0), so it falls through to `char + 0x20`, which wraps to a newline,
and the tokenizer drops it. `cloud_barret_be_careful` slugs as `_____be_careful`:
the name Echo-S put in the filename is a value the decoder never produces.

`suffix_match` closes that. The scene slug must be a proper WORD suffix of the
clip name, and the clip's extra words must be exactly the part the decoder
lost -- so it only fires where the scene slug still carries the underscores
that stood in for the dropped variable. Two words and eight characters
minimum, and the tail must name exactly one clip, so `careful` alone can never
pair anything.

None of this reaches the hash. The hash is FNV-1a over the raw bytes and does
not care how they decode; the match only decides which clip is linked to which
line.

THE ONE THING THAT CANNOT BE READ OFFLINE
=========================================
The strings live INLINE in the scene's AI script, not in a pool with an offset
table:

    93 02 24 4F 4E 07 54 00 41 54 54 41 43 4B ...
    ^  ^  D  o  n  '  t     a  t  t  a  c  k
    |  the string's first character, an opening quote
    the AI byte in front of it

The engine copies those strings into a RAM pool at scene load and the cave
hashes from the pool pointer. Whether that pointer includes the leading AI
byte is not observable from the file, and both spellings slug identically --
`chr(0xB3)` is dropped by the tokenizer either way.

Rather than guess, every start position whose slug matches the clip is staged.
They are hard links to one encode, the wrong ones are never opened, and it
comes to about 150 names for 29 clips. Guessing would have been one name and
a silent line.
"""
import os
import struct
import zlib


BLOCK = 0x2000
# 16 scene pointers per block, each a byte offset divided by four; unused
# slots are 0xFFFFFFFF. This is the PORT's container -- the PC game packs four
# LZS scenes per block, and this one packs sixteen GZIP ones. Reading it as
# the PC's gives four garbage "scenes" per block that decompress to nonsense.
POINTERS = 16
UNUSED = 0xFFFFFFFF
GZIP_WBITS = 16 + zlib.MAX_WBITS
SCENE_TEXT_LIMIT = 0x400          # the cave gives up at this length too

# `FFNx/src/voice.cpp:decode_ff7_text`. Every one of these consumes three more
# bytes after itself; 0xF8 consumes two and emits nothing.
ESCAPES = {
    0xEB: '{item_name}', 0xEC: '{number}', 0xED: '{target_name}',
    0xEE: '{attack_name}', 0xEF: '{special_number}', 0xF0: '{target_letter}',
}
SKIP_TWO = 0xF8
TERMINATOR = 0xFF

FNV_BASIS = 0x811C9DC5
FNV_PRIME = 0x01000193


def scenes(path):
    """(scene id, decompressed bytes) for every scene in a scene.bin."""
    with open(path, 'rb') as handle:
        data = handle.read()
    out = []
    for index in range(len(data) // BLOCK):
        block = data[index * BLOCK:(index + 1) * BLOCK]
        pointers = [p * 4 for p in
                    struct.unpack_from('<%dI' % POINTERS, block, 0)
                    if p != UNUSED]
        for slot, start in enumerate(pointers):
            end = (pointers[slot + 1] if slot + 1 < len(pointers) else BLOCK)
            try:
                scene = zlib.decompressobj(GZIP_WBITS).decompress(
                    block[start:end])
            except zlib.error:
                continue
            out.append((index * POINTERS + slot, scene))
    return out


def decode(buffer, start, limit=SCENE_TEXT_LIMIT):
    """
    (text, one past the terminator), or (None, None) if it does not terminate.

    Deliberately byte-for-byte FFNx's decoder, including that NOTHING but
    0xFF ends a run: a byte outside the printable range becomes a character
    the tokenizer will drop, not a reason to stop. Stopping early was worth
    26 of the 29 matches.
    """
    out = []
    cursor = start
    while cursor < len(buffer) and cursor - start < limit:
        char = buffer[cursor]
        cursor += 1
        if char == TERMINATOR:
            return ''.join(out), cursor
        if char in ESCAPES:
            out.append(ESCAPES[char])
            cursor += 3
        elif char == SKIP_TWO:
            cursor += 2
        else:
            out.append(chr((char + 0x20) & 0xFF))
    return None, None


def tokenize(text):
    """FFNx's `tokenize_text`: what the clip on disk is named."""
    out = []
    for char in text.lower():
        if char.isascii() and (char.isalpha() or char.isdigit()):
            out.append(char)
        elif char == ' ':
            out.append('_')
        elif char in '{}':
            out.append(char)
    return ''.join(out)


def fnv1a(data):
    """The cave's hash, over the raw bytes INCLUDING the 0xFF terminator."""
    value = FNV_BASIS
    for byte in data:
        value = ((value ^ byte) * FNV_PRIME) & 0xFFFFFFFF
    return value


def key_filename(digest):
    """The name `_build_battle_text_command_cave` asks the player for."""
    return 'battle/bd%08x.ogg' % digest


def pack_key(digest):
    return (0xB2 << 56) | digest


def filename(key):
    return key_filename(key & 0xFFFFFFFF)


def is_silent_set(rel_path):
    """
    True for Echo-S's `Battle VO_S` tree -- the SILENT twin.

    Echo-S ships two `_battle` trees with identical filenames, one of them
    filled with silence, and a `<Conditional>` picks between them on PC. This
    port cannot evaluate those, so both arrive. Choosing by whichever the
    directory walk reached first is a coin toss, and it has now lost twice:
    once in `echo_s_battlevo`, and again here, where it picked the silent tree
    and matched nothing because that tree holds only barks.
    """
    head = rel_path.replace('\\', '/').split('/')[0].strip().lower()
    return head.endswith('_s')


def shipped_clips(files):
    """
    {name: path} for every `_battle/<actor>/<name>.ogg` Echo-S ships.

    Takes the build's own (relative, absolute) file list rather than a root
    directory, because there is no single root: the clips are spread across
    `VO/Voice`, `Battle VO` and the silent `Battle VO_S`, and which one a
    given name should come from is a decision, not a walk order.

    Barks come along too. They are harmless -- a bark's name is `<action>_<n>`
    and will never equal a sentence slug -- and keeping them means this does
    not need to know which naming scheme a clip belongs to.
    """
    found = {}
    for rel, full in sorted(files):
        rel = rel.replace('\\', '/')
        parts = rel.split('/')
        if len(parts) < 3 or not parts[-1].lower().endswith('.ogg'):
            continue
        actor = parts[-2].lower()
        if not (actor.startswith('enemy_') or actor.startswith('char_')):
            continue
        if '_battle' not in [p.lower() for p in parts[:-1]]:
            continue
        name = parts[-1][:-4]
        if name not in found or (found[name][1] and not is_silent_set(rel)):
            found[name] = (full, is_silent_set(rel))
    return {name: full for name, (full, _silent) in found.items()}


# A tail has to be at least this many words and characters before it is
# allowed to name a clip on its own. `be_careful` is the shortest real one.
TAIL_MIN_WORDS = 2
TAIL_MIN_CHARS = 8


def speaker_tails(clips):
    """
    {word suffix: clip name} for clips whose name carries a speaker prefix.

    `cloud_barret_be_careful` offers `barret_be_careful` and `be_careful`; the
    one-word `careful` is below the floor and is never offered. A tail claimed
    by two clips is dropped rather than guessed at.
    """
    tails = {}
    for name in clips:
        if looks_like_a_bark(name):
            continue
        words = name.split('_')
        for cut in range(1, len(words) - TAIL_MIN_WORDS + 1):
            tail = '_'.join(words[cut:])
            if len(tail) < TAIL_MIN_CHARS:
                continue
            tails[tail] = None if tail in tails else name
    return {tail: name for tail, name in tails.items() if name}


def suffix_match(slug, tails):
    """
    The clip a scene slug names through its speaker prefix, or None.

    Requires leading underscores: those are what the dropped 0xEA variable and
    its operand decoded to. A slug with no such prefix is an ordinary line, and
    if it did not match a clip exactly it does not match one at all.
    """
    tail = slug.lstrip('_')
    if tail == slug or not tail:
        return None
    return tails.get(tail)


def looks_like_a_bark(name):
    """`bolt_3`, `help_10` -- Echo-S's battle BARK naming, not a line."""
    stem, _, tail = name.rpartition('_')
    return bool(stem) and tail.isdigit()


def entries(scene_bin, clips, entry_factory, log=lambda *_: None):
    """
    Staging entries for every scene string a shipped clip names.

    Returns (entries, matched slugs, unmatched slugs). One clip yields several
    entries -- see the module note on start positions.
    """
    if not clips:
        return [], set(), set()
    made = []
    seen = {}
    matched = set()
    tails = speaker_tails(clips)
    pending = []
    for scene_id, scene in scenes(scene_bin):
        for start in range(len(scene)):
            text, end = decode(scene, start)
            if not text or len(text) < 3:
                continue
            slug = tokenize(text)
            if slug in clips:
                digest = fnv1a(scene[start:end])
                if digest in seen:
                    continue
                seen[digest] = slug
                matched.add(slug)
                made.append(entry_factory(pack_key(digest), clips[slug]))
                continue
            name = suffix_match(slug, tails)
            if name is not None:
                pending.append((name, fnv1a(scene[start:end])))

    # A leading underscore is also what a start position part-way into a
    # sentence looks like, so the suffix rule cannot tell a dropped speaker
    # variable from a truncation of a line that matched perfectly well on its
    # own. Only clips the exact pass could not place at all are rescued -- for
    # anything it did place, its own start positions are the right ones.
    rescued = set()
    for name, digest in pending:
        if name in matched or digest in seen:
            continue
        seen[digest] = name
        rescued.add(name)
        made.append(entry_factory(pack_key(digest), clips[name]))
    matched |= rescued

    unmatched = set(clips) - matched
    if rescued:
        log('battle text: %d clip(s) matched through a speaker prefix the '
            'text does not carry: %s' % (len(rescued), ', '.join(sorted(rescued))))
    log('battle text: %d line(s) matched to %d clip(s), %d clip(s) with no '
        'line in this scene set' % (len(made), len(matched), len(unmatched)))
    return made, matched, unmatched
