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
        self.by_page = {('fship_2', 0): {0}}
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
