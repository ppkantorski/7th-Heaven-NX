#!/usr/bin/env python3
"""
tests/test_sfxbattle_routes.py -- BUILD 740: Cosmo's seven character battle
routes (HANDOFF-740 step A), executed.

The REAL cave runs under arm64emu at the player boundary, with the guest
address translator clobbering x1..x18 the way the real one may. Each play
reports the id written back to the caller, the descriptor writes and the
packed cursor word, and the tests hold it to:

  * a routed attacker gets ITS OWN rows, rotating, and each play of a level-2
    route points its row at the next variant (exact length, base + i*stride);
  * the same logical id from any other attacker, an enemy, or outside battle
    is left exactly as asked -- the leak BUILD 739 was built to prevent;
  * Barret is BUILD 739 exactly: same row sequence as the legacy cave, no
    descriptor writes;
  * cursors are independent nibbles of one word, and nothing else in it moves;
  * every register the stock continuation reads comes back as it should.

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
import ff7nx_audio_cave as AC                                  # noqa: E402
import ff7nx_deadspace as DS                                   # noqa: E402
import ff7nx_sfxbattle as SB                                   # noqa: E402

BSS = 0x3FEC4A8
PART = (0x995410, 0x995750)            # 'sfxbat' on the 1.0.3 module
TABS = (0x996550, 0x996750)            # 'sfxtab' (BUILD 742)
LEGACY_AT = 0x480000
HOST = 0x600000000                     # guest g lives at HOST + g
ARG = 0x5000                           # the caller's guest argument block
SP0 = 0x750001000
BASES = {}


def make_routes():
    routes = []
    base = 120000000
    layout = (list(SB.ROUTE_LAYOUT)
              + [(n, (k,), (r,), True) for n, k, r in SB.SPECIAL_LAYOUT]
              + [(n, tuple(('enemy', e, sid) for e in ids), (SB.ENEMY_ROW,),
                  False, True) for n, sid, ids in SB.ENEMY_LAYOUT])
    # Echo-S's config (it wins over Cosmo's): Rude 3, Reno 8, Rufus 8.
    counts = {'Omnislash hits': 1, 'Rude': 3, 'Reno': 8, 'Rufus': 8}
    for item in layout:
        name, keys, rows = item[:3]
        omni = len(item) > 3 and item[3]
        enemy = len(item) > 4
        count = counts.get(name, 6)
        level2 = count > len(rows)
        lens = tuple(1000 * (i + 1) + len(name) for i in range(count))
        stride = 8192 if level2 else 0
        routes.append({'name': name, 'keys': keys, 'omni': omni,
                       'enemy': enemy,
                       'slots': tuple(rows[:count]), 'variants': count,
                       'lens': lens, 'stride': stride,
                       'payload_base': base if level2 else None})
        if level2:
            BASES[name] = base
            base += stride * count
    return tuple(routes)


ROUTES = make_routes()
BY_NAME = {r['name']: r for r in ROUTES}

# BUILD 741: a stand-in for the sequential bridge's route table -- ids 18
# (Cloud's hit, Cosmo's 3000-3005), 28 and 563 rotate; 19 does not.
SHUFFLE_VA = 0x126CCB0
SHUFFLE_IDS = (28, 18, 27, 563)


def shuffle_words():
    words = []
    for sid in SHUFFLE_IDS:
        words += [(sid - 1) | (6 << 10) | (4096 << 13), 0x7000000]
    return words


class Battle:
    def __init__(self, routes=ROUTES, legacy=False, shuffle=True):
        self.mem = EM.Mem()
        for i, w in enumerate(shuffle_words()):
            self.mem.setu(SHUFFLE_VA + 4 * i, w, 4)
        shuffle = (SHUFFLE_VA, len(SHUFFLE_IDS)) if shuffle else None
        if legacy:
            words = SB.build_legacy_cave(
                LEGACY_AT, lambda i: LEGACY_AT + 4 * i, BSS,
                tuple(r for r in routes if r['name'] == 'Barret'))
            self.entry = LEGACY_AT
            self.code = {LEGACY_AT + 4 * i: w for i, w in enumerate(words)}
        else:
            bump = DS.Bump(*PART)
            tabs = DS.Bump(*TABS)
            self.entry, _t, self.table_bytes = SB.layout(bump, BSS, routes,
                                                         shuffle, tabs)
            self.code = dict(bump.placed)
            self.code.update(tabs.placed)
            self.used = bump.at - bump.lo
            self.tables_used = tabs.at - tabs.lo
            for va, w in self.code.items():       # records are read as data
                self.mem.setu(va, w, 4)
        self.writes = []

        def translate(cpu):
            g = cpu.x[0] & 0xFFFFFFFF
            cpu.x[0] = HOST + g
            if AC.META_GUEST <= g < AC.META_GUEST + 750 * AC.META_STRIDE:
                self.writes.append(g)
            for r in range(1, 19):
                cpu.x[r] = 0xDEAD0000 + r
        self.native = {AC.GUEST_TRANSLATE: translate}

    def channel_id(self, channel):
        return self.mem.u(HOST + SB.CHANNEL_GUEST + SB.CHANNEL_STRIDE
                          * (channel - 1) + SB.CHANNEL_ID, 4)

    def omnislash(self, on=True, slot=37):
        """Put the Omnislash loop in (or take it out of) effect100's list."""
        for i in range(SB.EFFECT100_SLOTS):
            self.mem.setu(HOST + SB.EFFECT100_FN_GUEST + 4 * i,
                          0x5BD96D if i == 3 else 0, 4)
        if on:
            self.mem.setu(HOST + SB.EFFECT100_FN_GUEST + 4 * slot,
                          SB.OMNISLASH_LOOP, 4)

    def play(self, sid, model=None, actor=0, mode=2, channel=1, held=0,
             enemy=None):
        """One play_sfx_on_channel. Returns (id, [(row, length, offset)]).

        `held` is the id the channel says it already holds (+0x40); after the
        call `self.held` is what the cave left there."""
        self.mem.setu(HOST + ARG + 0x10, channel, 4)
        for ch in range(1, SB.CHANNELS + 1):
            self.mem.setu(HOST + SB.CHANNEL_GUEST + SB.CHANNEL_STRIDE
                          * (ch - 1) + SB.CHANNEL_ID,
                          held if ch == channel else 0x5A5A, 4)
        self.mem.setu(HOST + SB.GAME_MODE_GUEST, mode, 1)
        self.mem.setu(HOST + SB.ANIM_EVENT_QUEUE_GUEST, actor, 1)
        if model is not None:
            self.mem.setu(HOST + SB.CHAR_INDEX_GUEST + 0x68 * actor, model, 1)
        self.mem.setu(HOST + ARG + 12, sid, 4)
        if enemy is not None:
            self.mem.setu(HOST + SB.FORMATION_GUEST + 0x68 * actor, enemy, 2)
        # every descriptor starts as a marker so a write is visible
        for row in SB.RESERVED_ROWS:
            at = HOST + AC.meta_guest(row)
            self.mem.setu(at, 0xAAAA0000 + row, 4)
            self.mem.setu(at + 4, 0xBBBB0000 + row, 4)
        cpu = EM.Cpu(self.mem, native=self.native)
        cpu.x = [0x740000000 + i * 0x100 for i in range(32)]
        cpu.x[8] = ARG
        keep = {r: cpu.x[r] for r in (19, 20, 21, 22, 23, 24, 25, 26, 30)}
        cpu.sp = SP0
        out = cpu.run(self.entry, self.code)
        assert out == AC.PLAYER_RESUME, hex(out)
        assert cpu.sp == SP0, 'stack not restored'
        for r, v in keep.items():
            assert cpu.x[r] == v, 'x%d not restored' % r
        got = self.mem.u(HOST + ARG + 12, 4)
        assert cpu.x[8] & 0xFFFFFFFF == got, 'w8 is not the written id'
        assert cpu.x[0] == HOST + ARG + 12, 'x0 is not the argument pointer'
        if 1 <= channel <= SB.CHANNELS:
            self.held = self.channel_id(channel)
        for ch in range(1, SB.CHANNELS + 1):
            if ch != channel:
                assert self.channel_id(ch) == 0x5A5A, 'another channel moved'
        desc = []
        for row in SB.RESERVED_ROWS:
            at = HOST + AC.meta_guest(row)
            n, off = self.mem.u(at, 4), self.mem.u(at + 4, 4)
            if (n, off) != (0xAAAA0000 + row, 0xBBBB0000 + row):
                desc.append((row, n, off))
        return got, desc

    def cursor(self):
        return self.mem.u(BSS, 4)


