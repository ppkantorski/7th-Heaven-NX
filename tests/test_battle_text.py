#!/usr/bin/env python3
"""
Echo-S's in-battle conversations.

A third voice system, and the only one addressed by the LINE rather than by
game state: `_build_battle_text_command_cave` hashes the string the battle
text queue is displaying, so staging has to produce the same hash from the
same bytes -- which means reading them out of `scene.bin`.

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
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import echo_s_battletext as BT                                  # noqa: E402
import ff7nx_voice                                              # noqa: E402

SCENE = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'workingdir',
                     'data', 'lang-en', 'battle', 'scene.bin')
ECHO_SCENE = os.path.join(ROOT, 'cache', 'Echo-s', 'Echo-S', 'battle',
                          'scene.bin')
ROOT_ECHO = os.path.join(ROOT, 'cache', 'Echo-s')
VOICE = os.path.join(ROOT_ECHO, 'VO', 'Voice')


class Decoder(unittest.TestCase):
    """Byte-for-byte FFNx, because the clip names were produced by it."""

    def test_text_bytes_are_ascii_minus_0x20(self):
        raw = bytes((ord(c) - 0x20) & 0xFF for c in 'Hi') + b'\xff'
        self.assertEqual(BT.decode(raw, 0)[0], 'Hi')

    def test_only_the_terminator_ends_a_run(self):
        """
        A byte outside the printable range becomes a character the tokenizer
        drops -- it does not stop the string. Treating it as a stop cost 26
        of the 29 matches.
        """
        raw = b'\x93' + bytes((ord(c) - 0x20) & 0xFF for c in 'ok') + b'\xff'
        text, end = BT.decode(raw, 0)
        self.assertIsNotNone(text)
        self.assertEqual(end, len(raw))
        self.assertEqual(BT.tokenize(text), 'ok')

    def test_the_escapes_consume_their_operands(self):
        raw = b'\xec\x00\x00\x00' + bytes((ord(c) - 0x20) & 0xFF
                                          for c in ' gil') + b'\xff'
        self.assertEqual(BT.decode(raw, 0)[0], '{number} gil')

    def test_tokenize_matches_ffnx(self):
        self.assertEqual(BT.tokenize("Don't attack when its tail is up"),
                         'dont_attack_when_its_tail_is_up')
        self.assertEqual(BT.tokenize('Rude Ò……Ó'), 'rude_')

    def test_the_hash_is_the_one_the_cave_computes(self):
        """
        FNV-1a, over the raw bytes INCLUDING the terminator. The cave's
        constants are asserted against this module's so the two cannot drift.
        """
        self.assertEqual(BT.FNV_BASIS, 0x811C9DC5)
        self.assertEqual(BT.FNV_PRIME, 0x01000193)
        self.assertEqual(BT.fnv1a(b'\xff'),
                         ((0x811C9DC5 ^ 0xFF) * 0x01000193) & 0xFFFFFFFF)

    def test_the_key_is_the_name_the_cave_asks_for(self):
        self.assertEqual(BT.key_filename(0xDEADBEEF), 'battle/bddeadbeef.ogg')
        self.assertEqual(BT.filename(BT.pack_key(0xDEADBEEF)),
                         'battle/bddeadbeef.ogg')
        self.assertLessEqual(len('battle/bddeadbeef'),
                             ff7nx_voice.VOICE_KEY_MAX_CHARS)


class Container(unittest.TestCase):
    """
    This port's scene.bin is NOT the PC's. Sixteen GZIP scenes per block
    behind a 0x40-byte pointer header, where the PC packs four LZS ones
    behind 0x10. Reading it the PC way yields four garbage blobs per block.
    """

    @unittest.skipUnless(os.path.isfile(SCENE), 'no dump scene.bin')
    def test_every_scene_is_the_canonical_size(self):
        found = BT.scenes(SCENE)
        self.assertGreaterEqual(len(found), 200)
        self.assertEqual({len(s) for _id, s in found}, {7808})

    @unittest.skipUnless(os.path.isfile(ECHO_SCENE), 'Echo-S not extracted')
    def test_echo_s_scene_bin_reads_the_same_way(self):
        found = BT.scenes(ECHO_SCENE)
        self.assertGreaterEqual(len(found), 200)
        self.assertEqual({len(s) for _id, s in found}, {7808})


class AgainstTheRelease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (os.path.isfile(ECHO_SCENE) and os.path.isdir(VOICE)):
            raise unittest.SkipTest('Echo-S not extracted')
        files = []
        for base, _dirs, names in os.walk(os.path.dirname(VOICE)):
            for name in names:
                if name.lower().endswith('.ogg'):
                    full = os.path.join(base, name)
                    files.append((os.path.relpath(full, ROOT_ECHO), full))
        cls.files = files
        cls.clips = BT.shipped_clips(files)

    def test_it_finds_the_clips(self):
        self.assertGreaterEqual(len(self.clips), 30)

    def test_it_never_resolves_into_the_silent_tree(self):
        """
        Echo-S ships two `_battle` trees with identical filenames, one of
        them silence. Picking by walk order chose `Battle VO_S`, whose only
        contents are barks -- so nothing matched a line and every battle
        conversation stayed quiet with a clean build log.
        """
        silent = [p for p in self.clips.values()
                  if 'vo_s' in p.replace(os.sep, '/').lower()]
        self.assertEqual(silent, [])
        self.assertIn('dont_attack_when_its_tail_is_up', self.clips)

    def test_a_bark_name_is_recognised_as_one(self):
        self.assertTrue(BT.looks_like_a_bark('bolt_3'))
        self.assertTrue(BT.looks_like_a_bark('help_10'))
        self.assertFalse(BT.looks_like_a_bark('dont_attack_when_its_tail_is_up'))

    def test_most_clips_match_a_real_line(self):
        made, matched, unmatched = BT.entries(
            ECHO_SCENE, self.clips, lambda key, path: (key, path))
        self.assertGreaterEqual(len(matched), 25)
        self.assertGreater(len(made), len(matched))    # several starts each
        self.assertLessEqual(len(unmatched), 5)

    def test_the_guard_scorpion_warning_is_matched(self):
        """The line that started this: the first boss's tail."""
        made, matched, _unmatched = BT.entries(
            ECHO_SCENE, self.clips, lambda key, path: (key, path))
        self.assertIn('dont_attack_when_its_tail_is_up', matched)
        names = {BT.filename(key) for key, _path in made}
        self.assertTrue(all(n.startswith('battle/bd') for n in names))

    def test_no_two_names_collide(self):
        made, _m, _u = BT.entries(ECHO_SCENE, self.clips,
                                  lambda key, path: (key, path))
        names = [BT.filename(k) for k, _p in made]
        self.assertEqual(len(names), len(set(names)))


