#!/usr/bin/env python3
"""
sfxmod.py -- applying an FFNx sound mod by rebuilding audio.fmt / audio.dat.

THE PROBLEM
===========
Cosmo Memory and Echo-S are built for FFNx's external sound path: loose
`.ogg` files under `sfx/`, addressed by an id, with a `config.toml` mapping
the game's sound numbers onto them. The Switch port has no such path. Every
`.ogg` string in `exefs/main` points at `data/music_ogg`; there is no
external SFX loader, no ambient layer, no voice layer.

It does have `audio.fmt` / `audio.dat`, the 750-slot sound archive the PC
game has always used. So the mod can be applied the long way round: decode
its .ogg, re-encode as the 4-bit MS ADPCM the archive holds, and rebuild the
pair with those samples in the slots `config.toml` names.

That is what this module does, and it is why the earlier conclusion --
"Cosmo Memory cannot work on Switch at all, <1% of the mod's IDs are in
range" -- was wrong. It compared the mod's OGG FILENAMES against the game's
sound ids. Those are different numbering schemes. `config.toml` is the map
between them, and through it the mod covers 720 of the 750 slots.

WHAT config.toml LOOKS LIKE
===========================
    [6]
    sequential = [ 6303 ]

    [563]
    sequential = [ 2108, 2109, 2110, 2111, 2112 ]

The section is the GAME's sound id (1..750, the slot in audio.fmt). The list
is OGG ids -- filenames under `sfx/`. Several means FFNx plays them in turn so
a repeated effect does not sound identical each time.

THE PORT DOES ROTATE NOW, and that is a correction to what this file used to
say. `audio.fmt` is still exactly 750 records -- that has never been
negotiable -- but the first variant goes in the ordinary slot and the rest are
appended after the archive in `audio.dat`, where `ff7nx_sfxshuffle`'s cave
selects one at the shared loader entry. `prepare_rotations()` below encodes
those extra payloads; `rotation_order()` decides which variant is the one that
lives in the real slot. `SEVENTH_NX_SFX_PICK=last` still picks the other end
of the list, and now rotates the whole list from there rather than reducing it
to a single OGG.

Sections whose name is not a number (`battle_char_01_16`,
`battle_enemy_0080_35`) are FFNx's per-actor overrides, parsed by
`parse_battle_overrides()`. They are NOT resolved here: a logical sound id has
already lost its per-action identity by the time the archive loader sees it,
which is what made every loader-time attempt at them leak one actor's sound
into another's. `ff7nx_sfxbattle` resolves them at the native player, before
its cache check, where the selected physical slot becomes both the cache key
and the loader id. The plain numeric mapping still applies underneath any
override the build does not enable.

LAYERING
========
A sound mod ships several folders that all contribute to `sfx/`: Cosmo
Memory has Base, plus UI_Dissidia / UI_R / UI_VII for the three menu sets and
V_Attacks / NV_Attacks for voiced or silent attacks. The .ogg pool is the
union of every ACTIVE folder, later folders winning, and configs merge the
same way per id. That is how 7th Heaven layers them and it falls out of
handing this module the files in application order.

OTHER AUDIO LAYERS
==================
This module owns only the fixed SFX archive.  The build's separate native
bridges handle Ambient loops, Echo-S voice, and the selected movie/tutorial
routes; keeping those streams out of this archive prevents a location loop or
dialogue lifetime from becoming a global sound-effect lifetime.
"""
import os
import re
import struct

import audio_dat

CONFIG_NAME = 'config.toml'
SFX_DIR = 'sfx'
PICK_ENV = 'SEVENTH_NX_SFX_PICK'
LOOPS_ENV = 'SEVENTH_NX_SFX_LOOPS'

_SECTION = re.compile(r'^\s*\[([^\]]+)\]\s*$')
_SEQUENTIAL = re.compile(r'^\s*sequential\s*=\s*\[([^\]]*)\]')
_BATTLE_OVERRIDE = re.compile(
    r'^battle_(char|enemy)_([0-9a-fA-F]+)_([0-9]+)$')


