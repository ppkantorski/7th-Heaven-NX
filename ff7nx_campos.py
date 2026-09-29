#!/usr/bin/env python3
"""
ff7nx_campos.py -- the field camera follows the player's REAL position, not
its three separately truncated whole-unit parts (BUILD 610), and the walk
step stores the model's real height instead of a whole unit (BUILD 610b).

BUILD 610b, AFTER THE HARDWARE REPORT ("did not improve at all")
  * 610 tagged the wrong calls. The recompiled 0x644075 does not keep its
    three `bl 0x64314F` in x86 order: +0x9F9E14 is the pointer hand, the
    camera is +0x9FA4AC. The exact-match check then refused both, so the
    camera ran stock. Every site is now proven from its own code
    (SITE_PROOF) before anything is written.
  * The cable "vibration" is the MODEL, not only the camera: see the walk
    height section (wcrimb_2 triangle 7 has dz/dy = 10, so Cloud hopped
    10 units every time y crossed a whole unit).

THE FAULT (hardware, 2026-09-29)
  wcrimb_2  walking slowly along the cable, the camera jerks up and down;
  cosin2    climbing the ladder, the camera twitches near the top.
Measured off the captures (phase correlation, 640-wide frames): on the cable
the picture moves in 4-6 px jumps every 5-6 frames, with 2 px BACKWARD steps
between them while Cloud keeps walking the same way.

WHY
The camera follow code projects the player onto the screen every frame
(x86 field_update_background_positions, 0x6442FD; the scripted follow of
SCRLC/SCRLA, 0x643C86 -> 0x643CEE). It builds the point from the model's
20.12 fixed-point position like this:

    x = (short)(pos.x >> 12)
    y = (short)(pos.y >> 12)
    z = (short)(pos.z >> 12) + view_z          (view_z = [0xCC0D9E])

and hands those three whole numbers to the projection (0x64314F ->
0x66307D, which converts them to float at 0x661098). Each part is floored on
its own. On a slope all three change together, and each one's floor ticks at
a different moment; the screen point is a weighted sum of them, so it moves in
big steps (a whole unit of height is ~2-3 screen units on a steep camera) and
steps BACK when one part ticks before another. That is the jitter, and it is
worst exactly where the walkmesh is steep and diagonal: the cable, the ladder.

FFNx does not have it: its camera (background.cpp,
field_apply_player_position_2D_translation_float) projects pos / 4096.f.

THE FIX
The same point, unfloored. Two small caves:

  * at the two camera call sites the `bl 0x64314F` becomes a call to a
    wrapper that tags the call (x24 = 'CAMP' << 32 | model index; x24 is not
    touched by 0x64314F, 0x66307D or 0x661098's bodies and is saved and
    restored by the wrapper);
  * in 0x66307D the `bl 0x661098` (short -> float) becomes a call to a cave
    that runs the stock conversion first and then, ONLY when the tag is
    present AND the three shorts it just converted are exactly the floored
    position of that model (so it cannot fire for any other caller), writes
    pos / 4096 (and pos.z / 4096 + view_z) over them.

The projection still rounds its result to a whole unit (_ftol), so the camera
still moves in whole units -- but monotonically, one unit at a time, as the
player really moves: no big steps, no backward steps. Every other use of the
projection (battle, world map, cursors, arrows) is byte-for-byte stock, and a
field whose camera does not move is unaffected.

SEVENTH_NX_CAMPOS=0 leaves all three sites stock.
"""
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_deadspace as DS

ENV = 'SEVENTH_NX_CAMPOS'
G2H = 0x10FC3A0

