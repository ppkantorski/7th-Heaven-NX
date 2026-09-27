#!/usr/bin/env python3
r"""
ff7nx_condor.py -- Fort Condor at 16:9 (BUILDs 565-566). exefs/main only.

    SEVENTH_NX_CONDOR=0          stock Fort Condor (everything below off)
    SEVENTH_NX_CONDOR_UNCROP=0   keep the stock 640x440 letterbox (the
                                 horizontal widening and the clear stay on)

WHAT THE MINIGAME DRAWS
=======================
The battlefield is not 3D terrain. It is ONE pre-rendered picture, 512 x 1024
PSX units, cut into the eight 256x256 pages map0..map7 of condor.lgp (two
columns, four rows: map0/map1 are the condor on the reactor under the sky --
the first screen -- and map6/map7 the foot of the mountain). The 3D units are
drawn over it.

x86 0x6099F8 (ARM +0x9182A0) picks the visible window of that picture, four
call sites, all the same shape:

    SetRect(&src, camx, camy, camx + 320, camy + 220)     PSX units
    SetRect(&dst, [0xC60938], [0xC6093C], ...)             game px, (0, 20)
    0x60A160(&src, &dst, alpha)

and 0x60A160 (ARM +0x919230) intersects `src` with each page (table 0x905560)
and draws each piece at dst + (piece - src) * 2. Only dst.left/top are read.

So the picture is drawn 320 units wide -- 640 game px, the 4:3 frame -- while
the picture itself is 512 wide. The camera x (0xC613CC, from 0xC60B00) runs
0..192, starting at 96, the middle. At 16:9 the frame is 427 units: there is
art for 53.5 units each side at the starting camera and at every camera x in
54..138, and the sides of the screen were simply never drawn -- black on the
first frame, then whatever the last minigame or field left there.

1. THE MAP WINDOW (cave at the head of 0x60A160, +0x919230)
===========================================================
Every call widens the rects in place before the stock body reads them:

    src.left -= 54   src.right += 54          (+ top/bottom by 10 with UNCROP)
    dst.left -= 108                           (dst.top by 20 with UNCROP)

Both rects are the caller's locals, rebuilt by SetRect right before each of
the four calls, so nothing accumulates. Where the window runs off the
picture (camera x < 54 or > 138) the page intersection simply draws nothing
there; the colour clear below makes that black instead of garbage. The four
calls are the ONLY callers of 0x60A160.

Hook: the first word `str d8, [sp, #-0x60]!`, replayed at the end of the
cave. The cave saves x19/x20/x29/x30 and translates every field address on
its own -- the translator is a page table, so a rect straddling a 4 KB page
must not be indexed off one translated pointer.

2. THE COLOUR CLEAR (x86 0x5F4A69, ARM +0x8BE624)
=================================================
The frame routine clears with 0x66064A(0, 1, 1, game_obj): colour 0, depth 1.
Only depth is ever cleared, so anything not painted keeps the last frame's
pixels -- the orange and blue garbage down the sides. FFNx forces a colour
clear for exactly this mode (common.cpp, MODE_CONDOR). One word:

    +0x8BE624  str wzr, [x0]  ->  str w20, [x0]     push 0 -> push 1

w20 is the `mov w20, #1` at +0x8BE55C that the two neighbouring `push 1`s
already store; it is not written in between. gfx_drv_clear derefs the colour
pointer on both paths, so a colour clear adds no new dereference.

3. THE LETTERBOX (after x86 0x5F48F8, ARM +0x8BCAD8) -- UNCROP, BUILD 566
======================================================================
0x5F4630(mode 2) sets the viewport to (0, 20, 640, 440) through 0x66067A,
which does two things with it: it hands it to the driver (device rect y
30..690 plus a TL matrix with _22 = 440/480 that squashes the whole 640x480
game space into the box -- 57-row bars and an 8 % squash), and it builds the
3D projection from it (0x67CCDE(..., w, h): aspect w/h).

BUILD 565 rewrote the call's arguments to (0, 0, 640, 480). That fixed the
bars but also changed the projection's aspect from 640/440 to 640/480, so
the 3D units grew 480/440 = 1.09x horizontally against the flat map --
measured on hardware, 305 px of unit travel for 279 px of map travel -- and
slid across it as the camera moved.

BUILD 566 leaves the call stock (game object and projection keep 640x440)
and, right after it returns, calls the driver's own gfx_drv_setviewport
(+0x10D6760, w0..w3) with (0, 0, 640, 480). Only the driver's rect and TL
matrix change: everything the game drew in its 640x480 space -- map, units,
HUD, all of it -- now lands 1:1 instead of squashed, together. Nothing else
calls the driver's setviewport (its only reference is the driver table the
game calls through 0x66067A), and condor calls 0x66067A only at init.

4. THE UNIT CULL (x86 0x601B37, ARM +0x8F9A2C..)
===============================================
0x6019E7 skips a unit unless camx <= x <= camx + 320 (x86 0x601B37..0x601B65)
and camy <= y <= camy + 240. At 16:9 a unit would pop in 54 units inside the
screen edge, so the x window is opened by 70 (54 + a body width) each way:

    +0x8F9A2C  add w9, w8, #0x140   ->  add w9, w8, #0x186   right
    +0x8F9A94  ldrh w8, [x0]        ->  ldrh w9, [x0]        left: w9 = camx
    +0x8F9A98  strh w8, [x22, #8]   ->  sub w9, w9, #70            - 70
    +0x8F9A9C  ldp w8, w9, [x22,#4] ->  ldr w8, [x22, #4]          w8 = x

(edx, which the left test no longer writes, is next written at x86 0x601AE0
on the loop path and read by nothing on the fall-through path.) With UNCROP
the bottom test gets the 20 extra units too:

    +0x8F9954  add w9, w8, #0xf0    ->  add w9, w8, #0x104

5. THE CAMERA (cave at the head of x86 0x6099D9, ARM +0x9181F0) -- BUILD 566
============================================================================
The camera (0xC60B00 x, 0xC60B04 y) runs 0..192 by 0..804 in stock, which
at 16:9 shows 54 units of nothing past the picture's left/right edge and 10
past its top/bottom. 0x6099D9 is the per-frame copy of the camera into the
render copy (0xC613CC/D8) at the head of the battlefield draw (0x5F824A);
the cave clamps in place first:

    camera x   54 .. 138          camera y   10 .. 794
    cursor x    0 .. 512          cursor y    0 .. 1008   (0xCBCCC0/C2)

The cursor clamp is the stock one (camera limit + 4:3 screen limit). The
game's own scroll code (0x5FE91B, 0x5FEF60) is untouched: at the new limit
it still "scrolls" one step, the cursor keeps its screen offset, and the
clamp pulls the camera back -- so the cursor walks on at the scroll speed
until it reaches the picture's edge, i.e. the 16:9 screen edge. The scroll
counters 0xC74C38/3C that the scroll code also bumps are zeroed before every
use (0x5F32FF, 0x601A31, 0x6022D2) and change nothing.

6. THE 3D PROJECTION (x86 0x67C3AE, ARM +0xB2EF98) -- BUILD 567
================================================================
BUILD 566 made the map and the units agree horizontally but not vertically:
on hardware the units slid up/down against the map as the camera moved.
The engine builds the projection in 0x67C3AE with _11 = h/w of the game
viewport (440/640, unchanged) and _22 = -1.0 (x86 `mov [edx+0x14],
0xBF800000`), and the driver maps that NDC through its rect AND its TL
matrix. Stock: rect 660 rows x _22 0.9167 = 605 rows for the 3D, and the map
(TL) 440 game px x 1.5 x 0.9167 = 605 rows -- together. BUILD 566's full
rect and identity matrix gave the map 660 rows but the 3D 720: units moved
1.09x as far vertically as the ground under them.

So while the engine mode is Fort Condor (port mode 13, *[0x12CE1F8]) the
projection's _22 is -440/480 instead of -1.0: 720 x 0.9167 = 660 rows, the
map's own. One cave on the constant; every other mode takes the stock word.
"""
from __future__ import annotations

