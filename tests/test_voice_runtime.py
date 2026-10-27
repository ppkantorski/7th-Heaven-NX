#!/usr/bin/env python3
"""
The Echo-S dialogue runtime, and the three sites it has to share.

WHAT IS ACTUALLY AT RISK HERE
=============================
The caves themselves are the author's and were settled on hardware over about
a hundred revisions. Re-testing their internals from a desk would be theatre.
What the PORT can break -- and what these tests are about -- is everything
around them:

  * that the sixteen sites it hooks are the sites it thinks they are, in the
    module this project actually ships rather than the one the author had;
  * that the three already-owned sites are CHAINED and not displaced, so
    analog-360 and the Cosmo Memory ambient bridge still run;
  * that it writes nothing outside its own hook sites except padding that was
    verified zero in THIS module, with eighteen other caves already in it;
  * that the pass order it depends on is enforced by a failure rather than by
    a comment.

THE ONE-INSTRUCTION TRAP
========================
`ff7nx_ambient.FIELD_HOOK` is 0x947CF4. `ff7nx_voice.FIELD_HOOK` is 0x947CF0.
They are one instruction apart and the difference is not cosmetic: W8 and W9
are loaded BY the instruction at 0x947CF0 and consumed at 0x947CF8, so a cave
written for the earlier site runs before they exist and a cave written for the
later one must preserve them. Importing the wrong name would compile, install,
and corrupt. It gets its own assertion.

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
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import ambientmod                                              # noqa: E402
import ff7nx_ambient as AM                                     # noqa: E402
import ff7nx_audio_cave as AC                                  # noqa: E402
import ff7nx_tables                                            # noqa: E402
import ff7nx_voice as VR                                       # noqa: E402

DUMP_MAIN = os.path.join(ROOT, 'dump', 'exefs', 'main')
STOCK_MAIN = os.path.join(ROOT, 'dump', 'exefs', 'main')
BUILT_MAIN = os.path.join(ROOT, 'sdout', 'atmosphere', 'contents',
                          '0100A5B00BDC6000', 'exefs', 'main')
FA_CONFIG = os.path.join(ROOT, 'cache', '[Tsunamods]_Cosmo_Memory', 'FA',
                         'Ambient', 'config.toml')


def a_module():
    for path in (DUMP_MAIN, STOCK_MAIN):
        if os.path.isfile(path):
            return path
    return None


def text_of(path):
    with open(path, 'rb') as handle:
        _segs, raw = AC.segments(handle.read())
    return raw[0]


def word(blob, va):
    return struct.unpack_from('<I', blob, va)[0]


def bss_of(path):
    with open(path, 'rb') as handle:
        return struct.unpack_from('<I', handle.read(0x40), 0x3C)[0]


def ambient_tables():
    configs, _oggs = ambientmod.collect([(FA_CONFIG, FA_CONFIG)])
    return AM.tables_from_config(ambientmod.merge_configs(configs))


class Sites(unittest.TestCase):
    """Every hooked address, against the real stock module."""

    @classmethod
    def setUpClass(cls):
        path = a_module()
        if path is None:
            raise unittest.SkipTest('no stock module available')
        cls.text = text_of(path)

    def test_all_runtime_sites_hold_the_word_they_were_derived_against(self):
        pairs = [
            ('MESSAGE pre', VR.MESSAGE_PRE_HOOK, VR.MESSAGE_PRE_ORIG),
            ('MESSAGE', VR.MESSAGE_HOOK, VR.MESSAGE_ORIG),
            ('ASK pre', VR.ASK_PRE_HOOK, VR.ASK_PRE_ORIG),
            ('field service', VR.FIELD_HOOK, VR.FIELD_ORIG),
            ('battle service', VR.BATTLE_HOOK, VR.BATTLE_ORIG),
            ('world service', VR.WORLD_SERVICE_HOOK, VR.WORLD_SERVICE_ORIG),
            ('battle action', VR.BATTLE_ACTION_HOOK, VR.BATTLE_ACTION_ORIG),
            ('battle text', VR.BATTLE_TEXT_HOOK, VR.BATTLE_TEXT_ORIG),
            ('name change', VR.NAME_CHANGE_HOOK, VR.NAME_CHANGE_ORIG),
            ('name gate', VR.NAME_CHANGE_STATE_GATE,
             VR.NAME_CHANGE_STATE_GATE_ORIG),
            ('input refresh', VR.INPUT_POST_REFRESH_HOOK,
             VR.INPUT_POST_REFRESH_ORIG),
            ('ogg worker loop', VR.WORKER_LOOP_HOOK, VR.WORKER_LOOP_ORIG),
            ('field MAPJUMP', VR.MAPJUMP_HOOK, VR.MAPJUMP_ORIG),
        ]
        for name, va, want in pairs:
            self.assertEqual(word(self.text, va), want,
                             '%s site +0x%X is not what it was derived '
                             'against' % (name, va))
        for va, want, _target, context in (VR.WORLD_MESSAGE_HOOKS
                                           + VR.WORLD_ASK_HOOKS):
            self.assertEqual(word(self.text, va), want,
                             'world %s site +0x%X moved' % (context, va))

    def test_the_field_site_is_not_the_ambient_one(self):
        """
        One instruction apart, and the difference decides whether W8/W9 are
        live when the cave runs. A shared import here would be a silent
        corruption, so the two are asserted to be different addresses and the
        ambient one to be exactly four bytes later.
        """
        self.assertNotEqual(VR.FIELD_HOOK, AM.FIELD_HOOK)
        self.assertEqual(AM.FIELD_HOOK, VR.FIELD_HOOK + 4)
        # ...and each expects the instruction that really is at its own site.
        self.assertEqual(word(self.text, VR.FIELD_HOOK), VR.FIELD_ORIG)
        self.assertEqual(word(self.text, AM.FIELD_HOOK), AM.FIELD_ORIG)

    def test_the_battle_and_world_services_really_are_shared(self):
        """These two ARE the same address as ambient's, deliberately."""
        self.assertEqual(VR.BATTLE_HOOK, AM.BATTLE_HOOK)
        self.assertEqual(VR.WORLD_SERVICE_HOOK, AM.WORLD_HOOK)


