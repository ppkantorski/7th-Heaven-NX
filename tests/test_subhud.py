#!/usr/bin/env python3
"""BUILD 770: submarine DEPTH/SPEED gauges toward the 16:9 edges.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import struct
import unittest
from unittest import mock

import ff7nx_subhud as S

STOCK = os.path.join(_nx_root, 'dump', 'exefs', 'main')


@unittest.skipUnless(os.path.isfile(STOCK), 'needs dump/exefs/main')
class SubHud(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import nxmap
        cls.text = bytes(nxmap.Main(STOCK).text)

    def test_29_words_each_a_mov_of_the_shifted_value(self):
        ws = S.words(bytearray(self.text), 67)
        self.assertEqual(len(ws), 29)
        for sites, half in ((S.INIT_SITES, True), (S.HUD_SITES, False)):
            for va, _w, val, side in sites:
                rd, got, _kind = S._value_of(ws[va][1])
                want = val + side * 67
                if half:
                    self.assertEqual((got - want) % 0x10000, 0, hex(va))
                else:
                    self.assertEqual(got, want, hex(va))
        # left and right move by the same amount, in opposite directions
        sides = {side for sites in (S.INIT_SITES, S.HUD_SITES)
                 for _va, _w, _v, side in sites}
        self.assertEqual(sides, {-1, 1})

    def test_constant_reads_keep_or_shift_exactly(self):
        ws = S.words(bytearray(self.text), 67)
        _rd, y, _ = S._value_of(ws[0x105E2A4][1])
        self.assertEqual(y, -238)                 # DEPTH label y unchanged
        _rd, x, _ = S._value_of(ws[0x106248C][1])
        self.assertEqual(x, 248 + 67)             # SPEED readout sprite 27

    def test_shared_register_for_glyph_u_is_left_stock(self):
        ws = S.words(bytearray(self.text), 67)
        self.assertNotIn(0x101DCC4, ws)

    def test_patch_is_idempotent_and_refuses_mixed_state(self):
        t = bytearray(self.text)
        self.assertEqual(len(S.patch_text(t, 67)), 29)
        self.assertEqual(S.patch_text(t, 67), {})
        t2 = bytearray(self.text)
        S.patch_text(t2, 67)
        struct.pack_into('<I', t2, 0x101C7E4,
                         struct.unpack_from('<I', self.text, 0x101C7E4)[0])
        with self.assertRaises(ValueError):
            S.patch_text(t2, 67)

    def test_anchor_moved_is_refused(self):
        t = bytearray(self.text)
        struct.pack_into('<I', t, 0x101DD74, 0xD503201F)
        with self.assertRaises(ValueError):
            S.patch_text(t, 67)

    def test_shift_env(self):
        with mock.patch.dict(os.environ, {S.SHIFT_ENV: '66'}):
            self.assertEqual(S.shift(), 66)
        with mock.patch.dict(os.environ, {S.SHIFT_ENV: 'x'}):
            self.assertEqual(S.shift(), S.DEFAULT_SHIFT)
        self.assertEqual(S.DEFAULT_SHIFT, 67)

    def test_off_switch(self):
        with mock.patch.dict(os.environ, {S.ENV: '0'}):
            self.assertFalse(S.enabled())


if __name__ == '__main__':
    unittest.main()
