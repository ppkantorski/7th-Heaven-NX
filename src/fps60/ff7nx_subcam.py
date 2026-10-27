#!/usr/bin/env python3
"""ff7nx_subcam.py -- scripted field camera moves in sub-unit steps, as on PC.

BUILD 670. mds7 on entry (hardware 10-14): "the field camera often moves in
sharper steps or rough steps ... is there a way to make the camera on the
fields move smoother during cutscenes?"

WHAT THE ENGINE DOES
====================
A scripted camera move (SCR2DL / SCR2DC, and SCRLC / SCRLA's scroll onto an
actor) is run by field_update_scripted_bg_movement (x86 0x643D22, ARM
+0x9F7DB0) once per frame:

    delta = interp(start, end, frames, step)        -> [0xCC15F0] x, [0xCC15F4] y

with interp the linear 0x6430A4 (start + (end - start) * step / frames,
truncated) or the smooth 0x6430F2 (a cosine ease whose angle is a BYTE, so it
only takes 128 values over the whole move). The delta is a whole unit. Every
field position follows it: the background layers' origin
(bg = 320 - offset - delta, tile x = mult * (320 - bg) + ...), the 3D world
position, the pointer hand and the exit arrows.

mds7's entry pan is SCR2DL over 200 frames (400 at 60 FPS) across about 100
units: a quarter of a unit a frame, so the picture stood still for three
frames and jumped 1.5 pixels on the fourth. The smooth moves also stand still
wherever the byte angle does not change.

FFNx (ff7/field/background.cpp) recomputes the same move in float
(field_get_linear/smooth_interpolated_value_float) and positions the layers
and the 3D world with that float. That is the PC look.

WHAT THIS DOES
==============
  capture  The seven ARM calls of the two interpolators inside 0x643D22 go
           through one cave that reads the call's four guest arguments,
           computes the same move in float (linear: t = step / frames;
           smooth: sin^2(t * pi / 2), which is (1 - cos(pi t)) / 2, the curve
           the byte table samples), keeps it for its axis, then calls the
           real interpolator: the game's own delta is unchanged.
  ON       At field_draw_everything's entry (after the frame's update):
           shift = (float - [0xCC15F0/4]) * mult per axis, in the units the
           field draws in, the position snapped to tenths of a unit as FFNx
           does (MIN_STEP: no cracks between tiles) -- only while the delta is still the value the
           interpolator returned (the camera range clamp, ff7nx_camclamp,
           may move it after) and the shift is under 8 units. No move this
           frame: no shift. Then the move is forgotten until the next update.
  DRAW     Every draw of the field scene (the one join every draw passes,
           +0x10DA270, after ff7nx_fieldzoom's zoom and ff7nx_daynight's
           tint) adds the shift in clip space: clip.x += tx * clip.w,
           clip.y += ty * clip.w, so a flat background draw and a 3D model
           move by the same number of pixels. tx/ty come from the scene's
           first flat (orthographic) draw: t = m[0] * shift_x, m[5] * shift_y.
  OFF      Just before the field's fade quads (the gray quads, x86 0x644E90)
           -- fades and the dialogue windows after them are never moved.
  DEFERRED Alpha-blended groups are drawn at the end of the frame, after the
           fades and the dialogue windows (ff7nx_daynight, BUILD 652). The
           ones queued inside the scene (field_78, +0x10D8DE4) are noted in a
           ring; draw_deferred (+0x10DA704) draws those with the shift too, so
           a glow or a see-through tile stays on the art it belongs to.

Layers 3/4 (parallax) move with speed/256 of the camera, not the whole
amount. ff7nx_subparallax's tile caves, given this module's state, take the
difference back: (speed - 256) / 256 of the shift.

The game's integer positions are untouched (triggers, walkmesh, culling).
The shift is under a unit for a linear move; a smooth move's byte angle can
leave the integer a few units behind the curve mid-move (about 1.2 % of the
move's length), and the shift is capped at 8 units, the port's culling
margin.

SEVENTH_NX_SUBCAM=0 leaves the module stock.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import math
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_deadspace as DS

ENV = 'SEVENTH_NX_SUBCAM'
VERSION = 'subcam-670b'

TRANSLATE = 0x10FC3A0
CTX_PTR = 0x12CE2B0
MODE_SLOT = 0x12CE1F8              # -> the port's engine mode (u32), 2 = field
MODE_FIELD = 2
DELTA_X = 0xCC15F0                 # field_curr_delta_world_pos_x (s16)
DELTA_Y = 0xCC15F4
MULT = 0xCFF1F0                    # field_bg_multiplier (int)
MAX_SHIFT = 8.0                    # units: never more (the culling margins)

LIN_ARM = 0x9DC090                 # x86 0x6430A4
SMOOTH_ARM = 0x9DC340              # x86 0x6430F2
# (ARM call site, axis 0 x / 1 y, smooth) -- the result is stored to
# w19 + 4 (0xCC15F0) or w19 + 8 (0xCC15F4) right after each (checked)
CAPTURE = ((0x9F7F8C, 0, 0), (0x9F8048, 1, 0), (0x9F81A4, 0, 0),
           (0x9F8268, 1, 0), (0x9F842C, 0, 1), (0x9F8608, 0, 1),
           (0x9F86C4, 1, 1))
FUNC_LO, FUNC_HI = 0x9F7DB0, 0x9F8790

ON_SITE = 0x9E6E8C                 # stp x20, x19, [sp, #0x30] (prologue)
ON_WORD = 0xA9034FF4
OFF_SITE = 0x9E7EE4                # sub w8, w8, #4   (before bl gray quads)
OFF_WORD = 0x51001108
GRAY_CALL = 0x9E7EEC               # bl x86 0x644E90
DRAW_SITE = 0x10DA270              # add x3, sp, #0x14
DRAW_WORD = 0x910053E3
DRAW_CALL = 0x10DA274              # bl +0x570 (copies the block)
MATRIX_SP = 0x28                   # BlockVertex.projectionMatrix on the stack

# state (BSS)
S_FLAG, S_VALID, S_F, S_SHIFT, S_Q, S_T, S_TSET, S_I = (
    0x00, 0x04, 0x0C, 0x14, 0x1C, 0x24, 0x2C, 0x30)
S_ACTIVE, S_DEF, S_HEAD, S_CUR, S_RING = 0x38, 0x3C, 0x40, 0x44, 0x50
RING = 512                         # deferred entries queued in one frame's scene
STATE_BYTES = S_RING + 4 * RING

# BUILD 670: alpha-blended groups are queued (gfx_drv field_78) and drawn at
# the END of the frame (draw_deferred), after the fade quads and the dialogue
# windows (ff7nx_daynight BUILD 652). The ones queued inside the scene are
# noted here and drawn with the shift; the others are not.
ALLOC_SITE = 0x10D8DE4             # stp w8, w24, [x20, #4]  (x20 = the new entry)
ALLOC_WORD = 0x2900E288
DEFER_SITE = 0x10DA704             # ldr w0, [x20, #0x50]    (x21 = the entry)
DEFER_WORD = 0xB9405280


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def _f(v):
    return struct.unpack('<I', struct.pack('<f', v))[0]


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


# ---------------------------------------------------------- extra encodings
def scvtf_s(rd, rn):                       # SCVTF Sd, Wn
    return 0x1E220000 | (rn << 5) | rd


def scvtf_fix(rd, rn, fbits):              # SCVTF Sd, Wn, #fbits
    return 0x1E020000 | ((64 - fbits) << 10) | (rn << 5) | rd


def fcvtzs_w(rd, rn):                      # FCVTZS Wd, Sn
    return 0x1E380000 | (rn << 5) | rd


def fabs_s(rd, rn):
    return 0x1E20C000 | (rn << 5) | rd


def fmadd_s(rd, rn, rm, ra):               # Sd = Sa + Sn * Sm
    return 0x1F000000 | (rm << 16) | (ra << 10) | (rn << 5) | rd


def frintn_s(rd, rn):                      # FRINTN Sd, Sn (to nearest, ties even)
    return 0x1E244000 | (rn << 5) | rd


def fcmp_zero(rn):
    return 0x1E202008 | (rn << 5)


def orr_reg(rd, rn, rm):
    return 0x2A000000 | (rm << 16) | (rn << 5) | rd


NE, GT, LE, GE = 1, 0xC, 0xD, 0xA


def _movw(e, rd, v):
    v &= 0xFFFFFFFF
    e(A.movz(rd, v & 0xFFFF))
    if v >> 16:
        e(A.movk_hi(rd, v >> 16))


def _fconst(e, sd, value, tmp=13):
    _movw(e, tmp, _f(value))
    e(A.fmov_s_from_w(sd, tmp))


def _bss(e, a, rd, state):
    e(A.adrp(rd, a.pc(), state & ~0xFFF))
    e(A.add_imm64(rd, rd, state & 0xFFF))


# ------------------------------------------------------------------ caves
def capture_stub(at, axis, smooth, common):
    a = AC.Asm(at, lambda i: at + 4 * i)
    a.emit(A.movz(9, axis))
    a.emit(A.movz(10, smooth))
    a.emit(A.b(a.pc(), common))
    return a.resolve()


def capture_common(at, state):
    """Entered by `bl` from a call site (x30 = the site + 4) with w9 = axis,
    w10 = smooth. The caller already pushed the guest return slot; the four
    guest arguments are at esp + 4. Calls the real interpolator and returns
    to the site. Only caller-saved state is touched (x19..x22 are saved)."""
    a = AC.Asm(at, lambda i: at + 4 * i)
    e = a.emit
    e(A.stp64_pre(29, 30, 31, -0x30))
    e(A.stp64_off(19, 20, 31, 0x10))
    e(A.stp64_off(21, 22, 31, 0x20))
    e(A.mov_reg(20, 9))                              # axis
    e(A.mov_reg(21, 10))                             # smooth
    e(A.adrp(19, a.pc(), CTX_PTR & ~0xFFF))
    e(A.ldr64(19, 19, CTX_PTR & 0xFFF))
    e(A.ldr(0, 19, 0x10))                            # guest esp (return slot)
    e(A.add_imm(0, 0, 4))
    e(A.bl(a.pc(), TRANSLATE))
    e(A.ldr(9, 0, 0))                                # start
    e(A.ldr(10, 0, 4))                               # end
    e(A.ldr(11, 0, 8))                               # frames
    e(A.ldr(12, 0, 12))                              # step
    e(A.cmp_imm(11, 0))
    a.bcond('call', LE)                              # no frames: nothing to keep
    e(scvtf_s(0, 9))
    e(scvtf_s(1, 10))
    e(A.fsub_s(1, 1, 0))                             # d = end - start
    e(scvtf_s(2, 11))
    e(scvtf_s(3, 12))
    e(A.fdiv_s(3, 3, 2))                             # t = step / frames
    a.cbz(21, 'mix')
    # smooth: w = sin^2(t * pi/2) = (1 - cos(pi t)) / 2
    _fconst(e, 4, math.pi / 2)
    e(A.fmul_s(4, 3, 4))                             # x
    e(A.fmul_s(5, 4, 4))                             # x^2
    _fconst(e, 6, 1.0 / 362880)
    _fconst(e, 7, -1.0 / 5040)
    e(fmadd_s(6, 6, 5, 7))                           # c9 x2 + c7
    _fconst(e, 7, 1.0 / 120)
    e(fmadd_s(6, 6, 5, 7))                           # (..) x2 + c5
    _fconst(e, 7, -1.0 / 6)
    e(fmadd_s(6, 6, 5, 7))                           # (..) x2 + c3
    _fconst(e, 7, 1.0)
    e(fmadd_s(6, 6, 5, 7))                           # (..) x2 + 1
    e(A.fmul_s(6, 6, 4))                             # sin x
    e(A.fmul_s(3, 6, 6))                             # w
    a.label('mix')
    e(fmadd_s(0, 1, 3, 0))                           # v = start + d * w
    _bss(e, a, 9, state)
    e(A.lsl(11, 20, 2))
    e(A.add_reg64(10, 9, 11))
    e(A.str_s(0, 10, S_F))
    e(A.movz(11, 1))
    e(A.str_(11, 10, S_VALID))
    a.label('call')
    a.cbz(21, 'lin')
    e(A.bl(a.pc(), SMOOTH_ARM))
    a.b('got')
    a.label('lin')
    e(A.bl(a.pc(), LIN_ARM))
    a.label('got')
    # the interpolator's own result (guest eax, stored as a s16): the ON cave
    # keeps the shift only while the delta is still this value -- a camera
    # range clamp after the move (ff7nx_camclamp) changes it
    e(A.ldr(9, 19, 0))
    e(A.lsl(9, 9, 16))
    e(A.asr(9, 9, 16))
    _bss(e, a, 10, state)
    e(A.lsl(11, 20, 2))
    e(A.add_reg64(10, 10, 11))
    e(A.str_(9, 10, S_I))
    e(A.ldp64_off(21, 22, 31, 0x20))
    e(A.ldp64_off(19, 20, 31, 0x10))
    e(A.ldp64_post(29, 30, 31, 0x30))
    e(A.ret())
    return a.resolve()


def _save(e):
    e(A.sub_imm64(31, 31, 0xc0))
    for r in range(0, 18, 2):
        e(A.stp64_off(r, r + 1, 31, 8 * r))
    e(A.stp64_off(18, 30, 31, 144))
    e(A.mrs_nzcv(1))
    e(A.str64(1, 31, 0xa0))


def _restore(e):
    e(A.ldr64(1, 31, 0xa0))
    e(A.msr_nzcv(1))
    for r in range(0, 18, 2):
        e(A.ldp64_off(r, r + 1, 31, 8 * r))
    e(A.ldp64_off(18, 30, 31, 144))
    e(A.add_imm64(31, 31, 0xc0))


def on_cave(at, state):
    """field_draw_everything's entry: the frame's shift from the kept float
    and the delta the frame is drawn with. Every caller-saved register, x30
    and the flags are kept; the displaced prologue word runs last."""
    a = AC.Asm(at, lambda i: at + 4 * i)
    e = a.emit
    _save(e)
    e(A.str_(31, 31, 0xa8))                          # mult = 0 until read
    _bss(e, a, 9, state)
    e(A.ldr(10, 9, S_VALID))
    e(A.ldr(11, 9, S_VALID + 4))
    e(orr_reg(10, 10, 11))
    a.cbz(10, 'none')
    _movw(e, 0, MULT)
    e(A.bl(a.pc(), TRANSLATE))
    e(A.ldr(8, 0, 0))
    e(A.str_(8, 31, 0xa8))
    for axis, delta in ((0, DELTA_X), (1, DELTA_Y)):
        lab = 'ax%d' % axis
        _bss(e, a, 9, state)
        e(A.str_(31, 9, S_SHIFT + 4 * axis))            # 0 unless kept below
        e(A.str_(31, 9, S_Q + 4 * axis))
        e(A.ldr(10, 9, S_VALID + 4 * axis))
        a.cbz(10, lab)
        _movw(e, 0, delta)
        e(A.bl(a.pc(), TRANSLATE))
        e(A.ldrsh(8, 0, 0))
        _bss(e, a, 9, state)
        e(A.ldr(10, 9, S_I + 4 * axis))
        e(A.cmp_reg(8, 10))
        a.bcond(lab, NE)                             # clamped after the move
        e(scvtf_s(0, 8))
        e(A.ldr_s(1, 9, S_F + 4 * axis))
        e(A.fsub_s(1, 1, 0))                         # float - delta
        e(fabs_s(2, 1))
        _fconst(e, 3, MAX_SHIFT)
        e(A.fcmp_s(2, 3))
        a.bcond(lab, GE)                             # clamped: no shift
        # FFNx's MIN_STEP: the position in tenths of a unit. A tile edge then
        # only lands on a pixel centre where a whole unit put it before (an
        # unsnapped edge a hair off a centre leaves a 1-px crack between two
        # tiles -- 670's grid of lines on single frames, hardware)
        _fconst(e, 3, 10.0)
        e(A.fmul_s(1, 1, 3))
        e(frintn_s(1, 1))
        e(A.fdiv_s(1, 1, 3))
        e(A.ldr(8, 31, 0xa8))
        e(scvtf_s(3, 8))
        e(A.fmul_s(1, 1, 3))                         # * mult: draw units
        e(A.str_s(1, 9, S_SHIFT + 4 * axis))
        _fconst(e, 3, 65536.0)
        e(A.fmul_s(2, 1, 3))
        e(fcvtzs_w(8, 2))
        e(A.str_(8, 9, S_Q + 4 * axis))
        a.label(lab)
    _bss(e, a, 9, state)
    e(A.ldr(10, 9, S_SHIFT))
    e(A.ldr(11, 9, S_SHIFT + 4))
    e(orr_reg(10, 10, 11))
    e(A.lsl(10, 10, 1))                              # +0.0 / -0.0 -> 0
    e(A.cmp_imm(10, 0))
    e(A.csinc(10, 31, 31, 0))                        # flag = shift != 0
    e(A.str_(10, 9, S_FLAG))
    e(A.str_(10, 9, S_ACTIVE))
    e(A.str_(31, 9, S_TSET))
    e(A.str_(31, 9, S_VALID))
    e(A.str_(31, 9, S_VALID + 4))
    e(A.str_(31, 9, S_DEF))
    e(A.str_(31, 9, S_HEAD))
    e(A.str_(31, 9, S_CUR))
    a.cbz(10, 'done')
    e(A.add_imm64(10, 9, S_RING))                    # a fresh ring
    e(A.movz(11, RING))
    a.label('wipe')
    e(A.str_(31, 10, 0))
    e(A.add_imm64(10, 10, 4))
    e(A.sub_imm(11, 11, 1))
    a.cbnz(11, 'wipe')
    a.b('done')
    a.label('none')
    for off in (S_FLAG, S_SHIFT, S_SHIFT + 4, S_Q, S_Q + 4, S_TSET,
                S_ACTIVE, S_DEF, S_HEAD, S_CUR):
        e(A.str_(31, 9, off))
    a.label('done')
    _restore(e)
    e(ON_WORD)
    e(A.b(a.pc(), ON_SITE + 4))
    return a.resolve()


def off_cave(at, state):
    """flag = 0 before the fade quads. x9 is dead (the next two words are a
    store of w8 and the `bl`).

    BUILD 707: S_DEF = 0 too. The field flushes its deferred list INSIDE the
    scene (x86 0x63A838, 0x666DF4 -> draw_deferred per entry), so the last
    scene entry's S_DEF = 1 outlived the flush, the gray quads and OFF: every
    immediate draw after the scene -- the dialogue windows and the countdown
    timer -- was moved by the camera's fraction until the next deferred
    entry or the next frame (the timer wobbling as Cloud runs, hardware)."""
    a = AC.Asm(at, lambda i: at + 4 * i)
    e = a.emit
    _bss(e, a, 9, state)
    e(A.str_(31, 9, S_FLAG))
    e(A.str_(31, 9, S_DEF))                          # BUILD 707: see below
    e(OFF_WORD)
    e(A.b(a.pc(), OFF_SITE + 4))
    return a.resolve()


def draw_cave(at, state):
    """At the join, before `add x3, sp, #0x14`: x0..x2 are the next call's
    arguments and are not touched; x9..x12, s16..s23 and the flags are dead
    (the `bl` one word later clobbers them, nothing reads them before)."""
    a = AC.Asm(at, lambda i: at + 4 * i)
    e = a.emit
    M = lambda i: MATRIX_SP + 4 * i                  # noqa: E731
    _bss(e, a, 9, state)
    e(A.ldr(10, 9, S_FLAG))
    e(A.ldr(11, 9, S_DEF))
    e(orr_reg(10, 10, 11))                           # the scene, or one of its
    a.cbz(10, 'out')                                 # deferred groups
    e(A.adrp(10, a.pc(), MODE_SLOT & ~0xFFF))
    e(A.ldr64(10, 10, MODE_SLOT & 0xFFF))
    e(A.ldr(10, 10, 0))
    e(A.cmp_imm(10, MODE_FIELD))
    a.bcond('out', NE)
    e(A.ldr(10, 9, S_TSET))
    a.cbnz(10, 'apply')
    # the scene's first flat draw sets the clip shift
    for i in (3, 7, 11):
        e(A.ldr_s(16, 31, M(i)))
        e(fcmp_zero(16))
        a.bcond('apply', NE)
    e(A.ldr_s(16, 31, M(15)))
    e(A.fmov_s_imm(17, 1.0))
    e(A.fcmp_s(16, 17))
    a.bcond('apply', NE)
    e(A.ldr_s(16, 31, M(0)))
    e(A.ldr_s(17, 9, S_SHIFT))
    e(A.fmul_s(16, 16, 17))
    e(A.str_s(16, 9, S_T))
    e(A.ldr_s(16, 31, M(5)))
    e(A.ldr_s(17, 9, S_SHIFT + 4))
    e(A.fmul_s(16, 16, 17))
    e(A.str_s(16, 9, S_T + 4))
    e(A.movz(10, 1))
    e(A.str_(10, 9, S_TSET))
    a.label('apply')
    e(A.ldr(10, 9, S_TSET))
    a.cbz(10, 'out')
    e(A.ldr_s(20, 9, S_T))
    e(A.ldr_s(22, 9, S_T + 4))
    for j in range(4):
        e(A.ldr_s(16, 31, M(4 * j + 3)))             # row w
        e(A.ldr_s(17, 31, M(4 * j)))
        e(fmadd_s(17, 20, 16, 17))                   # row x += tx * row w
        e(A.str_s(17, 31, M(4 * j)))
        e(A.ldr_s(17, 31, M(4 * j + 1)))
        e(fmadd_s(17, 22, 16, 17))                   # row y += ty * row w
        e(A.str_s(17, 31, M(4 * j + 1)))
    a.label('out')
    e(DRAW_WORD)
    e(A.b(a.pc(), DRAW_SITE + 4))
    return a.resolve()


def alloc_cave(at, state):
    """field_78 has a new deferred entry (x20, host): inside the scene, note
    its address (low 32 bits) in the ring. Outside it (BUILD 707), forget
    the address wherever the ring has it: the in-scene flush frees the list
    and the entries queued after the scene (windows, the countdown timer)
    are given the scene's freed addresses again -- found in the ring, they
    were drawn with the camera shift. x9..x12 are pushed and popped;
    nothing here sets the flags."""
    a = AC.Asm(at, lambda i: at + 4 * i)
    e = a.emit
    e(A.stp64_pre(9, 10, 31, -16))
    e(A.stp64_pre(11, 12, 31, -16))
    _bss(e, a, 9, state)
    e(A.ldr(10, 9, S_FLAG))
    a.cbz(10, 'after')
    e(A.ldr(10, 9, S_HEAD))
    e(A.and_mask(11, 10, 9))                         # % 512
    e(A.add_imm(10, 10, 1))
    e(A.str_(10, 9, S_HEAD))
    e(A.lsl(11, 11, 2))
    e(A.add_reg64(11, 9, 11))
    e(A.str_(20, 11, S_RING))
    a.b('done')
    a.label('after')
    e(A.ldr(10, 9, S_ACTIVE))
    a.cbz(10, 'done')
    e(A.add_imm64(10, 9, S_RING))
    e(A.movz(12, RING))
    a.label('scrub')
    e(A.ldr(11, 10, 0))
    e(A.sub_reg(11, 11, 20))                         # low 32 bits of x20
    a.cbnz(11, 'next')
    e(A.str_(31, 10, 0))
    a.label('next')
    e(A.add_imm64(10, 10, 4))
    e(A.sub_imm(12, 12, 1))
    a.cbnz(12, 'scrub')
    a.label('done')
    e(A.ldp64_post(11, 12, 31, 16))
    e(A.ldp64_post(9, 10, 31, 16))
    e(ALLOC_WORD)
    e(A.b(a.pc(), ALLOC_SITE + 4))
    return a.resolve()


def defer_cave(at, state):
    """draw_deferred is about to draw entry x21 (host): S_DEF = it was queued
    inside a moving scene. The search starts after the last match (entries
    are drawn in the order they were queued: one step each). x9..x14 are
    pushed and popped; nothing here sets the flags."""
    a = AC.Asm(at, lambda i: at + 4 * i)
    e = a.emit
    e(A.stp64_pre(9, 10, 31, -16))
    e(A.stp64_pre(11, 12, 31, -16))
    e(A.stp64_pre(13, 14, 31, -16))
    _bss(e, a, 9, state)
    e(A.str_(31, 9, S_DEF))
    e(A.ldr(10, 9, S_ACTIVE))
    a.cbz(10, 'done')
    e(A.ldr(12, 9, S_CUR))
    e(A.movz(13, RING))
    a.label('look')
    e(A.and_mask(12, 12, 9))
    e(A.lsl(11, 12, 2))
    e(A.add_reg64(11, 9, 11))
    e(A.ldr(11, 11, S_RING))
    e(A.sub_reg(11, 11, 21))                         # low 32 bits of x21
    a.cbz(11, 'hit')
    e(A.add_imm(12, 12, 1))
    e(A.sub_imm(13, 13, 1))
    a.cbnz(13, 'look')
    a.b('done')
    a.label('hit')
    e(A.movz(11, 1))
    e(A.str_(11, 9, S_DEF))
    e(A.add_imm(12, 12, 1))
    e(A.str_(12, 9, S_CUR))
    a.label('done')
    e(A.ldp64_post(13, 14, 31, 16))
    e(A.ldp64_post(11, 12, 31, 16))
    e(A.ldp64_post(9, 10, 31, 16))
    e(DEFER_WORD)
    e(A.b(a.pc(), DEFER_SITE + 4))
    return a.resolve()


# ------------------------------------------------------------------ checks
def check_sites(img):
    bad = []
    for site, axis, smooth in CAPTURE:
        want = A.bl(site, SMOOTH_ARM if smooth else LIN_ARM)
        if _w(img, site) != want:
            bad.append('+0x%X is %08X, not bl %s' % (site, _w(img, site),
                                                     'smooth' if smooth else 'linear'))
            continue
        # the result goes to [w19 + 4 + 4 * axis] (0xCC15F0 / 0xCC15F4)
        store = A.add_imm(0, 19, 4 + 4 * axis)
        nxt = site + 4
        if (_w(img, nxt) >> 26) == 0x05:             # b (shared tail)
            w = _w(img, nxt)
            nxt = nxt + 4 * (((w & 0x3FFFFFF) ^ 0x2000000) - 0x2000000)
        if not any(_w(img, nxt + 4 * k) == store for k in range(8)):
            bad.append('+0x%X: no store to w19+%d after it' % (site, 4 + 4 * axis))
    if _w(img, FUNC_LO + 0x1C) != A.movz(19, 0x15EC) or \
            _w(img, FUNC_LO + 0x20) != A.movk_hi(19, 0xCC):
        bad.append('0x643D22: w19 is not 0xCC15EC')
    for va, want, what in ((ON_SITE, ON_WORD, 'field_draw_everything prologue'),
                           (OFF_SITE, OFF_WORD, 'before the gray quads'),
                           (DRAW_SITE, DRAW_WORD, 'draw join'),
                           (ALLOC_SITE, ALLOC_WORD, 'field_78 new deferred entry'),
                           (DEFER_SITE, DEFER_WORD, 'draw_deferred, entry in x21')):
        if _w(img, va) != want:
            bad.append('+0x%X is %08X, not %08X (%s)' % (va, _w(img, va), want, what))
    for va, what in ((GRAY_CALL, 'the gray quads'), (DRAW_CALL, 'the block copy')):
        if (_w(img, va) >> 26) != 0x25:
            bad.append('+0x%X is not the bl to %s' % (va, what))
    return bad


def apply_to_nso(src, dest, stock=None, log=lambda *_: None, region=None):
    import nxmap
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    img = nxmap.Main(src).img
    bad = check_sites(img)
    if bad:
        raise ValueError('subcam: ' + '; '.join(bad))
    lo, hi = region if region else DS.part(src, 'subcam', stock)
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = (state - base) + STATE_BYTES + AC.bss_tail_slack(segs)
    at = (lo + 15) & ~15
    placed = {}

    def put(builder):
        nonlocal at
        words = builder(at)
        entry = at
        for i, x in enumerate(words):
            placed[entry + 4 * i] = x
        at = (entry + 4 * len(words) + 15) & ~15
        return entry
    common = put(lambda p: capture_common(p, state))
    stubs = {}
    for site, axis, smooth in CAPTURE:
        stubs[site] = put(lambda p, ax=axis, sm=smooth: capture_stub(p, ax, sm, common))
    on = put(lambda p: on_cave(p, state))
    off = put(lambda p: off_cave(p, state))
    draw = put(lambda p: draw_cave(p, state))
    alloc = put(lambda p: alloc_cave(p, state))
    defer = put(lambda p: defer_cave(p, state))
    if at > hi:
        raise ValueError('subcam: %d bytes do not fit the %d-byte part' % (at - lo, hi - lo))
    for va, x in placed.items():
        struct.pack_into('<I', text, va, x)
    for site, _ax, _sm in CAPTURE:
        struct.pack_into('<I', text, site, A.bl(site, stubs[site]))
    struct.pack_into('<I', text, ON_SITE, A.b(ON_SITE, on))
    struct.pack_into('<I', text, OFF_SITE, A.b(OFF_SITE, off))
    struct.pack_into('<I', text, DRAW_SITE, A.b(DRAW_SITE, draw))
    struct.pack_into('<I', text, ALLOC_SITE, A.b(ALLOC_SITE, alloc))
    struct.pack_into('<I', text, DEFER_SITE, A.b(DEFER_SITE, defer))
    raw[0] = bytes(text)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(AC.pack(blob, raw, growth))
    log('  sub-unit camera: scripted moves (SCR2DL/SCR2DC/SCRLC/SCRLA) drawn '
        'at their float position; caves +0x%X..+0x%X, state +0x%X (%s=0 '
        'disables)' % (lo, at, state, ENV))
    return {'state': state, 'bytes': at - lo, 'common': common, 'on': on,
            'off': off, 'draw': draw, 'alloc': alloc, 'defer': defer,
            'stubs': stubs}


def state_of(img):
    """The state address of an installed module (for ff7nx_subparallax),
    read back from the draw cave's first two words, or None."""
    w = _w(img, DRAW_SITE)
    if (w >> 26) != 0x05:
        return None
    cave = DRAW_SITE + 4 * (((w & 0x3FFFFFF) ^ 0x2000000) - 0x2000000)
    adrp, add = _w(img, cave), _w(img, cave + 4)
    if (adrp & 0x9F00001F) != 0x90000009 or (add & 0xFFC003FF) != 0x91000129:
        return None
    immlo, immhi = (adrp >> 29) & 3, (adrp >> 5) & 0x7FFFF
    page = (((immhi << 2) | immlo) ^ 0x100000) - 0x100000
    return ((cave & ~0xFFF) + (page << 12)) + ((add >> 10) & 0xFFF)
