#!/usr/bin/env python3
"""
tests/test_campreserve.py -- BUILD 533, the zero-travel SCR2D rule.

Executed under arm64emu:
  * a zero-travel field that is NOT an MVCAM field (tin_3) has its authored
    SCR2D x clamped to the pinned value -- the train black bar;
  * a zero-travel MVCAM field (southmk2) keeps it -- BUILD 513's reason;
  * a field with real travel (mds7pb_1) is clamped regardless of the bitmap;
  * over a sweep of ranges, deltas and vclip flags, the new cave writes
    exactly what ff7nx_camclamp's own cave writes whenever the bitmap bit is
    clear and the field has travel (the only case the two may not differ);
  * x20 survives, and the cave always leaves through INIT_RETURN_VA.
And on the dumped module: installs over camclamp, moves only the hook word
outside the SHIP part, and the bitmap is the maplist rule.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import random
import shutil
import struct
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as EM                                          # noqa: E402
import ff7nx_camclamp as K                                     # noqa: E402
import ff7nx_campreserve as R                                  # noqa: E402

DUMP_MAIN = os.path.join(_HERE, 'dump', 'exefs', 'main')
FLEVEL = os.path.join(_HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                      'field', 'flevel.lgp')
FLAT = 0x10000000
HDR_GUEST = 0x00D00000
CAVE = 0x400000
BITMAP = 0x480000
IDS = 800


def run(words, field_id, lo, hi, top, bottom, dx, dy, vclip, bits):
    mem = EM.Mem()
    mem.setu(FLAT + K.HDR_PTR, HDR_GUEST, 4)
    h = FLAT + HDR_GUEST
    for off, v in ((K.RANGE_LEFT, lo), (K.RANGE_RIGHT, hi),
                   (K.RANGE_TOP, top), (K.RANGE_BOTTOM, bottom)):
        mem.setu(h + off, v & 0xFFFF, 2)
    mem.setu(h + K.VCLIP_FLAG_OFF, vclip, 1)
    mem.setu(FLAT + K.DELTA_X, dx & 0xFFFF, 2)
    mem.setu(FLAT + K.DELTA_Y, dy & 0xFFFF, 2)
    mem.setu(FLAT + R.CURRENT_FIELD_ID, field_id, 2)
    for i, b in enumerate(bits):
        mem.setu(BITMAP + i, b, 1)

    def xlat(c):
        c.x[0] = FLAT + (c.x[0] & 0xFFFFFFFF)
        c.x[8] = c.x[9] = c.x[10] = 0x5A5A5A5A5A5A5A5A
    code = {CAVE + 4 * i: w for i, w in enumerate(words)}
    cpu = EM.Cpu(mem, native={K.XLAT: xlat})
    cpu.x = [0] * 32
    cpu.x[20] = 0xC0FFEE
    end = cpu.run(CAVE, code)
    s16 = lambda v: v - 0x10000 if v & 0x8000 else v
    return (end, s16(mem.u(FLAT + K.DELTA_X, 2)),
            s16(mem.u(FLAT + K.DELTA_Y, 2)), cpu.x[20])


def ours(vertical=True):
    return R.init_cave(lambda i: CAVE + 4 * i, BITMAP, IDS, vertical)


def stock(vertical=True):
    return K.init_cave_words(lambda i: CAVE + 4 * i, K.INIT_RETURN_VA,
                             vertical)


def bits_with(*ids):
    b = bytearray(IDS // 8)
    for i in ids:
        b[i >> 3] |= 1 << (i & 7)
    return bytes(b)


TIN_3, SOUTHMK2, MDS7PB_1 = 141, 200, 154


class Rule(unittest.TestCase):
    def test_tin_3_is_clamped(self):
        # shipped range -160..160 -> bounds [0, 0]; SCR2D x 16 -> delta -16
        end, dx, _dy, x20 = run(ours(), TIN_3, -160, 160, -136, 136,
                                -16, 0, 0, bits_with(SOUTHMK2))
        self.assertEqual(end, K.INIT_RETURN_VA)
        self.assertEqual(dx, 0)
        self.assertEqual(x20, 0xC0FFEE)

    def test_southmk2_keeps_its_movie_anchor(self):
        end, dx, dy, _ = run(ours(), SOUTHMK2, -160, 160, -120, 120,
                             -32, 32, 0, bits_with(SOUTHMK2))
        self.assertEqual(end, K.INIT_RETURN_VA)
        self.assertEqual((dx, dy), (-32, 32))

    def test_513_kept_tin_3_off_centre(self):
        """The regression, reproduced with ff7nx_camclamp's own cave."""
        _e, dx, _dy, _ = run(stock(), TIN_3, -160, 160, -136, 136,
                             -16, 0, 0, b'')
        self.assertEqual(dx, -16)

    def test_real_travel_is_clamped_even_in_an_mvcam_field(self):
        a = run(ours(), MDS7PB_1, -202, 202, -120, 120,
                96, 0, 0, bits_with(MDS7PB_1))
        b = run(stock(), MDS7PB_1, -202, 202, -120, 120, 96, 0, 0, b'')
        self.assertEqual(a, b)
        self.assertEqual(abs(a[1]), 42)

    def test_unknown_field_id_is_clamped(self):
        _e, dx, _dy, _ = run(ours(), IDS + 5, -160, 160, -120, 120,
                             -16, 0, 0, bits_with(SOUTHMK2))
        self.assertEqual(dx, 0)

    def test_agrees_with_camclamp_wherever_it_may(self):
        rnd = random.Random(533)
        for _ in range(400):
            half = rnd.choice((160, 170, 176, 202, 256, 300))
            lo, hi = -half + rnd.randint(-8, 8), half + rnd.randint(-8, 8)
            vh = rnd.choice((120, 128, 136, 150))
            top, bottom = -vh, vh
            dx, dy = rnd.randint(-200, 200), rnd.randint(-200, 200)
            vclip = rnd.choice((0, 1))
            fid = rnd.choice((TIN_3, SOUTHMK2))
            bits = bits_with(SOUTHMK2)
            a = run(ours(), fid, lo, hi, top, bottom, dx, dy, vclip, bits)
            travel = (hi - 160) > (lo + 160)
            if travel or fid == SOUTHMK2:
                b = run(stock(), fid, lo, hi, top, bottom, dx, dy, vclip, b'')
                self.assertEqual(a, b, (fid, lo, hi, top, bottom, dx, dy,
                                        vclip))
            elif lo + 160 == hi - 160:   # zero travel, not MVCAM: clamped
                self.assertEqual(a[1], -(lo + 160) if -(lo + 160) != 0 else 0,
                                 (lo, hi, dx))
            else:                        # lo > hi: nothing reachable, skip
                self.assertEqual(a[1], dx)

    def test_bitmap(self):
        names = ['dummy', 'southmk2', 'tin_3', 'white2']
        self.assertEqual(R.bitmap(names), bytes([0b1010]))