import os
import struct

import a64 as A
from ff7nx_analog_cave import Asm

ENV = 'SEVENTH_NX_CONDOR'
UNCROP_ENV = 'SEVENTH_NX_CONDOR_UNCROP'
VERSION = 'condor-567'

TRANSLATE = 0x10FC3A0
REGFILE_GOT = 0x12CE2B0

MX = 54                       # PSX units each side: ceil((427 - 320) / 2)
MY = 10                       # PSX units top/bottom: (480 - 440) / 2 / 2
UNIT_PAD = 70                 # MX + a unit's body

BLIT_FN = (0x60A160, 0x919230)
BLIT_SITE = 0x919230
BLIT_STOCK = 0xFC1A0FE8                     # str d8, [sp, #-0x60]!
BLIT_BACK = 0x919234
BLIT_ANCHORS = (
    (0x919234, 0xA90167FA, 'stp x26, x25, [sp, #0x10]'),
    (0x919250, 0xF9415AB5, 'ldr x21, [x21, #0x2b0]   register file'),
    (0x91927C, 0x79400008, 'ldrh w8, [x0]   alpha = [ebp+0x10]'),
)

CLEAR_WORDS = (
    (0x8BE624, 0xB900001F, 0xB9000014),     # str wzr,[x0] -> str w20,[x0]
)
CLEAR_ANCHORS = (
    (0x8BE55C, 0x320003F4, 'mov w20, #1'),
    (0x8BE5FC, 0xB9000014, 'str w20, [x0]   push 1'),
    (0x8BE610, 0xB9000014, 'str w20, [x0]   push 1'),
    (0x8BE634, 0x94070ACF, 'bl x86 0x66064A   the clear'),
)

