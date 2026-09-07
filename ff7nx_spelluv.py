"""Keep FF7 SPT coordinates in logical texels for resized TEX payloads.

The port has no external-texture layer: an upscaled replacement's pixels AND
its dimensions live in the TEX itself, so ``graphics_object.u_offset`` becomes
1/physical and an unchanged `.s` cell samples only a fraction of it.

Each resized TEX carries its exact per-axis scale in two otherwise-unused
header words.  After ``_load_texture`` has constructed the graphics object,
this patch multiplies that object's U/V reciprocals by the scale read directly
from the function's ``struc_3->tex_header`` argument.  The argument is live on
all construction branches; the half-built output-object chain used by build
252 was not.  Unmarked data, a zero or absurd scale, and a null anywhere in
the walk all fall through unchanged, so a vanilla archive is unaffected.  No
`.s` table, quad extent or texture dimension is rewritten.

Three earlier shapes are retired and are recognised only so an old output can
be migrated back to stock:

    build 249/250   a hook in the loader tail -- covered one of three paths
    build 251       the six draw-site reads in battle_animate_texture_spt --
                    one consumer of 82, 6 reads of 356
    build 252       six reciprocal sites -- tried to find the TEX header via
                    the output object before that object/chain existed
    FINDINGS-260    the shared dimension helper 0x672C95 -- right UVs, but
                    those numbers also size the on-screen quad, so the moon
                    came out correct and enormous
"""
from __future__ import annotations

import os
import struct

import a64 as A
import ff7nx_cave
import tex

HOOK = 0xA9FCD0
RETURN_VA = HOOK + 4
HOOK_STOCK = 0xB9401668                 # ldr w8, [x19, #0x14]
TRANSLATE = 0x10FC3A0                  # guest W address -> host X pointer
X86_ENTRY = 0x6710AC

SPT_X86_ENTRY = 0x5D1FFB
SPT_ARM_ENTRY = 0x7875B0
SPT_U_SITES = (0x78827C, 0x7882C0, 0x78860C)
SPT_V_SITES = (0x7883E0, 0x788424, 0x788698)
SPT_SITES = SPT_U_SITES + SPT_V_SITES
SPT_STOCK = 0xBD400000                 # ldr s0, [x0]

# ---- WHY ONE DRAW FUNCTION AND THE SHARED DIMENSIONS BOTH FAILED --------
#
# FINDINGS-260 correctly showed that correcting the reciprocal at six reads
# in ONE draw function could not work.  The count was the whole argument:
#
#     u_offset/v_offset WRITTEN in   1 function   (x86 0x6710AC, 6 stores,
#                                                  3 code paths)
#     u_offset/v_offset READ    in  82 functions, 356 reads
#     build 251 patched              1 function,    6 reads
#
# So 350 of 356 consumers kept dividing by the physical size. `moon_1` is
# drawn by one of the other 81, which is why the moon showed 1/3 of itself at
# 3x and 1/4 at 4x -- the fraction tracked the scale exactly, the signature of
# no correction at all rather than a wrong one.
#
# Build 249's loader hook was the right idea aimed one instruction too late:
# 0x6715B3 sits after the THIRD of the three (u,v) pairs, so it corrected one
# path and left two alone. "Not authoritative for every path" was the correct
# observation and the wrong conclusion -- the answer was all three paths, not
# a different strategy.
#
# All three paths get their dimensions from one helper, x86 0x672C95, which
# reads tex_header+0x3C/+0x40 and hands them back.  FINDINGS-260 changed that
# helper, but the dimensions are shared with geometry and are not merely UV
# inputs.
#
#     0xAF1AA0   str w20, [x0]     writes WIDTH  into the out-struct
#     0xAF1AE8   str w20, [x0]     writes HEIGHT into the out-struct
#
# ---- AND WHY EVEN THE WRITER WAS THE WRONG PLACE ------------------------
#
# FINDINGS-262. Correcting the dimension helper worked -- the UVs came out
# right -- and it broke the geometry, because those same two numbers size the
# on-screen quad. The proof arrived as a screenshot: with the helper patched,
# Odin's moon was correctly proportioned and ENORMOUS, so only a corner of it
# fit the frame. The earlier all-textures halve probe said the same thing in
# advance: it squished world-map textures and battle text, which are not UV
# effects at all.
#
# So the correction belongs on the RECIPROCAL and nothing else. `_load_texture`
# computes it in exactly six places, three (u, v) pairs for its three paths,
# and the recompiled form of each is
#
#     ldr s0, [1.0]  /  fcvt d0, s0  /  ldr d1, [x21, x8]  /  fdiv d0, d0, d1
#
# Replacing that one `fdiv` with a call that divides and then multiplies by the
# marked scale gives 1/logical, leaves the dimensions physical, and touches
# nothing that sizes a quad.
RECIP_U_SITES = (0xA9EE74, 0xA9EF20, 0xA9FBE4)
RECIP_V_SITES = (0xA9EFCC, 0xA9F00C, 0xA9FC90)
RECIP_SITES = RECIP_U_SITES + RECIP_V_SITES
RECIP_STOCK = 0x1E611800               # fdiv d0, d0, d1
RECIP_EBP = 0x14                       # [x19, #0x14] is the guest EBP
RECIP_OBJ = 0x10                       # in _load_texture: object at [EBP-0x10]

# ---- AND THERE ARE EIGHT OF THEM, NOT SIX -------------------------------
#
# FINDINGS-263. Every earlier scan looked for a reciprocal stored to +0x24 or
# +0x28 -- graphics_object.u_offset/v_offset -- and so found only the six in
# _load_texture. Searching instead for the CONSTANT, `fdivr [0x7B7ACC]`, finds
# eight:
#
#     0x6710AC  _load_texture   6 stores  -> +0x24 / +0x28
#     0x672E29  sprite loader   2 stores  -> +0x18 / +0x1C   <- missed by all
#
# 0x672E29 builds its own descriptor: width at +0x10, height at +0x14, their
# reciprocals at +0x18/+0x1C, and param * reciprocal at +0x28/+0x2C. It is a
# second, independent UV normaliser, and it is the one Odin's moon goes
# through. That is the whole discrepancy:
#
#   patching 0x672C95 (the shared DIMENSION helper) reached both, because
#     both call it -- so the UVs came out right and the geometry broke, since
#     those same numbers also size the quad
#   patching only _load_texture's six reciprocals reached one -- so Odin was
#     never corrected at all and kept showing its upper-left corner
#
# The graphics object is at a different local here: [EBP-8], not [EBP-0x10].
RECIP2_U_SITES = (0xAFB4C0,)           # -> descriptor +0x18 (1/width)
RECIP2_V_SITES = (0xAFB56C,)           # -> descriptor +0x1C (1/height)
RECIP2_SITES = RECIP2_U_SITES + RECIP2_V_SITES
RECIP2_OBJ = 0x08                      # in 0x672E29: object at [EBP-8]

# ---- AND THE DESCRIPTOR'S RAW DIMENSIONS ARE READ TOO --------------------
#
# FINDINGS-264, by elimination rather than by theory. The 0x672C95 result is
# read in exactly two places:
#
#   _load_texture   ONLY by the six fild -> reciprocal. Nothing else.
#   0x672E29        by its two reciprocals AND stored raw at descriptor
#                   +0x10 / +0x14.
#
# All eight reciprocals are now corrected and Odin did not move, while
# changing 0x672C95 itself always did. Since 0x672C95's only other effect is
# those two raw stores, they are what Odin reads. Correcting them here --
# rather than at 0x672C95 -- leaves _load_texture's dimensions physical, so
# nothing outside this one descriptor is touched.
DIMSTORE_U_SITE = 0xAFB418             # str w22, [x0]  -> descriptor +0x10
DIMSTORE_V_SITE = 0xAFB450             # str w22, [x0]  -> descriptor +0x14
DIMSTORE_SITES = (DIMSTORE_U_SITE, DIMSTORE_V_SITE)
DIMSTORE_STOCK = 0xB9000016            # str w22, [x0]

# ---- WHICH CALLER, MEASURED ---------------------------------------------
#
# FINDINGS-266. 0x672C95 has four callers. Only one of them supplies the
# dimension Odin's moon divides by, and it was found by measurement rather
# than inference: a probe divided the HEIGHT by a different power of two per
# caller, so the screen reported which one by how many moons stacked.
#
#     caller 1  _load_texture path 1   ->  half a moon
#     caller 2  _load_texture path 2   ->  one moon
#     caller 3  _load_texture path 3   ->  TWO moons      <-- observed
#     caller 4  0x672E29 sprite loader ->  four moons
#
# Two moons. So the correction goes on path 3's call and nowhere else, which
# is also what makes it safe: in _load_texture the 0x672C95 result feeds ONLY
# the fild -> reciprocal, so nothing geometric can move. The other three
# callers are left alone precisely because theirs DO size quads -- correcting
# all four at once is FINDINGS-262's enormous moon.
CALLER3_RETURN = 0xA9FB50              # the path-3 call's return address

# FINDINGS-267. Odin measured as caller 3, but all THREE _load_texture paths
# are equally safe: their 0x672C95 results are each used exactly once, by a
# `fild` feeding the reciprocal, and by nothing else. Verified instruction by
# instruction over the whole function. So the gate whitelists all three, which
# covers every texture the loader builds regardless of which mode it took.
#
# Caller 4 -- the 0x672E29 sprite loader -- stays excluded, and that exclusion
# is the whole safety property: ITS dimensions are also stored raw into the
# sprite descriptor at +0x10/+0x14, which sizes the on-screen quad. Correcting
# it is what produced FINDINGS-262's correct-but-enormous moon.
LOADER_RETURNS = (0xA9E874, 0xA9EC08, 0xA9FB50)   # paths 1, 2, 3
SPRITE_RETURN = 0xAFB3C4                          # 0x672E29 -- NEVER corrected


