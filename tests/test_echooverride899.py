#!/usr/bin/env python3
"""BUILD 899: ff7nx_echooverride -- Echo-S's per-scene cues, per-battle
themes and shuffled variants. The planner is tested on its own; the caves
are installed into the real module and the PATCHED WORDS are executed with
arm64emu (every changed word is the code map), so a wrong encoding, label or
register shows up as a wrong result.

    python3 -m pytest tests/test_echooverride899.py

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import os
import struct

import pytest

import arm64emu as E
import ff7nx_audio_cave as AC
import ff7nx_echooverride as O

MAIN = os.path.join(_nx_root, 'dump', 'exefs', 'main')
NAMES = ['none'] + ['t%02d' % i for i in range(1, 99)]
NAMES[7], NAMES[12], NAMES[52], NAMES[67] = 'bat', 'earis', 'chu2', 'bee'

needs_dump = pytest.mark.skipif(not os.path.isfile(MAIN),
                                reason='needs dump/exefs/main')


# ------------------------------------------------------------- the planner
def _plan(extra=None, shuffle=None, log=None):
    extra = extra if extra is not None else {
        'field_ealin_12_1.ogg': 'A', 'field_las4_2_0.ogg': 'B',
        'bat_527.ogg': 'C', 'bat1.ogg': 'D', 'bee1.ogg': 'E',
        'bee2.ogg': 'F'}
    shuffle = shuffle if shuffle is not None else {
        'bat': ['bat', 'bat1'], 'bee': ['bee', 'bee1', 'bee2']}
    fields = {'ealin_12': [38, 12], 'las4_2': [52]}
    return O.plan(extra, shuffle, fields.get, NAMES,
                  log=(log.append if log is not None else lambda *_: None))


def test_plan_resolves_each_kind():
    ents = _plan()
    kinds = [(e.kind, e.source_name) for e in ents]
    assert kinds == [('field', 'field_ealin_12_1.ogg'),
                     ('field', 'field_las4_2_0.ogg'),
                     ('battle', 'bat_527.ogg'),
                     ('shuffle', 'bat1.ogg'), ('shuffle', 'bee1.ogg'),
                     ('shuffle', 'bee2.ogg')]
    f = ents[0]
    assert (f.field, f.rel, f.midi) == ('ealin_12', 1, 12)
    assert f.hash == O.field_name_hash('ealin_12')
    assert [e.slot for e in ents] == list(range(99, 105))
    assert [e.alias for e in ents] == ['eo%02d' % i for i in range(6)]
    assert all(len(e.alias) == 4 for e in ents), 'thread-title safe'
    bee = [e for e in ents if e.kind == 'shuffle' and e.base == 'bee']
    assert bee[0].options[0] is None and len(bee[0].options) == 3


def test_plan_skips_what_cannot_be_resolved():
    log = []
    ents = _plan({'field_nosuch_0.ogg': 'A', 'field_ealin_12_5.ogg': 'B',
                  'bat1.ogg': 'C'}, {'bat': ['bat', 'bat1', 'bat9'],
                                     'zzz': ['zzz', 'z1']}, log)
    assert [e.source_name for e in ents] == ['bat1.ogg']
    text = '\n'.join(log)
    assert 'no field nosuch' in text and 'no music entry 5' in text
    assert 'bat9.ogg is not shipped' in text and '[zzz]' in text


def test_slots_never_reach_the_title_track():
    extra = {'bat_%d.ogg' % i: 'x' for i in range(40)}
    log = []
    ents = O.plan(extra, {}, lambda f: None, NAMES, log=log.append)
    assert len(ents) == O.SLOT_LAST - O.SLOT_FIRST + 1
    assert max(e.slot for e in ents) == 126
    assert any('no player slot left' in l for l in log)


def test_parse_shuffle_and_names():
    toml = "\n[bat]\nshuffle = [ 'bat', 'bat1' ]\n\n[Bee] # x\nshuffle = ['bee','bee1.ogg']\n"
    assert O.parse_shuffle(toml) == {'bat': ['bat', 'bat1'],
                                     'bee': ['bee', 'bee1']}
    assert O.is_override_name('field_canon_2_1.ogg')
    assert O.is_override_name('bat_743.ogg')
    assert O.is_override_name('bat1.ogg', {'bat1'})
    assert not O.is_override_name('bat1.ogg')
    assert not O.is_override_name('_field_jet_1.ogg')
    assert not O.is_override_name('bat_mako (for crossfade later).ogg')


def test_akao_ids_is_header_byte_minus_one():
    sec = bytearray(0x200)
    sec[2] = 1                                         # one entity
    struct.pack_into('<H', sec, 6, 2)                  # two AKAO blocks
    struct.pack_into('<2I', sec, 0x28, 0x100, 0x140)
    sec[0x100:0x105] = b'AKAO\x0d'
    sec[0x140:0x145] = b'AKAO\x35'
    assert O.akao_ids(bytes(sec)) == [12, 52]


# -------------------------------------------------------------- the caves
@pytest.fixture(scope='module')
def patched(tmp_path_factory):
    if not os.path.isfile(MAIN):
        pytest.skip('needs dump/exefs/main')
    ents = _plan()
    out = str(tmp_path_factory.mktemp('main') / 'main')
    report = O.apply_to_nso(MAIN, out, ents)
    _s0, before = AC.segments(open(MAIN, 'rb').read())
    _s1, after = AC.segments(open(out, 'rb').read())
    code = {}
    for off in range(0, len(before[0]), 4):
        w = struct.unpack_from('<I', after[0], off)[0]
        if w != struct.unpack_from('<I', before[0], off)[0]:
            code[off] = w
    assert O.PLAY_NATIVE in code and O.BATTLE_MUSIC_CALL in code
    return ents, report, code


PLAYER_HOST = 0x5000000
RET_TO = 0xFEED000
SP0 = 0x7F000000


def _cpu(report, mode=1, field='field\\ealin_12', battle_id=0, flag=0,
         current=0, counter=0):
    mem = E.Mem()
    calls = []

    def request(cpu):
        name = bytes(mem.read(cpu.x[1], 8)).split(b'\0')[0].decode()
        calls.append((name, cpu.x[2] & E.M32, cpu.x[3] & E.M32))
        for r in list(range(1, 19)):
            cpu.x[r] = E.GARBAGE
        cpu.x[0] = 0x55
    cpu = E.Cpu(mem, paged=True, native={O.REQUEST: request})
    mem.setu(O.PLAYER_PTR_PAGE + O.PLAYER_PTR_OFF, PLAYER_HOST, 8)
    mem.setu(PLAYER_HOST, PLAYER_HOST + 0x1000, 8)
    mem.setu(PLAYER_HOST + 0x1000 + O.CURRENT_OFF, current, 4)
    mem.setu(cpu.guest_to_host(O.GAME_MODE), mode, 2)
    mem.write(cpu.guest_to_host(O.FIELD_FILE_NAME), field.encode() + b'\0')
    mem.setu(cpu.guest_to_host(O.BATTLE_ID_GUEST), battle_id, 2)
    mem.setu(report['bss'] + O.BSS_FLAG, flag, 4)
    cpu.cntpct = counter
    for r in range(19, 30):
        cpu.x[r] = 0x1100 + r
    cpu.x[30] = RET_TO
    cpu.sp = SP0
    return cpu, calls


def _play(patched, id_, fade=3, **kw):
    ents, report, code = patched
    cpu, calls = _cpu(report, **kw)
    cpu.x[0], cpu.x[1] = id_, fade
    end = cpu.run(O.PLAY_NATIVE, code, start_pc=O.PLAY_NATIVE)
    return cpu, calls, end


def _is_stock(cpu, end, id_, fade=3):
    """Left through the displaced `str x21, [sp, #-0x30]!` into the stock
    body, with the arguments and every callee-saved register as they came."""
    assert end == O.PLAY_RESUME
    assert (cpu.x[0], cpu.x[1]) == (id_, fade)
    assert cpu.sp == SP0 - 0x30
    assert cpu.mem.u(cpu.sp, 8) == 0x1100 + 21
    assert [cpu.x[r] for r in range(19, 30)] == [0x1100 + r
                                                 for r in range(19, 30)]
    assert cpu.x[30] == RET_TO
    return True


def _requested(cpu, calls, end, alias, slot, fade=3):
    assert end == RET_TO, 'returns to PLAY_NATIVE\'s caller'
    assert calls == [(alias, slot, fade)]
    assert cpu.x[0] == 0x55, 'request\'s return value'
    assert cpu.sp == SP0
    assert [cpu.x[r] for r in range(19, 30)] == [0x1100 + r
                                                 for r in range(19, 30)]
    return True


@needs_dump
def test_field_cue_in_its_own_field(patched):
    cpu, calls, end = _play(patched, 12)
    assert _requested(cpu, calls, end, 'eo00', 99)


@needs_dump
def test_field_cue_name_forms(patched):
    for name in ('ealin_12', 'field\\ealin_12', 'data\\field\\ealin_12.dat'):
        cpu, calls, end = _play(patched, 12, field=name)
        assert _requested(cpu, calls, end, 'eo00', 99), name


@needs_dump
def test_field_cue_needs_field_track_and_mode(patched):
    for kw, id_ in (({'field': 'field\\ealin_1'}, 12),     # another field
                    ({}, 52),                              # its other track
                    ({'mode': 3}, 12),                     # the world map
                    ({'field': 'field\\las4_2'}, 12)):     # wrong pair
        cpu, calls, end = _play(patched, id_, **kw)
        assert calls == [] and _is_stock(cpu, end, id_), kw
    cpu, calls, end = _play(patched, 52, field='field\\las4_2', fade=0)
    assert _requested(cpu, calls, end, 'eo01', 100, fade=0)


@needs_dump
def test_battle_theme_only_during_the_battle_music_call(patched):
    cpu, calls, end = _play(patched, 33, flag=1, battle_id=527)
    assert _requested(cpu, calls, end, 'eo02', 101)
    # the same battle id outside that call is field state, not battle music
    cpu, calls, end = _play(patched, 33, flag=0, battle_id=527)
    assert calls == [] and _is_stock(cpu, end, 33)
    # inside the call, another battle: no theme, and no FIELD cue either
    cpu, calls, end = _play(patched, 12, flag=1, battle_id=528)
    assert calls == [] and _is_stock(cpu, end, 12)


@needs_dump
def test_shuffle_draws_from_the_counter(patched):
    # [bat] = bat, bat1: counter >> 4, mod 2 -> 0 stock, 1 the variant
    cpu, calls, end = _play(patched, 7, counter=0 << 4)
    assert calls == [] and _is_stock(cpu, end, 7)
    cpu, calls, end = _play(patched, 7, counter=1 << 4)
    assert _requested(cpu, calls, end, 'eo03', 102)
    # [bee] = bee, bee1, bee2: mod 3
    seen = []
    for k in range(3):
        cpu, calls, end = _play(patched, 67, counter=k << 4)
        seen.append(calls[0][0] if calls else 'stock')
    assert seen == ['stock', 'eo04', 'eo05']


@needs_dump
def test_shuffle_keeps_what_is_already_playing(patched):
    cpu, calls, end = _play(patched, 67, current=104, counter=0)
    assert _requested(cpu, calls, end, 'eo05', 104)
    cpu, calls, end = _play(patched, 67, current=67, counter=2 << 4)
    assert calls == [] and _is_stock(cpu, end, 67)


@needs_dump
def test_battle_music_shuffles_when_it_has_no_theme(patched):
    cpu, calls, end = _play(patched, 7, flag=1, battle_id=99, counter=1 << 4)
    assert _requested(cpu, calls, end, 'eo03', 102)


@needs_dump
def test_battle_wrapper_raises_the_flag_for_exactly_that_call(patched):
    ents, report, code = patched
    cpu, _ = _cpu(report)
    seen = {}

    def sound_operation(c):
        seen['flag'] = c.mem.u(report['bss'] + O.BSS_FLAG, 4)
        seen['args'] = [c.x[r] for r in range(0, 9)]
        for r in range(0, 19):
            c.x[r] = E.GARBAGE
        c.x[0] = 0x77
    cpu.native[O.SOUND_OPERATION] = sound_operation
    for r in range(0, 9):
        cpu.x[r] = 0x200 + r
    end = cpu.run(O.BATTLE_MUSIC_CALL, code, start_pc=O.BATTLE_MUSIC_CALL)
    assert end == O.BATTLE_MUSIC_CALL + 4
    assert seen['flag'] == 1
    assert seen['args'] == [0x200 + r for r in range(0, 9)]
    assert cpu.mem.u(report['bss'] + O.BSS_FLAG, 4) == 0
    assert cpu.x[0] == 0x77 and cpu.sp == SP0
    assert [cpu.x[r] for r in range(19, 30)] == [0x1100 + r
                                                 for r in range(19, 30)]


@needs_dump
def test_refuses_a_module_that_is_not_stock(tmp_path, patched):
    ents, _r, _c = patched
    once = str(tmp_path / 'once')
    O.apply_to_nso(MAIN, once, ents)
    with pytest.raises(Exception):
        O.apply_to_nso(once, str(tmp_path / 'twice'), ents)
