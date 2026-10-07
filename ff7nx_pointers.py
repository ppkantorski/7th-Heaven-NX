#!/usr/bin/env python3
"""
ff7nx_pointers.py -- the field pointer hand and the exit arrows. BUILD 611.

Three hardware reports (2026-09-29), three causes.

1. THE HAND JITTERS AS CLOUD MOVES (worst at angles)
   The hand's position is [0xCC0414]/[0xCC0410] = the player's projected
   position in WHOLE units (0x66307D rounds with _ftol) times the view
   multiplier. Cloud's model is drawn at his real position, so the hand
   steps up to a unit (3 px at 720p) against him, on both axes at once when
   he moves diagonally. BUILD 610b already feeds the projection his real
   position for the hand's two calls (ff7nx_campos, kind 1); what is left is
   the rounding of the RESULT. FFNx answers it in ff7_field_submit_draw_cursor
   by adding the float remainder to the hand's four vertices. Same here:
     * in 0x66307D, the two _ftol calls for screen x and y (+0xA968C4,
       +0xA969C4) become calls to a cave that, for a call ff7nx_campos tagged
       as the hand (x24 = 'CAMP'<<32 | 1<<16 | model), copies the value off
       the x87 stack (the unrounded screen x / y) into 8 bytes of BSS, then
       runs the stock _ftol;
     * the hand's draw submission (x86 0x60D7F6, +0x928BE4) adds
       (value - trunc(value)) * multiplier to its four vertices, in the guest
       frame where 0x60D572 built them, then submits.
   Nothing else is projected differently; untagged calls never write BSS.

2. THE HAND STOPS AT THE 4:3 EDGE
   0x60D572 clamps the hand's x to [vp, vp + 320 * m] (x86 0x60D5C8..
   0x60D618) -- the 4:3 window. FFNx widens it to the wide viewport
   (background.cpp compute_pointer_hand_position). Here the ARM x-clamp
   block (+0x928164 .. +0x928294, nothing branches into it) is replaced by a
   cave that clamps to [vp - 53 m, vp + 373 m] -- the 16:9 frame, 53 being
   FFNx's (and ff7nx_camclamp's) half-width margin -- and continues at the
   stock y clamp with the registers it expects (w19 0xCC0410, w20 0xCFF204,
   w21 0xCFF1F0, w22 0xCC0414).

3. TWO RED ARROWS FOR ONE EXIT
   0x64DA3B draws a red arrow at the midpoint of every gateway whose
   show-arrow byte (section 8 +0x218) is 1, and then every explicit arrow
   (+0x224, 12 x {x, y, z, type}). Many exits have both: the designers placed
   an explicit arrow inside the 4:3 picture because the gateway's own arrow
   sat on the exit line, outside it, where the 4:3 window cut it off. The
   widescreen/uncropped view shows the exit line, so both appear (mds6_3,
   mrkt1, mrkt2 ...). The fix hides the GATEWAY arrow when an explicit RED
   arrow belongs to the same exit: its nearest gateway on screen is that one
   and the two are within DUP_DIST field units on screen (projected with the
   field's own camera, section 2 -- checked against the mds6_3 capture to a
   few units). The table is one u16 mask per maplist id, computed at build
   time from the built flevel; the draw loop's flag load (+0x9FC154) reads
   it for the current field (u16 0xCC15D0). Section 8 is not edited.

Switches (exefs/main only):
  SEVENTH_NX_HANDSMOOTH=0   no sub-unit hand
  SEVENTH_NX_HANDWIDE=0     hand clamped to 4:3 as stock
  SEVENTH_NX_ARROWDEDUPE=0  every gateway arrow drawn as stock
"""
import math
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_deadspace as DS

G2H = 0x10FC3A0
MAGIC = 0x434D4150              # ff7nx_campos.MAGIC
KIND_CURSOR = 1

