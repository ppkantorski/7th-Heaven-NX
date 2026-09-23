#!/usr/bin/env python3
"""
ff7nx_facial.py -- the advanced facial-animation RUNTIME.

BUILD 443 taught flevel.lgp to take new entries; BUILD 444 shipped the art
(1,013 textures, 994 of them new names). Neither built the half that makes an
index mean anything, so on every build so far an eye index above the stock two
blink modes, and every mouth index, did nothing at all. This is that half.

WHAT THE FEATURE ACTUALLY IS, MEASURED RATHER THAN QUOTED
=========================================================
FFNx replaces `field_blink_eye_sub_649B50` wholesale and, on every call,
rebuilds `eye_<model>_<n>.TEX` / `eye_<model>r_<n>.TEX` / `mouth_<model>_<n>`
from the live model name, probes the filesystem for each, and reloads the
texture.  The indices it uses are the BLINK MODES the engine already drives:

    mode 1  eyes open      mode 2  eyes closed

so `eye_<model>_2` is the model's own closed eye, and 3..5 are expressions a
KAWAI EYETX opcode can ask for.

Three numbers decided the shape of this file, and all three were measured on
the archives and scripts this build actually ships:

  * `fb/flevel` carries a per-model eye set for 164 of the 388 HRC models the
    703 shipped fields load -- 1,054 of 5,445 model-loader slots.  Index 2 is
    present for every one of those 164.  The other 224 (Cloud, Tifa, Barret
    and the rest of the cast) use the GENERIC names, and the mod replaces
    those in place, so they already look right with no runtime at all.
  * the 703 fields contain 76 KAWAI EYETX instructions.  SIX set a mouth
    index.  TWO set an eye index above 2, and the stock opcode handler drops
    both before the blink routine is ever called (see THE GUARD below).
  * the module has 506 bytes of contiguous read-only data left after the
    shipping preset, so a 164-entry name table CANNOT be placed.

The third number is why this file has no lookup table.  `field_load_model_tex`
answers the existence question itself: on a name flevel.lgp does not have it
logs `ERROR: COULD NOT LOAD TEXTURE DATA FILE %s`, frees its own context and
returns NULL (x86 0x6705B1).  Nothing faults.  So the runtime PROBES once per
model per index and remembers the verdict in a bitmask, which is both smaller
than a table and automatically correct for whatever art is installed --
including a load order that does not ship the Ninostyle set at all.

THE GUARD, AND WHY IT IS NOT PATCHED
====================================
The stock EYETX handler (x86 0x6203A3) refuses any index outside 1..2:

    cmp ecx,1 ; jl skip ; cmp edx,2 ; jg skip     (left)
    cmp eax,1 ; jl skip ; cmp ecx,2 ; jg skip     (right)

and jumps past the blink call entirely.  Widening it looked like the
prerequisite for expressions.  It is not, and widening it would not have been
enough on its own: the per-frame blink loop (x86 0x639A9F/0x639AFE) rewrites
both modes to 2/2 or 1/1 EVERY FRAME, so an expression that arrived through a
widened guard would survive exactly one frame and then be overwritten.

So the expression is held in OUR state instead.  `CAVE_KAWAI` reads the
opcode's parameters at the handler's entry -- before the guard -- and latches
them; `CAVE_BLINK` applies the latch on every subsequent frame until the next
EYETX clears it.  The result is the one the guard patch was supposed to buy,
the expression persists instead of flickering for a frame, and not one byte of
stock script-opcode behaviour changes.

THE FOUR HOOKS
==============
    CAVE_KAWAI     x86 0x620136 entry   latch left/right/mouth from the script
    CAVE_BLINK     x86 0x649B50 entry   decide the index, park the texture
    CAVE_LOAD      (tail of CAVE_BLINK) build the names, probe, load
    CAVE_MOUTH     after the sub_6A2736 call inside the blink body
    CAVE_FREE      x86 0x63E1D8 entry   give every texture back

OWNERSHIP, WHICH IS THE PART THAT COULD LEAK
============================================
`field_load_model_eye_tex` CLEARS anim+0x180..0x18C and then loads into them.
It does not unload what was there.  So calling it a second time on a model
would drop the four pointers the stock model load created and leak them.

The rule here is therefore: the four stock pointers are captured the first
time a model is seen and held in our slot; every texture WE load is ours and
nobody else's; and `CAVE_FREE` -- spliced into the field's own model teardown,
x86 0x63E1D8, which runs at the top of `field_load_models` while the old
field's heap is still live -- writes the four stock pointers back into anim and
unloads ours.  The stock teardown loop then frees exactly what it created.
There is no path where a pointer is freed twice and none where one is dropped.

REGISTER DISCIPLINE
===================
The same rule `ff7nx_audio_cave` records: anything that must survive a call to
the translator or to a guest function lives in x19..x28, and each cave saves
and restores those itself.  Every cave replays its displaced instruction after
`restore_host`, so the stock continuation sees the register state it wrote.

GUEST MEMORY
============
The translator is a PAGE TABLE -- `translate(p) + n` is only `p + n` while n
stays inside the same 4 KB page.  Every guest access here either translates the
exact effective address it is about to touch, or works inside a 0x100-aligned,
0x100-byte block, which can never straddle a page.  The scratch and the guest
call frame are both carved out of the guest stack below ESP and ESP is put back
before the stock body resumes.
"""
import struct
import sys

try:
    import lz4.block                                          # noqa: F401
except ImportError:                                          # pragma: no cover
    sys.exit('need lz4:  pip install lz4 --break-system-packages')

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_cave
import ff7nx_tables
import nxmap

Asm = AC.Asm
GUEST_TRANSLATE = AC.GUEST_TRANSLATE

# Condition codes. a64 carries the ones its own callers needed; LO -- unsigned
# lower, the complement of HS -- is not among them, and guessing 0x3 wrong
# would encode a loop that runs once. tests/test_facial.py decodes every
# branch this file emits through capstone.
EQ, NE, HS, LO, LE, HI = A.EQ, A.NE, A.HS, 0x3, A.LE, A.HI


# ------------------------------------------------------------------ module
# Every address is a module offset into the 1.0.3 `exefs/main` whose build ID
# is 8CAAD5A4E142D2B8EBC1811B5AF05125, resolved through the recompilation map
# from the x86 entry point named beside it, and checked against the word it is
# expected to displace before anything is written.

BLINK_HOOK = 0x991FD0                 # x86 0x649B50 field_blink_3d_model
BLINK_ORIG = 0xA9BD57F6               # stp x22, x21, [sp, #-0x30]!
BLINK_RESUME = BLINK_HOOK + 4

MOUTH_HOOK = 0x9923A4                 # the word after `bl sub_6A2736`
MOUTH_ORIG = 0x29422668               # ldp w8, w9, [x19, #0x10]
MOUTH_RESUME = MOUTH_HOOK + 4

KAWAI_HOOK = 0x991560                 # x86 0x620136 KAWAI script opcode
KAWAI_ORIG = 0xF81B0FF9               # str x25, [sp, #-0x50]!
KAWAI_RESUME = KAWAI_HOOK + 4

FREE_HOOK = 0x9452B0                  # x86 0x63E1D8 field model teardown
FREE_ORIG = 0xA9BB67FA                # stp x26, x25, [sp, #-0x50]!
FREE_RESUME = FREE_HOOK + 4

SPEAK_HOOK = 0x970250                 # x86 0x618DBD MESSAGE opcode handler
SPEAK_ORIG = 0xF81C0FF7               # str x23, [sp, #-0x40]!
SPEAK_RESUME = SPEAK_HOOK + 4

# ff7nx_voice's own BSS block: offset 0 is the live field dialogue player, set
# when one is constructed and zeroed when it is deleted. It is the only thing
# in this build that knows a line is SOUNDING, which is what a mouth should
# move to. Reading it is the whole coupling between the two features; without
# a base the lip flap is not emitted at all.
VOICE_PLAYER_OFF = 0x000
MOUTH_FLAP_INDEX = 2                  # the open mouth the 10 sets ship
# The flap toggles every 1 << shift frames, so 3 at 60 FPS and 2 at 30 give
# the same ~3.7 openings a second -- inside the 3-6 syllables a second of
# ordinary speech, and slow enough not to read as a stutter.
FLAP_SHIFT_60 = 3
FLAP_SHIFT_30 = 2

# Guest functions, called with the translated cdecl frame `_guest_call` builds.
LOAD_EYE_TEX = 0xA01A40               # x86 0x64D2E6  (eye_data*, anim*)
LOAD_MODEL_TEX = 0xAEACC0             # x86 0x670496  (0,0,name,struc_3*,game)
UNLOAD_MODEL_TEX = 0xAEAC00           # x86 0x670483  (tex*)
CREATE_STRUC3 = 0xA9DE80              # x86 0x67455E  (struc_3*)

# The recompiler's guest register file.  ctx+0x10 is ESP, ctx+0x14 is EBP --
# read straight off the prologue every translated function shares.
CTX_PAGE = 0x12CE000
CTX_SLOT = 0x2B0
CTX_ESP = 0x10
CTX_EBP = 0x14


