#!/usr/bin/env python3
r"""
ff7nx_minifade.py -- the minigame fades to black are 4:3 wide.

    python3 ff7nx_minifade.py <exefs/main | sdout>            (verify)
    python3 ff7nx_minifade.py <exefs/main | sdout> --apply
    python3 ff7nx_minifade.py <exefs/main | sdout> --revert

Two groups, FIVE WORDS in all.  No cave, no relocation, no branch, no guest
data.  apply -> revert is byte-identical.  Both are FFNx widescreen.cpp
fixes, by name, and both use FFNx's wide_viewport_x / wide_viewport_width
(-107 / 854) -- the values every other full-frame fade in this tree ships.

GATE: the same as ff7nx_battlewide -- ON at 16:9, OFF at 4:3, where these
values would simply be wrong.


GROUP 1 -- CHOCOBO RACE (build 538)
===================================
`chocobo_submit_draw_fade_quad` (x86 0x77B1CE) has ONE caller (0x77949D),
which always passes the fade record at 0x97A498, and submits

    x = [0xE3BA84] + obj.x (+0x14, u16) * [0xE3BA80]
    w = obj.w (+0x28) * [0xE3BA80]          obj.w = 320, scale 2 -> 640

FFNx:
    patch_code_dword(chocobo_submit_draw_fade_quad_77B1CE + 0x99, &wide_viewport_x);
    patch_code_int(chocobo_fade_quad_data_97A498 + 0x28, wide_viewport_width / 2);

The recompiler keeps x86 registers in a context struct (x20 there) and routes
every guest access through the translator at +0x10FC3A0:

    +0xFEF9B8  ldr w8, [x0]      obj.w (320)   -> movz w8, #427
               (+0xFEF9BC mov w0, w19: x0 dead after the load)
    +0xFEF9B4  ...
    +0xFEFAB4  ldr w9, [x0]      [0xE3BA84]    -> movn w9, #106  (-107)
               (+0xFEFAB8 add w19, w9, w8: its only consumer, 32-bit)


GROUP 2 -- HIGHWAY (build 539)
==============================
`highway_submit_fade_quad` (x86 0x659532) pushes a hardcoded rect straight
into generic_submit_quad_graphics_object_671D2A (cdecl, 8 stack args, caller
cleans up with `add esp, 0x20`):

    +0x55 mov ecx, [ebp-4] ; +0x58 push ecx      colour
    +0x59 push 0x1E0                             h 480
    +0x5E push 0x280                             w 640
    +0x63 push 0                                 y
    +0x65 push 0                                 x

FFNx:
    patch_code_int (highway_submit_fade_quad_659532 + 0x5F, wide_viewport_width);
    patch_code_char(highway_submit_fade_quad_659532 + 0x66, wide_viewport_x);

Translated body +0xA3EEC0, four pushes in source order:

    +0xA3F0B4  str w20, [x19, #4]   the `mov ecx` shadow -> movn w11, #106
    +0xA3F0C0  str w20, [x0]        the push itself (untouched)
    +0xA3F0EC  mov w8, #0x280       w                  -> mov  w8,  #0x356
    +0xA3F118  str wzr, [x0]        x = 0              -> str  w11, [x0]

`w` is FFNx's one-word immediate.  `x` was strength-reduced to `str wzr`, so
it needs -107 in a register, and there is one for free:

  * the ECX SHADOW STORE IS DEAD.  The x86 pushes ecx and calls a cdecl
    function; ecx is not an argument, and it is next WRITTEN at +0x94
    (`mov ecx, [ebp-8]`) before anything reads it.  The push at +0xA3F0C0
    reads w20, not the shadow, so the colour is untouched;
  * W11 SURVIVES TO THE STORE.  Between +0xA3F0B4 and +0xA3F118 the only
    calls are five `bl +0x10FC3A0`, the translator, whose whole body
    (+0x10FC3A0..+0x10FC3D4) writes x0, x8, x9, x10 and NZCV and nothing
    else; no instruction in the span names w11.

build 204 once put this x fix in a CAVE, attributed to a battle fade;
ff7nx_battlewide still removes that build-204 form on every build and now
recognises this one as owned here (see its engine_fade_plan).
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nso_patcher                                            # noqa: E402

MINIFADE_ENV = 'SEVENTH_NX_MINIGAME_FADE'

WIDE_X = -107
WIDE_W = 854
TRANSLATE = 0x10FC3A0


def _movn_w(rd, value):
    return 0x12800000 | (((-value) - 1) << 5) | rd


def _movz_w(rd, value):
    return 0x52800000 | (value << 5) | rd


HIGHWAY_X_REG = 11
HIGHWAY_X_STORE = 0xB9000000 | HIGHWAY_X_REG      # str w11, [x0]

# group -> (x86 fn, ARM fn, [(name, va, stock, new, [(va, word|None, what)])])
GROUPS = {
    'chocobo race fade': (0x77B1CE, 0xFEF750, [
        ('width', 0xFEF9B8, 0xB9400008, _movz_w(8, WIDE_W // 2),
         [(0xFEF9AC, 0x1100A100, 'add w0, w8, #0x28   obj.w'),
          (0xFEF9B4, None, 'bl translate'),
          (0xFEF9BC, 0x2A1303E0, 'mov w0, w19   x0 dead after the load'),
          (0xFEF964, 0x52975013, 'mov w19, #0xba80'),
          (0xFEF968, 0x72A01C73, 'movk w19, #0xe3, lsl #16   scale global')]),
        ('x', 0xFEFAB4, 0xB9400009, _movn_w(9, WIDE_X),
         [(0xFEFAA4, 0x11001260, 'add w0, w19, #4   0xE3BA84'),
          (0xFEFAAC, None, 'bl translate'),
          (0xFEFAB8, 0x0B080133, 'add w19, w9, w8   the only consumer'),
          (0xFEFA7C, 0x11005100, 'add w0, w8, #0x14   obj.x')]),
    ]),
    'highway fade': (0x659532, 0xA3EEC0, [
        ('x value', 0xA3F0B4, 0xB9000674, _movn_w(HIGHWAY_X_REG, WIDE_X),
         [(0xA3F0B0, 0x51001100, 'sub w0, w8, #4'),
          (0xA3F0B8, 0xB9001260, 'str w0, [x19, #0x10]'),
          (0xA3F0BC, None, 'bl translate'),
          (0xA3F0C0, 0xB9000014, 'str w20, [x0]   the colour push reads w20')]),
        ('width', 0xA3F0EC, 0x52805008, _movz_w(8, WIDE_W),
         [(0xA3F0D0, None, 'bl translate'),
          (0xA3F0D4, 0x321B0FE8, 'mov w8, #0x1e0   h = 480'),
          (0xA3F0D8, 0xB9000008, 'str w8, [x0]'),
          (0xA3F0E8, None, 'bl translate'),
          (0xA3F0F0, 0xB9000008, 'str w8, [x0]   the width push')]),
        ('x store', 0xA3F118, 0xB900001F, HIGHWAY_X_STORE,
         [(0xA3F100, None, 'bl translate'),
          (0xA3F104, 0xB900001F, 'str wzr, [x0]   y = 0'),
          (0xA3F114, None, 'bl translate'),
          (0xA3F11C, 0xB9401268, 'ldr w8, [x19, #0x10]'),
          (0xA3F128, 0x9402D582, 'bl +0xAF4730   the quad draw')]),
    ]),
}

# ---------------------------------------------------------------------------
# BUILD 540 -- SNOWBOARD. FFNx widescreen.cpp "// Snowboard fix", all ten of
# its code words (the eleventh is the .rdata float snowboard_sky_quad_pos_x,
# which lives in ff7_en -- see EXE_PATCHES).
#
#   patch_code_int (sky_and_mountains_72DAF0 + 0xCC,  ceil(854/4))   160->214
#   patch_code_int (sky_and_mountains_72DAF0 + 0x140, ceil(854/4))   160->214
#   patch_code_float(snowboard_sky_quad_pos_x_7B7DB8, -107)          ff7_en
#   patch_code_int (submit_draw_sky_quad_72E31F + 0x173, 854 - 107)  640->747
#   patch_code_int (submit_draw_sky_quad_72E31F + 0x225, 854 - 107)  640->747
#   black / white fade / opaque quad (72DD94 / 72DD53 / 72DDD5):
#       width push 640 -> 854, x push 0 -> -107
#
# The three quad submitters are the same eleven-line function with a
# different colour, and translate identically to the highway one: the x push
# is `str wzr, [x0]`, so x comes from w11, loaded in the slot of the dead eax
# shadow store of `mov al, [ebp+8]` / `mov eax, [ebp+8]` (eax is next written
# by `lea eax, [ebp-4]`, whose store the recompiler emits after the pushes,
# and nothing between reads the eax slot).
#
# Mountains: the recompiler hoisted -0xA0 into w9 (`movn w9, #0x9f`, the only
# use is `sub w26, w9, w10` = -(x & 0x3f) - 0xA0) and the loop bound is
# `cmp w8, #0xa0`, whose overflow flag is formed from w24 = 0x9f (w24's only
# use). All three move together to 214.
#
# Sky quad: both `mov eax, 0x280` (x86 +0x173 and +0x225) became ONE
# `mov w20, #0x280`; the second site re-stores w20 (+0xE97D30) and nothing in
# between writes w20 -- proven before patching.
# ---------------------------------------------------------------------------
SNOW_TILE = -(-WIDE_W // 4)                      # ceil(854 / 4) = 214
SNOW_SKY_W = WIDE_W + WIDE_X                     # 747


def _cmp_w_imm(rn, imm):
    return 0x7100001F | (imm << 10) | (rn << 5)


def _quad_group(x86, arm, xslot, xslot_word, wsite, xsite):
    return (x86, arm, [
        ('x value', xslot, xslot_word, _movn_w(HIGHWAY_X_REG, WIDE_X),
         [(xslot + 4, None, 'bl translate'),
          (xslot + 8, 0x39000014 if xslot_word >> 24 == 0x39 else 0xB9000014,
           'store of the same value to [ebp-1] / [ebp-4]')]),
        ('width', wsite, 0x52805008, _movz_w(8, WIDE_W),
         [(wsite - 4, None, 'bl translate'),
          (wsite + 4, 0xB9000008, 'str w8, [x0]   the width push')]),
        ('x store', xsite, 0xB900001F, HIGHWAY_X_STORE,
         [(xsite - 4, None, 'bl translate'),
          (xsite + 4, 0x29422668, 'ldp w8, w9, [x19, #0x10]'),
          (xsite + 8, 0x51001134, 'sub w20, w9, #4   lea eax, [ebp-4]')]),
    ])


GROUPS.update({
    'snowboard black quad': _quad_group(0x72DD94, 0xE81020, 0xE810A8,
                                        0x39000274, 0xE81110, 0xE8113C),
    'snowboard white fade': _quad_group(0x72DD53, 0xE80E80, 0xE80F0C,
                                        0x39000274, 0xE80F74, 0xE80FA0),
    'snowboard opaque quad': _quad_group(0x72DDD5, 0xE811C0, 0xE81218,
                                         0xB9000274, 0xE81294, 0xE812C0),
    'snowboard mountains': (0x72DAF0, 0xE553F0, [
        ('tile start', 0xE55744, _movn_w(9, -0xA0), _movn_w(9, -SNOW_TILE),
         [(0xE55740, 0x7940000A, 'ldrh w10, [x0]'),
          (0xE55768, 0x4B0A013A, 'sub w26, w9, w10   its only use')]),
        ('bound flag', 0xE5575C, _movz_w(24, 0x9F), _movz_w(24, SNOW_TILE - 1),
         [(0xE5592C, 0x4B08030A, 'sub w10, w24, w8   its only use')]),
        ('bound', 0xE55930, _cmp_w_imm(8, 0xA0), _cmp_w_imm(8, SNOW_TILE),
         [(0xE55928, 0x79C00008, 'ldrsh w8, [x0]'),
          (0xE5593C, 0x1A9FA7E9, 'cset w9, lt')]),
    ]),
    'snowboard sky': (0x72E31F, 0xE97190, [
        ('width', 0xE97960, _movz_w(20, 0x280), _movz_w(20, SNOW_SKY_W),
         [(0xE9795C, None, 'bl translate'),
          (0xE9796C, 0xB90002B4, 'str w20, [x21]   eax = 0x280 (+0x173)'),
          (0xE97D30, 0xB90002B4, 'str w20, [x21]   eax = 0x280 (+0x225)')]),
    ]),
})

# ---------------------------------------------------------------------------
# GROUP -- SUBMARINE TIME-LIMIT FILL (build 559)
#
# x86 0x7916F7 pushes [0xe98c14] (colour), 0, 0, 0x7f7f0000, 0x1e0, 0x280, 0, 0
# and calls the quad submitter 0x671d2a (ARM +0xAF4730) -- the same full-screen
# red quad the highway/snowboard fixes widen.  Same recipe: width 640 -> 854,
# x 0 -> -107 through w11.  w11 is loaded in the slot of the eax shadow store
# of `mov eax, [0xe98c14]` (+0x10770A8): the push re-reads w20, not the slot,
# and the callee's first eax access is a write (+0xAF4790 `str w20, [x19]`).
# ---------------------------------------------------------------------------
GROUPS['submarine time-limit fill'] = (0x7916F7, 0x1077050, [
    ('x value', 0x10770A8, 0xB9000274, _movn_w(HIGHWAY_X_REG, WIDE_X),
     [(0x10770A0, 0xB9400014, 'ldr w20, [x0]   eax = [0xe98c14]'),
      (0x10770B0, None, 'bl translate'),
      (0x10770B4, 0xB9000014, 'str w20, [x0]   push eax')]),
    ('width', 0x1077120, 0x52805008, _movz_w(8, WIDE_W),
     [(0x107711C, None, 'bl translate'),
      (0x1077124, 0xB9000008, 'str w8, [x0]   the width push'),
      (0x1077108, 0x321B0FE8, 'mov w8, #0x1e0   the height push')]),
    ('x store', 0x107714C, 0xB900001F, HIGHWAY_X_STORE,
     [(0x1077148, None, 'bl translate'),
      (0x1077138, 0xB900001F, 'str wzr, [x0]   the y push'),
      (0x107715C, 0x97E9F575, 'bl +0xAF4730   the quad submitter'),
      (0xAF4790, 0xB9000274, 'callee: str w20, [x19]   first eax access')]),
])

# ---------------------------------------------------------------------------
# GROUP -- COASTER END FADE (build 562)
#
# x86 0x5F2417 (ARM +0x8A0B60), called from 0x5EA77E with an alpha, submits
# the coaster's one full-screen quad -- the fade to black -- into the type-8
# object [this+0xD4]:
#     x  = [0xC3F784] (+ 0.0)                  vertices 0 and 1
#     x  = [0x90147C] * 320 + [0xC3F784]       vertices 2 and 3
# with [0x90147C] the 2D scale (2 at the 640x480 mode 0x404D80 returns by
# default) and [0xC3F784] the viewport x (0). The 640 span becomes -108..748
# (FFNx's -107..747, rounded out by one pixel so no seam can show):
#   * left:  the int -> double of [0xC3F784] (`ldr s0 / sshll / scvtf d0,d0`)
#     becomes `ldr w9 / sub w9, w9, #108 / scvtf d0, w9` -- the middle word
#     of the three stays (`ldr w8, [x25, #0x60]` or `mov w0, w21`).
#   * right: `add w8, w8, w8, lsl #2 / lsl wN, w8, #6` (= s * 320) becomes
#     `mov w9, #374 / mul wN, w8, w9` (= s * 374).
# w9 is dead at every site: the next instruction to touch it is the
# translator call or a write (`ldr w9, [x25, #0x60]`, `mov w9, #0xf0`).
# ---------------------------------------------------------------------------
_LDR_S0 = 0xBD400000          # ldr s0, [x0]
_SSHLL = 0x0F20A400           # sshll v0.2d, v0.2s, #0
_SCVTF_DD = 0x5E61D800        # scvtf d0, d0
_LDR_W9 = 0xB9400009          # ldr w9, [x0]
_SUB_W9 = 0x51000000 | (-(WIDE_X - 1) << 10) | (9 << 5) | 9   # sub w9,w9,#108
_SCVTF_DW9 = 0x1E620120       # scvtf d0, w9
_TIMES5 = 0x0B080908          # add w8, w8, w8, lsl #2
COASTER_FADE_W = 374          # 320 + 54 at s = 1; 748 at s = 2
_MOV_W9_W = 0x52800009 | (COASTER_FADE_W << 5)


def _cf_left(ldr, mid, cvt, midword, tag):
    return [
        ('%s x load' % tag, ldr, _LDR_S0, _LDR_W9,
         [(ldr - 4, None, 'bl translate  [0xC3F784]')]),
        ('%s x shift' % tag, ldr + 4 if mid != ldr + 4 else ldr + 8,
         _SSHLL, _SUB_W9, [(mid, midword, 'the untouched middle word')]),
        ('%s x convert' % tag, cvt, _SCVTF_DD, _SCVTF_DW9, []),
    ]


GROUPS['coaster end fade'] = (0x5F2417, 0x8A0B60,
    _cf_left(0x8A0CE0, 0x8A0CE8, 0x8A0CEC, 0xB9406328, 'v0') +
    _cf_left(0x8A0F20, 0x8A0F24, 0x8A0F2C, 0x2A1503E0, 'v1') + [
    ('v2 width a', 0x8A117C, _TIMES5, _MOV_W9_W,
     [(0x8A1174, None, 'bl translate  [0x90147C]'),
      (0x8A1178, 0xB9400008, 'ldr w8, [x0]')]),
    ('v2 width b', 0x8A1180, 0x531A651C, 0x1B097D1C, []),   # lsl -> mul w28
    ('v3 width a', 0x8A13D8, _TIMES5, _MOV_W9_W,
     [(0x8A13D0, None, 'bl translate  [0x90147C]'),
      (0x8A13D4, 0xB9400008, 'ldr w8, [x0]')]),
    ('v3 width b', 0x8A13DC, 0x531A6515, 0x1B097D15, []),   # lsl -> mul w21
])

# (group, register, first word, one past the last word): nothing but the
# translator may touch the register in between.
LIVE_SPANS = (
    ('highway fade', HIGHWAY_X_REG, 0xA3F0B8, 0xA3F118),
    ('snowboard black quad', HIGHWAY_X_REG, 0xE810AC, 0xE8113C),
    ('snowboard white fade', HIGHWAY_X_REG, 0xE80F10, 0xE80FA0),
    ('snowboard opaque quad', HIGHWAY_X_REG, 0xE8121C, 0xE812C0),
    ('snowboard mountains', 9, 0xE55748, 0xE55768),
    ('submarine time-limit fill', HIGHWAY_X_REG, 0x10770AC, 0x107714C),
)
# (group, register, lo, hi): the register may be READ in between (it is
# re-stored) but nothing may WRITE it. Calls other than the translator are
# allowed only because w20 is callee-saved.
KEEP_SPANS = (
    ('snowboard sky', 20, 0xE97964, 0xE97D30),
)
# w24 is callee-saved; in the whole mountains function it may appear only in
# the prologue/epilogue pair and at its two sites.
W24_FN = (0xE553F0, 0xE55E10, (0xE5575C, 0xE5592C))
# the eax shadow slot the x value replaces must not be READ before the
# recompiled `lea eax, [ebp-4]` rewrites it
EAX_DEAD = (
    ('snowboard black quad', 0xE810AC, 0xE8114C),
    ('snowboard white fade', 0xE80F10, 0xE80FB0),
    ('snowboard opaque quad', 0xE8121C, 0xE812D0),
    ('submarine time-limit fill', 0x10770AC, 0x1077160),
)

# ff7_en .rdata: snowboard_sky_quad_pos_x (read at x86 0x72E34B and 0x72E3F1,
# both inside the sky quad submitter; nothing else references it).
EXE_PATCHES = (
    ('snowboard sky', 'sky quad x', 0x7B7DB8, 0.0, float(WIDE_X),
     ((0x72E34B, bytes.fromhex('D805B87D7B00')),
      (0x72E3F1, None))),
)


def enabled() -> bool:
    v = os.environ.get(MINIFADE_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false')
    try:
        import ff7nx_battlewide
        return ff7nx_battlewide.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def _main_path(target):
    if os.path.isdir(target):
        return os.path.join(target, 'atmosphere', 'contents',
                            '0100A5B00BDC6000', 'exefs', 'main')
    return target


def _text(nso):
    for seg in nso.segments:
        if seg.name == '.text':
            return bytes(seg.data)
    raise SystemExit('no .text segment')


def w32(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _is_bl_to(word, va, target):
    if (word >> 26) != 0x25:
        return False
    imm = word & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return va + imm * 4 == target


def _writes_reg(word, va, reg):
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    ins = next(md.disasm(struct.pack('<I', word), va), None)
    if ins is None:
        return True
    _read, write = ins.regs_access()
    return bool({ins.reg_name(r) for r in write} & {'w%d' % reg, 'x%d' % reg})


def _touches_reg(word, va, reg):
    """Does the instruction read or write x<reg>/w<reg>?  Decoded by capstone
    (its register access sets), not by bit fields: an immediate can carry the
    register number's bits -- `and w9, w0, #0xfff` in the translator does."""
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    ins = next(md.disasm(struct.pack('<I', word), va), None)
    if ins is None:
        return True                         # undecodable: refuse, do not guess
    read, write = ins.regs_access()
    names = {ins.reg_name(r) for r in list(read) + list(write)}
    return bool(names & {'w%d' % reg, 'x%d' % reg})