PROJ = 0x9EC510                 # 0x64314F's ARM body
CONV = 0xA88B70                 # 0x661098's ARM body (short[3] -> float[3])
CONV_SITE = 0xA963F4            # in 0x66307D: bl CONV   (context x21)
CONV_CTX = 21
# the call sites that project the player for the camera, and the two that
# project the pointer hand over it (they must agree, or the hand wobbles
# against the screen): (ARM bl PROJ, x86 call, guest address of the model
# index, its width, kind, what)
FOLLOW_SITES = (
    (0x9FA4AC, 0x6442FD, 0xCC162C, 2, 0, 'normal follow (player)'),
    (0xA123F4, 0x643CEE, 0xCC0DA6, 1, 0, 'scripted follow (SCRLC/SCRLA)'),
    (0x9F9E14, 0x64460D, 0xCC162C, 2, 1, 'pointer hand, normal camera'),
    (0x9FAEAC, 0x644984, 0xCC162C, 2, 1, 'pointer hand, scripted camera'),
)
# BUILD 610b. The recompiler does NOT keep 0x644075's three calls in x86
# order: its FIRST `bl 0x64314F` (+0x9F9E14) is the pointer hand (x86
# 0x64460D) and the camera is the second (+0x9FA4AC). 610 assumed the order,
# tagged the hand as the camera and the camera as the hand, the exact-match
# check refused both, and the camera ran stock -- nothing changed on
# hardware. Each site is now PROVEN from its own code: the recompiled push of
# the input vector's frame offset, `sub wN, w9, #off` seven words before the
# bl, must be the one the x86 call pushes (ebp-0x18 camera, ebp-0x28 hand,
# ebp-8 scripted follow).
SITE_PROOF = {
    0x9FA4AC: (0x9FA4AC - 0x24, 0x18),
    0xA123F4: (0xA123F4 - 0x24, 0x08),
    0x9F9E14: (0x9F9E14 - 0x24, 0x28),
    0x9FAEAC: (0x9FAEAC - 0x24, 0x28),
}
EV_POS = 0xCC167C               # field_event_data[0].model_pos.x
EV_STRIDE = 0x88
MAX_MODELS = 32
VIEW_Z = 0xCC0D9E               # word added to the floored z by both sites
MAGIC = 0x434D4150              # 'PAMC' little-endian -> upper half of x24
EQ, NE, HS, HI, LT, GT = 0, 1, 2, 8, 11, 12


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


# ---------------------------------------------------------------- encoders
def _ldrsb(rt, rn, imm=0):
    return 0x39C00000 | (imm << 10) | (rn << 5) | rt          # 32-bit dest


def _uxth(rd, rn):
    return 0x53003C00 | (rn << 5) | rd                        # ubfm #0,#15


def _sxth(rd, rn):
    return 0x13003C00 | (rn << 5) | rd                        # sbfm #0,#15


def _lsr64(rd, rn, sh):
    return 0xD340FC00 | (sh << 16) | (rn << 5) | rd           # ubfm x,#sh,#63


def _movz64(rd, imm16, hw):
    return 0xD2800000 | (hw << 21) | (imm16 << 5) | rd


def _movk64(rd, imm16, hw):
    return 0xF2800000 | (hw << 21) | (imm16 << 5) | rd


def _orr64(rd, rn, rm):
    return 0xAA000000 | (rm << 16) | (rn << 5) | rd


def _cmp_sxth(rn, rm):
    """cmp Wn, (short) Wm -- two words: sxth w9, Wm ; cmp Wn, w9."""
    return [_sxth(9, rm), A.cmp_reg(rn, 9)]


def _cmp_imm(rn, imm):
    return 0x7100001F | (imm << 10) | (rn << 5)


def _scvtf_fix(sd, wn, fbits):
    """scvtf Sd, Wn, #fbits  -- Wn / 2**fbits, exact up to rounding."""
    return 0x1E020000 | ((64 - fbits) << 10) | (wn << 5) | sd


def _scvtf(sd, wn):
    return 0x1E220000 | (wn << 5) | sd


def _str_s(st, rn, imm=0):
    return 0xBD000000 | ((imm >> 2) << 10) | (rn << 5) | st


