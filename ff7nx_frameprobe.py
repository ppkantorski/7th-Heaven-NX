#!/usr/bin/env python3
r"""
ff7nx_frameprobe.py -- a per-frame timing probe. DIAGNOSTIC ONLY, off unless
SEVENTH_NX_FRAME_PROBE=1.

WHY
===
Three limiter builds (525/526/527) each rested on a model of the heavy frames
that was never measured. This measures them. Every presented frame is split
into the pieces the code actually has, timed with the hardware counter
(`mrs cntpct_el0`, 19.2 MHz -- the same clock nn::os::GetSystemTick reads):

    sec  name      what is timed                               how
    0    LOGIC     field_loop_sub_63C17F (scripts, movement,   call sites +0x946740
                   camera, bg positions)                        and +0x94BEC0
    1    ANIM      field_animate_3d_models_6392BB               +0x9470A4, +0x94C678
    2    DRAW      field_draw_everything_63A60B                 +0x94B898, +0x94BAC8
    3    LIMIT     the field limiter spin 0x6384E6              +0x94B920, +0x94BBCC
    4    CATCHUP   the debt path's extra frame 0x60E96C         +0x94BA54
    5    DRVFLIP   native gfx_drv_flip, entry -> its tail call  +0x10DA880 / +0x10DABC0
    6    TIMER     the port's timeSetEvent pump +0x10ECC20      entry / ret
                   (run from +0xA550, every 2048th rdtsc call,
                   once per elapsed virtual millisecond)
    7    TEXLOAD   native gfx_drv_load_texture (palette ->      +0x10D6BC0 / ret
                   RGBA conversion + glTexStorage/SubImage)
    8    OGG       vgmstream open (voice probe, player,         +0x10FE090 / ret
                   ambience) -- ANY thread, see caveat
    9    TCB       the x86 timer callbacks that pump fires      call site +0x10ECCB0

VERSION 3 (after the v2 dumps): the timer pump measured 0.01 ms, so it is
NOT what fills the limiter. What v3 records instead, at the frame's LAST
limiter call, is the limiter's own view: elapsed = virtual clock - baseline
(guest 0xCFF8D8), the debt flag (0xCFFA98) and debt amount (0xCFFAA0), and,
per frame, how far the virtual clock drifted from the hardware counter. Slots
6/8/9 carry those (el, debt, drift); the TIMER/TCB hooks are gone.

VERSION 2 (after the first three hardware dumps): shader compiles and the
per-flip checker both measured 0.00 ms in every worst frame, so their slots
now time the emulated multimedia timer. In the first dumps the limiter
section held ~14.6 ms on frames whose work was ALREADY past the deadline --
time the limiter's own spin cannot account for, but a timer pump inside it
can. The kernel also clears x8..x28 on svcBreak (measured), so only x0..x7,
FP and LR carry data; the per-visit sums moved to the TLS dump.

plus per-frame counts: textures created (gfx_create_texture +0x4620) and the
pixels they carry, palette writes (gfx_drv_write_palette +0x10D9800), OGG
opens, shader compiles.

A frame ends where gfx_drv_flip hands over to its tail call, so every mode
(field, battle, menu, world) is recorded; only the field sections are split.

WHAT IT KEEPS
=============
Reset whenever the current field id (guest 0xCFF468, the maplist index ff7nx_daynight uses) changes:
  * sums of every section and of the frame period, over the whole field visit
  * how many frames ran over 16.9 / 18 / 20 / 25 / 33.4 ms
  * the SIXTEEN WORST frames of the visit (after its first 30 frames, so the
    fade-in load does not fill the table), each with its full breakdown
  * the last 52 frame periods

HOW IT GETS OUT
===============
Press the 3x booster (the port's speed toggle, multiplier 1 -> 3 at module
+0x12C99B8). On that edge the probe copies the worst-frames table onto the
live stack (first half) and into the thread's TLS (second half), loads the
aggregates into every register, and calls svcBreak. Atmosphere writes a crash report with the
registers, a 0x100-byte stack dump and a 0x100-byte TLS dump -- all of it
probe data. `frameprobe_read.py <report>` turns that back into a table.

The recompiled call sites get a wrapper that is transparent to its caller:
x9..x12 and x30 are saved and restored, and nothing in it sets the flags.
Native hooks use only registers the ABI already gives the callee.

CAVEAT: OGG opens also happen on the music/ambience worker threads. Their
time lands in whichever frame they overlap; two overlapping opens on different
threads can corrupt one sample. The count is what matters there.
"""
from __future__ import annotations

import os
import struct

import a64
import ff7nx_audio_cave as AC
import ff7nx_cave  # noqa: F401  (ANY_SPAN kept in call sites)
import nxmap
from ff7nx_audio_cave import Asm

A = a64

