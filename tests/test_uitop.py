#!/usr/bin/env python3
"""BUILD 684/686 UI-on-top check: python3 tests/test_uitop.py <stock main> <patched main>

Structural: exactly one call site changed, its cave resolves, the pass is
idempotent. Emulated (unicorn): from the patched call site, with the native
clear, the depth-clear wrapper and the menu draw stubbed, the clear runs once
with (colour 0, depth 1); then the wrapper runs once with the device and the
CURRENT depth target read from their GOT slots and s0 = 1.0; then the menu
draw is entered with the return address, sp and every callee-saved register
exactly as the stock `bl` would leave them.

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
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import nxmap  # noqa: E402
import ff7nx_uitop as U  # noqa: E402
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE  # noqa: E402
from unicorn.arm64_const import *  # noqa: E402,F403


def run(m, site, clear, menu, refs, seed):
    dev_slot, depth_slot, wrap = refs
    rnd = random.Random(seed)
    img = bytes(m.img)
    size = (len(img) + 0xFFFF) & ~0xFFFF
    mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    mu.mem_map(0, size)
    mu.mem_write(0, img)
    STACK = 0x7000_0000
    mu.mem_map(STACK - 0x10000, 0x20000)
    sp = STACK - 0x100 - 16 * rnd.randrange(16)
    mu.reg_write(UC_ARM64_REG_SP, sp)
    regs = {}
    for r in range(19, 30):
        v = rnd.getrandbits(64)
        regs[r] = v
        mu.reg_write(globals()['UC_ARM64_REG_X%d' % r], v)
    for r in range(0, 19):
        mu.reg_write(globals()['UC_ARM64_REG_X%d' % r], rnd.getrandbits(64))
    # the two GOT slots point at globals holding random device/depth objects
    GLOB = 0x7100_0000
    mu.mem_map(GLOB, 0x1000)
    dev_obj, depth_obj = rnd.getrandbits(64), rnd.getrandbits(64)
    mu.mem_write(GLOB, struct.pack('<QQ', dev_obj, depth_obj))
    mu.mem_write(dev_slot, struct.pack('<Q', GLOB))
    mu.mem_write(depth_slot, struct.pack('<Q', GLOB + 8))
    calls = []
    wraps = []
    out = {}

    def hook(uc, addr, size_, _):
        if addr == clear:
            calls.append((uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF,
                          uc.reg_read(UC_ARM64_REG_X1) & 0xFFFFFFFF,
                          uc.reg_read(UC_ARM64_REG_SP)))
            # behave like an AAPCS callee: trash caller-saved, keep the rest
            for r in range(0, 18):
                uc.reg_write(globals()['UC_ARM64_REG_X%d' % r], 0xDEAD0000 + r)
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
        elif addr == wrap:
            s0 = struct.unpack('<f', struct.pack(
                '<I', uc.reg_read(UC_ARM64_REG_S0) & 0xFFFFFFFF))[0]
            wraps.append((uc.reg_read(UC_ARM64_REG_X0),
                          uc.reg_read(UC_ARM64_REG_X1), s0, len(calls),
                          uc.reg_read(UC_ARM64_REG_SP)))
            for r in range(0, 18):
                uc.reg_write(globals()['UC_ARM64_REG_X%d' % r], 0xBEEF0000 + r)
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
        elif addr == menu:
            out['lr'] = uc.reg_read(UC_ARM64_REG_LR)
            out['sp'] = uc.reg_read(UC_ARM64_REG_SP)
            out['regs'] = {r: uc.reg_read(globals()['UC_ARM64_REG_X%d' % r])
                           for r in range(19, 30)}
            uc.emu_stop()
    mu.hook_add(UC_HOOK_CODE, hook)
    mu.emu_start(site, 0xFFFFFFFF, count=200)
    bad = []
    if len(calls) != 1:
        bad.append('clear called %d times' % len(calls))
    else:
        c, d, csp = calls[0]
        if (c, d) != (0, 1):
            bad.append('clear(%d, %d)' % (c, d))
        if csp % 16:
            bad.append('sp misaligned at clear')
    if len(wraps) != 1:
        bad.append('depth wrapper called %d times' % len(wraps))
    else:
        x0, x1, s0, after, wsp = wraps[0]
        if x0 != dev_obj:
            bad.append('wrapper x0 %#x, not the device global' % x0)
        elif x1 != depth_obj:
            bad.append('wrapper x1 %#x, not the current depth' % x1)
        if s0 != 1.0:
            bad.append('wrapper s0 %r' % s0)
        if after != 1:
            bad.append('wrapper ran before the clear')
        if wsp % 16:
            bad.append('sp misaligned at wrapper')
    if out.get('lr') != site + 4:
        bad.append('menu entered with lr %r' % out.get('lr'))
    if out.get('sp') != sp:
        bad.append('sp changed')
    if out.get('regs') != {r: regs[r] for r in range(19, 30)}:
        bad.append('callee-saved registers changed')
    return bad


def main():
    stock, patched = sys.argv[1], sys.argv[2]
    ms, mp = nxmap.Main(stock), nxmap.Main(patched)
    s = U.find_site(ms)
    p = U.find_site(mp)
    assert not isinstance(s, str) and s[2] == 'stock', s
    assert not isinstance(p, str) and p[2] == 'patched', p
    assert s[0] == p[0] and s[1] == p[1]
    site, menu = p[0], p[1]
    clear = U.native_clear(mp)
    assert clear is not None
    refs = U.depth_refs(mp, clear)
    assert not isinstance(refs, str), refs
    # only the site and free (zero) holes differ
    a, b = bytes(ms.img), bytes(mp.img)
    assert len(a) == len(b)
    for i in range(0, len(a), 4):
        if a[i:i + 4] != b[i:i + 4]:
            assert i == site or a[i:i + 4] == b'\0\0\0\0', hex(i)
    assert U.plan(mp)[0] == []
    fails = 0
    for seed in range(200):
        bad = run(mp, site, clear, menu, refs, seed)
        if bad:
            fails += 1
            if fails < 5:
                print('seed %d: %s' % (seed, '; '.join(bad)))
    print('ui top: site +0x%X, clear +0x%X, depth slot +0x%X via +0x%X, '
          'menu +0x%X: %d/200 failures'
          % (site, clear, refs[1], refs[2], menu, fails))
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