DRV_SETVIEWPORT = 0x10D6760               # gfx_drv_setviewport(w0..w3)
VIEWPORT_SITE = 0x8BCAD8
VIEWPORT_STOCK = 0x294226A8                 # ldp w8, w9, [x21, #0x10]
VIEWPORT_BACK = 0x8BCADC
VIEWPORT_ANCHORS = (
    (0x8BC754, 0x52812713, 'mov w19, #0x938'),
    (0x8BC758, 0x72A018D3, 'movk w19, #0xc6, lsl #16   w19 = 0xC60938'),
    (0x8BCAD4, 0x94071223, 'bl x86 0x66067A   set viewport'),
    (0x8BCAE0, 0x5103F120, 'sub w0, w9, #0xfc   next x86 insn'),
    (0x10D6760, 0x90000FC8, 'gfx_drv_setviewport: adrp x8, 0x12ce000'),
    (0x10D677C, 0x1E230045, 'gfx_drv_setviewport: ucvtf s5, w2'),
)
# BUILD 565's argument rewrite; recognised so a sdout that carries it is
# refused rather than stacked on.
OLD_VIEWPORT_WORDS = ((0x8BCA44, 0xB9400014, 0x52803C14),
                      (0x8BCA8C, 0xB9400014, 0x52800014))

PROJ_SITE = 0xB2EF98
PROJ_STOCK = 0x52B7F008                     # mov w8, #0xbf800000  (_22 = -1)
PROJ_BACK = 0xB2EF9C
PROJ_ANCHORS = (
    (0xB2EF8C, 0x11005100, 'add w0, w8, #0x14   &matrix._22'),
    (0xB2EF9C, 0xB9000008, 'str w8, [x0]'),
    (0xB2EFBC, 0x32091BE8, 'mov w8, #0x3f800000   _33 = 1.0 (next store)'),
)
MODE_SLOT = 0x12CE1F8                       # -> the port's engine mode (u32)
CONDOR_MODE = 13
PROJ_22 = struct.unpack('<I', struct.pack('<f', -440.0 / 480.0))[0]

