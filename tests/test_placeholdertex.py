"""BUILD 765: Hades' cauldron lid (hadesdat.t00, a 1x1 placeholder).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import shutil
import tempfile
import unittest

import ff7nx_placeholdertex as P

ROOT = nxpaths.ROOT
MAGIC = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                     'battle', 'magic.lgp')
MODS = os.environ.get('SEVENTH_NX_TEST_MODS') or os.path.join(ROOT, 'mods')
SYW = os.path.join(MODS, '[Tsunamods].SYW.Unified.Spells.Textures.V1.03.iro')
LID = 'FF7SYWU\\Base\\magic\\limit\\HADESDAT_T00_00.dds'


class Names(unittest.TestCase):
    def test_ffnx_alias(self):
        self.assertEqual(P.ffnx_alias('hadesdat_t00'), 'hadesdat.t00')
        self.assertEqual(P.ffnx_alias('mogurida_t03'), 'mogurida.t03')
        self.assertIsNone(P.ffnx_alias('brizara_1'))     # SYW's own typo stays out
        self.assertIsNone(P.ffnx_alias('smoke'))


@unittest.skipUnless(os.path.isfile(MAGIC), 'needs the dump magic.lgp')
class Archive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import lgp
        cls.a = lgp.Archive(MAGIC)

    def test_hades_lid_is_the_only_placeholder(self):
        hits = [e['name'] for e in self.a.entries
                if e['name'].lower().endswith(('.tex', '.t00', '.t01'))
                and P.is_placeholder(e['payload'])]
        self.assertEqual(hits, ['hadesdat.t00'])

    @unittest.skipUnless(os.path.isfile(SYW), 'needs the SYW Spells .iro')
    def test_the_lid_is_built_from_syw(self):
        import iro
        import tex
        import numpy as np
        tmp = tempfile.mkdtemp()
        try:
            dds = os.path.join(tmp, 'lid.dds')
            with open(dds, 'wb') as f:
                f.write(iro.read_one(SYW, LID))
            out = P.payloads(self.a, [(LID, dds)], os.path.join(tmp, 'c'))
            names = {self.a.entries[i]['name']: d for i, d in out.items()}
            # BUILD 783: the lid's model comes with it, made opaque
            self.assertEqual(sorted(names), ['hadesdat.p45', 'hadesdat.t00'])
            data = names['hadesdat.t00']
            t = tex.parse(data)
            self.assertEqual((t['width'], t['height'], t['num_palettes'],
                              t['colors_per_palette']), (256, 256, 1, 256))
            self.assertIsNone(tex.logical_scale(data))   # UVs are normalised
            idx = np.frombuffer(t['pixels'], np.uint8).reshape(256, 256)
            self.assertEqual(int(idx[0, 0]), 0)          # clear corner
            self.assertNotEqual(int(idx[128, 128]), 0)   # the molten disc
            # nothing else in the archive is offered
            os.environ[P.ENV] = '0'
            try:
                self.assertEqual(P.payloads(self.a, [(LID, dds)], tmp), {})
            finally:
                os.environ.pop(P.ENV, None)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


FIXTURE_P45 = os.path.join(ROOT, 'tests', 'fixtures', 'hadesdat.p45')


@unittest.skipUnless(os.path.isfile(FIXTURE_P45), 'needs the stock hadesdat.p45')
class SolidLid(unittest.TestCase):
    """BUILD 783: the lid (hundred 1) is AVERAGE in stock; made opaque."""

    def setUp(self):
        with open(FIXTURE_P45, 'rb') as f:
            self.p = f.read()

    def _hundred(self, p, i):
        import struct
        o, n = P._hundreds_at(p)
        self.assertEqual(n, 2)
        return struct.unpack_from('<25I', p, o + 100 * i)

    def test_stock_lid_is_alpha_blended_average(self):
        h0, h1 = self._hundred(self.p, 0), self._hundred(self.p, 1)
        self.assertEqual(h0[17], 4)                          # the pot: none
        self.assertEqual((h1[2] & 0x400, h1[3] & 0x400, h1[17]),
                         (0x400, 0x400, 0))                  # the lid: average

    def test_only_the_lid_blend_word_changes(self):
        new = P.solid_lid(self.p, 1)
        diff = [i for i in range(len(new)) if new[i] != self.p[i]]
        o, _n = P._hundreds_at(self.p)
        self.assertEqual(diff, [o + 100 + 0x44])
        self.assertEqual(self._hundred(new, 1)[17], 4)
        self.assertEqual(P.solid_lid(new, 1), new)           # idempotent

    def test_a_foreign_state_is_refused(self):
        self.assertEqual(P.solid_lid(self.p, 0), self.p)     # already opaque
        with self.assertRaises(ValueError):
            P.solid_lid(self.p, 2)                           # no such hundred
        bad = bytearray(self.p)
        o, _n = P._hundreds_at(self.p)
        bad[o + 100 + 0x44] = 1                              # additive
        with self.assertRaises(ValueError):
            P.solid_lid(bytes(bad), 1)

    def test_the_switch(self):
        os.environ[P.SOLID_ENV] = '0'
        try:
            self.assertFalse(P.solid_enabled())
        finally:
            os.environ.pop(P.SOLID_ENV, None)
        self.assertTrue(P.solid_enabled())


if __name__ == '__main__':
    unittest.main()
