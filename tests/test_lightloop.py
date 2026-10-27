#!/usr/bin/env python3
"""
tests/test_lightloop.py -- ff7nx_lightloop's native loop does what the
translated one did, run under arm64emu on the dumped module's real words.

Both run from the same state: the translated loop (+0xB4B954.., with
0x68DD1E's real body +0xB8E020) and the native one in its place. The vertex
routine and the timer tick are modelled (they record every call); the
translator is the paged model. After each run: the same calls with the same
arguments in the same order, the same ticks, the same guest memory byte for
byte, the same x86 context, the callee-saved registers and sp as they were,
and the same exit (+0xB4BD00). Vertex counts 0, 1, 2, 7, 40 and -3.

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

import arm64emu as EM                                          # noqa: E402
import ff7nx_audio_cave as AC                                  # noqa: E402
import ff7nx_lightloop as L                                    # noqa: E402

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP_MAIN = os.path.join(_HERE, 'dump', 'exefs', 'main')
VTX = 0x10F23C0
CTXH = 0x5000000            # host address of the x86 context
SP0 = 0x6000000
ESP = 0x00151000            # guest esp at the loop (ebp - 0x80)
EBP = ESP + 0x80


@unittest.skipUnless(os.path.exists(DUMP_MAIN), 'needs dump/exefs/main')
class LightLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        segs, raw = AC.segments(open(DUMP_MAIN, 'rb').read())
        cls.text = bytes(raw[0])
        t = bytearray(cls.text)
        cls.used, cls.vtx = L.patch_text(t)
        cls.patched = bytes(t)

    def code(self, text):
        out = {}
        for lo, hi in ((L.SITE, L.END), (L.DD1E, L.DD1E + 0xE0)):
            for va in range(lo, hi, 4):
                out[va] = struct.unpack_from('<I', text, va)[0]
        return out

    def run_one(self, text, count, seed):
        rnd = random.Random(seed)
        mem = EM.Mem()
        log = []

        def clobber(c, keep=()):
            for r in list(range(0, 19)):
                if r not in keep:
                    c.x[r] = rnd.getrandbits(64)
            c.n, c.z, c.c, c.v = 1, 0, 1, 1

        def vtx(c):
            args = tuple(c.x[i] & 0xFFFFFFFF for i in range(5))
            log.append(('vtx',) + args)
            out = c.guest_to_host(args[4])
            mem.setu(out, (args[0] * 3 + args[1] * 5 + args[2] + args[3]) & 0xFFFFFFFF, 4)
            clobber(c)
            c.x[0] = args[4]

        def tick(c):
            log.append(('tick',))
            clobber(c)
        c = EM.Cpu(mem, native={VTX: vtx, L.TICK: tick}, paged=True)
        g = c.guest_to_host
        # the guest: random stack and arrays, then the loop's locals
        r2 = random.Random(seed * 7 + 1)
        for va in range(ESP - 0x100, EBP + 0x40, 4):
            mem.setu(g(va), r2.getrandbits(32), 4)
        grp, light, nrm, vtxs = 0x00230F00, 0x00241000, 0x00260FF0, 0x00270FE8
        for va in range(nrm, nrm + 0x18 * max(count, 1) + 8, 4):
            mem.setu(g(va), r2.getrandbits(32), 4)
        for va in range(vtxs, vtxs + 0x20 * max(count, 1) + 8, 4):
            mem.setu(g(va), r2.getrandbits(32), 4)
        mem.setu(g(grp + 4), count, 4)
        mem.setu(g(EBP - 8), grp, 4)
        mem.setu(g(EBP - 0x1C), nrm, 4)
        mem.setu(g(EBP - 0x20), vtxs, 4)
        mem.setu(g(EBP + 0xC), light, 4)
        for off in range(0, 0x40, 4):
            mem.setu(CTXH + off, r2.getrandbits(32), 4)
        mem.setu(CTXH + 0x10, ESP, 4)
        mem.setu(CTXH + 0x14, EBP, 4)
        mem.setu(0x12CE2B0, CTXH, 8)     # the context's GOT slot (0x68DD1E reads it)
        c.x[8] = EBP                     # the translated block's w8 (ebp)
        c.x[19] = CTXH
        for r in range(20, 29):
            c.x[r] = 0x4000 + r
        c.x[29], c.sp = 0x4029, SP0
        end = c.run(L.SITE, None, code=self.code(text), max_steps=400000)
        regs = [c.x[r] for r in range(22, 30)] + [c.sp]
        return end, log, mem, regs

    def compare(self, count, seed):
        e0, l0, m0, r0 = self.run_one(self.text, count, seed)
        e1, l1, m1, r1 = self.run_one(self.patched, count, seed)
        self.assertEqual(e0, L.EXIT)
        self.assertEqual(e1, L.EXIT)
        self.assertEqual(l1, l0)
        n = max(count, 0)
        self.assertEqual(sum(1 for x in l0 if x[0] == 'vtx'), n)
        self.assertEqual(sum(1 for x in l0 if x[0] == 'tick'), n)
        self.assertEqual(r1, [0x4000 + r for r in range(22, 29)] + [0x4029, SP0])
        self.assertEqual(r0, r1)
        pages = set(m0.pages) | set(m1.pages)
        for p in sorted(pages):
            a = bytes(m0.pages.get(p, bytes(EM.PAGE)))
            b = bytes(m1.pages.get(p, bytes(EM.PAGE)))
            if p * EM.PAGE >= SP0 - 0x1000 and p * EM.PAGE < SP0:
                continue                     # the ARM stack below sp: scratch
            self.assertEqual(a, b, 'page 0x%X differs' % (p * EM.PAGE))

    def test_counts(self):
        for i, count in enumerate((0, 1, 2, 7, 40, -3)):
            with self.subTest(count=count):
                self.compare(count, 100 + i)

    def test_fits_and_routine(self):
        self.assertEqual(self.vtx, VTX)
        self.assertLessEqual(L.SITE + self.used, L.END)
        # only the loop's own words change
        diff = [va for va in range(0, len(self.text), 4)
                if self.text[va:va + 4] != self.patched[va:va + 4]]
        self.assertTrue(diff)
        self.assertTrue(all(L.SITE <= va < L.END for va in diff))

    def test_entered_only_from_its_branch(self):
        self.assertEqual(L.branches_into(self.text, L.SITE, L.END), [L.ENTRY_FROM])


if __name__ == '__main__':
    unittest.main()