def check_anchors(t, path=None):
    """Refuse unless every anchor around every site is the measured code."""
    if path is not None:
        import nxmap
        m = nxmap.Main(path)
        for group, (x86, arm, _s) in GROUPS.items():
            if m.x86_to_arm.get(x86) != arm:
                raise SystemExit('%s: x86 %#x is not translated at +%#x'
                                 % (group, x86, arm))
    for group, (_x, _a, sites) in GROUPS.items():
        for name, va, stock, new, anchors in sites:
            for ava, want, what in anchors:
                got = w32(t, ava)
                ok = (_is_bl_to(got, ava, TRANSLATE) if want is None
                      else got == want)
                if not ok:
                    raise SystemExit('%s %s: anchor +%#x (%s) holds %08X'
                                     % (group, name, ava, what, got))
    # register liveness: nothing but translator calls between the load and
    # its use may touch it, and the translator is the measured
    # x0/x8/x9/x10 leaf.
    for group, reg, lo, hi in LIVE_SPANS:
        calls = False
        for va in range(lo, hi, 4):
            word = w32(t, va)
            if _is_bl_to(word, va, TRANSLATE):
                calls = True
                continue
            if (word >> 26) in (0x25, 0x05):
                raise SystemExit('%s: +%#x calls something other than the '
                                 'translator' % (group, va))
            if _touches_reg(word, va, reg):
                raise SystemExit('%s: +%#x touches w%d' % (group, va, reg))
        for va in range(TRANSLATE, TRANSLATE + 0x38 if calls else TRANSLATE,
                        4):
            if _touches_reg(w32(t, va), va, reg):
                raise SystemExit('%s: the translator touches w%d at +%#x'
                                 % (group, reg, va))
    for group, reg, lo, hi in KEEP_SPANS:
        for va in range(lo, hi, 4):
            word = w32(t, va)
            if (word >> 26) in (0x25, 0x05):
                continue                      # callee-saved across calls
            if _writes_reg(word, va, reg):
                raise SystemExit('%s: +%#x writes w%d' % (group, va, reg))
    lo, hi, allowed = W24_FN
    for va in range(lo, hi, 4):
        word = w32(t, va)
        if va in allowed or (word & 0x7FC00000) in (0x29000000, 0x29400000,
                                                    0x29800000, 0x28C00000,
                                                    0x28800000, 0x29C00000):
            continue                                   # stp / ldp frame
        if not _is_bl_to(word, va, TRANSLATE) and (word >> 26) != 0x25 \
                and _touches_reg(word, va, 24):
            raise SystemExit('snowboard mountains: +%#x touches w24' % va)
    for group, lo, hi in EAX_DEAD:
        for va in range(lo, hi, 4):
            word = w32(t, va)
            # any load whose base is x19 and offset 0 reads the eax slot
            if (word & 0x3B000000) == 0x39000000 and (word >> 22) & 1 \
                    and ((word >> 5) & 31) == 19 and ((word >> 10) & 0xFFF) == 0:
                raise SystemExit('%s: +%#x reads the eax slot' % (group, va))


