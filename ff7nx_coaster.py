#!/usr/bin/env python3
r"""
ff7nx_coaster.py -- Gold Saucer Shooting Coaster: 360-degree analogue aim.
exefs/main only; one cave in the dead-space part 'coaster' (see
ff7nx_deadspace). The 16:9 edge pop-out is fixed in the ride data instead --
see ff7nx_coasterworld.

    SEVENTH_NX_COASTER_AIM=0        stock digital aim
    SEVENTH_NX_COASTER_AIM_SPEED=N  full-tilt cursor speed, units/tick (8)
    SEVENTH_NX_COASTER_YAW=0        stock (stepped) track yaw -- BUILD 563
    SEVENTH_NX_COASTER_EDGE=0       cursor stops at the 4:3 edges -- BUILD 564
    SEVENTH_NX_COASTER_FAR=F        draw distance x F (ff7nx_coasterworld)

1. AIM (x86 0x5EE150, ARM +0x887280)
====================================
The cursor is two int16s in PSX screen units, x 0xC3FB58 (0..320) and y
0xC3FB5C (0..240), clamped by the same function right after input. Stock, in
the ride state ([0xC3F890] == 1), four `IsHeld(bit)` tests add +-10 per 60 Hz
tick: 0x1000 up, 0x4000 down, 0x8000 left, 0x2000 right. The port turns the
stick into those four keys, so the cursor can only travel along 8 directions
at one speed.

The cave replaces that block when the left stick is out of its dead zone:

    v      = (right - left, down - up)       the port's own stick halves, read
                                             from the object ff7nx_analog uses
    m      = |v|; below DZ (0.15) -> stock digital path, untouched
    t      = min((m - DZ) / (1 - DZ), 1)     radial dead zone, rescaled
    step   = v / m * SPEED * t^2             any angle; fine control near centre
    pos    += step, in 16.16 fixed point

The 16-bit fractions live in the two padding halves 0xC3FB5A / 0xC3FB5E: every
x86 access to 0xC3FB58 / 0xC3FB5C is a non-indexed 16-bit one (23 each), no
instruction addresses the halves, and the nearest arrays end at 0xC3FB48
(0xC3FA80, 100 words) and 0xC3F9F8 (0xC3F9A8, 20 dwords). The game's own
clamps (x86 0x5EE3C5..) still run after the cave.

Hook: +0x8875EC `ldr w8, [x25, #0x10]` (x86 0x5EE25E, `push 0x4000`, the first
word after `cmp [0xC3F890], 1 / jne`). Active -> +0x887794 (x86 0x5EE2E8, the
first word after the four direction blocks); idle -> replay the word, back to
+0x8875F0. w21 holds 0xC3FB58 for the whole function (set at +0x8872B0, not
written again before +0x88813C); nothing outside the skipped block branches
into it. The cave saves x19/x20/x29/x30 on the stack and otherwise uses x0, x9, x11,
x15 and s0..s4 -- w11 is dead here (next written at +0x888074 before any
read), x9 is translator scratch, x15 and all FP registers are unused by the
function.

2. TRACK YAW (x86 0x5EA194, ARM +0x89E630) -- BUILD 563
=======================================================
Every position on the ride -- the camera (0x5EA973, 100 units behind the
car) and every track object (0x5EB5CF) -- comes from 0x5EA194(pos), which
interpolates the node table [0xC3F870] (3 angles a node, 4096 = 360 deg)
between node pos >> 16 and the next with t = pos & 0xFFFF. Pitch and roll
are interpolated properly. The yaw is not:

    d = (next.yaw - cur.yaw) / 32         <- only 1/32 of the step
    wrap d to +-0x800                      <- AFTER the /32, so it never fires
    yaw = -cur.yaw + t * d                 <- and the sign is backwards

so the yaw sits almost still through a node and JUMPS by the whole step
(up to 41 units, 3.6 deg) at the next one; where the heading crosses 0/4096
the unwrapped delta /32 is ~125 units, which is then swept through inside
one node (11 deg one way, then snapped back). Measured over the whole ride:
the largest yaw change between two 60 Hz ticks is 114 units (10 deg) at the
lift-hill speed; with the fix it is 7 units (0.6 deg) -- the real turn rate.
That stepping is the judder in the turns: the camera's heading jumps a few
degrees every node while the car and rails in front of it move smoothly.

Fix, three words in place at x86 0x5EA1EE..0x5EA207:
    +0x89E7D4  sub w8, w9, w8   -> sub w8, w8, w9    d = cur - next (sign)
    +0x89E7E0  add w8, w9, w8   -> nop               (round toward zero ...)
    +0x89E7E4  asr w20, w8, #5  -> mov w20, w8       (... of a /32 now gone)
The game's own wrap (+-0x800) now sees the whole step and works, and
yaw = -(cur + t * (next - cur)) is continuous at every node.

3. CURSOR TO THE 16:9 EDGES (x86 0x5EE3C5, BUILD 564)
=====================================================
Right after input the cursor x is clamped to 0..320 (PSX units; x2 at the
640 mode) -- the 4:3 frame. Every consumer maps it the same way, x * s +
viewport x: the hit test (0x5E99FB compares it with each target's
projected box), the cursor sprite and the beam (0x5F09C4, 0x5F0C81) -- so
nothing but the clamp knows about 4:3. The 16:9 frame is 640-space
-107..747, i.e. -53.5..373.5: the clamp becomes -53..373. Only when the
16:9 widescreen group is on (ff7nx_minifade.enabled()).

The recompiled clamps emulate x86 flags; each is rewritten to one real
compare, and the shadow flag bytes it leaves are dead (the next x86
instruction, `movsx / cmp` for y, sets flags again):
    right  +0x887A70  b.ne   -> cmp w8, #373
           +0x887A74  cmp    -> b.le  +0x887A8C (keep)
           +0x887A78  b.eq   -> nop
           +0x887A84  mov w8, #0x140 -> mov w8, #373
    left   +0x887A9C  ubfx (sign) -> cmn w8, #53
           +0x887AA4  strb wzr (OF) -> mov w11, #-53
           +0x887AA8  cbz    -> b.ge  +0x887AB8 (keep)
           +0x887AB4  strh wzr -> strh w11      (the translator keeps x11)

The hit test (0x5E99FB) also gates every target through 0x5EECB5: inside
the left and right planes of a frustum built once at ride start by
0x5EEA50 from the corners (+-160, +-120, 256) -- the 4:3 view. Nothing else
calls either (0x5EEDAE, 0x5EEF27, 0x5EF071, 0x5EF114 are dead variants),
so the corners' x go to +-214 (160 x 4/3, rounded out) and a target at the
16:9 edge can be hit where the cursor can now reach it:
           +0x894D0C  mov w20, #-160 -> #-214   (stored to two corners)
           +0x894D5C  mov w21, #160  -> #214

4. FAR PLANE AND FOG (BUILD 564, see ff7nx_coasterworld)
========================================================
    +0x884A8C/90  mov/movk w8, 14300.0f  -> 14300 * F   (game_obj+0x9A4)
    +0x8936A4     mov w8, #10410          -> 10410 * F   fog start [0xC3F750]
    +0x8936B4     mov w8, #14300          -> 14300 * F   fog end   [0xC3F754]
"""
from __future__ import annotations

