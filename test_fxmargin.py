#!/usr/bin/env python3
import collections
import os
import struct
import unittest
from unittest import mock

import numpy as np

import diag_common as DC
import field_bg_dense as FD
import ff7nx_fxmargin as FX
import ff7nx_marginart as MA
import ff7nx_marginblack as MB
import ff7nx_palrange as PR
import lgp
import preflight_marginart as PF
import render_field as RF
import _kshadow as KS
import _seam as SEAM


class FxMarginIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.arc = lgp.Archive(PF.DUMP)
        cls.provider = PF.provider(768)
        raw = PF._with_mod_section9(
            cls.arc.decompressed(cls.arc.index['mds7plr1']), 'mds7plr1')
        parts = lgp.split_sections(raw)
        parts[8], _ = PR.fix_field(parts[3], parts[8], 'mds7plr1',
                                   art_for=cls.provider.open('mds7plr1'))
        cls.before = lgp.join_sections(parts)
        filled, _ = MA.fill_field(
            'mds7plr1', cls.before, lgp,
            MA.provider_source(cls.provider), scope='all')
        cls.before = filled or cls.before
        cls.after, cls.stats = FX.split_field(
            'mds7plr1', cls.before, lgp, MA.provider_source(cls.provider))

    def test_exact_proof_case(self):
        self.assertIsNotNone(self.after)
        self.assertEqual(self.stats['units'], 644)
        self.assertEqual(self.stats['tiles'], 644)
        self.assertEqual(self.stats['pages'], 5)

    def test_only_complete_active_effect_records_move(self):
        a, b = [lgp.split_sections(x)[8] for x in (self.before, self.after)]
        sa, sb = DC.survey(a), DC.survey(b)
        pa = {p.slot: p for p in sa['pages']}
        pb = {p.slot: p for p in sb['pages']}
        ta = MB.read_tiles(a, sa, pa)
        tb = MB.read_tiles(b, sb, pb)
        self.assertEqual(len(ta), len(tb))
        changed = []
        for x, y in zip(ta, tb):
            old = a[x.off:x.off + 52]
            new = b[y.off:y.off + 52]
            if old != new:
                changed.append(x)
                self.assertGreaterEqual(x.layer, 2)
                self.assertNotEqual(a[x.off + FX.T_FX], 0)
                self.assertNotEqual(a[x.off + FX.T_BLEND], 0)
        self.assertEqual(len(changed), 644)
        self.assertEqual(sum(t.outside_43 for t in changed), 184)
        self.assertEqual(sum(not t.outside_43 for t in changed), 460)

    def test_original_pages_are_byte_identical(self):
        aa = {p.slot: p for p in DC.survey(lgp.split_sections(self.before)[8])['pages']}
        bb = {p.slot: p for p in DC.survey(lgp.split_sections(self.after)[8])['pages']}
        for slot, page in aa.items():
            self.assertIn(slot, bb)
            self.assertEqual(page.data, bb[slot].data)

    def test_new_fx_pages_are_palette_pure_and_base_stays_put(self):
        a, b = [lgp.split_sections(x)[8] for x in (self.before, self.after)]
        old_slots = {p.slot for p in DC.survey(a)['pages']}
        sb = DC.survey(b)
        pb = {p.slot: p for p in sb['pages']}
        new_slots = set(pb) - old_slots
        self.assertEqual(len(new_slots), 5)
        seen = {s: set() for s in new_slots}
        for t in MB.read_tiles(b, sb, pb):
            if t.slot in new_slots:
                seen[t.slot].add(t.pal)
            fx = b[t.off + FX.T_FX]
            if fx in new_slots:
                seen[fx].add(t.pal)
                self.assertIn(t.slot, old_slots)
                self.assertEqual(
                    a[t.off + FX.T_SRC_X_BIG:t.off + FX.T_SRC_Y_BIG + 4],
                    b[t.off + FX.T_SRC_X_BIG:t.off + FX.T_SRC_Y_BIG + 4])
        self.assertTrue(all(len(v) == 1 for v in seen.values()))
        self.assertEqual(set().union(*seen.values()), {9, 10})

    def test_all_authored_fx_cells_survive_as_distinct_pairs(self):
        a, b = [lgp.split_sections(x)[8] for x in (self.before, self.after)]
        sa, sb = DC.survey(a), DC.survey(b)
        pa = {p.slot: p for p in sa['pages']}
        pb = {p.slot: p for p in sb['pages']}
        old_slots = set(pa)
        source_pairs = set()
        moved_pairs = set()
        for t in MB.read_tiles(a, sa, pa):
            fx = a[t.off + FX.T_FX]
            if t.layer >= 2 and a[t.off + FX.T_BLEND] and fx:
                source_pairs.add((fx, a[t.off + FX.T_SRC_X2],
                                  a[t.off + FX.T_SRC_Y2]))
        for t in MB.read_tiles(b, sb, pb):
            fx = b[t.off + FX.T_FX]
            if fx not in old_slots:
                moved_pairs.add((fx, b[t.off + FX.T_SRC_X2],
                                 b[t.off + FX.T_SRC_Y2]))
        self.assertEqual(len(source_pairs), 644)
        self.assertEqual(len(moved_pairs), 644)

    def test_base_page_palette_and_uv_are_unchanged(self):
        a, b = [lgp.split_sections(x)[8] for x in (self.before, self.after)]
        sa, sb = DC.survey(a), DC.survey(b)
        pa = {p.slot: p for p in sa['pages']}
        pb = {p.slot: p for p in sb['pages']}
        old_slots = set(pa)
        ta = MB.read_tiles(a, sa, pa)
        tb = MB.read_tiles(b, sb, pb)
        for old, new in zip(ta, tb):
            self.assertEqual(old.slot, new.slot)
            self.assertIn(new.slot, old_slots)
            self.assertEqual(old.pal, new.pal)
            self.assertEqual(
                a[old.off + FX.T_SRC_X_BIG:old.off + FX.T_SRC_Y_BIG + 4],
                b[new.off + FX.T_SRC_X_BIG:new.off + FX.T_SRC_Y_BIG + 4])

    def test_no_capacity_means_no_bytes_change(self):
        present = len(DC.survey(lgp.split_sections(self.before)[8])['pages'])
        with mock.patch.object(FX.FR, 'max_total_pages', return_value=present + 1):
            new, st = FX.split_field(
                'mds7plr1', self.before, lgp,
                MA.provider_source(self.provider))
        self.assertIsNone(new)
        self.assertEqual(st['nofit'], 644)

    def test_fixed_point(self):
        new, st = FX.split_field(
            'mds7plr1', self.after, lgp, MA.provider_source(self.provider))
        self.assertIsNone(new)
        self.assertEqual(st['units'], 0)

    def test_every_non_target_field_is_refused(self):
        raw = PF._with_mod_section9(
            self.arc.decompressed(self.arc.index['junone5']), 'junone5')
        new, st = FX.split_field(
            'junone5', raw, lgp, MA.provider_source(self.provider))
        self.assertIsNone(new)
        self.assertEqual(st['units'], 0)

    def test_base_frame_is_identical_and_fx_frame_crosses_43(self):
        before, origin = RF.render(self.before, (2,), fx_frame=False)
        after, origin2 = RF.render(self.after, (2,), fx_frame=False)
        self.assertEqual(origin, origin2)
        self.assertTrue(np.array_equal(before, after))

        before, origin = RF.render(self.before, (2,), fx_frame=True)
        after, origin2 = RF.render(self.after, (2,), fx_frame=True)
        self.assertEqual(origin, origin2)
        changed = np.any(before != after, axis=2)
        ys, xs = np.where(changed)
        self.assertGreater(len(xs), 0)
        world_x = xs + origin[0]
        self.assertTrue(np.any((world_x >= -160) & (world_x < 160)))
        self.assertTrue(np.any((world_x < -160) | (world_x >= 160)))

    def test_downstream_chain_preserves_base_and_updates_complete_fx(self):
        KS.bootstrap()
        SEAM._init()
        g = SEAM._G
        bc = MA.provider_source(g['prov'])
        empty = {'units': 0, 'tiles': 0, 'pages': 0, 'dark': 0,
                 'no_art': 0, 'partial': 0, 'nofit': 0}
        with mock.patch.object(FX, 'split_field', return_value=(None, empty)):
            off_parts = KS.chain('mds7plr1', g['arch'], g['ent'], g['prov'],
                                 g['art'], bc, g['scope'])
        on_parts = KS.chain('mds7plr1', g['arch'], g['ent'], g['prov'],
                            g['art'], bc, g['scope'])
        off_raw, on_raw = lgp.join_sections(off_parts), lgp.join_sections(on_parts)
        changed_inside = changed_margin = False
        for fx_frame in (False, True):
            off, origin = RF.render(off_raw, (1, 2), fx_frame=fx_frame)
            on, origin2 = RF.render(on_raw, (1, 2), fx_frame=fx_frame)
            self.assertEqual(origin, origin2)
            xx = np.arange(off.shape[1]) + origin[0]
            inside = (xx >= -160) & (xx < 160)
            if not fx_frame:
                self.assertTrue(np.array_equal(off, on))
            else:
                changed_inside |= bool(np.any(off[:, inside] != on[:, inside]))
                changed_margin |= bool(np.any(off[:, ~inside] != on[:, ~inside]))
        self.assertTrue(changed_inside)
        self.assertTrue(changed_margin)

    def test_mds5_2_cross_layer_palette_reuse_keeps_exact_cosmos_backdrop(self):
        """The reported dark chain is one paletted layer-1 cell, not FX."""
        KS.bootstrap()
        SEAM._init()
        g = SEAM._G

        def _chain(disabled=False):
            env = {FD.CROSSLAYER_EXACT_ENV: '1'} if disabled else {}
            with mock.patch.dict(os.environ, env, clear=False):
                return KS.chain(
                    'mds5_2', g['arch'], g['ent'], g['prov'], g['art'],
                    MA.provider_source(g['prov']), g['scope'])

        # With the old broad multi-palette veto, page-0 cell (0,0) stays
        # paletted because unrelated layer-2 animation tiles reuse that atlas
        # coordinate through palettes 8/9.  The new rule moves only palette
        # 0's layer-1 reference; every upper-layer record stays byte-identical.
        oldp, newp = _chain(True), _chain(False)
        old9, new9 = oldp[8], newp[8]
        old_s, new_s = DC.survey(old9), DC.survey(new9)
        old_pm = {p.slot: p for p in old_s['pages']}
        new_pm = {p.slot: p for p in new_s['pages']}
        old_t = MB.read_tiles(old9, old_s, old_pm)
        new_t = MB.read_tiles(new9, new_s, new_pm)
        self.assertEqual(len(old_t), len(new_t))
        target = [(a, b) for a, b in zip(old_t, new_t)
                  if a.layer == 1 and a.dx == -400 and a.dy == -208]
        self.assertEqual(len(target), 1)
        a, b = target[0]
        self.assertEqual(old_pm[a.slot].depth, 1)
        self.assertEqual(new_pm[b.slot].depth, 2)
        self.assertEqual(new_pm[b.slot].px, 768)
        self.assertTrue(all(
            old9[a.off:a.off + 52] == new9[b.off:b.off + 52]
            for a, b in zip(old_t, new_t) if a.layer >= 2))
        self.assertEqual([(p.slot, p.depth) for p in old_s['pages']],
                         [(p.slot, p.depth) for p in new_s['pages']])

        # Pixel authority is Cosmos page 0 palette 0.  The corrected final
        # layer-1 cell is exactly its 768px 48x48 cell sampled onto 16 game
        # pixels; the disabled build is the visibly stepped paletted cell.
        mod = g['prov'].open('mds5_2')(0, 0)
        self.assertIsNotNone(mod)
        art = RF._d2_rgb(np.frombuffer(mod.buf, '<u2')
                         .reshape(mod.px, mod.px))[:48:3, :48:3]
        old_im, old_o = RF.render(lgp.join_sections(oldp), (1,))
        new_im, new_o = RF.render(lgp.join_sections(newp), (1,))
        self.assertEqual(old_o, (-400, -208))
        self.assertEqual(new_o, old_o)
        self.assertFalse(np.array_equal(old_im[:16, :16], art))
        self.assertTrue(np.array_equal(new_im[:16, :16], art))
        changed = np.any(old_im != new_im, axis=2)
        ys, xs = np.where(changed)
        self.assertGreater(len(xs), 0)
        self.assertTrue(np.all(xs < 16))
        self.assertTrue(np.all(ys < 16))

    def test_mds5_5_glass_variants_split_without_freezing_lamps(self):
        """Static glass moves by palette; the four-state lamp page does not."""
        name = 'mds5_5'
        raw = PF._with_mod_section9(
            self.arc.decompressed(self.arc.index[name]), name)
        parts = lgp.split_sections(raw)
        parts[8], _ = PR.fix_field(parts[3], parts[8], name,
                                   art_for=self.provider.open(name))
        before = lgp.join_sections(parts)
        filled, _ = MA.fill_field(
            name, before, lgp, MA.provider_source(self.provider), scope='all')
        before = filled or before
        after, st = FX.split_field(
            name, before, lgp, MA.provider_source(self.provider))
        self.assertIsNotNone(after)
        self.assertEqual(
            {k: st[k] for k in ('units', 'tiles', 'pages',
                                'truecolor_pages', 'ambiguous_kept')},
            {'units': 92, 'tiles': 92, 'pages': 2,
             'truecolor_pages': 2, 'ambiguous_kept': 10})

        a, b = [lgp.split_sections(x)[8] for x in (before, after)]
        sa, sb = DC.survey(a), DC.survey(b)
        pa = {p.slot: p for p in sa['pages']}
        pb = {p.slot: p for p in sb['pages']}
        ta = MB.read_tiles(a, sa, pa)
        tb = MB.read_tiles(b, sb, pb)
        self.assertEqual(len(ta), len(tb))
        changed_records = [(old, new) for old, new in zip(ta, tb)
                           if a[old.off:old.off + 52]
                           != b[new.off:new.off + 52]]
        self.assertEqual(len(changed_records), 92)
        self.assertTrue(all(old.layer >= 2 and old.pal in (3, 4)
                            for old, _new in changed_records))
        groups = collections.defaultdict(list)
        for old, new in zip(ta, tb):
            old_fx = a[old.off + FX.T_FX]
            if old.layer < 2 or not a[old.off + FX.T_BLEND] or not old_fx:
                continue
            groups[old.pal].append((old, new))
            old_rec = bytearray(a[old.off:old.off + 52])
            new_rec = bytearray(b[new.off:new.off + 52])
            old_rec[FX.T_FX] = new_rec[FX.T_FX]
            self.assertEqual(old_rec, new_rec)

        # Palette 3 is the 28-cell widescreen glass, palette 4 the 64-cell
        # interior glass. Each gets one complete 768px page. Palette 6 is ten
        # animated lamp records and must stay on original page 15.
        self.assertEqual({k: len(v) for k, v in groups.items()},
                         {3: 28, 4: 64, 6: 10})
        page_for_pal = {}
        for pal in (3, 4):
            slots = {b[n.off + FX.T_FX] for _o, n in groups[pal]}
            self.assertEqual(len(slots), 1)
            page_for_pal[pal] = next(iter(slots))
            page = pb[page_for_pal[pal]]
            self.assertEqual((page.depth, page.px), (2, 768))
        self.assertTrue(all(
            a[o.off + FX.T_FX] == b[n.off + FX.T_FX] == 15
            for o, n in groups[6]))
        self.assertEqual(pa[15].data, pb[15].data)

        # The new pages are literal Cosmos variants after the one required
        # additive-alpha premultiply. In particular, palette 0's transparent
        # far-right cells remain transparent exactly as FFNx renders them;
        # no reflected, stretched or interpolated pixels are synthesized.
        source = MA.provider_source(self.provider)
        for pal, used in ((3, 0), (4, 4)):
            direct = FX._provider_rgba(source, name, 15, used, 768)
            self.assertIsNotNone(direct)
            enc = direct.copy()
            aa = enc[..., 3].astype(np.uint16)
            enc[..., :3] = ((enc[..., :3].astype(np.uint16)
                             * aa[..., None] + 127) // 255).astype(np.uint8)
            enc[..., 3] = 255
            want = FX.FR.rgba_to_565_buf(
                enc.tobytes(), 768 * 768, width=768, black_ok=True)
            self.assertEqual(pb[page_for_pal[pal]].data, want)
        right = [n for _o, n in groups[3] if n.dx >= 160]
        self.assertEqual(len(right), 12)

    def test_mds5_5_dark_cell_is_only_cross_layer_exact_change(self):
        """The top discontinuity is one exact Cosmos layer-1 cell."""
        KS.bootstrap()
        SEAM._init()
        g = SEAM._G

        def _chain(disabled=False):
            env = {FD.CROSSLAYER_EXACT_ENV: '1'} if disabled else {}
            with mock.patch.dict(os.environ, env, clear=False):
                return KS.chain(
                    'mds5_5', g['arch'], g['ent'], g['prov'], g['art'],
                    MA.provider_source(g['prov']), g['scope'])

        oldp, newp = _chain(True), _chain(False)
        old9, new9 = oldp[8], newp[8]
        old_s, new_s = DC.survey(old9), DC.survey(new9)
        old_pm = {p.slot: p for p in old_s['pages']}
        new_pm = {p.slot: p for p in new_s['pages']}
        old_t = MB.read_tiles(old9, old_s, old_pm)
        new_t = MB.read_tiles(new9, new_s, new_pm)
        target = [(a, b) for a, b in zip(old_t, new_t)
                  if a.layer == 1 and a.dx == -160 and a.dy == -120]
        self.assertEqual(len(target), 1)
        a, b = target[0]
        self.assertEqual((old_pm[a.slot].depth, old_pm[a.slot].px), (1, 256))
        self.assertEqual((new_pm[b.slot].depth, new_pm[b.slot].px), (2, 768))
        self.assertEqual([(p.slot, p.depth, p.px) for p in old_s['pages']],
                         [(p.slot, p.depth, p.px) for p in new_s['pages']])
        diffs = [(a, b) for a, b in zip(old_t, new_t)
                 if old9[a.off:a.off + 52] != new9[b.off:b.off + 52]]
        self.assertEqual(diffs, target)
        self.assertTrue(all(
            old9[a.off:a.off + 52] == new9[b.off:b.off + 52]
            for a, b in zip(old_t, new_t) if a.layer >= 2))

        old_im, old_o = RF.render(lgp.join_sections(oldp), (1,))
        new_im, new_o = RF.render(lgp.join_sections(newp), (1,))
        self.assertEqual(old_o, new_o)
        mod = g['prov'].open('mds5_5')(0, 0)
        self.assertIsNotNone(mod)
        exact = RF._d2_rgb(np.frombuffer(mod.buf, '<u2')
                           .reshape(mod.px, mod.px))[:48:3, :48:3]
        x0, y0 = -160 - new_o[0], -120 - new_o[1]
        self.assertFalse(np.array_equal(old_im[y0:y0 + 16, x0:x0 + 16],
                                        exact))
        self.assertTrue(np.array_equal(new_im[y0:y0 + 16, x0:x0 + 16],
                                       exact))
        changed = np.any(old_im != new_im, axis=2)
        ys, xs = np.where(changed)
        self.assertGreater(len(xs), 0)
        self.assertTrue(np.all((xs + old_o[0] >= -160)
                               & (xs + old_o[0] < -144)))
        self.assertTrue(np.all((ys + old_o[1] >= -120)
                               & (ys + old_o[1] < -104)))

    def test_reported_mds5_effects_are_never_partially_moved(self):
        expected = {'mds5_2': (85, 1), 'mds5_3': (226, 1)}
        for name, (tiles, pages) in expected.items():
            raw = PF._with_mod_section9(
                self.arc.decompressed(self.arc.index[name]), name)
            parts = lgp.split_sections(raw)
            parts[8], _ = PR.fix_field(parts[3], parts[8], name,
                                       art_for=self.provider.open(name))
            before = lgp.join_sections(parts)
            filled, _ = MA.fill_field(
                name, before, lgp, MA.provider_source(self.provider),
                scope='all')
            before = filled or before
            after, st = FX.split_field(
                name, before, lgp, MA.provider_source(self.provider))
            self.assertIsNotNone(after)
            self.assertEqual(st['units'], tiles)
            self.assertEqual(st['tiles'], tiles)
            self.assertEqual(st['pages'], pages)
            self.assertEqual(st['truecolor_pages'], 1)

            base0, origin0 = RF.render(before, (1, 2), fx_frame=False)
            base1, origin1 = RF.render(after, (1, 2), fx_frame=False)
            self.assertEqual(origin0, origin1)
            self.assertTrue(np.array_equal(base0, base1))

            a, b = [lgp.split_sections(x)[8] for x in (before, after)]
            sa, sb = DC.survey(a), DC.survey(b)
            pa = {p.slot: p for p in sa['pages']}
            pb = {p.slot: p for p in sb['pages']}
            old_slots = set(pa)
            for slot in old_slots:
                self.assertEqual(pa[slot].data, pb[slot].data)
            new_slots = set(pb) - old_slots
            self.assertEqual(len(new_slots), 1)
            new_page = pb[next(iter(new_slots))]
            self.assertEqual(new_page.depth, 2)
            self.assertEqual(new_page.px, 768)
            # The full Cosmos page reaches the archive without the old
            # 16x16/downsample/palette round trip.
            source = MA.provider_source(self.provider)
            src, used = source(name, 15, 0)
            direct = FX._provider_rgba(source, name, 15, used, 768)
            self.assertIsNotNone(direct)
            soft_rows = []
            for t in MB.read_tiles(a, sa, pa):
                fx = a[t.off + FX.T_FX]
                if (t.layer < 2 or not fx
                        or not a[t.off + FX.T_BLEND]):
                    continue
                u, v = struct.unpack_from('<II', a,
                                          t.off + FX.T_SRC_X_BIG)
                sx = int(round(u / FX.UV_SCALE * FX.GRID)) * FX.TILE
                sy = int(round(v / FX.UV_SCALE * FX.GRID)) * FX.TILE
                soft_rows.append(((t.slot, fx, sx, sy, sx, sy, t.pal), [t]))
            softened = FX._soften_additive_alpha(
                name, direct, soft_rows, 768)
            if name == 'mds5_2':
                self.assertFalse(np.array_equal(softened[..., 3],
                                                direct[..., 3]))
                allowed = np.zeros((768, 768), bool)
                for key, _tiles in soft_rows:
                    x = key[4] * 3
                    y = key[5] * 3
                    allowed[y:y + 48, x:x + 48] = True
                changed_alpha = softened[..., 3] != direct[..., 3]
                self.assertFalse(np.any(changed_alpha & ~allowed))
            else:
                self.assertTrue(np.array_equal(softened, direct))
            enc = softened.copy()
            # The paletted adapter exposes binary coverage, while the
            # additive truecolor path reads Cosmos's raw DDS once, preserves
            # its resampled alpha, and bakes alpha*colour into RGB. This also
            # avoids the adapter's unnecessary 565 -> RGB -> 565 round trip.
            self.assertGreater(np.count_nonzero(
                (enc[..., 3] > 0) & (enc[..., 3] < 255)), 0)
            aa = enc[..., 3].astype(np.uint16)
            enc[..., :3] = ((enc[..., :3].astype(np.uint16)
                             * aa[..., None] + 127) // 255).astype(np.uint8)
            enc[..., 3] = 255
            want = FX.FR.rgba_to_565_buf(
                enc.tobytes(), 768 * 768,
                width=768, black_ok=True)
            self.assertEqual(new_page.data, want)
            # No palette enrichment: mds5_3's bottom-right transparent cells
            # can no longer draw palette-0's non-black entry as a rectangle.
            self.assertEqual(lgp.split_sections(before)[3],
                             lgp.split_sections(after)[3])
            active = [t for t in MB.read_tiles(b, sb, pb)
                      if t.layer >= 2 and b[t.off + FX.T_BLEND]
                      and b[t.off + FX.T_FX]]
            self.assertEqual(len(active), tiles)
            self.assertTrue(all(t.slot in old_slots and
                                b[t.off + FX.T_FX] not in old_slots
                                for t in active))
            self.assertEqual({b[t.off + FX.T_FX] for t in active}, new_slots)

            old_tiles = MB.read_tiles(a, sa, pa)
            new_tiles = MB.read_tiles(b, sb, pb)
            self.assertEqual(len(old_tiles), len(new_tiles))
            for old, new in zip(old_tiles, new_tiles):
                before_rec = bytearray(a[old.off:old.off + 52])
                after_rec = bytearray(b[new.off:new.off + 52])
                # The FX page selector is the sole permitted tile-record
                # change. Base page, palette, destination, raw coordinates,
                # packed runtime UV and blend mode remain authored values.
                before_rec[FX.T_FX] = after_rec[FX.T_FX]
                self.assertEqual(before_rec, after_rec)

            if name == 'mds5_3':
                arr = np.frombuffer(new_page.data, '<u2').reshape(768, 768)
                # The five cells forming the reported bottom-right strip are
                # mostly empty in Cosmos. They must be literal additive black
                # now, not a palette index whose colour fills the rectangle.
                cells = [(224, 208), (240, 208), (0, 224), (16, 224),
                         (32, 160)]
                zero = []
                for sx, sy in cells:
                    blk = arr[sy * 3:(sy + 16) * 3,
                              sx * 3:(sx + 16) * 3]
                    zero.append(float(np.mean(blk == 0)))
                self.assertGreater(min(zero), 0.90)


if __name__ == '__main__':
    unittest.main()
