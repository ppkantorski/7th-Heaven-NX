#!/usr/bin/env python3
"""
ff7nx_drawprobe.py -- battle draw-order probe. DIAGNOSTIC ONLY, off unless
SEVENTH_NX_FRAME_PROBE=draw.

WHY
===
With the 686 UI cave the Neo Bahamut white still covers the battle UI on
single frames, and the 688 depth seal after the menu made it worse. So
something is drawn after the battle menu, or the menu is not drawn on those
frames. Reading the code did not find either. This catches it in the running
game.

WHAT IT DOES
============
The battle menu call (the `bl` at +0x90C38, inside the battle frame 41BAB3,
whatever it now calls -- the uitop cave or the menu itself) is wrapped, so
every frame is split into three phases:

    0  before the battle menu
    1  inside it
    2  after it, up to gfx_drv_flip

The native driver entry points below are hooked. Each draw-type call counts
into its phase. A call made in phase 2 is recorded: which driver function,
the native return address (module offset), the guest return address on top
of the guest stack (the x86 caller, when the call came from game code), and
the render pass current at that moment (the port's set_pass value).

    id  +addr      function
    1   10D8310    field_78 (polygon set draw)
    2   10DA6D0    draw_deferred
    3   10DB1B0    flat/smooth 2D
    4   10DB3D0    textured 2D
    5   10DB4D0    paletted 2D
    6   10DB870    flat/smooth 3D
    7   10DBA60    lines
    8   10D68D0    clear              (logged, not counted as a draw)
    9   10DF010    blit
    10  10E0E90    texture blit
    11  10DF6E0    set_pass           (logged with the NEW pass, not a draw)
    12  10D90B0    begin_scene        (logged, not a draw)
    13  10D9270    end_scene          (logged, not a draw)

gfx_drv_flip (+0x10DA880) closes the frame: it pushes a 16-bit summary of the
frame (draws per phase, whether the menu ran) into a 64-frame ring and resets
the phase.

WHAT GETS OUT
=============
Press the 3x booster (multiplier 1 -> 3 at +0x12C99B8). On that edge, at the
next flip, the probe crashes on purpose with svcBreak. The crash report holds:

    x0       'DRPB' | version << 32
    x1       frames seen | summary ring head << 32 | event ring head << 48
    x2       phase-2 records (all) | frames that had any phase-2 draw << 32
    x3       unique-table overflow | frames the menu ran << 32
    x4..x7, FP, LR   the last six phase-2 events, newest last in ring order
    Stack Dump  unique phase-2 callers 0..15 (16 bytes each)
    TLS Dump    unique callers 16..23, then the 64 frame summaries

`drawprobe_read.py <crash report>` decodes it.

Hooks use only x9..x17, which the ABI gives a callee at entry: each entry
gets a six-word stub that calls one shared recorder. The menu
wrapper saves and restores x9/x10 and sets no flags, like the frame probe's.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import a64
import ff7nx_audio_cave as AC
import ff7nx_frameprobe as FP
from ff7nx_audio_cave import Asm

A = FP.A

VERSION = 2
MAGIC = 0x42505244                       # 'DRPB'
MODE = 'draw'

MENU_SITE = 0x90C38                      # bl in the battle frame 41BAB3
FLIP = FP.FLIP
FLIP_WORD = FP.FLIP_WORD
MULT = FP.MULT
GOT_PAGE = 0x12CE000
GOT_CTX = 0x2B0                          # -> guest register file (+0x10 esp)
GOT_PASS = 0x3D0                         # -> current set_pass value
GOT_PAGES = 0x8C0                        # -> guest page table (translate)
GOT_PENDING = 0x420                      # -> u8, blit pending
GOT_DEPTH = 0x640                        # -> current depth target
GOT_COLOUR = 0x630                       # -> current colour target

# (id, entry, first word, counts as a draw)
HOOKS = (
    (1, 0x10D8310, 0xD10483FF, True),    # sub sp, sp, #0x120
    (2, 0x10DA6D0, 0xD10143FF, True),    # sub sp, sp, #0x50
    (3, 0x10DB1B0, 0xD10143FF, True),
    (4, 0x10DB3D0, 0xD10143FF, True),
    (5, 0x10DB4D0, 0xD10243FF, True),    # sub sp, sp, #0x90
    (6, 0x10DB870, 0xD10103FF, True),    # sub sp, sp, #0x40
    (7, 0x10DBA60, 0xD10103FF, True),
    (8, 0x10D68D0, 0x6DBB2BEB, False),   # stp d11, d10, [sp, #-0x50]!
    (9, 0x10DF010, 0xF81D0FF5, True),    # str x21, [sp, #-0x30]!
    (10, 0x10E0E90, 0xD104C3FF, True),   # sub sp, sp, #0x130
    (11, 0x10DF6E0, 0x7100301F, False),  # cmp w0, #0xc
    (12, 0x10D90B0, 0xFC1C0FE8, False),  # str d8, [sp, #-0x40]!
    (13, 0x10D9270, 0xF81E0FF3, False),  # str x19, [sp, #-0x20]!
)
NAMES = {1: 'field_78', 2: 'draw_deferred', 3: 'draw2d_flat', 4: 'draw2d_tex',
         5: 'draw2d_pal', 6: 'draw3d_flat', 7: 'lines', 8: 'clear',
         9: 'blit', 10: 'tex_blit', 11: 'set_pass', 12: 'begin_scene',
         13: 'end_scene'}
SET_PASS_ID = 11

# BSS layout (v2)
PHASE, FRAME, LAST_MULT, NSIG, SIG, MENU = 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14
CNT = 0x18                               # 3 x u32, per phase
TOTAL2, FW2, OVER, MENUFR, NEV, R0HEAD = 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38
CUR = 0x40                               # 8 x u64: this frame's menu events
CUR_N = 8
RING0 = 0x80                             # 8 x u64: last events before it
META = 0x100                             # 32 x 16 bytes, one per pattern
NPAT = 32
DETS = 0x300                             # 32 x 128 bytes: ring0 + CUR
DET_BYTES = 128
REGIMG = 0x1300                          # 31 x u64
BSS_BYTES = 0x1400
DUMP_META = 16                           # newest patterns in the stack dump


def enabled():
    return os.environ.get(FP.ENV, '').strip().lower() == MODE


def _add_lsl12(rd, rn, imm):
    """ADD Xd, Xn, #imm, LSL #12."""
    return 0x91400000 | (imm << 10) | (rn << 5) | rd