ENV = 'SEVENTH_NX_FRAME_PROBE'

(LOGIC, ANIM, DRAW, LIMIT, CATCHUP, DRVFLIP, TIMER, TEXLOAD, OGG,
 TCB) = range(10)
EL_SLOT, DEBT_SLOT, DRIFT_SLOT = TIMER, OGG, TCB       # v3 record slots
SECTION_NAMES = ('logic', 'anim', 'draw', 'limit', 'catchup', 'drvflip',
                 'el@lim', 'texload', 'debt', 'drift')

# recompiled call sites: (site, section). Each is a `bl` in stock.
CALL_SITES = (
    (0x946740, LOGIC), (0x94BEC0, LOGIC),
    (0x9470A4, ANIM), (0x94C678, ANIM),
    (0x94B898, DRAW), (0x94BAC8, DRAW),
    (0x94B920, LIMIT), (0x94BBCC, LIMIT),
    (0x94BA54, CATCHUP),
)
# native functions timed entry -> ret: (entry, entry word, rets, section)
TIMED = (
    (0x10D6BC0, 0xD10203FF, (0x10D7048,), TEXLOAD),
    (0x10FE090, 0xD100C3FF, (0x10FE0EC,), OGG),
)
CREATE_TEXTURE = 0x4620
CREATE_TEXTURE_WORD = 0xD10283FF          # sub sp, sp, #0xa0
WRITE_PALETTE = 0x10D9800
WRITE_PALETTE_WORD = 0xF81B0FF9           # str x25, [sp, #-0x50]!
FLIP = 0x10DA880
FLIP_WORD = 0xFC1C0FE8                    # str d8, [sp, #-0x40]!
FLIP_TAIL = 0x10DABC0                     # b 0x10fa890
TROPHY_FN = 0x10FA890
RET = 0xD65F03C0
MULT = 0x12C99B8                          # booster multiplier, 1 <-> 3
FIELD_ID = 0xCFF468                       # guest u16 (ff7nx_daynight G_FIELD_ID, maplist index)

# ------------------------------------------------------------- BSS layout
START, ACC, CNT = 0x000, 0x080, 0x100
LAST_END, FRAMES, PHEAD, LAST_MULT = 0x140, 0x148, 0x14C, 0x150
PIX, CTEX, CPAL, LAST_FIELD = 0x154, 0x158, 0x15C, 0x160
SUMS = 0x180                              # 10 x u64
SUMP = 0x1D0                              # u64
BK = 0x1D8                                # 5 x u32
TOT = 0x1EC                               # tex, pal, ogg, shader, pix (u32)
BLOCK_END = 0x200                         # SUMS..BLOCK_END is regs x2..x17
REC = 0x200                               # 32 bytes
PRING = 0x220                             # 52 x u16
PRING_N = 32
TOP = 0x290                               # 16 x 32
TOP_N = 12
REGIMG = 0x490                            # 31 x u64
GPTR, EL, DAMT = 0x168, 0x170, 0x178      # v3: host ptr of guest 0xCFF000
DFLAG, NLIM, PREV_D = 0x590, 0x594, 0x598
BSS_BYTES = 0x5A0 + 16
ACCUM = 0x12CF200                         # the port's virtual rdtsc value
G_PAGE = 0xCFF000
G_BASELINE, G_DFLAG, G_DAMT = 0x8D8, 0xA98, 0xAA0

UNIT_SHIFT = 4                            # stored times are ticks >> 4
CPS = 19200000
BUCKETS_MS = (16.9, 18.0, 20.0, 25.0, 33.4)
WARMUP = 30
MAGIC = 0x42525046                        # 'FPRB'
VERSION = 3

# conditions
EQ, NE, HS, LO, HI, LS = 0, 1, 2, 3, 8, 9


# ---------------------------------------------------- encoders not in a64
def _pair_checked(fn):
    def wrapped(rt, rt2, rn, imm):
        if imm % 8 or not -512 <= imm <= 504:
            raise ValueError('ldp/stp offset %d out of range' % imm)
        return fn(rt, rt2, rn, imm)
    return wrapped


class _A:
    """a64, with the pair offsets range-checked (a64's are masked)."""
    def __getattr__(self, name):
        return getattr(a64, name)
    ldp64_off = staticmethod(_pair_checked(a64.ldp64_off))
    stp64_off = staticmethod(_pair_checked(a64.stp64_off))


A = _A()