class Install(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = a_module()
        if cls.base is None:
            raise unittest.SkipTest('no module available to patch')

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='voice-runtime-')
        self.out = os.path.join(self.tmp, 'main')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_installs_on_the_stock_module(self):
        report = VR.apply_to_nso(self.base, self.out)
        after = text_of(self.out)
        self.assertEqual(word(after, VR.MESSAGE_PRE_HOOK) >> 26, 0x05)  # B
        self.assertTrue(report['field_entry'])
        self.assertTrue(report['world_service_entry'])

    def test_grows_bss_by_exactly_its_own_block_and_nothing_else(self):
        report = VR.apply_to_nso(self.base, self.out)
        self.assertEqual(bss_of(self.out),
                         bss_of(self.base) + VR.VOICE_SCRATCH_BYTES
                         + report['scratch_align_pad'])
        self.assertEqual(len(text_of(self.out)), len(text_of(self.base)))

    def test_the_scratch_block_is_aligned(self):
        """
        Every structure in the block is at an 8-byte-multiple offset and is
        reached with 64-bit LDR/STR and LDP/STP. The base is wherever the
        PREVIOUS pass left BSS, and nothing upstream promises that is aligned:
        on the stock module it lands 8-aligned by luck, and composed after the
        five Cosmo Memory bridges it lands on 4 mod 8. So it is aligned here,
        and the padding is added to the growth so the block still sits inside
        what the loader allocates.
        """
        report = VR.apply_to_nso(self.base, self.out)
        self.assertEqual(report['scratch'] % 16, 0)
        self.assertLess(report['scratch_align_pad'], 16)
        # ...and the aligned block still ends inside the declared BSS.
        with open(self.out, 'rb') as handle:
            head = handle.read(0x40)
        segs = [struct.unpack_from('<III', head, off) for off in (0x10, 0x20, 0x30)]
        data_end = (segs[2][1] + segs[2][2] + 0xFFF) & ~0xFFF
        self.assertLessEqual(report['scratch'] + VR.VOICE_SCRATCH_BYTES,
                             data_end + bss_of(self.out))

    CAVES = ('message', 'ask', 'name_change', 'field', 'mapjump',
             'transition_worker', 'world_message', 'world_ask',
             'world_service', 'input')

    def _caves_at(self, level):
        out = os.path.join(self.tmp, 'level-' + level)
        report = VR.apply_to_nso(self.base, out, level=level)
        return {c for c in self.CAVES if report.get(c + '_entry')}

    def test_the_two_smallest_levels_split_core_in_half(self):
        """
        A bisect is only worth running if each step isolates ONE thing.
        `service` and `message` each install exactly one of core's two caves,
        they share none, and together they are core.
        """
        nothing = self._caves_at('none')
        service = self._caves_at('service')
        message = self._caves_at('message')
        core = self._caves_at('core')
        self.assertEqual(nothing, set())
        self.assertEqual(service,
                         {'field', 'mapjump', 'transition_worker'})
        self.assertEqual(message, {'message'})
        self.assertEqual(service & message, set())
        self.assertEqual(service | message, core)

    def test_from_core_up_each_level_only_adds(self):
        """
        Above core the levels stack, so the first one that crashes names the
        layer. A level that dropped a cave would make that answer a lie.
        """
        ladder = VR.VOICE_LEVELS[VR.VOICE_LEVELS.index('core'):]
        previous = None
        for level in ladder:
            got = self._caves_at(level)
            self.assertIn('message', got, level)
            self.assertIn('field', got, level)
            if previous is not None:
                self.assertTrue(previous <= got,
                                '%s dropped %s' % (level, previous - got))
            previous = got
        self.assertIn('input', previous, 'full must install everything')

    def test_spends_nothing_from_the_contiguous_table_budget(self):
        """
        The scarce resource in this project is contiguous .rodata/.text tail,
        not padding -- ambience needs 918 bytes of it and the SFX bridges
        compete for the rest. The voice runtime's whole point is that it needs
        NONE: the filename is rebuilt at runtime instead of being looked up.
        """
        with open(self.base, 'rb') as handle:
            before = ff7nx_tables.budget(handle.read())
        VR.apply_to_nso(self.base, self.out)
        with open(self.out, 'rb') as handle:
            after = ff7nx_tables.budget(handle.read())
        self.assertEqual(before, after)

    def test_writes_nothing_but_padding_and_its_own_sites(self):
        VR.apply_to_nso(self.base, self.out)
        before, after = text_of(self.base), text_of(self.out)
        sites = {VR.MESSAGE_PRE_HOOK, VR.ASK_PRE_HOOK, VR.NAME_CHANGE_HOOK,
                 VR.NAME_CHANGE_STATE_GATE, VR.FIELD_HOOK,
                 VR.WORLD_SERVICE_HOOK, VR.INPUT_POST_REFRESH_HOOK,
                 VR.MAPJUMP_HOOK, VR.WORKER_LOOP_HOOK, VR.WORKER_HEAD_HOOK}
        sites |= {va for va, _w, _t, _c in (VR.WORLD_MESSAGE_HOOKS
                                            + VR.WORLD_ASK_HOOKS)}
        trespass = [va for va in range(0, len(before), 4)
                    if before[va:va + 4] != after[va:va + 4]
                    and va not in sites and word(before, va) != 0]
        self.assertEqual(trespass, [],
                         'overwrote live code at %s'
                         % [hex(v) for v in trespass[:6]])

    def test_a_second_install_is_refused(self):
        VR.apply_to_nso(self.base, self.out)
        with self.assertRaises(ValueError):
            VR.apply_to_nso(self.out, os.path.join(self.tmp, 'again'))