def _sat(a, reg, cap, tmp):
    """reg = min(reg, cap), unsigned W. No other register but tmp."""
    a.emit(A.movz(tmp, cap))
    a.emit(A.cmp_reg(reg, tmp))
    a.emit(A.csel(reg, reg, tmp, FP.LO))


def build_stub(_e, addr, common, hid, draw, orig, resume):
    """Entry stub: w16 = id (| 0x100 for a draw), x17 = caller's LR."""
    a = Asm(_e, addr)
    a.emit(A.movz(16, hid | (0x100 if draw else 0)))
    a.emit(A.mov_reg64(17, 30))
    a.emit(A.bl(a.pc(), common))
    a.emit(A.mov_reg64(30, 17))
    a.emit(orig)
    a.emit(A.b(a.pc(), resume))
    return a.resolve()


def build_common(_e, addr, base):
    """Shared recorder. In: w16 id (+0x100 draw), x17 the hooked function's
    return address, w0 the set_pass argument. Uses x9..x16 only; x17 and
    x30 are kept.

    Every call is counted into its phase. Calls before the menu (except the
    per-draw blit check, id 9) go into an 8-deep ring; calls inside the menu
    go into this frame's event list and its signature. An event is

        bits 0..22   native return address >> 2
             23..26  id
             27..30  pass
             31      blit pending ([0x420])
             32..47  current depth target ([0x640]), low 16 bits
             48..63  current colour target ([0x630]), low 16 bits
    """
    a = Asm(_e, addr)
    AC.bss_ptr(a, 9, base)
    a.emit(A.ldr(10, 9, PHASE))
    a.emit(A.lsr(12, 16, 8))
    a.cbz(12, 'nocount')
    a.emit(A.add_reg64_lsl(11, 9, 10, 2))
    a.emit(A.ldr(12, 11, CNT))
    a.emit(A.add_imm(12, 12, 1))
    a.emit(A.str_(12, 11, CNT))
    a.label('nocount')
    a.emit(A.and_mask(16, 16, 8))
    a.emit(A.cmp_imm(10, 2))
    a.bcond('out', FP.EQ)
    a.emit(A.cmp_imm(16, 9))
    a.bcond('out', FP.EQ)
    # w12 = lr >> 2 | id << 23 | pass << 27 | pending << 31
    here = a.pc()
    a.emit(A.adr(11, here, here))
    a.emit(A.sub_reg64(11, 17, 11))
    AC.mov32(a, 12, here)
    a.emit(A.add_reg64(11, 11, 12))
    a.emit(A.lsr(12, 11, 2))
    a.emit(A.and_mask(12, 12, 23))
    a.emit(A.bfi(12, 16, 23, 4))
    a.emit(A.adrp(13, a.pc(), GOT_PAGE))
    a.emit(A.ldr64(13, 13, GOT_PASS))
    a.emit(A.ldr(13, 13, 0))
    a.emit(A.cmp_imm(16, SET_PASS_ID))
    a.emit(A.csel(13, 0, 13, FP.EQ))
    a.emit(A.bfi(12, 13, 27, 4))
    a.emit(A.adrp(13, a.pc(), GOT_PAGE))
    a.emit(A.ldr64(13, 13, GOT_PENDING))
    a.emit(A.ldrb(13, 13, 0))
    a.emit(A.bfi(12, 13, 31, 1))
    # w14 = depth target low 16 | colour target low 16 << 16
    a.emit(A.adrp(13, a.pc(), GOT_PAGE))
    a.emit(A.ldr64(13, 13, GOT_DEPTH))
    a.emit(A.ldr64(13, 13, 0))
    a.emit(A.and_mask(14, 13, 16))
    a.emit(A.adrp(13, a.pc(), GOT_PAGE))
    a.emit(A.ldr64(13, 13, GOT_COLOUR))
    a.emit(A.ldr64(13, 13, 0))
    a.emit(A.bfi(14, 13, 16, 16))
    a.emit(A.mov_reg(13, 12))
    a.emit(A.bfi64(13, 14, 32, 32))      # x13 = the event
    a.emit(A.cmp_imm(10, 1))
    a.bcond('menu', FP.EQ)
    # before the menu: ring
    a.emit(A.ldr(15, 9, R0HEAD))
    a.emit(A.add_reg64_lsl(11, 9, 15, 3))
    a.emit(A.str64(13, 11, RING0))
    a.emit(A.add_imm(15, 15, 1))
    a.emit(A.and_mask(15, 15, 3))
    a.emit(A.str_(15, 9, R0HEAD))
    a.b('out')
    # inside the menu: signature and event list
    a.label('menu')
    a.emit(A.ldr(15, 9, SIG))
    a.emit(A.add_reg_lsl(15, 15, 15, 5))
    a.emit(A.eor_reg(15, 15, 12))
    a.emit(A.add_reg_lsl(15, 15, 15, 5))
    a.emit(A.eor_reg(15, 15, 14))
    a.emit(A.str_(15, 9, SIG))
    a.emit(A.ldr(15, 9, NEV))
    a.emit(A.cmp_imm(15, CUR_N))
    a.bcond('full', FP.HS)
    a.emit(A.add_reg64_lsl(11, 9, 15, 3))
    a.emit(A.str64(13, 11, CUR))
    a.label('full')
    a.emit(A.add_imm(15, 15, 1))
    a.emit(A.str_(15, 9, NEV))
    a.label('out')
    a.emit(A.ret())
    return a.resolve()