# ------------------------------------------------------------- guest globals
G_ANIM_PTR = 0xCFF738                 # field_animation_data*, stride 0x190
G_ANIM_STRIDE = 0x190
G_MODELS_DATA = 0xCFFC60              # model record base, stride 0x288C
G_MODELS_STRIDE = 0x288C
G_MODEL_NAME_OFF = 0x200              # the path _splitpath'd for the name
G_EYE_BUFFER = 0x9078B8               # ff7_model_eye_texture_data[9]
G_EYE_STRIDE = 20
G_ENTITY_ID = 0xCC0964                # current_entity_id, a byte
G_MODEL_ID_ARRAY = 0xCBFB70           # entity -> model id
G_SCRIPT_CODE = 0xCBF5E8              # field script code base
G_SCRIPT_IP = 0xCC0CF8                # per-entity instruction pointer, u16
G_FIELD_ID = 0xCFF468                 # the field the models belong to, u16
G_STRUC3_DIR = 0x909288               # struc_3.base_directory the game uses
G_LGP_FLAG = 0x905AD8                 # non-zero when assets come from an LGP
G_GAME_OBJECT = 0xDB2BB8              # what get_game_object() returns

# field_animation_data
A_EYE_IDX = 0x20                      # which of the nine eye sets, 0..8, 9=NPC
A_CUSTOM_L = 0x180
A_STATIC_L = 0x184
A_CUSTOM_R = 0x188
A_STATIC_R = 0x18C

# ff7_polygon_set
P_NUMGROUPS = 0x10
P_PER_GROUP = 0x38
P_GROUP_ARRAY = 0x3C
MOUTH_GROUP = 3                       # what FFNx repoints; guarded on numgroups

# GROUP 3 IS NOT ALWAYS A MOUTH -- AND THAT IS THE WHOLE oldm3 BUG.
#
# FFNx's rule is "group 3 is the mouth", copied here. On `ujun_wstd_oldm3`
# and the other hunched old men it is not: group 3 is the ENTIRE textured
# head -- 162 polygons on the 1024x1024 NPC20.TEX -- so pointing it at a
# 32x32 mouth texture replaced the whole face and the head flashed pink
# while he spoke.
#
# The first fix for that banned the generic mouth for EVERY model carrying
# the NPC eye-set marker. It stopped the pink head and it also stopped the
# lip flap on every NPC in the game, because `npc_mouth_<n>` does not exist
# in the shipped art -- MEASURED: npc_mouth_2 ABSENT, c_mouth_2 PRESENT. The
# cast kept flapping, everyone else went silent, which is what was reported.
#
# This is the narrow test the model itself answers. A mouth is a quad; a
# head is not. `polygon_group_array[MOUTH_GROUP].numvert` separates them by
# an order of magnitude, so the flap is refused on geometry rather than on a
# blanket rule about who is an NPC.
P_POLY_GROUPS = 0x5C                  # ff7_polygon_set.polygon_group_array
PG_STRIDE = 0x24                      # sizeof(struct polygon_group)
PG_NUMVERT = 0x04                     # polygon_group.numvert
MOUTH_MAX_VERTS = 32                  # a quad is 4; oldm3's head group is 162
                                      # polygons. Anything in between splits
                                      # them; 32 keeps 8x margin over a mouth
                                      # and stays far under any head.

# struc_3, from the frame field_load_model_eye_tex builds at ebp-0x78
S3_BYTES = 0x80
S3_BASE_DIR = 0x24
S3_USE_LGP = 0x44
S3_LGP_NUM = 0x48
S3_MANGLER = 0x4C


# ---------------------------------------------------------------- our state
# One 64-byte slot per field model entity.  FF7_MAX_NUM_MODEL_ENTITIES is 32
# and `field_n_models` is a u16 that the loader caps at that, so 32 is the
# whole array rather than a guess.
MAX_MODELS = 32
SLOT_BYTES = 64

HDR_FLAP = 0x00                       # u32, voice lip-flap phase counter
HDR_SPEAKER = 0x04                    # u32, model id being voiced, or 0xFF
HDR_GSCRATCH = 0x08                   # u32, guest address of the 0x100 block
HDR_GCALL = 0x0C                      # u32, guest address of the call frame
HDR_ESP = 0x10                        # u32, guest ESP to put back
HDR_WANT = 0x14                       # u32, the index being resolved
HDR_CAND = 0x18                       # u32, which candidate name is in flight
HDR_ENTRY = 0x1C                      # u32, the eye-buffer entry being read
HDR_GENL = 0x20                       # 12 bytes, generic left basename
HDR_GENL_LEN = 0x2C                   # u32
HDR_GENR = 0x30                       # 12 bytes, generic right basename
HDR_GENR_LEN = 0x3C                   # u32
HDR_LR = 0x40                         # u64, X30 across the shared subroutine
SLOTS_OFF = 0x50
GEN_MAX = 11                          # what fits before the NUL in 12 bytes

SL_STOCK_CL = 0x00
SL_STOCK_L = 0x04
SL_STOCK_CR = 0x08
SL_STOCK_R = 0x0C
SL_ART_L = 0x10
SL_ART_R = 0x14
SL_MOUTH_TEX = 0x18
SL_ART_IDX = 0x1C                     # u8, index loaded in art_l/art_r
SL_MOUTH_IDX = 0x1D                   # u8, index loaded in mouth_tex
SL_FLAGS = 0x1E                       # u8, FLAG_* below
SL_MISS_EYE = 0x1F                    # u8, bit n = index n probed and absent
SL_EXPR = 0x20                        # u8, latched expression, 0 = none
SL_MOUTH_REQ = 0x21                   # u8, latched mouth index, 0 = none
SL_MISS_MOUTH = 0x22                  # u8
SL_APPLIED = 0x23                     # u8, index currently parked in anim
SL_NAME = 0x24                        # 12 bytes, lowercased HRC id, NUL ended
SL_NAMELEN = 0x30                     # u32
SL_MOUTH_SCRIPT = 0x34                # u8, what KAWAI EYETX asked for
SL_NAME_MAX = 8

FLAG_STOCK = 1
FLAG_NPC = 2                          # eye-set 9; use npc_* fallbacks

# The highest index a name is built for. The shipped set tops out at
# Y_EYE2_6, so 7 is one past everything that exists and 8 bits of
# `SL_MISS_EYE` cover every index the runtime will ever probe.
MAX_INDEX = 7

BSS_BYTES = SLOTS_OFF + MAX_MODELS * SLOT_BYTES

# The guest scratch block, carved below ESP.  0x100-aligned and 0x100 long, so
# one translation covers all of it.
GSCRATCH_BYTES = 0x100
GS_EYEDATA = 0x00                     # ff7_model_eye_texture_data, 20 bytes
GS_NAME_L = 0x20                      # "eye_<model>_<n>.tim"
GS_NAME_R = 0x40                      # "eye_<model>r_<n>.tim"
GS_NAME_M = 0x60                      # "mouth_<model>_<n>.tim"
GS_STRUC3 = 0x80                      # 0x80 bytes, to 0xFF

# How far below ESP the whole working set sits.  The call frame is the LOWER
# block so a callee's own pushes grow away from our data.
GUEST_WINDOW = 0x300


# ------------------------------------------------------------ extra encoders
# Forms a64.py does not carry.  tests/test_facial.py round-trips every one of
# them through capstone, for the reason a64.py's own forms are: an encoding
# that looks plausible in hex and decodes to the wrong register produces a
# structurally valid module that misbehaves.
def orr_imm_bit(rd, rn, bit):
    """ORR Wd, Wn, #(1 << bit) -- the one-bit logical immediate."""
    if not 0 <= bit < 32:
        raise ValueError('bit %d out of range' % bit)
    return 0x32000000 | (((32 - bit) & 31) << 16) | (rn << 5) | rd


def tbz(rt, bit, frm, to):
    """TBZ Wt, #bit, target."""
    off = (to - frm) >> 2
    if not -0x2000 <= off < 0x2000:
        raise ValueError('tbz out of range')
    return (0x36000000 | ((bit & 31) << 19) | ((off & 0x3FFF) << 5) | rt)


def lsrv(rd, rn, rm):
    """LSRV Wd, Wn, Wm -- a shift by a register, which a64 has no form for."""
    return 0x1AC02400 | (rm << 16) | (rn << 5) | rd


def lslv(rd, rn, rm):
    """LSLV Wd, Wn, Wm."""
    return 0x1AC02000 | (rm << 16) | (rn << 5) | rd


def orr_reg(rd, rn, rm):
    """ORR Wd, Wn, Wm."""
    return A.orr_lsl(rd, rn, rm, 0)


# ----------------------------------------------------------------- emitters
def _ctx(a, reg):
    """Materialise the guest register-file pointer into Xreg."""
    a.emit(A.adrp(reg, a.pc(), CTX_PAGE))
    a.emit(A.ldr64(reg, reg, CTX_SLOT))


