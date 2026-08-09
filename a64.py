"""Minimal ARM64 encoder. Every form here is checked against capstone by
test_a64() -- nothing is trusted on the strength of my bit-twiddling."""
SP, WZR, XZR = 31, 31, 31

def ldr(rt, rn, imm=0):    return 0xB9400000 | ((imm >> 2) << 10) | (rn << 5) | rt
def str_(rt, rn, imm=0):   return 0xB9000000 | ((imm >> 2) << 10) | (rn << 5) | rt
def ldrh(rt, rn, imm=0):   return 0x79400000 | ((imm >> 1) << 10) | (rn << 5) | rt
def strh(rt, rn, imm=0):   return 0x79000000 | ((imm >> 1) << 10) | (rn << 5) | rt
def ldrsh(rt, rn, imm=0):  return 0x79C00000 | ((imm >> 1) << 10) | (rn << 5) | rt
def ldrb(rt, rn, imm=0):   return 0x39400000 | (imm << 10) | (rn << 5) | rt
def strb(rt, rn, imm=0):   return 0x39000000 | (imm << 10) | (rn << 5) | rt
def ldr64(rt, rn, imm=0):  return 0xF9400000 | ((imm >> 3) << 10) | (rn << 5) | rt
def str64(rt, rn, imm=0):  return 0xF9000000 | ((imm >> 3) << 10) | (rn << 5) | rt

def add_imm(rd, rn, imm):   return 0x11000000 | (imm << 10) | (rn << 5) | rd
def add_imm64(rd, rn, imm): return 0x91000000 | (imm << 10) | (rn << 5) | rd
def sub_imm64(rd, rn, imm): return 0xD1000000 | (imm << 10) | (rn << 5) | rd
def add_reg(rd, rn, rm):    return 0x0B000000 | (rm << 16) | (rn << 5) | rd
def add_reg64(rd, rn, rm):  return 0x8B000000 | (rm << 16) | (rn << 5) | rd
def cmp_reg(rn, rm):        return 0x6B000000 | (rm << 16) | (rn << 5) | WZR
def cmp_imm(rn, imm):       return 0x71000000 | (imm << 10) | (rn << 5) | WZR
def and_mask(rd, rn, bits):
    """AND Wd, Wn, #(2**bits - 1)  -- N=0, immr=0, imms=bits-1."""
    return 0x12000000 | (0 << 16) | ((bits - 1) << 10) | (rn << 5) | rd
def lsl(rd, rn, sh):  return 0x53000000 | (((32 - sh) % 32) << 16) | ((31 - sh) << 10) | (rn << 5) | rd
def asr(rd, rn, sh):  return 0x13000000 | (sh << 16) | (31 << 10) | (rn << 5) | rd
def movz(rd, imm16):  return 0x52800000 | (imm16 << 5) | rd
def movk_hi(rd, imm16): return 0x72A00000 | (imm16 << 5) | rd
def adrp(rd, pc, target):
    imm = (target >> 12) - (pc >> 12)
    return 0x90000000 | ((imm & 3) << 29) | (((imm >> 2) & 0x7FFFF) << 5) | rd
def _rel(frm, to, bits, what):
    """
    Branch displacement in words, range-checked.

    Every branch encoder here used to mask the offset, which silently wraps a
    target that is too far away into a branch somewhere else entirely. That
    was harmless while every cave was one contiguous block a few hundred bytes
    long. It stopped being harmless when caves started being chained through
    reclaimed padding, where the two halves of a `b.cond` can legitimately end
    up megabytes apart -- so out of range now raises instead of encoding a
    wrong answer, and the caller either picks closer holes or splits the
    branch.
    """
    if (to - frm) & 3:
        raise ValueError('%s target 0x%X is not 4-byte aligned' % (what, to))
    off = (to - frm) >> 2
    lim = 1 << (bits - 1)
    if not -lim <= off < lim:
        raise ValueError('%s from 0x%X to 0x%X is %d bytes, outside the '
                         '+/-%d byte range of this form'
                         % (what, frm, to, to - frm, lim * 4))
    return off & ((1 << bits) - 1)


