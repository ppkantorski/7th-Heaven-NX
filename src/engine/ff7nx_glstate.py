#!/usr/bin/env python3
"""
ff7nx_glstate.py -- BUILD 788. Cut the GL driver's share of a busy frame.

Probe v5 (BUILD-787-GL-PROFILE) timed every GL call in fship_2: 9.8 ms of an
18.3 ms frame is spent inside the driver, on the one core that runs the game.
Two things the port's GL wrapper does dominate that, and both can be made
cheaper without touching what is drawn:

1. RENDER-TARGET ATTACHES (`attach`, SEVENTH_NX_GL_STATE=1)
   The render-target apply at +0x1132F50 re-attaches all six slots (COLOR0..3,
   DEPTH, STENCIL) every time its dirty byte is set: about 2,560
   glFramebufferTexture2D / glFramebufferRenderbuffer calls a frame for only
   eight framebuffer binds, nearly all of them re-attaching exactly what is
   already attached. Every call site of those two functions is hooked; the
   hook remembers, per framebuffer (names 1..63) and slot, what was last
   attached, and drops a call that would attach the same object again. A
   framebuffer bind updates which framebuffer is current; any delete of a
   texture, renderbuffer or framebuffer forgets everything; an unknown
   target or attachment point forgets everything and passes the call on.
   GL defines a repeated identical attach as a no-op, so skipping it cannot
   change what is drawn.

2. BUFFER UPLOADS (`upload`, SEVENTH_NX_GL_UPLOAD)
   glBufferData is 4.9 ms of the 9.8: 800 uploads a frame (vertices,
   indices, the per-draw uniform block), each one reallocating the buffer's
   storage with the STATIC_DRAW hint.
     stream  the three uploads say STREAM_DRAW instead (three words).
     sub     uploads that fit the buffer's existing storage use
             glBufferSubData, which keeps the storage; a buffer grows (to a
             4 KB multiple) only when an upload does not fit.
     ubo     `sub` for the per-draw uniform block only.
   For sub/ubo, the storage size of every buffer name below 4096 is tracked
   (cleared when glDeleteBuffers deletes it), the total extra storage is
   capped (BUDGET), and glBufferSubData is reached through glBufferData's GOT
   entry: the driver's export table puts it 0x70 bytes after glBufferData
   (dump/exefs/subsdk0). The first upload always goes through the stock
   call; only after it has resolved the GOT entry is the target checked
   word-for-word against subsdk0, and on any mismatch every upload stays on
   the stock path for the rest of the run.

Both are ON by default since BUILD 788b (hardware, 2026-10-23). The caves live in a
translated function body that is proven dead (x86 0x66F5FF), refused if any
earlier pass touched it, with BSS of their own. The pass runs after every
feature pass and before the frame probe, so a probe build times the result.

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
nxpaths.install()
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_frameprobe as FP
import nxmap
from ff7nx_audio_cave import Asm

STATE_ENV = 'SEVENTH_NX_GL_STATE'
UPLOAD_ENV = 'SEVENTH_NX_GL_UPLOAD'
UPLOAD_MODES = ('off', 'stream', 'sub', 'ubo')


# BUILD 788b: both ON by default, confirmed on hardware 2026-10-23 (fship_2
# from 18.3 ms to a locked 16.67 ms; fields, world map, battles and FMVs
# look right). SEVENTH_NX_GL_STATE=0 / SEVENTH_NX_GL_UPLOAD=off turn them off.
DEFAULT_ATTACH = True
DEFAULT_UPLOAD = 'sub'


def attach_enabled(env=None):
    raw = str((os.environ if env is None else env).get(STATE_ENV, '')
              ).strip().lower()
    if raw in ('1', 'on', 'yes', 'true'):
        return True
    if raw in ('0', 'off', 'no', 'false'):
        return False
    return DEFAULT_ATTACH


def upload_mode(env=None):
    raw = str((os.environ if env is None else env).get(UPLOAD_ENV, '')
              ).strip().lower()
    return raw if raw in UPLOAD_MODES else DEFAULT_UPLOAD


def enabled(env=None):
    return attach_enabled(env) or upload_mode(env) != 'off'


# ----------------------------------------------------------- the GL layer
GL_LO, GL_HI = 0x1132000, 0x113C900       # the port's GL wrapper
STATE_GOT = 0x12CEB80                     # -> -> the bind cache object
USAGE_STATIC = 0x52911C83                 # mov w3, #0x88e4 (STATIC_DRAW)
USAGE_STREAM = 0x52911C03                 # mov w3, #0x88e0 (STREAM_DRAW)
FB_TARGETS = (0x8D40, 0x8CA9)             # GL_FRAMEBUFFER, GL_DRAW_FRAMEBUFFER
COLOR0, DEPTH, STENCIL = 0x8CE0, 0x8D00, 0x8D20

# Every call into these imports, from a stock module: (va, is a tail call).
# The pass rescans the module it patches and refuses unless it finds exactly
# these and nothing else, so a branch some other pass adds is never missed.
SITES = {
    'glBindFramebuffer': (0x11338A8, 0x11338C8, 0x11338E8,
                          0x113C0A8, 0x113C6C0, 0x113C7C8),
    'glFramebufferTexture2D': (0x1133034, 0x1133074, 0x11330D4, 0x1133150,
                               0x1133254, 0x1133294, 0x11398DC, 0x1139900),
    'glFramebufferRenderbuffer': (0x1132FB8, 0x1132FD4, 0x1132FF4, 0x1133050,
                                  0x1133094, 0x11330F0, 0x1133110, 0x1133178,
                                  0x11331FC, 0x1133238, 0x11398C0, 0x113C264,
                                  0x113C470),
    'glDeleteTextures': (0x11393C8, 0x1139468, 0x11394EC, 0x1139EC8,
                         0x1139F58, 0x1139FDC, 0x113A3A8, 0x113A438,
                         0x113A4BC),
    'glDeleteRenderbuffers': (0x11343F0, 0x1134490, 0x1134520, 0x1134A20,
                              0x1134AC0, 0x1134B50),
    'glDeleteFramebuffers': (0x1132D88, 0x1132E08, 0x1132E7C, 0x1139954),
    'glDeleteBuffers': (0x1132854, 0x1132904, 0x11329C0),
    'glBufferData': (0x1132518, 0x1132728, 0x1132AA8),
    # BUILD 788c: per-program state
    'glUseProgram': (0x1133968,),
    'glLinkProgram': (0x11388F4,),
    'glDeleteProgram': (0x11387EC, 0x113886C, 0x113894C),
    'glGetUniformBlockIndex': (0x1136B2C, 0x1136BDC),
    'glUniformBlockBinding': (0x1136B48, 0x1136BF8),
    'glUniform1i': (0x1136A5C,),
}
ATTACH_IMPORTS = ('glBindFramebuffer', 'glFramebufferTexture2D',
                  'glFramebufferRenderbuffer', 'glDeleteTextures',
                  'glDeleteRenderbuffers', 'glDeleteFramebuffers',
                  'glUseProgram', 'glLinkProgram', 'glDeleteProgram',
                  'glGetUniformBlockIndex', 'glUniformBlockBinding',
                  'glUniform1i')

# The three uploads: (bl glBufferData, its `mov w3, #usage`, the bind-cache
# field that holds the name just bound for that target, target, name).
UPLOADS = ((0x1132518, 0x113250C, 0x224, 0x8892, 'vertex'),
           (0x1132728, 0x113271C, 0x228, 0x8893, 'index'),
           (0x1132AA8, 0x1132A9C, 0x22C, 0x8A11, 'uniform'))

# glBufferData / glBufferSubData in subsdk0 (the driver's export trampolines)
SUBSDK_BUFFERDATA = 0x36D40
SUBDATA_DELTA = 0x70
CHECK_OFFS = (0x40, SUBDATA_DELTA + 0x10, SUBDATA_DELTA + 0x40)

# ------------------------------------------------------------- BSS layout
CUR = 0x00          # u32 the bound draw framebuffer (0 = do not cache)
SUBPTR = 0x08       # u64 0 = unresolved, 1 = unavailable, else glBufferSubData
TOTAL = 0x10        # u64 bytes of storage the tracked buffers hold
STATS = 0x20        # u32 x 6: skipped, attached, fits, grows, plain, flushes
ATT = 0x40          # 64 framebuffers x 8 slots x 16 bytes
ATT_FBS, ATT_SLOTS = 64, 8
ATT_BYTES = ATT_FBS * ATT_SLOTS * 16
CAP = ATT + ATT_BYTES       # u32 storage size per buffer name
NAMES = 4096
CUR_PROG = 0x04     # u32 the program glUseProgram last made current
# BUILD 788c: per program name 1..255, 256 bytes: +0/+16 the two uniform
# block lookups (name pointer u64, index | 1 << 32), +32/+40 the block
# binding of binding points 0/1 (index | 1 << 32), +64 glUniform1i for
# locations 0..15 (value | 1 << 32). Linking or deleting any program forgets
# all of it (a link resets uniforms and block bindings).
PROG = CAP + 4 * NAMES
PROGS, PROG_STRIDE = 256, 256
UBI, UBB, U1I = 0, 32, 64
U1I_LOCS = 16
BSS_BYTES = PROG + PROGS * PROG_STRIDE
GRAIN = 4096
BUDGET = 48 << 20           # tracked storage never grows past this
ST_SKIP, ST_ATTACH, ST_FITS, ST_GROW, ST_PLAIN, ST_FLUSH, ST_PROG = range(7)

# The cave body: x86 0x66F5FF (9 KB of ARM), dead like the probe's own.
OWN_DEAD_X86 = 0x66F5FF
OWN_GUARD = 0x40
OWN_TAIL = 0x40

EQ, NE, HS, LO, HI, LS, LE = 0, 1, 2, 3, 8, 9, 0xD
XZR = 31


# ---------------------------------------------------- encoders not in a64
def ldr_w_reg(rt, rn, rm, shift=False):
    """LDR Wt, [Xn, Xm{, LSL #2}]"""
    return 0xB8606800 | (rm << 16) | (int(shift) << 12) | (rn << 5) | rt


def str_w_reg(rt, rn, rm, shift=False):
    """STR Wt, [Xn, Xm{, LSL #2}]"""
    return 0xB8206800 | (rm << 16) | (int(shift) << 12) | (rn << 5) | rt


def orr64_lsl(rd, rn, rm, sh):
    """ORR Xd, Xn, Xm, LSL #sh"""
    return 0xAA000000 | (rm << 16) | (sh << 10) | (rn << 5) | rd


def lsr64(rd, rn, sh):
    """LSR Xd, Xn, #sh (UBFM)"""
    return 0xD340FC00 | (sh << 16) | (rn << 5) | rd


def lsl64(rd, rn, sh):
    """LSL Xd, Xn, #sh (UBFM)"""
    return 0xD3400000 | (((64 - sh) % 64) << 16) | ((63 - sh) << 10) | (rn << 5) | rd


def add_imm64_lsl12(rd, rn, imm):
    """ADD Xd, Xn, #imm, LSL #12"""
    return 0x91400000 | (imm << 10) | (rn << 5) | rd


def stp_zero_post16(rn):
    return 0xA8817C00 | (31 << 10) | (rn << 5) | 31     # stp xzr,xzr,[xn],#16


def blr(rn):
    return 0xD63F0000 | (rn << 5)


def mov_from_sp(rd):
    return 0x91000000 | (31 << 5) | rd                  # mov xd, sp


class LitAsm(Asm):
    """Asm with 64-bit literals placed after the code."""

    def __init__(self, at, addr):
        super().__init__(at, addr)
        self.lits = []

    def ldr_lit(self, reg, value):
        """Xreg = value: adr Xreg, <literal>; ldr Xreg, [Xreg]."""
        self.fixups.append((len(self.words), ('lit', len(self.lits)), 'lit',
                            reg))
        self.lits.append(value)
        self.words.append(0)
        self.emit(A.ldr64(reg, reg, 0))

    def resolve(self):
        if len(self.words) % 2:
            self.emit(A.nop())
        lit_at = []
        for v in self.lits:
            lit_at.append(self.pc())
            self.emit(v & 0xFFFFFFFF)
            self.emit(v >> 32)
        plain = [f for f in self.fixups if f[2] != 'lit']
        for idx, (_l, k), _kind, reg in [f for f in self.fixups
                                          if f[2] == 'lit']:
            self.words[idx] = A.adr(reg, self.addr(idx), lit_at[k])
        self.fixups = plain
        return super().resolve()


def _stat(a, base_reg, which, tmp):
    a.emit(A.ldr(tmp, base_reg, STATS + 4 * which))
    a.emit(A.add_imm(tmp, tmp, 1))
    a.emit(A.str_(tmp, base_reg, STATS + 4 * which))


def _cmp_const(a, reg, value, tmp):
    a.emit(A.movz(tmp, value))
    a.emit(A.cmp_reg(reg, tmp))


def _flush(a, base, tmp_ptr=9, tmp_n=10):
    """Forget every remembered attachment. x9, x10 only."""
    AC.bss_ptr(a, tmp_ptr, base + ATT)
    a.emit(A.movz(tmp_n, ATT_FBS * ATT_SLOTS))
    a.label('flush_loop')
    a.emit(stp_zero_post16(tmp_ptr))
    a.emit(A.sub_imm(tmp_n, tmp_n, 1))
    a.cbnz(tmp_n, 'flush_loop')


# ------------------------------------------------------------------ caves
# Each cave stands in for one import and is entered exactly as the import's
# stub would be -- by `bl` or by a tail `b` -- so x30 is always the caller's
# return address. At a function's entry x9..x17 hold nothing a caller needs
# (AAPCS), and only those, the flags and the cave's own BSS are touched before
# the cave either returns or tail-branches to the real stub with x0..x7 as
# they came.
def build_bind(_e, addr, base, stub):
    """glBindFramebuffer(target, fb): remember fb when the draw binding moves."""
    a = Asm(_e, addr)
    _cmp_const(a, 0, FB_TARGETS[0], 10)
    a.bcond('set', EQ)
    _cmp_const(a, 0, FB_TARGETS[1], 10)
    a.bcond('pass', NE)
    a.label('set')
    AC.bss_ptr(a, 9, base)
    a.emit(A.str_(1, 9, CUR))
    a.label('pass')
    a.emit(A.b(a.pc(), stub))
    return a.resolve()


def build_attach(_e, addr, base, stub, texture):
    """glFramebufferTexture2D(target, attachment, textarget, texture, level)
    or glFramebufferRenderbuffer(target, attachment, rbtarget, rb).

    Key per (current framebuffer, slot): x12 = name | textarget << 32,
    x13 = level << 32 | kind << 1 | 1 (valid). A detach (name 0) is one key
    whichever function made it."""
    a = Asm(_e, addr)
    _cmp_const(a, 0, FB_TARGETS[0], 10)
    a.bcond('target', EQ)
    _cmp_const(a, 0, FB_TARGETS[1], 10)
    a.bcond('flush', NE)
    a.label('target')
    a.emit(A.movz(10, COLOR0))
    a.emit(A.sub_reg(10, 1, 10))
    a.emit(A.cmp_imm(10, 4))
    a.bcond('slot', LO)
    _cmp_const(a, 1, DEPTH, 11)
    a.bcond('not_depth', NE)
    a.emit(A.movz(10, 4))
    a.b('slot')
    a.label('not_depth')
    _cmp_const(a, 1, STENCIL, 11)
    a.bcond('flush', NE)
    a.emit(A.movz(10, 5))
    a.label('slot')
    AC.bss_ptr(a, 9, base)
    a.emit(A.ldr(11, 9, CUR))
    a.emit(A.sub_imm(12, 11, 1))
    a.emit(A.cmp_imm(12, ATT_FBS - 2))
    a.bcond('pass', HI)                   # only framebuffers 1..63
    a.cbz(3, 'detach')
    a.emit(A.mov_reg(12, 3))
    a.emit(A.bfi64(12, 2, 32, 32))
    if texture:
        a.emit(A.movz(13, 1))
        a.emit(A.bfi64(13, 4, 32, 32))
    else:
        a.emit(A.movz(13, 3))
    a.b('key')
    a.label('detach')
    a.emit(A.movz(12, 0))
    a.emit(A.movz(13, 1))
    a.label('key')
    a.emit(A.add_reg64_lsl(14, 9, 11, 7))
    a.emit(A.add_reg64_lsl(14, 14, 10, 4))
    a.emit(A.add_imm64(14, 14, ATT))
    a.emit(A.ldp64_off(15, 17, 14, 0))
    a.emit(A.cmp_reg64(15, 12))
    a.emit(A.ccmp_reg64(17, 13, 0, EQ))
    a.bcond('miss', NE)
    _stat(a, 9, ST_SKIP, 10)
    a.emit(A.ret())                       # already attached: a GL no-op
    a.label('miss')
    a.emit(A.stp64_off(12, 13, 14, 0))
    _stat(a, 9, ST_ATTACH, 10)
    a.label('pass')
    a.emit(A.b(a.pc(), stub))
    a.label('flush')
    _flush(a, base)
    AC.bss_ptr(a, 9, base)
    _stat(a, 9, ST_FLUSH, 10)
    a.emit(A.b(a.pc(), stub))
    return a.resolve()


def build_delete(_e, addr, base, stub, framebuffers):
    """glDelete{Textures,Renderbuffers,Framebuffers}(n, names): forget every
    attachment (a deleted name can come back for a new object). Deleting
    framebuffers also stops caching until the next bind: if the bound one
    went, GL fell back to 0."""
    a = Asm(_e, addr)
    if framebuffers:
        AC.bss_ptr(a, 9, base)
        a.emit(A.str_(XZR, 9, CUR))
    _flush(a, base)
    a.emit(A.b(a.pc(), stub))
    return a.resolve()


def _valid_key(a, dst, src):
    """Xdst = Wsrc | 1 << 32 -- a remembered 32-bit value, marked valid."""
    a.emit(A.mov_reg(dst, src))
    a.emit(A.movz(15, 1))
    a.emit(A.bfi64(dst, 15, 32, 32))


def _prog_slot(a, base, dst, prog_reg):
    """Xdst = &PROG[Wprog]. x12 too."""
    AC.bss_ptr(a, dst, base + PROG)
    a.emit(A.mov_reg(12, prog_reg))
    a.emit(A.add_reg64_lsl(dst, dst, 12, 8))


def build_use_program(_e, addr, base, stub):
    """glUseProgram(program): remember which one is current."""
    a = Asm(_e, addr)
    AC.bss_ptr(a, 9, base)
    a.emit(A.str_(0, 9, CUR_PROG))
    a.emit(A.b(a.pc(), stub))
    return a.resolve()


def build_forget_programs(_e, addr, base, stub):
    """glLinkProgram / glDeleteProgram: forget every program's state."""
    a = Asm(_e, addr)
    AC.bss_ptr(a, 9, base + PROG)
    AC.mov32(a, 10, PROGS * PROG_STRIDE // 16)
    a.label('loop')
    a.emit(stp_zero_post16(9))
    a.emit(A.sub_imm(10, 10, 1))
    a.cbnz(10, 'loop')
    a.emit(A.b(a.pc(), stub))
    return a.resolve()


def build_block_index(_e, addr, base, stub, k):
    """glGetUniformBlockIndex(program, name) at call site k: the index the
    driver gave for this program and this name pointer last time."""
    a = Asm(_e, addr)
    a.emit(A.sub_imm(10, 0, 1))
    a.emit(A.cmp_imm(10, PROGS - 2))
    a.bcond('pass', HI)
    _prog_slot(a, base, 11, 0)
    a.emit(A.add_imm64(11, 11, UBI + 16 * k))
    a.emit(A.ldp64_off(13, 14, 11, 0))
    a.emit(A.cmp_reg64(13, 1))
    a.bcond('miss', NE)
    a.emit(lsr64(15, 14, 32))
    a.cbz(15, 'miss')
    a.emit(A.mov_reg(0, 14))
    AC.bss_ptr(a, 9, base)
    _stat(a, 9, ST_PROG, 10)
    a.emit(A.ret())
    a.label('miss')
    a.emit(A.stp64_pre(29, 30, 31, -32))
    a.emit(mov_from_sp(29))
    a.emit(A.stp64_off(1, 11, 31, 16))
    a.emit(A.bl(a.pc(), stub))
    a.emit(A.ldp64_off(1, 11, 31, 16))
    a.emit(A.ldp64_post(29, 30, 31, 32))
    _valid_key(a, 14, 0)
    a.emit(A.stp64_off(1, 14, 11, 0))
    a.emit(A.ret())
    a.label('pass')
    a.emit(A.b(a.pc(), stub))
    return a.resolve()


def build_block_binding(_e, addr, base, stub):
    """glUniformBlockBinding(program, index, binding 0/1): drop a call that
    repeats that program's binding."""
    a = Asm(_e, addr)
    a.emit(A.sub_imm(10, 0, 1))
    a.emit(A.cmp_imm(10, PROGS - 2))
    a.bcond('pass', HI)
    a.emit(A.cmp_imm(2, 2))
    a.bcond('pass', HS)
    _prog_slot(a, base, 11, 0)
    a.emit(A.mov_reg(13, 2))
    a.emit(A.add_reg64_lsl(11, 11, 13, 3))
    _valid_key(a, 14, 1)
    a.emit(A.ldr64(13, 11, UBB))
    a.emit(A.cmp_reg64(13, 14))
    a.bcond('skip', EQ)
    a.emit(A.str64(14, 11, UBB))
    a.label('pass')
    a.emit(A.b(a.pc(), stub))
    a.label('skip')
    AC.bss_ptr(a, 9, base)
    _stat(a, 9, ST_PROG, 10)
    a.emit(A.ret())
    return a.resolve()


def build_uniform1i(_e, addr, base, stub):
    """glUniform1i(location 0..15, value) on the current program: drop a
    call that repeats what that program's location holds."""
    a = Asm(_e, addr)
    AC.bss_ptr(a, 9, base)
    a.emit(A.ldr(10, 9, CUR_PROG))
    a.emit(A.sub_imm(11, 10, 1))
    a.emit(A.cmp_imm(11, PROGS - 2))
    a.bcond('pass', HI)
    a.emit(A.cmp_imm(0, U1I_LOCS))
    a.bcond('pass', HS)
    _prog_slot(a, base, 11, 10)
    a.emit(A.mov_reg(13, 0))
    a.emit(A.add_reg64_lsl(11, 11, 13, 3))
    _valid_key(a, 14, 1)
    a.emit(A.ldr64(13, 11, U1I))
    a.emit(A.cmp_reg64(13, 14))
    a.bcond('skip', EQ)
    a.emit(A.str64(14, 11, U1I))
    a.label('pass')
    a.emit(A.b(a.pc(), stub))
    a.label('skip')
    _stat(a, 9, ST_PROG, 10)
    a.emit(A.ret())
    return a.resolve()


def build_delete_buffers(_e, addr, base, stub):
    """glDeleteBuffers(n, names): each tracked name's storage is gone."""
    a = Asm(_e, addr)
    AC.bss_ptr(a, 9, base + CAP)
    AC.bss_ptr(a, 10, base)
    a.emit(A.mov_reg(11, 0))
    a.emit(AC.mov64(12, 1))
    a.label('loop')
    a.emit(A.cmp_imm(11, 0))
    a.bcond('done', LE)                   # n <= 0
    a.emit(A.ldr_post(13, 12, 4))
    a.emit(A.sub_imm(14, 13, 1))
    a.emit(A.cmp_imm(14, NAMES - 2))
    a.bcond('next', HI)
    a.emit(A.add_reg64_lsl(13, 9, 13, 2))
    a.emit(A.ldr(15, 13, 0))
    a.emit(A.str_(XZR, 13, 0))
    a.emit(A.ldr64(14, 10, TOTAL))
    a.emit(A.sub_reg64(14, 14, 15))
    a.emit(A.str64(14, 10, TOTAL))
    a.label('next')
    a.emit(A.sub_imm(11, 11, 1))
    a.b('loop')
    a.label('done')
    a.emit(A.b(a.pc(), stub))
    return a.resolve()


def build_upload_site(_e, addr, field, body):
    """At a `bl glBufferData` site: w9 = the bind-cache field, then the body."""
    a = Asm(_e, addr)
    a.emit(A.movz(9, field))
    a.emit(A.b(a.pc(), body))
    return a.resolve()


def build_upload(_e, addr, base, stub, got, expect):
    """glBufferData(target w0, size x1, data x2, usage w3), w9 = cache field.

    The name the port just bound is read back from its bind cache. If it is a
    tracked name (1..4095) and the upload fits the storage the buffer already
    has, glBufferSubData(target, 0, size, data) replaces the data and keeps
    the storage. If it does not fit, the storage grows to a 4 KB multiple
    (glBufferData with no data) first, unless that would take the tracked
    total past BUDGET. Everything else -- untracked names, no data, size 0,
    glBufferSubData not yet resolved or not the expected code -- is the stock
    call, and records the size it gave the buffer."""
    a = LitAsm(_e, addr)
    a.emit(A.stp64_pre(29, 30, 31, -0x40))
    a.emit(mov_from_sp(29))
    a.emit(A.stp64_off(19, 20, 31, 0x10))
    a.emit(A.stp64_off(21, 22, 31, 0x20))
    a.emit(A.stp64_off(23, 24, 31, 0x30))
    a.emit(A.mov_reg(19, 0))
    a.emit(AC.mov64(20, 1))
    a.emit(AC.mov64(21, 2))
    a.emit(A.mov_reg(22, 3))
    AC.bss_ptr(a, 24, base)
    a.emit(A.adrp(10, a.pc(), STATE_GOT & ~0xFFF))
    a.emit(A.ldr64(10, 10, STATE_GOT & 0xFFF))
    a.emit(A.ldr64(10, 10, 0))
    a.emit(A.add_reg64(10, 10, 9))
    a.emit(A.ldr(23, 10, 0))              # the bound name
    a.emit(A.sub_imm(10, 23, 1))
    a.emit(A.cmp_imm(10, NAMES - 2))
    a.bcond('stock', HI)                  # untracked: just the stock call
    AC.bss_ptr(a, 11, base + CAP)
    a.emit(A.add_reg64_lsl(23, 11, 23, 2))  # x23 = &CAP[name] from here on
    a.emit(lsr64(10, 20, 31))
    a.cbnz(10, 'plain')                   # >= 2 GB
    a.cbz64(20, 'plain')
    a.cbz64(21, 'plain')
    a.emit(A.ldr64(10, 24, SUBPTR))
    a.emit(0xF100055F)                    # cmp x10, #1
    a.bcond('plain', LS)                  # 0 or 1
    a.emit(A.ldr(12, 23, 0))
    a.emit(A.cmp_reg64(20, 12))
    a.bcond('grow', HI)
    a.label('fits')
    a.emit(A.mov_reg(0, 19))
    a.emit(AC.mov64(1, XZR))
    a.emit(AC.mov64(2, 20))
    a.emit(AC.mov64(3, 21))
    a.emit(blr(10))
    _stat(a, 24, ST_FITS, 10)
    a.b('out')

    a.label('grow')                       # w12 = the storage it has now
    a.emit(A.add_imm64(13, 20, GRAIN - 1))
    a.emit(lsr64(13, 13, 12))
    a.emit(lsl64(13, 13, 12))             # newcap, a GRAIN multiple
    a.emit(A.ldr64(14, 24, TOTAL))
    a.emit(A.add_reg64(14, 14, 13))
    a.emit(A.sub_reg64(14, 14, 12))
    AC.mov32(a, 15, BUDGET)
    a.emit(A.cmp_reg64(14, 15))
    a.bcond('plain', HI)
    a.emit(A.str64(14, 24, TOTAL))
    a.emit(A.str_(13, 23, 0))
    a.emit(A.mov_reg(0, 19))
    a.emit(AC.mov64(1, 13))
    a.emit(AC.mov64(2, XZR))
    a.emit(A.mov_reg(3, 22))
    a.emit(A.bl(a.pc(), stub))
    _stat(a, 24, ST_GROW, 10)
    a.emit(A.ldr64(10, 24, SUBPTR))
    a.b('fits')

    a.label('plain')                      # tracked: CAP = what the call gives
    a.emit(A.ldr(12, 23, 0))
    a.emit(lsr64(13, 20, 31))
    a.emit(AC.mov64(14, 20))
    a.cbz64(13, 'small')
    a.emit(A.movz(14, 0))
    a.label('small')
    a.emit(A.str_(14, 23, 0))
    a.emit(A.ldr64(15, 24, TOTAL))
    a.emit(A.mov_reg(14, 14))
    a.emit(A.add_reg64(15, 15, 14))
    a.emit(A.sub_reg64(15, 15, 12))
    a.emit(A.str64(15, 24, TOTAL))
    a.label('stock')
    a.emit(A.mov_reg(0, 19))
    a.emit(AC.mov64(1, 20))
    a.emit(AC.mov64(2, 21))
    a.emit(A.mov_reg(3, 22))
    a.emit(A.bl(a.pc(), stub))
    _stat(a, 24, ST_PLAIN, 10)
    # after a stock call the lazy binder has filled glBufferData's GOT entry
    a.emit(A.ldr64(10, 24, SUBPTR))
    a.cbnz(10, 'out')                     # (resolved once, either way)
    a.emit(A.adrp(10, a.pc(), got & ~0xFFF))
    a.emit(A.ldr64(10, 10, got & 0xFFF))
    a.emit(A.movz(11, 1))
    for k, (off, word) in enumerate(zip(CHECK_OFFS, expect)):
        a.emit(A.ldr64(12, 10, off))
        a.ldr_lit(13, word)
        a.emit(A.cmp_reg64(12, 13))
        a.bcond('mismatch', NE)
    a.emit(A.add_imm64(11, 10, SUBDATA_DELTA))
    a.label('mismatch')
    a.emit(A.str64(11, 24, SUBPTR))

    a.label('out')
    a.emit(A.ldp64_off(23, 24, 31, 0x30))
    a.emit(A.ldp64_off(21, 22, 31, 0x20))
    a.emit(A.ldp64_off(19, 20, 31, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, 0x40))
    a.emit(A.ret())
    return a.resolve()


# ---------------------------------------------------------------- module
def _branch_target(word, va):
    if (word & 0x7C000000) != 0x14000000:
        return None
    imm = word & 0x03FFFFFF
    if imm & 0x02000000:
        imm -= 0x04000000
    return va + imm * 4


def call_sites(text, stubs, names):
    """{name: [(va, is_tail)]} -- every b/bl in .text to each named stub."""
    want = {stubs[n][0]: n for n in names}
    out = {n: [] for n in names}
    for va in range(0, len(text) - 3, 4):
        w = struct.unpack_from('<I', text, va)[0]
        t = _branch_target(w, va)
        if t in want:
            out[want[t]].append((va, not (w & 0x80000000)))
    return out


def got_loads(text, stubs, names):
    """adrp+ldr/add pairs naming one of these GOT slots or stubs outside the
    stubs themselves -- a function pointer this pass could not see through."""
    targets = {}
    for n in names:
        va, got = stubs[n]
        targets[got] = n
        targets[va] = n
    stub_words = {stubs[n][0] + 4 * k for n in names for k in range(4)}
    bad = []
    for va in range(0, len(text) - 7, 4):
        w = struct.unpack_from('<I', text, va)[0]
        if (w & 0x9F000000) != 0x90000000 or va in stub_words:
            continue
        imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
        if imm & (1 << 20):
            imm -= 1 << 21
        page = (va & ~0xFFF) + (imm << 12)
        rd = w & 31
        for k in range(1, 4):
            if va + 4 * k + 4 > len(text):
                break
            n = struct.unpack_from('<I', text, va + 4 * k)[0]
            if (n >> 5) & 31 != rd:
                continue
            if (n & 0xFFC00000) == 0xF9400000:            # ldr x, [rd, #o]
                off = ((n >> 10) & 0xFFF) * 8
            elif (n & 0xFFC00000) == 0x91000000:          # add x, rd, #o
                off = (n >> 10) & 0xFFF
            else:
                continue
            if page + off in targets:
                bad.append('+0x%X takes the address of %s'
                           % (va, targets[page + off]))
    return bad


def own_region(src, stock=None, exe=None):
    """(lo, hi) of this pass's dead body in `src`, proven dead and unused."""
    import ff7nx_fieldbg
    m = nxmap.Main(src)
    why = ff7nx_fieldbg._liveness(m, OWN_DEAD_X86)
    if not why:
        if exe is None:
            path = os.path.join(nxpaths.ROOT, 'dump', 'romfs', 'ff7',
                                'resources', 'ff7_1.02', 'ff7_en')
            with open(path, 'rb') as handle:
                exe = handle.read()
        import ff7nx_fxinterp
        why = ff7nx_fxinterp._x86_unreferenced(exe, OWN_DEAD_X86)
    if why:
        raise ValueError('x86 0x%X is not dead: %s' % (OWN_DEAD_X86, why))
    lo, hi = m.extent(OWN_DEAD_X86)
    lo, hi = lo + OWN_GUARD, hi - OWN_TAIL
    if stock is not None:
        if nxmap.Main(stock).text[lo:hi] != m.text[lo:hi]:
            raise ValueError('the body at +0x%X was already modified by '
                             'another pass' % lo)
    return lo, hi


def subsdk_words(path):
    """The three u64s glBufferData's GOT target must hold (CHECK_OFFS)."""
    with open(path, 'rb') as handle:
        segs, raw = AC.segments(handle.read())
    img = FP._image(segs, raw)
    return [struct.unpack_from('<Q', img, SUBSDK_BUFFERDATA + off)[0]
            for off in CHECK_OFFS]


def apply_to_nso(src, dest, stock=None, subsdk=None, attach=True,
                 upload='off'):
    if upload not in UPLOAD_MODES:
        raise ValueError('upload mode %r' % upload)
    if not attach and upload == 'off':
        raise ValueError('nothing to do')
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    stubs = FP.import_stubs(segs, raw)
    names = list(SITES)
    missing = [n for n in names if n not in stubs]
    if missing:
        raise ValueError('no import stub for %s' % ', '.join(missing))
    found = call_sites(text, stubs, names)
    bad = []
    for n in names:
        have = sorted(va for va, _t in found[n])
        if have != sorted(SITES[n]):
            bad.append('%s is called from %s, expected %s'
                       % (n, ' '.join('+0x%X' % v for v in have),
                          ' '.join('+0x%X' % v for v in SITES[n])))
    bad += got_loads(text, stubs, names)
    if upload == 'stream' or upload in ('sub', 'ubo'):
        for site, usage, _f, _t, what in UPLOADS:
            if struct.unpack_from('<I', text, usage)[0] != USAGE_STATIC:
                bad.append('+0x%X (%s upload) is not mov w3, #0x88e4'
                           % (usage, what))
    if bad:
        raise ValueError('; '.join(bad))
    expect = None
    if upload in ('sub', 'ubo'):
        if subsdk is None or not os.path.isfile(subsdk):
            raise ValueError('sub uploads need exefs/subsdk0 from the dump')
        expect = subsdk_words(subsdk)

    lo, hi = own_region(src, stock)
    scratch = AC.scratch_base(blob, segs)
    base = (scratch + 15) & ~15
    pool = FP.Bump(lo, hi)
    placed = {}
    tails = {n: dict((va, t) for va, t in found[n]) for n in names}

    def hook(name, cave):
        for va, tail in tails[name].items():
            placed[va] = (A.b if tail else A.bl)(va, cave)

    stub = {n: stubs[n][0] for n in names}
    hooked = {}
    if attach:
        hook('glBindFramebuffer', pool.put(
            lambda e, ad: build_bind(e, ad, base, stub['glBindFramebuffer'])))
        for n, tex in (('glFramebufferTexture2D', True),
                       ('glFramebufferRenderbuffer', False)):
            hook(n, pool.put(lambda e, ad, s=stub[n], t=tex:
                             build_attach(e, ad, base, s, t)))
        for n, fb in (('glDeleteTextures', False),
                      ('glDeleteRenderbuffers', False),
                      ('glDeleteFramebuffers', True)):
            hook(n, pool.put(lambda e, ad, s=stub[n], f=fb:
                             build_delete(e, ad, base, s, f)))
        hook('glUseProgram', pool.put(
            lambda e, ad: build_use_program(e, ad, base,
                                            stub['glUseProgram'])))
        for n in ('glLinkProgram', 'glDeleteProgram'):
            hook(n, pool.put(lambda e, ad, s=stub[n]:
                             build_forget_programs(e, ad, base, s)))
        for k, va in enumerate(SITES['glGetUniformBlockIndex']):
            cave = pool.put(lambda e, ad, kk=k: build_block_index(
                e, ad, base, stub['glGetUniformBlockIndex'], kk))
            placed[va] = (A.b if tails['glGetUniformBlockIndex'][va]
                          else A.bl)(va, cave)
        hook('glUniformBlockBinding', pool.put(
            lambda e, ad: build_block_binding(
                e, ad, base, stub['glUniformBlockBinding'])))
        hook('glUniform1i', pool.put(
            lambda e, ad: build_uniform1i(e, ad, base, stub['glUniform1i'])))
        for n in ATTACH_IMPORTS:
            hooked[n] = len(tails[n])
    streamed = []
    if upload == 'stream':
        for _site, usage, _f, _t, what in UPLOADS:
            placed[usage] = USAGE_STREAM
            streamed.append(what)
    subbed = []
    if upload in ('sub', 'ubo'):
        body = pool.put(lambda e, ad: build_upload(
            e, ad, base, stub['glBufferData'], stubs['glBufferData'][1],
            expect))
        for site, _usage, field, _t, what in UPLOADS:
            if upload == 'ubo' and what != 'uniform':
                continue
            cave = pool.put(lambda e, ad, f=field:
                            build_upload_site(e, ad, f, body))
            placed[site] = A.bl(site, cave)
            subbed.append(what)
        hook('glDeleteBuffers', pool.put(lambda e, ad: build_delete_buffers(
            e, ad, base, stub['glDeleteBuffers'])))
        hooked['glDeleteBuffers'] = len(tails['glDeleteBuffers'])
    words = (pool.at - lo) // 4 if pool.placed else 0
    placed.update(pool.placed)
    if pool.placed:
        # the rest of the body becomes a trap: a stray jump into it stops
        for va in range(pool.at, hi, 4):
            placed[va] = 0
    for va, w in placed.items():
        struct.pack_into('<I', text, va, w)
    growth = 0
    if attach or subbed:
        growth = base - scratch + BSS_BYTES + AC.bss_tail_slack(segs)
    out = AC.pack(blob, [bytes(text), raw[1], raw[2]], growth)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'base': base, 'region': lo, 'words': words, 'bss': growth,
            'hooked': hooked, 'stream': streamed, 'sub': subbed,
            'attach': attach, 'upload': upload, 'expect': expect}