def mrs_cntpct(rt):  return 0xD53BE020 | rt
def mrs_tpidrro(rt): return 0xD53BD060 | rt
def lsr64(rd, rn, sh): return 0xD340FC00 | (sh << 16) | (rn << 5) | rd
def mov_sp(rn): return 0x91000000 | (rn << 5) | 31          # mov sp, xN
def stp_zero_post16(rn): return 0xA8817C00 | (31 << 10) | (rn << 5) | 31
def udf(): return 0x00000000
def fmov_d0_x(rn): return 0x9E670000 | (rn << 5)
def fcvtzs_x_d0(rd): return 0x9E780000 | rd
def cneg_lt(rd): return 0xDA80A400 | (rd << 16) | (rd << 5) | rd
def cset_lt(rd): return 0x1A9FA7E0 | rd
GE = 10
def svc_break(): return 0xD40004C1                       # svc #0x26


def enabled():
    return os.environ.get(ENV, '').strip().lower() in ('1', 'on', 'yes',
                                                       'true')


def _bl_target(word, va):
    if word >> 26 != 0x25:
        return None
    imm = word & 0x3FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return va + imm * 4


def _b_target(word, va):
    if word >> 26 != 0x05:
        return None
    imm = word & 0x3FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return va + imm * 4


# ------------------------------------------------------------- the caves
def build_wrapper(_e, addr, base, callee, sec):
    """bl-site wrapper: time `callee` into ACC[sec], transparent."""
    a = Asm(_e, addr)
    a.emit(A.stp64_pre(9, 10, A.SP, -48))
    a.emit(A.stp64_off(11, 30, A.SP, 16))
    a.emit(mrs_cntpct(9))
    AC.bss_ptr(a, 10, base + START + 8 * sec)
    a.emit(A.str64(9, 10, 0))
    a.emit(A.ldp64_off(9, 10, A.SP, 0))
    a.emit(A.ldr64(11, A.SP, 16))
    a.emit(A.bl(a.pc(), callee))
    a.emit(A.stp64_off(9, 10, A.SP, 0))
    a.emit(A.str64(11, A.SP, 16))
    _emit_accumulate(a, base, sec, count=True)
    a.emit(A.ldp64_off(9, 10, A.SP, 0))
    a.emit(A.ldp64_off(11, 30, A.SP, 16))
    a.emit(A.add_imm64(A.SP, A.SP, 48))
    a.emit(A.ret())
    return a.resolve()


def build_limit_wrapper(_e, addr, base, callee):
    """LIMIT wrapper that also snapshots the limiter's own inputs."""
    a = Asm(_e, addr)
    a.emit(A.stp64_pre(9, 10, A.SP, -64))
    a.emit(A.stp64_off(11, 12, A.SP, 16))
    a.emit(A.stp64_off(13, 30, A.SP, 32))
    a.emit(mrs_cntpct(9))
    AC.bss_ptr(a, 10, base + START + 8 * LIMIT)
    a.emit(A.str64(9, 10, 0))
    AC.bss_ptr(a, 13, base)
    a.emit(A.ldr64(10, 13, GPTR))
    a.cbz64(10, 'skip')
    a.emit(A.ldr64(11, 10, G_BASELINE))
    AC.bss_ptr(a, 12, ACCUM)
    a.emit(A.ldr64(12, 12, 0))
    a.emit(A.sub_reg64(11, 12, 11))
    a.emit(A.str64(11, 13, EL))
    a.emit(A.ldr(11, 10, G_DFLAG))
    a.emit(A.str_(11, 13, DFLAG))
    a.emit(A.ldr64(11, 10, G_DAMT))
    a.emit(A.str64(11, 13, DAMT))
    a.emit(A.ldr(11, 13, NLIM))
    a.emit(A.add_imm(11, 11, 1))
    a.emit(A.str_(11, 13, NLIM))
    a.label('skip')
    a.emit(A.ldp64_off(9, 10, A.SP, 0))
    a.emit(A.ldp64_off(11, 12, A.SP, 16))
    a.emit(A.ldr64(13, A.SP, 32))
    a.emit(A.bl(a.pc(), callee))
    a.emit(A.stp64_off(9, 10, A.SP, 0))
    a.emit(A.stp64_off(11, 12, A.SP, 16))
    _emit_accumulate(a, base, LIMIT, count=True)
    a.emit(A.ldp64_off(9, 10, A.SP, 0))
    a.emit(A.ldp64_off(11, 12, A.SP, 16))
    a.emit(A.ldp64_off(13, 30, A.SP, 32))
    a.emit(A.add_imm64(A.SP, A.SP, 64))
    a.emit(A.ret())
    return a.resolve()


def _emit_accumulate(a, base, sec, count):
    """ACC[sec] += now - START[sec]; CNT[sec]++ -- x9..x11, no flags."""
    a.emit(mrs_cntpct(9))
    AC.bss_ptr(a, 10, base + START + 8 * sec)
    a.emit(A.ldr64(11, 10, 0))
    a.emit(A.sub_reg64(9, 9, 11))
    AC.bss_ptr(a, 10, base + ACC + 8 * sec)
    a.emit(A.ldr64(11, 10, 0))
    a.emit(A.add_reg64(11, 11, 9))
    a.emit(A.str64(11, 10, 0))
    if count:
        AC.bss_ptr(a, 10, base + CNT + 4 * sec)
        a.emit(A.ldr(11, 10, 0))
        a.emit(A.add_imm(11, 11, 1))
        a.emit(A.str_(11, 10, 0))


