#!/usr/bin/env python3
r"""
field_swap.py -- put ONE field back to its vanilla form, in place.

    python3 field_swap.py <flevel.lgp> bugin1c --show
    python3 field_swap.py <flevel.lgp> bugin1c --swap      # -> vanilla
    python3 field_swap.py <flevel.lgp> --restore

The archive write is `Archive.replace()` + `Archive.write()`, which is the
same pair `ff7nx_vclip --patch` has used to rewrite this file in place for
many builds -- not a new mechanism invented for this test.

WHY
===
`bugin1c` hangs on a black screen after the observatory FMV, with the music
still playing. Four runtime candidates have been eliminated one word at a
time -- the SCR2D initializer clamp, the day/night cycle, the 30 fps movie
counter halving, and the field voice runtime -- and none of them is it.

What has NOT been tested is whether the script itself is ours to answer for.
MEASURED over the built archive, every field in that area carries a MODIFIED
section 1:

    bugin1c   vanilla  9852 bytes   shipped 10250   MODIFIED
    bugin1b            6984                  7347   MODIFIED
    bugin1a           14240                 14695   MODIFIED
    bugin2            16034                 15213   MODIFIED

Echo-S rewrites field scripts to carry its voiced dialogue, so this is
expected -- but it means the script that hangs is not the one the game
shipped with, and it may be asking for behaviour FFNx provides and this port
does not.

This swaps that one field's payload back to the vanilla archive's, so the
scene runs its ORIGINAL script against our current runtime.

    still hangs  -> the script is not the variable. It is our runtime, and
                    Echo-S is cleared. Every measurement after that is taken
                    against a clean baseline.
    plays fine   -> the hang lives in the modded script, or in what the
                    modded script asks of us. That is a completely different
                    search, and a much narrower one.

WHAT YOU LOSE DURING THE TEST
=============================
The vanilla script has no Echo-S dialogue, so that scene plays in its
original form -- no voiced lines, and any Echo-S-specific staging is gone.
It is a diagnostic, not a setting. `--restore` puts the modded field back
from the backup this writes.

WHY ONE FIELD RATHER THAN A REBUILD
===================================
A full archive rebuild without Echo-S would answer the same question and
cost about forty minutes plus a settings change that then has to be undone.
This is one payload, seconds, and it is reversible from a file on disk.
`ff7nx_vclip --patch` set the precedent: a 1.5 GB rebuild is a poor way to
answer a yes/no question about one field.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _sha(b):
    return hashlib.sha1(b).hexdigest()[:16]


def _sections(raw, lgp):
    return lgp.split_sections(raw)


def show(flevel, vanilla, names, log=print):
    import lgp as L
    s, v = L.Archive(flevel), L.Archive(vanilla)
    log('  %-10s %-12s %-12s %s' % ('field', 'vanilla s1', 'shipped s1',
                                    'script section'))
    for n in names:
        se, ve = s.index.get(n), v.index.get(n)
        if se is None or ve is None:
            log('    %-10s %s' % (n, 'absent'))
            continue
        ss = _sections(s.decompressed(se), L)[0]
        vs = _sections(v.decompressed(ve), L)[0]
        log('    %-10s %-12d %-12d %s'
            % (n, len(vs), len(ss),
               'identical' if _sha(vs) == _sha(ss) else 'MODIFIED by a mod'))
    return 0


def swap(flevel, vanilla, names, log=print, backup=True):
    """Replace each named field's whole payload with the vanilla one."""
    import lgp as L
    s, v = L.Archive(flevel), L.Archive(vanilla)

    payloads, missing = {}, []
    for n in names:
        se, ve = s.index.get(n), v.index.get(n)
        if se is None or ve is None:
            missing.append(n)
            continue
        raw = v.decompressed(ve)
        payloads[n] = s.encode_field(raw)
        log('    %-10s <- vanilla (%d bytes decompressed)' % (n, len(raw)))
    if missing:
        log('  ! not in one of the archives: %s' % ', '.join(missing))
    if not payloads:
        return 1

    if backup:
        bak = flevel + '.fieldswap.bak'
        if not os.path.exists(bak):
            log('  backup -> %s  (this is a 1.5 GB copy, be patient)' % bak)
            shutil.copy2(flevel, bak)
        else:
            log('  backup already exists, left alone -> %s' % bak)

    s.replace(payloads)
    s.write(flevel)
    log('  wrote %s' % flevel)

    # Read it back. A swap that silently did not reach the archive is exactly
    # the failure mode this project keeps having.
    back = L.Archive(flevel)
    bad = []
    for n in payloads:
        got = _sections(back.decompressed(back.index[n]), L)[0]
        want = _sections(v.decompressed(v.index[n]), L)[0]
        if _sha(got) != _sha(want):
            bad.append(n)
    if bad:
        log('  ! READ BACK FAILED on %s -- do not boot this' % ', '.join(bad))
        return 1
    log('  read back: %d field(s) now carry the vanilla script' % len(payloads))
    return 0


def restore(flevel, log=print):
    bak = flevel + '.fieldswap.bak'
    if not os.path.exists(bak):
        log('  ! no backup at %s' % bak)
        return 1
    shutil.copy2(bak, flevel)
    log('  restored %s from %s' % (flevel, bak))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('fields', nargs='*', default=['bugin1c'])
    ap.add_argument('--vanilla-lgp',
                    help='the untouched flevel.lgp (default: dump/...)')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--swap', action='store_true',
                    help='put the named field(s) back to vanilla')
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--no-backup', action='store_true')
    a = ap.parse_args(argv)

    if a.restore:
        return restore(a.flevel)

    van = a.vanilla_lgp
    if not van:
        # sdout/atmosphere/contents/<id>/romfs/ff7/workingdir/data/field is
        # NINE levels below the project root, not eight. Walk further than
        # that and try both places an untouched flevel lives.
        root = a.flevel
        rel = (('dump', 'romfs', 'ff7', 'workingdir', 'data', 'field',
                'flevel.lgp'),
               ('game_data_files', 'field', 'flevel.lgp'))
        for _ in range(14):
            nxt = os.path.dirname(root)
            if nxt == root:
                break
            root = nxt
            for parts in rel:
                cand = os.path.join(root, *parts)
                if os.path.exists(cand) and os.path.abspath(cand) != \
                        os.path.abspath(a.flevel):
                    van = cand
                    break
            if van:
                break
    if not van or not os.path.exists(van):
        print('  ! need --vanilla-lgp: the untouched flevel.lgp was not found')
        return 1
    print('  vanilla: %s' % van)

    names = a.fields or ['bugin1c']
    if a.swap:
        return swap(a.flevel, van, names, backup=not a.no_backup)
    return show(a.flevel, van, names)


if __name__ == '__main__':
    raise SystemExit(main())