class Routing(unittest.TestCase):
    def test_it_fits_its_dead_space_part_with_room_to_spare(self):
        b = Battle()
        self.assertLessEqual(b.used, PART[1] - PART[0] - 128)   # BUILD 744: 688 of 832
        self.assertLessEqual(b.tables_used, TABS[1] - TABS[0] - 96)   # 744: 400 of 512

    def test_each_level2_route_rotates_its_rows_and_every_variant(self):
        for name in ('Vincent', 'Barret cut', 'Cait Sith 290',
                     'Cait Sith 309'):
            route = BY_NAME[name]
            (_kind, model, sid), = route['keys']
            b = Battle()
            rows = route['slots']
            for turn in range(14):
                c = turn % 6
                got, desc = b.play(sid, model)
                row = rows[c % len(rows)]
                self.assertEqual(got, row, (name, turn))
                self.assertEqual(desc, [(row, route['lens'][c],
                                         BASES[name] + c * route['stride'])],
                                 (name, turn))

    def test_barret_is_build_739_exactly(self):
        new, old = Battle(), Battle(legacy=True)
        seq_new, seq_old = [], []
        for _ in range(15):
            got, desc = new.play(16, 1)
            self.assertEqual(desc, [])            # no descriptor write
            seq_new.append(got)
            seq_old.append(old.play(16, 1)[0])
        self.assertEqual(seq_new, seq_old)
        self.assertEqual(seq_new[:7], [731, 732, 733, 734, 735, 736, 731])

    def test_the_same_id_from_anyone_else_stays_stock(self):
        b = Battle()
        for model in (0, 2, 4, 5, 8, 9):          # not 7: 274 is Vincent's
            self.assertEqual(b.play(274, model), (274, []))
        self.assertEqual(b.play(16, 7), (16, []))   # Vincent, Barret's id
        self.assertEqual(b.play(352, 3), (352, []))  # Aerith, Barret's cut
        self.assertEqual(b.play(290, 3), (290, []))
        self.assertEqual(b.play(342, 3), (342, []))  # 742: Aerith unrouted
        self.assertEqual(b.play(342, 8), (342, []))  # Cid: his own sound
        self.assertEqual(b.cursor(), 0)

    def test_an_enemy_attacker_stays_stock(self):
        b = Battle()
        for actor in (3, 4, 5, 9):
            # even with a party model index sitting in its actor slot
            self.assertEqual(b.play(274, 7, actor=actor), (274, []))
        self.assertEqual(b.cursor(), 0)

    def test_outside_battle_nothing_is_routed(self):
        b = Battle()
        for mode in (0, 1, 3, 5):
            self.assertEqual(b.play(274, 7, mode=mode), (274, []))
            self.assertEqual(b.play(16, 1, mode=mode), (16, []))
        self.assertEqual(b.cursor(), 0)

    def test_cursors_are_independent_three_bit_fields(self):
        b = Battle()
        b.mem.setu(BSS, 0xC0000000, 4)            # bits no route owns
        self.assertEqual(b.play(274, 7)[0], 737)
        self.assertEqual(b.play(16, 1)[0], 731)
        self.assertEqual(b.play(274, 7)[0], 738)
        self.assertEqual(b.play(290, 6)[0], 749)
        self.assertEqual(b.play(16, 1)[0], 732)
        self.assertEqual(b.play(309, 6)[0], 750)
        self.assertEqual(b.play(348, None, actor=5, enemy=0x30)[0], 739)
        # Barret 2, Vincent 2, Cait 290 (3) 1, Cait 309 (4) 1, Reno (8) 1
        self.assertEqual(b.cursor(), 0xC0000000 | 2 | (2 << 3) | (1 << 9)
                         | (1 << 12) | (1 << 24))

    def test_a_stale_cursor_wraps_instead_of_overrunning(self):
        b = Battle()
        b.mem.setu(BSS, 0x7 << 3, 4)              # Vincent's field at 7
        got, desc = b.play(274, 7)
        self.assertEqual(got, 737)                # treated as variant 0
        self.assertEqual(desc[0][2], BASES['Vincent'])
        self.assertEqual(b.cursor(), 1 << 3)

    def test_a_route_whose_payloads_never_landed_uses_its_rows_only(self):
        routes = tuple(dict(r, payload_base=None) if r['name'] == 'Vincent'
                       else r for r in ROUTES)
        b = Battle(routes)
        seq = [b.play(274, 7) for _ in range(4)]
        self.assertEqual(seq, [(737, []), (738, []), (737, []), (738, [])])

    def test_every_descriptor_write_is_inside_a_reserved_row(self):
        b = Battle()
        for name, keys, _rows in SB.ROUTE_LAYOUT:
            (_k, model, sid), = keys
            for _ in range(7):
                b.play(sid, model)
        for g in b.writes:
            row = (g - AC.META_GUEST) // AC.META_STRIDE + 1
            self.assertIn(row, SB.RESERVED_ROWS)
            self.assertNotIn(row, SB.BARRET_SLOTS)


