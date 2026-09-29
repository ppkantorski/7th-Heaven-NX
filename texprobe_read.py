#!/usr/bin/env python3
"""
texprobe_read.py -- decode the crash report of an R3 click with texprobe v2 on.

    python3 texprobe_read.py <crash_report.log>

Registers x0..x7, FP, LR carry the summary; the Stack Dump holds records
0..7 and the TLS Dump records 8..15 (see native/texprobe.c, dump()).
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import frameprobe_read as FR                 # noqa: E402

MAGIC = 0x33505854
MAGIC_V2 = 0x32505854
MAGIC_V1 = 0x42505854
FLAGS = ('SKIPPED', 'NOIMAGE', 'CHANGED', 'UNMATCHED', 'DUPHANDLE',
         'TSREUSED', 'BIG', 'STALE', 'CARRIED', 'REMADE', 'HCHANGED')


def recs(blob):
    """Records from a dump; creport may start a few words early, so try the
    word phases and keep the one whose records look sane."""
    best = []
    for phase in range(0, 32, 4):
        got = []
        for i in range(phase, len(blob) - 31, 32):
            r = struct.unpack_from('<8I', blob, i)
            if not any(r):
                continue
            w, h = r[0] & 0xFFF, (r[0] >> 12) & 0xFFF
            if not w or not h or not (r[0] >> 24) or not r[2] or r[2] >> 30:
                got = None
                break
            got.append(r)
        if got and len(got) > len(best):
            best = got
    return best


def show(r):
    a, b, ts, img, pal, hpre, hpost, iptr = r
    w, h, count = a & 0xFFF, (a >> 12) & 0xFFF, a >> 24
    idx, palidx, fl = b & 0x3FF, (b >> 10) & 0xFF, b >> 18
    flags = [n for k, n in enumerate(FLAGS) if fl & (1 << k)]
    if fl & (1 << 13):
        return ('      ^ in visit %d/%d (mode/field) #%-4d %4dx%-4d x%-4s img %08x pal %08x'
                '  handle -> %08x  set %08x  image %08x  %s'
                % (hpre >> 16, hpre & 0xFFFF, idx, w, h,
                   '255+' if count == 255 else count, img, pal, hpost, ts, iptr,
                   ' '.join(flags) or '-'))
    return ('  #%-4d %4dx%-4d pal#%-3d x%-4s img %08x pal %08x  handle %08x -> %08x'
            '  set %08x  image %08x  %s'
            % (idx, w, h, palidx, '255+' if count == 255 else count, img, pal,
               hpre, hpost, ts, iptr, ' '.join(flags) or '-'))


def key20(v):
    return (v >> 16) & 0xF, v & 0xFFFF


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    text = open(argv[1], errors='replace').read()
    th = FR._crashed_thread(text)
    regs = FR.parse_registers(th)
    x = [regs.get(i, 0) for i in range(8)] + [regs.get(29, 0), regs.get(30, 0)]
    if (x[0] >> 32) == MAGIC_V1:
        print('a texprobe v1 report -- reinstall the probe (--off, then --on)')
        return 1
    if (x[0] >> 32) == MAGIC_V2:
        print('(a v2 report: STALE also counts fresh uploads, no shadows)')
    elif (x[0] >> 32) != MAGIC:
        print('not a texprobe report (x0 = %016x)' % x[0])
        return 1
    key = x[0] & 0xFFFFFFFF
    print('texprobe: mode %d field %d (0x%X)' % (key >> 16, key & 0xFFFF, key & 0xFFFF))
    ncur, nref = x[1] >> 48, (x[1] >> 32) & 0xFFFF
    print('  this visit: #%d, %d textures, %d loader calls, %d uploads'
          % ((x[1] >> 16) & 0xFFFF, ncur, x[4] >> 32, x[4] & 0xFFFFFFFF))
    print('  reference (previous visit of the same field/mode): %s'
          % (('#%d, %d textures, %d calls, %d uploads'
              % (x[1] & 0xFFFF, nref, x[7] >> 32, x[7] & 0xFFFFFFFF)) if nref else 'NONE'))
    print('  unmatched %d   skipped %d   dup-handle %d   ts-reused %d'
          % (x[2] >> 48, (x[2] >> 32) & 0xFFFF, (x[2] >> 16) & 0xFFFF, x[2] & 0xFFFF))
    print('  changed-after-load %d   no-image %d   lost %d   STALE %d'
          % (x[3] >> 48, (x[3] >> 32) & 0xFFFF, (x[3] >> 16) & 0xFFFF, x[3] & 0xFFFF))
    print('  carried %d   remade %d   handle-changed %d   new sets made %d   pending %d'
          % (x[5] >> 48, (x[5] >> 32) & 0xFFFF, (x[5] >> 16) & 0xFFFF, x[5] & 0xFFFF,
             x[9] >> 60))
    if x[6]:
        print('  first unmatched texture\'s same-size twin in the reference: '
              'img %08x pal %08x' % (x[6] >> 32, x[6] & 0xFFFFFFFF))
    kept = [key20((x[8] >> (20 * k)) & 0xFFFFF) for k in range(3)] + \
           [key20((x[9] >> (20 * k)) & 0xFFFFF) for k in range(3)]
    print('  kept visits (mode/field): %s'
          % ', '.join('%d/%d' % mf for mf in kept if mf[0] or mf[1]))
    stack = FR.parse_dump(th, 'Stack Dump')
    tls = FR.parse_dump(th, 'TLS Dump')
    rs = recs(stack) + recs(tls)
    print('  records (sets carried in from before this visit, each with its '
          'record in another visit (^) and in the reference (^), then trouble, '
          'then the LAST loads):')
    for r in rs:
        print(show(r))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
