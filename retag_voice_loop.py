#!/usr/bin/env python3
"""
retag_voice_loop.py -- put the voice clips' loop tag back at zero, in place.

WHY THIS EXISTS
===============
Build 458 moved every voice clip's loop point from `LOOPSTART=0` to the final
decoded sample (`LOOPSTART=<granule-1>`, `LOOPLENGTH=1`). That build is marked
WITHDRAWN in its own document -- hardware disproved it -- but only the runtime
half was reverted in build 459, so the tag stayed in `voice_ogg.py` and has
been on every staged clip since log 334.

A loop point at the last sample asks the player to seek into the final Ogg
page. Zero is the only loop target that is a valid seek in every stream.

WHAT IT DOES
============
Rewrites ONLY the Vorbis comment packet. Audio packets are byte-for-byte
preserved, so this is seconds of work, not a re-encode, and the result is the
exact shape every build up to log 333 shipped.

It only touches files carrying the withdrawn tail signature: exactly one
LOOPSTART whose value is the final granule minus one, AND a LOOPLENGTH=1. The
port's music and per-location ambience do not match that and are left alone --
their loop points are real and must not be moved.

Hard links are broken deliberately: the staged clips are links into
`cache/_voice_ogg`, and writing through one would silently rewrite the cache.

USAGE
=====
    python3 retag_voice_loop.py                # the project's own sdout
    python3 retag_voice_loop.py --check        # report, change nothing
    python3 retag_voice_loop.py /path/to/music_ogg [...]
    python3 retag_voice_loop.py --to tail ...  # undo, for an A/B

WITH NO ARGUMENTS IT DOES BOTH `sdout` AND EVERY MOUNTED SD CARD IT FINDS,
because retagging `sdout` alone changes nothing the console will ever read,
and an earlier version of this file defaulted to `sdout` and quietly did
exactly that -- twice.
"""

import argparse
import glob
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import voice_ogg  # noqa: E402

TITLE_ID = '0100A5B00BDC6000'
INSIDE = os.path.join('romfs', 'ff7', 'workingdir', 'data', 'music_ogg')
SDOUT = os.path.join(HERE, 'sdout', 'atmosphere', 'contents', TITLE_ID, INSIDE)

# Where a card shows up once it is mounted. macOS puts removable volumes under
# /Volumes; the rest are here so this is not macOS-only.
MOUNTS = ('/Volumes/*', '/media/*/*', '/media/*', '/mnt/*', '/run/media/*/*')


def default_roots():
    """`sdout` plus every mounted card carrying this title, newest first."""
    roots, seen = [], set()
    for pattern in (SDOUT,) + tuple(
            os.path.join(m, 'atmosphere', 'contents', TITLE_ID, INSIDE)
            for m in MOUNTS):
        for found in sorted(glob.glob(pattern)):
            real = os.path.realpath(found)
            if os.path.isdir(found) and real not in seen:
                seen.add(real)
                roots.append(found)
    return roots


def classify(data):
    """'tail', 'zero' or None -- what loop tag this stream carries."""
    try:
        comments = voice_ogg._vorbis_comments(data)
    except voice_ogg.VoiceEncodeError:
        return None
    starts = [value for value in comments
              if value.upper().startswith(b'LOOPSTART=')]
    lengths = [value for value in comments
               if value.upper().startswith(b'LOOPLENGTH=')]
    if len(starts) != 1:
        return None
    value = starts[0].split(b'=', 1)[1]
    if lengths:
        if len(lengths) != 1 or lengths[0].split(b'=', 1)[1] != b'1':
            return None
        try:
            last = voice_ogg._last_granule(data)
        except voice_ogg.VoiceEncodeError:
            return None
        return 'tail' if value == str(last - 1).encode('ascii') else None
    return 'zero' if value == b'0' else None


