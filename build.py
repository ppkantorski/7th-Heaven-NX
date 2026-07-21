"""
Planning and building: turn a set of enabled mods into an SD-card tree.

Classification is exact rather than heuristic -- every candidate file is
matched by name against the real contents of the user's own archives.
"""
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from collections import Counter

import iro
import lgp
import p as pfile
import tex

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
ARCHIVES = {
    'char.lgp': 'data/field/char.lgp',
    'flevel.lgp': 'data/field/flevel.lgp',
    'battle.lgp': 'data/battle/battle.lgp',
    'magic.lgp': 'data/battle/magic.lgp',
    'world_us.lgp': 'data/wm/world_us.lgp',
    'menu_us.lgp': 'data/menu/menu_us.lgp',
}

MUSIC_DIR = 'data/music_ogg'

# FFNx external textures. No Switch loader exists for these.
FFNX_EXT = {'.dds', '.png', '.jpg', '.jpeg', '.bmp', '.tga', '.webp'}
META_EXT = {'.xml', '.txt', '.md', '.toml', '.cfg', '.ini', '.gif', '.html'}


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
        stamp = os.path.join(self.cache, '.iro-signature')
        want = _sig(self.path)
        if os.path.exists(stamp):
            with open(stamp) as f:
                if f.read().strip() == want:
                    self._load_manifest()
                    return False
            shutil.rmtree(self.cache, ignore_errors=True)
        log(f'extracting {self.filename} ...')
        os.makedirs(self.cache, exist_ok=True)
        written, skipped, failures = iro.extract(self.path, self.cache,
                                                 progress)
        for name, why in failures[:5]:
            log(f'  ! {name}: {why}')
        if failures:
            log(f'  ! {len(failures)} entries failed to extract')
        with open(stamp, 'w') as f:
            f.write(want)
        log(f'  {written} files extracted')
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

    def files_for(self, settings):
        """
        Yield (relative path, absolute path, option) for every selected file.
        `option` is the ModFolder that selected the file -- the unit the user
        toggles. Two files from the same option (even in different
        subdirectories like fb/char and fb/high) share it, so they are not
        treated as conflicting.
        """
        folders = iro.active_folders(self.manifest, settings) \
            if self.manifest else []
        out = []
        if not folders:
            # No folder declarations: the whole mod root is the payload.
            roots = [('', self.cache)]
        else:
            roots = [(f, os.path.join(self.cache, f)) for f in folders]
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
        self.skipped_ffnx = 0
        self.unmatched = []
        self.conflicts = []
        self.folder_conflicts = []   # (folder_a, folder_b, count)
        self.folder_of = {}          # archive -> {lowname: source folder}

    def total_portable(self):
        return (sum(len(v) for v in self.archive_files.values())
                + sum(len(v) for v in self.chunks.values())
                + len(self.music))