CAM_SITE = 0x9181F0
CAM_STOCK = 0xF81D0FF5                      # str x21, [sp, #-0x30]!
CAM_BACK = 0x9181F4
CAM_ANCHORS = (
    (0x9181F4, 0xA9014FF4, 'stp x20, x19, [sp, #0x10]'),
    (0x918220, 0x52816013, 'mov w19, #0xb00'),
    (0x918224, 0x72A018D3, 'movk w19, #0xc6, lsl #16   0xC60B00'),
    (0x918238, 0x11233260, 'add w0, w19, #0x8cc   0xC613CC'),
)
CAM_X, CAM_Y = 0xC60B00, 0xC60B04
CURSOR_X, CURSOR_Y = 0xCBCCC0, 0xCBCCC2
STOCK_CAM_X_MAX, STOCK_CAM_Y_MAX = 0xC0, 0x324
CURSOR_X_MAX = STOCK_CAM_X_MAX + 320                    # 512, the stock limit
CURSOR_Y_MAX = STOCK_CAM_Y_MAX + 0xCC                   # 1008, likewise
COND_LT, COND_GT = 11, 12


def camera_limits(my):
    """((addr, lo, hi), ...) the camera cave enforces."""
    return ((CAM_X, MX, STOCK_CAM_X_MAX - MX),
            (CAM_Y, my, STOCK_CAM_Y_MAX - my),
            (CURSOR_X, 0, CURSOR_X_MAX),
            (CURSOR_Y, 0, CURSOR_Y_MAX))

UNIT_X_WORDS = (
    (0x8F9A2C, 0x11050109, A.add_imm(9, 8, 0x140 + UNIT_PAD)),
    (0x8F9A94, 0x79400008, A.ldrh(9, 0)),
    (0x8F9A98, 0x790012C8, A.sub_imm(9, 9, UNIT_PAD)),
    (0x8F9A9C, 0x2940A6C8, A.ldr(8, 22, 4)),
)
UNIT_Y_WORDS = (
    (0x8F9954, 0x1103C109, A.add_imm(9, 8, 0xF0 + 2 * MY)),
)
UNIT_ANCHORS = (
    (0x8F9A10, 0x51003274, 'sub w20, w19, #0xc   0xC613CC camx'),
    (0x8F9A88, 0x51236260, 'sub w0, w19, #0x8d8  0xC60B00'),
    (0x8F9A8C, 0x2900FEC8, 'stp w8, wzr, [x22, #4]   ecx = x, edx = 0'),
    (0x8F9AA0, 0x4B09010A, 'sub w10, w8, w9   x - camx'),
    (0x8F9AC4, 0x54003761, 'b.ne  (x < camx: skip)'),
    (0x8F9A64, 0x35003A68, 'cbnz  (x > camx+320: skip)'),
    (0x8F9950, 0x2940A2CA, 'ldp w10, w8, [x22, #4]   y, camy'),
)


def _on(env, name, default='1'):
    v = (os.environ if env is None else env).get(name, default).strip().lower()
    return v not in ('0', 'off', 'no', 'false', 'stock')


def enabled(env=None):
    return _on(env, ENV)


def wide_enabled(env=None):
    """The widening only makes sense on a 16:9 build (ff7nx_minifade's gate)."""
    if not enabled(env):
        return False
    try:
        import ff7nx_minifade
        return ff7nx_minifade.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def uncrop_enabled(env=None):
    return wide_enabled(env) and _on(env, UNCROP_ENV)


