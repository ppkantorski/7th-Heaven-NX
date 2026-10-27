"""Executable contracts for Kujata's two projected ground layers.

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
import shutil
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import arm64emu as E  # noqa: E402
import ff7nx_terrainfx as T  # noqa: E402
import nxmap  # noqa: E402

STOCK = ROOT / 'game_data_files' / 'exefs' / 'main'
pytestmark = pytest.mark.skipif(not STOCK.exists(), reason='no stock main')


def word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def test_stock_anchors_and_projection_hooks_are_exact():
    m = nxmap.Main(str(STOCK))
    for va, expected in T.ANCHORS.items():
        assert word(m.img, va) == expected, hex(va)
    assert word(m.img, T.RETIRED_SECOND_SUBMIT) == T.RETIRED_SECOND_SUBMIT_STOCK
    assert word(m.img, T.RETIRED_KOTR_BLEND) == T.RETIRED_KOTR_BLEND_STOCK


@pytest.mark.parametrize('offsets', [T.GRID_X_OFFSETS, T.MESH_X_OFFSETS])
def test_scale_cave_changes_only_the_projected_x_components(offsets):
    base = 0x02000000
    body = T._scale_body(offsets, lambda i: base + i * 4)
    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=0x700000000, paged=True)
    host = cpu.guest_to_host(T.VERTEX_BANK)
    initial = {0: -12.0, 16: 30.0, 32: -47.5, 48: 101.25}
    for off, value in initial.items():
        mem.write(host + off, struct.pack('<f', value))
        mem.write(host + off + 4, struct.pack('<f', value + 0.25))
    cpu.sp = 0x800000400
    cpu.x[29], cpu.x[30] = 0x2919, 0x30AA
    cpu.n, cpu.z, cpu.c, cpu.v = 1, 0, 1, 0
    before_flags = (cpu.n, cpu.z, cpu.c, cpu.v)
    assert cpu.run(base, body) == base + len(body) * 4
    for off, value in initial.items():
        got = struct.unpack('<f', mem.read(host + off, 4))[0]
        want = value * (4.0 / 3.0) if off in offsets else value
        assert got == pytest.approx(want, rel=1e-6)
        assert struct.unpack('<f', mem.read(host + off + 4, 4))[0] == value + 0.25
    assert cpu.translate_calls == 1
    assert cpu.sp == 0x800000400
    assert (cpu.x[29], cpu.x[30]) == (0x2919, 0x30AA)
    assert (cpu.n, cpu.z, cpu.c, cpu.v) == before_flags


def test_apply_is_idempotent_cleanup_on_a_stock_module(tmp_path):
    target = tmp_path / 'main'
    shutil.copy(STOCK, target)
    original = target.read_bytes()
    assert T.apply_all(target, log=lambda *_: None) == 0
    assert T.apply_all(target, log=lambda *_: None) == 0
    assert T.apply_all(target, revert=True, log=lambda *_: None) == 0
    assert target.read_bytes() == original


def test_apply_installs_both_hooks_and_revert_returns_them(tmp_path):
    """
    Build 267 removed this and the animated field stopped reaching one side
    of the frame; build 266 had it and reached both. The two builds together
    are what un-retired it, so the install path is a contract now, not a
    dormant branch.
    """
    target = tmp_path / 'main'
    shutil.copy(STOCK, target)
    assert T.apply_all(target, log=lambda *_: None) == 0
    img = nxmap.Main(str(target)).img
    for hook, stock in ((T.GRID_HOOK, T.GRID_HOOK_STOCK),
                        (T.MESH_HOOK, T.MESH_HOOK_STOCK)):
        got = word(img, hook)
        assert got != stock, hex(hook)
        assert (got & 0xFC000000) == 0x14000000, hex(hook)   # an unconditional b
    assert T.apply_all(target, revert=True, log=lambda *_: None) == 0
    assert target.read_bytes() == STOCK.read_bytes()


def test_the_gate_follows_widescreen(monkeypatch):
    """4/3 exists only to cancel the wide shader's 0.75. At 4:3 there is no
    0.75 and this would stretch the floor 33% too wide."""
    monkeypatch.setenv(T.TERRAIN_FX_ENV, '0')
    assert T.enabled() is False
    monkeypatch.setenv(T.TERRAIN_FX_ENV, '1')
    assert T.enabled() is True
