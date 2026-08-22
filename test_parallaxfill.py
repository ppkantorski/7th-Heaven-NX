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

    def test_added_records_differ_only_in_a_destination_word(self):
        """A copy names the same page, uv, palette, blend and animation group.

        Both axes are covered: the vertical pass rewrites `dst_y` and the
        horizontal one `dst_x`, so both words are blanked before comparing.
        Anything else differing would mean a record was INVENTED.
        """
        back, tex = self.new9.find(b'BACK'), self.new9.find(b'TEXTURE')

        def key(buf, o):
            rec = bytearray(buf[o:o + PF.TILE_SIZE])
            struct.pack_into('<h', rec, PF.T_DSTY, 0)
            struct.pack_into('<h', rec, PF.T_DSTX, 0)
            return bytes(rec)

        old = {}
        for layer, _c, first, n in PF._layers(self.sec9,
                                              self.sec9.find(b'BACK'),
                                              self.sec9.find(b'TEXTURE')):
            for i in range(n):
                old.setdefault(layer, set()).add(
                    key(self.sec9, first + i * PF.TILE_SIZE))
        for layer, _c, first, n in PF._layers(self.new9, back, tex):
            if layer not in self.added:
                continue
            for i in range(n):
                self.assertIn(key(self.new9, first + i * PF.TILE_SIZE),
                              old[layer],
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

    @unittest.skipUnless(PF.EDGE_X, 'the pinned margin arm is off -- FINDINGS-283')
    def test_a_pinned_layer_is_never_tiled(self):
        """
        THE KEYHOLE REGRESSION, build 101. `onna_5` layer 4 is the Honey Bee
        Inn mask and its `bg4_speed_y` is 0, so `bg.y` is constant, `dst_y` is
        constant, and the layer is pinned to the viewport -- it cannot have a
        camera-dependent gap. Build 101 tiled it anyway and put opaque mask
        art, and the keyhole's own hole, where the artist never drew them.
        That is the black rectangles reported from hardware.

        53 of the archive's 96 parallax layers are speed-0 in x.

        NOT TILING IS STILL THE RULE. What build 148 adds is the OTHER half of
        `scrolls_x`'s own docstring: a pinned layer that does not reach the
        edges of the wider frame has its outermost column EXTENDED OUTWARD, in
        the margin only. So `dst_y` must still be untouched, and every added
        tile must land outside the 4:3 picture.
        """
        parts = field_parts('onna_5')
        hdr = PF.trigger_header(parts[7])
        self.assertEqual(hdr['bg4_speed_y'], 0)
        self.assertFalse(PF.scrolls(hdr, 4))
        self.assertEqual(PF.plan_layer_edge_x.__doc__ is None, False)

        back, tex = parts[8].find(b'BACK'), parts[8].find(b'TEXTURE')
        first, n = next((f, m) for lay, _c, f, m
                        in PF._layers(parts[8], back, tex) if lay == 4)
        rows_before = {struct.unpack_from('<h', parts[8], f + PF.T_DSTY)[0]
                       for f in range(first, first + n * PF.TILE_SIZE,
                                      PF.TILE_SIZE)}
        new9, added = PF.apply_to_section9(parts[8], parts[7])

        # the layer is not TILED: no new row exists anywhere
        back, tex = new9.find(b'BACK'), new9.find(b'TEXTURE')
        first, n = next((f, m) for lay, _c, f, m
                        in PF._layers(new9, back, tex) if lay == 4)
        rows_after = {struct.unpack_from('<h', new9, f + PF.T_DSTY)[0]
                      for f in range(first, first + n * PF.TILE_SIZE,
                                     PF.TILE_SIZE)}
        self.assertEqual(rows_before, rows_after,
                         'a viewport-pinned mask must never be tiled')

        # and every tile it gained is in the MARGIN: the mask the 4:3 player
        # sees is the same mask, at either engine setting.
        bg_x = PF.bg_x_rest(hdr, 4)
        width = PF.layer_width(hdr, 4)

        def inside_43(buf, f0, m, engine):
            out = set()
            for f in range(f0, f0 + m * PF.TILE_SIZE, PF.TILE_SIZE):
                x = PF.engine_shift(
                    struct.unpack_from('<h', buf, f + PF.T_DSTX)[0],
                    bg_x, width, engine)
                if x + PF.PTILE > bg_x - 320 and x < bg_x:
                    out.add(x)
            return out

        b0, t0 = parts[8].find(b'BACK'), parts[8].find(b'TEXTURE')
        f0, m0 = next((f, m) for lay, _c, f, m
                      in PF._layers(parts[8], b0, t0) if lay == 4)
        for engine in (PF.ENGINE_43, PF.ENGINE_169, PF.ENGINE_169_FULL):
            self.assertEqual(inside_43(new9, first, n, engine),
                             inside_43(parts[8], f0, m0, engine),
                             'the mask inside 4:3 changed')
        self.assertIn(4, added, 'onna_5 layer 4 stops 53.5 units short')

    def test_junon_train_overlays_are_never_repeated(self):
        """The moving train/banner layers are not tileable backdrops."""
        for name, authored_tiles in (('junonl2', 56), ('junonr2', 95)):
            with self.subTest(field=name):
                parts = field_parts(name)
                before = PF._layers(parts[8], parts[8].find(b'BACK'),
                                    parts[8].find(b'TEXTURE'))
                new9, added = PF.apply_to_section9(parts[8], parts[7],
                                                    field_name=name)
                after = PF._layers(new9, new9.find(b'BACK'),
                                   new9.find(b'TEXTURE'))
                n_before = next(n for layer, _c, _first, n in before
                                if layer == 4)
                n_after = next(n for layer, _c, _first, n in after
                               if layer == 4)
                self.assertEqual(n_before, authored_tiles)
                self.assertEqual(
                    n_after, n_before,
                    'the fill appended a second copy of the banner')
                self.assertNotIn(4, added)
                self.assertIn(3, added,
                              'the independent layer-3 fill regressed')

    def test_every_ROW_that_was_added_belongs_to_a_scrolling_layer(self):
        """The vertical rule, restated per AXIS rather than per layer.

        The old form of this test asserted `scrolls(hdr, layer)` for anything
        the pass touched at all, which conflated the two axes the moment the
        horizontal half started work: `wcrimb_2` layer 3 scrolls vertically and
        is pinned horizontally, and both statements are true of it.
        """
        arch = lgp.Archive(FLEVEL)
        checked = 0
        for name in arch.names()[:120]:
            entry = arch.index[name]
            if not arch.is_field(entry):
                continue
            parts = lgp.split_sections(arch.decompressed(entry))
            if len(parts) < 9:
                continue
            sec9 = parts[8]
            hdr = PF.trigger_header(parts[7])
            back, tex = sec9.find(b'BACK'), sec9.find(b'TEXTURE')
            if back < 0 or tex < 0:
                continue
            try:
                new9, added = PF.apply_to_section9(sec9, parts[7],
                                                   field_name=name)
            except PF.FillError:
                continue
            before = S.layer_rows(sec9)
            after = S.layer_rows(new9)
            for layer in added:
                checked += 1
                if set(after.get(layer, ())) - set(before.get(layer, ())):
                    self.assertTrue(
                        PF.scrolls(hdr, layer),
                        '%s layer %d gained a ROW and does not scroll'
                        % (name, layer))
        self.assertGreater(checked, 0, 'nothing was filled; test is vacuous')

    # ------------------------------------------------------------------ 148
    def test_the_engine_shift_is_transcribed(self):
        """`field_layer3_shift_tile_position`, one branch at a time."""
        bg, w = 160.0, 1024
        # inside the window: untouched
        self.assertEqual(PF.engine_shift(0, bg, w, PF.ENGINE_169), 0)
        self.assertEqual(PF.engine_shift(-160, bg, w, PF.ENGINE_169), -160)
        # at or past `bg.x + right_offset`, right_offset 0: wrapped BACK
        self.assertEqual(PF.engine_shift(160, bg, w, PF.ENGINE_169), 160 - w)
        # ...which is exactly why a right-hand column is stored at x + width
        self.assertEqual(PF.engine_shift(160 + w, bg, w, PF.ENGINE_169), 160)
        # at or before `bg.x - left_offset`: wrapped FORWARD
        self.assertEqual(PF.engine_shift(-400, bg, w, PF.ENGINE_169), -400 + w)

    def test_encode_dst_x_agrees_under_every_engine_setting(self):
        bg, w = 160.0, 1024
        for target in (-224, -192, 160, 192):
            stored = PF.encode_dst_x(target, bg, w)
            with self.subTest(target=target):
                self.assertIsNotNone(stored)
                self.assertEqual(
                    PF.engine_shift(stored, bg, w, PF.ENGINE_169), target)
                self.assertEqual(
                    PF.engine_shift(stored, bg, w, PF.ENGINE_169_FULL), target,
                    'closing ff7nx_fieldwide KNOWN GAP would move this tile')
                landed = PF.engine_shift(stored, bg, w, PF.ENGINE_43)
                self.assertTrue(
                    landed + PF.PTILE <= bg - 320 or landed >= bg,
                    'an added tile would be VISIBLE with widescreen off')

    def test_a_degenerate_wrap_is_refused(self):
        """`x + width` is only an address if `width` is a real period."""
        self.assertIsNone(PF.encode_dst_x(160, 160.0, 1))
        self.assertIsNone(PF.encode_dst_x(160, 160.0, 0))

    @unittest.skipUnless(PF.EDGE_X, 'the pinned margin arm is off -- FINDINGS-283')
    def test_fship_2_margin_closes_on_both_sides(self):
        parts = field_parts('fship_2')
        hdr = PF.trigger_header(parts[7])
        for sec9, want in ((parts[8], False),
                           (PF.apply_to_section9(parts[8], parts[7],
                                                 field_name='fship_2')[0],
                            True)):
            back, tex = sec9.find(b'BACK'), sec9.find(b'TEXTURE')
            first, n = next((f, m) for lay, _c, f, m
                            in PF._layers(sec9, back, tex) if lay == 3)
            xs = {}
            for o in range(first, first + n * PF.TILE_SIZE, PF.TILE_SIZE):
                xs.setdefault(struct.unpack_from('<h', sec9, o + PF.T_DSTX)[0],
                              []).append(o)
            bg_x = PF.bg_x_rest(hdr, 3)
            drawn = PF.drawn_map(xs, bg_x, PF.layer_width(hdr, 3))
            covered = PF._covered(drawn,
                                  bg_x - 320 - PF.PICTURE_MARGIN_X,
                                  bg_x + PF.PICTURE_MARGIN_X)
            self.assertEqual(covered, want)

    def test_an_object_layer_is_not_smeared(self):
        """`blin66_2` layer 3 is 96 units wide. It is not a backdrop."""
        parts = field_parts('blin66_2')
        hdr = PF.trigger_header(parts[7])
        sec9 = parts[8]
        back, tex = sec9.find(b'BACK'), sec9.find(b'TEXTURE')
        first, n = next((f, m) for lay, _c, f, m
                        in PF._layers(sec9, back, tex) if lay == 3)
        self.assertEqual(PF.plan_layer_edge_x(sec9, first, n, hdr, 3), [])

    def test_the_4_3_picture_never_gains_a_tile(self):
        """The invariant that makes this pass safe, checked over the archive.

        Not as a threshold -- as geometry. A candidate column that overlaps
        the 4:3 picture is never even considered, so with the widescreen words
        absent every added record is provably off screen.
        """
        arch = lgp.Archive(FLEVEL)
        checked = 0
        for name in arch.names()[:200]:
            entry = arch.index[name]
            if not arch.is_field(entry):
                continue
            parts = lgp.split_sections(arch.decompressed(entry))
            if len(parts) < 9:
                continue
            hdr = PF.trigger_header(parts[7])
            try:
                new9, added = PF.apply_to_section9(parts[8], parts[7],
                                                   field_name=name)
            except PF.FillError:
                continue
            if not added:
                continue
            shots = []
            for sec9 in (parts[8], new9):
                back, tex = sec9.find(b'BACK'), sec9.find(b'TEXTURE')
                shot = {}
                for layer, _c, first, n in PF._layers(sec9, back, tex):
                    if layer not in (3, 4) or PF.scrolls_x(hdr, layer):
                        continue
                    bg_x = PF.bg_x_rest(hdr, layer)
                    w = PF.layer_width(hdr, layer)
                    if w < PF.PTILE:
                        continue
                    seen = {PF.engine_shift(
                        struct.unpack_from('<h', sec9, o + PF.T_DSTX)[0],
                        bg_x, w, PF.ENGINE_43)
                        for o in range(first, first + n * PF.TILE_SIZE,
                                       PF.TILE_SIZE)}
                    shot[layer] = frozenset(x for x in seen
                                            if x + PF.PTILE > bg_x - 320
                                            and x < bg_x)
                shots.append(shot)
            for layer, was in shots[0].items():
                checked += 1
                self.assertEqual(
                    shots[1].get(layer), was,
                    '%s layer %d changed the 4:3 picture' % (name, layer))
        self.assertGreater(checked, 0, 'nothing was filled; test is vacuous')

    def test_the_right_margin_is_not_addressable_from_the_archive(self):
        """FINDINGS-283, and it is why `EDGE_X` is off.

        The wrap DOES bring a column written at `x + width` back to `x`. The
        cull that follows then drops it, because the cull applies the same
        `x < bg.x + right_offset` bound and `right_offset` is 0. So no stored
        value can put a tile past screen 320 -- the 4:3 right edge -- and the
        proof is one line of algebra rather than a threshold:

            screen(x) = 320 - bg.x + x     and     x < bg.x + 0
            =>  screen(x) < 320            for every drawable tile
        """
        bg, w = 160.0, 1024
        for stored in (160, 160 + w, 160 - w, 160 + 2 * w):
            landed = PF.engine_shift(stored, bg, w, PF.ENGINE_169)
            with self.subTest(stored=stored):
                culled = landed <= bg - PF.ENGINE_LEFT_OFFSET or landed >= bg
                self.assertTrue(culled,
                                'the cull would have to let this through')

    def test_off_switch(self):
        os.environ[PF.OFF_ENV] = '1'
        try:
            self.assertTrue(PF.disabled())
        finally:
            del os.environ[PF.OFF_ENV]
        self.assertFalse(PF.disabled())


if __name__ == '__main__':
    unittest.main(verbosity=2)