# 1. hand remainder
FTOL = 0x6670
FTOL_X = 0xA968C4               # bl 0x6670 after fadd [scale+0x38]  (x)
FTOL_Y = 0xA969C4               # bl 0x6670 after fadd [scale+0x3C]  (y)
FTOL_STOCK = {FTOL_X: 0x97D5BF6B, FTOL_Y: 0x97D5BF2B}
FP_REG = 22                     # x87 state block in 0x66307D's body
FP_TOP = 0x60
SUBMIT = 0x928BE4               # bl 0x928CE0 (0x63A171) in 0x60D572
SUBMIT_TGT = 0x928CE0
SUBMIT_STOCK = 0x9400003F
HAND_CTX = 23
VIEW_MULT = 0xCFF1F0
BSS_BYTES = 16                  # hand x, y ; camera x, y (BUILD 613)

# 2. hand clamp
CLAMP_SITE = 0x928164           # add w22, w19, #4
CLAMP_STOCK = 0x11001276
CLAMP_RESUME = 0x928294         # mov w0, w19   (the y clamp)
CLAMP_RESUME_STOCK = 0x2A1303E0
VIEW_X = 0xCFF204
HAND_X = 0xCC0414
HAND_Y = 0xCC0410
WIDE_MARGIN = 53

# 3. arrows
FLAG_SITE = 0x9FC154            # ldrb w8, [x0]   (show-arrow byte)
FLAG_STOCK = 0x39400008
FLAG_CONTEXT = {0x9FC14C: 0x11086100,       # add w0, w8, #0x218
                0x9FC158: 0x39002368}       # strb w8, [x27, #8]
ARROW_CTX = 27
FIELD_ID = 0xCC15D0             # u16 maplist index (ff7nx_campreserve)
DUP_DIST = 80.0

ENV_SMOOTH = 'SEVENTH_NX_HANDSMOOTH'
ENV_WIDE = 'SEVENTH_NX_HANDWIDE'
ENV_ARROW = 'SEVENTH_NX_ARROWDEDUPE'
ENVS = (ENV_SMOOTH, ENV_WIDE, ENV_ARROW, 'SEVENTH_NX_CAMRAIL')
NE, HS, GE, LE, EQ, HI, GT, LT = 1, 2, 10, 13, 0, 8, 12, 11


def _on(name, env=None):
    env = os.environ if env is None else env
    return env.get(name, '1').strip().lower() not in ('0', 'off', 'no', 'false')


# ------------------------------------------------------------------ helpers
def _mov32(w, reg, value):
    w.append(A.movz(reg, value & 0xFFFF))
    w.append(A.movk_hi(reg, (value >> 16) & 0xFFFF))


