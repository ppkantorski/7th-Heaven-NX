#!/usr/bin/env python3
"""
scan_camdat_waits.py -- what the battle camera scripts actually contain.

    python3 scan_camdat_waits.py sdout/.../workingdir/data/lang-en/battle

Walks every camera script in camdat0/1/2.bin with FFNx's own opcode tables and
reports the distribution of opcode 0xF5's wait operand. It answers two questions
that the `camera-wait` cave otherwise has to hand-wave:

  1. How many waits are above 0x3F? Those are the ones a STATIC byte patch of
     the camdat files (`--camdat`) cannot represent -- multiplied by four they
     exceed 255 and clamp. The cave has no such limit: it writes the game's
     16-bit `frames_to_wait` field, so 254*4 = 1016 fits.

  2. How many are in 0x80..0xFE? That is the only range where FFNx and the stock
     interpreter disagree. Stock reads the operand with a SIGNED byte load, so
     0x80..0xFE arrive negative and, since opcode 0xF4 decrements away from
     zero, mean "wait effectively forever". FFNx reads the same byte unsigned
     and turns them into a finite 512..1016 frame wait. The cave reproduces
     FFNx. If the count is tiny the choice does not matter; if it were large it
     would need rethinking.

FILE FORMAT
-----------
Four 32-bit PSX pointers at offset 0: position script table, focal script table,
position "-3" table, focal "-3" table. The last two hold three entries each (one
per variation index). Scripts come FIRST in the file and the tables sit at the
end, so the load base is recovered as

    base = header[3] + 0xC - filesize

which is checked by requiring every table pointer to land inside the file.
"""
import argparse
import os
import struct
import sys
from collections import Counter

# Transcribed from FFNx src/ff7/battle/camera.cpp -- numArgsPositionOpCode and
# numArgsOpCode. 0xF4 and 0xFF carry -1 there, meaning "handled specially", so
# they are absent here and handled by the walker.
POS_ARGS = {0xD5: 2, 0xD6: 0, 0xD7: 2, 0xD8: 9, 0xD9: 0, 0xDA: 0, 0xDB: 0,
            0xDC: 0, 0xDD: 1, 0xDE: 1, 0xDF: 0, 0xE0: 2, 0xE1: 0, 0xE2: 1,
            0xE3: 9, 0xE4: 8, 0xE5: 8, 0xE6: 7, 0xE7: 8, 0xE9: 8, 0xEB: 9,
            0xEF: 8, 0xF0: 7, 0xF1: 0, 0xF2: 5, 0xF3: 5, 0xF5: 1, 0xF7: 7,
            0xF8: 12, 0xF9: 6, 0xFE: 0}
POS_END = {0xEF, 0xF0, 0xF7, 0xFF}
FOC_ARGS = {0xD8: 9, 0xD9: 0, 0xDB: 0, 0xDC: 0, 0xDD: 1, 0xDE: 1, 0xDF: 0,
            0xE0: 2, 0xE1: 0, 0xE2: 1, 0xE3: 9, 0xE4: 8, 0xE5: 8, 0xE6: 7,
            0xE8: 8, 0xEA: 8, 0xEC: 9, 0xF0: 8, 0xF5: 1, 0xF8: 7, 0xF9: 7,
            0xFA: 6, 0xFE: 0}
FOC_END = {0xF0, 0xF8, 0xF9, 0xFF}


def walk(d, start, args, ends, waits, limit=6000):
    """One script. Returns True if it terminated cleanly."""
    p = start
    for _ in range(limit):
        if not (0 <= p < len(d)):
            return False
        op = d[p]
        p += 1
        if op == 0xF4:                       # tick the wait; no operand
            continue
        if op == 0xF5:                       # set the wait
            if p >= len(d):
                return False
            waits.append(d[p])
            p += 1
            continue
        if op == 0xFF:
            return True
        if op == 0xFE:
            if p < len(d) and d[p] == 0xC0:
                p += 1
            return True
        n = args.get(op)
        if n is None:
            return False
        p += n
        if op in ends:
            return True
    return False


def scan(path):
    d = open(path, 'rb').read()
    hdr = struct.unpack('<4I', d[:16])
    base = hdr[3] + 0xC - len(d)
    for h in hdr:
        if not (0 <= h - base < len(d)):
            raise SystemExit('%s: table pointer 0x%08X is outside the file for '
                             'load base 0x%08X -- format not understood'
                             % (path, h, base))
    spans = [(hdr[0] - base, hdr[1] - base, POS_ARGS, POS_END),
             (hdr[1] - base, hdr[2] - base, FOC_ARGS, FOC_END),
             (hdr[2] - base, hdr[2] - base + 0xC, POS_ARGS, POS_END),
             (hdr[3] - base, hdr[3] - base + 0xC, FOC_ARGS, FOC_END)]
    waits = Counter()
    ok = bad = 0
    for lo, hi, args, ends in spans:
        w = []
        for off in range(lo, hi, 4):
            ptr = struct.unpack('<I', d[off:off + 4])[0] - base
            if not (0 <= ptr < len(d)):
                bad += 1
                continue
            if walk(d, ptr, args, ends, w):
                ok += 1
            else:
                bad += 1
        waits.update(w)
    return base, ok, bad, waits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dir', help='folder holding camdat0/1/2.bin')
    a = ap.parse_args()
    grand = Counter()
    gok = gbad = 0
    print('%-14s %-12s %7s %6s %8s %6s %10s %11s'
          % ('file', 'load base', 'scripts', 'refus', 'F5 waits', 'max',
             '>0x3F', '0x80..0xFE'))
    for name in ('camdat0.bin', 'camdat1.bin', 'camdat2.bin'):
        p = os.path.join(a.dir, name)
        if not os.path.isfile(p):
            print('%-14s not found' % name)
            continue
        base, ok, bad, w = scan(p)
        grand.update(w)
        gok += ok
        gbad += bad
        print('%-14s 0x%08X %7d %6d %8d   0x%02X %10d %11d'
              % (name, base, ok, bad, sum(w.values()),
                 max(w) if w else 0,
                 sum(v for k, v in w.items() if 0x40 <= k <= 0xFE),
                 sum(v for k, v in w.items() if 0x80 <= k <= 0xFE)))
    tot = sum(grand.values())
    if not tot:
        return 1
    clamp = sum(v for k, v in grand.items() if 0x40 <= k <= 0xFE)
    quirk = sum(v for k, v in grand.items() if 0x80 <= k <= 0xFE)
    print('\n%d script(s) walked cleanly, %d refused, %d F5 wait operand(s)'
          % (gok, gbad, tot))
    print('  %d (%.1f%%) exceed 0x3F -- a static byte patch of these files '
          'would clamp every one of them at 255.' % (clamp, 100.0 * clamp / tot))
    print('  %d (%.2f%%) are in 0x80..0xFE, the only range where FFNx and the '
          'stock interpreter disagree.' % (quirk, 100.0 * quirk / tot))
    print('  %d are the 0xFF sentinel, which the cave leaves exactly as stock.'
          % grand.get(0xFF, 0))
    print('\n  most common waits: %s'
          % ', '.join('%d x%d' % (k, v)
                      for k, v in sorted(grand.items(),
                                         key=lambda kv: -kv[1])[:10]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
