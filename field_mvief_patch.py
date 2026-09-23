#!/usr/bin/env python3
r"""
field_mvief_patch.py -- retire a `wait until MVIEF reports 0` loop, one byte.

    python3 field_mvief_patch.py <flevel.lgp> --show
    python3 field_mvief_patch.py <flevel.lgp> --apply
    python3 field_mvief_patch.py <flevel.lgp> --restore

WHAT THIS IS TESTING
====================
MEASURED, 2026-09-23: with `bugin1c`'s vanilla script swapped in the scene
plays through; with the shipped Echo-S script it stops on a black screen with
the music still running.  So the variable is the script, and the disassembly
(`field_dis.py`) says which instruction.

The Echo-S script ends its observatory FMV like this:

    +02C9  fade  00 00 00 00 00 0C 02 00     ; fade to BLACK
    +02D2  mvief 06 05                       ; v = MVIEF
    +02D5  if2   v != 0  -> +02DF            ; jump out when the test FAILS
    +02DD  back  0B                          ; -> +02D2

FFNx's `opcode_IFSW_compare_sub` gives the comparison table outright -- 0 ==,
1 !=, 2 >, 3 <, 4 >=, 5 <= -- and FF7 takes the jump when the test is FALSE.
So this is `do { v = MVIEF; } while (v != 0);` -- **wait until the movie
stops**.  Vanilla wrote the same place as `while (v < 0x0491)`, a frame
threshold, which terminates on any counter that climbs.

WHY THE WAIT NEVER ENDS HERE
============================
MVIEF has two return paths, and a byte at guest `0xCC0B68` picks between them
(read out of the shipped module at the handler entry, 0x979800):

    byte[0xCC0B68] != 0   ->  u16 [0xCC0B70], THE POLL COUNTER, then ++
    byte[0xCC0B68] == 0   ->  u16 [*0xCBF9D8 + 0x88], the movie object

`ff7nx_dispatch` already documents the first: `0xCC0B70` is reset only when
MOVIE starts playback and is incremented by MVIEF itself, once per poll.  It
never returns to zero on its own.  By the time this loop is reached the
script has polled about 1140 times, so on that path `v != 0` is permanently
true and the field spins forever -- on the black frame the FADE just left,
with the music untouched.  That is the reported symptom exactly.

THE IDIOM IS NOT AN ECHO-S INVENTION
====================================
MEASURED over all 711 fields of both archives:

    vanilla   149 MVIEF, 2 of them `!= 0` loops -- both in `seto1`
    shipped   144 MVIEF, 3 of them `!= 0` loops -- seto1's two, and this one

`seto1` gets away with it because its loop runs IMMEDIATELY after MOVIE,
while the counter is still 0, so it falls straight through.  `bugin1c` runs
its loop 1140 polls later.  Same instruction, opposite outcome.

WHAT THIS PATCH DOES
====================
Changes the comparison byte from 1 (`!=`) to 5 (`<=`).  The loop then runs
while `v <= 0` and leaves the moment the counter is positive -- which, 1140
polls in, is the first read.  One byte, no length change, no offsets moved.

It is a DIAGNOSTIC, not the fix:

    hang gone  -> that loop is the hang. The permanent answer is either an
                  audited script correction in `echo_s_flevel.py` or making
                  MVIEF report 0 once playback has stopped, which would also
                  cover any other field that uses the idiom.
    hang stays -> I am wrong about the instruction, and the next step is
                  instrumenting the field VM rather than reading it.

The patch is safe under EITHER answer about which MVIEF path is live: if the
poll counter is being returned it is positive here, and if the movie object's
frame is being returned it is positive here too.  Both exit on the first
read.

NOTE ON SEQUENCING
==================
Run `field_swap.py <flevel.lgp> --restore` first if the vanilla script is
still swapped in -- otherwise this patches a field that no longer contains
the instruction, and it will say so rather than write anything.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import field_dis as FD                                         # noqa: E402

FIELD = 'bugin1c'
CMP_NE = 1                     # `!=`  -- the loop that never ends
CMP_LE = 5                     # `<=`  -- leaves as soon as the count is > 0
IF2_LEN = 8                    # opcode + banks + left u16 + right u16 + cmp + jump
IF2_CMP_OFF = 6                # the comparison byte within the instruction


def _sha(b):
    return hashlib.sha1(b).hexdigest()[:16]


def find_sites(section1):
    """[(offset of the if2, block label, cmp byte)] for `MVIEF ... == 0` loops.

    A site qualifies only when the if2 compares against a literal 0 AND an
    `mvief` sits directly on one side of it.  Both conditions, so an ordinary
    `if var == 0` elsewhere in the field can never be rewritten by accident.
    """
    meta, names, entries = FD.parse(section1)
    out = []
    for label, base, ins in FD._labelled(section1, meta, names, entries):
        for i, (off, n, size, raw, err) in enumerate(ins):
            if n != 'if2' or len(raw) != IF2_LEN:
                continue
            right = raw[3] | (raw[4] << 8)
            cmp_ = raw[IF2_CMP_OFF]
            if right != 0 or cmp_ not in (CMP_NE, CMP_LE):
                continue
            near = [ins[j][1] for j in (i - 1, i + 1) if 0 <= j < len(ins)]
            if 'mvief' not in near:
                continue
            out.append((off, label.split(',')[0].strip(), cmp_))
    return out


def _field_bytes(ar, name, L):
    e = ar.index.get(name)
    if e is None:
        raise SystemExit('  ! %s is not in the archive' % name)
    raw = ar.decompressed(e)
    p = struct.unpack_from('<9I', raw, 6)[0]
    ln = struct.unpack_from('<I', raw, p)[0]
    return raw, p + 4, raw[p + 4:p + 4 + ln]


def show(flevel, field, log=print):
    import lgp as L
    ar = L.Archive(flevel)
    raw, s1_at, sec1 = _field_bytes(ar, field, L)
    sites = find_sites(sec1)
    if not sites:
        log('  %s: no `MVIEF ... == 0` loop found (vanilla script swapped in?)'
            % field)
        return 1
    for off, label, cmp_ in sites:
        log('  %s  %s  +%04X  cmp=%d (%s)'
            % (field, label, off, cmp_,
               'NE -- never ends' if cmp_ == CMP_NE else 'LE -- patched'))
    return 0


def apply(flevel, field, restore=False, log=print, backup=True):
    import lgp as L
    ar = L.Archive(flevel)
    raw, s1_at, sec1 = _field_bytes(ar, field, L)
    want_from = CMP_LE if restore else CMP_NE
    want_to = CMP_NE if restore else CMP_LE

    sites = [s for s in find_sites(sec1) if s[2] == want_from]
    if not sites:
        log('  ! nothing to do: no site with cmp=%d in %s' % (want_from, field))
        return 1

    out = bytearray(raw)
    for off, label, _ in sites:
        at = s1_at + off + IF2_CMP_OFF
        if out[at] != want_from:
            log('  ! byte at +%04X is 0x%02X, not 0x%02X -- refusing'
                % (off, out[at], want_from))
            return 1
        out[at] = want_to
        log('    %s  +%04X  cmp %d -> %d' % (label, off, want_from, want_to))

    if backup:
        bak = flevel + '.mviefpatch.bak'
        if not os.path.exists(bak):
            log('  backup -> %s  (1.5 GB copy, be patient)' % bak)
            shutil.copy2(flevel, bak)
        else:
            log('  backup already exists, left alone -> %s' % bak)

    ar.replace({field: ar.encode_field(bytes(out))})
    ar.write(flevel)
    log('  wrote %s' % flevel)

    back = L.Archive(flevel)
    _, _, sec1b = _field_bytes(back, field, L)
    got = [s for s in find_sites(sec1b) if s[2] == want_to]
    if len(got) != len(sites):
        log('  ! READ BACK FAILED -- do not boot this')
        return 1
    log('  read back: %d site(s) now cmp=%d' % (len(got), want_to))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('--field', default=FIELD)
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--no-backup', action='store_true')
    a = ap.parse_args(argv)

    if a.apply or a.restore:
        return apply(a.flevel, a.field, restore=a.restore,
                     backup=not a.no_backup)
    return show(a.flevel, a.field)


if __name__ == '__main__':
    raise SystemExit(main())
