#!/usr/bin/env python3
r"""
ff7nx_uiwidth.py -- AN EXPERIMENT, not a fix.  Two bytes, fully reversible.

THE QUESTION IT ANSWERS
=======================
Some menu and dialogue boxes lose their RIGHT border.  Never the left, never
the top or bottom, and only some boxes.

The battle window layout is a data table in `ff7_en`'s .data.  Read out of
the stock file:

    command box     x=144  y=340  w=110  h=112     <-- 110 is NOT a multiple of 8
    defend/change   x= 48  y=340  w=112  h= 48
    sub menu        x=  0  y=348  w=640  h=112
    Manip box       x=100  y=340  w=440  h=112
    Coin box        x=100  y=348  w=328  h=116
    limit box       x=160  y=348  w=272  h=112

Five of six widths are multiples of 8.  The one that is not is the command
box -- the exact window photographed with its right border missing.

FF7 draws window frames from 8x8 tiles.  An integer `w / 8` loop covers
13 * 8 = 104 of 110 units and drops the last partial column, which would be
on the right and would only affect boxes whose width is not a multiple of 8.
Field dialogue widths are set per message by the field script, which is why
it is *some* dialogues rather than all of them.

That is a HYPOTHESIS.  It fits every constraint in the report -- right side
only, some boxes not others, both battle and field -- but the truncation
itself has NOT been found: none of the 19 functions that touch the field
window rect contains a divide by 8, so if the loop exists it is in a shared
draw routine reached by argument rather than through the globals.

SO THIS MODULE IS A MEASUREMENT
===============================
It rounds ONE width up, 110 -> 112, and nothing else.

    border appears   -> the mod-8 theory is right.  The real fix is then
                        either rounding widths up, or better, fixing the draw
                        loop to (w + 7) / 8 -- which would also fix the field
                        dialogues without touching any data at all.
    nothing changes  -> the theory is dead.  Cost: 2 game units, 3 screen
                        pixels, invisible.

Both outcomes are informative, which is the whole point.  Earlier in this
session I declined to change this same value precisely because I had no
prediction attached to it, and a change whose outcomes you cannot interpret
in advance teaches nothing whichever way it goes.

WHAT IT DOES NOT DO
===================
It is NOT wired into build.py or the GUI, deliberately.  It patches `ff7_en`
in romfs, not `exefs/main`, so it is independent of every other module in
this tree and of the 16:9 setting.  Run it by hand, test, run --revert.
"""
import argparse
import shutil
import struct
import sys
from pathlib import Path

# The battle window layout table, ff7_en .data.  Addresses confirmed against
# Enhanced Stock UI's hext files, which patch these same offsets by name
# ("command box X-Axis" = 0x91C3D8).
RECORDS = {
    0x91C35A: 'right box',
    0x91C3D8: 'command box',
    0x91C470: 'defend/change box',
    0x91C508: 'defend/change box (2)',
    0x91C638: 'sub menu',
    0x91C768: 'sub menu (2)',
    0x91CE88: 'Manip box',
    0x91CF20: 'Coin box',
    0x91D180: 'limit box',
}

# The one site this module changes: the command box's WIDTH field.
# Record base + 4, because the record is (x, y, w, h) as int16.
SITE = 0x91C3D8 + 4
STOCK_W = 110
NEW_W = 112

# The records whose widths the hypothesis says should already be fine.
EXPECT_MULTIPLE_OF_8 = (0x91C470, 0x91C508, 0x91C638, 0x91C768,
                        0x91CE88, 0x91CF20, 0x91D180)


