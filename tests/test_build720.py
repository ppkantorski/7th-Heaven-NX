"""BUILD 720 -- ff7nx_strandtile for every field, with the picture match
that keeps the black 256px margin filler where it is, and jun_i1's post
rim cut (ff7nx_edgesmooth.cut_rim) instead of 718's paler recolour.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

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

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ff7nx_edgesmooth as ES                                  # noqa: E402
import ff7nx_strandtile as ST                                  # noqa: E402

try:
    import scipy                                    # noqa: F401
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False


class _Page:
    def __init__(self, idx):
        self.data = idx.astype(np.uint8).tobytes()


def _565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


class StrandMatch(unittest.TestCase):
    def setUp(self):
        self.pal = [np.zeros((256, 3), np.uint8)]
        self.pal[0][1] = (200, 100, 40)
        idx = np.zeros((256, 256), np.uint8)
        idx[16:32, 32:48] = 1                       # cell (2, 1) is art
        self.page = _Page(idx)

    def _hd(self, rgb):
        return np.full((48, 48), _565(*rgb), np.uint16)

    def test_scope_is_every_field(self):
        self.assertIsNone(ST.FIELDS)
        self.assertEqual(ST.VERSION, 720)

    def test_same_picture_moves(self):
        ok, why = ST._matches(self.pal, self.page, 0, (2, 1),
                              self._hd((200, 100, 40)))
        self.assertTrue(ok, why)

    def test_different_picture_stays(self):
        ok, why = ST._matches(self.pal, self.page, 0, (2, 1),
                              self._hd((40, 160, 220)))
        self.assertFalse(ok)
        self.assertIn('different picture', why)

    def test_black_filler_stays(self):
        ok, why = ST._matches(self.pal, self.page, 0, (0, 0),
                              self._hd((0, 0, 0)))
        self.assertFalse(ok)
        self.assertIn('black filler', why)

    def test_no_palettes_is_717(self):
        self.assertEqual(ST._matches(None, self.page, 0, (0, 0), None),
                         (True, None))


def _post():
    """A 20-wide solid post with a dark 2-texel rim on its right."""
    t = np.zeros((12, 30), np.uint16)
    t[:, :20] = _565(200, 180, 130)
    t[:, 18:20] = _565(40, 36, 26)
    return t


@unittest.skipUnless(HAVE_SCIPY, 'cut_rim needs scipy (the build does)')
class CutRim(unittest.TestCase):
    def test_jun_i1_targets(self):
        (layer, period, x0, fringe, guard, pals), = ES.TARGETS['jun_i1']
        self.assertEqual((layer, period, fringe, guard, pals),
                         (2, None, None, (), {3}))
        self.assertEqual(ES.CUT_RIM, {'jun_i1': (3, 0.8)})

    def test_dark_rim_is_keyed_where_something_is_behind(self):
        t = _post()
        out, cut, mix = ES.cut_rim(t, np.ones(t.shape, bool), 3, 0.8)
        self.assertEqual((cut, mix), (24, 0))
        self.assertTrue((out[:, 18:20] == 0).all())
        self.assertTrue((out[:, :18] == t[:, :18]).all())

    def test_never_a_hole(self):
        t = _post()
        out, cut, _ = ES.cut_rim(t, np.zeros(t.shape, bool), 3, 0.8)
        self.assertEqual(cut, 0)
        self.assertTrue((out == t).all())

    def test_a_different_record_beside_blends_instead(self):
        t = _post()
        sig = np.zeros(t.shape, np.uint64)
        sig[6:, 20:] = 7                    # lower half: another record
        beside = np.full(t.shape + (3,), np.nan, np.float32)
        beside[:, 20:] = (160, 160, 160)
        out, cut, mix = ES.cut_rim(t, np.ones(t.shape, bool), 3, 0.8,
                                   sig=sig, beside=beside)
        self.assertEqual((cut, mix), (12, 12))
        self.assertTrue((out[:6, 18:20] == 0).all())
        lower = ES._rgbf(out[6:, 18:20])
        self.assertTrue((lower[..., 0] > 160).all())  # not dark any more
        self.assertTrue((lower[..., 0] < 200).all())  # not post-pale


if __name__ == '__main__':
    unittest.main()