def _stp_off(a, b, off):
    return 0xA9000000 | (((off // 8) & 0x7F) << 15) | (b << 10) | (31 << 5) | a


def _ldp_off(a, b, off):
    return 0xA9400000 | (((off // 8) & 0x7F) << 15) | (b << 10) | (31 << 5) | a


def _lsr64(rd, rn, sh):
    return 0xD340FC00 | (sh << 16) | (rn << 5) | rd


def _ubfx(rd, rn, lsb, width):
    return 0x53000000 | (lsb << 16) | ((lsb + width - 1) << 10) | (rn << 5) | rd


def _add_lsl64(rd, rn, rm, sh):
    return 0x8B000000 | (rm << 16) | (sh << 10) | (rn << 5) | rd


def _ldr_d(dt, xn, imm=0):
    return 0xFD400000 | ((imm >> 3) << 10) | (xn << 5) | dt


def _fcvt_sd(sd, dn):
    return 0x1E624000 | (dn << 5) | sd          # fcvt Sd, Dn


def _ldr_s(st, xn, imm=0):
    return 0xBD400000 | ((imm >> 2) << 10) | (xn << 5) | st


def _str_s(st, xn, imm=0):
    return 0xBD000000 | ((imm >> 2) << 10) | (xn << 5) | st


def _fcvtzs(wd, sn):
    return 0x1E380000 | (sn << 5) | wd


def _scvtf(sd, wn):
    return 0x1E220000 | (wn << 5) | sd


def _sxth(rd, rn):
    return 0x13003C00 | (rn << 5) | rd


def _cmp_imm(rn, imm):
    return 0x7100001F | (imm << 10) | (rn << 5)


def _csel(rd, rn, rm, cond):
    return 0x1A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd


def _ldrh_reg_lsl1(rt, xn, wm):
    """ldrh Wt, [Xn, Wm, UXTW #1]"""
    return 0x78607800 | (wm << 16) | (xn << 5) | rt


def _lsrv(rd, rn, rm):
    return 0x1AC02400 | (rm << 16) | (rn << 5) | rd


class _B:
    def __init__(self, addr):
        self.addr, self.w, self.fix = addr, [], []

    def emit(self, *ws):
        self.w.extend(ws)

    def here(self):
        return self.addr(len(self.w))

    def bl(self, target):
        self.w.append(A.bl(self.here(), target))

    def b(self, target):
        self.w.append(A.b(self.here(), target))

    def to(self, label, cond=None):
        self.fix.append((len(self.w), label, cond))
        self.w.append(0)

    def resolve(self, labels):
        for i, lab, cond in self.fix:
            frm, dst = self.addr(i), self.addr(labels[lab])
            self.w[i] = A.b(frm, dst) if cond is None else A.bcond(frm, dst, cond)
        return self.w


def _bss_ptr(b, reg, bss):
    b.emit(A.adrp(reg, b.here(), bss & ~0xFFF))
    b.emit(A.add_imm64(reg, reg, bss & 0xFFF))


# ------------------------------------------------------------------ caves
def ftol_body(addr, bss, slot):
    """Replaces `bl 0x6670` (_ftol of the x87 top) in 0x66307D. For a call
    tagged as the hand, copy the unrounded top into BSS first. Only x9, x10
    and s0/d0 are used before the stock call, which is a call anyway."""
    b = _B(addr)
    b.emit(A.stp64_pre(29, 30, 31, -16))
    b.emit(_lsr64(9, 24, 32))
    _mov32(b.w, 10, MAGIC)
    b.emit(A.cmp_reg(9, 10))
    b.to('call', NE)
    # kind 1 (the hand) -> slot, kind 0 (the camera, BUILD 613) -> slot + 8
    b.emit(_ubfx(9, 24, 16, 8), _cmp_imm(9, KIND_CURSOR))
    b.to('call', HI)
    b.emit(A.movz(10, 8), A.movz(11, 0), _csel(11, 11, 10, EQ))   # EQ: hand
    b.emit(A.ldr(9, FP_REG, FP_TOP),
           _add_lsl64(9, FP_REG, 9, 3),
           _ldr_d(0, 9, 0), _fcvt_sd(0, 0))
    _bss_ptr(b, 10, bss + slot)
    b.emit(A.add_reg64(10, 10, 11), _str_s(0, 10, 0))
    call = len(b.w)
    b.bl(FTOL)
    b.emit(A.ldp64_post(29, 30, 31, 16), A.ret())
    return b.resolve({'call': call})


def submit_body(addr, bss):
    """Replaces `bl 0x63A171` in 0x60D572: add the remainder of the hand's
    projected x / y (times the view multiplier) to the four vertices in the
    guest frame ([ebp-0x48] + 8k, [ebp-0x44] + 8k), then tail-call."""
    b = _B(addr)
    b.emit(A.stp64_pre(29, 30, 31, -32), _stp_off(19, 20, 16))
    _bss_ptr(b, 10, bss)
    b.emit(_ldr_s(0, 10, 0), _ldr_s(1, 10, 4),
           _fcvtzs(9, 0), _scvtf(2, 9), A.fsub_s(0, 0, 2),
           _fcvtzs(9, 1), _scvtf(2, 9), A.fsub_s(1, 1, 2))
    _mov32(b.w, 0, VIEW_MULT)
    b.bl(G2H)                                  # g2h leaves s0-s2 alone
    b.emit(A.ldr(9, 0, 0), _scvtf(2, 9),
           A.fmul_s(0, 0, 2), A.fmul_s(1, 1, 2))
    b.emit(A.ldr(19, HAND_CTX, 0x14))          # ebp
    for k in range(4):
        for off, s in ((0x48 - 8 * k, 0), (0x44 - 8 * k, 1)):
            b.emit(A.sub_imm(0, 19, off))
            b.bl(G2H)
            b.emit(_ldr_s(2, 0, 0), A.fadd_s(2, 2, s), _str_s(2, 0, 0))
    b.emit(_ldp_off(19, 20, 16), A.ldp64_post(29, 30, 31, 32))
    b.b(SUBMIT_TGT)                            # returns to SUBMIT + 4
    return b.resolve({})


def clamp_body(addr):
    """Replaces the x clamp block of 0x60D572 (entered at +0x928164, left at
    +0x928294). x = clamp(x, vp - 53 m, vp + 373 m), then the stock y clamp
    with w19..w22 as it expects them. x30 is dead here (the stock block
    calls the translator too; the prologue saved it). x24/x25 carry values
    across the translator calls and are restored before leaving."""
    b = _B(addr)
    b.emit(A.stp64_pre(24, 25, 31, -16))
    b.emit(A.add_imm(22, 19, 4))               # the displaced instruction
    _mov32(b.w, 20, VIEW_X)
    b.emit(A.sub_imm(21, 20, 0x14))            # 0xCFF1F0
    b.emit(A.mov_reg(0, 21))
    b.bl(G2H)
    b.emit(A.ldr(24, 0, 0))                    # m
    b.emit(A.mov_reg(0, 20))
    b.bl(G2H)
    b.emit(A.ldr(25, 0, 0))                    # vp
    # w24 = lo = vp - 53 m ; w25 = hi = vp + 373 m
    b.emit(A.movz(9, WIDE_MARGIN), A.mul(9, 9, 24),
           A.movz(10, 320 + WIDE_MARGIN), A.mul(10, 10, 24),
           A.sub_reg(24, 25, 9), A.add_reg(25, 25, 10))
    b.emit(A.mov_reg(0, 22))
    b.bl(G2H)
    b.emit(A.ldrsh(9, 0, 0),
           A.cmp_reg(9, 25), _csel(9, 25, 9, 12),       # gt -> hi
           A.cmp_reg(9, 24), _csel(9, 24, 9, 11),       # lt -> lo
           A.strh(9, 0, 0))
    b.emit(A.ldp64_post(24, 25, 31, 16))
    b.b(CLAMP_RESUME)
    return b.resolve({})


def flag_body(addr, table, n_ids):
    """Replaces `ldrb w8, [x0]` (gateway i's show-arrow byte; i is the guest
    eax at [x27]). w8 = 0 when this field's mask hides gateway i."""
    b = _B(addr)
    b.emit(A.ldrb(8, 0, 0))
    b.fix.append((len(b.w), 'ret', 'cbz8'))
    b.emit(0)
    b.emit(A.stp64_pre(29, 30, 31, -32), _stp_off(8, 19, 16))
    _mov32(b.w, 0, FIELD_ID)
    b.bl(G2H)
    b.emit(A.ldrh(19, 0, 0))                   # field id
    b.emit(_ldp_off(8, 9, 16))                 # w8 back (x9 = saved x19)
    b.emit(A.movz(10, n_ids), A.cmp_reg(19, 10))
    b.to('restore', HS)
    b.emit(A.adrp(10, b.here(), table & ~0xFFF),
           A.add_imm64(10, 10, table & 0xFFF),
           _add_lsl64(10, 10, 19, 1),
           A.ldrh(10, 10, 0),                   # mask
           A.ldr(11, ARROW_CTX, 0),             # i
           _lsrv(10, 10, 11),
           A.and_mask(10, 10, 1))
    b.fix.append((len(b.w), 'restore', 'cbz10'))
    b.emit(0)
    b.emit(A.movz(8, 0))                        # bit set -> not drawn
    restore = len(b.w)
    b.emit(A.mov_reg64(19, 9), A.ldp64_post(29, 30, 31, 32))
    ret = len(b.w)
    b.emit(A.ret())
    labels = {'restore': restore, 'ret': ret}
    for i, lab, how in list(b.fix):
        if how == 'cbz8':
            b.w[i] = A.cbz(8, b.addr(i), b.addr(labels[lab]))
        elif how == 'cbz10':
            b.w[i] = A.cbz(10, b.addr(i), b.addr(labels[lab]))
    b.fix = [f for f in b.fix if f[2] not in ('cbz8', 'cbz10')]
    return b.resolve(labels)


# ------------------------------------------------------------ the rail
# BUILD 613. mds5_3 (and every field whose trigger header +0x14 is 1 or 2)
# puts the camera on a DIAGONAL RAIL: 0x643628 projects the clamped camera
# point onto the line between two corners of the camera range, in integers,
# from an integer point. Walking at an angle to the rail, the point moves in
# a staircase (x ticks, then y ticks), and each tick moves the projection
# FORWARD along the rail for one axis and BACK for the other -- the camera
# steps a unit forward and back from frame to frame (measured: (+3,+3),
# (-3,-3), (+3,+3) px on consecutive frames). FFNx's float_sub_643628 does the
# same projection in float from the float camera point. Same here: the call
# at +0x9FA5B4 runs the stock function, then, for a rail field, recomputes
# the projection from the REAL point -- the integer point plus the remainder
# the camera's own projection rounded away (captured by the ftol cave as
# kind 0), taken per axis only when the range clip left that axis alone --
# and rounds it to the nearest unit.
RAIL_SITE = 0x9FA5B4            # bl 0x643628's body, in 0x644075
RAIL_TGT = 0xA108A0
RAIL_CTX = 21
RAIL_POINT = 0x38               # the camera point, [ebp-0x38] (x, y shorts)
TRIGGERS_HDR = 0xCFF454
VIEW_Y = 0xCFF208
ENV_RAIL = 'SEVENTH_NX_CAMRAIL'


def _fcvtas(wd, sn):
    return 0x1E240000 | (sn << 5) | wd


def rail_body(addr, bss):
    """Replaces `bl 0x643628` at +0x9FA5B4 (0x644075's normal camera path).

    Integers are gathered first (every translator call clobbers x0-x18);
    the float work is done after the last load and survives the two
    translator calls that store the result (they do not touch v-regs).
    Frame: x19-x28 saved; vy parked at sp+0x60."""
    b = _B(addr)
    b.emit(A.stp64_pre(19, 20, 31, -0x70),
           _stp_off(21, 22, 0x10), _stp_off(23, 24, 0x20),
           _stp_off(25, 26, 0x30), _stp_off(27, 28, 0x40),
           _stp_off(29, 30, 0x50))
    b.emit(A.ldr(19, RAIL_CTX, 0x14), A.sub_imm(19, 19, RAIL_POINT))
    b.emit(A.mov_reg(0, 19))
    b.bl(G2H)
    b.emit(A.ldrsh(20, 0, 0))                  # x before the rail
    b.emit(A.add_imm(0, 19, 2))
    b.bl(G2H)
    b.emit(A.ldrsh(22, 0, 0))                  # y before the rail
    b.bl(RAIL_TGT)                             # the stock projection
    _mov32(b.w, 0, TRIGGERS_HDR)
    b.bl(G2H)
    b.emit(A.ldr(23, 0, 0))                    # header (guest)
    b.emit(A.add_imm(0, 23, 0x14))
    b.bl(G2H)
    b.emit(A.ldrb(9, 0, 0), A.sub_imm(9, 9, 1), _cmp_imm(9, 1))
    b.to('done', HI)                           # not 1 or 2
    b.emit(A.add_imm(24, 9, 0))                # w24 = mode - 1 (0 or 1)
    regs = (25, 26, 27, 28)                    # L, T, R, B
    for k, r in enumerate(regs):
        b.emit(A.add_imm(0, 23, 0xC + 2 * k))
        b.bl(G2H)
        b.emit(A.ldrsh(r, 0, 0))
    _mov32(b.w, 0, VIEW_X)
    b.bl(G2H)
    b.emit(A.ldr(23, 0, 0))                    # vx (header no longer needed)
    _mov32(b.w, 0, VIEW_Y)
    b.bl(G2H)
    b.emit(A.ldr(9, 0, 0), A.str_(9, 31, 0x60))   # vy
    # ---- floats: s0,s1 the real point; s2..s5 A and B' ------------------
    _bss_ptr(b, 10, bss + 8)
    b.emit(_ldr_s(6, 10, 0), _ldr_s(7, 10, 4))     # captured cx, cy
    # x: frac only if trunc(cx) - vx == x_pre (the clip left x alone)
    b.emit(_fcvtzs(9, 6), A.sub_reg(10, 9, 23), A.cmp_reg(10, 20),
           _scvtf(16, 9), A.fsub_s(6, 6, 16),      # s6 = cx - trunc(cx)
           _scvtf(0, 20))
    b.to('nofx', NE)
    b.emit(A.fadd_s(0, 0, 6))
    nofx = len(b.w)
    b.emit(A.ldr(11, 31, 0x60),
           _fcvtzs(9, 7), A.sub_reg(10, 9, 11), A.cmp_reg(10, 22),
           _scvtf(16, 9), A.fsub_s(7, 7, 16),
           _scvtf(1, 22))
    b.to('nofy', NE)
    b.emit(A.fadd_s(1, 1, 7))
    nofy = len(b.w)
    # A = (L+160, T+120) B' = (R-160, B-120) for mode 1; mode 2 swaps the y's
    b.emit(A.add_imm(9, 25, 160), _scvtf(2, 9),        # Ax = L + 160
           A.add_imm(9, 26, 120), A.sub_imm(10, 28, 120),
           _cmp_imm(24, 0),
           _csel(11, 9, 10, EQ),                       # Ay
           _csel(12, 10, 9, EQ),                       # B'y
           _scvtf(3, 11),
           A.sub_imm(9, 27, 160), _scvtf(4, 9),        # B'x = R - 160
           _scvtf(5, 12))
    # d = B' - A ; dd = d.d ; t = ((p - A).d) / dd ; r = A + t d
    b.emit(A.fsub_s(4, 4, 2), A.fsub_s(5, 5, 3),       # s4,s5 = d
           A.fmul_s(16, 4, 4), A.fmul_s(17, 5, 5), A.fadd_s(16, 16, 17))
    b.emit(_fcmp_zero(16))
    b.to('done', EQ)
    b.emit(A.fsub_s(0, 0, 2), A.fsub_s(1, 1, 3),
           A.fmul_s(0, 0, 4), A.fmul_s(1, 1, 5), A.fadd_s(0, 0, 1),
           A.fdiv_s(0, 0, 16),                         # t
           A.fmul_s(1, 0, 5), A.fadd_s(1, 1, 3),       # ry
           A.fmul_s(0, 0, 4), A.fadd_s(0, 0, 2),       # rx
           _fcvtas(20, 0), _fcvtas(22, 1))
    b.emit(A.mov_reg(0, 19))
    b.bl(G2H)
    b.emit(A.strh(20, 0, 0))
    b.emit(A.add_imm(0, 19, 2))
    b.bl(G2H)
    b.emit(A.strh(22, 0, 0))
    done = len(b.w)
    b.emit(_ldp_off(29, 30, 0x50), _ldp_off(27, 28, 0x40),
           _ldp_off(25, 26, 0x30), _ldp_off(23, 24, 0x20),
           _ldp_off(21, 22, 0x10), A.ldp64_post(19, 20, 31, 0x70),
           A.ret())
    return b.resolve({'done': done, 'nofx': nofx, 'nofy': nofy})


def _fcmp_zero(sn):
    return 0x1E202008 | (sn << 5)              # fcmp Sn, #0.0


# ------------------------------------------------------------ arrow table
def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _project(cam, p):
    vx, vy, vz, (ox, oy, oz), zoom = cam
    d = lambda v: (v[0] * p[0] + v[1] * p[1] + v[2] * p[2]) / 4096.0
    x, y, z = d(vx) + ox, d(vy) + oy, d(vz) + oz
    if z <= 0:
        return None
    return x * zoom / z, y * zoom / z


def camera_of(sec2):
    vx = struct.unpack_from('<3h', sec2, 0)
    vy = struct.unpack_from('<3h', sec2, 6)
    vz = struct.unpack_from('<3h', sec2, 12)
    o = struct.unpack_from('<3i', sec2, 20)
    zoom = struct.unpack_from('<h', sec2, 36)[0]
    return vx, vy, vz, o, zoom


def hidden_gateways(sec2, sec8, dist=DUP_DIST):
    """Gateway indices whose red arrow duplicates an explicit red arrow."""
    if len(sec2) < 38 or len(sec8) < 0x224 + 12 * 16:
        return set()
    cam = camera_of(sec2)
    gws = []
    for i in range(12):
        v = struct.unpack_from('<9hH', sec8, 0x38 + 24 * i)
        if v[9] == 0x7FFF or not any(v[:6]):
            continue
        mid = ((v[0] + v[3]) // 2, (v[1] + v[4]) // 2, (v[2] + v[5]) // 2)
        if mid[0] == 0 and mid[1] == 0:        # 0x64DB59: never drawn
            continue
        sp = _project(cam, mid)
        if sp is not None:
            gws.append((i, sp, sec8[0x218 + i]))
    hide = set()
    for k in range(12):
        x, y, z, t = struct.unpack_from('<4i', sec8, 0x224 + 16 * k)
        if t != 1 or not gws:
            continue
        sp = _project(cam, (_s16(x), _s16(y), _s16(z)))
        if sp is None:
            continue
        d, g = min((math.hypot(sp[0] - g[1][0], sp[1] - g[1][1]), g)
                   for g in gws)
        if g[2] == 1 and d <= dist:
            hide.add(g[0])
    # BUILD 618z (woa_3, user 10-05): one exit line can be split into
    # several gateways with the same destination, end to end. Hiding the
    # one nearest the explicit arrow left its neighbour's arrow at the
    # corner of the 16:9 picture (gateways 1/2 -> map 705). Every gateway
    # chained to a hidden one (shared endpoint, same destination) goes too.
    segs = {}
    for i in range(12):
        v = struct.unpack_from('<9hH', sec8, 0x38 + 24 * i)
        if v[9] == 0x7FFF or not any(v[:6]):
            continue
        segs[i] = (v[0:3], v[3:6], v[6:10])
    grew = True
    while grew:
        grew = False
        for i, (a0, a1, dest) in segs.items():
            if i in hide or sec8[0x218 + i] != 1:
                continue
            for j in list(hide):
                if j not in segs or segs[j][2] != dest:
                    continue
                b0, b1 = segs[j][0], segs[j][1]
                if a0 in (b0, b1) or a1 in (b0, b1):
                    hide.add(i)
                    grew = True
                    break
    return hide


def _field_sections(payload):
    """(section 2 body, section 8 body) decompressed only as far as needed."""
    import ff7nx_wsdata as W
    head = W._lzss_head(payload, 42)
    starts = struct.unpack('<9I', head[6:42])
    end = starts[7] + 4 + 0x224 + 12 * 16
    raw = W._lzss_head(payload, end)
    s2 = raw[starts[1] + 4:starts[1] + 4 + 40]
    s8 = raw[starts[7] + 4:end]
    return s2, s8


def arrow_table(flevel_path):
    """(bytes: u16 mask per maplist id, {name: sorted gateways})"""
    import ff7nx_daynight as DN
    maplist = DN.read_maplist(flevel_path)
    index, read = DN._stream_index(flevel_path)
    masks = []
    report = {}
    for name in maplist:
        mask = 0
        if name in index:
            try:
                s2, s8 = _field_sections(read(name))
                hide = hidden_gateways(s2, s8)
            except Exception:                              # noqa: BLE001
                hide = set()
            for g in hide:
                mask |= 1 << g
            if hide:
                report[name] = sorted(hide)
        masks.append(mask)
    return struct.pack('<%dH' % len(masks), *masks), report


# ------------------------------------------------------------------ apply
def _word(text, va):
    return struct.unpack_from('<I', text, va)[0]


def check_sites(text, smooth, wide, arrow, rail=False):
    bad = []
    if rail and _word(text, RAIL_SITE) != A.bl(RAIL_SITE, RAIL_TGT):
        bad.append('+0x%X' % RAIL_SITE)
    if smooth:
        for va, want in FTOL_STOCK.items():
            if _word(text, va) != want:
                bad.append('+0x%X' % va)
        if _word(text, SUBMIT) != SUBMIT_STOCK:
            bad.append('+0x%X' % SUBMIT)
    if wide:
        if (_word(text, CLAMP_SITE) != CLAMP_STOCK
                or _word(text, CLAMP_RESUME) != CLAMP_RESUME_STOCK):
            bad.append('+0x%X' % CLAMP_SITE)
    if arrow:
        if _word(text, FLAG_SITE) != FLAG_STOCK or any(
                _word(text, va) != w for va, w in FLAG_CONTEXT.items()):
            bad.append('+0x%X' % FLAG_SITE)
    return bad


def apply_to_nso(src, dest, stock, flevel=None, smooth=None, wide=None,
                 arrow=None, rail=None):
    smooth = _on(ENV_SMOOTH) if smooth is None else smooth
    rail = _on(ENV_RAIL) if rail is None else rail
    wide = _on(ENV_WIDE) if wide is None else wide
    arrow = (_on(ENV_ARROW) if arrow is None else arrow) and bool(flevel)
    if not (smooth or wide or arrow or rail):
        raise ValueError('nothing to install')
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    bad = check_sites(text, smooth or rail, wide, arrow, rail)
    if bad:
        raise ValueError('sites not stock: ' + ', '.join(bad))
    lo, hi = DS.part(src, 'pointers', stock)
    pool = DS.Bump(lo, hi)
    words, rep = {}, {'smooth': smooth, 'wide': wide, 'arrow': arrow,
                      'rail': rail}
    growth = 0
    if smooth or rail:
        # the ftol capture serves both: hand (kind 1) and camera (kind 0)
        bss = AC.scratch_base(blob, segs)
        growth = BSS_BYTES + AC.bss_tail_slack(segs)
        for va, slot in ((FTOL_X, 0), (FTOL_Y, 4)):
            e = pool.put(lambda _e, ad, s=slot: ftol_body(ad, bss, s))
            words[va] = A.bl(va, e)
        rep['bss'] = bss
    if smooth:
        e = pool.put(lambda _e, ad: submit_body(ad, bss))
        words[SUBMIT] = A.bl(SUBMIT, e)
    if rail:
        e = pool.put(lambda _e, ad: rail_body(ad, bss))
        words[RAIL_SITE] = A.bl(RAIL_SITE, e)
    if wide:
        e = pool.put(lambda _e, ad: clamp_body(ad))
        words[CLAMP_SITE] = A.b(CLAMP_SITE, e)
    if arrow:
        table, report = arrow_table(flevel)
        n_ids = len(table) // 2
        at = pool.put_bytes(table)
        e = pool.put(lambda _e, ad: flag_body(ad, at, n_ids))
        words[FLAG_SITE] = A.bl(FLAG_SITE, e)
        rep['fields'] = report
    words.update(pool.placed)
    for va, w in words.items():
        struct.pack_into('<I', text, va, w)
    out = AC.pack(blob, [bytes(text), raw[1], raw[2]], growth)
    with open(dest, 'wb') as handle:
        handle.write(out)
    rep['words'] = len(pool.placed)
    return rep
