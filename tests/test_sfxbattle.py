#!/usr/bin/env python3
"""
The per-actor battle SFX routes.

WHAT THIS IS GUARDING, AND WHY IT IS NOT THE OBVIOUS THING
==========================================================
The interesting property of this bridge is WHERE it hooks, not what it plays.

Five revisions of a loader-time version were built and run on hardware and all
of them failed the same way: the native player compares and records the
LOGICAL sound id before it ever calls `sfx_load`, so a physical slot chosen in
the loader leaves the channel labelled with the shared id, and the next
request for that id -- a machine-gun enemy after Barret -- replays Barret's
buffer without entering the loader at all.

So the tests below care that:

  * the hook is at the PLAYER boundary, before the cache check, and not at
    the loader site the sequential bridge uses;
  * all THREE displaced instructions are replayed, and the cave resumes after
    them rather than re-running the translator call;
  * the chosen id is written back to the caller's own argument, which is what
    makes one coherent id reach the cache, loader and channel alike;
  * the reserved physical rows are re-checked as empty IN THE ARCHIVE BEING
    BUILT, not assumed from vanilla -- the footstep bridge already consumes
    one of the 26 empty rows;
  * the routes this build knowingly does NOT install are reported rather than
    silently dropped, with the enemy family flagged for the reason the mod
    author gave: the formation lookup resolved the wrong enemy.

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
import tempfile
import unittest

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import a64 as A                                                 # noqa: E402
import audio_dat                                                # noqa: E402
import ff7nx_audio_cave as AC                                   # noqa: E402
import ff7nx_sfxbattle as SB                                    # noqa: E402
import sfxmod                                                   # noqa: E402

MD = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
MAIN = os.path.join(ROOT, 'dump', 'exefs', 'main')
COSMO = os.path.join(ROOT, 'cache', '[Tsunamods]_Cosmo_Memory',
                     'Base', 'sfx', 'config.toml')

BARRET_PLAN = {'routes': ({'name': 'Barret', 'keys': (SB.BARRET_KEY,),
                           'slots': SB.BARRET_SLOTS, 'variants': 6},)}


def decode(words, at=0x400000):
    out = []
    for k, w in enumerate(words):
        for i in MD.disasm(struct.pack('<I', w), at + 4 * k):
            out.append('%s %s' % (i.mnemonic, i.op_str) if i.op_str
                       else i.mnemonic)
    return out


class Cave(unittest.TestCase):
    def build(self, addr=None):
        addr = addr or (lambda i: 0x400000 + 4 * i)
        return SB.build_legacy_cave(addr(0), addr, 0x3FEC4A8,
                                    BARRET_PLAN['routes'])

    def test_word_count_does_not_depend_on_the_layout(self):
        n = len(self.build())
        scattered = self.build(
            lambda i: 0x400000 + (((i * 0x80000) // max(1, n - 1)) & ~3))
        self.assertEqual(len(scattered), n)

    def test_every_word_decodes(self):
        words = self.build()
        self.assertEqual(len(decode(words)), len(words))

    def test_it_replays_all_three_displaced_instructions(self):
        # add w0, w8, #0xc / bl translator / ldr w8, [x0]. Replaying only the
        # first would leave the requested id unresolved.
        words = self.build()
        self.assertEqual(words[3], AC.PLAYER_ORIG)
        listing = decode(words)
        self.assertTrue(any(l.startswith('bl ') for l in listing))

    def test_it_resumes_after_the_displaced_sequence(self):
        words = self.build()
        last = decode([words[-1]], 0x400000 + 4 * (len(words) - 1))[0]
        self.assertTrue(last.startswith('b '))
        self.assertEqual(int(last.split('#')[1], 16), AC.PLAYER_RESUME)
        self.assertEqual(AC.PLAYER_RESUME, AC.PLAYER_HOOK + 12)

    def test_saved_registers_balance(self):
        mnem = [l.split()[0] for l in decode(self.build())]
        self.assertEqual(mnem.count('stp'), mnem.count('ldp'))

    def test_the_actor_stride_really_is_0x68(self):
        class Sink:
            def __init__(self):
                self.words = []

            def emit(self, w):
                self.words.append(w)
        sink = Sink()
        SB.actor_stride_0x68(sink, 8, 24)
        listing = decode(sink.words)
        shifts = [int(l.split('#')[1]) for l in listing if l.startswith('lsl')]
        self.assertEqual(shifts, [7, 4, 3])
        self.assertEqual((1 << 7) - (1 << 4) - (1 << 3), 0x68)


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(COSMO):
            raise unittest.SkipTest('Cosmo Memory config not extracted')
        with open(COSMO, encoding='utf-8', errors='replace') as f:
            cls.overrides = sfxmod.parse_battle_overrides(f.read())

    def test_every_live_character_key_is_enabled_and_no_enemy_key(self):
        # BUILD 740: Cosmo's character routes; BUILD 742: minus Aerith's two,
        # which can never fire (342/346 are Cid's spear sounds), plus the
        # two Omnislash-only routes. BUILD 744: every enemy key, with
        # Cosmo's 0080_348 typo moved to 007F_348.
        chars = {k for k in self.overrides if k[0] == 'char'}
        enemies = {k for k in self.overrides if k[0] == 'enemy'}
        dead = {('char', 3, 342), ('char', 3, 346)}
        self.assertEqual(len(chars), 7)
        self.assertEqual(len(enemies), 9)
        fixed = (enemies - {('enemy', 0x80, 348)}) | {('enemy', 0x7F, 348)}
        self.assertEqual(SB.SUPPORTED_KEYS,
                         (chars - dead) | {SB.OMNI_HIT, SB.OMNI_FINAL}
                         | fixed)
        self.assertEqual(set(SB.enemy_overrides(self.overrides)), fixed)
        self.assertEqual(len(self.overrides[SB.BARRET_KEY]),
                         len(SB.BARRET_SLOTS))

    def test_echo_s_enemy_families_fit_too(self):
        # BUILD 744: Echo-S loads after Cosmo and its enemy keys win -- Reno
        # and Rufus have EIGHT variants there.
        path = os.path.join(os.path.dirname(HERE), 'cache', 'Echo-s',
                            'Sound', 'sfx', 'config.toml')
        if not os.path.exists(path):
            self.skipTest('Echo-S is not unpacked here')
        with open(path, encoding='utf-8', errors='replace') as f:
            ov = sfxmod.parse_battle_overrides(f.read())
        found = SB.enemy_overrides(ov)
        sizes = {sid: len(found[('enemy', ids[0], sid)])
                 for _n, sid, ids in SB.ENEMY_LAYOUT}
        self.assertEqual(sizes, {35: 3, 348: 8, 274: 8})
        self.assertTrue(all(n <= SB.MAX_VARIANTS for n in sizes.values()))

    def test_every_enemy_family_fits_a_three_bit_cursor(self):
        for _name, sid, ids in SB.ENEMY_LAYOUT:
            for e in ids:
                v = SB.enemy_overrides(self.overrides).get(('enemy', e, sid))
                self.assertTrue(v, (e, sid))
                self.assertLessEqual(len(v), SB.MAX_VARIANTS)

    def test_aeriths_dead_keys_are_reported_not_dropped(self):
        pend = dict(SB.pending(self.overrides))
        for key in (('char', 3, 342), ('char', 3, 346)):
            self.assertIn('Cid', pend[key])

    def test_every_other_override_is_reported_with_a_reason(self):
        pend = dict(SB.pending(self.overrides))
        # Aerith's two, and Cosmo's 0080_348 (installed as 007F_348)
        self.assertEqual(set(pend), {('char', 3, 342), ('char', 3, 346),
                                     ('enemy', 0x80, 348)})
        self.assertTrue(all(why for why in pend.values()))
        self.assertIn('007F', pend[('enemy', 0x80, 348)])

    def test_the_reserved_rows_are_inside_the_fixed_archive(self):
        for slot in SB.RESERVED_ROWS:
            self.assertTrue(731 <= slot <= audio_dat.NUM_SLOTS)
        # and must not collide with the footstep bridge's row, or each other
        self.assertNotIn(AC.FOOTSTEP_SLOT, SB.RESERVED_ROWS)
        self.assertEqual(len(set(SB.RESERVED_ROWS)), len(SB.RESERVED_ROWS))
        self.assertEqual(SB.ROUTE_LAYOUT[0][2], SB.BARRET_SLOTS)

    def test_prepare_refuses_a_row_another_feature_already_took(self):
        entries = [audio_dat.Entry(b'', b'', loop=0, count=0)
                   for _ in range(audio_dat.NUM_SLOTS)]
        # the logical template must look usable, the reserved row must not
        entries[16 - 1] = audio_dat.Entry(b'\0' * audio_dat.FMT_FULL,
                                          b'\0' * 64, loop=0, count=1)
        entries[SB.BARRET_SLOTS[0] - 1] = audio_dat.Entry(
            b'\0' * audio_dat.FMT_FULL, b'\0' * 64, loop=0, count=1)
        with tempfile.TemporaryDirectory() as tmp:
            # BUILD 740: the route is skipped with its reason, not installed
            plan = SB.prepare(entries, {SB.BARRET_KEY: [3100, 3101]}, {}, tmp)
        self.assertEqual(plan['routes'], ())
        self.assertEqual(len(plan['skipped']), 1)
        self.assertIn('no longer empty', plan['skipped'][0][2])

    def omni_entries(self):
        e = [audio_dat.Entry(b'', b'', loop=0, count=0)
             for _ in range(audio_dat.NUM_SLOTS)]
        return e

    def test_omnislash_needs_the_mod_to_replace_cloud_s_critical(self):
        stock = self.omni_entries()
        stock[26] = audio_dat.Entry(b'\1' * audio_dat.FMT_FULL, b'\2' * 64,
                                    loop=0, count=1)
        with tempfile.TemporaryDirectory() as tmp:
            # the mod does not map 27: nothing to restore, no Omnislash routes
            plan = SB.prepare(self.omni_entries(), {('char', 9, 99): [1, 2]},
                              {}, tmp, config={28: [5, 6]},
                              stock_entries=stock)
            self.assertIsNone(plan)
            # it does: the hit row is the vanilla 27, byte for byte
            entries = self.omni_entries()
            plan = SB.prepare(entries, {('char', 9, 99): [1, 2]}, {}, tmp,
                              config={27: [3010, 3011]}, stock_entries=stock)
        names = [r['name'] for r in plan['routes']]
        self.assertEqual(names, ['Omnislash hits'])
        self.assertEqual(bytes(entries[SB.OMNI_HIT_ROW - 1].data),
                         bytes(stock[26].data))
        # the final blow has no voiced/NV pair here and says so
        self.assertIn('Omnislash final blow', [s[0] for s in plan['skipped']])

    def test_omnislash_can_be_switched_off(self):
        os.environ[SB.OMNI_ENV] = '0'
        try:
            with tempfile.TemporaryDirectory() as tmp:
                plan = SB.prepare(self.omni_entries(),
                                  {('char', 9, 99): [1, 2]}, {}, tmp,
                                  config={27: [3010, 3011]},
                                  stock_entries=self.omni_entries())
        finally:
            os.environ.pop(SB.OMNI_ENV, None)
        self.assertIsNone(plan)

    def test_prepare_returns_none_when_the_mod_names_no_supported_key(self):
        entries = [audio_dat.Entry(b'', b'', loop=0, count=0)
                   for _ in range(audio_dat.NUM_SLOTS)]
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(SB.prepare(entries, {('char', 9, 99): [1, 2]},
                                         {}, tmp))


class RealPatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(MAIN):
            raise unittest.SkipTest('exefs/main not available')

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sfxbattle-')
        self.dest = os.path.join(self.tmp, 'main')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_it_hooks_the_player_not_the_loader(self):
        report = SB.apply_to_nso(MAIN, self.dest, BARRET_PLAN)
        with open(self.dest, 'rb') as f:
            _segs, raw = AC.segments(f.read())
        # the player site is now a branch to our cave ...
        self.assertEqual(struct.unpack_from('<I', raw[0], AC.PLAYER_HOOK)[0],
                         A.b(AC.PLAYER_HOOK, report['cave_entry']))
        # ... and the LOADER site is untouched, which is the whole point:
        # this bridge must not be where the sequential one lives.
        self.assertEqual(struct.unpack_from('<I', raw[0], AC.LOAD_HOOK)[0],
                         AC.LOAD_ORIG)

    def test_it_costs_no_contiguous_table_space(self):
        report = SB.apply_to_nso(MAIN, self.dest, BARRET_PLAN)
        self.assertEqual(report['table_bytes'], 0)

    def test_bss_grows_by_the_one_cursor_word_only(self):
        # BUILD 740: seven 4-bit cursors in the 4 bytes BUILD 739 had --
        # Echo-S voice's page has 16 bytes of slack.
        with open(MAIN, 'rb') as f:
            before, = struct.unpack_from('<I', f.read(0x40), 0x3C)
        report = SB.apply_to_nso(MAIN, self.dest, BARRET_PLAN)
        with open(self.dest, 'rb') as f:
            after, = struct.unpack_from('<I', f.read(0x40), 0x3C)
        self.assertEqual(after - before, 4)
        self.assertEqual(report['bss_bytes'], 4)

    def test_a_second_install_is_refused(self):
        SB.apply_to_nso(MAIN, self.dest, BARRET_PLAN)
        self.assertRaises(ValueError, SB.apply_to_nso, self.dest,
                          self.dest + '.2', BARRET_PLAN)

    def full_plan(self):
        sys.path.insert(0, HERE)
        import test_sfxbattle_routes as R
        return {'routes': R.ROUTES}

    def test_build_740_routes_go_in_their_dead_space_part(self):
        import ff7nx_deadspace as DS
        report = SB.apply_to_nso(MAIN, self.dest, self.full_plan(),
                                 stock=MAIN)
        lo, hi = DS.part(MAIN, 'sfxbat')
        self.assertIsNone(report['fallback'])
        self.assertTrue(lo <= report['cave_entry'] < hi)
        self.assertEqual(report['routes'], 10)   # BUILD 744: + 3 enemy families
        with open(self.dest, 'rb') as f:
            out = f.read()
        _segs, raw = AC.segments(out)
        self.assertEqual(struct.unpack_from('<I', raw[0], AC.PLAYER_HOOK)[0],
                         A.b(AC.PLAYER_HOOK, report['cave_entry']))
        # the neighbouring parts are still stock: fieldpace and the lip-flap
        # speaker lookup still find their space untouched
        for part in ('pace', 'speak', 'amb', 'coaster', 'dntables'):
            DS.part(self.dest, part, MAIN)

    def test_741_reads_the_installed_sequential_table(self):
        import ff7nx_sfxshuffle as SS
        routes = {sid: {'count': 6, 'payload_base': 0x7000000 + sid * 0x10000,
                        'stride': 4096} for sid in (18, 27, 28, 563)}
        mid = os.path.join(self.tmp, 'shuffled')
        rep = SS.apply_to_nso(MAIN, mid, {'compact': True, 'routes': routes})
        report = SB.apply_to_nso(mid, self.dest, self.full_plan(), stock=MAIN,
                                 shuffle=(rep['table_va'], rep['routes']))
        self.assertIsNone(report['fallback'])
        self.assertEqual(report['rotating_repeats'], 4)
        self.assertIsNone(report['shuffle_note'])
        with open(self.dest, 'rb') as f:
            _segs, raw = AC.segments(f.read())
        # both bridges are in: the loader hook and the player hook
        self.assertNotEqual(struct.unpack_from('<I', raw[0], AC.LOAD_HOOK)[0],
                            AC.LOAD_ORIG)
        self.assertEqual(struct.unpack_from('<I', raw[0], AC.PLAYER_HOOK)[0],
                         A.b(AC.PLAYER_HOOK, report['cave_entry']))

    def test_a_bad_sequential_table_costs_only_the_repeat_fix(self):
        report = SB.apply_to_nso(MAIN, self.dest, self.full_plan(),
                                 stock=MAIN, shuffle=(AC.PLAYER_HOOK, 3))
        self.assertIsNone(report['fallback'])
        self.assertEqual(report['routes'], 10)   # BUILD 744: + 3 enemy families
        self.assertEqual(report['rotating_repeats'], 0)
        self.assertIn('does not read back', report['shuffle_note'])

    def test_a_written_part_falls_back_to_barret_in_the_pool(self):
        import ff7nx_deadspace as DS
        lo, _hi = DS.part(MAIN, 'sfxbat')
        with open(MAIN, 'rb') as f:
            blob = f.read()
        import ff7nx_tables
        _segs, _raw, space = ff7nx_tables.open_module(blob)
        struct.pack_into('<I', space.text, lo + 0x40, 0xD503201F)
        dirty = os.path.join(self.tmp, 'dirty')
        with open(dirty, 'wb') as f:
            f.write(AC.pack(blob, space.commit(), 0))
        report = SB.apply_to_nso(dirty, self.dest, self.full_plan(),
                                 stock=MAIN)
        self.assertIn('already written', report['fallback'])
        self.assertEqual([r['name'] for r in report['effective']], ['Barret'])
        self.assertEqual(len(report['left_out']), 9)
        self.assertEqual(report['bss_bytes'], 4)

    def test_an_unsupported_key_is_refused_rather_than_installed(self):
        plan = {'routes': ({'name': 'enemy', 'keys': (('enemy', 0x30, 348),),
                            'slots': (745,), 'variants': 2},)}
        self.assertRaises(ValueError, SB.apply_to_nso, MAIN, self.dest, plan)


if __name__ == '__main__':
    unittest.main(verbosity=2)