import os
import struct

import a64 as A
from ff7nx_analog_cave import Asm, fcvtzs_fix, orr_reg
import ff7nx_analog as AN

AIM_ENV = 'SEVENTH_NX_COASTER_AIM'
SPEED_ENV = 'SEVENTH_NX_COASTER_AIM_SPEED'
SPEED_DEFAULT = 8.0
DEADZONE = 0.15

TRANSLATE = 0x10FC3A0

AIM_FN = (0x5EE150, 0x887280)
AIM_SITE = 0x8875EC
AIM_STOCK = 0xB9401328                    # ldr w8, [x25, #0x10]
AIM_BACK = 0x8875F0
AIM_SKIP = 0x887794
AIM_ANCHORS = (
    (0x8872B0, 0x529F6B15, 'mov w21, #0xfb58'),
    (0x8872B4, 0x72A01875, 'movk w21, #0xc3, lsl #16'),
    (0x8875E8, 0x54002AC1, 'b.ne  (ride state != 1)'),
    (0x8875FC, 0x321203E8, 'mov w8, #0x4000   push 0x4000 (down)'),
    (0x887648, 0x11002918, 'add w24, w8, #0xa   y += 10'),
    (0x887784, 0x11002917, 'add w23, w8, #0xa   x += 10'),
    (0x887794, 0x5103A2B7, 'sub w23, w21, #0xe8   x86 0x5EE2E8'),
)