def group_state(t, group):
    sites = GROUPS[group][2]
    cur = [w32(t, va) for _n, va, _s, _w, _a in sites]
    if all(c == s for c, (_n, _v, s, _w, _a) in zip(cur, sites)):
        return 'stock'
    if all(c == w for c, (_n, _v, _s, w, _a) in zip(cur, sites)):
        return 'applied'
    return 'mixed'


def state(t):
    st = {group_state(t, g) for g in GROUPS}
    return st.pop() if len(st) == 1 else 'mixed'


def patch(path, mode, log=print):
    nso = nso_patcher.read_nso(nso_patcher.Path(path))
    t = _text(nso)
    spec = {'name': 'minigame fades', 'patches': []}
    for group, (_x, _a, sites) in GROUPS.items():
        for name, va, stock, new, _anc in sites:
            want, cur = (new, stock) if mode == 'apply' else (stock, new)
            if w32(t, va) == want:
                continue
            spec['patches'].append({
                'name': '%s %s' % (group, name), 'va': '0x%X' % va,
                'expect': struct.pack('<I', cur).hex(' '),
                'set': struct.pack('<I', want).hex(' ')})
    if not spec['patches']:
        return
    for line in nso_patcher.apply_spec(nso, spec):
        log('    ' + line)
    nso_patcher.Path(path).write_bytes(nso_patcher.rebuild(nso))
    back = state(_text(nso_patcher.read_nso(nso_patcher.Path(path))))
    if back != ('applied' if mode == 'apply' else 'stock'):
        raise SystemExit('write did not take (now %s)' % back)


