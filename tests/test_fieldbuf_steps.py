"""BUILD 757: the 4x and 4.5x (1080p) field render targets.

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

import ff7nx_fieldbuf as F

MAIN = os.path.join(nxpaths.ROOT, 'dump', 'exefs', 'main')


class Steps(unittest.TestCase):
    def test_presets(self):
        self.assertEqual(F.preset(3), {'width': 1280, 'height': 720,
                                       'scale': 3, 'ws_scale': 0.75})
        p4 = F.preset(4)
        self.assertEqual((p4['width'], p4['height']), (1706, 960))
        p45 = F.preset(4.5)
        self.assertEqual((p45['width'], p45['height'], p45['ws_scale']),
                         (1920, 1080, 0.75))
        self.assertEqual(F.preset('4.5'), p45)

    def test_every_step_is_texel_aligned_and_square(self):
        for n in F.SCALES:
            p = F.preset(n)
            w, h, s = p['width'], p['height'], p['ws_scale']
            self.assertAlmostEqual(w * s / 640.0, h / 480.0, places=9, msg=n)
            origin = w / 2.0 * (1.0 - s)
            self.assertAlmostEqual(origin, round(origin), places=6, msg=n)
            self.assertLessEqual(w, F.MAX_IMM)

    def test_old_steps_unchanged(self):
        self.assertEqual([(F.preset(n)['width'], F.preset(n)['height'])
                          for n in (1, 2, 3)],
                         [(428, 240), (854, 480), (1280, 720)])

    def test_env(self):
        old = os.environ.get(F.SCALE_ENV)
        try:
            for raw, want in (('3', 3), ('4', 4), ('4.5', 4.5), ('1080p', 4.5),
                              ('0', 0), ('junk', 1)):
                os.environ[F.SCALE_ENV] = raw
                self.assertEqual(F.env_scale(1), want, raw)
            os.environ[F.SCALE_ENV] = '4.5'
            self.assertIsInstance(F.env_scale(1), float)
            os.environ[F.SCALE_ENV] = '4.0'
            self.assertIsInstance(F.env_scale(1), int)
        finally:
            if old is None:
                os.environ.pop(F.SCALE_ENV, None)
            else:
                os.environ[F.SCALE_ENV] = old

    @unittest.skipUnless(os.path.isfile(MAIN), 'no dump/exefs/main')
    def test_the_patch_writes_1080_and_reads_back(self):
        tmp = tempfile.mkdtemp()
        try:
            m = os.path.join(tmp, 'main')
            shutil.copy(MAIN, m)
            for n in (4, 4.5):
                p = F.preset(n)
                self.assertEqual(F.apply(m, p['width'], p['height'],
                                         log=lambda *_: None), 0)
                self.assertEqual(F.read_size(m), (p['width'], p['height']))
                self.assertFalse(F.verify_sites(m))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_field_buffer_and_dev_mode_never_invalidate_an_archive(self):
        """BUILD 758: picking 4x/4.5x (or launching in developer mode) must
        not refuse SEVENTH_NX_REUSE_FLEVEL -- neither touches an archive."""
        import build
        self.assertIn(F.SCALE_ENV, build.ARCHIVE_NEUTRAL_ENV)
        self.assertIn('SEVENTH_NX_DEV', build.ARCHIVE_NEUTRAL_ENV)


if __name__ == '__main__':
    unittest.main()