def build_plan(mods, settings_by_mod, catalogs, log=lambda *_: None):
    """
    mods            ordered list of enabled Mod objects (later wins)
    settings_by_mod {mod.filename: {option id: value}}
    catalogs        {archive filename: set of lowercase entry names}

    Routing is by name AND by folder. A file whose name already exists in an
    archive goes there. A file that matches nothing -- a NEW model piece that
    a mod overhaul adds -- is routed to the archive that the majority of its
    sibling files (same source folder) map to, so the pieces a model needs
    travel with it instead of being dropped. Dropping them is what leaves
    models rendering blank.
    """
    plan = Plan()

    # First pass: gather archive candidates and remember their source folder.
    # candidates: list of dicts across all mods, in application order.
    candidates = []
    for mod in mods:
        settings = settings_by_mod.get(mod.filename, {})
        picked = mod.files_for(settings)
        log(f'{mod.display_name}: {len(picked)} candidate files')
        for rel, full, option in picked:
            base = os.path.basename(rel)
            low = base.lower()
            ext = os.path.splitext(low)[1]

            if ext in META_EXT:
                continue
            if ext in FFNX_EXT:
                plan.skipped_ffnx += 1
                continue
            if ext == '.ogg':
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

            direct = next((a for a, names in catalogs.items()
                           if low in names), None)
            candidates.append({
                'mod': mod, 'rel': rel, 'base': base, 'low': low,
                'full': full, 'direct': direct,
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

    # Second pass: assign every candidate to an archive. Keep ALL versions of
    # each entry (with their source subfolder) so models can be reassembled
    # atomically afterwards; the last one is the provisional winner.
    added_new = 0
    versions = {}  # target -> {low: [(subfolder, full, mod), ...]}
    for c in candidates:
        target = c['direct'] or route_target.get(c['route'])
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
    _warn_frankenstein(plan, log)
    return plan


_HRC_PIECE = re.compile(r'^\d+[ \t]+(.+)$', re.MULTILINE)
_RSD_PIECE = re.compile(r'(?:PLY|TEX\[\d+\])=(\S+)')


def _assemble_models_atomically(plan, versions, log):
    """
    Keep model archives internally consistent.

    Some mods ship several overlapping model sets (e.g. NinoStyle's fb/char
    plus the alternate fb/high, lazaro, efryt). Merging them flat mixes a
    skeleton from one set with a mesh from another and breaks models. When an
    archive has that overlap, this collapses it to the single base set -- the
    source subfolder that provides the most skeletons -- so every model is
    self-consistent, matching a hand-built fb/char-only archive. Pieces the
    base set does not touch simply stay vanilla.

    Archives with no overlap (battle.lgp, where each character and the enemies
    live in their own non-colliding folder) are left exactly as-is.
    """
    for target, byname in versions.items():
        if target == 'flevel.lgp':
            continue
        bucket = plan.archive_files.get(target)
        if not bucket:
            continue

        # Is any entry supplied by more than one source subfolder? If not,
        # there is nothing to collapse (this is the battle.lgp case).
        overlap = any(len({s for s, _, _ in vs}) > 1 for vs in byname.values())
        skel_count = Counter()
        for low, vs in byname.items():
            if low.endswith('.hrc'):
                for sub, _, _ in {(s, None, None) for s, _, _ in vs}:
                    skel_count[sub] += 1
        if not overlap or not skel_count:
            continue

        base = skel_count.most_common(1)[0][0]

        # Classify every other subfolder by SKELETON overlap with the base:
        # - shares .hrc names with base -> an ALTERNATE version pack of the
        #   same models (lazaro, fb/high vs low variants, Dynamic Weapons
        #   character variants) -> dropped, exactly as before, so no model
        #   mixes pieces from two versions.
        # - hrc-disjoint -> a SEPARATE-models pack (fb/chocobo: chibi
        #   chocobos, fb/high: hi-res scene models). Dropping these lost
        #   whole models that the base set never provides; include them,
        #   with the base set winning any (shared-texture) name collision.
        hrcs_of = {}
        for low, vs in byname.items():
            if low.endswith('.hrc'):
                for s, _, _ in vs:
                    hrcs_of.setdefault(s, set()).add(low)
        base_hrcs = hrcs_of.get(base, set())
        all_subs = {s for vs in byname.values() for s, _, _ in vs}
        separate = sorted(
            s for s in all_subs
            if s != base and hrcs_of.get(s) and not (hrcs_of[s] & base_hrcs))
        keep_order = [base] + separate

        kept = dropped = extra = 0
        for low, vs in byname.items():
            chosen = None
            for src in keep_order:
                v = next(((f, m) for s, f, m in vs if s == src), None)
                if v is not None:
                    chosen = (src, v)
                    break
            if chosen is not None:
                sub, v = chosen
                bucket[low] = v
                plan.folder_of.setdefault(target, {})[low] = sub
                if sub == base:
                    kept += 1
                else:
                    extra += 1
            else:
                # Provided only by an alternate pack -> fall back to
                # vanilla, exactly as a base-only build would.
                if low in bucket:
                    del bucket[low]
                    plan.folder_of.get(target, {}).pop(low, None)
                    dropped += 1
        msg = (f'  {target}: using base model set "{base}" ({kept} files')
        if separate:
            msg += (f' + {extra} from separate model sets '
                    f'{", ".join(separate)}')
        msg += f'; dropped {dropped} from overlapping alternates)'
        log(msg)


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


def _convert_battle_textures(name, mod_files, van, log, folder_of=None):
    """
    Replace truecolor TEX files headed for a battle archive with paletted
    conversions (see tex.py). Results are cached by source signature.
    Non-TEX files (geometry, skeletons, animations) pass through untouched.

    Player-character files (source option under a "Mains" folder) are
    EXEMPT: the players-only build is proven pixel-perfect on hardware as
    shipped, and must stay byte-identical to the reference archive.
    Players never use the death dissolve anyway.
    """
    os.makedirs(TEXCONV_CACHE, exist_ok=True)
    folder_of = folder_of or {}
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
        # Version tag invalidates caches from older converter policies
        # (v3: black 16-color headers; v4: 16-color quantization mud).
        cache_key = ('TEXCONV-V5-' + _sig(src)
                     + ('-' + _sig(van_path) if van_path else ''))
        cached = os.path.join(TEXCONV_CACHE,
                              f'{name}.{low}.{hashlib.sha1(cache_key.encode()).hexdigest()[:16]}')
        if os.path.exists(cached):
            out[low] = (cached, mod)
            converted += 1
            continue
        try:
            new, note = tex.convert_for_battle(data, van_data)
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
    return out


def _build_model_archive(name, archive_path, mod_files, romfs, pack_lgp,
                         log, folder_of=None):
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
                                      log, folder_of)

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
        mod_files = _convert_battle_textures(name, mod_files, van, log,
                                             folder_of)

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
    # Sort entries by lowercased name, exactly as PyFF7's lgp_pack command
    # does, so the archive is byte-identical to a hand-built one (pack_lgp
    # itself does not sort -- it packs in the order it is given).
    files = sorted(filemap.items(), key=lambda kv: kv[0])
    pack_lgp(files, dest)

    # Verify: reopen and confirm every entry we asked for is present.
    chk = lgp.Archive(dest)
    missing = [low for low in mod_files if low not in chk.index]
    if missing:
        os.remove(dest)
        log(f'  ! {name}: {len(missing)} entries missing after pack; '
            'output rejected (please report)')
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
                           folder_of=None):
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
        mod_files = _convert_battle_textures(name, mod_files, van, log,
                                             folder_of)
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