class SameChannelRepeat(unittest.TestCase):
    """BUILD 741: Omnislash replayed one grunt for every hit."""

    def test_a_rotating_repeat_on_the_same_channel_reloads(self):
        b = Battle()
        self.assertEqual(b.play(18, 0, channel=2, held=18), (18, []))
        self.assertEqual(b.held, 0)          # -> the player's miss path

    def test_omnislash_hits_each_reach_the_loader(self):
        b = Battle()
        for _ in range(15):
            b.play(18, 0, channel=3, held=18)
            self.assertEqual(b.held, 0)

    def test_a_different_held_sound_is_left_alone(self):
        b = Battle()
        b.play(18, 0, channel=2, held=19)
        self.assertEqual(b.held, 19)

    def test_a_non_rotating_repeat_keeps_the_stock_rewind(self):
        b = Battle()
        b.play(19, 0, channel=2, held=19)
        self.assertEqual(b.held, 19)

    def test_every_table_entry_is_found_first_middle_and_last(self):
        for sid in SHUFFLE_IDS:
            b = Battle()
            b.play(sid, 0, channel=1, held=sid)
            self.assertEqual(b.held, 0, sid)

    def test_enemy_repeats_rotate_too(self):
        b = Battle()
        b.play(18, None, actor=5, channel=4, held=18)
        self.assertEqual(b.held, 0)

    def test_outside_battle_nothing_changes(self):
        b = Battle()
        for mode in (0, 1, 3):
            b.play(18, 0, mode=mode, channel=2, held=18)
            self.assertEqual(b.held, 18)

    def test_a_routed_row_repeat_reloads_so_one_row_routes_rotate(self):
        b = Battle()
        got, desc = b.play(309, 6, channel=2, held=750)
        self.assertEqual(got, 750)
        self.assertEqual(b.held, 0)
        got, desc = b.play(309, 6, channel=2, held=750)
        self.assertEqual(desc[0][2], BASES['Cait Sith 309'] + 8192)

    def test_bad_channel_numbers_touch_nothing(self):
        for ch in (0, SB.CHANNELS + 1, 0xFFFFFFFF):
            b = Battle()
            self.assertEqual(b.play(18, 0, channel=ch, held=18), (18, []))

    def test_without_the_table_only_routes_reload(self):
        b = Battle(shuffle=False)
        b.play(18, 0, channel=2, held=18)
        self.assertEqual(b.held, 18)
        b.play(274, 7, channel=2, held=737)
        self.assertEqual(b.held, 0)


