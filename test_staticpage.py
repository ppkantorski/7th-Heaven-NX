#!/usr/bin/env python3
"""Regression gates for the fingerprinted Highwind static-page promotion."""
import hashlib
import os
import struct
import sys
import unittest
from types import SimpleNamespace

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC                                      # noqa: E402
import field_bg_native as FN                                  # noqa: E402
import ff7nx_staticpage as SP                                 # noqa: E402


class FakeProvider:
    def __init__(self, art):
        self.art = art
        self.by_page = {(name, 0): {0}
                        for name in tuple(SP.TARGETS) + tuple(SP.CELL_TARGETS)}
        self.ambiguous_slots = set()

    def open(self, name):
        return lambda page, pal: self.art if (page, pal) == (0, 0) else None


class TestStaticPage(unittest.TestCase):

    def setUp(self):
        self.old_target = SP.TARGETS['fship_2']
        self.old_refs = SP.REFS_SHA256
        self.parts, page_data, records = self.fixture()
        SP.TARGETS['fship_2'] = hashlib.sha256(page_data).hexdigest()
        SP.REFS_SHA256 = hashlib.sha256(b''.join(records)).hexdigest()
        n = SP.PAGE_PX * SP.PAGE_PX
        art = SimpleNamespace(
            px=SP.PAGE_PX,
            buf=(np.full(n, 0x0841, dtype='<u2').tobytes()),
            tmask=np.zeros((SP.PAGE_PX, SP.PAGE_PX), bool),
            hmask=np.ones((SP.PAGE_PX, SP.PAGE_PX), bool),
        )
        self.provider = FakeProvider(art)
        self.art = lambda *_args: None
        self.art.provider = self.provider

    def tearDown(self):
        SP.TARGETS['fship_2'] = self.old_target
        SP.REFS_SHA256 = self.old_refs

    @staticmethod
    def fixture():
        records = []
        for i in range(SP.PAGE_REFS):
            sx, sy = (i % 16) * 16, (i // 16) * 16
            r = bytearray(FN.TILE_SIZE)
            struct.pack_into('<hh', r, 2, i * 16, 0)       # destination
            struct.pack_into('<hh', r, 6, 0, 0)            # dense small UV
            struct.pack_into('<HH', r, 18, 0, 0)           # layer-1 size
            r[22] = 0                                      # palette
            r[26] = r[27] = r[30] = 0                     # param/state/blend
            struct.pack_into('<H', r, 28, 0)               # use_fx
            r[32] = r[34] = 0                              # base/fx
            struct.pack_into('<ii', r, 42,
                             sx * SP.UV_SCALE // 256,
                             sy * SP.UV_SCALE // 256)
            records.append(bytes(r))
        back = (b'BACK' + struct.pack('<HHHHH', 0, 0, len(records), 0, 0)
                + b''.join(records) + b'\0\0' + b'\0\0\0')
        page_data = bytes([1]) * (256 * 256)
        pages = [None] * FN.BG_MAX_PAGES
        pages[0] = FN.Page(0, 0, 1, page_data, 256)
        pages[26] = FN.Page(26, 0, 2,
                            bytes([2]) * (SP.PAGE_PX * SP.PAGE_PX * 2),
                            SP.PAGE_PX)
        sec9 = back + FN.build_texture_block(pages) + b'END'
        parts = [b''] * 9
        parts[8] = sec9
        return parts, page_data, records

    def test_promotes_in_place_without_touching_records_or_slots(self):
        old9 = self.parts[8]
        old_pages, old_start, _end, _px = DC.parse_pages(old9)
        out, stats = SP.improve_field('fship_2', self.parts, self.art)
        pages, start, _end, px = DC.parse_pages(out[8])
        self.assertEqual(out[8][:start], old9[:old_start])
        self.assertEqual([i for i, p in enumerate(pages) if p],
                         [i for i, p in enumerate(old_pages) if p])
        self.assertEqual((pages[0].size_flag, pages[0].depth, pages[0].px),
                         (0, 2, SP.PAGE_PX))
        self.assertEqual(pages[0].data, self.provider.art.buf)
        self.assertEqual(px, SP.PAGE_PX)
        self.assertEqual(stats['tiles'], SP.PAGE_REFS)
        again, second = SP.improve_field('fship_2', out, self.art)
        self.assertIsNone(again)
        self.assertEqual(second['already'], 1)

    def test_record_change_fails_closed(self):
        parts = list(self.parts)
        sec9 = bytearray(parts[8])
        first = sec9.find(b'BACK') + 4 + 10
        sec9[first + SP.T_STATE] = 1
        parts[8] = bytes(sec9)
        with self.assertRaises(SP.StaticPageError):
            SP.improve_field('fship_2', parts, self.art)

    def test_transparent_or_keyed_art_is_refused(self):
        self.provider.art.tmask[0, 0] = True
        self.provider.art.hmask[0, 0] = False
        with self.assertRaises(SP.StaticPageError):
            SP.improve_field('fship_2', self.parts, self.art)

    def test_other_field_is_untouched(self):
        out, stats = SP.improve_field('junonl2', self.parts, self.art)
        self.assertIsNone(out)
        self.assertEqual(stats['already'], 0)

    def test_off_switch(self):
        os.environ[SP.NO_ENV] = '1'
        try:
            self.assertFalse(SP.enabled())
        finally:
            del os.environ[SP.NO_ENV]
        self.assertTrue(SP.enabled())


class TestStaticCellRelocation(unittest.TestCase):

    NAME = 'mds7plr1'

    def setUp(self):
        self.old_plan = SP.CELL_TARGETS[self.NAME]
        self.parts, self.source, self.dest, self.target_off, self.fx_off = \
            self.fixture()
        target = self.parts[8][self.target_off:
                               self.target_off + FN.TILE_SIZE]
        self.plan = {
            'source_sha': hashlib.sha256(self.source).hexdigest(),
            'record_sha': hashlib.sha256(target).hexdigest(),
            'field_xy': (-96, -240), 'dest_slot': 14,
            'dest_cell': (3, 6),
            'dest_sha': hashlib.sha256(self.dest).hexdigest(),
        }
        SP.CELL_TARGETS[self.NAME] = self.plan
        n = SP.PAGE_PX * SP.PAGE_PX
        art = SimpleNamespace(
            px=SP.PAGE_PX,
            buf=np.arange(n, dtype='<u2').reshape(-1).clip(1).tobytes(),
            tmask=np.zeros((SP.PAGE_PX, SP.PAGE_PX), bool),
            hmask=np.ones((SP.PAGE_PX, SP.PAGE_PX), bool),
        )
        self.provider = FakeProvider(art)
        self.art = lambda *_args: None
        self.art.provider = self.provider

    def tearDown(self):
        SP.CELL_TARGETS[self.NAME] = self.old_plan

    @staticmethod
    def _record(field_xy, base, fx=0, use_fx=0, layer2=False):
        r = bytearray(FN.TILE_SIZE)
        struct.pack_into('<hh', r, 2, *field_xy)
        struct.pack_into('<hh', r, 6, 0, 0)
        struct.pack_into('<HH', r, 18, 0, 0)
        r[22] = 1 if layer2 else 0
        r[26] = r[27] = 0
        struct.pack_into('<H', r, 28, use_fx)
        r[30] = 1 if layer2 else 0
        r[32], r[34] = base, fx
        struct.pack_into('<ii', r, 42, 0, 0)
        return bytes(r)

    @classmethod
    def fixture(cls):
        target = cls._record((-96, -240), 0)
        dormant_fx = cls._record((0, 0), 0, 15, 1, layer2=True)
        back = (b'BACK' + struct.pack('<HHHHH', 0, 0, 1, 0, 0)
                + target + b'\0\0'
                + b'\x01' + struct.pack('<HHH', 0, 0, 1)
                + bytes(18) + dormant_fx + b'\0\0'
                + b'\0\0')
        source = bytes([3]) * (256 * 256)
        dest = bytes([4]) * (SP.PAGE_PX * SP.PAGE_PX * 2)
        pages = [None] * FN.BG_MAX_PAGES
        pages[0] = FN.Page(0, 0, 1, source, 256)
        pages[14] = FN.Page(14, 0, 2, dest, SP.PAGE_PX)
        pages[15] = FN.Page(15, 0, 1, bytes([5]) * (256 * 256), 256)
        sec9 = back + FN.build_texture_block(pages) + b'END'
        target_off = 4 + 10
        fx_off = target_off + FN.TILE_SIZE + 2 + 1 + 6 + 18
        parts = [b''] * 9
        parts[8] = sec9
        return parts, source, dest, target_off, fx_off

    def test_moves_only_static_record_and_one_unused_cell(self):
        old9 = self.parts[8]
        old_pages, old_start, _end, _px = DC.parse_pages(old9)
        old_fx = old9[self.fx_off:self.fx_off + FN.TILE_SIZE]
        out, stats = SP.improve_field(self.NAME, self.parts, self.art)
        pages, start, _end, _px = DC.parse_pages(out[8])

        self.assertEqual(stats['tiles'], 1)
        self.assertEqual(stats['relocated'], 1)
        self.assertEqual(pages[0].data, self.source)
        self.assertEqual(out[8][self.fx_off:self.fx_off + FN.TILE_SIZE], old_fx)
        self.assertEqual([i for i, p in enumerate(pages) if p],
                         [i for i, p in enumerate(old_pages) if p])
        self.assertEqual(sum(SP._page_bytes(p.px, p.depth) for p in pages if p),
                         sum(SP._page_bytes(p.px, p.depth)
                             for p in old_pages if p))
        moved = out[8][self.target_off:self.target_off + FN.TILE_SIZE]
        self.assertEqual(moved[SP.T_BASE], 14)
        step = SP.UV_SCALE // 16
        self.assertEqual(struct.unpack_from('<ii', moved, SP.T_BIG_X),
                         (3 * step, 6 * step))
        self.assertEqual(SP._cell_bytes(pages[14].data, (3, 6)),
                         SP._cell_bytes(self.provider.art.buf, (0, 0)))
        # Exactly the destination cell changed in the page.
        expected = SP._write_cell(self.dest, (3, 6),
                                  SP._cell_bytes(self.provider.art.buf, (0, 0)))
        self.assertEqual(pages[14].data, expected)
        self.assertEqual(start, old_start)

        again, second = SP.improve_field(self.NAME, out, self.art)
        self.assertIsNone(again)
        self.assertEqual(second['already'], 1)

    def test_declared_destination_consumer_fails_closed(self):
        parts = list(self.parts)
        sec9 = bytearray(parts[8])
        sec9[self.fx_off + SP.T_BASE] = self.plan['dest_slot']
        step = SP.UV_SCALE // 16
        struct.pack_into('<ii', sec9, self.fx_off + SP.T_BIG_X,
                         self.plan['dest_cell'][0] * step,
                         self.plan['dest_cell'][1] * step)
        parts[8] = bytes(sec9)
        with self.assertRaises(SP.StaticPageError):
            SP.improve_field(self.NAME, parts, self.art)


if __name__ == '__main__':
    unittest.main(verbosity=2)
