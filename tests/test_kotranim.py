"""BUILD 750: the 60 FPS mod's knight animations, repaired in-betweens.

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
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import ff7nx_kotranim as K

MOD = os.path.join(ROOT, 'cache', 'ff7-60fps-mod')
MAGIC = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                     'battle', 'magic.lgp')
HAVE = os.path.isdir(MOD) and os.path.exists(MAGIC)


def vanilla():
    import lgp
    a = lgp.Archive(MAGIC)
    return {e['name'].lower(): e['payload'] for e in a.entries
            if K.NAME_RE.match(e['name'].lower())}


@unittest.skipUnless(HAVE, 'needs the dump and the 60 FPS mod cache')
class KotrAnim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = vanilla()

    def mod(self, n, sub='KOTRAnimation60FPS'):
        return open(os.path.join(MOD, sub, 'magic', n), 'rb').read()

    def test_encoder_reproduces_every_vanilla_file(self):
        self.assertEqual(len(self.v), 13)
        for n, b in self.v.items():
            _h, f = K.decode(b)
            self.assertEqual(K.encode(b, f), b, n)

    def test_knight06_weapon_arm_holds_its_key(self):
        out, fixes = K.repair(self.mod('knight06.a00'), self.v['knight06.a00'])
        self.assertIsNotNone(out)
        _h, f = K.decode(out)
        key = [x % 4096 for x in f[96][1][40]]
        self.assertEqual(key, [1024, 1469, 0])
        for fr in (97, 98, 99, 101, 102, 103, 105, 106, 107, 85, 87, 90):
            self.assertLess(K._angle(K._quat(f[fr][1][40]), K._quat(key)), 1.0, fr)
        # the shipped file really was broken there
        _h, g = K.decode(self.mod('knight06.a00'))
        self.assertEqual([x % 4096 for x in g[97][1][40]], [1024, 0, 0])

    def test_keys_and_good_frames_untouched_and_idempotent(self):
        for n, vb in self.v.items():
            src = self.mod(n)
            out, fixes = K.repair(src, vb)
            if out is None:
                continue
            _h, a = K.decode(src)
            _h, b = K.decode(out)
            changed = {(fr, bi) for fr, bi, _o, _n in fixes}
            for fr in range(len(a)):
                self.assertEqual(a[fr][0], b[fr][0])
                for bi in range(len(a[fr][1])):
                    if (fr, bi) in changed:
                        self.assertNotEqual(fr % 4, 0)
                        continue
                    self.assertEqual([x % 4096 for x in a[fr][1][bi]],
                                     [x % 4096 for x in b[fr][1][bi]], (n, fr, bi))
            self.assertIsNone(K.repair(out, vb)[0], n)

    def test_the_30fps_files_are_not_mistaken_for_4x(self):
        for n, vb in self.v.items():
            p = os.path.join(MOD, 'KOTRAnimation30FPS', 'magic', n)
            if os.path.exists(p):
                self.assertIsNone(K.repair(open(p, 'rb').read(), vb)[0], n)

    def test_vanilla_is_left_alone(self):
        for n, vb in self.v.items():
            self.assertIsNone(K.repair(vb, vb)[0], n)


if __name__ == '__main__':
    unittest.main()
