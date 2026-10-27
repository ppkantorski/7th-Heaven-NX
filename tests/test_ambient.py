#!/usr/bin/env python3
"""
The per-location ambient runtime.

THE TWO THINGS THAT MATTER HERE
===============================
1. IT MUST NOT FIGHT analog-360. Upstream hooks ARM64 0x947CF0, which this
   project's 360-degree field movement has owned since BUILD 383 and which is
   ON in the shipping preset. This bridge takes 0x947CF4 instead -- the very
   next instruction -- so analog's cave replays its own displaced word,
   branches back to 0x947CF4, and the two chain. The tests below pin the
   site, pin that it is position-independent, and check both hooks survive in
   one module.

2. W8 AND W9 ARE LIVE ACROSS THAT HOOK. They are loaded at 0x947CF0 and
   consumed at 0x947CF8/0x947CFC, and this cave clobbers both -- the filename
   builder alone uses x6..x9. This is the exact trap that took the field
   footstep bridge five revisions (r1..r5 all reused W8 at a structurally
   identical site; r6, which saves and restores the pair, is the one that
   passed on hardware). So the save/restore is asserted directly rather than
   assumed from reading.

Also covered: the battle half must be OPTIONAL. Cosmo's option gates mean
`FA=1, BA=0` activates the field-only folder, and a build that has no battle
mappings must install the field cave and the two stop caves rather than
failing.

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

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import ambientmod                                              # noqa: E402
import ff7nx_ambient as AM                                     # noqa: E402
import ff7nx_audio_cave as AC                                  # noqa: E402

MD = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
MAIN = os.path.join(ROOT, 'dump', 'exefs', 'main')
FA = os.path.join(ROOT, 'cache', '[Tsunamods]_Cosmo_Memory',
                  'FA', 'Ambient', 'config.toml')
BA = os.path.join(ROOT, 'cache', '[Tsunamods]_Cosmo_Memory',
                  'BA', 'Ambient', 'config.toml')

# analog-360's site and the instruction this bridge displaces instead.
ANALOG_HOOK = 0x947CF0


def decode(words, at=0x400000):
    out = []
    for k, w in enumerate(words):
        for i in MD.disasm(struct.pack('<I', w), at + 4 * k):
            out.append('%s %s' % (i.mnemonic, i.op_str) if i.op_str
                       else i.mnemonic)
    return out


def load(paths):
    texts = []
    for p in paths:
        with open(p, encoding='utf-8', errors='replace') as f:
            texts.append(f.read())
    return ambientmod.merge_configs(texts)


class Tables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(FA):
            raise unittest.SkipTest('Cosmo ambient configs not extracted')

    def test_field_only_is_a_valid_configuration(self):
        field, battle = AM.tables_from_config(load([FA]))
        self.assertIsNotNone(field)
        self.assertIsNone(battle)
        self.assertEqual(field[1], 66)
        self.assertEqual(len(field[0]), (field[2] - field[1] + 1)
                         + 2 * len(field[3]))

    def test_battle_only_is_a_valid_configuration(self):
        if not os.path.isfile(BA):
            self.skipTest('BA config not extracted')
        field, battle = AM.tables_from_config(load([BA]))
        self.assertIsNone(field)
        self.assertIsNotNone(battle)

    def test_a_configuration_with_neither_is_refused(self):
        self.assertRaises(ValueError, AM.tables_from_config, {})

    def test_the_index_is_one_based_so_zero_means_no_loop(self):
        field, _b = AM.tables_from_config(load([FA]))
        blob, lo, hi, oggs = field
        span = hi - lo + 1
        self.assertTrue(any(blob[i] == 0 for i in range(span)),
                        'some fields in the range have no mapping')
        # 1..n are single loops; n+1.. are the rotating groups (BUILD 739)
        for i in range(span):
            self.assertLessEqual(blob[i], len(oggs) + len(field.groups))

    def test_triangle_keys_are_not_collapsed_into_whole_fields(self):
        cfg = {'field_10': {'sequential': ['1500']},
               'field_12_3': {'sequential': ['9999']}}
        field, _b = AM.tables_from_config(cfg)
        self.assertEqual(field[3], [1500])

    def test_sparse_dispatch_recovers_every_exact_mapping(self):
        field, _battle = AM.tables_from_config(load([FA]))
        entries = AM.table_entries(field)
        blob, lo, hi, oggs = field
        span = hi - lo + 1
        for i, choice in enumerate(blob[:span]):
            location = lo + i
            if choice > len(oggs):
                self.assertEqual(entries[location],
                                 field.groups[choice - len(oggs) - 1])
            elif choice:
                self.assertEqual(entries[location], oggs[choice - 1])
            else:
                self.assertNotIn(location, entries)


class Caves(unittest.TestCase):
    def field(self, addr=None):
        addr = addr or (lambda i: 0x400000 + 4 * i)
        return AM.build_field_cave(addr(0), addr, 0x3FEC4A8,
                                   0x126CC00, 0x126CD00, 66, 66)

    def test_the_hook_is_not_analog_360s(self):
        self.assertNotEqual(AM.FIELD_HOOK, ANALOG_HOOK)
        self.assertEqual(AM.FIELD_HOOK, ANALOG_HOOK + 4)

    def test_the_displaced_instruction_is_position_independent(self):
        # A cave executes from a padding hole, so an adrp/adr/branch replayed
        # there would compute a different address than it did at the site.
        self.assertNotEqual(AM.FIELD_ORIG & 0x1F000000, 0x10000000)
        self.assertNotEqual(AM.FIELD_ORIG & 0x7C000000, 0x14000000)

    def test_the_live_pair_is_saved_and_restored(self):
        listing = decode(self.field())
        self.assertIn('str x8, [sp, #%#x]' % AM.SAVE_W8, listing)
        self.assertIn('str x9, [sp, #%#x]' % AM.SAVE_W9, listing)
        self.assertIn('ldr x8, [sp, #%#x]' % AM.SAVE_W8, listing)
        self.assertIn('ldr x9, [sp, #%#x]' % AM.SAVE_W9, listing)

    def test_lookup_restores_the_guest_context_pointer(self):
        # The block-index division writes W25. Its enclosing field caller
        # still needs the full relocated X25 pointer after the hook returns.
        addr = lambda i: 0x400000 + 4 * i
        for words in (self.field(addr), AM.build_battle_cave(
                addr(0), addr, 0x3FEC4A8, 0x126CC00, 0x126CD00, 66, 66)):
            listing = decode(words)
            self.assertIn('udiv w25, w21, w24', listing)
            self.assertIn('str x25, [sp, #0x40]', listing)
            self.assertIn('ldr x25, [sp, #0x40]', listing)
            self.assertLess(listing.index('str x25, [sp, #0x40]'),
                            listing.index('udiv w25, w21, w24'))

    def test_the_stop_caves_do_not_need_the_pair(self):
        # They hook ADRPs at the top of the world and menu loops, where
        # nothing is live in x8/x9 -- so they must not pay for the saves.
        for build in (AM.build_world_stop_cave, AM.build_menu_stop_cave):
            listing = decode(build(0x400000, lambda i: 0x400000 + 4 * i,
                                   0x3FEC4A8))
            self.assertNotIn('str x8, [sp, #%#x]' % AM.SAVE_W8, listing)

    # ------------------------------------------- the mode stop, BUILD 506
    def mode(self, addr=None):
        addr = addr or (lambda i: 0x400000 + 4 * i)
        return AM.build_mode_stop_cave(addr(0), addr, 0x3FEC4A8)

    def test_the_mode_stop_chains_after_daynight_rather_than_fighting_it(self):
        """
        Both hooks live in `set_driver_mode`'s prologue. ff7nx_daynight takes
        the `stp x29, x30` at +0x10F3D04 and rejoins at +0x10F3D08; this takes
        the `mov w19, w0` at +0x10F3D0C. If day/night ever rejoined PAST this
        instruction, the ambient stop would silently never run -- which is the
        same shape as the bug it exists to fix, so it is asserted rather than
        assumed.
        """
        import ff7nx_daynight as DN
        self.assertNotEqual(AM.MODE_HOOK, DN.MODE_HOOK)
        self.assertLessEqual(DN.MODE_RESUME, AM.MODE_HOOK)
        self.assertEqual(AM.MODE_RESUME, AM.MODE_HOOK + 4)

    def test_the_displaced_mode_instruction_is_position_independent(self):
        self.assertNotEqual(AM.MODE_ORIG & 0x1F000000, 0x10000000)
        self.assertNotEqual(AM.MODE_ORIG & 0x7C000000, 0x14000000)

    def test_the_mode_stop_replays_the_move_after_restoring_x19(self):
        """
        The displaced instruction WRITES w19, and the cave saves/restores
        x19. Replaying it before the restore would put the old x19 back on
        top of the mode and `set_driver_mode` would dispatch on garbage.
        """
        listing = decode(self.mode())
        self.assertIn('mov w19, w0', listing)
        self.assertIn('ldp x19, x20, [sp, #0x10]', listing)
        self.assertLess(listing.index('ldp x19, x20, [sp, #0x10]'),
                        listing.index('mov w19, w0'))

    def test_the_mode_stop_keeps_w0_across_the_dispose(self):
        """OGG_DELETE is a call; w0 is the argument AND the mode the
        displaced instruction still needs."""
        listing = decode(self.mode())
        self.assertIn('str w0, [sp, #%#x]' % AM.MODE_SAVE_W0, listing)
        self.assertIn('ldr w0, [sp, #%#x]' % AM.MODE_SAVE_W0, listing)
        self.assertLess(listing.index('str w0, [sp, #%#x]' % AM.MODE_SAVE_W0),
                        listing.index('bl #%#x' % AM.OGG_DELETE))

    def test_field_and_battle_do_not_stop_ambience(self):
        """
        FFNx plays ambience in exactly two modes and stops in every other
        (`ff7_handle_ambient_playback`, ff7/misc.cpp:760). Getting this
        backwards would kill the loop the moment you walked into a field.
        """
        listing = decode(self.mode())
        self.assertIn('cmp w0, #%d' % AM.MODE_DRIVER_FIELD, listing)
        self.assertIn('cmp w0, #%d' % AM.MODE_DRIVER_BATTLE, listing)
        # both compares come before the dispose, and both skip it
        self.assertLess(listing.index('cmp w0, #%d' % AM.MODE_DRIVER_BATTLE),
                        listing.index('bl #%#x' % AM.OGG_DELETE))
        self.assertEqual(AM.MODE_DRIVER_FIELD, 2)
        self.assertEqual(AM.MODE_DRIVER_BATTLE, 3)

    def test_the_mode_stop_EXECUTES_correctly_for_every_mode(self):
        """
        Run the cave. This is the one that would have caught the bug: a
        live player must be disposed exactly once, for every mode except
        the two that play, and w0 must survive to the displaced `mov`.
        """
        import arm64emu as EM
        BSS, PLAYER = 0x3FEC4A8, 0x720000000
        for mode in (AM.MODE_DRIVER_FIELD, AM.MODE_DRIVER_BATTLE,
                     4, 5, 27, 1, 0):
            plays = mode in (AM.MODE_DRIVER_FIELD, AM.MODE_DRIVER_BATTLE)
            with self.subTest(mode=mode, plays=plays):
                mem = EM.Mem()
                mem.setu(BSS + 0, PLAYER, 8)       # a live player
                mem.setu(BSS + 16, 1, 4)           # ... and it is marked live
                disposed = []

                def delete(cpu):
                    disposed.append(cpu.x[0])

                cpu = EM.Cpu(mem, native={AM.OGG_DELETE: delete})
                cpu.x = [0x730000000 + i * 0x100 for i in range(32)]
                cpu.x[0] = mode
                cpu.sp = 0x740001000
                entry = 0x400000
                words = self.mode()
                cpu.run(entry, {entry + 4 * i: w
                                for i, w in enumerate(words)})

                if plays:
                    self.assertEqual(disposed, [], 'mode %d stopped the loop '
                                     'and it is a mode that PLAYS' % mode)
                    self.assertEqual(mem.u(BSS + 0, 8), PLAYER)
                    self.assertEqual(mem.u(BSS + 16, 4), 1)
                else:
                    self.assertEqual(disposed, [PLAYER],
                                     'mode %d did not dispose exactly once'
                                     % mode)
                    self.assertEqual(mem.u(BSS + 0, 8), 0,
                                     'the player pointer must be cleared')
                    self.assertEqual(mem.u(BSS + 16, 4), 0,
                                     'and the live flag with it')
                # the mode reaches the displaced instruction either way
                self.assertEqual(cpu.x[19] & 0xFFFFFFFF, mode)

    def test_the_mode_stop_is_idle_when_nothing_is_playing(self):
        """No live player means no call at all -- a dispose of a null
        pointer is how this would crash on the title screen."""
        import arm64emu as EM
        BSS = 0x3FEC4A8
        mem = EM.Mem()
        mem.setu(BSS + 16, 0, 4)            # not live
        disposed = []
        cpu = EM.Cpu(mem, native={AM.OGG_DELETE: lambda c: disposed.append(1)})
        cpu.x = [0x730000000 + i * 0x100 for i in range(32)]
        cpu.x[0] = 27
        cpu.sp = 0x740001000
        words = self.mode()
        cpu.run(0x400000, {0x400000 + 4 * i: w for i, w in enumerate(words)})
        self.assertEqual(disposed, [])
        self.assertEqual(cpu.x[19] & 0xFFFFFFFF, 27)

    # --------------------------------------- the battle pause, BUILD 507
    def test_pause_and_resume_are_the_players_own_symmetric_pair(self):
        """
        Found by reading the player's state transitions, not guessed. Both
        take only `this`, both lock through [this+0x58], both call one
        decoder method and write a state: 0x3750 writes 2 (the same value
        OGG_PLAY itself writes -- "playing"), 0x37B0 writes 4.
        """
        self.assertEqual(AM.OGG_RESUME, 0x3750)
        self.assertEqual(AM.OGG_PAUSE, 0x37B0)
        self.assertNotEqual(AM.OGG_PAUSE, AM.OGG_RESUME)

    def test_the_pause_flag_fits_the_state_block(self):
        self.assertLessEqual(AM.PAUSE_OFF + 4, AM.SCRATCH_BYTES)
        self.assertGreaterEqual(AM.PAUSE_OFF, 20)   # past mode at +16

    def test_the_battle_pause_runs_before_the_lookup(self):
        """
        It has to happen on every battle frame, including the ones where
        this battle has no ambience mapping and the lookup leaves early.
        """
        listing = decode(AM.build_battle_cave(
            0x400000, lambda i: 0x400000 + 4 * i, 0x3FEC4A8,
            0x126CC00, 0x126CD00, 66, 66))
        self.assertIn('bl #%#x' % AM.OGG_PAUSE, listing)
        self.assertLess(listing.index('bl #%#x' % AM.OGG_PAUSE),
                        listing.index('bl #%#x' % AM.OGG_CTOR))

    def test_only_the_battle_cave_pauses(self):
        """The field has no pause flag to follow, and the stop caves dispose
        outright -- neither should carry the cost."""
        field = decode(self.field())
        self.assertNotIn('bl #%#x' % AM.OGG_PAUSE, field)
        self.assertNotIn('bl #%#x' % AM.OGG_RESUME, field)
        for build in (AM.build_world_stop_cave, AM.build_menu_stop_cave,
                      AM.build_mode_stop_cave):
            listing = decode(build(0x400000, lambda i: 0x400000 + 4 * i,
                                   0x3FEC4A8))
            self.assertNotIn('bl #%#x' % AM.OGG_PAUSE, listing)

    def test_every_stop_clears_the_pause_flag(self):
        """A disposed player is not paused. A 1 left here would make the
        next battle's first frame resume a player that was never paused."""
        for build in (AM.build_world_stop_cave, AM.build_menu_stop_cave,
                      AM.build_mode_stop_cave):
            listing = decode(build(0x400000, lambda i: 0x400000 + 4 * i,
                                   0x3FEC4A8))
            self.assertIn('str wzr, [x19, #%#x]' % AM.PAUSE_OFF, listing)

    def test_the_battle_pause_EXECUTES_as_a_transition(self):
        """
        Run the cave over a battle: unpaused, paused, still paused,
        unpaused. Exactly one pause and one resume, on the edges -- calling
        it every frame would take the player's lock 60 times a second.
        """
        import arm64emu as EM
        BSS, PLAYER, PAUSED = 0x3FEC4A8, 0x720000000, 0x730000
        words = AM.build_battle_cave(0x400000, lambda i: 0x400000 + 4 * i,
                                     BSS, 0x126CC00, 0x126CD00, 66, 66)
        code = {0x400000 + 4 * i: w for i, w in enumerate(words)}
        mem = EM.Mem()
        mem.setu(BSS + 0, PLAYER, 8)
        mem.setu(BSS + 16, AM.BATTLE_MODE, 4)      # already in battle
        calls = []
        native = {
            AM.OGG_PAUSE: lambda c: calls.append(('pause', c.x[0])),
            AM.OGG_RESUME: lambda c: calls.append(('resume', c.x[0])),
        }

        def translate(cpu):
            # guest 0xDC0E6C -> our scratch page; anything else -> a zero word
            cpu.x[0] = PAUSED if (cpu.x[0] & 0xFFFFFFFF) == AM.PAUSED_GUEST \
                else PAUSED + 0x100
        native[AC.GUEST_TRANSLATE] = translate
        for stop in (AM.OGG_CTOR, AM.OGG_PLAY, AM.OGG_VOLUME, AM.OGG_DELETE,
                     AM.MALLOC):
            native.setdefault(stop, lambda c: None)

        for frame, paused in enumerate((0, 0, 1, 1, 1, 0, 0)):
            mem.setu(PAUSED, paused, 1)
            mem.setu(BSS + 8, 66, 4)          # location unchanged
            cpu = EM.Cpu(mem, native=native)
            cpu.x = [0x740000000 + i * 0x100 for i in range(32)]
            cpu.sp = 0x750001000
            cpu.run(0x400000, code)

        self.assertEqual(calls, [('pause', PLAYER), ('resume', PLAYER)],
                         'expected one pause on the edge into pause and one '
                         'resume on the edge out, got %r' % (calls,))

    def test_the_mode_stop_does_not_pay_for_the_live_pair(self):
        listing = decode(self.mode())
        self.assertNotIn('str x8, [sp, #%#x]' % AM.SAVE_W8, listing)

    def test_the_filename_is_owned_by_the_player_not_the_stack(self):
        # NativeOggPlayer's asynchronous worker retains this pointer to name
        # its Horizon thread.  The allocation must therefore include a
        # durable 13-byte stem, and the cave must form x1 from player x20.
        self.assertGreaterEqual(AM.PLAYER_NAME_BYTES, 13)
        self.assertEqual(AM.PLAYER_NAME_OFF, AM.PLAYER_BYTES)
        self.assertGreater(AM.PLAYER_ALLOC_BYTES, AM.PLAYER_BYTES)
        listing = decode(self.field())
        self.assertIn('mov w0, #%#x' % AM.PLAYER_ALLOC_BYTES, listing)
        self.assertIn('add x1, x20, #%#x' % AM.PLAYER_NAME_OFF, listing)

    def test_the_save_slots_cover_the_live_register_pair(self):
        # The filename no longer occupies this frame, but the W8/W9 pair
        # still must be restored before returning to the field instruction.
        self.assertGreaterEqual(AM.FRAME, AM.SAVE_W9 + 8)

    def test_adrp_sites_are_re_encoded_not_replayed(self):
        # An ADRP's page delta is relative to its own PC. Replaying the stock
        # word from a padding hole would name a different page entirely.
        for build, orig in ((AM.build_world_stop_cave, AM.WORLD_ORIG),
                            (AM.build_menu_stop_cave, AM.MENU_ORIG)):
            words = build(0x400000, lambda i: 0x400000 + 4 * i, 0x3FEC4A8)
            self.assertNotIn(orig, words)
            self.assertTrue(decode([words[-2]], 0x400000)[0].startswith('adrp'))

    def test_word_count_does_not_depend_on_layout(self):
        n = len(self.field())
        scattered = self.field(
            lambda i: 0x400000 + (((i * 0x80000) // max(1, n - 1)) & ~3))
        self.assertEqual(len(scattered), n)

    def test_every_word_decodes(self):
        for build in (self.field,):
            words = build()
            self.assertEqual(len(decode(words)), len(words))

    def test_saved_registers_balance(self):
        mnem = [l.split()[0] for l in decode(self.field())]
        self.assertEqual(mnem.count('stp'), mnem.count('ldp'))


class RealPatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(MAIN) or not os.path.isfile(FA):
            raise unittest.SkipTest('module or ambient config unavailable')
        cls.field, cls.battle = AM.tables_from_config(load([FA]))

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ambient-')
        self.dest = os.path.join(self.tmp, 'main')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_field_only_installs_three_caves(self):
        report = AM.apply_to_nso(MAIN, self.dest, self.field, None)
        self.assertIsNotNone(report['field'])
        self.assertIsNone(report['battle'])
        self.assertTrue(report['world_entry'])
        self.assertTrue(report['menu_entry'])
        with open(self.dest, 'rb') as f:
            _segs, raw = AC.segments(f.read())
        for site in (AM.FIELD_HOOK, AM.WORLD_HOOK, AM.MENU_HOOK):
            word, = struct.unpack_from('<I', raw[0], site)
            self.assertEqual(word & 0xFC000000, 0x14000000)
        # the battle site must be left completely alone
        word, = struct.unpack_from('<I', raw[0], AM.BATTLE_HOOK)
        self.assertEqual(word, AM.BATTLE_ORIG)

    def test_it_leaves_analog_360s_instruction_untouched(self):
        AM.apply_to_nso(MAIN, self.dest, self.field, None)
        with open(MAIN, 'rb') as f:
            _s, before = AC.segments(f.read())
        with open(self.dest, 'rb') as f:
            _s2, after = AC.segments(f.read())
        self.assertEqual(struct.unpack_from('<I', before[0], ANALOG_HOOK),
                         struct.unpack_from('<I', after[0], ANALOG_HOOK))

    def test_bss_grows_by_the_state_block_only(self):
        with open(MAIN, 'rb') as f:
            before, = struct.unpack_from('<I', f.read(0x40), 0x3C)
        report = AM.apply_to_nso(MAIN, self.dest, self.field, None)
        with open(self.dest, 'rb') as f:
            after, = struct.unpack_from('<I', f.read(0x40), 0x3C)
        # BUILD 739: plus one 8-byte pointer to the heap state, when the
        # fade runtime is in
        self.assertEqual(after - before, report['bss_bytes'])
        self.assertEqual(report['bss_bytes'],
                         AM.scratch_bytes(self.field, None,
                                          report['runtime'] is not None,
                                          report['scratch']))
        self.assertLessEqual(report['bss_bytes'], AM.SCRATCH_BYTES + 12)

    def test_native_thread_title_is_shortened_without_changing_ogg_paths(self):
        # NativeOggPlayer builds its decoder path separately from this
        # diagnostic thread title.  The stock format plus "ambient/NNNN"
        # overflows Horizon's short title limit and raises User Break.
        AM.apply_to_nso(MAIN, self.dest, self.field, None)
        with open(self.dest, 'rb') as f:
            _segs, raw = AC.segments(f.read())
        off = AM.MUSIC_THREAD_NAME_FORMAT - AM.RODATA_START
        got = raw[1][off:off + len(AM.MUSIC_THREAD_NAME_FORMAT_STOCK)]
        want = AM.MUSIC_THREAD_NAME_SAFE.ljust(
            len(AM.MUSIC_THREAD_NAME_FORMAT_STOCK), b'\0')
        self.assertEqual(got, want)

    def test_field_and_battle_install_together_without_claiming_tail_tables(self):
        if not os.path.isfile(BA):
            self.skipTest('BA config not extracted')
        _f, battle = AM.tables_from_config(load([BA]))
        report = AM.apply_to_nso(MAIN, self.dest, self.field, battle)
        self.assertIsNotNone(report['field'])
        self.assertIsNotNone(report['battle'])
        # 12-byte index chunks live in verified padding; only their compact
        # pointer tables and OGG pools consume contiguous tails.
        self.assertEqual(report['table_bytes'],
                         report['field']['table_bytes']
                         + report['battle']['table_bytes'])
        self.assertLess(report['table_bytes'], len(self.field[0]) + len(battle[0]))
        with open(self.dest, 'rb') as f:
            _segs, raw = AC.segments(f.read())
        for site in (AM.FIELD_HOOK, AM.BATTLE_HOOK, AM.WORLD_HOOK,
                     AM.MENU_HOOK):
            word, = struct.unpack_from('<I', raw[0], site)
            self.assertEqual(word & 0xFC000000, 0x14000000)

    def test_a_second_install_is_refused(self):
        AM.apply_to_nso(MAIN, self.dest, self.field, None)
        self.assertRaises(ValueError, AM.apply_to_nso, self.dest,
                          self.dest + '.2', self.field, None)

    def test_nothing_is_installed_when_there_are_no_tables(self):
        self.assertIsNone(AM.apply_to_nso(MAIN, self.dest, None, None))
        self.assertFalse(os.path.exists(self.dest))


if __name__ == '__main__':
    unittest.main(verbosity=2)