WRITER_X86_ENTRY = 0x672C95
WRITER_ARM_ENTRY = 0xAF1890
WRITER_U_SITE = 0xAF1AA0               # str w20, [x0]  <- texture WIDTH
WRITER_V_SITE = 0xAF1AE8               # str w20, [x0]  <- texture HEIGHT
WRITER_SITES = (WRITER_U_SITE, WRITER_V_SITE)
WRITER_STOCK = 0xB9000014              # str w20, [x0]
WRITER_SCRATCH = 8                     # [x19, #8] holds the tex_header guest ptr

# Exact surrounding code and called helper. These reject a different port or
# a shifted recompilation before any output is written.
ANCHORS = {
    0xA9E240: 0xFC1D0FE8,
    0xA9E244: 0xF90007F5,
    HOOK - 8: 0x941971B6,
    HOOK - 4: 0xBD000008,
    RETURN_VA: 0x51004100,
    RETURN_VA + 4: 0x941971B2,
    TRANSLATE: 0x34000180,
    TRANSLATE + 4: 0xD0000E88,
    TRANSLATE + 8: 0xF9446108,
    # The writer helper, and the two instructions that put the tex_header
    # guest pointer into the scratch slot the cave reads. If the port shifts,
    # these stop matching before anything is written.
    WRITER_ARM_ENTRY: 0xA9BE4FF4,      # stp x20, x19, [sp, #-0x20]!
    WRITER_U_SITE - 0x1C: 0xB9000A68,  # str w8, [x19, #8]   (tex_header ptr)
    WRITER_U_SITE - 4: 0x94182A41,     # bl  TRANSLATE
    WRITER_V_SITE - 0x1C: 0xB9000A68,  # str w8, [x19, #8]
    WRITER_V_SITE - 4: 0x94182A2F,     # bl  TRANSLATE
}
# The hook words themselves are deliberately not anchors.  ``state()``
# validates them against either stock or an exact, fully read-back cave shape.
# Keeping mutable hook words in ANCHORS made ``verify()`` reject the module
# immediately after we had installed it, and also made every recognised
# legacy shape impossible to migrate.


N_WORDS = 55                           # legacy loader caves only
LOGICAL_WORDS = 87


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _b_target(word, va):
    if (word & 0xFC000000) != 0x14000000:
        return None
    off = word & 0x03FFFFFF
    if off & (1 << 25):
        off -= 1 << 26
    return va + off * 4


def _bl_target(word, va):
    if (word & 0xFC000000) != 0x94000000:
        return None
    off = word & 0x03FFFFFF
    if off & (1 << 25):
        off -= 1 << 26
    return va + off * 4


def _loader_live_body(entry, addr):
    """Address-aware cave words; conditional labels use the real layout."""
    done = 50
    mark = tex.SYW_SCALE_MARK
    w = [None] * N_WORDS
    w[0] = A.stp64_pre(20, 21, A.SP, -0x40)
    w[1] = A.str64(30, A.SP, 0x10)
    w[2] = A.stp_q_off(0, 1, A.SP, 0x20)

    # Resolve the newly constructed graphics object from guest [EBP-0x10].
    # Keep its HOST pointer in nonvolatile X21 across all translator calls.
    w[3] = A.ldr(0, 19, 0x14)
    w[4] = A.sub_imm(0, 0, 0x10)
    w[5] = A.bl(addr(5), TRANSLATE)
    w[6] = A.cbz64(0, addr(6), addr(done))
    w[7] = A.ldr(0, 0)
    w[8] = A.cbz(0, addr(8), addr(done))
    w[9] = A.bl(addr(9), TRANSLATE)
    w[10] = A.cbz64(0, addr(10), addr(done))
    w[11] = A.mov_reg64(21, 0)

    # Follow the exact 32-bit guest-pointer chain used by
    # battle_animate_texture_spt:
    #   object+0x0C -> p_hundred
    #   hundred+0x14 -> ff7_texture_set
    #   texture_set+0x84 -> ff7_tex_header
    # Translate each pointer separately; guest pages are not host-contiguous.
    w[12] = A.ldr(0, 21, 0x0C)
    w[13] = A.cbz(0, addr(13), addr(done))
    w[14] = A.bl(addr(14), TRANSLATE)
    w[15] = A.cbz64(0, addr(15), addr(done))
    w[16] = A.ldr(0, 0, 0x14)
    w[17] = A.cbz(0, addr(17), addr(done))
    w[18] = A.bl(addr(18), TRANSLATE)
    w[19] = A.cbz64(0, addr(19), addr(done))
    w[20] = A.ldr(0, 0, 0x84)
    w[21] = A.cbz(0, addr(21), addr(done))
    w[22] = A.bl(addr(22), TRANSLATE)
    w[23] = A.cbz64(0, addr(23), addr(done))

    # Marker and packed per-axis factors. Each axis is independently 1..16;
    # this preserves authored non-native canvas aspects such as smoke1.
    w[24] = A.ldr(8, 0, tex.O_USER_MARK)
    w[25] = A.movz(9, mark & 0xFFFF)
    w[26] = A.movk_hi(9, mark >> 16)
    w[27] = A.cmp_reg(8, 9)
    w[28] = A.bcond(addr(28), addr(done), A.NE)
    w[29] = A.ldr(20, 0, tex.O_USER_SCALE)
    w[30] = A.and_mask(8, 20, 16)
    w[31] = A.cbz(8, addr(31), addr(done))
    w[32] = A.cmp_imm(8, 16)
    w[33] = A.bcond(addr(33), addr(done), A.HI)
    w[34] = A.lsr(9, 20, 16)
    w[35] = A.cbz(9, addr(35), addr(done))
    w[36] = A.cmp_imm(9, 16)
    w[37] = A.bcond(addr(37), addr(done), A.HI)
    w[38] = 0xD503201F                         # reserved marker-v1 slot
    w[39] = 0xD503201F

    # 1/(logical*k) * k = 1/logical. Re-extract after the calls so W8/W9
    # never need to survive the AAPCS64 volatile-register boundary.
    w[40] = A.and_mask(8, 20, 16)
    w[41] = A.ldr_s(0, 21, 0x24)
    w[42] = A.ucvtf_s(1, 8)
    w[43] = A.fmul_s(0, 0, 1)
    w[44] = A.str_s(0, 21, 0x24)
    w[45] = A.lsr(9, 20, 16)
    w[46] = A.ldr_s(0, 21, 0x28)
    w[47] = A.ucvtf_s(1, 9)
    w[48] = A.fmul_s(0, 0, 1)
    w[49] = A.str_s(0, 21, 0x28)

    # Restore all nonvolatile/native state and the FP temporaries used inline,
    # execute the displaced instruction, then rejoin the stock common block.
    w[50] = A.ldp_q_off(0, 1, A.SP, 0x20)
    w[51] = A.ldr64(30, A.SP, 0x10)
    w[52] = A.ldp64_post(20, 21, A.SP, 0x40)
    w[53] = HOOK_STOCK
    w[54] = A.b(addr(54), RETURN_VA)
    assert all(x is not None for x in w)
    return w


def _legacy_struc3_body(entry, addr):
    """Build-249 cave, retained only for exact in-place upgrade detection."""
    done = 50
    mark = tex.SYW_SCALE_MARK
    w = [None] * N_WORDS
    w[0] = A.stp64_pre(20, 21, A.SP, -0x40)
    w[1] = A.str64(30, A.SP, 0x10)
    w[2] = A.stp_q_off(0, 1, A.SP, 0x20)
    w[3] = A.ldr(0, 19, 0x14)
    w[4] = A.add_imm(0, 0, 0x10)
    w[5] = A.bl(addr(5), TRANSLATE)
    w[6] = A.cbz64(0, addr(6), addr(done))
    w[7] = A.ldr(0, 0)
    w[8] = A.cbz(0, addr(8), addr(done))
    w[9] = A.bl(addr(9), TRANSLATE)
    w[10] = A.cbz64(0, addr(10), addr(done))
    w[11] = A.ldr(0, 0, 0x28)
    w[12] = A.cbz(0, addr(12), addr(done))
    w[13] = A.bl(addr(13), TRANSLATE)
    w[14] = A.cbz64(0, addr(14), addr(done))
    w[15] = A.ldr(8, 0, tex.O_USER_MARK)
    w[16] = A.movz(9, mark & 0xFFFF)
    w[17] = A.movk_hi(9, mark >> 16)
    w[18] = A.cmp_reg(8, 9)
    w[19] = A.bcond(addr(19), addr(done), A.NE)
    w[20] = A.ldr(20, 0, tex.O_USER_SCALE)
    w[21] = A.and_mask(8, 20, 16)
    w[22] = A.cbz(8, addr(22), addr(done))
    w[23] = A.cmp_imm(8, 16)
    w[24] = A.bcond(addr(24), addr(done), A.HI)
    w[25] = A.lsr(9, 20, 16)
    w[26] = A.cbz(9, addr(26), addr(done))
    w[27] = A.cmp_imm(9, 16)
    w[28] = A.bcond(addr(28), addr(done), A.HI)
    w[29] = w[30] = 0xD503201F
    w[31] = A.ldr(0, 19, 0x14)
    w[32] = A.sub_imm(0, 0, 0x10)
    w[33] = A.bl(addr(33), TRANSLATE)
    w[34] = A.cbz64(0, addr(34), addr(done))
    w[35] = A.ldr(0, 0)
    w[36] = A.cbz(0, addr(36), addr(done))
    w[37] = A.bl(addr(37), TRANSLATE)
    w[38] = A.cbz64(0, addr(38), addr(done))
    w[39] = A.mov_reg64(21, 0)
    w[40] = A.and_mask(8, 20, 16)
    w[41] = A.ldr_s(0, 21, 0x24)
    w[42] = A.ucvtf_s(1, 8)
    w[43] = A.fmul_s(0, 0, 1)
    w[44] = A.str_s(0, 21, 0x24)
    w[45] = A.lsr(9, 20, 16)
    w[46] = A.ldr_s(0, 21, 0x28)
    w[47] = A.ucvtf_s(1, 9)
    w[48] = A.fmul_s(0, 0, 1)
    w[49] = A.str_s(0, 21, 0x28)
    w[50] = A.ldp_q_off(0, 1, A.SP, 0x20)
    w[51] = A.ldr64(30, A.SP, 0x10)
    w[52] = A.ldp64_post(20, 21, A.SP, 0x40)
    w[53] = HOOK_STOCK
    w[54] = A.b(addr(54), RETURN_VA)
    return w