def _build_flevel(archive_path, chunks, field_files, romfs, log):
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
    payloads = {}

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
            payloads[name] = archive.encode_field(raw_mod)
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
        payloads[name] = archive.encode_field(lgp.join_sections(van_secs))
        msg = f'  {name}: spliced sections {took} from mod'
        if held:
            msg += f', kept Switch-vanilla sections {held}'
        log(msg)

    for field, sections in chunks.items():
        entry = archive.index.get(field)
        if entry is None or not archive.is_field(entry):
            log(f'  ! no such field: {field}')
            continue
        parts = lgp.split_sections(archive.decompressed(entry))
        for idx, (src, _) in sorted(sections.items()):
            if not 1 <= idx <= 9:
                continue
            with open(src, 'rb') as f:
                parts[idx - 1] = f.read()
        payloads[field] = archive.encode_field(lgp.join_sections(parts))
    if chunks:
        log(f'  {len(chunks)} fields patched')

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
    log(f'  wrote flevel.lgp ({os.path.getsize(dest):,} bytes)')
    return dest


def apply_plan(plan, archive_paths, sdout, log=lambda *_: None,
               progress=lambda *_: None):
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
        log(f'building {name} ...')
        dest = _build_model_archive(name, archive_paths[name],
                                    plan.archive_files[name], romfs,
                                    pack_lgp, log,
                                    plan.folder_of.get(name))
        if dest:
            produced.append(dest)

    if do_flevel:
        progress(step, total, 'flevel.lgp')
        step += 1
        if 'flevel.lgp' in archive_paths:
            log('building flevel.lgp ...')
            dest = _build_flevel(archive_paths['flevel.lgp'], plan.chunks,
                                 flevel_fields, romfs, log)
            if dest:
                produced.append(dest)
        else:
            log('! flevel.lgp not in workingdir, skipping')

    for low, (src, _) in plan.music.items():
        dest = os.path.join(romfs, MUSIC_DIR, low)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)
        produced.append(dest)
    if plan.music:
        log(f'copied {len(plan.music)} music files')

    return produced


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