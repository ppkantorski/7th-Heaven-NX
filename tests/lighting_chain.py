"""Run the REAL patched main's ARM code (unicorn): the 675 stubs + blob, the
port's own thunk for x86 0x68DD1E, its native vertex routine (fallback) and
its translated 0x66CFE8. Only the guest->host translator (+0x10FC3A0) and
+0xA550 are stand-ins.

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
import struct, numpy as np, unicorn as U, sys
from unicorn.arm64_const import *
import nxmap
MAIN = sys.argv[1]
m = nxmap.Main(MAIN); img = bytes(m.img)
STATE = 0x4C21D10; SETUP_CALL, SETUP_TARGET = 0xB5D5D4, 0xBC7280
CTX_GOT = 0x12CE2B0; HB = 0x700000000; CTX = 0x6F0000000; SENT = 0x6E0000000
SIZE = (0x4C21D10 + 0x4000 + 0xFFF) & ~0xFFF
def mk():
    mu = U.Uc(U.UC_ARCH_ARM64, U.UC_MODE_ARM)
    mu.mem_map(0, SIZE); mu.mem_write(0, img)
    mu.mem_map(HB, 0x40000); mu.mem_map(CTX, 0x1000); mu.mem_map(SENT, 0x1000); mu.mem_map(0x6D0000000, 0x100000)
    mu.mem_write(CTX_GOT, struct.pack('<Q', CTX))
    mu.reg_write(UC_ARM64_REG_SP, 0x6D0000000 + 0xF0000)
    mu.reg_write(UC_ARM64_REG_CPACR_EL1, 0x300000)
    def hook(mu, addr, size, ud):
        if addr == 0x10FC3A0:
            w0 = mu.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF
            mu.reg_write(UC_ARM64_REG_X0, HB + w0)
            for r in range(1, 18): mu.reg_write(UC_ARM64_REG_X0 + r, 0xDEADBEEFCAFE0000 + r)   # clobber caller-saved
            mu.reg_write(UC_ARM64_REG_PC, mu.reg_read(UC_ARM64_REG_X30))
        elif addr == 0xA550:
            mu.reg_write(UC_ARM64_REG_PC, mu.reg_read(UC_ARM64_REG_X30))
    mu.hook_add(U.UC_HOOK_CODE, hook, begin=0x10FC3A0, end=0x10FC3A0)
    mu.hook_add(U.UC_HOOK_CODE, hook, begin=0xA550, end=0xA550)
    return mu
def gw32(mu, a, v): mu.mem_write(HB + a, struct.pack('<I', v))
def gwf(mu, a, vals): mu.mem_write(HB + a, struct.pack('<%df' % len(vals), *vals))
def esp(mu, v=None):
    if v is None: return struct.unpack('<I', mu.mem_read(CTX + 0x10, 4))[0]
    mu.mem_write(CTX + 0x10, struct.pack('<I', v))
rng = np.random.default_rng(3)
LIGHT, BONE, N, VC, AL, OUT, OUTF = 0x1000, 0x2000, 0x3000, 0x3100, 0x3200, 0x3300, 0x3400
D = rng.normal(size=(3,3)); D /= np.linalg.norm(D, axis=1)[:,None]
C = rng.uniform(0.3, 0.9, size=(3,3)); amb = np.array([0.52, 0.51, 0.50])
th = 1.1; R = np.array([[np.cos(th),0,np.sin(th)],[0,1,0],[-np.sin(th),0,np.cos(th)]])
M = (C @ D) @ R.T; DM = D @ R.T
def scene(mu):
    gw32(mu, LIGHT, 9)
    for i in range(3): gwf(mu, LIGHT + 0x38 + 16*i, list(D[i]) + [0]); gwf(mu, LIGHT + 0x78 + 16*i, list(C[i]) + [0]); gwf(mu, BONE + 16*i, list(R[i]) + [0])
    for ch in range(3): gwf(mu, LIGHT + 0xF8 + 16*ch, list(M[ch]) + [amb[ch]])
mu = mk(); scene(mu)
# ---- 1. setup: the real bl at 0xB5D5D4 -> stub -> lt_setup -> b 0x69C3D7
E = 0x8000; esp(mu, E)
for k, v in ((4, LIGHT + 0xB8), (8, BONE), (12, LIGHT + 0x24), (16, LIGHT + 0xF8)): gw32(mu, E + k, v)
mu.reg_write(UC_ARM64_REG_X19, 0x1919); mu.reg_write(UC_ARM64_REG_X29, 0x2929)
mu.emu_start(SETUP_CALL, SETUP_TARGET, count=200000)
lr = mu.reg_read(UC_ARM64_REG_X30); st = mu.mem_read(STATE, 96)
s = struct.unpack_from('<2I18f4I', st)
assert lr == SETUP_CALL + 4, hex(lr)
assert mu.reg_read(UC_ARM64_REG_X19) == 0x1919 and mu.reg_read(UC_ARM64_REG_X29) == 0x2929
assert s[0] == LIGHT + 0xF8 and s[1] == LIGHT and np.allclose(np.array(s[2:11]).reshape(3,3), DM, atol=1e-5) and np.allclose(np.array(s[11:20]).reshape(3,3), C, atol=1e-6)
assert esp(mu) == E
print('1 setup: reaches 0x69C3D7 with lr = site+4, x19/x29 kept, guest esp untouched; DM/C/key right')
def ffnx(n, vc):
    I = amb + C @ (np.clip(DM @ n, 0, None) ** 2)
    p = vc[::-1] * I
    return I, [255 if not (x < 255) else int(x) for x in p[::-1]]
def pc(n, vc):
    I = np.maximum(amb, amb + M @ n)
    p = vc[::-1] * I
    return [255 if not (x < 255) else int(x) for x in p[::-1]]
# ---- 2. per vertex, through the port's own x86-0x68DD1E thunk (+0xB8E020)
def vtx(mu, n, vc):
    gwf(mu, N, n); gwf(mu, VC, vc); mu.mem_write(HB + AL, bytes([1, 2, 3, 0x9A])); mu.mem_write(HB + OUT, bytes(4))
    E = 0x8000; esp(mu, E)
    for k, v in ((4, LIGHT + 0xF8), (8, N), (12, VC), (16, AL), (20, OUT)): gw32(mu, E + k, v)
    mu.reg_write(UC_ARM64_REG_X30, SENT)
    mu.emu_start(0xB8E020, SENT, count=200000)
    return list(mu.mem_read(HB + OUT, 4)), esp(mu)
bad = 0; floor_pc = 0
for k in range(100):
    n = rng.normal(size=3); n /= np.linalg.norm(n); vc = rng.uniform(80, 255, size=3)
    got, e = vtx(mu, n, vc); _, want = ffnx(n, vc)
    if got[:3] != want or got[3] != 0x9A or e != 0x8000 + 4: bad += 1; print('MISMATCH', got, want, hex(e))
print('2 vertex (FFNx path, the real thunk + stub + blob): 100 vertices,', bad, 'mismatches; guest esp popped as stock')
st = struct.unpack_from('<4I', mu.mem_read(STATE + 80, 16)); print('   counters setups/verts/fixes/fallbacks', st)
# fallback: no set-up light -> the port's own native routine, PC formula
mu.mem_write(STATE, struct.pack('<I', 0))
bad = 0
for k in range(50):
    n = rng.normal(size=3); n /= np.linalg.norm(n); vc = rng.uniform(80, 255, size=3)
    got, e = vtx(mu, n, vc); want = pc(n, vc)
    if any(abs(a - b) > 1 for a, b in zip(got[:3], want)) or got[3] != 0x9A: bad += 1; print('PC MISMATCH', got, want)
print('2b fallback (stock native routine): 50 vertices,', bad, 'mismatches vs the PC formula')
# ---- 3. the 0x66CFE8 call in the scripted-colour variant (+0xB8E1F0)
mu = mk(); scene(mu)
E = 0x8000; esp(mu, E)
for k, v in ((4, LIGHT + 0xB8), (8, BONE), (12, LIGHT + 0x24), (16, LIGHT + 0xF8)): gw32(mu, E + k, v)
mu.emu_start(SETUP_CALL, SETUP_TARGET, count=200000)
n = np.array([0.3, 0.8, -0.52]); n /= np.linalg.norm(n); gwf(mu, N, n); gwf(mu, OUTF, [7, 7, 7])
E = 0x9000; esp(mu, E)
for k, v in ((4, LIGHT + 0xF8), (8, N), (12, OUTF)): gw32(mu, E + k, v)
mu.reg_write(UC_ARM64_REG_X20, CTX); mu.reg_write(UC_ARM64_REG_X19, 0x5151)
mu.emu_start(0xB8E1F0, 0xB8E1F4, count=200000)
got = struct.unpack('<3f', mu.mem_read(HB + OUTF, 12)); I, _ = ffnx(n, np.ones(3))
assert np.allclose(got, I, atol=1e-5), (got, I)
assert esp(mu) == E + 4 and mu.reg_read(UC_ARM64_REG_X19) == 0x5151
print('3 matrix x normal (+0xB8E1F0 -> stub -> real 0x66CFE8 -> fix): FFNx intensity', np.round(got, 4), '; esp popped, x19 kept')
mu.mem_write(STATE, struct.pack('<I', 0)); gwf(mu, OUTF, [7, 7, 7]); esp(mu, E)
mu.emu_start(0xB8E1F0, 0xB8E1F4, count=200000)
got = struct.unpack('<3f', mu.mem_read(HB + OUTF, 12))
assert np.allclose(got, M @ n + amb, atol=1e-5), (got, M @ n + amb)
print('3b not set up: the stock 0x66CFE8 result is kept', np.round(got, 4))
print('ALL CHAIN TESTS PASS')
