"""BUILD 783: battle models keep the game's own lighting.

Hardware 10-22 (Shiva's reveal): with FFNx's intensity, whole parts of the
summon (arm, hand, hair strands) were left out of her light, which parts
changed with the effect's tick, and dark wedges sat on the lit body. In
battle (FF7 _mode word 0xCBF9DC == 2) have() refuses, so lt_vtx and lt_mvfix
take the stock path; in the field nothing changes.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os  # noqa: E402
import struct  # noqa: E402

import pytest  # noqa: E402

import ff7nx_lighting as L  # noqa: E402

U = pytest.importorskip('unicorn')
from unicorn.arm64_const import (UC_ARM64_REG_SP, UC_ARM64_REG_X30,  # noqa: E402
                                 UC_ARM64_REG_X0, UC_ARM64_REG_CPACR_EL1)

CODE, G2H, ORIG, ST, STK, H = 0x100000, 0x200000, 0x200100, 0x300000, 0x400000, 0x10000000
MODE = 0xCBF9DC
LIGHT, BONE, ESP, N, VC, AL, OUT, ESP2, OUTF = (0x1000, 0x2000, 0x8000, 0x3000,
                                                0x3100, 0x3200, 0x3300, 0x9000, 0x3400)


def _movz(rd, imm, hw):
    return 0xD2800000 | (hw << 21) | (imm << 5) | rd


def _movk(rd, imm, hw):
    return 0xF2800000 | (hw << 21) | (imm << 5) | rd


class Rig:
    def __init__(self, mode):
        self.mu = mu = U.Uc(U.UC_ARCH_ARM64, U.UC_MODE_ARM)
        for base, size in ((CODE, 0x10000), (G2H, 0x1000), (ST, 0x4000),
                           (STK - 0x10000, 0x10000), (H, 0x20000),
                           (H + (MODE & ~0xFFF), 0x1000), (0xDE000, 0x1000)):
            mu.mem_map(base, size)
        mu.mem_write(CODE, open(L.BLOB, 'rb').read())
        mu.mem_write(G2H, struct.pack('<4I', _movz(1, H & 0xFFFF, 0),
                                      _movk(1, (H >> 16) & 0xFFFF, 1),
                                      0x8B204020, 0xD65F03C0))
        mu.mem_write(ORIG, struct.pack('<2I', _movz(0, 0x77, 0), 0xD65F03C0))
        mu.mem_write(H + MODE, struct.pack('<H', mode))
        self.ent = L.entries()

    def w32(self, a, v):
        self.mu.mem_write(H + a, struct.pack('<I', v))

    def wf(self, a, vals):
        self.mu.mem_write(H + a, struct.pack('<%df' % len(vals), *vals))

    def run(self, fn, args):
        mu = self.mu
        mu.reg_write(UC_ARM64_REG_SP, STK - 0x100)
        mu.reg_write(UC_ARM64_REG_X30, 0xDEAD0)
        mu.reg_write(UC_ARM64_REG_CPACR_EL1, 0x300000)
        for i, a in enumerate(args):
            mu.reg_write(UC_ARM64_REG_X0 + i, a)
        mu.emu_start(CODE + self.ent[fn], 0xDEAD0, count=100000)
        return mu.reg_read(UC_ARM64_REG_X0)


def _setup(r):
    r.w32(LIGHT, 9)                                   # mode 1, directional
    for i in range(3):
        r.wf(LIGHT + 0x38 + 16 * i, [1.0 if j == i else 0.0 for j in range(3)])
        r.wf(LIGHT + 0x78 + 16 * i, [0.5, 0.4, 0.3])
        r.wf(BONE + 16 * i, [1.0 if j == i else 0.0 for j in range(3)])
        r.wf(LIGHT + 0xF8 + 16 * i, [0.1, 0.2, 0.3, 0.5])
    for off, v in ((4, LIGHT + 0xB8), (8, BONE), (12, LIGHT + 0x24),
                   (16, LIGHT + 0xF8)):
        r.w32(ESP + off, v)
    r.run('lt_setup', [ST, ESP, G2H])
    r.wf(N, [0.0, 0.0, 1.0])
    r.wf(VC, [200.0, 200.0, 200.0])
    r.mu.mem_write(H + AL, bytes([1, 2, 3, 0x80]))


@pytest.mark.parametrize('mode,ours', [(1, True), (3, True), (2, False)])
def test_783_vertex_lighting_is_ours_outside_battle_only(mode, ours):
    r = Rig(mode)
    _setup(r)
    got = r.run('lt_vtx', [LIGHT + 0xF8, N, VC, AL, OUT, ST, G2H, ORIG])
    assert (got == OUT) == ours and (got == 0x77) == (not ours)


@pytest.mark.parametrize('mode,ours', [(1, True), (2, False)])
def test_783_scripted_colour_fix_is_ours_outside_battle_only(mode, ours):
    r = Rig(mode)
    _setup(r)
    for off, v in ((4, LIGHT + 0xF8), (8, N), (12, OUTF)):
        r.w32(ESP2 + off, v)
    r.wf(OUTF, [9.0, 9.0, 9.0])
    r.run('lt_mvfix', [ST, ESP2, G2H])
    out = struct.unpack('<3f', bytes(r.mu.mem_read(H + OUTF, 12)))
    assert (out != (9.0, 9.0, 9.0)) == ours
