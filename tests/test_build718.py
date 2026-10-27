"""BUILD 718 -- spipe_1's pipe light (frames from a Cosmos folder the mod
does not install, then fxcomplete's second turn), mtcrl_2's stranded
layer-1 tiles (ff7nx_strandtile) and jun_i1's counter post edge
(ff7nx_edgesmooth, palette-filtered target). The Cosmos test skips without
mods/CosmosLimitBreak.iro.

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

import ff7nx_edgesmooth as ES                                  # noqa: E402
import ff7nx_palauto as PU                                     # noqa: E402
import ff7nx_strandtile as ST                                  # noqa: E402

IRO = os.path.join(ROOT, 'mods', 'CosmosLimitBreak.iro')
ACTIVE = {'limit break/', 'limit break aa/', 'limit break aaf/', 'config/'}


class _Prov:
    def __init__(self, active, inactive):
        self.state_slots = active
        self.inactive_state_slots = inactive


class _Art:
    def __init__(self, prov):
        self.provider = prov


class FramesFallback(unittest.TestCase):
    def test_installed_frames_win(self):
        art = _Art(_Prov({('f', 15, 4): ('a', 'b')},
                         {('f', 15, 4): ('x',)}))
        used = set()
        self.assertEqual(PU._frames(art, 'F', 15, 4, used), ['a', 'b'])
        self.assertEqual(used, set())

    def test_inactive_folder_is_the_fallback(self):
        art = _Art(_Prov({}, {('f', 15, 4): ('x', 'y')}))
        used = set()
        self.assertEqual(PU._frames(art, 'f', 15, 4, used), ['x', 'y'])
        self.assertEqual(used, {4})

    def test_nothing_anywhere(self):
        art = _Art(_Prov({}, {}))
        self.assertEqual(PU._frames(art, 'f', 15, 4, set()), [])

    def test_fxcomplete_after_respects_its_skip_lists(self):
        import ff7nx_fxcomplete as FC
        name = sorted(FC.EXCLUDE)[0]
        parts = [b''] * 9
        info = {'auto': ['x'], 'mb': 1.0}
        PU._fxcomplete_after(name, parts, None, None, None, 35.0, None,
                             info)
        self.assertEqual(info, {'auto': ['x'], 'mb': 1.0})
        self.assertEqual(parts, [b''] * 9)


@unittest.skipUnless(os.path.exists(IRO), 'needs CosmosLimitBreak.iro')
class SpipeFramesAreInactive(unittest.TestCase):
    def test_spipe_1_palette_4(self):
        import field_bg_repack as R
        prov = R.ArtProvider([(IRO, ACTIVE)], 768, lambda *a: None)
        self.assertFalse(prov.state_slots.get(('spipe_1', 15, 4)))
        self.assertEqual(
            len(prov.inactive_state_slots.get(('spipe_1', 15, 4), ())), 11)


def _tile(base=27, fx=0, blend=0, param=0, state=0, use_fx=0, u=0, v=0,
          w=0):
    t = bytearray(52)
    struct.pack_into('<HH', t, ST.T_W, w, w)
    t[ST.T_PARAM] = param
    t[ST.T_STATE] = state
    struct.pack_into('<H', t, ST.T_USE_FX, use_fx)
    t[ST.T_BLEND] = blend
    t[ST.T_BASE] = base
    t[ST.T_FX] = fx
    struct.pack_into('<II', t, ST.T_BIG, u, v)
    return bytes(t)


class StrandTile(unittest.TestCase):
    def test_cell_grid(self):
        self.assertEqual(ST._cell(_tile(u=13 * ST.STEP, v=2 * ST.STEP), 0),
                         (13, 2))
        self.assertIsNone(ST._cell(_tile(u=ST.STEP // 2), 0))
        self.assertIsNone(ST._cell(_tile(u=16 * ST.STEP), 0))

    def test_only_static_opaque_tiles_move(self):
        self.assertTrue(ST._static_l1(_tile(), 0))
        self.assertTrue(ST._static_l1(_tile(w=16), 0))
        for bad in (dict(fx=15), dict(blend=1), dict(param=61),
                    dict(state=3), dict(use_fx=1), dict(w=32)):
            self.assertFalse(ST._static_l1(_tile(**bad), 0), bad)

    def test_scope(self):
        # BUILD 720: every field (test_build720 covers the picture match)
        self.assertIsNone(ST.FIELDS)


class EdgeSmoothTarget(unittest.TestCase):
    def test_jun_i1_is_the_post_only(self):
        (layer, period, x0, fringe, guard, pals), = ES.TARGETS['jun_i1']
        self.assertEqual((layer, period, guard, pals), (2, None, (), {3}))
        # BUILD 720: the fringe recolour is off, CUT_RIM does the rim
        self.assertIsNone(fringe)

    def test_old_targets_unchanged(self):
        for name in ('ztruck', 'mtcrl_4', 'las4_4'):
            for t in ES.TARGETS[name]:
                self.assertEqual(len(t), 5, name)


if __name__ == '__main__':
    unittest.main()
