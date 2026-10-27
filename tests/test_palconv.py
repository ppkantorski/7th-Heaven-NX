#!/usr/bin/env python3
"""
tests/test_palconv.py -- ff7nx_palconv's native paletted conversion
writes what the driver's own +0x10D77D0 writes, both run under the
Unicorn emulator on the dumped module's real words.

The same random inputs for both: the palette at a guest address behind a
real page table (scattered host pages, so a palette that crosses a guest
page reads what stock reads; sometimes on the fallback page), indices past
the palette count (stock stops there), the colour key on and off, alpha
bytes of 0xFE, rows or columns of 0, fmt+0x10 of 0, no palette, and the
other formats (which go to the stock function through the C). Each run
starts at the target of load_texture's call (+0x10D6FC4): the conversion
in the stock text, the stub in the patched one. After each: the output
buffer and the bytes around it, the callee-saved registers, sp.
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
import ff7nx_palconv as P                                      # noqa: E402

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP_MAIN = os.path.join(_HERE, 'dump', 'exefs', 'main')

PAGES_GOT, DUMMY_GOT = 0x12CE8C0, 0x12CE8D8
TABLE = 0x21000000                 # 1M entries x 8
DUMMY = 0x22000000
POOL = 0x40000000                  # guest pages, scattered
SRC, OUT, FMT = 0x50000000, 0x51000000, 0x52000000
STACK_TOP = 0x30100000
SENTINEL = 0x2FFFF000


def _target(text):
    w = struct.unpack_from('<I', text, P.CALL)[0]
    off = w & 0x3FFFFFF
    if off & 0x2000000:
        off -= 0x4000000
    return P.CALL + 4 * off


@unittest.skipUnless(U is not None and os.path.exists(DUMP_MAIN),
                     'needs unicorn and dump/exefs/main')
class PalConv(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        blob = open(DUMP_MAIN, 'rb').read()
        cls.segs, cls.raw = AC.segments(blob)
        t = bytearray(cls.raw[0])
        P.check(t)
        lo, hi = P.dead_region(DUMP_MAIN, None)
        code = open(P.BLOB, 'rb').read()
        cls.entry, cls.stub, cls.end = P.patch_text(t, lo, hi, code, P.entries())
        cls.text0, cls.text1 = bytes(cls.raw[0]), bytes(t)

    def case(self, seed):
        """The inputs for one run, from the seed alone."""
        rnd = random.Random(seed)
        rows = rnd.choice((0, 1, 2, 7, 16, 33)) if rnd.random() < 0.9 else 256
        cols = rnd.choice((0, 1, 3, 8, 16, 31)) if rows != 256 else 256
        if rnd.random() < 0.05:
            rows, cols = rnd.choice(((0, 5), (5, 0)))
        n = rnd.choice((0, 1, 15, 16, 255, 256, rnd.getrandbits(8)))
        off = rnd.choice((0, 16, 32, 240, rnd.getrandbits(9)))
        hi = n + (1 if rnd.random() < 0.3 else 0)     # sometimes one past
        src = bytes(min(rnd.randint(0, hi), 255) if rnd.random() < 0.995 else 255
                    for _ in range(rows * cols))
        pal_g = rnd.choice((0x00D0_0FF0, 0x0090_0000, 0x0123_4567 & ~3,
                            0x02FF_F000 - 4 * rnd.randint(0, 300)))
        if rnd.random() < 0.03:
            pal_g = 0
        fmt = bytearray(rnd.getrandbits(8) for _ in range(0x80))
        struct.pack_into('<I', fmt, 0x10, 0 if rnd.random() < 0.05 else rnd.randint(1, 9))
        struct.pack_into('<I', fmt, 0x1C, n)
        struct.pack_into('<I', fmt, 0x24, pal_g)
        kind = 1 if rnd.random() < 0.85 else rnd.choice((0, 2, 3, 4))
        struct.pack_into('<I', fmt, 0x2C, kind)
        if kind != 1:
            # stock's direct-colour path: keep its divisors and shifts sane
            for f in (0x40, 0x44, 0x48, 0x4C):
                struct.pack_into('<I', fmt, f, rnd.getrandbits(32))
            for f in (0x50, 0x54, 0x58, 0x5C):
                struct.pack_into('<I', fmt, f, rnd.randint(0, 31))
            for f in (0x70, 0x74, 0x78, 0x7C):
                struct.pack_into('<I', fmt, f, rnd.choice((0, 1, 31, 255)))
            src = bytes(rnd.getrandbits(8) for _ in range(rows * cols * 4 + 8))
        entries = [rnd.choice((rnd.getrandbits(32), 0xFE000000 | rnd.getrandbits(24),
                               0xFF000000 | rnd.getrandbits(24)))
                   for _ in range(1024)]
        alpha = rnd.choice((0, 0xFF, 0x80, 0x7F)) << 24
        regs = dict(w5=rnd.getrandbits(32), w6=rnd.getrandbits(32),
                    w7=off, alpha=alpha | (rnd.getrandbits(24) if rnd.random() < 0.1 else 0))
        garbage = bytes(rnd.getrandbits(8) for _ in range(0x100))
        return rows, cols, src, bytes(fmt), pal_g, entries, regs, garbage, seed

    def run_one(self, text, case):
        rows, cols, src, fmt, pal_g, entries, regs, garbage, seed = case
        mu = U.Uc(U.UC_ARCH_ARM64, U.UC_MODE_ARM)
        top = max(s[1] + s[2] for s in self.segs) + 0x200000
        mu.mem_map(0, (top + 0xFFFFF) & ~0xFFFFF)
        mu.mem_write(0, text)
        for (_off, va, _sz), r in list(zip(self.segs, self.raw))[1:]:
            mu.mem_write(va, r)
        mu.mem_map(TABLE, 0x800000)
        mu.mem_map(DUMMY, 0x2000)
        mu.mem_map(POOL, 0x1000 * 64)
        mu.mem_map(SRC, 0x100000)
        mu.mem_map(OUT, 0x100000 * 5)
        mu.mem_map(FMT, 0x1000)
        mu.mem_map(STACK_TOP - 0x100000, 0x100000)
        mu.mem_map(SENTINEL, 0x1000)
        mu.mem_write(PAGES_GOT, struct.pack('<Q', TABLE))
        mu.mem_write(DUMMY_GOT, struct.pack('<Q', DUMMY))
        r2 = random.Random(seed * 31 + 7)
        perm = list(range(64))
        r2.shuffle(perm)
        mu.mem_write(DUMMY, bytes(r2.getrandbits(8) for _ in range(0x2000)))
        # the palette's guest pages (and the next ones), scattered; one in
        # six left unmapped (the fallback page)
        pal = struct.pack('<%dI' % len(entries), *entries)
        g = pal_g
        for k, gp in enumerate(range(g >> 12, ((g + len(pal)) >> 12) + 2)):
            if r2.random() < 1 / 6 and k:
                continue
            host = POOL + 0x1000 * perm[k]
            mu.mem_write(TABLE + 8 * gp, struct.pack('<Q', host))
            mu.mem_write(host, bytes(r2.getrandbits(8) for _ in range(0x1000)))
        for i in range(0, len(pal), 4):
            va = g + i
            ent = struct.unpack('<Q', mu.mem_read(TABLE + 8 * (va >> 12), 8))[0]
            if ent:
                mu.mem_write(ent + (va & 0xFFF), pal[i:i + 4])
        mu.mem_write(SRC, src)
        mu.mem_write(FMT, fmt)
        out = OUT + 0x100
        npx = max(rows * cols, 1)
        fill = (garbage * (4 * npx // len(garbage) + 2))
        mu.mem_write(OUT, fill[:4 * npx + 0x200])
        args = (SRC, out, rows, cols, FMT, regs['w5'], regs['w6'], regs['w7'])
        for r, v in enumerate(args):
            mu.reg_write(R.UC_ARM64_REG_X0 + r, v)
        sp = STACK_TOP - 0x200
        mu.mem_write(sp, struct.pack('<Q', regs['alpha']))
        for r in range(19, 29):
            mu.reg_write(R.UC_ARM64_REG_X0 + r, 0x1900 + r)
        mu.reg_write(R.UC_ARM64_REG_X29, 0x4029)
        mu.reg_write(R.UC_ARM64_REG_X30, SENTINEL)
        mu.reg_write(R.UC_ARM64_REG_SP, sp)
        mu.emu_start(_target(text), SENTINEL, count=60_000_000)
        return dict(pc=mu.reg_read(R.UC_ARM64_REG_PC),
                    out=bytes(mu.mem_read(OUT, 4 * npx + 0x200)),
                    regs=[mu.reg_read(R.UC_ARM64_REG_X0 + r) for r in range(19, 29)]
                    + [mu.reg_read(R.UC_ARM64_REG_X29), mu.reg_read(R.UC_ARM64_REG_SP)],
                    stack=bytes(mu.mem_read(sp, 0x10)))

    def compare(self, seed):
        case = self.case(seed)
        a = self.run_one(self.text0, case)
        b = self.run_one(self.text1, case)
        self.assertEqual(a['pc'], SENTINEL)
        self.assertEqual(b['pc'], SENTINEL)
        self.assertEqual(a['out'], b['out'], 'output differs, seed %d' % seed)
        self.assertEqual(a['regs'], b['regs'])
        self.assertEqual(a['stack'], b['stack'])
        return case, a

    def test_hooked(self):
        self.assertEqual(_target(self.text0), P.CONVERT)
        self.assertEqual(_target(self.text1), self.stub)
        self.assertLessEqual(self.end, P.dead_region(DUMP_MAIN, None)[1])

    def test_same_output(self):
        wrote = stopped = passed = 0
        for seed in range(400):
            with self.subTest(seed=seed):
                case, a = self.compare(seed)
                rows, cols, src, fmt = case[:4]
                kind = struct.unpack_from('<I', fmt, 0x2C)[0]
                n = struct.unpack_from('<I', fmt, 0x1C)[0]
                if kind != 1:
                    passed += 1
                elif rows * cols and any(i > n for i in src):
                    stopped += 1
                elif rows * cols:
                    wrote += 1
        # the random cases must cover all three kinds
        self.assertGreater(wrote, 100)
        self.assertGreater(stopped, 10)
        self.assertGreater(passed, 10)

    def test_full_page(self):
        """A 256x256 page, colour key on and off."""
        ran = 0
        for seed in range(1000, 1400):
            case = self.case(seed)
            if case[0] == 256:
                self.compare(seed)
                ran += 1
        self.assertGreater(ran, 10)


if __name__ == '__main__':
    unittest.main()
