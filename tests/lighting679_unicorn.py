"""679: the real lighting.bin -- the field's relight (0x64CA65 / 0x64C382)
lights each polygon set with ITS OWN bone, not the last one set up.
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
import struct, numpy as np, unicorn as U
from unicorn.arm64_const import *
blob = open('lighting.bin', 'rb').read()
ent = {l.split()[0]: int(l.split()[1], 16) for l in open('lighting.entry')}
CODE, G2H, ORIG, ST, STK, H = 0x100000, 0x200000, 0x200100, 0x300000, 0x400000, 0x10000000
def movz(rd, i, hw): return 0xD2800000 | (hw << 21) | (i << 5) | rd
def movk(rd, i, hw): return 0xF2800000 | (hw << 21) | (i << 5) | rd
g2h = [movz(1, H & 0xFFFF, 0), movk(1, (H >> 16) & 0xFFFF, 1), 0x8B204020, 0xD65F03C0]
orig = [movz(0, 0x77, 0), 0xD65F03C0]
SB = 0x20000
guest = bytearray(0x10000); state = [bytes(SB)]
def run(fn, args):
    mu = U.Uc(U.UC_ARCH_ARM64, U.UC_MODE_ARM)
    for b, n in ((CODE, 0x10000), (G2H, 0x1000), (ST, SB), (STK - 0x10000, 0x10000), (H, 0x10000), (H + 0xCBF000, 0x1000), (0xDE000, 0x1000)):  # 783: the mode word
        mu.mem_map(b, n)
    mu.mem_write(CODE, blob); mu.mem_write(G2H, struct.pack('<4I', *g2h)); mu.mem_write(ORIG, struct.pack('<2I', *orig))
    mu.mem_write(ST, state[0]); mu.mem_write(H, bytes(guest))
    mu.reg_write(UC_ARM64_REG_CPACR_EL1, 0x300000)
    mu.reg_write(UC_ARM64_REG_SP, STK - 0x100); mu.reg_write(UC_ARM64_REG_X30, 0xDEAD0)
    for i, a in enumerate(args): mu.reg_write(UC_ARM64_REG_X0 + i, a)
    mu.emu_start(CODE + ent[fn], 0xDEAD0, count=200000)
    state[0] = bytes(mu.mem_read(ST, SB)); guest[:] = mu.mem_read(H, 0x10000)
    return mu.reg_read(UC_ARM64_REG_X0)
def w32(a, v): struct.pack_into('<I', guest, a, v)
def wf(a, v): struct.pack_into('<%df' % len(v), guest, a, *v)
LIGHT, BA, BB, ESP, ESPE, N, VC, AL, OUT, ESP2, OUTF = 0x1000, 0x2000, 0x2100, 0x8000, 0x8100, 0x3000, 0x3100, 0x3200, 0x3300, 0x9000, 0x3400
PSA, PSB, PSC = 0x5000, 0x5800, 0x6000
rng = np.random.default_rng(3)
D = rng.normal(size=(3, 3)); D /= np.linalg.norm(D, axis=1)[:, None]
C = rng.uniform(0.3, 0.9, size=(3, 3)); amb = np.array([0.5, 0.48, 0.46])
def rot(a, b):
    ca, sa, cb, sb = np.cos(a), np.sin(a), np.cos(b), np.sin(b)
    return np.array([[ca, 0, sa], [0, 1, 0], [-sa, 0, ca]]) @ np.array([[1, 0, 0], [0, cb, -sb], [0, sb, cb]])
RA, RB = rot(0.2, 0.1), rot(2.4, -0.9)               # the head, and a swinging leg
w32(LIGHT, 8)                                         # KAWAI 9 draws with the light OUT of mode 1
for i in range(3):
    wf(LIGHT + 0x38 + 16 * i, D[i]); wf(LIGHT + 0x78 + 16 * i, C[i])
    wf(BA + 16 * i, RA[i]); wf(BB + 16 * i, RB[i])
def mset(R):
    M = (C @ D) @ R.T
    for ch in range(3): wf(LIGHT + 0xF8 + 16 * ch, list(M[ch]) + [amb[ch]])
def setup(bone, R):
    mset(R)
    w32(ESP + 4, LIGHT + 0xB8); w32(ESP + 8, bone); w32(ESP + 12, LIGHT + 0x24); w32(ESP + 16, LIGHT + 0xF8)
    run('lt_setup', [ST, ESP, G2H])
def entry(ps, kind):
    w32(ESPE + 4, ps); w32(ESPE + 8, LIGHT); run('lt_entry', [ST, ESPE, G2H, kind])
def lit(n, R):
    t = np.clip((D @ R.T) @ n, 0, None) ** 2
    return amb + C @ t
n = np.array([0.3, 0.5, 0.81]); n /= np.linalg.norm(n); wf(N, n); wf(VC, [100.0, 100.0, 100.0])
struct.pack_into('<4B', guest, AL, 0, 0, 0, 0x80)
def vtx():
    r = run('lt_vtx', [LIGHT + 0xF8, N, VC, AL, OUT, ST, G2H, ORIG])
    return r, list(guest[OUT:OUT + 3])
def want(R):
    I = lit(n, R); p = 100.0 * I
    return [255 if not (x < 255) else int(x) for x in p[::-1]]
# draw_3d_model: part A then part B, each set up then 0x68E92D
setup(BA, RA); entry(PSA, 0)
setup(BB, RB); entry(PSB, 0)
w32(LIGHT, 9)                                         # flags |= 1, then the relight (+0xF8 is B's)
entry(PSA, 1); r, got = vtx()
assert r == OUT and got == want(RA), (got, want(RA), want(RB))
entry(PSB, 1); r, got = vtx()
assert r == OUT and got == want(RB), (got, want(RB))
print('relight: part A with its own bone', want(RA), '(stock: B\'s', want(RB), ') -- part B with its own')
# 0x66CFE8 path too
w32(ESP2 + 4, LIGHT + 0xF8); w32(ESP2 + 8, N); w32(ESP2 + 12, OUTF)
entry(PSA, 1); wf(OUTF, [9, 9, 9]); run('lt_mvfix', [ST, ESP2, G2H])
assert np.allclose(struct.unpack_from('<3f', guest, OUTF), lit(n, RA), atol=1e-5)
print('mvfix: part A with its own bone')
# a set never bound: the cache is left as it was (stock behaviour for it)
entry(PSC, 1); r, got = vtx(); assert got == want(RA), got
print('unbound set: unchanged')
# a second 0x68E92D without a new setup does not rebind (no fresh setup)
entry(PSC, 0); entry(PSC, 1); r, got = vtx(); assert got == want(RA)
print('no fresh setup: no binding')
# next frame: a new pose for A is picked up
RA2 = rot(0.5, 0.3); w32(LIGHT, 8); setup(BA, RA2) if False else None
for i in range(3): wf(BA + 16 * i, RA2[i])
setup(BA, RA2); entry(PSA, 0); w32(LIGHT, 9); entry(PSA, 1); r, got = vtx()
assert got == want(RA2)
print('next frame: A rebound to its new bone\nok')
