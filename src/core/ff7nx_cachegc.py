#!/usr/bin/env python3
"""ff7nx_cachegc.py -- BUILD 754: clearing cache entries nothing uses any more.

WHAT PILES UP
=============
Every conversion cache here is keyed by its inputs: content hashes, source
size+mtime, settings. A key that changes writes a NEW entry. Nothing ever
deleted the old one, so every mod update, option change, cap change or
packer revision left a full generation behind. On the user's machine after
~180 builds: cache/_fieldlzs 87 GB (129,268 entries for ~700 fields),
cache/movies 26 GB (up to four encodes per movie). On top of that,
cache/<mod> holds the extraction of mods that are no longer installed, or
that are installed as a folder now (a folder mod is its own cache).

HOW STALE IS DECIDED
====================
- Mod extractions: cache/<stem> holding an `.iro-signature` whose
  mods/<stem>.iro no longer exists. Always cleared, at the end of a build.
- Conversion caches: by USE, not by age. For the length of a build, an
  audit hook (sys.addaudithook) records every file under cache/ that is
  opened, linked, copied or renamed, or named on a subprocess command line.
  A cache hit always reads its entry (or links it into sdout, for movies),
  so after a build that rebuilt everything, the entries it did not touch
  are the stale ones.

  This runs only after a FRESH build: one that finished and kept no archive
  from a previous run (no fast-reuse switch, no fingerprint hit). Any kept
  archive means its entries went unread, so nothing is swept. A cache
  directory the build never touched at all is left alone too: its feature
  was off this time.

BUILD 848: PER ARCHIVE. "Every archive rebuilt" almost never happens once
the archive fingerprints keep unchanged archives, so the sweep above almost
never ran and every mod update, option change or code update left a whole
generation behind (users reported cache/ over 60 GB). A conversion cache
that feeds known archives (ARCHIVE_DEPS) is now swept whenever THOSE
archives were rebuilt this run: _fieldlzs after any flevel.lgp rebuild,
_spell_dds after any magic.lgp rebuild. The rest keep the old rule.

BUILD 848: COMPACT (opt-in, Settings > Storage, SEVENTH_NX_COMPACT_CACHE=1).
After a successful build the mod extractions (cache/<stem> of each .iro,
~15 GB with the full mod list) are deleted; the next build re-extracts them
from the .iro. Re-extracted files would get new mtimes, and every archive
fingerprint and conversion-cache key reads size+mtime, so each compacted
extraction keeps a small COMPACT_MARK file: the .iro signature it was made
from and every file's (size, mtime_ns). build.Mod.ensure_extracted restores
those mtimes when the .iro is unchanged, so a compacted mod costs only the
extraction time. Folder mods are never touched.

Deleting a cache entry can only cost time (it is rebuilt when needed),
never correctness. SEVENTH_NX_CACHE_GC=0 turns the sweep off;
SEVENTH_NX_CACHE_GC=report lists what would go without deleting.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import nxpaths  # BUILD 755: project paths (src/nxpaths.py)
import os
import json
import re
import shutil
import sys

ENV = 'SEVENTH_NX_CACHE_GC'
ROOT = os.path.realpath(os.path.join(nxpaths.ROOT,
                                     'cache'))

# {directory under cache/: regex a sweepable entry's path (relative to that
# directory, '/' separated) must match, or None for every file}. Only caches
# whose hits were checked to read their entry. Not here: _vanilla (extracted
# game archives, checked by existence), _archive_fp / _sdout_manifest /
# _fieldsteps_fp (build state), lzs_native (a compiled library).
SWEEP = {
    '_fieldlzs': re.compile(r'^[0-9a-f]{40}$'),
    # BUILD 756: also the pre-`loopclose3` loop re-timings, which nothing
    # reads any more.
    'movies': re.compile(r'^[^/]+\.(?:[0-9a-f]{20}\.mp4|loopclose\.mkv)$'),
    '_texconv': None,
    '_texcap': None,
    '_pfix': None,
    '_debleed': None,
    '_spell_dds': None,
    # BUILD 756: the SFX re-encodes. sfxmod._encode_cached reads its entry
    # on every hit; the key carries the source ogg's size+mtime, so a mod
    # update or a key change (the `-plain` suffix) left a whole generation.
    'sfx': re.compile(r'^[^/]+\.wav$'),
    'sfx-battle': re.compile(r'^[^/]+\.wav$'),
    'sfx-fieldsteps': re.compile(r'^[^/]+\.wav$'),
    'sfx-rotations': re.compile(r'^[^/]+\.wav$'),
    'sfx-worldsteps': re.compile(r'^[^/]+\.wav$'),
}
# Not _voice_ogg: its step may verify only the entries it re-encodes, and a
# wrongly swept voice cache costs a long re-encode.

# BUILD 848: the archives a conversion cache feeds. Listed here, the cache is
# swept whenever all of these were rebuilt this run, whatever happened to the
# other archives. Only caches read by exactly these archives' builds:
#   _fieldlzs   build._encode_field_cached, called only from the flevel
#               passes (_bg_compact_only, _convert_field_backgrounds,
#               _lift_depth1_payloads, _bake_widescreen_ranges)
#   _spell_dds  ff7nx_spelltex.convert, magic.lgp's in-place rebuild only
ARCHIVE_DEPS = {
    '_fieldlzs': frozenset(('flevel.lgp',)),
    '_spell_dds': frozenset(('magic.lgp',)),
}

COMPACT_ENV = 'SEVENTH_NX_COMPACT_CACHE'
COMPACT_MARK = '.compact-mtimes'

_TOUCHED = set()
_ON = [False]
_HOOKED = [False]


def mode():
    v = os.environ.get(ENV, '').strip().lower()
    if v in ('0', 'off', 'no', 'false'):
        return 'off'
    if v == 'report':
        return 'report'
    return 'on'


def compact_enabled():
    return os.environ.get(COMPACT_ENV, '').strip().lower() in (
        '1', 'on', 'yes', 'true')


def read_compact_mark(cache_dir):
    """(signature, {relpath: (size, mtime_ns)}) a compacted extraction
    left, or None."""
    try:
        with open(os.path.join(cache_dir, COMPACT_MARK),
                  encoding='utf-8') as fh:
            data = json.load(fh)
        files = {k: (int(v[0]), int(v[1]))
                 for k, v in data.get('files', {}).items()}
        return str(data['sig']), files
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def restore_compact_mtimes(cache_dir, files):
    """Give each freshly extracted file the mtime it had before the
    compact, when its size still matches. Returns (kept, other)."""
    kept = other = 0
    for base, _dirs, names in os.walk(cache_dir):
        for fn in names:
            if fn.startswith('.'):
                continue
            path = os.path.join(base, fn)
            old = files.get(os.path.relpath(path, cache_dir))
            try:
                if old is not None and os.path.getsize(path) == old[0]:
                    os.utime(path, ns=(old[1], old[1]))
                    kept += 1
                else:
                    other += 1
            except OSError:
                other += 1
    return kept, other


def compact_mod_caches(mods_dir, log=lambda *_: None, act=True):
    """Delete every current .iro extraction, keeping its COMPACT_MARK.
    Returns bytes freed (or that would be)."""
    freed = 0
    try:
        names = sorted(os.listdir(ROOT))
    except OSError:
        return 0
    for name in names:
        path = os.path.join(ROOT, name)
        if name.startswith(('_', '.')) or not os.path.isdir(path):
            continue
        if not os.path.isfile(os.path.join(mods_dir, name + '.iro')):
            continue
        stamp = os.path.join(path, '.iro-signature')
        try:
            with open(stamp, encoding='utf-8') as fh:
                sig = fh.readline().strip()
        except OSError:
            continue                # not extracted (only listed), or compacted
        if not sig:
            continue
        files, total = {}, 0
        for base, _dirs, fns in os.walk(path):
            for fn in fns:
                p = os.path.join(base, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                total += st.st_size
                if not fn.startswith('.'):
                    files[os.path.relpath(p, path)] = (st.st_size,
                                                       st.st_mtime_ns)
        log('  cache: %s the extraction of %s (%s) -- re-extracted from the '
            '.iro by the next build' % ('removed' if act else 'would remove',
                                        name, _fmt(total)))
        freed += total
        if not act:
            continue
        tmp = os.path.join(path, COMPACT_MARK + '.part')
        try:
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump({'sig': sig, 'files': files}, fh)
            os.replace(tmp, os.path.join(path, COMPACT_MARK))
        except OSError as exc:
            log('  ! cache: %s left as is: %s' % (name, exc))
            continue
        try:
            os.remove(stamp)        # first: an interrupted compact re-extracts
        except OSError:
            continue
        for entry in os.listdir(path):
            if entry == COMPACT_MARK:
                continue
            p = os.path.join(path, entry)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    os.remove(p)
                except OSError:
                    pass
    return freed


def _note(p):
    try:
        p = os.fsdecode(p)
    except TypeError:
        return
    if not isinstance(p, str) or 'cache' not in p:
        return
    p = os.path.abspath(p)
    if p.startswith(ROOT + os.sep):
        _TOUCHED.add(p)
    else:
        rp = os.path.realpath(p)
        if rp.startswith(ROOT + os.sep):
            _TOUCHED.add(rp)


def _hook(event, args):
    if not _ON[0]:
        return
    try:
        if event == 'open':
            if isinstance(args[0], (str, bytes, os.PathLike)):
                _note(args[0])
        elif event in ('os.link', 'os.symlink', 'shutil.copyfile',
                       'shutil.copymode', 'os.rename', 'os.replace'):
            for a in args[:2]:
                if isinstance(a, (str, bytes, os.PathLike)):
                    _note(a)
        elif event == 'subprocess.Popen':
            cmd = args[1]
            if isinstance(cmd, (list, tuple)):
                for a in cmd:
                    if isinstance(a, (str, bytes, os.PathLike)):
                        _note(a)
            elif isinstance(cmd, (str, bytes)):
                for a in os.fsdecode(cmd).split():
                    _note(a)
    except Exception:                                           # noqa: BLE001
        pass


def start():
    """Begin recording cache use for this build."""
    _TOUCHED.clear()
    if not _HOOKED[0]:
        sys.addaudithook(_hook)
        _HOOKED[0] = True
    _ON[0] = True


def stop():
    _ON[0] = False


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _fmt(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return '%.1f %s' % (n, unit) if unit != 'B' else '%d B' % n
        n /= 1024.0
    return '%d B' % n


def orphaned_mod_caches(mods_dir):
    """[(path, bytes)] of cache/<stem> extractions whose .iro is gone.

    BUILD 756: a mod that was only ever LISTED, never built, has no
    `.iro-signature` -- discover_mods pulls just its mod.xml into
    cache/<stem>/ so the UI can show it. Removing the .iro left that folder
    behind forever ([HighDetailWorldMap]). A top-level mod.xml marks a mod
    extraction just as well; no conversion cache ever holds one."""
    out = []
    try:
        names = os.listdir(ROOT)
    except OSError:
        return out
    for name in sorted(names):
        path = os.path.join(ROOT, name)
        if name.startswith(('_', '.')) or not os.path.isdir(path):
            continue
        if not (os.path.isfile(os.path.join(path, '.iro-signature')) or
                os.path.isfile(os.path.join(path, 'mod.xml')) or
                os.path.isfile(os.path.join(path, COMPACT_MARK))):
            continue
        if os.path.isfile(os.path.join(mods_dir, name + '.iro')):
            continue
        total = 0
        for base, _d, files in os.walk(path):
            total += sum(_size(os.path.join(base, f)) for f in files)
        out.append((path, total))
    return out


def stale_entries():
    """{dir: [(path, bytes)]} of untouched entries, for directories this
    build touched at all."""
    out = {}
    for d, pat in SWEEP.items():
        root = os.path.join(ROOT, d)
        if not os.path.isdir(root):
            continue
        files = []
        if d == 'movies':
            for f in os.listdir(root):
                p = os.path.join(root, f)
                if os.path.isfile(p):
                    files.append((p, f))
        else:
            for base, _dirs, names in os.walk(root):
                for f in names:
                    p = os.path.join(base, f)
                    files.append((p, os.path.relpath(p, root).replace(os.sep, '/')))
        touched = [p for p, _r in files if p in _TOUCHED]
        if not touched:
            continue
        stale = [(p, _size(p)) for p, rel in files
                 if p not in _TOUCHED and not rel.startswith('.')
                 and (pat is None or pat.match(rel))]
        if stale:
            out[d] = stale
    return out


def sweep_orphans(mods_dir, log=lambda *_: None):
    """BUILD 756: at launch, clear the extractions of mods that are no longer
    installed. Needs no build: whether mods/<stem>.iro exists is the whole
    test. Returns bytes freed (or that would be, in report mode)."""
    m = mode()
    if m == 'off':
        return 0
    act = m == 'on'
    freed = 0
    for path, n in orphaned_mod_caches(mods_dir):
        log('cache: %s %s (%s) -- its mod is no longer installed as an .iro'
            % ('removed' if act else 'would remove', os.path.basename(path), _fmt(n)))
        if act:
            shutil.rmtree(path, ignore_errors=True)
        freed += n
    return freed


def sweep(mods_dir, kept, log=lambda *_: None, compact=False):
    """End-of-build cleanup. `kept`: the archives this build kept from an
    earlier run (a bool is read as the old `fresh`: True = none kept).
    `compact`: also delete the mod extractions (BUILD 848). Returns bytes
    freed (or that would be, in report mode)."""
    stop()
    if isinstance(kept, bool):
        kept = set() if kept else None
    kept = set(kept) if kept is not None else None
    m = mode()
    if m == 'off':
        return 0
    act = m == 'on'
    freed = 0
    orphans = orphaned_mod_caches(mods_dir)
    for path, n in orphans:
        log('  cache: %s %s (%s) -- its mod is no longer installed as an .iro'
            % ('removed' if act else 'would remove', os.path.basename(path), _fmt(n)))
        if act:
            shutil.rmtree(path, ignore_errors=True)
        freed += n
    held = []
    for d, stale in sorted(stale_entries().items()):
        deps = ARCHIVE_DEPS.get(d)
        if kept is None or (kept if deps is None else (deps & kept)):
            held.append(d)
            continue
        n = sum(b for _p, b in stale)
        log('  cache: %s %d unused entr%s in %s (%s)'
            % ('removed' if act else 'would remove', len(stale),
               'y' if len(stale) == 1 else 'ies', d, _fmt(n)))
        if act:
            for p, _b in stale:
                try:
                    os.remove(p)
                except OSError:
                    pass
            root = os.path.join(ROOT, d)
            for base, dirs, files in os.walk(root, topdown=False):
                if base != root and not dirs and not files:
                    try:
                        os.rmdir(base)
                    except OSError:
                        pass
        freed += n
    if held:
        log('  cache: %s not swept -- an archive they feed was kept from an '
            'earlier run, so their entries were not all read'
            % ', '.join(held))
    if compact:
        freed += compact_mod_caches(mods_dir, log, act)
    if freed:
        log('  cache: %s %s%s' % ('freed' if act else 'would free', _fmt(freed),
                                  '' if act else ' (%s=report)' % ENV))
    return freed
