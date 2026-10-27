"""BUILD 711 -- field SHAKE at its 1997 pace at 60 fps (ff7nx_shake60).

These read the vanilla flevel.lgp in game_data_files/. They skip when it is
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

VANILLA = os.path.join(ROOT, 'game_data_files', 'field', 'flevel.lgp')


def _script(a, name):
    import lgp
    return lgp.split_sections(a.decompressed(a.index[name]))[0]


@unittest.skipUnless(os.path.exists(VANILLA), 'no vanilla flevel.lgp')
class Shake60(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import lgp
        cls.a = lgp.Archive(VANILLA)

    def _edits(self, name):
        import ff7nx_shake60 as S
        sec = _script(self.a, name)
        new, edits = S.plan_field(sec)
        self.assertEqual(len(new), len(sec))
        return sec, new, edits

    def test_ztruck_both_axes_doubled(self):
        _s, _n, e = self._edits('ztruck')
        self.assertIn(('x', 22, 44), [(ax, o, n) for _off, ax, o, n in e])
        self.assertIn(('y', 31, 62), [(ax, o, n) for _off, ax, o, n in e])

    def test_zcoal_periods_doubled(self):
        for name, count in (('zcoal_1', 1), ('zcoal_3', 3)):
            _s, _n, e = self._edits(name)
            self.assertEqual(len(e), count, name)
            for _off, ax, old, new in e:
                self.assertEqual((ax, old, new), ('y', 2, 4), name)

    def test_only_period_bytes_change(self):
        sec, new, e = self._edits('ztruck')
        changed = {i for i in range(len(sec)) if sec[i] != new[i]}
        wanted = {off + (5 if ax == 'x' else 7) for off, ax, _o, _n in e}
        self.assertEqual(changed, wanted)

    def test_banked_period_left_alone(self):
        import ff7nx_shake60 as S
        sec, _n, e = self._edits('ztruck')
        off = next(o for o, ax, _o, _n in e if ax == 'x')
        banked = bytearray(sec)
        banked[off + 1] |= 0x01          # x period now read from a variable
        _new, e2 = S.plan_field(bytes(banked))
        self.assertNotIn((off, 'x'), [(o, ax) for o, ax, _a, _b in e2])
        self.assertIn((off, 'y'), [(o, ax) for o, ax, _a, _b in e2])

    def test_switch_off(self):
        import ff7nx_shake60 as S
        os.environ[S.OFF_ENV] = '1'
        try:
            payloads = {}
            st = S.apply_to_flevel(self.a, payloads)
        finally:
            del os.environ[S.OFF_ENV]
        self.assertEqual(st['names'], [])
        self.assertEqual(payloads, {})


if __name__ == '__main__':
    unittest.main()