def logical_body(entry, addr):
    """Correct the completed object's reciprocals at the common join.

    FINDINGS-265. Build 253 hooked the right INSTRUCTION and read the wrong
    POINTER. It took the TEX header from ``[EBP+0x10] -> struc_3 -> +0x28``.
    In `_load_texture` that word is only ever COMPARED TO ZERO (0x6713BF); it
    is never dereferenced as a header. So the marker never matched, the hook
    fell through every time, and builds 253-254 changed nothing at all.

    The header source used here is the one the game itself uses, in 0x672C95:

        object +0x0C -> p_hundred +0x14 -> texture_set +0x84 -> tex_header

    x86 0x6715B3 is still the right place. Paths 1 and 2 jump to 0x67134D,
    which REPLACES the object at [EBP-0x10] with a freshly allocated one and
    then falls through into path 3's own reciprocal stores -- so whatever the
    first two paths wrote is discarded, and this join is the only point where
    the surviving object's u/v are final.

    Each guest address is translated in full rather than offset from one host
    pointer, so an object spanning a guest page boundary stays correct.
    """
    done = 71
    mark = tex.SYW_SCALE_MARK
    w = [
        A.stp64_pre(0, 1, A.SP, -0xF0),           # 0
        A.stp64_off(2, 3, A.SP, 0x10),            # 1
        A.stp64_off(4, 5, A.SP, 0x20),            # 2
        A.stp64_off(6, 7, A.SP, 0x30),            # 3
        A.stp64_off(8, 9, A.SP, 0x40),            # 4
        A.stp64_off(10, 11, A.SP, 0x50),          # 5
        A.stp64_off(12, 13, A.SP, 0x60),          # 6
        A.stp64_off(14, 15, A.SP, 0x70),          # 7
        A.stp64_off(16, 17, A.SP, 0x80),          # 8
        A.stp64_off(18, 20, A.SP, 0x90),          # 9
        A.stp64_off(21, 30, A.SP, 0xA0),          # 10
        A.stp_q_off(0, 1, A.SP, 0xB0),            # 11
        A.mrs_nzcv(17),                           # 12
        A.str64(17, A.SP, 0xD0),                  # 13

        # The completed graphics object, from guest [EBP-0x10].
        A.ldr(0, 19, RECIP_EBP),                  # 14
        A.sub_imm(0, 0, RECIP_OBJ),               # 15
        A.bl(addr(16), TRANSLATE),                # 16
        A.cbz64(0, addr(17), addr(done)),         # 17
        A.ldr(20, 0),                             # 18 guest object -> w20
        A.cbz(20, addr(19), addr(done)),          # 19

        A.add_imm(0, 20, 0x0C),                   # 20 object +0x0C
        A.bl(addr(21), TRANSLATE),                # 21
        A.cbz64(0, addr(22), addr(done)),         # 22
        A.ldr(0, 0),                              # 23 p_hundred
        A.cbz(0, addr(24), addr(done)),           # 24
        A.add_imm(0, 0, 0x14),                    # 25 hundred +0x14
        A.bl(addr(26), TRANSLATE),                # 26
        A.cbz64(0, addr(27), addr(done)),         # 27
        A.ldr(0, 0),                              # 28 texture_set
        A.cbz(0, addr(29), addr(done)),           # 29
        A.add_imm(0, 0, 0x84),                    # 30 texture_set +0x84
        A.bl(addr(31), TRANSLATE),                # 31
        A.cbz64(0, addr(32), addr(done)),         # 32
        A.ldr(21, 0),                             # 33 guest tex_header -> w21
        A.cbz(21, addr(34), addr(done)),          # 34

        A.add_imm(0, 21, tex.O_USER_MARK),        # 35
        A.bl(addr(36), TRANSLATE),                # 36
        A.cbz64(0, addr(37), addr(done)),         # 37
        A.ldr(8, 0),                              # 38
        A.movz(9, mark & 0xFFFF),                 # 39
        A.movk_hi(9, mark >> 16),                 # 40
        A.cmp_reg(8, 9),                          # 41
        A.bcond(addr(42), addr(done), A.NE),      # 42

        A.add_imm(0, 21, tex.O_USER_SCALE),       # 43
        A.bl(addr(44), TRANSLATE),                # 44
        A.cbz64(0, addr(45), addr(done)),         # 45
        A.ldr(21, 0),                             # 46 packed scales -> w21
        A.and_mask(8, 21, 16),                    # 47 sx
        A.cbz(8, addr(48), addr(done)),           # 48
        A.cmp_imm(8, 16),                         # 49
        A.bcond(addr(50), addr(done), A.HI),      # 50
        A.lsr(9, 21, 16),                         # 51 sy
        A.cbz(9, addr(52), addr(done)),           # 52
        A.cmp_imm(9, 16),                         # 53
        A.bcond(addr(54), addr(done), A.HI),      # 54
        # Park both scales; every register below crosses a translator call.
        A.str_(8, A.SP, 0xD8),                    # 55
        A.str_(9, A.SP, 0xE0),                    # 56

        # object +0x24 *= sx
        A.add_imm(0, 20, 0x24),                   # 57
        A.bl(addr(58), TRANSLATE),                # 58
        A.ldr_s(0, 0),                            # 59
        A.ldr(8, A.SP, 0xD8),                     # 60
        A.ucvtf_s(1, 8),                          # 61
        A.fmul_s(0, 0, 1),                        # 62
        A.str_s(0, 0),                            # 63
        # object +0x28 *= sy
        A.add_imm(0, 20, 0x28),                   # 64
        A.bl(addr(65), TRANSLATE),                # 65
        A.ldr_s(0, 0),                            # 66
        A.ldr(9, A.SP, 0xE0),                     # 67
        A.ucvtf_s(1, 9),                          # 68
        A.fmul_s(0, 0, 1),                        # 69
        A.str_s(0, 0),                            # 70

        A.ldr64(17, A.SP, 0xD0),                  # 71 done
        A.msr_nzcv(17),                           # 72
        A.ldp_q_off(0, 1, A.SP, 0xB0),            # 73
        A.ldp64_off(2, 3, A.SP, 0x10),            # 74
        A.ldp64_off(4, 5, A.SP, 0x20),            # 75
        A.ldp64_off(6, 7, A.SP, 0x30),            # 76
        A.ldp64_off(8, 9, A.SP, 0x40),            # 77
        A.ldp64_off(10, 11, A.SP, 0x50),          # 78
        A.ldp64_off(12, 13, A.SP, 0x60),          # 79
        A.ldp64_off(14, 15, A.SP, 0x70),          # 80
        A.ldp64_off(16, 17, A.SP, 0x80),          # 81
        A.ldp64_off(18, 20, A.SP, 0x90),          # 82
        A.ldp64_off(21, 30, A.SP, 0xA0),          # 83
        A.ldp64_post(0, 1, A.SP, 0xF0),           # 84
        HOOK_STOCK,                               # 85 the displaced instruction
        A.b(addr(86), RETURN_VA),                 # 86
    ]
    assert len(w) == 87
    return w


def _loader_walk_physical(img, n_words=N_WORDS):
    """Addresses occupied by this cave, including allocator link branches."""
    entry = _b_target(_word(img, HOOK), HOOK)
    if entry is None or entry == RETURN_VA:
        return []
    va, out, logical = entry, [], 0
    while len(out) < 384 and va not in out:
        out.append(va)
        word = _word(img, va)
        target = _b_target(word, va)
        if target == RETURN_VA:
            return out if logical == n_words - 1 else []
        if target is not None:                 # allocator run-to-run link
            va = target
            continue
        logical += 1
        va += 4
    return []


def _loader_walk(img, n_words=N_WORDS):
    """Logical cave words with allocator links removed."""
    phys = _loader_walk_physical(img, n_words)
    if not phys:
        return []
    out = []
    for va in phys:
        word = _word(img, va)
        target = _b_target(word, va)
        if target is not None and target != RETURN_VA:
            continue
        out.append((va, word))
    return out


def _loader_live_installed(img):
    got = _loader_walk(img)
    if len(got) != N_WORDS:
        return False
    addrs = [va for va, _ in got]
    want = _loader_live_body(addrs[0], lambda i: addrs[i])
    return [word for _, word in got] == want


def legacy_installed(img):
    got = _loader_walk(img)
    if len(got) != N_WORDS:
        return False
    addrs = [va for va, _ in got]
    want = _legacy_struc3_body(addrs[0], lambda i: addrs[i])
    return [word for _, word in got] == want


def _logical_installed(img):
    got = _loader_walk(img, LOGICAL_WORDS)
    if len(got) != LOGICAL_WORDS:
        return False
    addrs = [va for va, _ in got]
    want = logical_body(addrs[0], lambda i: addrs[i])
    return [word for _, word in got] == want