FSQRT = lambda rd, rn: 0x1E21C000 | (rn << 5) | rd          # noqa: E731
COND_GT, COND_LE = 12, 13


YAW_ENV = 'SEVENTH_NX_COASTER_YAW'
YAW_WORDS = (
    (0x89E7D4, 0x4B080128, 0x4B090108),   # sub w8, w9, w8 -> sub w8, w8, w9
    (0x89E7E0, 0x0B080128, 0xD503201F),   # add w8, w9, w8 -> nop
    (0x89E7E4, 0x13057D14, 0x2A0803F4),   # asr w20, w8, #5 -> mov w20, w8
)
YAW_ANCHORS = (
    (0x89E7CC, 0x79C00008, 'ldrsh w8, [x0]   cur.yaw'),
    (0x89E7D0, 0xB94002A9, 'ldr w9, [x21]    next.yaw (eax)'),
    (0x89E7D8, 0x131F7D09, 'asr w9, w8, #31  cdq'),
    (0x89E7DC, 0x12001129, 'and w9, w9, #0x1f'),
    (0x89E7EC, 0x5101A100, 'sub w0, w8, #0x68   [ebp-0x68]'),
    (0x89E7FC, 0xB9000014, 'str w20, [x0]    d -> [ebp-0x68]'),
)
YAW_FN = (0x5EA194, 0x89E630)


