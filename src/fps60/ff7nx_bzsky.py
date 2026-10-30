#!/usr/bin/env python3
"""
ff7nx_bzsky.py -- BUILD 852. Bahamut ZERO's star field at the drawn rate.

WHAT WAS WRONG
==============
Bahamut ZERO draws its space background in `bahamut_zero_draw_bg_effect`
(x86 0x4859AA, ARM +0x250240): six 256-unit star tiles, offset on screen by
two angles it reads out of the summon's composite camera matrix at guest
0xAA66C0 (s16 m[1][1], m[1][2], m[0][2], m[2][2] -> 0x662573 -> & 0xFF).
It never builds that matrix itself. The summon's main loop does
(x86 0x484A5E):

    661e85(0xBFCB30 battle view, 0xA85468 summon frame, 0xAA66C0)

and between main-loop counters 55 and 234 it then turns the result about the
view's vertical axis (0x48B98D: a yaw eased 0 -> 0x1000 over 180 ticks,
built at [0xAA6544] and multiplied in front -- the orbit).

The main loop is one of FFNx's OneCallEffectDecorator functions, so at
60 FPS it runs on one frame in four; the star draw is pause-throttled and
draws on every frame. Every frame the stars were drawn from a matrix that
was rebuilt only once a tick: the background slid in 15 Hz steps while the
camera (interpolated since BUILD 384) and the models moved every frame.

THE FIX
=======
At the entry of 0x4859AA, before anything of it runs:

    AA66C0 = 661e85(view now, summon frame)                 always
    if the main loop's counter c is in the orbit (55 <= c < 235):
        48b98d(c - 55)        -> AA66C0 = yaw(c) x it      (the stock tick)
        on a paused frame, step k of the tick (k = 1..3 of 4):
            yaw(c+1) by the same routine, then
            AA66C0 = rotate(yaw(c) + (yaw(c+1) - yaw(c)) * k / 4) x view x frame

So the camera part is the live (interpolated) view, exactly what the models
are drawn with, and the orbit slides from one tick's angle toward the angle
the next tick will compute (0x48B98D is a pure function of its argument;
yaw(180) is 0x1000, a full turn, which is what the loop leaves at c = 235).
On the real call (phase 1) the result is the stock matrix bit for bit: the
same three calls with the same inputs the main loop just made.

c is not stored anywhere the star draw can see, but it is fixed relative to
the draw's own counter s ([0xBFB718 + 32*slot + 2]): the main loop registers
the draw when c = 35 and the draw runs in a later slot of the same pass, and
both count once per real tick, so c = s + 35 on the real call and s + 34 on
the paused ones (s has already counted that tick).

The phase is the throttle's own per-slot counter (ff7nx_dispatch, effect100
`ctr`): phase 1 is the real call, 2/3/0 the paused ones (Full 30 FPS: 1 and
0). A slot carrying any mark bit (excluded, KOTR, model, OneCall, camera) is
left alone.

Other Bahamut ZERO pieces read 0xAA66C0 too (the beam and particle loops
multiply their own frames by it); those that run after the star draw in a
frame now see the live view as well, and on the real call nothing differs.

Code: the upper 896 bytes of dead-space part 'wmtime' (it uses 1,056 of its
2 KB). SEVENTH_NX_BZ_SKY=0 leaves the background stepping.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_tables
from ff7nx_audio_cave import Asm

ENV = 'SEVENTH_NX_BZ_SKY'

TRANSLATE = 0x10FC3A0
CTX_GOT = 0x12CE2B0

HOOK = 0x250240                 # ARM entry of x86 0x4859AA
HOOK_ORIG = 0xA9BA6FFC          # stp x28, x27, [sp, #-0x60]!
RESUME = HOOK + 4

COMPOSE = 0xA8E1F0              # x86 0x661E85 (a, b, out): out = a x b
ROT_MATRIX = 0xA94380           # x86 0x662AD8 (angles, out)
ORBIT = 0x24E4C0                # x86 0x48B98D (n): the orbit yaw, in place

VIEW = 0xBFCB30                 # the battle view
FRAME = 0xA85468                # Bahamut ZERO's frame
SKY = 0xAA66C0                  # the composite the star draw reads
ORBIT_PTR = 0xAA6544            # -> {s16 rx, ry, rz; ...; matrix at +8}
CUR_EFFECT = 0xBF23B8           # the dispatcher's running slot
EFFECT_DATA = 0xBFB718          # 32-byte slot records; +2 the counter

DRAW_FIRST = 35                 # main-loop counter that registers the draw
ORBIT_FIRST = 55                # first counter that calls 0x48B98D
ORBIT_TICKS = 180               # ... for 180 ticks (arg 0..179)

# The x86 these constants come from, checked against ff7_en on every build.
X86_EXPECT = (
    # main loop: push 0xAA66C0; push 0xA85468; push 0xBFCB30; call 0x661E85
    (0x484A5E, bytes.fromhex('68c066aa00') + bytes.fromhex('686854a800')
     + bytes.fromhex('6830cbbf00')),
    # c == 0 after -5 -30: push 0x4859AA; call 0x5BEC50 (registers the draw)
    (0x484CFD, bytes.fromhex('68aa594800')),
    # the remaining-20 test, then -0x14 and the < 0xB4 orbit test
    (0x484C6B, bytes.fromhex('837dfc14')),
    (0x484D26, bytes.fromhex('817dfcb4000000')),
    # the orbit's own composition: 661e85([AA6544]+8, AA66C0, AA66C0)
    (0x48BBDC, bytes.fromhex('68c066aa0068c066aa00')),
    # the draw reads the composite, never writes it
    (0x485A38, bytes.fromhex('668b15c866aa00')),
)


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no',
                                                     'false', 'stock')


def check_exe(exe):
    """'' when ff7_en holds what this pass is built on, else why not."""
    if exe is None:
        return ''
    text = exe[0x200:0x200 + 0x3B5000]
    for va, want in X86_EXPECT:
        o = va - 0x401000
        got = text[o:o + len(want)]
        if got != want:
            return ('ff7_en 0x%X is %s, expected %s'
                    % (va, got.hex(), want.hex()))
    return ''


# -- emitters ---------------------------------------------------------------
# x20 = the emulated x86 context throughout (+0x10 esp). The translator and
# the game functions clobber x0..x18 (AAPCS); everything live is in x19..x24.

def _esp(a, delta):
    a.emit(A.ldr(8, 20, 0x10))
    a.emit(A.add_imm(8, 8, delta) if delta > 0 else A.sub_imm(8, 8, -delta))
    a.emit(A.str_(8, 20, 0x10))


def _guest(a, reg_or_value):
    """x0 = host pointer for a guest address (a constant or a w register)."""
    if isinstance(reg_or_value, str):
        a.emit(A.mov_reg(0, int(reg_or_value[1:])))
    else:
        AC.mov32(a, 0, reg_or_value)
    a.emit(A.bl(a.pc(), TRANSLATE))


def _call(a, fn, nargs):
    """The arguments are pushed; reserve the return slot (the callee pops
    it), call, pop the arguments."""
    _esp(a, -4)
    a.emit(A.bl(a.pc(), fn))
    _esp(a, 4 * nargs)


def _bl(a, name):
    """bl to a local subroutine, resolved by _resolve."""
    a.fixups.append((len(a.words), name, 'bl'))
    a.emit(0)


def build_cave(_entry, addr, ctr, freq=4):
    """The entry cave of 0x4859AA. `ctr` is the effect100 throttle's
    per-slot phase byte array (module BSS), `freq` 4 (60) or 2 (30)."""
    if freq not in (2, 4):
        raise ValueError('bzsky: freq %r' % freq)
    shift = freq.bit_length() - 1
    a = Asm(_entry, addr)
    a.emit(A.stp64_pre(29, 30, 31, -0x40))
    a.emit(A.stp64_off(19, 20, 31, 0x10))
    a.emit(A.stp64_off(21, 22, 31, 0x20))
    a.emit(A.stp64_off(23, 24, 31, 0x30))
    a.emit(A.adrp(20, a.pc(), CTX_GOT & ~0xFFF))
    a.emit(A.ldr64(20, 20, CTX_GOT & 0xFFF))
    # slot -> phase -> step (0 = the real call, 1..freq-1 paused)
    _guest(a, CUR_EFFECT)
    a.emit(A.ldrsh(23, 0, 0))                     # w23 = slot
    AC.bss_ptr(a, 9, ctr)
    a.emit(A.and_mask(8, 23, 7))
    a.emit(A.add_reg64(9, 9, 8))
    a.emit(A.ldrb(21, 9, 0))
    a.emit(A.lsr(8, 21, 2))
    a.cbnz(8, 'out')                              # a marked slot: stock
    a.emit(A.add_imm(21, 21, freq - 1))
    a.emit(A.and_mask(21, 21, shift))             # w21 = step
    # the draw's own counter -> the main loop's orbit argument
    AC.mov32(a, 8, EFFECT_DATA + 2)
    a.emit(A.add_reg_lsl(0, 8, 23, 5))
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.ldrsh(22, 0, 0))
    a.emit(A.sub_imm(22, 22, ORBIT_FIRST - DRAW_FIRST))
    a.cbz(21, 'real')
    a.emit(A.sub_imm(22, 22, 1))                  # s has counted the tick
    a.label('real')                               # w22 = c - 55
    _bl(a, 'compose')                         # AA66C0 = view x frame
    a.emit(A.cmp_imm(22, ORBIT_TICKS))
    a.bcond('out', A.HS)                          # outside the orbit
    a.emit(A.mov_reg(24, 22))
    _bl(a, 'orbit')                           # yaw(c): the stock tick
    a.cbz(21, 'out')
    _bl(a, 'yaw')
    a.emit(A.mov_reg(19, 0))                      # w19 = yaw(c)
    _bl(a, 'compose')
    a.emit(A.add_imm(24, 22, 1))
    _bl(a, 'orbit')                           # yaw(c+1)
    _bl(a, 'yaw')
    a.emit(A.sub_reg(8, 0, 19))
    a.emit(A.mul(8, 8, 21))
    a.emit(A.asr(8, 8, shift))
    a.emit(A.add_reg(24, 19, 8))                  # w24 = the yaw drawn now
    _bl(a, 'compose')                         # AA66C0 = view x frame
    _guest(a, ORBIT_PTR)
    a.emit(A.ldr(23, 0, 0))                       # w23 = [AA6544]
    a.emit(A.add_imm(0, 23, 2))
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.strh(24, 0, 0))                      # its ry
    a.emit(A.add_imm(24, 23, 8))
    a.emit(A.mov_reg(1, 24))
    _bl(a, 'push')
    a.emit(A.mov_reg(1, 23))
    _bl(a, 'push')
    _call(a, ROT_MATRIX, 2)                       # 662ad8(angles, +8)
    AC.mov32(a, 1, SKY)
    _bl(a, 'push')
    AC.mov32(a, 1, SKY)
    _bl(a, 'push')
    a.emit(A.mov_reg(1, 24))
    _bl(a, 'push')
    _call(a, COMPOSE, 3)                          # the turn, in front
    a.label('out')
    a.emit(A.ldp64_off(23, 24, 31, 0x30))
    a.emit(A.ldp64_off(21, 22, 31, 0x20))
    a.emit(A.ldp64_off(19, 20, 31, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, 0x40))
    a.emit(HOOK_ORIG)                             # the displaced prologue
    a.emit(A.b(a.pc(), RESUME))

    # push w1 onto the guest stack
    a.label('push')
    a.emit(A.stp64_pre(29, 30, 31, -0x20))
    a.emit(A.str_(1, 31, 0x10))                   # w1 across the translator
    a.emit(A.ldr(8, 20, 0x10))
    a.emit(A.sub_imm(0, 8, 4))
    a.emit(A.str_(0, 20, 0x10))
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.ldr(9, 31, 0x10))
    a.emit(A.str_(9, 0, 0))
    a.emit(A.ldp64_post(29, 30, 31, 0x20))
    a.emit(A.ret())

    # AA66C0 = 661e85(VIEW, FRAME, AA66C0)
    a.label('compose')
    a.emit(A.stp64_pre(29, 30, 31, -0x10))
    for v in (SKY, FRAME, VIEW):
        AC.mov32(a, 1, v)
        _bl(a, 'push')
    _call(a, COMPOSE, 3)
    a.emit(A.ldp64_post(29, 30, 31, 0x10))
    a.emit(A.ret())

    # 48b98d(w24): the orbit's yaw for argument w24, multiplied into AA66C0
    a.label('orbit')
    a.emit(A.stp64_pre(29, 30, 31, -0x10))
    a.emit(A.mov_reg(1, 24))
    _bl(a, 'push')
    _call(a, ORBIT, 1)
    a.emit(A.ldp64_post(29, 30, 31, 0x10))
    a.emit(A.ret())

    # w0 = s16 ry of [AA6544] (valid: 0x48B98D has just run)
    a.label('yaw')
    a.emit(A.stp64_pre(29, 30, 31, -0x10))
    _guest(a, ORBIT_PTR)
    a.emit(A.ldr(0, 0, 0))
    a.emit(A.add_imm(0, 0, 2))
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.ldrsh(0, 0, 0))
    a.emit(A.ldp64_post(29, 30, 31, 0x10))
    a.emit(A.ret())
    return _resolve(a)


def _resolve(a):
    """Asm.resolve plus 'bl' fixups to local labels."""
    bls = [f for f in a.fixups if f[2] == 'bl']
    a.fixups = [f for f in a.fixups if f[2] != 'bl']
    words = a.resolve()
    for i, name, _kind in bls:
        words[i] = A.bl(a.addr(i), a.labels[name])
    return words


def throttle_ctr(text):
    """The effect100 throttle's phase array, or raise when the throttle is
    not installed in this module."""
    import ff7nx_dispatch_sites as S
    thr = S.DISPATCH_SITES['effect100']['throttle']
    if not thr:
        raise ValueError('bzsky: no effect100 throttle in the sites file')
    pre = struct.unpack_from('<I', text, thr['pre'])[0]
    if pre == thr['pre_displaced'] or (pre & 0xFC000000) != 0x14000000:
        raise ValueError('bzsky: the effect100 throttle is not installed '
                         '(+0x%X is %08X)' % (thr['pre'], pre))
    return thr['ctr']


def apply_to_nso(src, dest, stock=None, exe=None, freq=4,
                 log=lambda *_: None):
    import ff7nx_deadspace as DS
    why = check_exe(exe)
    if why:
        raise ValueError('bzsky: ' + why)
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw, space = ff7nx_tables.open_module(blob)
    text = space.text
    got = struct.unpack_from('<I', text, HOOK)[0]
    if got != HOOK_ORIG:
        raise ValueError('bzsky: +0x%X is %08X, not the stock prologue %08X'
                         % (HOOK, got, HOOK_ORIG))
    ctr = throttle_ctr(text)
    if ctr + 0x80 > AC.scratch_base(blob, segs):
        raise ValueError('bzsky: the throttle array +0x%X is past the '
                         'module BSS' % ctr)
    lo, hi = DS.part(src, 'bzsky', stock)
    dead = DS.Bump(lo, hi)
    cave = dead.put(lambda e, ad: build_cave(e, ad, ctr, freq))
    dead.placed[HOOK] = A.b(HOOK, cave)
    for va, w in dead.placed.items():
        struct.pack_into('<I', text, va, w)
    out = AC.pack(blob, space.commit(), 0)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'cave': cave, 'bytes': dead.at - lo, 'ctr': ctr, 'freq': freq}
