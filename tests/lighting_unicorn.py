"""tests/lighting_unicorn.py

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
blob = open('lighting.bin','rb').read()
ent = {l.split()[0]: int(l.split()[1],16) for l in open('lighting.entry')}
CODE, G2H, ORIG, ST, STK, H = 0x100000, 0x200000, 0x200100, 0x300000, 0x400000, 0x10000000
def enc_movz(rd, imm16, hw): return 0xD2800000 | (hw << 21) | (imm16 << 5) | rd
def enc_movk(rd, imm16, hw): return 0xF2800000 | (hw << 21) | (imm16 << 5) | rd
g2h = [enc_movz(1, H & 0xFFFF, 0), enc_movk(1, (H >> 16) & 0xFFFF, 1), 0x8B204020, 0xD65F03C0]  # add x0, x1, w0, uxtw ; ret
orig = [enc_movz(0, 0x77, 0), 0xD65F03C0]
def run(fn, args):
    mu = U.Uc(U.UC_ARCH_ARM64, U.UC_MODE_ARM)
    for base, size in ((CODE, 0x10000), (G2H, 0x1000), (ST, 0x4000), (STK - 0x10000, 0x10000), (H, 0x20000), (H + 0xCBF000, 0x1000)):  # 783: the mode word
        mu.mem_map(base, size)
    mu.mem_write(CODE, blob)
    mu.mem_write(G2H, struct.pack('<4I', *g2h)); mu.mem_write(ORIG, struct.pack('<2I', *orig))
    mu.mem_write(ST, state[0]); mu.mem_write(H, bytes(guest))
    mu.reg_write(UC_ARM64_REG_SP, STK - 0x100); mu.reg_write(UC_ARM64_REG_X30, 0xDEAD0)
    mu.mem_map(0xDE000, 0x1000)
    for i, a in enumerate(args): mu.reg_write(UC_ARM64_REG_X0 + i, a)
    # enable FP
    mu.reg_write(UC_ARM64_REG_CPACR_EL1, 0x300000)
    mu.emu_start(CODE + ent[fn], 0xDEAD0, count=100000)
    state[0] = bytes(mu.mem_read(ST, 0x4000)); guest[:] = mu.mem_read(H, 0x20000)
    return mu.reg_read(UC_ARM64_REG_X0)
rng = np.random.default_rng(7)
guest = bytearray(0x20000); state = [bytes(0x4000)]
LIGHT, BONE, ESP, N, VC, AL, OUT, ESP2, OUTF = 0x1000, 0x2000, 0x8000, 0x3000, 0x3100, 0x3200, 0x3300, 0x9000, 0x3400
def w32(a, v): struct.pack_into('<I', guest, a, v)
def wf(a, vals): struct.pack_into('<%df' % len(vals), guest, a, *vals)
D = rng.normal(size=(3,3)); D /= np.linalg.norm(D, axis=1)[:,None]
C = rng.uniform(0.3, 0.9, size=(3,3))            # row = channel, col = light
amb = np.array([0.52, 0.51, 0.50])
th = 0.7; R = np.array([[np.cos(th),0,np.sin(th)],[0,1,0],[-np.sin(th),0,np.cos(th)]]) @ np.array([[1,0,0],[0,np.cos(.3),-np.sin(.3)],[0,np.sin(.3),np.cos(.3)]])
w32(LIGHT, 9)
for i in range(3): wf(LIGHT + 0x38 + 16*i, D[i]); wf(LIGHT + 0x78 + 16*i, C[i]); wf(BONE + 16*i, R[i])
M = (C @ D) @ R.T
for ch in range(3): wf(LIGHT + 0xF8 + 16*ch, list(M[ch]) + [amb[ch]])
w32(ESP + 4, LIGHT + 0xB8); w32(ESP + 8, BONE); w32(ESP + 12, LIGHT + 0x24); w32(ESP + 16, LIGHT + 0xF8)
run('lt_setup', [ST, ESP, G2H])
s = struct.unpack_from('<2I18f4I', state[0])
DM = (D @ R.T)
assert s[0] == LIGHT + 0xF8 and s[1] == LIGHT, s[:2]
assert np.allclose(np.array(s[2:11]).reshape(3,3), DM, atol=1e-5), 'dm'
assert np.allclose(np.array(s[11:20]).reshape(3,3), C, atol=1e-6), 'c'
print('setup ok: dm, c, key; setups', s[20])
def ref(n, vc):
    t = np.clip(DM @ n, 0, None) ** 2
    I = amb + C @ t
    p = vc[::-1][:3] * I                # vcolour floats are b,g,r; I is r,g,b
    return I, [255 if not (x < 255) else int(x) for x in p[::-1]]   # bytes b,g,r
bad = 0
for k in range(200):
    n = rng.normal(size=3); n /= np.linalg.norm(n)
    vc = rng.uniform(100, 255, size=3)
    wf(N, n); wf(VC, vc); struct.pack_into('<4B', guest, AL, 1, 2, 3, 0x80)
    r = run('lt_vtx', [LIGHT + 0xF8, N, VC, AL, OUT, ST, G2H, ORIG])
    got = list(guest[OUT:OUT+4]); I, want = ref(n, vc)
    if r != OUT or got[:3] != want or got[3] != 0x80: bad += 1; print('MISMATCH', got, want, r)
print('vtx: 200 vertices,', bad, 'mismatches')
r = run('lt_vtx', [LIGHT + 0x10F8, N, VC, AL, OUT, ST, G2H, ORIG])
assert r == 0x77, 'fallback'
print('fallback to stock for another matrix ok')
n = np.array([0.2, -0.9, 0.38]); n /= np.linalg.norm(n); wf(N, n)
w32(ESP2 + 4, LIGHT + 0xF8); w32(ESP2 + 8, N); w32(ESP2 + 12, OUTF); wf(OUTF, [9, 9, 9])
run('lt_mvfix', [ST, ESP2, G2H])
I, _ = ref(n, np.ones(3))
assert np.allclose(struct.unpack_from('<3f', guest, OUTF), I, atol=1e-5), (struct.unpack_from('<3f', guest, OUTF), I)
w32(ESP2 + 4, LIGHT + 0x10F8); wf(OUTF, [9, 9, 9]); run('lt_mvfix', [ST, ESP2, G2H])
assert struct.unpack_from('<3f', guest, OUTF) == (9.0, 9.0, 9.0)
print('mvfix ok (and leaves another matrix alone)')
w32(LIGHT, 8); run('lt_setup', [ST, ESP, G2H])
assert struct.unpack_from('<I', state[0])[0] == 0, 'cache must not hold a non-mode-1 light'
r = run('lt_vtx', [LIGHT + 0xF8, N, VC, AL, OUT, ST, G2H, ORIG]); assert r == 0x77, 'not mode 1 yet: stock'
w32(LIGHT, 9)                                  # the field's scripted light: flags |= 1 after the setup
r = run('lt_vtx', [LIGHT + 0xF8, N, VC, AL, OUT, ST, G2H, ORIG]); assert r == OUT, 'mode 1 now: the record is used'
print('675d: a light set up before it is mode 1 (the scripted field light) is lit once it is')
print('counters setups/verts/fixes/fallbacks', struct.unpack_from('<4I', state[0], 80))