def _translate(a, guest_reg):
    """Host pointer for the guest address in Wguest_reg, into X0."""
    if guest_reg != 0:
        a.emit(A.mov_reg(0, guest_reg))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))


def _translate_imm(a, guest):
    AC.mov32(a, 0, guest)
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))


def _gld32_imm(a, guest, dst):
    """Wdst = *(u32 *)guest, for a constant guest address."""
    _translate_imm(a, guest)
    a.emit(A.ldr(dst, 0, 0))


def _gld32(a, base, off, dst):
    """Wdst = *(u32 *)(Wbase + off). Translates the exact address."""
    a.emit(A.add_imm(0, base, off) if off else A.mov_reg(0, base))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldr(dst, 0, 0))


def _gst32(a, base, off, src):
    """*(u32 *)(Wbase + off) = Wsrc. `src` must be callee-saved."""
    a.emit(A.add_imm(0, base, off) if off else A.mov_reg(0, base))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.str_(src, 0, 0))


def _gld8(a, base, off, dst):
    a.emit(A.add_imm(0, base, off) if off else A.mov_reg(0, base))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldrb(dst, 0, 0))


def _gst8(a, base, off, src):
    a.emit(A.add_imm(0, base, off) if off else A.mov_reg(0, base))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.strb(src, 0, 0))


def _guest_call(a, ctx, gcall, bss, target, args):
    """
    Call a translated cdecl guest function.

    `ctx` holds the guest register file, `gcall` the guest address of the call
    frame -- [gcall] is the virtual return word every translated callee
    consumes in its epilogue, and the cdecl arguments follow it in order.
    `ctx`, `gcall`, `bss` and every ('reg', n) argument must be callee-saved,
    because building the frame calls the translator.

    An argument is one of:
        ('reg', n)              Wn
        ('zero',)              a literal 0
        ('bss', off)           the u32 at bss+off
        ('bssadd', off, d)     that u32 plus d -- a guest address inside the
                               0x100 scratch block, which is how the two
                               pointer arguments reach the texture loader
                               without spending a callee-saved register each.

    ESP is left where the callee's epilogue put it; the caller's epilogue puts
    the original back from HDR_ESP, which is the only copy this file trusts.
    EAX comes back at ctx+0, the x86 register file's first slot -- verified
    against `ldr w20, [x19]` at main+0xA01CDC, which is how the recompiled
    `field_load_model_eye_tex` reads this very function's result.
    """
    _translate(a, gcall)
    a.emit(A.str_(A.WZR, 0, 0))
    for i, arg in enumerate(args):
        off = 4 * (i + 1)
        if arg[0] == 'reg':
            a.emit(A.str_(arg[1], 0, off))
        elif arg[0] == 'zero':
            a.emit(A.str_(A.WZR, 0, off))
        elif arg[0] == 'bss':
            a.emit(A.ldr(9, bss, arg[1]))
            a.emit(A.str_(9, 0, off))
        elif arg[0] == 'bssadd':
            a.emit(A.ldr(9, bss, arg[1]))
            a.emit(A.add_imm(9, 9, arg[2]))
            a.emit(A.str_(9, 0, off))
        else:
            raise ValueError('unknown guest argument %r' % (arg,))
    a.emit(A.str_(gcall, ctx, CTX_ESP))
    a.emit(A.bl(a.pc(), target))


def _emit_lower(a, tag, reg):
    """
    Wreg to lower case, for A-Z only.

    A bare `orr #0x20` maps A-Z correctly and leaves 0-9 alone, which covers
    every HRC id in the shipped flevel -- and turns '_' into 0x7F, which is
    what it did to `b_eye2` the first time this ran. The range test is three
    words and cannot be wrong on a name this file has not seen.
    """
    a.emit(A.sub_imm(2, reg, 0x41))
    a.emit(A.cmp_imm(2, 26))
    a.bcond('low_%s_done' % tag, HS)
    a.emit(orr_imm_bit(reg, reg, 5))
    a.label('low_%s_done' % tag)


def _copy(a, tag, dst, src, count, stop=None, lower=False):
    """
    Host-memory byte copy: Xdst <- Xsrc for Wcount bytes, Xdst left past the
    end.  `stop` ends it early on that byte value; `lower` ORs in 0x20, which
    maps A-Z to a-z and leaves 0-9 alone, so an HRC id comes out in the case
    `lgp.Archive.add` stores.

    Scratch: w12 only, so this composes anywhere between translator calls.
    """
    a.label('cp_%s_top' % tag)
    a.cbz(count, 'cp_%s_end' % tag)
    a.emit(A.ldrb(12, src, 0))
    if stop is not None:
        a.emit(A.cmp_imm(12, stop))
        a.bcond('cp_%s_end' % tag, A.EQ)
    a.cbz(12, 'cp_%s_end' % tag)
    if lower:
        a.emit(orr_imm_bit(12, 12, 5))
    a.emit(A.strb(12, dst, 0))
    a.emit(A.add_imm64(dst, dst, 1))
    a.emit(A.add_imm64(src, src, 1))
    a.emit(A.sub_imm(count, count, 1))
    a.b('cp_%s_top' % tag)
    a.label('cp_%s_end' % tag)


def _lit(a, dst, text):
    """
    Append a byte string to the host pointer Xdst, advancing it.

    Written as 4- and 2-byte stores rather than a byte at a time.  The
    destination sits in the middle of a name being assembled so its alignment
    is not known statically; AArch64 permits unaligned access to normal
    memory, which the guest stack is.  The obvious byte-at-a-time version cost
    three words per character, and this feature is spending from 1,763 usable
    padding words with every other pass already installed -- so the budget is
    the reason, and it is worth writing down.
    """
    i = 0
    while i < len(text):
        n = 4 if len(text) - i >= 4 else (2 if len(text) - i >= 2 else 1)
        val = int.from_bytes(text[i:i + n], 'little')
        if n == 4:
            a.emit(A.movz(12, val & 0xFFFF))
            a.emit(A.movk_hi(12, val >> 16))
            a.emit(A.str_(12, dst, 0))
        elif n == 2:
            a.emit(A.movz(12, val))
            a.emit(A.strh(12, dst, 0))
        else:
            a.emit(A.movz(12, val))
            a.emit(A.strb(12, dst, 0))
        a.emit(A.add_imm64(dst, dst, n))
        i += n


def _emit_name(a, tag, host_scratch, off, bss, slot, parts):
    """
    Assemble one NUL-terminated guest filename at host_scratch+off.

    `parts` is read left to right:
        ('lit', b'eye_')                a literal
        ('model',)                      the slot's lowercased HRC id
        ('gen', HDR_GENL)               a generic basename from the header
        ('gentok', HDR_GENR)            that basename up to its first '_'
        ('digit',)                      HDR_WANT as one ASCII digit

    Everything here is host memory -- no translator call -- so w9..w12 stay
    live across the whole sequence.
    """
    a.emit(A.add_imm64(9, host_scratch, off))
    for n, part in enumerate(parts):
        if part[0] == 'lit':
            _lit(a, 9, part[1])
        elif part[0] == 'model':
            a.emit(A.add_imm64(10, slot, SL_NAME))
            a.emit(A.ldr(11, slot, SL_NAMELEN))
            _copy(a, '%s_%d' % (tag, n), 9, 10, 11, lower=False)
        elif part[0] == 'gen':
            a.emit(A.add_imm64(10, bss, part[1]))
            a.emit(A.ldr(11, bss, part[1] + 0xC))
            _copy(a, '%s_%d' % (tag, n), 9, 10, 11)
        elif part[0] == 'gentok':
            a.emit(A.add_imm64(10, bss, part[1]))
            a.emit(A.ldr(11, bss, part[1] + 0xC))
            _copy(a, '%s_%d' % (tag, n), 9, 10, 11, stop=0x5F)
        elif part[0] == 'digit':
            a.emit(A.ldr(12, bss, HDR_WANT))
            a.emit(A.add_imm(12, 12, 0x30))
            a.emit(A.strb(12, 9, 0))
            a.emit(A.add_imm64(9, 9, 1))
        else:
            raise ValueError('unknown name part %r' % (part,))
    a.emit(A.strb(A.WZR, 9, 0))


def _epilogue(a, displaced, resume, restore_esp=True):
    """Put ESP back, restore the host frame, replay the site, resume."""
    if restore_esp:
        a.emit(A.ldr(9, 24, HDR_ESP))
        a.emit(A.str_(9, 19, CTX_ESP))
    AC.restore_host(a, 0x80)
    a.emit(displaced)
    a.emit(A.b(a.pc(), resume))


def _gld8_imm(a, guest, dst):
    _translate_imm(a, guest)
    a.emit(A.ldrb(dst, 0, 0))


