#!/usr/bin/env python3
"""60 FPS partial field animations: FFNx's frame mapping. BUILD 580.

THE BUG (wcrimb_1's swinging bar stops half way)
================================================
The 60/30 FPS mod doubles every field model animation (wcrimb_1's bar,
EAHC.a: 90 frames -> 180). A FULL animation (ANIM!) just plays the longer
file at the doubled field rate and takes the same time. A PARTIAL one names
frames in the script -- `CANM! anim first last speed` / `CANIM` -- in the
ORIGINAL numbering, and nothing on this port translated them: wcrimb_1's
bar steps `CANM! 00 f f 01` for f = 0..0x59, i.e. the first 90 of 180
frames -- the first half of the swing, at double speed, then back to 0.
Every partial animation in the game plays the wrong span at 60 FPS the same
way (the item pick-ups, the pull on wcrm1pl's lever next to it, ...).

FFNx fixes this in `opcode_script_partial_animation_wrapper`
(src/ff7/field/opcode.cpp), installed for CANM!1/CANM!2/CANIM1/CANIM2 when
the limiter is 60 FPS: after the stock handler runs, for a model whose
animation state took the set-up path (types 0/1/3),

    currentFrame = 16 * first * 2            (CANIM: / speed)
    lastFrame    = (last * 2 + 1) / speed,  clamped to frames - 1

This is that, as one cave per handler at the point where the stock body has
just stored both fields (x86 0x6150DD in CANM!, 0x614D7E in CANIM -- the
`mov al, [0xCC0890]` that re-reads the opcode). The set-up path is the only
way to reach either point, which is FFNx's type 0/1/3 gate for free. The
continuity tweak FFNx adds for back-to-back calls is not reproduced; it only
avoids re-showing one 1/16 sub-frame.

Guest data (x86 addresses, all read through the translator):
    [0xCC0964]            current entity           byte
    [0xCBFB70 + ent]      its model (0xFF = none)  byte
    [0xCC0CF8 + ent*2]    script position          word
    [0xCBF5E8]            script base              dword
      +pos+2/+3/+4        first, last, speed       bytes
    [0xCC0B60] + m*0x88   field_event_data         +0x68 current, +0x6A last
    [0xCFF738] + m*0x190  animation data, +0x178 -> anim object, +8 frames

    SEVENTH_NX_CANIM60=0   off (only ever written on a 60 FPS build)
"""
from __future__ import annotations

import os
import struct

import a64 as A
from ff7nx_analog_cave import Asm

ENV = 'SEVENTH_NX_CANIM60'
VERSION = 'canim60-580'
TRANSLATE = 0x10FC3A0
MULT = 2
COND_EQ, COND_GT = 0, 12

# (name, hook, stock word there, divide the first frame by speed?)
SITES = (('CANM!', 0x96301C, 0x51035260, False),
         ('CANIM', 0x9624F8, 0x51035260, True))
# words around each hook that pin it to the handler we disassembled
ANCHORS = (
    (0x963018, 0x79000015, 'strh w21, [x0]   lastFrame store, CANM!'),
    (0x963034, 0x7102C51F, 'cmp w8, #0xb1    CANM!'),
    (0x9624F4, 0x79000015, 'strh w21, [x0]   lastFrame store, CANIM'),
    (0x962510, 0x7102C11F, 'cmp w8, #0xb0    CANIM'),
    (0x9626AC, 0x52812C93, 'mov w19, #0x964  CANM! entity ptr'),
    (0x961B4C, 0x52812C93, 'mov w19, #0x964  CANIM entity ptr'),
)

G_ENT = 0xCC0964
G_MODEL = 0xCBFB70
G_POS = 0xCC0CF8
G_SCRIPT = 0xCBF5E8
G_EVENT = 0xCC0B60
G_ANIM = 0xCFF738


def enabled(env=None):
    e = os.environ if env is None else env
    return e.get(ENV, '').strip().lower() not in ('0', 'off', 'no', 'false')