def _w(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _put_words(text, words, anchors, what):
    t = bytes(text)
    bad = ['%s anchor +%#x (%s) holds %08X' % (what, va, name, _w(t, va))
           for va, want, name in anchors if _w(t, va) != want]
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


def build_blit(my):
    """Cave at the head of 0x60A160: widen src/dst in place, replay, return."""
    fields = ((4, 0, -MX), (4, 4, -my), (4, 8, MX), (4, 12, my),
              (8, 0, -2 * MX), (8, 4, -2 * my))

    def build(at, addr):
        a = Asm(at, addr)
        a.emit(A.stp64_pre(29, 30, 31, -0x20))
        a.emit(A.stp64_off(19, 20, 31, 0x10))
        a.emit(A.adrp(19, a.pc(), REGFILE_GOT & ~0xFFF))
        a.emit(A.ldr64(19, 19, REGFILE_GOT & 0xFFF))
        arg = None
        for slot, off, delta in fields:
            if delta == 0:
                continue
            if slot != arg:
                # w20 = the guest rect pointer, argument `slot` (esp + slot;
                # esp + 0 is the return address the call pushed)
                a.emit(A.ldr(0, 19, 0x10))
                a.emit(A.add_imm(0, 0, slot))
                a.emit(A.bl(a.pc(), TRANSLATE))
                a.emit(A.ldr(20, 0))
                arg = slot
            if off:
                a.emit(A.add_imm(0, 20, off))
            else:
                a.emit(A.mov_reg(0, 20))
            a.emit(A.bl(a.pc(), TRANSLATE))
            a.emit(A.ldr(8, 0))
            a.emit(A.add_imm(8, 8, delta) if delta > 0
                   else A.sub_imm(8, 8, -delta))
            a.emit(A.str_(8, 0))
        a.emit(A.ldp64_off(19, 20, 31, 0x10))
        a.emit(A.ldp64_post(29, 30, 31, 0x20))
        a.emit(BLIT_STOCK)
        a.b('back')
        a.lab['back'] = BLIT_BACK
        return a.resolve()
    return build


def patch_blit(text, place, my):
    t = bytes(text)
    bad = ['blit anchor +%#x (%s) holds %08X' % (va, what, _w(t, va))
           for va, want, what in BLIT_ANCHORS if _w(t, va) != want]
    if _w(t, BLIT_SITE) != BLIT_STOCK:
        bad.append('blit site +%#x holds %08X' % (BLIT_SITE, _w(t, BLIT_SITE)))
    if bad:
        raise ValueError('; '.join(bad))
    entry, placed = place(build_blit(my))
    for va, word in placed.items():
        struct.pack_into('<I', text, va, word)
    written = dict(placed)
    word = A.b(BLIT_SITE, entry)
    struct.pack_into('<I', text, BLIT_SITE, word)
    written[BLIT_SITE] = word
    return written


def _movw(a, rd, v):
    a.emit(A.movz(rd, v & 0xFFFF))
    if v >> 16:
        a.emit(A.movk_hi(rd, v >> 16))


def build_camera(my):
    def build(at, addr):
        a = Asm(at, addr)
        a.emit(A.stp64_pre(29, 30, 31, -0x10))
        for va, lo, hi in camera_limits(my):
            _movw(a, 0, va)
            a.emit(A.bl(a.pc(), TRANSLATE))
            a.emit(A.ldrsh(8, 0))
            _movw(a, 9, lo)
            a.emit(A.cmp_reg(8, 9))
            a.emit(A.csel(8, 9, 8, COND_LT))
            _movw(a, 9, hi)
            a.emit(A.cmp_reg(8, 9))
            a.emit(A.csel(8, 9, 8, COND_GT))
            a.emit(A.strh(8, 0))
        a.emit(A.ldp64_post(29, 30, 31, 0x10))
        a.emit(CAM_STOCK)
        a.b('back')
        a.lab['back'] = CAM_BACK
        return a.resolve()
    return build


def build_projection():
    def build(at, addr):
        a = Asm(at, addr)
        a.emit(PROJ_STOCK)                              # w8 = -1.0f
        a.emit(A.adrp(9, a.pc(), MODE_SLOT & ~0xFFF))
        a.emit(A.ldr64(9, 9, MODE_SLOT & 0xFFF))
        a.emit(A.ldr(9, 9))
        a.emit(A.cmp_imm(9, CONDOR_MODE))
        a.bcond('out', 1)                               # b.ne: stock
        _movw(a, 8, PROJ_22)
        a.label('out')
        a.b('back')
        a.lab['back'] = PROJ_BACK
        return a.resolve()
    return build


def build_viewport():
    def build(at, addr):
        a = Asm(at, addr)
        a.emit(A.movz(0, 0))
        a.emit(A.movz(1, 0))
        a.emit(A.movz(2, 640))
        a.emit(A.movz(3, 480))
        a.emit(A.bl(a.pc(), DRV_SETVIEWPORT))
        a.emit(VIEWPORT_STOCK)
        a.b('back')
        a.lab['back'] = VIEWPORT_BACK
        return a.resolve()
    return build


def _hook(text, place, site, stock, anchors, build, what):
    t = bytes(text)
    bad = ['%s anchor +%#x (%s) holds %08X' % (what, va, name, _w(t, va))
           for va, want, name in anchors if _w(t, va) != want]
    if _w(t, site) != stock:
        bad.append('%s site +%#x holds %08X' % (what, site, _w(t, site)))
    if bad:
        raise ValueError('; '.join(bad))
    entry, placed = place(build)
    for va, word in placed.items():
        struct.pack_into('<I', text, va, word)
    written = dict(placed)
    word = A.b(site, entry)
    struct.pack_into('<I', text, site, word)
    written[site] = word
    return written


def patch_text(text, place, wide=True, uncrop=True):
    written = {}
    written.update(_put_words(text, CLEAR_WORDS, CLEAR_ANCHORS, 'clear'))
    if not wide:
        return written
    written.update(patch_blit(text, place, MY if uncrop else 0))
    written.update(_put_words(text, UNIT_X_WORDS, UNIT_ANCHORS, 'unit x'))
    my = MY if uncrop else 0
    written.update(_hook(text, place, CAM_SITE, CAM_STOCK, CAM_ANCHORS,
                         build_camera(my), 'camera'))
    if uncrop:
        t = bytes(text)
        if any(_w(t, va) != st for va, st, _n in OLD_VIEWPORT_WORDS):
            raise ValueError('BUILD 565 viewport words present -- rebuild '
                             'from the stock module')
        written.update(_hook(text, place, VIEWPORT_SITE, VIEWPORT_STOCK,
                             VIEWPORT_ANCHORS, build_viewport(), 'viewport'))
        written.update(_hook(text, place, PROJ_SITE, PROJ_STOCK,
                             PROJ_ANCHORS, build_projection(), 'projection'))
        written.update(_put_words(text, UNIT_Y_WORDS, UNIT_ANCHORS, 'unit y'))
    return written


def apply_to_nso(src, dest, log=lambda *_: None, stock=None, wide=None,
                 uncrop=None):
    import ff7nx_audio_cave as AC
    import ff7nx_deadspace as DS
    with open(src, 'rb') as fh:
        blob = fh.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    wide = wide_enabled() if wide is None else wide
    uncrop = (uncrop_enabled() if uncrop is None else uncrop) and wide
    lo, hi = DS.part(src, 'condor', stock)
    bump = DS.Bump(lo, hi)

    def place(build):
        before = dict(bump.placed)
        e = bump.put(build)
        return e, {k: v for k, v in bump.placed.items() if k not in before}
    if wide:
        log('  map-window cave in the dead-space part +0x%X..+0x%X' % (lo, hi))
    written = patch_text(text, place, wide, uncrop)
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, 0)
    chk_segs, _chk = AC.segments(out)
    assert chk_segs[0][2] == segs[0][2]
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as fh:
        fh.write(out)
    return {'words': len(written), 'wide': wide, 'uncrop': uncrop}