def _carve(a):
    """
    Save guest ESP and carve the working window out of the stack below it.

    The call frame is the LOWER of the two 0x100 blocks so a callee's own
    pushes grow away from the scratch, and both are 0x100-aligned, which is
    what makes a single translation valid for the whole of either: the
    translator is a page table, and a 0x100-aligned 0x100-byte block cannot
    straddle a 4 KB page.
    """
    a.emit(A.ldr(9, 19, CTX_ESP))
    a.emit(A.str_(9, 24, HDR_ESP))
    a.emit(A.sub_imm(9, 9, GUEST_WINDOW))
    a.emit(A.lsr(9, 9, 8))
    a.emit(A.lsl(9, 9, 8))
    a.emit(A.str_(9, 24, HDR_GCALL))
    a.emit(A.add_imm(9, 9, GSCRATCH_BYTES))
    a.emit(A.str_(9, 24, HDR_GSCRATCH))


def _slot(a, model_reg):
    """X20 = the 64-byte slot for the model id in Wmodel_reg."""
    a.emit(A.lsl(9, model_reg, 6))
    a.emit(A.add_imm64(20, 24, SLOTS_OFF))
    a.emit(AC.add_x_uxtw(20, 20, 9))


def build_generics_cave(cave, addr, bss):
    """
    The one shared subroutine: fill HDR_GENL / HDR_GENR from the eye set.

    Both slow paths need it and it is 63 words, which at the ~50% a chained
    cave pays for its branches is 190 of a 1,763-word padding budget to
    duplicate. X30 does not survive the translator calls inside, so it goes to
    BSS on the way in -- safe because the field script VM is single threaded
    and this is never re-entered.
    """
    a = Asm(cave, addr)
    a.emit(A.str64(30, 24, HDR_LR))
    _emit_generics(a, 'sub')
    a.emit(A.ldr64(30, 24, HDR_LR))
    a.emit(A.ret())
    return a.resolve()


def _emit_generics(a, tag):
    """
    Fill HDR_GENL / HDR_GENR with the basenames of the eye set in W25.

    These are the names FFNx falls back to when a model has no `eye_<model>_n`
    of its own -- `b_eye2` / `b_eye2r` and the rest -- and they are the only
    route to the cast's own indexed art (C_EYE2_3, T_EYE2_2, CHI_RED5_2 ...),
    which is not named after an HRC id.  They are read from the game's own
    `field_models_eye_blink_buffer` rather than hard-coded, so an eye set the
    module lays out differently cannot be silently mis-resolved.

    Uses W27/W28 and the caller's X24; W25 and W26 are left alone.
    """
    AC.mov32(a, 9, G_EYE_BUFFER)
    AC.mov32(a, 10, G_EYE_STRIDE)
    a.emit(A.mul(10, 25, 10))
    a.emit(A.add_reg(9, 9, 10))
    a.emit(A.str_(9, 24, HDR_ENTRY))
    for hdr, off in ((HDR_GENL, 8), (HDR_GENR, 0x10)):
        done = 'gen_%s_%x_done' % (tag, hdr)
        stop = 'gen_%s_%x_stop' % (tag, hdr)
        top = 'gen_%s_%x_top' % (tag, hdr)
        a.emit(A.str_(A.WZR, 24, hdr + 0xC))
        a.emit(A.ldr(9, 24, HDR_ENTRY))
        a.emit(A.add_imm(0, 9, off))
        a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
        a.emit(A.ldr(27, 0, 0))
        a.cbz(27, done)
        a.emit(A.add_imm64(28, 24, hdr))
        a.label(top)
        a.emit(A.mov_reg(0, 27))
        a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
        a.emit(A.ldrb(1, 0, 0))
        a.emit(A.cmp_imm(1, 0x2E))
        a.bcond(stop, A.EQ)
        a.cbz(1, stop)
        _emit_lower(a, 'gen_%s_%x' % (tag, hdr), 1)
        a.emit(A.strb(1, 28, 0))
        a.emit(A.add_imm64(28, 28, 1))
        a.emit(A.add_imm(27, 27, 1))
        a.emit(A.sub_reg64(9, 28, 24))
        a.emit(A.sub_imm(9, 9, hdr))
        a.emit(A.cmp_imm(9, GEN_MAX))
        a.bcond(top, LO)
        a.label(stop)
        a.emit(A.strb(A.WZR, 28, 0))
        a.emit(A.sub_reg64(9, 28, 24))
        a.emit(A.sub_imm(9, 9, hdr))
        a.emit(A.str_(9, 24, hdr + 0xC))
        a.label(done)


