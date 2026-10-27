#!/usr/bin/env python3
"""
tests/test_bgquads.py -- ff7nx_bgquads's native 0x6465FB does what the
translated one did, both run under the Unicorn emulator on the dumped
module's real words.

The same random state for both: guest pages scattered through host memory
behind a real page table (the translator's own walk, +0x10FC3A0, and the
C code's), unmapped guest pages on the fallback page, 42 layers with
random tile counts, blend modes and flags, sprite words that include NaN,
infinities and denormals, vertex buffers that cross page boundaries, the
real 0x676578, and a modelled 0x66E272 (records its arguments and the
guest stack pointer, moves the buffer cursor, returns 0 for some layers)
and timer tick. After each run: every guest page, the x86 context, the x87
state, the calls, the ticks, the callee-saved registers and sp.
Skipped where unicorn is not installed.

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
import unittest

try:
    import unicorn as U
    import unicorn.arm64_const as R
except ImportError:                                  # pragma: no cover
    U = None

import ff7nx_audio_cave as AC                                  # noqa: E402
import a64 as A                                                # noqa: E402
import ff7nx_bgquads as B                                      # noqa: E402

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP_MAIN = os.path.join(_HERE, 'dump', 'exefs', 'main')

CTX, X87, DUMMY = 0x20000000, 0x20001000, 0x20002000
TABLE = 0x21000000                 # 1M entries x 8
POOL = 0x40000000                  # guest pages live here, scattered
STACK_TOP = 0x30100000
SENTINEL = 0x2FFFF000
ESP = 0x0018FF00
SPR = 0x00400000                   # the 42 layers' sprite records
STATE = 0x00500000
LENT = 0x00600000                  # layer entries (0xCFFC70 table -> here)
OBJS = 0x00610000
BYTES = 0x00680000
VBUF = 0x00700000
GARB = 0xDEADBEEFCAFEF00D


@unittest.skipUnless(U is not None and os.path.exists(DUMP_MAIN),
                     'needs unicorn and dump/exefs/main')
class BgQuads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        blob = open(DUMP_MAIN, 'rb').read()
        cls.segs, cls.raw = AC.segments(blob)
        t = bytearray(cls.raw[0])
        lo, hi = B.dead_region(DUMP_MAIN, None)
        code = open(B.BLOB, 'rb').read()
        cls.entry, cls.stubs, cls.end = B.patch_text(t, lo, hi, code, B.entries())
        cls.text0, cls.text1 = bytes(cls.raw[0]), bytes(t)

    # ------------------------------------------------------------- set-up
    def build(self, text, seed):
        rnd = random.Random(seed)
        mu = U.Uc(U.UC_ARCH_ARM64, U.UC_MODE_ARM)
        top = max(s[1] + s[2] for s in self.segs) + 0x200000
        mu.mem_map(0, (top + 0xFFFFF) & ~0xFFFFF)
        mu.mem_write(0, text)
        for (_off, va, _sz), r in list(zip(self.segs, self.raw))[1:]:
            mu.mem_write(va, r)
        mu.mem_map(CTX, 0x3000)
        mu.mem_map(TABLE, 0x800000)
        mu.mem_map(STACK_TOP - 0x100000, 0x100000)
        mu.mem_map(SENTINEL, 0x1000)
        mu.mem_map(POOL, 0x1000 * 4096)
        mu.mem_write(B.CTX_GOT, struct.pack('<Q', CTX))
        mu.mem_write(B.X87_GOT, struct.pack('<Q', X87))
        mu.mem_write(B.PAGES_GOT, struct.pack('<Q', TABLE))
        mu.mem_write(B.DUMMY_GOT, struct.pack('<Q', DUMMY))
        self.slots = {}
        r2 = random.Random(seed * 31 + 7)
        perm = list(range(4096))
        r2.shuffle(perm)

        def page(gpage):
            if gpage not in self.slots:
                host = POOL + 0x1000 * perm[len(self.slots)]
                self.slots[gpage] = host
                mu.mem_write(TABLE + 8 * gpage, struct.pack('<Q', host))
                mu.mem_write(host, bytes(r2.getrandbits(8) for _ in range(0x1000)))
            return self.slots[gpage]

        def gw(va, data):
            for k, b in enumerate(data):
                h = page((va + k) >> 12) + ((va + k) & 0xFFF)
                mu.mem_write(h, bytes([b]))

        def g32(va, v):
            gw(va, struct.pack('<I', v & 0xFFFFFFFF))

        def g16(va, v):
            gw(va, struct.pack('<H', v & 0xFFFF))
        self.gw, self.g32 = gw, g32
        floats = [0x3F800000, 0x40490FDB, 0xC2C80000, 0x00000001, 0x80000000,
                  0x7F800000, 0xFF800000, 0x7FC00001, 0x7F800001, 0x3DCCCCCD,
                  0x447A0000, 0xC47A1235, 0x00800000, 0x7F7FFFFF]

        def fl():
            return rnd.choice(floats) if rnd.random() < 0.4 else rnd.getrandbits(32)
        # the stack, the globals, the tables
        for va in range(ESP - 0x200, ESP + 0x40, 4):
            g32(va, rnd.getrandbits(32))
        for pg in (0xD00, 0xCFF, 0x906, 0x7B7, 0xDB2, 0xCFC):
            page(pg)
        g32(0xD00050, SPR)
        g32(0xCFF21C, fl())
        g32(0x7B7918, rnd.choice((0x40000000, 0x3F000000, fl())))
        g32(0x906A70, rnd.getrandbits(32))
        g32(0xDB2BB8, STATE)
        g32(STATE + 0xA90, rnd.choice((0, 1)))
        g32(STATE + 0xA98, rnd.choice((0, 0, 5)))
        for L in range(42):
            cnt = rnd.choice((0, 0, -2, 1, 2, 3, 5, 9))
            g32(SPR + L * 0x1804, cnt)
            for i in range(max(cnt, 0) + 1):
                s = SPR + L * 0x1804 + i * 0x18
                for f in (4, 8, 0xC, 0x10, 0x14, 0x18):
                    g32(s + f, fl())
            ent, obj = LENT + L * 0x40, OBJS + L * 0x100
            g32(0xCFFC70 + 4 * L, ent)
            g32(ent + 8, obj)
            g16(ent + 0x18, rnd.choice((0, 0, 1)))
            g16(ent + 0x1A, rnd.choice((0, 2, 2, 1)))
            g32(ent + 0x14, rnd.getrandbits(32))
            vb = VBUF + L * 0x4000 + rnd.choice((0, 0x40, 0xFC0, 0xFA0, 0x7E0))
            g32(obj + 0x40, 0x80)
            g32(obj + 0x70, vb)
            g32(obj + 0x78, BYTES + L * 0x100)
            g32(obj + 0x7C, rnd.getrandbits(32))
            for va in range(vb, vb + 0x80 * (max(cnt, 0) + 1), 4):
                g32(va, rnd.getrandbits(32))
            for va in range(BYTES + L * 0x100, BYTES + L * 0x100 + 0x20, 4):
                g32(va, rnd.getrandbits(32))
        mu.mem_write(CTX, bytes(rnd.getrandbits(8) for _ in range(0x100)))
        mu.mem_write(CTX + 0x10, struct.pack('<II', ESP, ESP + 0x100))
        mu.mem_write(X87, bytes(rnd.getrandbits(8) for _ in range(0x60)))
        mu.mem_write(X87 + 0x60, struct.pack('<I', rnd.randrange(8)))
        mu.mem_write(DUMMY, bytes(0x1000))
        return mu

    START = B.ARM

    def run_one(self, text, seed):
        mu = self.build(text, seed)
        log, ticks, count = [], [0], [0]

        def ctxu(off):
            return struct.unpack('<I', mu.mem_read(CTX + off, 4))[0]

        def guest(va):
            p = struct.unpack('<Q', mu.mem_read(TABLE + 8 * (va >> 12), 8))[0]
            return p + (va & 0xFFF) if p else DUMMY

        def g32r(va):
            return struct.unpack('<I', mu.mem_read(guest(va), 4))[0]

        def clobber(keep=()):
            for r in range(0, 19):
                if r not in keep:
                    mu.reg_write(R.UC_ARM64_REG_X0 + r, GARB ^ r)

        def ret():
            mu.reg_write(R.UC_ARM64_REG_PC, mu.reg_read(R.UC_ARM64_REG_X30))

        def alloc():
            esp = ctxu(0x10)
            cnt, obj = g32r(esp + 4), g32r(esp + 8)
            log.append(('alloc', esp, cnt, obj, ctxu(0), ctxu(4), ctxu(8),
                        ctxu(0x14)))
            ok = ((obj >> 8) + cnt) % 5 != 0
            if ok and obj % 3 == 0:
                cur = g32r(obj + 0x70)
                mu.mem_write(guest(obj + 0x70), struct.pack('<I', cur + 0x80))
            mu.mem_write(CTX, struct.pack('<III', int(ok), 0x1111 * cnt, obj ^ 0x55))
            mu.mem_write(CTX + 0x10, struct.pack('<I', esp + 4))
            clobber()
            ret()

        def tick():
            ticks[0] += 1
            esp = ctxu(0x10)
            log.append(('tick', bytes(mu.mem_read(CTX, 0x28)),
                        bytes(mu.mem_read(X87, 0x68)),
                        tuple(g32r(esp + k) for k in range(4, 0x20, 4))))
            clobber()
            ret()

        def hook(uc, addr, size, _):
            count[0] += 1
            if addr == B.ALLOC:
                alloc()
            elif addr == B.TICK:
                tick()
        mu.hook_add(U.UC_HOOK_CODE, hook)
        for r in range(19, 29):
            mu.reg_write(R.UC_ARM64_REG_X0 + r, 0x4000 + r)
        for r in range(8, 16):
            mu.reg_write(R.UC_ARM64_REG_D0 + r, 0x5000 + r)
        mu.reg_write(R.UC_ARM64_REG_X29, 0x4029)
        mu.reg_write(R.UC_ARM64_REG_X30, SENTINEL)
        mu.reg_write(R.UC_ARM64_REG_SP, STACK_TOP - 0x100)
        mu.emu_start(self.START, SENTINEL, count=50_000_000)
        regs = ([mu.reg_read(R.UC_ARM64_REG_X0 + r) for r in range(19, 29)]
                + [mu.reg_read(R.UC_ARM64_REG_X29), mu.reg_read(R.UC_ARM64_REG_SP)]
                + [mu.reg_read(R.UC_ARM64_REG_D0 + r) for r in range(8, 16)])
        pages = {g: bytes(mu.mem_read(h, 0x1000)) for g, h in self.slots.items()}
        return dict(log=log, ticks=ticks[0], regs=regs, pages=pages,
                    ctx=bytes(mu.mem_read(CTX, 0x100)),
                    x87=bytes(mu.mem_read(X87, 0x68)),
                    pc=mu.reg_read(R.UC_ARM64_REG_PC), steps=count[0])

    def compare(self, seed):
        a = self.run_one(self.text0, seed)
        b = self.run_one(self.text1, seed)
        self.assertEqual(a['pc'], SENTINEL)
        self.assertEqual(b['pc'], SENTINEL)
        self.assertEqual(b['log'], a['log'])
        self.assertEqual(b['ticks'], a['ticks'])
        self.assertEqual(b['regs'], a['regs'])
        self.assertEqual(b['ctx'], a['ctx'])
        self.assertEqual(b['x87'], a['x87'])
        for g in sorted(a['pages']):
            self.assertEqual(b['pages'][g], a['pages'][g], 'guest page 0x%X' % (g << 12))
        return a['steps'], b['steps']

    def test_same_state(self):
        for seed in range(1, 13):
            with self.subTest(seed=seed):
                self.compare(seed)

    def test_hook_and_room(self):
        arms = [h[1] for h in B.HOOKS]
        for arm in arms:
            self.assertEqual(struct.unpack_from('<I', self.text1, arm)[0] >> 26, 0x05)
        lo, hi = B.dead_region(DUMP_MAIN, None)
        self.assertLessEqual(self.end, hi)
        diff = [va for va in range(0, len(self.text0), 4)
                if self.text0[va:va + 4] != self.text1[va:va + 4]]
        self.assertTrue(all(va in arms or lo <= va < hi for va in diff))


class TilePass(BgQuads):
    """BUILD 793: 0x64085C (depth 1) and 0x640C49 (depth 2), each tile
    tested against the view, flagged, and handed to the inlined
    add_page_tile. Random tile tables and view positions that put tiles on
    both sides of every edge test, flags/bytes/IDs that take every branch,
    and page slots whose counts move."""
    TILES, TABLE_PTR, COUNT, DEPTH1 = 0xCFFE00, 0xCFF3FC, 0xCFFDFC, True
    START = 0xA059D0
    WIDE = 0
    TA, IT = 0x00400000, 0x00480000
    PG = 0x00500000

    def build(self, text, seed):
        mu = BgQuads.build(self, text, seed)
        rnd = random.Random(seed * 7 + 3)
        g32 = self.g32

        def g16(va, v):
            self.gw(va, struct.pack('<H', v & 0xFFFF))

        def g8(va, v):
            self.gw(va, bytes([v & 0xFF]))
        x = rnd.randrange(-40, 420)
        y = rnd.randrange(-40, 300)
        g16(ESP + 4, x)
        g16(ESP + 8, y)
        g32(0xCFF1F0, rnd.choice((1, 2, 3, -1)))
        g32(0xCFF24C, rnd.choice((0, 5, -3, 16)))
        g32(0xCFF1D8, rnd.choice((0, 1)))
        g16(0xD00088, rnd.randrange(4))
        g32(0xCFF204, rnd.choice((0, 7, -12, 1 << 20)))
        g32(0xCFF208, rnd.choice((0, 3, -5)))
        g32(0xD00050, self.PG)
        for pg in range(64):           # 0x1058's page can move up to +18
            g32(self.PG + pg * 0x1804, rnd.randrange(6))
        n = rnd.choice((0, 1, 5, 17, 40))
        g16(self.COUNT, n)
        g32(self.TABLE_PTR, self.IT)
        g32(self.TILES, self.TA)
        for i in range(n):
            g32(self.IT + 4 * i, rnd.getrandbits(16) << 16 | rnd.randrange(12))
        for k in range(12):
            t = self.TA + k * 0x105C
            g16(t, x + rnd.randrange(-0x170 - self.WIDE, 0x20 + self.WIDE))
            g16(t + 2, y + rnd.randrange(-0x120 - self.WIDE, 0x20 + self.WIDE))
            for f in (4, 0x10, 0x14, 0x1040, 0x1044):
                g32(t + f, rnd.getrandbits(32))
            g16(t + 0x30, rnd.randrange(5))
            g8(t + 0x34, rnd.choice((0, 0, 1, 7, 200)))
            g8(t + 0x35, rnd.getrandbits(8))
            g32(t + 0x103C, rnd.choice((0, 0, 1)))
            g16(t + 0x1054, rnd.choice((0, 1, 2, 3, 4, 0x8000, rnd.getrandbits(16))))
            g16(t + 0x1056, rnd.randrange(42))
            g16(t + 0x1058, rnd.randrange(42))
        for b in range(256):
            g8(0xCC0E7A + b, rnd.choice((0, 0xFF, rnd.getrandbits(8))))
        for va in range(self.PG, self.PG + 64 * 0x1804, 0x1000):
            self.gw(va, b'\0')
        return mu

    def test_same_state(self):
        for seed in range(1, 41):
            with self.subTest(seed=seed):
                self.compare(seed)


class TilePass2(TilePass):
    TILES, TABLE_PTR, COUNT, DEPTH1 = 0xCFFE54, 0xCFF3F8, 0xCFFE5C, False
    START = 0xA06DE0


def widescreen(text, params, caves=0x9ADA00):
    """The two tile passes as ff7nx_uncrop / ff7nx_wsclamp leave them, with
    `params` = (origin y, top, left, right, bottom): the immediates, and
    each right/bottom site a `b` to `add w8, w8, #v; sub w10, w9, w8; b`
    through a veneer back (caves in the free dead body of x86 0x621E0E)."""
    oy, top, left, right, bottom = params
    at = caves
    for x86, (so, st, sl, sb, sr) in B.WS_SITES.items():
        struct.pack_into('<I', text, so, A.movz(9, oy))
        struct.pack_into('<I', text, st, A.sub_imm(9, 8, top))
        struct.pack_into('<I', text, sl, A.sub_imm(9, 8, left))
        for site, v in ((sb, bottom), (sr, right)):
            if not v:
                continue
            veneer = at + 0x10
            for k, w in enumerate((A.add_imm(8, 8, v), B.SUB_W10_W9_W8,
                                   A.b(at + 8, veneer))):
                struct.pack_into('<I', text, at + 4 * k, w)
            struct.pack_into('<I', text, veneer, A.b(veneer, site + 4))
            struct.pack_into('<I', text, site, A.b(site, at))
            at += 0x20
    return text


class WsTilePass(TilePass):
    """BUILD 795: the same, against the translation as the widescreen
    passes leave it (Patrick's ws-3d build: origin 232, left 400, right +64,
    bottom +16; top moved too, so every value is exercised)."""
    WS = (0xE8, 0x110, 0x190, 0x40, 0x10)
    WIDE = 0x60

    @classmethod
    def setUpClass(cls):
        blob = open(DUMP_MAIN, 'rb').read()
        cls.segs, cls.raw = AC.segments(blob)
        t0 = widescreen(bytearray(cls.raw[0]), cls.WS)
        t = bytearray(t0)
        lo, hi = B.dead_region(DUMP_MAIN, None)
        code = open(B.BLOB, 'rb').read()
        cls.entry, cls.stubs, cls.end = B.patch_text(t, lo, hi, code, B.entries())
        cls.text0, cls.text1 = bytes(t0), bytes(t)

    def test_params_read_back(self):
        for x86 in B.WS_SITES:
            self.assertEqual(B.ws_params(self.text0, x86), self.WS)
            self.assertEqual(B.ws_params(self.raw[0], x86), B.WS_STOCK)

    def test_hook_and_room(self):
        pass

    def test_refuses_other_shapes(self):
        t = bytearray(self.text0)
        so, st, sl, sb, sr = B.WS_SITES[0x64085C]
        struct.pack_into('<I', t, st, A.sub_imm(10, 8, 0x100))
        with self.assertRaises(ValueError):
            B.ws_params(t, 0x64085C)
        t = bytearray(self.text0)
        cave = B._b_target(struct.unpack_from('<I', t, sr)[0], sr)
        struct.pack_into('<I', t, cave, A.add_imm(9, 9, 0x40))
        with self.assertRaises(ValueError):
            B.ws_params(t, 0x64085C)


class WsTilePass2(WsTilePass):
    TILES, TABLE_PTR, COUNT, DEPTH1 = 0xCFFE54, 0xCFF3F8, 0xCFFE5C, False
    START = 0xA06DE0


if __name__ == '__main__':
    unittest.main()
