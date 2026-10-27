#!/usr/bin/env python3
"""
tests/test_calendar.py -- the menu date row, executed rather than reasoned
about.

The cave is built through the REAL allocator, so it is chained across whatever
padding holes the module has left, and then run in `arm64emu` at those real
addresses with the guest state, the guest clock bytes and the four drawing
helpers all faked. Every draw the cave makes is recorded with the arguments it
pushed, which is the only way to check a layout without a screenshot.

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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import a64 as A                                                # noqa: E402
import arm64emu                                                # noqa: E402
import ff7nx_audio_cave as AC                                  # noqa: E402
import ff7nx_cave                                              # noqa: E402
import ff7nx_calendar as C                                     # noqa: E402
import ff7nx_tables                                            # noqa: E402
import nxmap                                                   # noqa: E402

DUMP = os.path.join(ROOT, 'dump', 'exefs', 'main')

# The fake address space. Guest addresses are lifted by a constant so a
# translate is observable and a host pointer can never be mistaken for one.
GUEST_LIFT = 0x40000000
STATE = 0x10000000                    # x24, the guest register block
EBP = 0x00200000                      # a guest frame
ESP = 0x001F0000
BOX_X = 200
BOX_Y = 314                           # what the patched `mov w8, #0x13a` sets
RETURN_TO = 0x00D02EC4                # the instruction after the hooked `bl`


def _module():
    with open(DUMP, 'rb') as handle:
        blob = handle.read()
    segs, raw, own = ff7nx_tables.open_module(blob)
    return blob, own


def _w32(mem, addr, val):
    mem.setu(addr, val, 4)


def _w8(mem, addr, val):
    mem.setu(addr, val, 1)


def _r32(mem, addr):
    return mem.u(addr, 4)


class Harness:
    """A CPU with the guest world and the four draw helpers stubbed out."""

    def __init__(self, day=7, month=3):
        self.mem = arm64emu.Mem()
        self.draws = []
        self.cpu = arm64emu.Cpu(self.mem, native={
            AC.GUEST_TRANSLATE: self._translate,
            C.DRAW_CHAR: self._record('char', 5),
            C.DRAW_NUMBER: self._record('number', 6),
            C.DRAW_STRING: self._record('string', 5),
        })
        self.cpu.sp = 0x7FFF0000
        self.cpu.x[30] = RETURN_TO
        self.cpu.x[24] = STATE
        _w32(self.mem, STATE + C.ST_ESP, ESP)
        _w32(self.mem, STATE + C.ST_EBP, EBP)
        _w32(self.mem, GUEST_LIFT + EBP - 4, BOX_X)
        _w32(self.mem, GUEST_LIFT + EBP - 0x0C, BOX_Y)
        _w8(self.mem, GUEST_LIFT + C.G_DAYS, day)
        _w8(self.mem, GUEST_LIFT + C.G_MONTHS, month)
        _w8(self.mem, GUEST_LIFT + C.G_HOURS, 21)
        _w8(self.mem, GUEST_LIFT + C.G_MINUTES, 45)

    def _translate(self, cpu):
        cpu.translate_calls += 1
        cpu.x[0] = GUEST_LIFT + (cpu.x[0] & 0xFFFFFFFF)
        for reg in list(range(1, 19)) + [30]:
            cpu.x[reg] = 0xDEADBEEF          # the translator's real contract

    def _record(self, kind, args):
        def call(cpu):
            esp = _r32(self.mem, STATE + C.ST_ESP)
            values = [_r32(self.mem, GUEST_LIFT + esp + 4 + 4 * i)
                      for i in range(args)]
            self.draws.append((kind, values))
            # the recompiled callee pops its own return slot
            _w32(self.mem, STATE + C.ST_ESP, esp + 4)
        return call


def _lay_out(build):
    """Build one cave through the real allocator and return (entry, code)."""
    blob, space = _module()
    pool = ff7nx_cave.HolePool(space.text,
                               starts=set(nxmap.Main(DUMP).arm_starts))
    table = space.place('calendar-months', C.month_table(), align=4)
    entry, words = ff7nx_cave.emit_laid_out(
        pool, lambda cave, at: build(cave, at, table))
    return entry, words, table


class DateRow(unittest.TestCase):

    def setUp(self):
        self.entry, self.code, self.table = _lay_out(
            lambda cave, at, table: C.build_date_cave(
                cave, at, table, C.layout_from_env()))

    def _run(self, day=7, month=3):
        h = Harness(day=day, month=month)
        # the month table the cave indexes lives in the module, so put it
        # where the cave's own adrp+add will look for it
        for i, b in enumerate(C.month_table()):
            _w8(h.mem, self.table + i, b)
        out = h.cpu.run(self.entry, self.code, start_pc=self.entry,
                        max_steps=400000)
        self.assertEqual(out, RETURN_TO,
                         'the cave returns to the instruction after the hook, '
                         'which means it restored the x30 it was called with '
                         'even though GUEST_TRANSLATE destroyed it nine times')
        return h

    def test_it_draws_the_gil_label_first(self):
        h = self._run()
        self.assertEqual(h.draws[0][0], 'string')
        self.assertEqual(len(h.draws), 3,
                         'the displaced Gil draw, then Date, then the month')

    def test_it_uses_the_string_helper_and_not_the_character_one(self):
        """The two do not share a character set. Drawing `Date` through the
        character helper put Thai glyphs in the box on hardware."""
        h = self._run()
        self.assertEqual([k for k, _ in h.draws], ['string'] * 3)
        self.assertFalse([k for k, _ in h.draws if k == 'char'])

    def test_it_writes_Date_and_the_month_where_the_Hext_file_does(self):
        h = self._run(day=7, month=3)      # April
        label = [h.mem.u(GUEST_LIFT + C.LABEL_AT + i, 1) for i in range(5)]
        self.assertEqual(label, [C._char(c) for c in 'Date'] + [C.TERMINATOR])
        month = [h.mem.u(GUEST_LIFT + C.MONTH_AT + i, 1) for i in range(4)]
        self.assertEqual(month, [C._char(c) for c in 'Apr'] + [C.TERMINATOR])
        # ... and hands those exact pointers to the helper
        self.assertEqual([v[2] for _k, v in h.draws[1:]],
                         [C.LABEL_AT, C.MONTH_AT])

    def test_both_strings_land_inside_the_box_on_the_third_line(self):
        h = self._run()
        month_x, _sep_x, _day_x = C.layout_from_env()
        got_x = [v[0] for _k, v in h.draws[1:]]
        got_y = [v[1] for _k, v in h.draws[1:]]
        self.assertEqual(got_x, [BOX_X + C.LABEL_X, BOX_X + month_x])
        self.assertEqual(got_y, [BOX_Y + C.ROW3_LABEL_Y] * 2)
        for x in got_x:
            self.assertLess(x - BOX_X, C.BOX_WIDTH)
        for y in got_y:
            self.assertLess(y - BOX_Y, 0x68, 'inside the taller box')

    def test_the_Date_label_starts_in_the_same_column_as_Gil(self):
        """Gil's label X is an unconditional +6. The Time label's is a csel
        that picked the other value on hardware, which this nops out."""
        self.assertEqual(C.LABEL_X, 6)
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
        md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        word = struct.pack('<I', C.TIME_LABEL_X_ORIG)
        shown = next(md.disasm(word, C.TIME_LABEL_X_SITE))
        self.assertEqual(shown.mnemonic, 'csel',
                         'the instruction being pinned is still the select')
        self.assertIn('w9', shown.op_str)
        # and it is replaced by a nop, which leaves the 6 loaded before it
        self.assertEqual(next(md.disasm(struct.pack('<I', A.nop()),
                                        0)).mnemonic, 'nop')

    def test_the_day_ends_in_the_same_column_as_the_minutes(self):
        """Which is what "right-aligned with Time and Gil" means here.

        The day IS the seconds draw: same helper, same two digits. Sharing
        the minutes' X therefore makes the right edges identical, rather
        than nearly identical, and this is the check that keeps it so.
        """
        _month_x, _sep_x, day_x = C.layout_from_env()
        self.assertEqual(day_x, C.CLOCK_MINUTES_X)
        minutes = [new for _s, _w, new, what in C.CONSTANTS
                   if what == 'minutes X']
        self.assertEqual(len(minutes), 1)
        self.assertEqual((minutes[0] >> 10) & 0xFFF, day_x,
                         'the immediate actually written into the time row')
        self.assertLessEqual(day_x + 2 * C.CHAR_WIDTH, C.BOX_WIDTH)

    def test_the_day_and_separator_are_moved_onto_that_row_too(self):
        """Their X and Y are constants, so this is the only check they get:
        they must land on the third line, right of the month, in order."""
        month_x, sep_x, day_x = C.layout_from_env()
        rows = dict((what, (site, new)) for site, _w, new, what in C.CONSTANTS)
        self.assertLess(month_x + 2 * C.CHAR_WIDTH, sep_x, 'month, then /')
        self.assertLess(sep_x, day_x, '/ then the day')
        self.assertLess(day_x + 2 * C.CHAR_WIDTH, C.BOX_WIDTH)
        for what in ('the day X', 'the day Y', 'separator X', 'separator Y'):
            self.assertIn(what, rows)
        # the month must still clear the Date label on its left
        self.assertGreater(month_x, C.LABEL_X + 4 * C.CHAR_WIDTH)

    def test_the_colour_and_scale_are_the_ones_the_box_already_uses(self):
        h = self._run()
        for _kind, v in h.draws[1:]:
            self.assertEqual(v[-1], C.TEXT_SCALE)
            self.assertEqual(v[-2], C.TEXT_COLOUR)

    def test_it_fits_the_window_the_padding_pool_actually_has(self):
        """355 words did not, and the pass skipped itself in build 344."""
        words = C.build_date_cave([], lambda i: 4 * i, 0x1200000,
                                  C.layout_from_env())
        self.assertLess(len(words), 140,
                        'the widest window in the shipped main held 147')

    def test_every_month_indexes_its_own_three_letters(self):
        for index, name in enumerate(C.MONTHS):
            h = self._run(month=index)
            written = [h.mem.u(GUEST_LIFT + C.MONTH_AT + i, 1)
                       for i in range(3)]
            self.assertEqual(written, [C._char(c) for c in name], name)

    def test_a_savemap_that_has_never_held_a_month_cannot_index_the_table(self):
        """A byte of 0xFF must not read 765 bytes past a 36-byte table."""
        for rubbish in (12, 13, 200, 255):
            h = self._run(month=rubbish)
            written = [h.mem.u(GUEST_LIFT + C.MONTH_AT + i, 1)
                       for i in range(3)]
            self.assertEqual(written, [C._char(c) for c in 'Jan'],
                             'month %d' % rubbish)

    def test_the_guest_stack_is_left_exactly_where_the_callee_left_it(self):
        h = self._run()
        # one call in, one call's worth of cleanup: nine draws, each balanced
        self.assertEqual(_r32(h.mem, STATE + C.ST_ESP), ESP + 4,
                         'the displaced callee popped its return slot and '
                         'every draw this cave added balanced itself')

    def test_x19_to_x28_come_back_unchanged(self):
        """The site is a `bl`, so the cave owes the caller what a callee owes."""
        h = Harness()
        for i, b in enumerate(C.month_table()):
            _w8(h.mem, self.table + i, b)
        marks = {r: 0x1100 + r for r in range(19, 29) if r != 24}
        for r, v in marks.items():
            h.cpu.x[r] = v
        h.cpu.run(self.entry, self.code, start_pc=self.entry, max_steps=400000)
        for r, v in marks.items():
            self.assertEqual(h.cpu.x[r], v, 'x%d' % r)
        self.assertEqual(h.cpu.x[24], STATE, 'the guest state is untouched')


class ClockSource(unittest.TestCase):

    def _run(self, guest, reg, value):
        blob, space = _module()
        pool = ff7nx_cave.HolePool(space.text,
                                   starts=set(nxmap.Main(DUMP).arm_starts))
        entry, code = ff7nx_cave.emit_laid_out(
            pool, lambda cave, at: C.build_clock_cave(cave, at, guest, reg))
        h = Harness()
        _w8(h.mem, GUEST_LIFT + guest, value)
        h.cpu.x[0] = 0xABCD1234           # the caller's live guest stack slot
        h.cpu.x[reg] = 0x99999999
        h.cpu.run(entry, code, start_pc=entry)
        return h

    def test_each_site_gets_its_own_byte_in_its_own_register(self):
        for _site, _want, reg, guest, what in C.CLOCK_SITES:
            h = self._run(guest, reg, 21)
            self.assertEqual(h.cpu.x[reg], 21, what)

    def test_it_gives_the_caller_back_the_x0_it_was_holding(self):
        """x0 is the stack slot the next instruction translates. A load
        clobbers nothing, so neither may the call that replaced it."""
        h = self._run(C.G_MINUTES, 22, 45)
        self.assertEqual(h.cpu.x[22], 45)
        self.assertEqual(h.cpu.x[0] & 0xFFFFFFFF, 0xABCD1234)

    def test_the_separator_cave_writes_a_slash_and_keeps_x0(self):
        blob, space = _module()
        pool = ff7nx_cave.HolePool(space.text,
                                   starts=set(nxmap.Main(DUMP).arm_starts))
        entry, code = ff7nx_cave.emit_laid_out(pool, C.build_separator_cave)
        h = Harness()
        h.cpu.x[0] = 0x50000000
        h.cpu.run(entry, code, start_pc=entry)
        self.assertEqual(_r32(h.mem, 0x50000000), C.SEPARATOR)
        self.assertEqual(h.cpu.x[0], 0x50000000)


class Installation(unittest.TestCase):

    def test_every_word_it_overwrites_is_the_word_it_expects(self):
        _blob, space = _module()
        C.check_sites(space.text)          # raises if any site has moved

    def test_with_the_padding_pool_full_it_installs_in_its_dead_space_part(self):
        """BUILD 596: builds 372-377 logged `NoRoom: need 7 more word(s)` and
        shipped without the calendar once the day/night pass had grown."""
        import tempfile
        from unittest import mock
        import ff7nx_deadspace as DS
        real = ff7nx_cave.HolePool

        def full(img, *a, **k):
            return real(img, holes=[])
        out = os.path.join(tempfile.mkdtemp(), 'main')
        with mock.patch.object(ff7nx_cave, 'HolePool', full):
            r = C.apply_to_nso(DUMP, out, stock=DUMP)
        lo, hi = DS.part(DUMP, 'calendar')
        self.assertTrue(lo <= r['entry'] < hi)
        for where in r['clock'].values():
            self.assertTrue(lo <= where < hi)
        _segs, raw = AC.segments(open(out, 'rb').read())
        got = struct.unpack_from('<I', raw[0], C.DATE_HOOK)[0]
        self.assertEqual(got, A.bl(C.DATE_HOOK, r['entry']))

    def test_the_calendar_part_does_not_overlap_the_probe_or_the_far_field(self):
        import ff7nx_deadspace as DS
        c = DS.part(DUMP, 'calendar')
        for other in ('canim', 'probe', 'far'):
            o = DS.part(DUMP, other)
            self.assertTrue(c[1] <= o[0] or o[1] <= c[0], other)

    def test_it_refuses_a_module_whose_sites_have_moved(self):
        _blob, space = _module()
        struct.pack_into('<I', space.text, C.DATE_HOOK, 0xD503201F)   # nop
        with self.assertRaises(Exception):
            C.check_sites(space.text)

    def test_it_refuses_to_write_a_string_over_live_guest_data(self):
        import tempfile
        image = bytearray(0x600000)
        path = os.path.join(tempfile.mkdtemp(), 'ff7_en')
        with open(path, 'wb') as handle:
            handle.write(image)
        C.check_guest(path)                      # all zero: fine
        image[C.guest_offset(C.MONTH_AT)] = 0x41
        with open(path, 'wb') as handle:
            handle.write(image)
        with self.assertRaises(ValueError):
            C.check_guest(path)

    def test_it_is_gated_on_exactly_what_the_cycle_itself_is_gated_on(self):
        """A menu clock is only worth having if the clock behind it runs.

        Read out of build.py rather than asserted: both passes must ask the
        same two questions before they touch anything.
        """
        source = open(nxpaths.src_file('build.py')).read()

        def gate(name):
            body = source[source.index('def %s(' % name):]
            body = body[:body.index('\n    log(')]
            return [line.strip() for line in body.split('\n')
                    if line.strip().startswith('if not ')]

        cycle = gate('apply_daynight')
        calendar = gate('apply_calendar')
        self.assertIn('if not plan.echo_fields:', calendar)
        self.assertIn('if not _echo_daynight_requested(plan):', calendar)
        for condition in cycle:
            self.assertIn(condition, calendar,
                          'the cycle checks %r and the calendar does not'
                          % condition)
        self.assertIn('if not ff7nx_calendar.enabled():', calendar,
                      'and its own switch on top')

    def test_the_guest_parking_spots_are_where_the_Hext_file_puts_them(self):
        self.assertEqual(C.LABEL_AT, 0x915000)
        self.assertEqual(C.MONTH_AT, 0x913FB1)
        self.assertIsNotNone(C.guest_offset(C.LABEL_AT))
        self.assertIsNotNone(C.guest_offset(C.MONTH_AT))

    def test_the_month_table_is_the_mod_s_own(self):
        """config.toml's month_N_char_M, which is where these came from."""
        table = C.month_table()
        self.assertEqual(len(table), 36)
        self.assertEqual(list(table[0:3]), [42, 65, 78])       # Jan
        self.assertEqual(list(table[3:6]), [38, 69, 66])       # Feb
        self.assertEqual(list(table[33:36]), [36, 69, 67])     # Dec

    def test_the_constants_only_ever_change_one_immediate(self):
        for site, want, new, what in C.CONSTANTS:
            self.assertNotEqual(want, new, what)
            # same opcode, same registers: only the immediate field moved
            if (want >> 24) == 0x11:                    # add wD, wN, #imm
                self.assertEqual(want & 0x3FF, new & 0x3FF, what)
                self.assertEqual(want >> 22, new >> 22, what)
            else:                                       # movz wD, #imm
                self.assertEqual(want & 0x1F, new & 0x1F, what)
                self.assertEqual(want >> 21, new >> 21, what)

    def test_the_layout_setting_refuses_what_would_fall_out_of_the_box(self):
        old = os.environ.get(C.LAYOUT_ENV)
        try:
            os.environ[C.LAYOUT_ENV] = '0x3A,0x50,0xF0'
            with self.assertRaises(ValueError):
                C.layout_from_env()
            os.environ[C.LAYOUT_ENV] = '0x30,0x44,0x52'
            self.assertEqual(C.layout_from_env(), (0x30, 0x44, 0x52))
        finally:
            os.environ.pop(C.LAYOUT_ENV, None)
            if old is not None:
                os.environ[C.LAYOUT_ENV] = old


if __name__ == '__main__':
    unittest.main(verbosity=2)
