"""BUILD 681: ff7nx_spitreach -- Piazzo Shower and the 8 attacks sharing it.

Without arguments: structural checks on a synthetic module (stock regions
recognised, the patch recognised as patched, nothing else matches).
With `python3 tests/test_spitreach.py <stock main> <patched main>` and
unicorn installed: runs each region, stock and patched, from 300 random
guest states per copy (any slot, any ebp including ones 4 bytes into a page)
with a helper stub that trashes x8-x10 and the flags, and checks the patched
words leave everything the stock words leave, except speed /17 instead of
/24, emitter+0x16 = dy/17, sprite vy += that and position += 2 * velocity.

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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import a64 as A                                                # noqa: E402
import ff7nx_spitreach as S                                    # noqa: E402

HELPER = 0x10FC3A0
TAIL = 0x6DD4


def synthetic():
    img = bytearray(HELPER + 0x40)
    for _, ra, rb in S.SITES:
        for base, stock in ((ra, S.REGION_A_STOCK), (rb, S.REGION_B_STOCK)):
            for i, x in enumerate(stock):
                va = base + 4 * i
                struct.pack_into('<I', img, va,
                                 A.bl(va, HELPER) if x is None else x)
        struct.pack_into('<I', img, rb - 4, A.bl(rb - 4, 0xA95ED0))
    return img


def test_structure():
    img = synthetic()
    for _, ra, rb in S.SITES:
        assert S.site_state(img, ra, rb, HELPER) == 'stock'
        a = S.region_a(ra, HELPER)
        b = S.region_b(rb, HELPER, TAIL)
        assert len(a) == len(S.REGION_A_STOCK) and len(b) == len(S.REGION_B_STOCK)
        for i, x in enumerate(a):
            struct.pack_into('<I', img, ra + 4 * i, x)
        assert S.site_state(img, ra, rb, HELPER).startswith('neither')
        for i, x in enumerate(b):
            struct.pack_into('<I', img, rb + 4 * i, x)
        assert S.site_state(img, ra, rb, HELPER) == 'patched'
        struct.pack_into('<I', img, ra + 8, 0)
        assert S.site_state(img, ra, rb, HELPER).startswith('neither')
    # the tail: 16 words, only bl/ret leave it (no short-range branch)
    words = S.tail(HELPER)(0x1000, lambda i: 0x1000 + 4 * i)
    assert len(words) == 16 and words[-1] == A.ret(17)
    print('structure ok')


def emulate(stock_path, patched_path, trials=300):
    import nxmap
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn import arm64_const as R
    GB, X19 = 0x100000000, 0x80000000

    def run(img, start, end, guest, regs, seed):
        mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        mu.mem_map(0, (len(img) + 0xFFF) & ~0xFFF)
        mu.mem_write(0, bytes(img))
        mu.mem_map(GB + 0xB00000, 0x200000)
        mu.mem_map(GB + 0x7F0000, 0x10000)
        for ga, data in guest.items():
            mu.mem_write(GB + ga, data)
        mu.mem_map(X19, 0x1000)
        mu.mem_write(X19, struct.pack('<8I', *regs))
        rnd = random.Random(seed)
        for r in range(29):
            if r != 19:
                mu.reg_write(R.UC_ARM64_REG_X0 + r, rnd.getrandbits(64))
        mu.reg_write(R.UC_ARM64_REG_X19, X19)

        def hook(uc, addr, size, ud):
            uc.reg_write(R.UC_ARM64_REG_X0,
                         GB + (uc.reg_read(R.UC_ARM64_REG_X0) & 0xFFFFFFFF))
            for r in (R.UC_ARM64_REG_X8, R.UC_ARM64_REG_X9, R.UC_ARM64_REG_X10):
                uc.reg_write(r, rnd.getrandbits(64))
            uc.reg_write(R.UC_ARM64_REG_NZCV, rnd.getrandbits(4) << 28)
            uc.reg_write(R.UC_ARM64_REG_PC, uc.reg_read(R.UC_ARM64_REG_LR))
        mu.hook_add(UC_HOOK_CODE, hook, begin=HELPER, end=HELPER)
        mu.emu_start(start, end, count=2000)
        x19 = struct.unpack('<8I', mu.mem_read(X19, 32))
        return (mu.reg_read(R.UC_ARM64_REG_PC), mu.reg_read(R.UC_ARM64_REG_X8),
                x19, lambda ga, n: bytes(mu.mem_read(GB + ga, n)))

    stock = nxmap.Main(stock_path).img
    pat = nxmap.Main(patched_path).img
    h16 = lambda v: struct.pack('<h', v)                       # noqa: E731
    s16 = lambda b: struct.unpack('<h', b)[0]                  # noqa: E731
    slot = lambda k: 0xBFB718 + 32 * k                         # noqa: E731
    wrap = lambda v: (v + 0x8000) % 0x10000 - 0x8000           # noqa: E731
    rnd = random.Random(1)
    for what, ra, rb in S.SITES:
        for t in range(trials):
            ebp = 0x7F8000 + 4 * rnd.randrange(0x800)
            main, emit, spr = (slot(rnd.randrange(2000)) for _ in range(3))
            if len({main, emit, spr}) < 3:
                continue
            dist = rnd.randrange(20000)
            ty, sy = rnd.randrange(-3000, 3000), rnd.randrange(-3000, 3000)
            esp = ebp - 0x60
            g = {ebp - 0xC: struct.pack('<I', main),
                 ebp - 0x1C: struct.pack('<I', emit),
                 main + 0xE: h16(ty), main + 6: h16(sy),
                 emit + 0x16: h16(0x1234), emit + 0x18: h16(0x5678)}
            regs = (dist, 0xDEAD, 0xBEEF, 0, esp, ebp, 0, 0)
            end = ra + 4 * len(S.REGION_A_STOCK)
            p0, _, x0, m0 = run(stock, ra, end, g, regs, t)
            p1, _, x1, m1 = run(pat, ra, end, g, regs, t)
            q, vy = int(dist / 17), int((ty - sy) / 17)
            assert p0 == p1 == end, what
            assert s16(m0(emit + 0x18, 2)) == int(dist / 24)
            assert s16(m1(emit + 0x18, 2)) == q and s16(m1(emit + 0x16, 2)) == vy
            assert x1[4] == x0[4] == esp + 4 and x1[5] == ebp
            assert x1[0] == q and x1[1] == 17 and m1(main, 32) == m0(main, 32)

            cnt = rnd.randrange(0x10000)
            vel = [rnd.randrange(-300, 300) for _ in range(3)]
            pos = [rnd.randrange(-5000, 5000) for _ in range(3)]
            g = {ebp - 4: struct.pack('<I', emit), ebp - 8: struct.pack('<I', spr),
                 emit + 2: struct.pack('<H', cnt), emit + 0x16: h16(vy),
                 spr + 4: struct.pack('<3h', *pos),
                 spr + 0xC: struct.pack('<3h', *vel)}
            regs = (0x11, 0x22, 0x33333333, 0, esp - 0xC, ebp, 0, 0)
            end = rb + 4 * len(S.REGION_B_STOCK)
            p0, w0, x0, m0 = run(stock, rb, end, g, regs, t)
            p1, w1, x1, m1 = run(pat, rb, end, g, regs, t)
            ev = (vel[0], wrap(vel[1] + vy), vel[2])
            ep = tuple(wrap(pos[i] + 2 * ev[i]) for i in range(3))
            assert p0 == p1 == end and x0[:3] == x1[:3] and x0[4:6] == x1[4:6]
            assert w0 & 0xFFFFFFFF == w1 & 0xFFFFFFFF and m0(emit, 32) == m1(emit, 32)
            assert struct.unpack('<3h', m1(spr + 0xC, 6)) == ev
            assert struct.unpack('<3h', m1(spr + 4, 6)) == ep
            assert m0(spr, 4) == m1(spr, 4) and m0(spr + 0x12, 14) == m1(spr + 0x12, 14)
        print('%s: %d random states, stock == patched apart from the intended '
              'changes' % (what, trials))


if __name__ == '__main__':
    test_structure()
    if len(sys.argv) == 3:
        emulate(sys.argv[1], sys.argv[2])
    print('ok')