def b(frm, to):       return 0x14000000 | _rel(frm, to, 26, 'b')
def bl(frm, to):      return 0x94000000 | _rel(frm, to, 26, 'bl')
def bcond(frm, to, cond): return 0x54000000 | (_rel(frm, to, 19, 'b.cond') << 5) | cond
def cbz(rt, frm, to):     return 0x34000000 | (_rel(frm, to, 19, 'cbz') << 5) | rt
def cbz64(rt, frm, to):
    """CBZ Xt, label -- the 64-bit form. `cbz` above is always the W form;
    this exists because testing a pointer for zero must check all 64 bits,
    not just the low 32 (a flag *byte*, which is all `cbz` has ever been
    used for elsewhere in this file, never needs the distinction)."""
    return 0xB4000000 | (_rel(frm, to, 19, 'cbz') << 5) | rt
def cbnz(rt, frm, to):    return 0x35000000 | (_rel(frm, to, 19, 'cbnz') << 5) | rt
EQ, NE, GE, LE, LT, HS = 0x0, 0x1, 0xA, 0xD, 0xB, 0x2
HI = 0x8        # unsigned higher (C set and Z clear) -- used by the field-wait
                # overflow guard, which compares two values it has already
                # masked to 16 bits, so the unsigned test is the correct one.

def mov_reg(rd, rm):  return 0x2A0003E0 | (rm << 16) | rd      # ORR Wd, WZR, Wm


# ------------------------------------------------------------------ added for
# the pause-throttle caves. Same rule as everything above: capstone-checked in
# test_a64.py before any of it is allowed near a build.

def tbnz(rt, bit, frm, to):
    """
    TBNZ Wt, #bit, label -- branch if bit `bit` of Wt is set.

    Used instead of `tst`+`b.ne` because it touches no flags, so the throttle
    cave can sit anywhere without having to reason about what the surrounding
    translated code expects NZCV to hold. `bit` must be 0..31: the W form has
    b5 = 0, and a bit >= 32 would silently encode the X form against a 32-bit
    register.
    """
    if not 0 <= bit < 32:
        raise ValueError('tbnz bit %d is not in the W-register range' % bit)
    return (0x37000000 | (bit << 19) | (_rel(frm, to, 14, 'tbnz') << 5) | rt)


def adr(rd, frm, to):
    """ADR Xd, label -- PC-relative, +/-1MB. Reaches a table inside its cave."""
    imm = to - frm
    if not -(1 << 20) <= imm < (1 << 20):
        raise ValueError('adr target is %d bytes away, out of +/-1MB range'
                         % imm)
    return 0x10000000 | ((imm & 3) << 29) | (((imm >> 2) & 0x7FFFF) << 5) | rd


def lsr(rd, rn, sh):
    """LSR Wd, Wn, #sh -- UBFM Wd, Wn, #sh, #31."""
    if not 0 <= sh < 32:
        raise ValueError('lsr shift %d out of range' % sh)
    return 0x53000000 | (sh << 16) | (31 << 10) | (rn << 5) | rd


