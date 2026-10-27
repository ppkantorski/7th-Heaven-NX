"""BUILD 756: user settings vs locked developer settings.

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
import unittest

import echo_s_battlevo as B


class SummonPool(unittest.TestCase):
    SOURCES = {'_battle/char_07/summon_%d' % n: '/x/%d.ogg' % n
               for n in (2, 1, 10)}

    def test_fills_a_character_with_clips_and_no_section(self):
        pools = {(0, 3, 0xFFFF): ['_battle/char_00/summon_1']}
        src = dict(self.SOURCES, **{'_battle/char_00/summon_1': '/y.ogg'})
        out, filled = B.fill_missing_summon_pools(pools, src)
        self.assertEqual(filled, [7])
        self.assertEqual(out[(7, 3, 0xFFFF)],
                         ['_battle/char_07/summon_1', '_battle/char_07/summon_2',
                          '_battle/char_07/summon_10'])
        self.assertEqual(out[(0, 3, 0xFFFF)], pools[(0, 3, 0xFFFF)])

    def test_an_existing_summon_section_is_left_alone(self):
        pools = {(7, 3, 0x0010): ['_battle/char_07/summon_2']}
        out, filled = B.fill_missing_summon_pools(pools, self.SOURCES)
        self.assertEqual(filled, [])
        self.assertEqual(out, pools)

    def test_no_clips_no_pool(self):
        out, filled = B.fill_missing_summon_pools({}, {})
        self.assertEqual((out, filled), ({}, []))


if __name__ == '__main__':
    unittest.main()
