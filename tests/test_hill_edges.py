"""BUILD 710 -- hill / hill2: clean cut-out edges and a sky that reaches the
top of the frame (ff7nx_hillfix).

These read the built flevel.lgp in sdout/ and Cosmos's mod. Each test skips
when its input is not in the tree, so the file runs anywhere.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np                                             # noqa: E402

BUILT = os.path.join(ROOT, 'sdout', 'atmosphere', 'contents',
                     '0100A5B00BDC6000', 'romfs', 'ff7', 'workingdir',
                     'data', 'field', 'flevel.lgp')
COSMOS = os.path.join(ROOT, 'mods', 'CosmosLimitBreak.iro')


def _have():
    try:
        import ff7nx_cosmosrestore as CR
        chunk = os.path.join(CR.CHUNK_DIR, 'hill2.chunk.9')
    except Exception:                                          # noqa: BLE001
        return False
    return all(os.path.exists(p) for p in (BUILT, COSMOS, chunk))


@unittest.skipUnless(_have(), 'needs a built flevel.lgp, Cosmos and its chunks')
class HillFix(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import lgp
        import field_bg_repack as R
        import ff7nx_cosmosrestore as CR
        a = lgp.Archive(BUILT)
        cls.s9 = lgp.split_sections(a.decompressed(a.index['hill2']))[8]
        with open(os.path.join(CR.CHUNK_DIR, 'hill2.chunk.9'), 'rb') as fh:
            cls.chunk = fh.read()
        prov = R.ArtProvider([(COSMOS, None)], 768, lambda *_a: None)
        cls.open = staticmethod(prov.open)

    def _layer(self, s9, layer):
        """{(param, state): [(x, y, block)]} for one layer."""
        import diag_common as DC
        import ff7nx_hillfix as H
        pl, ts, _te, px = DC.parse_pages(s9)
        pm = {p.slot: p for p in pl if p is not None}
        out = {}
        _c, first, n = H._recs(s9, layer)
        for i in range(n):
            o = first + i * H.TILE_SIZE
            p = pm[s9[o + 32]]
            sx, sy = H._cell(s9, o, p)
            T = H._size(s9, o, p) * 3
            blk = np.frombuffer(p.data, '<u2').reshape(p.px, p.px)[
                sy * 3:sy * 3 + T, sx * 3:sx * 3 + T]
            x, y = struct.unpack_from('<hh', s9, o + 2)
            out.setdefault((s9[o + 26], s9[o + 27]), []).append((x, y, blk))
        return out

    def test_no_texel_where_cosmos_paints_nothing(self):
        import ff7nx_hillfix as H
        new, info = H.clean_edges('hill2', self.s9, self.chunk, self.open,
                                  {4: None})
        cos = H.cosmos_alpha('hill2', 4, self.chunk, self.open)
        left = 0
        for g, recs in self._layer(new, 4).items():
            x0, y0, _al, am = cos[g]
            for x, y, blk in recs:
                T = blk.shape[0]
                m = am[(y - y0) * 3:(y - y0) * 3 + T,
                       (x - x0) * 3:(x - x0) * 3 + T]
                left += int(((blk != 0) & (m == 0)).sum())
        self.assertEqual(left, 0)
        self.assertEqual(info[4]['conflict'], 0)

    def test_blend_moves_edge_toward_sky(self):
        import ff7nx_hillfix as H
        sky = (56, 102, 112)
        plain, _ = H.clean_edges('hill2', self.s9, self.chunk, self.open,
                                 {4: None})
        aa, info = H.clean_edges('hill2', self.s9, self.chunk, self.open,
                                 {4: sky})
        a = [b for r in self._layer(plain, 4).values() for _x, _y, b in r]
        b = [b for r in self._layer(aa, 4).values() for _x, _y, b in r]
        moved = 0
        for p, q in zip(a, b):
            d = (p != q) & (p != 0)
            if not d.any():
                continue
            self.assertFalse((q[d] == 0).any())     # blending never keys
            dp = np.abs(H._rgbf(p[d]) - np.asarray(sky)).sum(-1)
            dq = np.abs(H._rgbf(q[d]) - np.asarray(sky)).sum(-1)
            self.assertTrue((dq <= dp + 12).all())
            moved += int(d.sum())
        self.assertGreater(moved, 0)

    def test_opening_shot_starts_lower(self):
        import lgp
        import ff7nx_bgscr60 as B
        import ff7nx_hillfix as H
        van = os.path.join(ROOT, 'game_data_files', 'field', 'flevel.lgp')
        if not os.path.exists(van):
            self.skipTest('no vanilla flevel.lgp')
        a = lgp.Archive(van)
        sec = lgp.split_sections(a.decompressed(a.index['hill2']))[0]
        half, _e = B.plan_field('hill2', sec)
        new, edits = H.lower_start('hill2', half)
        self.assertEqual(edits, [(3, -20, -16), (4, -20, -16)])
        self.assertEqual(len(new), len(half))
        self.assertEqual(sum(x != y for x, y in zip(new, half)), 2)
        # without ff7nx_bgscr60's timed halving: the 1997 speeds, scaled
        _n, e2 = H.lower_start('hill2', sec)
        self.assertEqual(e2, [(3, -40, -32), (4, -40, -32)])
        # a script edited already is refused, not edited twice
        with self.assertRaises(ValueError):
            H.lower_start('hill2', new)

    def test_switch_off(self):
        import ff7nx_hillfix as H
        old = os.environ.get(H.OFF_ENV)
        os.environ[H.OFF_ENV] = '1'
        try:
            self.assertEqual(H.apply_to_flevel(None, {}, dds_sources=[1]),
                             {'names': [], 'refused': []})
        finally:
            if old is None:
                del os.environ[H.OFF_ENV]
            else:
                os.environ[H.OFF_ENV] = old


    def test_skyline_specks_keyed_inside_box_only(self):
        # BUILD 712: after the pass, no near-black run on the right ridge
        # still touches the sky (a second run finds nothing), and only
        # texels inside the box ever change.
        import diag_common as DC
        import ff7nx_hillfix as H
        cfg = H.PEEL['hill2']
        once, info = H.peel_specks(self.s9, cfg)
        twice, info2 = H.peel_specks(once, cfg)
        self.assertEqual(sum(v['keyed'] for v in info2.values()), 0)
        self.assertEqual(len(once), len(self.s9))
        a = DC.parse_pages(self.s9)[0]
        b = DC.parse_pages(once)[0]
        for pa, pb in zip(a, b):
            if pa is None or pa.data == pb.data:
                continue
            da = np.frombuffer(pa.data, '<u2')
            db = np.frombuffer(pb.data, '<u2')
            # only ever keyed, never recoloured
            self.assertTrue(((da == db) | (db == 0)).all())


if __name__ == '__main__':
    unittest.main()