def _recip_body_v1(axis, entry, addr):
    """The unfinished 56-word reciprocal cave left by the prior session.

    Retained only for exact migration.  It computes the right value, but its
    marker validation changes NZCV even though the displaced ``fdiv`` does
    not.  A translated caller is allowed to keep x86 condition state live in
    NZCV, so production must use :func:`recip_body` below.
    """
    if axis not in ('u', 'v'):
        raise ValueError('axis must be u or v')
    done = 44
    mark = tex.SYW_SCALE_MARK
    w = [
        A.stp64_pre(0, 1, A.SP, -0xC0),
        A.stp64_off(2, 3, A.SP, 0x10),
        A.stp64_off(4, 5, A.SP, 0x20),
        A.stp64_off(6, 7, A.SP, 0x30),
        A.stp64_off(8, 9, A.SP, 0x40),
        A.stp64_off(10, 11, A.SP, 0x50),
        A.stp64_off(12, 13, A.SP, 0x60),
        A.stp64_off(14, 15, A.SP, 0x70),
        A.stp64_off(16, 17, A.SP, 0x80),
        A.stp64_off(18, 30, A.SP, 0x90),
        A.stp_q_off(1, 2, A.SP, 0xA0),
        A.fdiv_d(0, 0, 1),
        A.ldr(0, 19, RECIP_EBP),
        A.sub_imm(0, 0, RECIP_OBJ),
        A.bl(addr(14), TRANSLATE),
        A.cbz64(0, addr(15), addr(done)),
        A.ldr(0, 0),
        A.cbz(0, addr(17), addr(done)),
        A.bl(addr(18), TRANSLATE),
        A.cbz64(0, addr(19), addr(done)),
        A.ldr(0, 0, 0x0C),
        A.cbz(0, addr(21), addr(done)),
        A.bl(addr(22), TRANSLATE),
        A.cbz64(0, addr(23), addr(done)),
        A.ldr(0, 0, 0x14),
        A.cbz(0, addr(25), addr(done)),
        A.bl(addr(26), TRANSLATE),
        A.cbz64(0, addr(27), addr(done)),
        A.ldr(0, 0, 0x84),
        A.cbz(0, addr(29), addr(done)),
        A.bl(addr(30), TRANSLATE),
        A.cbz64(0, addr(31), addr(done)),
        A.ldr(8, 0, tex.O_USER_MARK),
        A.movz(9, mark & 0xFFFF),
        A.movk_hi(9, mark >> 16),
        A.cmp_reg(8, 9),
        A.bcond(addr(36), addr(done), A.NE),
        A.ldr(8, 0, tex.O_USER_SCALE),
        (A.and_mask(8, 8, 16) if axis == 'u' else A.lsr(8, 8, 16)),
        A.cbz(8, addr(39), addr(done)),
        A.cmp_imm(8, 16),
        A.bcond(addr(41), addr(done), A.HI),
        A.ucvtf_d(1, 8),
        A.fmul_d(0, 0, 1),
        A.ldp_q_off(1, 2, A.SP, 0xA0),
        A.ldp64_off(2, 3, A.SP, 0x10),
        A.ldp64_off(4, 5, A.SP, 0x20),
        A.ldp64_off(6, 7, A.SP, 0x30),
        A.ldp64_off(8, 9, A.SP, 0x40),
        A.ldp64_off(10, 11, A.SP, 0x50),
        A.ldp64_off(12, 13, A.SP, 0x60),
        A.ldp64_off(14, 15, A.SP, 0x70),
        A.ldp64_off(16, 17, A.SP, 0x80),
        A.ldp64_off(18, 30, A.SP, 0x90),
        A.ldp64_post(0, 1, A.SP, 0xC0),
        A.ret(),
    ]
    assert len(w) == 56
    return w


def path3_dim_body(axis, entry, addr):
    """0x672C95's dimension store -> the logical extent, for loader callers.

    Gated on WHICH CALLER invoked the helper. The function's own saved LR sits
    at [sp, #0x18], so with a 0xB0 cave frame that is [sp, #0xC8]. The compare
    is on (LR - cave_base), a build-time constant, because both are runtime
    addresses whose absolute values are unknown until load.

    Whitelists _load_texture's three paths and excludes the sprite loader --
    see LOADER_RETURNS. That exclusion is the safety property, not an
    oversight.

    NZCV IS SAVED AND RESTORED. The displaced instruction is a plain store and
    sets no flags, so the translated code after it is entitled to whatever the
    flags held before -- and this cave runs several `cmp`s. The suite caught
    that after hardware had already called the fix good.

    The fallback in w2 is re-established after the translator call: a value
    living across `bl` has been the same bug three separate times here.
    """
    if axis not in ('u', 'v'):
        raise ValueError('axis must be u or v')
    pairs = ((2, 3), (4, 5), (6, 7), (8, 9), (10, 11), (12, 13),
             (14, 15), (16, 17), (18, 30))
    n = len(LOADER_RETURNS)
    proceed = 16 + 4 * n            # first instruction past the caller gate
    store = proceed + 16
    w = [A.stp64_pre(0, 1, A.SP, -0xB0)]
    for k, (a, b) in enumerate(pairs):
        w.append(A.stp64_off(a, b, A.SP, 0x10 * (k + 1)))
    w += [
        A.mrs_nzcv(3),                                     # 10
        A.str64(3, A.SP, 0xA0),                            # 11
        A.mov_reg(2, 20),                                  # 12 default
        A.ldr64(9, A.SP, 0xC8),                            # 13 saved LR
        A.adr(8, addr(14), addr(0)),                       # 14 cave base
        A.sub_reg64(9, 9, 8),                              # 15
    ]
    for j, ret in enumerate(LOADER_RETURNS):
        delta = (ret - entry) & 0xFFFFFFFF
        i = 16 + 4 * j
        w += [
            A.movz(10, delta & 0xFFFF),                    # i
            A.movk_hi(10, (delta >> 16) & 0xFFFF),         # i+1
            A.cmp_reg(9, 10),                              # i+2
            # the last candidate falls through to `store` on a miss; the
            # earlier ones jump forward on a hit
            (A.bcond(addr(i + 3), addr(store), A.NE)
             if j == n - 1
             else A.bcond(addr(i + 3), addr(proceed), A.EQ)),
        ]
    w += [
        A.ldr(0, 19, WRITER_SCRATCH),                      # proceed
        A.cbz(0, addr(proceed + 1), addr(store)),
        A.bl(addr(proceed + 2), TRANSLATE),
        A.mov_reg(2, 20),                                  # re-establish
        A.cbz64(0, addr(proceed + 4), addr(store)),
        A.ldr(8, 0, tex.O_USER_MARK),                      # the one word
        A.lsr(9, 8, 16),                                   # magic half
        A.movz(10, tex.SYW_SCALE_MAGIC),
        A.cmp_reg(9, 10),
        A.bcond(addr(proceed + 9), addr(store), A.NE),
        (A.and_mask(8, 8, 8) if axis == 'u' else A.lsr(8, 8, 8)),
        A.and_mask(8, 8, 8),
        A.cbz(8, addr(proceed + 12), addr(store)),
        A.cmp_imm(8, 16),
        A.bcond(addr(proceed + 14), addr(store), A.HI),
        A.udiv(2, 20, 8),                                  # proceed+15
        A.ldr64(3, A.SP, 0xA0),                            # store: flags
        A.msr_nzcv(3),
        A.ldr64(0, A.SP, 0),                               # destination
        A.str_(2, 0),                                      # displaced store
    ]
    for k, (a, b) in enumerate(pairs):
        w.append(A.ldp64_off(a, b, A.SP, 0x10 * (k + 1)))
    w += [A.ldp64_post(0, 1, A.SP, 0xB0), A.ret()]
    assert len(w) == store + 4 + len(pairs) + 2
    return w


PATH_DIM_WORDS = len(path3_dim_body('u', 0, lambda i: i * 4))


def _path3_installed(img, entry, axis):
    if entry is None:
        return False
    got = _walk_helper(img, entry)
    if len(got) != PATH_DIM_WORDS:
        return False
    addrs = [va for va, _ in got]
    return [x for _, x in got] == path3_dim_body(axis, addrs[0],
                                                 lambda i: addrs[i])


def path3_installed(img):
    u = _bl_target(_word(img, WRITER_U_SITE), WRITER_U_SITE)
    v = _bl_target(_word(img, WRITER_V_SITE), WRITER_V_SITE)
    return (_path3_installed(img, u, 'u') and _path3_installed(img, v, 'v'))


def dimstore_body(axis, entry, addr):
    """Replacement for ``str w22, [x0]`` into descriptor +0x10 / +0x14.

    Stores the LOGICAL extent. The graphics object is at guest [EBP-8] and the
    walk to its tex_header is the usual one.

    ``w22`` itself is left untouched, and the fallback is re-established after
    EVERY translator call: the real translator only clobbers x0/x8/x9/x10, but
    the emulator models the full AAPCS volatile set and caught a value dying
    across the second call here.
    """
    if axis not in ('u', 'v'):
        raise ValueError('axis must be u or v')
    done = 42
    mark = tex.SYW_SCALE_MARK
    pairs = ((2, 3), (4, 5), (6, 7), (8, 9), (10, 11), (12, 13),
             (14, 15), (16, 17))
    w = [A.stp64_pre(0, 1, A.SP, -0xA0)]
    for k, (a, b) in enumerate(pairs):
        w.append(A.stp64_off(a, b, A.SP, 0x10 * (k + 1)))
    w.append(A.stp64_off(18, 30, A.SP, 0x90))
    w.append(A.mov_reg(2, 22))
    w.append(A.ldr(0, 19, RECIP_EBP))
    w.append(A.sub_imm(0, 0, RECIP2_OBJ))
    w.append(A.bl(addr(13), TRANSLATE))
    w.append(A.mov_reg(2, 22))
    w.append(A.ldr(0, 0))
    w.append(A.cbz(0, addr(16), addr(done)))
    w.append(A.bl(addr(17), TRANSLATE))
    w.append(A.mov_reg(2, 22))
    w.append(A.ldr(0, 0, 0x0C))
    w.append(A.cbz(0, addr(20), addr(done)))
    w.append(A.bl(addr(21), TRANSLATE))
    w.append(A.mov_reg(2, 22))
    w.append(A.ldr(0, 0, 0x14))
    w.append(A.cbz(0, addr(24), addr(done)))
    w.append(A.bl(addr(25), TRANSLATE))
    w.append(A.mov_reg(2, 22))
    w.append(A.ldr(0, 0, 0x84))
    w.append(A.cbz(0, addr(28), addr(done)))
    w.append(A.bl(addr(29), TRANSLATE))
    w.append(A.mov_reg(2, 22))
    w.append(A.ldr(8, 0, tex.O_USER_MARK))
    w.append(A.movz(9, mark & 0xFFFF))
    w.append(A.movk_hi(9, mark >> 16))
    w.append(A.cmp_reg(8, 9))
    w.append(A.bcond(addr(35), addr(done), A.NE))
    w.append(A.ldr(8, 0, tex.O_USER_SCALE))
    w.append(A.and_mask(8, 8, 16) if axis == 'u' else A.lsr(8, 8, 16))
    w.append(A.cbz(8, addr(38), addr(done)))
    w.append(A.cmp_imm(8, 16))
    w.append(A.bcond(addr(40), addr(done), A.HI))
    w.append(A.udiv(2, 22, 8))
    w.append(A.ldr64(0, A.SP, 0))
    w.append(A.str_(2, 0))
    for k, (a, b) in enumerate(pairs):
        w.append(A.ldp64_off(a, b, A.SP, 0x10 * (k + 1)))
    w.append(A.ldp64_off(18, 30, A.SP, 0x90))
    w.append(A.ldp64_post(0, 1, A.SP, 0xA0))
    w.append(A.ret())
    assert len(w) == 55
    return w