class Omnislash(unittest.TestCase):
    """BUILD 742: original slashes on the hits, a grunt on the final blow."""

    def test_hits_play_the_original_27_row_and_rewind_as_stock(self):
        b = Battle()
        b.omnislash()
        for _ in range(14):
            self.assertEqual(b.play(27, 0, channel=2, held=747), (747, []))
            self.assertEqual(b.held, 747)    # one sound: no forced reload

    def test_the_final_blow_rotates_through_the_mixed_grunts(self):
        b = Battle()
        route = BY_NAME['Omnislash final blow']
        for turn in range(8):
            b.omnislash()
            got, desc = b.play(645, 0, channel=1, held=748)
            c = turn % 6
            self.assertEqual(got, 748)
            self.assertEqual(desc, [(748, route['lens'][c],
                                     BASES['Omnislash final blow']
                                     + c * route['stride'])])
            self.assertEqual(b.held, 0)      # each final blow reloads

    def test_the_loop_is_found_in_any_slot(self):
        for slot in (0, 1, 50, 99):
            b = Battle()
            b.omnislash(slot=slot)
            self.assertEqual(b.play(27, 0)[0], 747, slot)

    def test_outside_omnislash_cloud_keeps_his_voiced_critical(self):
        b = Battle()
        b.omnislash(on=False)
        self.assertEqual(b.play(27, 0, channel=2, held=27), (27, []))
        self.assertEqual(b.held, 0)          # 741: still rotates
        self.assertEqual(b.play(645, 0), (645, []))

    def test_only_cloud(self):
        b = Battle()
        b.omnislash()
        for model in (1, 2, 7, 8):
            self.assertEqual(b.play(27, model), (27, []))
        self.assertEqual(b.play(27, None, actor=4), (27, []))

    def test_other_omnislash_sounds_are_untouched(self):
        b = Battle()
        b.omnislash()
        for sid in (316, 554, 392, 393, 654, 577):
            self.assertEqual(b.play(sid, 0), (sid, []))

    def test_omnislash_does_not_disturb_the_other_routes(self):
        b = Battle()
        b.omnislash()
        self.assertEqual(b.play(16, 1)[0], 731)
        self.assertEqual(b.play(274, 7)[0], 737)


