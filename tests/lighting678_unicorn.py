"""678: the real lighting.bin -- field (mode 2): a mode-1 light's vertices are
their vertex colours (I = 1), 0x66CFE8's result becomes 1.0; any other mode,
or a light that is not mode 1: the stock routines, untouched.
Run from native/.

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
import struct, unicorn as U
from unicorn.arm64_const import *
blob = open('lighting.bin', 'rb').read()
ent = {l.split()[0]: int(l.split()[1], 16) for l in open('lighting.entry')}
CODE, G2H, ORIG, ST, STK, H, MODEP = 0x100000, 0x200000, 0x200100, 0x300000, 0x400000, 0x10000000, 0x500000
def movz(rd, i, hw): return 0xD2800000 | (hw << 21) | (i << 5) | rd
def movk(rd, i, hw): return 0xF2800000 | (hw << 21) | (i << 5) | rd
g2h = [movz(1, H & 0xFFFF, 0), movk(1, (H >> 16) & 0xFFFF, 1), 0x8B204020, 0xD65F03C0]
orig = [movz(0, 0x77, 0), 0xD65F03C0]
guest = bytearray(0x10000); state = [bytes(0x4000)]; mode = [2]
def run(fn, args):
    mu = U.Uc(U.UC_ARCH_ARM64, U.UC_MODE_ARM)
    for b, n in ((CODE, 0x10000), (G2H, 0x1000), (ST, 0x4000), (STK - 0x10000, 0x10000), (H, 0x10000), (MODEP, 0x1000), (0xDE000, 0x1000)):
        mu.mem_map(b, n)
    mu.mem_write(CODE, blob); mu.mem_write(G2H, struct.pack('<4I', *g2h)); mu.mem_write(ORIG, struct.pack('<2I', *orig))
    mu.mem_write(ST, state[0]); mu.mem_write(H, bytes(guest)); mu.mem_write(MODEP, struct.pack('<I', mode[0]))
    mu.reg_write(UC_ARM64_REG_CPACR_EL1, 0x300000)
    mu.reg_write(UC_ARM64_REG_SP, STK - 0x100); mu.reg_write(UC_ARM64_REG_X30, 0xDEAD0)
    for i, a in enumerate(args): mu.reg_write(UC_ARM64_REG_X0 + i, a)
    mu.emu_start(CODE + ent[fn], 0xDEAD0, count=100000)
    state[0] = bytes(mu.mem_read(ST, 0x4000)); guest[:] = mu.mem_read(H, 0x10000)
    return mu.reg_read(UC_ARM64_REG_X0)
LIGHT, BONE, ESP, N, VC, AL, OUT, ESP2, OUTF = 0x1000, 0x2000, 0x8000, 0x3000, 0x3100, 0x3200, 0x3300, 0x9000, 0x3400
def w32(a, v): struct.pack_into('<I', guest, a, v)
def wf(a, v): struct.pack_into('<%df' % len(v), guest, a, *v)
w32(LIGHT, 9)
for i in range(3): wf(BONE + 16 * i, [float(i == j) for j in range(4)])
w32(ESP + 4, LIGHT + 0xB8); w32(ESP + 8, BONE); w32(ESP + 12, LIGHT + 0x24); w32(ESP + 16, LIGHT + 0xF8)
wf(N, [0.0, 0.0, 1.0]); wf(VC, [40.5, 130.0, 300.0]); struct.pack_into('<4B', guest, AL, 1, 2, 3, 0x80)
w32(ESP2 + 4, LIGHT + 0xF8); w32(ESP2 + 8, N); w32(ESP2 + 12, OUTF)
def setup(m):
    mode[0] = m; run('lt_setup', [ST, ESP, G2H, MODEP])
# field, mode-1 light: vertex colours as they are (b,g,r floats -> bytes), alpha from al
setup(2)
r = run('lt_vtx', [LIGHT + 0xF8, N, VC, AL, OUT, ST, G2H, ORIG])
assert r == OUT and list(guest[OUT:OUT + 4]) == [40, 130, 255, 0x80], list(guest[OUT:OUT + 4])
wf(OUTF, [9, 9, 9]); run('lt_mvfix', [ST, ESP2, G2H])
assert struct.unpack_from('<3f', guest, OUTF) == (1.0, 1.0, 1.0)
print('field: unshaded vertex colours (clamped at 255), 0x66CFE8 -> 1.0')
# world map / battle: stock everywhere
for m in (3, 4, 13):
    setup(m)
    assert run('lt_vtx', [LIGHT + 0xF8, N, VC, AL, OUT, ST, G2H, ORIG]) == 0x77
    wf(OUTF, [9, 9, 9]); run('lt_mvfix', [ST, ESP2, G2H]); assert struct.unpack_from('<3f', guest, OUTF) == (9.0, 9.0, 9.0)
print('modes 3, 4, 13: stock routines, 0x66CFE8 result untouched')
# field, light not mode 1 (a ramp light): stock
w32(LIGHT, 8); setup(2)                  # set up while not mode 1 (the scripted light)
assert run('lt_vtx', [LIGHT + 0xF8, N, VC, AL, OUT, ST, G2H, ORIG]) == 0x77
w32(LIGHT, 9)
assert run('lt_vtx', [LIGHT + 0xF8, N, VC, AL, OUT, ST, G2H, ORIG]) == OUT
print('field: a light that is not mode 1 stays stock; the scripted light once mode 1 is unshaded')
assert run('lt_vtx', [LIGHT + 0x10F8, N, VC, AL, OUT, ST, G2H, ORIG]) == 0x77
print('another matrix: stock\nok')
