#!/usr/bin/env python3
"""
ff7nx_analog_cave.py -- the two caves for 360 degree field movement.

See ff7nx_analog.py for the mechanism and the direction model this implements.
Everything here is emitted through a tiny label-resolving assembler so the
branches stay readable; the words it produces are executed for real in
test_analog_cave.py.

REGISTER DISCIPLINE, which is the whole safety case
===================================================
The field cave calls the recompiler's guest->host translator three times, and
that call is modelled -- correctly -- as destroying x0..x18, x30 and the
flags. So every value that has to survive a translator call lives in x19..x28,
and those are saved and restored by the cave itself.

x0 at the hook is the return value of the `bl` immediately before it, and the
recompiled code overwrites it four instructions later without reading it, so
it is dead. Nothing else in x0..x18 can be live across a `bl` boundary either,
which is exactly why this hook site was chosen.
"""
import struct

import a64 as A
import ff7nx_analog as AN

WZR = 31
SP = 31


# ------------------------------------------------------------------ asm
class Asm:
    """
    Emit words with symbolic labels.

    `at` is the address of word 0. `addr`, if given, maps a word INDEX to its
    address -- that is what lets the same builder emit a cave chained through
    scattered padding holes: labels, branches and adrp pages all resolve
    against where each word really lands rather than against a contiguous
    block that does not exist. Without it the layout is contiguous from `at`,
    which is what the probe pass and the emulator tests use.
    """

    def __init__(self, at, addr=None):
        self.at = at
        self.addr = addr if addr is not None else (lambda i: at + 4 * i)
        self.w = []
        self.lab = {}
        self.fix = []

    def pc(self):
        return self.addr(len(self.w))

    def label(self, name):
        self.lab[name] = self.pc()

    def emit(self, word):
        self.w.append(word)

    def b(self, name):
        self.fix.append((len(self.w), name, 'b'))
        self.w.append(0)

    def bcond(self, name, cond):
        self.fix.append((len(self.w), name, cond))
        self.w.append(0)

    def cbz(self, rt, name, wide=False):
        self.fix.append((len(self.w), name, ('cbz64' if wide else 'cbz', rt)))
        self.w.append(0)

    def resolve(self):
        for idx, name, kind in self.fix:
            here = self.addr(idx)
            tgt = self.lab[name]
            if kind == 'b':
                self.w[idx] = A.b(here, tgt)
            elif isinstance(kind, tuple) and kind[0] == 'cbz':
                self.w[idx] = A.cbz(kind[1], here, tgt)
            elif isinstance(kind, tuple) and kind[0] == 'cbz64':
                self.w[idx] = cbz64(kind[1], here, tgt)
            else:
                self.w[idx] = A.bcond(here, tgt, kind)
        return self.w


# extra encodings this cave needs and a64.py does not carry
def ldr_s(rt, rn, imm):      return 0xBD400000 | ((imm >> 2) << 10) | (rn << 5) | rt
def fsub_s(rd, rn, rm):      return 0x1E203800 | (rm << 16) | (rn << 5) | rd
def fcvtzs_fix(rd, rn, fb):  return 0x1E180000 | ((64 - fb) << 10) | (rn << 5) | rd
def sdiv(rd, rn, rm):        return 0x1AC00C00 | (rm << 16) | (rn << 5) | rd
def eor_reg(rd, rn, rm):     return 0x4A000000 | (rm << 16) | (rn << 5) | rd
def orr_reg(rd, rn, rm):     return 0x2A000000 | (rm << 16) | (rn << 5) | rd
def sub_reg(rd, rn, rm):     return 0x4B000000 | (rm << 16) | (rn << 5) | rd
def sub_imm(rd, rn, imm):    return 0x51000000 | (imm << 10) | (rn << 5) | rd
def add_shifted(rd, rn, rm, sh): return 0x0B000000 | (sh << 10) | (rm << 16) | (rn << 5) | rd
def ldur_w(rt, rn, imm):     return 0xB8400000 | ((imm & 0x1FF) << 12) | (rn << 5) | rt
def ldursh_w(rt, rn, imm):   return 0x78C00000 | ((imm & 0x1FF) << 12) | (rn << 5) | rt
def sturh(rt, rn, imm):      return 0x78000000 | ((imm & 0x1FF) << 12) | (rn << 5) | rt
def csel(rd, rn, rm, cond):  return 0x1A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd
def ldrsb_w(rt, rn, imm):    return 0x39C00000 | (imm << 10) | (rn << 5) | rt


def cbz64(rt, frm, to):
    # 64-BIT cbz. The 32-bit form is wrong for a pointer: a host address may
    # legitimately have a zero low word (0x700000000 is exactly that), and the
    # cave would then bail on a perfectly good input object.
    return 0xB4000000 | ((((to - frm) >> 2) & 0x7FFFF) << 5) | rt

COND_EQ, COND_NE, COND_GE, COND_LT, COND_LE = 0, 1, 10, 11, 13

FRAME = 0x30