def build_entry(_e, addr, base, sec, orig, resume):
    """Function entry: START[sec] = now; replay; resume. x16/x17 only."""
    a = Asm(_e, addr)
    a.emit(mrs_cntpct(16))
    AC.bss_ptr(a, 17, base + START + 8 * sec)
    a.emit(A.str64(16, 17, 0))
    a.emit(orig)
    a.emit(A.b(a.pc(), resume))
    return a.resolve()


def build_exit(_e, addr, base, sec):
    """At a `ret`: accumulate, count, return. x9..x11 are the callee's."""
    a = Asm(_e, addr)
    _emit_accumulate(a, base, sec, count=True)
    a.emit(A.ret())
    return a.resolve()


def build_counter(_e, addr, base, off, orig, resume, pixels=False):
    """Function entry: ++u32 at `off` (and PIX += w2*w3 >> 10)."""
    a = Asm(_e, addr)
    if pixels:
        a.emit(A.mul(16, 2, 3))
        AC.bss_ptr(a, 17, base + PIX)
        a.emit(A.ldr(9, 17, 0))
        a.emit(A.add_reg_lsr(9, 9, 16, 10))
        a.emit(A.str_(9, 17, 0))
    AC.bss_ptr(a, 17, base + off)
    a.emit(A.ldr(9, 17, 0))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.str_(9, 17, 0))
    a.emit(orig)
    a.emit(A.b(a.pc(), resume))
    return a.resolve()


def build_flip_tail(_e, addr, base, frame_end):
    """Replaces flip's `b 0x10fa890`: time it, then close the frame."""
    a = Asm(_e, addr)
    a.emit(A.stp64_pre(29, 30, A.SP, -16))
    a.emit(mrs_cntpct(9))
    # DRVFLIP ends here: ACC[5] += now - START[5]
    AC.bss_ptr(a, 10, base + START + 8 * DRVFLIP)
    a.emit(A.ldr64(11, 10, 0))
    a.emit(A.sub_reg64(11, 9, 11))
    AC.bss_ptr(a, 10, base + ACC + 8 * DRVFLIP)
    a.emit(A.ldr64(12, 10, 0))
    a.emit(A.add_reg64(12, 12, 11))
    a.emit(A.str64(12, 10, 0))
    a.emit(A.bl(a.pc(), TROPHY_FN))
    a.emit(A.stp64_pre(0, 1, A.SP, -16))
    a.emit(A.bl(a.pc(), frame_end))
    a.emit(A.ldp64_post(0, 1, A.SP, 16))
    a.emit(A.ldp64_post(29, 30, A.SP, 16))
    a.emit(A.ret())
    return a.resolve()


def _sat16(a, reg):
    """reg = min(reg >> 4, 0xFFFF) on X reg. Uses x12."""
    a.emit(lsr64(reg, reg, UNIT_SHIFT))
    a.emit(A.movz(12, 0xFFFF))
    a.emit(A.cmp_reg64(reg, 12))
    a.emit(A.csel64(reg, 12, reg, HI))


def _min_w(a, reg, cap):
    """w reg = min(w reg, cap) for cap <= 0xFFFF. Uses w12."""
    a.emit(A.movz(12, cap))
    a.emit(A.cmp_reg(reg, 12))
    a.emit(A.csel(reg, 12, reg, HI))


def _zero(a, base, lo, hi, label):
    """Zero [base+lo, base+hi), 16-byte multiples. x9, x10."""
    AC.bss_ptr(a, 9, base + lo)
    AC.bss_ptr(a, 10, base + hi)
    a.label(label)
    a.emit(stp_zero_post16(9))
    a.emit(A.cmp_reg64(9, 10))
    a.bcond(label, LO)