def _stp_off(a, b, off):
    return 0xA9000000 | (((off // 8) & 0x7F) << 15) | (b << 10) | (31 << 5) | a


def _ldp_off(a, b, off):
    return 0xA9400000 | (((off // 8) & 0x7F) << 15) | (b << 10) | (31 << 5) | a


def _mov32(w, reg, value):
    w.append(A.movz(reg, value & 0xFFFF))
    w.append(A.movk_hi(reg, (value >> 16) & 0xFFFF))


class _B:
    """Words plus forward labels, laid out through addr(i)."""

    def __init__(self, addr):
        self.addr, self.w, self.fix = addr, [], []

    def emit(self, *ws):
        self.w.extend(ws)

    def here(self):
        return self.addr(len(self.w))

    def bl(self, target):
        self.w.append(A.bl(self.here(), target))

    def bcond_to(self, label, cond):
        self.fix.append((len(self.w), label, cond))
        self.w.append(0)

    def resolve(self, labels):
        for i, lab, cond in self.fix:
            self.w[i] = A.bcond(self.addr(i), self.addr(labels[lab]), cond)
        return self.w


# ------------------------------------------------------------------ caves
KIND_CAMERA = 0                 # shorts = floor(pos) (+ view z on z)
KIND_CURSOR = 1                 # shorts = floor(pos) + model offset (+ hand z)
OFF_X = 0xCC16B0 - EV_POS       # field_event_data offset_position_x/y/z,
OFF_Y = 0xCC16B8 - EV_POS       # relative to model_pos.x
OFF_Z = 0xCC16C0 - EV_POS
HAND_Z = 0xCC0D98               # word; the hand adds (short)((v << 7) >> 9)


def _ubfx(rd, rn, lsb, width):
    return 0x53000000 | (lsb << 16) | ((lsb + width - 1) << 10) | (rn << 5) | rd


def _cbnz(rt, frm, to):
    return A.cbnz(rt, frm, to)


def tag_body(addr, index_guest, width, kind=KIND_CAMERA):
    """The wrapper at a camera call site: tag x24, call 0x64314F, untag."""
    b = _B(addr)
    b.emit(A.stp64_pre(24, 30, 31, -16))
    _mov32(b.w, 0, index_guest)
    b.bl(G2H)                                  # clobbers x0, x8-x10 only
    b.emit(A.ldrsh(24, 0, 0) if width == 2 else _ldrsb(24, 0, 0))
    b.emit(_uxth(24, 24))                      # w24 = index (0xFFFF if <0)
    if kind:
        b.emit(A.movz(9, kind), A.orr_lsl(24, 24, 9, 16))
    b.emit(_movz64(9, MAGIC & 0xFFFF, 2),
           _movk64(9, MAGIC >> 16, 3),
           _orr64(24, 24, 9))                  # x24 = MAGIC<<32 | kind<<16 | i
    b.bl(PROJ)
    b.emit(A.ldp64_post(24, 30, 31, 16),
           A.ret())
    return b.resolve({})


def conv_body(addr):
    """In 0x66307D: the stock short->float conversion, then the precise
    position over it when (and only when) this is a tagged call whose three
    shorts are exactly what the tagged site builds from that model."""
    b = _B(addr)
    ctx = CONV_CTX
    b.emit(A.stp64_pre(19, 20, 31, -0x60),
           _stp_off(21, 22, 0x10), _stp_off(23, 24, 0x20),
           _stp_off(25, 26, 0x30), _stp_off(27, 28, 0x40),
           _stp_off(29, 30, 0x50))
    b.bl(CONV)                                 # stock: floats of the shorts
    # the tag
    b.emit(_lsr64(9, 24, 32))
    _mov32(b.w, 10, MAGIC)
    b.emit(A.cmp_reg(9, 10))
    b.bcond_to('out', NE)
    b.emit(_ubfx(28, 24, 16, 8),               # kind
           _uxth(25, 24),                      # model index
           _cmp_imm(25, MAX_MODELS))
    b.bcond_to('out', HS)
    b.emit(_cmp_imm(28, KIND_CURSOR))
    b.bcond_to('out', HI)
    # the conversion's two arguments: [ESP] = short *in, [ESP+4] = float *out
    b.emit(A.ldr(0, ctx, 0x10))
    b.bl(G2H)
    b.emit(A.ldr(26, 0, 0))
    b.emit(A.ldr(0, ctx, 0x10), A.add_imm(0, 0, 4))
    b.bl(G2H)
    b.emit(A.ldr(27, 0, 0))
    # the model's 20.12 position
    b.emit(A.movz(9, EV_STRIDE), A.mul(25, 25, 9))
    _mov32(b.w, 10, EV_POS)
    b.emit(A.add_reg(25, 25, 10))
    for k, reg in enumerate((19, 20, 22)):
        b.emit(A.add_imm(0, 25, 4 * k))
        b.bl(G2H)
        b.emit(A.ldr(reg, 0, 0))
    # what each site adds to the floored coordinate: x23, x24, x28
    b.fix.append((len(b.w), 'cursor', 'cbnz28'))
    b.emit(0)
    b.emit(A.mov_reg(23, 31), A.mov_reg(24, 31))   # camera: +0, +0,
    _mov32(b.w, 0, VIEW_Z)
    b.bl(G2H)
    b.emit(A.ldrsh(28, 0, 0))                  # + view z
    b.fix.append((len(b.w), 'common', 'b'))
    b.emit(0)
    cursor = len(b.w)
    for reg, off in ((23, OFF_X), (24, OFF_Y), (28, OFF_Z)):
        b.emit(A.add_imm(0, 25, off))
        b.bl(G2H)
        b.emit(A.ldr(reg, 0, 0))
    _mov32(b.w, 0, HAND_Z)
    b.bl(G2H)
    b.emit(A.ldrsh(9, 0, 0), A.lsl(9, 9, 7), A.asr(9, 9, 9), _sxth(9, 9),
           A.add_reg(28, 28, 9))
    common = len(b.w)
    # the shorts must be exactly what the site built
    for k, (reg, add) in enumerate(((19, 23), (20, 24), (22, 28))):
        b.emit(A.add_imm(0, 26, 2 * k))
        b.bl(G2H)
        b.emit(A.ldrsh(10, 0, 0),
               A.asr(9, reg, 12), _sxth(11, 9), A.cmp_reg(9, 11))
        b.bcond_to('out', NE)                  # floor fits a short
        b.emit(A.add_reg(9, 11, add), _sxth(11, 9), A.cmp_reg(9, 11))
        b.bcond_to('out', NE)                  # and the sum does too
        b.emit(A.cmp_reg(10, 9))
        b.bcond_to('out', NE)                  # and it is what was built
    # pos / 4096 + what the site adds
    for k, (reg, add) in enumerate(((19, 23), (20, 24), (22, 28))):
        b.emit(_scvtf_fix(k, reg, 12), _scvtf(3, add), A.fadd_s(k, k, 3))
    for k in range(3):                         # g2h leaves s0-s2 alone
        b.emit(A.add_imm(0, 27, 4 * k))
        b.bl(G2H)
        b.emit(_str_s(k, 0, 0))
    out = len(b.w)
    b.emit(_ldp_off(29, 30, 0x50), _ldp_off(27, 28, 0x40),
           _ldp_off(25, 26, 0x30), _ldp_off(23, 24, 0x20),
           _ldp_off(21, 22, 0x10), A.ldp64_post(19, 20, 31, 0x60),
           A.ret())
    labels = {'out': out, 'cursor': cursor, 'common': common}
    for i, lab, how in list(b.fix):
        if how == 'cbnz28':
            b.w[i] = A.cbnz(28, b.addr(i), b.addr(labels[lab]))
        elif how == 'b':
            b.w[i] = A.b(b.addr(i), b.addr(labels[lab]))
    b.fix = [f for f in b.fix if f[2] not in ('cbnz28', 'b')]
    return b.resolve(labels)


# ------------------------------------------------------------ walk height
# BUILD 610b. The walk step (x86 0x636C41) stores the model's height as a
# WHOLE unit: 0x6367B7 evaluates the triangle's plane at the TRUNCATED x, y
# (0x649F8A, integer divide) and 0x63761D stores `z << 12`. On a steep
# triangle the height therefore jumps by the slope every time x or y crosses
# a whole unit -- wcrimb_2's upper cable has slopes up to 10 (triangle 7),
# so Cloud hops 10 units (30 px at 720p) at a time: the "vibrating, hitting
# something" on the curved patch, and the camera follows the hops. The fix
# evaluates the SAME plane (same vertices, same cross product, same
# triangle) at the model's real 20.12 x, y, in 64-bit, and stores that.
# Falls back to the stock whole unit if nz is 0, the index is out of range,
# or the result is more than WALK_SANITY units from the stock one.
WALK_SITE = 0x9DED5C            # lsl w8, w8, #0xc  (x86 0x637620 shl ecx, 12)
WALK_STOCK = 0x53144D08
WALK_CONTEXT = {0x9DED58: 0xB9400008,        # ldr w8, [x0]      ([ebp-0x48])
                0x9DED60: 0xB90006C8}        # str w8, [x22, #4] (guest ecx)
WALK_CTX = 22
TRI_OFF = 0xCC16E8              # field_event_data[0].walkmesh triangle (u16)
WALK_TRIS = 0xCFF744            # -> triangle vertex array, 24 bytes each
WALK_SANITY = 64
WALK_ENV = 'SEVENTH_NX_WALKZ'


def walk_enabled(env=None):
    env = os.environ if env is None else env
    return env.get(WALK_ENV, '1').strip().lower() not in ('0', 'off', 'no',
                                                         'false')


def _smull(xd, wn, wm):
    return 0x9B207C00 | (wm << 16) | (wn << 5) | xd


def _smaddl(xd, wn, wm, xa):
    return 0x9B200000 | (wm << 16) | (xa << 10) | (wn << 5) | xd


def _smsubl(xd, wn, wm, xa):
    return 0x9B208000 | (wm << 16) | (xa << 10) | (wn << 5) | xd


def _sdiv64(xd, xn, xm):
    return 0x9AC00C00 | (xm << 16) | (xn << 5) | xd


def _sub64(xd, xn, xm):
    return 0xCB000000 | (xm << 16) | (xn << 5) | xd


def _lsl64(xd, xn, sh):
    return 0xD3400000 | (((64 - sh) % 64) << 16) | ((63 - sh) << 10) | (xn << 5) | xd


def _cmp64_imm(xn, imm):
    return 0xF100001F | (imm << 10) | (xn << 5)


def _cmn64_imm(xn, imm):
    return 0xB100001F | (imm << 10) | (xn << 5)


def _sxtw(xd, wn):
    return 0x93407C00 | (wn << 5) | xd


def _cbz64(rt, frm, to):
    return A.cbz64(rt, frm, to)


def walk_body(addr):
    """Replaces `lsl w8, w8, #12` (w8 = the stock whole-unit height).
    Returns w8 = the height in 20.12. The site is straight-line code between
    two translator calls: nothing volatile is live across it, so only
    x0-x18 may change here (x19-x30 are saved and restored).

    Registers: w19 stock z, w23 X, w24 Y (20.12), x25 triangle address,
    w26-28 v0, w20-22 a = v1 - v0; v2 is parked in the frame at sp+0x60.
    """
    b = _B(addr)
    ctx = WALK_CTX
    b.emit(A.stp64_pre(19, 20, 31, -0x70),
           _stp_off(21, 22, 0x10), _stp_off(23, 24, 0x20),
           _stp_off(25, 26, 0x30), _stp_off(27, 28, 0x40),
           _stp_off(29, 30, 0x50))
    b.emit(A.mov_reg(19, 8))                   # the stock whole unit
    # model id: [ebp+8] (movsx word)
    b.emit(A.ldr(0, ctx, 0x14), A.add_imm(0, 0, 8))
    b.bl(G2H)
    b.emit(A.ldrsh(20, 0, 0), _uxth(20, 20), _cmp_imm(20, MAX_MODELS))
    b.bcond_to('out', HS)
    # X = [ebp-0x50], Y = [ebp-0x4c] (the position just stored, 20.12)
    b.emit(A.ldr(0, ctx, 0x14), A.sub_imm(0, 0, 0x50))
    b.bl(G2H)
    b.emit(A.ldr(23, 0, 0))
    b.emit(A.ldr(0, ctx, 0x14), A.sub_imm(0, 0, 0x4C))
    b.bl(G2H)
    b.emit(A.ldr(24, 0, 0))
    # the model's triangle (u16 at 0xCC16E8 + id*0x88), updated by the
    # final 0x6367B7 call that produced the stock z
    b.emit(A.movz(9, EV_STRIDE), A.mul(20, 20, 9))
    _mov32(b.w, 10, TRI_OFF)
    b.emit(A.add_reg(0, 20, 10))
    b.bl(G2H)
    b.emit(A.ldrh(20, 0, 0))
    _mov32(b.w, 0, WALK_TRIS)
    b.bl(G2H)
    b.emit(A.ldr(25, 0, 0),                    # triangle array (guest)
           A.movz(9, 24), A.mul(20, 20, 9), A.add_reg(25, 25, 20))
    # v0 -> w26..w28, v1 -> w20..w22, v2 -> [sp+0x60..0x68]
    for voff, dst in ((0, (26, 27, 28)), (8, (20, 21, 22)),
                      (16, ('s60', 's64', 's68'))):
        for k, r in enumerate(dst):
            b.emit(A.add_imm(0, 25, voff + 2 * k))
            b.bl(G2H)
            if isinstance(r, int):
                b.emit(A.ldrsh(r, 0, 0))
            else:
                b.emit(A.ldrsh(9, 0, 0), A.str_(9, 31, 0x60 + 4 * k))
    b.emit(A.ldr(9, 31, 0x60), A.ldr(10, 31, 0x64), A.ldr(11, 31, 0x68))
    # b = v2 - v1  (w9..w11) ; a = v1 - v0  (w20..w22)
    b.emit(A.sub_reg(9, 9, 20), A.sub_reg(10, 10, 21), A.sub_reg(11, 11, 22),
           A.sub_reg(20, 20, 26), A.sub_reg(21, 21, 27),
           A.sub_reg(22, 22, 28))
    # 0x649F8A's normal, in 64-bit:
    #   nx = a.z*b.y - a.y*b.z ; ny = a.x*b.z - a.z*b.x ; nz = a.y*b.x - a.x*b.y
    b.emit(_smull(12, 22, 10), _smsubl(12, 21, 11, 12),
           _smull(13, 20, 11), _smsubl(13, 22, 9, 13),
           _smull(14, 21, 9), _smsubl(14, 20, 10, 14))
    b.fix.append((len(b.w), 'out', 'cbz14'))
    b.emit(0)
    # d = n . v0
    b.emit(_sxtw(16, 26), _smull(15, 26, 26))  # placeholder, replaced below
    b.w.pop(); b.w.pop()
    b.emit(_sxtw(16, 26), A.mul64(15, 12, 16),
           _sxtw(16, 27), _madd64(15, 13, 16, 15),
           _sxtw(16, 28), _madd64(15, 14, 16, 15))
    # Z = (d*4096 - nx*X - ny*Y) / nz   (trunc toward zero, as idiv)
    b.emit(_lsl64(15, 15, 12),
           _sxtw(16, 23), _msub64(15, 12, 16, 15),
           _sxtw(16, 24), _msub64(15, 13, 16, 15),
           _sdiv64(15, 15, 14))
    # sanity: |Z - z*4096| <= WALK_SANITY units, else stock
    b.emit(_sxtw(16, 19), _lsl64(16, 16, 12), _sub64(16, 15, 16),
           A.movz(17, WALK_SANITY), _lsl64(17, 17, 12),
           A.cmp_reg64(16, 17))
    b.bcond_to('out', GT)
    b.emit(_sub64(17, 31, 17), A.cmp_reg64(16, 17))
    b.bcond_to('out', LT)
    b.emit(A.mov_reg(8, 15))                   # the real height, 20.12
    b.fix.append((len(b.w), 'done', 'b'))
    b.emit(0)
    out = len(b.w)
    b.emit(A.lsl(8, 19, 12))                   # stock: z << 12
    done = len(b.w)
    b.emit(_ldp_off(29, 30, 0x50), _ldp_off(27, 28, 0x40),
           _ldp_off(25, 26, 0x30), _ldp_off(23, 24, 0x20),
           _ldp_off(21, 22, 0x10), A.ldp64_post(19, 20, 31, 0x70),
           A.ret())
    labels = {'out': out, 'done': done}
    for i, lab, how in list(b.fix):
        if how == 'cbz14':
            b.w[i] = A.cbz64(14, b.addr(i), b.addr(labels[lab]))
        elif how == 'b':
            b.w[i] = A.b(b.addr(i), b.addr(labels[lab]))
    b.fix = [f for f in b.fix if f[2] not in ('cbz14', 'b')]
    return b.resolve(labels)


def _madd64(xd, xn, xm, xa):
    return 0x9B000000 | (xm << 16) | (xa << 10) | (xn << 5) | xd


def _msub64(xd, xn, xm, xa):
    return 0x9B008000 | (xm << 16) | (xa << 10) | (xn << 5) | xd


# ------------------------------------------------------------ wall facing
# BUILD 610c. Rubbing a wall with the stick at a slight angle made Cloud
# vibrate (md0, hardware video). The player branch of
# field_update_models_positions (x86 0x634B3E..0x634BBD):
#
#     dir = keys -> 8-way + control direction (the 360 cave's nudge) + [a5]
#     [model+0x36] = dir                        0x634B77
#     call 0x636C41                             the walk step
#     if (![model+0x37]) [model+0x38] = [model+0x36]   0x634BB7  <- FACING
#
# and the walk step, when a side probe (+/-45 deg at the collision radius)
# touches the walkmesh edge, ROTATES [model+0x36] by 8 (11 deg) and retries.
# Along a wall at a slight angle it alternates: blocked -> rotate away ->
# next tick clear -> straight at the wall -> blocked ... so the facing copied
# from the rotated value flips 11-22 deg every tick (30 Hz at 60 fps). With
# the d-pad the eight directions line up with the walls and it never starts.
#
# Fix: the facing is the direction the player is pushing. 0x634B77's store
# also parks dir in the function's own unused frame slot [ebp-0x30] (the x86
# body never touches -0x30, -0x2C, -0x28, -0x18, -0xC or -8, and takes no
# frame address), and 0x634BB7 reads it back instead of the rotated byte --
# and writes it back to [model+0x36], so direction and facing agree. The walk
# step, its probes, its rotation and therefore the path are untouched.
FACE_STORE = 0x9D81AC           # strb w28, [x0]   (x86 0x634B77)
FACE_STORE_STOCK = 0x3900001C
FACE_LOAD = 0x9D829C            # ldrb w27, [x0]   (x86 0x634BB7)
FACE_LOAD_STOCK = 0x3940001B
FACE_CONTEXT = {0x9D81A8: None,              # bl g2h (checked by target)
                0x9D81B0: 0xB9401728,        # ldr w8, [x25, #0x14]
                0x9D8298: 0xB9400B28,        # ldr w8, [x25, #8]
                0x9D82A0: 0x0B1A0108}        # add w8, w8, w26
FACE_CTX = 25
FACE_SLOT = 0x30                # [ebp-0x30]
FACE_ENV = 'SEVENTH_NX_WALLFACE'


def face_enabled(env=None):
    env = os.environ if env is None else env
    return env.get(FACE_ENV, '1').strip().lower() not in ('0', 'off', 'no',
                                                         'false')


def face_store_body(addr):
    """Replaces `strb w28, [x0]`: the stock store, then [ebp-0x30] = w28.
    After the site only x0 and x8 are written before use; x28 is kept."""
    b = _B(addr)
    b.emit(FACE_STORE_STOCK,
           A.stp64_pre(29, 30, 31, -16),
           A.ldr(0, FACE_CTX, 0x14), A.sub_imm(0, 0, FACE_SLOT))
    b.bl(G2H)
    b.emit(A.strb(28, 0, 0),
           A.ldp64_post(29, 30, 31, 16), A.ret())
    return b.resolve({})


def face_load_body(addr):
    """Replaces `ldrb w27, [x0]` (x0 -> [model+0x36]): w27 = [ebp-0x30], and
    [model+0x36] = w27. w8 is live after the site and is preserved."""
    b = _B(addr)
    b.emit(A.stp64_pre(29, 30, 31, -32),
           0xA90103E8)                         # stp x8, x0, [sp, #16]
    b.emit(A.ldr(0, FACE_CTX, 0x14), A.sub_imm(0, 0, FACE_SLOT))
    b.bl(G2H)
    b.emit(A.ldrb(27, 0, 0),
           0xA94103E8,                         # ldp x8, x0, [sp, #16]
           A.strb(27, 0, 0),                   # direction = facing
           A.ldp64_post(29, 30, 31, 32), A.ret())
    return b.resolve({})


def face_state(text):
    a, b2 = _word(text, FACE_STORE), _word(text, FACE_LOAD)
    if a == FACE_STORE_STOCK and b2 == FACE_LOAD_STOCK:
        return 'stock'
    if _bl_target(FACE_STORE, a) and _bl_target(FACE_LOAD, b2):
        return 'on'
    return 'unknown'


def _face_context_ok(text):
    for va, want in FACE_CONTEXT.items():
        w = _word(text, va)
        if want is None:
            if _bl_target(va, w) != G2H:
                return False
        elif w != want:
            return False
    return True


# ------------------------------------------------------------------ state
def _word(text, va):
    return struct.unpack_from('<I', text, va)[0]


def _bl_target(va, w):
    if (w >> 26) != 0x25:
        return None
    imm = w & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return va + 4 * imm


def _sites():
    return [(CONV_SITE, CONV)] + [(s[0], PROJ) for s in FOLLOW_SITES]


def _sub_imm_of(w):
    """(rd, rn, imm) of `sub wD, wN, #imm`, else None."""
    if (w & 0xFFC00000) != 0x51000000:
        return None
    return w & 0x1F, (w >> 5) & 0x1F, (w >> 10) & 0xFFF


def sites_proven(text):
    """Every call site pushes the input vector the x86 call does."""
    bad = []
    for va, (at, off) in SITE_PROOF.items():
        got = _sub_imm_of(_word(text, at))
        if got is None or got[1] != 9 or got[2] != off:
            bad.append('+0x%X: expected sub wN, w9, #0x%X at +0x%X, found %08X'
                       % (va, off, at, _word(text, at)))
    return bad


def read_state(text):
    """'stock', 'on' or 'unknown'."""
    got = [_bl_target(va, _word(text, va)) for va, _t in _sites()]
    if all(g == t for g, (_va, t) in zip(got, _sites())):
        return 'stock'
    if all(g is not None and g != t for g, (_va, t) in zip(got, _sites())):
        return 'on'
    return 'unknown'


def walk_state(text):
    w = _word(text, WALK_SITE)
    if w == WALK_STOCK:
        return 'stock'
    return 'on' if _bl_target(WALK_SITE, w) is not None else 'unknown'


def _walk_context_ok(text):
    return all(_word(text, va) == want for va, want in WALK_CONTEXT.items())


def build_patches(text, lo, hi, camera=True, walk=True, face=False):
    """{va: word} for the caves and their hook words."""
    pool = DS.Bump(lo, hi)
    words, entries = {}, {}
    if camera:
        entries['conv'] = pool.put(lambda _e, addr: conv_body(addr))
        words[CONV_SITE] = A.bl(CONV_SITE, entries['conv'])
        for va, x86, guest, width, kind, _what in FOLLOW_SITES:
            e = pool.put(lambda _e, addr, g=guest, w=width, k=kind:
                         tag_body(addr, g, w, k))
            entries[x86] = e
            words[va] = A.bl(va, e)
    if walk:
        entries['walk'] = pool.put(lambda _e, addr: walk_body(addr))
        words[WALK_SITE] = A.bl(WALK_SITE, entries['walk'])
    if face:
        entries['face_store'] = pool.put(lambda _e, addr: face_store_body(addr))
        words[FACE_STORE] = A.bl(FACE_STORE, entries['face_store'])
        entries['face_load'] = pool.put(lambda _e, addr: face_load_body(addr))
        words[FACE_LOAD] = A.bl(FACE_LOAD, entries['face_load'])
    words.update(pool.placed)
    n = len(pool.placed)
    return words, entries, n


def apply_to_nso(src, dest, stock, camera=None, walk=None, face=None):
    camera = enabled() if camera is None else camera
    walk = walk_enabled() if walk is None else walk
    face = face_enabled() if face is None else face
    if not (camera or walk or face):
        raise ValueError('nothing to install')
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    if camera:
        state = read_state(text)
        if state != 'stock':
            raise ValueError('camera sites are %s, not stock (%s)' % (
                state, ', '.join('+0x%X %08X' % (va, _word(text, va))
                                 for va, _t in _sites())))
        bad = sites_proven(text)
        if bad:
            raise ValueError('call sites not proven: ' + '; '.join(bad))
    if walk:
        if walk_state(text) != 'stock' or not _walk_context_ok(text):
            raise ValueError('walk height site +0x%X is %08X, not the stock '
                             'lsl w8, w8, #12 between ldr w8, [x0] and '
                             'str w8, [x22, #4]'
                             % (WALK_SITE, _word(text, WALK_SITE)))
    if face:
        if face_state(text) != 'stock' or not _face_context_ok(text):
            raise ValueError('wall-facing sites +0x%X / +0x%X are %08X / %08X,'
                             ' not the stock player-branch store and load'
                             % (FACE_STORE, FACE_LOAD,
                                _word(text, FACE_STORE),
                                _word(text, FACE_LOAD)))
    lo, hi = DS.part(src, 'campos', stock)
    words, entries, n = build_patches(text, lo, hi, camera, walk, face)
    for va, w in words.items():
        struct.pack_into('<I', text, va, w)
    out = AC.pack(blob, [bytes(text), raw[1], raw[2]], 0)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'entries': entries, 'words': n, 'camera': camera, 'walk': walk,
            'face': face}