def _dimstore_installed(img, entry, axis):
    if entry is None:
        return False
    got = _walk_helper(img, entry)
    if len(got) != 55:
        return False
    addrs = [va for va, _ in got]
    return [x for _, x in got] == dimstore_body(axis, addrs[0],
                                                lambda i: addrs[i])


def dimstore_installed(img):
    u = _bl_target(_word(img, DIMSTORE_U_SITE), DIMSTORE_U_SITE)
    v = _bl_target(_word(img, DIMSTORE_V_SITE), DIMSTORE_V_SITE)
    return (_dimstore_installed(img, u, 'u')
            and _dimstore_installed(img, v, 'v'))


def recip_body(axis, entry, addr, obj_off=RECIP_OBJ):
    """Replacement for ``fdiv d0, d0, d1`` in _load_texture.

    On entry d0 = 1.0 and d1 = the PHYSICAL extent, exactly as the stock
    instruction expects. It performs that divide, then multiplies by the
    marked axis scale, so the graphics object receives 1/logical while every
    dimension in the game stays physical -- no quad changes size.

    The graphics object is the function's own local at guest [EBP-0x10]; the
    walk to its tex_header is the same one 0x672C95 performs. Every complete
    guest field address is translated separately because neighbouring guest
    pages need not be neighbouring host pages. Everything the translator may
    touch is saved: the real one only clobbers x0/x8/x9/x10, but the emulator
    models the full AAPCS volatile set and has caught live bugs here already.
    """
    if axis not in ('u', 'v'):
        raise ValueError('axis must be u or v')
    done = 55
    mark = tex.SYW_SCALE_MARK
    w = [
        A.stp64_pre(0, 1, A.SP, -0xD0),
        A.stp64_off(2, 3, A.SP, 0x10),
        A.stp64_off(4, 5, A.SP, 0x20),
        A.stp64_off(6, 7, A.SP, 0x30),
        A.stp64_off(8, 9, A.SP, 0x40),
        A.stp64_off(10, 11, A.SP, 0x50),
        A.stp64_off(12, 13, A.SP, 0x60),
        A.stp64_off(14, 15, A.SP, 0x70),
        A.stp64_off(16, 17, A.SP, 0x80),
        A.stp64_off(18, 30, A.SP, 0x90),
        A.stp_q_off(1, 2, A.SP, 0xA0),
        A.mrs_nzcv(17),
        A.str64(17, A.SP, 0xC0),
        A.fdiv_d(0, 0, 1),                       # the displaced instruction
        A.ldr(0, 19, RECIP_EBP),
        A.sub_imm(0, 0, obj_off),
        A.bl(addr(16), TRANSLATE),
        A.cbz64(0, addr(17), addr(done)),
        A.ldr(0, 0),                             # the graphics object
        A.cbz(0, addr(19), addr(done)),
        A.add_imm(0, 0, 0x0C),                   # graphics_object+0x0C
        A.bl(addr(21), TRANSLATE),
        A.cbz64(0, addr(22), addr(done)),
        A.ldr(0, 0),                             # p_hundred
        A.cbz(0, addr(24), addr(done)),
        A.add_imm(0, 0, 0x14),                   # p_hundred+0x14
        A.bl(addr(26), TRANSLATE),
        A.cbz64(0, addr(27), addr(done)),
        A.ldr(0, 0),                             # ff7_texture_set
        A.cbz(0, addr(29), addr(done)),
        A.add_imm(0, 0, 0x84),                   # texture_set+0x84
        A.bl(addr(31), TRANSLATE),
        A.cbz64(0, addr(32), addr(done)),
        A.ldr(0, 0),                             # tex_header
        A.cbz(0, addr(34), addr(done)),
        A.str_(0, A.SP, 0xC8),                   # guest header across calls
        A.add_imm(0, 0, tex.O_USER_MARK),
        A.bl(addr(37), TRANSLATE),
        A.cbz64(0, addr(38), addr(done)),
        A.ldr(8, 0),
        A.movz(9, mark & 0xFFFF),
        A.movk_hi(9, mark >> 16),
        A.cmp_reg(8, 9),
        A.bcond(addr(43), addr(done), A.NE),
        A.ldr(0, A.SP, 0xC8),
        A.add_imm(0, 0, tex.O_USER_SCALE),
        A.bl(addr(46), TRANSLATE),
        A.cbz64(0, addr(47), addr(done)),
        A.ldr(8, 0),
        (A.and_mask(8, 8, 16) if axis == 'u' else A.lsr(8, 8, 16)),
        A.cbz(8, addr(50), addr(done)),
        A.cmp_imm(8, 16),
        A.bcond(addr(52), addr(done), A.HI),
        A.ucvtf_d(1, 8),
        A.fmul_d(0, 0, 1),                       # 1/physical * k = 1/logical
        A.ldr64(17, A.SP, 0xC0),                 # done
        A.msr_nzcv(17),
        A.ldp_q_off(1, 2, A.SP, 0xA0),
        A.ldp64_off(2, 3, A.SP, 0x10),
        A.ldp64_off(4, 5, A.SP, 0x20),
        A.ldp64_off(6, 7, A.SP, 0x30),
        A.ldp64_off(8, 9, A.SP, 0x40),
        A.ldp64_off(10, 11, A.SP, 0x50),
        A.ldp64_off(12, 13, A.SP, 0x60),
        A.ldp64_off(14, 15, A.SP, 0x70),
        A.ldp64_off(16, 17, A.SP, 0x80),
        A.ldp64_off(18, 30, A.SP, 0x90),
        A.ldp64_post(0, 1, A.SP, 0xD0),
        A.ret(),
    ]
    assert len(w) == 69
    return w


def _recip_installed(img, entry, axis, obj_off=RECIP_OBJ):
    if entry is None:
        return False
    got = _walk_helper(img, entry)
    if len(got) != 69:
        return False
    addrs = [va for va, _ in got]
    return [x for _, x in got] == recip_body(
        axis, addrs[0], lambda i: addrs[i], obj_off)


def _recip_v1_installed(img, entry, axis):
    if entry is None:
        return False
    got = _walk_helper(img, entry)
    if len(got) != 56:
        return False
    addrs = [va for va, _ in got]
    want = _recip_body_v1(axis, addrs[0], lambda i: addrs[i])
    return [word for _, word in got] == want


def writer_body(axis, entry, addr):
    """Retired FINDINGS-260 dimension-helper body, for exact migration only.

    This must never be installed by production code.  It stores the logical
    extent where the port expects the physical one and therefore changes both
    UV normalisation and quad geometry.  Keeping the exact encoder lets
    ``state()`` recognise that old output and restore its hook sites to stock.

    On entry ``w20`` is the physical extent, ``x0`` is the host destination,
    and ``[x19, #WRITER_SCRATCH]`` still holds the guest ``tex_header``
    pointer.
    """
    if axis not in ('u', 'v'):
        raise ValueError('axis must be u or v')
    DONE = 27
    mark = tex.SYW_SCALE_MARK
    # NOTHING THE VALUE DEPENDS ON MAY LIVE ACROSS A `bl`. The first draft
    # loaded the fallback into w2 before the walk; the real TRANSLATE only
    # touches x0/x8/x9/x10, but the emulator models the full AAPCS volatile
    # set and caught it -- the unmarked path stored garbage. The fallback is
    # therefore set twice: once for the exit that never reaches the call, and
    # again immediately after it for every path that does.
    #
    # There is deliberately NO unconditional branch in this body.
    # `_walk_helper` follows a `b` as an allocator run-to-run link, so an
    # internal one would make the installed-state readback walk off into the
    # middle of itself and never match.
    w = [
        A.stp64_pre(0, 1, A.SP, -0xA0),         # 0
        A.stp64_off(2, 3, A.SP, 0x10),          # 1
        A.stp64_off(4, 5, A.SP, 0x20),          # 2
        A.stp64_off(6, 7, A.SP, 0x30),          # 3
        A.stp64_off(8, 9, A.SP, 0x40),          # 4
        A.stp64_off(10, 11, A.SP, 0x50),        # 5
        A.stp64_off(12, 13, A.SP, 0x60),        # 6
        A.stp64_off(14, 15, A.SP, 0x70),        # 7
        A.stp64_off(16, 17, A.SP, 0x80),        # 8
        A.stp64_off(18, 30, A.SP, 0x90),        # 9
        A.mov_reg(2, 20),                       # 10 fallback, pre-call exit
        A.ldr(0, 19, WRITER_SCRATCH),           # 11 guest tex_header
        A.cbz(0, addr(12), addr(DONE)),         # 12
        A.bl(addr(13), TRANSLATE),              # 13
        A.mov_reg(2, 20),                       # 14 fallback, re-established
        A.cbz64(0, addr(15), addr(DONE)),       # 15
        A.ldr(8, 0, tex.O_USER_MARK),           # 16
        A.movz(9, mark & 0xFFFF),               # 17
        A.movk_hi(9, mark >> 16),               # 18
        A.cmp_reg(8, 9),                        # 19
        A.bcond(addr(20), addr(DONE), A.NE),    # 20
        A.ldr(8, 0, tex.O_USER_SCALE),          # 21
        (A.and_mask(8, 8, 16) if axis == 'u'    # 22
         else A.lsr(8, 8, 16)),
        A.cbz(8, addr(23), addr(DONE)),         # 23 a zero scale never divides
        A.cmp_imm(8, 16),                       # 24
        A.bcond(addr(25), addr(DONE), A.HI),    # 25 nor an absurd one
        A.udiv(2, 20, 8),                       # 26 logical = physical / k
        A.ldr64(0, A.SP, 0),                    # 27 DONE: saved destination
        A.str_(2, 0),                           # 28 the displaced store
        A.ldp64_off(2, 3, A.SP, 0x10),          # 29
        A.ldp64_off(4, 5, A.SP, 0x20),          # 30
        A.ldp64_off(6, 7, A.SP, 0x30),          # 31
        A.ldp64_off(8, 9, A.SP, 0x40),          # 32
        A.ldp64_off(10, 11, A.SP, 0x50),        # 33
        A.ldp64_off(12, 13, A.SP, 0x60),        # 34
        A.ldp64_off(14, 15, A.SP, 0x70),        # 35
        A.ldp64_off(16, 17, A.SP, 0x80),        # 36
        A.ldp64_off(18, 30, A.SP, 0x90),        # 37
        A.ldp64_post(0, 1, A.SP, 0xA0),         # 38
        A.ret(),                                # 39
    ]
    assert len(w) == 40
    return w


