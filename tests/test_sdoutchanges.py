"""BUILD 850: what changed in sdout since the last build.

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
import os, shutil, tempfile, time, unittest
import build


def put(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


class SdoutChanges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sd = os.path.join(self.tmp, 'sdout')
        self._man = build.SDOUT_MANIFEST
        build.SDOUT_MANIFEST = os.path.join(self.tmp, 'manifest')
        self.p = lambda rel: os.path.join(self.sd, *rel.split('/'))
        put(self.p('atmosphere/a/exefs/main'), b'main1')
        put(self.p('atmosphere/a/romfs/flevel.lgp'), b'F' * 100)
        put(self.p('atmosphere/a/romfs/movie.mp4'), b'M' * 50)
        put(self.p('.DS_Store'), b'x')

    def tearDown(self):
        build.SDOUT_MANIFEST = self._man
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_it(self):
        return build.sdout_changes(self.sd)

    def changes_dir_files(self):
        d = os.path.join(self.tmp, build.CHANGES_DIR_NAME)
        out = []
        for base, _d, files in os.walk(d):
            out += [os.path.relpath(os.path.join(base, f), d).replace(os.sep, '/')
                    for f in files]
        return sorted(out)

    def test_first_then_unchanged(self):
        r = self.run_it()
        self.assertTrue(r['first'])
        self.assertEqual(self.changes_dir_files(), [])
        r = self.run_it()
        self.assertFalse(r['first'])
        self.assertEqual((r['added'], r['changed'], r['removed']), ([], [], []))

    def test_rewritten_same_bytes_is_not_a_change(self):
        self.run_it()
        time.sleep(0.01)
        put(self.p('atmosphere/a/romfs/flevel.lgp'), b'F' * 100)   # new mtime
        r = self.run_it()
        self.assertEqual(r['changed'], [])

    def test_changed_new_and_removed(self):
        self.run_it()
        time.sleep(0.01)
        put(self.p('atmosphere/a/exefs/main'), b'main2')
        put(self.p('atmosphere/a/romfs/new.bin'), b'N' * 7)
        os.remove(self.p('atmosphere/a/romfs/movie.mp4'))
        r = self.run_it()
        self.assertEqual(r['changed'], ['atmosphere/a/exefs/main'])
        self.assertEqual(r['added'], ['atmosphere/a/romfs/new.bin'])
        self.assertEqual(r['removed'], ['atmosphere/a/romfs/movie.mp4'])
        self.assertEqual(r['bytes'], 5 + 7)
        self.assertEqual(self.changes_dir_files(),
                         ['atmosphere/a/exefs/main', 'atmosphere/a/romfs/new.bin'])
        listing = open(os.path.join(self.tmp, build.CHANGES_LIST_NAME)).read()
        self.assertIn('movie.mp4', listing)
        self.assertIn('DELETE FROM THE SD CARD', listing)
        # the next build replaces the folder: only its own changes
        r = self.run_it()
        self.assertEqual(self.changes_dir_files(), [])

    def test_changes_dir_files_are_the_sdout_bytes(self):
        self.run_it()
        time.sleep(0.01)
        put(self.p('atmosphere/a/exefs/main'), b'main3')
        self.run_it()
        d = os.path.join(self.tmp, build.CHANGES_DIR_NAME, 'atmosphere', 'a',
                         'exefs', 'main')
        self.assertEqual(open(d, 'rb').read(), b'main3')


if __name__ == '__main__':
    unittest.main()
