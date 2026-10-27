#!/usr/bin/env python3
"""
The four Cosmo Memory audio bridges installed onto ONE module, in build order.

WHY THIS TEST EXISTS
====================
Each bridge has its own test. None of those can catch the failures that only
appear when all four are in the same module, and those are the ones with
teeth:

  1. TWO BRIDGES CLAIMING THE SAME CONTIGUOUS DATA. There is no allocator in
     the module itself -- a pass that appends to a segment tail it has not
     measured simply overwrites whatever the previous pass put there, with no
     error. `ff7nx_tables` is the shared measurement; this checks it holds.

  2. THE FIELD BRIDGE READING THE WRONG BSS. It deliberately shares the world
     bridge's nine dwords instead of extending BSS again. The original code
     re-derived that address as `page_align(data_end) + bssSize - 36`, which
     is correct ONLY while the world bridge is the last pass to have grown
     BSS. The sequential bridge grows it by 232 bytes, so in a real build that
     arithmetic lands inside the shuffle's cursors -- silently, with the
     footstep cursors reading and WRITING another feature's state. The fix is
     that the address is passed explicitly; the test below pins the value.

  3. A LATER PASS CLOBBERING AN EARLIER HOOK. Five hook sites across four
     bridges, all in .text, all installed by separate passes.

  4. THE TWO SFX BRIDGES DRIFTING ONTO THE SAME BOUNDARY. The sequential one
     belongs at the loader, the battle one at the player before its cache
     check. Collapsing them would reintroduce the cache alias that retired
     five revisions of the battle route.

The module used is the stock one. A build with the 60 FPS preset applied has
less room, which is what `test_budget_after_the_sixty_fps_preset` documents
using the real measured numbers.

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

import ff7nx_audio_cave as AC                                   # noqa: E402
import a64 as A                                                   # noqa: E402
import ff7nx_fieldsteps as FS                                   # noqa: E402
import ff7nx_sfxbattle as SB                                    # noqa: E402
import ff7nx_sfxshuffle as SH                                   # noqa: E402
import ff7nx_tables as TB                                       # noqa: E402
import ff7nx_worldsteps as WS                                   # noqa: E402

MD = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
MAIN = os.path.join(ROOT, 'dump', 'exefs', 'main')
WM_CONFIG = os.path.join(ROOT, 'cache', '[Tsunamods]_Cosmo_Memory',
                         'WM', 'sfx', 'config.toml')

# The measurement in ff7nx_tables' docstring, taken by running the 60 FPS pass
# with the shipping preset. If the preset's cave budget ever changes, this is
# the number the audio features are spending from and it should change here
# deliberately rather than be discovered on hardware.
SIXTY_FPS_RODATA_FREE = 851
SIXTY_FPS_TEXT_FREE = 984


def shuffle_plan(n=58):
    return {'compact': True,
            'routes': {sid: {'count': (i % 6) + 2,
                             'payload_base': 90_000_000 + i * 200_000,
                             'stride': 16_384 + i}
                       for i, sid in enumerate(range(1, n + 1))}}


def barret_plan():
    return {'routes': ({'name': 'Barret', 'keys': (SB.BARRET_KEY,),
                        'slots': SB.BARRET_SLOTS, 'variants': 6},)}


def bss_size(path):
    with open(path, 'rb') as f:
        return struct.unpack_from('<I', f.read(0x40), 0x3C)[0]


def word_at(path, va):
    with open(path, 'rb') as f:
        _segs, raw = AC.segments(f.read())
    return struct.unpack_from('<I', raw[0], va)[0]


class Compose(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(MAIN):
            raise unittest.SkipTest('exefs/main not available')
        if not os.path.isfile(WM_CONFIG):
            raise unittest.SkipTest('Cosmo Memory WM config not extracted')
        with open(WM_CONFIG, encoding='utf-8', errors='replace') as f:
            cls.groups = WS.routes_from_configs([f.read()])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='audio-compose-')
        self.main = os.path.join(self.tmp, 'main')
        shutil.copy(MAIN, self.main)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _step(self, fn, *args, **kwargs):
        out = os.path.join(self.tmp, 'next')
        report = fn(self.main, out, *args, **kwargs)
        shutil.move(out, self.main)
        return report

    def install_all(self):
        """Install all four in the order `7th_heaven_nx.run_build` uses."""
        r1 = self._step(SH.apply_to_nso, shuffle_plan())
        self.battle = self._step(SB.apply_to_nso, barret_plan())
        r2 = self._step(WS.apply_to_nso,
                        {'groups': self.groups, 'stride': 18_432,
                         'payload_base': 95_000_000, 'variants': 68,
                         'materials': 20})
        r3 = self._step(FS.apply_to_nso, r2['scratch'],
                        terrain={'payload_base': 99_000_000,
                                 'stride': 18_432})
        return r1, r2, r3

    # ------------------------------------------------------------------
    def test_all_four_install_onto_one_module(self):
        r1, r2, r3 = self.install_all()
        # The finished file re-parses and every segment hash verifies --
        # AC.segments raises if not.
        with open(self.main, 'rb') as f:
            _segs, raw = AC.segments(f.read())
        self.assertTrue(raw)
        self.assertEqual(r1['routes'], 58)
        self.assertEqual(r2['variants'], 68)
        self.assertTrue(r3['shared_world_scratch'])

    def test_every_hook_site_survives_the_later_passes(self):
        self.install_all()
        sites = (AC.LOAD_HOOK, AC.PLAYER_HOOK, WS.WORLD_MOVE_HOOK,
                 FS.FIELD_PLAYER_RESULT_HOOK, FS.FIELD_UPDATE_RETURN_HOOK)
        words = [word_at(self.main, site) for site in sites]
        for site, word in zip(sites, words):
            self.assertEqual(word & 0xFC000000, 0x14000000,
                             'hook +0x%X is %08X, not a branch' % (site, word))
        self.assertEqual(len(set(words)), len(sites),
                         'two hooks branch to the same cave')

    def test_bss_growth_is_exactly_what_the_four_asked_for(self):
        before = bss_size(MAIN)
        r1, r2, r3 = self.install_all()
        after = bss_size(self.main)
        self.assertEqual(after - before,
                         r1['bss_bytes'] + self.battle['bss_bytes']
                         + r2['bss_bytes'] + r3['bss_bytes'])
        # The field bridge must add nothing: a second BSS extension was the
        # one operation common to the three failing field overlays.
        self.assertEqual(r3['bss_bytes'], 0)

    def test_field_bridge_reads_the_world_bridge_s_own_scratch(self):
        """The field bridge takes the world bridge's address, not the clock."""
        _r1, r2, r3 = self.install_all()
        self.assertEqual(r3['scratch'], r2['scratch'])

    def test_the_ordering_hazard_the_explicit_address_removes(self):
        """
        Install WORLD, then the sequential bridge, then FIELD.

        This is the order the naive derivation cannot survive, and the reason
        the address is passed explicitly rather than recomputed. In the order
        `run_build` currently uses (shuffle first) the two happen to agree,
        which is exactly why this has to be tested in the other order -- the
        correctness must not depend on which pass ran when.
        """
        r2 = self._step(WS.apply_to_nso,
                        {'groups': self.groups, 'stride': 18_432,
                         'payload_base': 95_000_000, 'variants': 68,
                         'materials': 20})
        r1 = self._step(SH.apply_to_nso, shuffle_plan())
        r3 = self._step(FS.apply_to_nso, r2['scratch'],
                        terrain={'payload_base': 99_000_000,
                                 'stride': 18_432})

        # What the bridge actually uses: the world bridge's own address.
        self.assertEqual(r3['scratch'], r2['scratch'])

        # What re-deriving it from the finished module would have given --
        # 232 bytes too high, landing inside the sequential bridge's per-route
        # cursors, where the footstep code would both read and WRITE another
        # feature's state.
        with open(self.main, 'rb') as f:
            blob = f.read()
        segs, _raw = AC.segments(blob)
        derived = AC.scratch_base(blob, segs) - WS.SCRATCH_BYTES
        self.assertEqual(derived - r2['scratch'], r1['bss_bytes'])
        self.assertNotEqual(r3['scratch'], derived)

    def test_field_bridge_refuses_without_the_world_bridge(self):
        # It shares the world bridge's state and its physical archive row.
        self.assertRaises(ValueError, FS.apply_to_nso, MAIN,
                          os.path.join(self.tmp, 'x'), 0x3FEF610,
                          terrain={'payload_base': 1, 'stride': 16})

    def test_field_bridge_refuses_a_scratch_it_was_not_given(self):
        self._step(SH.apply_to_nso, shuffle_plan())
        r2 = self._step(WS.apply_to_nso,
                        {'groups': self.groups, 'stride': 18_432,
                         'payload_base': 95_000_000, 'variants': 68,
                         'materials': 20})
        self.assertTrue(r2['scratch'])
        self.assertRaises(ValueError, FS.apply_to_nso, self.main,
                          os.path.join(self.tmp, 'x'), None)

    def test_contiguous_data_is_shared_not_overwritten(self):
        with open(MAIN, 'rb') as f:
            ro_before, text_before = TB.budget(f.read())
        r1, _r2, _r3 = self.install_all()
        with open(self.main, 'rb') as f:
            ro_after, text_after = TB.budget(f.read())
        spent = (ro_before + text_before) - (ro_after + text_after)
        # Only the sequential bridge places a table; the two footstep bridges
        # keep their routes out of the module entirely (the world map's in the
        # cave's own compare chain, the field's in the flevel CMFS trailers).
        self.assertGreaterEqual(spent, r1['table_bytes'])
        self.assertLess(spent, r1['table_bytes'] + 8, 'unexpected extra table')

    def test_the_two_sfx_bridges_hook_different_boundaries(self):
        """
        The sequential bridge hooks the LOADER; the battle bridge hooks the
        PLAYER, before its cache check.

        That separation is not stylistic. A battle route resolved at the
        loader leaves the channel labelled with the shared logical id, so the
        next request for it replays the wrong actor's buffer without entering
        the loader at all -- the failure that retired five revisions.
        """
        self.install_all()
        self.assertNotEqual(AC.LOAD_HOOK, AC.PLAYER_HOOK)
        for site in (AC.LOAD_HOOK, AC.PLAYER_HOOK):
            self.assertEqual(word_at(self.main, site) & 0xFC000000, 0x14000000)

    def test_battle_routes_cost_no_contiguous_table_space(self):
        before = TB.budget(open(MAIN, 'rb').read())
        self._step(SB.apply_to_nso, barret_plan())
        after = TB.budget(open(self.main, 'rb').read())
        self.assertEqual(before, after)

    def test_reserved_archive_rows_do_not_overlap(self):
        # 26 rows are empty in vanilla. The footstep bridge takes one and the
        # Barret route takes six; they must be disjoint.
        self.assertNotIn(AC.FOOTSTEP_SLOT, SB.BARRET_SLOTS)
        self.assertEqual(len(set(SB.BARRET_SLOTS)), len(SB.BARRET_SLOTS))

    def test_budget_after_the_sixty_fps_preset(self):
        # Documents the number every audio feature is really spending from.
        # The stock module has more; a shipped build does not.
        self.assertEqual(SIXTY_FPS_RODATA_FREE + SIXTY_FPS_TEXT_FREE, 1835)
        space = TB.TableSpace(*AC.segments(open(MAIN, 'rb').read()))
        self.assertGreater(space.total_free,
                           SIXTY_FPS_RODATA_FREE + SIXTY_FPS_TEXT_FREE)

    def test_a_second_install_of_the_same_bridge_is_refused(self):
        self.install_all()
        self.assertRaises(ValueError, SH.apply_to_nso, self.main,
                          os.path.join(self.tmp, 'x'), shuffle_plan())
        self.assertRaises(ValueError, WS.apply_to_nso, self.main,
                          os.path.join(self.tmp, 'x'),
                          {'groups': self.groups, 'stride': 18_432,
                           'payload_base': 1, 'variants': 68,
                           'materials': 20})


