"""
echo_s_battlevo.py -- Echo-S's battle barks, from the index that actually
addresses them.

WHY THIS IS NOT `voicemod.ingest_battle_vo_from_files`
======================================================
That ingester reads `Battle VO/Voice/_battle/char_NN/<action>_<n>.ogg` and
turns the STEM into an action id through `ACTION_NAME_TO_ID`. It is an earlier
design, and the runtime that shipped superseded it: measured on the real
release it resolves 548 of a set's 1,079 clips, because half the stems are not
action names at all. `generic`, `help`, `limit`, `summon`, `restore`, `sense`,
`stole` -- those are POOL names, and a pool serves many different actions.

The runtime does not ask for a stem. `_build_battle_action_command_cave`
builds FFNx's own key out of live battle state:

    battle/bc<char:02x><cmd:02x><action:04x>      a party member's action
    battle/be<formation:04x><cmd:02x><action:04x>  an enemy's
    battle/bc<char:02x><cmd:02x>ffff              command-only fallback

and those are exactly the section names in Echo-S's `voice/config.toml`:

    [_battle-char_00-cmd_02_0000]                     <- Cloud, Magic, Cure
    shuffle = [ "_battle/char_00/restore_1", ... ]

So the config file IS the index, the same way the directory layout is the
index for field voice. This module reads it.

WHAT THE `prob\\N` FOLDERS ARE, AND WHY WE READ THE 100 ONE
===========================================================
Echo-S ships five copies of this config, one per `BVoice` setting, and the
difference between them is not which lines exist -- it is how many `silent_*`
files are padded into each shuffle list. At `prob\\25` a fifteen-clip pool is
padded to sixty, so FFNx's random pick lands on a real clip a quarter of the
time.

The cave does not need that trick: it has a deterministic 100-step counter and
the selected percentage baked in as an immediate, which is both exact and free
of forty-five silent files per pool. So this reads `prob\\100` -- the only copy
whose lists are the real pools -- and the percentage is passed to the patcher
instead.

VARIANTS
========
Pools hold 1 to 21 clips, median 9. The cave's key has room for one variant
digit, and it selects with `counter & 7`, so each key is staged in exactly
`VARIANTS` slots. A pool smaller than that repeats from the beginning; a pool
larger than that uses its first `VARIANTS`. Every slot is a hard link into the
same encode cache, so 812 keys x 8 slots is still only the ~900 distinct clips
on disk.

Fixing the slot count in the build rather than making the cave read a
per-key pool size is deliberate: it keeps the ARM64 side to one extra nibble
and one AND, and it means a missing slot -- which on hardware is silence with
no diagnostic -- cannot happen.

WHAT THIS DELIBERATELY DOES NOT TAKE
====================================
* `_battle-char_NN-cmd_CC_stole` / `_couldnt` / `_nothing` (54 sections).
  Command RESULT text, not an action. `_build_battle_text_command_cave` keys
  those by raw-text hash at a different hook.
* `_battle-enemy_NN-cmd_...` (22 sections). The `be` form needs a formation
  id, and Echo-S voices only a handful; they can follow once the party path
  has run on hardware.
"""
import os
import re


# One variant digit in the key, selected by `counter & 7` in the cave.
VARIANTS = 8

# `[_battle-char_00-cmd_02_0000]` and the command-only `[_battle-char_00-cmd_05]`.
# Actor and command are hex, not decimal: `char_0A` is slot ten.
_SECTION = re.compile(
    r'^_battle-char_([0-9A-Fa-f]{2})-cmd_([0-9A-Fa-f]{2})'
    r'(?:_([0-9A-Fa-f]{4}))?$')
_BLOCK = re.compile(r'^\[([^\]]+)\]\s*\n(.*?)(?=^\[|\Z)', re.M | re.S)
_QUOTED = re.compile(r'"([^"]+)"')

# The probability padding. Never staged: the cave's counter replaces it.
SILENT = '/silent/'

CONFIG_REL = os.path.join('prob', '100', 'voice', 'config.toml')


def key_filename(char_slot, command, action, variant):
    """
    The exact name the cave will ask the native player for.

    The actor and the command each get their own directory level. 853 keys x
    8 variants is 6,728 files, and putting them all in one directory made
    every bark pay for a lookup in something twenty times larger than the
    biggest field voice directory -- on the frame the action fires, on the
    main thread, through LayeredFS. Two levels bring it to a 112-file median,
    under what field voice already does without a hitch.
    `ff7nx_voice._emit_battle_actor_separator` writes the matching slashes,
    and `tests/test_battle_vo.py` asserts their offsets against the
    instructions the cave actually emits.
    """
    return 'battle/bc%02x/%02x/%04x%x.ogg' % (char_slot, command, action,
                                              variant)


