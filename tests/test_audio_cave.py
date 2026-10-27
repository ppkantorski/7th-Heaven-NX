#!/usr/bin/env python3
"""
Decode every encoding `ff7nx_audio_cave` adds, with a tool that did not
produce it.

This is the same discipline `test_a64.py` applies and it exists for the same
reason: an encoding that looks plausible in hex and decodes to the wrong
register produces a structurally valid module that misbehaves on hardware, and
only an independent decoder catches that. capstone is that decoder.

The emit-sequence helpers (`mov32`, `bss_ptr`, `save_host`, `restore_host`)
are checked as SEQUENCES -- the words they append, in order, disassembled as a
block -- because their correctness is a property of the sequence, not of any
one word. `save_host`/`restore_host` additionally have to be exact inverses,
which is asserted directly rather than read off the listing.

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import a64 as A                                                 # noqa: E402
import ff7nx_audio_cave as AC                                   # noqa: E402

MD = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)


def decode(words, pc=0x400000):
    blob = struct.pack('<%dI' % len(words), *words)
    return ['%s %s' % (i.mnemonic, i.op_str) if i.op_str else i.mnemonic
            for i in MD.disasm(blob, pc)]


def one(word, pc=0x400000):
    got = decode([word], pc)
    return got[0] if got else None


class Encoders(unittest.TestCase):
    def test_stp_ldp_sp_offsets(self):
        for off in (0, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70):
            self.assertEqual(one(AC.stp_off(21, 22, off)),
                             'stp x21, x22, [sp, #%#x]' % off if off
                             else 'stp x21, x22, [sp]')
            self.assertEqual(one(AC.ldp_off(23, 24, off)),
                             'ldp x23, x24, [sp, #%#x]' % off if off
                             else 'ldp x23, x24, [sp]')

    def test_stp_ldp_reject_bad_offsets(self):
        for bad in (4, 12, -8, 512 * 8):
            self.assertRaises(ValueError, AC.stp_off, 19, 20, bad)
            self.assertRaises(ValueError, AC.ldp_off, 19, 20, bad)

    def test_mov64_is_64_bit(self):
        # The whole point: a host pointer must not be truncated to 32 bits.
        for dst, src in ((0, 19), (20, 0), (28, 27)):
            self.assertEqual(one(AC.mov64(dst, src)),
                             'mov x%d, x%d' % (dst, src))

    def test_table_index_forms(self):
        self.assertEqual(one(AC.ldrb_uxtw(8, 20, 9)),
                         'ldrb w8, [x20, w9, uxtw]')
        self.assertEqual(one(AC.ldrh_uxtw_scaled(8, 20, 9)),
                         'ldrh w8, [x20, w9, uxtw #1]')
        self.assertEqual(one(AC.add_x_uxtw(21, 20, 9)),
                         'add x21, x20, w9, uxtw')

    def test_arithmetic(self):
        self.assertEqual(one(AC.udiv(8, 9, 10)), 'udiv w8, w9, w10')
        self.assertEqual(one(AC.msub(8, 9, 10, 11)),
                         'msub w8, w9, w10, w11')


class Sequences(unittest.TestCase):
    def asm(self, at=0x400000):
        return AC.Asm(at, lambda i: at + 4 * i)

    def test_mov32_materialises_the_whole_constant(self):
        for value in (0, 1, 0xFFFF, 0x10000, 0xCC0D8A, 0xDE0E84, 0xFFFFFFFF):
            a = self.asm()
            AC.mov32(a, 8, value)
            self.assertEqual(len(a.words), 2, 'mov32 must always be 2 words')
            listing = decode(a.words)
            self.assertEqual(listing[0], 'mov w8, #%#x' % (value & 0xFFFF)
                             if (value & 0xFFFF) >= 10
                             else 'mov w8, #%d' % (value & 0xFFFF))
            self.assertTrue(listing[1].startswith('movk w8,'), listing[1])

    def test_bss_ptr_reaches_its_target(self):
        # adrp gives the 4 KB page, add gives the low 12 bits: together they
        # must name the exact address from wherever the cave landed.
        for pc in (0x400000, 0x401004, 0x1152000):
            for target in (0x3FEC4A8, 0x3FEE328, 0x4000000):
                a = self.asm(pc)
                AC.bss_ptr(a, 20, target)
                listing = decode(a.words, pc)
                page = int(listing[0].split('#')[1], 16)
                low = int(listing[1].split('#')[1], 16) if '#' in listing[1] else 0
                self.assertEqual(page + low, target,
                                 'bss_ptr(%#x) from pc %#x resolved to %#x'
                                 % (target, pc, page + low))

    def test_save_and_restore_host_are_exact_inverses(self):
        save, restore = self.asm(), self.asm()
        AC.save_host(save)
        AC.restore_host(restore)
        s = decode(save.words)
        r = decode(restore.words)
        self.assertEqual(len(s), 6)
        self.assertEqual(len(r), 6)
        # Same register pairs, same slots, opposite direction and order.
        pairs_s = [x.replace('stp', '').split(',')[0:2] for x in s]
        pairs_r = [x.replace('ldp', '').split(',')[0:2] for x in reversed(r)]
        self.assertEqual(pairs_s, pairs_r)
        self.assertTrue(s[0].startswith('stp x19, x20, [sp, #-0x80]!'), s[0])
        self.assertTrue(r[-1].startswith('ldp x19, x20, [sp], #0x80'), r[-1])

    def test_save_host_leaves_the_condition_metadata_slot_free(self):
        # +0x60..+0x6f must stay unused: the field playback cave snapshots the
        # recompiler's condition metadata there, and an earlier revision that
        # put it at +0x00 overwrote saved X19/X20 before restore_host read it.
        a = self.asm()
        AC.save_host(a)
        used = set()
        for line in decode(a.words):
            if '#-' in line:
                used.update((0, 8))
            elif '#' in line.split('[')[-1]:
                off = int(line.split('#')[-1].rstrip(']'), 16)
                used.update((off, off + 8))
        self.assertNotIn(0x60, used)
        self.assertNotIn(0x68, used)

    def test_save_host_rejects_a_frame_that_cannot_hold_it(self):
        a = self.asm()
        self.assertRaises(ValueError, AC.save_host, a, 0x60)
        self.assertRaises(ValueError, AC.save_host, a, 0x88)


class LabelAssembler(unittest.TestCase):
    def test_branches_resolve_against_a_scattered_layout(self):
        # The layout emit_laid_out really produces: words spread over holes
        # far apart. Every branch must still name its label's real address.
        holes = [0x400000, 0x400004, 0x480000, 0x480004, 0x4C0000, 0x4C0004]
        a = AC.Asm(holes[0], lambda i: holes[i])
        a.bcond('done', A.NE)
        a.cbz64(19, 'done')
        a.b('done')
        a.emit(A.nop())
        a.label('done')
        a.emit(A.ret())
        a.emit(A.nop())
        words = a.resolve()
        self.assertEqual(len(words), 6)
        for i, want_target in ((0, holes[4]), (1, holes[4]), (2, holes[4])):
            listing = decode([words[i]], holes[i])
            self.assertIn(hex(want_target), listing[0],
                          'word %d from %#x: %s' % (i, holes[i], listing[0]))

    def test_word_count_is_independent_of_layout(self):
        # emit_laid_out calls a builder twice and compares lengths; a builder
        # whose word count moved with the addresses would be a silent
        # corruption, so the assembler must never vary its own.
        def build(addr):
            a = AC.Asm(addr(0), addr)
            a.b('end')
            AC.mov32(a, 8, 0xCC0D8A)
            AC.bss_ptr(a, 20, 0x3FEC4A8)
            a.label('end')
            a.emit(A.ret())
            return a.resolve()
        contiguous = build(lambda i: 0x400000 + 4 * i)
        scattered = build(lambda i: 0x400000 + 0x10000 * i)
        self.assertEqual(len(contiguous), len(scattered))


class ModuleHelpers(unittest.TestCase):
    MAIN = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'exefs', 'main')

    def setUp(self):
        if not os.path.isfile(self.MAIN):
            self.skipTest('exefs/main not available')
        with open(self.MAIN, 'rb') as f:
            self.blob = f.read()

    def test_segments_round_trip_through_pack(self):
        segs, raw = AC.segments(self.blob)
        out = AC.pack(self.blob, raw, 0)
        segs2, raw2 = AC.segments(out)
        self.assertEqual(raw, raw2, 'repack changed a segment')
        for a, b in zip(segs, segs2):
            self.assertEqual(a[1:], b[1:], 'repack moved a segment')
        self.assertEqual(struct.unpack_from('<I', out, 0x3C),
                         struct.unpack_from('<I', self.blob, 0x3C))

    def test_pack_grows_bss_by_exactly_what_was_asked(self):
        _segs, raw = AC.segments(self.blob)
        old, = struct.unpack_from('<I', self.blob, 0x3C)
        for grow in (4, 8, 36, 232):
            out = AC.pack(self.blob, raw, grow)
            new, = struct.unpack_from('<I', out, 0x3C)
            self.assertEqual(new, old + grow)

    def test_scratch_base_is_the_page_aligned_end_of_data(self):
        segs, _raw = AC.segments(self.blob)
        bss, = struct.unpack_from('<I', self.blob, 0x3C)
        got = AC.scratch_base(self.blob, segs)
        raw_end = segs[2][1] + segs[2][2]
        self.assertEqual(got, ((raw_end + 0xFFF) & ~0xFFF) + bss)
        # And specifically NOT the raw end -- that is the bug that crashed on
        # entering battle, and it is 0x328 bytes away on this module.
        self.assertNotEqual(got, raw_end + bss)

    def test_expect_word_accepts_the_stock_sites_and_refuses_others(self):
        _segs, raw = AC.segments(self.blob)
        text = raw[0]
        AC.expect_word(text, AC.LOAD_HOOK, AC.LOAD_ORIG, 'sfx_load')
        AC.expect_word(text, AC.PLAYER_HOOK, AC.PLAYER_ORIG, 'play_sfx')
        AC.expect_word(text, AC.PROXY_LOAD_HOOK, AC.PROXY_LOAD_ORIG, 'proxy')
        self.assertRaises(ValueError, AC.expect_word, text,
                          AC.LOAD_HOOK, 0xDEADBEEF, 'sfx_load')

    def test_player_resume_lands_after_the_displaced_sequence(self):
        # Three instructions are displaced at PLAYER_HOOK, not one. If the
        # resume offset ever drifts, the cave either re-runs the translator
        # call or skips the ldr that feeds w8 -- both silent, both wrong.
        _segs, raw = AC.segments(self.blob)
        text = raw[0]
        self.assertEqual(AC.PLAYER_RESUME, AC.PLAYER_HOOK + 12)
        mid, = struct.unpack_from('<I', text, AC.PLAYER_HOOK + 4)
        self.assertEqual(mid & 0xFC000000, 0x94000000, 'expected a bl')
        tail, = struct.unpack_from('<I', text, AC.PLAYER_HOOK + 8)
        self.assertEqual(tail, 0xB9400008, 'expected ldr w8, [x0]')

    def test_reserved_physical_rows_are_inside_the_fixed_archive(self):
        import audio_dat
        for slot in (AC.FOOTSTEP_SLOT,) + AC.BARRET_SLOTS:
            self.assertTrue(1 <= slot <= audio_dat.NUM_SLOTS,
                            'slot %d is outside the fixed 750 records' % slot)


if __name__ == '__main__':
    unittest.main(verbosity=2)