def stp64_pre(rt, rt2, rn, imm):
    """STP Xt, Xt2, [Xn, #imm]! -- pre-indexed, imm a multiple of 8."""
    if imm % 8 or not -512 <= imm < 512:
        raise ValueError('stp pre-index %d invalid' % imm)
    return (0xA9800000 | (((imm // 8) & 0x7F) << 15) | (rt2 << 10)
            | (rn << 5) | rt)


def ldp64_post(rt, rt2, rn, imm):
    """LDP Xt, Xt2, [Xn], #imm -- post-indexed, imm a multiple of 8."""
    if imm % 8 or not -512 <= imm < 512:
        raise ValueError('ldp post-index %d invalid' % imm)
    return (0xA8C00000 | (((imm // 8) & 0x7F) << 15) | (rt2 << 10)
            | (rn << 5) | rt)


def ret(rn=30):
    return 0xD65F0000 | (rn << 5)


def ldr_post(rt, rn, imm):
    """LDR Wt, [Xn], #imm -- 32-bit load, post-indexed. Walks a word table."""
    if not -256 <= imm < 256:
        raise ValueError('ldr post-index %d out of range' % imm)
    return 0xB8400400 | ((imm & 0x1FF) << 12) | (rn << 5) | rt


def mul(rd, rn, rm):
    """
    MUL Wd, Wn, Wm -- the MADD alias with Ra = WZR (Wd = Wn*Wm, low 32 bits).

    Only the alias is encoded/decoded (Ra fixed at WZR), not general MADD --
    that is the one shape the shared-prologue table lookup needs (idx *
    runtime stride), and keeping the decoder exact to what is emitted is the
    same discipline every other form here follows.
    """
    return 0x1B007C00 | (rm << 16) | (rn << 5) | rd

# ---------------------------------------------------------------- additions
# Added for the scripted-movement wrapper (ff7nx_smooth.py). Every one of
# these is round-tripped through capstone by test_smooth_wrap.py, so an
# encoding typo cannot reach a build.

def sub_reg(rd, rn, rm):    return 0x4B000000 | (rm << 16) | (rn << 5) | rd
def sub_imm(rd, rn, imm):   return 0x51000000 | (imm << 10) | (rn << 5) | rd


def eor_imm1(rd, rn):
    """EOR Wd, Wn, #1 -- flip the low bit. imms/immr for the 1-bit mask."""
    return 0x52000000 | (0 << 16) | (0 << 10) | (rn << 5) | rd


def csel(rd, rn, rm, cond):
    """CSEL Wd, Wn, Wm, cond -- Wn if cond else Wm."""
    return 0x1A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd


def orr_lsl(rd, rn, rm, sh):
    """ORR Wd, Wn, Wm, LSL #sh."""
    return 0x2A000000 | (sh << 10) | (rm << 16) | (rn << 5) | rd


def add_reg64_lsl(rd, rn, rm, sh):
    """ADD Xd, Xn, Xm, LSL #sh."""
    return 0x8B000000 | (sh << 10) | (rm << 16) | (rn << 5) | rd


def stp64_off(rt, rt2, rn, imm):
    """STP Xt, Xt2, [Xn, #imm] -- imm a multiple of 8."""
    return 0xA9000000 | (((imm >> 3) & 0x7F) << 15) | (rt2 << 10) | (rn << 5) | rt


def ldp64_off(rt, rt2, rn, imm):
    """LDP Xt, Xt2, [Xn, #imm] -- imm a multiple of 8."""
    return 0xA9400000 | (((imm >> 3) & 0x7F) << 15) | (rt2 << 10) | (rn << 5) | rt


def tbz(rt, bit, frm, to):
    off = _rel(frm, to, 14, 'tbz')
    return (0x36000000 | ((bit & 0x1F) << 19) | ((off & 0x3FFF) << 5) | rt
            | (0x80000000 if bit >= 32 else 0))


def add_reg_lsr(rd, rn, rm, sh):
    """ADD Wd, Wn, Wm, LSR #sh -- the +1-if-negative half of trunc-toward-0."""
    return 0x0B400000 | (sh << 10) | (rm << 16) | (rn << 5) | rd


def movz_movk(rd, val):
    """The two words that build a full 32-bit constant in Wd."""
    return [movz(rd, val & 0xFFFF), movk_hi(rd, (val >> 16) & 0xFFFF)]


# --- 64-bit compare / select / bitfield, and the shifted 32-bit add -------
# Added for ff7nx_uiclip, which has to scale the LOW HALF of two packed
# 64-bit rect words while leaving the high half alone, and choose between the
# scaled and unscaled pair without branching (a branch-free cave is one
# ff7nx_cave.emit_chained can scatter across padding holes with no custom
# walker). Every one of these is checked against capstone in that module's
# --verify, so a typo here cannot reach an image.
def add_reg_lsl(rd, rn, rm, sh):
    """add Wd, Wn, Wm, LSL #sh"""
    return 0x0B000000 | (rm << 16) | (sh << 10) | (rn << 5) | rd


def bfi64(rd, rn, lsb, width):
    """
    bfi Xd, Xn, #lsb, #width -- insert Xn[width-1:0] at Xd[lsb+width-1:lsb],
    PRESERVING every other bit of Xd. capstone renders the lsb=0 form as
    `bfxil`; same encoding, and the ARM ARM says so.
    """
    immr = (-lsb) % 64
    imms = width - 1
    return 0xB3400000 | (immr << 16) | (imms << 10) | (rn << 5) | rd


def cmp_reg64(rn, rm):
    """cmp Xn, Xm  (subs XZR, Xn, Xm)"""
    return 0xEB000000 | (rm << 16) | (rn << 5) | 31


def ccmp_reg64(rn, rm, nzcv, cond):
    """
    ccmp Xn, Xm, #nzcv, cond -- compare if `cond` holds, else ADOPT #nzcv as
    the flags. Chaining two of these is how the cave tests a 128-bit rect
    for equality in two instructions and no branch.
    """
    return 0xFA400000 | (rm << 16) | (cond << 12) | (rn << 5) | nzcv


def csel64(rd, rn, rm, cond):
    """csel Xd, Xn, Xm, cond -- Xd = cond ? Xn : Xm"""
    return 0x9A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd


EQ = 0