if __name__ == '__main__':
    unittest.main()


class RouterClassification(unittest.TestCase):
    """
    `build.py` drops any voice file `classify_voice_path` does not classify,
    so anything it refuses is invisible to every later pass. It refused two
    real shapes.
    """

    def setUp(self):
        import voicemod
        self.classify = voicemod.classify_voice_path

    def test_enemy_folders_are_recognised(self):
        """
        `_battle/enemy_%04X/<line>.ogg` is where every in-battle CONVERSATION
        lives. It was not a recognised shape, so the router discarded all 33
        of them and the staging pass could not see what it was asked to find.
        """
        got = self.classify(
            'VO/Voice/_battle/enemy_0016/dont_attack_when_its_tail_is_up.ogg')
        self.assertEqual(got, ('battle_enemy', 0x16,
                               'dont_attack_when_its_tail_is_up.ogg'))

    def test_the_actor_directory_is_hex(self):
        """
        FFNx writes `char_%02X` and `enemy_%04X`. Read as decimal, `char_0a`
        is refused outright and anything from `char_10` up lands in the wrong
        slot.
        """
        self.assertEqual(self.classify('VO/Voice/_battle/char_0a/bolt_1.ogg'),
                         ('battle', 10, 'bolt_1.ogg'))
        self.assertEqual(self.classify('VO/Voice/_battle/char_10/bolt_1.ogg'),
                         ('battle', 0x10, 'bolt_1.ogg'))

    def test_an_unknown_actor_directory_is_still_refused(self):
        self.assertIsNone(
            self.classify('VO/Voice/_battle/silent/silent_1.ogg'))
        self.assertIsNone(self.classify('VO/Voice/_battle/char_zz/x.ogg'))

    def test_field_paths_are_unchanged(self):
        self.assertEqual(self.classify('VO/Voice/md1_1/9.ogg'),
                         ('field', 'md1_1', '9.ogg'))



def _echo_files():
    out = []
    for base, _dirs, names in os.walk(ROOT_ECHO):
        for name in names:
            if name.lower().endswith('.ogg'):
                full = os.path.join(base, name)
                out.append((os.path.relpath(full, ROOT_ECHO), full))
    return out


# A full scan of scene.bin is ten seconds. These tests all ask about the same
# one, so it runs once.
_STAGED = []


