#!/usr/bin/env python3
"""
tests/test_frameprobe.py -- the frame-timing probe, executed under arm64emu
on the real layout it gets in the dumped module.

  * the recompiled-call-site wrapper is TRANSPARENT: flags, x9..x11 as the
    callee left them, x30, x16/x17 and every callee-saved register survive,
    and the callee's time lands in its section;
  * native entry/ret hooks time a function and replay the displaced word;
  * frame_end builds the record, the period ring, the sums, the buckets and
    the worst-16 table, clears the per-frame accumulators, and resets on a
    field change;
  * the booster edge 1 -> 3 (and only that edge) dumps: registers carry the
    magic, field, sums and periods, the live stack holds the worst frames 0..7
    and TLS holds 8..15, then svcBreak;
  * the dead body it borrows is proven dead and was unmodified, every word
    it writes disassembles, and only the intended hook words change.

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
import struct
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as EM                                          # noqa: E402
import ff7nx_audio_cave as AC                                  # noqa: E402
import ff7nx_frameprobe as P                                   # noqa: E402

DUMP_MAIN = os.path.join(_HERE, 'dump', 'exefs', 'main')
HOST = 0x800000              # host memory backing guest 0xCF0000..
SENTINEL = 0x5000000         # "return address" outside all code


def _layout():
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, 'main')
    rep = P.apply_to_nso(DUMP_MAIN, out, DUMP_MAIN)
    _s0, raw0 = AC.segments(open(DUMP_MAIN, 'rb').read())
    _s1, raw1 = AC.segments(open(out, 'rb').read())
    t0, t1 = raw0[0], raw1[0]
    lo = rep['region']
    code = {}
    for va in range(lo, lo + 4 * rep['words'] + 64, 4):
        code[va] = struct.unpack_from('<I', t1, va)[0]
    return rep, t0, t1, code


@unittest.skipUnless(os.path.exists(DUMP_MAIN), 'needs the dumped main')
class Probe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rep, cls.t0, cls.t1, cls.code = _layout()
        cls.base = cls.rep['base']

    # -------------------------------------------------------------- helpers
    def word(self, va):
        return struct.unpack_from('<I', self.t1, va)[0]

    def target(self, va):
        w = self.word(va)
        imm = w & 0x3FFFFFF
        if imm & (1 << 25):
            imm -= 1 << 26
        return va + imm * 4

    def cpu(self, natives=None):
        mem = EM.Mem()
        mem.setu(HOST + (P.FIELD_ID - 0xCF0000), 0x55, 2)

        def translate(c):
            c.x[0] = HOST + ((c.x[0] & 0xFFFFFFFF) - 0xCF0000)
            c.x[8] = c.x[9] = c.x[10] = 0x5A5A5A5A5A5A5A5A   # as the real one
        nat = {AC.GUEST_TRANSLATE: translate}
        nat.update(natives or {})
        c = EM.Cpu(mem, native=nat)
        c.sp = 0x700000
        c.x[30] = SENTINEL
        return c, mem

    def b(self, off, n=8):
        return self.mem.u(self.base + off, n)

    # ------------------------------------------------------------ wrapper
    def test_wrapper_is_transparent(self):
        site = P.CALL_SITES[4][0]              # DRAW
        wrap = self.target(site)
        callee = [t for s, sec, t in P._verify(self.t0)[0] if s == site][0]

        def fake(c):
            c.cntpct += 5000                   # the callee takes 5000 ticks
            c.x[0] = 0xABC
            c.x[9], c.x[10], c.x[11] = 91, 101, 111
        c, mem = self.cpu({callee: fake})
        self.mem = mem
        for r in range(32):
            if r not in (9, 10, 11, 30, 31):
                c.x[r] = 0x1000 + r
        c.n, c.z, c.c, c.v = 1, 0, 1, 1
        c.cntpct, c.cntpct_step = 100000, 7
        sp0 = c.sp
        end = c.run(wrap, self.code, start_pc=wrap)
        self.assertEqual(end, SENTINEL)
        self.assertEqual((c.n, c.z, c.c, c.v), (1, 0, 1, 1))
        self.assertEqual(c.x[0], 0xABC)
        self.assertEqual((c.x[9], c.x[10], c.x[11]), (91, 101, 111))
        for r in list(range(1, 9)) + list(range(12, 30)):
            self.assertEqual(c.x[r], 0x1000 + r, 'x%d' % r)
        self.assertEqual(c.sp, sp0)
        acc = self.b(P.ACC + 8 * P.DRAW)
        self.assertTrue(5000 <= acc <= 5000 + 7 * 3, acc)
        self.assertEqual(self.b(P.CNT + 4 * P.DRAW, 4), 1)

    def test_limit_wrapper_snapshots_the_limiters_inputs(self):
        site = [x for x, sec in P.CALL_SITES if sec == P.LIMIT][0]
        wrap = self.target(site)
        callee = [t for s_, sec, t in P._verify(self.t0)[0] if s_ == site][0]
        c, mem = self.cpu({callee: lambda cpu: setattr(
            cpu, 'cntpct', cpu.cntpct + 9000)})
        self.mem = mem
        page = 0x880000
        mem.setu(self.base + P.GPTR, page, 8)
        mem.setu(page + P.G_BASELINE, 1000000, 8)
        mem.setu(P.ACCUM, 1000000 + 12345, 8)
        mem.setu(page + P.G_DFLAG, 1, 4)
        mem.setu(page + P.G_DAMT, struct.unpack('<Q', struct.pack(
            '<d', -4800.0))[0], 8)
        for r in range(32):
            if r not in (30, 31):
                c.x[r] = 0x2000 + r
        c.n, c.z, c.c, c.v = 0, 1, 0, 1
        self.assertEqual(c.run(wrap, self.code, start_pc=wrap), SENTINEL)
        self.assertEqual((c.n, c.z, c.c, c.v), (0, 1, 0, 1))
        for r in range(30):
            self.assertEqual(c.x[r], 0x2000 + r, 'x%d' % r)
        self.assertEqual(self.b(P.EL), 12345)
        self.assertEqual(self.b(P.DFLAG, 4), 1)
        self.assertEqual(self.b(P.NLIM, 4), 1)
        self.assertEqual(self.b(P.ACC + 8 * P.LIMIT), 9000)
        # and frame_end turns them into the record's v3 slots and flags
        fe = self.rep['frame_end']
        c.x[30] = SENTINEL
        mem.setu(self.base + P.DAMT, struct.unpack('<Q', struct.pack(
            '<d', -4800.0))[0], 8)
        mem.setu(self.base + P.ACC + 8 * P.GL, 7000, 8)
        mem.setu(self.base + P.ACC + 8 * P.SWAP, 900, 8)
        rec = [mem.u(self.base + P.REC + 2 * k, 2) for k in range(16)]
        self.assertEqual(c.run(fe, self.code, start_pc=fe,
                               max_steps=200000), SENTINEL)
        rec = [mem.u(self.base + P.REC + 2 * k, 2) for k in range(16)]
        # v4: slots 6 and 9 are the GL / swap times, not el / drift
        self.assertEqual(rec[1 + P.GL], 7000 >> 4)
        self.assertEqual(rec[1 + P.SWAP], 900 >> 4)
        self.assertEqual(rec[1 + P.DEBT_SLOT], 4800 >> 4)
        flags = rec[12] >> 8
        self.assertEqual(flags & 1, 1)             # debt flag
        self.assertEqual(flags & 2, 0)             # (v4: no el sign)
        self.assertEqual(flags & 4, 4)             # debt amount negative
        self.assertEqual(flags >> 4, 1)            # one limiter call

    # ------------------------------------------------------ native hooks
    def test_native_entry_and_exit(self):
        entry, word, rets, sec = P.TIMED[0]
        e = self.target(entry)
        c, mem = self.cpu()
        self.mem = mem
        c.cntpct = 1000
        end = c.run(e, self.code, start_pc=e)
        self.assertEqual(end, entry + 4)
        self.assertEqual(c.sp, 0x700000 - 0x80, 'sub sp, sp, #0x80 replayed')
        self.assertEqual(self.b(P.START + 8 * sec), 1000)
        x = self.target(rets[0])
        c.cntpct = 4000
        self.assertEqual(c.run(x, self.code, start_pc=x), SENTINEL)
        self.assertEqual(self.b(P.ACC + 8 * sec), 3000)
        self.assertEqual(self.b(P.CNT + 4 * sec, 4), 1)

    def test_texture_counter(self):
        e = self.target(P.CREATE_TEXTURE)
        c, mem = self.cpu()
        self.mem = mem
        c.x[2], c.x[3] = 512, 512
        self.assertEqual(c.run(e, self.code, start_pc=e),
                         P.CREATE_TEXTURE + 4)
        self.assertEqual(self.b(P.CTEX, 4), 1)
        self.assertEqual(self.b(P.PIX, 4), 256)          # 512*512 >> 10
        self.assertEqual(c.sp, 0x700000 - 0xA0)

    def test_flip_entry_and_tail(self):
        e = self.target(P.FLIP)
        c, mem = self.cpu({P.TROPHY_FN: lambda cpu: setattr(
            cpu, 'cntpct', cpu.cntpct + 700)})
        self.mem = mem
        c.cntpct = 50000
        self.assertEqual(c.run(e, self.code, start_pc=e), P.FLIP + 4)
        self.assertEqual(c.sp, 0x700000 - 0x40, 'str d8, [sp, #-0x40]!')
        self.assertEqual(self.b(P.START + 8 * P.DRVFLIP), 50000)
        c.sp = 0x700000
        c.cntpct = 58000
        c.x[0], c.x[1] = 0x11, 0x22
        t = self.target(P.FLIP_TAIL)
        self.assertEqual(c.run(t, self.code, start_pc=t, max_steps=200000),
                         SENTINEL)
        self.assertEqual((c.x[0], c.x[1]), (0x11, 0x22))
        self.assertEqual(c.sp, 0x700000)
        # frame_end already moved this frame's DRVFLIP/TROPHY into the sums
        self.assertEqual(self.b(P.SUMS + 8 * P.DRVFLIP), 8000)

    # --------------------------------------------------------- frame_end
    def frame(self, c, fe, ticks, mult=1):
        self.mem.setu(P.MULT, mult, 4)
        c.cntpct += ticks
        c.x[30] = SENTINEL
        return c.run(fe, self.code, start_pc=fe, max_steps=200000)

    def test_frame_end(self):
        fe = self.rep['frame_end']
        c, mem = self.cpu()
        self.mem = mem
        c.cntpct = 10 ** 6
        per = int(16.6667e-3 * P.CPS)
        for i in range(40):
            mem.setu(self.base + P.ACC + 8 * P.LOGIC, 3200, 8)
            mem.setu(self.base + P.CNT + 4 * P.OGG, 1, 4)
            mem.setu(self.base + P.CPAL, 300, 4)
            self.assertEqual(self.frame(c, fe, per if i != 35 else per * 2),
                             SENTINEL)
        rec = [mem.u(self.base + P.REC + 2 * k, 2) for k in range(16)]
        self.assertEqual(rec[0], per >> 4)
        self.assertEqual(rec[1], 3200 >> 4)
        self.assertEqual(rec[11], 255 << 8)               # pal saturates at 255
        self.assertEqual(rec[12] & 0xFF, 1)               # one OGG open
        self.assertEqual(rec[14], 40)
        self.assertEqual(rec[15], 0x55)
        # accumulators cleared
        self.assertEqual(self.b(P.ACC + 8 * P.LOGIC), 0)
        self.assertEqual(self.b(P.CPAL, 4), 0)
        # sums over the field visit (first frame's period is a load: excluded)
        self.assertEqual(self.b(P.SUMS + 8 * P.LOGIC), 3200 * 40)
        self.assertEqual(self.b(P.TOT + 8, 4), 40)
        self.assertEqual(self.b(P.BK + 0, 4), 2)          # the load + frame 35
        # the worst frame of the visit (warm-up 30 excluded) is frame 35
        top = [mem.u(self.base + P.TOP + 32 * i, 2) for i in range(16)]
        self.assertEqual(max(top), (2 * per) >> 4)
        # field change resets
        mem.setu(HOST + (P.FIELD_ID - 0xCF0000), 0x66, 2)
        mem.setu(self.base + P.ACC + 8 * P.LOGIC, 3200, 8)
        self.frame(c, fe, per)
        self.assertEqual(self.b(P.FRAMES, 4), 1)
        self.assertEqual(max(mem.u(self.base + P.TOP + 32 * i, 2)
                             for i in range(16)), 0)
        self.assertEqual(self.b(P.SUMS + 8 * P.LOGIC), 3200)

    def test_trigger_only_on_1_to_3(self):
        fe = self.rep['frame_end']
        c, mem = self.cpu()
        self.mem = mem
        c.tls = 0x900000
        c.cntpct = 10 ** 6
        per = int(16.6667e-3 * P.CPS)
        for i in range(35):
            self.frame(c, fe, per, 1)
        self.frame(c, fe, per * 3 // 2, 1)          # a 25 ms frame
        self.assertEqual(self.frame(c, fe, per, 3 if False else 1), SENTINEL)
        # 3 -> 3 and 3 -> 1 do not dump; only 1 -> 3
        with self.assertRaises(EM.Udf):
            self.frame(c, fe, per, 3)
        self.assertEqual(c.x[0] & 0xFFFFFFFF, P.MAGIC)
        self.assertEqual(c.x[0] >> 32, P.VERSION)
        self.assertEqual((c.x[1] >> 32) & 0xFFFF, 0x55)
        self.assertEqual(c.x[1] & 0xFFFFFFFF, 38)
        sp = c.sp
        self.assertEqual(mem.u(sp, 8), mem.u(self.base + P.TOP, 8),
                         'worst frames 0..7 copied onto the live stack')
        self.assertEqual(mem.u(sp + 248, 8), mem.u(self.base + P.TOP + 248, 8))
        worst = mem.u(self.base + P.TOP, 2)
        self.assertTrue(any(mem.u(self.base + P.TOP + 32 * i, 2)
                            == (per * 3 // 2) >> 4 for i in range(16)))
        self.assertEqual(mem.u(0x900000, 8), mem.u(self.base + P.SUMS, 8),
                         'TLS starts with the per-visit sums block')
        self.assertEqual(mem.u(0x900000 + 128, 8),
                         mem.u(self.base + P.TOP + 256, 8))
        # the periods ride in x2..x7, FP, LR (the kernel zeroes x8..x28)
        self.assertEqual(c.x[2], mem.u(self.base + P.PRING, 8))
        self.assertEqual(c.x[30], mem.u(self.base + P.PRING + 56, 8))
        self.assertIsNotNone(worst)
        # ---- and the decoder reads it back out of a report-shaped text
        import io
        import frameprobe_read as R
        lines = ['Crash Report Information:', 'Crashed Thread Info:',
                 '    Registers:']
        for i in range(29):             # as the kernel reports them
            lines.append('        X[%02d]:                   %016X'
                         % (i, c.x[i] if i < 8 else 0))
        lines += ['        FP:                      %016X' % c.x[29],
                  '        LR:                      %016X' % c.x[30],
                  '        SP:                      %016X' % c.sp]

        def dump(title, addr):
            out = ['    %s:%s' % (title, ' ' * 30 + ' '.join(
                '%02x' % k for k in range(16)))]
            for off in range(0, 256, 16):       # Atmosphere 1.7 row layout
                out.append('                                 %012x %s' % (
                    addr + off, ' '.join('%02X' % mem.u(addr + off + k, 1)
                                         for k in range(16))))
            return out
        lines += dump('Stack Dump', c.sp) + dump('TLS Dump', c.tls)
        buf = io.StringIO()
        R.report('\n'.join(lines) + '\n', ['f%d' % i for i in range(200)],
                 buf)
        text = buf.getvalue()
        self.assertIn('FIELD  f85 (85)', text)
        self.assertIn('frames this visit: 38', text)
        self.assertIn('25.0', text)                  # the 25 ms frame
        self.assertIn('probe v4', text)
        self.assertIn('frames over: 16.9ms:2', text)
        self.assertIn('worst frames', text)

    # ------------------------------------------------------------ layout
    def test_only_hook_words_change_outside_the_dead_body(self):
        lo = self.rep['region']
        import nxmap
        # BUILD 786: the timing probe has its own dead body, outside every
        # ff7nx_deadspace part, so a probe build keeps every shipping pass
        own_lo, hi = nxmap.Main(DUMP_MAIN).extent(P.OWN_DEAD_X86)
        self.assertEqual(lo, own_lo + P.OWN_GUARD)
        hi -= P.OWN_TAIL
        import ff7nx_deadspace
        dlo, dhi = ff7nx_deadspace.region(DUMP_MAIN)
        self.assertTrue(hi <= dlo or lo >= dhi)
        changed = [va for va in range(0, len(self.t0), 4)
                   if not lo <= va < hi
                   and self.t0[va:va + 4] != self.t1[va:va + 4]]
        want = {s for s, _ in P.CALL_SITES}
        want |= {e for e, _w, _r, _s in P.TIMED}
        want |= {r for _e, _w, rets, _s in P.TIMED for r in rets}
        want |= {P.CREATE_TEXTURE, P.WRITE_PALETTE, P.FLIP, P.FLIP_TAIL}
        segs, raw = AC.segments(open(DUMP_MAIN, 'rb').read())
        want |= {va for _n, va, _g, _s in P.gl_hooks(segs, raw)[0]}
        self.assertEqual(set(changed), want)

    # ------------------------------------------------- v4: GL import stubs
    def test_gl_hooks_cover_the_driver(self):
        segs, raw = AC.segments(open(DUMP_MAIN, 'rb').read())
        hooks, skipped = P.gl_hooks(segs, raw)
        names = {n: sec for n, _v, _g, sec in hooks}
        self.assertEqual(names['eglSwapBuffers'], P.SWAP)
        for n in ('glDrawElements', 'glDrawArrays', 'glBufferData',
                  'glBindTexture', 'glUseProgram', 'glGetError'):
            self.assertEqual(names[n], P.GL, n)
        self.assertEqual(sorted(skipped), ['glBlitFramebuffer',
                                           'glCompressedTexSubImage2D',
                                           'glTexImage2D', 'glTexSubImage2D'])
        # every GL import is either hooked or deliberately left alone
        stubs = P.import_stubs(segs, raw)
        gl = {n for n in stubs if n.startswith('gl')}
        self.assertEqual(gl, {n for n in names if n.startswith('gl')}
                         | set(skipped))
        # and every one of them has a known argument count
        self.assertFalse(gl - set(P.GL_ARGS))

    def test_stub_cave_is_the_stub_plus_a_timer(self):
        segs, raw = AC.segments(open(DUMP_MAIN, 'rb').read())
        hooks = {n: (va, got, sec) for n, va, got, sec in
                 P.gl_hooks(segs, raw)[0]}
        for name in ('glDrawElements', 'eglSwapBuffers'):
            va, got, sec = hooks[name]
            # the stock stub, word for word
            w0 = struct.unpack_from('<I', self.t0, va)[0]
            self.assertEqual(w0 & 0x9F00001F, 0x90000010)
            cave = self.target(va)
            self.assertNotEqual(cave, va)
            FAKE = 0x6000000
            seen = {}

            def fake(cpu):
                seen['x16'], seen['x17'] = cpu.x[16], cpu.x[17]
                seen['args'] = [cpu.x[i] for i in range(8)]
                seen['sp'] = cpu.sp
                cpu.cntpct += 4000
                cpu.x[0] = 0xD0D0
                cpu.x[9] = 0x999
            c, mem = self.cpu({FAKE: fake})
            self.mem = mem
            mem.setu(got, FAKE, 8)
            for r in range(31):
                c.x[r] = 0x3000 + r
            c.x[30] = SENTINEL
            c.cntpct = 77
            sp0 = c.sp
            self.assertEqual(c.run(cave, self.code, start_pc=cave), SENTINEL)
            self.assertEqual(seen['x16'], got, 'x16 = the GOT slot, as the '
                             'stub leaves it for the lazy binder')
            self.assertEqual(seen['x17'], FAKE)
            self.assertEqual(seen['args'], [0x3000 + i for i in range(8)])
            self.assertEqual(seen['sp'], sp0 - 16)
            self.assertEqual(c.sp, sp0)
            self.assertEqual(c.x[0], 0xD0D0, 'the result survives')
            for r in list(range(1, 9)) + list(range(18, 30)):
                self.assertEqual(c.x[r], 0x3000 + r, 'x%d' % r)
            self.assertEqual(self.b(P.ACC + 8 * sec), 4000)
            self.assertEqual(self.b(P.CNT + 4 * sec, 4), 1)
            # a stack argument would have moved: none of the hooked
            # functions take one
            self.assertLessEqual(P.GL_ARGS.get(name, 2), 8)

    def test_every_written_word_disassembles(self):
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
        md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
        for va, w in self.code.items():
            if w:
                self.assertTrue(list(md.disasm(struct.pack('<I', w), va)),
                                'bad word %08X at 0x%X' % (w, va))


@unittest.skipUnless(os.path.exists(DUMP_MAIN), 'needs the dumped main')
class Profile(unittest.TestCase):
    """v5 (SEVENTH_NX_FRAME_PROBE=gl): the per-function GL profile."""

    @classmethod
    def setUpClass(cls):
        old = os.environ.get(P.ENV)
        os.environ[P.ENV] = 'gl'
        try:
            cls.rep, cls.t0, cls.t1, cls.code = _layout()
        finally:
            if old is None:
                os.environ.pop(P.ENV, None)
            else:
                os.environ[P.ENV] = old
        cls.base = cls.rep['base']
        segs, raw = AC.segments(open(DUMP_MAIN, 'rb').read())
        cls.hooks = P.gl_hooks(segs, raw)[0]

    def target(self, va):
        w = struct.unpack_from('<I', self.t1, va)[0]
        imm = w & 0x3FFFFFF
        if imm & (1 << 25):
            imm -= 1 << 26
        return va + imm * 4

    def test_profile_order_is_the_hook_order(self):
        self.assertTrue(self.rep['profile'])
        self.assertEqual([h[0] for h in self.hooks], P.profile_names())
        self.assertLessEqual(len(self.hooks), P.PROF_MAX)

    def test_time_count_repeats_and_dump(self):
        idx = {h[0]: i for i, h in enumerate(self.hooks)}
        FAKE = 0x6000000

        def fake(cpu):
            cpu.cntpct += 1600
            cpu.x[0] = 0x77
        mem = EM.Mem()
        mem.setu(HOST + (P.FIELD_ID - 0xCF0000), 0x55, 2)

        def translate(c):
            c.x[0] = HOST + ((c.x[0] & 0xFFFFFFFF) - 0xCF0000)
        c = EM.Cpu(mem, native={FAKE: fake, AC.GUEST_TRANSLATE: translate})
        c.sp = 0x700000
        c.tls = 0x900000
        for _n, _va, got, _s in self.hooks:
            mem.setu(got, FAKE, 8)

        def call(name, *args):
            va = [h[1] for h in self.hooks if h[0] == name][0]
            cave = self.target(va)
            for i, v in enumerate(args):
                c.x[i] = v
            for r in range(19, 29):
                c.x[r] = 0x4000 + r
            c.x[30] = SENTINEL
            sp0 = c.sp
            self.assertEqual(c.run(cave, self.code, start_pc=cave), SENTINEL)
            self.assertEqual(c.sp, sp0)
            self.assertEqual(c.x[0], 0x77)
            for r in range(19, 29):
                self.assertEqual(c.x[r], 0x4000 + r)

        # the first frame end enters the field (and clears the profile)
        fe = self.rep['frame_end']
        mem.setu(P.MULT, 1, 4)
        c.x[30] = SENTINEL
        c.cntpct = 10 ** 6
        self.assertEqual(c.run(fe, self.code, start_pc=fe,
                               max_steps=400000), SENTINEL)
        # glBindTexture(GL_TEXTURE_2D, 5) x3 then (.., 6): 2 repeats of 4
        for tex in (5, 5, 5, 6):
            call('glBindTexture', 0xDE1, tex)
        call('glDrawElements', 4, 300, 0x1403, 0)
        e = self.base + P.PROF + P.PROF_ENTRY * idx['glBindTexture']
        self.assertEqual(mem.u(e, 8), 4 * 1600)
        self.assertEqual(mem.u(e + 8, 4), 4)
        self.assertEqual(mem.u(e + 12, 4), 2)
        d = self.base + P.PROF + P.PROF_ENTRY * idx['glDrawElements']
        self.assertEqual(mem.u(d + 8, 4), 1)
        self.assertEqual(mem.u(d + 12, 4), 0)
        # and the frame total still lands in GL
        self.assertEqual(mem.u(self.base + P.ACC + 8 * P.GL, 8), 5 * 1600)
        # the booster edge on the next frame end: the dump carries it
        mem.setu(P.MULT, 3, 4)
        c.x[30] = SENTINEL
        with self.assertRaises(EM.Udf):
            c.run(fe, self.code, start_pc=fe, max_steps=400000)
        self.assertEqual(c.x[0] >> 32, 5)
        frames = mem.u(self.base + P.FRAMES, 4)
        stack = bytes(mem.u(c.sp + k, 1) for k in range(256))
        tls = bytes(mem.u(c.tls + k, 1) for k in range(256))
        import frameprobe_read as R
        rows = {r[0]: r for r in R.profile_rows(stack, tls)}
        b = rows['glBindTexture']
        self.assertEqual(b[2], 4 // frames)
        self.assertAlmostEqual(b[1], ((4 * 1600) >> 4) // frames * R.UNIT_MS)
        self.assertEqual(b[3], 50)
        self.assertEqual(rows['glDrawElements'][3], 0)


@unittest.skipUnless(os.path.exists(DUMP_MAIN), 'needs the dumped main')
class Driver(unittest.TestCase):
    """v6 (SEVENTH_NX_FRAME_PROBE=drv): the native driver profile."""

    @classmethod
    def setUpClass(cls):
        old = os.environ.get(P.ENV)
        os.environ[P.ENV] = 'drv'
        try:
            cls.rep, cls.t0, cls.t1, cls.code = _layout()
        finally:
            if old is None:
                os.environ.pop(P.ENV, None)
            else:
                os.environ[P.ENV] = old
        cls.base = cls.rep['base']
        cls.idx = {n: i for i, (_v, n)
                   in enumerate(P.DRV_FUNCS + P.DRV_EXTRA)}
        cls.va = {n: v for v, n in P.DRV_FUNCS + P.DRV_EXTRA}

    def target(self, va):
        w = struct.unpack_from('<I', self.t1, va)[0]
        self.assertEqual(w >> 26, 0x05, 'no b at +0x%X' % va)
        imm = w & 0x3FFFFFF
        if imm & (1 << 25):
            imm -= 1 << 26
        return va + imm * 4

    def setUp(self):
        self.mem = EM.Mem()
        self.mem.setu(HOST + (P.FIELD_ID - 0xCF0000), 0x55, 2)

        def translate(c):
            c.x[0] = HOST + ((c.x[0] & 0xFFFFFFFF) - 0xCF0000)
        self.c = EM.Cpu(self.mem, native={AC.GUEST_TRANSLATE: translate})
        self.c.sp = 0x700000
        self.c.tls = 0x900000

    def enter(self, name, ret):
        """Run `name`'s entry hook with x30 = ret; returns where it went."""
        c = self.c
        c.x[30] = ret
        cave = self.target(self.va[name])
        return c.run(cave, self.code, start_pc=cave)

    def leave(self):
        c = self.c
        return c.run(c.x[30], self.code, start_pc=c.x[30])

    def entry(self, name):
        return self.mem.u(self.base + P.DPROF + P.DPROF_ENTRY
                          * self.idx[name], 8), \
            self.mem.u(self.base + P.DPROF + P.DPROF_ENTRY * self.idx[name]
                       + 8, 4), \
            self.mem.u(self.base + P.DPROF + P.DPROF_ENTRY * self.idx[name]
                       + 16, 8)

    def top(self):
        e = self.base + P.DPROF + P.DPROF_ENTRY * len(P.DRV_FUNCS
                                                      + P.DRV_EXTRA)
        return self.mem.u(e, 8), self.mem.u(e + 8, 4), self.mem.u(e + 16, 8)

    def gl(self, ticks):
        g = self.base + P.GLMONO
        self.mem.setu(g, self.mem.u(g, 8) + ticks, 8)

    def test_every_listed_function_is_hooked(self):
        self.assertTrue(self.rep['drv'])
        self.assertEqual(self.rep['drv_skipped'], [])
        self.assertEqual(self.rep['drv_hooked'],
                         [n for _v, n in P.DRV_FUNCS + P.DRV_EXTRA]
                         + ['field_78 game-code calls (12 sites)',
                            'game calls (42 sites)'])
        self.assertEqual(P.drv_names()[len(P.DRV_FUNCS + P.DRV_EXTRA)],
                         P.DRV_TOP_NAME)
        for va, _n in P.DRV_FUNCS + P.DRV_EXTRA:
            self.target(va)

    def test_time_calls_and_gl_inside(self):
        c = self.c
        for r in range(9):
            c.x[r] = 0x3000 + r
        c.cntpct = 10 ** 6
        sp0 = c.sp
        # setmatrix starts `str x21, [sp, #-0x30]!`: replayed, then on
        self.assertEqual(self.enter('setmatrix', SENTINEL),
                         self.va['setmatrix'] + 4)
        self.assertEqual(c.sp, sp0 - 0x30)
        self.assertEqual(self.mem.u(c.sp, 8), c.x[21])
        for r in range(9):
            self.assertEqual(c.x[r], 0x3000 + r, 'x%d' % r)
        self.assertNotEqual(c.x[30], SENTINEL)
        c.cntpct += 5000                        # the body, 1600 of it GL
        self.gl(1600)
        c.x[0], c.x[1], c.x[8] = 0xAA, 0xBB, 0xCC
        self.assertEqual(self.leave(), SENTINEL)
        self.assertEqual((c.x[0], c.x[1], c.x[8]), (0xAA, 0xBB, 0xCC))
        self.assertEqual(c.x[30], SENTINEL)
        t, n, g = self.entry('setmatrix')
        self.assertEqual((t, n, g), (5000, 1, 1600))
        self.assertEqual(self.top(), (5000, 1, 1600))

    def test_nested_calls_count_once_outermost(self):
        c = self.c
        c.cntpct = 10 ** 6
        self.enter('field_78', SENTINEL)
        outer = c.x[30]
        c.cntpct += 1000
        self.enter('setmatrix', 0x5100000)
        c.cntpct += 300
        self.gl(200)
        self.assertEqual(self.leave(), 0x5100000)
        c.x[30] = outer
        c.cntpct += 700
        self.assertEqual(self.leave(), SENTINEL)
        self.assertEqual(self.entry('setmatrix'), (300, 1, 200))
        self.assertEqual(self.entry('field_78'), (2000, 1, 200))
        self.assertEqual(self.top(), (2000, 1, 200))

    def test_recursion_runs_untimed(self):
        c = self.c
        c.cntpct = 10 ** 6
        self.enter('draw_deferred', SENTINEL)
        outer = c.x[30]
        self.enter('draw_deferred', 0x5200000)
        self.assertEqual(c.x[30], 0x5200000)     # left alone
        c.x[30] = outer
        c.cntpct += 400
        self.assertEqual(self.leave(), SENTINEL)
        self.assertEqual(self.entry('draw_deferred')[:2], (400, 1))
        # and it is free again
        self.enter('draw_deferred', SENTINEL)
        self.assertNotEqual(c.x[30], SENTINEL)

    def test_moved_cbz_and_adrp(self):
        c = self.c
        va = self.va['setrenderstate_6c']
        w = struct.unpack_from('<I', self.t0, va)[0]
        imm = (w >> 5) & 0x7FFFF
        taken = va + imm * 4
        c.x[0] = 0
        self.assertEqual(self.enter('setrenderstate_6c', SENTINEL), taken)
        self.leave()
        c.x[0] = 7
        self.assertEqual(self.enter('setrenderstate_6c', SENTINEL), va + 4)
        self.leave()
        va = self.va['setviewport']
        w = struct.unpack_from('<I', self.t0, va)[0]
        imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
        if imm & (1 << 20):
            imm -= 1 << 21
        self.assertEqual(self.enter('setviewport', SENTINEL), va + 4)
        self.assertEqual(c.x[w & 0x1F], (va & ~0xFFF) + (imm << 12))

    def test_field_78_callees(self):
        sites = P.sub_sites(self.t0)
        self.assertEqual(len(sites), 12)
        top = len(P.DRV_FUNCS + P.DRV_EXTRA)
        seen = []

        def callx(cpu):
            cpu.cntpct += 2000
            seen.append((cpu.x[0], cpu.mem.u(cpu.sp, 4)))
            cpu.x[0] = 0x55
            for r in range(9, 18):
                cpu.x[r] = 0xDEAD
        self.c.native[0xA060] = callx
        c = self.c
        c.cntpct = 10 ** 6
        for site, k, is_b in sites:
            if k != 0:
                continue
            w = struct.unpack_from('<I', self.t1, site)[0]
            self.assertEqual(w >> 26, 0x05 if is_b else 0x25)
            imm = w & 0x3FFFFFF
            if imm & (1 << 25):
                imm -= 1 << 26
            cave = site + imm * 4
            c.x[0] = 0x6A2865
            self.mem.setu(c.sp, 0x1234, 4)          # a stack argument
            sp0 = c.sp
            c.x[30] = SENTINEL
            self.assertEqual(c.run(cave, self.code, start_pc=cave), SENTINEL)
            self.assertEqual(c.x[0], 0x55)
            self.assertEqual(c.sp, sp0)
        n = sum(1 for _s, k, _b in sites if k == 0)
        self.assertEqual(seen, [(0x6A2865, 0x1234)] * n)
        e = self.base + P.DPROF + P.DPROF_ENTRY * (top + 1)
        self.assertEqual(self.mem.u(e + 8, 4), n)
        self.assertEqual(self.mem.u(e, 8), 2000 * n)
        self.assertEqual(self.top()[1], 0)          # not an outermost call

    def test_draw_everything_callees(self):
        """v6e: each call out of draw_everything is timed in its callee's
        row, with the outermost driver time inside it in the GL slot."""
        sites = P.game_sites(self.t0)
        self.assertEqual(len(sites), 42)
        top = len(P.DRV_FUNCS + P.DRV_EXTRA)
        topt = self.base + P.DPROF + P.DPROF_ENTRY * top
        first = P.game_first()
        self.assertEqual(P.drv_names()[first], P.GAME_GROUPS[0][2][0][1])
        self.assertEqual(P.drv_names()[-1], P.GAME_GROUPS[-1][2][-1][1])
        self.assertLessEqual(len(P.drv_names()), P.DRV_DUMP_N)

        def callee(cpu):
            cpu.cntpct += 900
            self.mem.setu(topt, self.mem.u(topt, 8) + 250, 8)   # driver
            cpu.x[0] = 0x77
            for r in range(9, 18):
                cpu.x[r] = 0xDEAD
        c = self.c
        for _s, _k, tgt in sites:
            c.native[tgt] = callee
        c.cntpct = 10 ** 6
        for site, k, tgt in sites:
            w = struct.unpack_from('<I', self.t1, site)[0]
            self.assertEqual(w >> 26, 0x25, 'site +0x%X' % site)
            imm = w & 0x3FFFFFF
            if imm & (1 << 25):
                imm -= 1 << 26
            cave = site + imm * 4
            sp0 = c.sp
            c.x[0] = 0x11
            c.x[30] = SENTINEL
            self.assertEqual(c.run(cave, self.code, start_pc=cave), SENTINEL)
            self.assertEqual((c.x[0], c.sp), (0x77, sp0))
        from collections import Counter
        per = Counter(k for _s, k, _t in sites)
        for k, n in per.items():
            e = self.base + P.DPROF + P.DPROF_ENTRY * (first + k)
            self.assertEqual(self.mem.u(e + 8, 4), n)
            self.assertEqual(self.mem.u(e, 8), 900 * n)
            self.assertEqual(self.mem.u(e + 16, 8), 250 * n)

    def test_relocate_refuses_pc_relative(self):
        self.assertIsNone(P.relocate_first(0x17FFFFC8, 0x10DBB40, 0x2000))
        self.assertIsNone(P.relocate_first(0x94000010, 0x1000, 0x2000))
        self.assertIsNone(P.relocate_first(0x58000040, 0x1000, 0x2000))
        self.assertIsNone(P.relocate_first(0x10000040, 0x1000, 0x2000))
        self.assertIsNone(P.relocate_first(0x54000040, 0x1000, 0x2000))
        self.assertIsNone(P.relocate_first(0x36000040, 0x1000, 0x2000))
        self.assertEqual(len(P.relocate_first(0xD10103FF, 0x1000, 0x2000)),
                         2)

    def test_dump_and_reader(self):
        c = self.c
        fe = self.rep['frame_end']
        self.mem.setu(P.MULT, 1, 4)
        c.x[30] = SENTINEL
        c.cntpct = 10 ** 6
        self.assertEqual(c.run(fe, self.code, start_pc=fe,
                               max_steps=400000), SENTINEL)
        for _ in range(3):
            self.enter('field_78', SENTINEL)
            c.cntpct += 3200
            self.gl(1600)
            self.leave()
        self.mem.setu(P.MULT, 3, 4)
        c.x[30] = SENTINEL
        with self.assertRaises(EM.Udf):
            c.run(fe, self.code, start_pc=fe, max_steps=400000)
        self.assertEqual(c.x[0] >> 32, P.DRV_VERSION)
        frames = self.mem.u(self.base + P.FRAMES, 4)
        stack = bytes(self.mem.u(c.sp + k, 1) for k in range(256))
        tls = bytes(self.mem.u(c.tls + k, 1) for k in range(256))
        import frameprobe_read as R
        rows = {r[0]: r for r in R.drv_rows(stack, tls)}
        name, ms, n, gl = rows['field_78']
        self.assertEqual(n, 3 // frames)
        self.assertAlmostEqual(ms, ((3 * 3200) >> 4) // frames * R.UNIT_MS)
        self.assertAlmostEqual(gl, ((3 * 1600) >> 4) // frames * R.UNIT_MS)
        self.assertEqual(rows[P.DRV_TOP_NAME][2], n)


@unittest.skipUnless(os.path.exists(DUMP_MAIN), 'needs the dumped main')
class Gpu(Driver):
    """v9 (SEVENTH_NX_FRAME_PROBE=gpu): a 1x1 glReadPixels through its GOT
    slot before and after every outermost driver call."""
    FAKE = 0x6100000
    WAIT = (700, 900, 1100, 1300)

    @classmethod
    def setUpClass(cls):
        old = os.environ.get(P.ENV)
        os.environ[P.ENV] = 'gpu'
        try:
            cls.rep, cls.t0, cls.t1, cls.code = _layout()
        finally:
            if old is None:
                os.environ.pop(P.ENV, None)
            else:
                os.environ[P.ENV] = old
        cls.base = cls.rep['base']
        cls.idx = {n: i for i, (_v, n)
                   in enumerate(P.DRV_FUNCS + P.DRV_EXTRA)}
        cls.va = {n: v for v, n in P.DRV_FUNCS + P.DRV_EXTRA}
        # v11: the split passes, in the game rows' slots
        for k, (v, n) in enumerate(P.GPU_SPLIT):
            cls.idx[n] = P.game_first() + k
            cls.va[n] = v
        segs, raw = AC.segments(open(DUMP_MAIN, 'rb').read())
        cls.got = P.import_stubs(segs, raw)[P.GL_READ_PIXELS][1]

    def setUp(self):
        self.mem = EM.Mem()
        self.mem.setu(HOST + (P.FIELD_ID - 0xCF0000), 0x55, 2)
        self.mem.setu(self.got, self.FAKE, 8)
        self.reads = []

        def translate(c):
            c.x[0] = HOST + ((c.x[0] & 0xFFFFFFFF) - 0xCF0000)

        def read_pixels(c):
            self.reads.append(([c.x[i] for i in range(6)], c.x[6] - c.sp,
                               c.x[16], c.x[17]))
            c.cntpct += self.WAIT[(len(self.reads) - 1) % len(self.WAIT)]
            for r in range(19):                 # the callee's to clobber
                c.x[r] = 0xDEAD0000 + r
            c.x[30] = 0xBAD
        self.c = EM.Cpu(self.mem, native={AC.GUEST_TRANSLATE: translate,
                                          self.FAKE: read_pixels})
        self.c.sp = 0x700000
        self.c.tls = 0x900000

    def gpu(self, i):
        return self.mem.u(self.base + P.DPROF + P.DPROF_ENTRY * i + 24, 8)

    def test_every_listed_function_is_hooked(self):
        self.assertTrue(self.rep['gpu'])
        self.assertEqual(self.rep['drv_skipped'], [])
        # v11: the split passes instead of the game's call sites
        self.assertEqual(self.rep['drv_hooked'],
                         [n for _v, n in P.DRV_FUNCS + P.DRV_EXTRA
                          + P.GPU_SPLIT]
                         + ['field_78 game-code calls (12 sites)'])
        for va, _n in P.DRV_FUNCS + P.DRV_EXTRA + P.GPU_SPLIT:
            self.target(va)
        names = P.drv_names(P.GPU_VERSION)
        for k, (_v, n) in enumerate(P.GPU_SPLIT):
            self.assertEqual(names[P.game_first() + k], n)
        self.assertEqual(names[P.gpu_row(5)], P.GPU_ROWS[5])
        self.assertLessEqual(len(P.drv_names(P.GPU_VERSION)), P.DRV_DUMP_N)
        self.assertEqual(P.drv_names(P.GPU_VERSION)[P.gpu_row(0)],
                         P.GPU_ROWS[0])

    def test_time_calls_and_gl_inside(self):
        # the v6 test, with the two waits: neither is in the call's time
        c = self.c
        for r in range(9):
            c.x[r] = 0x3000 + r
        c.cntpct = 10 ** 6
        self.assertEqual(self.enter('setmatrix', SENTINEL),
                         self.va['setmatrix'] + 4)
        for r in range(9):
            self.assertEqual(c.x[r], 0x3000 + r, 'x%d' % r)
        self.assertEqual(len(self.reads), 1)
        args, buf, x16, x17 = self.reads[0]
        self.assertEqual(args, [0, 0, 1, 1, 0x1908, 0x1401])
        self.assertTrue(0 <= buf <= P.GSYNC_FRAME - 4, hex(buf))
        self.assertEqual((x16, x17), (self.got, self.FAKE))
        c.cntpct += 5000
        self.gl(1600)
        c.x[0], c.x[1], c.x[8] = 0xAA, 0xBB, 0xCC
        self.assertEqual(self.leave(), SENTINEL)
        self.assertEqual((c.x[0], c.x[1], c.x[8]), (0xAA, 0xBB, 0xCC))
        self.assertEqual(c.x[30], SENTINEL)
        self.assertEqual(len(self.reads), 2)
        i, top = self.idx['setmatrix'], len(P.DRV_FUNCS + P.DRV_EXTRA)
        self.assertEqual(self.entry('setmatrix'), (5000, 1, 1600))
        self.assertEqual(self.top(), (5000, 1, 1600))
        self.assertEqual(self.gpu(P.gpu_row(0)), 700)
        self.assertEqual(self.gpu(i), 900)
        self.assertEqual(self.gpu(top), 900)

    def test_nested_calls_count_once_outermost(self):
        super().test_nested_calls_count_once_outermost()
        self.assertEqual(len(self.reads), 2, 'only the outermost call waits')
        self.assertEqual(self.gpu(self.idx['field_78']), 900)
        self.assertEqual(self.gpu(self.idx['setmatrix']), 0)

    def test_recursion_runs_untimed(self):
        super().test_recursion_runs_untimed()

    def test_draw_everything_callees(self):
        # v11: a gpu build leaves the game's call sites stock (their rows
        # hold the split passes)
        for site, _k, _tgt in P.game_sites(self.t0):
            self.assertEqual(struct.unpack_from('<I', self.t1, site),
                             struct.unpack_from('<I', self.t0, site),
                             '+0x%X' % site)

    def test_split_pass_inside_a_call(self):
        # v11: end_scene > the capture blit > the blit. Each split pass
        # waits before (onto "inside callers" and top) and after (its row
        # and top); end_scene's row keeps what is left.
        c = self.c
        c.cntpct = 10 ** 6
        top = len(P.DRV_FUNCS + P.DRV_EXTRA)
        self.enter('end_scene', SENTINEL)
        outer = c.x[30]
        c.cntpct += 100
        cap = 'capture blit 0x10DACB0'
        self.assertEqual(self.enter(cap, 0x5100000), self.va[cap] + 4)
        mid = c.x[30]
        blit = 'blit 0x10DF010'
        self.enter(blit, 0x5180000)
        c.cntpct += 50
        self.assertEqual(self.leave(), 0x5180000)
        c.x[30] = mid
        c.cntpct += 200
        c.x[0] = 0x77
        self.assertEqual(self.leave(), 0x5100000)
        self.assertEqual(c.x[0], 0x77)
        c.x[30] = outer
        self.assertEqual(self.leave(), SENTINEL)
        self.assertEqual(len(self.reads), 6)
        # waits in order: 700 end_scene in, 900 cap in, 1100 blit in,
        # 1300 blit out, 700 cap out, 900 end_scene out
        self.assertEqual(self.gpu(P.gpu_row(0)), 700)
        self.assertEqual(self.gpu(P.gpu_row(5)), 900 + 1100)
        self.assertEqual(self.gpu(self.idx[blit]), 1300)
        self.assertEqual(self.gpu(self.idx[cap]), 700)
        self.assertEqual(self.gpu(self.idx['end_scene']), 900)
        self.assertEqual(self.gpu(top), 900 + 1100 + 1300 + 700 + 900)
        self.assertEqual(self.entry(blit)[:2], (50, 1))
        self.assertEqual(self.entry(cap)[1], 1)
        self.assertEqual(self.top()[1], 1)

    def test_split_pass_outside_the_driver(self):
        # the render-mode switch, called straight from translated code:
        # an outermost call (row 0 before, its row and top after)
        c = self.c
        c.cntpct = 10 ** 6
        sw = 'switch 0x10DF6E0 (render mode)'
        self.enter(sw, SENTINEL)
        c.cntpct += 300
        self.assertEqual(self.leave(), SENTINEL)
        self.assertEqual(len(self.reads), 2)
        self.assertEqual(self.gpu(P.gpu_row(0)), 700)
        self.assertEqual(self.gpu(P.gpu_row(5)), 0)
        self.assertEqual(self.gpu(self.idx[sw]), 900)
        self.assertEqual(self.gpu(len(P.DRV_FUNCS + P.DRV_EXTRA)), 900)
        self.assertEqual(self.entry(sw)[:2], (300, 1))

    def test_load_texture_waits(self):
        # v10: a wait at load_texture's entry and at its ret
        c = self.c
        c.cntpct = 10 ** 6
        for r in range(9):
            c.x[r] = 0x3000 + r
        cave = self.target(0x10D6BC0)
        self.assertEqual(c.run(cave, self.code, start_pc=cave),
                         0x10D6BC4)
        for r in range(9):
            self.assertEqual(c.x[r], 0x3000 + r, 'x%d' % r)
        self.assertEqual(len(self.reads), 1)
        c.cntpct += 4000
        c.x[0], c.x[30] = 0xAA, SENTINEL
        ret = self.target(0x10D7048)
        self.assertEqual(c.run(ret, self.code, start_pc=ret), SENTINEL)
        self.assertEqual(c.x[0], 0xAA)
        self.assertEqual(len(self.reads), 2)
        self.assertEqual(self.gpu(P.gpu_row(3)), 700)
        self.assertEqual(self.gpu(P.gpu_row(4)), 900)
        self.assertEqual(self.mem.u(self.base + P.ACC + 8 * P.TEXLOAD, 8),
                         4000, 'the waits are not load_texture time')

    def test_dump_and_reader(self):
        c = self.c
        fe = self.rep['frame_end']
        self.mem.setu(P.MULT, 1, 4)
        c.x[30] = SENTINEL
        c.cntpct = 10 ** 6
        self.assertEqual(c.run(fe, self.code, start_pc=fe,
                               max_steps=400000), SENTINEL)
        for _ in range(3):
            self.enter('field_78', SENTINEL)
            c.cntpct += 3200
            self.leave()
        self.mem.setu(P.MULT, 3, 4)
        c.x[30] = SENTINEL
        with self.assertRaises(EM.Udf):
            c.run(fe, self.code, start_pc=fe, max_steps=400000)
        self.assertEqual(c.x[0] >> 32, P.GPU_VERSION)
        frames = self.mem.u(self.base + P.FRAMES, 4)
        stack = bytes(self.mem.u(c.sp + k, 1) for k in range(256))
        tls = bytes(self.mem.u(c.tls + k, 1) for k in range(256))
        import frameprobe_read as R
        rows = {r[0]: r for r in R.drv_rows(stack, tls, P.GPU_VERSION)}
        _name, ms, n, gpu = rows['field_78']
        self.assertEqual(n, 3 // frames)
        self.assertAlmostEqual(ms, ((3 * 3200) >> 4) // frames * R.UNIT_MS)
        # waits 700/900/1100/1300 cycle: the exits got 900, 1300, 900
        self.assertAlmostEqual(gpu, ((900 + 1300 + 900) >> 4) // frames
                               * R.UNIT_MS)
        self.assertAlmostEqual(rows[P.GPU_ROWS[0]][3],
                               ((700 + 1100 + 700) >> 4) // frames
                               * R.UNIT_MS)
        out = []
        R._gpu_profile(R.drv_rows(stack, tls, P.GPU_VERSION),
                       [None] * 16, frames, out.append)
        text = ''.join(out)
        self.assertIn('field_78', text)
        self.assertIn(P.GPU_ROWS[2], text)


@unittest.skipUnless(os.path.exists(DUMP_MAIN), 'needs the dumped main')
class Layer(unittest.TestCase):
    """v12 (SEVENTH_NX_FRAME_PROBE=layer): the foreground-layer boxes."""
    CUR, LAYER, FLAG = 0x6200000, 0x6200100, 0x6200200
    RT_LAYER, RT_MAIN = 0x7700000, 0x7710000
    VERTS = 0x6300000

    @classmethod
    def setUpClass(cls):
        old = os.environ.get(P.ENV)
        os.environ[P.ENV] = 'layer'
        try:
            cls.rep, cls.t0, cls.t1, cls.code = _layout()
        finally:
            if old is None:
                os.environ.pop(P.ENV, None)
            else:
                os.environ[P.ENV] = old
        cls.base = cls.rep['base']
        cls.first = P.game_first()

    def setUp(self):
        self.mem = EM.Mem()
        m = self.mem
        m.setu(P.GOT_CUR_RT, self.CUR, 8)
        m.setu(P.GOT_LAYER_RT, self.LAYER, 8)
        m.setu(P.GOT_MERGE_FLAG, self.FLAG, 8)
        m.setu(self.LAYER, self.RT_LAYER, 8)
        m.setu(self.CUR, self.RT_LAYER, 8)
        m.setu(self.FLAG, 1, 1)
        self.c = EM.Cpu(self.mem)
        self.c.sp = 0x700000

    def target(self, va):
        w = struct.unpack_from('<I', self.t1, va)[0]
        self.assertEqual(w >> 26, 0x05, 'no b at +0x%X' % va)
        imm = w & 0x3FFFFFF
        if imm & (1 << 25):
            imm -= 1 << 26
        return va + imm * 4

    def row(self, k):
        e = self.base + P.DPROF + P.DPROF_ENTRY * (self.first + k)
        return self.mem.u(e, 8), self.mem.u(e + 8, 4)

    def draw(self, pts, vtype=3, w25=1):
        c = self.c
        for i, (x, y) in enumerate(pts):
            self.mem.write(self.VERTS + 32 * i, struct.pack('<ff', x, y))
        c.x[22], c.x[23], c.x[24], c.x[25] = self.VERTS, len(pts), vtype, w25
        for r in (19, 20, 21, 26, 27, 28, 29):
            c.x[r] = 0x1900 + r
        cave = self.target(P.LAYER_SUBMIT)
        out = c.run(cave, self.code, start_pc=cave)
        for r in (19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29):
            self.assertNotEqual(c.x[r], None)
        return out

    def merge(self):
        c = self.c
        c.x[0] = 0x55
        sp = c.sp
        cave = self.target(P.LAYER_MERGE)
        out = c.run(cave, self.code, start_pc=cave)
        self.assertEqual(out, P.LAYER_MERGE + 4)
        self.assertEqual(c.x[0], 0x55)
        self.assertEqual(c.sp, sp - 0x100)
        c.sp = sp
        return out

    def test_hooked(self):
        self.assertIn('foreground layer boxes (submit +0x%X, merge +0x%X)'
                      % (P.LAYER_SUBMIT, P.LAYER_MERGE),
                      self.rep['drv_hooked'])
        self.assertFalse(any(h.startswith('game calls')
                             for h in self.rep['drv_hooked']))
        self.assertLessEqual(self.first + len(P.LAYER_ROWS), P.DRV_DUMP_N)
        names = P.drv_names(P.LAYER_VERSION)
        self.assertEqual(names[self.first], P.LAYER_ROWS[0])
        self.assertEqual(len(names), len(P.drv_names()))

    def test_resumes_like_the_tbz(self):
        self.assertEqual(self.draw([(1, 2)], w25=1), P.LAYER_SUBMIT + 4)
        self.assertEqual(self.draw([(1, 2)], w25=0), P.LAYER_SUBMIT_TBZ)

    def test_box_and_merge(self):
        self.draw([(100.7, 50.2), (300.0, 60.0)])
        self.draw([(120.0, 200.9), (-20.5, 90.0)])
        self.merge()
        # box x -20..300, y 50..200: 320 x 150 = 48000 (< 25%)
        self.assertEqual(self.row(0), (48000 >> 2, 1))
        self.assertEqual(self.row(3)[0], -20 + 1024)
        self.assertEqual(self.row(4)[0], 300 + 1024)
        self.assertEqual(self.row(5)[0], 50 + 1024)
        self.assertEqual(self.row(6)[0], 200 + 1024)
        self.assertEqual(self.row(7)[1], 1)
        self.assertEqual(self.row(11)[1], 2)
        # reset: the next layer starts empty
        self.draw([(0.0, 0.0), (640.0, 480.0)])
        self.merge()
        self.assertEqual(self.row(10)[1], 1)
        self.assertEqual(self.row(0), ((48000 >> 2) + (640 * 480 >> 2), 2))
        self.merge()
        self.assertEqual(self.row(2)[1], 1, 'nothing drawn')

    def test_dump_and_reader(self):
        c = self.c
        self.mem.setu(HOST + (P.FIELD_ID - 0xCF0000), 0x55, 2)

        def translate(cpu):
            cpu.x[0] = HOST + ((cpu.x[0] & 0xFFFFFFFF) - 0xCF0000)
        c = self.c = EM.Cpu(self.mem, native={AC.GUEST_TRANSLATE: translate})
        c.sp, c.tls = 0x700000, 0x900000
        fe = self.rep['frame_end']
        self.mem.setu(P.MULT, 1, 4)
        c.x[30] = SENTINEL
        c.cntpct = 10 ** 6
        self.assertEqual(c.run(fe, self.code, start_pc=fe,
                               max_steps=400000), SENTINEL)
        for _ in range(4):
            self.draw([(0.0, 0.0), (320.0, 240.0)])
            self.merge()
        self.mem.setu(P.MULT, 3, 4)
        c.x[30] = SENTINEL
        with self.assertRaises(EM.Udf):
            c.run(fe, self.code, start_pc=fe, max_steps=400000)
        self.assertEqual(c.x[0] >> 32, P.LAYER_VERSION)
        frames = self.mem.u(self.base + P.FRAMES, 4)
        stack = bytes(self.mem.u(c.sp + k, 1) for k in range(256))
        tls = bytes(self.mem.u(c.tls + k, 1) for k in range(256))
        import frameprobe_read as R
        out = []
        R._drv_profile(stack, tls, [None] * 16, frames, out.append,
                       P.LAYER_VERSION)
        text = ''.join(out)
        self.assertEqual(frames, 2)             # the dump frame counts too
        self.assertIn('merges 2 ', text)
        self.assertIn('x 0..320, y 0..240, 76800 px = 25.0%', text)
        self.assertIn('25-50%: 2,', text)

    def test_3d_and_other_targets(self):
        self.draw([(1.0, 1.0)], vtype=1)
        self.assertEqual(self.row(12)[1], 1)
        self.mem.setu(self.CUR, self.RT_MAIN, 8)
        self.draw([(5.0, 5.0)])                 # into main: not counted
        self.assertEqual(self.row(11)[1], 0)
        self.merge()
        self.assertEqual(self.row(1)[1], 1)
        self.assertEqual(self.row(0)[1], 1)
        self.mem.setu(self.FLAG, 0, 1)          # no merge runs
        self.merge()
        self.assertEqual(self.row(0)[1], 1)


@unittest.skipUnless(os.path.exists(DUMP_MAIN), 'needs the dumped main')
class DriverSecondWord(unittest.TestCase):
    """789d: an entry another pass owns (blankhold's `b` at the submit
    helper) is hooked at its second word instead."""

    def test_hook_at_plus_4(self):
        import a64
        blob = open(DUMP_MAIN, 'rb').read()
        segs, raw = AC.segments(blob)
        text = bytearray(raw[0])
        va = 0x10D9D70
        struct.pack_into('<I', text, va, a64.b(va, 0x10DBB40))
        tmp = tempfile.mkdtemp()
        src, out = os.path.join(tmp, 'in'), os.path.join(tmp, 'out')
        with open(src, 'wb') as handle:
            handle.write(AC.pack(blob, [bytes(text), raw[1], raw[2]], 0))
        old = os.environ.get(P.ENV)
        os.environ[P.ENV] = 'drv'
        try:
            rep = P.apply_to_nso(src, out, DUMP_MAIN)
        finally:
            if old is None:
                os.environ.pop(P.ENV, None)
            else:
                os.environ[P.ENV] = old
        self.assertIn('helper 0x10D9D70 (3D/2D submit) (at +4)',
                      rep['drv_hooked'])
        t1 = AC.segments(open(out, 'rb').read())[1][0]
        self.assertEqual(struct.unpack_from('<I', t1, va)[0],
                         a64.b(va, 0x10DBB40))       # the pass's word kept
        code = {}
        lo = rep['region']
        for k in range(lo, lo + 4 * rep['words'] + 64, 4):
            code[k] = struct.unpack_from('<I', t1, k)[0]
        w = struct.unpack_from('<I', t1, va + 4)[0]
        imm = w & 0x3FFFFFF
        if imm & (1 << 25):
            imm -= 1 << 26
        cave = va + 4 + imm * 4
        c = EM.Cpu(EM.Mem())
        c.sp = 0x700000
        c.x[30] = SENTINEL
        c.cntpct = 10 ** 6
        self.assertEqual(c.run(cave, code, start_pc=cave), va + 8)
        self.assertNotEqual(c.x[30], SENTINEL)
        c.cntpct += 777
        self.assertEqual(c.run(c.x[30], code, start_pc=c.x[30]), SENTINEL)
        idx = [n for _v, n in P.DRV_FUNCS + P.DRV_EXTRA].index(
            'helper 0x10D9D70 (3D/2D submit)')
        e = rep['base'] + P.DPROF + P.DPROF_ENTRY * idx
        self.assertEqual((c.mem.u(e, 8), c.mem.u(e + 8, 4)), (777, 1))


if __name__ == '__main__':
    unittest.main(verbosity=2)