def build_menu_wrapper(_e, addr, base, callee):
    """Replaces `bl callee` at the menu site: phase 1 around it, then 2."""
    a = Asm(_e, addr)
    a.emit(A.stp64_pre(9, 10, A.SP, -32))
    a.emit(A.stp64_off(29, 30, A.SP, 16))
    a.emit(A.add_imm64(29, A.SP, 16))
    AC.bss_ptr(a, 9, base)
    a.emit(A.movz(10, 1))
    a.emit(A.str_(10, 9, PHASE))
    a.emit(A.ldr(10, 9, MENU))
    a.emit(A.add_imm(10, 10, 1))
    a.emit(A.str_(10, 9, MENU))
    a.emit(A.ldp64_off(9, 10, A.SP, 0))
    a.emit(A.bl(a.pc(), callee))
    a.emit(A.stp64_off(9, 10, A.SP, 0))
    AC.bss_ptr(a, 9, base)
    a.emit(A.movz(10, 2))
    a.emit(A.str_(10, 9, PHASE))
    a.emit(A.ldp64_off(9, 10, A.SP, 0))
    a.emit(A.ldp64_off(29, 30, A.SP, 16))
    a.emit(A.add_imm64(A.SP, A.SP, 32))
    a.emit(A.ret())
    return a.resolve()


