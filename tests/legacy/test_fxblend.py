#!/usr/bin/env python3
"""Binary gate for the narrowly scoped truecolor additive FX pages.

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
import tempfile
import unittest
from unittest import mock

import arm64emu
import ff7nx_fieldbg as FB
import ff7nx_fxmargin as FX
import nxmap


HERE = os.path.dirname(os.path.abspath(__file__))
STOCK = os.path.join(HERE, 'dump', 'exefs', 'main')
LO, HI = 0x10DC3D8, 0x10DC4A0
REGISTER = 0x10DC4E0


class FxBlendBinary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = nxmap.Main(STOCK)
        cls.stock = {off: struct.unpack_from('<I', cls.main.text, off)[0]
                     for off in range(LO, HI, 4)}
        cls.patched = dict(cls.stock)
        for off, (_old, new, _why) in FB._fx_blend_words().items():
            cls.patched[off] = new

    def _run(self, code, slot, depth):
        calls = []
        mem = arm64emu.Mem()
        page = 0x10000
        mem.setu(page + 0x1A, depth, 2)

        def registered(cpu):
            calls.append((cpu.get(2, True), cpu.get(3, True),
                          cpu.get(4, True)))

        cpu = arm64emu.Cpu(mem, native={REGISTER: registered})
        cpu.set(8, depth, True)       # ldrh at +0x3d4 already executed
        cpu.set(23, slot)
        cpu.set(24, page)
        cpu.set(25, 4, True)          # stock opaque constant
        cpu.set(21, 111, True)
        cpu.set(19, 222, True)
        end = cpu.run(LO, None, code=code, start_pc=LO)
        self.assertEqual(end, HI)
        return calls

    def test_exactly_eight_verified_words(self):
        words = FB._fx_blend_words()
        self.assertEqual(set(words), set(FB.FX_BLEND_SITES))
        self.assertEqual(len(words), 8)
        for off, (old, _new, _why) in words.items():
            self.assertEqual(struct.unpack_from('<I', self.main.text, off)[0],
                             old)

    def test_every_depth1_registration_is_identical(self):
        for slot in range(29):
            self.assertEqual(self._run(self.stock, slot, 1),
                             self._run(self.patched, slot, 1), slot)

    def test_depth2_slots_15_23_additive_24_25_average(self):
        for slot in range(29):
            stock = self._run(self.stock, slot, 2)
            patched = self._run(self.patched, slot, 2)
            self.assertEqual(len(stock), 1, slot)
            self.assertEqual(len(patched), 1, slot)
            self.assertEqual(stock[0][2], 4, slot)
            self.assertEqual(patched[0][2],
                             1 if 15 <= slot < 24 else
                             0 if slot in (24, 25) else 4, slot)

    def test_real_module_diff_is_exact(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, 'main')
            self.assertTrue(FB.apply_to_nso(STOCK, out, lambda *_a: None,
                                            768, max_raw=4_000_000))
            patched = nxmap.Main(out)
            diff = {off for off in range(0, len(self.main.text), 4)
                    if self.main.text[off:off + 4]
                    != patched.text[off:off + 4]}
            self.assertEqual(diff,
                             set(FB.words(768, max_raw=4_000_000)))

    def test_one_rollback_disables_data_and_binary_halves(self):
        with mock.patch.dict(os.environ,
                             {FX.NO_TRUECOLOR_ENV: '1'}, clear=False):
            self.assertFalse(FX.truecolor_enabled())
            self.assertEqual(FB._fx_blend_words(), {})


if __name__ == '__main__':
    unittest.main()