EDGE_ENV = 'SEVENTH_NX_COASTER_EDGE'
EDGE_LEFT, EDGE_RIGHT = -53, 373
EDGE_FRUSTUM = 214                  # ceil(160 * (16/9) / (4/3))
EDGE_WORDS = (
    (0x887A70, 0x540000E1, 0x7100001F | (EDGE_RIGHT << 10) | (8 << 5)),
    (0x887A74, 0x7105011F, 0x540000CD),            # b.le +0x887A8C
    (0x887A78, 0x540000A0, 0xD503201F),
    (0x887A84, 0x52802808, 0x52800008 | (EDGE_RIGHT << 5)),
    (0x887A9C, 0x530F3D08, 0x3100001F | (-EDGE_LEFT << 10) | (8 << 5)),
    (0x887AA4, 0x39009B3F, 0x12800000 | ((-EDGE_LEFT - 1) << 5) | 11),
    (0x887AA8, 0x34000088, 0x5400008A),            # b.ge +0x887AB8
    (0x887AB4, 0x7900001F, 0x7900000B),            # strh w11, [x0]
    # the hit test's frustum gate (x86 0x5EEA50): corners x +-160 -> +-214
    (0x894D0C, 0x128013F4, 0x12800000 | ((EDGE_FRUSTUM - 1) << 5) | 20),
    (0x894D5C, 0x52801415, 0x52800000 | (EDGE_FRUSTUM << 5) | 21),
)
EDGE_ANCHORS = (
    (0x887A40, 0x7105011F, 'cmp w8, #0x140'),
    (0x887A7C, 0x2A1503E0, 'mov w0, w21'),
    (0x887A80, None, 'bl translate'),
    (0x887A88, 0x79000008, 'strh w8, [x0]'),
    (0x887A8C, 0x2A1503E0, 'mov w0, w21   (right skip target)'),
    (0x887A94, 0x79C00008, 'ldrsh w8, [x0]   x again'),
    (0x887AAC, 0x2A1503E0, 'mov w0, w21'),
    (0x887AB0, None, 'bl translate'),
    (0x887AB8, 0x110012B7, 'add w23, w21, #4   (left skip target)'),
    (0x894D10, 0xB9000014, 'str w20, [x0]   corner x [ebp-0x60]'),
    (0x894DA4, 0xB9000014, 'str w20, [x0]   corner x [ebp-0x50]'),
    (0x894DB4, 0x12800EF4, 'mov w20, #-0x78   (next corner y)'),
    (0x894D60, 0xB9000015, 'str w21, [x0]   corner x [ebp-0x80]'),
    (0x894DEC, 0xB9000015, 'str w21, [x0]   corner x [ebp-0x70]'),
)
FAR_SITES = (0x884A8C, 0x884A90, 0x8936A4, 0x8936B4)
FAR_ANCHORS = (
    (0x884A80, 0x11269100, 'add w0, w8, #0x9a4   game_obj far'),
    (0x884A94, 0xB9000008, 'str w8, [x0]'),
    (0x89369C, 0x51002260, 'sub w0, w19, #8   0xC3F750'),
    (0x8936A8, 0xB9000008, 'str w8, [x0]'),
    (0x8936AC, 0x51001260, 'sub w0, w19, #4   0xC3F754'),
    (0x8936B8, 0xB9000008, 'str w8, [x0]'),
)


def _movz_w(rd, v):
    return 0x52800000 | ((v & 0xFFFF) << 5) | rd


def _movk16_w(rd, v):
    return 0x72A00000 | ((v & 0xFFFF) << 5) | rd


def far_words(f):
    """(va, stock, new) for draw distance x f."""
    far = struct.unpack('<I', struct.pack('<f', 14300.0 * f))[0]
    return (
        (0x884A8C, _movz_w(8, 0x7000), _movz_w(8, far & 0xFFFF)),
        (0x884A90, _movk16_w(8, 0x465F), _movk16_w(8, far >> 16)),
        (0x8936A4, _movz_w(8, 10410), _movz_w(8, int(10410 * f + 0.5))),
        (0x8936B4, _movz_w(8, 14300), _movz_w(8, int(14300 * f + 0.5))),
    )


def edge_enabled(env=None):
    if not _on(env, EDGE_ENV):
        return False
    try:
        import ff7nx_minifade
        return ff7nx_minifade.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def _put_words(text, words, anchors, what):
    t = bytes(text)
    bad = []
    for va, want, name in anchors:
        got = _w(t, va)
        ok = (A_is_bl(got, va) if want is None else got == want)
        if not ok:
            bad.append('%s anchor +%#x (%s) holds %08X' % (what, va, name, got))
    if bad:
        raise ValueError('; '.join(bad))
    cur = [_w(t, va) for va, _s, _n in words]
    if cur == [n for _v, _s, n in words]:
        return {}
    if cur != [st for _v, st, _n in words]:
        raise ValueError('%s words are in a mixed state' % what)
    out = {}
    for va, _st, new in words:
        struct.pack_into('<I', text, va, new)
        out[va] = new
    return out


def A_is_bl(word, va):
    if (word >> 26) != 0x25:
        return False
    imm = word & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return va + imm * 4 == TRANSLATE


def yaw_enabled(env=None):
    return _on(env, YAW_ENV)


def _on(env, name, default='1'):
    v = (os.environ if env is None else env).get(name, default).strip().lower()
    return v not in ('0', 'off', 'no', 'false', 'stock')


