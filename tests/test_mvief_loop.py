#!/usr/bin/env python3
"""
The `wait until MVIEF reports 0` loop, and the one byte that retires it.

These build a field section 1 by hand rather than reading the archive, so the
test says what the code does without needing a 1.5 GB file present.

The shape under test, straight out of the shipped `bugin1c`:

    mvief 06 05
    if2   var[6][5] != 0  -> out
    back  0B

FFNx's `opcode_IFSW_compare_sub` fixes the comparison table (0 ==, 1 !=,
2 >, 3 <, 4 >=, 5 <=) and FF7 jumps when the test is FALSE, so that is
`do { v = MVIEF; } while (v != 0);`.  MVIEF's poll counter at guest
`0xCC0B70` is reset only by MOVIE and incremented by MVIEF itself, so 1140
polls into the scene the loop can never leave.

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

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import field_dis as FD                                         # noqa: E402
import field_mvief_patch as P                                  # noqa: E402

OP_RET = 0x00
OP_MVIEF = 0xFA
OP_IF2 = None          # resolved from the same table field_dis uses
OP_BACK = None

from PyFF7.field import OP                                     # noqa: E402
for _i, (_n, _s) in enumerate(OP):
    if _n == 'if2' and OP_IF2 is None:
        OP_IF2 = _i
    if _n == 'back' and OP_BACK is None:
        OP_BACK = _i


def if2(bank, addr, right, cmp_, jump):
    return bytes([OP_IF2, bank]) + struct.pack('<HH', addr, right) \
        + bytes([cmp_, jump])


def build(code, n_entities=1):
    """A minimal but real section 1: header, one actor, 32 entry offsets."""
    head_len = 32 + 8 * n_entities + 64 * n_entities
    body = bytes(code)
    string_table = head_len + len(body)
    head = struct.pack('<HBBHHH6x8s8s', 0x0502, n_entities, 0,
                       string_table, 0, 1, b'test', b'test')
    head += b'ACTOR\0\0\0' * n_entities
    for _ in range(n_entities):
        head += struct.pack('<32H', *([head_len] * 32))
    assert len(head) == head_len, (len(head), head_len)
    return head + body


MVIEF_LOOP = bytes([OP_MVIEF, 0x06, 0x05]) \
    + if2(0x60, 0x0005, 0x0000, P.CMP_NE, 0x03) \
    + bytes([OP_BACK, 0x0B, OP_RET])


class TheLoopIsRecognised(unittest.TestCase):

    def test_the_shipped_shape_is_found(self):
        sites = P.find_sites(build(MVIEF_LOOP))
        self.assertEqual(len(sites), 1, sites)
        self.assertEqual(sites[0][2], P.CMP_NE)

    def test_the_disassembly_reads_it_as_written(self):
        sec = build(MVIEF_LOOP)
        meta, names, entries = FD.parse(sec)
        blocks = FD._labelled(sec, meta, names, entries)
        ops = [x[1] for x in blocks[0][2]]
        self.assertEqual(ops[:3], ['mvief', 'if2', 'back'])

    def test_an_if_against_zero_with_no_mvief_is_left_alone(self):
        """The guard that keeps this off ordinary `if var == 0` code.

        Both conditions have to hold -- a literal zero AND an adjacent
        MVIEF -- or a field full of perfectly normal zero tests would be
        rewritten.
        """
        code = if2(0x60, 0x0005, 0x0000, P.CMP_NE, 0x03) + bytes([OP_RET])
        self.assertEqual(P.find_sites(build(code)), [])

    def test_a_threshold_loop_is_left_alone(self):
        """`while (v < 0x0491)` is vanilla's own spelling and terminates."""
        code = if2(0x60, 0x0005, 0x0491, 3, 0x06) \
            + bytes([OP_MVIEF, 0x06, 0x05, OP_BACK, 0x0B, OP_RET])
        self.assertEqual(P.find_sites(build(code)), [])


class ThePatchIsOneByte(unittest.TestCase):

    def test_the_comparison_becomes_le_and_nothing_else_moves(self):
        sec = bytearray(build(MVIEF_LOOP))
        off, _, cmp_ = P.find_sites(bytes(sec))[0]
        before = bytes(sec)
        sec[off + P.IF2_CMP_OFF] = P.CMP_LE
        after = bytes(sec)
        self.assertEqual(len(before), len(after))
        self.assertEqual(sum(a != b for a, b in zip(before, after)), 1)
        self.assertEqual(P.find_sites(after)[0][2], P.CMP_LE)

    def test_le_against_zero_cannot_spin_on_a_climbing_counter(self):
        """Why `<=` was chosen over anything cleverer.

        The loop body runs while the test is TRUE.  `v <= 0` is true only
        while the count is zero, and the count is incremented by the MVIEF
        that just ran -- so the loop leaves after at most two polls no
        matter which of MVIEF's two return paths is live.  `!=` is true for
        every value the counter will ever hold again.
        """
        def loops_while(cmp_, v):
            return {P.CMP_NE: v != 0, P.CMP_LE: v <= 0}[cmp_]
        for v in (0, 1, 2, 570, 1140):
            self.assertFalse(loops_while(P.CMP_LE, v) and v > 0)
        self.assertTrue(all(loops_while(P.CMP_NE, v)
                            for v in (1, 2, 570, 1140)))