def parse_config(text):
    """
    {game sound id: [ogg id, ...]} from a Cosmo-style config.toml.

    Hand-rolled rather than via a TOML library: the file is 270 KB of two
    shapes, the project has no TOML dependency, and Python's own tomllib is
    3.11+ while this has to run wherever the GUI does.
    """
    out = {}
    current = None
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            name = m.group(1).strip()
            current = int(name) if name.isdigit() else None
            continue
        if current is None:
            continue
        m = _SEQUENTIAL.match(line)
        if m:
            ids = [int(p) for p in re.findall(r'-?\d+', m.group(1))]
            if ids:
                out.setdefault(current, []).extend(ids)
    return out


def merge_configs(texts):
    """Later wins, per sound id."""
    merged = {}
    for t in texts:
        merged.update(parse_config(t))
    return merged


def parse_battle_overrides(text):
    """Parse FFNx's actor-specific SFX keys from one config file.

    ``battle_char_01_16`` means character model ID ``0x01`` playing game SFX
    ID 16; ``battle_enemy_0080_35`` is the matching formation-ID form.  These
    are deliberately separate from numeric archive mappings because the game
    can reuse the same SFX ID for an unrelated attacker.
    """
    out = {}
    current = None
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            key = _BATTLE_OVERRIDE.match(m.group(1).strip())
            current = ((key.group(1), int(key.group(2), 16),
                        int(key.group(3))) if key else None)
            continue
        if current is None:
            continue
        m = _SEQUENTIAL.match(line)
        if m:
            ids = [int(p) for p in re.findall(r'-?\d+', m.group(1))]
            if ids:
                out[current] = ids
    return out


_WM_FOOTSTEP = re.compile(r'^wm_footsteps_(\d+)_(\d+)_(\d+)$')


def parse_named_sequences(text, accept):
    """
    {section name: [ogg id, ...]} for every section `accept(name)` returns true
    for, preserving the order each list is written in.

    The general form behind `parse_config` and `parse_battle_overrides`. It
    exists because a THIRD shape turned up -- Cosmo's world-map footstep keys,
    `wm_footsteps_<model>_<walkmap type>_159` -- and the alternative was a
    tomllib import. tomllib is 3.11+, this has to run wherever the GUI does,
    and `WM/sfx/config.toml` additionally opens with several hundred lines of
    ASCII-art comment that a strict TOML parser is entitled to reject. One
    hand-written parser for all three shapes is the smaller risk.
    """
    out, current = {}, None
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            name = m.group(1).strip()
            current = name if accept(name) else None
            continue
        if current is None:
            continue
        m = _SEQUENTIAL.match(line)
        if m:
            ids = [int(p) for p in re.findall(r'-?\d+', m.group(1))]
            if ids:
                out[current] = ids
    return out


def parse_world_footsteps(text, sound_id=159):
    """
    {(player model, walkmap type): [ogg id, ...]} from a `WM/sfx/config.toml`.

    FFNx reads the current world entity's walkmap type and plays the key that
    names it, only from its replacement for world movement. Ordinary fields
    are a different path entirely and provide no terrain material -- see
    `ff7nx_fieldsteps` for that one.
    """
    raw = parse_named_sequences(text, lambda k: _WM_FOOTSTEP.match(k))
    out = {}
    for key, ids in raw.items():
        model, material, sound = map(int, _WM_FOOTSTEP.match(key).groups())
        if sound == sound_id:
            out[(model, material)] = tuple(rotation_order(ids))
    return out


def merge_world_footsteps(texts, sound_id=159):
    """Later config wins per (model, material), as everywhere else."""
    merged = {}
    for text in texts:
        merged.update(parse_world_footsteps(text, sound_id))
    return merged


def merge_battle_overrides(texts):
    """Later active config files override an actor-specific mapping whole."""
    merged = {}
    for text in texts:
        merged.update(parse_battle_overrides(text))
    return merged


def choose(ogg_ids):
    """Which of a rotating set to use. FFNx alternates; we cannot."""
    ordered = rotation_order(ogg_ids)
    return ordered[0] if ordered else None


def rotation_order(ogg_ids):
    """Return a sequential list with the configured initial element first.

    The archive builder has always honoured ``SEVENTH_NX_SFX_PICK=last`` for
    a one-variant Switch build.  The runtime bridge retains that choice by
    rotating the entire list instead of reducing it to a single OGG.
    """
    values = list(ogg_ids)
    if values and os.environ.get(PICK_ENV, '').lower() == 'last':
        return values[-1:] + values[:-1]
    return values


