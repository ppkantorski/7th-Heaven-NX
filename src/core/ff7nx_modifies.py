#!/usr/bin/env python3
"""What a mod, or one of its options, changes in the game on the Switch.

The "Modifies" lines in the GUI. They used to name ARCHIVES -- `char.lgp`,
`music (.ogg)` -- found by a name match against the dump or, without one, by
guessing from folder names (`/battle` -> battle.lgp). That told the player
little and was often wrong: Cosmo Memory's ~1,400 sound files read as
"music", Cosmos Limit Break's 18,000 HD backgrounds read as nothing at all
(FFNx DDS were simply skipped), and a PC-only option looked like it did
something.

This walks the mod's own file list with the SAME routing build_plan uses,
in the same order, and reports the game CONTENT each file lands in: field
backgrounds, battle models, sound effects, voice acting. Files the Switch
build has no use for are counted separately, so an option whose files are
all PC-only says so.

It is read-only and has no side effects: it reads the .iro directory
listing (no extraction), the manifest, and the dump's name catalogs. It
never runs build_plan, whose passes set module state a build depends on.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os
from collections import Counter

import build
import iro
import movies as movie_convert
import voicemod
import ff7nx_chardds
import ff7nx_difix
import ff7nx_esui
import ff7nx_fieldbg
import ff7nx_idleanim
import ff7nx_minigametex
import ff7nx_nitpick
import ff7nx_spelltex
import ff7nx_titlemusic


# Display order. A category appears at most once, in this order, whatever
# order the files came in.
ORDER = (
    'Field backgrounds',
    'Field dialogue',
    'Field models',
    'Field props (HD)',
    'Field character animations',
    'Battle models',
    'Battle backgrounds',
    'Battle data',
    'Spell effects',
    'World map',
    'Highwind deck',
    'Minigames',
    'Menus',
    'Menus & battle UI',
    'Game text & data',
    'Credits',
    'Movies',
    'Movie audio',
    'Music',
    'Sound effects',
    'Ambient sound',
    'Voice acting',
    'Game code',
    'Controller input',
)

# Where an archive entry lands, in player terms.
ARCHIVE_CONTENT = {
    'char.lgp': 'Field models',
    # A PC field file contributes only its palette and background sections
    # (build.SAFE_MOD_SECTIONS); script, camera, walkmesh stay Switch-vanilla.
    'flevel.lgp': 'Field backgrounds',
    'battle.lgp': 'Battle models',
    'magic.lgp': 'Spell effects',
    'world_us.lgp': 'World map',
    'menu_us.lgp': 'Menus',
    'cr_us.lgp': 'Credits',
}
for _a in build.ARCHIVES:
    if _a.startswith('high-'):
        ARCHIVE_CONTENT[_a] = 'Highwind deck'
for _arcs in build.MINIGAME_DIRECT_FOLDERS.values():
    for _a in _arcs:
        ARCHIVE_CONTENT[_a] = 'Minigames'

UNUSED = 'unused'       # the bucket for files the Switch build drops


def _content(archive):
    return ARCHIVE_CONTENT.get(archive, archive)


def _dirs(rel):
    return [p.lower() for p in os.path.dirname(rel).split('/') if p]


def _selected(mod, folders):
    """The mod's entries under `folders` (forward slashes), as files_for
    would see them once extracted: mod.xml, dot files and preview folders
    out; with no folders declared at all, the whole mod."""
    declared = bool(mod.manifest and mod.manifest.folders)
    if folders is None:
        prefixes = None
    else:
        prefixes = tuple(f.replace('\\', '/').strip('/').lower() + '/'
                         for f in folders if f)
        if not prefixes:
            return []
    out = []
    for raw in mod.entries():
        rel = raw.replace('\\', '/')
        low = rel.lower()
        base = low.rsplit('/', 1)[-1]
        if not base or base == 'mod.xml' or base.startswith('.'):
            continue
        if any(p in ('preview', 'previews') for p in low.split('/')[:-1]):
            continue
        if prefixes is not None:
            if not low.startswith(prefixes):
                continue
        elif declared:
            continue
        out.append(rel)
    return out


def _baseline_hext(mod):
    """HEXT outside every declared folder: the mod's root `hext/` folder or
    a root-level .hext. build_plan always takes these."""
    declared = [f.replace('\\', '/').strip('/').lower() + '/'
                for f, _c in (mod.manifest.folders if mod.manifest else [])]
    n = 0
    for raw in mod.entries():
        low = raw.replace('\\', '/').lower()
        if any(low.startswith(d) for d in declared):
            continue
        parts = low.split('/')
        if (low.endswith('.hext') and len(parts) <= 2 and
                (len(parts) == 1 or parts[0] == 'hext')) or \
                (parts[0] == 'hext' and low.endswith('.txt')):
            n += 1
    return n


def _soundtrack(rels):
    """build_plan's per-mod test: vgmstream/ is music unless the mod ships
    more FFNx sound (sfx/ambient/voice/movies) than vgmstream files. Decided
    over the mod's whole selection, never over one option's folders."""
    vgm = ffnx_sound = 0
    for rel in rels:
        if not rel.lower().endswith('.ogg'):
            continue
        d = _dirs(rel)
        if build.VGMSTREAM_DIR in d:
            vgm += 1
        elif any(p in build.FFNX_AUDIO_DIRS for p in d):
            ffnx_sound += 1
    return not (vgm and ffnx_sound > vgm)


def classify(mod, rels, catalogs, field_bg=True, take_vgm=None):
    """Counter {category: files} for `rels`, routed as build_plan routes
    them, plus whether any category had to be GUESSED (no dump catalogs).
    Files the Switch build does not use count under UNUSED."""
    out = Counter()
    guessed = False
    catalogs = catalogs or {}
    mid = (getattr(mod.manifest, 'mod_id', '') or '').lower()
    echo = mid == build.ECHO_S_MOD_ID
    syw_pack = ff7nx_minigametex.is_pack(mod)
    chardds_on = ff7nx_chardds.enabled()
    stage_map = None

    nitpick = ff7nx_nitpick.handles(mod)
    if take_vgm is None:
        take_vgm = _soundtrack(rels)

    pending = []          # archive candidates: (route, low, direct)
    for rel in rels:
        base = rel.rsplit('/', 1)[-1]
        low = base.lower()
        ext = os.path.splitext(low)[1]
        d = _dirs(rel)

        if syw_pack and ext == '.dds':
            asset = ff7nx_minigametex._asset(rel)
            if asset:
                out['Highwind deck' if asset[0] == 'high'
                    else 'Minigames'] += 1
            else:
                out[UNUSED] += 1
            continue
        if build.sfxmod.SFX_DIR in d and (
                low == build.sfxmod.CONFIG_NAME or ext == '.ogg'):
            out['Sound effects'] += 1
            continue
        if build.ambientmod.AMBIENT_DIR in d and (
                low == build.ambientmod.CONFIG_NAME or ext == '.ogg'):
            out['Ambient sound'] += 1
            continue
        if echo and len(d) >= 1 and d[-1] == 'flevel.lgp' and \
                '.' not in low and (not catalogs or low in
                                    catalogs.get('flevel.lgp', ())):
            out['Field dialogue'] += 1
            continue
        if echo and d == ['materia tutorial', 'movies'] and low == 'mkup.avi':
            out['Movies'] += 1
            continue
        if echo and 'kernel' in d:
            out['Game text & data' if build._echo_s_kernel_name(mod, rel)
                else UNUSED] += 1
            continue
        if ext in build.META_EXT:
            if 'hext' in d and ext == '.txt':
                out['Game code'] += 1
            continue
        if ext == '.hext':
            out['Game code'] += 1
            continue
        if ext == '.dds' and 'battle' in d and build.BATTLE_STAGE_RE.search(low):
            if stage_map is None:
                try:
                    stage_map = build._battle_bg_dds_map()
                except Exception:                              # noqa: BLE001
                    stage_map = {}
            m = build.BATTLE_STAGE_RE.search(low)
            key = '%02d_%02d' % (int(m.group(1)), int(m.group(2)))
            out['Battle backgrounds' if key in stage_map else UNUSED] += 1
            continue
        if ext == '.dds' and 'world' in d:
            parsed = build._world_dds_name(low)
            if parsed and parsed[1] == 0:
                names = catalogs.get('world_us.lgp')
                if names is None:
                    guessed = True
                    out['World map'] += 1
                    continue
                if parsed[0] in names:
                    out['World map'] += 1
                    continue
        if ext == '.dds' and 'magic' in d:
            parsed = ff7nx_spelltex._parse_dds_name(base)
            if parsed:
                names = catalogs.get('magic.lgp')
                if names is None:
                    guessed = True
                    out['Spell effects'] += 1
                    continue
                if (parsed[0] + '.tex') in names:
                    out['Spell effects'] += 1
                    continue
        if nitpick and ext == '.dds' and d and d[-1] in ('menu', 'cr'):
            # the title art, Red XIII's portrait and the attract-mode
            # credits: converted to TEX by ff7nx_nitpick
            out['Menus' if d[-1] == 'menu' else 'Credits'] += 1
            continue
        if ext == '.dds' and 'field' in d:
            # Cosmos Limit Break's HD backgrounds, read straight out of the
            # .iro by the field-background pass when it is on
            out['Field backgrounds' if field_bg else UNUSED] += 1
            continue
        if ext == '.dds' and d and d[-1] == 'char' and chardds_on and \
                ff7nx_chardds._DDS.fullmatch(low):
            out['Field props (HD)'] += 1
            continue
        if ext in build.FFNX_EXT:
            out[UNUSED] += 1
            continue
        if ext in movie_convert.MOVIE_EXT:
            out['Movies'] += 1
            continue
        holder = next((p for p in d if p in build.LOOSE_DIRS), None)
        if holder and (ext == '.bin' or (holder == 'battle' and ext == '.dat')):
            out['Battle data' if holder == 'battle'
                else 'Game text & data'] += 1
            continue
        if ext == '.ogg':
            holder = next((p for p in d if p in build.FFNX_AUDIO_DIRS), None)
            if holder == 'voice':
                out['Voice acting' if voicemod.classify_voice_path(rel)
                    else UNUSED] += 1
            elif holder == 'movies':
                out['Movie audio' if movie_convert.movie_audio_kind(rel)
                    else UNUSED] += 1
            elif holder:
                out[UNUSED] += 1
            elif build.VGMSTREAM_DIR in d and low != \
                    ff7nx_titlemusic.TRACK and not take_vgm:
                out[UNUSED] += 1
            else:
                out['Music'] += 1
            continue
        if '.chunk.' in low:
            try:
                section = int(low.rpartition('.')[2])
            except ValueError:
                section = None
            out['Field backgrounds' if section in build.SAFE_MOD_SECTIONS
                else UNUSED] += 1
            continue
        if low in build.ARCHIVES:
            out[UNUSED] += 1          # a whole PC archive, refused
            continue
        hits = [a for a, names in catalogs.items() if low in names]
        hits = build._minigame_direct_hits(rel, hits)
        pending.append((os.path.dirname(rel), rel.lower(),
                        hits[0] if hits else None))

    # Unmatched entries follow their folder's majority, as in build_plan;
    # with no catalogs at all, the folder-name hints stand in (a guess).
    votes = {}
    for route, _low, direct in pending:
        if direct:
            votes.setdefault(route, Counter())[direct] += 1
    for route, low, direct in pending:
        target = direct
        if target is None and route in votes:
            target = votes[route].most_common(1)[0][0]
        if target is None and not catalogs:
            target = next((arc for key, arc in build.FOLDER_ARCHIVE_HINTS
                           if low.startswith(key) or '/' + key in low), None)
            if target is not None:
                guessed = True
        out[_content(target) if target else UNUSED] += 1
    return out, guessed


# Mods applied by their own pass rather than file by file. What they change
# is fixed by that pass, not by their files.
def _special(mod):
    if ff7nx_esui.handles(mod):
        return ['Menus & battle UI']
    if ff7nx_idleanim.handles(mod):
        return ['Field character animations']
    if ff7nx_difix.handles(mod):
        return ['Controller input']
    return None


_cache = {}


def _summarise(mod, folders, catalogs, field_bg, baseline, take_vgm=None):
    key = (mod.path, tuple(folders) if folders is not None else None,
           bool(catalogs), field_bg, baseline, take_vgm)
    hit = _cache.get(key)
    if hit is None:
        counts, guessed = classify(mod, _selected(mod, folders), catalogs,
                                   field_bg, take_vgm)
        if baseline:
            n = _baseline_hext(mod)
            if n:
                counts['Game code'] += n
        unused = counts.pop(UNUSED, 0)
        cats = sorted(counts, key=lambda c: ORDER.index(c)
                      if c in ORDER else len(ORDER))
        hit = _cache[key] = (cats, unused, guessed)
    return hit


def for_mod(mod, settings, catalogs=None, field_bg=True):
    """([category, ...], unused file count, guessed) for the mod as
    currently configured."""
    if not mod.manifest:
        return [], 0, False
    special = _special(mod)
    if special:
        return special, 0, False
    declared = bool(mod.manifest.folders)
    folders = iro.active_folders(mod.manifest, settings) if declared else None
    return _summarise(mod, folders, catalogs, field_bg, True)


# FFNx.toml keys a mod's <FFNxConfig> can set that this port acts on, and
# what they change. Every other key is read by FFNx on the PC only.
FFNX_KEYS = {
    'external_sfx_always_centered': 'Sound effects',   # ff7nx_sfxcenter
}


def _ffnx_keys(manifest, option_id):
    """FFNx.toml keys whose <FFNxConfig> entry is gated on this option."""
    want = option_id.casefold()
    keys = set()
    for block in iro._FFNX_BLOCK.findall(manifest.raw):
        for key, attrs, _value in iro._FFNX_FLAG.findall(block):
            if any(k.casefold() == want for k, _v in iro._ATTRS.findall(attrs)):
                keys.add(key)
    return keys


def for_option(mod, option, settings=None, catalogs=None, field_bg=True):
    """The same for every folder this option gates, across all its values.
    An option that gates no folder can still set FFNx.toml keys (or, for
    Echo-S, a runtime setting); it is described by those instead."""
    m = mod.manifest
    if not m:
        return [], 0, False
    special = _special(mod)
    if special:
        return special, 0, False
    folders = [f for f, cond in m.folders
               if option.id in build._cond_option_ids(cond)]
    if folders:
        everything = _selected(mod, iro.active_folders(m, settings or {})
                               if m.folders else None)
        return _summarise(mod, folders, catalogs, field_bg, False,
                          _soundtrack(everything))
    if (m.mod_id or '').lower() == build.ECHO_S_MOD_ID and \
            option.id.casefold() == 'auto':
        return ['Field dialogue'], 0, False     # the auto-advance hook
    keys = _ffnx_keys(m, option.id)
    cats = sorted({FFNX_KEYS[k] for k in keys if k in FFNX_KEYS},
                  key=ORDER.index)
    if cats:
        return cats, 0, False
    return [], len(keys), False                 # PC-only FFNx settings


def text(cats, unused, guessed):
    """One display line: 'Music, Movie audio', '(est.)' when a category
    was guessed without the dump, or a plain statement when nothing of it
    reaches the Switch."""
    if not cats:
        return 'Nothing on the Switch (PC only)' if unused else ''
    line = ', '.join(cats)
    if guessed:
        line += ' (est.)'
    return line
