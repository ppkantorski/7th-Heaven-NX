#!/usr/bin/env python3
"""ff7nx_subfollow.py -- the field camera follows the player in sub-unit
steps while you walk, as on PC.

BUILD 693. Hardware, 10-15 (video, walking in a field): "when im walking in
the game, it feels like the camera moves in rigid steps".

WHAT THE VIDEO SHOWS
====================
Measured off the capture (phase correlation, 640-wide frames): while Cloud
walks, the picture stands still for 3 or 4 frames and then jumps one field
unit (3 px at 720p), every 4 to 5 frames. Cloud himself moves smoothly.

WHY
===
The player camera is mode 0 of field_update_background_positions (x86
0x644075, ARM +0x9F9000; flag 0xCC15E4 = 0). Every frame it projects the
player to the screen and makes that the camera:

    p      = project(player)           0x64314F -> 0x66307D, _ftol: WHOLE units
    p     -= view (0xCFF204, 0xCFF208)
    clip(p)                             0x6438F6 the camera range rectangle,
                                        0x643628 the diagonal rail (header +0x14)
    delta  = -p                         -> 0xCC15F0 / 0xCC15F4

ff7nx_campos (BUILD 610) feeds the projection the player's real position, so
p is monotonic, but the projection still truncates its result: the camera,
and with it every layer and the 3D world, moves a whole unit at a time. At
walking speed that is one unit every 4 to 5 frames: the steps in the video.
ff7nx_subcam (BUILD 670) fixed the same thing for scripted moves only.

FFNx does not round here: background.cpp keeps the projected point in float
and positions the layers and the world with it.

WHAT THIS DOES
==============
ff7nx_pointers (BUILD 613) already copies the unrounded screen x / y of the
camera's projection (campos tag kind 0) off the x87 stack into its BSS
(+8 / +12), for the rail. One cave at +0x9FA5B8, right after both clips
returned (before the `ldp` that reloads esp / ebp), turns that into the
camera's float position and hands it to ff7nx_subcam, the same way subcam's
own interpolator capture does:

  * per axis: t = trunc(screen); pre = (short)(t - view). Only if the clip
    left the axis alone (the point now in [ebp-0x38] is still `pre`):
        f = pre + (screen - t)
        f = min(f, hi), then max(f, lo)        the clip's own order
    with the rectangle 0x6438F6 uses: x in [L + 160, R - 160], y in
    [T + 120, B - 120] (header +0xC/+0xE/+0x10/+0x12). So the float camera
    never shows past the range edge, not even a fraction of a unit.
  * subcam state: S_F = -f (the float delta), S_I = (short)-p (the delta the
    game is about to store), S_VALID = 1.

subcam's ON cave then does what it does for a scripted move: shift = S_F -
delta (only while the delta is still S_I, under 8 units, snapped to tenths
as FFNx does), added in clip space to every field draw up to the fade quads.
ff7nx_subparallax gives layers 3/4 their parallax share of it, and
ff7nx_pointers' hand remainder keeps the hand on Cloud.

Rail fields (header +0x14 = 1 or 2) are left as they are: the rail cave
rounds its own float projection, and the camera is not the clipped point.
No pointers capture (SEVENTH_NX_HANDSMOOTH=0 and SEVENTH_NX_CAMRAIL=0) or no
subcam: the build says so and leaves the site stock.

The game's integers are untouched (triggers, walkmesh, culling, the scroll
the scripts read). The shift is always under one unit.

SEVENTH_NX_SUBFOLLOW=0 leaves the site stock.

BUILD 772: THE DIAGONAL RAIL FIELDS TOO
========================================
Hardware 10-21 (video, mds5_3, walking left to right): "the camera is very
jittery ... like its traversing a very rigid path". Measured on all 640
frames (phase correlation, 1280x720): the picture moves by exactly 0 or 3 px
(one field unit) per frame, x and y on DIFFERENT frames -- a staircase. The
three rail fields (trigger header +0x14 = 1 or 2: mds5_2, mds5_3, ship_1)
were left out above: their camera point is ff7nx_pointers' rail cave's
projection, which it rounds (fcvtas) from a float it has just computed.

That float is still in s0 / s1 when the rail cave returns into the site
(the translator never touches a v-register). With rail=True the cave keeps
them first thing and, on a rail field, takes each axis exactly when it
rounds to the point the game now holds (otherwise they are not this frame's
rail and nothing is kept), clamped to the same rectangle and kept under a
unit off the point -- the same contract as the walking camera. Built only
when the module's rail cave is present (rail_installed); the cave with
rail=False is byte-identical to BUILD 693. SEVENTH_NX_SUBFOLLOW_RAIL=0 keeps
whole units on rail fields.

BUILD 773: THE SCRIPTED FOLLOW TOO
===================================
Hardware 10-21 (mrkt2, after chasing Aerith in, twice): smooth before the
entry scene, stepped walking around after it, smooth again after a shop or
re-entry. The scene leaves the camera in field_update_scripted_bg_movement's
mode 1 -- "follow this model" -- which never finishes: every frame 0x643C86
projects the model to a whole-unit point, 0x6438F6 clips it, delta = -point,
until a later camera op or a field reload puts mode 0 back. FFNx draws that
mode from the model's float position too. follow_cave2 adds a second entry
at +0x9F84FC (right after that clip; x22 the context, the point at [ebp-8])
sharing the body: ff7nx_campos already tags that projection (+0xA123F4,
kind 0), so the capture holds its unrounded point and the same per-axis
rule applies. No rail on that path (FFNx's neither). The walking entry is
exactly 772's. SEVENTH_NX_SUBFOLLOW_SCRIPTED=0 keeps whole units there.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_deadspace as DS
import ff7nx_subcam as SC

ENV = 'SEVENTH_NX_SUBFOLLOW'
VERSION = 'subfollow-773'

TRANSLATE = 0x10FC3A0
SITE = 0x9FA5B8                    # ldp w8, w9, [x21, #0x10]  (esp, ebp)
SITE_WORD = 0x294226A8
CLIP_CALL = 0x9FA5B4               # bl 0x643628 (stock) or the pointers rail cave
RAIL_STOCK = 0xA108A0              # x86 0x643628
CTX = 21                           # the guest context in 0x644075
POINT = 0x38                       # [ebp-0x38]: the camera point (x, y shorts)
# the words after the site, as they are stock (checked)
AFTER = {0x9FA5BC: 0x11002108,     # add w8, w8, #8
         0x9FA5C0: 0x5100E120,     # sub w0, w9, #0x38
         0x9FA5D4: 0x4B0803F4}     # neg w20, w8
HEADER_PTR = 0xCFF454              # field_triggers_header (pointer)
VIEW_X = 0xCFF204
VIEW_Y = 0xCFF208
HALF_W, HALF_H = 160, 120          # 0x6438F6's margins

# ff7nx_pointers' ftol capture (kind 0 = camera at bss + 8 / + 12)
import ff7nx_pointers as PT        # noqa: E402

GT, MI, LS, NE = 0xC, 0x4, 0x9, 0x1


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _bl_target(va, w):
    if (w >> 26) != 0x25:
        return None
    return va + 4 * (((w & 0x3FFFFFF) ^ 0x2000000) - 0x2000000)


def _sxth(rd, rn):
    return 0x13003C00 | (rn << 5) | rd


def _fneg_s(rd, rn):
    return 0x1E214000 | (rn << 5) | rd


def _fcsel(rd, rn, rm, cond):
    return 0x1E200C00 | (rm << 16) | (cond << 12) | (rn << 5) | rd


def _mov32(e, rd, v):
    e(A.movz(rd, v & 0xFFFF))
    e(A.movk_hi(rd, (v >> 16) & 0xFFFF))


def _ptr(e, a, rd, host):
    e(A.adrp(rd, a.pc(), host & ~0xFFF))
    e(A.add_imm64(rd, rd, host & 0xFFF))


# frame: x19..x24, x29/x30 at 0..0x40; values at 0x40..
F_L, F_T, F_R, F_B, F_VX, F_VY, F_OX, F_OY = (0x40 + 4 * k for k in range(8))
F_RX, F_RY = 0x60, 0x64         # BUILD 772: the rail cave's float point
FRAME = 0x60
FRAME_RAIL = 0x70               # 772: room for F_RX / F_RY


def _csinc(rd, rn, rm, cond):
    return 0x1A800400 | (rm << 16) | (cond << 12) | (rn << 5) | rd


def _fcvtas_w(rd, sn):
    return 0x1E240000 | (sn << 5) | rd          # round to nearest, ties away


def follow_cave(at, state, bss, rail=False):
    """At +0x9FA5B8, after `bl 0x643628` (or the rail cave) returned:
    x0..x18, x30 and the flags are dead (a call just clobbered them).
    x19..x24 and x29/x30 are saved; the displaced `ldp` runs last, with
    x21 (the guest context) as it came in.

    BUILD 772, rail=True: ff7nx_pointers' rail cave (the call that just
    returned, on a rail field) leaves the camera's FLOAT point on the rail
    in s0 / s1 -- it rounds a copy (fcvtas) for the game. They are kept
    first thing (the translator touches no v-register), and on a rail
    field each axis is taken when it rounds exactly to the point the game
    now holds -- otherwise s0 / s1 are not the rail's and nothing is kept."""
    a = AC.Asm(at, lambda i: at + 4 * i)
    e = a.emit
    frame = FRAME_RAIL if rail else FRAME
    e(A.stp64_pre(19, 20, 31, -frame))
    e(A.stp64_off(21, 22, 31, 0x10))
    e(A.stp64_off(23, 24, 31, 0x20))
    e(A.stp64_off(29, 30, 31, 0x30))
    if rail:
        e(A.str_s(0, 31, F_RX))
        e(A.str_s(1, 31, F_RY))
    e(A.mov_reg64(19, CTX))
    # the range header; rail fields (772: rail=False) are left alone
    _mov32(e, 0, HEADER_PTR)
    e(A.bl(a.pc(), TRANSLATE))
    e(A.ldr(20, 0, 0))
    e(A.add_imm(0, 20, 0x14))
    e(A.bl(a.pc(), TRANSLATE))
    e(A.ldrb(9, 0, 0))
    e(A.sub_imm(9, 9, 1))
    e(A.cmp_imm(9, 1))
    if rail:
        e(_csinc(24, 31, 31, 0x8))                   # w24 = (1 or 2): cset LS
    else:
        a.bcond('out', LS)                           # 1 or 2: the rail
    for k, off in enumerate((F_L, F_T, F_R, F_B)):
        e(A.add_imm(0, 20, 0xC + 2 * k))
        e(A.bl(a.pc(), TRANSLATE))
        e(A.ldrsh(9, 0, 0))
        e(A.str_(9, 31, off))
    for guest, off in ((VIEW_X, F_VX), (VIEW_Y, F_VY)):
        _mov32(e, 0, guest)
        e(A.bl(a.pc(), TRANSLATE))
        e(A.ldr(9, 0, 0))
        e(A.str_(9, 31, off))
    for k, off in enumerate((F_OX, F_OY)):
        e(A.ldr(0, 19, 0x14))                        # ebp
        e(A.sub_imm(0, 0, POINT - 2 * k))
        e(A.bl(a.pc(), TRANSLATE))
        e(A.ldrsh(9, 0, 0))
        e(A.str_(9, 31, off))
    # ---- no translator calls from here: host pointers only ---------------
    _ptr(e, a, 22, bss + 8)                          # captured screen x, y
    _ptr(e, a, 23, state)                            # ff7nx_subcam's state
    for axis, (lo, hi, margin, v, o, fr) in enumerate(
            ((F_L, F_R, HALF_W, F_VX, F_OX, F_RX),
             (F_T, F_B, HALF_H, F_VY, F_OY, F_RY))):
        skip = 'skip%d' % axis
        if rail:
            a.cbnz(24, 'rail%d' % axis)
        e(A.ldr_s(0, 22, 4 * axis))                  # screen
        e(SC.fcvtzs_w(9, 0))                         # t = trunc (as _ftol)
        e(A.ldr(10, 31, v))
        e(A.sub_reg(10, 9, 10))
        e(_sxth(10, 10))                             # pre = (short)(t - view)
        e(A.ldr(11, 31, o))                          # the point after the clips
        e(A.cmp_reg(10, 11))
        a.bcond(skip, NE)                            # the clip moved it
        e(SC.scvtf_s(1, 9))
        e(A.fsub_s(1, 0, 1))                         # screen - t
        e(SC.scvtf_s(2, 11))
        e(A.fadd_s(2, 2, 1))                         # f = point + remainder
        if rail:
            a.b('tail%d' % axis)
            # BUILD 772: a rail field -- f is the rail cave's float point,
            # taken only if it rounds (as the cave rounded) to the point
            a.label('rail%d' % axis)
            e(A.ldr_s(2, 31, fr))
            e(_fcvtas_w(9, 2))
            e(A.ldr(11, 31, o))
            e(A.cmp_reg(9, 11))
            a.bcond(skip, NE)                        # not this frame's rail
            a.label('tail%d' % axis)
        e(A.ldr(12, 31, hi))
        e(A.sub_imm(12, 12, margin))
        e(SC.scvtf_s(3, 12))
        e(A.fcmp_s(2, 3))
        e(_fcsel(2, 3, 2, GT))                       # f > hi -> hi
        e(A.ldr(12, 31, lo))
        e(A.add_imm(12, 12, margin))
        e(SC.scvtf_s(3, 12))
        e(A.fcmp_s(2, 3))
        e(_fcsel(2, 3, 2, MI))                       # f < lo -> lo
        # never more than a unit off the point (and not NaN)
        e(SC.scvtf_s(3, 11))
        e(A.fsub_s(3, 2, 3))
        e(SC.fabs_s(3, 3))
        e(A.fmov_s_imm(4, 1.0))
        e(A.fcmp_s(3, 4))
        a.bcond(skip, 0x5)                           # PL: not < 1 (or NaN)
        e(A.sub_reg(11, 31, 11))
        e(_sxth(11, 11))
        e(A.str_(11, 23, SC.S_I + 4 * axis))         # the delta about to be stored
        e(_fneg_s(2, 2))
        e(A.str_s(2, 23, SC.S_F + 4 * axis))         # the float delta
        e(A.movz(9, 1))
        e(A.str_(9, 23, SC.S_VALID + 4 * axis))
        a.label(skip)
    a.label('out')
    e(A.mov_reg64(21, 19))
    e(A.ldp64_off(29, 30, 31, 0x30))
    e(A.ldp64_off(23, 24, 31, 0x20))
    e(A.ldp64_off(21, 22, 31, 0x10))
    e(A.ldp64_post(19, 20, 31, frame))
    e(SITE_WORD)
    e(A.b(a.pc(), SITE + 4))
    return a.resolve()


