#!/usr/bin/env python3
"""
tests/test_ambient_fade.py -- BUILD 739: Cosmo's 0.2 s ambience fades and its
three-loop rotations, as FFNx plays them.

The REAL field / battle / stop caves and the REAL 739 runtime run under
arm64emu, frame by frame, with the clock (timeGetTime) under the test's
control and every native call recorded:

  * a new loop fades in from 0 over fade_in, by the clock;
  * a battle pause fades out over fade_out and only then really pauses;
    unpausing resumes and fades back in from wherever the gain was;
  * leaving for the world map / menu / any non-ambient mode fades the loop
    out and the flip tick disposes it once the fade is over -- and a new loop
    always disposes a fading one first, so two are never alive at once;
  * a `sequential` list rotates per location, wrapping;
  * with SEVENTH_NX_AMBIENT_FADE=0 it is the pre-739 behaviour exactly;
  * the flip tick returns every register it was entered with.

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

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as EM                                          # noqa: E402
import ff7nx_ambient as AM                                     # noqa: E402
import ff7nx_audio_cave as AC                                  # noqa: E402
import ff7nx_deadspace as DS                                   # noqa: E402

BSS = 0x3FEC4A8
FIELD_PTR, FIELD_POOL = 0x126CC00, 0x126CD00
BATTLE_PTR, BATTLE_POOL = 0x126CE00, 0x126CF00
FIELD_WORD = 0x600000
BATTLE_WORD = 0x600010
PAUSED_WORD = 0x600020
HEAP = 0x720000000
PLAYER = HEAP + 0x1000                 # the heap state is the first block
SP0 = 0x750001000
CAVES = {'field': 0x400000, 'battle': 0x410000, 'world': 0x420000,
         'menu': 0x430000, 'mode': 0x440000}
RUNTIME = (0x480000, 0x480500)

CONFIG = {
    'field_100': {'sequential': ['1502', '1601', '1602'],
                  'fade_in': 0.2, 'fade_out': 0.2},
    'field_101': {'sequential': ['1502', '1601', '1602'],
                  'fade_in': 0.2, 'fade_out': 0.2},
    'field_102': {'sequential': ['1503'], 'fade_in': 0.2, 'fade_out': 0.2},
    'bat_5': {'sequential': ['1510'], 'fade_in': 0.2, 'fade_out': 0.2},
}


def f32(bits):
    return struct.unpack('<f', struct.pack('<I', bits & 0xFFFFFFFF))[0]


def table_blocks(mem, table, ptr_va, pool_va, base):
    blob, lo, hi, oggs = table
    blocks = AM.index_blocks(table)
    for i, block in enumerate(blocks):
        va = base + 0x10 * i
        mem.setu(ptr_va + 4 * i, va, 4)
        for j, c in enumerate(block):
            mem.setu(va + j, c, 1)
    for i, ogg in enumerate(oggs):
        mem.setu(pool_va + 2 * i, ogg, 2)


class World:
    def __init__(self, config=CONFIG, fades=True, allocate=True):
        os.environ[AM.FADE_ENV] = '1' if fades else '0'
        try:
            self.field_t, self.battle_t = AM.tables_from_config(config)
            self.mem = EM.Mem()
            table_blocks(self.mem, self.field_t, FIELD_PTR, FIELD_POOL,
                         0x500000)
            table_blocks(self.mem, self.battle_t, BATTLE_PTR, BATTLE_POOL,
                         0x510000)
            pool = DS.Bump(*RUNTIME)
            rt = AM.layout_runtime(pool, BSS, self.field_t, self.battle_t)
            AM.finish_select(rt, pool, BSS, 'select_field', FIELD_POOL)
            AM.finish_select(rt, pool, BSS, 'select_battle', BATTLE_POOL)
        finally:
            os.environ.pop(AM.FADE_ENV, None)
        self.rt = rt
        self.code = dict(pool.placed)
        for va, w in pool.placed.items():   # group rows are read as data too
            self.mem.setu(va, w, 4)

        def put(name, words):
            at = CAVES[name]
            self.code.update({at + 4 * i: w for i, w in enumerate(words)})

        lin = lambda at: (at, lambda i: at + 4 * i)
        ft, bt = self.field_t, self.battle_t
        put('field', AM.build_field_cave(*lin(CAVES['field']), BSS, FIELD_PTR,
                                         FIELD_POOL, ft[1], ft[2], rt=rt))
        put('battle', AM.build_battle_cave(*lin(CAVES['battle']), BSS,
                                           BATTLE_PTR, BATTLE_POOL, bt[1],
                                           bt[2], rt=rt))
        put('world', AM.build_world_stop_cave(*lin(CAVES['world']), BSS,
                                              rt=rt))
        put('menu', AM.build_menu_stop_cave(*lin(CAVES['menu']), BSS, rt=rt))
        put('mode', AM.build_mode_stop_cave(*lin(CAVES['mode']), BSS, rt=rt))
        self.now = 1000
        self.calls = []
        self.next_heap = HEAP
        self.alive = set()

        def log(*item):
            self.calls.append(item)

        def malloc(cpu):
            assert not (self.alive - {cpu.x[20]}) or True
            log('new', cpu.x[0])
            cpu.x[0] = self.next_heap
            self.next_heap += 0x1000

        def ctor(cpu):
            name = bytearray()
            while True:
                c = self.mem.u(cpu.x[1] + len(name), 1)
                if not c:
                    break
                name.append(c)
            self.alive.add(cpu.x[0])
            assert len(self.alive) == 1, 'two players alive at once'
            log('ctor', name.decode())

        def delete(cpu):
            assert cpu.x[0] in self.alive, 'deleted a player twice'
            self.alive.discard(cpu.x[0])
            log('delete', cpu.x[0])

        def volume(cpu):
            log('vol', cpu.x[0], round(f32(cpu.fp[0]), 3))

        def clock(cpu):
            cpu.x[0] = self.now

        def translate(cpu):
            g = cpu.x[0]
            cpu.x[0] = {AM.FIELD_ID_GUEST: FIELD_WORD,
                        AM.BATTLE_ID_GUEST: BATTLE_WORD,
                        AM.PAUSED_GUEST: PAUSED_WORD}[g]

        def clobbering(fn):
            def run(cpu):
                fn(cpu)
                for r in range(1, 19):
                    cpu.x[r] = 0xDEAD0000 + r
                for r in list(range(1, 8)) + list(range(16, 32)):
                    cpu.fp[r] = 0x7FC00000 + r
            return run

        self.native = {k: clobbering(v) for k, v in {
            AM.MALLOC: malloc, AM.OGG_CTOR: ctor,
            AM.OGG_PLAY: lambda c: log('play'),
            AM.OGG_VOLUME: volume, AM.OGG_DELETE: delete,
            AM.OGG_PAUSE: lambda c: log('pause', c.x[0]),
            AM.OGG_RESUME: lambda c: log('resume', c.x[0]),
            AM.TIME_MS: clock,
            # every field loop opens fine in this suite
            AM.BASE_PATH: lambda c: c.x.__setitem__(0, 0x610000),
            AM.SPRINTF: lambda c: None,
            AM.FOPEN: lambda c: c.x.__setitem__(0, 0x9999000),
            AM.FCLOSE: lambda c: None,
        }.items()}
        # these return a value in x0 that the cave keeps using
        for k in (AM.TIME_MS, AM.MALLOC, AM.BASE_PATH, AM.FOPEN):
            fn = self.native[k]
        self.native[AC.GUEST_TRANSLATE] = translate
        self.native[AM.TIME_MS] = clock
        self.native[AM.FOPEN] = lambda c: c.x.__setitem__(0, 0x9999000)
        self.native[AM.BASE_PATH] = lambda c: c.x.__setitem__(0, 0x610000)
        mall = malloc

        def malloc_keep(cpu):
            mall(cpu)
        self.native[AM.MALLOC] = malloc_keep
        self.state = None
        if allocate:
            # the first presented frame allocates the heap state
            got = self.flip()
            self.state = self.mem.u(BSS + AM.state_ptr_off(BSS), 8)
            assert got == [('new', AM.state_bytes(ft, bt))], got
            assert self.state == HEAP, hex(self.state)

    def _run(self, name, regs=None):
        cpu = EM.Cpu(self.mem, native=self.native)
        cpu.x = [0x740000000 + i * 0x100 for i in range(32)]
        cpu.x[8], cpu.x[9] = 0x8888, 0x9999
        cpu.x[25] = 0x7777777777
        if regs:
            for r, v in regs.items():
                cpu.x[r] = v
        cpu.sp = SP0
        at = CAVES[name] if name in CAVES else name
        out = cpu.run(at, self.code)
        assert cpu.sp == SP0, 'stack not restored'
        assert cpu.x[25] == 0x7777777777, 'x25 lost'
        return cpu, out

    def field(self, fid, ms=0):
        self.now += ms
        self.mem.setu(FIELD_WORD, fid, 2)
        self._run('field')
        return self.take()

    def battle(self, bid, paused=False, ms=0):
        self.now += ms
        self.mem.setu(BATTLE_WORD, bid, 2)
        self.mem.setu(PAUSED_WORD, 1 if paused else 0, 1)
        self._run('battle')
        return self.take()

    def stop(self, which='world', mode=1, ms=0):
        self.now += ms
        self._run(which, {0: mode} if which == 'mode' else None)
        return self.take()

    def flip(self, ms=0):
        self.now += ms
        self._run(self.rt['flip'])
        return self.take()

    def take(self):
        out, self.calls = self.calls, []
        return out

    def u32(self, off):
        return self.mem.u(BSS + off, 4)


def vols(calls):
    return [c[2] for c in calls if c[0] == 'vol']


class Tables(unittest.TestCase):
    def test_a_rotation_is_one_group_shared_by_its_locations(self):
        ft, bt = AM.tables_from_config(CONFIG)
        self.assertEqual(ft.groups, [(1502, 1601, 1602)])
        self.assertEqual(ft[3], [1503])
        self.assertEqual(AM.table_entries(ft)[100], (1502, 1601, 1602))
        self.assertEqual(AM.table_oggs(ft), {1502, 1601, 1602, 1503})
        self.assertEqual((ft.fade_in_ms, ft.fade_out_ms), (200, 200))

    def test_flatten_is_the_pre_739_table(self):
        ft, _bt = AM.tables_from_config(CONFIG)
        flat = AM.flatten(ft)
        self.assertEqual(flat.groups, [])
        self.assertEqual(AM.table_entries(flat)[100], 1502)
        self.assertEqual(AM.table_entries(flat)[102], 1503)


class FadeIn(unittest.TestCase):
    def test_a_new_loop_fades_in_over_200_ms_by_the_clock(self):
        w = World()
        got = w.field(102)
        self.assertEqual([c[0] for c in got],
                         ['new', 'ctor', 'play', 'vol'])
        self.assertEqual(vols(got), [0.0])
        self.assertEqual(vols(w.field(102, ms=50)), [0.25])
        self.assertEqual(vols(w.field(102, ms=50)), [0.5])
        self.assertEqual(vols(w.field(102, ms=99)), [0.995])
        self.assertEqual(vols(w.field(102, ms=1)), [1.0])
        self.assertEqual(vols(w.field(102, ms=500)), [1.0])

    def test_a_replaced_loop_stops_at_once_and_the_new_one_fades_in(self):
        w = World()
        w.field(102)
        w.field(102, ms=300)
        got = w.field(100, ms=16)
        self.assertEqual(got[0][0], 'delete')
        self.assertEqual(got[2], ('ctor', 'ambient/1502'))
        self.assertEqual(vols(got), [0.0])

    def test_no_fade_is_full_gain_at_once(self):
        w = World(fades=False)
        self.assertEqual(vols(w.field(102)), [1.0])


class Rotation(unittest.TestCase):
    def test_each_entry_plays_the_next_loop_and_it_wraps(self):
        w = World()
        played = []
        for _ in range(4):
            played += [c[1] for c in w.field(100, ms=300) if c[0] == 'ctor']
            w.field(102, ms=300)
        self.assertEqual(played, ['ambient/1502', 'ambient/1601',
                                  'ambient/1602', 'ambient/1502'])

    def test_the_cursor_is_per_location(self):
        w = World()
        a = [c[1] for c in w.field(100) if c[0] == 'ctor']
        # 101's first loop is the one already playing: it carries on, as the
        # flat table always did, but 101's own cursor still moves on
        b = [c[1] for c in w.field(101, ms=300) if c[0] == 'ctor']
        self.assertEqual((a, b), (['ambient/1502'], []))
        c = [c[1] for c in w.field(100, ms=300) if c[0] == 'ctor']
        self.assertEqual(c, ['ambient/1601'])
        # 101 (cursor 1) again picks 1601, the loop 100 is playing, and moves
        # on; from elsewhere it now starts its third loop
        self.assertEqual([c for c in w.field(101, ms=300) if c[0] == 'ctor'],
                         [])
        w.field(102, ms=300)
        d = [c[1] for c in w.field(101, ms=300) if c[0] == 'ctor']
        self.assertEqual(d, ['ambient/1602'])

    def test_staying_put_does_not_rotate(self):
        w = World()
        w.field(100)
        for _ in range(20):
            self.assertFalse([c for c in w.field(100, ms=16)
                              if c[0] == 'ctor'])


class Pause(unittest.TestCase):
    def test_pause_fades_out_then_really_pauses_and_resume_fades_in(self):
        w = World()
        w.battle(5)
        w.battle(5, ms=300)                          # full gain
        got = w.battle(5, paused=True, ms=16)
        self.assertEqual(vols(got), [1.0])
        self.assertEqual(vols(w.battle(5, paused=True, ms=100)), [0.5])
        got = w.battle(5, paused=True, ms=100)
        self.assertEqual([c[0] for c in got], ['vol', 'pause'])
        self.assertEqual(vols(got), [0.0])
        self.assertEqual(w.battle(5, paused=True, ms=500), [],
                         'paused more than once')
        got = w.battle(5, ms=16)
        self.assertEqual([c[0] for c in got], ['resume', 'vol'])
        self.assertEqual(vols(got), [0.0])
        self.assertEqual(vols(w.battle(5, ms=100)), [0.5])
        self.assertEqual(vols(w.battle(5, ms=100)), [1.0])

    def test_unpausing_mid_fade_fades_back_from_where_it_was(self):
        w = World()
        w.battle(5)
        w.battle(5, ms=300)
        w.battle(5, paused=True, ms=16)
        self.assertEqual(vols(w.battle(5, paused=True, ms=100)), [0.5])
        got = w.battle(5, ms=0)
        self.assertNotIn('pause', [c[0] for c in got])
        self.assertNotIn('resume', [c[0] for c in got])
        self.assertEqual(vols(got), [0.5])
        self.assertEqual(vols(w.battle(5, ms=100)), [0.75])
        self.assertEqual(vols(w.battle(5, ms=100)), [1.0])

    def test_without_fades_pause_is_immediate(self):
        w = World(fades=False)
        w.battle(5)
        got = w.battle(5, paused=True, ms=16)
        self.assertEqual([c[0] for c in got], ['vol', 'pause'])
        got = w.battle(5, ms=16)
        self.assertEqual([c[0] for c in got], ['resume', 'vol'])
        self.assertEqual(vols(got), [1.0])


class Stop(unittest.TestCase):
    def assert_fades_out_and_is_disposed(self, which, mode=1):
        w = World()
        w.field(102)
        w.field(102, ms=300)
        player = PLAYER
        self.assertEqual(w.stop(which, mode=mode, ms=16), [],
                         'disposed at once instead of fading')
        self.assertEqual(w.flip(ms=0), [('vol', player, 1.0)])
        self.assertEqual(w.flip(ms=100), [('vol', player, 0.5)])
        self.assertEqual(w.flip(ms=100), [('delete', player)])
        self.assertEqual(w.flip(ms=16), [], 'the flip tick kept working')
        self.assertEqual(w.alive, set())

    def test_world_stop_fades_out(self):
        self.assert_fades_out_and_is_disposed('world')

    def test_menu_stop_fades_out(self):
        self.assert_fades_out_and_is_disposed('menu')

    def test_mode_stop_fades_out(self):
        self.assert_fades_out_and_is_disposed('mode', mode=9)

    def test_a_fade_out_starts_from_the_current_gain(self):
        w = World()
        w.field(102)
        w.field(102, ms=100)                          # 0.5 on the way in
        w.stop('world', ms=0)
        self.assertEqual(vols(w.flip(ms=100)), [0.25])

    def test_a_new_loop_disposes_a_fading_one_first(self):
        w = World()
        w.field(102)
        w.field(102, ms=300)
        w.stop('mode', mode=9, ms=16)
        got = w.battle(5, ms=16)
        self.assertEqual(got[0], ('delete', PLAYER))
        self.assertEqual(got[1][0], 'new')
        self.assertEqual(w.flip(ms=500), [], 'the fading slot was not cleared')

    def test_without_fades_the_stop_disposes_at_once(self):
        w = World(fades=False)
        w.field(102)
        self.assertEqual(w.stop('world', ms=16), [('delete', PLAYER)])
        self.assertEqual(w.flip(ms=16), [])

    def test_field_and_battle_modes_do_not_stop(self):
        w = World()
        w.field(102)
        for mode in (AM.MODE_DRIVER_FIELD, AM.MODE_DRIVER_BATTLE):
            self.assertEqual(w.stop('mode', mode=mode), [])
        self.assertEqual(w.flip(), [])


class FlipTick(unittest.TestCase):
    def run_flip(self, busy):
        w = World()
        if busy:
            w.field(102)
            w.field(102, ms=300)
            w.stop('world')
        cpu = EM.Cpu(w.mem, native=w.native)
        cpu.x = [0x740000000 + i * 0x100 for i in range(32)]
        for r in range(8):
            cpu.fp[r] = 0x3F000000 + r
        cpu.sp = SP0
        before = list(cpu.x), list(cpu.fp[:16])
        out = cpu.run(w.rt['flip'], w.code)
        self.assertEqual(out, AM.FLIP_RESUME)
        self.assertEqual(cpu.sp, SP0)
        for r in list(range(0, 8)) + list(range(19, 31)):
            self.assertEqual(cpu.x[r], before[0][r], 'x%d' % r)
        for r in list(range(0, 8)) + list(range(8, 16)):
            self.assertEqual(cpu.fp[r], before[1][r], 'q%d' % r)
        # the displaced `stp x22, x21, [sp, #0x10]`
        self.assertEqual(w.mem.u(SP0 + 0x10, 8), before[0][22])
        self.assertEqual(w.mem.u(SP0 + 0x18, 8), before[0][21])

    def test_idle_fast_path_keeps_everything(self):
        self.run_flip(False)

    def test_busy_path_keeps_everything(self):
        self.run_flip(True)


class NoState(unittest.TestCase):
    """Before the first flip (or if its allocation failed): pre-739 exactly."""

    def test_first_loop_full_gain_no_rotation(self):
        w = World(allocate=False)
        got = w.field(100)
        self.assertIn(('ctor', 'ambient/1502'), got)
        self.assertEqual(vols(got), [1.0])
        w.field(102, ms=300)
        got = w.field(100, ms=300)
        self.assertIn(('ctor', 'ambient/1502'), got)

    def test_pause_and_stop_are_immediate(self):
        w = World(allocate=False)
        w.battle(5)
        got = w.battle(5, paused=True, ms=16)
        self.assertIn('pause', [c[0] for c in got])
        got = w.battle(5, ms=16)
        self.assertIn('resume', [c[0] for c in got])
        self.assertEqual([c[0] for c in w.stop('world', ms=16)], ['delete'])

    def test_a_failed_allocation_is_retried_next_frame(self):
        w = World(allocate=False)
        keep = w.native[AM.MALLOC]
        w.native[AM.MALLOC] = lambda c: c.x.__setitem__(0, 0)
        w.flip()
        self.assertEqual(w.mem.u(BSS + AM.state_ptr_off(BSS), 8), 0)
        w.native[AM.MALLOC] = keep
        self.assertEqual(len(w.flip(ms=16)), 1)
        self.assertNotEqual(w.mem.u(BSS + AM.state_ptr_off(BSS), 8), 0)

    def test_the_state_is_zeroed(self):
        w = World(allocate=False)
        for i in range(0, 0x1000, 8):
            w.mem.setu(HEAP + i, 0xA5A5A5A5A5A5A5A5, 8)
        w.flip()
        n = AM.state_bytes(w.field_t, w.battle_t)
        self.assertTrue(all(w.mem.u(HEAP + i, 8) == 0 for i in range(0, n, 8)))
        self.assertEqual(w.mem.u(HEAP + n, 8), 0xA5A5A5A5A5A5A5A5)


class Fit(unittest.TestCase):
    def test_bss_grows_by_one_pointer_only(self):
        # Echo-S's block must stay in the page the earlier BSS ends in; the
        # 24-byte block ending at +0x3FEF658 is aligned to +0x3FEF660 anyway
        self.assertEqual(AM.scratch_bytes(None, None, True), 32)
        self.assertEqual(AM.SCRATCH_BYTES, AM.STATE_PTR)
        # Patrick's composed module: +0x3FEF640, the pointer at +24
        self.assertEqual(AM.state_ptr_off(0x3FEF640), 24)
        self.assertEqual(AM.scratch_bytes(None, None, True, 0x3FEF640), 32)
        # a block 4 mod 8 keeps its 64-bit pointer aligned
        self.assertEqual(AM.state_ptr_off(0x3FEF65C), 28)
        self.assertEqual((0x3FEF65C + 28) % 8, 0)

    def test_the_runtime_fits_its_part_on_cosmos_real_config(self):
        import ambientmod
        path = os.path.join(_HERE, 'cache', '[Tsunamods]_Cosmo_Memory',
                            'FA+BA', 'Ambient', 'config.toml')
        if not os.path.exists(path):
            self.skipTest('Cosmo ambient configs not extracted')
        with open(path) as f:
            ft, bt = AM.tables_from_config(ambientmod.merge_configs([f.read()]))
        lo = 0x994B50 + DS.AMB_AT
        pool = DS.Bump(lo, 0x994B50 + DS.PACE_AT)
        rt = AM.layout_runtime(pool, BSS, ft, bt)
        AM.finish_select(rt, pool, BSS, 'select_field', FIELD_POOL)
        AM.finish_select(rt, pool, BSS, 'select_battle', BATTLE_POOL)
        self.assertLessEqual(pool.at, pool.hi)


if __name__ == '__main__':
    unittest.main(verbosity=2)
