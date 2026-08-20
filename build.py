"""
Planning and building: turn a set of enabled mods into an SD-card tree.

Classification is exact rather than heuristic -- every candidate file is
matched by name against the real contents of the user's own archives.

Copyright (c) 2026 ppkantorski
"""
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from collections import Counter

import exe_patch
import iro
import lgp
import movies as movie_convert
import audio_dat
import sfxmod
import p as pfile
import tex
import battle_stage_bg
import ff7nx_widescreen
import ff7nx_ws
import ff7nx_shaders
import ff7nx_fieldbuf
import ff7nx_fieldbg
import ff7nx_movieclip
import ff7nx_letterbox
import ff7nx_vclip
import ff7nx_modelcull
import ff7nx_moviealign
import ff7nx_moviecull
import ff7nx_moviebars
import ff7nx_camclamp
import ff7nx_battlewide
import ff7nx_swirlscale
import ff7nx_uiclip
import ff7nx_credits
import ff7nx_uncrop
import ff7nx_marginblack
import ff7nx_blackcell
import ff7nx_marginpage
import ff7nx_parallaxfill
import ff7nx_marginpal
import ff7nx_palkey
import field_bg_dense
import ff7nx_bgclear
import ff7nx_bgcolor
import ff7nx_marginart
import ff7nx_palrange
import field_bg_native
import field_bg_repack
import field_bg_compact
import field_bg_pagecap
import field_bg_shadow

# Largest DECOMPRESSED field file this build wrote. apply_field_bg
# needs it: the game decompresses a field into a fixed 2,000,000-byte
# buffer (see ff7nx_fieldbg section E) and a repacked field overflows
# it. Set by _build_flevel, read by apply_field_bg.
FIELD_BG_MAX_RAW = 0
# THE LOADER BUFFER WE ARE WILLING TO ASK FOR, and the field size that keeps
# `ff7nx_fieldbg.field_buffer_bytes` (next power of two at or above 125% of the
# largest field) inside it.
#
# This was 2,097,152 -- one doubling of the stock 2,000,000 -- because that is
# what had been proven on hardware, and at 256px exactly one field of 711 ever
# crossed the 4/5 mark. At 512px it is the single thing standing between here
# and the goal. MEASURED, build 19, at 512px with the cap still at 1,677,721:
#
#     366 of 709 field(s) DROPPED their whole repack
#     15 field(s) could not have an over-full page split and CAN STILL CRASH,
#        mkt_mens among them
#
# So more than half the archive silently built at the old settings, and the
# 256-tiles-per-page fix could not be applied to the rooms that needed it --
# which is why Men's Hall came back.
#
# THIS IS A CEILING, NOT A SIZE. `field_buffer_bytes` picks the next power of
# two above 125% of whatever the biggest field in THIS build actually is, so
# raising the ceiling costs nothing unless a field needs it: a 256px build
# still lands on 2,097,152, and a 512px build lands on 8,388,608 because that
# is what its largest field asks for. What the ceiling does is stop a runaway
# and decide which fields get silently dropped -- and dropping 366 of them is
# far worse than one more doubling.
#
# The buffer is a guest heap allocation, and `ff7nx_heap` raised that heap
# from the port's hardcoded 64 MB to 256. 16 MB is 6% of it against 13% for
# the stock 2 MB in the stock 64 MB pool, so the ceiling is more generous
# than the shipping game's and still leaves the heap emptier.
#
# Projected from the shipped archive, the largest field at 512px is ~4.6 MB.
# A worst case of 12 truecolor pages at 512px is 6.3 MB of pixels alone, and
# the page-cap split can add one more, so 8 MB as a CEILING would have been
# one unlucky field away from dropping something again.
FIELD_BG_BUFFER_MAX = 16 * 1024 * 1024
FIELD_BG_RAW_CAP = FIELD_BG_BUFFER_MAX * 4 // 5

# The depth-1 page side this build INTENDS to end up with. Distinct from
# `field_bg_native.D1_PAGE_PX`, which is the size a section currently HOLDS
# and stays at 256 until the very last pass. FINDINGS-223.
FIELD_BG_D1_TARGET_PX = 256

# Fields whose BACKGROUND is left at stock -- no Cosmos section 9, no margin
# passes, no repack. A DIAGNOSTIC, not a setting: see the long note at the use
# site in `_convert_field_backgrounds`. Empty in a shipping build.
#
#   FIELD_BG_SKIP_FIELDS = frozenset({'las0_2'})   <- the isolation experiment
#   RESULT, build 77: `las4_1` came out BYTE-IDENTICAL TO VANILLA in all nine
#   sections -- confirmed against the shipped archive -- and the field looked
#   "lower resolution but 100% the same". Lower resolution proves the skip
#   worked; identical proves THE FIELD BACKGROUND IS NOT THE CAUSE.
#   Emptied again: it costs the field its upscale and buys nothing.
#   FINDINGS-179.
FIELD_BG_SKIP_FIELDS = frozenset()
import dds_decode
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PYFF7_DIR = os.path.join(HERE, 'PyFF7')
VANILLA_CACHE = os.path.join(HERE, 'cache', '_vanilla')


def ensure_pyff7(log=lambda *_: None):
    """
    Locate PyFF7 next to this script, cloning it on first use. PyFF7's
    pack_lgp is the packer that produced the user's known-good hardware
    build: it regenerates the lookup and conflict tables and can add new
    entries, which a plain replace-in-place cannot. Returns pack_lgp.
    """
    if not os.path.isdir(os.path.join(PYFF7_DIR, 'PyFF7')):
        log('fetching PyFF7 (one-time)...')
        try:
            subprocess.run(
                ['git', 'clone', '--depth', '1',
                 'https://github.com/niemasd/PyFF7.git', PYFF7_DIR],
                check=True, capture_output=True)
        except Exception as exc:
            raise RuntimeError(
                'PyFF7 is required to build model archives but could not be '
                f'downloaded. Clone https://github.com/niemasd/PyFF7 into '
                f'{PYFF7_DIR} and retry. ({exc})')
    if PYFF7_DIR not in sys.path:
        sys.path.insert(0, PYFF7_DIR)
    from PyFF7.lgp import pack_lgp
    return pack_lgp


def vanilla_unpack(name, archive_path, log=lambda *_: None):
    """
    Extract a vanilla LGP's entries to individual files, cached and keyed on
    the archive's size+mtime. Returns {lowercase name: file path}. These are
    the untouched base files that pack_lgp reuses for everything a mod does
    not replace.
    """
    dest = os.path.join(VANILLA_CACHE, name)
    stamp = os.path.join(dest, '.sig')
    want = _sig(archive_path)
    if os.path.exists(stamp):
        with open(stamp) as f:
            if f.read().strip() == want:
                return {fn.lower(): os.path.join(dest, fn)
                        for fn in os.listdir(dest) if fn != '.sig'}
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    archive = lgp.Archive(archive_path)
    out = {}
    for e in archive.entries:
        p = os.path.join(dest, e['name'])
        with open(p, 'wb') as f:
            f.write(e['payload'])
        out[e['name'].lower()] = p
    with open(stamp, 'w') as f:
        f.write(want)
    log(f'  unpacked vanilla {name} ({len(out)} entries)')
    return out

# NOTE: an earlier version lowercased the internal filename references inside
# .hrc/.rsd files, on the theory that the Switch resolves them
# case-sensitively against its lowercase entry names. A known-good manual
# build disproved this: it leaves the references uppercase (e.g.
# "PLY=AAAC.PLY") and renders correctly, while the lowercased version renders
# blank. Mod file contents are therefore passed through UNCHANGED; only the
# LGP entry name is lowercased. No reference rewriting is done.

TITLE_ID = '0100A5B00BDC6000'
ROMFS = 'romfs/ff7/workingdir'

# Archives searched for a matching entry name, and where they live.
# Loose data files the port reads by PATH rather than out of an LGP. The
# strings are in ff7_en: `kernel/kernel.bin`, `kernel/kernel2.bin`,
# `kernel/window.bin`, all relative to the workingdir's data/ root.
#
# kernel2.bin is the text half of the kernel -- magic and item names,
# descriptions, battle command names -- which is what a spell-rename mod
# ships. [Tsunamods] Wizard Staff was landing its kernel2.bin in
# `unrecognised` and doing nothing at all.
# The value is only a FALLBACK. The Switch port relocates language content --
# `%s/data/lang-%s/movies/%s.avi` and a bare `lang-en/` are both in exefs/main
# -- and the dump proves it: its `data/` has battle, field, menu, movies,
# music_ogg, wm and five `lang-XX` folders, and NO `kernel` folder at all.
# Writing to `data/kernel/kernel2.bin` produced a file the game never opens.
#
# So the destination is found by looking the vanilla file up in the dump and
# mirroring wherever it actually lives. See _loose_destination.
LOOSE_DIRS = {'kernel': 'data/kernel'}

ARCHIVES = {
    'char.lgp': 'data/field/char.lgp',
    'flevel.lgp': 'data/field/flevel.lgp',
    'battle.lgp': 'data/battle/battle.lgp',
    'magic.lgp': 'data/battle/magic.lgp',
    'world_us.lgp': 'data/wm/world_us.lgp',
    'menu_us.lgp': 'data/menu/menu_us.lgp',
}

MUSIC_DIR = 'data/music_ogg'

# One movie-camera record: the ten dwords the reader copies per call
# at x86 0x40AD84.
CAM_RECORD = 40

# Extra copies of the LAST camera record, in vanilla frames, appended after
# the stretched track.
#
# The reader zeroes its output record at the top of EVERY call (x86 0x40ACA0
# -- ten dwords at 0x9A0730) and every bail path returns that zeroed buffer
# rather than the last good one. So the moment the budget runs out the camera
# does not freeze, it goes to a null transform, and the models composited
# over the movie stop being drawn.
#
# The track and the budget are both sized from the .cam file, and a
# re-encoded movie is not always exactly `ratio` times the vanilla frame
# count: the operator's opening.mp4 came out 119.5667 s = 3587 frames against
# a x2 track of 3584 records. Three frames, a tenth of a second -- and those
# three frames are drawn with a zeroed camera. That is the brief "blink"
# right before the FMV hands over to gameplay.
#
# A tail of repeated final records covers the rounding: past the authored end
# the camera holds its last position, which is what a frozen camera should do,
# and the budget grows with the file so it is still spending after the movie
# runs out. One vanilla second is far more slack than any encoder rounding
# needs and costs 2.4 KB per track.
CAM_TAIL_FRAMES = 30

# The Switch runs the genuine x86 ff7.exe from romfs (NOT a workingdir
# path -- it lives beside workingdir under ff7/). Verified: it's a PE32
# binary with a .dotemu section. A patched exe / HEXT-baked exe drops in
# here via LayeredFS. Path is relative to romfs/ff7.
EXE_REL = 'resources/ff7_1.02/ff7_en'
ROMFS_FF7 = 'romfs/ff7'

# FFNx external textures. No Switch loader exists for these.
FFNX_EXT = {'.dds', '.png', '.jpg', '.jpeg', '.bmp', '.tga', '.webp'}
META_EXT = {'.xml', '.txt', '.md', '.toml', '.cfg', '.ini', '.gif', '.html'}

# Battle background stage textures: STAGE<NN>_T<NN>_<variant>.DDS under a
# "battle" folder in the mod. These are NOT a real FFNx external-texture
# load path on this port (no live DDS binding for battle stage geometry --
# see battle_stage_bg.py docstring for the full reverse-engineering
# writeup); they must be downsampled/quantized and spliced into the
# native data/battle/stage<NN>.dat container at build time instead of
# being written out as loose files.
import re as _re
BATTLE_STAGE_RE = _re.compile(r'stage0*(\d+)_t0*(\d+)_\d+\.dds$',
                              _re.IGNORECASE)


def _no_switch_loader(name):
    """
    True for an .iro entry this build can never use, judged from its PATH
    ALONE, so it need not be extracted at all.

    Deliberately narrower than the skip in build_plan, and derived from it:
    build_plan drops every FFNX_EXT file EXCEPT a .dds under a `battle`
    folder, so that is exactly the carve-out here, widened by also keeping
    anything whose name matches BATTLE_STAGE_RE wherever it sits. Nothing
    else is judged -- a file kept here can still be dropped later, but a
    file dropped here was already guaranteed to be dropped later.

    This is not a tidiness measure. Cosmos Limit Break is a 3.1 GB .iro
    carrying 18,350 field .dds files against 683 usable flevel.lgp
    background sections; extracting the lot writes ~3 GB of cache that the
    build then walks past, file by file, on every run.
    """
    low = name.replace('\\', '/').lower()
    base = low.rsplit('/', 1)[-1]
    if os.path.splitext(base)[1] not in FFNX_EXT:
        return False
    if BATTLE_STAGE_RE.search(base):
        return False
    return 'battle' not in low.split('/')[:-1]

# FFNx AUDIO directories, which are not music and must never reach music_ogg.
#
# The Switch port streams music from `data/music_ogg/<name>.ogg` -- the string
# `%s/data/music_ogg/%s.ogg` is in exefs/main -- which is why a soundtrack
# replacement works by simply copying .ogg files in. Nothing else in `main`
# reads an .ogg: there is no external-SFX path, no ambient layer and no
# external voice, and movies are `%s/data/movies/%s.mp4` with their own audio.
#
# Sound mods built for FFNx (Cosmo Memory, Echo-S) are almost entirely .ogg,
# and every one of those files is addressed by FFNx's own scheme:
#
#   sfx/<id>.ogg          FFNx use_external_sfx, hooks the game's play_sfx
#   Ambient/<id>.ogg      FFNx ambient layer
#   voice/<field>/<id>.ogg  FFNx external voice
#   movies/<NAME>.ogg     FFNx sound overlay on top of the video
#
# Treating those as music does not merely waste SD space. `plan.music` is
# keyed on BASENAME, so Cosmo Memory alone lands 1380 files on 1376 names.
# A soundtrack mod's tracks sit at the top level of their folder, so keying
# the exclusion on the containing directory separates the two cleanly.
FFNX_AUDIO_DIRS = {'sfx', 'ambient', 'voice', 'movies'}

# vgmstream/ IS MUSIC, and excluding it was a regression.
#
# `vgmstream/` was in the list above until 2026-07-28, on the strength of one
# observation: Cosmo Memory ships `Base/vgmstream/Wind.ogg`, an ambient sound
# aliased through FFNx's vgmstream loader, and it lands on the real music
# track `wind.ogg`. True, but the conclusion was backwards. In FFNx,
# `music/vgmstream/<name>.ogg` is exactly where a MUSIC replacement puts its
# tracks -- vgmstream is the audio backend for the music channel, not a sound
# effect path -- so excluding the directory wholesale silently discarded
# every soundtrack mod that ships the normal way.
#
# [Tsunamods] Arranged Soundtrack: 94 candidate files, 91 of them in
# `vgmstream/`, all dropped. The mod was a no-op and the log said "skipped,
# no Switch loader" as though that were correct.
#
# The right discriminator is the game's own track list. A vgmstream file
# whose stem is a real music track IS a replacement for that track; one that
# is not is an FFNx alias for something the port cannot play. `wind.ogg` is
# a real track, so Cosmo Memory's `Wind.ogg` would still be taken -- but
# Cosmo Memory cannot work on Switch at all (no external-SFX path, see the
# note above) and is not a case worth breaking every soundtrack mod for.
VGMSTREAM_DIR = 'vgmstream'


def _sig(path):
    st = os.stat(path)
    return f'{st.st_size}-{int(st.st_mtime)}'


class Mod:
    """An .iro on disk, extracted on demand into the cache."""

    def __init__(self, path, cache_root):
        self.path = path
        self.filename = os.path.basename(path)
        self.stem = os.path.splitext(self.filename)[0]
        self.cache = os.path.join(cache_root, self.stem)
        self.manifest = None
        self.error = None
        self._entries = None
        # FFNx-only image entries left out of the cache entirely -- see
        # _no_switch_loader(). Reported as skipped FFNx textures by
        # build_plan, exactly as if they had been extracted and then
        # dropped, so the summary the user reads does not change meaning.
        self.skipped_images = 0

    def entries(self):
        """Raw entry paths inside the .iro, read from the directory listing
        only -- no extraction, no decompression. Cached after first read, so
        it's cheap enough to call for every mod just to populate the UI."""
        if self._entries is None:
            try:
                self._entries = iro.list_entries(self.path)
            except Exception:
                self._entries = []
        return self._entries

    def ensure_extracted(self, log=lambda *_: None, progress=None):
        # The stamp is two lines now: the .iro signature, then how many
        # entries _no_switch_loader() held back. Line two is what lets the
        # count survive into a cached run, where nothing is extracted at
        # all. A one-line stamp from an older build reads as "0 skipped"
        # and forces no re-extraction, because line one still matches.
        stamp = os.path.join(self.cache, '.iro-signature')
        want = _sig(self.path)
        if os.path.exists(stamp):
            with open(stamp) as f:
                lines = f.read().splitlines()
            if lines and lines[0].strip() == want:
                try:
                    self.skipped_images = int(lines[1]) if len(lines) > 1 else 0
                except ValueError:
                    self.skipped_images = 0
                self._load_manifest()
                return False
            shutil.rmtree(self.cache, ignore_errors=True)
        log(f'extracting {self.filename} ...')
        os.makedirs(self.cache, exist_ok=True)
        written, skipped, failures, excluded = iro.extract(
            self.path, self.cache, progress, skip=_no_switch_loader)
        for name, why in failures[:5]:
            log(f'  ! {name}: {why}')
        if failures:
            log(f'  ! {len(failures)} entries failed to extract')
        self.skipped_images = excluded
        with open(stamp, 'w') as f:
            f.write(f'{want}\n{excluded}\n')
        log(f'  {written} files extracted')
        if excluded:
            log(f'  {excluded} FFNx external texture(s) not extracted -- '
                'this port has no loader for them, so they would be dead '
                'files taking up cache space')
        self._load_manifest()
        return True

    def _load_manifest(self):
        path = os.path.join(self.cache, 'mod.xml')
        if not os.path.exists(path):
            # Pull just mod.xml straight out of the .iro so the UI can show
            # options before the (slow) full extraction has happened. Reading
            # one entry only touches the archive directory plus that entry,
            # so it is fast even for a multi-GB mod.
            try:
                data = iro.read_one(self.path, 'mod.xml')
            except Exception as exc:
                self.error = f'could not read {self.filename}: {exc}'
                self.manifest = None
                return
            if data is None:
                self.error = 'no mod.xml in archive'
                self.manifest = None
                return
            os.makedirs(self.cache, exist_ok=True)
            with open(path, 'wb') as f:
                f.write(data)
        try:
            self.manifest = iro.Manifest(path)
            self.error = None
        except Exception as exc:
            self.manifest = None
            self.error = f'mod.xml unreadable: {exc}'

    @property
    def display_name(self):
        if self.manifest and self.manifest.name:
            return self.manifest.name
        return self.stem


    def _folder_resolver(self):
        """
        Map a manifest folder name onto the directory that is actually there,
        ignoring case.

        7th Heaven runs on Windows, where the filesystem does not care. Mod
        authors therefore do not either, and Enhanced Stock UI declares 71
        folders whose case does not match what it ships -- `Avatars/Main`
        against `Avatars/main`, `Battleview/HelpBoxesNone` against
        `BattleView/HelpBoxesNone`. FOUR of those are active at the mod's own
        defaults, so on a case-sensitive volume the user's Help Box, Action
        Box and Avatar choices resolved to a directory that was not there and
        contributed nothing at all, in silence.

        Resolution is per path COMPONENT, because the mismatch can be at any
        depth, and the exact name is preferred wherever it exists so a mod
        that really does ship two folders differing only in case is not
        collapsed.
        """
        cache = self.cache

        def resolve(folder):
            path = cache
            for part in folder.replace('\\', os.sep).split(os.sep):
                if not part:
                    continue
                cand = os.path.join(path, part)
                if os.path.isdir(cand):
                    path = cand
                    continue
                try:
                    hit = next((e for e in os.listdir(path)
                                if e.lower() == part.lower()
                                and os.path.isdir(os.path.join(path, e))),
                               None)
                except OSError:
                    hit = None
                if hit is None:
                    return os.path.join(path, part)   # report it as missing
                path = os.path.join(path, hit)
            return path

        return resolve


    def files_for(self, settings, read=None, log=None):
        """
        Yield (relative path, absolute path, option) for every selected file.
        `option` is the ModFolder that selected the file -- the unit the user
        toggles. Two files from the same option (even in different
        subdirectories like fb/char and fb/high) share it, so they are not
        treated as conflicting.
        """
        folders = iro.active_folders(self.manifest, settings,
                                     read=read, log=log) \
            if self.manifest else []
        out = []
        declared = bool(self.manifest and self.manifest.folders)
        resolve = self._folder_resolver()
        if not folders:
            if declared:
                # The mod DOES declare folders and every one of them is gated
                # off. That is "apply nothing", not "apply everything".
                #
                # The fallback below exists for mods with no <ModFolder> at
                # all, and it used to fire here as well: switching [Tsunamods]
                # Wizard Staff's one option OFF emplaced the mod root, which
                # meant all six kernel2.bin variants at once and whichever
                # landed last. An off switch that applies more than the on
                # switch is worse than no switch.
                return out
            # No folder declarations: the whole mod root is the payload.
            roots = [('', self.cache)]
        else:
            roots = [(f, resolve(f)) for f in folders]
        for option, root in roots:
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if d.lower() not in ('preview', 'previews')]
                for fn in filenames:
                    if fn.startswith('.') or fn == 'mod.xml':
                        continue
                    full = os.path.join(dirpath, fn)
                    out.append((os.path.relpath(full, self.cache), full,
                                option))
        return out


# ------------------------------------------------- "what does this touch?"
# UI-facing helpers only -- informational, not used by the real build. They
# read the mod's own .iro directory listing (no extraction) and classify each
# entry the same way build_plan's first pass does: an exact name match
# against the user's own archive contents (catalogs) when available,
# otherwise a folder-name guess so the UI still has something to show before
# workingdir/ has been scanned or the mod has been extracted.

FOLDER_ARCHIVE_HINTS = (
    ('flevel', 'flevel.lgp'),
    ('char', 'char.lgp'),
    ('enem', 'battle.lgp'),      # Enemies, enemy
    ('mains', 'battle.lgp'),     # 7H convention for player battle models
    ('monster', 'battle.lgp'),
    ('summon', 'magic.lgp'),
    ('battle', 'battle.lgp'),
    ('magic', 'magic.lgp'),
    ('world', 'world_us.lgp'),
    ('menu', 'menu_us.lgp'),
)

ARCHIVE_DISPLAY = {
    'char.lgp': 'char.lgp', 'flevel.lgp': 'flevel.lgp',
    'battle.lgp': 'battle.lgp', 'magic.lgp': 'magic.lgp',
    'world_us.lgp': 'world_us.lgp', 'menu_us.lgp': 'menu_us.lgp',
    'music': 'music (.ogg)',
}

_COND_TERM = re.compile(r'([^()]+?)\s*(?:!=|=)\s*\d+')


def _cond_option_ids(condition):
    return {m.group(1).strip() for m in _COND_TERM.finditer(condition or '')}


def archives_for_folders(mod, folders, catalogs=None):
    """
    Best-effort answer to "what will this set of ModFolders touch".

    Returns (archives: set[str], exact: bool). `exact` is True only if every
    matched file was resolved by a real hit in `catalogs` (the user's own
    archive contents) -- otherwise the result is a folder-name guess and
    should be shown to the user as an estimate.
    """
    archives = set()
    exact = bool(catalogs)
    saw_file = False
    prefixes = tuple((f.replace('\\', '/').rstrip('/') + '/').lower()
                     for f in folders if f) if folders else None
    for raw in mod.entries():
        rel = raw.replace('\\', '/')
        low = rel.lower()
        if prefixes is not None and not low.startswith(prefixes):
            continue
        base = low.rsplit('/', 1)[-1]
        if not base or base == 'mod.xml' or base.startswith('.'):
            continue
        ext = os.path.splitext(base)[1]
        if ext in META_EXT or ext in FFNX_EXT:
            continue
        if ext == '.ogg':
            archives.add('music')
            saw_file = True
            continue
        saw_file = True
        if catalogs:
            hit = next((a for a, names in catalogs.items() if base in names),
                       None)
            if hit:
                archives.add(hit)
                continue
        exact = False
        for key, arc in FOLDER_ARCHIVE_HINTS:
            if low.startswith(key) or f'/{key}' in low:
                archives.add(arc)
                break
    return archives, (exact and saw_file)


def option_archives(mod, option, catalogs=None):
    """Archives touched by whichever ModFolder(s) this option gates, across
    all of its possible values (not just the one currently selected)."""
    if not mod.manifest:
        return set(), False
    folders = [f for f, cond in mod.manifest.folders
               if option.id in _cond_option_ids(cond)]
    if not folders:
        return set(), False
    return archives_for_folders(mod, folders, catalogs)


def mod_archives(mod, settings, catalogs=None):
    """Archives touched by the mod as currently configured."""
    if not mod.manifest:
        return set(), False
    folders = iro.active_folders(mod.manifest, settings)
    return archives_for_folders(mod, folders, catalogs)


def archives_text(archives, exact):
    """Render an (archives, exact) pair for display."""
    if not archives:
        return ''
    names = ', '.join(sorted(ARCHIVE_DISPLAY.get(a, a) for a in archives))
    return names if exact else f'{names} (est.)'


class Plan:
    """What a set of mods would do to the user's archives."""

    def __init__(self):
        self.archive_files = {}   # archive -> {lowername: (src, mod)}
        self.chunks = {}          # field -> {section: (src, mod)}
        self.music = {}           # basename -> (src, mod)
        self.movies = {}          # lowercase stem -> (src, mod)
        self.loose = {}           # (holder, name) -> (src, mod)
        self.battle_bg = {}       # (stage_num, tile_num) -> (src, mod)
        self.sfx = []             # ordered (rel, full, mod) under sfx/
        self.opening_dims = None  # (w, h) of the emplaced opening
        self.opening_fps = None   # frame rate of the emplaced opening, for
                                  # reporting only -- see build_exe()
        self.skipped_ffnx = 0
        self.skipped_ffnx_audio = {}   # FFNx audio dir -> file count
        self.music_from_vgmstream = 0  # tracks taken from vgmstream/
        self.unmatched = []
        self.conflicts = []
        self.folder_conflicts = []   # (folder_a, folder_b, count)
        self.folder_of = {}          # archive -> {lowname: source folder}
        self.hext_files = []         # [(path, mod)] FFNx exe patches to bake
        self.field_dds_sources = []  # [(iro path, allowed folder prefixes)]
                                     # carrying upscaled field background art
                                     # -- see field_bg_repack.ArtProvider
        self.battle_bg_native_names = set()  # battle.lgp lownames synthesized
                                              # from Avalanche Arisen's DDS
                                              # tiles -- see BATTLE_BG_TEX_CAP
        self.widescreen = None       # (config.toml, movie_config.toml, mod)
                                     # -- FFNx's per-field widescreen table,
                                     # baked into flevel.lgp section 8 by
                                     # _build_flevel. See ff7nx_ws.py.

    def total_portable(self):
        return (sum(len(v) for v in self.archive_files.values())
                + sum(len(v) for v in self.chunks.values())
                + len(self.music) + len(self.loose) + len(self.battle_bg)
                + (1 if self.sfx else 0))


def build_plan(mods, settings_by_mod, catalogs, log=lambda *_: None,
               music_names=None, runtime_read=None):
    """
    mods            ordered list of enabled Mod objects (later wins)
    settings_by_mod {mod.filename: {option id: value}}
    catalogs        {archive filename: set of lowercase entry names}
    runtime_read    callable(varspec) -> int for `<Conditional>` RuntimeVar
                    gates, or None to leave every conditional folder in. See
                    iro.exe_var_reader: the variables these mods test are
                    bytes in the ff7 exe, so the exe answers them.
    music_names     lowercase `<name>.ogg` of every vanilla track, or None.
                    Used only to decide whether a file under `vgmstream/` is
                    a music replacement or an FFNx alias. None means "take
                    them all", which is the right default for a soundtrack
                    mod and the behaviour before the vgmstream exclusion.

    Routing is by name AND by folder. A file whose name already exists in an
    archive goes there. A file that matches nothing -- a NEW model piece that
    a mod overhaul adds -- is routed to the archive that the majority of its
    sibling files (same source folder) map to, so the pieces a model needs
    travel with it instead of being dropped. Dropping them is what leaves
    models rendering blank.
    """
    plan = Plan()

    # FFNx textures held back at extraction (see _no_switch_loader) never
    # reach the walk below, so they are counted in here to keep the
    # "FFNx textures : N (skipped, no Switch loader)" line honest.
    for mod in mods:
        plan.skipped_ffnx += getattr(mod, 'skipped_images', 0)

    # THE DEPTH-1 PAGE SIZE, RESOLVED ONCE AND SHARED. FINDINGS-223.
    #
    # The module's read length and the file's stored size are the same number
    # seen from two sides, and if they disagree the TEXTURE walk desynchronises
    # rather than degrading. So it is resolved in exactly one place and pushed
    # into both modules, and `_field_bg_d1_guard` below re-checks that they
    # still agree right before `main` is written.
    # THE TARGET IS NOT THE CURRENT SIZE, AND CONFLATING THEM DESTROYED A BUILD.
    #
    # `field_bg_native.D1_PAGE_PX` means "the size the section in my hands
    # HOLDS RIGHT NOW". For the whole pipeline that is 256, because the lift
    # is the last pass and has not run yet. Setting it to 512 here made
    # `parse_texture_block` read every 64 KB paletted page as a 256 KB one:
    # marginart, both blackcells, marginpage, the transparency key and the
    # repack all desynchronised at once, which is what "cannot reshape array
    # of size 262144 into shape (256,256)" and 557 sections "did not parse as
    # a background" were. The lift then failed on the wreckage.
    #
    # So the BUILD INTENT lives in its own name, and `D1_PAGE_PX` is flipped
    # only inside `field_bg_native.lift_depth1`, for the length of one
    # serialise.
    global FIELD_BG_D1_TARGET_PX
    FIELD_BG_D1_TARGET_PX = ff7nx_fieldbg.d1_page_px()
    ff7nx_fieldbg.D1_PAGE_PX = FIELD_BG_D1_TARGET_PX
    if field_bg_native.D1_PAGE_PX != field_bg_native.VANILLA_PX:
        raise RuntimeError(
            'field_bg_native.D1_PAGE_PX is %d at the start of the build; it '
            'must be %d until lift_depth1 runs'
            % (field_bg_native.D1_PAGE_PX, field_bg_native.VANILLA_PX))
    if FIELD_BG_D1_TARGET_PX != field_bg_native.VANILLA_PX:
        log('  field background: DEPTH-1 PAGES AT %dpx (%s). Every paletted '
            'page in the archive is lifted and %d extra module word(s) are '
            'written. Build 108 lifts by 2x REPLICATION, so the picture is '
            'expected to be IDENTICAL -- see FINDINGS-223.'
            % (FIELD_BG_D1_TARGET_PX, ff7nx_fieldbg.D1_PX_ENV,
               len(ff7nx_fieldbg._d1_words())))

    # Upscaled field background art. These .dds are NOT extracted -- there
    # are 18,270 of them in Cosmos Limit Break and they are 3 GB -- so all
    # that is collected here is which .iro to read them out of and which of
    # its option folders are switched on. field_bg_repack reads the ones a
    # field actually needs straight out of the archive at build time.
    if ff7nx_fieldbg.enabled():
        for mod in mods:
            entries = mod.entries()
            if not any(e.lower().endswith('.dds')
                       and '/field/' in e.lower().replace(chr(92), '/')
                       for e in entries):
                continue
            settings = settings_by_mod.get(mod.filename, {})
            folders = (iro.active_folders(mod.manifest, settings)
                       if mod.manifest else [])
            allowed = {f.lower().replace(chr(92), '/').rstrip('/') + '/'
                       for f in folders} or None
            plan.field_dds_sources.append((mod.path, allowed))

    # FFNx's per-field widescreen table. `CONFIG/widescreen/config.toml` is
    # NOT metadata: it is the half of the feature that decides which fields
    # can be widened at all, and Cosmos Limit Break's copy takes coverage
    # from 341 of 711 fields (the gate alone) to 647. It used to be
    # discarded here as an FFNx-only file. See ff7nx_ws.py.
    #
    # Collected in mod order with LATER WINNING, the same rule as every
    # other asset, and only when 16:9 is actually switched on -- with the
    # feature off nothing reads it and walking every mod cache for it is
    # wasted I/O.
    if ff7nx_ws.wants_bake():
        cfg, mov, alts, root = ff7nx_ws.find_config(
            [getattr(m, 'cache', None) for m in mods])
        if cfg:
            plan.widescreen = (cfg, mov, os.path.basename(root or ''))
            log(f'  widescreen config: {os.path.relpath(cfg, root)}'
                f'   (from {os.path.basename(root or "?")})')
            for a in alts:
                log(f'    (also present, not used: '
                    f'{os.path.relpath(a, root)})')

    # FFNx HEXT exe-patch files: mods ship these under a `hext/` folder (or
    # as .hext). They aren't archive data -- they patch the x86 ff7 exe the
    # Switch runs.
    #
    # These used to be collected across every enabled mod IGNORING option
    # gating, on the grounds that a compatibility-flag patch must not be
    # missed. That is fine for a mod with one hext file and catastrophic for
    # one built out of alternatives: Enhanced Stock UI ships **162** of them,
    # including ten mutually exclusive Battle View layouts each with its own
    # `hext/all.txt` and three driver configs. Applying all of them at once
    # writes ten different layouts over the same addresses and the last one
    # to be walked wins -- which is not the one the user chose.
    #
    # So gating applies, with one carve-out: a hext file that lies OUTSIDE
    # every folder the manifest declares has no gate to be judged by and is
    # always taken. That is where an ungated compatibility patch lives, and
    # it keeps the original behaviour for the mods that motivated it.
    def _is_hext(name):
        low = name.lower()
        return low.endswith('.hext') or low.endswith('.txt')

    # ORDER MATTERS, and it has to be 7th Heaven's order.
    #
    # HEXT collisions resolve by LAST WRITE WINS, so the sequence decides
    # which mod's value survives. 7th Heaven applies mods in priority order
    # and, within a mod, its folders in manifest declaration order. This used
    # to walk the filesystem, which is neither -- with Enhanced Stock UI's 45
    # files that made the winner of any overlap depend on directory order and
    # therefore irreproducible between machines.
    #
    # `mods` arrives already in application order (lowest priority first, so
    # later writes win), and `files_for` yields folders in declaration order,
    # so taking the gated files from it preserves both. Files outside every
    # declared folder go FIRST within their mod: they are the mod's baseline,
    # and a folder the user actually chose should be able to override them.
    for mod in mods:
        root = mod.cache
        if not os.path.isdir(root):
            continue
        settings = settings_by_mod.get(mod.filename, {})
        declared = [f.replace('\\', os.sep).lower()
                    for f, _c in (mod.manifest.folders if mod.manifest else [])]
        gated = []
        seen = set()
        for rel, full, _option in mod.files_for(settings,
                                                read=runtime_read):
            parts = [p.lower() for p in rel.replace('\\', '/').split('/')]
            if 'hext' in parts[:-1] and _is_hext(parts[-1]) \
                    and full not in seen:
                seen.add(full)
                gated.append(full)
        ungated = []
        for dirpath, _dirs, files in sorted(os.walk(root)):
            rel_dir = os.path.relpath(dirpath, root)
            rel_l = '' if rel_dir == '.' else rel_dir.lower()
            parts = {p.lower() for p in dirpath.split(os.sep)}
            in_hext_dir = 'hext' in parts
            inside = any(rel_l == d or rel_l.startswith(d + os.sep)
                         for d in declared)
            if inside:
                continue
            for fn in sorted(files):
                low = fn.lower()
                if low.endswith('.hext') or (in_hext_dir
                                             and low.endswith('.txt')):
                    ungated.append(os.path.join(dirpath, fn))
        for full in ungated + gated:
            plan.hext_files.append((full, mod))
    if plan.hext_files:
        log(f'found {len(plan.hext_files)} HEXT exe-patch file(s) '
            '(will bake into ff7 exe if a base exe is provided)')

    # First pass: gather archive candidates and remember their source folder.
    # candidates: list of dicts across all mods, in application order.
    candidates = []
    for mod in mods:
        settings = settings_by_mod.get(mod.filename, {})
        picked = mod.files_for(settings, read=runtime_read, log=log)
        log(f'{mod.display_name}: {len(picked)} candidate files')

        # Decide, ONCE PER MOD, whether this mod's `vgmstream/` files are a
        # soundtrack or FFNx aliases -- see the VGMSTREAM_DIR note above.
        #
        # Per file is not enough: Cosmo Memory's `Base/vgmstream/Wind.ogg` IS
        # a real track name, so a name test alone would still let a sound mod
        # overwrite the `wind` music track. Per mod it separates cleanly: a
        # soundtrack replacement's vgmstream names are nearly all real
        # tracks, a sound mod's are nearly all not.
        vgm, ffnx_sound = [], 0
        for rel, _f, _o in picked:
            if os.path.splitext(rel)[1].lower() != '.ogg':
                continue
            dparts = [p.lower() for p in
                      os.path.dirname(rel).replace('\\', '/').split('/')]
            if VGMSTREAM_DIR in dparts:
                vgm.append(os.path.basename(rel).lower())
            elif any(p in FFNX_AUDIO_DIRS for p in dparts):
                ffnx_sound += 1

        # Two signals, and a mod has to pass both:
        #
        #   1. It must not also ship FFNx sound. A soundtrack replacement has
        #      no sfx/, Ambient/ or voice/; Cosmo Memory is almost entirely
        #      those, and its lone `Base/vgmstream/Wind.ogg` is an ambient
        #      sound aliased through the music backend.
        #   2. Its vgmstream names must mostly BE real tracks. This catches a
        #      sound mod that ships nothing but aliases, where signal 1 says
        #      nothing.
        #
        # Signal 2 needs the dump's track list. Without it, signal 1 alone
        # decides -- never "skip", because a missing dump must not silently
        # turn a soundtrack mod into a no-op. That failure is exactly what
        # this whole block exists to undo.
        take_vgm = True
        why = ''
        if vgm and ffnx_sound > len(vgm):
            # PROPORTION, not presence. The first version of this test was
            # `if ffnx_sound:` and it misfired immediately: [Tsunamods]
            # Arranged Soundtrack ships 91 tracks under vgmstream/ and TWO
            # files under movies/ (FFNx's audio overlay for a cutscene), and
            # those two were enough to condemn all 91. A soundtrack mod is
            # perfectly entitled to carry a little movie audio.
            #
            # Cosmo Memory is the case this is for, and it is not close:
            # ~1380 files under sfx/, Ambient/ and voice/ against a single
            # `Base/vgmstream/Wind.ogg`. Requiring the sound files to
            # OUTNUMBER the vgmstream ones separates the two by an order of
            # magnitude in either direction.
            take_vgm, why = False, (
                'it ships %d FFNx sound file(s) against %d under vgmstream/, '
                'so vgmstream/ here is an alias path'
                % (ffnx_sound, len(vgm)))
        elif vgm and music_names is not None:
            hit = sum(1 for n in vgm if n in music_names)
            if hit * 2 < len(vgm):
                take_vgm, why = False, (
                    'only %d of %d names are real music tracks' % (hit, len(vgm)))
        if vgm and not take_vgm:
            log(f'  {mod.display_name}: {len(vgm)} file(s) under vgmstream/ '
                f'treated as FFNx aliases, not a soundtrack -- {why}')
        for rel, full, option in picked:
            base = os.path.basename(rel)
            low = base.lower()
            ext = os.path.splitext(low)[1]

            # An FFNx sound mod, BEFORE the .toml lands in META_EXT and the
            # .ogg lands in the FFNx-audio skip. These are not dead files:
            # they rebuild audio.fmt / audio.dat, which the port does read.
            # Order matters within a mod and between mods, so they are
            # collected as an ordered list and resolved once at emplacement.
            dirs_l = [p.lower() for p in
                      os.path.dirname(rel).replace('\\', '/').split('/')]
            if sfxmod.SFX_DIR in dirs_l and (low == sfxmod.CONFIG_NAME
                                             or ext == '.ogg'):
                plan.sfx.append((rel, full, mod))
                continue

            if ext in META_EXT:
                continue

            # Battle stage background tiles (STAGE<NN>_T<NN>_<variant>.DDS
            # under a "battle" folder) are not a live FFNx texture-load path
            # on this port. Two things happen with these, both driven
            # entirely by the .iro's own DDS content -- no external archive
            # needed:
            #
            # 1. battle.lgp: each tile is BC7-decoded and matched, via the
            #    static (stage,tile)->real-entry-name table in
            #    battle_bg_dds_map.json, to the real vanilla battle.lgp
            #    entry it replaces (e.g. STAGE01_T00 -> "ohac"). That table
            #    was built once by perceptual content-matching this same
            #    mod's two parallel PC releases (this DDS/.iro one and an
            #    older native-.tex one distributed for the classic lgp_edit
            #    workflow) and is shipped as static data -- see
            #    _synthesize_battle_bg_tex for the decode step. Landing in
            #    battle.lgp under the real name means it goes through the
            #    SAME hardware-proven conversion as character/enemy skin
            #    replacements (_convert_battle_textures).
            # 2. data/battle/stage57.dat: stage 57 (Safer Sephiroth) is ALSO
            #    a one-off, hardcoded-by-filename load from a loose
            #    container file (confirmed by disassembly of lasboss3.cpp),
            #    independent of battle.lgp. plan.battle_bg still collects
            #    the raw tile for battle_stage_bg.py to splice into that
            #    container at emplacement -- see _emplace_battle_bg.
            if ext == '.dds' and 'battle' in dirs_l:
                m = BATTLE_STAGE_RE.search(low)
                if m:
                    stage_num, tile_num = int(m.group(1)), int(m.group(2))
                    plan.battle_bg[(stage_num, tile_num)] = (full, mod)
                    native_name = _battle_bg_dds_map().get(
                        '%02d_%02d' % (stage_num, tile_num))
                    if native_name:
                        staged = _synthesize_battle_bg_tex_cached(
                            full, native_name, log=log)
                        if staged:
                            hits = [a for a, names in catalogs.items()
                                    if native_name in names]
                            direct = hits[0] if hits else 'battle.lgp'
                            plan.battle_bg_native_names.add(native_name)
                            candidates.append({
                                'mod': mod,
                                'rel': 'battle_bg_dds/' + native_name,
                                'base': native_name, 'low': native_name,
                                'full': staged,
                                'direct': direct, 'hits': hits or [direct],
                                'route': (mod.filename, 'battle_bg_dds'),
                                'option': option,
                            })
                    continue

            if ext in FFNX_EXT:
                plan.skipped_ffnx += 1
                continue
            # Movies. The port reads data/movies/<name>.mp4 and nothing
            # else, so every movie container a PC mod might ship is collected
            # here and re-encoded at emplacement time. Keyed on the STEM,
            # because the game asks for `<name>.mp4` regardless of what the
            # mod called its container -- Cosmos FMV ships WebM named .avi.
            if ext in movie_convert.MOVIE_EXT:
                plan.movies[os.path.splitext(low)[0]] = (full, mod)
                continue

            # Loose files the port opens by path. Matched on the
            # containing directory so a mod's own folder layout above it
            # (MSN/kernel/kernel2.bin) does not matter.
            parts_l = [p.lower() for p in
                       os.path.dirname(rel).replace('\\', '/').split('/')]
            holder = next((p for p in parts_l if p in LOOSE_DIRS), None)
            if holder and ext in ('.bin',):
                plan.loose[(holder, low)] = (full, mod)
                continue

            if ext == '.ogg':
                # ANY component, not just the immediate parent: FFNx's voice
                # layer is voice/<field name>/<id>.ogg, so the directory that
                # identifies it is the grandparent, and external_sfx_path is a
                # root that mods nest under however they like.
                parts = [p.lower() for p in
                         os.path.dirname(rel).replace('\\', '/').split('/')]
                holder = next((p for p in parts if p in FFNX_AUDIO_DIRS), None)
                if holder:
                    plan.skipped_ffnx_audio[holder] = \
                        plan.skipped_ffnx_audio.get(holder, 0) + 1
                    continue
                if VGMSTREAM_DIR in parts:
                    # A music replacement shipped the FFNx way. Taken as
                    # music when this mod looks like a soundtrack, and
                    # additionally filtered by name so a stray alias inside
                    # one cannot land on a track it is not.
                    if not take_vgm or (music_names is not None
                                        and low not in music_names):
                        plan.skipped_ffnx_audio[VGMSTREAM_DIR] = \
                            plan.skipped_ffnx_audio.get(VGMSTREAM_DIR, 0) + 1
                        continue
                    plan.music_from_vgmstream += 1
                plan.music[low] = (full, mod)
                continue
            if '.chunk.' in low:
                field, _, section = low.rpartition('.')
                field = field[:-len('.chunk')] if field.endswith('.chunk') \
                    else field
                try:
                    idx = int(section)
                except ValueError:
                    plan.unmatched.append(rel)
                    continue
                plan.chunks.setdefault(field, {})[idx] = (full, mod)
                continue

            hits = [a for a, names in catalogs.items() if low in names]
            direct = hits[0] if hits else None
            candidates.append({
                'mod': mod, 'rel': rel, 'base': base, 'low': low,
                'full': full, 'direct': direct, 'hits': hits,
                # ROUTING uses the immediate subdirectory: one ModFolder can
                # span several archives (fb/char -> char.lgp, fb/world ->
                # world_us.lgp), so subfolders must be routed independently.
                'route': (mod.filename, os.path.dirname(rel)),
                # CONFLICT REPORTING uses the toggled option, so subfolders of
                # one option (fb/char, fb/high) are not treated as rivals.
                'option': option,
            })

    # Decide each source subfolder's archive by majority vote of its matched
    # files, so new (unmatched) files in that subfolder inherit the same
    # target instead of being dropped.
    route_votes = {}
    for c in candidates:
        if c['direct']:
            route_votes.setdefault(c['route'], Counter())[c['direct']] += 1
    route_target = {k: v.most_common(1)[0][0] for k, v in route_votes.items()}

    # A folder's vote decides where its UNMATCHED files go, and nothing more.
    # It is deliberately not used to override a name match: the vote is a
    # weak signal. "Chocobo - NinoStyle/World" is a world-map folder whose
    # matched names mostly exist in char.lgp, so its vote is char.lgp -- and
    # an earlier version of the reroute below trusted that and dragged
    # `aja.hrc`, a world_us.lgp entry, into char.lgp. See _reroute_by_folder.

    # Second pass: assign every candidate to an archive. Keep ALL versions of
    # each entry (with their source subfolder) so models can be reassembled
    # atomically afterwards; the last one is the provisional winner.
    added_new = 0
    rerouted = _reroute_by_folder(candidates, route_target)
    versions = {}  # target -> {low: [(subfolder, full, mod), ...]}
    for c in candidates:
        target = c.get('target') or _provisional_target(c, route_target)
        if target is None:
            plan.unmatched.append(c['rel'])
            continue
        if c['direct'] is None:
            added_new += 1
        bucket = plan.archive_files.setdefault(target, {})
        if c['low'] in bucket and bucket[c['low']][1] is not c['mod']:
            plan.conflicts.append((target, c['base'],
                                   bucket[c['low']][1].display_name,
                                   c['mod'].display_name))
        bucket[c['low']] = (c['full'], c['mod'])
        plan.folder_of.setdefault(target, {})[c['low']] = c['option']
        versions.setdefault(target, {}).setdefault(c['low'], []).append(
            (c['route'][1], c['full'], c['mod']))

    # Reassemble each model from a single source, preferring the base set, so
    # enabling an overlapping option can never mix pieces from two versions of
    # a character. Only affects archives that use the .hrc model structure.
    _assemble_models_atomically(plan, versions, log)

    if added_new:
        log(f'routed {added_new} new files (added to archives, not just '
            'replaced)')
    if rerouted:
        log(f'  {len(rerouted)} model part(s) moved to follow their model '
            'instead of a name match in another archive:')
        for rel, was, now, why in rerouted[:8]:
            log(f'      {os.path.basename(rel)}: {was} -> {now}'
                f'   (referenced by {why} in {os.path.dirname(rel)})')
        if len(rerouted) > 8:
            log(f'      ... and {len(rerouted) - 8} more')
    _warn_frankenstein(plan, log)
    return plan


BATTLE_BG_DDS_MAP_PATH = os.path.join(HERE, 'battle_bg_dds_map.json')
BATTLE_BG_DDS_CACHE = os.path.join(HERE, 'cache', '_battle_bg_dds')

_battle_bg_dds_map_cache = None


def _battle_bg_dds_map():
    """
    {"<stage>_<tile>": "<real battle.lgp entry name>"} for the Avalanche
    Arisen battle background mod, e.g. "01_00": "ohac" (STAGE01_T00 ->
    vanilla entry "ohac").

    The mod is distributed two ways by the same author. The one installed
    via 7th Heaven (STAGE##_T##_00.DDS under FFNx's live external-texture
    naming) has NO loader on this port -- there's no runtime DDS binding
    for battle stage geometry, so those files are otherwise inert here.
    The ORIGINAL PC release ships the same art under the real vanilla
    battle.lgp entry names it replaces (e.g. 'oxae'), for the classic
    lgp_edit tool -- but names like that carry no information back to the
    DDS side; FF7's LGP naming is an algorithmic hash of a numeric id, not
    a stored string, so there's no way to derive "ohac" from "STAGE01_T00"
    directly.

    This table bridges the two: built once by perceptually content-
    matching all 578 tiles between the two releases (bucketed by exact
    pixel dimensions -- which turned out to match 1:1 between the two
    distributions -- then Hungarian-assigned by 64x64 downsampled RGB
    distance). Every one of the 578 assignments was either a confident,
    uniquely-best match (>500 margin over the 2nd-best candidate) or a
    near-exact tie against another candidate (both costs the same to
    within roughly 1 unit) -- FF7 genuinely reuses some generic backgrounds
    across multiple stage ids, so a tied pair is a real duplicate in the
    source art, not a matching error; either assignment in a tied pair is
    visually correct. Spot-checked stage 57 (Safer Sephiroth, whose real
    identity is independently confirmed via disassembly) against its own
    match and it's correct.

    Loaded once and cached; missing/corrupt file returns {} (features
    fall back to just the stage57.dat splice path).
    """
    global _battle_bg_dds_map_cache
    if _battle_bg_dds_map_cache is None:
        try:
            with open(BATTLE_BG_DDS_MAP_PATH, encoding='utf-8') as f:
                _battle_bg_dds_map_cache = json.load(f)
        except (OSError, ValueError):
            _battle_bg_dds_map_cache = {}
    return _battle_bg_dds_map_cache


def _synthesize_battle_bg_tex(dds_bytes):
    """
    Decode a STAGE##_T##_00.DDS mod tile (BC1/BC3/BC7, DX10 header) to RGBA
    and wrap it as a minimal truecolor .tex blob that tex.parse() accepts:
    a HEADER_LEN header (version=1, real width/height, bytes_per_pixel=4,
    palette_flag=0/palette_size=0) followed by raw BGRA pixel bytes -- the
    channel order tex.py's own truecolor readers expect on disk.

    Once wrapped, this is indistinguishable to the rest of the pipeline
    from a real PC-native truecolor .tex: it flows through the SAME
    hardware-proven _convert_battle_textures path (resize/quantize to
    <=256x256 paletted) already used for character/enemy skins.
    """
    rgba, w, h = dds_decode.decode_dds(dds_bytes)
    hdr = bytearray(tex.HEADER_LEN)
    struct.pack_into('<I', hdr, tex.O_VERSION, 1)
    struct.pack_into('<I', hdr, tex.O_WIDTH, w)
    struct.pack_into('<I', hdr, tex.O_HEIGHT, h)
    struct.pack_into('<I', hdr, tex.O_BYTES_PER_PIXEL, 4)
    struct.pack_into('<I', hdr, tex.O_PAL_FLAG, 0)
    struct.pack_into('<I', hdr, tex.O_PAL_SIZE, 0)
    img = Image.frombytes('RGBA', (w, h), rgba)
    r, g, b, a = img.split()
    bgra_bytes = Image.merge('RGBA', (b, g, r, a)).tobytes()
    return bytes(hdr) + bgra_bytes


def _synthesize_battle_bg_tex_cached(dds_path, native_name, log=lambda *_: None):
    """
    Disk-cached wrapper around _synthesize_battle_bg_tex, keyed on the
    source DDS file's signature (size+mtime) and the target name, so
    repeated builds skip the BC7 decode when the mod's DDS hasn't changed.
    Returns the cached/staged file path, or None on decode failure (logged,
    not raised -- one bad tile should not abort the whole build).
    """
    os.makedirs(BATTLE_BG_DDS_CACHE, exist_ok=True)
    key = f'{native_name}.{_sig(dds_path)}'
    staged = os.path.join(BATTLE_BG_DDS_CACHE, key)
    if os.path.isfile(staged):
        return staged
    try:
        with open(dds_path, 'rb') as f:
            dds_bytes = f.read()
        tex_bytes = _synthesize_battle_bg_tex(dds_bytes)
    except Exception as exc:
        log(f'  battle background: failed to decode {dds_path} for '
            f'{native_name} -- {exc}')
        return None
    tmp = staged + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(tex_bytes)
    os.replace(tmp, staged)
    # prune stale cache entries for this same target name
    for fn in os.listdir(BATTLE_BG_DDS_CACHE):
        if fn.startswith(native_name + '.') and fn != key:
            try:
                os.remove(os.path.join(BATTLE_BG_DDS_CACHE, fn))
            except OSError:
                pass
    return staged


_HRC_PIECE = re.compile(r'^\d+[ \t]+(.+)$', re.MULTILINE)
_RSD_PIECE = re.compile(r'(?:PLY|TEX\[\d+\])=(\S+)')


MODEL_PREFER_ENV = 'SEVENTH_NX_MODEL_PREFER'
MODEL_IGNORE_ENV = 'SEVENTH_NX_MODEL_IGNORE'

# Which overlay wins a model that several of them provide, best first. Matched
# as a case-insensitive substring against the source subfolder path.
#
# NinoStyle Chibi ships Efryt's Little Work and Dynamic Weapons with BYTE
# IDENTICAL AAAA.hrc files -- both add the `aaad1` bone that carries a weapon
# on Cloud's back -- so something has to break the tie. Efryt is first because
# it is the one that is complete on its own: it ships the sword mesh AAAE1.p
# and the recoloured cl.TEX its rsd samples, and neither changes at runtime.
DEFAULT_MODEL_PREFER = 'Efryt,Lazaro'

# Source subfolders excluded from model-set competition entirely.
#
# Dynamic Weapons is here because of what it is, not because it is broken. Its
# Chibi/Char ships AAAD1.rsd -- the weapon bone -- but NOT the AAAE1.p that
# rsd names. That mesh exists only inside its 16 per-weapon folders
# ("Dynamic Weapons/Cloud/01 - Buster Sword/char/AAAE1.p" and so on), which
# 7th Heaven copies in live as equipment changes. The Switch port has no
# mechanism for that, so the model can never be complete here; the same is
# true of its Cid and Aerith sets.
#
# The completeness gate below would reject it for those models anyway, which
# is a nice independent confirmation. It is named here as well so the choice
# is visible in the log rather than an emergent property. To try it anyway:
#     SEVENTH_NX_MODEL_IGNORE=""
#     SEVENTH_NX_MODEL_PREFER="Dynamic Weapons,Lazaro"
# It will then win exactly those models it can actually supply whole.
DEFAULT_MODEL_IGNORE = 'Dynamic Weapons'


def _model_pref():
    def split(env, default):
        raw = os.environ.get(env)
        if raw is None:
            raw = default
        return [s.strip().lower() for s in raw.split(',') if s.strip()]
    return (split(MODEL_PREFER_ENV, DEFAULT_MODEL_PREFER),
            split(MODEL_IGNORE_ENV, DEFAULT_MODEL_IGNORE))


def _assemble_models_atomically(plan, versions, log):
    """
    Keep model archives internally consistent, PER MODEL.

    Some mods ship several overlapping model sets (NinoStyle's fb/char plus
    Lazaro, Efryt and Dynamic Weapons). Merging them flat mixes a skeleton
    from one set with a mesh from another and breaks models.

    The first version of this function solved that by collapsing the whole
    archive to a single base set -- the subfolder providing the most skeletons
    -- and discarding every other overlapping set. That is safe and it is also
    far too blunt: those other folders are not competing FORKS of the mod,
    they are its own options, all enabled by default, and 7th Heaven layers
    them. Collapsing to `fb/char` silently threw away
    every one of them. Efryt's Little Work -- "Buster Sword on Cloud's back"
    -- ships a complete 16-part AAAA.hrc against fb's 15, and the whole
    option went in the bin because its skeleton names collided with the base.
    The old log line even reported it: "dropped 4 from overlapping alternates"
    were the sword's own parts.

    Atomicity is a property of a MODEL, not of an archive. So: for each .hrc,
    pick one winning subfolder and take that model's entire part list from it.
    A subfolder is only eligible for a model if it provides EVERY rsd its own
    copy of the .hrc names, and every .p those rsds name -- i.e. it ships the
    model complete. A folder that ships half a model can never win, which is
    the property the old collapse was really protecting.

    Ranking, best first:
      1. explicit preference (SEVENTH_NX_MODEL_PREFER) -- see DEFAULT_MODEL_PREFER;
      2. more parts, so an overlay that ADDS a bone beats the base;
      3. the base set, so an overlay that merely restates the base changes
         nothing;
      4. name, so the result is deterministic.

    Folders listed in SEVENTH_NX_MODEL_IGNORE never compete.

    Models nobody ships completely, files belonging to no model (shared
    textures, the 60 FPS mod's 3,208 .a animation files) and archives with no
    overlap at all (battle.lgp) are left exactly as the normal load-order
    resolution left them.
    """
    prefer, ignore = _model_pref()

    for target, byname in versions.items():
        if target == 'flevel.lgp':
            continue
        bucket = plan.archive_files.get(target)
        if not bucket:
            continue

        overlap = any(len({s for s, _, _ in vs}) > 1 for vs in byname.values())
        hrcs_of = {}
        for low, vs in byname.items():
            if low.endswith('.hrc'):
                for s, _, _ in vs:
                    hrcs_of.setdefault(s, set()).add(low)
        if not overlap or not hrcs_of:
            continue

        base = max(hrcs_of, key=lambda s: (len(hrcs_of[s]), s))
        model_subs = set(hrcs_of)

        def src_file(low, sub):
            for s, f, m in byname.get(low, ()):
                if s == sub:
                    return f, m
            return None

        def parts_of(hrc_low, sub):
            """
            (geometry entry names, texture entry names, complete?)

            Completeness is a GEOMETRY property only. A model must bring its
            own skeleton, every rsd and every .p, because those three describe
            each other and mixing them is what breaks a model. Textures are
            deliberately not required: sheets are shared between models
            (NinoStyle's cl.TEX serves several), and a mod that recolors one
            character legitimately ships a texture and nothing else.
            """
            got = src_file(hrc_low, sub)
            if not got:
                return [], [], False
            geom, tex, ok = [], [], True
            for rsd in _hrc_parts(_read(got[0])):
                key = rsd + '.rsd'
                geom.append(key)
                rf = src_file(key, sub)
                if rf is None:
                    ok = False
                    continue
                ply, ts = _rsd_refs(_read(rf[0]))
                tex += [t + '.tex' for t in ts]
                if ply:
                    key = ply + '.p'
                    geom.append(key)
                    if src_file(key, sub) is None:
                        ok = False
            return geom, tex, ok

        # ---- pick a winner per model -----------------------------------
        def rank(item):
            sub, parts = item
            pi = next((i for i, p in enumerate(prefer) if p in sub.lower()),
                      len(prefer))
            return (pi, -len(parts), 0 if sub == base else 1, sub.lower())

        owner = {}                      # part entry name -> winning subfolder
        tex_claims = {}                 # texture entry name -> {subfolder}
        moved = []
        for hrc_low in sorted({h for hs in hrcs_of.values() for h in hs}):
            cand = []
            for sub in sorted(hrcs_of):
                if hrc_low not in hrcs_of[sub]:
                    continue
                if any(g in sub.lower() for g in ignore):
                    continue
                geom, tex, ok = parts_of(hrc_low, sub)
                if ok:
                    cand.append((sub, geom, tex))
            if not cand:
                continue
            cand.sort(key=lambda c: rank((c[0], c[1])))
            win, geom, tex = cand[0]
            owner[hrc_low] = win
            for p in geom:
                owner.setdefault(p, win)
            # A texture only follows the model if the WINNER ships it. Efryt's
            # Little Work recolors cl.TEX and its sword rsd samples cl.TIM, so
            # taking Efryt's mesh with the base sheet would texture the sword
            # off the wrong image. Claims are collected and resolved after all
            # models are decided, because one sheet can serve several.
            for t in tex:
                if src_file(t, win) is not None:
                    tex_claims.setdefault(t, set()).add(win)
            if win != base and len(cand) > 1:
                moved.append((hrc_low, win, len(geom),
                              [c[0] for c in cand[1:]]))

        retex = 0
        for t, subs in tex_claims.items():
            best = min(subs, key=lambda s: rank((s, ())))
            if best != base and owner.get(t) != best:
                owner[t] = best
                retex += 1

        # ---- apply ------------------------------------------------------
        kept = claimed = dropped = data_only = 0
        for low, vs in byname.items():
            subs = {s for s, _, _ in vs}
            if not (subs & model_subs):
                data_only += 1
                continue
            want = owner.get(low)
            if want is not None:
                v = src_file(low, want)
                if v is not None:
                    bucket[low] = v
                    plan.folder_of.setdefault(target, {})[low] = want
                    if want == base:
                        kept += 1
                    else:
                        claimed += 1
                    continue
            # Not part of any model we resolved. Keep it if the base set (or
            # any non-ignored folder) provides it; otherwise fall back to
            # vanilla rather than leave an orphaned piece of an alternate.
            fallback = None
            for sub in [base] + sorted(subs - {base}):
                if any(g in sub.lower() for g in ignore):
                    continue
                v = src_file(low, sub)
                if v is not None:
                    fallback = (sub, v)
                    break
            if fallback is not None:
                bucket[low] = fallback[1]
                plan.folder_of.setdefault(target, {})[low] = fallback[0]
                kept += 1
            elif low in bucket:
                del bucket[low]
                plan.folder_of.get(target, {}).pop(low, None)
                dropped += 1

        msg = (f'  {target}: base model set "{base}" ({kept} files)')
        if claimed:
            msg += f'; {claimed} file(s) claimed by overlay models'
        if retex:
            msg += f' (incl. {retex} texture(s) following their model)'
        if dropped:
            msg += f'; dropped {dropped} orphaned piece(s)'
        if data_only:
            msg += f'; kept {data_only} from data-only folders'
        log(msg)
        if moved:
            log(f'  {target}: {len(moved)} model(s) taken from an overlay '
                'instead of the base set:')
            for hrc_low, win, n, others in moved[:15]:
                msg = f'      {hrc_low}: "{win}" ({n} parts)'
                if others:
                    msg += f'   [also offered by {", ".join(others)}]'
                log(msg)
            if len(moved) > 15:
                log(f'      ... and {len(moved) - 15} more')
        if ignore:
            log(f'      ({MODEL_IGNORE_ENV}: not competing -- '
                f'{", ".join(ignore)})')


def _warn_frankenstein(plan, log):
    """
    Flag models whose skeleton (.hrc) and geometry pieces end up coming from
    different enabled options. That is the only overlap that actually breaks a
    model (missing part, wrong mesh). Raw file overlap between options is
    normal and not reported. Only char.lgp uses this model structure.
    """
    files = plan.archive_files.get('char.lgp')
    folder_of = plan.folder_of.get('char.lgp')
    if not files or not folder_of:
        return
    names = set(files)

    def resolve(ref):
        ref = ref.lower()
        for c in (ref, ref + '.rsd'):
            if c in names:
                return c
        return None

    broken = []
    pair_counts = Counter()
    for low in list(files):
        if not low.endswith('.hrc'):
            continue
        hrc_folder = folder_of.get(low)
        try:
            text = open(files[low][0], 'rb').read().decode('latin1')
        except OSError:
            continue
        for m in _HRC_PIECE.finditer(text):
            for tok in m.group(1).split():
                e = resolve(tok.strip())
                if e and folder_of.get(e) and folder_of[e] != hrc_folder:
                    broken.append(low)
                    pair_counts[frozenset((hrc_folder, folder_of[e]))] += 1
                    break
            else:
                continue
            break

    if not broken:
        return
    plan.folder_conflicts = [(sorted(p)[0], sorted(p)[1], n)
                             for p, n in pair_counts.most_common()]
    log(f'NOTE: {len(broken)} model(s) draw pieces from two different enabled '
        'options and may look wrong:')
    for low in broken[:8]:
        log(f'    {low}')
    for a, b, n in plan.folder_conflicts:
        log(f'  conflict between options "{a or "(base)"}" and '
            f'"{b or "(base)"}"')
    log('  This is a conflict inside the mod, not a packing error. If one of '
        'these models looks wrong, turn one of those two options off.')


# --------------------------------------------------------------------------
# WHERE THE GAME IS
#
# Everything this tool reads comes out of one Switch dump, so it should be
# able to point at one folder rather than have the working data, the exe and
# the NSO scattered next to the scripts. A v1.0.3_5 dump looks like:
#
#     dump/
#       exefs/
#         main  main.npdm  rtld  sdk  subsdk0  subsdk1  subsdk2
#       romfs/
#         be_loc/
#         ff7/
#           font/  resources/  shaders/  workingdir/
#
# which supplies every input in one place:
#
#     workingdir  romfs/ff7/workingdir          the LGP archives
#     exe         romfs/ff7/resources/ff7_1.02/ff7_en
#     nso         exefs/main
#     shaders     romfs/ff7/shaders
#
# The older layout -- a bare `workingdir/` beside the scripts -- still works
# and is still searched, so nobody's existing setup breaks.
DUMP_ENV = 'SEVENTH_NX_DUMP'
DUMP_WORKINGDIR = os.path.join('romfs', 'ff7', 'workingdir')
DUMP_EXE = os.path.join('romfs', 'ff7', 'resources', 'ff7_1.02', 'ff7_en')
DUMP_NSO = os.path.join('exefs', 'main')
DUMP_SHADERS = os.path.join('romfs', 'ff7', 'shaders')


class GameDump:
    """Resolved input paths. `kind` is 'dump' or 'workingdir'."""

    def __init__(self, root, kind, workingdir):
        self.root = root
        self.kind = kind
        self.workingdir = workingdir

    def _sub(self, rel):
        if self.kind != 'dump':
            return None
        p = os.path.join(self.root, rel)
        return p if os.path.exists(p) else None

    @property
    def exe(self):
        return self._sub(DUMP_EXE)

    @property
    def nso(self):
        return self._sub(DUMP_NSO)

    @property
    def shaders(self):
        return self._sub(DUMP_SHADERS)

    def describe(self):
        if self.kind != 'dump':
            return 'workingdir found (no full dump)'
        have = [n for n, p in (('exe', self.exe), ('exefs/main', self.nso),
                               ('shaders', self.shaders)) if p]
        return 'game dump found' + (' — %s' % ', '.join(have) if have else '')


def _looks_like_dump(path):
    return os.path.isdir(os.path.join(path, DUMP_WORKINGDIR))


def find_game_dump(here, log=lambda *_: None):
    """
    Locate the game data. Returns a GameDump, or None if nothing is found.

    Search order:
      1. $SEVENTH_NX_DUMP                     -- explicit wins
      2. <here>/dump                          -- the recommended layout
      3. <here>/workingdir                    -- the old layout
      4. any immediate subfolder of <here> that contains romfs/ff7/workingdir
         (so an unpacked dump keeps working whatever it was named)
    """
    env = os.environ.get(DUMP_ENV)
    if env:
        env = os.path.expanduser(env)
        if _looks_like_dump(env):
            return GameDump(env, 'dump', os.path.join(env, DUMP_WORKINGDIR))
        if os.path.isdir(env):
            # Pointed straight at a workingdir, or at romfs/ff7.
            for rel in ('', DUMP_WORKINGDIR, os.path.join('ff7', 'workingdir'),
                        'workingdir'):
                p = os.path.join(env, rel) if rel else env
                if os.path.isdir(os.path.join(p, 'data')):
                    return GameDump(env, 'workingdir', p)
        log(f'! {DUMP_ENV}={env} does not look like a dump or a workingdir')

    cand = os.path.join(here, 'dump')
    if _looks_like_dump(cand):
        return GameDump(cand, 'dump', os.path.join(cand, DUMP_WORKINGDIR))

    legacy = os.path.join(here, 'workingdir')
    if os.path.isdir(legacy):
        return GameDump(here, 'workingdir', legacy)

    try:
        subs = sorted(d for d in os.listdir(here)
                      if os.path.isdir(os.path.join(here, d)))
    except OSError:
        subs = []
    for d in subs:
        p = os.path.join(here, d)
        if _looks_like_dump(p):
            return GameDump(p, 'dump', os.path.join(p, DUMP_WORKINGDIR))
    return None


def load_catalogs(workingdir, log=lambda *_: None):
    catalogs, paths = {}, {}
    for name, rel in ARCHIVES.items():
        path = os.path.join(workingdir, rel)
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'rb') as f:
                creator = f.read(12)
                if not creator.endswith(b'SQUARESOFT'):
                    continue
                count = struct.unpack('<i', f.read(4))[0]
                names = set()
                for _ in range(count):
                    e = f.read(27)
                    names.add(e[:20].split(b'\0')[0]
                              .decode('ascii', 'replace').lower())
            catalogs[name] = names
            paths[name] = path
            log(f'  {name}: {len(names)} entries')
        except Exception as exc:
            log(f'  ! {name}: {exc}')
    return catalogs, paths


TEXCONV_CACHE = os.path.join(HERE, 'cache', '_texconv')
TEXCAP_CACHE = os.path.join(HERE, 'cache', '_texcap')
DEBLEED_CACHE = os.path.join(HERE, 'cache', '_debleed')

# Field/world texture dimension cap, in pixels. Off (None) by default --
# the field/world modules are documented above as fine with full-size
# truecolor, proven on hardware with the mod sizes tested so far. This is
# an OPT-IN escape valve for mod sets that push field model textures far
# past what was tested (some Nino-style packs upscale every NPC skin to
# 512-1024px; a scene that loads a dozen-plus of them at once multiplies
# that fast), exposed as a GUI/CLI choice rather than a silent default so
# turning it on is a deliberate, visible trade of texture fidelity for
# headroom. 0 or unset disables it -- exact current behaviour.
FIELD_TEX_CAP_ENV = 'SEVENTH_NX_FIELD_TEX_CAP'

# Colour-key de-fringing for field/world model textures. ON by default: it is
# the one texture change in this file that provably cannot alter the image --
# it rewrites palette entry 0 and nothing else, and every index byte is checked
# to be identical afterwards. See tex.debleed() for the measurement that
# motivates it (73% of vanilla char.lgp textures have a filtering boundary
# against a black transparent entry). Set to 1 to turn it off for A/B.
NO_DEBLEED_ENV = 'SEVENTH_NX_NO_DEBLEED'

# Battle background texture cap -- same idea as FIELD_TEX_CAP_ENV above, but
# scoped to ONLY the tiles this tool itself synthesizes from Avalanche
# Arisen's DDS mod (see _synthesize_battle_bg_tex / BATTLE_BG_DDS_MAP_PATH).
# Every other file that lands in battle.lgp/magic.lgp -- vanilla, or any
# other mod's character/enemy skins -- keeps the hardware-proven 256px
# default untouched, regardless of this setting.
#
# Unlike the field cap, this has no "off" state: the battle module always
# needs SOME target resolution for the paletted conversion, and 256 is the
# only value proven correct on hardware so far, so it's also what an unset
# env var falls back to (see _battle_bg_tex_cap). Values above 256 are an
# explicit, visible trade: the mod's own art tops out at 1024px per tile,
# but whether the battle module's texture bind path tolerates more than the
# 256px enemy/character textures have been tested at is unverified -- this
# is the one-click way to try it and revert if it looks wrong in-game.
BATTLE_BG_TEX_CAP_ENV = 'SEVENTH_NX_BATTLE_BG_TEX_CAP'

# Field BACKGROUND budget, in 256x256 texture pages per field. The third
# member of the cap family, and the one that could not be expressed in
# pixels no matter how much one would like the symmetry.
#
# WHY NOT PIXELS
# --------------
# The other two caps resize a texture. A field background cannot be
# resized: section 9 of a field file stores its art as up to 42 pages of
# EXACTLY 256x256 8-bit indexed pixels, and every tile sprite in the layer
# lists addresses a (page, x, y) inside them. Rewriting a page at another
# size invalidates every sprite that samples it, so "cap this background at
# 512px" has no meaning -- the pages are already 256px and always were.
# Cosmos Limit Break's own upscale is not in this data at all; it lives in
# the 18,350 FFNx .dds files this port cannot load. What its flevel.lgp
# sections DO carry is the widescreen EXTENSION, which pays for the wider
# image with more pages: vanilla `nivl_b22` uses 8, the mod's uses 12.
#
# So the quantity worth bounding is pages, which is exactly the background
# art the module has to hold for one field: 64 KB apiece. A field over
# budget keeps its Switch-vanilla background section -- unchanged, correct,
# just not extended -- rather than being dropped or half-applied.
#
# Unset/0 disables it, matching FIELD_TEX_CAP_ENV, so a build with nothing
# set behaves exactly as it did before this existed.
def _log_page_cost_report(page_cost, px, log, ng_retry=0,
                          ng_over=None, ng_margin=0):
    """
    What every field will ask the driver for at this page size.

    HANDOFF-52 3.4 item 4. The build already parses all 709 sections, so this
    costs nothing and answers the question the reboot-and-look loop was
    answering slowly: which fields are the expensive ones, and how close is
    the worst of them to the ~18 MB where black bars appeared.

    The ceiling is deliberately NOT enforced -- failure was measured
    non-monotonic in the budget (black at 18 MB, clean at 14), so no number
    here is a law. It is reported so the page size can be chosen against real
    fields rather than against the arithmetic in a handoff.
    """
    if not page_cost:
        return
    MB = 1048576.0
    page_cost.sort(key=lambda r: -r[2])
    worst_name, worst_pages, worst_bytes = page_cost[0][:3]
    total = sum(r[2] for r in page_cost)
    log('')
    log(f'  field background cost at {px}x{px} '
        f'({field_bg_repack._page_bytes(px, 2) / MB:.2f} MB per truecolor '
        f'page, {field_bg_repack._page_bytes(256, 1) / MB:.2f} MB per '
        f'vanilla paletted one):')
    log(f'      heaviest field  {worst_name} -- {worst_pages} page(s), '
        f'{worst_bytes / MB:.2f} MB')
    log(f'      mean {total / len(page_cost) / MB:.2f} MB across '
        f'{len(page_cost)} field(s)')

    # PAGE COUNT, reported because bytes are not what breaks.
    # field_load_textures makes one texture per present page and abandons the
    # loop on the first failure, so this line is the one to read.
    by_pages = sorted(page_cost, key=lambda r: -r[1])
    # GREW relative to what this field STARTED with, per field. That is the
    # only comparison that means anything -- a field the mod already ships
    # with 12 pages has not grown by still having 12.
    over = [r for r in page_cost if r[3] is not None and r[1] > r[3]]
    log(f'      pages per field: max {by_pages[0][1]} ({by_pages[0][0]}), '
        f'mean {sum(r[1] for r in page_cost) / len(page_cost):.1f}')
    if over:
        over.sort(key=lambda r: r[3] - r[1])
        names = ', '.join('%s (%d->%d)' % (n, b, k)
                          for n, k, _c, b in over[:8])
        log(f'      {len(over)} field(s) GREW their page count: {names}'
            + (' ...' if len(over) > 8 else ''))
        log(f'      Every page is a texture, and field_load_textures '
            f'(x86 0x640292) abandons the whole loop on the first one it '
            f'cannot allocate -- every page after it draws nothing, which is '
            f'what scattered black squares are. Lower '
            f'{field_bg_repack.MAX_TOTAL_PAGES_ENV} (currently '
            f'{field_bg_repack.max_total_pages() or "unlimited"}).')
    # WHAT THE NO-GROWTH LOOP ACTUALLY DID, as a number rather than a promise.
    # The loop was missing entirely in the build that grew 123 fields while
    # the SAFETY line below asserted it could not.
    if ng_retry:
        log(f'      no-growth: {ng_retry} field(s) had the dense repack '
            f'RE-RUN at a lower truecolor ceiling because they still held '
            f'more pages than they started with after compaction. That is '
            f'the loop working -- it costs colour depth in those fields and '
            f'buys back the textures the loader has to allocate.')
    if ng_margin:
        log(f'      no-growth: {ng_margin} field(s) hold more pages than the '
            f'MOD ships because ff7nx_marginpage added a palette-pure margin '
            f'page to them. That page is not optional -- it is what stops the '
            f'margin being drawn through a foreign colour table -- and it is '
            f'NOT charged to the repack. MEASURED: charging it cost 285 '
            f'field(s) their promotion and did not remove the page anyway.')
    if ng_over:
        names = ', '.join('%s (%d->%d)' % (n, b, k) for n, b, k in ng_over[:8])
        log(f'      ! no-growth: {len(ng_over)} field(s) are STILL over their '
            f'starting page count with the repack fully disabled, so the '
            f'growth is not this pass\'s -- it is ff7nx_marginpage\'s extra '
            f'page. {names}' + (' ...' if len(ng_over) > 8 else ''))
    for label, ceiling in (('over 18 MB (black bars were MEASURED here)', 18.0),
                           ('over 14 MB (clean on hardware at this size)',
                            14.0),
                           ('over 6 MB', 6.0)):
        over = [r for r in page_cost if r[2] / MB > ceiling]
        if over:
            names = ', '.join('%s (%.1f MB)' % (n, b / MB)
                              for n, _p, b, _bf in over[:8])
            log(f'      {len(over)} field(s) {label}: {names}'
                + (' ...' if len(over) > 8 else ''))
            break
    else:
        log(f'      no field exceeds 6 MB -- the whole archive is comfortably '
            f'inside everything ever measured')
    log(f'      SAFETY: {field_bg_repack.safety_note()}')
    _log_render_target_match(px, log)
    _log_uniformity(px, log)


def _log_uniformity(px, log):
    """
    Whether this page size can produce a UNIFORM picture at all.

    It usually cannot, and the reason is structural rather than budgetary.
    A truecolor page has no index channel, so 0x0000 has to mean transparent
    (x86 0x6470E0) -- which means a cell carrying a COLOUR KEY can never be
    promoted, and the paletted page holding it stays 256x256 forever. The
    depth-1 page size is deliberately not patched (the loader's #0x10000 is
    shared with the depth-1 read count), so it cannot follow.

    MEASURED over all 709 fields of a real flevel:

        281 (40%)  have no fx-page tiles
         32 ( 5%)  have no colour-key pixels
         32 ( 5%)  have NEITHER -- the only fields that could ever be
                   100% truecolor

    So at any page size above 256, 95% of fields are GUARANTEED to mix a
    px-sized truecolor page with a 256px paletted one, and that mismatch is a
    resolution difference, which is visible. At exactly 256 the two are the
    same size and differ only in colour depth, which is not.

    No budget, ceiling or promotion mode changes this. It is the reason a
    bigger page size trades uniformity for sharpness rather than simply
    buying quality.
    """
    if px <= field_bg_native.VANILLA_PX:
        log('      uniform: promoted and unpromoted pages are both '
            f'{px}x{px} here, so they differ only in COLOUR DEPTH, not '
            'resolution. This is the only page size that can look uniform '
            '-- see the note below.')
        return
    if field_bg_repack.replace_only():
        log(f'      WORST-LOOKING COMBINATION: replace-only qualifies only '
            f'13% of pages (437 of 3,315, measured), so this build puts '
            f'{px}px pages beside 256px ones at a ratio of about 1 to 7. '
            f'Replace-only is the safest setting for MEMORY and the worst '
            f'for APPEARANCE at any size above 256 -- the two goals point in '
            f'opposite directions here.')
    log(f'      NOT UNIFORM: some pages stay 256x256 PALETTED, so at {px}px '
        f'they sit next to {px // 256}x sharper neighbours in the same '
        f'picture. How many is a moving number -- read DENSE REPACK above '
        f'for this build rather than trusting a figure quoted here.')
    log('      CORRECTED, and the old text here was wrong: it claimed a '
        'truecolor page CANNOT hold a colour key and that only 32 of 709 '
        'fields could ever be 100% truecolor, "no budget or ceiling changes '
        'this". MEASURED in the UNMODIFIED game: vanilla ships 1,091,741 '
        'truecolor texel(s) equal to 0x0000 across 26 field(s) -- cosmo, '
        'cosmo2, fr_e, gaiin_6, gaiin_7, blin67_4 and others. If 0x0000 drew '
        'opaque there the stock game would have black rectangles in all 26. '
        'It does not. 0x0000 means TRANSPARENT on a depth-2 page, which is '
        'what a cut-out needs, so the key is NOT a barrier to promotion. '
        'FINDINGS-152.')


def _log_render_target_match(px, log):
    """
    Does the field RENDER TARGET match the page size?

    These are two independent settings that come out of ONE pool, and getting
    them out of step is expensive in exactly the way that produces black
    squares. A background tile is 16 texels covering 32 game units, so a page
    of `px` gives cells of `px/16` texels and needs a target of
    `px/16 / 32 * 853` pixels across before any of that detail can appear:

        128px pages -> 1x (428x240)      3.13 MB
        256px pages -> 1x (428x240)      3.13 MB
        512px pages -> 2x (854x480)     12.51 MB
        768px pages -> 3x (1280x720)    28.12 MB
        1024px pages -> would need 4x, which does not exist

    THIS IS A QUALITY REPORT, NOT A MEMORY ONE. Do not read a mismatch here
    as a cause of dropped textures.

    RENDER-TARGET MEMORY IS A DEAD THEORY, KILLED TWICE ON HARDWARE:
    HANDOFF-52 1.2 predicted that dropping 3x -> 1x would fix the bands and
    it did not, and it was re-tested for the black squares with the same
    result -- the squares do not care what the field render resolution is.
    HANDOFF-52 6 lists "the bands are the render-target memory" as a
    correction to an earlier wrong claim, and it was then re-derived a second
    time from a pool argument, which is why this warning is written here in
    the code rather than left in a document.

    So the only advice this function gives is to RAISE THE PAGE SIZE to match
    the target already chosen. It never suggests lowering the render
    resolution -- that is not a solution, it is a regression, and it does not
    address the symptom anyway.
    """
    MB = 1048576.0
    targets = {1: (428, 240), 2: (854, 480), 3: (1280, 720)}
    try:
        import ff7nx_fieldbuf
        have = ff7nx_fieldbuf.env_scale()
    except Exception:                                          # noqa: BLE001
        return
    if have not in targets:
        return
    need_w = (px / 16.0) / 32.0 * 853.333
    want = next((k for k in sorted(targets)
                 if targets[k][0] >= need_w - 1), None)
    hw, hh = targets[have]
    log(f'      render target {have}x ({hw}x{hh}, '
        f'{hw * hh * 4 * 8 / MB:.2f} MB for 8 buffers)')
    if want is None:
        log(f'      {px}px pages would need a target wider than 3x to be '
            f'resolved at all; some of that detail can never appear.')
    elif have > want:
        bigger = [k for k in sorted(targets) if k == have]
        # what page size would this target actually resolve?
        matched_px = None
        for cand in (1024, 768, 512, 256, 128):
            if (cand / 16.0) / 32.0 * 853.333 <= targets[have][0] + 1:
                matched_px = cand
                break
        log(f'      the {have}x target can resolve pages up to '
            f'{matched_px}px; at {px}px it is upscaling {px}px of detail to '
            f'fill it. RAISING the page size is what uses the target you '
            f'already have -- lowering the render resolution is not '
            f'suggested and does not affect dropped textures (hardware, '
            f'twice).')
        del bigger
    elif have < want:
        ww, wh = targets[want]
        log(f'      the target is smaller than {px}px pages can fill; {want}x '
            f'({ww}x{wh}) is what resolves them fully. Some of that detail '
            f'cannot reach the screen.')
    else:
        log(f'      matched: {px}px pages are exactly resolved at {have}x')


FIELD_BG_CAP_ENV = 'SEVENTH_NX_FIELD_BG_CAP'

# SUPERSEDED, 2026-08-01. The paragraph above is still an accurate
# description of the FORMAT, but the conclusion drawn from it -- "there is no
# dimension to resize" -- was wrong. The 256 is a literal in `exefs/main`, in
# six words, and it can be moved: see ff7nx_fieldbg.py and
# README-field-bg-512-MEASURED.md.
#
# The page-COUNT cap it created is inert in practice. Cosmos Limit Break's
# fields run 1-15 pages against a 42-page ceiling, so at every setting the
# GUI offered, zero fields were held back. It is kept here so an existing
# settings.json or a scripted build does not change behaviour, but the GUI
# control is gone, replaced by the page-SIZE setting below.
#
# ff7nx_fieldbg.PAGE_PX_ENV ('SEVENTH_NX_FIELD_BG_PAGE_PX') is the real
# control: 256 (off), 512 or 1024, and it patches the module AND rewrites
# flevel section 9 so the two agree.

# Compressed field payloads, keyed by the hash of their raw bytes. The
# compressor is pure Python and the verify pass decompresses everything it
# produces, which together cost a couple of minutes across a 683-field mod
# -- once. See _encode_field_cached.
FIELDLZS_CACHE = os.path.join(HERE, 'cache', '_fieldlzs')

# Field background sections address at most this many texture pages, each
# 256x256 at 1 or 2 bytes per pixel. Both numbers are format constants, not
# choices.
BG_MAX_PAGES = 42
BG_PAGE_BYTES = 256 * 256


PFIX_CACHE = os.path.join(HERE, 'cache', '_pfix')

# battle.lgp two-letter model prefixes -> enemy names (community list,
# Kuroda Masahiro, Qhimm forums topic 7613). Only used for log readability.
ENEMY_NAMES = {
    'aq': 'MP', 'ar': 'Guard Hound', 'as': 'Mono Drive', 'at': 'Grunt',
    'au': '1st Ray', 'av': 'Sweeper', 'aw': 'Guard Scorpion',
    'ax': 'Grashstrike', 'ay': 'Rocket Launcher', 'az': 'Whole Eater',
    'bd': 'Smogger', 'be': 'Special Combat', 'bf': 'Blood Taste',
    'bg': 'Proto Machinegun', 'bh': 'Airbuster', 'bi': 'Vice',
    'bj': "Corneo's Lackey", 'bk': 'Scotch', 'bl': 'APS', 'bm': 'Sahagin',
    'bs': 'Hell House', 'bt': 'Hell House (angry)',
    'bu': 'Aero Combatant (fly)', 'bv': 'Aero Combatant',
    'bw': 'Reno (Pillar)', 'cb': 'Hammer Blaster', 'cc': "Blaster's pod",
    'ce': 'Soldier 3rd', 'cf': 'Mighty Grunt', 'cg': 'Mighty Grunt (weak)',
    'ch': 'Moth Slasher', 'ci': 'Grenade Combatant', 'cm': 'Sample HO512',
    'cn': 'HO512 (small)', 'co': 'Hundred Gunner', 'cp': 'Heli Gunner',
    'cq': 'Rufus', 'cr': 'Dark Nation', 'cs': 'Heli', 'ct': 'Motorball',
    'cu': 'Devil Ride', 'cv': 'Custom Sweeper', 'cx': 'Prowler',
    'dn': 'Bottomswell', 'ee': 'Bandit', 'eh': 'Dyne',
    'ex': 'Reno (Gongaga)', 'ey': 'Rude (Gongaga)', 'gi': 'Palmer',
    'ic': 'Snow', 'je': 'Death Machine', 'jp': 'Submarine Crew',
    'jq': 'Submarine Captain', 'jr': 'Underwater MP',
    'ko': 'Reno (Gelnika)', 'kp': 'Rude (Gelnika)', 'kv': 'Rude (Rocket)',
    'lk': 'Elena', 'll': 'Reno (Midgar raid)', 'lm': 'Rude (Midgar raid)',
    'lt': 'Hojo', 'nb': 'Chocobo', 'nc': 'Chocobo', 'nd': 'Chocobo',
    'ne': 'Chocobo', 'nf': 'Chocobo', 'ng': 'Chocobo', 'nh': 'Chocobo',
    'ni': 'Chocobo', 'nj': 'Chocobo', 'nk': 'Chocobo', 'nl': 'Chocobo',
    'nm': 'Chocobo', 'nn': 'Chocobo', 'no': 'Chocobo', 'sl': 'Hellmasker',
}


def _apply_battle_experiments(mod_files, folder_of, log):
    """
    Hardware-isolation experiments, e.g.:
        SEVENTH_NX_EXPERIMENT=texonly:ar,geoonly:as
    texonly:XX -> apply ONLY the mod's texture slots (XXac..XXal) for that
    model; skeleton/parts/anims stay Switch-vanilla. The model will look
    UV-scrambled -- intended; the question is whether it still does the
    death dissolve.
    geoonly:XX -> apply everything EXCEPT textures (vanilla textures).
    Also scrambled-looking; same question, opposite variable.
    """
    spec = os.environ.get('SEVENTH_NX_EXPERIMENT', '').strip()
    if not spec:
        return mod_files
    modes = {}
    for item in spec.split(','):
        if ':' in item:
            mode, pfx = item.split(':', 1)
            modes[pfx.strip().lower()] = mode.strip().lower()
    if not modes:
        return mod_files
    out = {}
    dropped = Counter()
    for low, val in mod_files.items():
        mode = modes.get(low[:2]) if len(low) == 4 and low.isalpha() else None
        if mode:
            is_texture = 'ac' <= low[2:] <= 'al'
            if (mode == 'texonly' and not is_texture) \
                    or (mode == 'geoonly' and is_texture):
                dropped[low[:2]] += 1
                continue
        out[low] = val
    for pfx, n in sorted(dropped.items()):
        who = ENEMY_NAMES.get(pfx, pfx)
        log(f'  EXPERIMENT {modes[pfx]} {pfx} ({who}): dropped {n} mod '
            'file(s), Switch-vanilla used for those')
    return out


def _transplant_render_state(mod_files, van, folder_of, log):
    """
    Copy each vanilla battle part's "hundreds" render-state block into the
    replacing mod part (geometry untouched). The death dissolve re-renders
    parts using this state; exporter-default state in mod parts is the
    prime remaining suspect for the missing dissolve. Skips player files
    (Mains) to preserve the byte-identical regression build. Disable with
    SEVENTH_NX_NO_VANILLA_HUNDREDS=1.
    """
    os.makedirs(PFIX_CACHE, exist_ok=True)
    folder_of = folder_of or {}
    out = {}
    done = skipped = 0
    for low, (src, mod) in mod_files.items():
        if (len(low) != 4 or not low.isalpha() or low[2:] < 'am'
                or low[2:] == 'da'
                or 'main' in str(folder_of.get(low, '')).lower()):
            out[low] = (src, mod)
            continue
        try:
            with open(src, 'rb') as f:
                mod_bytes = f.read()
            if low in van:
                with open(van[low], 'rb') as f:
                    van_bytes = f.read()
                new, why = pfile.transplant_hundreds(mod_bytes, van_bytes)
            else:
                new, why = None, 'no vanilla counterpart'
            # part doctor: exporter-anomaly fixes (vcolType/normIdxFlag/
            # vertex alphas) -- the death-effect path re-renders parts via
            # vertex colors, and the mod's parts declare themselves
            # colorless. Applied whether or not the transplant ran.
            fixed, fwhy = pfile.normalize_part(new if new else mod_bytes)
            if fixed is not None:
                new, why = fixed, (why + ' + ' if new else '') + fwhy
        except OSError:
            new, why = None, 'io error'
        if new is None:
            if why not in ('already identical', 'unparseable P file',
                           'already normal', 'no vanilla counterpart'):
                skipped += 1
            out[low] = (src, mod)
            continue
        van_sig = _sig(van[low]) if low in van else 'novan'
        cached = os.path.join(
            PFIX_CACHE,
            f'{low}.{hashlib.sha1(("PFIX-V7-" + _sig(src) + van_sig).encode()).hexdigest()[:16]}')
        if not os.path.exists(cached):
            with open(cached, 'wb') as f:
                f.write(new)
        out[low] = (cached, mod)
        done += 1
    if done or skipped:
        log(f'  render-state: {done} part(s) given vanilla hundreds '
            f'(death-dissolve state), {skipped} skipped on group mismatch '
            '(set SEVENTH_NX_NO_VANILLA_HUNDREDS=1 to disable)')
    return out


def _files_equal(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, 'rb') as fa, open(b, 'rb') as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def _battle_enemy_report(mod_files, van, folder_of, log):
    """
    Per-enemy component diff for battle.lgp, and the vanilla-animation
    experiment toggle.

    Battle model entries are 4-letter names: 2-letter model prefix + slot
    suffix (aa=skeleton, ab=animation script incl. the DEATH sequence,
    ac..al=textures, am+=body parts, da=animation data). The enemy death
    "red dissolve" is triggered from the death sequence, so if a mod ships
    a CHANGED ab/da the dissolve can be lost even when the model itself is
    fine. This logs, per non-player model, which slots actually differ from
    vanilla -- that report is what lets us attribute hardware symptoms
    (black texture / no dissolve) to a specific slot.

    With SEVENTH_NX_KEEP_VANILLA_ANIM=1, changed ab/da files are dropped so
    the Switch keeps its own animation scripts/data -- but only for models
    whose skeleton (aa) is byte-identical to vanilla, where vanilla
    animations are guaranteed to fit.
    """
    keep_van_anim = os.environ.get('SEVENTH_NX_KEEP_VANILLA_ANIM') == '1'
    folder_of = folder_of or {}
    groups = {}
    for low in mod_files:
        if len(low) == 4 and low.isalpha():
            if 'main' in str(folder_of.get(low, '')).lower():
                continue
            groups.setdefault(low[:2], []).append(low)

    anim_changed = skel_changed = 0
    dropped = []
    for pfx, lows in sorted(groups.items()):
        diffs = {}
        for low in lows:
            vp = van.get(low)
            src = mod_files[low][0]
            diffs[low[2:]] = ('new' if vp is None
                              else 'same' if _files_equal(src, vp)
                              else 'DIFF')
        skel_diff = diffs.get('aa') == 'DIFF'
        anim_diff = [s for s in ('ab', 'da') if diffs.get(s) == 'DIFF']
        if skel_diff:
            skel_changed += 1
        if anim_diff:
            anim_changed += 1
            if keep_van_anim and not skel_diff:
                for s in anim_diff:
                    del mod_files[pfx + s]
                    dropped.append(pfx + s)
        interesting = {k: v for k, v in sorted(diffs.items())
                       if v != 'same' and k in ('aa', 'ab', 'da')}
        if interesting:
            who = ENEMY_NAMES.get(pfx)
            label = f'{pfx} ({who})' if who else pfx
            log(f'    {label}: ' + ', '.join(f'{k}={v}'
                                             for k, v in interesting.items()))
    if groups:
        log(f'  battle report: {len(groups)} non-player models; '
            f'{skel_changed} change the skeleton (aa), '
            f'{anim_changed} change animation files (ab/da)')
    if dropped:
        log(f'  KEEP_VANILLA_ANIM: dropped {len(dropped)} mod animation '
            'file(s); Switch-vanilla ab/da will be used for those models')
    elif keep_van_anim and anim_changed:
        log('  KEEP_VANILLA_ANIM: nothing dropped (all changed-anim models '
            'also change their skeleton; vanilla animations would not fit)')
    return mod_files


def _convert_battle_textures(name, mod_files, van, log, folder_of=None,
                             battle_bg_native_names=None):
    """
    Replace truecolor TEX files headed for a battle archive with paletted
    conversions (see tex.py). Results are cached by source signature.
    Non-TEX files (geometry, skeletons, animations) pass through untouched.

    Player-character files (source option under a "Mains" folder) are
    EXEMPT: the players-only build is proven pixel-perfect on hardware as
    shipped, and must stay byte-identical to the reference archive.
    Players never use the death dissolve anyway.

    battle_bg_native_names: lowername set from
    plan.battle_bg_native_names -- ONLY these get the user-configurable
    BATTLE_BG_TEX_CAP_ENV cap (see _battle_bg_tex_cap). Every other file,
    from any other mod, keeps the fixed 256px hardware-proven default,
    unaffected by that setting.
    """
    os.makedirs(TEXCONV_CACHE, exist_ok=True)
    folder_of = folder_of or {}
    battle_bg_native_names = battle_bg_native_names or ()
    bg_cap = None
    out = {}
    converted = 0
    for low, (src, mod) in mod_files.items():
        opt = str(folder_of.get(low, ''))
        if 'main' in opt.lower():
            out[low] = (src, mod)
            continue
        try:
            with open(src, 'rb') as f:
                data = f.read()
        except OSError:
            out[low] = (src, mod)
            continue
        if not tex.is_unpaletted(data):
            out[low] = (src, mod)
            continue
        van_path = van.get(low)
        van_data = None
        if van_path:
            try:
                with open(van_path, 'rb') as f:
                    van_data = f.read()
            except OSError:
                pass
        is_battle_bg = low in battle_bg_native_names
        if is_battle_bg and bg_cap is None:
            bg_cap = _battle_bg_tex_cap()
        cap = bg_cap if is_battle_bg else 256
        # Version tag invalidates caches from older converter POLICIES.
        # The cap is in the key too, but the cap alone is not enough and that
        # cost a whole build: someone selected 768, the doubling bug produced
        # 512, and the result was cached under `-cap768`. Fixing the bug did
        # not change the key, so the next build reused the wrong-sized
        # conversion and battle.lgp came out byte-identical -- the setting
        # looked broken twice for two completely different reasons.
        #
        # BUMP THIS whenever the pixels convert_for_battle() produces change
        # for an input it has already seen. Changing the cap is a different
        # question from changing what a cap MEANS.
        #   v3  black 16-color headers
        #   v4  16-color quantization mud
        #   v5  1x256 paletted for everything
        #   v6  upscale by a whole factor, not by doubling, so a cap that is
        #       not a power of two is actually reachable
        #   v7  the reserved transparent entry carries the boundary colour
        #       instead of black, so filtering stops drawing a dark line
        #       along every atlas seam
        cache_key = ('TEXCONV-V7-' + _sig(src) + f'-cap{cap}'
                     + ('-' + _sig(van_path) if van_path else ''))
        cached = os.path.join(TEXCONV_CACHE,
                              f'{name}.{low}.{hashlib.sha1(cache_key.encode()).hexdigest()[:16]}')
        if os.path.exists(cached):
            out[low] = (cached, mod)
            converted += 1
            continue
        try:
            new, note = tex.convert_for_battle(data, van_data, cap=cap)
        except Exception as exc:
            log(f'  ! texconv {low}: {exc}; using original')
            out[low] = (src, mod)
            continue
        if new is None:
            out[low] = (src, mod)
            continue
        with open(cached, 'wb') as f:
            f.write(new)
        out[low] = (cached, mod)
        converted += 1
        log(f'  texconv {low}: {note}')
    if converted:
        log(f'  {name}: {converted} truecolor texture(s) converted to '
            'paletted (set SEVENTH_NX_NO_TEXCONV=1 to disable)')
    if bg_cap is not None and bg_cap != 256:
        log(f'  {name}: Avalanche Arisen battle background tiles capped at '
            f'{bg_cap}px (everything else stays at the proven 256px)')
    return out


def _field_tex_cap():
    """Current cap in pixels, or None if disabled. Invalid/zero -> None."""
    raw = os.environ.get(FIELD_TEX_CAP_ENV, '').strip()
    if not raw:
        return None
    try:
        val = int(raw)
    except ValueError:
        return None
    return val if val > 0 else None


def _battle_bg_tex_cap():
    """
    Cap in pixels for Avalanche Arisen's own synthesized battle background
    tiles only (see BATTLE_BG_TEX_CAP_ENV). Unset/invalid/zero -> 256, the
    same default tex.convert_for_battle() has always used -- so a build
    with nothing set behaves exactly as before this setting existed.
    """
    raw = os.environ.get(BATTLE_BG_TEX_CAP_ENV, '').strip()
    if not raw:
        return 256
    try:
        val = int(raw)
    except ValueError:
        return 256
    return val if val > 0 else 256


def _field_bg_cap():
    """Texture pages allowed per field background, or None if disabled."""
    raw = os.environ.get(FIELD_BG_CAP_ENV, '').strip()
    if not raw:
        return None
    try:
        val = int(raw)
    except ValueError:
        return None
    return val if 0 < val < BG_MAX_PAGES else None


def _bg_compact_only(archive, payloads, log):
    """
    Fit the archive to the console without touching how it looks.

    No promotion, no colour change, no resolution change, no palette
    decision -- cells are relocated between pages of the same depth, size and
    blend group with their bytes copied verbatim, and pages nothing points at
    any more stop being textures. Every field checks its own output and is
    handed back untouched if anything is off (field_bg_compact.self_check).

    This is what "Field background page size: Off" now does, because Off used
    to mean "leave the mod's page counts exactly as they are" and the mod's
    page counts are the problem. See the note at the head of
    _bg_field_backgrounds.
    """
    global FIELD_BG_MAX_RAW
    n_fields = n_saved = n_moved = n_merged = 0
    rejected = []
    over = []
    for name in sorted(archive.index):
        entry = archive.index[name]
        if not archive.is_field(entry):
            continue
        blob = payloads.get(name)
        if blob is not None:
            if not _is_lzs_wrapped(blob):
                continue
            raw = lgp.lzs_decompress(blob[4:])
        else:
            raw = archive.decompressed(entry)
        try:
            parts = lgp.split_sections(raw)
        except Exception:                                      # noqa: BLE001
            continue
        try:
            new9, cst = field_bg_compact.compact_section9(parts[8])
        except Exception as exc:                                # noqa: BLE001
            log(f'  ! field background: {name} not compacted -- {exc}')
            continue
        if cst.rejected:
            rejected.append((name, cst.rejected))
        if cst.pages_after and cst.pages_after > 12:
            over.append((name, cst.pages_after))
        if not cst.saved:
            continue
        n_fields += 1
        n_saved += cst.saved
        n_moved += cst.cells_moved
        n_merged += cst.cells_merged
        parts[8] = new9
        new_raw = lgp.join_sections(parts)
        if len(new_raw) > FIELD_BG_MAX_RAW:
            FIELD_BG_MAX_RAW = len(new_raw)
        payloads[name] = _encode_field_cached(archive, new_raw)
    log(f'  field background: NOT promoted (page size Off) -- colours, '
        f'resolution and palettes are exactly as the mods left them.')
    log(f'      FITTED: {n_saved} texture(s) freed across {n_fields} field(s)'
        f' -- {n_moved:,} cell(s) relocated, {n_merged:,} merged as '
        f'byte-identical.')
    log(f'      Every present page is one texture and field_load_textures '
        f'(x86 0x640292) abandons the loop on the first it cannot serve. '
        f'MEASURED on this mod: 169 field(s) arrive holding MORE pages than '
        f'vanilla (worst 15 against 12) before anything here runs, which is '
        f'what the black squares are.')
    if over:
        log(f'      {len(over)} field(s) STILL above 12 pages after fitting:')
        for nm, k in sorted(over, key=lambda r: -r[1])[:10]:
            log(f'          {nm:<12} {k} page(s)')
    if rejected:
        log(f'      ! {len(rejected)} field(s) failed the self-check and were '
            f'left exactly as they came in:')
        for nm, why in rejected[:8]:
            log(f'          {nm:<12} {why}')
    return n_fields, 0, 0


def _bg_texture_pages(sec9):
    """
    How many 256x256 texture pages a field background section uses, or None
    if the section does not parse as one.

    Only the TEXTURE block is walked. The palette and the four sprite layers
    ahead of it are skipped by finding the marker, because their layout
    varies with the field and none of it is needed to answer this: each page
    is `u16 present`, and when present `u16 size, u16 depth` followed by
    256*256*depth bytes.

    Finding a marker by search invites a false positive inside sprite data,
    so the walk is required to land within a few bytes of the end of the
    section -- exactly 42 page slots, consuming everything. A wrong start
    offset does not survive that, and a None here means "leave this field
    alone", never "assume it is fine".
    """
    start = sec9.find(b'TEXTURE')
    if start < 0:
        return None
    o = start + 7
    n = len(sec9)
    pages = 0
    for _ in range(BG_MAX_PAGES):
        if o + 2 > n:
            return None
        present = struct.unpack('<H', sec9[o:o + 2])[0]
        o += 2
        if not present:
            continue
        if o + 4 > n:
            return None
        _size, depth = struct.unpack('<HH', sec9[o:o + 4])
        o += 4
        if depth not in (1, 2):
            return None
        o += BG_PAGE_BYTES * depth
        pages += 1
    if not 0 <= n - o <= 64:
        return None
    return pages


# Fields whose mod BACKGROUND is held back because the field runs out of
# FF7's HEAP when it carries one. Sections 1-8 are unaffected -- only the
# section-9 background reverts to Switch vanilla.
#
# WHY THIS EXISTS, and it is a workaround, not a fix.
#
# MEASURED, from an Atmosphere crash report (FS 2002-6065, an nnSdk Abort
# out of fopen): the faulting call is `fopen("Documents/heap_dump.txt","w")`
# at +0x10EE6A0, and the function that makes it prints '<heap>' and
# 'remain2:0x%08x' -- it is FF7's HEAP DUMP. Its caller at +0x10EE860 is the
# allocator: it walks the free list (size at blk+0x14, next at blk+0x1c),
# needs `requested + 0x34`, and when no block is big enough it dumps the heap
# and returns NULL. So the field does not have a bad tile or a bad page. It
# runs the game out of memory, and the abort is the dying diagnostic --
# which is why every structural check on this field passes.
#
# CONFIRMED ON HARDWARE: mkt_mens ships at 441,281 bytes with our repacked
# background (section 9: 225,409 -> 422,007, two pages promoted to
# truecolor). Handing 192 KB back by restoring vanilla section 9 makes it
# load. So the remaining headroom in that room is under 192 KB.
#
# THE REAL FIX IS IN, AND THIS LIST NOW TURNS ITSELF OFF.
#
# FINDINGS-106: the heap reservation is not in FF7 at all. The port binds
# the Win32 API by name through a shim table at 0x1196B98, and its
# `HeapCreate` (+0x10EE7B0) throws away the arguments ff7_en passes it
# (x86 0x40E019 asks for a growable 4 KB heap) and hands back a hardcoded
# 64 MB pool. `ff7nx_heap.py` raises it -- nine words, and `apply_heap`
# writes them into exefs/main at the end of the build.
#
# So this list is conditional on `ff7nx_heap.HEAP_MB`. At the stock 64 MB it
# holds mkt_mens back exactly as it always did. At any raised size it holds
# NOTHING back, because the only reason it ever existed was the 64 MB
# ceiling and keeping it on would mask the very thing the next build is
# meant to test.
#
# THAT COUPLING IS REAL AND IT IS ONE-WAY. _build_flevel runs long before
# apply_heap, so a raised HEAP_MB means flevel.lgp ships mkt_mens's full mod
# background and DEPENDS on the module patch landing. If apply_heap fails --
# no game dump, a module an earlier build already patched -- the SD tree is
# inconsistent and Men's Hall crashes again. apply_heap says so in those
# words when it fails; this is the same contract apply_field_bg already has
# with the page size.
#
# ADDING A FIELD: if another room crashes on entry with no visual warning,
# put its section-8 name here and rebuild. Losing one room's upscaled
# background beats losing the room. If it crashes on a RAISED heap, that is
# a finding, not a field to add -- write it down before reaching for this.
HEAP_TIGHT_FIELDS = {
    'mkt_mens',      # Wall Market, Men's Hall (the gym). Crash MEASURED.
}


def _heap_is_raised():
    """True when this build will write a heap larger than the port's 64 MB.

    Asks ff7nx_heap the same question apply_heap does, and answers False if
    the module is missing or the size it names cannot actually be encoded --
    so a build that will NOT get the patch keeps the workaround.
    """
    try:
        import ff7nx_heap
    except ImportError:
        return False
    mb = ff7nx_heap.HEAP_MB
    return mb != ff7nx_heap.STOCK_MB and ff7nx_heap.encodable(mb) is None


def _heap_tight_fields():
    """The hold-back list, empty once the heap patch is in the build.

    No environment variable. The list is a code constant and the switch is
    `ff7nx_heap.HEAP_MB`, which is also a code constant -- one setting, in
    one place, that both halves of the build read.
    """
    if _heap_is_raised():
        return set()
    return set(HEAP_TIGHT_FIELDS)


def _hold_back_heap_tight(chunks, log):
    """Revert section 9 to Switch vanilla for the fields named above."""
    names = _heap_tight_fields()
    if not names:
        if _heap_is_raised():
            import ff7nx_heap
            log('  field background: heap hold-back is OFF because this '
                f'build raises FF7\'s heap to {ff7nx_heap.HEAP_MB} MB -- '
                'every field, including mkt_mens (Men\'s Hall), keeps its '
                'full mod background')
            log('    this flevel.lgp now DEPENDS on exefs/main getting the '
                'heap patch. If the module pass at the end of this build '
                'does not run, Men\'s Hall will crash on entry again.')
        else:
            log('  field background: heap hold-back list is EMPTY -- every '
                'field keeps its mod background')
        return chunks
    kept = {}
    held = []
    for field, sections in chunks.items():
        if field.lower() in names and 9 in sections:
            rest = {i: v for i, v in sections.items() if i != 9}
            held.append(field)
            if rest:
                kept[field] = rest
        else:
            kept[field] = sections
    if held:
        log('  field background: %d field(s) kept their Switch-vanilla '
            'background because the room runs FF7 out of HEAP with ours '
            '(sections 1-8 still come from the mod): %s'
            % (len(held), ', '.join(sorted(held))))
        log('    this is a WORKAROUND, and the fix now exists: raise '
            'ff7nx_heap.HEAP_MB above 64 and this list turns itself off.')
    missing = sorted(n for n in names
                     if not any(f.lower() == n for f in chunks))
    if missing:
        log('  field background: heap hold-back names not present in this '
            'build (harmless): %s' % ', '.join(missing))
    return kept


def _cap_field_backgrounds(chunks, log, max_pages):
    """
    Hold back section-9 replacements whose background needs more texture
    pages than the budget allows (see FIELD_BG_CAP_ENV).

    Only section 9 is affected. A field that also ships other sections keeps
    them: the sections are independent, and the point is to bound background
    art, not to veto the whole field.
    """
    kept = {}
    over = []
    unparsed = 0
    for field, sections in chunks.items():
        src = sections.get(9)
        if src is None:
            kept[field] = sections
            continue
        try:
            with open(src[0], 'rb') as f:
                pages = _bg_texture_pages(f.read())
        except OSError:
            pages = None
        if pages is None:
            unparsed += 1
            kept[field] = sections
            continue
        if pages <= max_pages:
            kept[field] = sections
            continue
        over.append((field, pages))
        rest = {i: v for i, v in sections.items() if i != 9}
        if rest:
            kept[field] = rest
    if over:
        over.sort(key=lambda t: (-t[1], t[0]))
        log(f'  field background cap: {len(over)} field(s) need more than '
            f'{max_pages} texture page(s) ({max_pages * BG_PAGE_BYTES // 1024}'
            ' KB) and kept their Switch-vanilla background:')
        for field, pages in over[:8]:
            log(f'      {field}: {pages} pages '
                f'({pages * BG_PAGE_BYTES // 1024} KB)')
        if len(over) > 8:
            log(f'      ... and {len(over) - 8} more')
    if unparsed:
        log(f'  field background cap: {unparsed} section(s) did not parse as '
            'a background and were left alone')
    return kept


# PAGE COUNT PER FIELD AS THE MOD SHIPS IT -- the only correct no-growth
# baseline, and neither of the two obvious candidates is it.
#
#   vanilla flevel          WRONG, too strict. Cosmos ships its own section 9
#                           for 683 fields and its page counts are ITS OWN:
#                           MEASURED off the mod's chunk.9 -- fship_2 15 pages
#                           against vanilla's 12, church 4 against 3, chrin_3b
#                           5 against 4. Those pages are not ours to remove and
#                           chasing them strips truecolor for nothing.
#   `parts[8]` in the loop  WRONG, too lax. `ff7nx_marginart` and
#                           `ff7nx_marginpage` have already run by then, and
#                           marginpage adds ~1 page per field -- so the loop
#                           compares 8 against 8 and marginpage's page is free
#                           by definition. MEASURED: this is why the first
#                           restored loop logged `266 field(s) RE-RUN` and 0
#                           GREW while mds5_1 stayed at 8 against the mod's 7,
#                           byte-identical to the build before it.
#
# So snapshot it in `_build_flevel`, after the mod's chunks are in and before
# our own passes touch anything.
_PAGES_BEFORE_MARGIN = {}


def _snapshot_page_counts(archive, payloads):
    """{field: live page count} as the archive stands right now."""
    out = {}
    for name in archive.index:
        entry = archive.index[name]
        if not archive.is_field(entry):
            continue
        blob = payloads.get(name)
        try:
            if blob is not None:
                if not _is_lzs_wrapped(blob):
                    continue
                raw = lgp.lzs_decompress(blob[4:])
            else:
                raw = archive.decompressed(entry)
            sec9 = lgp.split_sections(raw)[8]
            pages, _s, _e = field_bg_native.parse_texture_block(
                sec9, field_bg_native.VANILLA_PX)
        except Exception:                                      # noqa: BLE001
            continue
        out[name] = sum(1 for p in pages if p is not None)
    return out


def _convert_field_backgrounds(archive, payloads, log, dds_sources=()):
    """
    Rewrite section 9 of EVERY field so the file agrees with a module patched
    by ff7nx_fieldbg.

    "Every", not "every modded one", and that is the whole point. The module
    patch is global: once it is in, the loader reads
    `page_px * page_px * 2` bytes for ANY depth-2 page, in any field, modded
    or not. Vanilla flevel.lgp has 51 such pages across a handful of fields
    (cosmo, cosmo2, blin67_4, fr_e ...). Leaving them at 256 would over-read
    the stream and corrupt everything after them in that field.

    Runs on `payloads` -- the replacements the mod passes already decided --
    plus every untouched entry. A field with no depth-2 page is not rewritten
    at all, so with the mod's backgrounds still paletted this pass costs a
    couple of dozen re-encodes, not 709.

    Returns (n_fields, n_pages, bytes_added).
    """
    px = ff7nx_fieldbg.page_px()
    # PAGE SIZE "OFF" MEANS DO NOT PROMOTE. IT DOES NOT MEAN DO NOTHING.
    #
    # MEASURED against Cosmos Limit Break's own `chunk.9` sections -- the ones
    # this build splices in and the console actually loads -- compared with
    # the stock archive:
    #
    #     the mod as shipped        169 field(s) hold MORE pages than vanilla
    #                               does, worst fship_2 at 15 against 12
    #     after compaction alone     41 field(s), worst 13
    #                               224 textures freed, nothing promoted
    #
    # Every ceiling in this tree was calibrated against VANILLA flevel.lgp
    # while the console loads the MOD's. `field_load_textures` (x86 0x640292)
    # abandons the loop on the first texture it cannot serve, so a field that
    # arrives asking for 15 when the port was provisioned for 12 drops the
    # rest of its picture -- with promotion switched off, at page size Off,
    # before any of this code has had an opinion. That is what the black
    # squares were, and it is why removing the memory cap made things worse:
    # the cap had been accidentally holding down a page count the INPUT data
    # was inflating.
    #
    # So Off still compacts. It changes no colour, no resolution and no
    # palette -- it only stops asking the console for textures it was never
    # built to serve.
    if px == ff7nx_fieldbg.OFF_PAGE_PX:
        if not field_bg_compact.enabled():
            return 0, 0, 0
        return _bg_compact_only(archive, payloads, log)
    # NOT `px == VANILLA_PX` any more. 256 used to mean "off" and returned
    # here, which is why "256px truecolor" -- the cheapest promotion there is,
    # 0.38 MB a page against vanilla paletted's 0.31 -- had never been tried:
    # the setting existed but this line threw it away. Off is its own value
    # now (see ff7nx_fieldbg.OFF_PAGE_PX) and 256 falls through to the repack.

    # ------------------------------------------------------------------
    # SAY THE GROWTH MODE OUT LOUD, FIRST, BEFORE ANY OTHER NUMBER.
    #
    # It was printed only as a SAFETY footnote 40 lines below a wall of
    # per-page statistics, and two consecutive hardware builds were run
    # believing it had been changed when it had not. The mode is the single
    # setting that decides whether the loader is asked for more textures
    # than the stock game asks for, so it goes at the top where a glance at
    # the log answers "did my change take effect".
    # ------------------------------------------------------------------
    _mode = ('NO GROWTH' if field_bg_repack.no_growth()
             else 'REPLACE ONLY' if field_bg_repack.replace_only() else 'OFF')
    log('  field background: PAGE GROWTH = %s' % _mode)
    if _mode == 'OFF':
        log('      ! OFF means every page the mod covers is promoted and the '
            'page count GROWS. field_load_textures (x86 0x640292) abandons '
            'the whole loop on the first page it cannot allocate and every')
        log('        page after it draws nothing -- that is what the '
            'scattered black squares are. If you are chasing black squares, '
            'this is the setting: Field background -> Page growth -> '
            '"No growth".')
    # THE BUDGET IS CALIBRATED FOR 512px PAGES AND NOTHING ELSE.
    #
    # budget_bytes()'s own evidence -- "fields with 1 truecolor page were
    # clean, fields with 3-4 were black", "a 512px truecolor page costs
    # 1.5 MB" -- was all taken at 512px. At 256px a truecolor page is
    # 0.38 MB, four times cheaper, so a 4 MB cap that admitted ~2 pages at
    # 512 is being applied to pages that cost a quarter as much. It excludes
    # art for a reason that does not hold at this page size.
    #
    # budget_bytes() also records that the failure is NOT MONOTONIC in the
    # budget (18 MB black, 14 MB clean; lowering to 4.0 made margins worse),
    # which means no value of it can be argued to be safe. It is a guess
    # wearing a number.
    # `budget_bytes()` returns UNLIMITED == 1<<60 for "no budget", not 0, so
    # a truthiness test fires on Unlimited and prints 1099511627776.0 MB.
    # Compare against the sentinel.
    _bud = field_bg_repack.budget_bytes()
    if _bud >= field_bg_repack.UNLIMITED:
        _bud = 0
    if _bud and px != 512:
        log('      ! the %.1f MB per-field budget was MEASURED at 512px '
            'pages, where a truecolor page costs 1.50 MB. These are %dpx '
            'pages at %.2f MB.' % (_bud / 1048576.0, px,
                                   (px * px * 2 + px * px * 4) / 1048576.0))
        log('        The calibration does not transfer, and it is excluding '
            'art to respect a ceiling measured somewhere else. Set the field '
            'background memory budget to Unlimited unless you are '
            'deliberately reproducing a 512px result.')

    dense = {'fields': 0, 'cells': 0, 'pages': 0, 'pages_before': 0,
             'from_art': 0, 'from_art_borrow': 0, 'from_vanilla': 0,
             'refused': [], 'toobig': []}
    art = None
    if dds_sources:
        art = field_bg_repack.ArtProvider(dds_sources, px, log)
        if art:
            log(f'  field background: {len(art.slots):,} upscaled page image(s)'
                f' across {len(art.fields()):,} field(s) available'
                + (f'; {art.ambiguous} slot(s) had SEVERAL dumps of the '
                   f'same page ({art.ambiguous_base} took the base state, '
                   f'{art.ambiguous_arbitrary} had no base dump and took '
                   f'the first by name)'
                   if art.ambiguous else ''))
        else:
            log('  field background: no upscaled art found in the enabled '
                'mods -- truecolor pages will be rescaled, but nothing gets '
                'sharper')
            art = None
    if art is not None and field_bg_repack._np is None:
        log('  field background: numpy is not installed, so the pixel '
            'conversion runs in pure Python and this will take a very long '
            'time. `pip3 install numpy` and rebuild.')

    # THE MARGIN PAGE SPLIT, as a hook the repack calls between promotion and
    # compaction. It used to be its own pass ahead of this whole function; it
    # renamed pages before promotion could look the mod's art up by page name,
    # which left 78% of margin tiles 8-bit against 43% of interior ones.


    nf = npg = grew = 0
    up_fields = up_pages = up_cells = up_new = 0
    up_exact = up_borrowed = up_single = up_transparent = 0
    up_fx = up_art = 0
    up_capped = up_dropped = up_wrongpal = 0
    cmp_fields = cmp_saved = cmp_merged = cmp_moved = 0
    cmp_rejected = []
    # FINDINGS-128: refused on purpose because compacting would have put a page
    # over the 256-tiles-per-frame limit. NOT a self-check failure, and it must
    # not be reported as one -- the existing line below says "this is a bug,
    # please report it", which would be wrong and alarming.
    cmp_windowed = []
    # NO-GROWTH, ENFORCED RATHER THAN ASSERTED.
    #
    # `field_bg_repack` is no longer called, and the ceiling loop that made
    # "no growth" true lived inside it. Its DESCRIPTION STRING
    # (field_bg_repack.py:867) was still being printed, so the log claimed
    # "the loader is never asked for more textures than this archive already
    # asked for" four lines under "123 field(s) GREW their page count". Both
    # cannot be true. These count what the restored loop actually does.
    ng_retry = 0                      # dense repacks re-run at a lower ceiling
    ng_over = []                      # (field, before, after) still over at 0
    # FINDINGS-129: fields allowed to keep their pages, and therefore their
    # truecolor, because the frame guard refused to pack them.
    ng_frame_kept = []
    # FINDINGS-131: texels whose green LSB the engine would smear onto blue.
    green_lsb = {'texels': 0, 'fields': 0, 'names': []}
    ng_margin = 0                     # fields the MARGIN passes grew, reported
                                      # but not charged to the repack
    no_cover = no_fit = 0
    skipped = []
    skipped_all_or_nothing = []       # (field, pages it would have promoted)
    raw_capped = []                   # (field, bytes it would have decompressed
                                      # to) -- dropped by FIELD_BG_RAW_CAP
    pagecap = {'fields': 0, 'pages': 0, 'tiles': 0, 'worst': 0}
    # FINDINGS-122: pages caught by the single-screen hard 256 that the
    # grandfathered cap let through. Counted separately so the change is one
    # line in the log diff instead of a shift inside an existing number.
    pagecap_single = {'fields': 0, 'pages': 0, 'tiles': 0, 'names': [],
                      'strategy': {}}
    # FINDINGS-126: cells moved into free space on a page that already exists,
    # where the band had no slot to duplicate into.
    pagecap_reloc = {'fields': 0, 'cells': 0, 'names': []}
    palclamp = {'fields': 0, 'tiles': 0, 'names': []}
    pagecap_dropped = []              # (field, pages) the raw cap refused
    pagecap_refused = []              # (field, [(slot, tiles), ...])
    page_cost = []                    # (field, live pages, bytes) for the
                                      # per-field ceiling report below
    for name in sorted(archive.index):
        entry = archive.index[name]
        if not archive.is_field(entry):
            continue
        # ---- ONE FIELD BACK TO STOCK, DELIBERATELY. FINDINGS-176.
        #
        # `las0_2` (bottom of the Northern Cave) has been broken for many
        # sessions -- image skewed to the top-left, no character, crash on
        # movement -- and every hypothesis so far has died:
        #
        #   camera range      its section 8 is byte-identical to vanilla and
        #                     its range is the stock 320, so the camera cannot
        #                     travel at all           (FINDINGS-168 s1)
        #   the 256 array     Cosmos's own chunk.9 measures the same 256 in
        #                     window and runs on PC   (FINDINGS-168 s1.4)
        #   page count        "the build that crashed and the build that did
        #                     not had IDENTICAL page counts in the fields that
        #                     crashed" -- the note 100 lines below
        #   our own data      it renders CORRECTLY offline, whole scene, not
        #                     skewed (render_field, build 74 vs vanilla)
        #   field logic       sections 0,1,2,4,5,6,7 are BYTE-IDENTICAL to
        #                     vanilla; we touch only section 3 (5 entry-0
        #                     bytes) and section 9
        #
        # So the next question is the one nobody has asked: is it the
        # background at all? Naming a field here reverts its background to
        # STOCK -- no Cosmos section 9, no margin passes, no repack -- while
        # the rest of the archive builds normally.
        #
        #   still broken  ->  it is NOT the field background. Stop looking
        #                     here and look at the texture pool, the exefs
        #                     patches, or the field's own model/script load.
        #   fixed         ->  it IS the background, and bisecting is then
        #                     cheap: put it back and drop MAX_TRUECOLOR_PAGES
        #                     for this field, then the margin passes, and so
        #                     on.
        #
        # EMPTY IN A SHIPPING BUILD. This costs the named field its upscale,
        # so it is a diagnostic, not a fix -- although leaving `las0_2` stock
        # would be a defensible workaround if the background turns out to be
        # the cause and nothing cheaper helps.
        if name.lower().split('.')[0] in FIELD_BG_SKIP_FIELDS:
            log(f'  field background: {name} left at STOCK '
                f'(FIELD_BG_SKIP_FIELDS -- diagnostic, FINDINGS-176)')
            payloads.pop(name, None)
            continue
        blob = payloads.get(name)
        if blob is not None:
            if not _is_lzs_wrapped(blob):
                continue                      # stored raw; not a field file
            raw = lgp.lzs_decompress(blob[4:])
        else:
            raw = archive.decompressed(entry)
        try:
            parts = lgp.split_sections(raw)
        except Exception:                                      # noqa: BLE001
            continue
        global FIELD_BG_MAX_RAW
        if len(raw) > FIELD_BG_MAX_RAW:
            FIELD_BG_MAX_RAW = len(raw)
        try:
            new9, k = field_bg_native.resize_section9(parts[8], px)
        except field_bg_native.Section9Error as exc:
            skipped.append((name, str(exc)))
            continue
        # Repack AFTER the rescale, so the pre-existing truecolor pages are
        # already at `px` and parse_texture_block agrees with itself.
        # ---- PROMOTE, THEN PAY FOR IT
        #
        # The promotion ADDS a page rather than replacing one, because the
        # original paletted page has to stay alive for every tile that could
        # not move (colour-key cells, fx-page tiles). What it leaves behind is
        # a set of pages that are mostly EMPTY -- and every present page is a
        # texture whether it holds 256 cells or 6.
        #
        # So the leftovers get packed back down. Cells are relocated by the
        # same two u32s the promotion already rewrites, between pages of the
        # same depth, size and blend group, with the bytes copied verbatim.
        # NOTE: the promote-then-compact pass this paragraph described is
        # gone. The dense repack below replaces every original page, so there
        # are no leftovers to pack down and no ceiling to walk.
        # ---- DENSE REPACK, CAPPED AT THE DENSITY HARDWARE SURVIVES
        #
        # MEASURED with the real .iro over 110 fields: the promotion that runs
        # on this console never puts more than THREE truecolor pages in one
        # field (mean 1.41). Both frozen builds averaged 4.7 with every page
        # truecolor -- at a LOWER total page count -- so the truecolor count is
        # the one quantity that separates them. Vanilla ships 26 truecolor
        # pages across 400 fields; this path is a rarity in the stock game.
        #
        # So: pack the most-drawn cells densely into at most three truecolor
        # pages and leave the rest where they are. The pages left behind
        # already carry Cosmos art -- `ff7nx_marginart` writes 335,457 cells of
        # it into them -- so nothing reverts to vanilla pixels and the
        # widescreen alignment holds. The difference is colour depth, not art.
        st = cst = None
        _pre9 = new9

        # WHAT THIS FIELD ASKED FOR BEFORE THIS BUILD TOUCHED IT.
        #
        # THE BASELINE HAS TO COME FROM THE ARCHIVE, NOT FROM `parts`.
        #
        # `parts` is the section as the EARLIER passes left it, and
        # `ff7nx_marginpage` has already added ~1 page per field by the time
        # we get here. Measuring no-growth against that makes marginpage's
        # page free by definition: the loop compares 8 against 8, stops, and
        # the field still asks the loader for one more texture than the mod
        # shipped.
        #
        # MEASURED, and this is why the first restored loop changed nothing
        # where it mattered: with `parts[8]` as the baseline the log printed
        # `no-growth: 266 field(s) RE-RUN` and zero GREW, while mds5_1 stayed
        # at 8 pages against the mod's 7 and mds5_3 at 8 against 6 -- byte
        # for byte the same as the build before it, in exactly the fields
        # that were crashing.
        #
        # `archive` still holds the mod's own field; `payloads` holds ours.
        # So decode the archive entry when we have replaced it.
        # THE BASELINE IS THE SECTION AS THE MARGIN PASSES LEFT IT, and that
        # is a deliberate retreat from enforcing the mod's own count.
        #
        # MEASURED, both ways, on hardware:
        #
        #   baseline = parts[8]   (post-marginpage)
        #       266 field(s) re-run, 637 field(s) promoted, 325,512 cells,
        #       mds5_1 at 8 pages against the mod's 7.   NO CRASH.
        #   baseline = the mod's own count
        #       1,196 field(s) re-run, 352 field(s) promoted, 163,980 cells,
        #       and 197 field(s) STILL over anyway because marginpage's page
        #       is not this pass's to give back.
        #
        # The strict baseline cost 285 fields their promotion -- almost half
        # the archive dropped to 8-bit -- to chase a page the loop cannot
        # reach. And the thing it was meant to prevent turned out not to be
        # the problem: the build that crashed and the build that did not had
        # IDENTICAL page counts in the fields that crashed, so page count is
        # not the mechanism.
        #
        # `ff7nx_marginpage`'s extra page is not optional -- it is what stops
        # the margin being drawn through a foreign colour table -- and the mod
        # itself ships fields at up to 15 pages that the console runs. So the
        # rule this pass is held to is the honest one: DO NOT GROW WHAT THE
        # MARGIN PASSES HANDED YOU. The mod-relative number is still
        # snapshotted and still reported, it is just no longer enforced.
        try:
            _b, _bs, _be = field_bg_native.parse_texture_block(
                parts[8], field_bg_native.VANILLA_PX)
            before = sum(1 for p in _b if p is not None)
        except Exception:                                      # noqa: BLE001
            before = None
        before_mod = _PAGES_BEFORE_MARGIN.get(name)

        # ---- DENSE REPACK + COMPACT, UNDER A REAL NO-GROWTH CEILING
        #
        # The repack is ADDITIVE: the originals stay for every cell that did
        # not promote, and `ff7nx_marginpage` has already added ~1 page per
        # field upstream. Compaction pays some of that back but not all of it,
        # and the build that crashed grew 123 fields while the log asserted it
        # could not -- mds5_1 7->8, mds5_3 6->8, church 3->5, the whole Sector
        # 5 corridor.
        #
        # So: measure after compaction and, if the field still holds more
        # pages than it started with, re-run the repack at a lower truecolor
        # ceiling until it does not. Down to zero, which is the paletted
        # section plus compaction -- still Cosmos art, because `marginart`
        # wrote 335,457 cells of it into the paletted pages.
        #
        # HANDOFF-78 2.9: the LAST no-growth retry loop walked coverage from
        # 71% to 12% because the margin page split ran INSIDE it and its pages
        # counted on every pass. This loop re-runs ONE pass, always from the
        # same `_pre9`, so nothing accumulates across iterations.
        _af = _pf = None
        if art is not None and name.lower() in art.fields():
            _af, _pf = art.open(name), art.palettes
        _try9, dst, cst, _dense_ok = _pre9, None, None, False
        try:
            for _tc in range(field_bg_dense.MAX_TRUECOLOR_PAGES, -1, -1):
                try:
                    _try9, dst = field_bg_dense.dense_repack(
                        parts[3], _pre9, name, _af, _pf, px, max_tc=_tc)
                except Exception as exc:                       # noqa: BLE001
                    log(f'  ! field background: {name} not repacked -- {exc}')
                    _try9, dst = _pre9, None
                _dense_ok = (dst is not None and not dst.refused
                             and dst.cells > 0)
                if _dense_ok and (len(raw) - len(parts[8]) + len(_try9)
                                  > FIELD_BG_RAW_CAP):
                    # One field over 1,677,721 bytes re-patches the loader's
                    # decompression buffer from the proven 2,097,152 to an
                    # untested 4,194,304 for the WHOLE GAME. Not worth one
                    # field.
                    #
                    # AND IT USED TO BE SILENT, WHICH COST A DIAGNOSIS.
                    #
                    # At 256px this never fires -- MEASURED across the shipped
                    # flevel, 0 of 711 fields cross the cap and the largest is
                    # 1,424,642. At a bigger page size it fires constantly:
                    # projecting the same 711 fields with section 9's pixel
                    # payload scaled by (px/256)^2 gives 17 fields over at
                    # 320px, 170 at 384px, 354 at 448px and 515 at 512px.
                    #
                    # Every one of those reverts to its pre-repack section 9
                    # with no promotion at all, and said nothing. A 512px
                    # build therefore came out looking like "512px does not
                    # work" when what actually happened is that three quarters
                    # of the game quietly built at the old settings. Name
                    # them.
                    raw_capped.append(
                        (name, len(raw) - len(parts[8]) + len(_try9)))
                    _try9, _dense_ok = _pre9, False
                cst = None
                if field_bg_compact.enabled():
                    try:
                        _try9, cst = field_bg_compact.compact_section9(
                            _try9, src_px=px)
                    except Exception as exc:                   # noqa: BLE001
                        log(f'  ! field background: {name} not compacted '
                            f'-- {exc}')
                        cst = None
                if before is None or not field_bg_repack.no_growth():
                    break
                try:
                    _lp, _lx, _ly = field_bg_native.parse_texture_block(
                        _try9, px)
                    _live = sum(1 for p in _lp if p is not None)
                except Exception:                              # noqa: BLE001
                    break
                if _live <= before:
                    break
                # DO NOT PAY FOR THE FRAME GUARD IN TRUECOLOR.
                #
                # This loop drops the truecolor ceiling until the field stops
                # growing. That is right when the growth is the repack's own
                # doing. It is wrong when the growth is `field_bg_compact`
                # DECLINING to pack, because packing that field would have put
                # a page over 256 tiles in one frame -- the field legitimately
                # needs those pages, and giving up its colour depth does not
                # make it safer, it only makes it worse-looking.
                #
                # MEASURED, build 42, which shipped the guard without this:
                #
                #     dense repack cells   287,480 -> 261,320   -26,160
                #     dense repack fields      607 ->     547   60 fields lost
                #                                              promotion entirely
                #
                # So: when compaction was refused for frame safety, accept the
                # page count as long as the field is still inside the ceiling
                # the settings actually enforce. The ceiling is the budget;
                # no-growth is a heuristic underneath it.
                if getattr(cst, 'window_refused', None):
                    _cap = field_bg_repack.max_total_pages()
                    if not _cap or _live <= _cap:
                        ng_frame_kept.append((name, before, _live))
                        break
                if _tc == 0:
                    # Nothing left to give: the growth is not this pass's.
                    # Name it rather than assert it did not happen.
                    ng_over.append((name, before, _live))
                    break
                ng_retry += 1
        finally:
            if _af is not None:
                art.close()
        new9 = _try9
        # ---- 256 TILES PER PAGE. THE ONLY HARD LIMIT THE GAME ACTUALLY HAS.
        #
        # FINDINGS-110. `add_page_tile` (x86 0x6464BA) appends into a 42-slot
        # array of 0x1804-byte records -- 4 bytes of count then exactly 256
        # entries of 0x18 -- and NEVER bounds-checks. Tile 257 on a page
        # writes its float x straight into the NEXT page's counter, and the
        # submit loop hands that value to draw_graphics_object, which turns it
        # into a several-hundred-megabyte malloc. That is the Men's Hall
        # crash, and it is why 64, 256 and 512 MB heaps all failed with
        # byte-identical stacks.
        #
        # Every page-moving pass above can cause it: the dense repack packs
        # onto fewer pages (6.17 -> 2.26 per field) and the compactor merges
        # byte-identical cells, so several tiles come to share one cell and a
        # page passes 256 without gaining a single cell. This runs LAST and
        # enforces the invariant on whatever they produced.
        #
        # The limit is on SIMULTANEOUSLY VISIBLE tiles, so the cap is
        # max(256, what vanilla already does for this field) -- vanilla
        # crater_2 names one page from 1912 tiles and has been fine since
        # 1997 because it scrolls. MEASURED across the shipped flevel: that
        # rule touches 142 fields and adds 189 pages; a flat 256 would touch
        # 413, add 704 and still leave 17 over.
        try:
            _van9 = lgp.split_sections(archive.decompressed(entry))[8]
        except Exception:                                      # noqa: BLE001
            _van9 = None
        # A TILE MAY NOT NAME A PALETTE THE FIELD DOES NOT HAVE.
        #
        # Cosmos's widescreen tiles carry whatever palette byte they happened
        # to have, because FFNx replaces the page with a DDS and never applies
        # it -- `ff7nx_marginpal` documents exactly this. On the Switch the
        # index IS applied. MEASURED on `md8_1`, the Sector 8 fire scene:
        # fourteen layer-2 tiles at dx -224/-208 and 192/208 name palette 13
        # when the field ships thirteen, and that set of coordinates is the
        # column of black rectangles down both edges of the screen.
        # `marginpal` only repoints LAYER 1 placeholder tiles, so nothing
        # caught these.
        try:
            new9, _npal, _npg = field_bg_pagecap.clamp_palettes(
                new9, parts[3], src_px=px)
            if _npal:
                palclamp['fields'] += 1
                palclamp['tiles'] += _npal
                palclamp['names'].append(name)
        except Exception as exc:                               # noqa: BLE001
            log(f'  ! field background: {name} palette clamp failed -- {exc}')
        try:
            _cap9, _cst = field_bg_pagecap.cap_section9(
                new9, src_px=px, vanilla_sec9=_van9)
        except Exception as exc:                               # noqa: BLE001
            log(f'  ! field background: {name} page cap failed -- {exc}')
            _cap9, _cst = new9, None
        # ADOPT THE SECTION WHENEVER IT CHANGED, NOT ONLY WHEN A PAGE WAS ADDED.
        #
        # This read `if _cst.pages_added:` and threw away any result that did
        # not grow the page count. Cell relocation (FINDINGS-126) adds NO page
        # by design -- that is the whole point of it -- so its work was computed
        # and discarded. MEASURED in build 40: `rckt3` and `spipe_2` kept their
        # relocation only because they happened to need a split in the same
        # call, while `junair2` relocated a cell, was reported as capped, left
        # the un-cappable list, and shipped its page 27 still at 367 in-window.
        #
        # The size guard stays: relocation cannot change the section's length
        # (same pages, same tiles, same bytes moved within them), so it can
        # never trip it, and a split still has to fit.
        _changed = _cst is not None and (_cst.pages_added
                                         or getattr(_cst, 'relocated', None))
        if _changed:
            # The split duplicates the overloaded page BYTE FOR BYTE into a
            # free slot and repoints the excess tiles, so every moved tile
            # keeps its u, v and palette and samples identical texels. The
            # only cost is the extra page, and it is only paid where we
            # exceeded what vanilla ships.
            if (len(raw) - len(parts[8]) + len(_cap9)) > FIELD_BG_RAW_CAP:
                pagecap_dropped.append((name, _cst.pages_added))
            else:
                new9 = _cap9
                # COUNTED ONLY ONCE THE SECTION IS ADOPTED. Sitting outside
                # this branch, it would report relocation for a field whose
                # section was then dropped by FIELD_BG_RAW_CAP -- claiming work
                # that did not ship, which is the bug this build exists to fix.
                if getattr(_cst, 'relocated', None):
                    pagecap_reloc['fields'] += 1
                    pagecap_reloc['cells'] += sum(_cst.relocated.values())
                    pagecap_reloc['names'].append(name)
                if _cst.pages_added:
                    pagecap['fields'] += 1
                    pagecap['pages'] += _cst.pages_added
                    pagecap['tiles'] += _cst.tiles_moved
                    if _cst.over:
                        pagecap['worst'] = max(pagecap['worst'],
                                               max(_cst.over.values()))
                # ATTRIBUTABLE ONLY, INCLUDING THE FIELD COUNT.
                #
                # Build 34 counted the field's TOTAL pages/tiles here and
                # overstated the rule's cost 2.5x. Build 35 fixed the pages and
                # tiles but left the field count keyed on "window_over fired at
                # all", which reported 86 fields for 37 attributable pages --
                # in ~49 of them the grandfathered cap was already splitting
                # and the window rule merely agreed. Same error, one line over.
                # A field counts only if this rule is why work happened.
                if getattr(_cst, 'ss_pages', 0):
                    pagecap_single['fields'] += 1
                    pagecap_single['pages'] += _cst.ss_pages
                    pagecap_single['tiles'] += _cst.ss_tiles
                    pagecap_single['names'].append(name)
                    for _v in getattr(_cst, 'strategy', {}).values():
                        pagecap_single['strategy'][_v] = \
                            pagecap_single['strategy'].get(_v, 0) + 1
        if _cst is not None and _cst.refused:
            pagecap_refused.append((name, _cst.refused))
        if _dense_ok:
            dense['fields'] += 1
            dense['cells'] += dst.cells
            dense['pages'] += dst.pages
            dense['pages_before'] += dst.pages_before
            dense['from_art'] += dst.from_art
            dense['from_art_borrow'] += dst.from_art_borrow
            dense['from_vanilla'] += dst.from_vanilla
        if cst is not None and getattr(cst, 'rejected', None):
            if getattr(cst, 'window_refused', None):
                cmp_windowed.append((name, cst.window_refused))
            else:
                cmp_rejected.append((name, cst.rejected))
        if cst is not None and cst.saved:
            cmp_fields += 1
            cmp_saved += cst.saved
            cmp_merged += cst.cells_merged
            cmp_moved += cst.cells_moved
        if st is not None:
            no_cover += st.pages_uncovered
            no_fit += st.pages_nofit
            if st.pages_allornothing:
                skipped_all_or_nothing.append((name, st.pages_allornothing))
            if st:
                up_fields += 1
                up_pages += st.pages_upgraded
                up_exact += st.pages_exact
                up_single += st.pages_single
                up_cells += st.cells
                up_borrowed += st.cells_borrowed
                up_transparent += st.cells_transparent
                up_fx += st.tiles_fx
                up_art += st.cells_art_transparent
                up_new += st.new_pages
                up_capped += st.pages_capped
                up_wrongpal += getattr(st, 'cells_wrong_palette', 0)
                up_dropped += st.pages_dropped
        # What this field will actually ask the driver for, computed from the
        # section as it now stands. Cheap -- the block is already parsed and
        # in cache -- and it turns "reboot and look for black squares" into a
        # build-log line.
        try:
            _pages, _s, _e = field_bg_native.parse_texture_block(new9, px)
            live = [p for p in _pages if p is not None]
            # BEFORE: the MOD's own page count, computed above from the
            # archive entry rather than from `parts` -- see the comment at
            # the top of this loop. Recomputing it here from `parts[8]` was
            # what made the GREW list disagree with reality: it counted
            # marginpage's page as part of the baseline.
            page_cost.append((
                name, len(live),
                sum(field_bg_repack._page_bytes(
                    field_bg_native.D1_PAGE_PX if p.depth == 1 else px,
                    p.depth) for p in live),
                before))
        except Exception:                                      # noqa: BLE001
            pass
        # `_dense_ok` HAS TO BE IN THIS TEST. The old promotion reported
        # through `st`/`cst`; with those gone the guard skipped every field
        # and wrote no payload at all -- the repack ran, logged 709 fields,
        # and none of it reached the archive.
        if before_mod is not None and before is not None and before > before_mod:
            ng_margin += 1
        if not k and not st and not (cst and cst.saved) and not _dense_ok:
            continue
        # GREEN-LSB BACKSTOP. See field_bg_native.scrub_green_lsb -- the
        # engine ORs green's low bit onto the top bit of blue on one display
        # path, vanilla never sets it, and we do on 5 fields. The writer has
        # not been found; this restores the invariant regardless and reports
        # how much it had to fix, so the number falls to zero when the real
        # cause is.
        try:
            _gp, _gs, _ge = field_bg_native.parse_texture_block(new9, px)
            _gn = field_bg_native.scrub_green_lsb(_gp)
            if _gn:
                new9 = field_bg_native.replace_texture_block(
                    new9, _gp, _gs, _ge)
                green_lsb['texels'] += _gn
                green_lsb['fields'] += 1
                green_lsb['names'].append(name)
        except Exception:                                      # noqa: BLE001
            pass
        parts[8] = new9
        new_raw = lgp.join_sections(parts)
        if len(new_raw) > FIELD_BG_MAX_RAW:
            FIELD_BG_MAX_RAW = len(new_raw)
        payloads[name] = _encode_field_cached(archive, new_raw)
        nf += 1
        npg += k
        grew += len(new9) - len(parts[8]) + (len(new_raw) - len(raw))
    if dense['fields']:
        # FORMAT FIRST, CONCATENATE SECOND. `%` binds tighter than `+`, so
        # `base + suffix % args` folds the suffix INTO the format string and
        # the outer tuple has nothing left to fill. That killed build 54 in
        # ff7nx_marginpal.summarise and then killed build 56 HERE, one message
        # after a test was written for it -- the test only covered module
        # summarise() functions, not build.py's inline log() calls. There is
        # now an AST check in test_summarise.py that finds this shape anywhere.
        _dense_line = (
            '  field background DENSE REPACK (base cap %d truecolor page(s) '
            'per field; the LOW-SLOT PROBE below lifts it where free low '
            'slots exist, so fields DO exceed this): %s cell(s) packed '
            'onto %d page(s) across %d field(s) -- %.2f pages per field '
            'against %.2f before. %s exact from the mod, %s borrowed, %s from '
            'the paletted page. Everything not promoted keeps the Cosmos art '
            'the margin pass already wrote into it.'
            % (field_bg_dense.MAX_TRUECOLOR_PAGES, f"{dense['cells']:,}",
               dense['pages'], dense['fields'],
               dense['pages'] / max(dense['fields'], 1),
               dense['pages_before'] / max(dense['fields'], 1),
               f"{dense['from_art']:,}", f"{dense['from_art_borrow']:,}",
               f"{dense['from_vanilla']:,}"))
        # FINDINGS-149. The fix landing, and the number to read first.
        # 0 means the hue detector never fired and this build is build 59.
        _hbc = getattr(field_bg_dense.dense_repack, 'hue_first_cells', 0)
        _hbf = getattr(field_bg_dense.dense_repack, 'hue_first_fields', 0)
        if _hbc:
            _dense_line += (
                ' -- HUE-BROKEN FIRST: %s cell(s) across %s field(s) render '
                'through a palette more than %.3f from their own art in '
                'chromaticity. They are promoted AHEAD of the tile-reuse '
                'order, and they are EXEMPT from TRUE_BLACK -- which is what '
                'was pinning them. MEASURED: mds5_5 (Sector 5 slum outskirts) '
                'had 13 of 40 margin sky cells held on the paletted page '
                'because they are >=25%% opaque black, and that page\'s palette '
                'has a bluest entry of 41, so the sky rendered flat olive. '
                'Promoting costs a 0.9/255 lift on black; refusing costs the '
                'entire hue. Sky cells promoted: mds5_5 27/40 -> 40/40, '
                'mds6_3 5/40 -> 32/40, at NO extra pages in either field. '
                'WATCH FOR: a dark seam where a promoted dark cell meets an '
                'unpromoted neighbour -- that is the risk TRUE_BLACK exists '
                'to avoid and the reason this is scoped to hue-broken cells. '
                'KEPT THE ART: %s cell(s) skipped the borrow recolour, which '
                'takes the DETAIL from Cosmos and the COLOUR from the palette '
                'the tile names -- fatal when that palette cannot hold the '
                'art. mds5_5 margin sky now renders (74.8, 78.2, 74.6), '
                'byte-identical to Cosmos\'s own art, where builds 60 and 61 '
                'gave (79.5, 67.8, 27.8). Scoped to hue-broken cells so the '
                'mds6_2 / Wall Market brown side keeps the recolour. '
                'UNMEASURABLE: %s cell(s) had no art to compare against even '
                'after following ff7nx_marginpage.ORIGIN back to the page they '
                'were moved from. That is NOT "sound" -- it is the detector '
                'unable to look, and it reading as 0.0 is what made build 60 '
                'inert (FINDINGS-150). If this number is large the origin map '
                'is not reaching this pass.'
                % (f"{_hbc:,}", f"{_hbf:,}", field_bg_dense.HUE_BROKEN_DIST,
                   f"{getattr(field_bg_dense.dense_repack, 'hue_kept_art', 0):,}",
                   f"{getattr(field_bg_dense.hue_broken, 'unmeasured', 0):,}"))
        _low = getattr(field_bg_dense.dense_repack, 'low_slots_offered', 0)
        if _low:
            _dense_line += (
                ' -- LOW-SLOT PROBE: %s free slot(s) below %d were offered to '
                '%s (placement %s, FINDINGS-156: \'desc\' + top 14 is the '
                'build 65 probe, \'asc\' + top 25 is build 64). '
                'Slots 29+ do not render on this port '
                '(builds 52 and 55: black squares, no crash), but the engine '
                'reads a page\'s TYPE from section 9 rather than from its slot '
                '(x86 0x62D147) and draws any type-2 page below slot 33 opaque '
                '(x86 0x6403C0), so a truecolor page in a free LOW slot should '
                'draw. If these fields are clean the ceiling is placement, not '
                'capacity.'
                % (f"{_low:,}",
                   field_bg_dense.LOW_SLOT_TOP + 1,
                   ('EVERY field (LOW_SLOT_FIELDS is empty, which means no '
                    'restriction -- the old wording read "0 named field(s)" '
                    'here and looked like the probe had not fired)')
                   if not field_bg_dense.LOW_SLOT_FIELDS else
                   ('%d named field(s) (%s)'
                    % (len(field_bg_dense.LOW_SLOT_FIELDS),
                       ', '.join(sorted(field_bg_dense.LOW_SLOT_FIELDS)[:4]))),
                   '%s (%s free low slot first)'
                   % (field_bg_dense.LOW_SLOT_ORDER,
                      'highest' if field_bg_dense.LOW_SLOT_ORDER == 'desc'
                      else 'lowest')))
        # SUB-UNIT KEY. FINDINGS-247. Printed because HANDOFF-246's second
        # trap is a whole session spent gating a pass that never ran, and
        # the log is the only place that can be checked.
        _suc = getattr(field_bg_dense.dense_repack, 'subunit_cells', 0)
        if _suc:
            _dense_line += (
                ' -- SUB-UNIT KEY: %s layer-2 cut-out cell(s) had their '
                'colour key refined below unit size across %s unit(s) the '
                'mod cuts partially, un-keying %s texel(s). Our key is '
                'uniform over a unit by construction and Cosmos\'s alpha is '
                'not, so where an overlay\'s edge crossed a unit we used to '
                'throw the WHOLE unit away and the silhouette eroded back to '
                'the unit grid -- 3 screen pixels at a time, visible only '
                'where two layers overlap because on layer 1 there is '
                'nothing behind. MEASURED over 5 fields before the fix: '
                '50,196 partially-cut units and we keyed 42,481 of them. '
                'Threshold is PageArt.hmask (alpha >= 128, the 50%% rule) '
                'and NOT tmask (alpha >= 8) -- Cosmos outlines its overlays '
                'dark, and drawing a quarter-alpha outline opaque is a black '
                'fringe one texel wide. Un-keying only ever makes a texel '
                'opaque, never transparent, and only inside a unit that '
                'keeps at least one keyed texel, so no cell loses its key. '
                'Set SEVENTH_NX_NO_SUBUNIT_KEY=1 to restore build 118.'
                % (f"{_suc:,}",
                   f"{getattr(field_bg_dense.dense_repack, 'subunit_units', 0):,}",
                   f"{getattr(field_bg_dense.dense_repack, 'subunit_texels', 0):,}"))
        # MOD-CLEAR KEY. FINDINGS-253. Same reason as the block above: a pass
        # that silently never fires is HANDOFF-246's second trap, and the log
        # is the only place it can be checked after the fact.
        _mcc = getattr(field_bg_dense.dense_repack, 'modclear_cells', 0)
        if _mcc:
            _dense_line += (
                ' -- MOD-CLEAR KEY: %s layer-2 cut-out cell(s) gained the '
                'colour key on %s texel(s) where COSMOS PAINTS NOTHING but '
                'vanilla\'s index is not 0 (%s of those cells are clear in '
                'full). We took the key from vanilla\'s index and the FILL '
                'from vanilla\'s pixel, so a texel the mod calls empty over a '
                'non-zero vanilla index was neither keyed nor skipped -- it '
                'was painted with the 1997 art\'s hard black outline. That is '
                'the black stair-step on the Highwind hull, and it is why one '
                'silhouette is flawless in places and choppy in others: where '
                'the old outline happened to be index 0 we keyed it, where it '
                'was a dark non-zero index we drew it. MEASURED on fship_1: '
                'of 312,897 texels the mod calls clear we keyed 307,052 '
                '(98.1%%) and drew 5,845, of which 2,683 are near-black. '
                'Threshold is PageArt.tmask (alpha < 8) and NOT hmask -- this '
                'arm ADDS key, so it must be sure the mod paints NOTHING, '
                'which is the OPPOSITE conservative end from SUB-UNIT KEY '
                'above. Adding key reveals what is behind; the per-texel '
                'reveal census (_kmodclear.py) is what proves something does. '
                'Set SEVENTH_NX_NO_MODCLEAR_KEY=1 to restore build 120.'
                % (f"{_mcc:,}",
                   f"{getattr(field_bg_dense.dense_repack, 'modclear_texels', 0):,}",
                   f"{getattr(field_bg_dense.dense_repack, 'modclear_whole', 0):,}"))
        # THIN STRUCTURE RECOVERED FROM THE NATIVE ALPHA. FINDINGS-258.
        _wt = getattr(field_bg_dense.dense_repack, 'wire_texels', 0)
        if _wt:
            _dense_line += (
                ' -- THIN STRUCTURE: %s texel(s) were handed back from the '
                'colour key and given the mod\'s own colour, because the mod '
                'PAINTS there and only the downsample said otherwise. '
                'PageArt.tmask is alpha < 8 computed AFTER resample_rgba, an '
                'alpha-weighted BOX filter, so it asks whether the AVERAGE '
                'coverage of a texel is under 3%% -- and for a thin structure '
                'that is the wrong question. mds7plr1\'s fence wire is about '
                'one native pixel wide, 1024 -> 768 puts ~1.8 native pixels '
                'in a texel, and a wire crossing a corner averages under the '
                'threshold; the mod-clear arm then keyed it and the green '
                'background showed through as specks. MEASURED against the '
                'native DDS: 8,508 keyed texels on mds7plr1 and 3,968 on '
                'fship_2 have native art that is fully OPAQUE, and `present` '
                'and `opaque` are the SAME number -- the signature of a thin '
                'structure lost to a box filter, not of an antialiased edge. '
                'PageArt.amax/cmax max-pool the NATIVE alpha and carry that '
                'pixel\'s colour, which matters because rgb_to_565 returns '
                'EMPTY below the alpha cut: without cmax these texels come '
                'back at NEAR_BLACK and the fix trades a green speck for a '
                'black one (measured at exactly mean luminance 8.0). Scoped '
                'to BRIGHT recovered colour so Cosmos\'s own dark outline '
                'stays keyed -- archive-wide ZERO texels handed back render '
                'near-black, mean luminance 97.6 of 255, and zero are newly '
                'keyed. Set SEVENTH_NX_NO_PAINT_MAXPOOL=1 to restore '
                'build 123.'
                % f"{_wt:,}")
        # COVER-LICENSED MOD-CLEAR KEY. FINDINGS-257.
        _cc = getattr(field_bg_dense.dense_repack, 'cover_cells', 0)
        if _cc:
            _dense_line += (
                ' -- COVER LICENCE: %s cell(s) carried a per-texel proof '
                'that SOMETHING draws behind them, which lets the MOD-CLEAR '
                'arm above key a texel that is not black. Build 121 could '
                'only key what was already black, so the non-black half of '
                'every fat edge stayed at UNIT resolution -- one game pixel, '
                'four screen pixels, a visible stair-step. MEASURED with '
                '_kstep.py on fship_1: 955 uniform boundary units -> 488. '
                'The cover raster INCLUDES the parallax, and that is the one '
                'place layers 3/4 are allowed to matter: for COLOUR a '
                'scrolling backdrop is useless, but for the KEY "does '
                'anything draw here" has the same answer at every camera '
                'position. It excludes other layer-2 cells and any parallax '
                'cell that is not opaque throughout, because this arm keys '
                'those too and cover taken from them is not a fixed point. '
                'archive-wide the fat edge (_kedge extra) falls 1,776,580 -> '
                '131,799, and ZERO non-black texels are keyed with nothing '
                'at all behind them. Set SEVENTH_NX_NO_MODCLEAR_COVER=1 to '
                'restore build 122.'
                % f"{_cc:,}")
        # BAKED PARTIAL-ALPHA BLEND. FINDINGS-255.
        _bl = getattr(field_bg_dense.dense_repack, 'blend_cells', 0)
        if _bl:
            _dense_line += (
                ' -- BAKED BLEND: %s cell(s) had %s texel(s) of dark rim '
                'blended toward what draws behind them, from %s cell(s) '
                'whose backdrop could be judged at all. Cosmos authors those '
                'texels at alpha 128..249 and FFNx BLENDS them; a truecolor '
                'page here has a 1-bit colour key and no alpha, so we drew '
                'them at full strength and the result is a hard dark rim '
                'tracing every silhouette. We cannot ship alpha, but we can '
                'ship its RESULT -- alpha*mod + (1-alpha)*backdrop is exactly '
                'the pixel FFNx produces. COLOUR ONLY: not one texel changes '
                'keyed status, no page, tile, byte or kilobyte moves. Scoped '
                'to texels that render below %s of 255 AND whose backdrop is '
                'brighter in EVERY channel, so the arm can only ever lighten '
                'a dark rim -- art can never become black even if the '
                'backdrop were wrong. Layers 3 and 4 are never used as a '
                'backdrop: they scroll, so what is behind them is a '
                'different answer at every camera position. MEASURED over '
                '681 fields: 177,576 texels, mean lift +12.7 of 255, zero '
                'darkened. Set SEVENTH_NX_NO_BLEND=1 to restore build 121.'
                % (f"{_bl:,}",
                   f"{getattr(field_bg_dense.dense_repack, 'blend_texels', 0):,}",
                   f"{getattr(field_bg_dense.dense_repack, 'backdrop_cells', 0):,}",
                   field_bg_dense.BLEND_DARK))
        # IN-PLACE PARALLAX CONVERSION. FINDINGS-249.
        _ip = getattr(field_bg_dense.dense_repack, 'inplace_big', 0)
        if _ip:
            _dense_line += (
                ' -- IN-PLACE PARALLAX: %s 32-unit page(s) in %s field(s) '
                'were converted from 8-bit to truecolor IN THEIR OWN SLOT, '
                'promoting %s cell(s). A parallax page whose every key is '
                'promotable frees exactly one page and needs exactly one, so '
                'it does not need a FREE slot -- and that was the only thing '
                'stopping fship_2, whose slots 4..14 are eleven convertible '
                '32-unit paletted pages while its only free slots (26/27/28) '
                'go to the 16-unit half. The engine reads a page TYPE from '
                'section 9 rather than from its slot (x86 0x62D147) and draws '
                'any type-2 page below slot 33 opaque (x86 0x6403C0), and '
                'build 119 already ships 32-unit truecolor pages in low slots '
                '(mtcrl_4 at 12/13/14). PAGE-NEUTRAL by construction. The '
                'cost is HEAP: %0.2f MB per conversion at %dpx, bounded by '
                'field_bg_dense.FIELD_MB_CAP = %.1f MB, which is the heaviest '
                'field already proven on hardware (mrkt4, 27.31 MB). That '
                'ceiling is why fship_2 takes 4 of its 11 and not all 11. '
                'Set SEVENTH_NX_NO_INPLACE_BIG=1 to restore build 119.'
                % (f"{_ip:,}",
                   f"{getattr(field_bg_dense.dense_repack, 'inplace_fields', 0):,}",
                   f"{getattr(field_bg_dense.dense_repack, 'inplace_cells', 0):,}",
                   (field_bg_repack._page_bytes(px, 2)
                    - field_bg_repack._page_bytes(256, 1)) / 1048576.0,
                   px, field_bg_dense.FIELD_MB_CAP))
        log(_dense_line)
    if up_fields:
        log(f'  field background: UPSCALED {up_pages} page(s) in {up_fields} '
            f'field(s) -> {up_new} truecolor page(s), {up_cells:,} cell(s) '
            f'at {px}x{px}')
        # COVERAGE, STATED AS A FRACTION.
        #
        # "184 pages in 113 fields" reads like a result. Against the 692
        # fields the mod actually ships art for it is 16%, and the other 84%
        # of the game draws vanilla pages -- which on a console looks like
        # the mod is not installed. That went unnoticed for a whole build
        # because the two numbers were never printed next to each other.
        _avail = len(art.fields()) if art else 0
        if _avail:
            _pct = 100.0 * up_fields / _avail
            log(f'      COVERAGE: {up_fields} of {_avail} field(s) the mod '
                f'ships art for got any of it ({_pct:.0f}%)')
            if _pct < 60.0:
                log('      ! most of the game is still drawing VANILLA pages. '
                    'If the centre of the screen looks unmodded while the '
                    '16:9 side regions look upscaled, this line is why --')
                log('        Settings -> Field background: choose the '
                    '"Uniform - 256px" preset (page growth "No growth"). '
                    'Replace-only promotes only pages where EVERY tile can '
                    'move, which the mod\'s added wide pages always satisfy '
                    'and vanilla centre pages almost never do.')
        log(f'      {up_single} page(s) the mod dumped as a single image -- '
            'the engine only ever makes one texture for those, so every tile '
            'on them is exact')
        log(f'      {up_art:,} cell(s) kept their paletted page -- the mod\'s '
            'own art is transparent there, and a truecolor page turns '
            'transparent into opaque black')
        log(f'      {up_fx:,} tile(s) kept their paletted page -- they draw '
            'from an fx page on animation frames and the two share one u,v')
        log(f'      {up_transparent:,} tile(s) kept their paletted page -- '
            'their cell has a colour key and a truecolor page cannot hold '
            'one')
        log(f'      {up_exact} multi-palette page(s) fully covered; '
            f'{up_borrowed:,} of {up_cells:,} cell(s) '
            f'({100.0 * up_borrowed / max(up_cells, 1):.1f}%) took a '
            'neighbouring palette')
        if up_wrongpal:
            log(f'      {up_wrongpal:,} cell(s) kept their paletted page -- '
                f'the mod ships no image for their OWN palette, and at slot '
                f'0x0F and up the engine makes one texture per palette, so '
                f'borrowing a neighbour\'s would draw them in the wrong '
                f'COLOURS. They still draw, in their own colours, at the '
                f'same size -- they just do not get the colour-depth '
                f'upgrade. MEASURED before this rule: 20.9% of promoted '
                f'cells were the wrong colour, which is what "upscaled and '
                f'stock textures side by side" actually was. Set '
                f'{field_bg_repack.BORROW_ENV}=nearest for the old '
                f'behaviour.')
    if cmp_fields:
        log(f'      COMPACTED: {cmp_saved} page(s) freed across {cmp_fields} '
            f'field(s) by packing the leftovers back down -- {cmp_moved:,} '
            f'cell(s) relocated, {cmp_merged:,} merged as byte-identical. '
            f'Every present page is a texture whether it holds 256 cells or '
            f'6, and after promotion the originals are mostly empty. The '
            f'cells move by the same two u32s the promotion rewrites, '
            f'between pages of the same depth, size and blend group, bytes '
            f'copied verbatim -- so this frees textures without changing a '
            f'pixel. Verify with verify_compact.py; turn it off with '
            f'{field_bg_compact.COMPACT_ENV}=0.')
    if cmp_windowed:
        _c = sum(len(v) for _n, v in cmp_windowed)
        log(f'      FRAME-LIMIT GUARD: {len(cmp_windowed)} field(s) left '
            f'UNCOMPACTED because packing them would have put {_c} page(s) '
            f'over 256 tiles in one camera frame -- '
            + ', '.join(n for n, _ in cmp_windowed[:10])
            + ('' if len(cmp_windowed) <= 10 else ', ...'))
        log('          Compaction merges BYTE-IDENTICAL cells, so several '
            'tiles come to share one cell and a page can pass 256 without '
            'gaining a single cell. add_page_tile (x86 0x6464BA) has room for '
            'exactly 256 and does not bounds-check, so tile 257 writes into '
            'the next page\'s counter and the allocator is asked for '
            'hundreds of megabytes. MEASURED: las0_2, the bottom of the '
            'Northern Cave, CRASHED the game on hardware the moment the '
            'camera scrolled up -- Cosmos ships that field with every page at '
            'most 256 in a frame and 1.00 tiles per cell; compaction made it '
            '547 and 2.14, saving ZERO pages. FINDINGS-128.')
        log('          Refusing costs only pages, never quality: the cells '
            'are byte-identical, so the picture is the same either way. '
            'MEASURED across the archive, no field goes over the 16-page '
            'ceiling and none loses a truecolor promotion.')
    if cmp_rejected:
        log(f'      ! {len(cmp_rejected)} field(s) FAILED the compaction '
            f'self-check and were left exactly as they came in. This is a '
            f'bug in field_bg_compact, not a setting -- please report it '
            f'with this list:')
        for nm, why in cmp_rejected[:12]:
            log(f'          {nm:<12} {why}')
        if len(cmp_rejected) > 12:
            log(f'          ... and {len(cmp_rejected) - 12} more')
    elif not field_bg_compact.enabled():
        log(f'      compaction is OFF ({field_bg_compact.COMPACT_ENV}=0). '
            f'That is the pass that pays for the promoted pages out of the '
            f'originals; without it the page count grows by about 2 per '
            f'field and that is what black squares are.')
    if up_dropped or up_capped:
        log(f'      {up_dropped} original page(s) freed -- nothing points at '
            f'them any more, so they no longer cost a texture')
        if up_capped and field_bg_repack.budget_bytes() >= \
                field_bg_repack.UNLIMITED:
            # MISATTRIBUTION, fixed. With the budget Unlimited nothing can be
            # capped BY the budget, but this line still claimed it was and
            # quoted the 1<<60 sentinel as "1099511627776.0 MB". What actually
            # held these back is no-growth's ceiling loop: a field that still
            # holds more pages than it started with after compaction is
            # repacked at a lower ceiling until it does not.
            log(f'      {up_capped} page(s) left paletted by the NO-GROWTH '
                f'ceiling -- their field still held more pages than vanilla '
                f'after compaction, so it was repacked lower until it did '
                f'not. The budget is Unlimited and capped nothing.')
        elif up_capped:
            mb = field_bg_repack.budget_bytes() / 1048576.0
            log(f'      {up_capped} page(s) left paletted to stay inside the '
                f'{mb:.1f} MB per-field texture budget. Every present page '
                f'becomes a texture, a 512px truecolor one costs 1.5 MB '
                f'(raw + the 32bpp surface), and field_load_textures '
                f'(x86 0x640292) aborts the whole loop on the first failure '
                f'-- every page after it keeps handle 0 and never draws, '
                f'which is what the scattered black squares were. MEASURED: '
                f'fields with 1 truecolor page were clean, fields with 3-4 '
                f'were black. Tune with '
                f'{field_bg_repack.BUDGET_ENV}=<megabytes>.')
    if palclamp['tiles']:
        log(f"      PALETTE CLAMP: {palclamp['tiles']} tile(s) in "
            f"{palclamp['fields']} field(s) named a palette the field does "
            f"not have, and were repointed at the palette their cell is "
            f"actually drawn with.")
        log('          Cosmos leaves the palette byte of its widescreen tiles '
            'at whatever it was, because FFNx replaces the page with a DDS '
            'and never applies it. The Switch applies it, and an index past '
            'the end of the table reads whatever follows -- which is the '
            'black rectangles down the edges of md8_1.')
    if pagecap['fields'] or pagecap_dropped or pagecap_refused:
        log(f"      PAGE CAP (256 tiles per page, the game's own limit): "
            f"{pagecap['fields']} field(s) had a page split, "
            f"{pagecap['pages']} page(s) added, {pagecap['tiles']} tile(s) "
            f"repointed. Worst page held {pagecap['worst']} tiles.")
        log('          add_page_tile (x86 0x6464BA) appends into 42 slots of '
            '0x1804 bytes -- 4 of count then exactly 256 entries of 0x18 -- '
            'and never bounds-checks, so tile 257 writes its x coordinate '
            "into the NEXT page's counter and the submit loop turns that "
            'into a several-hundred-MB malloc. FINDINGS-110.')
        log('          The split duplicates the page byte for byte and '
            'repoints the excess tiles, so every moved tile keeps its u, v '
            'and palette and samples identical texels. The cap is '
            'max(256, what vanilla already does), because the limit is on '
            'SIMULTANEOUSLY VISIBLE tiles and a scrolling field only ever '
            'submits a screenful.')
    if pagecap_single['fields']:
        log(f"          WINDOW CAP: "
            f"{pagecap_single['fields']} field(s), "
            f"{pagecap_single['pages']} page(s) added, "
            f"{pagecap_single['tiles']} tile(s) repointed -- "
            + ', '.join(pagecap_single['names'][:12])
            + ('' if len(pagecap_single['names']) <= 12 else ', ...'))
        log('          add_page_tile is called once per tile SUBMITTED THIS '
            'FRAME, which is the tiles inside the camera window wherever the '
            'camera is. MEASURED with a sliding window over all 709 fields: '
            'vanilla NEVER exceeds 256 in a window, at any camera position, '
            'and reaches exactly 256 -- the original tooling packed right up '
            'to the limit and never past it. Our build broke that in 27 '
            'fields, 2,682 tiles over. FINDINGS-123.')
        log('          The count is the BINDING page (the fx page when a tile '
            'carries one), not the raw texture id. By raw id vanilla itself '
            'is "over" constantly -- blue_2 739, hyou5_2 953 -- and has '
            'shipped since 1997, which is how we know the raw id is not what '
            'add_page_tile sees.')
        log('          Build 34 keyed this on "the field fits on one screen", '
            'a heuristic that could only ever see 202 of 709 fields. The '
            'window measure is a strict superset -- a field that fits in the '
            'window has one window position, so its in-window count IS its '
            'total. VERIFIED offline: 0 pages the old rule caught that this '
            'one misses, 0 fields where it does less work. FINDINGS-122 fixed '
            'md8_1 with it; the 9 fields it could not see are all SCROLLING '
            'ones -- trnad_3 355, ghotel 349, datiao_8 326, junpb_3 296, '
            'qd 267, blin63_1 264, games 262, trnad_4 261, delmin1 257.')
    if pagecap_single['strategy']:
        log('          split strategy: '
            + ', '.join(f'{k} {v}' for k, v in
                        sorted(pagecap_single['strategy'].items()))
            + ' -- section-9 order is roughly RASTER order, so a sequential '
            'chunk is a spatially contiguous band and a camera window over it '
            'still draws nearly all of it. Round-robin over screen position '
            'spreads each window across the copies and so needs fewer pages: '
            'nivl_b22 p20 (852 tiles) is 232 in-window after a sequential '
            'split and 179 after round-robin, which is 3 pages instead of 4 '
            'in a band with 2 free slots. Sequential is tried first at every '
            'size, so nothing the cap already handled is divided differently.')
    if green_lsb['texels']:
        log(f"      GREEN-LSB BACKSTOP: {green_lsb['texels']:,} truecolor "
            f"texel(s) in {green_lsb['fields']} field(s) had green's low bit "
            f"set and were masked -- " + ', '.join(green_lsb['names'][:10]))
        log('          The engine\'s non-565 display path (x86 0x63F350) ORs '
            'green\'s low bit onto the TOP BIT OF BLUE, so a texel with '
            '0x0020 set gains a large blue component there. Vanilla never '
            'sets it; we do, on whole pages at a time, every value exactly '
            'vanilla + 0x0020. It is NOT the resize (verified byte-exact). '
            'THE WRITER HAS NOT BEEN FOUND -- this is a backstop, and this '
            'number falling to zero is how we will know it was fixed. '
            'FINDINGS-131.')
    if ng_frame_kept:
        log(f'      FRAME GUARD, COLOUR KEPT: {len(ng_frame_kept)} field(s) '
            f'kept their page count -- and so their truecolor -- because the '
            f'frame guard declined to pack them, not because the repack grew '
            f'them: '
            + ', '.join(n for n, _b, _a in ng_frame_kept[:10])
            + ('' if len(ng_frame_kept) <= 10 else ', ...'))
        log('          The no-growth loop drops the truecolor ceiling until a '
            'field stops growing. That is right when the repack caused the '
            'growth and wrong when field_bg_compact DECLINED to pack for '
            'frame safety -- the field needs those pages either way, and '
            'giving up colour depth does not make it safer. Build 42 shipped '
            'the guard without this and lost 26,160 truecolor cells across 60 '
            'fields. Bounded by the page ceiling, which is the real budget.')
    if pagecap_reloc['fields']:
        log(f"          CELL RELOCATION: {pagecap_reloc['cells']} cell(s) "
            f"moved in {pagecap_reloc['fields']} field(s) -- "
            + ', '.join(pagecap_reloc['names'][:12])
            + ('' if len(pagecap_reloc['names']) <= 12 else ', ...'))
        log('          These pages had NO free slot to duplicate into -- the '
            'opaque truecolor band is three slots (26, 27, 28) and all three '
            'were in use. But the pages already there were packed unevenly: '
            'rckt3 held 256 / 272-OVER / 2 with 254 free cells on the third. '
            'So the cell is copied byte-for-byte into free space on a page of '
            'the same depth, size flag and blend group that is ALREADY in the '
            'field, and the tile\'s u/v are rewritten to point at it. No page '
            'is added. VERIFIED: every tile of every touched field samples '
            'identical texels and the same palette before and after. '
            'FINDINGS-126.')
        log('          Only pages bound by the TEXTURE ID. A tile has one u/v '
            'pair shared with its fx page, and an fx pair must sit at the same '
            'grid coordinate, so moving one half would break it. las0_2 page '
            '21 is bound by 547 fx tiles and 0 by texture id -- it is named '
            'below rather than half-moved.')
    if not field_bg_pagecap.FX_SPLIT:
        log('          fx-byte split is OFF. A tile carrying an fx page BINDS '
            'the fx page, and this splitter repoints the texture id at offset '
            '32, so those pages are named below rather than fixed. That set '
            'holds the worst in the archive -- nivl_b22 p20 716, nivl_b2 p20 '
            '700, las0_2 p21 547. Enable field_bg_fx_split to split them; it '
            'writes a byte no pass has written before and gets its own build.')
    if pagecap_dropped:
        log(f'      ! page cap: {len(pagecap_dropped)} field(s) needed a '
            f'split that would cross FIELD_BG_RAW_CAP, so they keep the '
            f'over-full page and CAN STILL CRASH: '
            + ', '.join(n for n, _ in pagecap_dropped[:12]))
    if pagecap_refused:
        log(f'      ! page cap: {len(pagecap_refused)} field(s) could not be '
            f'capped (tiles naming an absent page, or no free slot in the '
            f'42-page array): '
            + ', '.join(n for n, _ in pagecap_refused[:12]))
    if raw_capped:
        worst = sorted(raw_capped, key=lambda r: -r[1])[:12]
        log(f'  ! field background: {len(raw_capped)} of {len(page_cost)} '
            f'field(s) DROPPED their whole repack because the field would '
            f'decompress to more than FIELD_BG_RAW_CAP '
            f'({FIELD_BG_RAW_CAP:,} bytes). Each one ships its pre-repack '
            f'section 9 -- no promotion, no compaction, nothing.')
        for nm, n in worst:
            log(f'        {nm:<12} would have been {n:,} bytes')
        if len(raw_capped) > len(worst):
            log(f'        ... and {len(raw_capped) - len(worst)} more')
        log('    THIS IS A BUILD POLICY, NOT A HARDWARE LIMIT. The cap is '
            '4/5 of the 2,097,152-byte loader buffer that has been proven on '
            'hardware; crossing it makes ff7nx_fieldbg.field_buffer_bytes '
            'patch the loader to the next power of two, which has not been. '
            'At 256px it never fires. At a bigger page size it fires for '
            'most of the game, which is what "512px does not work" has '
            'looked like.')
        log('    To go past it: raise FIELD_BG_RAW_CAP in build.py and accept '
            'the larger loader buffer. With the guest heap raised there is '
            'room for it -- an 8 MB buffer is 3% of a 256 MB heap where it '
            'was 13% of the stock 64 MB.')
    if skipped_all_or_nothing:
        worst = sorted(skipped_all_or_nothing,
                       key=lambda r: -r[1])[:12]
        log(f'      {len(skipped_all_or_nothing)} field(s) kept their vanilla '
            f'background because not ALL of their pages fitted -- '
            f'all-or-nothing is on, so a field is never left half truecolor '
            f'and half paletted:')
        for nm, n in worst:
            log(f'          {nm:<12} {n} page(s) would have been promoted')
        if len(skipped_all_or_nothing) > len(worst):
            log(f'          ... and {len(skipped_all_or_nothing) - len(worst)}'
                f' more')
        log(f'      Raise or remove the budget ({field_bg_repack.BUDGET_ENV}'
            f'=0 for unlimited), drop to a smaller page size, or set '
            f'{field_bg_repack.PARTIAL_ENV}=1 to promote what fits.')
    thr = field_bg_repack.black_cell_threshold()
    if thr > 0:
        log(f'      cells at least {thr * 100:.0f}% opaque black kept their '
            f'paletted page, where black is exactly RGB(0,0,0). A truecolor '
            f'page has no index channel, so 0x0000 has to mean transparent '
            f'(x86 0x6470E0) and solid black takes a {0.9:.1f}/255 lift '
            f'instead. MEASURED: this makes ~85% of black pixels true black '
            f'for ~7.5% of cells. Tune with '
            f'{field_bg_repack.TRUE_BLACK_ENV} (0 = off, 1.0 = only '
            f'fully-black cells).')
    if no_cover or no_fit:
        log(f'  field background: {no_cover} page(s) left paletted -- the mod '
            'ships no image for any palette they use'
            + (f'; {no_fit} more for want of a free truecolor slot'
               if no_fit else ''))
    _log_page_cost_report(page_cost, px, log, ng_retry, ng_over, ng_margin)
    if nf:
        log(f'  field background: {npg} truecolor page(s) in {nf} field(s) '
            f'rescaled to {px}x{px}')
    if FIELD_BG_MAX_RAW:
        buf = ff7nx_fieldbg.field_buffer_bytes(FIELD_BG_MAX_RAW)
        log(f'  field background: largest field now decompresses to '
            f'{FIELD_BG_MAX_RAW:,} bytes; the game\'s buffer is 2,000,000, so '
            f'exefs/main will be patched to {buf:,}')
    if skipped:
        log(f'  field background: {len(skipped)} section(s) did not parse as '
            'a background and were left at 256px:')
        for name, why in skipped[:6]:
            log(f'      {name}: {why}')
        if len(skipped) > 6:
            log(f'      ... and {len(skipped) - 6} more')
    return nf, npg, grew


def _lift_depth1_payloads(archive, payloads, log=lambda *_: None):
    """
    Rewrite every depth-1 page in every payload at `field_bg_native.D1_PAGE_PX`.

    FINDINGS-223. No-op unless the depth-1 size has been raised.

    WHY THE CAP CHECK IS HERE AND NOT IN THE CAP PASS. `cap_section9` decides
    its budget while depth-1 pages are still 256, so its arithmetic is 1.21x
    optimistic once this runs. Rather than teach it a size it cannot see yet,
    this refuses outright: a field that would cross FIELD_BG_RAW_CAP keeps its
    UNLIFTED payload and is named, because half a lift is not a smaller
    picture, it is a desynchronised TEXTURE walk.

    MEASURED before this shipped (`_k512gate.py`, all 741 entries off the
    build-107 archive): the largest field lands at 5,878,431 against a
    13,421,772 cap, so `refused` should be empty and a non-empty one is worth
    stopping for.
    """
    dst = FIELD_BG_D1_TARGET_PX
    if dst == field_bg_native.VANILLA_PX:
        return
    if field_bg_native.D1_PAGE_PX != field_bg_native.VANILLA_PX:
        raise RuntimeError(
            'the depth-1 lift was reached with D1_PAGE_PX already at %d; '
            'some pass upstream has been parsing paletted pages at the wrong '
            'size' % field_bg_native.D1_PAGE_PX)
    px = ff7nx_fieldbg.page_px()
    if px == ff7nx_fieldbg.OFF_PAGE_PX:
        px = field_bg_native.VANILLA_PX
    n_fields = n_pages = 0
    refused, failed, untouched = [], [], 0
    biggest = 0
    # EVERY FIELD IN THE ARCHIVE, not just the ones this build changed.
    #
    # `payloads` holds only modified fields. The ~30 fields no pass touched
    # would ship straight from the source archive with 256px paletted pages
    # while the module reads 512 -- so they are exactly the fields that would
    # break, and iterating `payloads` would have missed all of them. Same
    # loop shape as `ff7nx_marginblack.apply_to_flevel`, which is the
    # established contract for a late pass.
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        payload = payloads.get(name)
        try:
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
        except Exception as exc:                               # noqa: BLE001
            failed.append((name, '%s: %s' % (type(exc).__name__, exc)))
            continue
        if payload is None:
            untouched += 1
        try:
            parts = lgp.split_sections(raw)
            new9, k = field_bg_native.lift_depth1(
                parts[8], px, field_bg_native.VANILLA_PX, dst,
                art=field_bg_shadow.lift_art(name))
            if not k:
                continue
            parts[8] = new9
            new_raw = lgp.join_sections(parts)
        except Exception as exc:                               # noqa: BLE001
            failed.append((name, '%s: %s' % (type(exc).__name__, exc)))
            continue
        if len(new_raw) > FIELD_BG_RAW_CAP:
            refused.append((name, len(new_raw)))
            continue
        biggest = max(biggest, len(new_raw))
        payloads[name] = _encode_field_cached(archive, new_raw)
        n_fields += 1
        n_pages += k
    if untouched:
        log('    (%d field(s) had no payload before this pass and were lifted '
            'straight from the source archive -- they carry paletted pages '
            'too, so leaving them alone was never an option here)' % untouched)
    log('  field background DEPTH-1 LIFT: %s paletted page(s) taken from '
        '%dx%d to %dx%d across %s field(s). Every texel not covered by the '
        'ART line below is 2x replication, so it is build 108 exactly and '
        'any visible difference there is the module patch, not the art. '
        'Largest field now %s bytes against a %s cap.'
        % (f'{n_pages:,}', field_bg_native.VANILLA_PX,
           field_bg_native.VANILLA_PX, dst, dst, f'{n_fields:,}',
           f'{biggest:,}', f'{FIELD_BG_RAW_CAP:,}'))
    _sh = field_bg_shadow.summarise()
    if _sh:
        log(_sh)
    # A PARTIAL LIFT IS NOT A DEGRADED BUILD, IT IS A BROKEN ONE, so this
    # raises rather than logging and carrying on. A field left at 256 while
    # the module reads 0x40000 per paletted page desynchronises its TEXTURE
    # walk on the first one; the failure is not confined to that field's
    # appearance and there is no partial credit to bank.
    if refused or failed:
        for name, size in refused:
            log('  ! field background DEPTH-1 LIFT: %s would be %s bytes, '
                'over FIELD_BG_RAW_CAP' % (name, f'{size:,}'))
        for name, err in failed:
            log('  ! field background DEPTH-1 LIFT: %s -- %s' % (name, err))
        raise RuntimeError(
            'the depth-1 lift could not cover %d field(s) (%d refused for '
            'size, %d failed to parse). Every paletted page in the archive '
            'has to move together with the module, so this build is not '
            'shippable and nothing further will be written. Set '
            '%s=256 to build without the lift.'
            % (len(refused) + len(failed), len(refused), len(failed),
               ff7nx_fieldbg.D1_PX_ENV))


def _encode_field_cached(archive, raw):
    """
    archive.encode_field() with the result cached by content.

    The compressor and its verify pass are both pure Python, so the first
    build over a mod that rewrites hundreds of fields spends real time here
    -- Cosmos Limit Break's 683 fields are ~300 MB of raw field data. Keying
    the cache on the RAW BYTES (not the source path) means it also survives
    a mod being re-extracted, an option being toggled off and back on, and
    two fields that happen to compose to the same result.
    """
    try:
        os.makedirs(FIELDLZS_CACHE, exist_ok=True)
        path = os.path.join(
            FIELDLZS_CACHE,
            hashlib.sha1(b'FIELDLZS-V1-' + raw).hexdigest())
        if os.path.exists(path):
            with open(path, 'rb') as f:
                return f.read()
    except OSError:
        path = None
    out = archive.encode_field(raw)
    if path:
        try:
            tmp = path + '.part'
            with open(tmp, 'wb') as f:
                f.write(out)
            os.replace(tmp, path)
        except OSError:
            pass
    return out


def _debleed_textures(name, mod_files, van, log):
    """
    Recolour the transparent palette entry of every colour-keyed texture in
    a model archive, vanilla or mod, so bilinear filtering stops drawing
    black lines along the gutters of the atlas.

    Runs for EVERY model archive, not just the field ones. The seam is a
    property of FF7's texture format, not of any particular module, and it is
    just as visible on an enemy as on an NPC. Measured on the vanilla
    archives this project ships:

        char.lgp    695 TEX, 458 de-fringe
        battle.lgp  787 TEX, 322 colour-keyed, 319 de-fringe

    For battle.lgp this also covers what `convert_for_battle` cannot: that
    function refuses anything already paletted, so it only ever fixed the
    truecolor mod textures it converts itself -- every vanilla enemy skin
    kept its black entry and its seams.

    Applied to the MOD's files and, where an entry is untouched by the mod,
    left alone -- vanilla entries are only rewritten if the mod does not
    replace them, which is handled by the caller merging this into
    `mod_files`. Anything the fix cannot prove safe is skipped: no colour
    key, no transparent texels, not paletted, not a TEX.

    Every rewrite is verified with tex.check_indices_unchanged before it is
    accepted, so a bug here cannot silently alter artwork -- it can only fail
    to help.
    """
    if os.environ.get(NO_DEBLEED_ENV, '').strip().lower() in (
            '1', 'true', 'yes', 'on'):
        return mod_files
    os.makedirs(DEBLEED_CACHE, exist_ok=True)
    out = dict(mod_files)
    done = refused = from_van = 0

    # Every .tex in the archive, not just the ones the mod replaces. The seam
    # is in FF7's own atlas layout, so a vanilla NPC the mod leaves alone has
    # it too -- and most of the characters you notice it on are exactly the
    # ones nobody bothered to remake. A vanilla entry that de-fringes is added
    # to the overlay so it actually reaches the archive; one that does not is
    # left out entirely, so untouched entries are still reused byte for byte.
    todo = [(low, src, mod) for low, (src, mod) in mod_files.items()]
    todo += [(low, path, None) for low, path in (van or {}).items()
             if low not in mod_files]

    for low, src, mod in todo:
        # Detected by CONTENT, not by name. battle.lgp's textures have no
        # extension at all -- they are called "aa", "da" and so on -- so an
        # `endswith('.tex')` filter silently skipped the entire archive, which
        # is exactly the half of this that enemies live in. tex.parse() is
        # strict enough to be the test: it checks the version word AND that
        # the file length matches the header's own dimensions exactly, which
        # is what stops a .p model (also version 1) being mistaken for one.
        try:
            with open(src, 'rb') as f:
                head = f.read(4)
                if len(head) < 4 or head != b'\x01\x00\x00\x00':
                    continue
                data = head + f.read()
        except OSError:
            continue
        key = 'DEBLEED-V1-' + ('van-' if mod is None else 'mod-') + _sig(src)
        cached = os.path.join(
            DEBLEED_CACHE,
            '%s.%s.%s' % (name, low,
                          hashlib.sha1(key.encode()).hexdigest()[:16]))
        if os.path.exists(cached):
            out[low] = (cached, mod)
            done += 1
            continue
        try:
            new, _note = tex.debleed(data)
        except Exception:
            continue
        if new is None:
            continue
        if not tex.check_indices_unchanged(data, new):
            refused += 1
            continue
        tmp = cached + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(new)
        os.replace(tmp, cached)
        out[low] = (cached, mod)
        done += 1
        if mod is None:
            from_van += 1
    if done:
        log('  %s: %d texture(s) de-fringed (%d of them vanilla entries the '
            'mod does not replace) -- the transparent palette entry now '
            'carries the colour of the art beside it instead of black, so '
            'filtering stops drawing a dark line along every atlas seam '
            '(set %s=1 to disable)' % (name, done, from_van, NO_DEBLEED_ENV))
    if refused:
        log('  ! %s: %d de-fringe(s) REFUSED -- the rewrite would have changed '
            'pixel indices, so the original was kept' % (name, refused))
    return out


def _cap_field_textures(name, mod_files, log, max_dim):
    """
    Downscale any mod TEX file wider or taller than `max_dim`, format
    preserved (see tex.cap_dimensions). Opt-in companion to
    _convert_battle_textures: that one exists because the battle module
    NEEDS paletted data; this one exists only because a mod's textures can
    be larger than the hardware has room for all-at-once, so it is gated
    behind an explicit cap rather than applied by default.
    """
    os.makedirs(TEXCAP_CACHE, exist_ok=True)
    out = {}
    capped = 0
    saved_bytes = 0
    for low, (src, mod) in mod_files.items():
        try:
            with open(src, 'rb') as f:
                data = f.read()
        except OSError:
            out[low] = (src, mod)
            continue
        cache_key = f'TEXCAP-V1-{max_dim}-' + _sig(src)
        cached = os.path.join(
            TEXCAP_CACHE,
            f'{name}.{low}.{hashlib.sha1(cache_key.encode()).hexdigest()[:16]}')
        if os.path.exists(cached):
            out[low] = (cached, mod)
            capped += 1
            continue
        try:
            new, note = tex.cap_dimensions(data, max_dim)
        except Exception as exc:
            log(f'  ! texcap {low}: {exc}; using original')
            out[low] = (src, mod)
            continue
        if new is None:
            out[low] = (src, mod)
            continue
        with open(cached, 'wb') as f:
            f.write(new)
        out[low] = (cached, mod)
        capped += 1
        saved_bytes += len(data) - len(new)
        log(f'  texcap {low}: {note}')
    if capped:
        log(f'  {name}: {capped} texture(s) capped at {max_dim}px '
            f'(~{saved_bytes / 1_048_576:.1f} MB saved on disk; set '
            f'{FIELD_TEX_CAP_ENV}=0 to disable)')
    return out


# One .hrc bone line: "<count> RSD [RSD...]". Matched against a single
# already-split line, NOT with (?m) against the whole file -- `\s` matches
# newlines, so a multiline pattern happily swallows the blank line and the
# next bone's name and parent as if they were RSD names. That inflated
# BEEC.HRC from 22 parts to 64 and put bone names in the part list.
def _provisional_target(c, route_target):
    """Where a candidate goes on name-match-first rules (the old behaviour)."""
    return c['direct'] or route_target.get(c['route'])


def _reroute_by_folder(candidates, route_target, route_pure=None):
    """
    Make a model's parts follow the model, when they were split across
    archives by a name collision.

    The bug this fixes: NinoStyle ships the world-map chocobo as
    fb/world/aja.hrc plus its texture fb/world/CBHA.TEX. `aja.hrc` is a
    world_us.lgp entry so the model routed correctly -- but `cbha.tex` also
    exists in vanilla CHAR.LGP, and an exact name match beat the folder
    outright, so the texture was filed into char.lgp while the model that
    samples it went to world_us.lgp. Vanilla's world chocobo is untextured,
    so nothing in world_us.lgp supplied it and the model referenced a texture
    its own archive did not contain.

    WHY NOT "A DEDICATED FOLDER ALWAYS WINS"
    ---------------------------------------
    That was the first attempt and it is wrong. Measured against the real
    archives it moved 7 files, and 3 of them were damage: the folder
    "Chocobo - NinoStyle/World" contains mostly names that exist in char.lgp,
    so its majority vote is char.lgp, and the rule cheerfully dragged
    `aja.hrc` -- a world_us.lgp entry -- out of world_us.lgp. A folder's name
    and a folder's vote are both weaker evidence than what the model itself
    says.

    So the trigger is the reference, not the folder: a part is moved only when
    a MODEL IN THE SAME FOLDER names it, and that model resolved to a
    different archive. Two further conditions, both required:

      * the part must not already be going where the model went;
      * moving it must take nothing away from the archive it leaves. That
        holds in either of two ways:
          - the part was only going there by FOLDER INHERITANCE, never by a
            name match, so that archive was never going to have it anyway
            (`aqgc.tex` is in no vanilla archive at all); or
          - some OTHER candidate still supplies that name there --
            fb/char/CBHA.TEX covers char.lgp, which is why char.lgp keeps its
            own character texture and world_us.lgp gets the chocobo's.

    That second clause has to count candidates other than the one being moved.
    Counting the file itself let `aqgc.tex` "cover" its own departure, which
    is not a guarantee, it is a tautology.

    Returns [(rel, from_archive, to_archive, why)] for logging.
    """
    by_folder = {}
    for c in candidates:
        by_folder.setdefault(c['route'], {})[c['low']] = c
    cover_count = Counter()
    for c in candidates:
        t = _provisional_target(c, route_target)
        if t:
            cover_count[(t, c['low'])] += 1

    moved = []
    for route, files in by_folder.items():
        for low, hc in list(files.items()):
            if not low.endswith('.hrc'):
                continue
            model_target = _provisional_target(hc, route_target)
            if not model_target:
                continue
            for rsd in _hrc_parts(_read(hc['full'])):
                rc = files.get(rsd + '.rsd')
                if rc is None:
                    continue
                ply, texs = _rsd_refs(_read(rc['full']))
                parts = [rsd + '.rsd'] + ([ply + '.p'] if ply else []) \
                    + [t + '.tex' for t in texs]
                for key in parts:
                    pc = files.get(key)
                    if pc is None or pc.get('target'):
                        continue
                    was = _provisional_target(pc, route_target)
                    if not was or was == model_target:
                        continue
                    # Lossless either because it was never a name match there,
                    # or because someone ELSE still supplies that name there.
                    if was in pc['hits'] and cover_count[(was, key)] < 2:
                        continue
                    pc['direct'] = (model_target if model_target in pc['hits']
                                    else None)
                    pc['target'] = model_target
                    moved.append((pc['rel'], was, model_target, low))
    return moved


_RE_RSD_TOKEN = re.compile(rb'^(\d+)[ \t]+([A-Za-z0-9_]{1,8}(?:[ \t]+[A-Za-z0-9_]{1,8})*)$')
_RE_RSD_TEX = re.compile(rb'TEX\[\d+\]\s*=\s*([A-Za-z0-9_]+)\.\w+', re.I)
_RE_RSD_PLY = re.compile(rb'PLY\s*=\s*([A-Za-z0-9_]+)\.\w+', re.I)


def _read(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except OSError:
        return None


def _hrc_parts(blob):
    """RSD basenames referenced by an .hrc, in bone order."""
    if not blob or not blob.lstrip().startswith(b':HEADER_BLOCK'):
        return []
    out = []
    for line in blob.replace(b'\r\n', b'\n').split(b'\n'):
        m = _RE_RSD_TOKEN.match(line.strip())
        if not m or int(m.group(1)) < 1:
            continue
        out += [t.decode('ascii', 'replace').lower()
                for t in m.group(2).split()]
    return out


def _rsd_refs(blob):
    """(ply basename, [tex basenames]) declared by an .rsd."""
    if not blob or b'@RSD' not in blob[:16]:
        return None, []
    ply = _RE_RSD_PLY.search(blob)
    return (ply.group(1).decode('ascii', 'replace').lower() if ply else None,
            [t.decode('ascii', 'replace').lower()
             for t in _RE_RSD_TEX.findall(blob)])


def _model_graph(van):
    """
    {hrc entry name: {'geom': {rsd/p entry names}, 'tex': {tex entry names}}}
    for every vanilla model in this archive, plus a reverse index from part to
    the models that use it.
    """
    models, used_by = {}, {}
    for hrc in [k for k in van if k.endswith('.hrc')]:
        blob = _read(van[hrc])
        parts = _hrc_parts(blob)
        if not parts:
            continue
        geom, tex = set(), set()
        for rsd in parts:
            key = rsd + '.rsd'
            geom.add(key)
            ply, ts = _rsd_refs(_read(van[key]) if key in van else None)
            if ply:
                geom.add(ply + '.p')
            tex |= {t + '.tex' for t in ts}
        models[hrc] = {'geom': geom, 'tex': tex}
        for part in geom | tex:
            used_by.setdefault(part, set()).add(hrc)
    return models, used_by


def _drop_stray_model_parts(name, mod_files, van, log):
    """
    Remove mod GEOMETRY files that land on a model the mod does not otherwise
    replace.

    The failure this exists for: a mod ships thousands of files into a shared
    4-letter namespace, and one of them happens to carry the same name as a
    part of a vanilla model it never intended to touch. The archive router has
    no way to know -- `bfaa.p` is just a filename -- so the part goes in, and
    the result is one polygon set drawn against another model's material and
    bone. On the Sector 7 shop dog (BEEC.HRC, `dog2_sk`) that is 1 of 44 parts
    replaced and reads in-game as a flat gradient panel sticking out of its
    foot. `bfaa.p` is referenced by exactly one model in all of vanilla
    char.lgp, so nothing wanted it there.

    Rule, deliberately conservative -- three conditions, all required:

      1. the model's .hrc is NOT replaced by the mod. If it is, the mod owns
         the model and every part it ships for it is intentional;
      2. the mod supplies only a HANDFUL of the model's geometry parts --
         at most 2, or an eighth of the model, whichever is larger. A
         collision lands on one or two files; a mod genuinely reworking a
         model, even partially, ships dozens. "Fewer than half" was the first
         threshold tried and it discarded a legitimate 36-of-44-part rework,
         which is a far worse outcome than leaving one stray part in;
      3. the part is not used by any model the mod DOES own. Sixteen part
         files are shared between models in vanilla char.lgp, and dropping a
         shared part to protect an untouched model would break a replaced one.

    TEXTURES ARE NEVER DROPPED. Retexture-only mods are legitimate and common,
    and a texture cannot put geometry in the wrong place.

    Set SEVENTH_NX_KEEP_STRAY_PARTS=1 to disable and get the old behaviour.
    """
    if os.environ.get('SEVENTH_NX_KEEP_STRAY_PARTS') == '1':
        return mod_files
    models, used_by = _model_graph(van)
    if not models:
        return mod_files
    owned = {h for h in models if h in mod_files}
    protected = set()
    for h in owned:
        protected |= models[h]['geom']

    stray, by_model = {}, {}
    for hrc, m in models.items():
        if hrc in owned:
            continue
        have = [p for p in m['geom'] if p in mod_files]
        if not have or len(have) > max(2, len(m['geom']) // 8):
            continue
        for p in have:
            if p in protected:
                continue
            stray[p] = hrc
            by_model.setdefault(hrc, []).append(p)
    if not stray:
        return mod_files

    out = {k: v for k, v in mod_files.items() if k not in stray}
    log(f'  {name}: dropped {len(stray)} stray mod part(s) that landed on '
        f'{len(by_model)} model(s) the mod does not replace')
    log('      (a name collision puts one bone\'s geometry on another model; '
        'set SEVENTH_NX_KEEP_STRAY_PARTS=1 to keep them)')
    for hrc in sorted(by_model)[:12]:
        total = len(models[hrc]['geom'])
        log(f'      {hrc}: {", ".join(sorted(by_model[hrc]))} '
            f'({len(by_model[hrc])}/{total} part(s)) -> vanilla')
    if len(by_model) > 12:
        log(f'      ... and {len(by_model) - 12} more model(s)')
    return out


def _field_model_report(name, mod_files, van, log):
    """
    Report field models the mod only PARTLY replaces, and models whose
    texturing the mod changes.

    Why this exists
    ---------------
    A field model is not one file. `AAAA.HRC` names a bone tree; each bone
    names an `.RSD`; each `.RSD` names a `.PLY` (geometry) and zero or more
    `.TEX`. A mod that ships a new mesh for some bones and leaves the rest
    vanilla produces a chimera, and the visible result is a piece of geometry
    in the wrong place or sampling a texture that was never meant for it --
    "a stray panel sticking out of its foot" is the canonical shape of it.

    The sharpest case, and the reason for the second warning below: several
    vanilla field models are entirely UNTEXTURED (`NTEX=0` on every RSD --
    the Sector 7 shop dog, BEEC.HRC / skeleton `dog2_sk`, is one). If the mod
    supplies a textured RSD for one bone of such a model, that bone starts
    sampling a texture with UVs the rest of the model knows nothing about.
    Nothing downstream can detect that; only this comparison can.

    This is read-only. It changes no file and drops nothing -- it names the
    models to look at, the same way the battle report does. Turn it off with
    SEVENTH_NX_NO_MODEL_REPORT=1 if the output is noise for your mod set.
    """
    if os.environ.get('SEVENTH_NX_NO_MODEL_REPORT') == '1':
        return
    hrcs = [k for k in van if k.endswith('.hrc')]
    if not hrcs:
        return

    def van_blob(key):
        p = van.get(key)
        return _read(p) if p else None

    def cur_blob(key):
        hit = mod_files.get(key)
        return _read(hit[0]) if hit else van_blob(key)

    partial, retextured, missing_tex = [], [], []
    for hrc in sorted(hrcs):
        # Geometry and textures are counted separately. A model whose every
        # bone comes from the mod but whose shared texture sheet is still
        # vanilla is NOT half-replaced -- counting the sheet made 29 perfectly
        # healthy models report as "48/50 parts", which buries the one real
        # finding in noise.
        # Walk the bone list of the .hrc that will actually SHIP, not
        # vanilla's. NinoStyle re-authors several models with a different
        # skeleton layout -- fb/char's ahdf.hrc names abjc/abje/acaa where
        # vanilla names ahea/ahec/ahfa -- so measuring a replaced model
        # against vanilla's part names finds none of them and reports a
        # complete, self-consistent model as "1/35 parts from the mod". That
        # false positive accounted for most of the 34 models this listed.
        geom, tex_mod, tex_van = [hrc], set(), set()
        for rsd in _hrc_parts(cur_blob(hrc)):
            key = rsd + '.rsd'
            geom.append(key)
            _p_v, tv = _rsd_refs(van_blob(key))
            ply_m, tm = _rsd_refs(cur_blob(key))
            tex_van |= set(tv)
            tex_mod |= set(tm)
            if ply_m:
                geom.append(ply_m + '.p')
        if len(geom) < 2:
            continue
        parts = geom + [t + '.tex' for t in tex_mod]
        touched = [p for p in geom if p in mod_files]
        if touched and len(touched) < len(geom):
            partial.append((hrc, len(touched), len(geom)))
        if touched and not tex_van and tex_mod:
            retextured.append((hrc, sorted(tex_mod)[:3]))
        for t in sorted(tex_mod):
            key = t + '.tex'
            if key not in mod_files and key not in van:
                missing_tex.append((hrc, key))

    if partial:
        log(f'  {name}: {len(partial)} model(s) only partly replaced by the '
            'mod (vanilla and mod parts mixed):')
        for hrc, got, tot in partial[:12]:
            log(f'      {hrc}: {got}/{tot} part(s) from the mod')
        if len(partial) > 12:
            log(f'      ... and {len(partial) - 12} more')
    if retextured:
        log(f'  {name}: {len(retextured)} model(s) are UNTEXTURED in vanilla '
            'but textured by the mod --')
        log('      if one of these shows a stray textured panel, this is why:')
        for hrc, sample in retextured:
            log(f'      {hrc}: now references {", ".join(sample)}')
    if missing_tex:
        log(f'  {name}: {len(missing_tex)} texture(s) referenced by a mod RSD '
            'are in neither archive:')
        for hrc, key in missing_tex[:12]:
            log(f'      {hrc} -> {key}  (will sample garbage)')
    if not (partial or retextured or missing_tex):
        log(f'  {name}: model consistency ok (no partly-replaced or newly '
            'textured models)')


def _lookup_cell(filename):
    """
    FF7's lookup-table cell for an LGP entry name, 0..899.

    Reproduces `char_to_lookup_value` from PyFF7 (itself taken from the
    reference lgp.c), which is VERIFIED against Square's own tables: it
    regenerates the stored 3600-byte table byte-exactly for vanilla char.lgp,
    battle.lgp, magic.lgp, flevel.lgp, world_us.lgp and disc_us.lgp.

    The collisions are real and deliberate: digits share values with 'a'-'j',
    '_' with 'k', '-' with 'l', and a '.' second character folds an entry into
    its first character's "no second character" cell.
    """
    def value(c):
        if c == '.':
            return -1
        if c == '_':
            return 10
        if c == '-':
            return 11
        if c.isdigit():
            return ord(c) - ord('0')
        if c.isalpha():
            return ord(c.lower()) - ord('a')
        raise ValueError(f'invalid LGP filename character {c!r}')
    base = filename.split('/')[-1]
    return value(base[0]) * 30 + (value(base[1]) if len(base) > 1 else -1) + 1


def _lookup_unreachable(path):
    """
    Entry names present in an LGP's TOC that the GAME cannot find.

    Walks the archive the way the engine does -- name -> cell -> (first index,
    count) run -> linear scan of that run -- and returns every name the scan
    misses. Returns [] for a healthy archive; every vanilla archive scores 0.
    """
    with open(path, 'rb') as f:
        data = f.read()
    count = struct.unpack('<i', data[12:16])[0]
    off = lgp.CREATOR_LEN + 4
    names = []
    for _ in range(count):
        names.append(data[off:off + 20].split(b'\0')[0]
                     .decode('latin1').lower())
        off += lgp.TOC_ENTRY_LEN
    table = [struct.unpack('<HH', data[off + i * 4:off + i * 4 + 4])
             for i in range(900)]
    bad = []
    for nm in names:
        try:
            cell = _lookup_cell(nm)
        except (ValueError, IndexError):
            bad.append(nm)
            continue
        if not 0 <= cell < 900:
            bad.append(nm)
            continue
        start, n = table[cell]
        if n == 0 or not 0 < start <= len(names):
            bad.append(nm)
            continue
        if nm not in names[start - 1:start - 1 + n]:
            bad.append(nm)
    return bad


def _build_model_archive(name, archive_path, mod_files, romfs, pack_lgp,
                         log, folder_of=None, battle_bg_native_names=None):
    """
    Rebuild a model LGP (char/battle/magic/world/menu) with PyFF7: reuse
    every untouched vanilla entry, overlay the mod's files unchanged, add any
    new entries, and let pack_lgp regenerate the tables. Returns the output
    path, or None on failure.

    Mod file *contents* are used exactly as shipped. Only the LGP entry NAME
    is lowercased (to match the Switch's lowercase archive). The internal
    references inside .hrc/.rsd files are left in their original (upper)case
    -- a known-good manual build keeps them uppercase and renders correctly,
    and lowercasing them makes models render blank.

    Archives with DUPLICATE entry names (magic.lgp: 5252 entries, 3454
    unique -- files in internal directories resolved via the conflict
    table) cannot go through unpack+repack: extracting by name collapses
    duplicates and silently drops entries (1798 in magic.lgp!), scrambling
    spell effects (the "Bolt looks sideways" bug). Those archives are
    rebuilt IN-PLACE instead, preserving every entry and the conflict
    table verbatim.
    """
    with open(archive_path, 'rb') as f:
        f.read(12)
        count = struct.unpack('<i', f.read(4))[0]
        toc_names = [f.read(27)[:20].split(b'\0')[0].decode('ascii', 'replace').lower()
                     for _ in range(count)]
    if len(toc_names) != len(set(toc_names)):
        return _build_inplace_archive(name, archive_path, mod_files, romfs,
                                      log, folder_of, battle_bg_native_names)

    van = vanilla_unpack(name, archive_path, log)
    filemap = dict(van)  # lowercase entry name -> disk path (unchanged bytes)

    # The Switch battle module needs paletted textures (the enemy death
    # dissolve and some magic effects are palette-driven; truecolor mod
    # textures vanish instead of fading, or render white). Convert them.
    # The field/world modules are fine with truecolor -- proven on
    # hardware -- so only battle.lgp and magic.lgp are converted.
    if name == 'battle.lgp':
        mod_files = _apply_battle_experiments(mod_files, folder_of, log)
        mod_files = _battle_enemy_report(mod_files, van, folder_of, log)
        if os.environ.get('SEVENTH_NX_NO_VANILLA_HUNDREDS') != '1':
            mod_files = _transplant_render_state(mod_files, van, folder_of,
                                                 log)
    if (name in ('battle.lgp', 'magic.lgp')
            and os.environ.get('SEVENTH_NX_NO_TEXCONV') != '1'):
        mod_files = _convert_battle_textures(
            name, mod_files, van, log, folder_of,
            battle_bg_native_names=battle_bg_native_names)

    # Opt-in only (see FIELD_TEX_CAP_ENV above) -- disabled by default, so
    # a build with nothing set behaves exactly as before this was added.
    if name in ('char.lgp', 'world_us.lgp'):
        mod_files = _drop_stray_model_parts(name, mod_files, van, log)
        _field_model_report(name, mod_files, van, log)
        cap = _field_tex_cap()
        if cap:
            mod_files = _cap_field_textures(name, mod_files, log, cap)

    # Every model archive, and AFTER both the battle conversion and the field
    # cap so neither can undo it. Idempotent on anything already de-fringed.
    mod_files = _debleed_textures(name, mod_files, van, log)

    # Does this mod actually change the archive? Track new entries and whether
    # any replacement differs from vanilla. If nothing changes, we skip the
    # whole archive rather than writing a needless copy of vanilla.
    added = really_changed = 0
    for low, (src, _) in mod_files.items():
        if low not in filemap:
            added += 1
            really_changed += 1
        else:
            try:
                van_path = filemap[low]
                if os.path.getsize(src) != os.path.getsize(van_path):
                    really_changed += 1
                elif open(src, 'rb').read() != open(van_path, 'rb').read():
                    really_changed += 1
            except OSError:
                really_changed += 1
        filemap[low] = src  # reference the mod file as-is

    if really_changed == 0:
        log(f'  {name}: mod changes nothing here, not writing an archive')
        return None

    log(f'  {len(mod_files)} files applied ({added} new, '
        f'{really_changed - added} genuinely replaced, '
        f'{len(mod_files) - really_changed} identical to vanilla); '
        f'{len(filemap)} total entries')

    dest = os.path.join(romfs, ARCHIVES[name])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        os.remove(dest)
    # Sort entries by LOOKUP CELL first, then by lowercased name.
    #
    # THIS ORDER IS NOT COSMETIC AND A PLAIN NAME SORT CORRUPTS THE ARCHIVE.
    #
    # An LGP's 3600-byte lookup table stores, per (first char, second char)
    # cell, a (first TOC index, count) PAIR -- a RUN. Every entry that maps to
    # a cell must therefore be CONTIGUOUS in the TOC, or the run covers the
    # wrong entries and the game cannot find the ones that fell outside it.
    #
    # FF7's char_to_lookup_value maps '0'-'9' to 0-9 -- the SAME values as
    # 'a'-'j' (and '_' to 10 = 'k', '-' to 11 = 'l'). So `h3.tex` shares a cell
    # with every `hd*` name. Sorted by name, `h3` lands before `haaa` while
    # `hd*` lands after `hc*`: not contiguous. pack_lgp then writes
    # (start = h3.tex, count = 62), a run of ha*/hb*/hc* files, and ALL 61
    # `hd*` entries become unreachable by the game's own lookup.
    #
    # MEASURED, build 79: Ninostyle ships `h3.tex` and `p3.tex`. `hd*` held
    # `hdaf.a` -- Yuffie's field animation -- and 22 fields load it, including
    # las4_1, las4_0 and las4_4, the bottom of the Northern Cave. Loading one
    # crashed in field_upd_single_model (x86 0x636C41). Deleting char.lgp
    # "fixed" it because the base game's archive has a correct table.
    # See FINDINGS-183.
    #
    # For names whose first two characters are both letters, the cell index is
    # monotonic in those characters, so this key is IDENTICAL to a name sort.
    # Only names with a digit/underscore/period in the first two characters
    # move -- battle.lgp, magic.lgp and world_us.lgp come out byte-identical.
    files = sorted(filemap.items(),
                   key=lambda kv: (_lookup_cell(kv[0]), kv[0]))
    pack_lgp(files, dest)

    # Verify: reopen and confirm every entry we asked for is present.
    chk = lgp.Archive(dest)
    missing = [low for low in mod_files if low not in chk.index]
    if missing:
        os.remove(dest)
        log(f'  ! {name}: {len(missing)} entries missing after pack; '
            'output rejected (please report)')
        return None

    # Verify HARDER: being present in the TOC is not enough. Simulate the
    # game's own lookup -- cell -> run -> linear scan -- for every entry. This
    # is the gate that would have caught the h3.tex collision in seconds.
    unreachable = _lookup_unreachable(dest)
    if unreachable:
        os.remove(dest)
        log(f'  ! {name}: {len(unreachable)} entries are in the TOC but '
            f'UNREACHABLE through the lookup table '
            f'(e.g. {", ".join(unreachable[:5])}); output rejected. '
            'The TOC ordering and the lookup table disagree -- see the '
            'sort key above.')
        return None
    out_size = os.path.getsize(dest)
    van_size = os.path.getsize(archive_path)
    log(f'  wrote {name} ({out_size:,} bytes, {len(chk.entries)} entries)')
    if van_size and out_size > van_size * 1.5:
        log(f'  note: {name} grew to {out_size / van_size:.1f}x its original '
            f'size ({van_size:,} -> {out_size:,}). One of the enabled options '
            'is adding a lot of data; if the game behaves oddly, that option '
            'is the first thing to turn off.')
    return dest


# Sections a PC mod field may safely contribute when replacing a Switch
# field. Everything else (script, camera, models, walkmesh, tile setup,
# encounters, triggers) stays Switch-vanilla, because those encode IDs and
# offsets that diverge between the PC mod baseline and the Switch build.
# Widen this set only after a hardware test proves a section safe.
#   1 script/dialogue  2 camera    3 models     4 palette  5 walkmesh
#   6 tile setup       7 encounters 8 triggers  9 background
SAFE_MOD_SECTIONS = {4, 9}


def _is_lzs_wrapped(data):
    """True for FF7's size-prefixed LZS container (fields et al.)."""
    return (len(data) >= 4
            and struct.unpack('<I', data[:4])[0] == len(data) - 4)


def _try_sections(raw):
    """split_sections, or None if this is not a 9-section field file."""
    try:
        return lgp.split_sections(raw)
    except Exception:
        return None


def _tex_dims(data):
    """(width, height) if data looks like an FF7 TEX file, else None."""
    if len(data) < 0x70 or struct.unpack('<I', data[:4])[0] != 1:
        return None
    w, h = struct.unpack('<II', data[0x3C:0x44])
    if not (0 < w <= 4096 and 0 < h <= 4096):
        return None
    return w, h


def _build_inplace_archive(name, archive_path, mod_files, romfs, log,
                           folder_of=None, battle_bg_native_names=None):
    """
    Replace entry payloads inside the vanilla archive without repacking:
    every entry (including duplicate-named ones) and the lookup/conflict
    tables are preserved byte-for-byte; only the named entries' contents
    change. Mod names that already exist are replaced (all such names are
    unique in practice -- verified for cyvadat.* in magic.lgp); names the
    archive lacks cannot be added on this path and are skipped with a log.
    """
    log(f'  {name}: archive uses duplicate entry names (internal '
        'directories); rebuilding in-place to preserve all entries')
    # texture preprocessing still applies (battle-module-safe paletted)
    if (name in ('battle.lgp', 'magic.lgp')
            and os.environ.get('SEVENTH_NX_NO_TEXCONV') != '1'):
        van = vanilla_unpack(name, archive_path, log)
        mod_files = _convert_battle_textures(
            name, mod_files, van, log, folder_of,
            battle_bg_native_names=battle_bg_native_names)
    archive = lgp.Archive(archive_path)
    payloads = {}
    new_names = []
    changed = 0
    for low, (src, _) in mod_files.items():
        try:
            with open(src, 'rb') as f:
                data = f.read()
        except OSError:
            continue
        entry = archive.index.get(low)
        if entry is None:
            new_names.append(low)
            continue
        if entry['payload'] != data:
            changed += 1
        payloads[low] = data
    if new_names:
        log(f'  ! {name}: {len(new_names)} new entries cannot be added '
            f'in-place, skipped (e.g. {new_names[0]})')
    if changed == 0:
        log(f'  {name}: mod changes nothing here, not writing an archive')
        return None
    archive.replace(payloads)
    dest = os.path.join(romfs, ARCHIVES[name])
    archive.write(dest)
    chk = lgp.Archive(dest)
    if chk.middle != archive.middle or len(chk.entries) != len(archive.entries):
        os.remove(dest)
        log(f'  ! {name}: in-place rebuild failed verification; rejected')
        return None
    log(f'  {len(payloads)} entries replaced in place ({changed} changed); '
        f'wrote {name} ({os.path.getsize(dest):,} bytes, '
        f'{len(chk.entries)} entries)')
    return dest


WIDESCREEN_CACHE = os.path.join(HERE, 'cache', '_widescreen')


def _bake_widescreen_ranges(archive, payloads, widescreen, log):
    """
    Write FFNx's per-field camera ranges into flevel.lgp's own section 8.

    `field_clip_with_camera_range_float` (FFNx background.cpp:417) reads the
    range out of `field_triggers_header` and then REPLACES it with the one
    `config.toml` supplies. There is no runtime object to replace it with
    here, and building one would mean a 711-entry lookup table in a module
    with about 31 KB of usable cave space. Writing the config's numbers into
    the field data instead makes `field_triggers_header->camera_range`
    simply correct at load, and costs no cave space and no lookup.

    Runs LAST, after both mod replacement paths and after the background
    conversion, so it is the final word on section 8 and cannot be
    overwritten by a pass that rebuilt the field for another reason. It
    edits only the four `int16` at +0x0C of section 8's body; the section
    length does not change, so nothing else in the field moves.

    Only fields whose range the config actually CHANGES are re-encoded --
    on Cosmos Limit Break that is 41 of 711, because the config's main lever
    is the explicit `mode` key rather than the range (README-45 §8.2).

    Returns the stats dict, or None if nothing was configured.
    """
    if not widescreen:
        return None
    cfg_path, mov_path, mod_name = widescreen
    config, movie_config = ff7nx_ws.load(cfg_path, mov_path)
    if not config:
        log(f'  ! widescreen: {cfg_path} parsed to nothing; camera ranges '
            f'left alone')
        return None

    clamp = ff7nx_ws.wants_clamp()
    stats = ff7nx_ws.apply_to_flevel(
        archive, payloads, config, movie_config,
        encode=lambda raw: _encode_field_cached(archive, raw),
        clamp=clamp,
        # NOT next to build.py. `_archive_fingerprint` hashes every .py in
        # this folder, so a build that writes a .py here changes its own
        # cache key and every subsequent build rebuilds flevel.lgp from
        # scratch -- 130 MB and several minutes, forever.
        table_path=os.path.join(WIDESCREEN_CACHE, 'widescreen_fields.py'),
        log=log)

    log(f'  widescreen: {stats.get("total", 0)} field(s), '
        f'{stats.get("wide", 0)} would be wide '
        f'({100.0 * stats.get("wide", 0) / max(1, stats.get("total", 1)):.1f}%'
        f'), {stats.get("gated_in", 0)} of them without the config at all')
    by_mode = stats.get('by_mode') or {}
    if by_mode:
        log('    modes: ' + ', '.join(f'{k} {v}'
                                      for k, v in sorted(by_mode.items())))
    log(f'    camera ranges written: {stats.get("written", 0)}'
        f' (config {stats.get("from_config", 0)}'
        + (f', clamp {stats.get("from_clamp", 0)}' if clamp else '')
        + ')')

    # Say what the config asks for that a range edit cannot express, rather
    # than approximating it. h_offset/v_offset shift the camera POINT before
    # the clamp; moving the range moves the bounds, which is a different
    # thing, and pretending otherwise would put the camera slightly wrong on
    # every field that uses them.
    # THE PER-FIELD VERTICAL-CLIP FLAG.
    #
    # Here rather than in `ff7nx_ws` because it is not a camera RANGE -- it is
    # the gate on `ff7nx_camclamp`'s vertical leg, and one owner per fact. It
    # runs on the same `payloads` dict immediately after, so a field whose
    # range was just rewritten is decoded once more and the flag set on the
    # already-staged bytes rather than on the archive's original.
    #
    # Default OFF everywhere, which is FFNx's default and vanilla's behaviour.
    # If this pass is skipped or fails, the vertical clamp simply never fires
    # -- the Sector 8 band comes back and no camera is ever frozen. That is
    # the safe direction, and it is the opposite of what the ungated clamp
    # did.
    try:
        vstats = ff7nx_vclip.apply_archive(
            archive, payloads, lgp, config,
            encode=lambda raw: _encode_field_cached(archive, raw),
            log=log)
        stats['vclip'] = vstats
    except Exception as exc:                                   # noqa: BLE001
        log(f'  ! vertical clip: pass skipped ({exc}); the scripted camera '
            f'will not be clamped vertically on any field')

    gap = ff7nx_ws.config_report(config)
    if gap['point_shift']:
        log(f'    note: {len(gap["point_shift"])} field(s) also ask for '
            f'h_offset/v_offset/reset_vertical_pos. Those shift the camera '
            f'point, not its bounds, so they are NOT baked -- they need the '
            f'framing stage.')
    if gap['unknown_keys']:
        log(f'    note: config keys FFNx does not read: '
            f'{", ".join(gap["unknown_keys"])}')

    return stats


def _build_flevel(archive_path, chunks, field_files, romfs, log,
                  dds_sources=(), widescreen=None):
    """
    Patch flevel.lgp. Three replacement shapes, decided per entry by
    comparing the mod file with how the VANILLA entry is stored:

    - vanilla entry is raw (eye textures, maplist...): store the mod file
      raw. Wrapping these in LZS (an earlier bug) crashes the game the
      moment the texture is used -- e.g. chibi eye textures crashing when
      characters come into view.
    - vanilla entry is an LZS-wrapped FIELD and the mod file is a field:
      selective section splice -- take only SAFE_MOD_SECTIONS from the mod,
      keep the risky sections Switch-vanilla, re-wrap.
    - vanilla entry is LZS-wrapped but not a field (tut files...): re-wrap
      the mod data.

    Plus the existing explicit .chunk.<n> section patches.
    """
    archive = lgp.Archive(archive_path)
    van_size = os.path.getsize(archive_path)
    payloads = {}

    cap = _field_bg_cap()
    if cap and chunks:
        chunks = _cap_field_backgrounds(chunks, log, cap)

    if chunks:
        chunks = _hold_back_heap_tight(chunks, log)

    for name, (src, _) in field_files.items():
        entry = archive.index.get(name)
        if entry is None:
            log(f'  ! flevel: no such entry {name}, skipped')
            continue
        with open(src, 'rb') as f:
            data = f.read()
        # Normalize the mod file to raw bytes whether or not it shipped
        # wrapped, so storage is decided solely by the vanilla entry.
        raw_mod = lgp.lzs_decompress(data[4:]) if _is_lzs_wrapped(data) \
            else data

        if not _is_lzs_wrapped(entry['payload']):
            # Vanilla stores this entry raw -> store the mod file raw.
            cap = _field_tex_cap()
            if cap:
                try:
                    capped, cap_note = tex.cap_dimensions(raw_mod, cap)
                except Exception:
                    capped, cap_note = None, None
                if capped is not None:
                    raw_mod = capped
                    log(f'  {name}: texcap {cap_note}')
            payloads[name] = raw_mod
            dims_v = _tex_dims(entry['payload'])
            dims_m = _tex_dims(raw_mod)
            note = ''
            if dims_v and dims_m and dims_m != dims_v:
                note = (f' [TEX {dims_v[0]}x{dims_v[1]} -> '
                        f'{dims_m[0]}x{dims_m[1]}'
                        + (' NON-POWER-OF-TWO' if
                           (dims_m[0] & (dims_m[0] - 1)) or
                           (dims_m[1] & (dims_m[1] - 1)) else '')
                        + ' -- if this build crashes, this upscaled texture'
                          ' is the first suspect]')
            log(f'  {name}: stored raw ({len(raw_mod):,} bytes){note}')
            continue

        van_raw = archive.decompressed(entry)
        van_secs = _try_sections(van_raw)
        mod_secs = _try_sections(raw_mod)
        if van_secs is None or mod_secs is None:
            # LZS-wrapped but not a 9-section field: plain re-wrap.
            payloads[name] = _encode_field_cached(archive, raw_mod)
            log(f'  {name}: re-wrapped ({len(raw_mod):,} bytes)')
            continue

        # Field vs field: splice only the safe sections.
        took, held = [], []
        for i in range(lgp.N_SECTIONS):
            if mod_secs[i] == van_secs[i]:
                continue
            if (i + 1) in SAFE_MOD_SECTIONS:
                van_secs[i] = mod_secs[i]
                took.append(i + 1)
            else:
                held.append(i + 1)
        if not took:
            log(f'  {name}: field skipped -- it only changes unsafe '
                f'sections {held} (kept Switch vanilla)')
            continue
        payloads[name] = _encode_field_cached(
            archive, lgp.join_sections(van_secs))
        msg = f'  {name}: spliced sections {took} from mod'
        if held:
            msg += f', kept Switch-vanilla sections {held}'
        log(msg)

    # Fields whose recomposed bytes match vanilla exactly are LEFT ALONE
    # rather than re-encoded. A section-9 mod is not obliged to change every
    # field it ships -- Cosmos Limit Break's `ancnt1.chunk.9` is byte-for-byte
    # the vanilla section -- and re-encoding those would swap a correct
    # vanilla payload for a differently-compressed one, spend compressor time
    # on it, and inflate the archive for no change on screen.
    identical = 0
    patched = 0
    for n_done, (field, sections) in enumerate(sorted(chunks.items())):
        entry = archive.index.get(field)
        if entry is None or not archive.is_field(entry):
            log(f'  ! no such field: {field}')
            continue
        parts = lgp.split_sections(archive.decompressed(entry))
        # Compared against the RECOMPOSED vanilla field, not the decompressed
        # one. Vanilla fields carry ~14 bytes past the last section that
        # join_sections does not reproduce (it lays the sections out
        # contiguously from the header it writes), so comparing with the
        # original bytes finds a difference in every single field and the
        # test never fires.
        van_raw = lgp.join_sections(parts)
        for idx, (src, _) in sorted(sections.items()):
            if not 1 <= idx <= 9:
                continue
            with open(src, 'rb') as f:
                parts[idx - 1] = f.read()
        raw = lgp.join_sections(parts)
        if raw == van_raw:
            identical += 1
            continue
        payloads[field] = _encode_field_cached(archive, raw)
        patched += 1
        if len(chunks) > 100 and (n_done + 1) % 100 == 0:
            log(f'    ... {n_done + 1}/{len(chunks)} fields')
    if chunks:
        log(f'  {patched} fields patched'
            + (f', {identical} identical to vanilla and left untouched'
               if identical else ''))

    # LAST, and over every field, not just the modded ones -- see the
    # docstring. Must come after both replacement paths have decided their
    # payloads, or a mod's section 9 would be converted and then overwritten
    # with an unconverted one.
    # BEFORE the repack, and that ordering is the whole correctness argument.
    #
    # Cosmos names its art `<field>_<page>_<palette>.dds` against the VANILLA
    # page numbering. `_convert_field_backgrounds` renumbers and COMPACTS -- a
    # real build logs "661 page(s) freed across 317 field(s), 279,534 cell(s)
    # relocated". MEASURED after that pass:
    #
    #     mds6_2  dump slots [0,1,2,3,4]  ->  built slots [2,3,4,26,27,28]
    #             pages with identical content: NONE
    #
    # So writing Cosmos's page-0 art into the BUILT archive's slot 0 lands it
    # on cells that now hold something else. It renders as garbage -- bright
    # yellow blocks where the quantiser matched a light palette entry. And
    # `bwhlin` kept 4 of its 6 pages, so it looked perfect, which is exactly
    # how the error survived being tested on one field.
    #
    # Running first, the page numbering still matches the mod's, and the
    # repack promotes and compacts on top of correct data. A page it later
    # promotes to truecolor takes the DDS at full depth anyway.
    _PAGES_BEFORE_MARGIN.clear()
    _PAGES_BEFORE_MARGIN.update(_snapshot_page_counts(archive, payloads))
    # SAY IT OUT LOUD. The first version of the no-growth loop used `parts[8]`
    # as its baseline and was a silent no-op in the fields being tested -- the
    # log said `266 field(s) RE-RUN` and `0 GREW` while mds5_1 stayed one page
    # over what the mod ships. There is no way to tell those two states apart
    # from the log, so print the baseline itself.
    log('  field background: no-growth baseline snapshotted for %d field(s) '
        'BEFORE the margin passes (the mod\'s own page count -- not vanilla, '
        'which is too strict, and not the post-marginpage section, which is '
        'too lax)' % len(_PAGES_BEFORE_MARGIN))
    # FINDINGS-158. BEFORE the margin passes, and both halves matter:
    # marginart SKIPS a cell whose palette byte is >= npg, so a tile naming a
    # palette that does not exist never receives Cosmos's art -- and on this
    # port the palette IS applied, so the lookup runs off the end of section
    # 3's palette array. That is the mds5_3 white speckle and the mds5_5
    # black blobs. Every offline renderer we own decodes with `pal %% npg`,
    # so this is invisible until it reaches hardware.
    _pr_art = None
    _bc_art = None      # the DDS provider ff7nx_blackcell needs, set below
    if dds_sources:
        try:
            _pr_art = field_bg_repack.ArtProvider(
                dds_sources, ff7nx_fieldbg.page_px(), lambda *_a: None)
        except Exception:                                      # noqa: BLE001
            _pr_art = None
    _pr = ff7nx_palrange.apply_to_flevel(
        archive, payloads,
        encode=lambda raw: _encode_field_cached(archive, raw), log=log,
        art=_pr_art)
    log(ff7nx_palrange.summarise(_pr['tiles'], _pr['cells'], _pr['fields'],
                                 _pr['pals']))
    if _pr['refused']:
        log('  ! palette range: %d field(s) not changed (%s)'
            % (len(_pr['refused']),
               ', '.join('%s: %s' % r for r in _pr['refused'][:2])))
    if dds_sources and ff7nx_marginart.enabled():
        _art = field_bg_repack.ArtProvider(
            dds_sources, ff7nx_fieldbg.page_px(), log)
        if _art:
            _ma_scope = ff7nx_marginart.scope()
            log('  margin art scope: %s' % (
                'MARGIN + INTERIOR -- Cosmos art replaces vanilla inside the '
                '4:3 picture too, on layer 1' if _ma_scope == 'all'
                else 'MARGIN ONLY -- the 4:3 picture is not touched'))
            _bc_art = ff7nx_marginart.provider_source(_art)
            # ---- ARM THE 512px SHADOW. BUILD 109, HANDOFF-224.
            #
            # HERE and not earlier, because this is the first pass that has
            # Cosmos's art in hand, and not later, because it is the ONLY
            # pass that has it while the page numbering still matches the
            # names Cosmos gave its DDS files (see this call's ordering note
            # below, and FINDINGS-197).
            #
            # `arm` disarms itself unless the depth-1 lift is going to run at
            # exactly 2x, so under a 256px build every `SH.record` call
            # inside `fill_field` returns 0 and the build is bit-for-bit
            # build 107. It does NOT touch `field_bg_native.D1_PAGE_PX` --
            # see HANDOFF-224 s0.10; the whole point of the shadow is that
            # the pipeline stays in 256-unit coordinates until the lift.
            if field_bg_shadow.arm(FIELD_BG_D1_TARGET_PX,
                                   field_bg_native.VANILLA_PX):
                log('  depth-1 ART: recording Cosmos\'s 512px art alongside '
                    'the 256px pages, for the lift to write instead of '
                    'replicating. A page with no recording keeps build '
                    '108\'s 2x replication.')
            ma_stats = ff7nx_marginart.apply_to_flevel(
                archive, payloads, ff7nx_marginart.provider_source(_art),
                encode=lambda raw: _encode_field_cached(archive, raw), log=log,
                scope=_ma_scope)
            ma_line = ff7nx_marginart.summarise(ma_stats)
            if ma_line:
                log('  ' + ma_line)
            # Reported separately from the fill because it is a separate
            # decision with its own failure mode: the fill can write every
            # cell it is asked to and STILL produce a flat block if the
            # palette it quantised against cannot hold the art. HANDOFF-81.
            mpal_line = ff7nx_marginpal.summarise(ma_stats.get('pal'))
            if mpal_line:
                log('  ' + mpal_line)

            if not ff7nx_blackcell.disabled():
                # `overlay=True` ONLY HERE, and the ordering is the reason.
                # The overlay-margin fill reads Cosmos's art by PAGE NUMBER,
                # and Cosmos names its DDS against the page the cell is on
                # now. The second call site below runs AFTER the repack, the
                # compactor and the page cap have renumbered the pages, so the
                # same lookup would return a different page's picture there.
                # See ff7nx_blackcell.overlay_cells and FINDINGS-197.
                bc0 = ff7nx_blackcell.apply_to_flevel(
                    archive, payloads, _bc_art,
                    encode=lambda raw: _encode_field_cached(archive, raw),
                    log=log, overlay=True)
                bc0_line = ff7nx_blackcell.summarise(bc0)
                if bc0_line:
                    log(bc0_line)

            # AFTER the fill and BEFORE the repack, and both halves of that
            # matter.
            #
            # AFTER, because Cosmos names its art `<field>_<page>_<pal>.dds`
            # against the page the cell is on NOW. Move the cell first and the
            # lookup misses -- measured: the split ran, the pages came out
            # palette-pure, and every moved cell stayed flat filler.
            #
            # BEFORE, because the repack renumbers and compacts, and a page
            # this pass created has to be visible to that accounting like any
            # other.
            mp_stats = ff7nx_marginpage.apply_to_flevel(
                archive, payloads,
                encode=lambda raw: _encode_field_cached(archive, raw), log=log)
            mp_line = ff7nx_marginpage.summarise(mp_stats)
            if mp_line:
                log('  ' + mp_line)
            # (the note below is kept for the record)
            # THE MARGIN PAGE SPLIT WAS BRIEFLY REMOVED.
            #
            # It existed for one reason: a depth-1 page is drawn through ONE
            # palette, so a margin sharing a page with tiles of another
            # palette came out through the wrong colour table -- the Sector 6
            # yellow. The dense repack bakes every cell with the palette it
            # names, so there is no page left that can be drawn through a
            # foreign table and nothing for the split to fix. Removing it also
            # removes the page growth it cost and the compaction freeze that
            # protected it.
            # AFTER the split, so it sees the palette page each margin tile
            # finally names.
            #
            # The console DRAWS palette index 0 instead of discarding it.
            # PROVED on hardware 2026-08-05: the first version of this pass
            # wrote BLACK at index 0 and the same build both removed the
            # Sector 6 yellow (mds6_2/mds6_3 store PURE YELLOW there) and put
            # black speckles across Wall Market's interior. One change, both
            # effects -- so the key is drawn, and black is merely less wrong
            # than yellow. The pass now DE-FRINGES: entry 0 takes the mean
            # colour of the art beside the index-0 pixels, the same treatment
            # `_debleed_textures` already gives char.lgp and battle.lgp.
            pk_stats = ff7nx_palkey.apply_to_flevel(
                archive, payloads,
                encode=lambda raw: _encode_field_cached(archive, raw), log=log)
            pk_line = ff7nx_palkey.summarise(pk_stats)
            if pk_line:
                log('  ' + pk_line)

    if ff7nx_fieldbg.enabled():
        _convert_field_backgrounds(archive, payloads, log, dds_sources)

    # RUN TWICE, BEFORE AND AFTER THE REPACK, AND BOTH ARE NEEDED.
    #
    # BEFORE (in the margin block above): Cosmos names its art
    # `<field>_<page>_<pal>.dds` against the page the cell is on THEN, so that
    # is the only point at which the lookup resolves. It fixed 3312 tiles in
    # 77 fields on build 81.
    #
    # AFTER (here): the repack renumbers and promotes, and it CREATES empty
    # cells of its own. MEASURED on build 81's shipped archive -- `trnad_3`
    # went from ONE black tile on depth-1 slot 0 to SEVEN on depth-2 slot 26,
    # and 1309 tiles survived a log line that said "3312 fixed". This second
    # pass catches the ones whose DDS still resolves against the new page
    # number: cosmo, fr_e and qc, 280 tiles, all depth-2.
    #
    # Neither call can regress anything -- both only ever write a cell that is
    # entirely empty, and both discard their own work on a field if the black
    # count does not fall. See FINDINGS-186.
    #
    # Layer 1 is not colour-keyed -- FFNx sets color_key only for type 2
    # (ff7/field/field.cpp:56) -- so a layer-1 tile sampling an all-zero cell
    # is drawn as a BLACK SQUARE. `ff7nx_marginart` is RIGHT to refuse those
    # cells: a depth-1 page is one index array and the cell is shared with
    # layer-2+ tiles at other palettes.
    #
    # This pass ran BEFORE the repack in build 81 and the repack undid it.
    # MEASURED: `trnad_3` went from ONE black tile on depth-1 slot 0 to SEVEN
    # on depth-2 slot 26, because the repack promoted those cells onto a
    # truecolor page and left them empty there. The log said "3312 fixed" and
    # the shipped archive still had 1309. Running after the repack is the only
    # point at which the pages are final. See FINDINGS-186.
    if _bc_art is not None and not ff7nx_blackcell.disabled():
        bc_stats = ff7nx_blackcell.apply_to_flevel(
            archive, payloads, _bc_art,
            encode=lambda raw: _encode_field_cached(archive, raw), log=log)
        bc_line = ff7nx_blackcell.summarise(bc_stats)
        if bc_line:
            log(bc_line)

    # AFTER the background conversion, and that ordering is not cosmetic: the
    # repack builds one truecolor texture per (page, cell, PALETTE) actually
    # referenced, so a palette page introduced before it runs would be one the
    # mod has no image for, the nearest would be borrowed for it, and the
    # filler would come back in its original colour. See ff7nx_marginblack.py,
    # ORDERING. HANDOFF-65 §4.
    mb_stats = ff7nx_marginblack.apply_to_flevel(
        archive, payloads, encode=lambda raw: _encode_field_cached(archive, raw),
        log=log)
    mb_line = ff7nx_marginblack.summarise(mb_stats)
    if mb_line:
        log('  ' + mb_line)

    # ------------------------------------------ DEPTH-1 RESOLUTION LIFT
    # FINDINGS-223. DEAD LAST, and that is the design rather than a
    # convenience: every pass above -- marginart, both blackcells,
    # marginblack, the repack, the cap -- reads and writes paletted art in
    # 256-unit coordinates, and none of them has to learn about this. The
    # lift is a pure format change applied once the pages are final.
    #
    # It is also all-or-nothing per BUILD, not per field. Section 9 has no
    # per-page size field, so the engine infers a depth-1 page's dimension
    # from a patched constant; a single 256px page left behind would be read
    # as 512px and desynchronise the whole TEXTURE walk from that slot on.
    _lift_depth1_payloads(archive, payloads, log)

    # LAST of the section-9 passes, and deliberately so. FINDINGS-207.
    #
    # Layers 3 and 4 do not cull vertically (FINDINGS-205), so what puts a
    # parallax tile on screen is the WRAP, whose period is the trigger
    # header's bg3/bg4_height -- a field that reads 1024 in 55 of the 96
    # parallax layers and matches the art in only 39. Where it is wrong the
    # layer runs out at the top of the picture instead of repeating. This
    # copies rows at +/- the layer's own measured span, which is the repeat
    # the wrap would have made.
    #
    # WHY LAST: it only ever COPIES a tile record byte for byte, changing
    # `dst_y` and nothing else, so the copy names whatever page, uv, palette
    # and blend its source ended up with after every pass above. Running it
    # earlier would put tiles into the repack, the page cap and the no-growth
    # accounting that none of those passes have a reason to see.
    #
    # MEASURED before it was wired in, whole archive: 62 gapped parallax
    # layers -> 2, none made worse, 9,108 tiles added across 80 fields, and
    # the worst per-page IN-FRAME tile count -- FINDINGS-110's real cap --
    # grew on ZERO fields. It cannot, because only one copy of a repeated row
    # is inside a 240-unit window at a time.
    pf_stats = ff7nx_parallaxfill.apply_to_flevel(
        archive, payloads, encode=lambda raw: _encode_field_cached(archive, raw),
        log=log)
    pf_line = ff7nx_parallaxfill.summarise(pf_stats)
    if pf_line:
        log(pf_line)
    if pf_stats['refused']:
        log('  ! parallax fill: %d field(s) not changed (%s)'
            % (len(pf_stats['refused']),
               ', '.join('%s: %s' % r for r in pf_stats['refused'][:3])))

    # AFTER that, so the camera range is the last thing written into section
    # 8 and cannot be reverted by a field the background pass rebuilt. The
    # two passes touch different sections (8 vs 9) and both go through the
    # content-keyed encode cache, so ordering costs nothing but correctness.
    ws_stats = _bake_widescreen_ranges(archive, payloads, widescreen, log)

    try:
        archive.replace(payloads)
    except lgp.NewEntriesRequired as exc:
        log(f'  ! flevel: {len(exc.names)} new fields cannot be added')
        return None

    dest = os.path.join(romfs, ARCHIVES['flevel.lgp'])
    archive.write(dest)
    if lgp.Archive(dest).middle != archive.middle:
        os.remove(dest)
        log('  ! flevel: tables did not survive rebuild; rejected')
        return None

    # Read the camera ranges back OUT of the file that was just written and
    # compare them with the plan. This is the check that makes the whole
    # data half falsifiable without a console: the numbers in the archive
    # either are the config's or they are not. It is not fatal -- a wrong
    # camera range is a framing bug, not a crash -- but it must be loud,
    # because the failure mode it catches is silent on hardware.
    if ws_stats and ws_stats.get('plan'):
        ok, problems = ff7nx_ws.verify_flevel(
            dest, ws_stats['before'], ws_stats['plan'])
        if ok:
            log(f'  widescreen: verified -- all '
                f'{len(ws_stats["plan"])} camera range(s) are in the '
                f'rebuilt archive and nothing else moved')
        else:
            log(f'  ! widescreen: VERIFY FAILED ({len(problems)} problem(s))'
                f' -- the archive is still valid, its camera ranges are not')
            for p in problems[:10]:
                log(f'      {p}')

    out_size = os.path.getsize(dest)
    log(f'  wrote flevel.lgp ({out_size:,} bytes, '
        f'{out_size / van_size:.2f}x vanilla)')
    if van_size and out_size > van_size * 1.5:
        log(f'  note: flevel.lgp grew to {out_size / van_size:.1f}x its '
            f'original size ({van_size:,} -> {out_size:,}). Background '
            'sections are the only thing in here big enough to do that; '
            f'a lower field background page size '
            f'({ff7nx_fieldbg.PAGE_PX_ENV}) brings it back down.')
    return dest


MOVIE_CACHE_ENV = 'SEVENTH_NX_MOVIE_CACHE'
MOVIE_30FPS_ENV = 'SEVENTH_NX_MOVIE_30FPS'
ANALOG_360_ENV = 'SEVENTH_NX_ANALOG_360'
NO_AUTORUN_ENV = 'SEVENTH_NX_NO_AUTORUN'
NO_CHEATS_ENV = 'SEVENTH_NX_NO_CHEATS'
LIMITER_FPS_ENV = 'SEVENTH_NX_LIMITER_FPS'
SMOOTH_SCRIPTED_ENV = 'SEVENTH_NX_SMOOTH_SCRIPTED'


def movie_30fps():
    """Is 30 FPS FMV support on?"""
    return os.environ.get(MOVIE_30FPS_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def analog_360():
    """Is 360 degree field movement on?"""
    return os.environ.get(ANALOG_360_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def no_autorun():
    """Is the stick's tilt-to-run off?"""
    return os.environ.get(NO_AUTORUN_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def no_cheats():
    """Is the right stick click disabled?"""
    return os.environ.get(NO_CHEATS_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def smooth_scripted():
    """
    Smooth scripted/cutscene field movement -- PART OF THE 60 FPS SET, ON.

    This is not an option any more. It is not a separate feature either: the
    60 FPS set makes the field loop run twice as often, and scripted movement
    still advances once per PAIR of frames, so without this it visibly steps.
    Fixing that is part of the same job as making the field run at 60, so it
    ships with it.

    `SEVENTH_NX_SMOOTH_SCRIPTED=0` still turns it off. That exists to bisect, not to configure -- if a
    scene ever misbehaves, this is the first thing to switch off to find out
    whether it is implicated, and the answer is one build away instead of one
    code change away. Anything other than an explicit off value leaves it on.
    """
    return os.environ.get(SMOOTH_SCRIPTED_ENV, '').strip().lower() not in (
        '0', 'false', 'no', 'off')


def limiter_fps():
    """
    What the busy-wait frame limiters should aim for, or 0 to leave them at 60.

    See ff7nx_60fps.frame_pacing_note(). Aiming higher does not make the game
    run faster -- the display paces it at 60 -- it stops the limiter from
    being the last thing to happen before the vsync deadline.
    """
    v = os.environ.get(LIMITER_FPS_ENV, '').strip()
    try:
        return float(v) if v else 0.0
    except ValueError:
        return 0.0


def movie_quality():
    v = os.environ.get(movie_convert.QUALITY_ENV, '').strip().lower()
    return v if v in movie_convert.QUALITY_CRF \
        else movie_convert.QUALITY_DEFAULT


def movie_fit():
    """
    How converted FMVs are sized.

    'fit' resamples on the PC, with Lanczos, to the size the console really
    draws (movies.device_footprint -- 1440x1008 for the 1280x896 shape).
    'native' keeps whatever the mod ships, which is what every build before
    MOVIECONV-V2 did and is kept so the two can be A/B'd.
    """
    v = os.environ.get(movie_convert.FIT_ENV, '').strip().lower()
    return v if v in dict(movie_convert.FIT_CHOICES) \
        else movie_convert.FIT_DEFAULT


def movie_colour():
    """BT.709 normalisation on ('bt709') or off ('off')."""
    v = os.environ.get(movie_convert.COLOUR_ENV, '').strip().lower()
    return v if v in dict(movie_convert.COLOUR_CHOICES) \
        else movie_convert.COLOUR_DEFAULT


def _movie_cache_dir(sdout_root):
    """
    Where converted movies are kept between builds.

    Next to the app, NOT in the system temp directory. The first version put
    it under tempfile.gettempdir(), which on macOS and Linux is periodically
    swept and is cleared outright on reboot -- so "cached" meant "until you
    restart your machine", and a full FMV pack would silently re-encode from
    scratch. It lives beside cache/ and sdout/ now, so it survives exactly as
    long as the extracted mods do.
    """
    override = os.environ.get(MOVIE_CACHE_ENV)
    if override:
        return os.path.expanduser(override)
    # Beside sdout/, never INSIDE it: everything under sdout is copied to the
    # SD card, and sdout is the thing a user deletes to start clean.
    return os.path.join(os.path.dirname(os.path.abspath(sdout_root)),
                        'cache', 'movies')


def _link_or_copy(src, dest):
    """Hardlink the cached encode into place, falling back to a copy.

    A hardlink is instant and costs no extra disk; the copy is there for the
    case that matters in practice -- sdout on a different filesystem from the
    cache, e.g. written straight to an SD card."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        os.remove(dest)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


def _emplace_movies(plan, romfs, sdout, dump, log, progress, produced):
    """
    Re-encode every movie a mod supplied into the port's mp4 shape.

    Encoding is slow -- seconds per movie, and a full FMV pack is dozens --
    so results are cached on (source identity, target settings). The cache
    key includes the quality level, so changing it in the GUI re-encodes
    instead of handing back a file made at the old one.

    Frame rate IS a setting, and it is the only one that changes the game's
    timing rather than its picture: 30 fps with the movie caves, or the
    vanilla 15 fps with none of them. A mod's own rate is never carried
    through, because the caves are unconditional and a movie at a third rate
    would desync against both.
    """
    if not plan.movies and not movie_30fps():
        return
    quality = movie_quality()
    fit = movie_fit()
    colour = movie_colour()
    normalise = movie_30fps()
    if not movie_convert.have_ffmpeg():
        log(f'! {len(plan.movies)} movie(s) skipped: ffmpeg/ffprobe not found '
            'on PATH. Install ffmpeg and rebuild; nothing else is affected.')
        return

    # EVERY movies directory in the dump, not just data/movies.
    #
    # The port keeps localised movies under the language folders: the dump has
    # `data/lang-en/movies/ending2.mp4` and `joneva_e.mp4` alongside the 100+
    # in `data/movies`. Enumerating only the latter left those two at 15 fps
    # while the frame counter was being halved for everything, and while
    # `ending2.cam` -- the largest camera track in moviecam.lgp at 7980
    # records -- was stretched x2 for a movie that had not been. The ending
    # would have run its camera at half speed with its cues at double time.
    #
    # Keyed by (directory relative to workingdir, stem) so each localisation
    # is converted separately and goes back where it came from.
    vanilla_dirs = []
    if dump and dump.workingdir:
        for dirpath, _dirs, _files in os.walk(dump.workingdir):
            if os.path.basename(dirpath).lower() == 'movies':
                vanilla_dirs.append(
                    os.path.relpath(dirpath, dump.workingdir)
                    .replace(os.sep, '/'))
    vanilla_dirs.sort(key=lambda d: (d != movie_convert.MOVIE_DIR, d))
    vanilla_at = {}          # (reldir, stem) -> absolute path
    for d in vanilla_dirs:
        full = os.path.join(dump.workingdir, *d.split('/'))
        for fn in sorted(os.listdir(full)):
            stem, ext = os.path.splitext(fn.lower())
            if ext in movie_convert.MOVIE_EXT:
                vanilla_at[(d, stem)] = os.path.join(full, fn)
    vanilla_dir = (os.path.join(dump.workingdir, 'data', 'movies')
                   if dump and dump.workingdir else None)
    cache = _movie_cache_dir(sdout)
    os.makedirs(cache, exist_ok=True)
    # 30 FPS FMV support divides the game's movie frame counter
    # unconditionally, so EVERY movie the game can open has to be at the
    # doubled rate -- including the ones no mod replaced. Those are pulled
    # from the dump and frame-doubled, which costs almost nothing in H.264
    # and leaves the running time identical.
    #
    # A mod ships `ending2` by stem, the way the PC game stores it, so it has
    # to be aimed at the directory the vanilla copy of that stem lives in.
    # Where several languages carry the same stem the English one is used and
    # the rest keep their own text, the same rule the kernel files follow.
    def home_for(stem):
        here = [d for d in vanilla_dirs if (d, stem) in vanilla_at]
        if not here:
            return movie_convert.MOVIE_DIR
        if len(here) == 1:
            return here[0]
        eng = [d for d in here if 'lang-en' in d]
        return eng[0] if eng else here[0]

    work = {}
    for stem, (msrc, mmod) in plan.movies.items():
        work[(home_for(stem), stem)] = (msrc, mmod)
    borrowed = 0
    if normalise:
        if not vanilla_at:
            log('! 30 FPS FMV support needs the game\'s own movies, but no '
                'movies directory is in the dump. Skipped; movies left as '
                'shipped.')
            normalise = False
        else:
            for key, path in sorted(vanilla_at.items()):
                if key not in work:
                    work[key] = (path, None)
                    borrowed += 1

    label = next((lab for name, _crf, lab in movie_convert.QUALITY_LEVELS
                  if name == quality), quality)
    if normalise:
        log('movies: %d to build at %d fps (%d from the mod, %d of the '
            'game\'s own brought along so the frame counter stays correct)'
            % (len(work), movie_convert.NORMALISED_FPS,
               len(work) - borrowed, borrowed))
    else:
        log('movies: %d to convert down to the vanilla %g fps (no movie '
            'caves are applied at this setting, so every frame counter and '
            'field clock is stock)'
            % (len(work), movie_convert.VANILLA_MOVIE_FPS))
    log('        quality: %s, crf %d' % (label, movie_convert.crf_for(quality)))
    log('        sizing: %s   colour: %s'
        % (dict(movie_convert.FIT_CHOICES)[fit],
           dict(movie_convert.COLOUR_CHOICES)[colour]))
    if fit == 'fit':
        log('        the console draws a movie into at most %dx%d device '
            'pixels (measured, see movies.py); anything larger is minified '
            'by a single bilinear tap in video_p.glsl, so it is resampled '
            'here with Lanczos instead'
            % (movie_convert.TARGET_W, movie_convert.TARGET_H))
    if len(vanilla_dirs) > 1:
        log('        directories: %s' % ', '.join(vanilla_dirs))

    done = failed = cached = copied = 0
    resized = recoloured = 0
    oversize_left = []       # (stem, w, h) when fit is off but should not be
    undersize = []           # (stem, w, h) -- the shader has to magnify these
    spent = 0.0
    # OFF used to mean "whatever rate the mod ships", which made the
    # checkbox useless as a control: Cosmos FMV ships 30 fps, so unticking
    # left a 30 fps movie in data/movies with the movie-fps cave switched
    # OFF -- the frame counter then ran at twice the vanilla rate with
    # nothing correcting it, which is worse than either honest setting and
    # is why "test it with the box unticked" never produced a clean answer.
    # OFF now means the vanilla rate, so the two positions of one checkbox
    # are a genuine A/B: 15 fps with no caves, or 30 fps with them.
    target_fps = (movie_convert.NORMALISED_FPS if normalise
                  else movie_convert.VANILLA_MOVIE_FPS)
    for i, (reldir, stem) in enumerate(sorted(work)):
        src, _mod = work[(reldir, stem)]
        progress(i, len(work), f'movie {stem}')
        dest = os.path.join(romfs, *reldir.split('/'), stem + '.mp4')
        vanilla = vanilla_at.get((reldir, stem))

        try:
            # Everything that changes the OUTPUT goes in the key, and nothing
            # that does not. Content hash rather than mtime, so re-extracting
            # the .iro does not invalidate a pack that has not changed.
            # `vanilla is not None` is in there because a silent mod file
            # borrows the original's soundtrack, so whether one was available
            # changes what comes out.
            # CONVERTER_VERSION is in here, not just the settings. A
            # 768px texture fix once silently did nothing because its key
            # carried the cap but not the code that applied it, so an
            # unchanged input with unchanged settings kept serving the old
            # output. Any change to what convert() emits bumps that string.
            key = movie_convert.source_key(
                src, '%s|%s|%d|%s|%s|%d|%d|%s|%s|%s'
                % (movie_convert.CONVERTER_VERSION,
                   quality,
                   movie_convert.crf_for(quality),
                   movie_convert.TARGET_PROFILE,
                   movie_convert.TARGET_PRESET,
                   movie_convert.TARGET_ARATE,
                   vanilla is not None,
                   target_fps,
                   fit,
                   colour))
            tag = stem if reldir == movie_convert.MOVIE_DIR else \
                '%s.%s' % (reldir.replace('/', '_'), stem)
            cached_file = os.path.join(cache, f'{tag}.{key}.mp4')

            if os.path.exists(cached_file):
                _link_or_copy(cached_file, dest)
                cached += 1
                produced.append(dest)
                if (reldir, stem) == (movie_convert.MOVIE_DIR,
                                     'opening'):
                    oi = movie_convert.probe(cached_file)
                    plan.opening_fps = oi['fps'] if oi else None
                    if oi:
                        plan.opening_dims = (oi['width'], oi['height'])
                continue

            info = movie_convert.probe(src)
            if info is None:
                log(f'  ! {stem}: not a video file, skipped')
                failed += 1
                continue

            # A mod that already ships the exact target format gets copied
            # rather than re-encoded: a second lossy pass buys nothing.
            # A mod already shipping the target format is copied only when
            # its audio is present -- otherwise it still needs the original's
            # soundtrack muxed in, which means a real encode.
            # Copying is only right when the file is ALREADY what we want,
            # which under normalisation means already at the target rate.
            at_rate = (not target_fps
                       or abs(info['fps'] - target_fps) < 0.01)
            if movie_convert.already_target(info, fit=fit, colour=colour) \
                    and info['has_audio'] and at_rate:
                shutil.copyfile(src, cached_file)
                _link_or_copy(cached_file, dest)
                copied += 1
                produced.append(dest)
                if (reldir, stem) == (movie_convert.MOVIE_DIR,
                                     'opening'):
                    plan.opening_fps = info['fps']
                    plan.opening_dims = (info['width'], info['height'])
                log(f'  ok {stem}: already H.264/mp4, copied as-is')
                continue

            # Encode into the CACHE, then link into place. Writing the cache
            # copy first means an interrupted build leaves the cache correct
            # and sdout missing a file, rather than the other way round.
            t0 = time.time()
            r = movie_convert.convert(src, cached_file, vanilla=vanilla,
                                      quality=quality, target_fps=target_fps,
                                      fit=fit, colour=colour, log=log)
            took = time.time() - t0
            spent += took
            _link_or_copy(cached_file, dest)
            done += 1
            produced.append(dest)
            if (reldir, stem) == (movie_convert.MOVIE_DIR,
                                 'opening'):
                plan.opening_fps = r['out']['fps']
                plan.opening_dims = (r['out']['width'],
                                     r['out']['height'])
            extra = ' + original audio' if r['borrowed_audio'] else ''
            if r.get('doubled'):
                extra += ' (frame-doubled)'
            shown = stem if reldir == movie_convert.MOVIE_DIR else \
                '%s/%s' % (reldir.rsplit('/', 2)[-2], stem)
            if r['fit_reason'] == 'fit':
                resized += 1
                extra += ' (%dx%d -> %dx%d, lanczos)' % (
                    r['src']['width'], r['src']['height'],
                    r['out']['width'], r['out']['height'])
            elif r['src']['width'] < r['drawn'][0] * 0.98:
                undersize.append((stem, r['src']['width'],
                                  r['src']['height']))
            if r['colour_converted']:
                recoloured += 1
            if fit != 'fit' and r['src']['width'] > r['drawn'][0] * 1.02:
                oversize_left.append((stem, r['src']['width'],
                                      r['src']['height']))
            log('  ok %-12s %s -> h264 %s fps, crf %d%s  [%.1fs]'
                % (shown, movie_convert.describe_source(r['src']),
                   r['fps'], r['crf'], extra, took))
        except movie_convert.MissingFFmpeg as exc:
            log(f'! movies skipped: {exc}')
            return
        except Exception as exc:                       # noqa: BLE001
            log(f'  ! {stem}: {exc}')
            failed += 1
            # A cache entry only ever appears after a successful, verified
            # encode, so a failed one must not leave a stub behind for the
            # next build to trust.
            stale = locals().get('cached_file')
            if stale and os.path.exists(stale):
                os.remove(stale)

    # Resolution, reported every build so "is it being downscaled?" is
    # answerable from the log rather than from ffprobe -- and so is the
    # question behind it, which is not "what size is the file" but "what
    # size does the console DRAW it". describe_drawn() answers the second.
    if resized:
        log('        %d movie(s) resampled to the drawn size with Lanczos '
            '(instead of being bilinear-minified on the console)' % resized)
    if recoloured:
        log('        %d movie(s) converted to BT.709 limited, which is what '
            'romfs/shaders/video_p.glsl hardcodes' % recoloured)
    for stem, w, h in oversize_left[:6]:
        log('        ! %s is %dx%d, larger than the %dx%d the console draws '
            '-- with sizing set to "native" the GPU minifies it with one '
            'bilinear tap' % (stem, w, h,
                              movie_convert.TARGET_W, movie_convert.TARGET_H))
    if undersize:
        s0, w0, h0 = undersize[0]
        log('        note: %d movie(s) are SMALLER than the console draws '
            '(e.g. %s at %dx%d). Nothing here can add detail they do not '
            'have -- install custom_shaders/hd_video/video_p.glsl, which '
            'reconstructs magnified movies.'
            % (len(undersize), s0, w0, h0))
    if plan.opening_dims:
        w, h = plan.opening_dims
        van_dims = None
        if vanilla_dir:
            vo = os.path.join(vanilla_dir, 'opening.mp4')
            if os.path.exists(vo):
                vi = movie_convert.probe(vo)
                if vi:
                    van_dims = (vi['width'], vi['height'])
        drawn = movie_convert.device_footprint(w, h)
        if van_dims and van_dims != (w, h):
            log('movie size: opening built at %dx%d, the port\'s own is '
                '%dx%d' % (w, h, van_dims[0], van_dims[1]))
        elif van_dims:
            log('movie size: opening %dx%d, same as the port\'s own' % (w, h))
        else:
            log('movie size: opening %dx%d' % (w, h))
        log('            the console draws it at %dx%d device pixels %s'
            % (drawn[0], drawn[1],
               '(1:1)' if abs(w - drawn[0]) <= 2 else
               ('(magnified %.2fx)' % (drawn[0] / float(w)) if w < drawn[0]
                else '(MINIFIED %.2fx)' % (w / float(drawn[0])))))

    parts = [f'{done} converted' + (f' in {spent:.0f}s' if spent else '')]
    if cached:
        parts.append(f'{cached} reused from cache (no re-encode)')
    if copied:
        parts.append(f'{copied} copied as-is')
    if failed:
        parts.append(f'{failed} FAILED')
    log('movies: ' + ', '.join(parts))
    if done:
        log(f'        cache: {cache}')
        log('        later builds reuse these unless the mod file or the '
            'frame-rate setting changes')


def _emplace_moviecam(sdout, dump, log, produced):
    """
    Extend the movie-camera budget so a 30 fps movie does not run out of
    camera track halfway through.

    THE BUG
    -------
    `moviecam.lgp` holds one 40-byte camera record per movie FRAME:
    `opening.cam` is 71680 bytes = 1792 records = 1792 frames of a 119.47 s
    movie at 15 fps. The reader (x86 0x40AC9A) picks its record by frame --

        edx = get_movie_frame() * 0x28 >> 2        ; 0x40AD46, 10 dwords
        ecx = [0x9A0710] + edx*4

    -- but spends its budget by CALL:

        cmp  [0x9A071C], [0x9A0720]                ; 0x40AD1F, stop if past
        ...
        [0x9A071C] += 10                           ; 0x40AD98, once per call
        [0x9A0720] = camsize/4 - 2                 ; 0x40AFC9, at load

    17918 dwords at 10 a call is exactly 1792 calls. The field calls it once
    per tick, and during a movie the field ticks once per decoded frame, so
    at the vanilla 15 fps the budget, the track and the movie all end
    together at 119.5 s. With the movie set at 30 fps the field ticks 30
    times a second, the budget is spent twice as fast, and it runs out at
    59.7 s -- from there the camera is frozen on whatever record it last
    read while the movie plays on for another minute. `movie-fps` corrects
    the record INDEX and does nothing for the budget, because the two are
    driven by different clocks.

    AND `movie-fps` DOES NOT REACH THIS READER
    ------------------------------------------
    This is the part that took five rounds to find. `movie-fps` patches the
    sixteen-byte stub at ARM64 0x42290 that x86 `get_movie_frame` maps to.
    The moviecam reader does not call that stub: the recompiler INLINED the
    stub sequence into the reader's own body --

        00042078  mov   w0, #5
        0004207C  movk  w0, #0xb00b, lsl #16
        00042084  bl    #0xa510             ; straight to the dispatcher
        0004208C  ldr   w22, [x21]          ; guest EAX, UNHALVED
        000420B0  add   w8, w8, w8, lsl #2  ; x5
        000420B4  ubfiz w8, w8, #1, #0x1d   ; x2   -> frame * 10 dwords

    -- so it reads the RAW counter. There are eleven inlined native-stub
    sequences in the module and `get_movie_frame` accounts for two of them;
    the earlier note that it had "only two call sites" counted calls to the
    stub, not copies of it.

    At 30 fps the camera track is therefore indexed at 30 records a second
    against a track holding one record per VANILLA frame. It reaches the last
    record at 59.7 s -- the halfway point -- and what happens next depends on
    the budget: unpadded it froze there, and padded with a second copy
    APPENDED it ran straight into that copy and replayed the whole track over
    the back half of the movie. Both of those are what the operator saw, in
    that order.

    THE FIX: STRETCH THE TRACK, DO NOT SHORTEN THE INDEX
    ----------------------------------------------------
    Each 40-byte record is written `ratio` times IN PLACE, so the track holds
    one record per 30 fps frame:

        r0 r0 r1 r1 r2 r2 ...

    Raw frame f now selects interleaved record f, which is original record
    f // 2 -- exactly the record vanilla shows at the same instant. The
    camera advances every tick instead of every other one, the track ends
    with the movie at 119.5 s, and the budget (`size/4 - 2`, spent 10 dwords
    a call) comes out at 3584 calls, which is 119.5 s at 30 ticks a second.
    Track, budget, index and movie land together again -- the property the
    vanilla data has and the whole system is built on.

    Appending a copy gives the same budget and the wrong pictures; the
    interleave is the part that matters.

    Records are 40 bytes because the reader copies ten dwords. An entry whose
    length is not a whole number of records (`list.txt`) is repeated wholesale
    instead -- it is not camera data and nothing indexes it.

    That is why this is a padded archive rather than another cave. It cannot
    crash, it needs no cave space, it leaves `exefs/main` untouched, and
    unticking the setting restores the stock file.
    """
    if not movie_30fps() or dump is None or not dump.workingdir:
        return
    found = None
    for dirpath, _dirs, files in os.walk(dump.workingdir):
        for fn in files:
            if fn.lower() == 'moviecam.lgp':
                found = os.path.join(dirpath, fn)
                break
        if found:
            break
    if not found:
        log('! moviecam.lgp not found in the dump -- the movie camera budget '
            'is left at its 15 fps size, so the camera will freeze partway '
            'through long movies')
        return
    rel = os.path.relpath(found, dump.workingdir)
    dest = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID, ROMFS, rel)
    ratio = movie_convert.NORMALISED_RATIO
    def stretch(payload):
        if not payload or len(payload) % CAM_RECORD:
            return payload * ratio      # not camera data; nothing indexes it
        body = b''.join(payload[i:i + CAM_RECORD] * ratio
                        for i in range(0, len(payload), CAM_RECORD))
        return body + payload[-CAM_RECORD:] * (CAM_TAIL_FRAMES * ratio)
    try:
        arc = lgp.Archive(found)
        arc.replace({n: stretch(e['payload']) for n, e in arc.index.items()})
        arc.write(dest)
    except Exception as exc:                                   # noqa: BLE001
        log(f'! moviecam.lgp: {exc} -- left alone')
        return
    produced.append(dest)
    log(f'movie camera: {len(arc.entries)} track(s) stretched x{ratio} -- each '
        f'{CAM_RECORD}-byte record repeated in place, one per '
        f'{movie_convert.NORMALISED_FPS} fps frame, '
        f'+{CAM_TAIL_FRAMES * ratio} held records so a movie a few frames '
        f'long does not run off the end into a zeroed camera')
    log('              (the reader inlines get_movie_frame at ARM64 0x42078, '
        'so it reads the RAW counter that movie-fps cannot reach)')


def _emplace_sfx(plan, romfs, sdout, dump, log, produced):
    """
    Apply an FFNx sound mod by rebuilding audio.fmt / audio.dat.

    The port reads .ogg from `data/music_ogg` and nowhere else, so a sound
    mod's loose files are dead on the SD card. Its audio is not: the same
    750-slot archive the PC game uses is here too, and the mod's own
    `config.toml` says which of its sounds belongs in which slot. Re-encoding
    them into the archive is the only route in, and it is a complete one for
    sound EFFECTS -- 715 of the 750 slots, on the set Cosmo Memory ships.

    Ambience, voice and the cutscene overlay stay out: those need loaders the
    port does not have, and they are reported as skipped elsewhere.

    Untouched slots are carried over byte for byte, so a failure part way
    through leaves an archive that is still the game's own.
    """
    if not plan.sfx:
        return
    files = [(rel, full) for rel, full, _mod in plan.sfx]
    configs, oggs = sfxmod.collect(files)
    if not configs:
        log('! %d sound file(s) found under sfx/ but no %s to map them onto '
            'the game\'s slots -- skipped'
            % (len(files), sfxmod.CONFIG_NAME))
        return
    fmt_rel, fmt_src = _find_in_dump(dump, 'audio.fmt')
    dat_rel, dat_src = _find_in_dump(dump, 'audio.dat')
    if not fmt_src or not dat_src:
        log('! sound mod skipped: audio.fmt / audio.dat are not in the dump, '
            'and they are the only way in -- the port has no external SFX '
            'path.')
        return

    try:
        entries = audio_dat.read(fmt_src, dat_src)
    except Exception as exc:                                   # noqa: BLE001
        log(f'! sound mod skipped: could not read the vanilla archive ({exc})')
        return

    config = sfxmod.merge_configs(configs)
    log('sound effects: %d mapping(s) from %d config file(s), %d .ogg in the '
        'pool' % (len(config), len(configs), len(oggs)))
    try:
        res = sfxmod.rebuild(entries, config, oggs,
                             cache_dir=os.path.join(_movie_cache_dir(sdout),
                                                    '..', 'sfx'),
                             log=log)
    except audio_dat.MissingFFmpeg as exc:
        log(f'! sound mod skipped: {exc}')
        return
    if not res.replaced:
        log('! sound mod produced no replacements -- archive left alone')
        return

    fmt_bytes, dat_bytes = audio_dat.dumps(entries)
    for rel, blob in ((fmt_rel, fmt_bytes), (dat_rel, dat_bytes)):
        dest = os.path.join(romfs, *rel.split('/'))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(blob)
        produced.append(dest)
    for line in sfxmod.describe(res):
        log(line)
    log('               wrote %s and %s' % (fmt_rel, dat_rel))


def _find_in_dump(dump, name):
    """(path relative to the workingdir, absolute path) of a dump file."""
    if dump is None or not dump.workingdir:
        return None, None
    for dirpath, _dirs, files in os.walk(dump.workingdir):
        for fn in files:
            if fn.lower() == name:
                full = os.path.join(dirpath, fn)
                return (os.path.relpath(full, dump.workingdir)
                        .replace(os.sep, '/'), full)
    return None, None


def _loose_destination(holder, name, dump, log):
    """
    Where a loose data file actually belongs, found rather than assumed.

    Returns a path relative to the workingdir. The vanilla copy is looked up
    by name inside the dump and its location mirrored, because the port does
    not keep these where the PC game does: `kernel/kernel2.bin` is what
    ff7_en asks for, but the dump has no `data/kernel` -- language content
    lives under `data/lang-XX/`.

    Several languages ship the same filename. An English mod belongs in the
    English one, so a `lang-en` match wins; failing that a unique match is
    taken, and an ambiguous one is reported rather than guessed at.
    """
    fallback = '%s/%s' % (LOOSE_DIRS[holder], name)
    if dump is None or not dump.workingdir:
        return fallback, None
    matches = []
    for dirpath, _dirs, files in os.walk(dump.workingdir):
        if os.path.basename(dirpath).lower() != holder:
            continue
        for fn in files:
            if fn.lower() == name:
                matches.append(os.path.relpath(os.path.join(dirpath, fn),
                                               dump.workingdir))
    if not matches:
        log('! %s: no vanilla copy in the dump -- writing to %s, which is '
            'where the PC game keeps it. If the mod has no effect, this is '
            'the first thing to check.' % (name, fallback))
        return fallback, None
    if len(matches) == 1:
        return matches[0].replace(os.sep, '/'), matches[0]
    english = [m for m in matches if 'lang-en' in m.replace(os.sep, '/')]
    if len(english) == 1:
        return english[0].replace(os.sep, '/'), english[0]
    log('! %s: %d vanilla copies in the dump (%s) and none of them is the '
        'only English one -- using the first'
        % (name, len(matches), ', '.join(sorted(matches))))
    return matches[0].replace(os.sep, '/'), matches[0]


def _battle_stage_vanilla_path(stage_num, dump, log):
    """Find data/battle/stage<NN>.dat in the dump, mirroring
    _loose_destination's found-not-assumed approach."""
    name = 'stage%02d.dat' % stage_num
    if dump is None or not dump.workingdir:
        return None
    for dirpath, _dirs, files in os.walk(dump.workingdir):
        if os.path.basename(dirpath).lower() != 'battle':
            continue
        for fn in files:
            if fn.lower() == name:
                return os.path.join(dirpath, fn)
    log('! stage%02d: no vanilla stage%02d.dat in the dump -- battle '
        'background tiles for this stage cannot be converted (no native '
        'container to splice into), skipping' % (stage_num, stage_num))
    return None


def _emplace_battle_bg(plan, romfs, dump, log, produced):
    """Convert and splice STAGE<NN>_T<NN>_00.DDS mod tiles into the native
    data/battle/stage<NN>.dat container.

    Native format is a fixed 256x256, 256-color-indexed PSX TIM embedded in
    the stage container (see battle_stage_bg.py). Mod DDS tiles (typically
    512x512-1024x1024, BC7) are decoded, resampled DOWN to the native tile
    size, and Floyd-Steinberg quantized to 256 colors -- there is no
    renderer-side path on this port to display a larger native tile, so
    this recovers art/detail differences from the mod but not resolution
    beyond native. That constraint is unchanged from the field_tex_cap
    investigation; battle stage backgrounds do not share that cap
    mechanism since they are a spliced container, not a standalone .tex.
    """
    if not plan.battle_bg:
        return
    by_stage = {}
    for (stage_num, tile_num), (src, mod) in plan.battle_bg.items():
        by_stage.setdefault(stage_num, {})[tile_num] = (src, mod)

    converted_stages = 0
    skipped_tiles = 0
    for stage_num in sorted(by_stage):
        vanilla_path = _battle_stage_vanilla_path(stage_num, dump, log)
        if vanilla_path is None:
            skipped_tiles += len(by_stage[stage_num])
            continue

        stage_bytes = open(vanilla_path, 'rb').read()
        stage = battle_stage_bg.parse_stage(stage_bytes)
        if stage['ambiguous'] or not stage['tiles']:
            log('! stage%02d: native container not recognized (%s) -- '
                'skipping this stage entirely rather than risk a corrupt '
                'splice' % (stage_num, '; '.join(stage['notes']) or
                            'no tiles found'))
            skipped_tiles += len(by_stage[stage_num])
            continue

        tile_updates = {}
        for tile_num, (src, mod) in sorted(by_stage[stage_num].items()):
            if tile_num >= len(stage['tiles']):
                log('! stage%02d T%02d: mod supplies this tile but the '
                    'native container only has %d tile(s) -- skipping this '
                    'tile' % (stage_num, tile_num, len(stage['tiles'])))
                skipped_tiles += 1
                continue
            try:
                dds_bytes = open(src, 'rb').read()
                rgba, w, h = dds_decode.decode_dds(dds_bytes)
            except Exception as e:
                log('! stage%02d T%02d: could not decode %s (%s) -- '
                    'skipping this tile' %
                    (stage_num, tile_num, os.path.basename(src), e))
                skipped_tiles += 1
                continue
            tl = stage['tiles'][tile_num]
            tw, th = tl['w'], tl['h']
            img = Image.frombytes('RGBA', (w, h), rgba)
            if (w, h) != (tw, th):
                img = img.resize((tw, th), Image.LANCZOS)
            tile_updates[tile_num] = list(img.getdata())

        if not tile_updates:
            continue

        new_stage = battle_stage_bg.build_modified_stage(
            stage_bytes, stage, tile_updates)
        rel = os.path.relpath(vanilla_path, dump.workingdir).replace(
            os.sep, '/')
        dest = os.path.join(romfs, *rel.split('/'))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(new_stage)
        produced.append(dest)
        converted_stages += 1
        log('battle background: stage%02d -- %d tile(s) converted -> %s'
            % (stage_num, len(tile_updates), rel))

    if converted_stages:
        log('battle backgrounds: %d stage(s) converted (native 256x256 '
            '8bpp cap -- see notes if this looks unchanged in-game)'
            % converted_stages)
    if skipped_tiles:
        log('battle backgrounds: %d tile(s) skipped, see warnings above'
            % skipped_tiles)


ARCHIVE_FP_CACHE = os.path.join(HERE, 'cache', '_archive_fp')
NO_ARCHIVE_CACHE_ENV = 'SEVENTH_NX_NO_ARCHIVE_CACHE'


def _stat_sig(path):
    """(size, mtime_ns) for a path, or None if it is not there."""
    try:
        st = os.stat(path)
        return (st.st_size, st.st_ns if hasattr(st, 'st_ns') else st.st_mtime_ns)
    except OSError:
        return None


def _ws_fingerprint(widescreen):
    """
    The widescreen config as a cache key component.

    `plan.widescreen` is a path pair, not a mod file, so it is invisible to
    the `files` half of `_archive_fingerprint`. Without this, switching from
    a mod that ships a config to one that does not would reuse the flevel
    built against the first -- 41 fields with the wrong camera range and no
    line in the log to say why.

    Size+mtime, the same trade the rest of the fingerprint makes.
    """
    if not widescreen:
        return ''
    out = []
    for path in widescreen[:2]:
        if not path:
            continue
        try:
            st = os.stat(path)
            out.append(f'{path}:{st.st_size}:{int(st.st_mtime)}')
        except OSError:
            out.append(f'{path}:missing')
    return '|'.join(out)


def _archive_fingerprint(name, archive_path, files, extra):
    """
    Everything that can change what an archive build produces.

    The per-asset caches (_texconv, _texcap, _pfix, _fieldlzs, _battle_bg_dds)
    already stop the EXPENSIVE conversions from being redone, but they do not
    stop the archive itself from being reassembled and rewritten -- and that is
    most of the wall clock on a no-op rebuild: flevel alone decompresses all
    709 fields just to compare them, and battle.lgp + char.lgp + flevel is
    ~830 MB of output written every time.

    So this fingerprints the INPUTS and lets an unchanged archive be skipped
    outright. Deliberately conservative -- a stale archive is far worse than a
    slow build, so anything that could plausibly change the output is in here:

      * the vanilla archive (size + mtime)
      * every mod source file feeding this archive (path, size, mtime)
      * every SEVENTH_NX_* environment variable, since they are the settings
      * the packer's own .py files, so editing the code invalidates
      * the destination itself, so deleting or touching the output rebuilds

    Content hashing the mod files would be stricter, but Cosmos Limit Break
    alone is 17k files and hashing them costs more than it saves; size+mtime is
    what every build system uses for this and it is the right trade here.
    """
    h = hashlib.sha1()
    h.update(b'ARCHIVE-FP-V1\0' + name.encode())
    h.update(repr(_stat_sig(archive_path)).encode())
    for low in sorted(files):
        v = files[low]
        src = v[0] if isinstance(v, (tuple, list)) else v
        if isinstance(src, dict):            # flevel: {section: (path, mod)}
            for k in sorted(src):
                q = src[k]
                q = q[0] if isinstance(q, (tuple, list)) else q
                h.update(('%s|%s|%r' % (low, k, _stat_sig(q))).encode())
            continue
        h.update(('%s|%r' % (low, _stat_sig(src))).encode())
    for k in sorted(os.environ):
        if k.startswith('SEVENTH_NX'):
            h.update(('%s=%s\0' % (k, os.environ[k])).encode())
    for fn in sorted(os.listdir(HERE)):
        if fn.endswith('.py'):
            h.update(('%s|%r' % (fn, _stat_sig(os.path.join(HERE, fn)))).encode())
    h.update(repr(extra).encode())
    return h.hexdigest()


def _archive_cache_ok(name, dest, fp, log):
    """
    (True, payload) if `dest` is already the product of fingerprint `fp`.

    `payload` carries any value the SKIPPED build would otherwise have
    computed as a side effect. flevel needs this: `_build_flevel` sets the
    module-level FIELD_BG_MAX_RAW, and `apply_field_bg` sizes the exefs/main
    field-decompression-buffer patch from it. Skipping the build without
    restoring that number would patch the module to a stale or zero size,
    which is the crash this whole feature had in build #2. Caching an output
    means caching its side effects too.
    """
    if os.environ.get(NO_ARCHIVE_CACHE_ENV, '').strip() == '1':
        return False, None
    rec = os.path.join(ARCHIVE_FP_CACHE, name + '.fp')
    try:
        with open(rec) as f:
            parts = f.read().split('\n')
        stored, size, mtime, payload = parts[0], parts[1], parts[2], parts[3]
    except (OSError, IndexError, ValueError):
        return False, None
    sig = _stat_sig(dest)
    if sig is None or stored != fp:
        return False, None
    # the output must also be exactly the file we left behind
    if str(sig[0]) != size or str(sig[1]) != mtime:
        return False, None
    log(f'  {name}: unchanged since the last build, kept '
        f'({sig[0]:,} bytes). {NO_ARCHIVE_CACHE_ENV}=1 forces a rebuild.')
    return True, payload


def _archive_cache_store(name, dest, fp, payload=''):
    try:
        os.makedirs(ARCHIVE_FP_CACHE, exist_ok=True)
        sig = _stat_sig(dest)
        if sig is None:
            return
        with open(os.path.join(ARCHIVE_FP_CACHE, name + '.fp'), 'w') as f:
            f.write('%s\n%d\n%d\n%s\n' % (fp, sig[0], sig[1], payload))
    except OSError:
        pass


SDOUT_MANIFEST = os.path.join(HERE, 'cache', '_sdout_manifest')


def prune_stale(sdout, produced, log=lambda *_: None):
    """
    Delete files a PREVIOUS build of this sdout wrote that this one did not.

    Toggling a feature off stops the packer writing its file, but nothing was
    removing the copy the last build left behind -- so the SD tree kept
    serving it and the only reliable fix was deleting sdout by hand every
    time. This closes that.

    SAFETY, and it is the whole design: this only ever deletes paths recorded
    in OUR OWN manifest from the previous build of this same sdout. A file the
    packer did not write is never a candidate, no matter what it is or where
    it sits, so pointing the tool at a directory with other content in it
    cannot destroy anything. The manifest lives in the packer's cache keyed by
    the sdout path, not inside sdout, so it never ends up on the SD card.

    A build that is interrupted leaves the old manifest in place, which is the
    safe direction: the next completed build prunes against it.
    """
    try:
        os.makedirs(SDOUT_MANIFEST, exist_ok=True)
    except OSError:
        return
    root = os.path.abspath(sdout)
    key = hashlib.sha1(root.encode()).hexdigest()[:16]
    rec = os.path.join(SDOUT_MANIFEST, key + '.json')

    now = set(os.path.abspath(p) for p in produced if p)
    try:
        with open(rec) as f:
            old = set(json.load(f))
    except (OSError, ValueError, TypeError):
        old = set()

    # only inside this sdout, and only what we wrote and no longer write
    stale = sorted(p for p in old - now
                   if os.path.abspath(p).startswith(root + os.sep))
    removed = []
    for path in stale:
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed.append(path)
        except OSError as exc:
            log(f'  ! could not remove {os.path.relpath(path, root)}: {exc}')

    # directories that this emptied, deepest first; rmdir refuses non-empty
    # ones so nothing with content in it can go
    for d in sorted({os.path.dirname(p) for p in removed},
                    key=len, reverse=True):
        while d.startswith(root + os.sep):
            try:
                os.rmdir(d)
            except OSError:
                break
            d = os.path.dirname(d)

    if removed:
        log(f'removed {len(removed)} file(s) left by a previous build that '
            f'this one no longer produces:')
        for path in removed[:8]:
            log(f'    {os.path.relpath(path, root)}')
        if len(removed) > 8:
            log(f'    ... and {len(removed) - 8} more')

    try:
        with open(rec, 'w') as f:
            json.dump(sorted(now), f)
    except OSError:
        pass


def apply_plan(plan, archive_paths, sdout, log=lambda *_: None,
               progress=lambda *_: None, dump=None):
    """Write the finished SD tree. Returns a list of produced files."""
    romfs = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID, ROMFS)
    produced = []

    model_targets = sorted(a for a in plan.archive_files if a != 'flevel.lgp')
    flevel_fields = plan.archive_files.get('flevel.lgp', {})
    do_flevel = bool(plan.chunks) or bool(flevel_fields)
    total = len(model_targets) + (1 if do_flevel else 0)
    step = 0

    pack_lgp = ensure_pyff7(log) if model_targets else None

    for name in model_targets:
        if name not in archive_paths:
            log(f'! {name} not in workingdir, skipping')
            continue
        progress(step, total, name)
        step += 1
        dest_path = os.path.join(romfs, ARCHIVES[name])
        fp = _archive_fingerprint(
            name, archive_paths[name], plan.archive_files[name],
            (sorted((plan.folder_of.get(name) or {}).items()),
             sorted(plan.battle_bg_native_names or ())))
        hit, _ = _archive_cache_ok(name, dest_path, fp, log)
        if hit:
            produced.append(dest_path)
            continue
        log(f'building {name} ...')
        dest = _build_model_archive(name, archive_paths[name],
                                    plan.archive_files[name], romfs,
                                    pack_lgp, log,
                                    plan.folder_of.get(name),
                                    plan.battle_bg_native_names)
        if dest:
            produced.append(dest)
            _archive_cache_store(name, dest, fp)

    if do_flevel:
        progress(step, total, 'flevel.lgp')
        step += 1
        if 'flevel.lgp' in archive_paths:
            fdest = os.path.join(romfs, ARCHIVES['flevel.lgp'])
            ffp = _archive_fingerprint(
                'flevel.lgp', archive_paths['flevel.lgp'],
                dict(flevel_fields),
                (sorted((k, sorted(v)) for k, v in plan.chunks.items()),
                 sorted(plan.field_dds_sources or ()),
                 # the widescreen config decides 41 fields' camera ranges,
                 # and it lives outside `flevel_fields`, so it has to be in
                 # the key by hand or swapping mods would reuse a stale
                 # archive built against the previous one
                 _ws_fingerprint(plan.widescreen)))
            hit, payload = _archive_cache_ok('flevel.lgp', fdest, ffp, log)
            if hit:
                produced.append(fdest)
                dest = None
                # restore the side effect the skipped build would have set
                global FIELD_BG_MAX_RAW
                try:
                    FIELD_BG_MAX_RAW = int(payload)
                except (TypeError, ValueError):
                    FIELD_BG_MAX_RAW = 0
                log(f'      (largest field {FIELD_BG_MAX_RAW:,} bytes, '
                    f'restored from the cache so exefs/main is still sized '
                    f'from this flevel)')
            else:
                log('building flevel.lgp ...')
                dest = _build_flevel(archive_paths['flevel.lgp'], plan.chunks,
                                     flevel_fields, romfs, log,
                                     plan.field_dds_sources,
                                     plan.widescreen)
                if dest:
                    _archive_cache_store('flevel.lgp', dest, ffp,
                                         str(FIELD_BG_MAX_RAW))
            if dest:
                produced.append(dest)
        else:
            log('! flevel.lgp not in workingdir, skipping')

    _emplace_movies(plan, romfs, sdout, dump, log, progress, produced)
    _emplace_moviecam(sdout, dump, log, produced)
    _emplace_sfx(plan, romfs, sdout, dump, log, produced)
    _emplace_battle_bg(plan, romfs, dump, log, produced)

    placed = []
    for (holder, name), (loose_src, _mod) in sorted(plan.loose.items()):
        rel, found = _loose_destination(holder, name, dump, log)
        dest = os.path.join(romfs, *rel.split('/'))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(loose_src, dest)
        produced.append(dest)
        placed.append((rel, found is not None))
    for rel, found in placed:
        log('loose data file: %s%s'
            % (rel, '' if found else '   (assumed -- no vanilla copy found)'))

    for low, (src, _) in plan.music.items():
        dest = os.path.join(romfs, MUSIC_DIR, low)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)
        produced.append(dest)
    if plan.music:
        log(f'copied {len(plan.music)} music files')

    # Executable channel: a base x86 ff7 exe (optionally with HEXT baked in)
    # routed to the romfs exe path via LayeredFS. The base exe is found from
    # SEVENTH_NX_EXE, else a file named ff7_en / ff7.exe next to these
    # scripts or in the workingdir. HEXT comes from enabled mods (collected
    # in build_plan) plus SEVENTH_NX_HEXT.
    exe_src = _find_base_exe(archive_paths, dump)
    hext_paths = [p for p, _ in plan.hext_files]
    hext_paths += [p for p in os.environ.get('SEVENTH_NX_HEXT', '').split(os.pathsep) if p]
    if hext_paths and not exe_src:
        log(f'! {len(hext_paths)} HEXT patch(es) found but no base ff7 exe '
            '(put ff7_en or ff7.exe next to the scripts, or set '
            'SEVENTH_NX_EXE); exe not built')
    elif exe_src:
        dest = build_exe(exe_src, hext_paths, sdout, log,
                         opening_fps=plan.opening_fps)
        if dest:
            produced.append(dest)

    return produced


FPS_ENV = 'SEVENTH_NX_60FPS'


def apply_fps_patches(sdout, dump, log=lambda *_: None, produced=()):
    """
    Run the 60 FPS patch set into an ALREADY BUILT sdout tree.

    Must run last. It reads two things the mod build produces:

      * `battle.lgp` -- the animation-script waits are scaled x4 in the
        archive the mods were just written into, not in the dump's copy,
        which the build overwrites anyway. If no mod touched battle.lgp the
        dump's archive is the shipped one and is used instead.
      * the exe -- if HEXT patches produced one in sdout, that becomes the
        base so both sets of changes survive. Otherwise the dump's exe is the
        base. The 60 FPS patcher verifies every original byte, so if a HEXT
        pack happens to touch one of its addresses the build stops with a
        mismatch rather than silently producing a half-patched exe.

    `produced` is the file list from this build. Only files in it are treated
    as fresh; anything else in sdout is a LEFTOVER FROM A PREVIOUS RUN and is
    ignored in favour of the dump. That distinction is the whole reason this
    argument exists: sdout is not cleared between builds, so on a second run
    the exe sitting there is the one this function patched last time, and
    feeding it back in aborts the build with "input is not stock?" -- a
    confusing message for what is really just a stale file. Neither of these
    patch sets is idempotent (the exe verifies stock bytes, the battle.lgp
    waits would be scaled to x16), so starting from pristine sources every
    time is the only correct behaviour.

    Writes into the same sdout, so the result is one tree to copy to the SD
    card rather than two to merge by hand.
    """
    if dump is None or not dump.nso:
        log('! 60 FPS: needs exefs/main from a full game dump; skipped')
        return []
    sys.path.insert(0, HERE)
    try:
        import ff7nx_60fps as fps
    except ImportError as exc:
        log(f'! 60 FPS: cannot import ff7nx_60fps ({exc}); skipped')
        return []

    fresh = {os.path.normpath(os.path.abspath(p)) for p in produced}

    def from_this_build(path):
        return os.path.normpath(os.path.abspath(path)) in fresh

    romfs_ff7 = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID,
                             ROMFS_FF7)
    built_exe = os.path.join(romfs_ff7, EXE_REL)
    base_exe = built_exe if from_this_build(built_exe) else dump.exe
    if not base_exe or not os.path.exists(base_exe):
        log('! 60 FPS: no stock ff7_en to patch (not in the dump, and no HEXT '
            'exe was built); skipped')
        return []
    built_battle = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID,
                                ROMFS, ARCHIVES['battle.lgp'])
    battle = built_battle if from_this_build(built_battle) else None
    if battle is None:
        cand = os.path.join(dump.workingdir, ARCHIVES['battle.lgp'])
        battle = cand if os.path.exists(cand) else None

    log('')
    log('applying 60 FPS patches ...')
    log(f'  base exe    {base_exe}'
        + ('   (HEXT output)' if base_exe == built_exe else '   (from dump)'))
    log(f'  base main   {dump.nso}')
    log(f'  battle.lgp  {battle or "(none -- animation waits not scaled)"}'
        + ('   (built)' if battle == built_battle else
           '   (from dump)' if battle else ''))

    # The identity check must see the STOCK exe. `base_exe` is the one this
    # build produced, and if any mod shipped HEXT patches into .text -- a UI
    # mod will ship hundreds -- its hash no longer matches the reference and
    # the whole 60 FPS set aborts. That is constraint A in the handoff, and
    # it turned up for real the first time Enhanced Stock UI was enabled.
    argv = fps.recommended_argv(out=sdout, exe=base_exe, nso=dump.nso,
                                battle_lgp=battle,
                                exe_identity=(dump.exe
                                              if base_exe != dump.exe else None))
    # 30 FPS FMV support. The cave divides the movie frame counter, and it is
    # only correct because _emplace_movies() has just built the WHOLE movie
    # set at the doubled rate. One setting drives both halves; enabling this
    # group by hand without that step would desync every 15 fps movie.
    # 360 degree field movement. ONE code cave; see ff7nx_analog.py. It goes
    # in reclaimed inter-function alignment padding rather than the 2,464-byte
    # gap between .text and .rodata, so this costs the 60 FPS cave budget
    # nothing and no other group has to be turned off to make room. The two
    # lookup tables go in .rodata's tail. See notes/README-28 and -29.
    #
    # SEVENTH_NX_ANALOG_DIAG=1 alongside this builds a diagnostic variant that
    # ignores the stick and rotates a flat 45 degrees, so a build that appears
    # to do nothing can be told apart from a cave that is not running.
    if analog_360():
        argv = [x + ',analog-360' if k and argv[k - 1] == '--enable' else x
                for k, x in enumerate(argv)]
    # Two input tweaks; see ff7nx_nocheats.py. `no-autorun` is a single word
    # patch, `no-cheats` two two-word caves in reclaimed padding, so neither
    # costs the 60 FPS cave budget anything.
    for on, group in ((no_autorun(), 'no-autorun'), (no_cheats(), 'no-cheats')):
        if on:
            argv = [x + ',' + group if k and argv[k - 1] == '--enable' else x
                    for k, x in enumerate(argv)]
    if limiter_fps():
        argv += ['--limiter-fps', str(limiter_fps())]
    if movie_30fps():
        # Two separate faults, both visible only during a movie:
        #   movie-fps   the DECODED FRAME index, doubled by a 30 fps file
        #   movie-poll  the MVIEF POLL count, doubled by the 60 FPS field
        # The second is a 60 FPS artifact that the game's own 15 fps movies
        # have as well; it rides with this switch because a cutscene long
        # enough to notice it is exactly what an FMV mod provides.
        #
        # movie-update is NOT here. It halves the number of guest movie
        # updates so the field clock during a movie matches vanilla, and it
        # was tested on hardware: the picture correctly dropped to 15 fps and
        # the early model overlay in md1stin DID NOT MOVE. That result rules
        # the field tick rate out as the cause and makes the group a
        # regression in the only way the operator can see -- so it stays
        # available by name (`--enable movie-update`) for bisecting and is
        # never shipped by default. Do not put it back without a hardware
        # result that says it helps.
        extra = ','.join((fps.MOVIE_FPS_GROUP, fps.MOVIE_POLL_GROUP))
        argv = [a + ',' + extra
                if k and argv[k - 1] == '--enable' else a
                for k, a in enumerate(argv)]
        log('  30 FPS FMV support: movie frame counter / %d, and the MVIEF '
            'poll counter / 2 so overlaid models keep their cue'
            % movie_convert.NORMALISED_RATIO)
    before = _tree_snapshot(sdout)
    if not fps.run(argv, log=lambda s: log('  ' + s if s else '')):
        log('! 60 FPS: FAILED -- the rest of the build is still valid, but '
            'this tree is 30 FPS')
        return []
    new_files = sorted(set(_tree_snapshot(sdout)) - set(before))
    log(f'  60 FPS patches applied ({len(new_files)} new file(s))')
    # _tree_snapshot's difference reports files that did not EXIST before, so
    # on a rebuild -- sdout is never cleared -- the module and exe this pass
    # just rewrote are absent from it, and it returns an empty list while
    # having written two files. Any later pass that uses this list to answer
    # "did this build produce the module?" then gets "no" and reaches for the
    # dump's stock copy instead.
    #
    # That is not hypothetical: it silently reverted the whole 60 FPS set on
    # the second build. The two files this pass definitely wrote are named
    # explicitly.
    for path in (os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID,
                              'exefs', 'main'),
                 built_exe):
        if os.path.exists(path) and path not in new_files:
            new_files.append(path)
    return sorted(new_files)


def apply_widescreen(sdout, dump, log=lambda *_: None, produced=()):
    """
    Stop the renderer manufacturing a 4:3 logical width, so the black bars
    at the sides go away. See ff7nx_widescreen.py for the reverse
    engineering and for what this does NOT fix.

    Runs AFTER apply_fps_patches for the same reason that one runs last: it
    edits `exefs/main`, and if the 60 FPS set just wrote a patched module
    into sdout, that is the one to build on so both survive. Anything in
    sdout that this build did not produce is a leftover from a previous run
    and is ignored in favour of the dump -- the same rule, for the same
    reason, as apply_fps_patches.

    Unlike the 60 FPS set this IS idempotent-safe in the only way that
    matters: nso_patcher verifies all four original words, so re-running it
    over an already-patched module fails cleanly rather than corrupting it.
    """
    if not ff7nx_widescreen.enabled():
        return []
    if dump is None or not dump.nso:
        log('! widescreen: needs exefs/main from a full game dump; skipped')
        return []
    dest = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID, 'exefs',
                        'main')
    fresh = {os.path.normpath(os.path.abspath(p)) for p in produced}
    built = os.path.normpath(os.path.abspath(dest)) in fresh
    src = dest if built else dump.nso
    log('')
    log('applying 16:9 logical width (pillarbox removal) ...')

    # REFUSE to overwrite a module this build did not produce.
    #
    # The first version of this trusted `produced` alone, and `produced` was
    # lying (see apply_fps_patches). It based on the dump's stock module and
    # wrote the result over a fully patched 60 FPS one -- reverting 110 word
    # patches and 24 code caves without a word in the log. Four bytes of
    # widescreen are not worth that risk, so the destination is checked
    # directly: if something is already there and it is NOT what we are about
    # to build on, stop.
    if not built and os.path.exists(dest):
        try:
            same = (os.path.getsize(dest) == os.path.getsize(dump.nso)
                    and open(dest, 'rb').read() == open(dump.nso, 'rb').read())
        except OSError:
            same = False
        if not same:
            log(f'! widescreen: {dest}')
            log('  already holds a module this build did not produce -- most '
                'likely a patched one from an earlier run. Basing on the '
                "dump's stock copy would silently throw those patches away, "
                'so nothing was written.')
            log('  Turn the 60 FPS switch on so both passes run together, or '
                'delete sdout/ and rebuild.')
            return []
    log(f'  base main   {src}'
        + ('   (60 FPS output)' if built else '   (from dump)'))
    tmp = dest + '.ws-tmp'
    if not ff7nx_widescreen.apply_to_nso(src, tmp, log):
        if os.path.exists(tmp):
            os.remove(tmp)
        log('! widescreen: FAILED -- the rest of the build is still valid, '
            'this tree just keeps the black bars')
        return []
    os.replace(tmp, dest)
    for h in (720, 1080):
        log(f'  {h}p: logical width '
            f'{ff7nx_widescreen.logical_width(h, False)} -> '
            f'{ff7nx_widescreen.logical_width(h, True)}')
    log('  note: 2D menus and boxes are laid out against this width too, so '
        'expect UI to shift. The background is FITTED to it -- without a '
        'mod that ships wider backgrounds it will stretch rather than '
        'extend. 3D models and the battle camera are unaffected (the '
        'projection matrix is a separate, unsolved patch).')
    return [dest] if not built else []


def apply_field_bg(sdout, dump, log=lambda *_: None, produced=()):
    """
    Let field background TRUECOLOR pages be bigger than 256x256.

    Six words in `exefs/main`; see ff7nx_fieldbg.py for the derivation and
    README-field-bg-512-MEASURED.md for how each one was measured. depth-1
    (8-bit paletted) pages are deliberately left at 256 -- the loader's
    #0x10000 is shared with their read count and is NOT touched.

    Runs AFTER apply_fps_patches and apply_widescreen, on their output, for
    exactly the same reason those two run last: it edits `exefs/main`, and
    basing on the dump's stock module would silently revert whatever they
    wrote. The same refusal-to-clobber check is applied, for the same
    reason.

    This pass needs no cave space. All six words are in-place immediates,
    nowhere near the 60 FPS sites or the tail gap at 0x1152660.
    """
    if not ff7nx_fieldbg.enabled():
        return []
    px = ff7nx_fieldbg.page_px()
    if not ff7nx_fieldbg.patches_module(px, FIELD_BG_MAX_RAW):
        # 256px on a build whose biggest field still fits the stock
        # 2,000,000 byte buffer. Every word this pass would write already
        # holds the value it wants, so there is nothing to do -- and, more
        # usefully, nothing to REQUIRE: this is the one field-background
        # setting that ships as a flevel.lgp alone, with no game dump.
        log('')
        log(f'{px}x{px} field background pages need no module patch '
            f'(the stock words already say {px}); flevel.lgp carries this '
            f'setting on its own.')
        return []
    if dump is None or not dump.nso:
        log('! field background: needs exefs/main from a full game dump; '
            'skipped')
        return []
    dest = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID, 'exefs',
                        'main')
    fresh = {os.path.normpath(os.path.abspath(p)) for p in produced}
    built = os.path.normpath(os.path.abspath(dest)) in fresh
    src = dest if built else dump.nso
    log('')
    log(f'applying {px}x{px} field background pages ...')
    if not built and os.path.exists(dest):
        try:
            same = (os.path.getsize(dest) == os.path.getsize(dump.nso)
                    and open(dest, 'rb').read() == open(dump.nso, 'rb').read())
        except OSError:
            same = False
        if not same:
            log(f'! field background: {dest}')
            log('  already holds a module this build did not produce -- most '
                'likely a patched one from an earlier run. Basing on the '
                "dump's stock copy would silently throw those patches away, "
                'so nothing was written.')
            log('  Turn the 60 FPS switch on so the passes run together, or '
                'delete sdout/ and rebuild.')
            return []
    log(f'  base main   {src}'
        + ('   (previous patch output)' if built else '   (from dump)'))
    tmp = dest + '.fbg-tmp'
    if not ff7nx_fieldbg.apply_to_nso(src, tmp, log, px,
                                      max_raw=FIELD_BG_MAX_RAW):
        if os.path.exists(tmp):
            os.remove(tmp)
        log('! field background: FAILED -- the rest of the build is still '
            'valid, field backgrounds just stay at 256px. flevel.lgp was '
            'built to match this setting, so turn it back off and rebuild '
            'before using this SD tree.')
        return []
    os.replace(tmp, dest)
    return [dest] if not built else []


def apply_heap(sdout, dump, log=lambda *_: None, produced=()):
    """
    Raise FF7's guest heap from the 64 MB the port hardcoded.

    THIS IS THE ONE THAT MATTERS. Every field-texture ceiling in this
    project -- truecolor on every page, all of Cosmos's art, 512px pages --
    is the same ceiling, and it is FF7 running out of heap, not a rendering
    problem. HANDOFF-105 has the crash log; ff7nx_heap.py has the
    disassembly. Short version:

      * The port does not recompile the Win32 API. It binds it by NAME
        through a 203-entry shim table at 0x1196B98, and `HeapCreate`
        (+0x10EE7B0) IGNORES the arguments `ff7_en` passes it (x86 0x40E019
        asks for a growable 4 KB heap with no maximum) and hands back a
        fixed 64 MB pool at guest 0x02000000.
      * `HeapAlloc` (+0x10EE860) is FIRST FIT. When the free list runs out
        it dumps the heap to "Documents/heap_dump.txt", which cannot exist
        on Switch, and nnSdk aborts. That abort IS the Men's Hall crash.
      * The pool cannot simply grow. `<virt>` sits immediately above it and
        every guest region is carved from ONE 80 MB host malloc that has
        4.44 MB spare. So the patch moves three things together: heap size,
        `<virt>` base, arena size -- nine words, five of which are inlined
        copies of the same lazy arena init.

    HEAP_TIGHT_FIELDS -- the mkt_mens section-9 holdback -- is the bandaid
    this replaces. It stays in place until a build with this pass has been
    walked through Men's Hall on hardware with the list emptied; see §4.3 of
    the handoff for that test.

    Runs after every other module pass, on their output, for exactly the
    same reason they run last: it edits `exefs/main`, and basing on the
    dump's stock module would silently revert whatever they wrote. Same
    refusal-to-clobber check, same wording, as apply_field_bg.

    No cave space. All nine words are in-place immediates in the memory
    shims at +0x10EE5xx / +0x10EE7xx / +0x10FBxxx / +0x10FCxxx / +0x10FDxxx,
    nowhere near the 60 FPS sites or the tail gap at 0x1152660. The guest
    BASE is deliberately not touched: the port's own graphics driver passes
    0x02000000 to `HeapAlloc`/`HeapFree` as a literal in twelve places, and
    keeping the base means none of them move.
    """
    import ff7nx_heap
    mb = ff7nx_heap.HEAP_MB
    if mb == ff7nx_heap.STOCK_MB:
        return []
    why = ff7nx_heap.encodable(mb)
    if why:
        log(f'! guest heap: HEAP_MB = {mb} cannot be written -- {why}')
        return []
    if dump is None or not dump.nso:
        log('! guest heap: needs exefs/main from a full game dump; skipped')
        return []
    dest = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID, 'exefs',
                        'main')
    fresh = {os.path.normpath(os.path.abspath(p)) for p in produced}
    built = os.path.normpath(os.path.abspath(dest)) in fresh
    src = dest if built else dump.nso
    log('')
    log(f'raising the FF7 guest heap {ff7nx_heap.STOCK_MB} -> {mb} MB ...')
    if not built and os.path.exists(dest):
        try:
            same = (os.path.getsize(dest) == os.path.getsize(dump.nso)
                    and open(dest, 'rb').read() == open(dump.nso, 'rb').read())
        except OSError:
            same = False
        if not same:
            log(f'! guest heap: {dest}')
            log('  already holds a module this build did not produce -- most '
                'likely a patched one from an earlier run. Basing on the '
                "dump's stock copy would silently throw those patches away, "
                'so nothing was written.')
            log('  Turn the 60 FPS switch on so the passes run together, or '
                'delete sdout/ and rebuild.')
            return []
    log(f'  base main   {src}'
        + ('   (previous patch output)' if built else '   (from dump)'))
    tmp = dest + '.heap-tmp'
    already = (ff7nx_heap.read_mb(src) == mb)
    if not ff7nx_heap.apply_to_nso(src, tmp, log, mb):
        if os.path.exists(tmp):
            os.remove(tmp)
        if not already:
            log('! guest heap: FAILED -- and this one is NOT harmless.')
            log('  _build_flevel already dropped the heap hold-back because '
                f'HEAP_MB is {mb}, so flevel.lgp in this SD tree ships '
                'mkt_mens with its full mod background and expects the '
                'raised heap. With a stock 64 MB module, Men\'s Hall will '
                'crash on entry.')
            log('  Either fix the cause above and rebuild, or set '
                'ff7nx_heap.HEAP_MB = 64 and rebuild so flevel.lgp goes back '
                'to holding that room back.')
        return []
    os.replace(tmp, dest)
    log('  the host arena is one malloc out of nnSdk\'s heap, which '
        '+0x1150DE0 sizes to ALL available application memory rounded down '
        'to 2 MB -- so this is not competing with a fixed reservation. If '
        'it ever does not fit, map_region aborts at +0x10FB580 on boot '
        'rather than corrupting anything.')
    return [dest] if not built else []


BG_CLEAR_ENV = 'SEVENTH_NX_BG_CLEAR'


def bg_clear_enabled(env=None):
    """
    RETIRED. Always False unless SEVENTH_NX_BG_CLEAR=force.

    FINDINGS-92 §6. Measured on hardware twice and it changed nothing, because
    the flat margin colour is not the clear colour -- `ff7nx_marginart` and
    `ff7nx_marginpal` fixed that at the source, which is why the margins have
    been correct since. The GUI checkbox is gone and this pass is no longer
    called from the pipeline.

    Refused HERE and not by flipping a default, and the word is deliberately
    'force' rather than '1', because FINDINGS-91 §6 is the whole reason: the
    settings save path writes every key on every build, so any '1'-shaped
    default is one stale settings.json away from coming back.

    ---- the original note, kept because the derivation is correct ----

    OFF by default, on the module's own instructions.

    `ff7nx_bgcolor.py` wrote the decision tree before any of this shipped:

        * Margins turn black.      Confirmed, the margin was the clear colour.
        * Margins are unchanged.   Then the flat colour is NOT the clear
                                   colour, and THIS PATCH SHOULD BE REVERTED.

    MEASURED on hardware, 2026-08-05: applied, verified in the log
    (`2 word(s) verified and applied`), and the Sector 6 margins are
    unchanged. So the second branch is the one we are on. The patch is kept
    and wired -- it is correct code and costs one build to retry -- but it is
    off unless asked for.

    settings.json `bg_clear: 1` / the GUI checkbox turns it on.
    """
    raw = (env if env is not None
           else os.environ.get(BG_CLEAR_ENV, '0')).strip().lower()
    return raw == 'force'


def apply_bg_clear(sdout, dump, log=lambda *_: None, produced=()):
    """
    Make the FRAME CLEAR COLOUR black, so the 16:9 margins are black instead
    of the field's own background colour.

    RETRACTION, AND IT IS MINE. An earlier version of this pass shipped
    `ff7nx_bgclear`, whose header says `gfx_drv_clear` has "zero callers" and
    is dead code. I measured that ("callers of the clear thunk in this image:
    0"), reported it, and wired the patch in. It is a FALSE NEGATIVE, and
    `ff7nx_bgcolor.py` -- sitting in the same directory -- had already written
    down why:

        gfx_drv_clear_all / gfx_drv_clear are entries 143/144 of the port's
        gfx-driver table (gfx_drv_table.txt, dump_gfx_table.py). They are
        FUNCTION POINTERS installed into FF7's `struct gfx_driver` and called
        indirectly from the main loop. A B/BL scan is blind to them by
        construction -- it reports zero callers for gfx_drv_flip too.

    So the clear was never dead. It runs every frame, and it clears to the
    colour `gfx_drv_setbg` stored -- the field's own background colour.
    Inside 4:3 the art covers it; outside, it IS the margin. That is why the
    margin "reads as the field's dominant palette colour": it literally is
    one. Adding a second call to the same clear, with the same colour, was
    always going to change nothing, and on hardware it changed nothing.

    The patch that does the job is two words in `gfx_drv_setbg`, storing XZR
    instead of the colour it was handed. Disassembled in `ff7nx_bgcolor.py`;
    same instruction, same addressing mode, no cave, no displaced instruction.

    LAST of the module passes, after the field-background page size.
    """
    if not bg_clear_enabled():
        return []
    dest = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID, 'exefs',
                        'main')
    fresh = {os.path.normpath(os.path.abspath(p)) for p in produced}
    built = os.path.normpath(os.path.abspath(dest)) in fresh
    src = dest if built else dump.nso
    log('')
    log('forcing the frame clear colour to BLACK (16:9 margins) ...')
    if not built and not os.path.exists(src):
        log('! black margins: no module to patch')
        return []
    log(f'  base main   {src}'
        + ('   (previous patch output)' if built else '   (from dump)'))
    tmp = dest + '.bgcolor-tmp'
    try:
        ok = ff7nx_bgcolor.apply(src, tmp, log)
    except Exception as exc:                                   # noqa: BLE001
        log(f'! black margins: {type(exc).__name__}: {exc}')
        ok = False
    if not ok:
        if os.path.exists(tmp):
            os.remove(tmp)
        log('! black margins: NOT applied -- the rest of the build is still '
            'valid, the 16:9 margins just keep the field background colour.')
        return []
    os.replace(tmp, dest)
    log(f'  wrote {dest}')
    return [dest] if not built else []


FIELD_FRAME_ENV = ff7nx_letterbox.LETTERBOX_ENV
MODEL_CULL_ENV = ff7nx_modelcull.MODELCULL_ENV
MOVIE_ALIGN_ENV = ff7nx_moviealign.MOVIEALIGN_ENV
BATTLE_WIDE_ENV = ff7nx_battlewide.BATTLEWIDE_ENV
SWIRL_SCALE_ENV = ff7nx_swirlscale.SWIRLSCALE_ENV
UI_CLIP_ENV = ff7nx_uiclip.UICLIP_ENV
CREDITS_ENV = ff7nx_credits.CREDITS_ENV


def _ws_on():
    try:
        return ff7nx_ws.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def apply_field_frame(sdout, dump, log=lambda *_: None, produced=()):
    """
    Give the field the whole 16:9 frame: no painted letterbox, no 448-of-480
    crop, the background centred in it, and the model cull widened to match.
    FINDINGS-88.

    Seven words from `ff7nx_letterbox` plus two from `ff7nx_modelcull`. No
    caves, no displaced instructions, every one an immediate or a single
    store, byte-exactly reversible.

    Three things belong here rather than in the modules.

      * TWO GATES, NOT ONE. The frame height and the movie quad are the
        "Full-height 16:9 field" checkbox: they are one visual change and
        splitting them is worse than doing neither, because a field that has
        moved and an FMV that has not makes the cut jump 24 px. The model cull
        is NOT on that checkbox and has no switch of its own -- its box is
        4:3-sized, which against a 16:9 frame is a plain bug, and there is no
        configuration in which an NPC should be culled while still inside the
        picture. It follows the 16:9 setting alone.
      * BOTH GATES ARE UNDER 16:9, AND THAT IS NOT CAUTION. At 4:3 the 448-of-480
        letterbox is the framing FF7 was authored in and the port paints it
        deliberately; opening it would show 32 game units the composition
        never expected on every field. `ff7nx_modelcull`'s numbers are worse
        than useless at 4:3 -- they keep models alive outside a frame that
        never widened. FFNx guards both the same way: its uncrop helpers go
        through `is_fieldmap_wide()` and its model cull through
        `widescreen_enabled`.
      * THE MOVIE QUAD IS THE THIRD THING THAT HAS TO MOVE. FF7's FMVs hand
        straight over to gameplay, so the quad (game (0,0)-(640,H)) has to be
        shifted the same +16 game units or the cut jumps 24 px and models
        drawn over the video sit low against it. `ff7nx_moviealign` is a
        12-word cave in the padding pool, and it is the only cave here.
      * THE CENTRING HAS THREE LEGS, NOT TWO, AND THEY TRAVEL TOGETHER. This
        entry said "two halves" until 2026-08-08 and the missing third is what
        put Cloud beside the ladder in the Reactor 1 escape:

            four tile origins  224 -> 232   the BACKGROUND   (+8 tile x2)
            [0xCFF200]         224 -> 240   field SPRITES    (steam, fire)
            set_field_viewport y 0 -> 16    3D MODELS        (via _42)

        All three are the SAME +16 game units -- 24 device rows at 720p -- and
        FFNx writes the first and third from one `if` (ff7_opengl.cpp:312).
        Ship two of the three and characters are 24 px off the ground they
        stand on: HANDOFF-85's "attempt 2", the mis-encoded 232 of 2026-08-07,
        and v7's h=480 are all the same omission wearing different clothes.
        `ff7nx_letterbox` writes all three or none.
      * THE FIELD VIEWPORT HEIGHT IS NOT A KNOB. It stays 448 in every mode.
        The view is opened by the uncrop SCISSOR, exactly as FFNx does it
        (renderer.cpp:1667), and the uncrop caves are gated on the literal
        pair `y == 16 && h == 448`. Setting h to 480 opens the picture by
        accident, disarms the three caves that were meant to open it, and
        rescales every model against a background that did not rescale. The
        module refuses to plan that combination now rather than print it.
      * A CONSTANT IS VERIFIED BY DECODING IT BACK. FINDINGS-88 8d: a patch
        that verifies its STRUCTURE has not verified its CONTENT. Both modules
        assert at import that every replacement word's immediate is the number
        in the patch's own name, and `--verify` prints it.

    THE LAST MODULE PASS, and since FINDINGS-92 the only one that touches
    exefs/main after ff7nx_ws -- `apply_bg_clear` and `apply_movie_clip` are
    both retired and neither is called any more. Every cave here takes padding
    holes, and `ff7nx_cave`'s allocator re-checks that its holes are still zero
    IN THE MODULE IT IS HANDED, so each pass below sees what the ones before it
    took. Only the moviebars/moviealign order is load-bearing; see below.
    """
    want_frame = ff7nx_letterbox.enabled()
    want_cull = ff7nx_modelcull.enabled()
    want_movie = ff7nx_moviealign.enabled()
    # RETIRED on a hardware result. `ff7nx_moviecull` was installed, gated
    # correctly and executing, and models were still drawn in all four
    # margins during an FMV -- FINDINGS-91 §9. The cull is an early-out on a
    # model's ORIGIN with ~50 units of slack; it removes whole models and
    # cannot slice one, so no pair of bounds could ever have done this job.
    # `ff7nx_moviebars` replaces it.
    #
    # Refused HERE rather than by flipping the module's default, because that
    # is the mistake FINDINGS-91 §6 wrote up: the GUI writes the environment
    # variable on every save, so a module default is not a gate. The word is
    # deliberately 'force' and not '1', so no checkbox and no settings.json
    # can produce it.
    want_mcull = str(os.environ.get(ff7nx_moviecull.MOVIECULL_ENV,
                                    '')).strip().lower() == 'force'
    want_bars = ff7nx_moviebars.enabled()
    want_clamp = ff7nx_camclamp.enabled()
    want_battle = ff7nx_battlewide.enabled()
    want_swirl = ff7nx_swirlscale.enabled()
    want_uiclip = ff7nx_uiclip.enabled()
    want_credits = ff7nx_credits.enabled()
    if not (want_frame or want_cull or want_movie or want_mcull
            or want_bars or want_clamp or want_battle or want_swirl
            or want_uiclip or want_credits):
        # SAY SO. `ff7nx_ws.apply_module` learned this the expensive way and
        # wrote it down: "Silence here cost a whole build." A pass that is
        # gated OFF and prints nothing is indistinguishable in the log from a
        # pass that was never wired in, and on 2026-08-07 that ambiguity cost
        # a build and a boot -- the log went straight from the page-size pass
        # to the movie clip and there was no way to tell which had happened.
        log('')
        log('field frame: NOT APPLIED -- the field keeps its painted '
            'letterbox and its 448-of-480 crop, and the model cull keeps its '
            '4:3 box.')
        log(f'  16:9 is {"ON" if _ws_on() else "OFF"} '
            f'({ff7nx_ws.WIDESCREEN_ENV}={os.environ.get(ff7nx_ws.WIDESCREEN_ENV, "")!r})')
        for name, env, want in (
                ('frame height', FIELD_FRAME_ENV, want_frame),
                ('movie align', MOVIE_ALIGN_ENV, want_movie),
                ('movie bars', ff7nx_moviebars.MOVIEBARS_ENV, want_bars),
                ('battle overlays', BATTLE_WIDE_ENV, want_battle),
                ('swirl scale', SWIRL_SCALE_ENV, want_swirl),
                ('2D viewport scale', UI_CLIP_ENV, want_uiclip),
                ('credits fade quad', CREDITS_ENV, want_credits),
                ('model cull', MODEL_CULL_ENV, want_cull)):
            log(f'  {name:14s} {"on" if want else "off"}   '
                f'({env}={os.environ.get(env, "<unset, follows 16:9>")!r})')
        log('  frame height and movie align are the "Full-height 16:9 field" '
            'checkbox; the model cull has no switch and follows 16:9 alone.')
        log('  See FINDINGS-88. To force it on for one build: '
            f'{FIELD_FRAME_ENV}=1 {MODEL_CULL_ENV}=1 {MOVIE_ALIGN_ENV}=1')
        return []
    if dump is None or not dump.nso:
        log('! field frame: needs exefs/main from a full game dump; skipped')
        return []
    dest = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID, 'exefs',
                        'main')
    fresh = {os.path.normpath(os.path.abspath(p)) for p in produced}
    built = os.path.normpath(os.path.abspath(dest)) in fresh
    src = dest if built else dump.nso
    log('')
    log('opening the field frame to 16:9 ...' if want_frame
        else 'widening the field model cull to 16:9 ...')
    if not built and not os.path.exists(src):
        log('! field frame: no module to patch')
        return []
    # Same refusal to clobber as apply_movie_clip: if no earlier pass produced
    # exefs/main this run, `src` is the dump's stock module, and writing that
    # over an sdout/main a previous build patched would silently revert 60 FPS,
    # the field buffer and everything else.
    if not built and os.path.exists(dest):
        try:
            same = (os.path.getsize(dest) == os.path.getsize(dump.nso)
                    and open(dest, 'rb').read() == open(dump.nso, 'rb').read())
        except OSError:
            same = False
        if not same:
            log(f'! field frame: {dest}')
            log('  already holds a module this build did not produce. Not '
                'touching it.')
            return []
    log(f'  base main   {src}'
        + ('   (previous patch output)' if built else '   (from dump)'))
    if not built:
        shutil.copyfile(src, dest)
    rc = 0
    if want_frame:
        rc |= ff7nx_letterbox.apply(dest, log=log)
    if want_cull:
        rc |= ff7nx_modelcull.apply(dest, log=log)
    if want_movie:
        rc |= ff7nx_moviealign.apply(dest, log=log)
    if want_bars:
        # AFTER ff7nx_moviealign, always. The bars are placed against the
        # movie quad, and moviealign is what decides whether that quad sits at
        # game y 0 or y 16. ff7nx_moviebars READS +0x10DE8F0 to find out, so
        # running it first would place the top and bottom bars 24 px away from
        # the picture they are supposed to meet.
        #
        # FMV ONLY. A credits arm was tried and ROLLED BACK -- HANDOFF-104
        # s5.1. Its gate read [0xF4F454], which looked like "the credits are
        # running" from its three write sites but is sticky: the clear sits
        # inside one arm of a jump-table sub-state machine, so once the intro
        # set it the pillarbox painted over the whole game. The intro's side
        # margins are still handled, but by ff7nx_credits' colour clear, not
        # by this.
        rc |= ff7nx_moviebars.apply(dest, log=log)
    else:
        log('  movie margin bars: OFF -- models will draw over the black '
            'margins during an FMV. '
            f'({ff7nx_moviebars.MOVIEBARS_ENV}='
            f'{os.environ.get(ff7nx_moviebars.MOVIEBARS_ENV, "<unset>")!r})')
    if want_mcull:
        # AFTER ff7nx_modelcull, always: the cave's not-playing branch is the
        # displaced word, so running it first would bake 40/400 into both
        # arms. ff7nx_moviecull refuses in that case rather than writing a
        # cave that does nothing, but the ordering is the real guarantee.
        log('  movie model cull: RETIRED, forced on by '
            f'{ff7nx_moviecull.MOVIECULL_ENV}=force. It is a proven null '
            'result -- see FINDINGS-91 §9 -- and it costs 22 words of padding '
            'for nothing.')
        rc |= ff7nx_moviecull.apply(dest, log=log)
    if want_clamp:
        rc |= ff7nx_camclamp.apply(dest, log=log)
    # --- the battle overlays -------------------------------------------
    # Independent of everything above: different functions, no shared words,
    # no ordering constraint.  They are here rather than in a pass of their
    # own because they patch the same exefs/main and share the 16:9 gate;
    # splitting them out would duplicate the copy-and-refuse logic for no
    # gain.
    #
    # NEITHER touches the STORED battle rect, which is what lets them coexist
    # with ff7nx_letterbox's uncrop leg -- that leg matches the literal rect
    # `cmp wY,#0 / cmp wH,#332`, and a stored change would silently stop it
    # firing and bring the black band back.  Both modules assert this in
    # --verify; FINDINGS-99 4 is the build that proved it matters.
    if want_battle:
        rc |= ff7nx_battlewide.apply_all(dest, log=log)
    else:
        log('  battle overlays: OFF -- summon and limit-break flashes and '
            'the battle fade cover only the middle 4:3. '
            f'({BATTLE_WIDE_ENV}='
            f'{os.environ.get(BATTLE_WIDE_ENV, "<unset>")!r})')
    if want_swirl:
        rc |= ff7nx_swirlscale.apply(dest, log=log)
    else:
        log('  swirl scale: OFF -- the battle-entry swirl squeezes the 16:9 '
            f'freeze frame into 4:3. ({SWIRL_SCALE_ENV}='
            f'{os.environ.get(SWIRL_SCALE_ENV, "<unset>")!r})')
    # --- the 2D viewport scale -------------------------------------------
    # FINDINGS-103. `ff7nx_ws` puts the widescreen scale in the VERTEX SHADER,
    # so 2D geometry lands at 1.5x + 160 -- but a window's own viewport rect
    # is still computed on the CPU with a hardcoded /640 and no widescreen
    # term, i.e. 2x. The two agree only at game x = 320, so a window wholly
    # left of centre loses its RIGHT border, one wholly right of centre loses
    # its LEFT border, and an edge near 320 loses part of one.
    #
    # A 21-word cave at +0x10D9F48 puts the shader's own transform on the
    # rect -- x -> (3x)/4 + tW/8 -- and leaves FULL-SCREEN rects alone.
    #
    # NOT "point the viewport at the full rect". That was the first version,
    # it was two words, it shipped, and it fixed the borders by DELETING the
    # clip -- which is also the clip FF7 uses to hide a window's contents
    # while the box opens and closes, so dialogue text went on drawing over
    # the field after its box had shrunk away. The rect has to be scaled, not
    # replaced. `ff7nx_uiclip` anchors on those two words being stock and
    # refuses if it finds the old patch.
    #
    # Nothing else in this pass touches +0x10D9D70, so there is no ordering
    # constraint. It is last only so its log line sits with the other 16:9
    # corrections.
    if want_uiclip:
        rc |= ff7nx_uiclip.apply(dest, log=log)
    else:
        log('  2D viewport scale: OFF -- menu and dialogue boxes lose the '
            'border on whichever side faces screen centre. '
            f'({UI_CLIP_ENV}={os.environ.get(UI_CLIP_ENV, "<unset>")!r})')
    # --- the credits fade quad ------------------------------------------
    # HANDOFF-104. The intro/prelude is FF7's CREDITS mode -- a still texture,
    # music and 2D text, NOT an FMV, which is why ff7nx_moviebars never
    # covered it. Its black fade quad spans game x 0..640, so at 16:9 the side
    # margins are never repainted and the credit text FF7 stages off-screen
    # smears there. FFNx fixes this by name (src/ff7/widescreen.cpp:299,
    # "// Credits fix"); this is the same correction, x only, y untouched so
    # the quad stays full height.
    if want_credits:
        rc |= ff7nx_credits.apply(dest, log=log)
    else:
        log('  credits fade quad: OFF -- the intro fade covers only the '
            'middle 4:3 and credit text smears in the side margins. '
            f'({CREDITS_ENV}={os.environ.get(CREDITS_ENV, "<unset>")!r})')
    if rc:
        log('! field frame: one or more passes refused -- the module is '
            'whatever the passes that DID run left. Check the lines above.')
    log(f'  wrote {dest}')
    log('  PASS/FAIL on hardware: walk to the bottom of a field. No black '
        'band, characters on the ground, NPCs already there at the side '
        'edges rather than appearing.')
    return [dest] if not built else []


MOVIE_CLIP_ENV = ff7nx_movieclip.MOVIECLIP_ENV


def apply_movie_clip(sdout, dump, log=lambda *_: None, produced=()):
    """
    Clip drawing to the 4:3 picture while a movie plays, so field models stop
    being drawn in the black 16:9 margin beside an FMV. HANDOFF-80 §5.0.

    A 17-word cave in `ff7nx_cave`'s padding pool, hooked at +0x1133FE8 --
    the last instruction before `b +0x11521C0`, which is the only tail-call
    to the glScissor PLT stub in the module. While the movie's `is_playing`
    flag is set it shrinks the scissor box to the central WS_SCALE of
    whatever was asked for; otherwise the box is byte-identical.

    Derivation is in ff7nx_movieclip.py. Three things belong here:

      * It hooks a FUNCTION, not a vtable slot. Two earlier versions patched
        the driver's per-draw calls at +0x10D9F3C / +0x10D9F54 (vtable +0x188
        and +0x190). The first scaled the whole picture -- +0x188 is the
        viewport -- and the second did nothing at all, because the renderer
        actually constructed is not the class whose vtable the image lets you
        read. Hooking downstream of the dispatch removes that whole class of
        error.
      * THE BAND COMES FROM THE SHIPPED SHADER. `ff7nx_movieclip` reads
        `#define WS_SCALE` out of the `romfs/ff7/shaders/tlmain_vv.glsl` this
        very SD tree carries and bakes it in as 16.16. That file is what
        moves the picture, so it is the only honest source -- and it means
        this pass and `ff7nx_ws`'s shader can never disagree.
      * IT REFUSES ON A 4:3 BUILD. With widescreen off WS_SCALE is 1.0, the
        shader moves nothing, there is no margin, and shrinking the box would
        be a regression rather than a no-op. `clips_anything()` is checked
        here as well as in the module so the log says why.

    Runs after every other module pass, on their output, for the same reason
    they run late: it edits `exefs/main`, and the cave allocator re-checks
    that its padding holes are still zero IN THE MODULE BEING PATCHED, so it
    must see what the earlier caves actually took. It also has to run after
    `ff7nx_shaders`/`ff7nx_ws` have written the shader it reads.
    """
    # ------------------------------------------------------------------
    # RETIRED -- FINDINGS-91 §1. Refused HERE rather than left to the
    # module's default, because the default is not the last word: the GUI
    # writes SEVENTH_NX_MOVIE_CLIP from a saved checkbox on every build, and
    # a settings.json written before the retirement says `movie_clip: true`.
    # That is not hypothetical -- it is what put the stale margins back on
    # the first build after the retirement landed. A gate that any older
    # settings file can reopen is not a gate.
    #
    # `SEVENTH_NX_MOVIE_CLIP=force` still applies it, for an A/B and nothing
    # else. The word is deliberately not '1', so no checkbox can produce it.
    # ------------------------------------------------------------------
    # No longer called from the pipeline either -- 7th_heaven_nx.py dropped it
    # with the checkbox. Kept callable only so `=force` can still stage an A/B.
    if os.environ.get(MOVIE_CLIP_ENV, '').strip().lower() != 'force':
        if ff7nx_movieclip.enabled():
            log('movie 4:3 clip: RETIRED, not applied')
            log('  The scissor it narrowed also caught the PRESENTATION BLIT, '
                'so the back buffer\'s margins were never written and froze '
                'the last field frame drawn before the FMV. ff7nx_moviebars '
                'does the job instead, by PAINTING the four margins in the '
                'flip path -- last, over the finished frame -- so overhang '
                'goes under the black and no frame state changes.')
            log('  (SEVENTH_NX_MOVIE_CLIP=force overrides this for an A/B.)')
        return []
    log('! movie 4:3 clip: FORCED ON. This is the retired scissor patch and '
        'it WILL leave stale field art in the 16:9 margins during every FMV.')
    if dump is None or not dump.nso:
        log('! movie 4:3 clip: needs exefs/main from a full game dump; '
            'skipped')
        return []
    # The 4:3 check, before anything else is logged: on a build with
    # widescreen off there is no margin for a model to be drawn in, the
    # shader's WS_SCALE is 1.0, and clipping to "the central 1.0" is a no-op
    # at best. Say so once and leave.
    ws = ff7nx_ws.ws_scale()
    if not ff7nx_movieclip.clips_anything(ws):
        log('')
        log(f'movie 4:3 clip: WS_SCALE is {ws:.8f} -- this build is 4:3, so '
            f'nothing is ever drawn outside the picture and there is nothing '
            f'to clip. Not applied.')
        return []
    dest = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID, 'exefs',
                        'main')
    fresh = {os.path.normpath(os.path.abspath(p)) for p in produced}
    built = os.path.normpath(os.path.abspath(dest)) in fresh
    src = dest if built else dump.nso
    log('')
    log('clipping models to the movie\'s 4:3 rect ...')
    if not built and not os.path.exists(src):
        log('! movie 4:3 clip: no module to patch')
        return []
    # REFUSAL TO CLOBBER. This pass defaults ON, which every other module pass
    # does not, so it is the one that can find itself running alone: if no
    # other pass produced exefs/main this run, `src` is the DUMP's stock
    # module, and writing that over an sdout/main a previous build patched
    # silently reverts 60 FPS, the field buffer and everything else. Same
    # check, and the same wording, as apply_field_bg.
    if not built and os.path.exists(dest):
        try:
            same = (os.path.getsize(dest) == os.path.getsize(dump.nso)
                    and open(dest, 'rb').read() == open(dump.nso, 'rb').read())
        except OSError:
            same = False
        if not same:
            log(f'! movie 4:3 clip: {dest}')
            log('  already holds a module this build did not produce -- most '
                'likely a patched one from an earlier run. Basing on the '
                "dump's stock copy would silently throw those patches away, "
                'so nothing was written.')
            log('  Turn the 60 FPS switch on so the passes run together, or '
                'delete sdout/ and rebuild.')
            return []
    log(f'  base main   {src}'
        + ('   (previous patch output)' if built else '   (from dump)'))
    tmp = dest + '.movieclip-tmp'
    try:
        ok = ff7nx_movieclip.apply_to_nso(src, tmp, log)
    except Exception as exc:                                   # noqa: BLE001
        log(f'! movie 4:3 clip: {type(exc).__name__}: {exc}')
        ok = False
    if not ok:
        if os.path.exists(tmp):
            os.remove(tmp)
        log('! movie 4:3 clip: NOT applied -- the rest of the build is still '
            'valid, models just keep spilling into the margin during FMVs.')
        return []
    os.replace(tmp, dest)
    log(f'  wrote {dest}')
    log('  PASS/FAIL on hardware: Reactor 1 explosion, Cloud on the left '
        'margin. Sliced at the movie edge = the scissor is honoured.')
    return [dest] if not built else []


def _tree_snapshot(root):
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            out.append(os.path.join(dirpath, f))
    return out


def _find_base_exe(archive_paths, dump=None):
    env = os.environ.get('SEVENTH_NX_EXE')
    if env and os.path.exists(env):
        return env
    # A full dump carries the game's own exe at
    # romfs/ff7/resources/ff7_1.02/ff7_en. Use it: it is the build the console
    # actually ships and, per FINDINGS-4, its code is byte-identical to the PC
    # 1.02 build at every virtual address, so nothing is lost by preferring
    # the file that came with the data everything else is being read from.
    if dump is not None and dump.exe:
        return dump.exe
    # This used to say: "prefer a PC exe over a stray Switch ff7_en, because
    # the Switch's own trimmed exe has everything at different addresses
    # (verified by byte diff)."
    #
    # THAT IS WRONG, and it kept the Switch's own executable out of every
    # build for no reason. A byte diff of two PE files compares FILE OFFSETS.
    # These two builds differ in file alignment (raw 0x400 vs 0x200) and the
    # Switch build drops the .FTS section, so naturally the raw offsets move.
    # The VIRTUAL layout -- which is the only thing a patch address means --
    # is identical:
    #
    #   .text   VA 0x401000  all 0x3B4639 stored bytes identical
    #   .rdata  VA 0x7B6000  all 0x3CC0   stored bytes identical
    #   .data   VA 0x7BA000  all 0x1E2E06 stored bytes identical
    #
    # Same ImageBase, entry point and link timestamp; sha1 of .text is equal.
    # The PC build merely stores more trailing linker padding. Every VA-based
    # patch in this tree applies to both, and `main`'s recompilation map is
    # keyed on x86 VAs so it addresses both equally.
    #
    # The order below is now just a preference, not a correctness rule: a PC
    # ff7.exe is still listed first because that is what HEXT packs and
    # Scarlet target, and it is the file most people already have.
    cands = [os.path.join(HERE, n) for n in
             ('ff7.exe', 'ff7_en_PC.exe', 'ff7_en')]
    wd = os.path.dirname(next(iter(archive_paths.values()))) if archive_paths else None
    if wd:
        cands += [os.path.join(wd, n) for n in
                  ('ff7.exe', 'ff7_en_PC.exe', 'ff7_en')]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def build_exe(exe_src, hext_paths, sdout, log, opening_fps=None):
    """
    Write romfs/ff7/resources/ff7_1.02/ff7_en from a supplied x86 exe with
    optional HEXT patches baked in. Returns the output path or None.

    - exe_src: path to a Windows x86 ff7 exe (e.g. an edited Steam/2013
      build). If omitted but HEXT is given, the caller must instead point
      SEVENTH_NX_EXE at a base exe -- HEXT alone has nothing to patch.
    - hext_paths: FFNx .hext/.txt patch files applied in order.
    - opening_fps: frame rate of the opening movie as emplaced. Used only to
      warn; see the note below on why nothing is patched for it.
    """
    if not exe_src:
        log('! exe: HEXT given but no SEVENTH_NX_EXE base exe; skipped')
        return None
    try:
        with open(exe_src, 'rb') as f:
            data = f.read()
    except OSError as exc:
        log(f'! exe: cannot read {exe_src}: {exc}')
        return None
    if not exe_patch.is_ff7_exe(data):
        log('! exe: not a recognizable x86 FF7 executable; skipped '
            '(need a Windows ff7.exe, e.g. the 2013 Steam build)')
        return None
    # NOTHING IS PATCHED HERE FOR A HIGH-FPS OPENING, AND THAT IS DELIBERATE.
    #
    # FF7's field loop compares current_movie_frame against a hardcoded 1760
    # at 0x63C2B8 (derived: main_loop -> field_main_loop ->
    # field_loop_sub_63C17F + 0x139), gated on the opening field, and a 30 fps
    # opening reaches that frame in half the wall-clock time. Scaling the
    # constant looks like the obvious fix and it is what FFNx does.
    #
    # It cannot be done HERE. ff7nx_60fps.py verifies the executable before
    # patching it, by hashing .text[0:0x3B4639] -- and 0x63C2B8 lands inside
    # that range. Writing the scaled cue makes the hash miss, identify_exe()
    # aborts, and the ENTIRE 60 FPS patch set silently fails to apply: the
    # game drops to 30 fps and every one of the battle and field fixes is
    # gone. That is a far worse outcome than an early music cue, and it is
    # not obvious from the symptom, so the constraint is written down rather
    # than left to be rediscovered.
    #
    # Anything that wants to edit .text has to happen AFTER the 60 FPS
    # patcher has run and verified, not before.
    if opening_fps and opening_fps > movie_convert.VANILLA_MOVIE_FPS:
        log('  note: the opening movie is %.6g fps, not the %g the game was '
            'built around. Scripted cues in the opening are keyed to movie '
            'FRAME NUMBERS and will fire early.'
            % (opening_fps, movie_convert.VANILLA_MOVIE_FPS))

    total_applied = 0
    by_section = {}
    pe = None
    try:
        pe = exe_patch.parse_pe(data)
    except Exception:                                          # noqa: BLE001
        pe = None

    def _section_of(va):
        if not pe:
            return None
        for name, sva, vsz, _foff, _rsz in pe['sections']:
            if sva <= va < sva + vsz:
                return name
        return '(unmapped)'

    # A HEXT FILE IS ALL OR NOTHING.
    #
    # The Switch does not run this exe's code. `exefs/main` is an ahead-of-
    # time ARM64 recompilation of all 10,952 x86 functions, generated from
    # the stock .text and shipped prebuilt, so editing .text afterwards
    # cannot change an instruction that was compiled into a different file
    # months ago. This project's own patch set is the proof: every code
    # constant it changes is an ARM64 WORD patch in `main`, and every
    # ff7_en patch it makes is .rdata or .data. It has never once patched
    # .text to change behaviour, because that does not work.
    #
    # A mod author does not write a HEXT file expecting half of it to land.
    # Enhanced Stock UI's `00-Main.txt` moves menu geometry with 106 code
    # patches and 9 data ones; apply the 9 alone and the menu is laid out
    # for a routine that was never changed. That is exactly the "scaling is
    # screwed up, most of it missing" the operator saw -- worse than the mod
    # being absent, because a stock UI at least agrees with itself.
    #
    # So a file that touches .text is skipped WHOLE. Files that are purely
    # .rdata/.data still apply -- colours, tables, strings and the 60 FPS
    # mod's compatibility flag all work.
    #
    # SEVENTH_NX_HEXT_TEXT=apply restores the old behaviour for anyone who
    # wants to experiment.
    force = os.environ.get('SEVENTH_NX_HEXT_TEXT', '').lower() == 'apply'
    skipped_files = []
    written = {}          # byte address -> (file, value) for collision report
    collisions = []
    for hp in hext_paths:
        try:
            with open(hp, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
        except OSError as exc:
            log(f'  ! hext {hp}: {exc}')
            continue
        counts = {}
        try:
            for va, _blob in exe_patch.parse_hext(text):
                s = _section_of(va)
                if s:
                    counts[s] = counts.get(s, 0) + 1
        except Exception:                                      # noqa: BLE001
            counts = {}
        for k, v in counts.items():
            by_section[k] = by_section.get(k, 0) + v
        if counts.get('.text') and not force:
            skipped_files.append((os.path.basename(hp), counts.get('.text', 0),
                                  sum(v for k, v in counts.items()
                                      if k in ('.data', '.rdata'))))
            continue
        log(f'  applying HEXT {os.path.basename(hp)} ...')
        # COLLISIONS, reported the way an archive override is.
        #
        # Last write wins, which is 7th Heaven's rule and the reason the order
        # above has to be its order too. Silence about it is the problem: two
        # mods disagreeing over one address is exactly the case where the
        # result is neither mod's intent, and it should be visible in the log
        # rather than discovered in game.
        try:
            for va, blob in exe_patch.parse_hext(text):
                for k in range(len(blob)):
                    prev = written.get(va + k)
                    if prev is not None and prev[1] != blob[k]:
                        collisions.append((va + k, prev[0],
                                           os.path.basename(hp)))
                    written[va + k] = (os.path.basename(hp), blob[k])
        except Exception:                                      # noqa: BLE001
            pass
        data, applied, _ = exe_patch.apply_hext(data, text, log)
        total_applied += applied

    if skipped_files:
        code = sum(t for _n, t, _d in skipped_files)
        data_lost = sum(d for _n, _t, d in skipped_files)
        log('  %d HEXT file(s) skipped whole: they patch x86 CODE (%d '
            'patches), which this port does not run --' % (len(skipped_files),
                                                           code))
        log('        exefs/main is a prebuilt ARM64 recompilation, so a .text '
            'edit changes nothing.')
        log('        Their %d data patch(es) are skipped WITH them on '
            'purpose: half a UI patch set is worse than none, because the '
            'geometry no longer matches the code.' % data_lost)
        log('        (SEVENTH_NX_HEXT_TEXT=apply to force them in anyway.)')
        for n, t, d in skipped_files[:8]:
            log('          %-28s %d code, %d data' % (n, t, d))
        if len(skipped_files) > 8:
            log('          ... and %d more' % (len(skipped_files) - 8))

    # WHICH SECTION a HEXT patch lands in decides whether it does anything.
    #
    # The Switch does not run this exe's code. `exefs/main` is an ahead-of-
    # time ARM64 recompilation of all 10,952 x86 functions, generated from
    # the stock .text, and constants that live inside instructions are baked
    # into that ARM64 -- which is exactly why the 60 FPS work patches ARM64
    # WORDS for them and only touches ff7_en for values in .rdata/.data.
    #
    # So a HEXT patch into .text is inert here. One into .data or .rdata is
    # not: the recompiled code reads those through the guest memory map at
    # runtime, so colours, coordinates, table values and strings all work.
    #
    # Enhanced Stock UI is the first mod where this matters at scale -- 1651
    # of its 1954 patches are code. Saying so in the log beats letting
    # someone conclude the build is broken when it is doing exactly what it
    # can.
    if collisions:
        byfile = {}
        for _va, first, second in collisions:
            byfile[(first, second)] = byfile.get((first, second), 0) + 1
        log('  hext collisions: %d byte(s) written twice with different '
            'values -- last write wins, as in 7th Heaven' % len(collisions))
        for (first, second), n in sorted(byfile.items(),
                                         key=lambda kv: -kv[1])[:6]:
            log('        %-26s overridden by %-26s (%d byte(s))'
                % (first, second, n))
    if by_section:
        log('  hext targets: %s'
            % ', '.join('%s %d' % (k, v) for k, v in sorted(by_section.items())))
    romfs_ff7 = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID,
                             ROMFS_FF7)
    dest = os.path.join(romfs_ff7, EXE_REL)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(data)
    log(f'  wrote ff7_en ({len(data):,} bytes'
        + (f', {total_applied} HEXT patches baked in' if total_applied else '')
        + ')')
    return dest


def save_settings(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=1)


def load_settings(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}