@unittest.skipUnless(os.path.exists(DUMP_MAIN) and os.path.exists(FLEVEL),
                     'needs the dump')
class Module(unittest.TestCase):
    def test_installs_over_camclamp(self):
        import ff7nx_audio_cave as AC
        import ff7nx_daynight
        import ff7nx_deadspace as DS
        import nxmap
        tmp = tempfile.mkdtemp()
        try:
            a = os.path.join(tmp, 'a')
            shutil.copy(DUMP_MAIN, a)
            self.assertEqual(K.apply(a, log=lambda *_: None), 0)
            b = os.path.join(tmp, 'b')
            maplist = ff7nx_daynight.read_maplist(FLEVEL)
            rep = R.apply_to_nso(a, b, maplist, DUMP_MAIN)
            ta = AC.segments(open(a, 'rb').read())[1][0]
            tb = AC.segments(open(b, 'rb').read())[1][0]
            lo, hi = DS.part(b, 'ship')
            changed = [va for va in range(0, len(ta), 4)
                       if ta[va:va + 4] != tb[va:va + 4]
                       and not lo <= va < hi]
            self.assertEqual(changed, [K.INIT_HOOK_VA])
            self.assertIn('southmk2', rep['preserved'])
            self.assertNotIn('tin_3', rep['preserved'])
            n = len(maplist)
            bm = tb[rep['bitmap']:rep['bitmap'] + (n + 7) // 8]
            self.assertEqual(bm, R.bitmap(maplist))
            # the probe still installs beside it, in its own part
            import ff7nx_frameprobe as P
            c = os.path.join(tmp, 'c')
            P.apply_to_nso(b, c, DUMP_MAIN)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
