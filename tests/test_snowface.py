"""tests/test_snowface.py

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
import os, sys, struct, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import ff7nx_snowface as F

VAN = os.path.join(os.path.dirname(HERE), 'cache', '_vanilla', 'snowboard-us.lgp')


def _load(name):
    return open(os.path.join(VAN, name), 'rb').read()


@unittest.skipUnless(os.path.isdir(VAN), 'vanilla snowboard-us.lgp not extracted')
class SnowFace(unittest.TestCase):
    def test_stock_is_recognised(self):
        for n in F.TMD_NAMES:
            self.assertEqual(F.state(n, _load(n)), 'stock', n)

    def test_patch_changes_only_the_face_object(self):
        for n in F.TMD_NAMES:
            van = _load(n)
            new, _ = F.patch(n, van)
            self.assertEqual(len(new), len(van))
            self.assertEqual(F.state(n, new), 'patched')
            vt, prims = F._layout(van, F.STOCK_PRIMS[n][0])
            allowed = set(range(vt, vt + 8 * len(F.STOCK_VERTS)))
            for q in prims:
                allowed |= set(range(q, q + 24))
            diffs = {i for i, (a, b) in enumerate(zip(new, van)) if a != b}
            self.assertTrue(diffs and diffs <= allowed, n)

    def test_only_depth_changes(self):
        for n in F.TMD_NAMES:
            van = _load(n)
            new, _ = F.patch(n, van)
            vt, prims = F._layout(van, F.STOCK_PRIMS[n][0])
            for q in prims:                           # UVs, indices: stock
                self.assertEqual(new[q:q + 24], van[q:q + 24])
            for i, (x, y, z) in enumerate(F.STOCK_VERTS):
                self.assertEqual(struct.unpack_from('<hhh', new, vt + 8 * i),
                                 (x, y, z - F.LIFT))

    def test_idempotent_and_revertible(self):
        for n in F.TMD_NAMES:
            van = _load(n)
            new, _ = F.patch(n, van)
            self.assertEqual(F.patch(n, new)[0], new)
            self.assertEqual(F.patch(n, new, revert=True)[0], van)

    def test_unknown_passes_through(self):
        bad = bytearray(_load('for_gs.tmd'))
        vt, _ = F._layout(bytes(bad), 284)
        struct.pack_into('<h', bad, vt, 99)
        out, note = F.patch('for_gs.tmd', bytes(bad))
        self.assertEqual(out, bytes(bad))
        self.assertIn('left alone', note)
        self.assertEqual(F.patch('other.tmd', b'x')[0], b'x')

    def test_env_off(self):
        os.environ[F.ENV] = '0'
        try:
            self.assertFalse(F.enabled())
        finally:
            del os.environ[F.ENV]
        self.assertTrue(F.enabled())


if __name__ == '__main__':
    unittest.main()


class StretchToRegion(unittest.TestCase):
    def test_port_squeeze_is_undone(self):
        import numpy as np
        import ff7nx_ddstex
        s = 4                                     # SYW-like source scale
        src = np.zeros((64 * s, 64 * s, 4), np.uint8)
        src[20 * s:36 * s, 18 * s:34 * s] = (30, 140, 230, 255)   # an "eye"
        out = ff7nx_ddstex.stretch_to_region(src)
        self.assertEqual(out.shape, src.shape)
        # the port samples logical (u, v) at texel (u*64/56, v*64/48)
        for u, v in ((18, 20), (25, 28), (33, 35), (10, 10), (40, 40)):
            tu = int((u + 0.5) * 64 / 56 * s)
            tv = int((v + 0.5) * 64 / 48 * s)
            want = src[int((v + 0.5) * s), int((u + 0.5) * s), 3]
            self.assertEqual(out[tv, tu, 3], want, (u, v))