def build_cave(hook, divide):
    def b(at, addr):
        a = Asm(at, addr)
        a.emit(A.stp64_pre(29, 30, 31, -0x40))
        a.emit(A.stp64_off(19, 20, 31, 0x10))
        a.emit(A.stp64_off(21, 22, 31, 0x20))
        a.emit(A.stp64_off(23, 24, 31, 0x30))

        def load(dst, guest, width, add=None, add_lsl=0):
            for w in A.movz_movk(0, guest):
                a.emit(w)
            if add is not None:
                a.emit(A.add_reg_lsl(0, 0, add, add_lsl) if add_lsl
                       else A.add_reg(0, 0, add))
            a.emit(A.bl(a.pc(), TRANSLATE))
            a.emit({1: A.ldrb, 2: A.ldrh, 4: A.ldr}[width](dst, 0, 0))

        load(19, G_ENT, 1)                              # ent
        load(20, G_MODEL, 1, add=19)                    # model
        a.emit(A.cmp_imm(20, 0xFF))
        a.bcond('out', COND_EQ)
        load(21, G_POS, 2, add=19, add_lsl=1)           # pos
        load(22, G_SCRIPT, 4)
        a.emit(A.add_reg(22, 22, 21))                   # guest opcode addr
        a.emit(A.add_imm(0, 22, 4))
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldrb(24, 0, 0))                        # speed
        a.cbz(24, 'out')
        a.emit(A.add_imm(0, 22, 3))
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldrb(23, 0, 0))                        # last
        a.emit(A.add_imm(0, 22, 2))
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldrb(21, 0, 0))                        # first
        # current = 16 * first * MULT (/ speed for CANIM)
        a.emit(A.lsl(21, 21, 4 + (MULT.bit_length() - 1)))
        if divide:
            a.emit(A.udiv(21, 21, 24))
        # last = (last * MULT + 1) / speed
        a.emit(A.lsl(23, 23, MULT.bit_length() - 1))
        a.emit(A.add_imm(23, 23, 1))
        a.emit(A.udiv(23, 23, 24))
        # clamp to the loaded animation's frame count - 1
        for w in A.movz_movk(0, G_ANIM):
            a.emit(w)
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldr(22, 0, 0))                         # anim table (guest)
        a.cbz(22, 'store')
        a.emit(A.movz(9, 0x190))
        a.emit(A.mul(9, 20, 9))
        a.emit(A.add_reg(0, 22, 9))
        a.emit(A.add_imm(0, 0, 0x178))
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldr(0, 0, 0))                          # anim object (guest)
        a.cbz(0, 'store')
        a.emit(A.add_imm(0, 0, 8))
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldr(9, 0, 0))                          # number_of_frames
        a.cbz(9, 'store')
        a.emit(A.sub_imm(9, 9, 1))
        a.emit(A.cmp_reg(23, 9))
        a.emit(A.csel(23, 9, 23, COND_GT))              # signed min
        a.label('store')
        load(22, G_EVENT, 4)
        a.emit(A.movz(9, 0x88))
        a.emit(A.mul(9, 20, 9))
        a.emit(A.add_reg(22, 22, 9))                    # guest event data
        a.emit(A.add_imm(0, 22, 0x68))
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.strh(21, 0, 0))
        a.emit(A.add_imm(0, 22, 0x6A))
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.strh(23, 0, 0))
        a.label('out')
        a.emit(A.ldp64_off(23, 24, 31, 0x30))
        a.emit(A.ldp64_off(21, 22, 31, 0x20))
        a.emit(A.ldp64_off(19, 20, 31, 0x10))
        a.emit(A.ldp64_post(29, 30, 31, 0x40))
        a.emit(0x51035260)                              # sub w0, w19, #0xd4
        a.b('back')
        a.lab['back'] = hook + 4
        return a.resolve()
    return b


def _w(t, va):
    return struct.unpack_from('<I', t, va)[0]


def patch_text(text, place):
    t = bytes(text)
    bad = ['canim60 anchor +%#x (%s) holds %08X' % (va, n, _w(t, va))
           for va, want, n in ANCHORS if _w(t, va) != want]
    bad += ['canim60 hook +%#x holds %08X' % (hook, _w(t, hook))
            for _n, hook, stock, _d in SITES if _w(t, hook) != stock]
    if bad:
        raise ValueError('; '.join(bad))
    written = {}
    for _n, hook, _stock, divide in SITES:
        entry, placed = place(build_cave(hook, divide))
        for va, word in placed.items():
            struct.pack_into('<I', text, va, word)
        written.update(placed)
        word = A.b(hook, entry)
        struct.pack_into('<I', text, hook, word)
        written[hook] = word
    return written


def apply_to_nso(src, dest, log=lambda *_: None, stock=None):
    import ff7nx_audio_cave as AC
    import ff7nx_deadspace as DS
    with open(src, 'rb') as fh:
        blob = fh.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    lo, hi = DS.part(src, 'canim', stock)
    bump = DS.Bump(lo, hi)

    def place(bld):
        before = dict(bump.placed)
        e = bump.put(bld)
        return e, {k: v for k, v in bump.placed.items() if k not in before}
    written = patch_text(text, place)
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, 0)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as fh:
        fh.write(out)
    return {'words': len(written)}