def _emit_v3_slots(a, base):
    """Record slots el / debt / drift (abs, 16-tick units) and w26 = flags:
    bit0 debt flag at the last limiter call, bit1 el < 0, bit2 debt < 0,
    bit3 drift < 0 (virtual clock gained on the counter), bits4-7 limiter
    calls this frame. x19 = now, x20 = base, x24 = record."""
    a.emit(A.ldr(26, 20, DFLAG))
    a.emit(A.cmp_imm(26, 0))
    a.emit(A.csinc(26, 31, 31, EQ))          # w26 = dflag != 0
    # el
    a.emit(A.ldr64(9, 20, EL))
    a.emit(A.cmp_reg64(9, 31))
    a.emit(cset_lt(10))
    a.emit(A.orr_lsl(26, 26, 10, 1))
    a.emit(cneg_lt(9))
    _sat16(a, 9)
    a.emit(A.strh(9, 24, 2 + 2 * EL_SLOT))
    # debt amount (double) -> ticks
    a.emit(A.ldr64(9, 20, DAMT))
    a.emit(fmov_d0_x(9))
    a.emit(fcvtzs_x_d0(9))
    a.emit(A.cmp_reg64(9, 31))
    a.emit(cset_lt(10))
    a.emit(A.orr_lsl(26, 26, 10, 2))
    a.emit(cneg_lt(9))
    _sat16(a, 9)
    a.emit(A.strh(9, 24, 2 + 2 * DEBT_SLOT))
    # drift = (counter - virtual) now minus the same last frame
    AC.bss_ptr(a, 10, ACCUM)
    a.emit(A.ldr64(10, 10, 0))
    a.emit(A.sub_reg64(10, 19, 10))
    a.emit(A.ldr64(9, 20, PREV_D))
    a.emit(A.str64(10, 20, PREV_D))
    a.emit(A.sub_reg64(9, 10, 9))
    a.emit(A.cmp_reg64(9, 31))
    a.emit(cset_lt(10))
    a.emit(A.orr_lsl(26, 26, 10, 3))
    a.emit(cneg_lt(9))
    _sat16(a, 9)
    a.emit(A.strh(9, 24, 2 + 2 * DRIFT_SLOT))
    a.emit(A.ldr(9, 20, NLIM))
    _min_w(a, 9, 15)
    a.emit(A.orr_lsl(26, 26, 9, 4))


