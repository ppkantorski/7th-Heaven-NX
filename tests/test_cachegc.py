"""BUILD 754: cache cleanup.

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
import os, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import ff7nx_cachegc as G


def touch(path, data=b'x'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


class CacheGC(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.root = os.path.join(self.tmp, 'cache')
        self.mods = os.path.join(self.tmp, 'mods')
        os.makedirs(self.mods)
        self._root = G.ROOT
        G.ROOT = self.root
        os.environ.pop(G.ENV, None)
        self.f = {
            'used': os.path.join(self.root, '_fieldlzs', 'a' * 40),
            'stale': os.path.join(self.root, '_fieldlzs', 'b' * 40),
            'odd': os.path.join(self.root, '_fieldlzs', 'notakey.txt'),
            'mov_used': os.path.join(self.root, 'movies', 'opening.%s.mp4' % ('1' * 20)),
            'mov_old': os.path.join(self.root, 'movies', 'opening.%s.mp4' % ('2' * 20)),
            'mov_other': os.path.join(self.root, 'movies', 'audio', 'x.wav'),
            'idle': os.path.join(self.root, '_texconv', 'whatever'),
            'vanilla': os.path.join(self.root, '_vanilla', 'x'),
        }
        for p in self.f.values():
            touch(p)
        touch(os.path.join(self.root, 'Gone', '.iro-signature'))
        touch(os.path.join(self.root, 'Gone', 'big.bin'), b'y' * 100)
        touch(os.path.join(self.root, 'Here', '.iro-signature'))
        touch(os.path.join(self.mods, 'Here.iro'))

    def tearDown(self):
        G.ROOT = self._root
        G.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self):
        G.start()
        open(self.f['used'], 'rb').read()
        link = os.path.join(self.tmp, 'sd.mp4')
        if os.path.exists(link):
            os.remove(link)
        os.link(self.f['mov_used'], link)

    def exists(self, k):
        return os.path.exists(self.f[k])

    def test_fresh_build_sweeps_only_what_it_did_not_read(self):
        self.build()
        G.sweep(self.mods, True)
        self.assertTrue(self.exists('used'))
        self.assertFalse(self.exists('stale'))
        self.assertTrue(self.exists('odd'))           # not a cache key
        self.assertTrue(self.exists('mov_used'))
        self.assertFalse(self.exists('mov_old'))
        self.assertTrue(self.exists('mov_other'))     # not a top-level encode
        self.assertTrue(self.exists('idle'))          # dir untouched this build
        self.assertTrue(self.exists('vanilla'))       # never swept
        self.assertFalse(os.path.exists(os.path.join(self.root, 'Gone')))
        self.assertTrue(os.path.exists(os.path.join(self.root, 'Here')))

    def test_a_kept_archive_blocks_the_sweep_but_not_orphans(self):
        self.build()
        G.sweep(self.mods, False)
        self.assertTrue(self.exists('stale'))
        self.assertTrue(self.exists('mov_old'))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'Gone')))

    def test_report_and_off_delete_nothing(self):
        for m in ('report', '0'):
            os.environ[G.ENV] = m
            self.build()
            logs = []
            G.sweep(self.mods, True, logs.append)
            self.assertTrue(self.exists('stale'))
            self.assertTrue(os.path.exists(os.path.join(self.root, 'Gone')))
        os.environ.pop(G.ENV)

    def test_a_subprocess_argument_counts_as_use(self):
        G.start()
        open(self.f['used'], 'rb').read()
        subprocess.run([sys.executable, '-c', 'pass', self.f['stale']], check=True)
        G.sweep(self.mods, True)
        self.assertTrue(self.exists('stale'))

    # ---- BUILD 756 ---------------------------------------------------
    def test_a_listed_but_never_built_mod_is_an_orphan_too(self):
        """discover_mods writes only mod.xml for a mod that was never
        extracted -- no .iro-signature. [HighDetailWorldMap] sat there."""
        touch(os.path.join(self.root, '[Listed]', 'mod.xml'))
        touch(os.path.join(self.root, 'Kept', 'mod.xml'))
        touch(os.path.join(self.mods, 'Kept.iro'))
        touch(os.path.join(self.root, 'sfx', 'a.wav'))     # a cache, no mod.xml
        G.sweep_orphans(self.mods)
        self.assertFalse(os.path.exists(os.path.join(self.root, '[Listed]')))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'Gone')))
        self.assertTrue(os.path.exists(os.path.join(self.root, 'Kept')))
        self.assertTrue(os.path.exists(os.path.join(self.root, 'Here')))
        self.assertTrue(os.path.exists(os.path.join(self.root, 'sfx', 'a.wav')))
        self.assertTrue(self.exists('stale'))     # launch never touches entries

    def test_launch_sweep_obeys_report_and_off(self):
        for m in ('report', '0'):
            os.environ[G.ENV] = m
            G.sweep_orphans(self.mods)
            self.assertTrue(os.path.exists(os.path.join(self.root, 'Gone')))
        os.environ.pop(G.ENV)

    def test_sfx_generations_and_old_loop_close_are_swept(self):
        used = os.path.join(self.root, 'sfx', '1.ogg-10-5-44100-1024-plain.wav')
        old = os.path.join(self.root, 'sfx', '1.ogg-10-5-44100-1024.wav')
        levels = os.path.join(self.root, 'sfx', 'audible-levels.json')
        lc_old = os.path.join(self.root, 'movies', 'last4_2.loopclose.mkv')
        lc_new = os.path.join(self.root, 'movies',
                              'last4_2.loopclose3.9b8ff5d4c3dd.mkv')
        for p in (used, old, levels, lc_old, lc_new):
            touch(p)
        self.build()
        open(used, 'rb').read()
        G.sweep(self.mods, True)
        self.assertTrue(os.path.exists(used))
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(levels))
        self.assertFalse(os.path.exists(lc_old))
        self.assertTrue(os.path.exists(lc_new))   # current tag: not a sweep key


if __name__ == '__main__':
    unittest.main()
