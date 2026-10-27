#!/usr/bin/env python3
"""
The sequential-SFX bridge: route packing, cave shape, and a real patch.

WHAT THIS IS GUARDING
=====================
Three separate classes of failure, each of which has actually happened on
hardware in this feature's history and none of which a "does it run" check
would catch:

  1. A ROUTE FIELD THAT WRAPS. The route word packs slot, count and stride
     into 32 bits. A stride one bit too wide does not raise -- it silently
     becomes a different, valid-looking route, and the cave then selects a
     descriptor belonging to an unrelated sound.
  2. A CAVE WHOSE WORD COUNT DEPENDS ON ITS ADDRESSES. `emit_laid_out` builds
     the cave twice, once on a pretend contiguous layout to count words and
     once on the real scattered one. If those disagree the cave is corrupt,
     so the builder's length must not move with the layout.
  3. A TABLE THAT DID NOT SURVIVE THE REPACK. The table lands in whichever
     tail had room, and the module is recompressed afterwards. Reading it
     back out of the finished file -- through the segment that really holds
     it -- is the only check that covers both.

The archive invariant (`audio.fmt` stays at exactly 750 records) is asserted
too, because every retired approach in this feature's history broke it, and it
is the one property that makes the whole design safe.

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
import struct
import sys
import unittest

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import a64 as A                                                 # noqa: E402
import ff7nx_audio_cave as AC                                   # noqa: E402
import ff7nx_sfxshuffle as SH                                   # noqa: E402

MD = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
MAIN = os.path.join(ROOT, 'dump', 'exefs', 'main')


def sample_plan(n=58, first_id=1):
    """A plan shaped like the real Cosmo one: 58 routes, 2..7 variants each."""
    routes = {}
    for i in range(n):
        routes[first_id + i] = {'count': (i % 6) + 2,
                                'payload_base': 90_000_000 + i * 200_000,
                                'stride': 16_384 + i}
    return {'compact': True, 'routes': routes}


class RouteWord(unittest.TestCase):
    def test_round_trips_at_every_field_boundary(self):
        for slot, count, stride in ((0, 2, 1),
                                    (17, 6, 18_432),
                                    (749, 7, (1 << 19) - 1)):
            w = SH.route_word(slot, count, stride)
            self.assertEqual(w & 0x3FF, slot)
            self.assertEqual((w >> 10) & 7, count)
            self.assertEqual(w >> 13, stride)

    def test_refuses_every_field_that_would_wrap(self):
        for bad in ((1 << 10, 2, 1),        # slot too wide
                    (0, 1, 1),              # a single variant is not a route
                    (0, 1 << 3, 1),         # count too wide
                    (0, 2, 0),              # zero stride
                    (0, 2, 1 << 19)):       # stride too wide
            self.assertRaises(ValueError, SH.route_word, *bad)

    def test_normalise_refuses_ids_outside_the_fixed_archive(self):
        for bad_id in (0, 751, 1000):
            plan = {'compact': True,
                    'routes': {bad_id: {'count': 2, 'payload_base': 0,
                                        'stride': 16}}}
            self.assertRaises(ValueError, SH.normalise, plan)

    def test_normalise_refuses_a_non_compact_plan(self):
        self.assertRaises(ValueError, SH.normalise, {'routes': {}})
        self.assertRaises(ValueError, SH.normalise, None)

    def test_table_is_eight_bytes_per_route_in_slot_order(self):
        plan = sample_plan(4)
        routes = SH.normalise(plan)
        blob = SH.table_bytes(routes)
        self.assertEqual(len(blob), 8 * len(routes))
        for k, (slot, count, base, stride) in enumerate(routes):
            word, pbase = struct.unpack_from('<II', blob, 8 * k)
            self.assertEqual(word, SH.route_word(slot, count, stride))
            self.assertEqual(pbase, base)


class Cave(unittest.TestCase):
    def build(self, addr, n=58):
        return SH.build_cave(addr(0), addr, 0x3FEC4A8, 0x126CCB0, n)

    def test_word_count_does_not_depend_on_the_layout(self):
        contiguous = self.build(lambda i: 0x400000 + 4 * i)
        scattered = self.build(lambda i: 0x400000 + 0x8000 * i)
        self.assertEqual(len(contiguous), len(scattered))

    def test_every_word_decodes(self):
        words = self.build(lambda i: 0x400000 + 4 * i)
        for k, w in enumerate(words):
            got = list(MD.disasm(struct.pack('<I', w), 0x400000 + 4 * k))
            self.assertTrue(got, 'word %d (%08X) does not decode' % (k, w))

    def test_it_replays_the_displaced_instruction_first(self):
        # The cave is entered by a branch written OVER `sub w19, w8, #1`, so
        # if it does not replay that instruction the requested sound id is
        # never computed and every route misses.
        words = self.build(lambda i: 0x400000 + 4 * i)
        self.assertEqual(words[0], AC.LOAD_ORIG)

    def test_it_returns_to_the_instruction_after_the_hook(self):
        words = self.build(lambda i: 0x400000 + 4 * i)
        last = list(MD.disasm(struct.pack('<I', words[-1]),
                              0x400000 + 4 * (len(words) - 1)))[0]
        self.assertEqual(last.mnemonic, 'b')
        self.assertEqual(int(last.op_str.lstrip('#'), 16), AC.LOAD_RESUME)

    def test_saved_registers_are_restored_on_both_exits(self):
        # x20..x25 are saved once on entry. Both the "no route matched" exit
        # and the "descriptor replaced" exit fall through the same epilogue,
        # so the counts must balance exactly -- an unbalanced pair is what
        # aborted the Barret loader probe on hardware.
        words = self.build(lambda i: 0x400000 + 4 * i)
        listing = [MD.disasm(struct.pack('<I', w), 0x400000 + 4 * k)
                   for k, w in enumerate(words)]
        mnem = []
        for dis in listing:
            for i in dis:
                mnem.append(i.mnemonic)
        self.assertEqual(mnem.count('stp'), mnem.count('ldp'),
                         'stp/ldp are unbalanced: %r' % (mnem,))


class RealPatch(unittest.TestCase):
    """Patch a real module and read the result back out of the finished file."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(MAIN):
            raise unittest.SkipTest('exefs/main not available')

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix='sfxshuffle-')
        self.dest = os.path.join(self.tmp, 'main')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_applies_to_the_stock_module_and_verifies(self):
        plan = sample_plan()
        report = SH.apply_to_nso(MAIN, self.dest, plan)
        self.assertEqual(report['routes'], 58)
        self.assertEqual(report['table_bytes'], 8 * 58)
        self.assertEqual(report['bss_bytes'], 4 * 58)

        with open(self.dest, 'rb') as f:
            out = f.read()
        segs, raw = AC.segments(out)        # re-verifies all three hashes

        # the hook really is a branch to the cave
        word, = struct.unpack_from('<I', raw[0], AC.LOAD_HOOK)
        self.assertEqual(word, A.b(AC.LOAD_HOOK, report['cave_entry']))

        # every route survives, read through whichever segment holds it
        for k, (slot, count, base, stride) in enumerate(SH.normalise(plan)):
            va = report['table_va'] + 8 * k
            self.assertEqual(AC.read_word(segs, raw, va),
                             SH.route_word(slot, count, stride))
            self.assertEqual(AC.read_word(segs, raw, va + 4), base)

    def test_bss_grows_by_one_cursor_per_route_and_nothing_else(self):
        with open(MAIN, 'rb') as f:
            before, = struct.unpack_from('<I', f.read(0x40), 0x3C)
        report = SH.apply_to_nso(MAIN, self.dest, sample_plan())
        with open(self.dest, 'rb') as f:
            after, = struct.unpack_from('<I', f.read(0x40), 0x3C)
        self.assertEqual(after - before, report['bss_bytes'])

    def test_it_refuses_a_module_whose_hook_is_already_taken(self):
        # Install once, then try again on the output. The second attempt must
        # refuse rather than branch over the first cave's entry.
        SH.apply_to_nso(MAIN, self.dest, sample_plan())
        again = self.dest + '.2'
        self.assertRaises(ValueError,
                          SH.apply_to_nso, self.dest, again, sample_plan())

    def test_the_archive_record_count_is_not_this_pass_s_business(self):
        # The whole design exists so audio.fmt stays at 750 records. This pass
        # touches only the module, so the assertion is that it writes NO
        # archive file at all -- if that ever changes, the 750 invariant has
        # a second author and this test should fail loudly.
        import audio_dat
        self.assertEqual(audio_dat.NUM_SLOTS, 750)
        SH.apply_to_nso(MAIN, self.dest, sample_plan())
        self.assertEqual(sorted(os.listdir(self.tmp)), ['main'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
