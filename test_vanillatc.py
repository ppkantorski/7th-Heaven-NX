#!/usr/bin/env python3
"""
test_vanillatc.py -- FINDINGS-282.

Unit tests run anywhere; the archive tests skip when no flevel.lgp is present.
"""
import os
import sys
import unittest

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import field_bg_native as FN                                   # noqa: E402
import ff7nx_vanillatc as VT                                   # noqa: E402
import lgp                                                     # noqa: E402

CANDIDATES = [
    os.path.join(_HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data', 'field',
                 'flevel.lgp'),
    os.path.join(_HERE, 'flevel.wide.lgp'),
]
FLEVEL = next((p for p in CANDIDATES if os.path.exists(p)), None)

S = 3                      # 768 / 256
PX = 256 * S


def _art(px, fill=0x1234, hmask=None):
    class A:
        pass
    a = A()
    a.px = px
    a.buf = np.full((px, px), np.uint16(fill), np.uint16).tobytes()
    a.hmask = np.ones((px, px), bool) if hmask is None else hmask
    return a


class TestCellGates(unittest.TestCase):
    """`convert_page` is the whole risk surface. One gate at a time."""

    def setUp(self):
        self.van = np.full((256, 256), np.uint16(0x0841), np.uint16)
        self.art = np.full((PX, PX), np.uint16(0x1234), np.uint16)
        self.dst = np.repeat(np.repeat(self.van, S, axis=0), S, axis=1).copy()

    def test_an_agreeing_cell_is_substituted(self):
        n, t = VT.convert_page(self.dst, self.van, self.art, {(0, 0)}, PX)
        self.assertEqual(n, 1)
        self.assertEqual(t, (VT.TILE * S) ** 2)
        self.assertTrue((self.dst[:VT.TILE * S, :VT.TILE * S] == 0x1234).all())

    def test_an_unreferenced_cell_is_never_touched(self):
        VT.convert_page(self.dst, self.van, self.art, {(0, 0)}, PX)
        rest = self.dst[VT.TILE * S:, :]
        self.assertTrue((rest == 0x0841).all(),
                        'a cell no tile samples was rewritten')

    def test_a_cell_cosmos_would_FILL_is_refused(self):
        """Vanilla clear, Cosmos opaque -- the silhouette would change."""
        self.van[0, 0] = 0
        self.dst = np.repeat(np.repeat(self.van, S, axis=0), S, axis=1).copy()
        n, _t = VT.convert_page(self.dst, self.van, self.art, {(0, 0)}, PX)
        self.assertEqual(n, 0)

    def test_a_cell_cosmos_would_CUT_is_refused(self):
        """Vanilla opaque, Cosmos clear -- a hole the artist did not draw."""
        self.art[0:S, 0:S] = 0
        n, _t = VT.convert_page(self.dst, self.van, self.art, {(0, 0)}, PX)
        self.assertEqual(n, 0)

    def test_sub_cell_detail_is_allowed(self):
        """Cosmos may vary INSIDE a cell -- that is the whole point.

        What it may not do is change whether the cell paints at all. A cell
        that is opaque in both, with different colours in every texel, must
        substitute: it is 9 texels of Cosmos where there was one of 1997.
        """
        self.art[:VT.TILE * S, :VT.TILE * S] = np.arange(
            (VT.TILE * S) ** 2, dtype=np.uint16).reshape(VT.TILE * S, -1) | 1
        n, _t = VT.convert_page(self.dst, self.van, self.art, {(0, 0)}, PX)
        self.assertEqual(n, 1)

    def test_a_cell_another_pass_already_wrote_is_left_alone(self):
        """GATE 2: only the untouched upscale may be replaced."""
        self.dst[0, 0] = 0x7777
        n, _t = VT.convert_page(self.dst, self.van, self.art, {(0, 0)}, PX)
        self.assertEqual(n, 0)
        self.assertEqual(int(self.dst[0, 0]), 0x7777)

    def test_the_opacity_reduction_is_ANY_not_ALL(self):
        """A cell Cosmos only antialiases into reads as a disagreement.

        `_opaque_16` calls a cell opaque as soon as the mod paints ONE texel
        in it, which is the conservative direction: it produces a refusal, not
        a substitution, wherever the two sources disagree at all.
        """
        blk = np.zeros((VT.TILE * S, VT.TILE * S), np.uint16)
        blk[0, 0] = 0x1234
        m = VT._opaque_16(blk, S)
        self.assertTrue(bool(m[0, 0]))
        self.assertEqual(int(m.sum()), 1)


