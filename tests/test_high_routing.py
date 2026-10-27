"""tests/test_high_routing.py

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
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build
import ff7nx_minigametex as M


class HighRouting(unittest.TestCase):
    def test_high_folder_wins_over_char_name_match(self):
        # BUILD 549: fb/high/BNAA.RSD lost to char.lgp's bnaa.rsd by name
        self.assertEqual(build._minigame_direct_hits(
            'fb/high/BNAA.RSD', ['char.lgp', 'high-us.lgp']), ['high-us.lgp'])
        self.assertEqual(build._minigame_direct_hits(
            'dynamic weapons/chibi/High/BRIC.rsd', ['char.lgp', 'high-us.lgp']),
            ['high-us.lgp'])

    def test_other_folders_route_exactly_as_before(self):
        # Ninostyle's highway riders ship in fb/char and exist only in high-us
        self.assertEqual(build._minigame_direct_hits(
            'fb/char/ZCAB.rsd', ['high-us.lgp']), ['high-us.lgp'])
        self.assertEqual(build._minigame_direct_hits(
            'fb/char/BNAA.rsd', ['char.lgp', 'high-us.lgp']),
            ['char.lgp', 'high-us.lgp'])
        # the minigame folder rule is untouched
        self.assertEqual(build._minigame_direct_hits(
            'fb/chocobo/BO.TEX', ['char.lgp']), ['chocobo.lgp'])
        self.assertEqual(build._minigame_direct_hits(
            'fb/char/BO.TEX', ['chocobo.lgp', 'char.lgp']), ['char.lgp'])


class HighHud(unittest.TestCase):
    def test_hud_limits_only_touch_the_hud(self):
        self.assertEqual(M._texture_limit('high-us.lgp', 'guaa.tex', 768), 512)
        self.assertEqual(M._texture_limit('high-us.lgp', 'zsaa.tex', 768), 768)
        self.assertTrue(M._smooth_for('high-us.lgp', 'kuaa.tex'))
        self.assertFalse(M._smooth_for('high-us.lgp', 'dsaa.tex'))
        self.assertTrue(M._smooth_for('snowboard-us.lgp', 'waku.tex'))
        self.assertFalse(M._smooth_for('chocobo.lgp', 'guaa.tex'))
        self.assertEqual(M._texture_limit('chocobo.lgp', 'guaa.tex', 768), 768)


if __name__ == '__main__':
    unittest.main()
