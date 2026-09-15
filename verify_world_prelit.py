#!/usr/bin/env python3
"""
Check the BUILT world archive, not the build log.

    python3 verify_world_prelit.py [path/to/world_us.lgp]

Defaults to the shipped archive under ./sdout. It reports, for every
version-1 `.p` entry:

  * how many are PRE-LIT (vertextype 1) and how many are still UNLIT (0),
  * that every pre-lit part actually has one vertex colour per vertex,
  * and, for comparison, the same counts for vanilla world_us.lgp.

Exit status is 0 only when the shipped archive has no unlit part left.
See FINDINGS-411 for why an unlit part is the whole defect.
"""
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lgp                                                     # noqa: E402

SHIPPED = ('sdout/atmosphere/contents/0100A5B00BDC6000/romfs/ff7/'
           'workingdir/data/wm/world_us.lgp')
VANILLA = 'game_data_files/wm/world_us.lgp'
P_ENTRY = re.compile(r'\.[0-9]*p$')
VERTEXTYPE = {0: 'UNLIT (D3DVERTEX)', 1: 'pre-lit (D3DLVERTEX)',
              2: 'TLVERTEX (2D)'}


def survey(path):
    a = lgp.Archive(path)
    counts = {}
    unlit = []
    short = []
    for name in a.names():
        if not P_ENTRY.search(name):
            continue
        data = a.index[name]['payload']
        if len(data) < 128:
            continue
        (version, _f4, vertextype, numverts, _nn, _f14, _nt,
         numvertcolors) = struct.unpack_from('<8I', data, 0)
        if version != 1:
            continue
        counts[vertextype] = counts.get(vertextype, 0) + 1
        if vertextype == 0:
            unlit.append(name)
        elif vertextype == 1 and numvertcolors != numverts:
            short.append((name, numverts, numvertcolors))
    return counts, unlit, short


def report(label, path):
    if not os.path.isfile(path):
        print('%-8s %s  -- not found' % (label, path))
        return None
    counts, unlit, short = survey(path)
    total = sum(counts.values())
    print('%-8s %s' % (label, path))
    print('         %d version-1 .p entries' % total)
    for vt in sorted(counts):
        print('           vertextype %d  %-22s %4d'
              % (vt, VERTEXTYPE.get(vt, '?'), counts[vt]))
    if short:
        print('         ! %d pre-lit part(s) without one colour per vertex:'
              % len(short))
        for nm, nv, nc in short[:10]:
            print('             %s  numverts %d  numvertcolors %d'
                  % (nm, nv, nc))
    if unlit:
        print('         ! still unlit: %s%s'
              % (', '.join(unlit[:12]), ' ...' if len(unlit) > 12 else ''))
    return counts, unlit, short


def main(argv):
    shipped = argv[0] if argv else SHIPPED
    print('')
    report('vanilla', VANILLA)
    print('')
    got = report('shipped', shipped)
    print('')
    if got is None:
        print('FAIL: no shipped archive at %s' % shipped)
        return 2
    counts, unlit, short = got
    if unlit:
        print('FAIL: %d world model part(s) are still UNLIT. The world '
              'module will light them and they will be iridescent.'
              % len(unlit))
        return 1
    if short:
        print('FAIL: %d pre-lit part(s) do not carry one vertex colour per '
              'vertex.' % len(short))
        return 1
    print('PASS: every world model part is pre-lit and carries its own '
          'vertex colours. Nothing on the world map is lit at draw time.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
