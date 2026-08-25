#!/usr/bin/env python3
"""Exact-chain gates for the general animated-FX repairs."""
from __future__ import annotations

import os
import hashlib
import struct
import unittest

import numpy as np

import _kreplay1
import diag_common as DC
import field_bg_native as FN
import field_bg_pagecap as PC
import field_bg_repack as FR
import ff7nx_fxcoverage as FXC
import ff7nx_fxpalette as FXP
import ff7nx_marginart as MA
import ff7nx_marginblack as MB


class TargetedFxRepairs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = FR.ArtProvider([('mods/CosmosLimitBreak.iro', None)],
                                      768, lambda *_a: None)
        cls.art = MA.provider_source(cls.provider)

    def _palette_before(self, name):
        os.environ[FXP.OFF_ENV] = '1'
        try:
            return _kreplay1.replay(name)
        finally:
            os.environ.pop(FXP.OFF_ENV, None)

    def test_palette_manifest_repairs_every_proven_field(self):
        totals = __import__('collections').Counter()
        for name in FXP.TARGET_FIELDS:
            parts = self._palette_before(name)
            out, st = FXP.apply_to_section9(name, parts[8], parts[3],
                                             self.art, 768)
            self.assertFalse(st['refused'], (name, st['refused']))
            for key in ('fields', 'slots', 'records', 'replaced'):
                totals[key] += st[key]
            again, st2 = FXP.apply_to_section9(
                name, out, parts[3], self.art, 768)
            self.assertEqual(again, out)
            self.assertTrue(st2['refused'])
        self.assertEqual(dict(totals), {
            'fields': FXP.EXPECTED_FIELDS,
            'slots': FXP.EXPECTED_SLOTS,
            'records': FXP.EXPECTED_RECORDS,
            'replaced': FXP.EXPECTED_UNITS,
        })

    def test_mtnvl6_replaces_black255_with_live_palette_trajectories(self):
        parts = self._palette_before('mtnvl6')
        before = parts[8]
        out, st = FXP.apply_to_section9('mtnvl6', before, parts[3],
                                        self.art, 768)
        self.assertEqual((st['records'], st['pages'], st['replaced']),
                         (159, 0, 2870))
        self.assertFalse(st['refused'])
        self.assertLess(st['new_error'], 3.1)
        self.assertGreater(st['old_error'], 180.0)
        p0, _a, _b = FN.parse_texture_block(before, 768)
        p1, _a, _b = FN.parse_texture_block(out, 768)
        self.assertEqual(p0[15].data, p1[15].data)
        old = np.frombuffer(p0[16].data, np.uint8)
        new = np.frombuffer(p1[16].data, np.uint8)
        changed = old != new
        self.assertEqual(int(changed.sum()), 2870)
        self.assertTrue(np.all((~changed) | ((old == 255)
                                            & np.isin(new, (1, 2, 3, 4)))))
        self.assertEqual(dict(__import__('collections').Counter(new[changed])),
                         {1: 38, 2: 2782, 3: 46, 4: 4})
        old2 = old.reshape(256, 256)
        new2 = new.reshape(256, 256)
        masks = {8: np.zeros((256, 256), bool),
                 9: np.zeros((256, 256), bool)}
        surv = DC.survey(before)
        tiles = MB.read_tiles(before, surv,
                              {q.slot: q for q in p0 if q is not None})
        for tile in tiles:
            if before[tile.off + FN.TILE_TEXTURE_ID2] != 16:
                continue
            u, v = struct.unpack_from('<II', before, tile.off + 42)
            cx = round(u / 10_000_000 * 16)
            cy = round(v / 10_000_000 * 16)
            masks[tile.pal][cy * 16:cy * 16 + 16,
                            cx * 16:cx * 16 + 16] = True
        changed2 = old2 != new2
        self.assertEqual(int((changed2 & masks[9]).sum()), 2869)
        self.assertEqual(int((changed2 & masks[8]).sum()), 1)
        self.assertEqual((int(old2[47, 47]), int(new2[47, 47])), (255, 3))
        self.assertIsNone(p1[17])
        self.assertIsNone(p1[18])
        self.assertEqual(sum(q is not None for q in p1), 8)
        again, st2 = FXP.apply_to_section9('mtnvl6', out, parts[3],
                                           self.art, 768)
        self.assertEqual(again, out)
        self.assertTrue(st2['refused'])

    def test_nvdun1_radial_cells_continue_without_second_lobe(self):
        os.environ[FXC.OFF_ENV] = '1'
        try:
            parts = _kreplay1.replay('nvdun1')
        finally:
            os.environ.pop(FXC.OFF_ENV, None)
        before = parts[8]
        out, st = FXC.apply_to_section9(
            'nvdun1', before, parts[3], self.art, 768)
        self.assertEqual((st['tiles'], st['cells'], st['pages']), (7, 7, 0))
        self.assertEqual((st['bands'], st['geometry'], st['dark_veto']),
                         (1, 2, 1))
        self.assertEqual((st['state_veto'], st['shape_veto'], st['fit_veto']),
                         (0, 0, 0))
        before_pages, _a, _b = FN.parse_texture_block(before, 768)
        pages, _a, _b = FN.parse_texture_block(out, 768)
        pmap = {q.slot: q for q in pages if q is not None}
        for slot, page in enumerate(before_pages):
            if page is not None and slot != 15:
                self.assertEqual(page.data, pages[slot].data)
        surv = DC.survey(out)
        tiles = MB.read_tiles(out, surv, pmap)
        left = {t.dy: t for t in tiles
                if t.layer == 2 and t.dx == -224
                and out[t.off + FN.TILE_TEXTURE_ID] == 0
                and out[t.off + FN.TILE_TEXTURE_ID2] == 15}
        old = {t.dy: t for t in tiles
               if t.layer == 2 and t.dx == -208
               and out[t.off + FN.TILE_TEXTURE_ID2] == 15}
        opposite = {t.dy: t for t in tiles
                    if t.layer == 2 and t.dx == -176
                    and 152 <= t.dy <= 248
                    and out[t.off + FN.TILE_TEXTURE_ID2] == 15}
        expected_rows = set(range(152, 249, 16))
        self.assertEqual(set(left), expected_rows)
        self.assertEqual(set(opposite), expected_rows)
        self.assertNotIn(1, pmap)
        a15 = np.frombuffer(pmap[15].data, np.uint8).reshape(256, 256)

        def cell(tile):
            u, v = struct.unpack_from('<II', out, tile.off + 42)
            return (round(u / 10_000_000 * 16) * 16,
                    round(v / 10_000_000 * 16) * 16)

        for y in expected_rows:
            nsx, nsy = cell(left[y])
            osx, osy = cell(old[y])
            new_cell = a15[nsy:nsy + 16, nsx:nsx + 16]
            outward = a15[osy:osy + 16, osx]
            # Exact hardware seam in palette-index space, but neither the
            # duplicated lobe from Build 169 nor a flat edge-clamp strip.
            self.assertTrue(np.array_equal(new_cell[:, -1], outward))
            source_cell = a15[osy:osy + 16, osx:osx + 16]
            self.assertFalse(np.array_equal(new_cell, source_cell[:, ::-1]))
            self.assertFalse(np.array_equal(
                new_cell, np.repeat(outward[:, None], 16, axis=1)))
            self.assertEqual(out[left[y].off + 10], out[old[y].off + 10])
            self.assertEqual(out[left[y].off + 12], out[old[y].off + 12])
            src = out[old[y].off:old[y].off + FN.TILE_SIZE]
            new = out[left[y].off:left[y].off + FN.TILE_SIZE]
            changed = {i for i, (a, b) in enumerate(zip(src, new)) if a != b}
            self.assertLessEqual(changed,
                                 {2, 3, 14, 16, 42, 43, 44, 45,
                                  46, 47, 48, 49})
        self.assertEqual(
            hashlib.sha256(pmap[15].data).hexdigest(),
            'bf0bd1d6f91f79643c6e7079a223af2bcaf069bb4bbe730a350f76255d49fb70')
        detail = st['details'][0]
        self.assertAlmostEqual(detail[9], 1.0860182046890259)
        self.assertAlmostEqual(detail[10], 0.6746816390327045)
        self.assertAlmostEqual(detail[11], 1.9369503259658813)
        self.assertLessEqual(PC.effective_counts(out, 768).get(15, 0), 256)
        again, st2 = FXC.apply_to_section9(
            'nvdun1', out, parts[3], self.art, 768)
        self.assertEqual(again, out)
        self.assertEqual(st2['tiles'], 0)


if __name__ == '__main__':
    unittest.main()