def apply(main, revert=False, log=print) -> int:
    path = _main_path(str(main))
    try:
        t = _text(nso_patcher.read_nso(nso_patcher.Path(path)))
        check_anchors(t, path)
    except (SystemExit, nso_patcher.PatchError) as exc:
        log('  ! minigame fades: %s' % exc)
        return 1
    for g in GROUPS:
        if group_state(t, g) == 'mixed':
            log('  ! %s: a site is in an unrecognised state; refusing to '
                'guess.' % g)
            return 1
    if state(t) == ('stock' if revert else 'applied'):
        log('  minigame fades: already %s'
            % ('reverted' if revert else 'applied'))
        return 0
    try:
        patch(path, 'revert' if revert else 'apply', log=log)
    except (SystemExit, nso_patcher.PatchError) as exc:
        log('  ! minigame fades: %s' % exc)
        return 1
    if not revert:
        log('  chocobo race fade quad: x 0 -> %d, w 640 -> %d (record width '
            '320 -> %d, times the race\'s scale of 2), height untouched -- '
            'FFNx widescreen.cpp "Chocobo fix"' % (WIDE_X, WIDE_W, WIDE_W // 2))
        log('  highway fade quad: x 0 -> %d, w 640 -> %d, y 0 / h 480 '
            'untouched -- FFNx widescreen.cpp "Highway fix" (x via the dead '
            'ecx shadow slot and w11; no cave)' % (WIDE_X, WIDE_W))
        log('  snowboard: black / white fade / opaque quads x 0 -> %d, w 640 '
            '-> %d; mountain tiles +-160 -> +-%d; sky quad right edge 640 '
            '-> %d -- FFNx widescreen.cpp "Snowboard fix" (the sky quad x '
            'is ff7_en data, see apply_exe)'
            % (WIDE_X, WIDE_W, SNOW_TILE, SNOW_SKY_W))
        log('  coaster end fade: x -108 .. +748 (s x 374), was 0 .. 640')
        log('  submarine time-limit red fill: x 0 -> %d, w 640 -> %d '
            '(x via the dead eax shadow slot and w11; no cave)'
            % (WIDE_X, WIDE_W))
    return 0


def _exe_path(target):
    if os.path.isdir(target):
        return os.path.join(target, 'atmosphere', 'contents',
                            '0100A5B00BDC6000', 'romfs', 'ff7', 'resources',
                            'ff7_1.02', 'ff7_en')
    return target


def _pe_off(data, va):
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    optsz = struct.unpack_from('<H', data, pe + 20)[0]
    off = pe + 24 + optsz
    for i in range(nsec):
        vsize, rva, rsize, raw = struct.unpack_from('<IIII', data,
                                                    off + 40 * i + 8)
        base = rva + 0x400000
        if base <= va < base + rsize:
            return raw + va - base
    raise ValueError('VA %#x not in file' % va)


def apply_exe(target, revert=False, log=print) -> int:
    """The ff7_en half: snowboard_sky_quad_pos_x (FFNx patch_code_float)."""
    path = _exe_path(str(target))
    try:
        data = bytearray(open(path, 'rb').read())
        for group, name, va, stock, new, refs in EXE_PATCHES:
            for rva, want in refs:
                if want is not None:
                    got = bytes(data[_pe_off(data, rva):_pe_off(data, rva)
                                     + len(want)])
                    if got != want:
                        raise ValueError('%s: x86 %#x is %s' % (group, rva,
                                                               got.hex()))
            o = _pe_off(data, va)
            cur = struct.unpack_from('<f', data, o)[0]
            want = stock if revert else new
            if cur not in (stock, new):
                raise ValueError('%s %s at %#x holds %r' % (group, name, va,
                                                           cur))
            if cur != want:
                struct.pack_into('<f', data, o, want)
                log('  %s %s %g -> %g (ff7_en %#x)' % (group, name, cur,
                                                        want, va))
    except (OSError, ValueError) as exc:
        log('  ! minigame fades (ff7_en): %s' % exc)
        return 1
    tmp = path + '.minifade-tmp'
    with open(tmp, 'wb') as fh:
        fh.write(data)
    os.replace(tmp, path)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('target')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--apply', action='store_true')
    g.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    path = _main_path(a.target)
    if a.apply or a.revert:
        sys.exit(apply(path, revert=a.revert))
    t = _text(nso_patcher.read_nso(nso_patcher.Path(path)))
    check_anchors(t, path)
    print('  %s' % path)
    for grp in GROUPS:
        print('  %-18s %s' % (grp, group_state(t, grp)))


if __name__ == '__main__':
    main()