def build_frame_end(_e, addr, base):
    a = Asm(_e, addr)
    a.emit(A.stp64_pre(29, 30, A.SP, -96))
    a.emit(A.stp64_off(19, 20, A.SP, 16))
    a.emit(A.stp64_off(21, 22, A.SP, 32))
    a.emit(A.stp64_off(23, 24, A.SP, 48))
    a.emit(A.stp64_off(25, 26, A.SP, 64))
    a.emit(mrs_cntpct(19))
    AC.bss_ptr(a, 20, base)
    # period
    a.emit(A.ldr64(9, 20, LAST_END))
    a.emit(A.sub_reg64(21, 19, 9))
    a.emit(A.str64(19, 20, LAST_END))
    # field id
    AC.mov32(a, 0, FIELD_ID)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrh(22, 0, 0))
    AC.mov32(a, 0, G_PAGE)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.str64(0, 20, GPTR))
    a.emit(A.ldr(9, 20, LAST_FIELD))
    a.emit(A.cmp_reg(9, 22))
    a.bcond('same_field', EQ)
    a.emit(A.str_(22, 20, LAST_FIELD))
    a.emit(A.str_(31, 20, FRAMES))
    _zero(a, base, SUMS, BLOCK_END, 'z_sums')
    _zero(a, base, TOP, TOP + 32 * TOP_N, 'z_top')
    a.label('same_field')
    a.emit(A.ldr(23, 20, FRAMES))
    a.emit(A.add_imm(23, 23, 1))
    a.emit(A.str_(23, 20, FRAMES))
    # ---- the record
    a.emit(A.add_imm64(24, 20, REC))
    a.emit(A.mov_reg64(9, 21))
    _sat16(a, 9)
    a.emit(A.strh(9, 24, 0))
    a.emit(A.mov_reg(25, 9))                 # w25 = period units
    for k in range(10):
        a.emit(A.ldr64(9, 20, ACC + 8 * k))
        a.emit(A.ldr64(10, 20, SUMS + 8 * k))
        a.emit(A.add_reg64(10, 10, 9))
        a.emit(A.str64(10, 20, SUMS + 8 * k))
        _sat16(a, 9)
        a.emit(A.strh(9, 24, 2 + 2 * k))
    # counts
    _emit_v3_slots(a, base)
    for lo_off, hi_off, slot, tot_lo, tot_hi in (
            (CTEX, CPAL, 22, TOT + 0, TOT + 4),):
        a.emit(A.ldr(9, 20, lo_off))
        a.emit(A.ldr(11, 20, tot_lo))
        a.emit(A.add_reg(11, 11, 9))
        a.emit(A.str_(11, 20, tot_lo))
        _min_w(a, 9, 255)
        a.emit(A.ldr(10, 20, hi_off))
        a.emit(A.ldr(11, 20, tot_hi))
        a.emit(A.add_reg(11, 11, 10))
        a.emit(A.str_(11, 20, tot_hi))
        a.emit(A.mov_reg(13, 9))
        a.emit(A.mov_reg(9, 10))
        _min_w(a, 9, 255)
        a.emit(A.orr_lsl(9, 13, 9, 8))
        a.emit(A.strh(9, 24, slot))
    # ogg opens | flags
    a.emit(A.ldr(9, 20, CNT + 4 * OGG))
    a.emit(A.ldr(11, 20, TOT + 8))
    a.emit(A.add_reg(11, 11, 9))
    a.emit(A.str_(11, 20, TOT + 8))
    _min_w(a, 9, 255)
    a.emit(A.orr_lsl(9, 9, 26, 8))
    a.emit(A.strh(9, 24, 24))
    a.emit(A.ldr(9, 20, PIX))
    a.emit(A.ldr(11, 20, TOT + 16))
    a.emit(A.add_reg(11, 11, 9))
    a.emit(A.str_(11, 20, TOT + 16))
    _min_w(a, 9, 0xFFFF)
    a.emit(A.strh(9, 24, 26))
    a.emit(A.strh(23, 24, 28))
    a.emit(A.strh(22, 24, 30))
    # ---- period sum and buckets (loads excluded: saturated period)
    a.emit(A.movz(12, 0xFFFF))
    a.emit(A.cmp_reg(25, 12))
    a.bcond('no_sum', EQ)
    a.emit(A.ldr64(10, 20, SUMP))
    a.emit(A.add_reg64(10, 10, 21))
    a.emit(A.str64(10, 20, SUMP))
    a.label('no_sum')
    for i, ms in enumerate(BUCKETS_MS):
        ticks = int(round(ms * CPS / 1000.0))
        AC.mov32(a, 10, ticks)
        a.emit(A.cmp_reg64(21, 10))
        a.bcond('bk%d' % i, LS)
        a.emit(A.ldr(11, 20, BK + 4 * i))
        a.emit(A.add_imm(11, 11, 1))
        a.emit(A.str_(11, 20, BK + 4 * i))
        a.label('bk%d' % i)
    # ---- period ring
    a.emit(A.ldr(9, 20, PHEAD))
    a.emit(A.add_imm64(10, 20, PRING))
    a.emit(A.add_reg64_lsl(10, 10, 9, 1))
    a.emit(A.strh(25, 10, 0))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.cmp_imm(9, PRING_N))
    a.emit(A.csel(9, 9, 31, LO))
    a.emit(A.str_(9, 20, PHEAD))
    # ---- top 16 (after warm-up, not loads)
    a.emit(A.cmp_imm(23, WARMUP))
    a.bcond('no_top', LS)
    a.emit(A.movz(12, 0xFFFF))
    a.emit(A.cmp_reg(25, 12))
    a.bcond('no_top', EQ)
    a.emit(A.add_imm64(9, 20, TOP))          # cursor
    a.emit(A.mov_reg64(26, 9))                # min slot
    a.emit(A.ldrh(13, 9, 0))                  # min value
    a.emit(A.add_imm64(10, 20, TOP + 32 * TOP_N))
    a.label('scan')
    a.emit(A.ldrh(11, 9, 0))
    a.emit(A.cmp_reg(11, 13))
    a.emit(A.csel(13, 11, 13, LO))
    a.emit(A.csel64(26, 9, 26, LO))
    a.emit(A.add_imm64(9, 9, 32))
    a.emit(A.cmp_reg64(9, 10))
    a.bcond('scan', LO)
    a.emit(A.cmp_reg(25, 13))
    a.bcond('no_top', LS)
    a.emit(A.ldp64_off(9, 10, 24, 0))
    a.emit(A.stp64_off(9, 10, 26, 0))
    a.emit(A.ldp64_off(9, 10, 24, 16))
    a.emit(A.stp64_off(9, 10, 26, 16))
    a.label('no_top')
    # ---- clear this frame's accumulators
    _zero(a, base, ACC, CNT + 0x40, 'z_acc')
    a.emit(A.str64(31, 20, EL))
    a.emit(A.str64(31, 20, DAMT))
    a.emit(A.str_(31, 20, DFLAG))
    a.emit(A.str_(31, 20, NLIM))
    a.emit(A.str_(31, 20, PIX))
    a.emit(A.str_(31, 20, CTEX))
    a.emit(A.str_(31, 20, CPAL))
    # ---- trigger: booster 1 -> 3
    AC.bss_ptr(a, 9, MULT)
    a.emit(A.ldr(9, 9, 0))
    a.emit(A.ldr(10, 20, LAST_MULT))
    a.emit(A.str_(9, 20, LAST_MULT))
    a.emit(A.cmp_imm(10, 1))
    a.bcond('done', NE)
    a.emit(A.cmp_imm(9, 3))
    a.bcond('done', NE)
    a.b('dump')
    a.label('done')
    a.emit(A.ldp64_off(19, 20, A.SP, 16))
    a.emit(A.ldp64_off(21, 22, A.SP, 32))
    a.emit(A.ldp64_off(23, 24, A.SP, 48))
    a.emit(A.ldp64_off(25, 26, A.SP, 64))
    a.emit(A.ldp64_post(29, 30, A.SP, 96))
    a.emit(A.ret())
    # ---- dump: build the register image, then crash on purpose
    a.label('dump')
    a.emit(A.add_imm64(24, 20, REGIMG))
    AC.mov32(a, 9, MAGIC)
    AC.mov32(a, 10, VERSION)
    a.emit(A.bfi64(9, 10, 32, 32))
    a.emit(A.str64(9, 24, 0))
    a.emit(A.ldr(9, 20, FRAMES))
    a.emit(A.bfi64(9, 22, 32, 16))
    a.emit(A.ldr(10, 20, PHEAD))
    a.emit(A.bfi64(9, 10, 48, 16))
    a.emit(A.str64(9, 24, 8))
    # regs x2..x7, FP, LR: the last 32 periods (the kernel zeroes x8..x28)
    for i in range(0, 48, 8):
        a.emit(A.ldr64(9, 20, PRING + i))
        a.emit(A.str64(9, 24, 16 + i))
    a.emit(A.ldr64(9, 20, PRING + 48))
    a.emit(A.str64(9, 24, 8 * 29))
    a.emit(A.ldr64(9, 20, PRING + 56))
    a.emit(A.str64(9, 24, 8 * 30))
    # worst frames 0..7 -> the live stack ("Stack Dump"); the per-visit sums
    # block and worst frames 8..11 -> the thread's TLS ("TLS Dump"). SP stays
    # on the real stack: creport only dumps a stack in the stack region.
    a.emit(mrs_tpidrro(10))
    a.emit(A.add_imm64(13, 20, TOP))
    a.emit(A.add_imm64(14, 20, SUMS))
    for i in range(0, 256, 16):
        a.emit(A.ldp64_off(11, 12, 13, i))
        a.emit(A.stp64_off(11, 12, A.SP, i))
    for i in range(0, BLOCK_END - SUMS, 16):
        a.emit(A.ldp64_off(11, 12, 14, i))
        a.emit(A.stp64_off(11, 12, 10, i))
    for i in range(0, 128, 16):
        a.emit(A.ldp64_off(11, 12, 13, 256 + i))
        a.emit(A.stp64_off(11, 12, 10, 128 + i))
    a.emit(A.mov_reg64(30, 24))
    for r in range(0, 30, 2):
        a.emit(A.ldp64_off(r, r + 1, 30, 8 * r))
    a.emit(A.ldr64(30, 30, 240))
    # svcBreak, not a CPU exception: nnSdk's user exception handler would
    # catch a fault and abort from its own context (FINDINGS-302 -- every
    # register zero). A Break is reported with THIS thread's registers.
    # w0 = 'FPRB' has bit 31 clear, so it is not a notification-only break.
    a.emit(svc_break())
    a.emit(udf())
    return a.resolve()