# ------------------------------------------------------------- BUILD 773
# The SCRIPTED follow: field_update_scripted_bg_movement (x86 0x643D22),
# mode 1 (SCRLC / SCRLA "follow this model"), ARM +0x9F84D4: 0x643C86
# projects the followed model to a WHOLE-unit point, 0x6438F6 clips it, and
# the delta is -point every frame -- for as long as the camera stays in
# that mode (it never finishes: only a later camera op or a field reload
# puts mode 0 back). Hardware 10-21, mrkt2 after chasing Aerith: smooth,
# then stepped after the entry scene, smooth again after a shop or re-entry.
# ff7nx_campos already tags that projection (+0xA123F4, kind 0), so the
# capture holds its unrounded screen point; the same per-axis rule as the
# walking camera turns it into the float camera here. No rail on this path
# (FFNx's mode 1 does not call 0x643628 either).
SCR_SITE = 0x9F84FC                # ldp w8, w9, [x22, #0x10]  after the clip
SCR_WORD = 0x294226C8
SCR_AFTER = {0x9F84F8: None,       # bl 0x6438F6 (checked as a bl)
             0x9F8500: 0x11001108,  # add w8, w8, #4
             0x9F8504: 0x51002120}  # sub w0, w9, #8   [ebp-8]: the point