# --------------------------------------------------------------- the caves
def build_blink_cave(cave, addr, bss, load_entry, mload_entry,
                     voice_bss=None, flap_shift=FLAP_SHIFT_60):
    """
    The hot half.  Runs once per model per frame, from the entry of
    `field_blink_3d_model`, and in the steady state does nothing but compare
    two bytes: the index the engine wants is the one already parked in
    `anim->static_*_eye_tex`, so there is no file I/O, no allocation and no
    guest call on the path that runs 1,920 times a second.
    """
    a = Asm(cave, addr)
    AC.save_host(a, 0x80)
    _ctx(a, 19)
    AC.bss_ptr(a, 24, bss)
    _carve(a)

    # cdecl arguments: [esp+4] field_animation_data, [esp+8] blink data
    a.emit(A.ldr(9, 24, HDR_ESP))
    a.emit(A.add_imm(0, 9, 4))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldr(21, 0, 0))
    a.emit(A.ldr(9, 24, HDR_ESP))
    a.emit(A.add_imm(0, 9, 8))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldr(22, 0, 0))
    a.cbz(21, 'out')
    a.cbz(22, 'out')

    _gld8(a, 22, 3, 23)
    a.emit(A.cmp_imm(23, MAX_MODELS))
    a.bcond('out', HS)
    _slot(a, 23)

    # Which of the nine eye sets this model uses.  9 is the NPC marker. FFNx
    # borrows Cloud's generic EYES for it, but deliberately uses npc_mouth_N
    # rather than Cloud's c_mouth_N.  Keep that distinction in the slot before
    # mapping the eye set to zero.  Missing it made every voiced NPC eligible
    # for Cloud's mouth texture; on oldm3, group 3 is the entire NPC20-textured
    # head, so the lip flap replaced the whole face and made it flash pink.
    # Entry 8 has has_eyes == 0 in the module and has no fallback.
    _gld8(a, 21, A_EYE_IDX, 25)
    a.emit(A.cmp_imm(25, 9))
    a.bcond('eye_set', NE)
    a.emit(A.ldrb(9, 20, SL_FLAGS))
    a.emit(A.movz(10, FLAG_NPC))
    a.emit(orr_reg(9, 9, 10))
    a.emit(A.strb(9, 20, SL_FLAGS))
    a.emit(A.movz(25, 0))
    a.label('eye_set')
    a.emit(A.cmp_imm(25, 8))
    a.bcond('out', HS)

    # once per model per field: take the four pointers the stock model load
    # created, and the lowercased HRC id every name is built from.
    a.emit(A.ldrb(9, 20, SL_FLAGS))
    a.emit(A.and_mask(9, 9, 1))
    a.cbnz(9, 'have_stock')
    for off, sl in ((A_CUSTOM_L, SL_STOCK_CL), (A_STATIC_L, SL_STOCK_L),
                    (A_CUSTOM_R, SL_STOCK_CR), (A_STATIC_R, SL_STOCK_R)):
        _gld32(a, 21, off, 26)
        a.emit(A.str_(26, 20, sl))
    _gld32_imm(a, G_MODELS_DATA, 27)
    a.cbz(27, 'out')
    AC.mov32(a, 9, G_MODELS_STRIDE)
    a.emit(A.mul(9, 23, 9))
    a.emit(A.add_reg(27, 27, 9))
    a.emit(A.add_imm(27, 27, G_MODEL_NAME_OFF))
    a.emit(A.add_imm64(28, 20, SL_NAME))
    a.label('nm')
    a.emit(A.mov_reg(0, 27))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldrb(1, 0, 0))
    a.emit(A.cmp_imm(1, 0x2E))
    a.bcond('nm_end', A.EQ)
    a.cbz(1, 'nm_end')
    _emit_lower(a, 'nm', 1)
    a.emit(A.strb(1, 28, 0))
    a.emit(A.add_imm64(28, 28, 1))
    a.emit(A.add_imm(27, 27, 1))
    a.emit(A.sub_reg64(9, 28, 20))
    a.emit(A.sub_imm(9, 9, SL_NAME))
    a.emit(A.cmp_imm(9, SL_NAME_MAX))
    a.bcond('nm', LO)
    a.label('nm_end')
    a.emit(A.strb(A.WZR, 28, 0))
    a.emit(A.sub_reg64(9, 28, 20))
    a.emit(A.sub_imm(9, 9, SL_NAME))
    a.emit(A.str_(9, 20, SL_NAMELEN))
    a.cbz(9, 'out')
    a.emit(A.ldrb(9, 20, SL_FLAGS))
    a.emit(A.movz(10, FLAG_STOCK))
    a.emit(orr_reg(9, 9, 10))
    a.emit(A.strb(9, 20, SL_FLAGS))
    a.label('have_stock')

    # What the mouth should be this frame. A KAWAI EYETX index wins outright:
    # it is the author saying so. Otherwise, if this model is the one that
    # opened the dialogue window and ff7nx_voice has a player sounding for it,
    # flap. That second clause is not FFNx behaviour and could not be -- FFNx
    # has no voice runtime. It is here because the measured alternative is a
    # mouth that moves six times in the whole game.
    #
    # It is settled HERE, before the eye decision, and not next to the mouth
    # comparison at the bottom: the slow eye path leaves through the mouth
    # loader without coming back, so a request written after the branch would
    # be a frame stale every time an eye texture changed.
    a.emit(A.ldrb(26, 20, SL_MOUTH_SCRIPT))
    a.cbnz(26, 'have_mouth')
    if voice_bss is not None:
        a.emit(A.ldr(9, 24, HDR_SPEAKER))
        a.emit(A.add_imm(10, 23, 1))
        a.emit(A.cmp_reg(9, 10))
        a.bcond('have_mouth', NE)
        AC.bss_ptr(a, 27, voice_bss + VOICE_PLAYER_OFF)
        a.emit(A.ldr64(9, 27, 0))
        a.cbz64(9, 'have_mouth')
        a.emit(A.ldr(9, 24, HDR_FLAP))
        a.emit(A.add_imm(9, 9, 1))
        a.emit(A.str_(9, 24, HDR_FLAP))
        a.emit(A.lsr(9, 9, flap_shift))
        a.emit(A.and_mask(9, 9, 1))
        a.cbz(9, 'have_mouth')
        a.emit(A.movz(26, MOUTH_FLAP_INDEX))
    a.label('have_mouth')
    a.emit(A.strb(26, 20, SL_MOUTH_REQ))

    # the blink data is four bytes at a four-aligned address, so one
    # translation reaches all of it
    a.emit(A.mov_reg(0, 22))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(AC.mov64(28, 0))
    a.emit(A.ldrb(26, 28, 0))
    a.emit(A.ldrb(27, 28, 1))

    a.emit(A.ldrb(9, 20, SL_EXPR))
    a.cbz(9, 'no_expr')
    a.emit(A.mov_reg(26, 9))
    a.b('have_want')
    a.label('no_expr')
    a.emit(A.cmp_reg(26, 27))
    a.bcond('stock', NE)
    a.label('have_want')
    # Index 0 and 1 are the OPEN-eye states, and the stock body's mode-1
    # branch never reads static_*_eye_tex -- it draws the model's own group.
    # So there is nothing to choose, nothing to load, and nothing to put
    # back: leaving whatever is parked alone is both correct and the reason
    # the open frames -- most of them -- cost nothing. Swapping the pointer
    # back here instead made the cache thrash between index 1 and index 2,
    # which is a texture load on every blink of every model.
    a.emit(A.cmp_imm(26, 2))
    a.bcond('mouth', LO)
    a.emit(A.cmp_imm(26, MAX_INDEX + 1))
    a.bcond('stock', HS)
    a.emit(A.ldrb(9, 20, SL_MISS_EYE))
    a.emit(lsrv(9, 9, 26))
    a.emit(A.and_mask(9, 9, 1))
    a.cbnz(9, 'stock')
    a.emit(A.ldrb(9, 20, SL_APPLIED))
    a.emit(A.cmp_reg(9, 26))
    a.bcond('applied', A.EQ)
    a.emit(A.ldrb(9, 20, SL_ART_IDX))
    a.emit(A.cmp_reg(9, 26))
    a.bcond('park', A.EQ)
    a.emit(A.str_(26, 24, HDR_WANT))
    a.emit(A.str_(A.WZR, 24, HDR_CAND))
    a.emit(A.b(a.pc(), load_entry))

    a.label('park')
    a.emit(A.ldr(27, 20, SL_ART_L))
    _gst32(a, 21, A_STATIC_L, 27)
    a.emit(A.ldr(27, 20, SL_ART_R))
    _gst32(a, 21, A_STATIC_R, 27)
    a.emit(A.strb(26, 20, SL_APPLIED))
    a.b('applied')

    a.label('stock')
    a.emit(A.ldrb(9, 20, SL_APPLIED))
    a.cbz(9, 'mouth')
    a.emit(A.ldr(27, 20, SL_STOCK_L))
    _gst32(a, 21, A_STATIC_L, 27)
    a.emit(A.ldr(27, 20, SL_STOCK_R))
    _gst32(a, 21, A_STATIC_R, 27)
    a.emit(A.strb(A.WZR, 20, SL_APPLIED))
    a.b('mouth')

    a.label('applied')
    # An expression has to be re-asserted every frame: the per-frame blink
    # loop rewrote both modes to 1/1 or 2/2 immediately before this call, and
    # mode 2 is the one that displays static_*_eye_tex, which is where the
    # expression is parked.
    a.emit(A.ldrb(9, 20, SL_EXPR))
    a.cbz(9, 'mouth')
    a.emit(A.mov_reg(0, 22))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.movz(9, 2))
    a.emit(A.strb(9, 0, 0))
    a.emit(A.strb(9, 0, 1))

    a.label('mouth')
    a.emit(A.ldrb(9, 20, SL_MOUTH_REQ))
    a.emit(A.ldrb(10, 20, SL_MOUTH_IDX))
    a.emit(A.cmp_reg(9, 10))
    a.bcond('out', A.EQ)
    a.emit(A.b(a.pc(), mload_entry))

    a.label('out')
    _epilogue(a, BLINK_ORIG, BLINK_RESUME)
    return a.resolve()


def build_speak_cave(cave, addr, bss):
    """
    Remember which model opened a dialogue window.

    The voice runtime knows a line is playing but not who is saying it; the
    MESSAGE opcode handler knows who but not whether anything sounds. This is
    the one byte that joins them, taken at the handler's entry where
    `current_entity_id` is still the entity running the script. Stored as
    model+1 so a zeroed BSS cannot read as "Cloud is speaking".
    """
    a = Asm(cave, addr)
    AC.save_host(a, 0x80)
    _ctx(a, 19)
    AC.bss_ptr(a, 24, bss)
    _gld8_imm(a, G_ENTITY_ID, 23)
    AC.mov32(a, 9, G_MODEL_ID_ARRAY)
    a.emit(A.add_reg(9, 9, 23))
    a.emit(A.mov_reg(0, 9))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldrb(23, 0, 0))
    a.emit(A.cmp_imm(23, MAX_MODELS))
    a.bcond('out', HS)
    a.emit(A.add_imm(23, 23, 1))
    a.emit(A.str_(23, 24, HDR_SPEAKER))
    a.label('out')
    _epilogue(a, SPEAK_ORIG, SPEAK_RESUME, restore_esp=False)
    return a.resolve()


