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
is OGG ids -- filenames under `sfx/`. Several means FFNx plays them in turn
so a repeated effect does not sound identical each time; the port cannot
rotate, so the first is used. `SEVENTH_NX_SFX_PICK=last` takes the other end
if a particular sound lands badly.

Sections whose name is not a number (`battle_enemy_0080_35`) are FFNx's
per-enemy overrides. They need a hook the port does not have and are ignored;
the plain numeric mapping still applies underneath them.

LAYERING
========
A sound mod ships several folders that all contribute to `sfx/`: Cosmo
Memory has Base, plus UI_Dissidia / UI_R / UI_VII for the three menu sets and
V_Attacks / NV_Attacks for voiced or silent attacks. The .ogg pool is the
union of every ACTIVE folder, later folders winning, and configs merge the
same way per id. That is how 7th Heaven layers them and it falls out of
handing this module the files in application order.

WHAT IS STILL NOT COVERED
=========================
`Ambient/`, `voice/` and `movies/` need engine hooks that do not exist on
this port -- there is nowhere for a per-field ambience layer or a voice track
to be read from. Those folders stay skipped, and the build reports them.
So the mod's sound EFFECTS apply; its ambience, voice acting and cutscene
overlays do not.
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


def choose(ogg_ids):
    """Which of a rotating set to use. FFNx alternates; we cannot."""
    if not ogg_ids:
        return None
    return ogg_ids[-1] if os.environ.get(PICK_ENV, '').lower() == 'last' \
        else ogg_ids[0]


class Result:
    def __init__(self):
        self.replaced = 0
        self.skipped_no_ogg = []      # sound ids whose ogg is not shipped
        self.skipped_empty = []       # sound ids the game does not use
        self.skipped_loop = []        # sound ids whose vanilla entry loops
        self.skipped_range = []       # ids outside 1..750
        self.failed = []              # (sound id, reason)
        self.cached = 0
        self.bytes_before = 0
        self.bytes_after = 0

    @property
    def total_skipped(self):
        return (len(self.skipped_no_ogg) + len(self.skipped_empty)
                + len(self.skipped_range) + len(self.skipped_loop)
                + len(self.failed))


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
        if slot.loop and not replace_loops:
            # A LOOPING SLOT IS LEFT ALONE.
            #
            # The vanilla loop points index samples that the replacement does
            # not have, and there is no way to derive correct ones for new
            # audio. Writing a plausible pair is the dangerous option: a
            # looping sound whose end marker is wrong may never report
            # finishing, and anything that waits for it -- a post-battle
            # sequence, a scripted pause -- waits forever. That is a freeze,
            # not a glitch, and it would be nowhere near the sound in the
            # log.
            #
            # These are a handful of slots out of 750. Keeping the game's own
            # for them costs almost nothing and removes the whole class.
            # SEVENTH_NX_SFX_LOOPS=replace takes them anyway, with looping
            # switched OFF so they play once.
            res.skipped_loop.append(sound_id)
            continue
        ogg_id = choose(config[sound_id])
        src = oggs.get(ogg_id)
        if not src:
            res.skipped_no_ogg.append(sound_id)
            continue
        try:
            wav = _encode_cached(src, slot, cache_dir)
        except audio_dat.MissingFFmpeg:
            raise
        except Exception as exc:                                # noqa: BLE001
            res.failed.append((sound_id, str(exc)))
            continue
        if wav[0]:
            res.cached += 1
        try:
            entries[sound_id - 1] = audio_dat.entry_from_wav(wav[1], like=slot)
        except Exception as exc:                                # noqa: BLE001
            res.failed.append((sound_id, str(exc)))
            continue
        res.replaced += 1

    res.bytes_after = sum(len(e.data) for e in entries if not e.empty)
    return res


def _encode_cached(src, slot, cache_dir):
    """(was_cached, wav bytes). Keyed on content and on the slot's format."""
    if not cache_dir:
        return False, audio_dat.encode(src, slot.sample_rate,
                                       slot.block_align)
    st = os.stat(src)
    key = '%s-%d-%d-%s-%s' % (os.path.basename(src), st.st_size,
                              int(st.st_mtime), slot.sample_rate,
                              slot.block_align)
    key = re.sub(r'[^A-Za-z0-9._-]', '_', key)
    path = os.path.join(cache_dir, key + '.wav')
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return True, f.read()
    wav = audio_dat.encode(src, slot.sample_rate, slot.block_align)
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
                     'own -- new audio has no loop points and a wrong one '
                     'never ends (%s)'
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
