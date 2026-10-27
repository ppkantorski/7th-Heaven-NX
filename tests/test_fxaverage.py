#!/usr/bin/env python3
"""BUILD 768: average-blend (slots 24..25) FX pages from Cosmos HD art.

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
import unittest
from unittest import mock

import numpy as np

import ff7nx_fieldbg as FB
import ff7nx_fxpages as FP
import field_bg_native as FN

PX = 768


def _page(fill=(200, 120, 40)):
    img = np.zeros((PX, PX, 4), np.uint8)
    img[..., :3] = fill
    return img


class Encoding(unittest.TestCase):
    def test_cut_out_keys_transparent_and_lifts_black(self):
        img = _page((0, 0, 0))
        img[:48, :48, 3] = 255          # one opaque, pure-black cell
        img[:48, 48:96, 3] = 100        # below the cut
        v = np.frombuffer(FP._encode_average(img, PX), '<u2').reshape(PX, PX)
        self.assertTrue((v[:48, :48] != FN.EMPTY).all())
        self.assertTrue((v[:48, 48:96] == FN.EMPTY).all())
        self.assertTrue((v[100:, 100:] == FN.EMPTY).all())

    def test_dark_rim_takes_interior_colour_inside_its_cell_only(self):
        img = _page((200, 120, 40))
        img[0:48, 0:48, 3] = 255
        img[0:48, 0:48, :3] = (200, 120, 40)
        img[0:48, 46:48, :3] = 0         # painted black rim, cell (0,0)
        img[0:48, 48:96, 3] = 255        # neighbour cell, solid blue
        img[0:48, 48:96, :3] = (0, 0, 255)
        img[0:48, 60:96, 3] = 0          # ...whose right part is cut
        out = FP._edge_colour(img, PX)
        # Cell (0,0) is fully kept: no rim inside the cell, untouched.
        self.assertTrue((out[:48, 46:48] == 0).all())
        img[0:48, 40:46, 3] = 255
        img[0:48, 44:46, 3] = 0          # now a real cut beside the rim
        img[0:48, 46:48, 3] = 0
        img[0:48, 42:44, :3] = 0
        out = FP._edge_colour(img, PX)
        self.assertTrue((out[:48, 42:44, 0] > 150).all())
        # nothing blue bled into cell (0,0)
        self.assertTrue((out[:48, :48, 2] < 100).all())

    def test_solid_and_bright_rim_untouched(self):
        img = _page((180, 180, 180))
        img[:48, :24, 3] = 255
        out = FP._edge_colour(img, PX)
        np.testing.assert_array_equal(out, img[..., :3])


class BakedEdge(unittest.TestCase):
    """BUILD 769: anti-aliased edge baked against the art underneath."""
    def _cell(self):
        img = np.zeros((PX, PX, 4), np.uint8)
        yy, xx = np.mgrid[:48, :48]
        disc = (yy - 24) ** 2 + (xx - 24) ** 2 < 15 ** 2
        img[:48, :48, :3] = (250, 200, 40)
        img[:48, :48, 3] = np.where(disc, 255, 0)
        return img, disc

    def test_edge_is_a_ramp_toward_the_art_under_it(self):
        img, disc = self._cell()
        under = np.full((48, 48, 3), (100, 0, 0), np.float32)
        out = FP._bake_edges(img, PX, {(0, 0): under})
        c = out[:48, :48]
        # interior keeps the fire, far outside stays the key
        self.assertTrue((c[24, 24, :3] == (250, 200, 40)).all())
        self.assertEqual(c[0, 0, 3], 0)
        # a row across the edge: red channel falls monotonically, and some
        # texels sit strictly between fire and under (the ramp)
        row = c[24, :24, 1].astype(int)
        drawn = c[24, :24, 3] > 0
        mid = row[drawn]
        self.assertTrue(((mid > 0) & (mid < 200)).any())
        self.assertTrue(np.all(np.diff(mid) >= 0))
        # it grew: coverage reaches past the authored disc
        self.assertGreater(int((c[..., 3] > 0).sum()), int(disc.sum()))

    def test_no_under_means_hard_cut_unchanged(self):
        img, _ = self._cell()
        np.testing.assert_array_equal(FP._bake_edges(img, PX, {}), img)

    def test_off_switch(self):
        self.assertTrue(hasattr(FP, 'AVG_AA'))


class Gates(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(FP.BANDS['additive'], (0x0F, 0x18, 1))
        self.assertEqual(FP.BANDS['average'], (0x18, 0x1A, 0))

    def test_env_off_switches(self):
        with mock.patch.dict(os.environ, {FP.NO_AVERAGE_ENV: '1'}):
            self.assertFalse(FP.average_enabled())
            out, st = FP.upgrade_section9('x', b'', None, PX, band='average')
            self.assertEqual(out, b'')
        with mock.patch.dict(os.environ, {FB.FX_TRUECOLOR_DISABLE_ENV: '1'}):
            self.assertFalse(FP.average_enabled())
            self.assertEqual(FB._fx_blend_words(), {})

    def test_ladder_has_eight_words_including_dead_slot(self):
        w = FB._fx_blend_words()
        self.assertEqual(len(w), 8)
        self.assertIn(0x10DC414, w)
        self.assertEqual(w[0x10DC414][0], 0x14000003)


@unittest.skipUnless(os.environ.get('NX_PRISILA_BUILT'), 'NX_PRISILA_BUILT')
class Prisila(unittest.TestCase):
    """Real built prisila (lzs payload) with a provider pointed at the DDS."""
    def test_only_page_24_changes(self):
        import lgp
        import diag_common as DC
        b = open(os.environ['NX_PRISILA_BUILT'], 'rb').read()
        parts = list(lgp.split_sections(lgp.lzs_decompress(b[4:])))
        dds = os.environ['NX_PRISILA_DDS']

        class R:
            def read(self, e):
                return open(e, 'rb').read()

        class Prov:
            slots = {('prisila', 24, 4): ('r', dds)}
            readers = {'r': R()}
            ambiguous_slots = set()
            any_state_slots = set()
            state_slots = {}

        def art(*_a):
            return None
        art.provider = Prov()
        out, st = FP.upgrade_section9('prisila', parts[8], art, PX,
                                      band='average')
        self.assertEqual(st['page_names'], ['prisila:24'])
        self.assertEqual(st['tiles'], 80)
        a, ts, _te, _ = DC.parse_pages(parts[8])
        b2, _, _, _ = DC.parse_pages(out)
        self.assertEqual(out[:ts], parts[8][:ts])
        for p, q in zip(a, b2):
            if p is None:
                continue
            if p.slot == 24:
                self.assertEqual((q.depth, q.px), (2, PX))
            else:
                self.assertEqual((p.depth, p.data), (q.depth, q.data))


if __name__ == '__main__':
    unittest.main()