def _sat_store_b(a, src_off, cap, dst, dst_off):
    """strb min([x9 + src_off], cap) -> [dst + dst_off]. Uses w15, w16."""
    a.emit(A.ldr(15, 9, src_off))
    _sat(a, 15, cap, 16)
    a.emit(A.strb(15, dst, dst_off))


def build_flip(_e, addr, base):
    """gfx_drv_flip entry: close the frame, maybe dump. x9..x17 only.

    A frame that ran the battle menu is filed under its menu signature: a
    table of up to 32 patterns, each with a count, first and last frame,
    and the events of its LAST occurrence (the 8 calls before the menu,
    oldest first, then up to 8 calls inside it).
    """
    a = Asm(_e, addr)
    AC.bss_ptr(a, 9, base)
    # frames with any draw after the menu
    a.emit(A.ldr(11, 9, CNT + 8))
    a.emit(A.ldr(12, 9, FW2))
    a.emit(A.cmp_imm(11, 0))
    a.emit(A.csinc(12, 12, 12, FP.EQ))
    a.emit(A.str_(12, 9, FW2))
    a.emit(A.ldr(10, 9, MENU))
    a.cbz(10, 'nomenu')
    a.emit(A.ldr(12, 9, MENUFR))
    a.emit(A.add_imm(12, 12, 1))
    a.emit(A.str_(12, 9, MENUFR))
    # find this frame's signature
    a.emit(A.ldr(11, 9, SIG))
    a.emit(A.ldr(13, 9, NSIG))
    a.emit(A.add_imm64(12, 9, META))
    a.emit(A.movz(14, 0))
    a.label('find')
    a.emit(A.cmp_reg(14, 13))
    a.bcond('notfound', FP.HS)
    a.emit(A.ldr(15, 12, 0))
    a.emit(A.cmp_reg(15, 11))
    a.bcond('found', FP.EQ)
    a.emit(A.add_imm64(12, 12, 16))
    a.emit(A.add_imm(14, 14, 1))
    a.b('find')
    a.label('notfound')
    a.emit(A.cmp_imm(13, NPAT))
    a.bcond('overflow', FP.HS)
    a.emit(A.str_(11, 12, 0))
    a.emit(A.str_(31, 12, 4))
    a.emit(A.ldr(15, 9, FRAME))
    a.emit(A.strh(15, 12, 8))
    a.emit(A.add_imm(13, 13, 1))
    a.emit(A.str_(13, 9, NSIG))
    a.label('found')
    a.emit(A.ldr(15, 12, 4))
    a.emit(A.add_imm(15, 15, 1))
    a.emit(A.str_(15, 12, 4))
    a.emit(A.ldr(15, 9, FRAME))
    a.emit(A.strh(15, 12, 10))
    _sat_store_b(a, NEV, 255, 12, 12)
    _sat_store_b(a, CNT, 255, 12, 13)
    _sat_store_b(a, CNT + 4, 255, 12, 14)
    _sat_store_b(a, CNT + 8, 255, 12, 15)
    # x15 = this pattern's detail
    a.emit(A.add_imm64(15, 9, DETS & 0xFFF))
    a.emit(A.add_reg64_lsl(15, 15, 14, 7))
    if DETS >> 12:
        raise ValueError('DETS must stay below 0x1000')
    # the ring before the menu, oldest first
    a.emit(A.ldr(12, 9, R0HEAD))
    for k in range(8):
        a.emit(A.add_imm(13, 12, k))
        a.emit(A.and_mask(13, 13, 3))
        a.emit(A.add_reg64_lsl(13, 9, 13, 3))
        a.emit(A.ldr64(13, 13, RING0))
        a.emit(A.str64(13, 15, 8 * k))
    # the events inside the menu (unused slots zero)
    a.emit(A.add_imm64(13, 9, CUR))
    for k in range(0, 64, 16):
        a.emit(A.ldp64_off(10, 11, 13, k))
        a.emit(A.stp64_off(10, 11, 15, 64 + k))
    a.b('filed')
    a.label('overflow')
    a.emit(A.ldr(15, 9, OVER))
    a.emit(A.add_imm(15, 15, 1))
    a.emit(A.str_(15, 9, OVER))
    a.label('filed')
    a.label('nomenu')
    # reset the frame
    for off in (PHASE, MENU, CNT, CNT + 4, CNT + 8, NEV, SIG):
        a.emit(A.str_(31, 9, off))
    a.emit(A.add_imm64(13, 9, CUR))
    for k in range(0, 64, 16):
        a.emit(A.stp64_off(31, 31, 13, k))
    a.emit(A.ldr(12, 9, FRAME))
    a.emit(A.add_imm(12, 12, 1))
    a.emit(A.str_(12, 9, FRAME))
    # trigger: booster 1 -> 3
    AC.bss_ptr(a, 10, MULT)
    a.emit(A.ldr(10, 10, 0))
    a.emit(A.ldr(11, 9, LAST_MULT))
    a.emit(A.str_(10, 9, LAST_MULT))
    a.emit(A.cmp_imm(11, 1))
    a.bcond('done', FP.NE)
    a.emit(A.cmp_imm(10, 3))
    a.bcond('dump', FP.EQ)
    a.label('done')
    a.emit(FLIP_WORD)
    a.emit(A.b(a.pc(), FLIP + 4))
    # ---- dump (never returns)
    a.label('dump')
    # w10 = most common pattern, w11 = rarest (ties: the newer)
    a.emit(A.ldr(13, 9, NSIG))
    a.emit(A.movz(10, 0))
    a.emit(A.movz(11, 0))
    a.emit(A.movz(14, 0))                # index
    a.emit(A.movz(16, 0))                # max count
    AC.mov32(a, 17, 0xFFFFFFFF)          # min count
    a.emit(A.add_imm64(12, 9, META))
    a.label('scan')
    a.emit(A.cmp_reg(14, 13))
    a.bcond('scanned', FP.HS)
    a.emit(A.ldr(15, 12, 4))
    a.emit(A.cmp_reg(15, 16))
    a.emit(A.csel(16, 15, 16, FP.HI))
    a.emit(A.csel(10, 14, 10, FP.HI))
    a.emit(A.cmp_reg(15, 17))
    a.emit(A.csel(17, 15, 17, FP.LS))
    a.emit(A.csel(11, 14, 11, FP.LS))
    a.emit(A.add_imm64(12, 12, 16))
    a.emit(A.add_imm(14, 14, 1))
    a.b('scan')
    a.label('scanned')
    # register image
    a.emit(A.add_imm64(12, 9, REGIMG & 0xFFF))
    if REGIMG >> 12 == 1:
        a.emit(_add_lsl12(12, 12, 1))
    if REGIMG >> 12 not in (0, 1):
        raise ValueError('REGIMG out of reach')
    AC.mov32(a, 14, MAGIC)
    AC.mov32(a, 15, VERSION)
    a.emit(A.bfi64(14, 15, 32, 32))
    a.emit(A.str64(14, 12, 0))
    a.emit(A.ldr(14, 9, FRAME))
    a.emit(A.ldr(15, 9, MENUFR))
    a.emit(A.bfi64(14, 15, 32, 32))
    a.emit(A.str64(14, 12, 8))
    a.emit(A.ldr(14, 9, FW2))
    a.emit(A.ldr(15, 9, OVER))
    a.emit(A.bfi64(14, 15, 32, 32))
    a.emit(A.str64(14, 12, 16))
    a.emit(A.mov_reg(14, 13))            # NSIG | max << 16 | min << 24
    a.emit(A.bfi64(14, 10, 16, 8))
    a.emit(A.bfi64(14, 11, 24, 8))
    a.emit(A.str64(14, 12, 24))
    # x4..x7, FP, LR: the menu events of the newest pattern
    a.emit(A.sub_imm(14, 13, 1))
    a.emit(A.cmp_imm(13, 0))
    a.emit(A.csel(14, 13, 14, FP.EQ))
    a.emit(A.add_imm64(15, 9, DETS))
    a.emit(A.add_reg64_lsl(15, 15, 14, 7))
    for k, r in enumerate((4, 5, 6, 7, 29, 30)):
        a.emit(A.ldr64(16, 15, 64 + 8 * k))
        a.emit(A.str64(16, 12, 8 * r))
    # stack: the newest 16 pattern entries
    a.emit(A.sub_imm(14, 13, DUMP_META))
    a.emit(A.cmp_imm(13, DUMP_META))
    a.emit(A.csel(14, 14, 31, FP.HS))    # start = max(0, NSIG - 16)
    a.emit(A.add_imm64(15, 9, META))
    a.emit(A.add_reg64_lsl(15, 15, 14, 4))
    for i in range(0, 256, 16):
        a.emit(A.ldp64_off(13, 14, 15, i))
        a.emit(A.stp64_off(13, 14, A.SP, i))
    # TLS: details of the most common and of the rarest pattern
    a.emit(FP.mrs_tpidrro(17))
    for half, reg in ((0, 10), (128, 11)):
        a.emit(A.add_imm64(15, 9, DETS))
        a.emit(A.add_reg64_lsl(15, 15, reg, 7))
        for i in range(0, 128, 16):
            a.emit(A.ldp64_off(13, 14, 15, i))
            a.emit(A.stp64_off(13, 14, 17, half + i))
    a.emit(A.mov_reg64(30, 12))
    for r in range(0, 30, 2):
        a.emit(A.ldp64_off(r, r + 1, 30, 8 * r))
    a.emit(A.ldr64(30, 30, 240))
    a.emit(FP.svc_break())
    a.emit(FP.udf())
    return a.resolve()