def helper_body(axis, entry, addr):
    """Shared draw-time replacement for ``ldr s0,[x0]``.

    ``x0`` is the host pointer to drawable+0x24 (U) or +0x28 (V). Every
    caller-visible integer register, q1/q2, and NZCV is preserved around the
    three guest-pointer translations. q0 intentionally receives the same
    scalar load as stock, multiplied by the marked TEX's axis factor.
    """
    if axis not in ('u', 'v'):
        raise ValueError('axis must be u or v')
    finish = 37
    field = 0x24 if axis == 'u' else 0x28
    mark = tex.SYW_SCALE_MARK
    w = [
        A.stp64_pre(0, 1, A.SP, -0xD0),
        A.stp64_off(2, 3, A.SP, 0x10),
        A.stp64_off(4, 5, A.SP, 0x20),
        A.stp64_off(6, 7, A.SP, 0x30),
        A.stp64_off(8, 9, A.SP, 0x40),
        A.stp64_off(10, 11, A.SP, 0x50),
        A.stp64_off(12, 13, A.SP, 0x60),
        A.stp64_off(14, 15, A.SP, 0x70),
        A.stp64_off(16, 17, A.SP, 0x80),
        A.stp64_off(18, 30, A.SP, 0x90),
        A.stp_q_off(1, 2, A.SP, 0xA0),
        A.mrs_nzcv(17),
        A.str64(17, A.SP, 0xC0),
        A.movz(17, 1),
        A.str_(17, A.SP, 0xC8),
        A.ldr64(18, A.SP, 0),
        A.sub_imm64(18, 18, field),
        A.ldr(0, 18, 0x0C),
        A.cbz(0, addr(18), addr(finish)),
        A.bl(addr(19), TRANSLATE),
        A.ldr(0, 0, 0x14),
        A.cbz(0, addr(21), addr(finish)),
        A.bl(addr(22), TRANSLATE),
        A.ldr(0, 0, 0x84),
        A.cbz(0, addr(24), addr(finish)),
        A.bl(addr(25), TRANSLATE),
        A.ldr(8, 0, tex.O_USER_MARK),
        A.movz(9, mark & 0xFFFF),
        A.movk_hi(9, mark >> 16),
        A.eor_reg(8, 8, 9),
        A.cbnz(8, addr(30), addr(finish)),
        A.ldr(8, 0, tex.O_USER_SCALE),
        (A.and_mask(8, 8, 16) if axis == 'u' else A.lsr(8, 8, 16)),
        A.cbz(8, addr(33), addr(finish)),
        A.cmp_imm(8, 16),
        A.bcond(addr(35), addr(finish), A.HI),
        A.str_(8, A.SP, 0xC8),
        A.ldr(17, A.SP, 0xC8),
        A.ldr64(0, A.SP, 0),
        A.ldr_s(0, 0),
        A.ucvtf_s(1, 17),
        A.fmul_s(0, 0, 1),
        A.ldr64(17, A.SP, 0xC0),
        A.msr_nzcv(17),
        A.ldp_q_off(1, 2, A.SP, 0xA0),
        A.ldp64_off(18, 30, A.SP, 0x90),
        A.ldp64_off(16, 17, A.SP, 0x80),
        A.ldp64_off(14, 15, A.SP, 0x70),
        A.ldp64_off(12, 13, A.SP, 0x60),
        A.ldp64_off(10, 11, A.SP, 0x50),
        A.ldp64_off(8, 9, A.SP, 0x40),
        A.ldp64_off(6, 7, A.SP, 0x30),
        A.ldp64_off(4, 5, A.SP, 0x20),
        A.ldp64_off(2, 3, A.SP, 0x10),
        A.ldp64_post(0, 1, A.SP, 0xD0),
        A.ret(),
    ]
    assert len(w) == 56
    return w


def _walk_helper(img, entry):
    """Return logical helper words, skipping allocator link branches."""
    va, out, seen = entry, [], set()
    while len(out) < 128 and va not in seen:
        seen.add(va)
        word = _word(img, va)
        target = _b_target(word, va)
        if target is not None:                 # allocator run-to-run link
            va = target
            continue
        out.append((va, word))
        if word == A.ret():
            return out
        va += 4
    return []


def _writer_installed(img, entry, axis):
    """Recognise the retired dimension-writer cave exactly for migration."""
    if entry is None:
        return False
    got = _walk_helper(img, entry)
    if len(got) != 40:
        return False
    addrs = [va for va, _ in got]
    want = writer_body(axis, addrs[0], lambda i: addrs[i])
    return [word for _, word in got] == want


def _helper_installed(img, entry, axis):
    got = _walk_helper(img, entry)
    if len(got) != 56:
        return False
    addrs = [va for va, _ in got]
    want = helper_body(axis, addrs[0], lambda i: addrs[i])
    return [word for _, word in got] == want


def reciprocal_installed(img):
    """The ineffective build-252 six-site shape, accepted for migration."""
    ut = {_bl_target(_word(img, va), va) for va in RECIP_U_SITES}
    vt = {_bl_target(_word(img, va), va) for va in RECIP_V_SITES}
    if None in ut or None in vt or len(ut) != 1 or len(vt) != 1:
        return False
    return (_recip_installed(img, ut.pop(), 'u')
            and _recip_installed(img, vt.pop(), 'v'))


def installed(img):
    """True when both dimension stores reach a well-formed path-3 cave."""
    return path3_installed(img)


def dimstore_installed(img):
    u = _bl_target(_word(img, DIMSTORE_U_SITE), DIMSTORE_U_SITE)
    v = _bl_target(_word(img, DIMSTORE_V_SITE), DIMSTORE_V_SITE)
    return (_dimstore_installed(img, u, 'u')
            and _dimstore_installed(img, v, 'v'))


def recip_body(axis, entry, addr, obj_off=RECIP_OBJ):
    """Replacement for ``fdiv d0, d0, d1`` in _load_texture.

    On entry d0 = 1.0 and d1 = the PHYSICAL extent, exactly as the stock
    instruction expects. It performs that divide, then multiplies by the
    marked axis scale, so the graphics object receives 1/logical while every
    dimension in the game stays physical -- no quad changes size.

    The graphics object is the function's own local at guest [EBP-0x10]; the
    walk to its tex_header is the same one 0x672C95 performs. Every complete
    guest field address is translated separately because neighbouring guest
    pages need not be neighbouring host pages. Everything the translator may
    touch is saved: the real one only clobbers x0/x8/x9/x10, but the emulator
    models the full AAPCS volatile set and has caught live bugs here already.
    """
    if axis not in ('u', 'v'):
        raise ValueError('axis must be u or v')
    done = 55
    mark = tex.SYW_SCALE_MARK
    w = [
        A.stp64_pre(0, 1, A.SP, -0xD0),
        A.stp64_off(2, 3, A.SP, 0x10),
        A.stp64_off(4, 5, A.SP, 0x20),
        A.stp64_off(6, 7, A.SP, 0x30),
        A.stp64_off(8, 9, A.SP, 0x40),
        A.stp64_off(10, 11, A.SP, 0x50),
        A.stp64_off(12, 13, A.SP, 0x60),
        A.stp64_off(14, 15, A.SP, 0x70),
        A.stp64_off(16, 17, A.SP, 0x80),
        A.stp64_off(18, 30, A.SP, 0x90),
        A.stp_q_off(1, 2, A.SP, 0xA0),
        A.mrs_nzcv(17),
        A.str64(17, A.SP, 0xC0),
        A.fdiv_d(0, 0, 1),                       # the displaced instruction
        A.ldr(0, 19, RECIP_EBP),
        A.sub_imm(0, 0, obj_off),
        A.bl(addr(16), TRANSLATE),
        A.cbz64(0, addr(17), addr(done)),
        A.ldr(0, 0),                             # the graphics object
        A.cbz(0, addr(19), addr(done)),
        A.add_imm(0, 0, 0x0C),                   # graphics_object+0x0C
        A.bl(addr(21), TRANSLATE),
        A.cbz64(0, addr(22), addr(done)),
        A.ldr(0, 0),                             # p_hundred
        A.cbz(0, addr(24), addr(done)),
        A.add_imm(0, 0, 0x14),                   # p_hundred+0x14
        A.bl(addr(26), TRANSLATE),
        A.cbz64(0, addr(27), addr(done)),
        A.ldr(0, 0),                             # ff7_texture_set
        A.cbz(0, addr(29), addr(done)),
        A.add_imm(0, 0, 0x84),                   # texture_set+0x84
        A.bl(addr(31), TRANSLATE),
        A.cbz64(0, addr(32), addr(done)),
        A.ldr(0, 0),                             # tex_header
        A.cbz(0, addr(34), addr(done)),
        A.str_(0, A.SP, 0xC8),                   # guest header across calls
        A.add_imm(0, 0, tex.O_USER_MARK),
        A.bl(addr(37), TRANSLATE),
        A.cbz64(0, addr(38), addr(done)),
        A.ldr(8, 0),
        A.movz(9, mark & 0xFFFF),
        A.movk_hi(9, mark >> 16),
        A.cmp_reg(8, 9),
        A.bcond(addr(43), addr(done), A.NE),
        A.ldr(0, A.SP, 0xC8),
        A.add_imm(0, 0, tex.O_USER_SCALE),
        A.bl(addr(46), TRANSLATE),
        A.cbz64(0, addr(47), addr(done)),
        A.ldr(8, 0),
        (A.and_mask(8, 8, 16) if axis == 'u' else A.lsr(8, 8, 16)),
        A.cbz(8, addr(50), addr(done)),
        A.cmp_imm(8, 16),
        A.bcond(addr(52), addr(done), A.HI),
        A.ucvtf_d(1, 8),
        A.fmul_d(0, 0, 1),                       # 1/physical * k = 1/logical
        A.ldr64(17, A.SP, 0xC0),                 # done
        A.msr_nzcv(17),
        A.ldp_q_off(1, 2, A.SP, 0xA0),
        A.ldp64_off(2, 3, A.SP, 0x10),
        A.ldp64_off(4, 5, A.SP, 0x20),
        A.ldp64_off(6, 7, A.SP, 0x30),
        A.ldp64_off(8, 9, A.SP, 0x40),
        A.ldp64_off(10, 11, A.SP, 0x50),
        A.ldp64_off(12, 13, A.SP, 0x60),
        A.ldp64_off(14, 15, A.SP, 0x70),
        A.ldp64_off(16, 17, A.SP, 0x80),
        A.ldp64_off(18, 30, A.SP, 0x90),
        A.ldp64_post(0, 1, A.SP, 0xD0),
        A.ret(),
    ]
    assert len(w) == 69
    return w