def build_load_cave(cave, addr, bss, mload_entry, gen_entry):
    """
    The eye loader.  Entered by a branch from the blink cave with x19..x28
    already live, and it exits through the mouth loader's entry, which falls
    straight through to the shared epilogue when there is no mouth to change.
    """
    a = Asm(cave, addr)
    a.emit(A.ldr(27, 20, SL_ART_L))
    a.cbz(27, 'f1')
    a.emit(A.ldr(28, 24, HDR_GCALL))
    _guest_call(a, 19, 28, 24, UNLOAD_MODEL_TEX, [('reg', 27)])
    a.emit(A.str_(A.WZR, 20, SL_ART_L))
    a.label('f1')
    a.emit(A.ldr(27, 20, SL_ART_R))
    a.cbz(27, 'f2')
    a.emit(A.ldr(28, 24, HDR_GCALL))
    _guest_call(a, 19, 28, 24, UNLOAD_MODEL_TEX, [('reg', 27)])
    a.emit(A.str_(A.WZR, 20, SL_ART_R))
    a.label('f2')
    a.emit(A.strb(A.WZR, 20, SL_ART_IDX))

    a.emit(A.bl(a.pc(), gen_entry))

    a.label('cand_top')
    a.emit(A.ldr(27, 24, HDR_GSCRATCH))
    a.emit(A.mov_reg(0, 27))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(AC.mov64(28, 0))
    a.emit(A.ldr(9, 24, HDR_CAND))
    a.cbnz(9, 'cand1')
    _emit_name(a, 'l0', 28, GS_NAME_L, 24, 20,
               [('lit', b'eye_'), ('model',), ('lit', b'_'), ('digit',),
                ('lit', b'.tim')])
    _emit_name(a, 'r0', 28, GS_NAME_R, 24, 20,
               [('lit', b'eye_'), ('model',), ('lit', b'r_'), ('digit',),
                ('lit', b'.tim')])
    a.b('names_done')
    a.label('cand1')
    a.emit(A.ldr(9, 24, HDR_GENL_LEN))
    a.cbz(9, 'give_up')
    a.emit(A.ldr(9, 24, HDR_GENR_LEN))
    a.cbz(9, 'give_up')
    _emit_name(a, 'l1', 28, GS_NAME_L, 24, 20,
               [('gen', HDR_GENL), ('lit', b'_'), ('digit',), ('lit', b'.tim')])
    _emit_name(a, 'r1', 28, GS_NAME_R, 24, 20,
               [('gen', HDR_GENR), ('lit', b'_'), ('digit',), ('lit', b'.tim')])
    a.label('names_done')

    a.emit(A.movz(9, 1))
    a.emit(A.str_(9, 28, GS_EYEDATA + 0))
    a.emit(A.str_(A.WZR, 28, GS_EYEDATA + 4))
    a.emit(A.ldr(9, 24, HDR_GSCRATCH))
    a.emit(A.add_imm(9, 9, GS_NAME_L))
    a.emit(A.str_(9, 28, GS_EYEDATA + 8))
    a.emit(A.str_(A.WZR, 28, GS_EYEDATA + 12))
    a.emit(A.ldr(9, 24, HDR_GSCRATCH))
    a.emit(A.add_imm(9, 9, GS_NAME_R))
    a.emit(A.str_(9, 28, GS_EYEDATA + 16))

    a.emit(A.ldr(28, 24, HDR_GCALL))
    _guest_call(a, 19, 28, 24, LOAD_EYE_TEX,
                [('bss', HDR_GSCRATCH), ('reg', 21)])

    _gld32(a, 21, A_STATIC_L, 27)
    a.emit(A.str_(27, 20, SL_ART_L))
    _gld32(a, 21, A_STATIC_R, 27)
    a.emit(A.str_(27, 20, SL_ART_R))
    # the loader clears all four slots before it loads; the two custom
    # pointers are not ours to lose (eye set 4 has a real one), so put them
    # back from the capture.
    a.emit(A.ldr(27, 20, SL_STOCK_CL))
    _gst32(a, 21, A_CUSTOM_L, 27)
    a.emit(A.ldr(27, 20, SL_STOCK_CR))
    _gst32(a, 21, A_CUSTOM_R, 27)

    a.emit(A.ldr(27, 20, SL_ART_L))
    a.cbz(27, 'partial')
    a.emit(A.ldr(27, 20, SL_ART_R))
    a.cbz(27, 'partial')
    a.emit(A.strb(26, 20, SL_ART_IDX))
    a.emit(A.strb(26, 20, SL_APPLIED))
    a.emit(A.b(a.pc(), mload_entry))

    a.label('partial')
    a.emit(A.ldr(27, 20, SL_ART_L))
    a.cbz(27, 'p1')
    a.emit(A.ldr(28, 24, HDR_GCALL))
    _guest_call(a, 19, 28, 24, UNLOAD_MODEL_TEX, [('reg', 27)])
    a.label('p1')
    a.emit(A.ldr(27, 20, SL_ART_R))
    a.cbz(27, 'p2')
    a.emit(A.ldr(28, 24, HDR_GCALL))
    _guest_call(a, 19, 28, 24, UNLOAD_MODEL_TEX, [('reg', 27)])
    a.label('p2')
    a.emit(A.str_(A.WZR, 20, SL_ART_L))
    a.emit(A.str_(A.WZR, 20, SL_ART_R))
    a.emit(A.ldr(27, 20, SL_STOCK_L))
    _gst32(a, 21, A_STATIC_L, 27)
    a.emit(A.ldr(27, 20, SL_STOCK_R))
    _gst32(a, 21, A_STATIC_R, 27)
    a.emit(A.strb(A.WZR, 20, SL_APPLIED))
    a.emit(A.ldr(9, 24, HDR_CAND))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.str_(9, 24, HDR_CAND))
    a.emit(A.cmp_imm(9, 2))
    a.bcond('cand_top', LO)

    a.label('give_up')
    a.emit(A.ldrb(9, 20, SL_MISS_EYE))
    a.emit(A.movz(10, 1))
    a.emit(lslv(10, 10, 26))
    a.emit(orr_reg(9, 9, 10))
    a.emit(A.strb(9, 20, SL_MISS_EYE))
    a.emit(A.b(a.pc(), mload_entry))
    return a.resolve()


def build_mload_cave(cave, addr, bss, gen_entry):
    """
    The mouth loader, and the shared exit for every slow path.

    `field_load_model_eye_tex` cannot be reused here: it loads a PAIR into
    anim's own slots.  A mouth is a single texture that has to stay ours and
    be handed to the renderer through `hundred_data_group_array[3]`, so this
    builds the file context the way the stock eye loader does -- the same
    `create_struc_3_info`, the same base directory, the same use_lgp/lgp_num
    pair -- and calls `field_load_model_tex` directly.
    """
    a = Asm(cave, addr)
    a.emit(A.ldrb(26, 20, SL_MOUTH_REQ))
    a.emit(A.ldrb(9, 20, SL_MOUTH_IDX))
    a.emit(A.cmp_reg(26, 9))
    a.bcond('out', A.EQ)

    a.emit(A.ldr(27, 20, SL_MOUTH_TEX))
    a.cbz(27, 'freed')
    a.emit(A.ldr(28, 24, HDR_GCALL))
    _guest_call(a, 19, 28, 24, UNLOAD_MODEL_TEX, [('reg', 27)])
    a.emit(A.str_(A.WZR, 20, SL_MOUTH_TEX))
    a.label('freed')
    a.emit(A.strb(A.WZR, 20, SL_MOUTH_IDX))
    a.cbz(26, 'out')
    a.emit(A.cmp_imm(26, MAX_INDEX + 1))
    a.bcond('settle', HS)
    a.emit(A.ldrb(9, 20, SL_MISS_MOUTH))
    a.emit(lsrv(9, 9, 26))
    a.emit(A.and_mask(9, 9, 1))
    a.cbnz(9, 'settle')

    a.emit(A.str_(26, 24, HDR_WANT))
    a.emit(A.str_(A.WZR, 24, HDR_CAND))
    a.emit(A.bl(a.pc(), gen_entry))

    a.label('cand_top')
    a.emit(A.ldr(27, 24, HDR_GSCRATCH))
    a.emit(A.mov_reg(0, 27))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(AC.mov64(28, 0))
    a.emit(A.ldr(9, 24, HDR_CAND))
    a.cbnz(9, 'cand1')
    _emit_name(a, 'm0', 28, GS_NAME_M, 24, 20,
               [('lit', b'mouth_'), ('model',), ('lit', b'_'), ('digit',),
                ('lit', b'.tim')])
    a.b('named')
    a.label('cand1')
    # Eye-set 9 is an NPC marker, not another name for Cloud.  FFNx first
    # probes mouth_<model>_N and then npc_mouth_N for this case.  In
    # particular it must never fall through to c_mouth_N: many NPC heads have
    # a fourth material group that covers the whole face rather than a mouth
    # quad, and feeding Cloud's tiny mouth texture to it recolours the head.
    # The NPC-only `npc_mouth_<n>` probe is GONE. It never resolved -- that
    # name is not in the shipped art -- so it cost every NPC its lip flap to
    # protect the handful of models whose group 3 is a whole head. Those are
    # now refused by MOUTH_MAX_VERTS in the apply cave, on the geometry, so
    # an NPC takes the generic mouth exactly as it did before that fix.
    a.emit(A.ldr(9, 24, HDR_GENR_LEN))
    a.cbz(9, 'settle')
    _emit_name(a, 'm1', 28, GS_NAME_M, 24, 20,
               [('gentok', HDR_GENR), ('lit', b'_mouth_'), ('digit',),
                ('lit', b'.tim')])
    a.label('named')

    a.emit(A.ldr(28, 24, HDR_GCALL))
    _guest_call(a, 19, 28, 24, CREATE_STRUC3,
                [('bssadd', HDR_GSCRATCH, GS_STRUC3)])
    a.emit(A.ldr(27, 24, HDR_GSCRATCH))
    a.emit(A.mov_reg(0, 27))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(AC.mov64(28, 0))
    AC.mov32(a, 9, G_STRUC3_DIR)
    a.emit(A.str_(9, 28, GS_STRUC3 + S3_BASE_DIR))
    a.emit(A.movz(9, 1))
    a.emit(A.str_(9, 28, GS_STRUC3 + S3_USE_LGP))
    a.emit(A.str_(9, 28, GS_STRUC3 + S3_LGP_NUM))
    a.emit(A.str_(A.WZR, 28, GS_STRUC3 + S3_MANGLER))

    _gld32_imm(a, G_GAME_OBJECT, 27)
    a.emit(A.ldr(28, 24, HDR_GCALL))
    _guest_call(a, 19, 28, 24, LOAD_MODEL_TEX,
                [('zero',), ('zero',),
                 ('bssadd', HDR_GSCRATCH, GS_NAME_M),
                 ('bssadd', HDR_GSCRATCH, GS_STRUC3),
                 ('reg', 27)])
    a.emit(A.ldr(27, 19, 0))                       # the guest EAX
    a.cbnz(27, 'got')
    a.emit(A.ldr(9, 24, HDR_CAND))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.str_(9, 24, HDR_CAND))
    a.emit(A.cmp_imm(9, 2))
    a.bcond('cand_top', LO)

    a.label('settle')
    # Record the verdict AND accept the request, so a name that is not in the
    # archive is asked for exactly once per model per index rather than on
    # every frame for as long as the request stands.
    a.emit(A.ldrb(9, 20, SL_MISS_MOUTH))
    a.emit(A.movz(10, 1))
    a.emit(lslv(10, 10, 26))
    a.emit(orr_reg(9, 9, 10))
    a.emit(A.strb(9, 20, SL_MISS_MOUTH))
    a.emit(A.strb(26, 20, SL_MOUTH_IDX))
    a.b('out')

    a.label('got')
    a.emit(A.str_(27, 20, SL_MOUTH_TEX))
    a.emit(A.strb(26, 20, SL_MOUTH_IDX))

    a.label('out')
    # Every slow path leaves through here, including the one that has just
    # loaded an expression for the first time, so the mode force lives here
    # as well as on the fast path. It is conditional on the expression being
    # the index actually parked: if its art was not in the archive we must
    # NOT force the closed-eye mode, or the model would sit there with its
    # eyes shut instead of blinking normally.
    a.emit(A.ldrb(9, 20, SL_EXPR))
    a.cbz(9, 'done')
    a.emit(A.ldrb(10, 20, SL_APPLIED))
    a.emit(A.cmp_reg(9, 10))
    a.bcond('done', NE)
    a.emit(A.mov_reg(0, 22))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.movz(9, 2))
    a.emit(A.strb(9, 0, 0))
    a.emit(A.strb(9, 0, 1))
    a.label('done')
    _epilogue(a, BLINK_ORIG, BLINK_RESUME)
    return a.resolve()


