#!/usr/bin/env python3
"""
ff7nx_lightloop.py -- BUILD 791. The per-vertex loop of a field model's
light, native.

THE COST (frame probe v8, hardware, shadows on): light_polygon_set
(x86 0x68E92D, ARM +0xB4AF90) lights a model part's vertices on the CPU.
For a field model's light (flags 9: mode 1, directional, no scripted
colour) its loop is x86 0x68EA86..0x68EAE1: per vertex, 0x68DD1E(light
+0xF8, [normal], normal+4, normal+0x14, vertex+0x10). 0x68DD1E's ARM body
(+0xB8E020) only moves its five arguments off the guest stack into the
port's native vertex routine (+0x10F23C0, or ff7nx_lighting's lt_vtx in its
place). fship_2: 10,262 vertices a frame, 2.8 ms in 0x68E92D, of which the
routine and its wrapper are 1.5 ms and the translated loop around them
about 1.0 ms; mrkt2: 6,382 vertices, 1.6 ms. The time is the translation's:
every push, local and register is a guest-memory store or load through the
guest translator, about 25 a vertex.

WHAT THIS DOES
==============
The loop's ARM (+0xB4B954..+0xB4BB2C, the x86 block at 0x68EA86 and the
loop body; reached only by 0x68E9F4's `je` at +0xB4B398, checked on every
build) is replaced in place by the same loop in native code: per vertex,
the same call of the same vertex routine (whatever +0xB8E0B4 calls in the
module as built: the stock routine or ff7nx_lighting's), with the same
arguments in the same order, then the timer-pump tick 0x68DD1E's body makes
(+0xA550). Afterwards everything the translated loop leaves is written as
it leaves it: the locals (ebp-0x1C, -0x20, -0x24), eax/ecx/edx, the three
flag bytes of its last compare, and the guest stack words its last
iteration pushed. The exit is the same block (+0xB4BD00, x86 0x68EBDD).
tests/test_lightloop.py runs both on an emulator and compares the guest
memory, the context, the calls and the ticks.

The picture is unchanged: the vertex colours are the same routine's.
SEVENTH_NX_LIGHT_LOOP=0 keeps the translated loop.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import nxpaths  # BUILD 755: project paths (src/nxpaths.py)
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC

VERSION = 791
ENV = 'SEVENTH_NX_LIGHT_LOOP'

SITE, END = 0xB4B954, 0xB4BB2C       # x86 0x68EA86 .. the loop's last block
EXIT = 0xB4BD00                      # x86 0x68EBDD (after the loop)
ENTRY_FROM = 0xB4B398                # 0x68E9F4: je 0x68EA86 (tbz w9, #2)
VTX_CALL = 0xB8E0B4                  # 0x68DD1E's call of the vertex routine
LOOP_CALL = 0xB4BA68                 # the translated loop's call of 0x68DD1E
DD1E = 0xB8E020
TICK = 0xA550                        # the timer-pump tick
G2H = AC.GUEST_TRANSLATE             # +0x10FC3A0

CTX = 19                             # x19: the x86 context (the function's)
EAX, ECX, EDX, ESP, EBP = 0x0, 0x4, 0x8, 0x10, 0x14
ZF, SF, OF = 0x23, 0x24, 0x26        # the flag bytes a translated cmp writes
GT = 0xC

STOCK_HEAD = 0x51002100                 # sub w0, w8, #8 (then bl G2H)


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def _bl_target(word, at):
    if (word & 0xFC000000) != 0x94000000:
        return None
    off = word & 0x3FFFFFF
    if off & 0x2000000:
        off -= 0x4000000
    return at + 4 * off


def check(text):
    """The stock loop is still there, entered only from ENTRY_FROM."""
    got = struct.unpack_from('<I', text, SITE)[0]
    if got != STOCK_HEAD or _bl_target(struct.unpack_from('<I', text, SITE + 4)[0],
                                          SITE + 4) != G2H:
        raise ValueError('light loop: +0x%X is not the stock block (%08X)' % (SITE, got))
    if _bl_target(struct.unpack_from('<I', text, LOOP_CALL)[0], LOOP_CALL) != DD1E:
        raise ValueError('light loop: +0x%X is not the call of 0x68DD1E' % LOOP_CALL)
    if struct.unpack_from('<I', text, ENTRY_FROM)[0] != A.tbz(9, 2, ENTRY_FROM, SITE):
        raise ValueError('light loop: +0x%X is not its entry branch' % ENTRY_FROM)
    vtx = _bl_target(struct.unpack_from('<I', text, VTX_CALL)[0], VTX_CALL)
    if vtx is None:
        raise ValueError('light loop: +0x%X is not a call' % VTX_CALL)
    refs = branches_into(text, SITE + 4, END)
    if refs:
        raise ValueError('light loop: the loop is entered from %s'
                         % ', '.join('+0x%X' % r for r in refs))
    return vtx


def branches_into(text, lo, hi):
    """Every branch or adr outside [lo, hi) whose target is in it."""
    n = len(text) // 4
    ws = struct.unpack_from('<%dI' % n, text)

    def sx(v, bits):
        return v - (1 << bits) if v & (1 << (bits - 1)) else v
    out = []
    for i, w in enumerate(ws):
        pc = 4 * i
        if (w & 0x7C000000) == 0x14000000:
            tg = pc + 4 * sx(w & 0x3FFFFFF, 26)
        elif (w & 0xFF000010) == 0x54000000 or (w & 0x7E000000) == 0x34000000:
            tg = pc + 4 * sx((w >> 5) & 0x7FFFF, 19)
        elif (w & 0x7E000000) == 0x36000000:
            tg = pc + 4 * sx((w >> 5) & 0x3FFF, 14)
        elif (w & 0x9F000000) == 0x10000000:
            tg = pc + sx((((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3), 21)
        else:
            continue
        if lo <= tg < hi and not lo <= pc < hi:
            out.append(pc)
    return out


class _Asm:
    def __init__(self, at):
        self.at, self.w, self.fix = at, [], []

    def pc(self):
        return self.at + 4 * len(self.w)

    def __call__(self, *ws):
        self.w.extend(ws)

    def bl(self, to):
        self.w.append(A.bl(self.pc(), to))

    def b(self, to):
        self.w.append(A.b(self.pc(), to))

    def later(self, make):
        """a branch to a label not placed yet: make(frm, to)"""
        self.fix.append((len(self.w), make))
        self.w.append(0)
        return len(self.fix) - 1

    def place(self, i, to):
        k, make = self.fix[i]
        self.w[k] = make(self.at + 4 * k, to)


def _local(a, off, sub=True):
    """x0 = host address of [ebp -/+ off]"""
    a(A.ldr(8, CTX, EBP), (A.sub_imm if sub else A.add_imm)(0, 8, off))
    a.bl(G2H)


def build(at, vtx):
    """The native loop, placed at `at` (SITE). x19 is the context (the
    function's own); x20/x21 are the function's scratch (it saved them);
    x22..x24 are saved here."""
    a = _Asm(at)
    # 0x68EA86: eax = [ebp-8]; ecx = [eax+4]; [ebp-0x24] = ecx
    _local(a, 8)
    a(A.ldr(9, 0, 0), A.str_(9, CTX, EAX), A.add_imm(0, 9, 4))
    a.bl(G2H)
    a(A.ldr(20, 0, 0), A.str_(20, CTX, ECX))
    _local(a, 0x24)
    a(A.str_(20, 0, 0), A.cmp_imm(20, 0))
    go = a.later(lambda f, t: A.bcond(f, t, GT))
    # no vertex: the flags of cmp [ebp-0x24], 0, then the exit
    a(A.csinc(9, A.WZR, A.WZR, A.NE), A.strb(9, CTX, ZF),
      A.lsr(9, 20, 31), A.strb(9, CTX, SF), A.strb(A.WZR, CTX, OF))
    a.b(EXIT)
    a.place(go, a.pc())
    a(A.stp64_pre(22, 23, 31, -0x20), A.str64(24, 31, 0x10))
    _local(a, 0x1C)
    a(A.ldr(21, 0, 0))                                  # w21 = normal
    _local(a, 0x20)
    a(A.ldr(22, 0, 0))                                  # w22 = vertex
    _local(a, 0xC, sub=False)
    a(A.ldr(23, 0, 0), A.add_imm(23, 23, 0xF8))         # w23 = light + 0xF8
    loop = a.pc()
    a(A.mov_reg(0, 21))
    a.bl(G2H)
    a(A.ldr(24, 0, 0), A.mov_reg(1, 24), A.mov_reg(0, 23),
      A.add_imm(2, 21, 4), A.add_imm(3, 21, 0x14), A.add_imm(4, 22, 0x10))
    a.bl(vtx)
    a(A.str_(0, CTX, EAX))                              # 0x68DD1E's eax
    a.bl(TICK)
    a(A.add_imm(22, 22, 0x20), A.add_imm(21, 21, 0x18), A.sub_imm(20, 20, 1))
    a.w.append(A.cbnz(20, a.pc(), loop))
    # the locals, then what the last iteration pushed below esp: its five
    # arguments (esp-4 .. esp-0x14) and 0x68DD1E's saved ebp (esp-0x1C)
    _local(a, 0x20)
    a(A.str_(22, 0, 0))
    _local(a, 0x1C)
    a(A.str_(21, 0, 0))
    _local(a, 0x24)
    a(A.str_(A.WZR, 0, 0))

    def push(off, reg, value):
        a(A.ldr(8, CTX, ESP), A.sub_imm(0, 8, off))
        a.bl(G2H)
        if value:
            value()
        a(A.str_(reg, 0, 0))
    push(0x4, 9, lambda: a(A.sub_imm(9, 22, 0x10)))     # vertex + 0x10 (last)
    push(0x8, 9, lambda: a(A.sub_imm(9, 21, 0x4)))      # normal + 0x14
    push(0xC, 9, lambda: a(A.sub_imm(9, 21, 0x14)))     # normal + 4
    push(0x10, 24, None)                                # [normal]
    push(0x14, 23, None)                                # light + 0xF8
    push(0x1C, 9, lambda: a(A.ldr(9, CTX, EBP)))        # ebp
    # eax = vertex, ecx = normal, edx = 0; cmp 0, 0
    a(A.str_(22, CTX, EAX), A.str_(21, CTX, ECX), A.str_(A.WZR, CTX, EDX),
      A.movz(9, 1), A.strb(9, CTX, ZF), A.strb(A.WZR, CTX, SF),
      A.strb(A.WZR, CTX, OF))
    a(A.ldr64(24, 31, 0x10), A.ldp64_post(22, 23, 31, 0x20))
    a.b(EXIT)
    return a.w


def patch_text(text):
    """Write the native loop into `text` (bytearray, the module's .text);
    returns (bytes used, vertex routine)."""
    vtx = check(text)
    words = build(SITE, vtx)
    if SITE + 4 * len(words) > END:
        raise ValueError('light loop: %d words do not fit %d'
                         % (len(words), (END - SITE) // 4))
    for i, w in enumerate(words):
        struct.pack_into('<I', text, SITE + 4 * i, w)
    for va in range(SITE + 4 * len(words), END, 4):
        struct.pack_into('<I', text, va, 0xD4200000)    # brk #0: unreachable
    return 4 * len(words), vtx


def apply_to_nso(src, dest, stock=None, log=lambda *_: None):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    used, vtx = patch_text(text)
    raw[0] = bytes(text)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(AC.pack(blob, raw, 0))
    log('  model light loop: native (x86 0x68EA86, %d bytes in place at +0x%X); '
        'vertex routine +0x%X' % (used, SITE, vtx))
    return {'site': SITE, 'bytes': used, 'vtx': vtx}
