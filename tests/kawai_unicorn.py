"""677: kw_tint (del3's KAWAI 9 vertex tint, faded) on the real blob.
Run from native/: python3 ../tests/kawai_unicorn.py [blob entry]

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
import struct, sys, unicorn as U
from unicorn.arm64_const import *
blob = open(sys.argv[1] if len(sys.argv) > 1 else 'lighting.bin', 'rb').read()
ent = {l.split()[0]: int(l.split()[1], 16) for l in open(sys.argv[2] if len(sys.argv) > 2 else 'lighting.entry')}
CODE, G2H, ST, STK, H = 0x100000, 0x200000, 0x300000, 0x400000, 0x10000000
def movz(rd, i, hw): return 0xD2800000 | (hw << 21) | (i << 5) | rd
def movk(rd, i, hw): return 0xF2800000 | (hw << 21) | (i << 5) | rd
g2h = [movz(1, H & 0xFFFF, 0), movk(1, (H >> 16) & 0xFFFF, 1), 0x8B204020, 0xD65F03C0]
MODE, ESP, EBP = 0xCFFAA8, 0x8000, 0x8200
def run(mode, s, a, v, rgb):
    mu = U.Uc(U.UC_ARCH_ARM64, U.UC_MODE_ARM)
    for base, size in ((CODE, 0x10000), (G2H, 0x1000), (ST, 0x4000), (STK - 0x10000, 0x10000),
                       (H, 0x10000), (H + 0xCFF000, 0x1000), (0xDE000, 0x1000)):
        mu.mem_map(base, size)
    mu.mem_write(CODE, blob); mu.mem_write(G2H, struct.pack('<4I', *g2h))
    mu.mem_write(H + MODE, struct.pack('<I', mode))
    r, g, b = rgb
    mu.mem_write(H + ESP + 4, struct.pack('<3i2I', r << 16, g << 8, b, 0x111, 0x222))
    mu.mem_write(H + EBP + 0x18, struct.pack('<h', s))
    mu.mem_write(H + EBP - 0xA0, struct.pack('<f', a)); mu.mem_write(H + EBP - 0x98, struct.pack('<f', v))
    mu.reg_write(UC_ARM64_REG_CPACR_EL1, 0x300000)
    mu.reg_write(UC_ARM64_REG_SP, STK - 0x100); mu.reg_write(UC_ARM64_REG_X30, 0xDEAD0)
    for i, x in enumerate((ST, ESP, EBP, G2H)): mu.reg_write(UC_ARM64_REG_X0 + i, x)
    mu.emu_start(CODE + ent['kw_tint'], 0xDEAD0, count=10000)
    R, G, B, src, dst = struct.unpack('<3i2I', mu.mem_read(H + ESP + 4, 20))
    assert (src, dst) == (0x111, 0x222)
    return (R >> 16, G >> 8, B)
C = (128, 200, 255)
off = len(sys.argv) > 1
# del3 (mode 1): line = 40 * 1.85 = 74, band = 0.12 * 74 = 8.88
for v, want in ((40, (0, 0, 0)), (69.5, (0, 0, 0)), (90, C), (79, C)):
    got = run(1, -40, 1.85, v, C); exp = (0, 0, 0) if off else want
    assert got == exp, (v, got, exp); print('del3 v=%5.1f ->' % v, got)
if not off:
    mid = run(1, -40, 1.85, 74.0, C); assert mid == (64, 100, 127), mid; print('del3 v= 74.0 (the line) ->', mid)
    prev = (-1, -1, -1)
    for i in range(41):
        v = 69.5 + 9.0 * i / 40
        g = run(1, -40, 1.85, v, C); assert all(x >= p for x, p in zip(g, prev)), (v, g, prev); prev = g
    print('del3: monotonic fade across the band 69.5..78.5')
    assert run(1, -40, 1.85, float('nan'), C) == (0, 0, 0)
# every other mode: the stock test, colour or nothing
for mode in (2, 3, 6):
    assert run(mode, -40, 1.5, 60.0001, C) == C
    assert run(mode, -40, 1.5, 60.0, C) == (0, 0, 0)            # strict >
    assert run(mode, -40, 1.5, 59, C) == (0, 0, 0)
    assert run(mode, -40, 1.5, float('nan'), C) == (0, 0, 0)
    assert run(mode, -40, 1.5, 70, (-40, 30, -7)) == (-40, 30, -7)
print('other modes: stock test kept exactly (strict >, NaN skips, signed colours)')
print('ok')