def aim_enabled(env=None):
    return _on(env, AIM_ENV)


def aim_speed(env=None):
    raw = (os.environ if env is None else env).get(SPEED_ENV, '').strip()
    try:
        v = float(raw) if raw else SPEED_DEFAULT
    except ValueError:
        v = SPEED_DEFAULT
    return v if 1.0 <= v <= 20.0 else SPEED_DEFAULT


def _fconst(a, sreg, value, wtmp=15):
    bits = struct.unpack('<I', struct.pack('<f', value))[0]
    a.emit(A.movz(wtmp, bits & 0xFFFF))
    a.emit(A.movk_hi(wtmp, bits >> 16))
    a.emit(A.fmov_s_from_w(sreg, wtmp))


def _enter(a):
    # Only x19/x20 and the return address have to survive a translator call:
    # saved here on the stack (16-byte aligned), not assumed of the callee.
    a.emit(A.stp64_pre(29, 30, 31, -0x20))
    a.emit(A.stp64_off(19, 20, 31, 0x10))


def _leave(a):
    a.emit(A.ldp64_off(19, 20, 31, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, 0x20))


def build_aim(speed):
    dz = DEADZONE

    def build(at, addr):
        a = Asm(at, addr)
        _enter(a)
        a.emit(A.adrp(9, a.pc(), AN.INPUT_GOT & ~0xFFF))
        a.emit(A.ldr64(9, 9, AN.INPUT_GOT & 0xFFF))
        a.cbz(9, 'idle', wide=True)
        for off in AN.INPUT_CHAIN:
            a.emit(A.ldr64(9, 9, off))
            a.cbz(9, 'idle', wide=True)
        a.emit(A.ldr_s(0, 9, AN.OBJ_UP))
        a.emit(A.ldr_s(1, 9, AN.OBJ_DOWN))
        a.emit(A.ldr_s(2, 9, AN.OBJ_RIGHT))
        a.emit(A.ldr_s(3, 9, AN.OBJ_LEFT))
        a.emit(A.fsub_s(1, 1, 0))                 # y = down - up
        a.emit(A.fsub_s(0, 2, 3))                 # x = right - left
        a.emit(A.fmul_s(2, 0, 0))
        a.emit(A.fmul_s(3, 1, 1))
        a.emit(A.fadd_s(2, 2, 3))                 # m^2
        _fconst(a, 4, dz * dz)
        a.emit(A.fcmp_s(2, 4))
        a.bcond('idle', COND_LE)                  # also NaN -> idle
        a.emit(FSQRT(2, 2))                       # m
        _fconst(a, 4, dz)
        a.emit(A.fsub_s(3, 2, 4))
        _fconst(a, 4, 1.0 / (1.0 - dz))
        a.emit(A.fmul_s(3, 3, 4))                 # t
        _fconst(a, 4, 1.0)
        a.emit(A.fcmp_s(3, 4))
        a.emit(A.fcsel_s(3, 4, 3, COND_GT))       # t = min(t, 1)
        a.emit(A.fmul_s(3, 3, 3))                 # t^2
        _fconst(a, 4, float(speed))
        a.emit(A.fmul_s(3, 3, 4))
        a.emit(A.fdiv_s(3, 3, 2))                 # k = speed t^2 / m
        a.emit(A.fmul_s(0, 0, 3))
        a.emit(A.fmul_s(1, 1, 3))
        a.emit(fcvtzs_fix(19, 0, 16))             # dx, 16.16
        a.emit(fcvtzs_fix(20, 1, 16))             # dy, 16.16
        a.emit(A.mov_reg(0, 21))                  # 0xC3FB58
        a.emit(A.bl(a.pc(), TRANSLATE))
        for pos, frac, d in ((0, 2, 19), (4, 6, 20)):
            a.emit(A.ldrsh(15, 0, pos))
            a.emit(A.ldrh(11, 0, frac))
            a.emit(A.lsl(15, 15, 16))
            a.emit(orr_reg(15, 15, 11))
            a.emit(A.add_reg(15, 15, d))
            a.emit(A.strh(15, 0, frac))
            a.emit(A.asr(15, 15, 16))
            a.emit(A.strh(15, 0, pos))
        _leave(a)
        a.b('skip')
        a.label('idle')
        _leave(a)
        a.emit(AIM_STOCK)
        a.b('back')
        a.lab['skip'] = AIM_SKIP
        a.lab['back'] = AIM_BACK
        return a.resolve()
    return build


