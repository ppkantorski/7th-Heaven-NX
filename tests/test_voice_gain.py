#!/usr/bin/env python3
"""
tests/test_voice_gain.py -- how loud a voice clip is actually played.

FFNx does not play a voice at unity. `set_voice_volume()` in src/voice.cpp:

    voice_volume = 2.0f + (100 - external_voice_music_fade_volume) / 100.0f

which with the shipped fade of 25 is 2.75, and it is passed to
`playVoice(name, window, voice_volume, ...)` OUTSIDE the
`enable_voice_music_fade` branch -- so it applies whether or not the music
ducks. Echo-S is mixed against that. We played 1.0, i.e. 8.8 dB below what
the mod was authored for, uniformly, on every line of every build.

That is why the encoder could measure every clip at exactly its target and
the lines could still be hard to hear: no encoder target can make up 8.8 dB
against a peak ceiling.

The gain is a setting rather than a copy of 2.75, because SoLoud's soft
clipper is what makes 2.75 survivable on PC and this port's mixer ceiling is
not known. These tests hold the two things that must be true whatever it is
set to: the DEFAULT emits the instruction every previous build emitted, and
a raised gain reaches BOTH sites -- the create and the per-tick refresh.

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
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import a64 as A                                                # noqa: E402
import ff7nx_cave as C                                          # noqa: E402
import ff7nx_voice as V                                         # noqa: E402

ENTRY = 0x600000
BSS = 0x3FEF65C

# The word every build before this one emitted at both sites.
FMOV_S0_1_0 = 0x1E2E1000


def service(voice_gain):
    """The field voice service cave, as a flat list of words."""
    return V._build_field_clocked_voice_service(
        ENTRY, lambda i: ENTRY + i * 4, BSS, voice_gain=voice_gain,
        duck_percent=100, auto_advance=False, defer_foreign_play=True)


class TheEncoder(unittest.TestCase):
    """`fmov s0, #imm` -- 256 values, and nothing else."""

    def test_one_point_zero_is_the_word_that_has_always_been_emitted(self):
        self.assertEqual(A.fmov_s_imm(0, 1.0), FMOV_S0_1_0)

    def test_the_table_is_the_whole_encodable_set(self):
        self.assertEqual(len(A.FMOV_IMM8), 256)
        for value in (1.0, 1.25, 1.375, 1.5, 1.75, 2.0, 2.75, 0.5, -1.5):
            word = A.fmov_s_imm(0, value)
            # everything but imm8 (bits 20:13) and Rd (bits 4:0) is fixed
            self.assertEqual(word & 0xFFE01FE0, 0x1E201000,
                             'not an FMOV scalar immediate: %#x' % word)
            # and it decodes back to the value asked for
            imm8 = (word >> 13) & 0xFF
            a, b = (imm8 >> 7) & 1, (imm8 >> 6) & 1
            bits = (a << 31) | ((b ^ 1) << 30) | ((0x1F if b else 0) << 25) \
                | ((imm8 & 0x3F) << 19)
            self.assertEqual(
                struct.unpack('<f', struct.pack('<I', bits))[0], value)

    def test_a_value_it_cannot_encode_is_refused_not_rounded(self):
        """Silently rounding a gain would be a very quiet bug."""
        for value in (1.4, 2.8, 1.01, 3.3):
            with self.assertRaises(ValueError):
                A.fmov_s_imm(0, value)

    def test_but_the_nearest_one_can_be_asked_for(self):
        self.assertEqual(A.nearest_fmov_imm(1.4), 1.375)
        self.assertEqual(A.nearest_fmov_imm(2.8), 2.75)
        self.assertEqual(A.nearest_fmov_imm(1.0), 1.0)


class TheServiceCave(unittest.TestCase):

    def test_the_default_build_is_the_one_every_build_has_shipped(self):
        """
        1.0 must emit the exact word, not an equivalent-looking one. This is
        what makes the default a no-op against every module already on a card
        and every cached comparison against one.
        """
        self.assertEqual(V.VOICE_GAIN_DEFAULT, 1.0)
        words = service(1.0)
        self.assertIn(FMOV_S0_1_0, words)

    def test_a_raised_gain_reaches_every_site(self):
        """
        THE REFRESH SITE IS THE ONE THAT MATTERS. `_emit_deadline_stop` runs
        on every field callback and re-applies the gain, because an
        independent player has no MusicManager entry to hold it. A 1.0 left
        behind there would let the line START at the right level and drop one
        tick later -- which would be indistinguishable, by ear, from the bug
        this is fixing.
        """
        base = service(1.0)
        raised = service(1.5)
        self.assertEqual(len(base), len(raised),
                         'the gain must not change the cave size')

        want = A.fmov_s_imm(0, 1.5)
        changed = [i for i, (x, y) in enumerate(zip(base, raised)) if x != y]
        self.assertTrue(changed, 'the gain did not reach the cave at all')
        for i in changed:
            self.assertEqual(base[i], FMOV_S0_1_0,
                             'word %d changed and was not the gain' % i)
            self.assertEqual(raised[i], want)
        # create and refresh: at least two, and no 1.0 left anywhere
        self.assertGreaterEqual(len(changed), 2, changed)
        self.assertNotIn(FMOV_S0_1_0, raised,
                         'a 1.0 survived -- the gain would be undone')

    def test_silence_is_still_silence(self):
        """`fmov s0, wzr` stops a clip. It must not be swept up by the gain
        -- a stop that plays at 1.5 is a stop that does not stop."""
        for gain in (1.0, 1.5, 2.75):
            self.assertIn(0x1E2703E0, service(gain))

    def test_every_gain_the_setting_allows_actually_builds(self):
        for gain in sorted(v for v in A.FMOV_IMM8
                           if 0 < v <= V.VOICE_GAIN_MAX):
            words = service(gain)
            self.assertIn(A.fmov_s_imm(0, gain), words, gain)

    def test_a_gain_outside_the_range_is_a_build_error(self):
        for bad in (0.0, -1.0, V.VOICE_GAIN_MAX + 0.5, 1.4):
            with self.assertRaises(ValueError):
                V._build_field_clocked_voice_service(
                    ENTRY, lambda i: ENTRY + i * 4, BSS, voice_gain=bad,
                    duck_percent=100, auto_advance=False)


class TheSetting(unittest.TestCase):

    def _gain(self, raw):
        import build
        before = os.environ.get('SEVENTH_NX_VOICE_GAIN')
        if raw is None:
            os.environ.pop('SEVENTH_NX_VOICE_GAIN', None)
        else:
            os.environ['SEVENTH_NX_VOICE_GAIN'] = raw
        try:
            return build._echo_voice_gain(lambda *_a: None)
        finally:
            if before is None:
                os.environ.pop('SEVENTH_NX_VOICE_GAIN', None)
            else:
                os.environ['SEVENTH_NX_VOICE_GAIN'] = before

    def test_the_default_is_unity_and_that_is_deliberate(self):
        """A constant cannot make an uneven set even, and the reference the
        whole set is matched to was judged correct while playing at 1.0.
        Raising this would move the one line known to be right."""
        import build
        self.assertEqual(build.ECHO_VOICE_GAIN, 1.0)
        self.assertEqual(self._gain(None), 1.0)
        self.assertEqual(self._gain(''), 1.0)
        # and the MODULE default is still the historical no-op, so anything
        # that builds without going through build.py is unchanged
        self.assertEqual(V.VOICE_GAIN_DEFAULT, 1.0)

    def test_it_takes_a_number_and_snaps_to_what_can_be_encoded(self):
        self.assertEqual(self._gain('1.5'), 1.5)
        self.assertEqual(self._gain('2.75'), 2.75)
        self.assertEqual(self._gain('1.4'), 1.375)

    def test_nonsense_falls_back_rather_than_failing_the_build(self):
        """A bad value in one environment variable must not cost someone a
        whole build; it is reported and ignored."""
        import build
        for raw in ('loud', '0', '-1', '99', 'x1.5'):
            self.assertEqual(self._gain(raw), build.ECHO_VOICE_GAIN, raw)

    def test_it_is_registered_as_main_only(self):
        """It writes exefs/main and nothing else, so trying a value must not
        cost an archive rebuild -- which is the whole reason it is a setting
        instead of a constant."""
        import build
        self.assertIn('SEVENTH_NX_VOICE_GAIN', build.MAIN_ONLY_ENV)
        self.assertIn('SEVENTH_NX_VOICE_GAIN', build.ARCHIVE_NEUTRAL_ENV)


if __name__ == '__main__':
    unittest.main()