class Result:
    def __init__(self):
        self.replaced = 0
        self.skipped_no_ogg = []      # sound ids whose ogg is not shipped
        self.skipped_empty = []       # sound ids the game does not use
        self.skipped_loop = []        # sound ids whose vanilla entry loops
        self.skipped_range = []       # ids outside 1..750
        self.failed = []              # (sound id, reason)
        self.cached = 0
        self.replaced_ids = []
        self.bytes_before = 0
        self.bytes_after = 0

    @property
    def total_skipped(self):
        return (len(self.skipped_no_ogg) + len(self.skipped_empty)
                + len(self.skipped_range) + len(self.skipped_loop)
                + len(self.failed))


class SequenceProbe:
    """Encoded variants for one runtime SFX-rotation probe.

    ``audio.fmt`` cannot grow beyond its 750 game-owned records.  The first
    variant therefore replaces the real slot in the normal way; the remaining
    samples are returned to be appended after the archive.  A small ExeFS
    bridge selects their offsets at playback time.
    """

    def __init__(self, sound_id, ogg_ids, payloads):
        self.sound_id = sound_id
        self.ogg_ids = list(ogg_ids)
        self.payloads = list(payloads)


class ContextSequence:
    """Encoded variants for one FFNx battle-character/enemy override."""

    def __init__(self, kind, actor_id, sound_id, ogg_ids, payloads):
        self.kind = kind
        self.actor_id = actor_id
        self.sound_id = sound_id
        self.ogg_ids = list(ogg_ids)
        self.payloads = list(payloads)


def rebuild_sequence_probe(entries, sound_id, ogg_ids, oggs, cache_dir=None):
    """Encode a non-looping rotating slot without adding ``audio.fmt`` rows.

    This is intentionally a narrow primitive for the hardware bridge probe,
    not a second general archive builder.  It verifies that every candidate
    has the target slot's codec constraints before touching ``entries``.
    """
    if not 1 <= sound_id <= len(entries):
        raise ValueError('sound id %d is outside the archive' % sound_id)
    slot = entries[sound_id - 1]
    if slot.empty:
        raise ValueError('sound id %d is an empty archive slot' % sound_id)
    if slot.loop:
        raise ValueError('sound id %d loops; it is unsafe for the probe'
                         % sound_id)
    if len(ogg_ids) < 2:
        raise ValueError('sound id %d needs at least two variants' % sound_id)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)

    encoded = []
    for ogg_id in ogg_ids:
        src = oggs.get(ogg_id)
        if not src:
            raise ValueError('sound id %d needs %d.ogg, which is not active'
                             % (sound_id, ogg_id))
        _cached, wav = _encode_cached(src, slot, cache_dir)
        entry = audio_dat.entry_from_wav(wav, like=slot)
        # The native loader keeps the original format descriptor in the
        # fixed fmt row.  Its rate/block geometry must consequently agree.
        if (entry.sample_rate != slot.sample_rate or
                entry.block_align != slot.block_align or
                entry.channels != slot.channels):
            raise ValueError('sound id %d variant %d does not match slot '
                             'format' % (sound_id, ogg_id))
        if entry.loop:
            raise ValueError('sound id %d variant %d unexpectedly loops'
                             % (sound_id, ogg_id))
        encoded.append(entry)

    entries[sound_id - 1] = encoded[0]
    return SequenceProbe(sound_id, ogg_ids, [e.data for e in encoded])