class Records(unittest.TestCase):
    def test_records_refuse_rows_outside_the_reserve(self):
        bad = dict(ROUTES[1], slots=(618, 619))
        self.assertRaises(ValueError, SB.route_records, (bad,))

    def test_records_refuse_an_enemy_key_on_a_character_route(self):
        bad = dict(ROUTES[1], keys=(('enemy', 0x44, 274),))
        self.assertRaises(ValueError, SB.route_records, (bad,))

    def test_records_refuse_a_character_key_on_an_enemy_route(self):
        bad = dict(BY_NAME['Rufus'], keys=(('char', 7, 274),))
        self.assertRaises(ValueError, SB.route_records, (bad,))

    def test_records_refuse_an_enemy_route_without_level2(self):
        bad = dict(BY_NAME['Rufus'], payload_base=None)
        self.assertRaises(ValueError, SB.route_records, (bad,))

    def test_records_refuse_too_many_variants_for_a_cursor(self):
        bad = dict(ROUTES[1], variants=9, lens=(1,) * 9)
        self.assertRaises(ValueError, SB.route_records, (bad,))

    def test_ten_routes_is_the_most_one_word_holds(self):
        self.assertEqual(len(ROUTES), SB.MAX_ROUTES)
        self.assertRaises(ValueError, SB.route_records,
                          ROUTES + (ROUTES[1],))

    def test_enemy_map_names_every_family_key_once(self):
        emap = SB.enemy_map(ROUTES)
        pairs = [struct.unpack_from('<HH', emap, i)
                 for i in range(0, len(emap), 4)]
        self.assertEqual(len(pairs), 9)
        idx = {r['name']: i for i, r in enumerate(ROUTES)}
        for name, _sid, ids in SB.ENEMY_LAYOUT:
            for e in ids:
                self.assertIn((e, SB.ENEMY_KEY | idx[name]), pairs)