def build_field_cave(cave, snap_va, atan_va, addr=None, diag=False):
    """
    `snap_va` and `atan_va` are the module addresses of the two lookup tables.
    They live in .rodata, not in the cave: a chained cave is cut into two- and
    three-word runs and a table has to be contiguous to be indexed.

    Only THREE registers have to survive the translator calls -- x19 (the
    scratch block), x22 (the offset) and x28 (the field id) -- so those are the
    only callee-saved ones the cave preserves. Everything else is computed in
    x0..x17 and is dead before the first `bl`.

    ADDRESS DISCIPLINE, which is the correctness case (see ff7nx_analog.py):
    the recompiler's translator is a 4 KB PAGE TABLE, so a host pointer it
    returns is only valid for the page it was asked about. Every guest address
    this cave wants is therefore built in GUEST space and translated whole, and
    every translated pointer is dereferenced at offset 0 or at an offset inside
    the same aligned dword. Nothing is ever indexed off a translated pointer.
    """
    a = Asm(cave, addr)
    a.emit(A.stp64_pre(29, 30, SP, -FRAME))
    a.emit(0xA9000000 | ((16 // 8) << 15) | (22 << 10) | (SP << 5) | 19)
    a.emit(A.str64(28, SP, 32))
    a.emit(A.adrp(19, a.pc(), AN.ANALOG_BASE & ~0xFFF))
    a.emit(A.add_imm64(19, 19, AN.ANALOG_BASE & 0xFFF))
    if diag:
        # DIAGNOSTIC: no input object, no key buffer, no stick -- just a fixed
        # 45 degrees, so the only thing under test is "does this cave run, and
        # does its write reach the byte the game reads". See analog_diag().
        a.emit(A.movz(22, 32))
        a.b('write')
    # ---- the input object, resolved the way the port's own getters do ----
    # Instruction for instruction the tail at 0x1DC0 that GetAxis (0x1D80) and
    # IsButtonHeld (0x1AD0) share, plus a null check at every link instead of
    # only the last one. This is the object whose floats the DirectInput
    # emulation thresholds into the four direction scancodes read below, so
    # the stick and the key mask cannot come from different devices.
    a.emit(A.adrp(9, a.pc(), AN.INPUT_GOT & ~0xFFF))
    a.emit(A.ldr64(9, 9, AN.INPUT_GOT & 0xFFF))
    a.cbz(9, 'out', wide=True)
    for off in AN.INPUT_CHAIN:
        a.emit(A.ldr64(9, 9, off))
        a.cbz(9, 'out', wide=True)
    # ---- stick -> fixed point (1/4096) ---------------------------------
    # Split positive/negative halves of the LEFT stick, measured at 0x111C164
    # and pinned to axis ids 0x10..0x13 by 0x111BF60.
    a.emit(ldr_s(0, 9, AN.OBJ_UP))
    a.emit(ldr_s(1, 9, AN.OBJ_DOWN))
    a.emit(ldr_s(2, 9, AN.OBJ_RIGHT))
    a.emit(ldr_s(3, 9, AN.OBJ_LEFT))
    a.emit(fsub_s(0, 0, 1))             # iy = up - down
    a.emit(fsub_s(2, 2, 3))             # ix = right - left
    a.emit(fcvtzs_fix(11, 0, 12))       # w11 = iy
    a.emit(fcvtzs_fix(10, 2, 12))       # w10 = ix
    # ---- key mask -> w12, snapped direction -> w13 ---------------------
    a.emit(A.adrp(9, a.pc(), AN.KEYBUF & ~0xFFF))
    a.emit(A.add_imm64(9, 9, AN.KEYBUF & 0xFFF))
    a.emit(A.ldrb(12, 9, AN.DIK_RIGHT))
    a.emit(A.ldrb(14, 9, AN.DIK_UP))
    a.emit(A.ldrb(15, 9, AN.DIK_LEFT))
    a.emit(A.ldrb(16, 9, AN.DIK_DOWN))
    for r in (12, 14, 15, 16):          # the port writes 0x80, not 1
        a.emit(A.lsr(r, r, 7))
    a.emit(add_shifted(12, 12, 14, 1))
    a.emit(add_shifted(12, 12, 15, 2))
    a.emit(add_shifted(12, 12, 16, 3))
    a.emit(A.adrp(9, a.pc(), snap_va & ~0xFFF))
    a.emit(A.add_imm64(9, 9, snap_va & 0xFFF))
    a.emit(A.add_reg64(9, 9, 12))
    a.emit(A.ldrb(13, 9, 0))
    a.emit(A.cmp_imm(13, 0xFF))
    a.bcond('zero_off', COND_EQ)
    # ---- |ix|, |iy| ----------------------------------------------------
    a.emit(A.asr(2, 10, 31))
    a.emit(eor_reg(0, 10, 2))
    a.emit(sub_reg(0, 0, 2))            # w0 = ax
    a.emit(A.asr(3, 11, 31))
    a.emit(eor_reg(1, 11, 3))
    a.emit(sub_reg(1, 1, 3))            # w1 = ay
    a.emit(orr_reg(4, 0, 1))
    a.cbz(4, 'zero_off')
    # ---- one divide, both octant halves --------------------------------
    # hi = max(ax,ay), lo = min(ax,ay). The flags from this compare are still
    # live at the `csel` further down: sdiv, lsl, add and ldrb do not set them.
    a.emit(A.cmp_reg(0, 1))
    a.emit(csel(6, 0, 1, COND_GE))      # hi
    a.emit(csel(7, 1, 0, COND_GE))      # lo
    a.emit(A.lsl(4, 7, 7))
    a.emit(A.add_reg(4, 4, 6))
    a.emit(A.lsl(5, 6, 1))
    a.emit(sdiv(4, 4, 5))
    a.emit(A.adrp(9, a.pc(), atan_va & ~0xFFF))
    a.emit(A.add_imm64(9, 9, atan_va & 0xFFF))
    a.emit(A.add_reg64(9, 9, 4))
    a.emit(A.ldrb(4, 9, 0))             # a = atan(lo/hi), 0..32
    a.emit(A.movz(5, 64))
    a.emit(sub_reg(5, 5, 4))            # 64 - a
    a.emit(csel(4, 4, 5, COND_GE))      # |y| bigger -> reflect about 45 deg
    # ---- quadrant ------------------------------------------------------
    a.emit(A.movz(5, 128))
    a.emit(sub_reg(5, 5, 4))            # 128 - a
    a.emit(A.cmp_imm(10, 0))
    a.emit(csel(4, 5, 4, COND_LT))      # ix < 0
    a.emit(sub_reg(5, WZR, 4))          # -a
    a.emit(A.cmp_imm(11, 0))
    a.emit(csel(4, 5, 4, COND_LT))      # iy < 0
    a.emit(A.and_mask(4, 4, 8))
    # ---- offset --------------------------------------------------------
    a.emit(sub_reg(22, 4, 13))
    a.emit(A.and_mask(22, 22, 8))
    a.emit(A.cmp_imm(22, 128))
    a.bcond('write', COND_LE)
    a.emit(sub_imm(22, 22, 256))
    a.b('write')
    a.label('zero_off')
    a.emit(A.mov_reg(22, WZR))
    # ---- write the control direction -----------------------------------
    # Four translator calls, each handed a COMPLETE guest address. w28 holds
    # the field id across the last three of them, which is why it is saved.
    a.label('write')
    a.emit(A.movz(0, AN.FIELD_ID_GUEST & 0xFFFF))
    a.emit(A.movk_hi(0, AN.FIELD_ID_GUEST >> 16))
    a.emit(A.bl(a.pc(), AN.TRANSLATE))
    a.emit(A.ldrh(28, 0, 0))                    # w28 = field_id
    # is a field level actually loaded? 0xCFF454 keeps a stale pointer after
    # the level buffer is freed, so this is the guard FFNx makes with its
    # `level_data != nullptr` test.
    a.emit(A.movz(0, AN.LEVEL_PTR_GUEST & 0xFFFF))
    a.emit(A.movk_hi(0, AN.LEVEL_PTR_GUEST >> 16))
    a.emit(A.bl(a.pc(), AN.TRANSLATE))
    a.emit(A.ldr(0, 0, 0))
    a.cbz(0, 'out')
    # the game's own cached `level_data + triggers_offset + 4`
    a.emit(A.movz(0, AN.TRIGGERS_PTR_GUEST & 0xFFFF))
    a.emit(A.movk_hi(0, AN.TRIGGERS_PTR_GUEST >> 16))
    a.emit(A.bl(a.pc(), AN.TRANSLATE))
    a.emit(A.ldr(0, 0, 0))
    a.cbz(0, 'out')
    # translate the address of the BYTE, not of the section it sits in
    a.emit(A.add_imm(0, 0, AN.CONTROL_DIR_OFF))
    a.emit(A.bl(a.pc(), AN.TRANSLATE))          # x0 = host &control_direction
    a.emit(A.ldr(3, 19, 0))                     # saved field id
    a.emit(A.ldr(4, 19, 4))                     # captured flag
    a.cbz(4, 'capture')
    a.emit(A.cmp_reg(28, 3))
    a.bcond('use_saved', COND_EQ)
    a.label('capture')
    a.emit(ldrsb_w(5, 0, 0))                    # the field's own value
    a.emit(A.str_(5, 19, 8))
    a.emit(A.str_(28, 19, 0))
    a.emit(A.movz(6, 1))
    a.emit(A.str_(6, 19, 4))
    a.b('have_base')
    a.label('use_saved')
    a.emit(A.ldr(5, 19, 8))
    a.label('have_base')
    a.emit(A.add_reg(5, 5, 22))
    a.emit(A.strb(5, 0, 0))                     # one byte, like the game reads
    a.label('out')
    a.emit(0xA9400000 | ((16 // 8) << 15) | (22 << 10) | (SP << 5) | 19)
    a.emit(A.ldr64(28, SP, 32))
    a.emit(A.ldp64_post(29, 30, SP, FRAME))
    a.emit(AN.FIELD_ORIG)
    a.emit(A.b(a.pc(), AN.FIELD_HOOK + 4))
    return a.resolve()