class Chaining(unittest.TestCase):
    """The three shared sites, composed with the bridge that owns them."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(DUMP_MAIN):
            raise unittest.SkipTest('dump/exefs/main not available')
        if not os.path.isfile(FA_CONFIG):
            raise unittest.SkipTest('Cosmo Memory ambient config not extracted')
        cls.field_table, cls.battle_table = ambient_tables()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='voice-chain-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def test_ambient_then_voice_composes(self):
        with_ambient = self._path('ambient')
        AM.apply_to_nso(DUMP_MAIN, with_ambient, self.field_table,
                        self.battle_table)
        before = text_of(with_ambient)
        after_path = self._path('voice')
        report = VR.apply_to_nso(with_ambient, after_path)
        after = text_of(after_path)

        # The world stop was claimed by ambient, so the voice pass must have
        # chained rather than displaced it.
        chained = {site: target for site, _who, target in report['chained']}
        self.assertIn(VR.WORLD_SERVICE_HOOK, chained)
        self.assertEqual(chained[VR.WORLD_SERVICE_HOOK],
                         VR._direct_branch_target(
                             VR.WORLD_SERVICE_HOOK,
                             word(before, VR.WORLD_SERVICE_HOOK)))

        # ...and that target must actually be branched to from the new cave.
        targets = set()
        for va in range(0, len(before), 4):
            if before[va:va + 4] == after[va:va + 4]:
                continue
            target = VR._direct_branch_target(va, word(after, va))
            if target is not None:
                targets.add(target)
        self.assertIn(chained[VR.WORLD_SERVICE_HOOK], targets,
                      'the world service cave never jumps to the bridge it '
                      'claims to have chained behind')

        # Ambient's other sites are untouched.
        for va in (AM.FIELD_HOOK, AM.MENU_HOOK):
            self.assertEqual(word(before, va), word(after, va),
                             'the voice pass disturbed +0x%X' % va)

    def test_voice_then_ambient_is_refused(self):
        """
        The pass order is a real dependency, and this is what enforces it.
        `ff7nx_ambient` installs with `expect_word`, so if the voice runtime
        got there first the build stops with the site named -- which is very
        much better than composing something that half works.
        """
        with_voice = self._path('voice-first')
        VR.apply_to_nso(DUMP_MAIN, with_voice)
        with self.assertRaises(ValueError) as caught:
            AM.apply_to_nso(with_voice, self._path('ambient-second'),
                            self.field_table, self.battle_table)
        self.assertIn('%X' % AM.WORLD_HOOK, str(caught.exception).upper())

    def test_the_music_thread_title_is_shared_not_fought_over(self):
        """
        Both features construct the same NativeOggPlayer, and both shorten the
        same diagnostic Horizon thread title because an over-long one aborts
        in SetThreadNamePointer. Whichever installs first has fixed it for the
        other, so the second must accept the literal it finds -- but only
        because it is byte-for-byte what it would have written itself.
        """
        with_ambient = self._path('ambient')
        AM.apply_to_nso(DUMP_MAIN, with_ambient, self.field_table,
                        self.battle_table)
        # No exception is the assertion.
        VR.apply_to_nso(with_ambient, self._path('voice'))

        # And a title neither of them wrote is still refused.
        with open(DUMP_MAIN, 'rb') as handle:
            _segs, raw = AC.segments(handle.read())
        raw = list(raw)
        rodata = bytearray(raw[1])
        offset = VR.MUSIC_THREAD_NAME_FORMAT - VR.RODATA_START
        width = len(VR.MUSIC_THREAD_NAME_FORMAT_STOCK)
        rodata[offset:offset + width] = b'somebody else\0'.ljust(width, b'\0')
        raw[1] = bytes(rodata)
        with self.assertRaises(ValueError):
            VR._replace_music_thread_name(raw, 'music stream')


class FullStack(unittest.TestCase):
    """Against the module this project really ships, caves and all."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(BUILT_MAIN):
            raise unittest.SkipTest('no built sdout module to compose against')

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='voice-full-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_installs_over_the_whole_shipping_preset(self):
        """
        60 FPS, widescreen, analog-360 and all five Cosmo bridges are already
        in this module, and eighteen caves are already out of the padding
        pool. This is the run that matters.
        """
        out = os.path.join(self.tmp, 'main')
        before = text_of(BUILT_MAIN)
        report = VR.apply_to_nso(BUILT_MAIN, out)
        after = text_of(out)

        chained = {site for site, _who, _t in report['chained']}
        self.assertIn(VR.FIELD_HOOK, chained,
                      'analog-360 owns 0x947CF0 in the shipping preset; the '
                      'voice pass should have reported chaining behind it')
        self.assertIn(VR.WORLD_SERVICE_HOOK, chained)

        sites = {VR.MESSAGE_PRE_HOOK, VR.ASK_PRE_HOOK, VR.NAME_CHANGE_HOOK,
                 VR.NAME_CHANGE_STATE_GATE, VR.FIELD_HOOK,
                 VR.WORLD_SERVICE_HOOK, VR.INPUT_POST_REFRESH_HOOK,
                 VR.MAPJUMP_HOOK, VR.WORKER_LOOP_HOOK, VR.WORKER_HEAD_HOOK}
        sites |= {va for va, _w, _t, _c in (VR.WORLD_MESSAGE_HOOKS
                                            + VR.WORLD_ASK_HOOKS)}
        trespass = [va for va in range(0, len(before), 4)
                    if before[va:va + 4] != after[va:va + 4]
                    and va not in sites and word(before, va) != 0]
        self.assertEqual(trespass, [],
                         'took padding that another cave already holds: %s'
                         % [hex(v) for v in trespass[:6]])

        # Nothing anybody else owns moved.
        for va in (0x947CF4, 0x8FB20, 0xD0C3D4):
            self.assertEqual(word(before, va), word(after, va),
                             '+0x%X moved' % va)