class FootstepCadence(unittest.TestCase):
    """
    The cadence must follow the frame limiter, not assume 30 Hz.

    THE BUG THIS PINS. Both footstep bridges pace themselves by counting loop
    iterations, and FFNx's rule is in seconds. The bridges were written
    against a build with no 60 FPS group, where the field and world loops run
    at 30 Hz. The 60 FPS group rewrites both limiter divisors to 60, and
    `limiter_fps` raises them further -- the shipping setting is 66.

    At 66 Hz the original 15-tick walk cooldown is 0.227 s, which is FASTER
    than the 0.30 s FFNx uses for running. On hardware that is exactly what it
    sounded like: a walk paced like a sprint, out of step with the animation.
    """

    def test_stock_rate_reproduces_the_original_constants(self):
        self.assertEqual(FS.cooldowns(30.0),
                         (FS.WALK_COOLDOWN, FS.RUN_COOLDOWN))
        self.assertEqual(WS.cooldowns(30.0),
                         (WS.HUMAN_COOLDOWN, WS.MOUNT_COOLDOWN))

    def test_the_cadence_is_the_right_number_of_SECONDS_at_every_rate(self):
        for hz in (30.0, 60.0, 66.0):
            walk, run = FS.cooldowns(hz)
            self.assertAlmostEqual(walk / hz, FS.WALK_SECONDS, delta=0.02)
            self.assertAlmostEqual(run / hz, FS.RUN_SECONDS, delta=0.02)
            human, mount = WS.cooldowns(hz)
            self.assertAlmostEqual(human / hz, WS.HUMAN_SECONDS, delta=0.02)
            self.assertAlmostEqual(mount / hz, WS.MOUNT_SECONDS, delta=0.02)

    def test_the_shipping_setting_is_not_faster_than_running(self):
        # The regression, stated as the symptom: walking must never be paced
        # faster than FFNx's own running cadence.
        walk, run = FS.cooldowns(66.0)
        self.assertGreater(walk / 66.0, run / 66.0)
        self.assertGreaterEqual(walk / 66.0, FS.RUN_SECONDS)
        # and the old constant really was wrong at this rate
        self.assertLess(FS.WALK_COOLDOWN / 66.0, FS.RUN_SECONDS)

    def test_a_bad_rate_cannot_produce_a_footstep_every_frame(self):
        for bad in (0, -1, None):
            self.assertEqual(FS.cooldowns(bad), FS.cooldowns(30.0))
            self.assertEqual(WS.cooldowns(bad), WS.cooldowns(30.0))

    def test_word_count_does_not_move_with_the_rate(self):
        # emit_laid_out builds a cave twice and compares lengths, so a rate
        # that changed the instruction COUNT would be a silent corruption.
        import os
        if not os.path.isfile(WM_CONFIG):
            self.skipTest('Cosmo Memory WM config not extracted')
        with open(WM_CONFIG, encoding='utf-8', errors='replace') as f:
            groups = WS.routes_from_configs([f.read()])
        addr = lambda i: 0x400000 + 4 * i
        for lo, hi in ((30.0, 66.0),):
            self.assertEqual(
                len(WS.build_cave(0x400000, addr, 0x3FEC4A8, 9, 1, groups,
                                  tick_hz=lo)),
                len(WS.build_cave(0x400000, addr, 0x3FEC4A8, 9, 1, groups,
                                  tick_hz=hi)))
            self.assertEqual(
                len(FS.build_play_cave(0x400000, addr, 0x3FEC4A8, tick_hz=lo)),
                len(FS.build_play_cave(0x400000, addr, 0x3FEC4A8, tick_hz=hi)))

    def test_field_terrain_selection_cannot_clobber_the_guest_frame(self):
        """
        The field bridge inserts a translated SFX call where the original
        field loop made none.  CMFS selection uses x21..x24 as scratch, so
        holding the live guest EAX/ECX values in those registers restores
        selector scratch over the translated x86 context on the first step.

        Keep the complete context in a dedicated native frame instead.  This
        is deliberately checked on the terrain path: the plain stock-159
        path does not exercise the clobbering selector.
        """
        addr = lambda i: 0x400000 + 4 * i
        words = FS.build_play_cave(
            0x400000, addr, 0x3FEC4A8,
            terrain={'payload_base': 99_000_000, 'stride': 18_432})

        self.assertEqual(FS.PLAY_FRAME, 0xC0)
        self.assertIn(A.stp64_pre(19, 20, A.SP, -FS.PLAY_FRAME), words)
        self.assertIn(A.ldp64_post(19, 20, A.SP, FS.PLAY_FRAME), words)

        saved = ((0, FS.SAVE_EAX), (4, FS.SAVE_ECX),
                 (8, FS.SAVE_EDX), (0xC, FS.SAVE_EBX),
                 (0x10, FS.SAVE_ESP), (0x14, FS.SAVE_EBP),
                 (0x18, FS.SAVE_ESI), (0x1C, FS.SAVE_EDI))
        for guest_off, save_off in saved:
            # Snapshot into the native frame and restore from it, rather
            # than from selector-clobbered x21..x24.
            self.assertIn(A.str_(21, A.SP, save_off), words)
            self.assertIn(A.ldr(21, A.SP, save_off), words)
            self.assertIn(A.str_(21, 25, guest_off), words)

        for save_off, guest_off in ((FS.SAVE_COND0, 0x20),
                                    (FS.SAVE_COND1, 0x28)):
            self.assertIn(A.str64(21, A.SP, save_off), words)
            self.assertIn(A.ldr64(21, A.SP, save_off), words)
            self.assertIn(A.str64(21, 25, guest_off), words)