def _recip_installed(img, entry, axis, obj_off=RECIP_OBJ):
    if entry is None:
        return False
    got = _walk_helper(img, entry)
    if len(got) != 69:
        return False
    addrs = [va for va, _ in got]
    return [x for _, x in got] == recip_body(
        axis, addrs[0], lambda i: addrs[i], obj_off)


def _recip_v1_installed(img, entry, axis):
    if entry is None:
        return False
    got = _walk_helper(img, entry)
    if len(got) != 56:
        return False
    addrs = [va for va, _ in got]
    want = _recip_body_v1(axis, addrs[0], lambda i: addrs[i])
    return [word for _, word in got] == want


def writer_body(axis, entry, addr):
    """Retired FINDINGS-260 dimension-helper body, for exact migration only.

    This must never be installed by production code.  It stores the logical
    extent where the port expects the physical one and therefore changes both
    UV normalisation and quad geometry.  Keeping the exact encoder lets
    ``state()`` recognise that old output and restore its hook sites to stock.

    On entry ``w20`` is the physical extent, ``x0`` is the host destination,
    and ``[x19, #WRITER_SCRATCH]`` still holds the guest ``tex_header``
    pointer.
    """
    if axis not in ('u', 'v'):
        raise ValueError('axis must be u or v')
    DONE = 27
    mark = tex.SYW_SCALE_MARK
    # NOTHING THE VALUE DEPENDS ON MAY LIVE ACROSS A `bl`. The first draft
    # loaded the fallback into w2 before the walk; the real TRANSLATE only
    # touches x0/x8/x9/x10, but the emulator models the full AAPCS volatile
    # set and caught it -- the unmarked path stored garbage. The fallback is
    # therefore set twice: once for the exit that never reaches the call, and
    # again immediately after it for every path that does.
    #
    # There is deliberately NO unconditional branch in this body.
    # `_walk_helper` follows a `b` as an allocator run-to-run link, so an
    # internal one would make the installed-state readback walk off into the
    # middle of itself and never match.
    w = [
        A.stp64_pre(0, 1, A.SP, -0xA0),         # 0
        A.stp64_off(2, 3, A.SP, 0x10),          # 1
        A.stp64_off(4, 5, A.SP, 0x20),          # 2
        A.stp64_off(6, 7, A.SP, 0x30),          # 3
        A.stp64_off(8, 9, A.SP, 0x40),          # 4
        A.stp64_off(10, 11, A.SP, 0x50),        # 5
        A.stp64_off(12, 13, A.SP, 0x60),        # 6
        A.stp64_off(14, 15, A.SP, 0x70),        # 7
        A.stp64_off(16, 17, A.SP, 0x80),        # 8
        A.stp64_off(18, 30, A.SP, 0x90),        # 9
        A.mov_reg(2, 20),                       # 10 fallback, pre-call exit
        A.ldr(0, 19, WRITER_SCRATCH),           # 11 guest tex_header
        A.cbz(0, addr(12), addr(DONE)),         # 12
        A.bl(addr(13), TRANSLATE),              # 13
        A.mov_reg(2, 20),                       # 14 fallback, re-established
        A.cbz64(0, addr(15), addr(DONE)),       # 15
        A.ldr(8, 0, tex.O_USER_MARK),           # 16
        A.movz(9, mark & 0xFFFF),               # 17
        A.movk_hi(9, mark >> 16),               # 18
        A.cmp_reg(8, 9),                        # 19
        A.bcond(addr(20), addr(DONE), A.NE),    # 20
        A.ldr(8, 0, tex.O_USER_SCALE),          # 21
        (A.and_mask(8, 8, 16) if axis == 'u'    # 22
         else A.lsr(8, 8, 16)),
        A.cbz(8, addr(23), addr(DONE)),         # 23 a zero scale never divides
        A.cmp_imm(8, 16),                       # 24
        A.bcond(addr(25), addr(DONE), A.HI),    # 25 nor an absurd one
        A.udiv(2, 20, 8),                       # 26 logical = physical / k
        A.ldr64(0, A.SP, 0),                    # 27 DONE: saved destination
        A.str_(2, 0),                           # 28 the displaced store
        A.ldp64_off(2, 3, A.SP, 0x10),          # 29
        A.ldp64_off(4, 5, A.SP, 0x20),          # 30
        A.ldp64_off(6, 7, A.SP, 0x30),          # 31
        A.ldp64_off(8, 9, A.SP, 0x40),          # 32
        A.ldp64_off(10, 11, A.SP, 0x50),        # 33
        A.ldp64_off(12, 13, A.SP, 0x60),        # 34
        A.ldp64_off(14, 15, A.SP, 0x70),        # 35
        A.ldp64_off(16, 17, A.SP, 0x80),        # 36
        A.ldp64_off(18, 30, A.SP, 0x90),        # 37
        A.ldp64_post(0, 1, A.SP, 0xA0),         # 38
        A.ret(),                                # 39
    ]
    assert len(w) == 40
    return w


def helper_body(axis, entry, addr):
    """Shared draw-time replacement for ``ldr s0,[x0]``.

    ``x0`` is the host pointer to drawable+0x24 (U) or +0x28 (V). Every
    caller-visible integer register, q1/q2, and NZCV is preserved around the
    three guest-pointer translations. q0 intentionally receives the same
    scalar load as stock, multiplied by the marked TEX's axis factor.
    """
    if axis not in ('u', 'v'):
        raise ValueError('axis must be u or v')
    finish = 37
    field = 0x24 if axis == 'u' else 0x28
    mark = tex.SYW_SCALE_MARK
    w = [
        A.stp64_pre(0, 1, A.SP, -0xD0),
        A.stp64_off(2, 3, A.SP, 0x10),
        A.stp64_off(4, 5, A.SP, 0x20),
        A.stp64_off(6, 7, A.SP, 0x30),
        A.stp64_off(8, 9, A.SP, 0x40),
        A.stp64_off(10, 11, A.SP, 0x50),
        A.stp64_off(12, 13, A.SP, 0x60),
        A.stp64_off(14, 15, A.SP, 0x70),
        A.stp64_off(16, 17, A.SP, 0x80),
        A.stp64_off(18, 30, A.SP, 0x90),
        A.stp_q_off(1, 2, A.SP, 0xA0),
        A.mrs_nzcv(17),
        A.str64(17, A.SP, 0xC0),
        A.movz(17, 1),
        A.str_(17, A.SP, 0xC8),
        A.ldr64(18, A.SP, 0),
        A.sub_imm64(18, 18, field),
        A.ldr(0, 18, 0x0C),
        A.cbz(0, addr(18), addr(finish)),
        A.bl(addr(19), TRANSLATE),
        A.ldr(0, 0, 0x14),
        A.cbz(0, addr(21), addr(finish)),
        A.bl(addr(22), TRANSLATE),
        A.ldr(0, 0, 0x84),
        A.cbz(0, addr(24), addr(finish)),
        A.bl(addr(25), TRANSLATE),
        A.ldr(8, 0, tex.O_USER_MARK),
        A.movz(9, mark & 0xFFFF),
        A.movk_hi(9, mark >> 16),
        A.eor_reg(8, 8, 9),
        A.cbnz(8, addr(30), addr(finish)),
        A.ldr(8, 0, tex.O_USER_SCALE),
        (A.and_mask(8, 8, 16) if axis == 'u' else A.lsr(8, 8, 16)),
        A.cbz(8, addr(33), addr(finish)),
        A.cmp_imm(8, 16),
        A.bcond(addr(35), addr(finish), A.HI),
        A.str_(8, A.SP, 0xC8),
        A.ldr(17, A.SP, 0xC8),
        A.ldr64(0, A.SP, 0),
        A.ldr_s(0, 0),
        A.ucvtf_s(1, 17),
        A.fmul_s(0, 0, 1),
        A.ldr64(17, A.SP, 0xC0),
        A.msr_nzcv(17),
        A.ldp_q_off(1, 2, A.SP, 0xA0),
        A.ldp64_off(18, 30, A.SP, 0x90),
        A.ldp64_off(16, 17, A.SP, 0x80),
        A.ldp64_off(14, 15, A.SP, 0x70),
        A.ldp64_off(12, 13, A.SP, 0x60),
        A.ldp64_off(10, 11, A.SP, 0x50),
        A.ldp64_off(8, 9, A.SP, 0x40),
        A.ldp64_off(6, 7, A.SP, 0x30),
        A.ldp64_off(4, 5, A.SP, 0x20),
        A.ldp64_off(2, 3, A.SP, 0x10),
        A.ldp64_post(0, 1, A.SP, 0xD0),
        A.ret(),
    ]
    assert len(w) == 56
    return w