if __name__ == '__main__':
    unittest.main()


class SecondWindowLines(unittest.TestCase):
    """
    A line opened on a window other than the one currently speaking.

    The port has one NativeOggPlayer where FFNx has one per window, so the
    two cannot both be audible. The question is what happens to the second
    one, and the answer used to be "discarded": `drop_foreign_play` cleared
    the pending command, and Wedge's md8_1 line (window 3, behind Barret's
    window 1) never played. Measured on the composed flevel, 342 of 544
    fields have voiced lines on more than one window.

    It is now held instead, and the service's own `completed` path -- which
    already documents "a queued, existing line will be constructed in
    `commands` below" -- picks it up when the first clip ends.
    """

    def _service(self, defer):
        return VR._build_field_clocked_voice_service(
            0, lambda i: 0x800000 + i * 4, 0x3FEF660,
            duck_percent=25, auto_advance=True,
            defer_foreign_play=defer)

    def test_holding_the_request_does_not_clear_the_command(self):
        """
        The whole fix. Clearing `VOICE_PENDING_CMD` is what threw the line
        away; the request is otherwise already complete, so not clearing it
        is all that is needed.
        """
        import a64 as A                                         # noqa: E402
        clear = A.str_(A.WZR, 19, VR.VOICE_PENDING_CMD_OFF)
        self.assertGreater(self._service(False).count(clear),
                           self._service(True).count(clear))

    def test_the_deadline_safety_net_survives_on_the_held_path(self):
        """
        A worker that never reports completion must still time out while a
        request is held -- otherwise one stuck player silences every line
        after it, which is worse than the bug being fixed.
        """
        self.assertGreater(len(self._service(True)),
                           len(self._service(False)))

    def test_battle_still_replaces_rather_than_holding(self):
        """
        Battle passes `allow_foreign_replace=True` and must keep doing so: a
        bark is tied to the action on screen and is worthless late.
        """
        import inspect                                          # noqa: E402
        source = inspect.getsource(VR)
        self.assertIn('allow_foreign_replace=True', source)

    def test_all_three_policies_install_on_the_real_module(self):
        """
        replace (the default) / defer / drop. The hold is retained because
        cargoin wants it -- Wedge's window has 10.ogg and Biggs's
        simultaneous window deliberately has none -- but it is not the
        default, because a held line is one the player reads without hearing.
        """
        src = os.path.join(ROOT, 'dump', 'exefs', 'main')
        if not os.path.isfile(src):
            self.skipTest('no dump/exefs/main')
        # replace, defer, and what `defer_foreign_play` should report:
        # replacing wins, so the hold is only in effect when it does not.
        cases = ((True, True, False), (False, True, True),
                 (False, False, False))
        for replace, defer, want_defer in cases:
            out = tempfile.mktemp(suffix='.nso')
            try:
                report = VR.apply_to_nso(
                    src, out, level='full', message_phase='post',
                    duck_percent=25, allow_foreign_field_replace=replace,
                    defer_foreign_play=defer)
                self.assertEqual(report['allow_foreign_field_replace'],
                                 replace)
                self.assertEqual(report['defer_foreign_play'], want_defer)
                self.assertTrue(report['field_entry'])
            finally:
                if os.path.exists(out):
                    os.unlink(out)