def _w(t, va):
    return struct.unpack_from('<I', t, va)[0]


def check_text(t):
    bad = []
    if _w(t, AIM_SITE) != AIM_STOCK:
        bad.append('aim site +%#x holds %08X' % (AIM_SITE, _w(t, AIM_SITE)))
    for va, want, what in AIM_ANCHORS:
        if _w(t, va) != want:
            bad.append('aim anchor +%#x (%s) holds %08X' % (va, what,
                                                             _w(t, va)))
    return bad


def patch_text(text, place, speed=None):
    speed = aim_speed() if speed is None else speed
    bad = check_text(bytes(text))
    if bad:
        raise ValueError('; '.join(bad))
    entry, placed = place(build_aim(speed))
    for va, word in placed.items():
        struct.pack_into('<I', text, va, word)
    written = dict(placed)
    word = A.b(AIM_SITE, entry)
    struct.pack_into('<I', text, AIM_SITE, word)
    written[AIM_SITE] = word
    return written


def yaw_state(t):
    cur = [_w(t, va) for va, _s, _n in YAW_WORDS]
    if cur == [st for _v, st, _n in YAW_WORDS]:
        return 'stock'
    if cur == [n for _v, _s, n in YAW_WORDS]:
        return 'applied'
    return 'mixed'


def patch_yaw(text):
    t = bytes(text)
    bad = ['yaw anchor +%#x (%s) holds %08X' % (va, what, _w(t, va))
           for va, want, what in YAW_ANCHORS if _w(t, va) != want]
    if bad:
        raise ValueError('; '.join(bad))
    st = yaw_state(t)
    if st == 'applied':
        return {}
    if st != 'stock':
        raise ValueError('track yaw words are in a mixed state')
    written = {}
    for va, _stock, new in YAW_WORDS:
        struct.pack_into('<I', text, va, new)
        written[va] = new
    return written


def apply_to_nso(src, dest, log=lambda *_: None, stock=None, speed=None,
                 aim=None, yaw=None, edge=None, far=None):
    import ff7nx_audio_cave as AC
    import ff7nx_deadspace as DS
    with open(src, 'rb') as fh:
        blob = fh.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    lo, hi = DS.part(src, 'coaster', stock)
    bump = DS.Bump(lo, hi)

    def place(build):
        before = dict(bump.placed)
        e = bump.put(build)
        return e, {k: v for k, v in bump.placed.items() if k not in before}
    aim = aim_enabled() if aim is None else aim
    yaw = yaw_enabled() if yaw is None else yaw
    written = {}
    if aim:
        log('  caves in the dead-space part +0x%X..+0x%X' % (lo, hi))
        written.update(patch_text(text, place, speed))
    if yaw:
        written.update(patch_yaw(text))
    edge = edge_enabled() if edge is None else edge
    if edge:
        written.update(_put_words(text, EDGE_WORDS, EDGE_ANCHORS, 'edge'))
    if far is None:
        import ff7nx_coasterworld
        far = ff7nx_coasterworld.far_factor()
    if far != 1.0:
        written.update(_put_words(text, far_words(far), FAR_ANCHORS, 'far'))
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, 0)
    chk_segs, _chk = AC.segments(out)
    assert chk_segs[0][2] == segs[0][2]
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as fh:
        fh.write(out)
    return {'words': len(written)}