class Enemies(unittest.TestCase):
    """BUILD 744: Cosmo's Reno, Rude and Rufus grunts."""

    FAMILIES = (('Rude', 35, (0x80, 0x113, 0x119, 0x12A)),
                ('Reno', 348, (0x30, 0x7F, 0x112, 0x129)),
                ('Rufus', 274, (0x44,)))

    def test_every_family_rotates_every_variant_through_row_739(self):
        for name, sid, ids in self.FAMILIES:
            route = BY_NAME[name]
            b = Battle()
            for turn in range(16):
                eid = ids[turn % len(ids)]
                actor = 4 + turn % 6
                got, desc = b.play(sid, None, actor=actor, enemy=eid,
                                   channel=2, held=739)
                c = turn % route['variants']
                self.assertEqual(got, 739, (name, turn))
                self.assertEqual(desc, [(739, route['lens'][c],
                                         BASES[name] + c * route['stride'])],
                                 (name, turn))
                self.assertEqual(b.held, 0)      # always a fresh load

    def test_families_keep_their_own_cursors_when_they_interleave(self):
        b = Battle()
        seq = []
        for turn in range(6):
            for name, sid, ids in self.FAMILIES:
                _got, desc = b.play(sid, None, actor=5, enemy=ids[0])
                seq.append((name, desc[0][2]))
        for name, _sid, _ids in self.FAMILIES:
            route = BY_NAME[name]
            mine = [off for n, off in seq if n == name]
            self.assertEqual(mine, [BASES[name] + (t % route['variants'])
                                    * route['stride'] for t in range(6)])

    def test_rufus_and_vincent_each_keep_their_own_274(self):
        b = Battle()
        _g, d_v = b.play(274, 7, actor=1)
        _g, d_r = b.play(274, None, actor=4, enemy=0x44)
        self.assertEqual(d_v[0][0], 737)
        self.assertEqual(d_r[0][0], 739)
        self.assertEqual(d_r[0][2], BASES['Rufus'])
        _g, d_v = b.play(274, 7, actor=1)
        self.assertEqual(d_v[0], (738, BY_NAME['Vincent']['lens'][1],
                                  BASES['Vincent'] + 8192))

    def test_gongaga_rude_does_not_get_renos_grunts(self):
        b = Battle()
        self.assertEqual(b.play(348, None, actor=4, enemy=0x80),
                         (348, []))            # Cosmo's typo is not routed
        self.assertEqual(b.play(348, None, actor=5, enemy=0x7F)[0], 739)

    def test_the_wrong_id_or_enemy_stays_stock(self):
        b = Battle()
        self.assertEqual(b.play(35, None, actor=4, enemy=0x30), (35, []))
        self.assertEqual(b.play(348, None, actor=4, enemy=0x80), (348, []))
        self.assertEqual(b.play(274, None, actor=6, enemy=0x30), (274, []))
        for eid in (0, 0x43, 0x45, 0xFFFF, 0x1044, 0x0144):
            self.assertEqual(b.play(274, None, actor=4, enemy=eid),
                             (274, []), hex(eid))
        self.assertEqual(b.cursor(), 0)

    def test_only_enemy_slots_four_to_nine(self):
        b = Battle()
        for actor in (0, 1, 2, 3, 10, 11, 255):
            self.assertEqual(b.play(274, None, actor=actor, enemy=0x44),
                             (274, []), actor)
        for actor in range(4, 10):
            self.assertEqual(b.play(274, None, actor=actor,
                                    enemy=0x44)[0], 739, actor)

    def test_a_party_member_with_an_enemy_id_in_its_slot_is_a_party_member(self):
        b = Battle()
        # actor 1's +0x24 holds Rufus's id; it is still the party lookup
        b.mem.setu(HOST + SB.FORMATION_GUEST + 0x68, 0x44, 2)
        self.assertEqual(b.play(274, 2, actor=1), (274, []))

    def test_enemy_repeats_of_unrouted_rotating_ids_still_reload(self):
        b = Battle()
        b.play(18, None, actor=5, enemy=0x44, channel=4, held=18)
        self.assertEqual(b.held, 0)

    def test_outside_battle_nothing_is_routed(self):
        b = Battle()
        for mode in (0, 1, 3, 5):
            self.assertEqual(b.play(348, None, actor=4, mode=mode,
                                    enemy=0x30), (348, []))
        self.assertEqual(b.cursor(), 0)

    def test_without_enemy_routes_the_cave_has_no_enemy_path(self):
        chars = tuple(r for r in ROUTES if not r['enemy'])
        b = Battle(chars)
        self.assertEqual(b.play(274, None, actor=4, enemy=0x44), (274, []))
        self.assertEqual(SB.enemy_map(chars), b'')
        self.assertLess(b.used, Battle().used)


if __name__ == '__main__':
    unittest.main(verbosity=2)