def build_mouth_apply_cave(cave, addr, bss):
    """
    Point the head's mouth group at the loaded texture.

    Spliced immediately after the stock body's call to `field_sub_6A2736`,
    which has just reset every entry of `hundred_data_group_array` to the
    model's own descriptors -- so a mouth that stops being requested cannot
    persist, and this write is the only thing that can override group 3.
    """
    a = Asm(cave, addr)
    AC.save_host(a, 0x80)
    _ctx(a, 19)
    AC.bss_ptr(a, 24, bss)

    a.emit(A.ldr(9, 19, CTX_EBP))
    a.emit(A.add_imm(0, 9, 0xC))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldr(22, 0, 0))
    a.cbz(22, 'out')
    _gld8(a, 22, 3, 23)
    a.emit(A.cmp_imm(23, MAX_MODELS))
    a.bcond('out', HS)
    _slot(a, 23)
    a.emit(A.ldr(26, 20, SL_MOUTH_TEX))
    a.cbz(26, 'out')

    a.emit(A.ldr(9, 19, CTX_EBP))
    a.emit(A.sub_imm(0, 9, 0x14))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldr(21, 0, 0))
    a.cbz(21, 'out')
    # FFNx tests `hundred_data_group_array[3] != NULL`, which reads past the
    # end of the array on a model with fewer than four groups.  numgroups is
    # the test that cannot.
    _gld32(a, 21, P_NUMGROUPS, 27)
    a.emit(A.cmp_imm(27, MOUTH_GROUP + 1))
    a.bcond('out', LO)
    # Is group 3 actually a mouth? See MOUTH_MAX_VERTS. Seven words, and it
    # is what lets every NPC flap again without recolouring the old men.
    _gld32(a, 21, P_POLY_GROUPS, 27)
    a.cbz(27, 'out')
    a.emit(A.add_imm(0, 27, PG_STRIDE * MOUTH_GROUP + PG_NUMVERT))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldr(9, 0, 0))
    a.emit(A.cmp_imm(9, MOUTH_MAX_VERTS))
    a.bcond('out', HI)                 # a head, not a mouth -- leave it alone
    _gld32(a, 21, P_GROUP_ARRAY, 27)
    a.cbz(27, 'out')
    a.emit(A.add_imm(0, 27, 4 * MOUTH_GROUP))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.str_(26, 0, 0))
    a.emit(A.movz(26, 1))
    _gst32(a, 21, P_PER_GROUP, 26)

    a.label('out')
    _epilogue(a, MOUTH_ORIG, MOUTH_RESUME, restore_esp=False)
    return a.resolve()


def build_kawai_cave(cave, addr, bss):
    """
    Latch the EYETX parameters at the KAWAI opcode handler's entry.

    This runs BEFORE the handler's own 1..2 guard, which is the whole point:
    the guard drops an expression index and the stock path throws the mouth
    byte away (x86 0x6203EB writes a literal 0 over it).  Reading the script
    directly costs one hook and leaves every byte of the stock handler alone.
    """
    a = Asm(cave, addr)
    AC.save_host(a, 0x80)
    _ctx(a, 19)
    AC.bss_ptr(a, 24, bss)

    _gld8_imm(a, G_ENTITY_ID, 23)
    AC.mov32(a, 9, G_SCRIPT_IP)
    a.emit(A.lsl(10, 23, 1))
    a.emit(A.add_reg(9, 9, 10))
    a.emit(A.mov_reg(0, 9))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldrh(26, 0, 0))
    _gld32_imm(a, G_SCRIPT_CODE, 27)
    a.cbz(27, 'out')
    a.emit(A.add_reg(27, 27, 26))

    _gld8(a, 27, 1, 25)                            # the KAWAI byte size
    a.emit(A.cmp_imm(25, 6))
    a.bcond('out', LO)
    _gld8(a, 27, 2, 25)                            # subcode; 0 is EYETX
    a.cbnz(25, 'out')

    AC.mov32(a, 9, G_MODEL_ID_ARRAY)
    a.emit(A.add_reg(9, 9, 23))
    a.emit(A.mov_reg(0, 9))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldrb(23, 0, 0))
    a.emit(A.cmp_imm(23, MAX_MODELS))
    a.bcond('out', HS)
    _slot(a, 23)

    _gld8(a, 27, 5, 28)                            # the mouth index
    a.emit(A.strb(28, 20, SL_MOUTH_SCRIPT))
    _gld8(a, 27, 3, 25)                            # the left eye index
    a.emit(A.cmp_imm(25, 2))
    a.bcond('clear', LE)
    a.emit(A.cmp_imm(25, MAX_INDEX + 1))
    a.bcond('clear', HS)
    a.emit(A.strb(25, 20, SL_EXPR))
    a.b('out')
    a.label('clear')
    a.emit(A.strb(A.WZR, 20, SL_EXPR))

    a.label('out')
    _epilogue(a, KAWAI_ORIG, KAWAI_RESUME, restore_esp=False)
    return a.resolve()


def build_free_cave(cave, addr, bss):
    """
    Give everything back, at the field's own model teardown.

    x86 0x63E1D8 is the loop `field_load_models` runs before it loads
    anything, over every model of the field being left, and it calls
    `field_unload_model_eye_tex` on each.  Running first means the old field's
    heap is still live, so the pointers are safe to unload; putting the four
    stock pointers back means the stock loop then frees exactly what the stock
    model load created, and nothing we allocated is freed twice or dropped.
    """
    a = Asm(cave, addr)
    AC.save_host(a, 0x80)
    _ctx(a, 19)
    AC.bss_ptr(a, 24, bss)
    _carve(a)

    _gld32_imm(a, G_ANIM_PTR, 22)
    a.emit(A.movz(23, 0))
    a.label('top')
    _slot(a, 23)
    a.emit(A.ldrb(9, 20, SL_FLAGS))
    a.emit(A.and_mask(9, 9, 1))
    a.cbz(9, 'next')
    a.cbz(22, 'unload')
    AC.mov32(a, 9, G_ANIM_STRIDE)
    a.emit(A.mul(9, 23, 9))
    a.emit(A.add_reg(21, 22, 9))
    # PUT BACK ONLY WHAT WE ACTUALLY PARKED -- FINDINGS-515.
    #
    # The old form wrote all four captured pointers back unconditionally, and
    # that is the leak. This cave runs at the NEXT field's model teardown, so
    # the contract "anim still holds what we put there" only survives if
    # nothing re-created the model's textures in between. A BATTLE DOES, and
    # so does exiting to the title: both tear the models down and reload them
    # without ever reaching this hook. The slot is then stale, and writing it
    # back put a dead pointer over the texture the game had just made -- that
    # texture became unreachable and was never freed, once per model per
    # field, which is why it scaled with fields walked.
    #
    # So each side is now checked against the anim entry before anything is
    # written or freed: restore the stock pointer ONLY where the entry still
    # holds OUR art, and free our art ONLY in that same case. If the game
    # replaced it, it is not ours any more -- leave it alone and let the
    # stock teardown own it.
    #
    # The two CUSTOM pointers are no longer written here at all. The loader
    # clears all four and `build_load_cave` puts the custom pair back in the
    # same breath, so by teardown the entry already holds them; rewriting
    # them could only be a no-op or the same stale-pointer damage.
    for off, stock_sl, art_sl in ((A_STATIC_L, SL_STOCK_L, SL_ART_L),
                                  (A_STATIC_R, SL_STOCK_R, SL_ART_R)):
        a.emit(A.ldr(26, 20, art_sl))
        a.cbz(26, 'keep_%02x' % art_sl)          # nothing of ours parked
        _gld32(a, 21, off, 9)                    # what the entry holds NOW
        a.emit(A.cmp_reg(9, 26))
        a.bcond('keep_%02x' % art_sl, NE)        # the game replaced it
        # W27, not W9: `_gst32` calls the translator, and only the
        # callee-saved registers survive that `bl` -- see its docstring. The
        # first cut of this used W9 and stored the translator's leftovers.
        a.emit(A.ldr(27, 20, stock_sl))
        _gst32(a, 21, off, 27)                   # the stock pointer goes back
        a.emit(A.ldr(27, 24, HDR_GCALL))
        _guest_call(a, 19, 27, 24, UNLOAD_MODEL_TEX, [('reg', 26)])
        a.label('keep_%02x' % art_sl)
    a.label('unload')
    # The mouth is never parked in the anim entry -- the stock body resets
    # `hundred_data_group_array` every frame -- so nothing else can own it
    # and it is always ours to give back.
    a.emit(A.ldr(26, 20, SL_MOUTH_TEX))
    a.cbz(26, 'skip_mouth')
    a.emit(A.ldr(27, 24, HDR_GCALL))
    _guest_call(a, 19, 27, 24, UNLOAD_MODEL_TEX, [('reg', 26)])
    a.label('skip_mouth')
    for off in range(0, SLOT_BYTES, 8):
        a.emit(A.str64(A.XZR, 20, off))
    a.label('next')
    a.emit(A.add_imm(23, 23, 1))
    a.emit(A.cmp_imm(23, MAX_MODELS))
    a.bcond('top', LO)
    _epilogue(a, FREE_ORIG, FREE_RESUME)
    return a.resolve()


