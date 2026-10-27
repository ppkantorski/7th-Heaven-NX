"""675b on the REAL patched main: two lights (two parts, two bones) set up,
then lit alternately -- each must use ITS record; all four vertex variants;
a ramp light (+0x17C == 2) is never written.

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
exec(open('chain_test.py').read().split("rng = np.random.default_rng(3)")[0])
rng = np.random.default_rng(11)
def mklight(base, bone, th):
    D = rng.normal(size=(3,3)); D /= np.linalg.norm(D, axis=1)[:,None]
    C = rng.uniform(0.3, 0.9, size=(3,3)); amb = rng.uniform(0.3, 0.6, size=3)
    R = np.array([[np.cos(th),0,np.sin(th)],[0,1,0],[-np.sin(th),0,np.cos(th)]]) @ np.array([[1,0,0],[0,np.cos(th/2),-np.sin(th/2)],[0,np.sin(th/2),np.cos(th/2)]])
    return dict(base=base, bone=bone, D=D, C=C, amb=amb, R=R, M=(C @ D) @ R.T, DM=D @ R.T)
LA = mklight(0x1000, 0x2000, 0.8); LB = mklight(0x5000, 0x6000, -1.9)
def put(mu, L, flags=9, mode2=0):
    gw32(mu, L['base'], flags); gw32(mu, L['base'] + 0x17C, mode2)
    for i in range(3):
        gwf(mu, L['base'] + 0x38 + 16*i, list(L['D'][i]) + [0]); gwf(mu, L['base'] + 0x78 + 16*i, list(L['C'][i]) + [0])
        gwf(mu, L['bone'] + 16*i, list(L['R'][i]) + [0])
    for ch in range(3): gwf(mu, L['base'] + 0xF8 + 16*ch, list(L['M'][ch]) + [L['amb'][ch]])
def setup(mu, L):
    E = 0x8000; esp(mu, E)
    for k, v in ((4, L['base'] + 0xB8), (8, L['bone']), (12, L['base'] + 0x24), (16, L['base'] + 0xF8)): gw32(mu, E + k, v)
    mu.emu_start(SETUP_CALL, SETUP_TARGET, count=200000)
def I_ffnx(L, n): return L['amb'] + L['C'] @ (np.clip(L['DM'] @ n, 0, None) ** 2)
def bytes_of(I, vc): return [255 if not (x < 255) else int(x) for x in (vc[::-1] * I)[::-1]]
mu = mk(); put(mu, LA); put(mu, LB)
setup(mu, LA); setup(mu, LB)            # both parts set up, then lit in a row (the field's order)
N2, VC2, AL2, OUT2 = 0x3000, 0x3100, 0x3200, 0x3300
bad = 0
for k in range(120):
    L = LA if k % 2 == 0 else LB
    n = rng.normal(size=3); n /= np.linalg.norm(n); vc = rng.uniform(80, 255, size=3)
    gwf(mu, N2, n); gwf(mu, VC2, vc); mu.mem_write(HB + AL2, bytes([0, 0, 0, 0x55])); mu.mem_write(HB + OUT2, bytes(4))
    E = 0x8000; esp(mu, E)
    for kk, v in ((4, L['base'] + 0xF8), (8, N2), (12, VC2), (16, AL2), (20, OUT2)): gw32(mu, E + kk, v)
    mu.reg_write(UC_ARM64_REG_X30, SENT); mu.emu_start(0xB8E020, SENT, count=200000)
    got = list(mu.mem_read(HB + OUT2, 4))
    if got[:3] != bytes_of(I_ffnx(L, n), vc) or got[3] != 0x55: bad += 1
print('A. two lights set up, lit alternately through the 0x68DD1E thunk: 120 vertices,', bad, 'mismatches')
# B. the 0x66CFE8 call sites of the other three variants, alternating lights
for site in (0xB8E1F0, 0xB8D214, 0xB8C404):
    bad = 0
    for k in range(20):
        L = LA if k % 2 else LB
        n = rng.normal(size=3); n /= np.linalg.norm(n); gwf(mu, N2, n); gwf(mu, 0x3400, [7, 7, 7])
        E = 0x9000; esp(mu, E)
        for kk, v in ((4, L['base'] + 0xF8), (8, N2), (12, 0x3400)): gw32(mu, E + kk, v)
        mu.reg_write(UC_ARM64_REG_X20, CTX); mu.reg_write(UC_ARM64_REG_X21, CTX); mu.reg_write(UC_ARM64_REG_X19, 0x77)
        mu.emu_start(site, site + 4, count=200000)
        got = struct.unpack('<3f', mu.mem_read(HB + 0x3400, 12))
        if not np.allclose(got, I_ffnx(L, n), atol=1e-5) or esp(mu) != E + 4 or mu.reg_read(UC_ARM64_REG_X19) != 0x77: bad += 1
    print('B. +0x%X (0x66CFE8 call): 20 calls, %d mismatches' % (site, bad))
# C. 675c: a light with a ramp sub-mode (+0x17C == 2, del3's Cloud) gets the
#    new lighting too, and its ramp is never touched
mu = mk(); LR_ = mklight(0x1000, 0x2000, 0.4); put(mu, LR_, flags=9, mode2=2)
mu.mem_write(HB + 0x1000 + 0x1A4, bytes(range(256)) * 4)
setup(mu, LR_)
assert bytes(mu.mem_read(HB + 0x1000 + 0x1A4, 1024)) == bytes(range(256)) * 4
n = np.array([0.3, -0.2, 0.93]); n /= np.linalg.norm(n); vc = np.array([180., 200., 220.])
gwf(mu, N2, n); gwf(mu, VC2, vc); mu.mem_write(HB + AL2, bytes([0, 0, 0, 0x55]))
E = 0x8000; esp(mu, E)
for kk, v in ((4, LR_['base'] + 0xF8), (8, N2), (12, VC2), (16, AL2), (20, OUT2)): gw32(mu, E + kk, v)
mu.reg_write(UC_ARM64_REG_X30, SENT); mu.emu_start(0xB8E020, SENT, count=200000)
assert list(mu.mem_read(HB + OUT2, 3)) == bytes_of(I_ffnx(LR_, n), vc), (list(mu.mem_read(HB + OUT2, 3)), bytes_of(I_ffnx(LR_, n), vc))
print('C. ramp sub-mode light (del3 Cloud case): FFNx lighting, ramp untouched')
# D. a record whose light has since become mode 2/3 is ignored
mu = mk(); put(mu, LA); setup(mu, LA); gw32(mu, LA['base'], 10)
n = np.array([0.0, 0.0, 1.0]); gwf(mu, N2, n); gwf(mu, 0x3400, [7, 7, 7])
E = 0x9000; esp(mu, E)
for kk, v in ((4, LA['base'] + 0xF8), (8, N2), (12, 0x3400)): gw32(mu, E + kk, v)
mu.mem_write(STATE, struct.pack('<I', 0))     # cold cache
mu.reg_write(UC_ARM64_REG_X21, CTX); mu.emu_start(0xB8C404, 0xB8C408, count=200000)
got = struct.unpack('<3f', mu.mem_read(HB + 0x3400, 12))
assert np.allclose(got, LA['M'] @ n + LA['amb'], atol=1e-5)
print('D. a light no longer mode 1: stock result kept')
# E. 675d: the field's scripted light -- set up while flags = 8 (not mode 1),
#    then flags |= 1 (x86 0x64A281), then lit (del3's Cloud)
mu = mk(); LE = mklight(0x1000, 0x2000, 1.3); put(mu, LE, flags=8)
setup(mu, LE)
gw32(mu, LE['base'], 9)
n = np.array([-0.4, 0.5, 0.77]); n /= np.linalg.norm(n); vc = np.array([150., 190., 230.])
gwf(mu, N2, n); gwf(mu, VC2, vc); mu.mem_write(HB + AL2, bytes([0, 0, 0, 0x55]))
E = 0x8000; esp(mu, E)
for kk, v in ((4, LE['base'] + 0xF8), (8, N2), (12, VC2), (16, AL2), (20, OUT2)): gw32(mu, E + kk, v)
mu.reg_write(UC_ARM64_REG_X30, SENT); mu.emu_start(0xB8E020, SENT, count=200000)
ctr = struct.unpack('<4I', mu.mem_read(STATE + 80, 16))
assert ctr[1] == 1 and ctr[3] == 0, ('counters setups/verts/fixes/fallbacks', ctr)
assert list(mu.mem_read(HB + OUT2, 3)) == bytes_of(I_ffnx(LE, n), vc)
print('E. scripted field light (set up before mode 1, then flags |= 1): FFNx lighting')
print('ALL 675d CHAIN TESTS PASS')
