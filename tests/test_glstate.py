#!/usr/bin/env python3
"""
tests/test_glstate.py -- the GL cost cut (BUILD 788), executed under arm64emu
on the real layout it gets in the dumped module.

  * a framebuffer bind updates the current framebuffer for the draw target
    only, and always reaches the real stub with its arguments;
  * an attach that repeats what that framebuffer's slot already holds is
    dropped; any other attach reaches the stub, and a detach is one key
    whichever function made it; another framebuffer, a delete, an unknown
    target or attachment, or framebuffer 0 never drop a call;
  * sub uploads: the first upload is stock and resolves glBufferSubData
    against subsdk0's words; then an upload that fits is glBufferSubData, one
    that does not grows the storage to a 4 KB multiple first, and untracked
    names, no data, a mismatching driver, and the budget all stay stock;
    glDeleteBuffers forgets a name's storage;
  * only the intended words change, and every word written decodes.

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
import tempfile
import unittest

import arm64emu as EM                                          # noqa: E402
import ff7nx_audio_cave as AC                                  # noqa: E402
import ff7nx_frameprobe as FP                                  # noqa: E402
import ff7nx_glstate as G                                      # noqa: E402

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP_MAIN = os.path.join(_HERE, 'dump', 'exefs', 'main')
SUBSDK = os.path.join(_HERE, 'dump', 'exefs', 'subsdk0')
SENTINEL = 0x5000000
CACHE_OBJ = 0x900000
DRIVER = 0xA00000            # where the test puts glBufferData's code


def _layout(attach, upload):
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, 'main')
    rep = G.apply_to_nso(DUMP_MAIN, out, DUMP_MAIN, SUBSDK, attach, upload)
    _s0, raw0 = AC.segments(open(DUMP_MAIN, 'rb').read())
    segs, raw1 = AC.segments(open(out, 'rb').read())
    t0, t1 = raw0[0], raw1[0]
    lo = rep['region']
    code = {}
    for va in range(lo, lo + 4 * rep['words'], 4):
        code[va] = struct.unpack_from('<I', t1, va)[0]
    stubs = FP.import_stubs(segs, raw1)
    return rep, t0, t1, code, stubs


def _target(text, va):
    w = struct.unpack_from('<I', text, va)[0]
    imm = w & 0x3FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return va + imm * 4


@unittest.skipUnless(os.path.exists(DUMP_MAIN) and os.path.exists(SUBSDK),
                     'needs the dumped main and subsdk0')
class GlState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (cls.rep, cls.t0, cls.t1, cls.code,
         cls.stubs) = _layout(True, 'sub')
        cls.base = cls.rep['base']

    def stub(self, name):
        return self.stubs[name][0]

    def cave(self, name):
        return _target(self.t1, G.SITES[name][0])

    def cpu(self, natives=None):
        mem = EM.Mem()
        c = EM.Cpu(mem, native=natives or {})
        c.sp = 0x700000
        c.x[30] = SENTINEL
        self.mem = mem
        for va, w in self.code.items():        # the literals are read as data
            mem.setu(va, w, 4)
        return c

    def call(self, c, name, *args):
        """Enter `name`'s cave as its first call site does. Returns where it
        left: SENTINEL (dropped) or the stub (passed on)."""
        for i, v in enumerate(args):
            c.x[i] = v
        c.x[30] = SENTINEL
        for r in range(19, 30):
            c.x[r] = 0x7700 + r
        sp0 = c.sp
        entry = self.cave(name)
        end = c.run(entry, self.code, start_pc=entry)
        self.assertEqual(c.sp, sp0)
        for r in range(19, 30):
            self.assertEqual(c.x[r], 0x7700 + r, 'x%d' % r)
        if end != SENTINEL:
            self.assertEqual(end, self.stub(name))
            for i, v in enumerate(args):
                self.assertEqual(c.x[i] & 0xFFFFFFFF, v & 0xFFFFFFFF,
                                 'x%d reached the stub changed' % i)
            self.assertEqual(c.x[30], SENTINEL)
        return end

    def bss(self, off, n=4):
        return self.mem.u(self.base + off, n)

    # ---------------------------------------------------------- attaches
    def test_bind_tracks_the_draw_framebuffer(self):
        c = self.cpu()
        self.assertEqual(self.call(c, 'glBindFramebuffer', 0x8D40, 5),
                         self.stub('glBindFramebuffer'))
        self.assertEqual(self.bss(G.CUR), 5)
        self.call(c, 'glBindFramebuffer', 0x8CA8, 9)      # read only
        self.assertEqual(self.bss(G.CUR), 5)
        self.call(c, 'glBindFramebuffer', 0x8CA9, 7)      # draw
        self.assertEqual(self.bss(G.CUR), 7)

    def test_repeated_attach_is_dropped(self):
        c = self.cpu()
        tex, rb = 'glFramebufferTexture2D', 'glFramebufferRenderbuffer'
        S, D = self.stub(tex), SENTINEL
        self.call(c, 'glBindFramebuffer', 0x8D40, 5)
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8CE0, 0xDE1, 7, 0), S)
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8CE0, 0xDE1, 7, 0), D)
        # a different texture, level or textarget is attached
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8CE0, 0xDE1, 8, 0), S)
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8CE0, 0xDE1, 8, 1), S)
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8CE0, 0x8515, 8, 1), S)
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8CE0, 0x8515, 8, 1), D)
        # each slot is its own
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8CE3, 0xDE1, 8, 1), S)
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8D00, 0xDE1, 8, 1), S)
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8D20, 0xDE1, 8, 1), S)
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8D20, 0xDE1, 8, 1), D)
        # a renderbuffer with the same name is not the texture
        self.assertEqual(self.call(c, rb, 0x8D40, 0x8D20, 0xDE1, 8),
                         self.stub(rb))
        self.assertEqual(self.call(c, rb, 0x8D40, 0x8D20, 0xDE1, 8), D)
        # a detach is a detach, by either function
        self.assertEqual(self.call(c, rb, 0x8D40, 0x8CE1, 0x8D41, 0),
                         self.stub(rb))
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8CE1, 0xDE1, 0, 0), D)
        self.assertEqual(self.call(c, rb, 0x8D40, 0x8CE1, 0x8D41, 0), D)
        # another framebuffer remembers separately
        self.call(c, 'glBindFramebuffer', 0x8D40, 6)
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8CE0, 0xDE1, 7, 0), S)
        self.call(c, 'glBindFramebuffer', 0x8D40, 5)
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8CE0, 0x8515, 8, 1), D)
        self.assertEqual(self.bss(G.STATS + 4 * G.ST_SKIP), 7)

    def test_what_is_never_dropped(self):
        c = self.cpu()
        tex = 'glFramebufferTexture2D'
        S = self.stub(tex)
        args = (0x8D40, 0x8CE0, 0xDE1, 7, 0)
        # framebuffer 0 and 64+ are not cached
        for fb in (0, 64, 0x10000):
            self.call(c, 'glBindFramebuffer', 0x8D40, fb)
            self.assertEqual(self.call(c, tex, *args), S)
            self.assertEqual(self.call(c, tex, *args), S)
        self.call(c, 'glBindFramebuffer', 0x8D40, 63)
        self.assertEqual(self.call(c, tex, *args), S)
        self.assertEqual(self.call(c, tex, *args), SENTINEL)
        # every delete forgets
        for name in ('glDeleteTextures', 'glDeleteRenderbuffers'):
            self.assertEqual(self.call(c, name, 1, 0x880000), self.stub(name))
            self.assertEqual(self.bss(G.CUR), 63)
            self.assertEqual(self.call(c, tex, *args), S)
            self.assertEqual(self.call(c, tex, *args), SENTINEL)
        self.assertEqual(self.call(c, 'glDeleteFramebuffers', 1, 0x880000),
                         self.stub('glDeleteFramebuffers'))
        self.assertEqual(self.bss(G.CUR), 0)
        self.assertEqual(self.call(c, tex, *args), S)
        self.assertEqual(self.call(c, tex, *args), S)
        # an unknown target or attachment forgets and passes on
        self.call(c, 'glBindFramebuffer', 0x8D40, 63)
        self.call(c, tex, *args)
        self.assertEqual(self.call(c, tex, *args), SENTINEL)
        self.assertEqual(self.call(c, tex, 0x8CA8, 0x8CE0, 0xDE1, 7, 0), S)
        self.assertEqual(self.call(c, tex, *args), S)
        self.assertEqual(self.call(c, tex, 0x8D40, 0x8CE4, 0xDE1, 7, 0), S)
        self.assertEqual(self.call(c, tex, *args), S)
        self.assertEqual(self.call(c, tex, 0x8D40, 0x821A, 0xDE1, 7, 0), S)
        self.assertEqual(self.call(c, tex, *args), S)
        self.assertEqual(self.call(c, tex, *args), SENTINEL)

    # ------------------------------------------------------ program state
    def test_uniform_block_index_is_remembered(self):
        got = []

        def index(c):
            got.append((c.x[0] & 0xFFFFFFFF, c.x[1]))
            c.x[0] = 7 if c.x[1] == 0x11A992D else 0xFFFFFFFF
            for r in range(1, 19):
                c.x[r] = 0xAB00 + r
        c = self.cpu({self.stub('glGetUniformBlockIndex'): index})
        sites = G.SITES['glGetUniformBlockIndex']

        def ask(site, prog, name):
            c.x[0], c.x[1] = prog, name
            c.x[30] = SENTINEL
            for r in range(19, 30):
                c.x[r] = 0x7700 + r
            sp0 = c.sp
            entry = _target(self.t1, site)
            end = c.run(entry, self.code, start_pc=entry)
            if end == self.stub('glGetUniformBlockIndex'):     # tail call
                self.assertEqual(c.x[30], SENTINEL)
                self.assertEqual((c.x[0], c.x[1]), (prog, name))
                index(c)
                end = c.x[30]
            self.assertEqual(end, SENTINEL)
            self.assertEqual(c.sp, sp0)
            for r in range(19, 30):
                self.assertEqual(c.x[r], 0x7700 + r)
            return c.x[0] & 0xFFFFFFFF
        self.assertEqual(ask(sites[0], 3, 0x11A992D), 7)
        self.assertEqual(ask(sites[0], 3, 0x11A992D), 7)
        self.assertEqual(len(got), 1)
        self.assertEqual(ask(sites[1], 3, 0x11A8864), 0xFFFFFFFF)  # invalid
        self.assertEqual(ask(sites[1], 3, 0x11A8864), 0xFFFFFFFF)
        self.assertEqual(len(got), 2)
        self.assertEqual(ask(sites[0], 4, 0x11A992D), 7)          # other prog
        self.assertEqual(len(got), 3)
        self.assertEqual(ask(sites[0], 0, 0x11A992D), 7)          # never 0
        self.assertEqual(ask(sites[0], 0, 0x11A992D), 7)
        self.assertEqual(len(got), 5)
        self.call(c, 'glLinkProgram', 3)                          # forgets
        self.assertEqual(ask(sites[0], 3, 0x11A992D), 7)
        self.assertEqual(len(got), 6)
        self.assertEqual(ask(sites[0], 3, 0x11A992D), 7)
        self.assertEqual(len(got), 6)
        self.call(c, 'glDeleteProgram', 9)
        self.assertEqual(ask(sites[0], 3, 0x11A992D), 7)
        self.assertEqual(len(got), 7)

    def test_block_binding_and_uniform1i(self):
        c = self.cpu()
        bb, u1 = 'glUniformBlockBinding', 'glUniform1i'
        self.assertEqual(self.call(c, bb, 3, 2, 0), self.stub(bb))
        self.assertEqual(self.call(c, bb, 3, 2, 0), SENTINEL)
        self.assertEqual(self.call(c, bb, 3, 2, 1), self.stub(bb))
        self.assertEqual(self.call(c, bb, 3, 2, 1), SENTINEL)
        self.assertEqual(self.call(c, bb, 3, 5, 1), self.stub(bb))
        self.assertEqual(self.call(c, bb, 4, 5, 1), self.stub(bb))
        self.assertEqual(self.call(c, bb, 3, 2, 2), self.stub(bb))  # binding 2
        self.assertEqual(self.call(c, bb, 3, 2, 2), self.stub(bb))
        # glUniform1i follows the current program
        self.assertEqual(self.call(c, u1, 1, 0), self.stub(u1))     # none yet
        self.assertEqual(self.call(c, u1, 1, 0), self.stub(u1))
        self.call(c, 'glUseProgram', 3)
        self.assertEqual(self.bss(G.CUR_PROG), 3)
        self.assertEqual(self.call(c, u1, 1, 0), self.stub(u1))
        self.assertEqual(self.call(c, u1, 1, 0), SENTINEL)
        self.assertEqual(self.call(c, u1, 1, 2), self.stub(u1))
        self.assertEqual(self.call(c, u1, 2, 2), self.stub(u1))
        self.assertEqual(self.call(c, u1, 15, 2), self.stub(u1))
        self.assertEqual(self.call(c, u1, 15, 2), SENTINEL)
        self.assertEqual(self.call(c, u1, 16, 2), self.stub(u1))    # untracked
        self.assertEqual(self.call(c, u1, 16, 2), self.stub(u1))
        self.assertEqual(self.call(c, u1, 0xFFFFFFFF, 2), self.stub(u1))
        self.call(c, 'glUseProgram', 4)
        self.assertEqual(self.call(c, u1, 1, 2), self.stub(u1))
        self.call(c, 'glUseProgram', 3)
        self.assertEqual(self.call(c, u1, 1, 2), SENTINEL)
        self.call(c, 'glLinkProgram', 4)                           # forgets
        self.assertEqual(self.call(c, u1, 1, 2), self.stub(u1))
        self.assertEqual(self.call(c, bb, 3, 2, 0), self.stub(bb))

    # ------------------------------------------------------------ uploads
    def upload_cpu(self, words=None):
        calls = []

        def buffer_data(c):
            calls.append(('data', c.x[0] & 0xFFFFFFFF, c.x[1], c.x[2],
                          c.x[3] & 0xFFFFFFFF))
            for r in list(range(0, 19)):
                c.x[r] = 0xDEAD0000 + r                    # caller-saved
            # the lazy binder has now filled the GOT entry
            self.mem.setu(self.stubs['glBufferData'][1], DRIVER, 8)

        def sub_data(c):
            calls.append(('sub', c.x[0] & 0xFFFFFFFF, c.x[1], c.x[2], c.x[3]))
            for r in list(range(0, 19)):
                c.x[r] = 0xBEEF0000 + r

        c = self.cpu({self.stub('glBufferData'): buffer_data,
                      DRIVER + G.SUBDATA_DELTA: sub_data})
        self.mem.setu(G.STATE_GOT, CACHE_OBJ - 0x100, 8)
        self.mem.setu(CACHE_OBJ - 0x100, CACHE_OBJ, 8)
        self.mem.setu(self.stubs['glBufferData'][1], 0x1150EA0, 8)  # PLT0
        for off, w in zip(G.CHECK_OFFS, words or self.rep['expect']):
            self.mem.setu(DRIVER + off, w, 8)
        return c, calls

    def upload(self, c, site, name, size, data, usage=0x88E4):
        _s, _u, field, target, _w = [u for u in G.UPLOADS if u[0] == site][0]
        self.mem.setu(CACHE_OBJ + field, name, 4)
        c.x[0], c.x[1], c.x[2], c.x[3] = target, size, data, usage
        c.x[30] = SENTINEL
        for r in range(19, 30):
            c.x[r] = 0x6600 + r
        sp0 = c.sp
        entry = _target(self.t1, site)
        end = c.run(entry, self.code, start_pc=entry)
        self.assertEqual(end, SENTINEL)
        self.assertEqual(c.sp, sp0)
        for r in range(19, 30):
            self.assertEqual(c.x[r], 0x6600 + r, 'x%d' % r)
        return target

    def cap(self, name):
        return self.bss(G.CAP + 4 * name)

    def test_sub_uploads(self):
        c, calls = self.upload_cpu()
        V, I, U = (u[0] for u in G.UPLOADS)
        t = self.upload(c, U, 3, 100, 0x1000)         # first: stock, resolves
        self.assertEqual(calls, [('data', t, 100, 0x1000, 0x88E4)])
        self.assertEqual(self.bss(G.SUBPTR, 8), DRIVER + G.SUBDATA_DELTA)
        self.assertEqual(self.cap(3), 100)
        self.assertEqual(self.bss(G.TOTAL, 8), 100)
        del calls[:]
        self.upload(c, U, 3, 80, 0x2000)              # fits
        self.assertEqual(calls, [('sub', t, 0, 80, 0x2000)])
        del calls[:]
        self.upload(c, U, 3, 100, 0x2000)             # fits exactly
        self.assertEqual(calls, [('sub', t, 0, 100, 0x2000)])
        del calls[:]
        self.upload(c, U, 3, 5000, 0x3000)            # grows to 8 KB
        self.assertEqual(calls, [('data', t, 8192, 0, 0x88E4),
                                 ('sub', t, 0, 5000, 0x3000)])
        self.assertEqual(self.cap(3), 8192)
        self.assertEqual(self.bss(G.TOTAL, 8), 8192)
        del calls[:]
        t = self.upload(c, V, 4, 4096, 0x4000)        # vertex, grows to 4 KB
        self.assertEqual(calls, [('data', t, 4096, 0, 0x88E4),
                                 ('sub', t, 0, 4096, 0x4000)])
        self.assertEqual(self.bss(G.TOTAL, 8), 8192 + 4096)
        del calls[:]
        t = self.upload(c, I, 9, 64, 0)               # no data: stock
        self.assertEqual(calls, [('data', t, 64, 0, 0x88E4)])
        self.assertEqual(self.cap(9), 64)
        del calls[:]
        self.upload(c, I, 9, 0, 0x5000)               # size 0: stock
        self.assertEqual(calls, [('data', t, 0, 0x5000, 0x88E4)])
        self.assertEqual(self.cap(9), 0)
        del calls[:]
        for name in (0, 4096, 0xFFFFFFFF):            # untracked: stock
            self.upload(c, I, name, 64, 0x5000)
            self.assertEqual(calls, [('data', t, 64, 0x5000, 0x88E4)])
            del calls[:]
        self.assertEqual(self.bss(G.TOTAL, 8), 8192 + 4096)
        # glDeleteBuffers forgets 3 (and ignores 0 and 5000)
        self.mem.setu(0x880000, 3, 4)
        self.mem.setu(0x880004, 0, 4)
        self.mem.setu(0x880008, 5000, 4)
        self.assertEqual(self.call(c, 'glDeleteBuffers', 3, 0x880000),
                         self.stub('glDeleteBuffers'))
        self.assertEqual(self.cap(3), 0)
        self.assertEqual(self.bss(G.TOTAL, 8), 4096)
        self.upload(c, G.UPLOADS[2][0], 3, 80, 0x2000)
        self.assertEqual(calls, [('data', 0x8A11, 4096, 0, 0x88E4),
                                 ('sub', 0x8A11, 0, 80, 0x2000)])

    def test_budget_and_mismatch_stay_stock(self):
        c, calls = self.upload_cpu()
        U = G.UPLOADS[2][0]
        self.upload(c, U, 3, 100, 0x1000)
        self.mem.setu(self.base + G.TOTAL, G.BUDGET - 100, 8)
        del calls[:]
        self.upload(c, U, 3, 5000, 0x1000)            # would pass the budget
        self.assertEqual(calls, [('data', 0x8A11, 5000, 0x1000, 0x88E4)])
        self.assertEqual(self.cap(3), 5000)
        self.assertEqual(self.bss(G.TOTAL, 8), G.BUDGET - 100 + 4900)
        bad = list(self.rep['expect'])
        bad[1] ^= 1
        c, calls = self.upload_cpu(bad)
        for _ in range(3):
            self.upload(c, U, 3, 100, 0x1000)
        self.assertEqual(self.bss(G.SUBPTR, 8), 1)
        self.assertEqual([k[0] for k in calls], ['data'] * 3)

    # ------------------------------------------------------------- module
    def test_only_the_intended_words_change(self):
        lo, hi = self.rep['region'], None
        import nxmap
        lo0, hi = G.own_region(DUMP_MAIN, DUMP_MAIN)
        self.assertEqual(lo, lo0)
        sites = set()
        for n in G.SITES:
            if n == 'glBufferData' or n in G.ATTACH_IMPORTS or \
                    n == 'glDeleteBuffers':
                sites.update(G.SITES[n])
        changed = [va for va in range(0, len(self.t0), 4)
                   if self.t0[va:va + 4] != self.t1[va:va + 4]]
        for va in changed:
            self.assertTrue(va in sites or lo <= va < hi, '+0x%X' % va)
        for va in sites:
            self.assertIn(_target(self.t1, va), self.code)
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        n_lit = 0
        for va, w in self.code.items():
            ins = list(md.disasm(struct.pack('<I', w), va))
            if not ins:
                n_lit += 1                     # the literal pool only
        self.assertLessEqual(n_lit, 6)
        del nxmap

    def test_stream_is_three_words(self):
        rep, t0, t1, _code, _stubs = _layout(False, 'stream')
        changed = [va for va in range(0, len(t0), 4)
                   if t0[va:va + 4] != t1[va:va + 4]]
        self.assertEqual(changed, sorted(u[1] for u in G.UPLOADS))
        for va in changed:
            self.assertEqual(struct.unpack_from('<I', t1, va)[0],
                             G.USAGE_STREAM)
        self.assertEqual(rep['bss'], 0)

    def test_encoders(self):
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)

        def dis(w, at=0x1000):
            i = list(md.disasm(struct.pack('<I', w), at))[0]
            return ('%s %s' % (i.mnemonic, i.op_str)).strip()
        self.assertEqual(dis(G.ldr_w_reg(23, 10, 9)), 'ldr w23, [x10, x9]')
        self.assertEqual(dis(G.ldr_w_reg(15, 9, 13, True)),
                         'ldr w15, [x9, x13, lsl #2]')
        self.assertEqual(dis(G.str_w_reg(31, 9, 13, True)),
                         'str wzr, [x9, x13, lsl #2]')
        self.assertEqual(dis(G.orr64_lsl(12, 12, 13, 32)),
                         'orr x12, x12, x13, lsl #32')
        self.assertEqual(dis(G.lsr64(10, 20, 31)), 'lsr x10, x20, #0x1f')
        self.assertEqual(dis(G.USAGE_STATIC), 'mov w3, #0x88e4')
        self.assertEqual(dis(G.USAGE_STREAM), 'mov w3, #0x88e0')
        self.assertEqual(dis(G.lsl64(13, 13, 12)), 'lsl x13, x13, #0xc')
        self.assertEqual(dis(0xF100055F), 'cmp x10, #1')
        self.assertEqual(dis(G.mov_from_sp(29)), 'mov x29, sp')


if __name__ == '__main__':
    unittest.main()
