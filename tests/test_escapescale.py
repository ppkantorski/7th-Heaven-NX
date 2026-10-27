"""BUILD 404 -- Escape's ripple is the battle swirl's squeeze.

The 2D overlay path uses ortho(0, 640, 480, 0) and the vertex shader scales x
by WS_SCALE = 0.75, so a 640-unit grid lands in the central 4:3 while the
target it samples holds the full 16:9 frame. ff7nx_swirlscale fixed this for
the swirl; this is the same transform at Escape's one x store.

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

import nxmap                                                   # noqa: E402
import ff7nx_escapescale as E                                  # noqa: E402
import ff7nx_swirlscale as S                                   # noqa: E402

MAIN = ROOT / 'exefs' / 'main'
needs_main = pytest.mark.skipif(not MAIN.exists(), reason='no exefs/main')


def test_the_transform_is_the_swirls_transform():
    """Same constants, same arithmetic -- not a second invention."""
    assert (E.NUM, E.DEN, E.CENTRE) == (S.NUM, S.DEN, S.CENTRE)
    assert E.body('w8', 'w9') == S.body('w8', 'w9')


@pytest.mark.parametrize('x,want', [(0, -106), (320, 320), (640, 746)])
def test_the_corners_land_on_the_16_9_frame(x, want):
    assert E.expected(x) == want


def test_the_transform_is_monotonic_and_centred():
    xs = list(range(0, 641, 16))
    ys = [E.expected(x) for x in xs]
    assert ys == sorted(ys)
    assert E.expected(320) == 320, 'the centre must be a fixed point'
    left, right = E.expected(0), E.expected(640)
    assert abs((320 - left) - (right - 320)) <= 1, (left, right)


@needs_main
def test_the_hook_is_found_and_is_unique():
    m = nxmap.Main(str(MAIN))
    found, why = E.find_hook(m)
    assert found is not None, why
    hook, val, scratch = found
    lo, hi = m.extent(E.ESCAPE_MESH)
    assert lo <= hook < hi
    assert val == 'w8' and scratch == 'w9', (val, scratch)


@needs_main
def test_the_displaced_instruction_is_the_x_store():
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    m = nxmap.Main(str(MAIN))
    hook, val, _s = E.find_hook(m)[0]
    w = struct.unpack_from('<I', m.img, hook)[0]
    i = next(md.disasm(struct.pack('<I', w), hook))
    assert i.mnemonic == 'str', i.mnemonic
    assert i.op_str.startswith(val + ','), i.op_str


@needs_main
def test_the_scratch_register_is_dead():
    m = nxmap.Main(str(MAIN))
    hook, _v, scratch = E.find_hook(m)[0]
    assert E.scratch_is_dead(m, hook, scratch)


@needs_main
def test_rect_y_is_reached_differently_so_the_anchor_cannot_collide():
    """Y must not be scaled: only the horizontal axis is squeezed."""
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    m = nxmap.Main(str(MAIN))
    lo, hi = m.extent(E.ESCAPE_MESH)
    ins = list(md.disasm(bytes(m.img[lo:hi]), lo))
    rx = E._hoisted_reg(ins, E.RECT_X)
    assert rx is not None
    xs = [i for i in ins if i.mnemonic == 'mov' and i.op_str == 'w0, %s' % rx]
    ys = [i for i in ins if i.mnemonic == 'add' and i.op_str == 'w0, %s, #4' % rx]
    assert len(xs) == 1, 'rect.x site is not unique (%d)' % len(xs)
    assert len(ys) >= 1, 'rect.y is not reached as %s + 4' % rx


@needs_main
def test_apply_revert_and_idempotence(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    before = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert E.apply(dst, log=lambda *a: None) == 0
    once = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert once != before
    assert E.apply(dst, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == once, 'not idempotent'
    assert E.apply(dst, revert=True, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == before
    assert E.apply(dst, revert=True, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == before


@needs_main
def test_the_installed_cave_computes_the_model(tmp_path):
    """Run the cave's own words and compare against expected()."""
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    assert E.apply(dst, log=lambda *a: None) == 0
    m = nxmap.Main(str(dst))
    hook, _v, _s = E.find_hook(m)[0]
    cave, why = E.walk_cave(m, hook)
    assert cave is not None, why
    words = [(va, w) for va, w in sorted(cave.items())]

    def run(x):
        r = {8: x, 9: 0}
        for va, w in words:
            i = next(md.disasm(struct.pack('<I', w), va))
            mn = i.mnemonic
            ops = [o.strip() for o in i.op_str.split(',')]
            if mn == 'lsl':
                r[int(ops[0][1:])] = r[int(ops[1][1:])] << int(ops[2].lstrip('#'), 0)
            elif mn == 'sub' and ops[2].startswith('#'):
                r[int(ops[0][1:])] = r[int(ops[1][1:])] - int(ops[2].lstrip('#'), 0)
            elif mn in ('mov', 'movz') and ops[1].startswith('#'):
                r[int(ops[0][1:])] = int(ops[1].lstrip('#'), 0)
            elif mn == 'sdiv':
                a, b = r[int(ops[1][1:])], r[int(ops[2][1:])]
                q = abs(a) // abs(b)
                r[int(ops[0][1:])] = q if (a < 0) == (b < 0) else -q
        return r[8]

    for x in range(0, 641, 40):
        assert run(x) == E.expected(x), (x, run(x), E.expected(x))
    assert run(0) <= -106 and run(640) >= 746, (run(0), run(640))


@needs_main
def test_the_cave_words_come_from_padding(tmp_path):
    """Every cave word must have been zero before the patch."""
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    stock = nxmap.Main(str(MAIN))
    patches, _n, problems = E.plan(stock)
    assert problems == [], problems
    hook = E.find_hook(stock)[0][0]
    for p in patches:
        va = int(p['va'], 16)
        if va == hook:
            continue
        assert p['expect'] == '00000000', (p['name'], p['expect'])
