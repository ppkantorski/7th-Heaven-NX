"""BUILD 851: two voice sources with byte-identical audio share one cache
entry, and it is written by one worker at a time (Windows WinError 5).

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
import os, shutil, tempfile, threading, time, types, unittest
import voice_ogg as V


class SameContent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (V.CACHE, V.encode, V.verify, V.encoder_id)
        V.CACHE = os.path.join(self.tmp, 'cache')
        self.writers, self.max_writers, self.encodes = {}, [0], []
        guard = threading.Lock()

        def encode(source, target):
            with guard:
                n = self.writers.get(target, 0) + 1
                self.writers[target] = n
                self.max_writers[0] = max(self.max_writers[0], n)
                self.encodes.append(target)
            time.sleep(0.05)
            with open(target, 'wb') as f:
                f.write(b'OggS' + open(source, 'rb').read())
            with guard:
                self.writers[target] -= 1
        V.encode = encode
        V.verify = lambda path: (os.path.getsize(path), 10)
        V.encoder_id = lambda: 'test'

    def tearDown(self):
        V.CACHE, V.encode, V.verify, V.encoder_id = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_identical_audio_is_encoded_once(self):
        src = os.path.join(self.tmp, 'src')
        os.makedirs(src)
        entries = []
        for i in range(12):
            p = os.path.join(src, 'line%02d.ogg' % i)
            with open(p, 'wb') as f:
                f.write(b'same audio' if i < 8 else b'other %d' % i)
            entries.append(types.SimpleNamespace(key='k%02d' % i,
                                                 source_path=p))
        out = os.path.join(self.tmp, 'out')
        report = V.stage(entries, out, filename=lambda k: k + '.ogg',
                         workers=8)
        self.assertEqual(self.max_writers[0], 1)
        self.assertEqual(len(self.encodes), 5)     # 1 shared + 4 distinct
        self.assertEqual(report['encoded'], 5)
        self.assertEqual(report['cached'], 7)
        self.assertEqual(report['staged'], 12)
        self.assertEqual(report['failed'], [])


if __name__ == '__main__':
    unittest.main()
