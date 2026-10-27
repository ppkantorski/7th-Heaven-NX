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
import shutil
import subprocess
import tempfile
import unittest

import audio_dat as AD
import sfxmod as S

FF = shutil.which('ffmpeg')


class _Slot:
    sample_rate, block_align, channels, loop = 44100, 1024, 1, 1


@unittest.skipUnless(FF, 'needs ffmpeg')
class LoopEnd(unittest.TestCase):
    """BUILD 759: a LOOPSTART-only loop ends where the sound ends, not where
    MS ADPCM's last block padding ends; and the circular high-pass."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.ogg = cls.tmp + '/buzz.ogg'
        # 39,808 frames: not a whole number of 1024-byte mono blocks
        # (2,036 frames each), so the encoder pads -- the sound-150 shape.
        subprocess.run([FF, '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=f=50:r=44100:d=0.902676,volume=0.3',
                        '-f', 'lavfi', '-i', 'sine=f=200:r=44100:d=0.902676',
                        '-filter_complex', 'amix=inputs=2', '-ac', '1',
                        '-metadata', 'LOOPSTART=0', '-c:a', 'libvorbis',
                        cls.ogg], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _loop(self, hp=None):
        _, wav = S._encode_cached(self.ogg, _Slot, None, True, highpass=hp)
        fmt, data, start, end = AD.parse_wav(wav)
        total = AD.decoded_bytes(fmt, len(data))
        return start, end, total

    def test_loop_stops_at_the_sound_not_the_padding(self):
        start, end, total = self._loop()
        self.assertEqual(start, 0)
        self.assertLess(end, total)                    # padding excluded
        frames = AD._decoded_frames(FF, self.ogg, 44100, 1)
        self.assertEqual(end, frames * 2)

    def test_highpass_keeps_the_loop_and_its_length(self):
        self.assertEqual(self._loop(90), self._loop())

    def test_highpass_list_is_empty_and_env_still_wins(self):
        import os
        self.assertEqual(S.HIGHPASS_HZ, {})
        self.assertIsNone(S._highpass_for(150))
        os.environ[S.HIGHPASS_ENV] = '0'
        try:
            self.assertIsNone(S._highpass_for(150))
        finally:
            os.environ.pop(S.HIGHPASS_ENV)


class Mute(unittest.TestCase):
    """BUILD 759b: sound 150 (the train's alert drone) is silenced."""

    def test_default_and_override(self):
        import os
        os.environ.pop(S.MUTE_ENV, None)
        self.assertEqual(S.muted_ids(), (150,))
        for raw, want in (('', ()), ('none', ()), ('150, 7', (150, 7))):
            os.environ[S.MUTE_ENV] = raw
            try:
                self.assertEqual(S.muted_ids(), want)
            finally:
                os.environ.pop(S.MUTE_ENV)

    @unittest.skipUnless(FF, 'needs ffmpeg')
    def test_rebuild_mutes_150_with_its_own_geometry(self):
        import os
        import numpy as np
        d = os.path.join(nxpaths.ROOT, 'dump', 'romfs', 'ff7', 'workingdir',
                         'data', 'sound')
        if not os.path.isfile(os.path.join(d, 'audio.fmt')):
            self.skipTest('no dump audio')
        ents = AD.read(os.path.join(d, 'audio.fmt'),
                       os.path.join(d, 'audio.dat'))
        before = ents[149]
        res = S.rebuild(ents, {}, {}, None)
        after = ents[149]
        self.assertEqual(res.muted, [150])
        self.assertEqual((len(after.data), after.loop, after.fmt),
                         (len(before.data), before.loop, before.fmt))
        self.assertFalse(any(after.data))
        self.assertIs(ents[150], ents[150])           # 151 untouched
        wav = (b'RIFF' + (36 + 14 + len(after.data)).to_bytes(4, 'little') +
               b'WAVEfmt ' + len(after.fmt).to_bytes(4, 'little') + after.fmt +
               b'data' + len(after.data).to_bytes(4, 'little') + after.data)
        p = subprocess.run([FF, '-v', 'error', '-i', '-', '-f', 's16le', '-'],
                           input=wav, capture_output=True).stdout
        self.assertTrue(len(p) > 0)
        self.assertEqual(int(np.abs(np.frombuffer(p, np.int16)).max()), 0)

if __name__ == '__main__':
    unittest.main()
