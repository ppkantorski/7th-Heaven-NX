#!/usr/bin/env python3
"""Regression gates for the exact fship_1/fship_12 black-wedge correction."""
import os
import sys
import unittest

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_fshipart as FA                                  # noqa: E402


class TestFshipArt(unittest.TestCase):

    def fixture(self, side=48):
        """A separate main shadow plus the left/bottom wedge fingerprint."""
        a = np.zeros((side, side), dtype='<u2')
        # Main strut shadow: top/right.  It must never be selected.
        a[:side // 3, side // 2:] = 0x0841
        # Wedge: isolated, touches left and bottom, ~9% of the cell.
        y0 = side * 7 // 16
        for y in range(y0, side):
            width = max(1, (y - y0 + 1) * side // (3 * (side - y0)))
            a[y, :width] = 0x0841
        return a

    def test_only_the_isolated_wedge_is_selected(self):
        a = self.fixture()
        comp = FA.artifact_component(a)
        self.assertIsNotNone(comp)
        self.assertTrue(all(x < a.shape[1] * 0.45 for y, x in comp))
        self.assertTrue(any(x == 0 for y, x in comp))
        self.assertTrue(any(y == a.shape[0] - 1 for y, x in comp))
        self.assertFalse(any(x >= a.shape[1] // 2 and y == 0
                             for y, x in comp),
                         'the main strut shadow was selected')

    def test_connected_or_oversized_dark_art_is_refused(self):
        a = self.fixture()
        a[:, :a.shape[1] // 2] = 0x0841
        self.assertIsNone(FA.artifact_component(a))

    def test_bright_geometry_is_not_part_of_the_mask(self):
        a = self.fixture()
        a[-1, :] = 0x2104       # visibly brighter than RGB(8,8,8)
        comp = FA.artifact_component(a)
        self.assertIsNone(comp,
                          'breaking the proven left/bottom fingerprint must '
                          'fail closed')

    def test_other_fields_are_byte_identical(self):
        raw = b'not even a field'
        out, changed = FA.apply_to_field(
            raw, lambda _raw: (), lambda _parts: b'', 'junonl2')
        self.assertIs(out, raw)
        self.assertEqual(changed, 0)

    def test_off_switch(self):
        os.environ[FA.OFF_ENV] = '1'
        try:
            self.assertTrue(FA.disabled())
        finally:
            del os.environ[FA.OFF_ENV]
        self.assertFalse(FA.disabled())


if __name__ == '__main__':
    unittest.main(verbosity=2)
