#!/usr/bin/env python3
"""
audit_mods.py -- is everything in your .iro mods actually landing somewhere?

    python3 audit_mods.py --mods mods --workingdir workingdir
    python3 audit_mods.py --mods mods --workingdir workingdir --archive magic.lgp

Answers one question, per mod and per archive: of the files this mod ships, how
many match an entry that exists in your real archives, how many are new files
that would be routed by folder, and how many match NOTHING and are therefore
dropped.

`7th_heaven_nx.py` already prints an `unrecognised` count at build time, but it
prints the first five and moves on, and a file that is silently dropped looks
exactly like a file that was applied. This lists every one, grouped, so "is it
all being applied?" has an answer instead of a vibe.

It reads the .iro entry table and your archive headers only -- nothing is
extracted, nothing is written, and it does not need the modules build.py needs.

WHAT COUNTS AS FINE
===================
  matched      the name exists in that archive; it will replace it.
  new-in-folder  the name is new, but its sibling files in the same source
                 folder route to a known archive, so it travels with them.
                 Model overhauls legitimately add pieces this way.
  FFNx-only    .dds/.png texture replacements and shaders. The Switch has no
               FFNx texture loader, so these are skipped by design, not lost.
  DROPPED      matches nothing and has no sibling consensus. This is the only
               category that means something is missing.
"""
import argparse
import os
import re
import struct
import sys
from collections import Counter, defaultdict

ARCHIVES = {
    'char.lgp': 'data/field/char.lgp',
    'flevel.lgp': 'data/field/flevel.lgp',
    'battle.lgp': 'data/battle/battle.lgp',
    'magic.lgp': 'data/battle/magic.lgp',
    'world_us.lgp': 'data/wm/world_us.lgp',
    'menu_us.lgp': 'data/menu/menu_us.lgp',
}
FOLDER_HINTS = (
    ('flevel', 'flevel.lgp'), ('char', 'char.lgp'), ('enem', 'battle.lgp'),
    ('mains', 'battle.lgp'), ('monster', 'battle.lgp'), ('summon', 'magic.lgp'),
    ('battle', 'battle.lgp'), ('magic', 'magic.lgp'), ('world', 'world_us.lgp'),
    ('menu', 'menu_us.lgp'),
)
FFNX_EXT = {'.dds', '.png', '.jpg', '.tga', '.psd', '.frag', '.vert', '.sh',
            '.toml', '.cfg', '.md', '.txt', '.xml', '.lua'}
MUSIC_EXT = {'.ogg', '.mp3', '.flac', '.wav'}


def load_catalogs(workingdir):
    """{archive: set(lowercase entry names)} straight from the LGP headers."""
    cats = {}
    for name, rel in ARCHIVES.items():
        path = os.path.join(workingdir, rel)
        if not os.path.exists(path):
            continue
        with open(path, 'rb') as f:
            if not f.read(12).endswith(b'SQUARESOFT'):
                continue
            count = struct.unpack('<i', f.read(4))[0]
            names = set()
            for _ in range(count):
                e = f.read(27)
                if len(e) < 27:
                    break
                names.add(e[:20].split(b'\0')[0].decode('ascii',
                                                        'replace').lower())
            cats[name] = names
    return cats


def route_by_folder(rel):
    low = rel.replace('\\', '/').lower()
    for frag, arch in FOLDER_HINTS:
        if frag in low:
            return arch
    return None