def rewrite(path, want, check):
    with open(path, 'rb') as handle:
        data = handle.read()
    have = classify(data)
    if have is None:
        return 'skipped'
    if have == want:
        return 'already'
    if check:
        return 'would'
    converted = voice_ogg._with_final_sample_loop(data, mode=want)
    if classify(converted) != want:
        raise SystemExit('%s: the rewrite did not produce %s' % (path, want))
    # Write beside the target and rename: the staged clip is a hard link into
    # the encoder cache, and rewriting it in place would rewrite the cache
    # entry through that link. The rename gives this path its own inode and
    # leaves the cache exactly as the build left it.
    handle, temporary = tempfile.mkstemp(
        prefix='.%s.' % os.path.basename(path), suffix='.tmp.ogg',
        dir=os.path.dirname(path))
    try:
        with os.fdopen(handle, 'wb') as output:
            output.write(converted)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return 'retagged'


def drop_romfs_metadata(root):
    """Delete Atmosphere's cached romfs layout above `root`, if there is one.

    Rewriting a tag changes the file's LENGTH, and `romfs_metadata.bin` is
    where Atmosphere remembers every file's offset and length. Leaving a stale
    one behind means the console reads the new clips at the old lengths, which
    looks like a completely different bug. Doing it here rather than telling
    someone to remember it is the point.
    """
    path = os.path.abspath(root)
    while True:
        candidate = os.path.join(path, 'romfs_metadata.bin')
        if os.path.isfile(candidate):
            os.unlink(candidate)
            return candidate
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('roots', nargs='*')
    ap.add_argument('--to', choices=('zero', 'tail'), default='zero')
    ap.add_argument('--check', action='store_true',
                    help='report what would change and write nothing')
    args = ap.parse_args()
    roots = args.roots or default_roots()
    if not roots:
        raise SystemExit('found no music_ogg to work on')
    on_a_card = [r for r in roots
                 if os.path.realpath(r) != os.path.realpath(SDOUT)]

    counts = {'retagged': 0, 'already': 0, 'skipped': 0, 'would': 0}
    dropped = []
    for root in roots:
        if not os.path.isdir(root):
            raise SystemExit('not a directory: %s' % root)
        print('scanning %s' % root)
        before = counts['retagged']
        for base, _dirs, files in os.walk(root):
            for name in files:
                if not name.endswith('.ogg'):
                    continue
                counts[rewrite(os.path.join(base, name), args.to,
                               args.check)] += 1
        if counts['retagged'] > before and not args.check:
            gone = drop_romfs_metadata(root)
            if gone:
                dropped.append(gone)
    print('\n%-10s %d  (loop tag now %s)'
          % ('retagged', counts['retagged'], args.to))
    if counts['would']:
        print('%-10s %d  (--check: nothing was written)'
              % ('would', counts['would']))
    print('%-10s %d  (already %s)' % ('unchanged', counts['already'], args.to))
    print('%-10s %d  (music and ambience -- their loop points are real)'
          % ('left alone', counts['skipped']))
    for gone in dropped:
        print('\ndeleted %s\n  (retagging changes each file\'s length, and '
              'that is the cache of every file\'s length. Atmosphere rebuilds '
              'it on the next boot.)' % gone)
    if counts['retagged'] and not dropped:
        print('\nNOTE: no romfs_metadata.bin was found above these files. If '
              'you copy them\n      onto the SD card, delete\n      '
              'atmosphere/contents/<title id>/romfs_metadata.bin there.')

    # The whole point is what the CONSOLE reads. Say so plainly either way:
    # a run that only touched `sdout` has changed nothing you can test.
    print('')
    if not on_a_card:
        print('*** NO SD CARD WAS FOUND, so nothing the console reads has '
              'changed. ***')
        print('    Insert the card and run this again, or pass its path:')
        print('      python3 %s \\' % os.path.basename(__file__))
        print('        "/Volumes/<YOUR CARD>/atmosphere/contents/%s/%s"'
              % (TITLE_ID, INSIDE))
    else:
        for root in on_a_card:
            print('wrote to the card at %s' % root)
        print('boot the game -- nothing needs copying.')


if __name__ == '__main__':
    main()