def pack_key(char_slot, command, action, variant):
    """A stable identity for `voice_ogg.stage`'s duplicate check."""
    return ((0xB1 << 56) | (char_slot << 40) | (command << 32) |
            (action << 16) | variant)


def unpack_key(key):
    return ((key >> 40) & 0xFF, (key >> 32) & 0xFF, (key >> 16) & 0xFFFF,
            key & 0xFF)


def filename(key):
    """`voice_ogg.stage`'s `filename=` callable for these keys."""
    return key_filename(*unpack_key(key))


def parse_config(text):
    """
    {(char_slot, command, action): [clip stem, ...]} from a voice config.

    `action` is 0xFFFF for a command-only section, which is the fallback key
    the cave falls back to when the exact one is not staged. Silent padding is
    dropped. Sections this port does not serve are skipped silently -- the
    caller logs the counts.
    """
    pools = {}
    for name, body in _BLOCK.findall(text):
        match = _SECTION.match(name.strip())
        if not match:
            continue
        char_slot = int(match.group(1), 16)
        command = int(match.group(2), 16)
        action = int(match.group(3), 16) if match.group(3) else 0xFFFF
        clips = [c for c in _QUOTED.findall(body) if SILENT not in c]
        if clips:
            pools[(char_slot, command, action)] = clips
    return pools


def config_path(mod_root):
    path = os.path.join(mod_root, CONFIG_REL)
    return path if os.path.isfile(path) else None


def entries(pools, sources, entry_factory, log=lambda *_: None):
    """
    One entry per (key, variant), or a reason it could not be made.

    `sources` maps a config clip stem (`_battle/char_00/restore_1`) to a file
    on disk. Returns (entries, missing) where `missing` names the stems the
    config referenced and the mod did not ship -- 44 of them on the release,
    all Vincent transformation limits, which are simply absent.
    """
    made, missing = [], set()
    for (char_slot, command, action), clips in sorted(pools.items()):
        usable = [c for c in clips if c in sources]
        for absent in (c for c in clips if c not in sources):
            missing.add(absent)
        if not usable:
            continue
        for variant in range(VARIANTS):
            clip = usable[variant % len(usable)]
            made.append(entry_factory(
                pack_key(char_slot, command, action, variant),
                sources[clip]))
    log('battle VO: %d key(s) over %d variant(s) -> %d staged name(s), '
        '%d referenced clip(s) the mod does not ship'
        % (len(pools), VARIANTS, len(made), len(missing)))
    return made, sorted(missing)


def is_silent_set(rel_path):
    """
    True for Echo-S's `Battle VO_S` tree.

    `_S` IS FOR SILENT, AND THIS IS THE WHOLE REASON THIS FUNCTION EXISTS.
    Echo-S ships two trees with identical filenames. `Battle VO` is the
    performance: 1,221 clips, 59.9 MB, median 31.6 KB. `Battle VO_S` is the
    same names filled with silence: median 4.3 KB, the exact size of
    `silent/silent_1.ogg`. On PC a `<Conditional>` with a RuntimeVar decides
    between them, and this port cannot evaluate RuntimeVars, so both arrive.

    Picking by whichever the directory walk reached first is therefore a coin
    toss between working battle voice and a build where every bark plays
    silence -- with nothing in the log to say so, because a 4 KB Ogg encodes,
    verifies and stages exactly like a real one. Prefer the performance
    explicitly, always.
    """
    head = rel_path.replace('\\', '/').split('/')[0].strip().lower()
    return head.endswith('_s')


def sources_from_files(files):
    """
    {`_battle/char_00/restore_1`: disk path} from build.py's file list.

    The config names clips the way FFNx resolves them -- under the voice root,
    without an extension -- so this rebuilds that spelling from whatever
    folder the clip actually arrived in, rather than assuming one. Sorted, so
    two builds over the same mod pick the same file.
    """
    found = {}
    for rel, full in sorted(files):
        rel = rel.replace('\\', '/')
        parts = rel.split('/')
        if len(parts) < 3 or not parts[-1].lower().endswith('.ogg'):
            continue
        actor, stem = parts[-2], parts[-1][:-4]
        if not (actor.startswith('char_') or actor.startswith('enemy_')):
            continue
        name = '_battle/%s/%s' % (actor, stem)
        if name not in found or (found[name][1] and not is_silent_set(rel)):
            found[name] = (full, is_silent_set(rel))
    return {name: full for name, (full, _silent) in found.items()}