# ------------------------------------------------------------- installation
SITES = (
    ('blink', BLINK_HOOK, BLINK_ORIG, 'field_blink_3d_model entry'),
    ('mouth', MOUTH_HOOK, MOUTH_ORIG, 'the word after the sub_6A2736 call'),
    ('kawai', KAWAI_HOOK, KAWAI_ORIG, 'KAWAI script-opcode handler entry'),
    ('free', FREE_HOOK, FREE_ORIG, 'field model teardown entry'),
)
SPEAK_SITE = ('speak', SPEAK_HOOK, SPEAK_ORIG, 'MESSAGE opcode handler entry')

# ---------------------------------------------------------------------------
# THE BLINK HOOK IS INSTALLED AGAIN -- THE LEAK IS FIXED (build 517)
# ---------------------------------------------------------------------------
# Build 514 withheld this hook because the guest heap drained as fields were
# walked until an allocation failed, and `ff7nx_heap` NOPs the failure abort,
# so it showed up as corrupt battle textures rather than a crash.
#
# The cause was in `build_free_cave` and is fixed: it wrote all four captured
# pointers back into the anim entry unconditionally, which is only valid if
# nothing re-created the model's textures since the capture. A BATTLE does,
# and so does exiting to the title -- both reload the models without ever
# reaching FREE_HOOK. The stale write then buried the texture the game had
# just made, once per model per field.
#
# It now restores and frees ONLY where the anim entry still holds our art,
# and `test_teardown_after_the_game_replaced_the_textures_itself` is that
# case: it fails on the old code with the fresh pointer overwritten, and
# passes now.
#
# Set SEVENTH_NX_FACIAL_NO_BLINK=1 to withhold it again -- which costs the
# emotional eye expressions AND the voice lip flap, because the flap
# decision is made inside this same cave.
NO_BLINK_ENV = 'SEVENTH_NX_FACIAL_NO_BLINK'
BLINK_ENV = NO_BLINK_ENV              # kept: build.py reports the name


def blink_hook_wanted():
    import os
    return os.environ.get(NO_BLINK_ENV, '').strip().lower() not in (
        '1', 'true', 'yes', 'on')


def sites(voice_bss=None):
    return SITES + ((SPEAK_SITE,) if voice_bss is not None else ())


def build_all(pool, bss, voice_bss=None, flap_shift=FLAP_SHIFT_60):
    """
    Lay out the five caves in dependency order and return
    (entries, {address: word}).

    The order is forced: `build_load_cave` branches to the mouth loader's
    entry and `build_blink_cave` branches to both, so each has to exist before
    whatever names it.  There is no cycle -- every slow path leaves through
    the mouth loader's shared epilogue rather than returning to its caller --
    which is what lets each cave be confined to its own address window instead
    of needing one window big enough for all of them.
    """
    placed = {}
    entries = {}

    entries['gen'], words = ff7nx_cave.emit_laid_out(
        pool, lambda cave, addr: build_generics_cave(cave, addr, bss))
    placed.update(words)

    entries['mload'], words = ff7nx_cave.emit_laid_out(
        pool, lambda cave, addr: build_mload_cave(
            cave, addr, bss, entries['gen']))
    placed.update(words)

    entries['load'], words = ff7nx_cave.emit_laid_out(
        pool, lambda cave, addr: build_load_cave(
            cave, addr, bss, entries['mload'], entries['gen']))
    placed.update(words)

    entries['blink'], words = ff7nx_cave.emit_laid_out(
        pool, lambda cave, addr: build_blink_cave(
            cave, addr, bss, entries['load'], entries['mload'],
            voice_bss=voice_bss, flap_shift=flap_shift))
    placed.update(words)

    if voice_bss is not None:
        entries['speak'], words = ff7nx_cave.emit_laid_out(
            pool, lambda cave, addr: build_speak_cave(cave, addr, bss))
        placed.update(words)

    entries['mouth'], words = ff7nx_cave.emit_laid_out(
        pool, lambda cave, addr: build_mouth_apply_cave(cave, addr, bss))
    placed.update(words)

    entries['kawai'], words = ff7nx_cave.emit_laid_out(
        pool, lambda cave, addr: build_kawai_cave(cave, addr, bss))
    placed.update(words)

    entries['free'], words = ff7nx_cave.emit_laid_out(
        pool, lambda cave, addr: build_free_cave(cave, addr, bss))
    placed.update(words)

    for name, site, _orig, _what in sites(voice_bss):
        if name == 'blink' and not blink_hook_wanted():
            continue                      # FINDINGS-515; cave built, unhooked
        placed[site] = A.b(site, entries[name])
    return entries, placed


def apply_to_nso(src, dest, space=None, voice_bss=None, fps=60):
    """
    Install the facial runtime, patching `src` into `dest`.

    Every site is checked against the word it is expected to displace before
    anything is written, so a module another feature already hooked -- or a
    different module entirely -- refuses the patch rather than corrupting it.

    `voice_bss` is where `ff7nx_voice` put its state in THIS module.  Pass it
    and the lip flap is built; leave it out and not one word of the flap is
    emitted, the MESSAGE handler is not touched, and the feature is exactly
    the FFNx port.  It has to come from the caller because both blocks are
    allocated from the same growing BSS and only the build knows the order
    the passes ran in.
    """
    import os
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw, own = ff7nx_tables.open_module(blob)
    space = own if space is None else space
    text = space.text
    for _name, site, orig, what in sites(voice_bss):
        AC.expect_word(text, site, orig, 'facial: %s' % what)
    bss = AC.scratch_base(blob, segs)
    if voice_bss is not None and not 0 < voice_bss < bss:
        raise ValueError('facial: voice BSS 0x%X is not below this pass\'s '
                         'own block at 0x%X -- the voice pass has to run '
                         'first' % (voice_bss, bss))
    flap_shift = FLAP_SHIFT_60 if fps >= 60 else FLAP_SHIFT_30

    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))
    entries, placed = build_all(pool, bss, voice_bss=voice_bss,
                                flap_shift=flap_shift)
    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)

    out = AC.pack(blob, space.commit(), BSS_BYTES)
    _segs, check_raw = AC.segments(out)
    for name, site, orig, _what in sites(voice_bss):
        got = struct.unpack_from('<I', check_raw[0], site)[0]
        if name == 'blink' and not blink_hook_wanted():
            # Withheld on purpose: the site must still hold the STOCK word.
            if got != orig:
                raise ValueError('facial: the blink site was withheld but '
                                 'does not hold the stock word')
            continue
        if got != A.b(site, entries[name]):
            raise ValueError('facial: the %s hook did not survive the repack'
                             % name)
    old_bss, = struct.unpack_from('<I', blob, 0x3C)
    if struct.unpack_from('<I', out, 0x3C)[0] != old_bss + BSS_BYTES:
        raise ValueError('facial: BSS did not grow by the state block')

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'blink_hook': blink_hook_wanted(),
            'bss_bytes': BSS_BYTES, 'bss_base': bss, 'entries': entries,
            'cave_words': len(placed) - len(sites(voice_bss)),
            'table_bytes': 0, 'models': MAX_MODELS, 'max_index': MAX_INDEX,
            'lip_flap': voice_bss is not None, 'flap_shift': flap_shift,
            'voice_bss': voice_bss}
