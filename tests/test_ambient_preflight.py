#!/usr/bin/env python3
"""
tests/test_ambient_preflight.py -- an ambience loop that cannot be opened
must not close the software.

BUILD 524. Measured on hardware one switch at a time: only the field tick
matters, the loop's content does not, and `abort-hang-file` froze the game --
so New Game dies in NativeOggPlayer's constructor, where a failed
`fopen(".../data/music_ogg/ambient/NNNN.ogg")` prints "music file can not be
loaded" and calls exit(-1).

These run the REAL field cave under arm64emu, frame by frame, with a real
lookup table in memory and the libc calls stubbed, and check:

  * a failed open allocates and constructs NOTHING, and is retried;
  * the path we open is byte-for-byte the one the constructor will open;
  * the first frame the open succeeds, the loop starts exactly as before;
  * the old loop is still retired at once, whatever the new one does;
  * the stack, x25 and the live W8/W9 pair come back intact on every path.

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
import sys
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as EM                                          # noqa: E402
import ff7nx_ambient as AM                                     # noqa: E402
import ff7nx_audio_cave as AC                                  # noqa: E402

BSS = 0x3FEC4A8
POINTERS, POOL = 0x126CC00, 0x126CD00
BLOCK0, BLOCK1 = 0x500000, 0x500010
FIELD_WORD = 0x600000
BASE_STR = 0x610000
BASE = b'rom:/ff7/workingdir'
HEAP = 0x720000000
SP0 = 0x750001000
LO, HI = 100, 120
MD1STIN, MD1_1, BLACKBGH = 116, 117, 109
WANT_1503 = BASE + b'/data/music_ogg/ambient/1503.ogg'


def cstr(mem, at):
    out = bytearray()
    while True:
        c = mem.u(at + len(out), 1)
        if not c:
            return bytes(out)
        out.append(c)


class World:
    def __init__(self, builder=AM.build_field_cave):
        # Only the FIELD hook has a live W8/W9 pair to preserve; the battle
        # hook sits past a prologue where nothing volatile is live.
        self.check_pair = builder is AM.build_field_cave
        self.mem = EM.Mem()
        self.mem.setu(POINTERS, BLOCK0, 4)
        self.mem.setu(POINTERS + 4, BLOCK1, 4)
        self.mem.setu(BLOCK1 + (MD1STIN - LO - 12), 1, 1)
        self.mem.setu(BLOCK1 + (MD1_1 - LO - 12), 2, 1)
        self.mem.setu(POOL, 1503, 2)
        self.mem.setu(POOL + 2, 1511, 2)
        for i, c in enumerate(BASE + b'\0'):
            self.mem.setu(BASE_STR + i, c, 1)
        for i, c in enumerate(b'%s/data/music_ogg/%s.ogg\0'):
            self.mem.setu(AM.PATH_FORMAT + i, c, 1)
        for i, c in enumerate(b'rb\0'):
            self.mem.setu(AM.MODE_RB + i, c, 1)
        self.opens_ok = False
        self.calls = []
        self.opened = []
        self.next_heap = HEAP
        words = builder(0x400000, lambda i: 0x400000 + 4 * i, BSS,
                        POINTERS, POOL, LO, HI)
        self.code = {0x400000 + 4 * i: w for i, w in enumerate(words)}

        def malloc(cpu):
            self.calls.append(('new',))
            cpu.x[0] = self.next_heap
            self.next_heap += 0x1000

        def ctor(cpu):
            self.calls.append(('ctor', cstr(self.mem, cpu.x[1]).decode()))

        def sprintf(cpu):
            fmt = cstr(self.mem, cpu.x[1])
            a, b = cstr(self.mem, cpu.x[2]), cstr(self.mem, cpu.x[3])
            assert fmt == b'%s/data/music_ogg/%s.ogg', fmt
            out = a + b'/data/music_ogg/' + b + b'.ogg' + b'\0'
            for i, c in enumerate(out):
                self.mem.setu(cpu.x[0] + i, c, 1)

        def fopen(cpu):
            path = cstr(self.mem, cpu.x[0])
            assert cstr(self.mem, cpu.x[1]) == b'rb'
            self.opened.append(path)
            cpu.x[0] = 0x9999000 if self.opens_ok else 0

        def fclose(cpu):
            assert cpu.x[0] == 0x9999000
            self.calls.append(('fclose',))

        # Every native call clobbers the caller-saved registers, the way a
        # real callee may -- which is exactly what would expose a cave that
        # keeps something live in x0..x18 across one.
        def clobbering(fn):
            def run(cpu):
                fn(cpu)
                for r in range(1, 19):
                    cpu.x[r] = 0xDEAD0000 + r
            return run

        self.native = {k: clobbering(v) for k, v in {
            AC.GUEST_TRANSLATE: lambda c: c.x.__setitem__(0, FIELD_WORD),
            AM.BASE_PATH: lambda c: c.x.__setitem__(0, BASE_STR),
            AM.SPRINTF: sprintf,
            AM.FOPEN: fopen,
            AM.FCLOSE: fclose,
            AM.MALLOC: malloc,
            AM.OGG_CTOR: ctor,
            AM.OGG_PLAY: lambda c: self.calls.append(('play',)),
            AM.OGG_VOLUME: lambda c: None,
            AM.OGG_DELETE: lambda c: self.calls.append(('delete',)),
        }.items()}
        # GUEST_TRANSLATE must return its pointer, so it clobbers x1.. only.
        self.native[AC.GUEST_TRANSLATE] = lambda c: c.x.__setitem__(
            0, FIELD_WORD)

    def frame(self, field):
        self.mem.setu(FIELD_WORD, field, 2)
        cpu = EM.Cpu(self.mem, native=self.native)
        cpu.x = [0x740000000 + i * 0x100 for i in range(32)]
        cpu.x[8], cpu.x[9] = 0x8888, 0x9999
        cpu.x[25] = 0x7777777777
        cpu.sp = SP0
        cpu.run(0x400000, self.code)
        assert cpu.sp == SP0, 'stack not restored: %#x' % cpu.sp
        assert cpu.x[25] == 0x7777777777, 'x25 lost'
        if self.check_pair:
            assert (cpu.x[8] & 0xFFFFFFFF,
                    cpu.x[9] & 0xFFFFFFFF) == (0x8888, 0x9999), 'W8/W9 lost'

    def run(self, field, n=1):
        for _ in range(n):
            self.frame(field)
        out, self.calls = self.calls, []
        return out


class Preflight(unittest.TestCase):

    def test_a_loop_that_will_not_open_allocates_nothing(self):
        w = World()
        self.assertEqual(w.run(MD1STIN, 5), [])
        self.assertEqual(len(w.opened), 5, 'it must ask again every frame')

    def test_the_path_is_the_one_the_constructor_opens(self):
        w = World()
        w.run(MD1STIN)
        self.assertEqual(w.opened, [WANT_1503])

    def test_it_starts_the_first_frame_the_open_succeeds(self):
        w = World()
        w.run(MD1STIN, 10)
        w.opens_ok = True
        self.assertEqual(w.run(MD1STIN),
                         [('fclose',), ('new',), ('ctor', 'ambient/1503'),
                          ('play',)])
        self.assertEqual(w.run(MD1STIN, 50), [], 'started more than once')

    def test_when_it_opens_nothing_else_changes(self):
        """The good path is the shipped path plus one open and one close."""
        w = World()
        w.opens_ok = True
        self.assertEqual(w.run(MD1STIN),
                         [('fclose',), ('new',), ('ctor', 'ambient/1503'),
                          ('play',)])

    def test_the_new_game_route(self):
        """md1stin -> blackbgh -> md1stin -> md1_1, opens failing at first."""
        w = World()
        self.assertEqual(w.run(MD1STIN), [])
        self.assertEqual(w.run(BLACKBGH, 20), [])
        self.assertEqual(w.run(MD1STIN, 3), [])
        w.opens_ok = True
        self.assertEqual(w.run(MD1STIN)[-2:],
                         [('ctor', 'ambient/1503'), ('play',)])
        got = w.run(MD1_1)
        self.assertEqual(got[0], ('delete',),
                         'the old loop must be retired first')
        self.assertEqual(got[-2:], [('ctor', 'ambient/1511'), ('play',)])

    def test_the_old_loop_is_retired_even_if_the_new_one_cannot_open(self):
        w = World()
        w.opens_ok = True
        w.run(MD1STIN)
        w.opens_ok = False
        self.assertEqual(w.run(MD1_1, 5), [('delete',)])

    def test_the_battle_cave_is_unchanged(self):
        """Not implicated, and the padding pool is nearly full."""
        words = AM.build_battle_cave(0x400000, lambda i: 0x400000 + 4 * i,
                                     BSS, POINTERS, POOL, LO, HI)
        at = {0x400000 + 4 * i: w for i, w in enumerate(words)}
        targets = {AM.FOPEN, AM.BASE_PATH, AM.SPRINTF}
        for va, w in at.items():
            if (w & 0xFC000000) == 0x94000000:
                imm = w & 0x3FFFFFF
                if imm & (1 << 25):
                    imm -= 1 << 26
                self.assertNotIn(va + 4 * imm, targets)


if __name__ == '__main__':
    unittest.main(verbosity=2)
