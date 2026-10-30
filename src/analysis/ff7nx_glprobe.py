#!/usr/bin/env python3
"""
ff7nx_glprobe.py -- BUILD 860e. GL state probe. DIAGNOSTIC ONLY.

WHY
===
mogu_1's stream is cut at a straight vertical line (screen x 960 of 1280)
for as long as the opening narration runs, until Mog walks out of his
house. The data, the frame simulator, the deferred-draw clip (860b) and the
deferred-draw viewport (860d) have all been ruled out on hardware. This
records the GL state every draw of ONE frame is made with, so the next step
is read off the console instead of guessed.

WHAT IT RECORDS
===============
The module's own PLT stubs for glViewport, glScissor, glEnable, glDisable,
glDepthFunc, glDepthMask and glBindFramebuffer keep a shadow of that state;
glDrawElements / glDrawArrays append the state they draw with to a list,
consecutive draws with the same state counted in one entry. Each entry is
two u64:

    A  viewport x | y << 12 | w << 24 | h << 36  (12 bits each, signed)
       | framebuffer << 48 (8 bits) | scissor on << 56 | depth test << 57
       | blend << 58 | cull << 59 | depth mask << 60
    B  scissor x | y << 12 | w << 24 | h << 36
       | (depth func - 0x200) << 48 (3 bits) | draws << 51 (13 bits)

Press the 3x booster (multiplier 1 -> 3 at +0x12C99B8): the NEXT frame is
recorded (eglSwapBuffers to eglSwapBuffers) and at its end the probe stops
the game on purpose (svcBreak). The crash report carries:

    X0   'GLPB' | version << 32
    X1   entries recorded (all, including those past the list) | draws << 32
    X2..X7, FP, LR   entries 30..33 (A, B each)
    Stack Dump  'GLPBSTK0' header, then entries 0..14
    TLS Dump    'GLPBTLS0' header, then entries 15..29

tools/probes/glprobe_read.py decodes it. The probe's code lives in the
timing probe's own dead body (x86 0x66ED3D); nothing else is changed.
Apply it to a finished exefs/main:

    python3 ff7nx_glprobe.py <main> <dest> [--stock <dump main>]

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: run directly, find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
if _nx_os.path.join(_nx_root, 'src') not in _nx_sys.path:
    _nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_frameprobe as FP
from ff7nx_audio_cave import Asm

VERSION = 2                            # 2: per-draw entries with caller chains
MAGIC = 0x42504C47                     # 'GLPB'
MULT = FP.MULT                         # booster multiplier, 1 <-> 3
EQ, NE, HS, LO = 0, 1, 2, 3

# BSS
PHASE, LAST_MULT, N, DRAWS = 0x00, 0x04, 0x08, 0x0C
VP, SC = 0x10, 0x20                    # 4 x u32 each
FLAGS, DFUNC, DMASK, FB = 0x30, 0x34, 0x38, 0x3C
RING = 0x40
MAXE = 34
BSS_BYTES = RING + 16 * MAXE + 0x40

CAPS = {0x0C11: 0, 0x0B71: 1, 0x0BE2: 2, 0x0B44: 3}   # scissor, depth, blend, cull
HOOKED = ('glViewport', 'glScissor', 'glEnable', 'glDisable', 'glDepthFunc',
          'glDepthMask', 'glBindFramebuffer', 'glDrawElements',
          'glDrawArrays', 'eglSwapBuffers')


def orr64_lsl(rd, rn, rm, sh):
    return 0xAA000000 | (rm << 16) | (sh << 10) | (rn << 5) | rd


def cmp64_lsl(rn, rm, sh):
    return 0xEB000000 | (rm << 16) | (sh << 10) | (rn << 5) | 31


def lsl64(rd, rn, sh):
    return 0xD3400000 | (((64 - sh) % 64) << 16) | ((63 - sh) << 10) | (rn << 5) | rd


def lsr64(rd, rn, sh):
    return 0xD340FC00 | (sh << 16) | (rn << 5) | rd


def movz64_hw(rd, imm16, hw):
    return 0xD2800000 | (hw << 21) | (imm16 << 5) | rd


def _br17():
    return 0xD61F0220                  # br x17


def _stub_tail(a, got):
    a.emit(A.adrp(16, a.pc(), got & ~0xFFF))
    a.emit(A.ldr64(17, 16, got & 0xFFF))
    a.emit(A.add_imm64(16, 16, got & 0xFFF))
    a.emit(_br17())


def build_store4(base, off, got):
    """glViewport / glScissor: shadow = w0..w3."""
    def b(at, addr):
        a = Asm(at, addr)
        AC.bss_ptr(a, 9, base)
        for k in range(4):
            a.emit(A.str_(k, 9, off + 4 * k))
        _stub_tail(a, got)
        return a.resolve()
    return b


def build_store1(base, off, got):
    """glDepthFunc / glDepthMask: shadow = w0. glBindFramebuffer: w1."""
    def b(at, addr):
        a = Asm(at, addr)
        AC.bss_ptr(a, 9, base)
        a.emit(A.str_(1 if off == FB else 0, 9, off))
        _stub_tail(a, got)
        return a.resolve()
    return b


def build_cap(base, got, on):
    """glEnable / glDisable: the flag bit for the caps we follow."""
    def b(at, addr):
        a = Asm(at, addr)
        AC.bss_ptr(a, 9, base)
        a.emit(A.ldr(10, 9, FLAGS))
        for cap, bit in CAPS.items():
            AC.mov32(a, 11, cap)
            a.emit(A.cmp_reg(0, 11))
            a.bcond('no%d' % bit, NE)
            a.emit(A.movz(11, 1 << bit))
            if on:
                a.emit(A.orr_lsl(10, 10, 11, 0))
            else:
                a.emit(0x0A200000 | (11 << 16) | (10 << 5) | 10)   # bic w10,w10,w11
            a.label('no%d' % bit)
        a.emit(A.str_(10, 9, FLAGS))
        _stub_tail(a, got)
        return a.resolve()
    return b


def build_draw(base, got):
    """glDrawElements / glDrawArrays: while recording, add the state."""
    def b(at, addr):
        a = Asm(at, addr)
        AC.bss_ptr(a, 9, base)
        a.emit(A.ldr(10, 9, PHASE))
        a.emit(A.cmp_imm(10, 1))
        a.bcond('tail', NE)
        a.emit(A.ldr(10, 9, DRAWS))
        a.emit(A.add_imm(10, 10, 1))
        a.emit(A.str_(10, 9, DRAWS))
        # A in x10
        a.emit(movz64_hw(10, 0, 0))
        for k in range(4):
            a.emit(A.ldr(11, 9, VP + 4 * k))
            a.emit(A.bfi64(10, 11, 12 * k, 12))
        a.emit(A.ldr(11, 9, FB))
        a.emit(A.bfi64(10, 11, 48, 8))
        a.emit(A.ldr(11, 9, FLAGS))
        a.emit(A.bfi64(10, 11, 56, 4))
        a.emit(A.ldr(11, 9, DMASK))
        a.emit(A.bfi64(10, 11, 60, 1))
        # B (no count) in x11
        a.emit(movz64_hw(11, 0, 0))
        for k in range(4):
            a.emit(A.ldr(12, 9, SC + 4 * k))
            a.emit(A.bfi64(11, 12, 12 * k, 12))
        a.emit(A.ldr(12, 9, DFUNC))
        a.emit(A.bfi64(11, 12, 48, 3))
        # same as the last entry? then count it there
        a.emit(A.ldr(12, 9, N))
        a.cbz(12, 'new')
        a.emit(A.cmp_imm(12, MAXE))
        a.bcond('new', HS)
        a.emit(A.add_imm64(13, 9, RING - 16))
        a.emit(A.add_reg64_lsl(13, 13, 12, 4))
        a.emit(A.ldr64(14, 13, 0))
        a.emit(A.cmp_reg64(14, 10))
        a.bcond('new', NE)
        a.emit(A.ldr64(14, 13, 8))
        a.emit(lsl64(15, 14, 13))
        a.emit(cmp64_lsl(15, 11, 13))
        a.bcond('new', NE)
        a.emit(lsr64(15, 14, 51))
        AC.mov32(a, 16, 0x1FFF)
        a.emit(A.cmp_reg(15, 16))
        a.bcond('tail', HS)
        a.emit(movz64_hw(15, 8, 3))                 # 1 << 51
        a.emit(A.add_reg64(14, 14, 15))
        a.emit(A.str64(14, 13, 8))
        a.b('tail')
        a.label('new')
        a.emit(A.cmp_imm(12, MAXE))
        a.bcond('count', HS)
        a.emit(A.add_imm64(13, 9, RING))
        a.emit(A.add_reg64_lsl(13, 13, 12, 4))
        a.emit(A.str64(10, 13, 0))
        a.emit(movz64_hw(15, 8, 3))
        a.emit(A.add_reg64(11, 11, 15))
        a.emit(A.str64(11, 13, 8))
        a.label('count')
        a.emit(A.add_imm(12, 12, 1))
        a.emit(A.str_(12, 9, N))
        a.label('tail')
        _stub_tail(a, got)
        return a.resolve()
    return b


MODE_GOT = 0x12CE3D0                   # -> u32 driver render mode
DEV_GOT = 0x12CE510                    # -> -> -> device (RT0 at +0x28)
CHAIN_DRAWS = MAXE // 2                # 17 draws, two slots each


def build_draw_chain(base, got):
    """VERSION 2. The first CHAIN_DRAWS draws of the frame, each as two
    slots: (A, B) as in v1 with fb in bits 48..51 and the render mode in
    52..55 of A and B's count field holding (RT0 >> 4) & 0x1FFF; then four
    u32 return addresses (module offsets) walked up the frame chain from
    the caller of the GL stub. Only x9..x15 are used (x0..x7 are the GL
    call's arguments; x16/x17 belong to the stub tail)."""
    def b(at, addr):
        a = Asm(at, addr)
        AC.bss_ptr(a, 9, base)
        a.emit(A.ldr(10, 9, PHASE))
        a.emit(A.cmp_imm(10, 1))
        a.bcond('tail', NE)
        a.emit(A.ldr(12, 9, DRAWS))
        a.emit(A.add_imm(10, 12, 1))
        a.emit(A.str_(10, 9, DRAWS))
        a.emit(A.str_(10, 9, N))
        a.emit(A.cmp_imm(12, CHAIN_DRAWS))
        a.bcond('tail', HS)
        # x13 = slot pair address
        a.emit(A.add_imm64(13, 9, RING))
        a.emit(A.add_reg64_lsl(13, 13, 12, 5))
        # A
        a.emit(movz64_hw(10, 0, 0))
        for k in range(4):
            a.emit(A.ldr(11, 9, VP + 4 * k))
            a.emit(A.bfi64(10, 11, 12 * k, 12))
        a.emit(A.ldr(11, 9, FB))
        a.emit(A.bfi64(10, 11, 48, 4))
        a.emit(A.adrp(11, a.pc(), MODE_GOT & ~0xFFF))
        a.emit(A.ldr64(11, 11, MODE_GOT & 0xFFF))
        a.emit(A.ldr(11, 11, 0))
        a.emit(A.bfi64(10, 11, 52, 4))
        a.emit(A.ldr(11, 9, FLAGS))
        a.emit(A.bfi64(10, 11, 56, 4))
        a.emit(A.ldr(11, 9, DMASK))
        a.emit(A.bfi64(10, 11, 60, 1))
        a.emit(A.str64(10, 13, 0))
        # B
        a.emit(movz64_hw(10, 0, 0))
        for k in range(4):
            a.emit(A.ldr(11, 9, SC + 4 * k))
            a.emit(A.bfi64(10, 11, 12 * k, 12))
        a.emit(A.ldr(11, 9, DFUNC))
        a.emit(A.bfi64(10, 11, 48, 3))
        a.emit(A.adrp(11, a.pc(), DEV_GOT & ~0xFFF))
        a.emit(A.ldr64(11, 11, DEV_GOT & 0xFFF))
        a.emit(A.ldr64(11, 11, 0))
        a.cbz64(11, 'nodev')
        a.emit(A.ldr64(11, 11, 0))
        a.cbz64(11, 'nodev')
        a.emit(A.ldr64(11, 11, 0x28))
        a.emit(lsr64(11, 11, 4))
        a.emit(A.bfi64(10, 11, 51, 13))
        a.label('nodev')
        a.emit(A.str64(10, 13, 8))
        # module base in x15
        a.emit(A.adr(15, a.pc(), a.pc()))
        here = a.pc() - 4
        AC.mov32(a, 14, here)
        a.emit(A.sub_reg64(15, 15, 14))
        # frame chain
        a.emit(A.mov_reg64(12, 29))
        for k in range(4):
            a.cbz64(12, 'chain_end')
            a.emit(A.ldr64(14, 12, 8))
            a.emit(A.sub_reg64(14, 14, 15))
            a.emit(A.str_(14, 13, 16 + 4 * k))
            a.emit(A.ldr64(12, 12, 0))
        a.label('chain_end')
        a.label('tail')
        _stub_tail(a, got)
        return a.resolve()
    return b


def build_swap(base, got):
    """eglSwapBuffers: the phase machine and the dump."""
    def b(at, addr):
        a = Asm(at, addr)
        AC.bss_ptr(a, 9, base)
        a.emit(A.ldr(10, 9, PHASE))
        a.emit(A.cmp_imm(10, 1))
        a.bcond('dump', EQ)
        AC.bss_ptr(a, 11, MULT)
        a.emit(A.ldr(11, 11, 0))
        a.emit(A.ldr(12, 9, LAST_MULT))
        a.emit(A.str_(11, 9, LAST_MULT))
        a.emit(A.cmp_imm(12, 1))
        a.bcond('tail', NE)
        a.emit(A.cmp_imm(11, 3))
        a.bcond('tail', NE)
        a.emit(A.movz(10, 1))
        a.emit(A.str_(10, 9, PHASE))
        a.emit(A.str_(31, 9, N))
        a.emit(A.str_(31, 9, DRAWS))
        a.label('tail')
        _stub_tail(a, got)
        # ---- dump
        a.label('dump')
        a.emit(A.add_imm64(20, 9, RING))
        # stack: header + entries 0..14
        AC.mov32(a, 10, 0x42504C47)
        AC.mov32(a, 11, 0x304B5453)                  # 'STK0'
        a.emit(A.bfi64(10, 11, 32, 32))
        a.emit(A.ldr(11, 9, N))
        a.emit(A.stp64_off(10, 11, 31, 0))
        for i in range(15):
            a.emit(A.ldp64_off(12, 13, 20, 16 * i))
            a.emit(A.stp64_off(12, 13, 31, 16 + 16 * i))
        # TLS: header + entries 15..29
        a.emit(FP.mrs_tpidrro(14))
        AC.mov32(a, 10, 0x42504C47)
        AC.mov32(a, 11, 0x30534C54)                  # 'TLS0'
        a.emit(A.bfi64(10, 11, 32, 32))
        a.emit(A.ldr(11, 9, N))
        a.emit(A.stp64_off(10, 11, 14, 0))
        for i in range(15):
            a.emit(A.ldp64_off(12, 13, 20, 16 * (15 + i)))
            a.emit(A.stp64_off(12, 13, 14, 16 + 16 * i))
        # registers
        a.emit(A.ldp64_off(2, 3, 20, 16 * 30))
        a.emit(A.ldp64_off(4, 5, 20, 16 * 31))
        a.emit(A.ldp64_off(6, 7, 20, 16 * 32))
        a.emit(A.ldp64_off(29, 30, 20, 16 * 33))
        a.emit(A.ldr(1, 9, N))
        a.emit(A.ldr(10, 9, DRAWS))
        a.emit(A.bfi64(1, 10, 32, 32))
        AC.mov32(a, 0, MAGIC)
        a.emit(A.movz(10, VERSION))
        a.emit(A.bfi64(0, 10, 32, 32))
        a.emit(FP.svc_break())
        a.emit(FP.udf())
        return a.resolve()
    return b


def apply_to_nso(src, dest, stock=None, log=print):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    stubs = FP.import_stubs(segs, raw)
    missing = [n for n in HOOKED if n not in stubs]
    if missing:
        raise ValueError('imports not found: %s' % missing)
    for name in HOOKED:
        va, _got = stubs[name]
        if (struct.unpack_from('<I', text, va)[0] & 0x9F00001F) != 0x90000010:
            raise ValueError('%s stub +0x%X is already hooked' % (name, va))
    exe = None
    for p in (os.path.join(nxpaths.ROOT, 'dump', 'romfs', 'ff7', 'resources',
                           'ff7_1.02', 'ff7_en'),
              os.path.join(nxpaths.ROOT, 'ff7_en')):
        if os.path.isfile(p):
            exe = open(p, 'rb').read()
            break
    lo, hi = FP.own_region(src, stock, exe=exe)
    base = AC.scratch_base(blob, segs)
    base = (base + 15) & ~15
    growth = (base - AC.scratch_base(blob, segs)) + BSS_BYTES + \
        AC.bss_tail_slack(segs)
    bump = FP.Bump(lo, hi)
    caves = {}
    for name in HOOKED:
        va, got = stubs[name]
        if name in ('glViewport', 'glScissor'):
            build = build_store4(base, VP if name == 'glViewport' else SC, got)
        elif name == 'glDepthFunc':
            build = build_store1(base, DFUNC, got)
        elif name == 'glDepthMask':
            build = build_store1(base, DMASK, got)
        elif name == 'glBindFramebuffer':
            build = build_store1(base, FB, got)
        elif name in ('glEnable', 'glDisable'):
            build = build_cap(base, got, name == 'glEnable')
        elif name == 'eglSwapBuffers':
            build = build_swap(base, got)
        else:
            build = (build_draw_chain(base, got) if VERSION >= 2
                     else build_draw(base, got))
        caves[name] = (va, bump.put(build))
    for va, w in bump.placed.items():
        struct.pack_into('<I', text, va, w)
    for name, (va, entry) in caves.items():
        struct.pack_into('<I', text, va, A.b(va, entry))
    raw[0] = bytes(text)
    with open(dest, 'wb') as f:
        f.write(AC.pack(blob, raw, growth))
    log('gl state probe: %d stubs hooked, code +0x%X..+0x%X, BSS +0x%X'
        % (len(caves), lo, bump.at, base))
    return {'base': base, 'caves': caves}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dest')
    ap.add_argument('--stock')
    a = ap.parse_args()
    apply_to_nso(a.src, a.dest, a.stock)