def rebuild(entries, config, oggs, cache_dir=None, log=lambda *_: None):
    """
    Replace slots in `entries` (from audio_dat.read) with the mod's audio.

    config  {sound id: [ogg id, ...]}
    oggs    {ogg id: path to .ogg}
    Returns a Result. `entries` is modified in place.

    A slot the game does not use is left alone: an empty slot has no format
    block to inherit a sample rate from, and inventing one would put a sound
    where the engine never looks for it while growing audio.dat.
    """
    res = Result()
    replace_loops = os.environ.get(LOOPS_ENV, '').lower() == 'replace'
    res.bytes_before = sum(len(e.data) for e in entries if not e.empty)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)

    for sound_id in sorted(config):
        if not 1 <= sound_id <= len(entries):
            res.skipped_range.append(sound_id)
            continue
        slot = entries[sound_id - 1]
        if slot.empty:
            res.skipped_empty.append(sound_id)
            continue
        ogg_id = choose(config[sound_id])
        src = oggs.get(ogg_id)
        if not src:
            res.skipped_no_ogg.append(sound_id)
            continue
        loop_metadata = audio_dat.source_loop_metadata(src) if slot.loop else None
        if slot.loop and not replace_loops and not loop_metadata:
            # A looping row is replaced only when Cosmo supplies an explicit
            # LOOPSTART marker.  The marker is converted from OGG sample frames
            # to the archive's decoded-byte coordinates.  Rows without one
            # retain their vanilla payload and loop geometry.
            res.skipped_loop.append(sound_id)
            continue
        preserve_loop = bool(slot.loop and loop_metadata)
        try:
            wav = _encode_cached(src, slot, cache_dir, preserve_loop)
        except audio_dat.MissingFFmpeg:
            raise
        except Exception as exc:                                # noqa: BLE001
            res.failed.append((sound_id, str(exc)))
            continue
        if wav[0]:
            res.cached += 1
        try:
            entry = audio_dat.entry_from_wav(wav[1], like=slot)
            if preserve_loop and not entry.loop:
                raise audio_dat.BadArchive(
                    'loop metadata was lost while encoding %s' %
                    os.path.basename(src))
            entries[sound_id - 1] = entry
        except Exception as exc:                                # noqa: BLE001
            res.failed.append((sound_id, str(exc)))
            continue
        res.replaced += 1
        res.replaced_ids.append(sound_id)

    res.bytes_after = sum(len(e.data) for e in entries if not e.empty)
    return res


def prepare_rotations(entries, config, oggs, replaced_ids, cache_dir=None):
    """Encode the extra payloads needed by the native sequential bridge.

    ``rebuild()`` has already replaced the first ordered payload in each
    regular archive row.  This helper creates only the remaining payloads for
    successful, non-looping multi-variant rows; the caller appends them after
    ``audio.dat`` and supplies their descriptors to the ExeFS bridge.

    It deliberately does not try to rotate loop slots.  Their loop points
    describe the original payload and the main rebuild already has the same
    conservative rule.
    """
    replaced = set(replaced_ids)
    result = []
    skipped = []
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    for sound_id in sorted(config):
        ordered = rotation_order(config[sound_id])
        if len(ordered) < 2:
            continue
        if sound_id not in replaced:
            skipped.append((sound_id, 'primary variant was not rebuilt'))
            continue
        if not 1 <= sound_id <= len(entries):
            skipped.append((sound_id, 'outside archive'))
            continue
        slot = entries[sound_id - 1]
        if slot.empty or slot.loop:
            skipped.append((sound_id, 'empty or looping archive row'))
            continue
        payloads = [slot.data]
        try:
            for ogg_id in ordered[1:]:
                src = oggs.get(ogg_id)
                if not src:
                    raise ValueError('%d.ogg is not active' % ogg_id)
                _cached, wav = _encode_cached(src, slot, cache_dir)
                extra = audio_dat.entry_from_wav(wav, like=slot)
                if (extra.loop or extra.sample_rate != slot.sample_rate or
                        extra.block_align != slot.block_align or
                        extra.channels != slot.channels):
                    raise ValueError('%d.ogg does not match slot format' % ogg_id)
                payloads.append(extra.data)
        except (audio_dat.MissingFFmpeg, OSError, ValueError) as exc:
            skipped.append((sound_id, str(exc)))
            continue
        result.append(SequenceProbe(sound_id, ordered, payloads))
    return result, skipped


