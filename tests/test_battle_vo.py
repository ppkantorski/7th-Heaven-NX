#!/usr/bin/env python3
"""
Echo-S's battle barks: the index, the key, and the one number that has to
agree in two files.

THE FAILURE THESE GUARD AGAINST
===============================
The runtime builds its own filename out of live battle state and hands it to
the native player. If the build stages a different name, the player opens
nothing and the bark is silent -- with no error anywhere, in the build or on
the console. So the name is asserted against the exact bytes the cave emits,
not against a restatement of the format.

The variant digit is the same hazard twice over: the cave selects a slot with
`counter & (2**BATTLE_VARIANT_BITS - 1)` and the build stages
`echo_s_battlevo.VARIANTS` of them. If those two ever disagree the cave asks
for a slot that was never staged, and again the only symptom is silence.

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
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import struct                                                  # noqa: E402

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN   # noqa: E402

import echo_s_battlevo as B                                    # noqa: E402
import ff7nx_voice                                             # noqa: E402
import voicemod                                                # noqa: E402

BSS = 0x3FEF65C
_MD = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)


def _decode(words):
    blob = b''.join(struct.pack('<I', w) for w in words)
    return ['%s %s' % (i.mnemonic, i.op_str)
            for i in _MD.disasm(blob, 0x500000)]

ECHO = os.path.join(ROOT, 'cache', 'Echo-s')


def have_echo():
    return B.config_path(ECHO) is not None


class Contract(unittest.TestCase):
    def test_variant_count_matches_the_cave_mask(self):
        """
        The cave masks the action counter to select a slot. Staging a
        different number of slots means it can ask for one that is not there,
        and a missing battle clip is silent with nothing logged.
        """
        self.assertEqual(B.VARIANTS, 1 << ff7nx_voice.BATTLE_VARIANT_BITS)

    def test_the_filename_is_the_key_the_cave_builds(self):
        """
        `battle/bc<char:02x>/<cmd:02x><action:04x><variant:01x>` -- spelled
        out here so a change to either side has to be deliberate.
        """
        self.assertEqual(B.key_filename(0x00, 0x02, 0x0000, 0),
                         'battle/bc00/02/00000.ogg')
        self.assertEqual(B.key_filename(0x0A, 0x1F, 0xABCD, 7),
                         'battle/bc0a/1f/abcd7.ogg')

    def test_the_cave_writes_the_actor_separator_where_the_name_has_it(self):
        """
        The slash is emitted by the cave, and its offset has to match the
        staged name exactly. A mismatch here is every bark silent, so assert
        against the produced instructions rather than against the format
        string twice.
        """
        import a64 as A                                        # noqa: E402
        words = ff7nx_voice._build_battle_action_command_cave(
            0, lambda i: 0x800000 + i * 4, 0x3FEF660, 25)
        slash = A.movz(8, ord('/'))
        pairs = {words[i + 1] for i, w in enumerate(words[:-1]) if w == slash}
        # party members: `bc` + 2 char digits, then 2 command digits.
        self.assertIn(A.strb(8, 1, 4), pairs)
        self.assertIn(A.strb(8, 1, 7), pairs)
        # enemies: `be` + 4 formation digits, then 2 command digits.
        self.assertIn(A.strb(8, 1, 6), pairs)
        self.assertIn(A.strb(8, 1, 9), pairs)

    def test_the_name_has_exactly_the_separators_the_cave_writes(self):
        """Two levels in the cave must be two levels in the staged name."""
        name = B.key_filename(0x00, 0x02, 0x0000, 0)
        self.assertEqual(name.count('/'), 3)   # battle/ + actor/ + command/

    @unittest.skipUnless(have_echo(), 'Echo-S not extracted')
    def test_no_directory_holds_much_more_than_a_field_voice_one_does(self):
        """
        The reason the separators exist, measured on the real release rather
        than on a fixture.

        One flat battle directory was 6,728 files against field voice's
        largest of 344, and a bark pays for that lookup on the frame the
        action fires, on the main thread, through LayeredFS. Splitting on the
        actor gave a 728 median; splitting on the command as well gives 112,
        with the worst case (Magic, ~47 spells x 8 variants) at 376 -- the
        same order as a busy field.
        """
        import collections                                     # noqa: E402
        with open(B.config_path(ECHO), encoding='utf-8',
                  errors='replace') as handle:
            pools = B.parse_config(handle.read())
        counts = collections.Counter()
        for (char, command, action) in pools:
            for variant in range(B.VARIANTS):
                name = B.key_filename(char, command, action, variant)
                counts[name.rsplit('/', 1)[0]] += 1
        median = sorted(counts.values())[len(counts) // 2]
        self.assertGreater(len(counts), 20)
        self.assertLessEqual(median, 200, counts.most_common(3))
        self.assertLessEqual(max(counts.values()), 400, counts.most_common(3))

    def test_a_command_only_section_becomes_the_ffff_fallback(self):
        """The cave falls back to the command key with an ffff action."""
        pools = B.parse_config('[_battle-char_03-cmd_05]\n'
                               'shuffle = [ "_battle/char_03/steal_1" ]\n')
        self.assertEqual(list(pools), [(3, 5, 0xFFFF)])
        self.assertEqual(B.key_filename(3, 5, 0xFFFF, 0),
                         'battle/bc03/05/ffff0.ogg')

    def test_the_key_fits_the_runtime_buffer(self):
        longest = B.key_filename(0xFF, 0xFF, 0xFFFF, 0xF)[:-len('.ogg')]
        self.assertLessEqual(len(longest), ff7nx_voice.VOICE_KEY_MAX_CHARS)

    def test_actor_and_command_are_hex_not_decimal(self):
        """`char_0A` is slot ten. Reading it as decimal silently mis-keys it."""
        pools = B.parse_config('[_battle-char_0A-cmd_0B_00FF]\n'
                               'shuffle = [ "_battle/char_0a/x_1" ]\n')
        self.assertEqual(list(pools), [(0x0A, 0x0B, 0x00FF)])

    def test_silent_padding_is_never_staged(self):
        """
        Echo-S implements its percentage by padding pools with silent files.
        The cave has a counter instead, so staging them would be dead weight
        AND would make a quarter of barks play silence twice over.
        """
        pools = B.parse_config(
            '[_battle-char_00-cmd_02_0000]\n'
            'shuffle = [ "_battle/char_00/a_1", "_battle/silent/silent_1" ]\n')
        self.assertEqual(pools[(0, 2, 0)], ['_battle/char_00/a_1'])

    def test_sections_this_port_does_not_serve_are_skipped(self):
        """Result text and enemy formations are other hooks, not this one."""
        pools = B.parse_config(
            '[_battle-char_00-cmd_05_stole]\nshuffle = [ "_battle/x/y_1" ]\n'
            '[_battle-enemy_10-cmd_02_0000]\nshuffle = [ "_battle/x/z_1" ]\n')
        self.assertEqual(pools, {})

    def test_a_short_pool_fills_every_slot(self):
        """
        Repeating is the point: a two-clip pool must still answer all eight
        slots, because the cave will ask for every one of them.
        """
        pools = {(0, 2, 0): ['a', 'b']}
        made, missing = B.entries(pools, {'a': '/A.ogg', 'b': '/B.ogg'},
                                  lambda k, p: (k, p))
        self.assertEqual(len(made), B.VARIANTS)
        self.assertEqual([p for _k, p in made],
                         ['/A.ogg', '/B.ogg'] * (B.VARIANTS // 2))
        self.assertEqual(missing, [])

    def test_a_clip_the_mod_does_not_ship_is_reported_not_staged(self):
        pools = {(0, 2, 0): ['there', 'gone']}
        made, missing = B.entries(pools, {'there': '/T.ogg'},
                                  lambda k, p: (k, p))
        self.assertEqual(missing, ['gone'])
        self.assertEqual({p for _k, p in made}, {'/T.ogg'})
        self.assertEqual(len(made), B.VARIANTS)


class SilentSet(unittest.TestCase):
    """
    `Battle VO_S` is the SILENT twin of `Battle VO`, same filenames throughout.
    Picking it means every bark is silence and nothing anywhere says so, which
    is the single worst failure available in this module.
    """

    def test_the_performance_wins_whichever_order_the_walk_gives(self):
        real = ('Battle VO/Voice/_battle/char_00/bolt_1.ogg', '/real.ogg')
        mute = ('Battle VO_S/Voice/_battle/char_00/bolt_1.ogg', '/mute.ogg')
        for order in ([real, mute], [mute, real]):
            found = B.sources_from_files(order)
            self.assertEqual(found['_battle/char_00/bolt_1'], '/real.ogg',
                             order)

    def test_the_silent_tree_is_recognised_by_name(self):
        self.assertTrue(B.is_silent_set('Battle VO_S/Voice/x/y.ogg'))
        self.assertFalse(B.is_silent_set('Battle VO/Voice/x/y.ogg'))

    def test_the_silent_tree_is_still_usable_when_it_is_all_there_is(self):
        """Never drop a clip outright: a silent one beats a missing slot."""
        found = B.sources_from_files(
            [('Battle VO_S/Voice/_battle/char_00/bolt_1.ogg', '/mute.ogg')])
        self.assertEqual(found['_battle/char_00/bolt_1'], '/mute.ogg')

    @unittest.skipUnless(have_echo(), 'Echo-S not extracted')
    def test_on_the_release_nothing_resolves_into_the_silent_tree(self):
        files = []
        for base, _dirs, names in os.walk(ECHO):
            for name in names:
                if name.lower().endswith('.ogg'):
                    full = os.path.join(base, name)
                    files.append((os.path.relpath(full, ECHO), full))
        with open(B.config_path(ECHO), encoding='utf-8',
                  errors='replace') as handle:
            pools = B.parse_config(handle.read())
        sources = B.sources_from_files(files)
        used = {sources[c] for clips in pools.values() for c in clips
                if c in sources}
        self.assertTrue(used)
        from_silent = [p for p in used
                       if re.search(r'(?i)battle vo_s', p.replace('\\', '/'))]
        self.assertEqual(from_silent, [])
        # And the give-away: the silent twins are all about 4 KB.
        self.assertGreater(
            sum(os.path.getsize(p) for p in used) / len(used), 10 * 1024)


class AgainstTheRelease(unittest.TestCase):
    """Numbers from the real Echo-S release, not from a fixture."""

    @classmethod
    def setUpClass(cls):
        if not have_echo():
            raise unittest.SkipTest('Echo-S not extracted')
        with open(B.config_path(ECHO), encoding='utf-8',
                  errors='replace') as handle:
            cls.pools = B.parse_config(handle.read())
        files = []
        for base, _dirs, names in os.walk(ECHO):
            for name in names:
                if name.lower().endswith('.ogg'):
                    full = os.path.join(base, name)
                    files.append((os.path.relpath(full, ECHO), full))
        cls.sources = B.sources_from_files(files)

    def test_it_finds_the_action_keys(self):
        exact = [k for k in self.pools if k[2] != 0xFFFF]
        fallback = [k for k in self.pools if k[2] == 0xFFFF]
        self.assertGreater(len(exact), 700)
        self.assertGreater(len(fallback), 15)

    def test_every_key_stages_every_slot(self):
        made, _missing = B.entries(
            self.pools, self.sources,
            lambda key, path: voicemod.Entry(key, source_path=path))
        by_key = {}
        for entry in made:
            char, cmd, action, variant = B.unpack_key(entry.key)
            by_key.setdefault((char, cmd, action), set()).add(variant)
        self.assertTrue(by_key)
        for key, variants in by_key.items():
            self.assertEqual(variants, set(range(B.VARIANTS)), key)

    def test_no_two_entries_claim_one_filename(self):
        made, _missing = B.entries(
            self.pools, self.sources,
            lambda key, path: voicemod.Entry(key, source_path=path))
        names = [B.filename(e.key) for e in made]
        self.assertEqual(len(names), len(set(names)))

    def test_the_config_covers_far_more_than_the_stem_ingester(self):
        """
        The reason this module exists. `ACTION_NAME_TO_ID` resolves a stem to
        an action id and misses every pool name; the config addresses the keys
        directly.
        """
        made, _missing = B.entries(
            self.pools, self.sources,
            lambda key, path: voicemod.Entry(key, source_path=path))
        self.assertGreater(len({e.source_path for e in made}), 800)


if __name__ == '__main__':
    unittest.main()


# ------------------------------------- what 3x collisions need, FINDINGS 509
class Collisions(unittest.TestCase):
    """
    Facts established while diagnosing the 3x truncation, pinned so the next
    attempt does not have to rediscover them. The FIX is not in yet -- see
    FINDINGS-509 for why a build-time flag is the wrong shape.
    """

    def cave(self):
        import ff7nx_voice as V
        return V._build_battle_text_command_cave(
            0x500000, lambda i: 0x500000 + 4 * i, BSS)

    def test_battle_publishes_the_signature_as_the_owner(self):
        """
        THE FACT THAT MAKES A FIX POSSIBLE. In the FIELD the voice owner is a
        window id, so two lines on one window compare equal and replace. In
        BATTLE `battle_text_publish` stores BATTLE_TEXT_LAST_OFF -- the
        line's own text signature -- and `create` copies it to OWNER, so
        every NEW line differs from the one playing.

        That means the service's non-replace path would genuinely defer in
        battle rather than silently doing nothing. If this ever became a
        constant, any future defer would stop working without a symptom.
        """
        import ff7nx_voice as V
        listing = _decode(self.cave())
        publish = listing.index('str w8, [x24, #%#x]'
                                % V.VOICE_PENDING_OWNER_OFF)
        self.assertIn('ldr w8, [x24, #%#x]' % V.BATTLE_TEXT_LAST_OFF,
                      listing[:publish])

    def test_barks_and_battle_text_share_one_service(self):
        """
        THE FACT THAT RULES OUT THE EASY FIX. `battle_probability or
        battle_text` builds a SINGLE service, so `allow_foreign_replace` is
        not per-line: turning it off to let a scripted boss line finish would
        also defer attack barks, and a late bark is wrong -- which is what
        `test_battle_still_replaces_rather_than_holding` guards.
        """
        source = open(nxpaths.src_file('ff7nx_voice.py'),
                      encoding='utf-8').read()
        build = source[:source.index('battle_service_entry, '
                                     'battle_service_placed =')]
        guard = build.rindex('if battle_probability or battle_text:')
        # nothing between the guard and the build splits barks from text
        between = build[guard:]
        self.assertNotIn('if battle_text', between)
        self.assertNotIn('if battle_probability:', between)