def _walk_helper(img, entry):
    """Return logical helper words, skipping allocator link branches."""
    va, out, seen = entry, [], set()
    while len(out) < 128 and va not in seen:
        seen.add(va)
        word = _word(img, va)
        target = _b_target(word, va)
        if target is not None:                 # allocator run-to-run link
            va = target
            continue
        out.append((va, word))
        if word == A.ret():
            return out
        va += 4
    return []


def _writer_installed(img, entry, axis):
    """Recognise the retired dimension-writer cave exactly for migration."""
    if entry is None:
        return False
    got = _walk_helper(img, entry)
    if len(got) != 40:
        return False
    addrs = [va for va, _ in got]
    want = writer_body(axis, addrs[0], lambda i: addrs[i])
    return [word for _, word in got] == want


def _helper_installed(img, entry, axis):
    got = _walk_helper(img, entry)
    if len(got) != 56:
        return False
    addrs = [va for va, _ in got]
    want = helper_body(axis, addrs[0], lambda i: addrs[i])
    return [word for _, word in got] == want


def reciprocal_installed(img):
    """The ineffective build-252 six-site shape, accepted for migration."""
    ut = {_bl_target(_word(img, va), va) for va in RECIP_U_SITES}
    vt = {_bl_target(_word(img, va), va) for va in RECIP_V_SITES}
    if None in ut or None in vt or len(ut) != 1 or len(vt) != 1:
        return False
    return (_recip_installed(img, ut.pop(), 'u')
            and _recip_installed(img, vt.pop(), 'v'))


def tail_hook_installed(img):
    """RETIRED build-253 shape, kept only so it can be recognised."""
    return _logical_installed(img)


def reciprocal_v1_installed(img):
    """The pre-NZCV reciprocal shape, accepted only so it can be upgraded."""
    ut = {_bl_target(_word(img, va), va) for va in RECIP_U_SITES}
    vt = {_bl_target(_word(img, va), va) for va in RECIP_V_SITES}
    if None in ut or None in vt or len(ut) != 1 or len(vt) != 1:
        return False
    return (_recip_v1_installed(img, ut.pop(), 'u')
            and _recip_v1_installed(img, vt.pop(), 'v'))


def dimension_installed(img):
    """The retired FINDINGS-260 shape: the shared dimension helper."""
    t = [_bl_target(_word(img, va), va) for va in WRITER_SITES]
    if None in t:
        return False
    return (_writer_installed(img, t[0], 'u')
            and _writer_installed(img, t[1], 'v'))


def draw_reads_installed(img):
    """The retired build-251 shape: six diverted reads in one consumer."""
    ut = [_bl_target(_word(img, va), va) for va in SPT_U_SITES]
    vt = [_bl_target(_word(img, va), va) for va in SPT_V_SITES]
    return (None not in ut and None not in vt and len(set(ut)) == 1
            and len(set(vt)) == 1
            and _helper_installed(img, ut[0], 'u')
            and _helper_installed(img, vt[0], 'v'))


def sprite_recip_installed(img):
    """True when 0x672E29's two descriptor reciprocals reach a valid cave."""
    ut = {_bl_target(_word(img, va), va) for va in RECIP2_U_SITES}
    vt = {_bl_target(_word(img, va), va) for va in RECIP2_V_SITES}
    if None in ut or None in vt or len(ut) != 1 or len(vt) != 1:
        return False
    return (_recip_installed(img, ut.pop(), 'u', RECIP2_OBJ)
            and _recip_installed(img, vt.pop(), 'v', RECIP2_OBJ))


def state(img):
    if installed(img):
        return 'logical'
    if any(_word(img, va) != WRITER_STOCK for va in WRITER_SITES):
        # some earlier experiment sits on the dimension stores
        if dimension_installed(img):
            return 'legacy-dimension'
        return 'unknown'
    if dimstore_installed(img):
        return 'legacy-sprite-dim'
    if sprite_recip_installed(img):
        return 'legacy-sprite-dim'
    if reciprocal_installed(img):
        return 'legacy-recip-v2'
    if reciprocal_v1_installed(img):
        return 'legacy-recip-v1'
    if any(_word(img, va) != RECIP_STOCK for va in RECIP_SITES + RECIP2_SITES):
        return 'unknown'
    if any(_word(img, va) != DIMSTORE_STOCK for va in DIMSTORE_SITES):
        return 'unknown'
    if draw_reads_installed(img):
        return 'legacy-draw-reads'
    if not all(_word(img, va) == SPT_STOCK for va in SPT_SITES):
        return 'unknown'
    if _word(img, HOOK) == HOOK_STOCK:
        return 'stock'
    if _logical_installed(img):
        return 'legacy-tail-only'
    if _loader_live_installed(img):
        return 'legacy-loader-live'
    return 'legacy-struc3' if legacy_installed(img) else 'unknown'


def verify(module):
    bad = []
    img = module.img
    for va, want in ANCHORS.items():
        got = _word(img, va)
        if got != want:
            bad.append('+0x%X is %08X, expected %08X' % (va, got, want))
    if module.x86_to_arm.get(X86_ENTRY) != 0xA9E240:
        bad.append('guest _load_texture does not map to +0xA9E240')
    if module.x86_to_arm.get(SPT_X86_ENTRY) != SPT_ARM_ENTRY:
        bad.append('guest battle_animate_texture_spt does not map to '
                   '+0x%X' % SPT_ARM_ENTRY)
    if state(img) == 'unknown':
        bad.append('the hook carries an unrecognised branch/cave')
    return bad


def build_patches(img, starts=None):
    """Correct the path-3 dimension, and nothing else. FINDINGS-266.

    Every other shape this module has shipped is retired here, because two of
    them at once would compound. The full history is in the notes above; the
    short version is that the correction has to be per-CALL-SITE, and which
    call site was measured on hardware rather than reasoned about.
    """
    current = state(img)
    if current == 'logical':
        return {}
    if current not in ('stock', 'legacy-loader-live', 'legacy-struc3',
                       'legacy-recip-v1', 'legacy-recip-v2', 'legacy-tail-only',
                       'legacy-draw-reads', 'legacy-dimension',
                       'legacy-sprite-dim'):
        raise ValueError('unrecognised spell UV hook')
    pool = ff7nx_cave.HolePool(img, starts=starts)
    u_entry, out = ff7nx_cave.emit_laid_out(
        pool, lambda e, ad: path3_dim_body('u', e, ad), span=0x80000)
    v_entry, vout = ff7nx_cave.emit_laid_out(
        pool, lambda e, ad: path3_dim_body('v', e, ad), span=0x80000)
    out.update(vout)
    out[WRITER_U_SITE] = A.bl(WRITER_U_SITE, u_entry)
    out[WRITER_V_SITE] = A.bl(WRITER_V_SITE, v_entry)

    # Retire everything else, by SHAPE rather than by headline state, so an
    # interrupted build carrying two old experiments is cleaned as well.
    if _word(img, HOOK) != HOOK_STOCK:
        out[HOOK] = HOOK_STOCK
    for va in RECIP_SITES + RECIP2_SITES:
        if _word(img, va) != RECIP_STOCK:
            out[va] = RECIP_STOCK
    for va in DIMSTORE_SITES:
        if _word(img, va) != DIMSTORE_STOCK:
            out[va] = DIMSTORE_STOCK
    for va in SPT_SITES:
        if _word(img, va) != SPT_STOCK:
            out[va] = SPT_STOCK
    return out


def hx(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def spec(module):
    if state(module.img) == 'logical':
        return None
    patches = build_patches(module.img, set(module.arm_starts))
    return {'name': 'spell TEX physical size -> logical texel step',
            'patches': [{'name': '+0x%X' % va, 'va': hex(va),
                         'expect': hx(_word(module.img, va)), 'set': hx(word)}
                        for va, word in sorted(patches.items())]}


def apply_to_nso(src, dest, log=lambda *_: None):
    """Patch ``src`` into ``dest``. Return False if already installed/error."""
    from pathlib import Path
    import nxmap
    import nso_patcher

    module = nxmap.Main(str(src))
    bad = verify(module)
    if bad:
        log('! spell logical UV: module does not match this port; skipped')
        for item in bad:
            log('    ' + item)
        return False
    patch_spec = spec(module)
    if patch_spec is None:
        log('  spell logical UV: already installed; nothing to write')
        return False
    try:
        nso = nso_patcher.read_nso(Path(str(src)))
        nso_patcher.apply_spec(nso, patch_spec)
        data = nso_patcher.rebuild(nso)
    except Exception as exc:                                      # noqa: BLE001
        log('! spell logical UV: %s' % exc)
        return False
    os.makedirs(os.path.dirname(os.path.abspath(str(dest))), exist_ok=True)
    with open(dest, 'wb') as fh:
        fh.write(data)
    log('  spell logical UV: _load_texture now corrects the completed object '
        'once at its common join, using the direct struc_3 TEX header and '
        'per-axis scale (1x..16x): step = scale / physical_size. Texture '
        'dimensions, .s tables and on-screen quad extents remain stock. '
        'Build 252\'s six half-built-object hooks were retired.')
    return True


def corrected_steps(width, height, marker):
    """Pure model used by tests: resulting (u_offset, v_offset)."""
    sx, sy = marker if marker else (1, 1)
    if not (1 <= sx <= 16 and 1 <= sy <= 16):
        sx = sy = 1
    return sx / float(width), sy / float(height)