class WorldFootstepCollision(unittest.TestCase):
    """The world bridge must use collision-resolved, not requested, motion."""

    def test_position_history_does_not_overlap_the_field_shared_state(self):
        # Field footsteps intentionally own cursor zero as a pending bit and
        # word eight as their cooldown.  World collision history starts only
        # after those nine words, so the two mutually-exclusive modes cannot
        # reinterpret one another's state.
        self.assertEqual(WS.CADENCE_OFF, FS.COOLDOWN_OFF)
        self.assertEqual(WS.GROUP_CURSOR_BYTES, FS.WORLD_SCRATCH_BYTES - 4)
        self.assertEqual(WS.LAST_POS_X_OFF, FS.WORLD_SCRATCH_BYTES)
        self.assertEqual(WS.LAST_POS_Z_OFF, WS.LAST_POS_X_OFF + 4)
        self.assertEqual(WS.POS_VALID_OFF, WS.LAST_POS_Z_OFF + 4)
        self.assertEqual(WS.SCRATCH_BYTES, WS.POS_VALID_OFF + 4)

    def test_cave_reads_the_real_player_position_before_spending_cadence(self):
        # The hook's x86 deltas precede collision.  A cave that did not form
        # E04918 here would still play while pressing into a wall.
        if not os.path.isfile(WM_CONFIG):
            self.skipTest('Cosmo Memory WM config not extracted')
        with open(WM_CONFIG, encoding='utf-8', errors='replace') as f:
            groups = WS.routes_from_configs([f.read()])
        addr = lambda i: 0x400000 + 4 * i
        words = WS.build_cave(0x400000, addr, 0x3FEC4A8, 9, 1, groups)
        self.assertIn(A.movz(0, WS.WORLD_PLAYER_POS_GUEST & 0xFFFF), words)
        self.assertIn(A.movk_hi(0, WS.WORLD_PLAYER_POS_GUEST >> 16), words)


if __name__ == '__main__':
    unittest.main(verbosity=2)
