"""BUILD 615: ealin_2's clock change happens before the fade-in, not after.

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

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import echo_s_flevel as E  # noqa: E402
import lgp  # noqa: E402

ECHO = os.path.expanduser('~/mnt/7th_heaven_nx/cache/Echo-s/Echo-S/flevel.lgp')
if not os.path.isdir(ECHO):
    ECHO = os.path.join(HERE, 'cache', 'Echo-s', 'Echo-S', 'flevel.lgp')

SET_HOURS = bytes.fromhex('80 10 0f 02')
FADE_IN = bytes.fromhex('6b 00 00 00 00 00 02 01 ff')
WSIZE = bytes.fromhex('50 01 18 00 10 00 b0 00 29 00')
AFTER = bytes.fromhex('03 01 47')          # reqew 01 47, the old skip target


def _section1(name):
    raw = open(os.path.join(ECHO, name), 'rb').read()
    try:
        return lgp.split_sections(raw)[0]
    except Exception:                                         # noqa: BLE001
        return lgp.split_sections(lgp.lzs_decompress(raw[4:]))[0]


def _routine_of(section, at):
    for start, end in E._routine_blocks(section):
        if start <= at < end:
            return start, end
    raise AssertionError('offset 0x%X is in no routine' % at)


def test_the_clock_moves_ahead_of_the_fade_in():
    if not os.path.isdir(ECHO):
        return
    s = _section1('ealin_2')
    out, n = E.hoist_time_change_behind_fade(s, 'ealin_2')
    assert n == 1 and len(out) == len(s)
    assert s.index(SET_HOURS) > s.index(FADE_IN), 'Echo-S: clock after fade'
    assert out.index(SET_HOURS) < out.index(FADE_IN), 'clock still late'
    assert out.count(SET_HOURS) == 1
    # only the moved span differs
    diff = [i for i in range(len(s)) if s[i] != out[i]]
    span = len(E.TIME_HOIST_SCENE) + len(E.TIME_HOIST_CLOCK)
    assert max(diff) - min(diff) < span


def test_every_instruction_and_jump_still_lines_up():
    if not os.path.isdir(ECHO):
        return
    s = _section1('ealin_2')
    out, _n = E.hoist_time_change_behind_fade(s, 'ealin_2')
    start, end = _routine_of(out, out.index(SET_HOURS))
    before, trunc_a = E._decode_block(s, *_routine_of(s, s.index(SET_HOURS)))
    after, trunc_b = E._decode_block(out, start, end)
    assert not trunc_a and not trunc_b
    assert len(before) == len(after), 'the routine decodes differently'
    offs = {o for o, _op, _sz in after}
    moved_at = out.index(E.TIME_HOIST_CLOCK)
    # the gating `if story == 0xAA` (IFUW 0x16 ... jj) still jumps past it
    gate = moved_at - 8
    assert out[gate] == 0x16 and out[gate + 4] == 0xAA
    assert gate + 7 + out[gate + 7] > moved_at + span_len()
    # the dialogue branch: `if bank3[0x41] & 2` -> wsize, skip -> reqew 01 47
    if_at = out.index(bytes.fromhex('14 30 41 02 06 06'), moved_at)
    assert if_at + 5 + out[if_at + 5] == out.index(WSIZE, moved_at)
    skip_at = if_at + 6 + 3
    assert out[skip_at] == 0x10
    target = skip_at + 1 + out[skip_at + 1]
    assert target in offs
    assert out[target:target + 3] == AFTER
    # and in Echo-S's own bytes the same skip reached the same instruction
    s_skip = s.index(bytes.fromhex('24 20 00 10 16')) + 3
    assert s[s_skip + 1 + s[s_skip + 1]:][:3] == AFTER


def span_len():
    return len(E.TIME_HOIST_SCENE) + len(E.TIME_HOIST_CLOCK)


def test_nothing_else_in_the_mod_changes():
    if not os.path.isdir(ECHO):
        return
    for name in sorted(os.listdir(ECHO)):
        path = os.path.join(ECHO, name)
        if not os.path.isfile(path):
            continue
        try:
            s = _section1(name)
        except Exception:                                     # noqa: BLE001
            continue
        out, n = E.hoist_time_change_behind_fade(s, name)
        if name == 'ealin_2':
            assert n == 1
        else:
            assert (out, n) == (s, 0), name


def test_already_hoisted_bytes_are_left_alone():
    if not os.path.isdir(ECHO):
        return
    s = _section1('ealin_2')
    once, _n = E.hoist_time_change_behind_fade(s, 'ealin_2')
    assert E.hoist_time_change_behind_fade(once, 'ealin_2') == (once, 0)
