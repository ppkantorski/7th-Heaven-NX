"""BUILD 672: nivl_e1's shooting star in truecolor, its fade as levels.

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

import pytest

HERE = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, HERE)
import ff7nx_starfade as S   # noqa: E402

VAN = os.path.join(HERE, 'dump/romfs/ff7/workingdir/data/field/flevel.lgp')
B, VAR, P = 41, 8, 0xFF


def _run(code, start, var, budget=8, ticks=60, pre=()):
    """A tiny field interpreter for the ops the star routine uses, with the
    x86 loop's budget (0x60CD77: 8 opcodes, stop early on a yielding op).
    Returns the set of (param, state) shown after each tick."""
    on, v, pc, out = set(pre), dict(var), start, []
    wait = 0
    for _t in range(ticks):
        n = budget
        while n:
            op = code[pc]
            if op == 0x00:                                   # RET
                out.append(frozenset(on))
                return out
            y = 0
            if op == 0x24:                                   # WAIT f
                if wait == 0:
                    wait = code[pc + 1]
                wait -= 1
                if wait:
                    y = 1
                else:
                    pc += 3
            elif op in (0xE0, 0xE1):                         # BGON/BGOFF
                bk, a, b = code[pc + 1], code[pc + 2], code[pc + 3]
                prm = v.get(a, 0) if bk >> 4 else a
                st = v.get(b, 0) if bk & 15 else b
                (on.add if op == 0xE0 else on.discard)((prm, st))
                pc += 4
            elif op == 0x80:                                 # SETBYTE
                bk, a, b = code[pc + 1], code[pc + 2], code[pc + 3]
                v[a] = v.get(b, 0) if bk & 15 else b
                pc += 4
            elif op == 0x78:                                 # MINUS
                v[code[pc + 2]] -= code[pc + 3]
                pc += 4
            elif op == 0x14:                                 # IFUB > / else
                a, c, cmp, j = code[pc + 2], code[pc + 3], code[pc + 4], code[pc + 5]
                assert cmp == 2
                pc = pc + 6 if v[a] > c else pc + 5 + j
            elif op == 0x10:                                 # JMPF
                pc += code[pc + 1] + 1
            elif op == 0x12:                                 # JMPB (yields)
                pc -= code[pc + 1]
                y = 1
            else:
                raise AssertionError('op %02X at %d' % (op, pc))
            n -= 1
            if y:
                break
        out.append(frozenset(on))
    raise AssertionError('no end')


def _vanilla_tail():
    """WAIT 2; BGOFF 2,2; then the star's held frame + fade."""
    return bytes.fromhex('240200' 'e1000202')


def test_new_bytes_keep_length_and_jumps():
    new = S.new_bytes(B, VAR, P, 23)
    assert len(new) == len(S.OLD) == 50
    # the IF's else and the BACK land where vanilla's did
    assert new[13] == 0x14 and new[18] == 0x1B and new[43:45] == b'\x12\x1e'
    assert new[45:49] == bytes((0xE1, 0x50, P, 0x00))


def test_fade_runs_every_level_one_per_tick_with_no_gap():
    n = 23
    code = _vanilla_tail() + S.new_bytes(B, VAR, P, n)
    shown = _run(code, 0, {P: 0x33}, pre={(2, 2)})
    lv = [sorted(s) for s in shown]
    # tick 0: the WAIT; tick 1: flight frame off AND level 21 on (no gap)
    assert lv[0] == [(2, 2)]
    assert lv[1] == [(B + 21, 0)]
    # then one level per tick, 21 -> 0 (v 63 -> 0 by 3: vanilla's 22 steps
    # at vanilla's pace -- vanilla's first pass spills over the budget too:
    # tick 1 the held frame at the full palette, tick 2 at 63, tick 3 at 60)
    seq = [s[0][0] - B for s in lv[1:] if s]
    assert seq == [21] + list(range(21, -1, -1))
    assert all(len(s) == 1 for s in lv[1:-1] if s)
    assert lv[-1] == []                                  # BGOFF at the end


def test_vanilla_has_the_routine_once_and_it_patches():
    if not os.path.exists(VAN):
        pytest.skip('no vanilla flevel')
    import lgp
    import ff7nx_daynight as D
    import echo_s_flevel as ES
    _i, rd = D._stream_index(VAN)
    s0 = bytes(lgp.split_sections(lgp.lzs_decompress(rd('nivl_e1')[4:]))[0])
    new = S.patch_script(s0, B, VAR, P, 23)
    assert len(new) == len(s0)
    diff = [i for i in range(len(s0)) if s0[i] != new[i]]
    at = s0.find(S.OLD)
    assert diff and at <= diff[0] and diff[-1] < at + len(S.OLD)
    for a, b in ES._routine_blocks(new):
        assert not ES._decode_block(new, a, b)[1]


def test_palstates_filters_default_off():
    import inspect
    import ff7nx_palstates as PS
    sig = inspect.signature(PS._targets)
    assert sig.parameters['bits'].default is None
    assert sig.parameters['skip_other'].default is False
    assert inspect.signature(PS._vanilla_cells).parameters[
        'with_state'].default is False


def test_version_is_in_the_flevel_key():
    src = open(nxpaths.src_file('build.py')).read()
    assert "('starfade', ff7nx_starfade.VERSION," in src and S.VERSION == 672