@unittest.skipIf(FLEVEL is None, 'no flevel.lgp')
class TestAgainstTheArchive(unittest.TestCase):

    def parts(self, name):
        arch = lgp.Archive(FLEVEL)
        return lgp.split_sections(arch.decompressed(arch.index[name]))

    def test_a_promoted_page_is_never_a_candidate(self):
        """Only a page VANILLA shipped as depth-2 may be rewritten.

        A page `field_bg_dense` promoted is depth-2 too, and its pixels were
        chosen cell by cell with far more care than a whole-cell copy. The
        `was_d2` set is taken from the ORIGINAL section for exactly this.
        """
        p = self.parts('cosmo')
        v9 = p[8]
        vpages, _s, _e = FN.parse_texture_block(v9, FN.VANILLA_PX)
        was = {q.slot for q in vpages if q is not None and q.depth == 2}
        self.assertEqual(was, {26, 27},
                         'cosmo ships exactly two vanilla depth-2 pages')
        self.assertNotIn(15, was, 'slot 15 is paletted and gets promoted')

    def test_it_is_a_no_op_without_art(self):
        p = self.parts('cosmo')
        new9, _k = FN.resize_section9(p[8], PX)
        out, st = VT.apply_to_section9(new9, p[8], PX, lambda _pg, _pal: None)
        self.assertEqual(out, new9)
        self.assertEqual(st.pages, 0)
        self.assertEqual(st.no_art, 2)

    def test_the_off_switch(self):
        os.environ[VT.OFF_ENV] = '1'
        try:
            self.assertTrue(VT.disabled())
            p = self.parts('cosmo')
            new9, _k = FN.resize_section9(p[8], PX)
            out, st = VT.apply_to_section9(new9, p[8], PX,
                                           lambda _pg, _pal: _art(PX))
            self.assertEqual(out, new9)
            self.assertEqual(st.pages, 0)
        finally:
            del os.environ[VT.OFF_ENV]
        self.assertFalse(VT.disabled())

    def test_a_page_whose_art_is_the_wrong_size_is_refused_whole(self):
        p = self.parts('cosmo')
        new9, _k = FN.resize_section9(p[8], PX)
        out, st = VT.apply_to_section9(new9, p[8], PX,
                                       lambda _pg, _pal: _art(512))
        self.assertEqual(out, new9)
        self.assertEqual(st.refused_page, 2)

    def test_an_opaque_texel_at_0x0000_refuses_the_page(self):
        """It would punch a hole: 0x0000 is the colour key on depth 2."""
        p = self.parts('cosmo')
        new9, _k = FN.resize_section9(p[8], PX)
        bad = _art(PX, fill=0)                      # opaque everywhere, all 0
        out, st = VT.apply_to_section9(new9, p[8], PX, lambda _pg, _pal: bad)
        self.assertEqual(out, new9)
        self.assertEqual(st.refused_page, 2)

    def test_an_ambiguous_page_is_refused(self):
        """Several DDS states = an animated FX page. Substituting collapses it."""
        p = self.parts('cosmo')
        new9, _k = FN.resize_section9(p[8], PX)
        out, st = VT.apply_to_section9(
            new9, p[8], PX, lambda _pg, _pal: _art(PX), field='cosmo',
            ambiguous={('cosmo', 26, 0), ('cosmo', 27, 0)})
        self.assertEqual(out, new9)
        self.assertEqual(st.ambiguous, 2)

    def test_referenced_cells_finds_only_sampled_cells(self):
        p = self.parts('cosmo')
        s9 = p[8]
        refs = VT.referenced_cells(s9, s9.find(b'BACK'), s9.find(b'TEXTURE'),
                                   {26, 27})
        self.assertTrue(refs[26], 'cosmo samples slot 26')
        for cells in refs.values():
            for cx, cy in cells:
                self.assertLess(cx, 16)
                self.assertLess(cy, 16)

    def test_the_section_length_never_changes(self):
        """No page, slot, palette, uv, tile or header word moves."""
        p = self.parts('cosmo')
        new9, _k = FN.resize_section9(p[8], PX)
        out, st = VT.apply_to_section9(new9, p[8], PX,
                                       lambda _pg, _pal: _art(PX))
        self.assertGreater(st.pages, 0)
        self.assertEqual(len(out), len(new9))
        a, as_, ae = FN.parse_texture_block(new9, PX)
        b, bs, be = FN.parse_texture_block(out, PX)
        self.assertEqual((as_, ae), (bs, be))
        self.assertEqual(new9[:as_], out[:bs])
        self.assertEqual(new9[ae:], out[be:])
        self.assertEqual([(q.slot, q.depth, q.px, q.size_flag)
                          for q in a if q is not None],
                         [(q.slot, q.depth, q.px, q.size_flag)
                          for q in b if q is not None])


if __name__ == '__main__':
    unittest.main(verbosity=2)
