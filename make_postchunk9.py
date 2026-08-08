#!/usr/bin/env python3
"""
make_postchunk9.py -- the state ff7nx_marginart ACTUALLY OPERATES ON, on disk.

HANDOFF-67 s15 asked for this three times and it was never built. Every
marginart bug has been hard to reproduce because neither archive on disk is the
right one:

    the DUMP           has no Cosmos margin tiles at all, so the margin pass
                       has almost nothing to do and a margin bug cannot appear
    the BUILT archive  has had its pages renumbered and compacted by
                       field_bg_repack, so Cosmos's page numbering no longer
                       applies and writing into it lands on the wrong cells

The pass runs between those two: after `build.py` splices the mod's
`<field>.chunk.9` into section 9, and before `_convert_field_backgrounds`.
This script produces exactly that intermediate archive, using the same splice
build.py does, so `ff7nx_marginart.py` and the diag tools can be pointed at it
directly.

    python3 make_postchunk9.py \
        dump/romfs/ff7/workingdir/data/field/flevel.lgp \
        "<uploads>/cosmos_limit_extract/LIMIT BREAK/flevel.lgp" \
        --out flevel.postchunk9.lgp

The result is NOT a shippable archive -- no repack, no compaction, no
widescreen bake. It is a test fixture.
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import lgp                                                     # noqa: E402


def splice(flevel, chunk_dir, out, fields=None, log=print):
    arc = lgp.Archive(flevel)
    payloads = {}
    n_ok = n_same = n_miss = 0
    for name in arc.names():
        entry = arc.index.get(name)
        if entry is None or not arc.is_field(entry):
            continue
        if fields and name not in fields:
            continue
        src = os.path.join(chunk_dir, name + '.chunk.9')
        if not os.path.exists(src):
            n_miss += 1
            continue
        parts = lgp.split_sections(arc.decompressed(entry))
        # Compared against the RECOMPOSED field, not the decompressed bytes --
        # vanilla fields carry ~14 bytes past the last section that
        # join_sections does not reproduce, so comparing with the original
        # finds a difference in every field and the test never fires. Same
        # rule as build.py.
        van = lgp.join_sections(parts)
        with open(src, 'rb') as f:
            parts[8] = f.read()
        raw = lgp.join_sections(parts)
        if raw == van:
            n_same += 1
            continue
        payloads[name] = arc.encode_field(raw)
        n_ok += 1
    log('spliced %d field(s); %d identical to vanilla and left alone; '
        '%d with no chunk.9' % (n_ok, n_same, n_miss))
    arc.replace(payloads)
    arc.write(out)
    log('wrote %s' % out)
    return n_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel', help='the DUMP flevel.lgp')
    ap.add_argument('chunks', help='the mod\'s "LIMIT BREAK/flevel.lgp" dir')
    ap.add_argument('--out', required=True)
    ap.add_argument('--fields', nargs='*')
    a = ap.parse_args()
    if not os.path.isdir(a.chunks):
        raise SystemExit('chunks is not a directory: %r  '
                         '(quote it, do not backslash-escape spaces)'
                         % a.chunks)
    splice(a.flevel, a.chunks, a.out, a.fields)


if __name__ == '__main__':
    main()