def prepare_battle_overrides(entries, overrides, oggs, cache_dir=None):
    """Encode all payloads for safe actor-specific battle SFX overrides.

    These entries do not have an archive row of their own: FFNx selects them
    by current attacker and falls back to the numeric SFX mapping otherwise.
    The Switch bridge mirrors that decision, so every override payload is
    appended after ``audio.dat`` and none replaces a globally shared ID.
    """
    result = []
    skipped = []
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    for (kind, actor_id, sound_id), values in sorted(overrides.items()):
        ordered = rotation_order(values)
        if not 1 <= sound_id <= len(entries):
            skipped.append(((kind, actor_id, sound_id), 'outside archive'))
            continue
        slot = entries[sound_id - 1]
        if slot.empty or slot.loop:
            skipped.append(((kind, actor_id, sound_id),
                            'empty or looping archive row'))
            continue
        payloads = []
        try:
            for ogg_id in ordered:
                src = oggs.get(ogg_id)
                if not src:
                    raise ValueError('%d.ogg is not active' % ogg_id)
                _cached, wav = _encode_cached(src, slot, cache_dir)
                item = audio_dat.entry_from_wav(wav, like=slot)
                if (item.loop or item.sample_rate != slot.sample_rate or
                        item.block_align != slot.block_align or
                        item.channels != slot.channels):
                    raise ValueError('%d.ogg does not match slot format' % ogg_id)
                payloads.append(item.data)
        except (audio_dat.MissingFFmpeg, OSError, ValueError) as exc:
            skipped.append(((kind, actor_id, sound_id), str(exc)))
            continue
        if payloads:
            result.append(ContextSequence(kind, actor_id, sound_id, ordered,
                                          payloads))
    return result, skipped


def _encode_cached(src, slot, cache_dir, preserve_loop=False):
    """(was_cached, wav bytes). Keyed on content and on the slot's format."""
    if not cache_dir:
        return False, audio_dat.encode(src, slot.sample_rate,
                                       slot.block_align, preserve_loop)
    st = os.stat(src)
    key = '%s-%d-%d-%s-%s-%s' % (os.path.basename(src), st.st_size,
                                 int(st.st_mtime), slot.sample_rate,
                                 slot.block_align,
                                 'loopmeta2' if preserve_loop else 'plain')
    key = re.sub(r'[^A-Za-z0-9._-]', '_', key)
    path = os.path.join(cache_dir, key + '.wav')
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return True, f.read()
    wav = audio_dat.encode(src, slot.sample_rate, slot.block_align,
                           preserve_loop)
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(wav)
    os.replace(tmp, path)
    return False, wav


def collect(files):
    """
    Split an ordered list of (relative path, absolute path) into the pieces
    this module needs: config texts in order, and the ogg pool.

    Only files under a directory called `sfx` are considered, at any depth,
    so a mod's own folder layout above it does not matter.
    """
    configs, oggs = [], {}
    for rel, full in files:
        parts = [p.lower() for p in rel.replace('\\', '/').split('/')]
        if SFX_DIR not in parts[:-1]:
            continue
        name = parts[-1]
        if name == CONFIG_NAME:
            try:
                with open(full, encoding='utf-8', errors='replace') as f:
                    configs.append(f.read())
            except OSError:
                pass
        elif name.endswith('.ogg'):
            stem = name[:-4]
            if stem.isdigit():
                oggs[int(stem)] = full
    return configs, oggs


def describe(res, total_slots=audio_dat.NUM_SLOTS):
    """One paragraph for the build log."""
    lines = ['sound effects: %d of %d slots replaced from the mod'
             % (res.replaced, total_slots)]
    if res.cached:
        lines.append('               %d reused from cache' % res.cached)
    if res.skipped_no_ogg:
        lines.append('               %d mapped but the .ogg is not in an '
                     'enabled folder' % len(res.skipped_no_ogg))
    if res.skipped_empty:
        lines.append('               %d map to slots the game leaves empty'
                     % len(res.skipped_empty))
    if res.skipped_loop:
        lines.append('               %d LOOPING slot(s) left as the game\'s '
                     'own -- those sources have no LOOPSTART metadata (%s)'
                     % (len(res.skipped_loop),
                        ', '.join('#%d' % i for i in res.skipped_loop[:8])))
    if res.skipped_range:
        lines.append('               %d outside slots 1..%d'
                     % (len(res.skipped_range), total_slots))
    if res.failed:
        lines.append('               %d FAILED to encode (%s)'
                     % (len(res.failed),
                        ', '.join('#%d' % i for i, _ in res.failed[:4])))
    lines.append('               audio.dat %.1f MB -> %.1f MB'
                 % (res.bytes_before / 1048576.0, res.bytes_after / 1048576.0))
    return lines
