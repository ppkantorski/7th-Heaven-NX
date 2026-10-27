"""BUILD 713 -- ff7nx_palauto: two-level flickers (junpb_1's Diner sign,
juninn's lamps) and fields with Echo-S's disabled Time actor.

These read the vanilla flevel.lgp in game_data_files/ and the built one in
sdout/. Each test skips when its input is missing.

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

VANILLA = os.path.join(ROOT, 'game_data_files', 'field', 'flevel.lgp')
BUILT = os.path.join(ROOT, 'sdout', 'atmosphere', 'contents',
                     '0100A5B00BDC6000', 'romfs', 'ff7', 'workingdir',
                     'data', 'field', 'flevel.lgp')


def _parts(path, name):
    import lgp
    a = lgp.Archive(path)
    return list(lgp.split_sections(a.decompressed(a.index[name])))


def _lattice(sec0, ef):
    import ff7nx_palauto as PU
    uses = PU.var_uses(sec0, ef['var'], skip={o for o, _l in ef['sites']})
    dead = PU.dead_init_sets(sec0, uses, ef['sites'])
    return PU.lattice(sec0, uses, dead)[:4], dead


@unittest.skipUnless(os.path.exists(VANILLA), 'no vanilla flevel.lgp')
class Flicker(unittest.TestCase):

    def test_junpb_1_sign_two_states(self):
        import ff7nx_palauto as PU
        sec0 = _parts(VANILLA, 'junpb_1')[0]
        ef = [e for e in PU.find_effects(sec0) if e['q'] == 6][0]
        (step, _anchor, lo, hi), dead = _lattice(sec0, ef)
        self.assertEqual((lo, hi, step), (0x30, 0x80, 0x50))
        self.assertEqual(dead, set())

    def test_juninn_init_value_left_out(self):
        import ff7nx_palauto as PU
        sec0 = _parts(VANILLA, 'juninn')[0]
        got = {}
        for ef in PU.find_effects(sec0):
            (step, _a, lo, hi), dead = _lattice(sec0, ef)
            self.assertEqual(len(dead), 1)
            got[ef['q']] = (lo, hi, step)
        self.assertEqual(got, {7: (37, 48, 11), 9: (32, 53, 21)})

    def test_stepped_variable_unchanged(self):
        # a variable with PLUS/MINUS keeps every SET (no dead init sets)
        import ff7nx_palauto as PU
        uses = [(0, 'set', 3), (10, 'plus', 3)]
        self.assertEqual(PU.dead_init_sets(b'', uses, []), set())


@unittest.skipUnless(os.path.exists(BUILT), 'no built flevel.lgp')
class BuiltScripts(unittest.TestCase):

    def test_time_actor_does_not_block(self):
        import ff7nx_palauto as PU
        for name in ('juninn', 'cosin3', 'losinn'):
            PU._stream(_parts(BUILT, name)[0])      # raises if refused

    def test_juninn_lamps_always_on(self):
        import ff7nx_palauto as PU
        parts = _parts(BUILT, 'juninn')
        rp, bits = PU.always_on(parts[0], parts[8], 7)
        self.assertEqual(rp, 2)
        self.assertEqual(bits, {2})

    def test_param0_effect_untouched(self):
        import ff7nx_palauto as PU
        parts = _parts(BUILT, 'junpb_1')
        self.assertEqual(PU.always_on(parts[0], parts[8], 6), (0, None))


if __name__ == '__main__':
    unittest.main()