SCR_POINT = 0x8                    # [ebp-8] x, [ebp-6] y
SCR_ENV = 'SEVENTH_NX_SUBFOLLOW_SCRIPTED'
F_POFF, F_SEL = 0x68, 0x6C


def scripted_enabled(env=None):
    env = os.environ if env is None else env
    return env.get(SCR_ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def follow_cave2(at, state, bss, rail=True):
    """BUILD 773: two entries, one body. `at` is the walking camera's
    (+0x9FA5B8, x21 the guest context, point [ebp-0x38], the rail as 772
    when `rail`); `entry2` (returned) is the scripted follow's (+0x9F84FC,
    x22 the context, point [ebp-8], never a rail). Each entry saves the
    same registers, records its point offset and which exit to take; the
    body is follow_cave's, the exit runs that site's displaced `ldp`."""
    a = AC.Asm(at, lambda i: at + 4 * i)
    e = a.emit

    def save():
        e(A.stp64_pre(19, 20, 31, -FRAME_RAIL))
        e(A.stp64_off(21, 22, 31, 0x10))
        e(A.stp64_off(23, 24, 31, 0x20))
        e(A.stp64_off(29, 30, 31, 0x30))
    save()                                           # entry 1: walking
    e(A.str_s(0, 31, F_RX))
    e(A.str_s(1, 31, F_RY))
    e(A.mov_reg64(19, CTX))
    e(A.movz(9, POINT))
    e(A.str_(9, 31, F_POFF))
    e(A.str_(31, 31, F_SEL))                         # wzr: exit 1
    a.b('body')
    entry2 = at + 4 * len(a.words)
    save()                                           # entry 2: scripted
    e(A.mov_reg64(19, 22))
    e(A.movz(9, SCR_POINT))
    e(A.str_(9, 31, F_POFF))
    e(A.movz(9, 1))
    e(A.str_(9, 31, F_SEL))
    a.label('body')
    _mov32(e, 0, HEADER_PTR)
    e(A.bl(a.pc(), TRANSLATE))
    e(A.ldr(20, 0, 0))
    e(A.add_imm(0, 20, 0x14))
    e(A.bl(a.pc(), TRANSLATE))
    e(A.ldrb(9, 0, 0))
    e(A.sub_imm(9, 9, 1))
    e(A.cmp_imm(9, 1))
    e(_csinc(24, 31, 31, 0x8))                       # w24 = rail field
    e(A.ldr(10, 31, F_SEL))
    a.cbz(10, 'walk')
    e(A.movz(24, 0))                                 # scripted: never a rail
    a.label('walk')
    if not rail:
        a.cbnz(24, 'out')                            # walking on a rail: stock
    for k, off in enumerate((F_L, F_T, F_R, F_B)):
        e(A.add_imm(0, 20, 0xC + 2 * k))
        e(A.bl(a.pc(), TRANSLATE))
        e(A.ldrsh(9, 0, 0))
        e(A.str_(9, 31, off))
    for guest, off in ((VIEW_X, F_VX), (VIEW_Y, F_VY)):
        _mov32(e, 0, guest)
        e(A.bl(a.pc(), TRANSLATE))
        e(A.ldr(9, 0, 0))
        e(A.str_(9, 31, off))
    for k, off in enumerate((F_OX, F_OY)):
        e(A.ldr(0, 19, 0x14))                        # ebp
        e(A.ldr(9, 31, F_POFF))
        e(A.sub_reg(0, 0, 9))
        if k:
            e(A.add_imm(0, 0, 2))
        e(A.bl(a.pc(), TRANSLATE))
        e(A.ldrsh(9, 0, 0))
        e(A.str_(9, 31, off))
    _ptr(e, a, 22, bss + 8)
    _ptr(e, a, 23, state)
    for axis, (lo, hi, margin, v, o, fr) in enumerate(
            ((F_L, F_R, HALF_W, F_VX, F_OX, F_RX),
             (F_T, F_B, HALF_H, F_VY, F_OY, F_RY))):
        skip = 'skip%d' % axis
        if rail:
            a.cbnz(24, 'rail%d' % axis)
        e(A.ldr_s(0, 22, 4 * axis))
        e(SC.fcvtzs_w(9, 0))
        e(A.ldr(10, 31, v))
        e(A.sub_reg(10, 9, 10))
        e(_sxth(10, 10))
        e(A.ldr(11, 31, o))
        e(A.cmp_reg(10, 11))
        a.bcond(skip, NE)
        e(SC.scvtf_s(1, 9))
        e(A.fsub_s(1, 0, 1))
        e(SC.scvtf_s(2, 11))
        e(A.fadd_s(2, 2, 1))
        if rail:
            a.b('tail%d' % axis)
            a.label('rail%d' % axis)
            e(A.ldr_s(2, 31, fr))
            e(_fcvtas_w(9, 2))
            e(A.ldr(11, 31, o))
            e(A.cmp_reg(9, 11))
            a.bcond(skip, NE)
            a.label('tail%d' % axis)
        e(A.ldr(12, 31, hi))
        e(A.sub_imm(12, 12, margin))
        e(SC.scvtf_s(3, 12))
        e(A.fcmp_s(2, 3))
        e(_fcsel(2, 3, 2, GT))
        e(A.ldr(12, 31, lo))
        e(A.add_imm(12, 12, margin))
        e(SC.scvtf_s(3, 12))
        e(A.fcmp_s(2, 3))
        e(_fcsel(2, 3, 2, MI))
        e(SC.scvtf_s(3, 11))
        e(A.fsub_s(3, 2, 3))
        e(SC.fabs_s(3, 3))
        e(A.fmov_s_imm(4, 1.0))
        e(A.fcmp_s(3, 4))
        a.bcond(skip, 0x5)
        e(A.sub_reg(11, 31, 11))
        e(_sxth(11, 11))
        e(A.str_(11, 23, SC.S_I + 4 * axis))
        e(_fneg_s(2, 2))
        e(A.str_s(2, 23, SC.S_F + 4 * axis))
        e(A.movz(9, 1))
        e(A.str_(9, 23, SC.S_VALID + 4 * axis))
        a.label(skip)
    a.label('out')
    e(A.ldr(16, 31, F_SEL))
    e(A.ldp64_off(29, 30, 31, 0x30))
    e(A.ldp64_off(23, 24, 31, 0x20))
    e(A.ldp64_off(21, 22, 31, 0x10))
    e(A.ldp64_post(19, 20, 31, FRAME_RAIL))
    a.cbnz(16, 'exit2')
    e(SITE_WORD)
    e(A.b(a.pc(), SITE + 4))
    a.label('exit2')
    e(SCR_WORD)
    e(A.b(a.pc(), SCR_SITE + 4))
    return a.resolve(), entry2


# ------------------------------------------------------------------ checks
def pointers_bss(img):
    """ff7nx_pointers' capture BSS, read back from its x ftol cave
    (`adrp x10` + `add x10, x10`), or None if the capture is not installed."""
    cave = _bl_target(PT.FTOL_X, _w(img, PT.FTOL_X))
    if cave is None or cave == PT.FTOL:
        return None
    for k in range(32):
        adrp, add = _w(img, cave + 4 * k), _w(img, cave + 4 * k + 4)
        if (adrp & 0x9F00001F) == 0x9000000A and (add & 0xFFC003FF) == 0x9100014A:
            immlo, immhi = (adrp >> 29) & 3, (adrp >> 5) & 0x7FFFF
            page = (((immhi << 2) | immlo) ^ 0x100000) - 0x100000
            base = ((cave + 4 * k) & ~0xFFF) + (page << 12)
            return base + ((add >> 10) & 0xFFF)
    return None


def pointers_capture_ok(img, cave):
    """The cave keeps kind 0 (the camera) too, not only the hand: its
    kind check is `cmp w9, #1` + `b.hi` (kinds 0 and 1), as BUILD 613."""
    want = PT._cmp_imm(9, PT.KIND_CURSOR)
    return any(_w(img, cave + 4 * k) == want for k in range(16))


RAIL_ENV = 'SEVENTH_NX_SUBFOLLOW_RAIL'


def rail_enabled(env=None):
    env = os.environ if env is None else env
    return env.get(RAIL_ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def rail_installed(img):
    """BUILD 772: the call before the site is ff7nx_pointers' rail cave
    (the only thing that leaves the rail's float point in s0 / s1)."""
    t = _bl_target(CLIP_CALL, _w(img, CLIP_CALL))
    if t is None or t == RAIL_STOCK:
        return False
    # its float result: `fcvtas w20, s0` + `fcvtas w22, s1` (pointers 613)
    want = {PT._fcvtas(20, 0), PT._fcvtas(22, 1)}
    have = {_w(img, t + 4 * k) for k in range(160)}
    return want <= have


def check_scripted(img):
    """BUILD 773: what is wrong with the scripted follow site ([] = fine)."""
    bad = []
    if _w(img, SCR_SITE) != SCR_WORD:
        bad.append('+0x%X is %08X, not ldp w8, w9, [x22, #0x10]'
                   % (SCR_SITE, _w(img, SCR_SITE)))
    for va, want in SCR_AFTER.items():
        if want is None:
            if _bl_target(va, _w(img, va)) != 0xA11530:
                bad.append('+0x%X is not bl 0x6438F6' % va)
        elif _w(img, va) != want:
            bad.append('+0x%X is %08X, not %08X' % (va, _w(img, va), want))
    return bad


def check_sites(img):
    bad = []
    if _w(img, SITE) != SITE_WORD:
        bad.append('+0x%X is %08X, not ldp w8, w9, [x21, #0x10]' % (SITE, _w(img, SITE)))
    if _bl_target(CLIP_CALL, _w(img, CLIP_CALL)) is None:
        bad.append('+0x%X is not the bl to 0x643628' % CLIP_CALL)
    for va, want in AFTER.items():
        if _w(img, va) != want:
            bad.append('+0x%X is %08X, not %08X' % (va, _w(img, va), want))
    if SC.state_of(img) is None:
        bad.append('ff7nx_subcam is not installed (its state is where the float goes)')
    bss = pointers_bss(img)
    if bss is None:
        bad.append('ff7nx_pointers\' camera capture is not installed '
                   '(SEVENTH_NX_HANDSMOOTH / SEVENTH_NX_CAMRAIL)')
    else:
        cave = _bl_target(PT.FTOL_X, _w(img, PT.FTOL_X))
        if not pointers_capture_ok(img, cave):
            bad.append('ff7nx_pointers\' ftol cave does not keep the camera (kind 0)')
    return bad


def apply_to_nso(src, dest, stock=None, log=lambda *_: None, region=None):
    import nxmap
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    img = nxmap.Main(src).img
    bad = check_sites(img)
    if bad:
        raise ValueError('subfollow: ' + '; '.join(bad))
    state, bss = SC.state_of(img), pointers_bss(img)
    lo, hi = region if region else DS.part(src, 'follow', stock)
    at = (lo + 15) & ~15
    rail = rail_installed(img) and rail_enabled()
    scripted = scripted_enabled() and not check_scripted(img)
    if scripted:
        words, entry2 = follow_cave2(at, state, bss, rail=rail)
    else:
        words, entry2 = follow_cave(at, state, bss, rail=rail), None
    if at + 4 * len(words) > hi:
        raise ValueError('subfollow: %d bytes do not fit the %d-byte part'
                         % (4 * len(words), hi - lo))
    for i, x in enumerate(words):
        struct.pack_into('<I', text, at + 4 * i, x)
    struct.pack_into('<I', text, SITE, A.b(SITE, at))
    if entry2 is not None:
        struct.pack_into('<I', text, SCR_SITE, A.b(SCR_SITE, entry2))
    raw[0] = bytes(text)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(AC.pack(blob, raw, 0))
    log('  sub-unit follow camera: the player camera (mode 0) drawn at its '
        'float position, clipped to the camera range; cave +0x%X..+0x%X, '
        'floats from ff7nx_pointers +0x%X into ff7nx_subcam +0x%X (%s=0 '
        'disables)' % (at, at + 4 * len(words), bss + 8, state, ENV))
    log('  diagonal rail fields (mds5_2, mds5_3, ship_1): %s'
        % ('the float point on the rail too (BUILD 772; %s=0 keeps whole '
           'units)' % RAIL_ENV if rail else
           'whole units (no rail cave, or %s=0)' % RAIL_ENV))
    log('  scripted follow (SCRLC / SCRLA following a model, e.g. mrkt2 '
        'after its entry scene): %s'
        % ('float position too (BUILD 773, entry +0x%X; %s=0 keeps whole '
           'units)' % (entry2, SCR_ENV) if scripted else
           'whole units (%s=0, or the site is not stock)' % SCR_ENV))
    return {'cave': at, 'bytes': 4 * len(words), 'state': state, 'bss': bss,
            'rail': rail, 'scripted': scripted}