# ------------------------------------------------------------- applying
def _verify(text):
    """(call sites usable, problems) -- refuses a word it does not expect."""
    bad, sites = [], []
    for site, sec in CALL_SITES:
        w = struct.unpack_from('<I', text, site)[0]
        tgt = _bl_target(w, site)
        if tgt is None:
            bad.append('call site +0x%X holds %08X, not a bl' % (site, w))
        else:
            sites.append((site, sec, tgt))
    for entry, word, rets, _sec in TIMED:
        if struct.unpack_from('<I', text, entry)[0] != word:
            bad.append('+0x%X entry is not %08X' % (entry, word))
        for r in rets:
            if struct.unpack_from('<I', text, r)[0] != RET:
                bad.append('+0x%X is not ret' % r)
    for va, word in ((CREATE_TEXTURE, CREATE_TEXTURE_WORD),
                     (WRITE_PALETTE, WRITE_PALETTE_WORD), (FLIP, FLIP_WORD)):
        if struct.unpack_from('<I', text, va)[0] != word:
            bad.append('+0x%X is not %08X' % (va, word))
    if _b_target(struct.unpack_from('<I', text, FLIP_TAIL)[0],
                 FLIP_TAIL) != TROPHY_FN:
        bad.append('+0x%X is not b 0x%X' % (FLIP_TAIL, TROPHY_FN))
    return sites, bad


# ------------------------------------------------------------- code space
# The padding pool is spent by the time the late passes run (measured on a
# finished build: ~195 usable words, almost all single-word holes). The probe
# needs ~1000. It borrows the translated body of a function PROVEN dead
# instead: x86 0x623D28, the 25 KB PSX tile rasteriser that
# ff7nx_fieldbg already proves unreachable (reached only from 0x620BD3, which
# nothing reaches). Its ARM body is 72 KB, and a probe build is diagnostic --
# the shipping build never carries this.
DEAD_TRAMPOLINE = 0x620BD3
DEAD_BODY = 0x623D28