class LinesThatOutlastTheirWindow(unittest.TestCase):
    """
    Echo-S writes lines longer than the window that shows them.

    md8_1 is the measured case: `WINDOW 3 / MESSAGE 3,9 / WAIT 39` puts a
    7.3-second Wedge scream behind a window the script closes after 39 frames
    -- 0.65 s. Publishing STOP on that close cut the clip there, and because
    the service needs a frame or two to preflight, malloc and construct the
    player, in practice it cancelled the request before a sample was audible.

    The same shape is why other lines go quiet and the next one is fine: a
    script-timed window kills its own line, an input-waiting window does not.
    """

    def _message(self, stop_on_close):
        return VR._build_message_command_cave(
            0, lambda i: 0x800000 + i * 4, 0x3FEF660,
            key_mode='folder', stop_on_close=stop_on_close,
            resume_instruction=VR.MESSAGE_ORIG,
            resume_address=VR.MESSAGE_HOOK + 4)

    def test_dismissing_a_box_stops_its_line_by_default(self):
        """
        The close the producer can see is always a player dismissal: it sits
        in the MESSAGE opcode handler, which only re-runs while a BLOCKING
        message is parked on it. A script-timed window is closed by WCLS, a
        different opcode, so this path never sees it -- which is why
        stopping here cannot cut off Wedge's 7.3 s line behind a 39-frame
        window, and why build 427 turning it off was over-broad.
        """
        import a64 as A                                         # noqa: E402
        queue_stop = A.movz(8, VR.VOICE_CMD_STOP)
        self.assertIn(queue_stop, self._message(True))
        self.assertNotIn(queue_stop, self._message(False))

    def test_the_run_on_is_still_reachable(self):
        """One ExeFS-only rebuild has to be able to get it back."""
        src = os.path.join(ROOT, 'dump', 'exefs', 'main')
        if not os.path.isfile(src):
            self.skipTest('no dump/exefs/main')
        out = tempfile.mktemp(suffix='.nso')
        try:
            report = VR.apply_to_nso(src, out, level='full',
                                     message_phase='post', duck_percent=25,
                                     stop_on_close=False)
            self.assertFalse(report['stop_on_close'])
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_both_install_on_the_real_module(self):
        src = os.path.join(ROOT, 'dump', 'exefs', 'main')
        if not os.path.isfile(src):
            self.skipTest('no dump/exefs/main')
        for stop in (False, True):
            out = tempfile.mktemp(suffix='.nso')
            try:
                report = VR.apply_to_nso(
                    src, out, level='full', message_phase='post',
                    duck_percent=25, stop_on_close=stop)
                self.assertEqual(report['stop_on_close'], stop)
                self.assertTrue(report['message_entry'])
            finally:
                if os.path.exists(out):
                    os.unlink(out)