def audit(iro_path, cats, verbose_drops):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import iro
    entries = iro.list_entries(iro_path)
    # .iro entry paths use WINDOWS separators. os.path.basename does not split
    # on backslashes off Windows, so every basename comes back as the whole
    # path and nothing ever matches an archive entry -- which reads as "this
    # mod replaces nothing", the exact opposite of the truth.
    rels = [(e[0] if isinstance(e, (tuple, list)) else e).replace('\\', '/')
            for e in entries]

    per_arch = Counter()
    names_by_arch = defaultdict(list)
    dropped, ffnx, music, newfolder = [], 0, 0, Counter()
    folder_votes = defaultdict(Counter)

    # first pass: where does each folder mostly point?
    for rel in rels:
        base = os.path.basename(rel).lower()
        folder = os.path.dirname(rel).lower()
        for arch, names in cats.items():
            if base in names:
                folder_votes[folder][arch] += 1
                break

    for rel in rels:
        base = os.path.basename(rel).lower()
        ext = os.path.splitext(base)[1]
        folder = os.path.dirname(rel).lower()
        if ext in MUSIC_EXT:
            music += 1
            continue
        if ext in FFNX_EXT:
            ffnx += 1
            continue
        hit = None
        for arch, names in cats.items():
            if base in names:
                hit = arch
                break
        if hit:
            per_arch[hit] += 1
            names_by_arch[hit].append(base)
            continue
        # New file. build.py's rule, exactly: a candidate with no name match
        # inherits the archive that the MAJORITY of its source-subfolder
        # siblings matched. There is no name-fragment fallback in the builder,
        # so there must not be one here either -- an audit that is more
        # generous than the thing it audits reports files as applied that are
        # actually dropped.
        vote = folder_votes.get(folder)
        arch = vote.most_common(1)[0][0] if vote else None
        if arch:
            newfolder[arch] += 1
            names_by_arch[arch].append(base + '  (new)')
        else:
            dropped.append(rel)
    return per_arch, newfolder, ffnx, music, dropped, names_by_arch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mods', default='mods')
    ap.add_argument('--workingdir', default='workingdir')
    ap.add_argument('--archive', help='only report this archive')
    ap.add_argument('--show-drops', type=int, default=25)
    ap.add_argument('--list-archive', metavar='ARCHIVE',
                    help='print the ENTRY NAMES each mod sends to this archive '
                         '(e.g. magic.lgp) instead of just counting them. This '
                         'is how you find out what a mod actually changes.')
    a = ap.parse_args()

    cats = load_catalogs(a.workingdir)
    if not cats:
        sys.exit('no LGP archives found under %s -- expected e.g. %s'
                 % (a.workingdir, ARCHIVES['battle.lgp']))
    print('archives read from %s:' % a.workingdir)
    for k in sorted(cats):
        print('   %-14s %6d entries' % (k, len(cats[k])))
    missing = [k for k in ARCHIVES if k not in cats]
    if missing:
        print('   not present  : %s' % ', '.join(sorted(missing)))
        print('   (files destined for those cannot be checked here)')

    iros = sorted(f for f in os.listdir(a.mods) if f.lower().endswith('.iro'))
    if not iros:
        sys.exit('no .iro files in %s' % a.mods)

    grand_drop = 0
    if missing:
        print('\n   !! %d archive(s) are absent, so every file destined for '
              'them will\n      look "dropped" below. Point --workingdir at '
              'your full ripped data\n      before believing the UNROUTED '
              'count.' % len(missing))
    for fn in iros:
        path = os.path.join(a.mods, fn)
        try:
            per, new, ffnx, music, dropped, names = audit(path, cats,
                                                          a.show_drops)
        except Exception as exc:
            print('\n%s\n   could not read: %s' % (fn, exc))
            continue
        total = sum(per.values()) + sum(new.values()) + ffnx + music + \
            len(dropped)
        print('\n%s   (%d file(s))' % (fn, total))
        for arch in sorted(set(per) | set(new)):
            if a.archive and arch != a.archive:
                continue
            print('   %-14s %5d replace existing%s'
                  % (arch, per.get(arch, 0),
                     ', %d new (routed by folder)' % new[arch]
                     if new.get(arch) else ''))
        if ffnx:
            print('   %-14s %5d (skipped by design -- no Switch loader)'
                  % ('FFNx/textures', ffnx))
        if music:
            print('   %-14s %5d' % ('music', music))
        if a.list_archive and names.get(a.list_archive):
            got = sorted(names[a.list_archive])
            print('   --- %d entry name(s) sent to %s ---'
                  % (len(got), a.list_archive))
            for i in range(0, len(got), 8):
                print('       ' + '  '.join('%-12s' % n for n in got[i:i + 8]))
        if dropped:
            grand_drop += len(dropped)
            print('   %-14s %5d  <-- these match nothing and are DROPPED'
                  % ('UNROUTED', len(dropped)))
            for d in dropped[:a.show_drops]:
                print('        %s' % d)
            if len(dropped) > a.show_drops:
                print('        ... and %d more' % (len(dropped) - a.show_drops))
        else:
            print('   %-14s %5d' % ('UNROUTED', 0))

    print('\n%s' % ('-' * 60))
    if grand_drop:
        print('%d file(s) across all mods are dropped. Anything in an archive '
              'listed as\n"not present" above is unknown rather than dropped -- '
              'point --workingdir at\nyour full ripped data to check those too.'
              % grand_drop)
    else:
        print('Nothing dropped: every non-FFNx file in these mods routes to an '
              'archive.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