def _verify(text):
    bad = []
    w = struct.unpack_from('<I', text, MENU_SITE)[0]
    menu = FP._bl_target(w, MENU_SITE)
    if menu is None:
        bad.append('menu site +0x%X holds %08X, not a bl' % (MENU_SITE, w))
    for hid, entry, word, _d in HOOKS:
        got = struct.unpack_from('<I', text, entry)[0]
        if got != word:
            bad.append('%s +0x%X is %08X, not %08X' % (NAMES[hid], entry,
                                                       got, word))
    if struct.unpack_from('<I', text, FLIP)[0] != FLIP_WORD:
        bad.append('flip +0x%X is not %08X' % (FLIP, FLIP_WORD))
    return menu, bad


def apply_to_nso(src, dest, stock=None):
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    menu, bad = _verify(text)
    if bad:
        raise ValueError('; '.join(bad))
    import ff7nx_deadspace
    lo, hi = ff7nx_deadspace.part(src, 'probe', stock)
    scratch = AC.scratch_base(blob, segs)
    base = (scratch + 15) & ~15
    pool = FP.Bump(lo, hi)
    placed = {}
    fl = pool.put(lambda e, ad: build_flip(e, ad, base))
    placed[FLIP] = A.b(FLIP, fl)
    mw = pool.put(lambda e, ad: build_menu_wrapper(e, ad, base, menu))
    placed[MENU_SITE] = A.bl(MENU_SITE, mw)
    common = pool.put(lambda e, ad: build_common(e, ad, base))
    hooks = {}
    for hid, entry, word, draw in HOOKS:
        h = pool.put(lambda e, ad, i=hid, d=draw, w=word, r=entry + 4:
                     build_stub(e, ad, common, i, d, w, r))
        placed[entry] = A.b(entry, h)
        hooks[hid] = h
    placed.update(pool.placed)
    for va in range(pool.at, hi, 4):
        placed[va] = FP.udf()
    for va, w in placed.items():
        struct.pack_into('<I', text, va, w)
    growth = base - scratch + BSS_BYTES + AC.bss_tail_slack(segs)
    out = AC.pack(blob, [bytes(text), raw[1], raw[2]], growth)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'base': base, 'flip': fl, 'menu_wrapper': mw, 'menu': menu,
            'common': common, 'hooks': hooks, 'sites': len(HOOKS) + 2,
            'words': (pool.at - lo) // 4, 'region': lo, 'bss': growth}