class NonBlockingMessages(unittest.TestCase):
    """
    A MESSAGE whose script does not wait for it.

    md8_1 uses the non-blocking idiom for Wedge's scream:

        WMODE 3,0,0 ; WINDOW 3 ; MESSAGE 3,9 ; WAIT 39 ; WCLS 3

    The script carries straight on, so the opcode handler -- and the producer
    cave inside it -- runs ONCE. `VOICE_LAST_OPCODE` holds the previous
    MESSAGE_STATE, and publishing only on an observed 0 -> nonzero transition
    needs two consecutive frames to see it. There are none, so the line was
    never requested; and the single pass stored a nonzero state that was
    never cleared, because the close was never observed either, leaving that
    window unable to publish anything afterwards.

    The dialog id identifies the line, so a change in it also starts one. It
    rides in the high byte of the halfword that table already holds, so this
    costs no BSS.
    """

    def _message(self, on_dialog_change):
        return VR._build_message_command_cave(
            0, lambda i: 0x800000 + i * 4, 0x3FEF660,
            key_mode='folder', publish_on_dialog_change=on_dialog_change,
            resume_instruction=VR.MESSAGE_ORIG,
            resume_address=VR.MESSAGE_HOOK + 4)

    def test_the_dialog_id_is_compared_against_the_window_s_last(self):
        import a64 as A                                         # noqa: E402
        words = self._message(True)
        # W28 holds the dialog id, W9 the unpacked previous one.
        self.assertIn(A.cmp_reg(9, 28), words)
        self.assertNotIn(A.cmp_reg(9, 28), self._message(False))

    def test_the_entry_is_packed_not_widened(self):
        """No new BSS: the dialog rides in the high byte of the halfword."""
        import a64 as A                                         # noqa: E402
        words = self._message(True)
        self.assertIn(A.lsl(9, 28, 8), words)       # dialog << 8
        self.assertIn(A.lsr(9, 8, 8), words)        # and unpacked back out
        self.assertIn(A.and_mask(8, 8, 8), words)   # state is the low byte

    def test_the_block_is_unchanged_in_size(self):
        src = os.path.join(ROOT, 'dump', 'exefs', 'main')
        if not os.path.isfile(src):
            self.skipTest('no dump/exefs/main')
        sizes = set()
        for flag in (True, False):
            out = tempfile.mktemp(suffix='.nso')
            try:
                report = VR.apply_to_nso(
                    src, out, level='full', message_phase='post',
                    duck_percent=25, publish_on_dialog_change=flag)
                self.assertEqual(report['publish_on_dialog_change'], flag)
                sizes.add(report['bss_bytes'])
            finally:
                if os.path.exists(out):
                    os.unlink(out)
        self.assertEqual(len(sizes), 1, sizes)


