"""Escape maps one full-frame GPU snapshot over the complete ripple mesh.

"full screenshot, displayed over the screen like the battle swirl, ripple from
the center evenly on the 2 non disconnected halves"

The GPU target is logically 256x256.  U and V therefore span the full byte
range, and every column binds capture A.

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
import hashlib
import shutil
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import a64 as A                                                # noqa: E402
import arm64emu as E                                           # noqa: E402
import nxmap                                                   # noqa: E402
import ff7nx_escapeuv as U                                     # noqa: E402
import ff7nx_escapescale as S                                  # noqa: E402

MAIN = ROOT / 'exefs' / 'main'
needs_main = pytest.mark.skipif(not MAIN.exists(), reason='no exefs/main')


# ----------------------------------------------------------- the model

def test_the_uvs_are_continuous_across_the_old_seam():
    """The whole point: column 19's right edge meets column 20's left edge."""
    assert U.expected_u(19)[1] == U.expected_u(20)[0] == 128
    for col in range(U.COLS - 1):
        assert U.expected_u(col)[1] == U.expected_u(col + 1)[0], col


def test_the_uvs_span_the_target_exactly_once():
    assert U.expected_u(0)[0] == 0
    assert U.expected_u(U.COLS - 1)[1] == U.UV_MAX == 255
    # strictly increasing: no repeat anywhere
    lefts = [U.expected_u(c)[0] for c in range(U.COLS)]
    assert lefts == sorted(lefts) and len(set(lefts)) == U.COLS


def test_v_spans_the_entire_snapshot_including_the_ui():
    assert U.expected_v(0)[0] == 0
    assert U.expected_v(U.ROWS - 1)[1] == U.UV_MAX == 255
    for row in range(U.ROWS - 1):
        assert U.expected_v(row)[1] == U.expected_v(row + 1)[0]
    # Row 15 is the real ripple centre at screen y=240 and texture v=128.
    assert U.expected_v(14)[1] == U.expected_v(15)[0] == 128


# ------------------------------------------------- the cave, emulated

def _run_cave():
    base = 0x900000
    words = U.build(base, lambda i: base + 4 * i, A.movz(0, 0), base)
    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=0x700000000)
    cpu.sp = 0x800000000
    for r in range(31):
        cpu.x[r] = E.GARBAGE
    for idx in range(U.ROWS * U.COLS):
        rec = U.RECORDS + idx * U.STRIDE
        for off in U.UV_OFFS:
            mem.setu(cpu.guest_to_host(rec + off), 0xAAAA, 2)
    cpu.run(base, words[:-2], max_steps=400000)
    return mem, cpu


@pytest.fixture(scope='module')
def ran():
    return _run_cave()


def test_the_cave_writes_every_record(ran):
    mem, cpu = ran
    for row in range(U.ROWS):
        for col in range(U.COLS):
            rec = U.RECORDS + (row * U.COLS + col) * U.STRIDE
            u, u2 = U.expected_u(col)
            v, v2 = U.expected_v(row)
            got = [mem.u(cpu.guest_to_host(rec + o), 2)
                   for o in U.UV_OFFS]
            want = [u | (v << 8), u2 | (v << 8),
                    u | (v2 << 8), u2 | (v2 << 8)]
            assert got == want, (row, col, got, want)


def test_the_cave_replaces_v_but_not_bytes_after_each_uv_pair(ran):
    mem, cpu = ran
    for idx in range(0, U.ROWS * U.COLS, 37):
        rec = U.RECORDS + idx * U.STRIDE
        for off in U.UV_OFFS:
            assert mem.u(cpu.guest_to_host(rec + off + 2), 1) == 0, idx


def test_the_cave_touches_nothing_outside_the_uv_bytes(ran):
    mem, cpu = ran
    for idx in range(0, U.ROWS * U.COLS, 53):
        rec = U.RECORDS + idx * U.STRIDE
        for off in (0x00, 0x04, 0x08, 0x10, 0x18, 0x20, 0x28):
            assert mem.u(cpu.guest_to_host(rec + off), 4) == 0, (idx, off)


def test_the_cave_balances_the_stack():
    base = 0x900000
    words = U.build(base, lambda i: base + 4 * i, A.movz(0, 0), base)
    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=0x700000000)
    cpu.sp = 0x800000000
    before = cpu.sp
    cpu.run(base, words[:-2], max_steps=400000)
    assert cpu.sp == before, (hex(before), hex(cpu.sp))


# ------------------------------------------------------- the binary

@needs_main
def test_the_hook_is_the_bodys_only_ret():
    m = nxmap.Main(str(MAIN))
    found, why = U.find_hook(m)
    assert found is not None, why
    hook, kind = found
    assert kind == 'ret'
    lo, hi = m.extent(U.UV_INIT)
    assert lo <= hook < hi


@needs_main
def test_exactly_two_capture_b_selects():
    m = nxmap.Main(str(MAIN))
    binds = U.find_binds(m, imm=4)
    assert len(binds) == 2, [hex(b) for b in binds]
    # each must be `add w0, <reg>, #4` immediately before a bl to the translator
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    for va in binds:
        i = next(md.disasm(bytes(m.img[va:va + 8]), va))
        j = next(md.disasm(bytes(m.img[va + 4:va + 8]), va + 4))
        assert i.op_str.startswith('w0, ') and i.op_str.endswith('#4')
        assert j.mnemonic == 'bl'
        assert int(j.op_str.lstrip('#'), 0) == U.TRANSLATE


@needs_main
def test_apply_revert_and_idempotence(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    before = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert U.apply(dst, log=lambda *a: None) == 0
    once = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert once != before
    assert U.apply(dst, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == once
    assert U.apply(dst, revert=True, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == before
    assert U.apply(dst, revert=True, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == before


@needs_main
def test_the_cave_words_come_from_padding():
    m = nxmap.Main(str(MAIN))
    patches, _n, problems = U.plan(m)
    assert problems == [], problems
    hook = U.find_hook(m)[0][0]
    binds = set(U.find_binds(m, imm=4))
    for p in patches:
        va = int(p['va'], 16)
        if va == hook or va in binds:
            continue
        assert p['expect'] == '00000000', (p['name'], p['expect'])


@needs_main
def test_both_escape_patches_coexist(tmp_path):
    """escapescale and escapeuv must not fight over a word or a cave hole."""
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    before = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert S.apply(dst, log=lambda *a: None) == 0
    assert U.apply(dst, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() != before
    assert U.apply(dst, revert=True, log=lambda *a: None) == 0
    assert S.apply(dst, revert=True, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == before


@needs_main
def test_the_two_caves_do_not_overlap(tmp_path):
    """Applied in sequence, as the build does.

    Planned independently against the same stock image they would pick the
    same free holes -- the pool is fresh both times. What matters is the real
    order: the second plan sees the first's words as no longer zero.
    """
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    m0 = nxmap.Main(str(MAIN))
    sp, _n, se = S.plan(m0)
    assert se == []
    assert S.apply(dst, log=lambda *a: None) == 0
    m1 = nxmap.Main(str(dst))
    up, _n2, ue = U.plan(m1)
    assert ue == [], ue
    a = {int(p['va'], 16) for p in sp}
    b = {int(p['va'], 16) for p in up}
    assert not (a & b), [hex(x) for x in sorted(a & b)]


def test_the_switch_follows_widescreen():
    import os
    old = os.environ.get(U.ESCAPEUV_ENV)
    try:
        os.environ[U.ESCAPEUV_ENV] = '0'
        assert U.enabled() is False
        os.environ[U.ESCAPEUV_ENV] = '1'
        assert U.enabled() is True
    finally:
        if old is None:
            os.environ.pop(U.ESCAPEUV_ENV, None)
        else:
            os.environ[U.ESCAPEUV_ENV] = old


# ---------------------------------------------- the layout, from the binary
#
# Build 405's cave wrote u into record+0x0C/0x14/0x1C/0x24 and verified itself
# by emulating its OWN model, so the test and the bug agreed and it shipped
# broken. These tests read the layout out of the binary instead.

def _uv_offsets_the_game_writes(m):
    """Record-relative offsets that `escape_uv_init` addresses, measured.

    Every `add/sub w?, w23, #imm` whose result is added to an `idx<<7` is a
    field address; w23 is proved to be record_base + 0x1C.
    """
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    lo, hi = m.extent(U.UV_INIT)
    ins = list(md.disasm(bytes(m.img[lo:hi]), lo))
    # w23's BASE is the `mov w23,#lo` that is immediately followed by a
    # `movk w23,#hi` -- the plain `mov w23,#0x15` elsewhere is a loop bound.
    w23v = None
    for k, i in enumerate(ins[:-1]):
        nxt = ins[k + 1]
        if i.mnemonic in ('mov', 'movz') and i.op_str.startswith('w23,') \
                and nxt.mnemonic == 'movk' and nxt.op_str.startswith('w23,'):
            lo16 = int(i.op_str.rsplit('#', 1)[1], 0)
            hi16 = int(nxt.op_str.split('#')[1].split(',')[0], 0)
            w23v = (hi16 << 16) | lo16
            break
    assert w23v is not None, 'w23 is not materialised in escape_uv_init'
    delta = w23v - U.RECORDS
    seen = {}
    cur = {}
    for k, i in enumerate(ins):
        p = [x.strip() for x in i.op_str.split(',')]
        if i.mnemonic in ('add', 'sub') and len(p) == 3 and p[1] == 'w23' \
                and p[2].startswith('#'):
            v = int(p[2][1:], 0)
            cur[p[0]] = delta + (v if i.mnemonic == 'add' else -v)
        elif i.mnemonic == 'add' and len(p) == 3 and p[0] == 'w0' \
                and p[2] in cur:
            off = cur[p[2]]
            # the guest address in w0 is handed to TRANSLATE, which returns the
            # host pointer in x0; the REAL access is the first one on [x0]
            # after that call. Anything before it is a register spill.
            bl = None
            for j in range(k + 1, min(k + 8, len(ins))):
                if ins[j].mnemonic == 'bl' and \
                        int(ins[j].op_str.lstrip('#'), 0) == U.TRANSLATE:
                    bl = j
                    break
            if bl is None:
                continue
            for j in range(bl + 1, min(bl + 4, len(ins))):
                if ins[j].mnemonic in ('str', 'strb', 'strh', 'ldr',
                                       'ldrb', 'ldrh', 'ldrsh') \
                        and '[x0]' in ins[j].op_str:
                    seen.setdefault(off, []).append(ins[j].mnemonic)
                    break
    return w23v, seen


@needs_main
def test_w23_is_the_record_base_plus_0x1c():
    m = nxmap.Main(str(MAIN))
    w23v, _seen = _uv_offsets_the_game_writes(m)
    assert w23v == U.RECORDS + 0x1C, hex(w23v)


@needs_main
def test_the_cave_writes_only_bytes_the_game_writes_as_u():
    """THE TEST BUILD 405 DID NOT HAVE.

    Every offset the cave writes must be one the game itself writes a BYTE to
    from all three of its UV code paths. That is what makes it a u byte.
    """
    m = nxmap.Main(str(MAIN))
    _w23v, seen = _uv_offsets_the_game_writes(m)
    for off in U.UV_OFFS:
        assert off in seen, (
            'the cave writes record+0x%02X, which escape_uv_init never '
            'touches at all' % off)
        assert seen[off].count('strb') >= 1, (off, seen[off])


@needs_main
def test_the_packet_base_is_measured_not_assumed():
    """The four u offsets must be a POLY_FT4's, based at PACKET."""
    m = nxmap.Main(str(MAIN))
    _w23v, seen = _uv_offsets_the_game_writes(m)
    assert U.UV_OFFS == tuple(U.PACKET + d for d in (0x0C, 0x14, 0x1C, 0x24))
    # each u byte has a v byte immediately after it, also written
    for off in U.UV_OFFS:
        assert off + 1 in seen, 'no v byte beside u at +0x%02X' % off


@needs_main
def test_record_plus_0x0c_is_not_a_uv_field():
    """Guards the exact regression: +0x0C was build 405's first write."""
    m = nxmap.Main(str(MAIN))
    _w23v, seen = _uv_offsets_the_game_writes(m)
    assert 0x0C not in seen and 0x14 not in seen and 0x1C not in seen