import echo_s_flevel as EF                                     # noqa: E402


def with_actors(code, n_entities=1):
    return build(code, n_entities)


class TheBuildTimePass(unittest.TestCase):
    """`echo_s_flevel.retire_endless_mvief_waits` -- what a rebuild applies."""

    def setUp(self):
        for lst in (EF.RETIRED_MVIEF_WAITS, EF.KEPT_STOCK_MVIEF_WAITS,
                    EF.UNWALKABLE_MVIEF_SCAN):
            del lst[:]

    def test_it_retires_the_wait_and_changes_exactly_one_byte(self):
        sec = build(MVIEF_LOOP)
        out, changed = EF.retire_endless_mvief_waits(sec, None, 'testfield')
        self.assertEqual(changed, 1)
        self.assertEqual(len(out), len(sec))
        self.assertEqual(sum(a != b for a, b in zip(sec, out)), 1)
        self.assertEqual(EF.RETIRED_MVIEF_WAITS, ['testfield'])

    def test_a_wait_the_stock_script_already_has_is_left_alone(self):
        """seto1's case. The game's own script is not ours to rewrite."""
        sec = build(MVIEF_LOOP)
        out, changed = EF.retire_endless_mvief_waits(sec, sec, 'seto1')
        self.assertEqual(changed, 0)
        self.assertEqual(out, sec)
        self.assertEqual(EF.KEPT_STOCK_MVIEF_WAITS, ['seto1'])
        self.assertEqual(EF.RETIRED_MVIEF_WAITS, [])

    def test_a_new_wait_on_a_different_variable_is_still_retired(self):
        """The stock check matches bank and address, not the field name."""
        stock = build(MVIEF_LOOP)
        other = bytes([OP_MVIEF, 0x06, 0x09]) \
            + if2(0x60, 0x0009, 0x0000, P.CMP_NE, 0x03) \
            + bytes([OP_BACK, 0x0B, OP_RET])
        sec = build(other)
        out, changed = EF.retire_endless_mvief_waits(sec, stock, 'somewhere')
        self.assertEqual(changed, 1)

    def test_an_undecodable_tail_is_reported_and_never_fatal(self):
        """anfrst_1/3/4's case, reduced.

        `disable_pc_time_cycle_actor` points an actor's entries at a bare RET
        and leaves its old body behind as orphaned bytes. Those bytes are not
        reachable and need not decode. Refusing the field over them would
        break a build that has always accepted it.
        """
        sec = bytearray(build(MVIEF_LOOP + bytes([0x1A, 0x1A, 0x1A])))
        out, changed = EF.retire_endless_mvief_waits(bytes(sec), None, 'anfrst')
        self.assertEqual(changed, 1)          # the reachable site still found
        self.assertEqual(EF.UNWALKABLE_MVIEF_SCAN, ['anfrst'])

    def test_a_special_opcode_with_an_unknown_subcode_is_not_fatal(self):
        """The one that broke a build.

        `instruction_size` looks a SPECIAL (0x0F) subcode up in a dict that
        only holds 0xF5..0xFF, so any other byte after it raises KeyError --
        not IndexError, not ValueError.  Naming the exceptions was a bet on a
        third-party table's failure modes and it lost.  This pass must stop,
        report and change nothing for EVERY way the decode can fail.
        """
        sec = build(MVIEF_LOOP + bytes([0x0F, 0x14, 0x00]))
        out, changed = EF.retire_endless_mvief_waits(sec, None, 'mds7pb')
        self.assertEqual(changed, 1)
        self.assertEqual(EF.UNWALKABLE_MVIEF_SCAN, ['mds7pb'])

    def test_a_stock_script_it_cannot_read_retires_nothing(self):
        """Fail closed.

        If the stock script will not decode we cannot tell a mod's new wait
        from the game's own, and rewriting the game's own is the one outcome
        worth avoiding.
        """
        sec = build(MVIEF_LOOP)
        out, changed = EF.retire_endless_mvief_waits(sec, b'\x00' * 4, 'f')
        self.assertEqual(changed, 0)
        self.assertEqual(out, sec)

    def test_running_it_twice_is_a_no_op(self):
        """A rebuild over an already-corrected script must not drift."""
        sec = build(MVIEF_LOOP)
        once, first = EF.retire_endless_mvief_waits(sec, None, 'f')
        twice, second = EF.retire_endless_mvief_waits(once, None, 'f')
        self.assertEqual((first, second), (1, 0))
        self.assertEqual(once, twice)


if __name__ == '__main__':
    unittest.main(verbosity=2)
