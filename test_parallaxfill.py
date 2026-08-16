#!/usr/bin/env python3
"""
test_parallaxfill.py -- FINDINGS-207.

Runs against the shipped flevel if there is one, else the dump's. Every check
that needs an archive is skipped rather than failed when neither exists, so
this is safe to run anywhere.
"""
import os
import struct
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import lgp                                                     # noqa: E402
import ff7nx_parallaxfill as PF                                # noqa: E402
import _psurvey as S                                           # noqa: E402

# THE DUMP FIRST, AND ON PURPOSE. `sdout` holds the LAST BUILD'S OUTPUT, which
# already carries this pass's rows -- so "does it fill?" measured there is
# always "no, it is a fixed point", which is true and useless. The vanilla
# dump is the only unfilled archive on disk.
CANDIDATES = [
    os.path.join(_HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data', 'field',
                 'flevel.lgp'),
    os.path.join(_HERE, 'sdout', 'atmosphere', 'contents', '0100A5B00BDC6000',
                 'romfs', 'ff7', 'workingdir', 'data', 'field', 'flevel.lgp'),
]
FLEVEL = next((p for p in CANDIDATES if os.path.exists(p)), None)
FIELD = 'wcrimb_2'


def field_parts(name=FIELD):
    arch = lgp.Archive(FLEVEL)
    return lgp.split_sections(arch.decompressed(arch.index[name]))


@unittest.skipIf(FLEVEL is None, 'no flevel.lgp')
class TestParallaxFill(unittest.TestCase):

    def setUp(self):
        self.parts = field_parts()
        self.sec7, self.sec9 = self.parts[7], self.parts[8]
        self.new9, self.added = PF.apply_to_section9(self.sec9, self.sec7)

    def test_it_fills_the_reported_field(self):
        self.assertTrue(self.added, '%s should need rows' % FIELD)
        self.assertIn(3, self.added)

    def test_the_top_gap_closes(self):
        h = S.trigger_header(self.sec7)
        for layer, rows_before in S.layer_rows(self.sec9).items():
            if layer not in (3, 4):
                continue
            H = h['bg3_h'] if layer == 3 else h['bg4_h']
            bgs = S.bg_positions(h, layer)
            before = max(S.top_gap(rows_before, H, b, 256, 16, layer)
                         for b in bgs)
            after = max(S.top_gap(S.layer_rows(self.new9)[layer], H, b,
                                  256, 16, layer) for b in bgs)
            with self.subTest(layer=layer):
                self.assertLessEqual(after, before)
                if before:
                    self.assertEqual(after, 0)

    def test_the_trigger_header_is_not_touched(self):
        """
        The whole reason this pass adds art instead of correcting
        `bg3_height`: that word also reduces the layer's SCROLL position
        through `remainder()` (FFNx background.cpp:889), so writing it would
        move the layer as well as its repeat.
        """
        self.assertEqual(self.parts[7], self.sec7)
        self.assertEqual(PF.trigger_header(self.sec7),
                         PF.trigger_header(self.parts[7]))

    def test_added_records_differ_only_in_dst_y(self):
        back, tex = self.new9.find(b'BACK'), self.new9.find(b'TEXTURE')
        old = {}
        for layer, _c, first, n in PF._layers(self.sec9,
                                              self.sec9.find(b'BACK'),
                                              self.sec9.find(b'TEXTURE')):
            for i in range(n):
                o = first + i * PF.TILE_SIZE
                rec = bytearray(self.sec9[o:o + PF.TILE_SIZE])
                struct.pack_into('<h', rec, PF.T_DSTY, 0)
                old.setdefault(layer, set()).add(bytes(rec))
        for layer, _c, first, n in PF._layers(self.new9, back, tex):
            if layer not in self.added:
                continue
            for i in range(n):
                o = first + i * PF.TILE_SIZE
                rec = bytearray(self.new9[o:o + PF.TILE_SIZE])
                struct.pack_into('<h', rec, PF.T_DSTY, 0)
                self.assertIn(bytes(rec), old[layer],
                              'a record was invented, not copied')

    def test_the_layer_walk_still_lands_on_TEXTURE(self):
        """The counts were updated; if they were not, this raises."""
        PF._layers(self.new9, self.new9.find(b'BACK'),
                   self.new9.find(b'TEXTURE'))

    def test_running_it_again_adds_nothing(self):
        _again, added = PF.apply_to_section9(self.new9, self.sec7)
        self.assertEqual(added, {},
                         'the fill is not a fixed point -- it would grow the '
                         'archive on every build')

    def test_other_layers_are_untouched(self):
        a = S.layer_rows(self.sec9)
        b = S.layer_rows(self.new9)
        for layer in (1, 2):
            if layer in a:
                self.assertEqual(a[layer], b.get(layer),
                                 'layer %d changed' % layer)

    def test_a_layer_that_does_not_scroll_is_never_touched(self):
        """
        THE KEYHOLE REGRESSION, build 101. `onna_5` layer 4 is the Honey Bee
        Inn mask and its `bg4_speed_y` is 0, so `bg.y` is constant, `dst_y` is
        constant, and the layer is pinned to the viewport -- it cannot have a
        camera-dependent gap. Build 101 tiled it anyway and put opaque mask
        art, and the keyhole's own hole, where the artist never drew them.
        That is the black rectangles reported from hardware.

        46 of the archive's 96 parallax layers are speed-0.
        """
        parts = field_parts('onna_5')
        hdr = PF.trigger_header(parts[7])
        self.assertEqual(hdr['bg4_speed_y'], 0)
        self.assertFalse(PF.scrolls(hdr, 4))
        _new9, added = PF.apply_to_section9(parts[8], parts[7])
        self.assertEqual(added, {},
                         'a viewport-pinned mask must never be tiled')

    def test_every_filled_layer_actually_scrolls(self):
        arch = lgp.Archive(FLEVEL)
        checked = 0
        for name in arch.names()[:120]:
            entry = arch.index[name]
            if not arch.is_field(entry):
                continue
            parts = lgp.split_sections(arch.decompressed(entry))
            if len(parts) < 9:
                continue
            try:
                _n9, added = PF.apply_to_section9(parts[8], parts[7])
            except PF.FillError:
                continue
            hdr = PF.trigger_header(parts[7])
            for layer in added:
                checked += 1
                self.assertTrue(PF.scrolls(hdr, layer),
                                '%s layer %d does not scroll' % (name, layer))
        self.assertGreater(checked, 0, 'nothing was filled; test is vacuous')

    def test_off_switch(self):
        os.environ[PF.OFF_ENV] = '1'
        try:
            self.assertTrue(PF.disabled())
        finally:
            del os.environ[PF.OFF_ENV]
        self.assertFalse(PF.disabled())


if __name__ == '__main__':
    unittest.main(verbosity=2)