def _staged():
    if not _STAGED:
        clips = BT.shipped_clips(_echo_files())
        made, matched, unmatched = BT.entries(ECHO_SCENE, clips,
                                              lambda key, src: (key, src))
        exact = {}
        for _id, scene in BT.scenes(ECHO_SCENE):
            for start in range(len(scene)):
                text, end = BT.decode(scene, start)
                if not text or len(text) < 3:
                    continue
                slug = BT.tokenize(text)
                if slug in clips:
                    exact.setdefault(BT.fnv1a(scene[start:end]), clips[slug])
        _STAGED.append((clips, made, matched, unmatched, exact))
    return _STAGED[0]


class SpeakerPrefixTests(unittest.TestCase):
    """
    Three clips are named for a speaker the line does not contain.

    Their lines open with byte 0xEA and a three-byte operand -- a
    character-name variable. FFNx's decoder has no case for it (its escape
    table starts at 0xEB), so it falls through to `char + 0x20`, wraps to a
    newline, and the tokenizer drops it. The name on disk is a value the
    decoder can never produce.
    """

    RESCUED = ('cloud_barret_be_careful', 'cloud_its_gonna_fire_that_laser',
               'barret_i_dunno_whats_goin_on_but')

    def test_0xea_is_what_swallows_the_speaker(self):
        """`!, be careful!` -- the name is a variable, not text."""
        line = bytes([0x93, 0xEA, 0x00, 0x00, 0x00, 0xB2, 0xEA, 0x00, 0x01,
                      0x0C, 0x00, 0x42, 0x45, 0x00, 0x43, 0x41, 0x52, 0x45,
                      0x46, 0x55, 0x4C, 0x01, 0xB3, 0xFF])
        text, end = BT.decode(line, 0)
        self.assertEqual(end, len(line))
        self.assertEqual(BT.tokenize(text).lstrip('_'), 'be_careful')

    def test_a_tail_must_be_two_words_and_eight_characters(self):
        tails = BT.speaker_tails({'cloud_barret_be_careful': 'x'})
        self.assertIn('be_careful', tails)
        self.assertIn('barret_be_careful', tails)
        self.assertNotIn('careful', tails)                   # one word
        self.assertNotIn('cloud_barret_be_careful', tails)   # not a suffix

    def test_a_tail_two_clips_claim_names_neither(self):
        tails = BT.speaker_tails({'cloud_look_out_now': 'x',
                                  'barret_look_out_now': 'y'})
        self.assertNotIn('look_out_now', tails)

    def test_the_rule_needs_the_underscores_the_variable_left(self):
        """
        Without them the slug is an ordinary line: if it did not match a clip
        exactly, it does not match one at all.
        """
        tails = BT.speaker_tails({'cloud_barret_be_careful': 'x'})
        self.assertEqual(BT.suffix_match('_____be_careful', tails),
                         'cloud_barret_be_careful')
        self.assertIsNone(BT.suffix_match('be_careful', tails))

    def test_barks_never_offer_a_tail(self):
        self.assertEqual(BT.speaker_tails({'cross_slash_1': 'x'}), {})

    @unittest.skipUnless(os.path.exists(ECHO_SCENE), 'needs the Echo-S cache')
    def test_every_shipped_line_now_has_a_home(self):
        clips, _made, matched, unmatched, _exact = _staged()
        lines = {n for n in clips if not BT.looks_like_a_bark(n)}
        self.assertEqual(sorted(unmatched & lines), [])
        self.assertEqual(len(matched), len(lines))
        for name in self.RESCUED:
            self.assertIn(name, matched)

    @unittest.skipUnless(os.path.exists(ECHO_SCENE), 'needs the Echo-S cache')
    def test_a_bark_is_never_dragged_in_by_a_tail(self):
        _clips, _made, matched, _un, _exact = _staged()
        self.assertEqual([n for n in matched if BT.looks_like_a_bark(n)], [])

    @unittest.skipUnless(os.path.exists(ECHO_SCENE), 'needs the Echo-S cache')
    def test_no_two_clips_share_a_staged_hash(self):
        """A collision plays the wrong line, which is worse than silence."""
        _clips, made, _m, _u, _exact = _staged()
        owners = {}
        for key, source in made:
            owners.setdefault(key, set()).add(source)
        self.assertEqual([k for k, v in owners.items() if len(v) > 1], [])

    @unittest.skipUnless(os.path.exists(ECHO_SCENE), 'needs the Echo-S cache')
    def test_the_rescue_may_only_add(self):
        """
        Every hash the exact pass found before must still be staged, against
        the same clip. A mid-sentence start position looks exactly like a
        dropped speaker variable, so the rescue is confined to clips the exact
        pass placed nowhere at all.
        """
        _clips, made, _m, _u, exact = _staged()
        staged = {key & 0xFFFFFFFF: src for key, src in made}
        for digest, source in exact.items():
            self.assertEqual(staged.get(digest), source)
        self.assertEqual(len(exact), 114)