MAIN = os.path.join(ROOT, 'dump', 'exefs', 'main') \
    if 'ROOT' in dir() else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'dump', 'exefs', 'main')


class CaveSizeIsNotAllowedToCREEP(unittest.TestCase):
    """
    THE CHECK THAT WOULD HAVE STOPPED BUILD 449, AND THE ONE 450 GOT WRONG.

    449 added 32 words to the SHARED service builder. The world service went
    from 625 to 689 laid-out words, the allocator could not place it on a real
    build, `apply_to_nso` raised, and the ENTIRE dialogue runtime went
    uninstalled -- no voice acting anywhere.

    450's test asserted the LOGICAL body length instead, which for the world
    service is ~314. It would have passed 449 happily. The number that has to
    fit is the LAID-OUT size `apply_to_nso` reports, bridges between scattered
    holes included, so that is what is asserted here.

    And there is no universal ceiling to assert against: `ask` is 828 and works
    fine, while `world_service` failed at 689. Different caves get different
    windows. So the rule is the only one the evidence supports -- **a cave may
    not grow past a size that is known to work** -- and these numbers come from
    builds that ran on hardware.
    """

    KNOWN_GOOD = {
        # The services are back to their pre-456 sizes. One-shot behavior is
        # encoded as a final-sample Ogg loop, so no service or worker grows.
        #
        # BUILD 460 made `defer` the module default as well as the build's, so
        # these are measured with the foreign-window path that has actually
        # been played. `defer` emits the deadline stop on the held-request
        # branch where `replace` emitted an unconditional jump, which is the
        # few words of difference in the two service caves.
        'field_words': 578,
        'world_service_words': 629,
        'battle_service_words': 511,
        'message_words': 322,       # includes the burst guard's own words
        'ask_words': 828,
        'world_ask_words': 482,
        'world_message_words': 272,
        'battle_text_words': 402,
    }
    SLACK = 8       # allocator noise between runs, not licence to grow

    @unittest.skipUnless(os.path.exists(MAIN), 'needs the game dump')
    def test_no_cave_has_grown_past_a_size_known_to_work(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            report = VR.apply_to_nso(MAIN, os.path.join(tmp, 'main'),
                                     level='full', battle_text=True)
        grown = []
        for key, good in sorted(self.KNOWN_GOOD.items()):
            got = report.get(key)
            if got is None:
                continue
            if got > good + self.SLACK:
                grown.append('%s %d -> %d (+%d)'
                             % (key.replace('_words', ''), good, got,
                                got - good))
        self.assertEqual(
            grown, [],
            'a cave grew past a size known to work on hardware. Build 449 did '
            'exactly this and silenced the whole runtime: %s' % '; '.join(grown))

    @unittest.skipUnless(os.path.exists(MAIN), 'needs the game dump')
    def test_the_replay_guard_is_only_in_the_field_service(self):
        """
        Build 449 put a duplicate guard in the SHARED service builder. The
        world service went 625 -> 689 against a 696-word window, the allocator
        could not place it, `apply_to_nso` raised, and the game shipped with no
        voice acting at all. The guard is a field-dialogue fix; ASK, world and
        battle must be byte-for-byte the sizes they have always been.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            report = VR.apply_to_nso(MAIN, os.path.join(tmp, 'main'),
                                     level='full', battle_text=True)
        self.assertLessEqual(report['world_service_words'], 627 + self.SLACK)
        self.assertLessEqual(report['ask_words'], 828 + self.SLACK)
        self.assertLessEqual(report['battle_service_words'], 509 + self.SLACK)
        # and the same builder, asked for the world and battle variants,
        # must emit the same word count with the flag on or off
        for kw in ({'world_mode': True}, {'battle_mode': True}):
            plain = VR._build_field_clocked_voice_service(
                0, lambda i: i * 4, 0x3FEF65C, **kw)
            asked = VR._build_field_clocked_voice_service(
                0, lambda i: i * 4, 0x3FEF65C, replay_guard='always', **kw)
            self.assertEqual(len(plain), len(asked), kw)
