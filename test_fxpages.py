#!/usr/bin/env python3
"""Gates for the archive-wide in-place additive FX page conversion."""
import os
import unittest
from unittest import mock

import diag_common as DC
import field_bg_native as FN
import ff7nx_fxmargin as FXM
import ff7nx_fxpages as FP
import ff7nx_marginart as MA
import ff7nx_marginblack as MB
import lgp
import preflight_marginart as PF


OUTPUT = os.path.join(
    '/Volumes/NVME/Users/ppkantorski/Downloads/ff7_mods/7th_heaven_nx',
    'dump/romfs/ff7/workingdir/data/field/flevel.lgp')


class AdditiveFxPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        arc = lgp.Archive(OUTPUT)
        cls.raw = arc.decompressed(arc.index['ujunon1'])
        cls.parts = lgp.split_sections(cls.raw)
        cls.art = MA.provider_source(PF.provider(768))

    def _run(self, sec9=None, **kw):
        return FP.upgrade_section9(
            'ujunon1', self.parts[8] if sec9 is None else sec9,
            self.art, 768, **kw)

    def test_under_junon_four_complete_smoke_pages_upgrade_in_place(self):
        before, s0, e0 = FN.parse_texture_block(self.parts[8], 768)
        out, st = self._run()
        after, s1, e1 = FN.parse_texture_block(out, 768)
        self.assertEqual(st['pages'], 4)
        self.assertEqual(st['tiles'], 882)
        self.assertEqual(st['page_names'],
                         ['ujunon1:15', 'ujunon1:16',
                          'ujunon1:17', 'ujunon1:18'])
        self.assertEqual(self.parts[8][:s0], out[:s1])
        self.assertEqual(self.parts[8][e0:], out[e1:])
        self.assertEqual([p.slot for p in before if p],
                         [p.slot for p in after if p])
        self.assertTrue(all(after[s].depth == 2 and after[s].px == 768
                            for s in range(15, 19)))

    def test_mixed_blend_page_is_vetoed_not_partially_written(self):
        sec9 = bytearray(self.parts[8])
        surv = DC.survey(bytes(sec9))
        pages = {p.slot: p for p in surv['pages']}
        tile = next(t for t in MB.read_tiles(bytes(sec9), surv, pages)
                    if sec9[t.off + FN.TILE_TEXTURE_ID2] == 15)
        sec9[tile.off + FP.T_BLEND_MODE] = 2
        out, st = self._run(bytes(sec9))
        pages, _s, _e = FN.parse_texture_block(out, 768)
        self.assertEqual(st['blend_veto'], 1)
        self.assertEqual(st['pages'], 3)
        self.assertEqual(pages[15].depth, 1)
        self.assertTrue(all(pages[s].depth == 2 for s in range(16, 19)))

    def test_page_also_used_as_base_is_vetoed(self):
        sec9 = bytearray(self.parts[8])
        surv = DC.survey(bytes(sec9))
        pages = {p.slot: p for p in surv['pages']}
        tile = next(t for t in MB.read_tiles(bytes(sec9), surv, pages)
                    if sec9[t.off + FN.TILE_TEXTURE_ID2] == 15)
        sec9[tile.off + FN.TILE_TEXTURE_ID] = 15
        out, st = self._run(bytes(sec9))
        pages, _s, _e = FN.parse_texture_block(out, 768)
        self.assertEqual(st['base_veto'], 1)
        self.assertEqual(st['pages'], 3)
        self.assertEqual(pages[15].depth, 1)

    def test_multiple_dds_runtime_states_are_vetoed(self):
        key = ('ujunon1', 15, 0)
        ambiguous = self.art.provider.ambiguous_slots
        self.assertNotIn(key, ambiguous)       # Cosmos ships one smoke state
        ambiguous.add(key)
        try:
            out, st = self._run()
        finally:
            ambiguous.remove(key)
        pages, _s, _e = FN.parse_texture_block(out, 768)
        self.assertEqual(st['art_veto'], 1)
        self.assertEqual(st['pages'], 3)
        self.assertEqual(pages[15].depth, 1)

    def test_hard_budget_refuses_whole_pages(self):
        out, st = self._run(max_raw_delta=0, max_runtime_delta=0)
        self.assertEqual(out, self.parts[8])
        self.assertEqual(st['pages'], 0)
        self.assertEqual(st['budget_veto'], 4)

    def test_one_rollback_disables_archive_and_binary_halves(self):
        with mock.patch.dict(os.environ,
                             {FXM.NO_TRUECOLOR_ENV: '1'}, clear=False):
            out, st = self._run()
            self.assertEqual(out, self.parts[8])
            self.assertEqual(st['pages'], 0)
            self.assertFalse(FP.enabled())


if __name__ == '__main__':
    unittest.main()