class Pe:
    """`ff7_en` as a VA-addressable, writable image."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = bytearray(self.path.read_bytes())
        d = self.data
        pe = struct.unpack('<I', d[0x3C:0x40])[0]
        nsec = struct.unpack('<H', d[pe + 6:pe + 8])[0]
        optsz = struct.unpack('<H', d[pe + 20:pe + 22])[0]
        off = pe + 24 + optsz
        self.sections = []
        for i in range(nsec):
            s = d[off + 40 * i: off + 40 * (i + 1)]
            name = bytes(s[:8]).rstrip(b'\0').decode('ascii', 'replace')
            vsize, va, rsize, raw = struct.unpack('<IIII', s[8:24])
            self.sections.append((name, va + 0x400000, raw, rsize, vsize))

    def off(self, va):
        for name, base, raw, rsize, vsize in self.sections:
            if base <= va < base + max(rsize, vsize):
                o = raw + (va - base)
                if o + 2 > len(self.data):
                    raise KeyError('va 0x%X past end of file' % va)
                return o
        raise KeyError('va 0x%X in no section' % va)

    def u16(self, va):
        o = self.off(va)
        return struct.unpack('<H', self.data[o:o + 2])[0]

    def rec(self, va):
        o = self.off(va)
        return struct.unpack('<4h', self.data[o:o + 8])

    def write_u16(self, va, v):
        o = self.off(va)
        self.data[o:o + 2] = struct.pack('<H', v)

    def save(self):
        self.path.write_bytes(bytes(self.data))


def state(pe):
    w = pe.u16(SITE)
    return 'PATCHED' if w == NEW_W else ('stock' if w == STOCK_W else 'UNKNOWN')


def show(path, log=print):
    pe = Pe(path)
    log('  %s' % path)
    log('  the battle window layout table:')
    for va, nm in sorted(RECORDS.items()):
        x, y, w, h = pe.rec(va)
        flag = '' if w % 8 == 0 else '   <-- w is NOT a multiple of 8'
        log('    0x%06X  %-22s x=%5d y=%5d w=%5d h=%5d%s'
            % (va, nm, x, y, w, h, flag))
    log('')
    log('  command box width at 0x%06X: %d  (%s)'
        % (SITE, pe.u16(SITE), state(pe)))


def apply(path, revert=False, log=print) -> int:
    pe = Pe(path)
    cur = pe.u16(SITE)
    want_from, want_to = (NEW_W, STOCK_W) if revert else (STOCK_W, NEW_W)
    if cur == want_to:
        log('  nothing to do -- command box width is already %d' % want_to)
        return 0
    if cur != want_from:
        log('  ! command box width at 0x%06X is %d, expected %d.'
            % (SITE, cur, want_from))
        log('  refusing to write.')
        return 1
    x, y, w, h = pe.rec(0x91C3D8)
    if (x, y, h) != (144, 340, 112):
        log('  ! the command box record reads (%d, %d, %d, %d); this build is '
            'not the one this module was measured against.' % (x, y, w, h))
        log('  refusing to write.')
        return 1
    pe.write_u16(SITE, want_to)
    pe.save()
    log('  command box width %d -> %d  @ 0x%06X (file offset 0x%X)'
        % (want_from, want_to, SITE, pe.off(SITE)))
    log('  2 byte(s) written to %s' % path)
    return 0


def verify(path, log=print) -> int:
    ok = fail = 0

    def chk(c, what):
        nonlocal ok, fail
        if c:
            ok += 1
            log('    ok    ' + what)
        else:
            fail += 1
            log('    FAIL  ' + what)

    pe = Pe(path)
    log('  the file is the one this was measured against:')
    x, y, w, h = pe.rec(0x91C3D8)
    chk((x, y, h) == (144, 340, 112),
        'the command box record is (%d, %d, w, %d)' % (x, y, h))
    chk(w in (STOCK_W, NEW_W),
        'its width is %d (stock %d or patched %d)' % (w, STOCK_W, NEW_W))

    log('  the measurement this experiment rests on:')
    chk(STOCK_W % 8 != 0,
        'the command box width %d is NOT a multiple of 8 (%d over)'
        % (STOCK_W, STOCK_W % 8))
    chk(NEW_W % 8 == 0, 'the proposed width %d IS a multiple of 8' % NEW_W)
    chk(NEW_W - STOCK_W == 2,
        'the change is %d game units -- 3 screen px at 1.5 px/unit, so a null '
        'result is invisible' % (NEW_W - STOCK_W))
    bad = []
    for va in EXPECT_MULTIPLE_OF_8:
        _, _, ww, _ = pe.rec(va)
        if ww % 8:
            bad.append((va, ww))
    chk(not bad,
        'every OTHER box in the table has a width divisible by 8 (%s)'
        % ('clean' if not bad else bad))

    log('  x=144 is what was measured on screen:')
    chk(x == 144,
        'the record says x=144 and the captured left border sits at game '
        'x=144.0, so this table really is what is drawn')

    log('  the site is the width field, not a neighbour:')
    chk(SITE == 0x91C3D8 + 4, 'the width is at record base + 4')
    chk(pe.u16(0x91C3D8) == 144, 'base + 0 is x = 144')
    chk(pe.u16(0x91C3D8 + 2) == 340, 'base + 2 is y = 340')
    chk(pe.u16(0x91C3D8 + 6) == 112, 'base + 6 is h = 112')

    log('')
    log('  %d check(s) pass, %d fail' % (ok, fail))
    log('')
    log('  WHAT THE OUTCOMES MEAN')
    log('    border appears  -> mod-8 truncation confirmed; the real fix is')
    log('                       the draw loop, (w + 7) / 8, which also fixes')
    log('                       the field dialogue boxes for free.')
    log('    no change       -> theory dead, cost 3 screen pixels. Revert.')
    return 1 if fail else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split('\n')[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('ff7_en', help='path to romfs/ff7/resources/ff7_1.02/ff7_en')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args(argv)
    if a.verify:
        return verify(a.ff7_en)
    if a.apply or a.revert:
        return apply(a.ff7_en, revert=a.revert)
    show(a.ff7_en)
    return 0


if __name__ == '__main__':
    sys.exit(main())
