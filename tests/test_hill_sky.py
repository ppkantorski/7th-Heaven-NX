"""BUILD 709 -- hill / hill2: the sky behind the 16:9 margin.

Two fixes, both measured on hill2 (the morning on the hill before the
crater) and hill (the night before):

  * ff7nx_blackcell no longer paints black where Cosmos paints nothing, so
    the layer-1 margin cells over the sky stay see-through;
  * ff7nx_bgscr60 halves scripted layer moves that start and stop, so the
    sky lands where 1997 put it at 60 fps.

These read the real archives and mods. Each test skips when its input is
not in the tree, so the file runs anywhere.

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
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np                                             # noqa: E402

VANILLA = os.path.join(ROOT, 'game_data_files', 'field', 'flevel.lgp')
COSMOS = os.path.join(ROOT, 'mods', 'CosmosLimitBreak.iro')


def _script(name):
    import lgp
    a = lgp.Archive(VANILLA)
    return list(lgp.split_sections(a.decompressed(a.index[name])))[0]


@unittest.skipUnless(os.path.exists(VANILLA), 'no vanilla flevel.lgp')
class BgscrTimed(unittest.TestCase):

    def test_hill2_moves_are_halved(self):
        import ff7nx_bgscr60 as B
        _sec, edits = B.plan_field('hill2', _script('hill2'))
        got = {(l, a, b) for l, a, b in edits}
        # the sky entity's one-frame placement and the director's scroll
        self.assertIn((3, (0, 560), (0, 280)), got)
        self.assertIn((4, (320, 800), (160, 400)), got)
        self.assertIn((3, (0, -40), (0, -20)), got)
        self.assertIn((3, (0, -5), (0, -2)), got)       # truncates, as FFNx

    def test_hill_moves_are_halved(self):
        import ff7nx_bgscr60 as B
        _sec, edits = B.plan_field('hill', _script('hill'))
        self.assertIn((3, (0, 560), (0, 280)), {(l, a, b) for l, a, b in edits})

    def test_persistent_unchanged(self):
        import ff7nx_bgscr60 as B
        _sec, edits = B.plan_field('fship_1', _script('fship_1'))
        self.assertEqual(edits, [(3, (-512, 0), (-256, 0))])

    def test_variable_speed_layers_left_whole(self):
        import ff7nx_bgscr60 as B
        for name in ('junonl2', 'junonr2', 'zcoal_3'):
            with self.assertRaises(ValueError, msg=name):
                B.plan_field(name, _script(name))

    def test_switch_restores_708(self):
        import ff7nx_bgscr60 as B
        old = B.TIMED
        try:
            B.TIMED = False
            with self.assertRaises(ValueError):
                B.plan_field('hill2', _script('hill2'))
        finally:
            B.TIMED = old

    def test_script_still_decodes(self):
        import ff7nx_bgscr60 as B
        sec = _script('hill2')
        new, _e = B.plan_field('hill2', sec)
        self.assertEqual(len(new), len(sec))
        self.assertEqual([o[0] for o in B._ops(new)],
                         [o[0] for o in B._ops(sec)])


@unittest.skipUnless(os.path.exists(VANILLA) and os.path.exists(COSMOS),
                     'needs vanilla flevel.lgp and CosmosLimitBreak.iro')
class BlackcellCover(unittest.TestCase):
    """hill2's margin cells at dy 72/88 must stay keyed where Cosmos is clear."""

    CLEAR = [(-224, 72), (-208, 72), (-192, 72), (176, 72), (192, 72),
             (208, 72), (-224, 88), (-208, 88)]

    @classmethod
    def setUpClass(cls):
        import lgp
        import preflight_marginart as PF
        import ff7nx_palrange as PR
        import ff7nx_marginart as MA
        import field_bg_repack as R
        prov = R.ArtProvider([(COSMOS, None)], 512, lambda *_a: None)
        # staticmethod: a plain function on the class would bind to `self`
        art = MA.provider_source(prov)
        cls.art = staticmethod(art)
        a = lgp.Archive(PF.DUMP)
        raw = PF._with_mod_section9(a.decompressed(a.index['hill2']), 'hill2')
        parts = lgp.split_sections(raw)
        parts[8], _ = PR.fix_field(parts[3], parts[8], name='hill2',
                                   art_for=prov.open('hill2'))
        raw = lgp.join_sections(parts)
        new, _ = MA.fill_field('hill2', raw, lgp, art,
                               scope=PF.build_scope())
        cls.raw = new or raw

    def _cells(self, raw):
        import lgp
        import diag_common as DC
        import ff7nx_marginblack as MB
        parts = lgp.split_sections(raw)
        s9 = parts[8]
        surv = DC.survey(s9)
        pages = {p.slot: p for p in surv['pages']}
        out = {}
        for t in MB.read_tiles(s9, surv, pages):
            if t.layer != 1 or (t.dx, t.dy) not in self.CLEAR:
                continue
            p = pages[t.slot]
            a = np.frombuffer(p.data, np.uint8)
            px = int(round(len(a) ** 0.5))
            k = px // 256
            out[(t.dx, t.dy)] = a.reshape(px, px)[t.sy * k:(t.sy + 16) * k,
                                                  t.sx * k:(t.sx + 16) * k]
        return out

    def test_clear_cells_stay_keyed(self):
        import ff7nx_blackcell as BC
        new, st = BC.fill_field('hill2', self.raw, self.art, overlay=True)
        cells = self._cells(new or self.raw)
        self.assertEqual(len(cells), len(self.CLEAR))
        for pos, c in cells.items():
            self.assertFalse(c.any(), '%s was painted' % (pos,))
        self.assertGreaterEqual(st.get('mod_clear', 0), len(self.CLEAR))

    def test_708_painted_them(self):
        import ff7nx_blackcell as BC
        old = BC.HONOUR_COVER
        try:
            BC.HONOUR_COVER = False
            new, _st = BC.fill_field('hill2', self.raw, self.art, overlay=True)
        finally:
            BC.HONOUR_COVER = old
        cells = self._cells(new)
        self.assertTrue(all(c.all() for c in cells.values()))


if __name__ == '__main__':
    unittest.main()