def _dead_region(src, stock=None):
    import ff7nx_fieldbg
    m = nxmap.Main(src)
    spans = []
    for va in (DEAD_TRAMPOLINE, DEAD_BODY):
        why = ff7nx_fieldbg._liveness(m, va, spans)
        if why:
            raise ValueError('x86 0x%X is not dead: %s' % (va, why))
        spans.append(m.extent(va))
    lo, hi = m.extent(DEAD_BODY)
    if stock is not None:
        st = nxmap.Main(stock)
        if st.text[lo:hi] != m.text[lo:hi]:
            raise ValueError('the dead body at +0x%X was already modified '
                             'by another pass' % lo)
    return lo, hi


class Bump:
    """Contiguous allocator in the dead body, 16-byte aligned caves."""

    def __init__(self, lo, hi):
        self.at, self.hi = (lo + 15) & ~15, hi
        self.placed = {}

    def put(self, build):
        n = len(build(0, lambda i: 4 * i))
        entry = self.at
        words = build(entry, lambda i: entry + 4 * i)
        if len(words) != n or entry + 4 * n > self.hi:
            raise ValueError('probe cave does not fit')
        for i, w in enumerate(words):
            self.placed[entry + 4 * i] = w
        self.at = (entry + 4 * n + 15) & ~15
        return entry


def apply_to_nso(src, dest, stock=None):
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    sites, bad = _verify(text)
    if bad:
        raise ValueError('; '.join(bad))
    import ff7nx_deadspace
    lo, hi = ff7nx_deadspace.part(src, 'probe', stock)   # BUILD 533: shared
    scratch = AC.scratch_base(blob, segs)
    base = (scratch + 15) & ~15
    pool = Bump(lo, hi)
    placed = {}

    def put(build, span=None):
        return pool.put(build)

    frame_end = put(lambda e, ad: build_frame_end(e, ad, base))
    tail = put(lambda e, ad: build_flip_tail(e, ad, base, frame_end),
               ff7nx_cave.ANY_SPAN)
    placed[FLIP_TAIL] = A.b(FLIP_TAIL, tail)
    fe = put(lambda e, ad: build_entry(e, ad, base, DRVFLIP, FLIP_WORD,
                                       FLIP + 4), ff7nx_cave.ANY_SPAN)
    placed[FLIP] = A.b(FLIP, fe)
    wrappers = {}
    for site, sec, tgt in sites:
        key = (tgt, sec)
        if key not in wrappers:
            if sec == LIMIT:
                wrappers[key] = put(lambda e, ad, t=tgt:
                                    build_limit_wrapper(e, ad, base, t))
            else:
                wrappers[key] = put(lambda e, ad, t=tgt, s=sec:
                                    build_wrapper(e, ad, base, t, s),
                                    ff7nx_cave.ANY_SPAN)
        placed[site] = A.bl(site, wrappers[key])
    for entry, word, rets, sec in TIMED:
        ent = put(lambda e, ad, s=sec, w=word, r=entry + 4:
                  build_entry(e, ad, base, s, w, r), ff7nx_cave.ANY_SPAN)
        placed[entry] = A.b(entry, ent)
        ex = put(lambda e, ad, s=sec: build_exit(e, ad, base, s),
                 ff7nx_cave.ANY_SPAN)
        for r in rets:
            placed[r] = A.b(r, ex)
    ct = put(lambda e, ad: build_counter(e, ad, base, CTEX,
                                         CREATE_TEXTURE_WORD,
                                         CREATE_TEXTURE + 4, pixels=True),
             ff7nx_cave.ANY_SPAN)
    placed[CREATE_TEXTURE] = A.b(CREATE_TEXTURE, ct)
    wp = put(lambda e, ad: build_counter(e, ad, base, CPAL,
                                         WRITE_PALETTE_WORD,
                                         WRITE_PALETTE + 4),
             ff7nx_cave.ANY_SPAN)
    placed[WRITE_PALETTE] = A.b(WRITE_PALETTE, wp)
    placed.update(pool.placed)
    # the rest of the dead body becomes a trap, so a stray jump into it
    # stops loudly instead of running half an old rasteriser
    for va in range(pool.at, hi, 4):
        placed[va] = udf()
    for va, w in placed.items():
        struct.pack_into('<I', text, va, w)
    out = AC.pack(blob, [bytes(text), raw[1], raw[2]],
                  base - scratch + BSS_BYTES)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'base': base, 'frame_end': frame_end, 'sites': len(sites),
            'words': (pool.at - lo) // 4, 'region': lo,
            'bss': base - scratch + BSS_BYTES}
