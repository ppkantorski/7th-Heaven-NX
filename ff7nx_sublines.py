#!/usr/bin/env python3
"""
ff7nx_sublines.py -- BUILD 552. GL_LINES drawn two pixels wide.

WHY
===
The submarine's grid (build 551), its heading line and its target boxes are
the only GL_LINES the game draws. The port draws them 1 px wide, no line
smoothing, then the whole frame goes through FXAA (the 'hd' set here, the
port's own otherwise) and a scale to the output. A 1 px, red-on-dark line is
exactly what FXAA and a non-integer rescale break into dashes: on hardware
the grid read as "too thin, pixels dropped". The port imports no
glLineWidth (nor eglGetProcAddress), so the width cannot be asked for.

WHAT THIS DOES
==============
The port's two primitive submitters are the only callers of glDrawElements
and glDrawArrays in the module:

    +0x1137228  bl glDrawElements   (mode w0, count w1, 0x1403, indices x3)
    +0x11371BC  bl glDrawArrays     (mode w0, first w1, count w2)

Each `bl` is pointed at a small dispatcher. Anything that is not GL_LINES
(mode 1) goes straight on to the real stub with every argument register and
LR untouched -- a `br` through x16/x17, the intra-procedure scratch pair the
PLT stub itself clobbers. GL_LINES goes to a body that draws the same
primitives three times:

    glGetIntegerv(GL_VIEWPORT, v)
    draw                      at the viewport as it was
    glViewport(v.x+1, v.y)    draw     one pixel right
    glViewport(v.x, v.y+1)    draw     one pixel up
    glViewport(v)             restored exactly

which is a 2 px line in every direction. Lines are opaque (the flatline
polygon set is created with blend mode 4, none), so the overlap of the three
copies is invisible; depth-test and every other state are left as they were.
The viewport is read back from GL, not from the port's state cache, and put
back to that same value, so the cache never disagrees with GL.

Both caves are branch-free apart from `bl`/`br` (ff7nx_cave.ANY_SPAN).

Env SEVENTH_NX_LINE_WIDTH=1 leaves the lines stock.

BUILD 553 -- THE GRID'S DEPTH PULL
==================================
The grid lines are the edges of the floor cells the terrain draws (same
projected z), and the floor is translucent, deferred, and drawn AFTER the
lines: wherever the floor's depth wins, the line is washed out under it --
the dashes hardware showed on every blue surface, worse on the two nudged
copies (a one-pixel shift on a grazing floor moves the floor's depth a lot).

Build 552 pulled z in by a fixed 512 float ulps. The projection is FF7's
(0x67CCDE: fov 45, near 3, far 65536), so z = A - B/d and 1 - z ~ n/d: a
fixed ulp count is a fixed WORLD distance only at one range -- ~40 units at
d = 2000 but ~0.1 at d = 100, right where the floor is nearest and
steepest. The pull is now RELATIVE in distance:

    z' = z * (1 + k) - k        <=>   1 - z' = (1 - z)(1 + k)
                                <=>   d' ~ d / (1 + k)

i.e. every grid vertex is drawn k (default 2 %) nearer the camera than it
is. A rock still hides the grid behind it unless the grid is within 2 % of
the rock's own distance -- which is the grid ON the rock.

Done in one shared cave, reached by `bl` from the 16 line-vertex z sites
(ff7nx_subgrid.ZSITES; the word replaced is the dead emulated-register copy
of z). d0/d1 are saved and restored; w9 is dead there (the translator call
that follows clobbers it).

Env SEVENTH_NX_SUB_GRID_BIAS = pull in percent (default 2; 0 = none).

BUILD 555 -- THE SUBMARINE LIMITER, FFNx STYLE
==============================================
x86 0x78C9E1 (ARM +0x1037180) measures its period from the start of the
LOGIC step (0x77E01F marks 0xE74348), and the draw + present run after the
wait, so every frame is period + draw: 33.3 + ~3.7 ms stock (27 FPS), and
with the grid's ~9.7 ms of draw, 25 FPS -- the whole game (turning,
torpedoes, enemies) at 83 % speed. Build 551's divisor 33 only moved it.

FFNx replaces this function (ff7_limit_fps, MODE_SUBMARINE): every call
flips the draw/logic status 0xE73F18 and waits until 1/60 s after the
previous call returned. The loop calls it twice per pass (before the logic,
before the draw), so logic and draw are each given their own 1/60 slot on a
fixed cadence: 30 logic steps and 30 draws a second, whatever the draw
costs up to 16.7 ms. This cave is that, in place of the translated body:

    status = !status
    frame = countspersecond / 60          (game_obj +0x30, the port's own)
    do now = clock() while now - last < frame     (clock: +0x9CD0, the
    last = now                                     same speed-hacked timer)
    esp += 4                              (the x86 `ret`)

`last` lives in guest 0xE74338, the stock limiter's own scratch clock,
which nothing else reads. The loop's backward branch is a computed `br`
(adrp/add/csel), so the cave is branch-free in the `b.cond` sense and can
take any padding hole. Env SEVENTH_NX_SUB_PACE=0 keeps the stock limiter.
"""
from __future__ import annotations

import os
import struct

import a64 as A
import ff7nx_cave

ENV = 'SEVENTH_NX_LINE_WIDTH'

STUB_DRAW_ELEMENTS = 0x1152310
STUB_DRAW_ARRAYS = 0x1152300
STUB_GET_INTEGERV = 0x11520B0
STUB_VIEWPORT = 0x11521B0
GL_LINES = 1
GL_VIEWPORT = 0x0BA2

# (hook site, stub it calls) -- the only two draw calls in the module
SITES = ((0x1137228, STUB_DRAW_ELEMENTS),
         (0x11371BC, STUB_DRAW_ARRAYS))

ANCHORS = (
    (0x1137210, 0xF0000528),   # adrp x8, primitive map
    (0x1137214, 0x9107F108),   # add  x8, x8, #0x1fc
    (0x113721C, 0xB8735900),   # ldr  w0, [x8, w19, uxtw #2]   mode
    (0x1137220, 0x52828062),   # mov  w2, #0x1403
    (0x11371B4, 0xB8735900),   # ldr  w0, [x8, w19, uxtw #2]   mode
    (0x11371B8, 0x2A1503E2),   # mov  w2, w21
    (0x11DE1FC, None),         # the map itself, checked below
)
PRIM_MAP = (0, 0, 1, 3, 4, 6, 5)     # GL_POINTS, POINTS, LINES, ...


BIAS_ENV = 'SEVENTH_NX_SUB_GRID_BIAS'
DEFAULT_BIAS_PCT = 2.0


def bias_pct(env=None):
    raw = (os.environ if env is None else env).get(BIAS_ENV, '').strip()
    try:
        v = float(raw) if raw else DEFAULT_BIAS_PCT
    except ValueError:
        v = DEFAULT_BIAS_PCT
    return v if 0.0 <= v <= 20.0 else DEFAULT_BIAS_PCT


def zsites():
    import ff7nx_subgrid
    return ff7nx_subgrid.ZSITES


def build_zbias(k):
    """z (float bits in w23) -> z*(1+k) - k, in place. d0/d1 preserved."""
    one_k = struct.unpack('<I', struct.pack('<f', 1.0 + k))[0]
    kk = struct.unpack('<I', struct.pack('<f', k))[0]

    def build(at, addr):
        w = []
        emit = w.append
        emit(0x6DBF07E0)                   # stp d0, d1, [sp, #-16]!
        emit(A.fmov_s_from_w(0, 23))       # s0 = z
        emit(A.movz(9, one_k & 0xFFFF))
        emit(A.movk_hi(9, one_k >> 16))
        emit(A.fmov_s_from_w(1, 9))        # s1 = 1 + k
        emit(A.fmul_s(0, 0, 1))
        emit(A.movz(9, kk & 0xFFFF))
        emit(A.movk_hi(9, kk >> 16))
        emit(A.fmov_s_from_w(1, 9))        # s1 = k
        emit(A.fsub_s(0, 0, 1))
        emit(A.fmov_w_from_s(23, 0))       # w23 = z'
        emit(0x6CC107E0)                   # ldp d0, d1, [sp], #16
        emit(A.ret())
        return w
    return build


PACE_ENV = 'SEVENTH_NX_SUB_PACE'
PACE_SITE = 0x1037180                 # translated entry of x86 0x78C9E1
PACE_STOCK = 0xFC1B0FE8               # str d8, [sp, #-0x50]!
# BUILD 556 / 562. The port's flip() has its OWN 1/30 s wait for game modes
# 10, 12 and 14 (+0x10DA898: mode mask 0x5400; wait on +0x9CD0 from the
# previous flip). Those are the PORT's mode numbers, set by the native
# set_mode at +0x10F3D00 -- NOT the x86 ones (0xCBF9DC). Its callers
# (measured, BUILD 562): chocobo race init 0x76D597 -> 9, snowboard 0x722C10
# -> 10, SUBMARINE 0x77D030 -> 11, COASTER 0x5E8D49 -> 12, condor 0x5F47B5
# -> 13, highway 0x650310 -> 14. So the wait capped the snowboard, the
# COASTER and the highway at 30; the submarine was never in the mask (BUILD
# 556 read bit 10 as the submarine -- wrong; its 30 came from the game's own
# limiter, which the pace cave replaced in 555/557).
#
# 556 cleared bit 10 (snowboard). BUILD 562 also clears bit 12: the coaster
# steps its world in 60 Hz ticks from the elapsed time (x86 0x5E8F9B, catch-
# up ticks), exactly like the snowboard, so without the wait it presents at
# the display rate at the same game speed -- FFNx runs both at 60. The
# highway (14) keeps the wait. SEVENTH_NX_MINIGAME_FLIP=0 restores 0x5400.
FLIP_ENV = 'SEVENTH_NX_MINIGAME_FLIP'
FLIP_MASK = (0x10DA8B0, 0x528A8009, 0x52880009)   # mov w9, #0x5400 -> #0x4000
PORT_MODES = {9: 'chocobo', 10: 'snowboard', 11: 'submarine', 12: 'coaster',
              13: 'condor', 14: 'highway'}
FLIP_ANCHORS = (
    (0x10DA8A8, 0x7100391F),          # cmp w8, #0xe
    (0x10DA8AC, 0x1AC82128),          # lsl w8, w9, w8
    (0x10DA8B4, 0x0A090108),          # and w8, w8, w9
    (0x10DA8B8, 0x7A409904),          # ccmp w8, #0, #4, ls
    (0x10DA8BC, 0x54000200),          # b.eq past the wait
)
PACE_ANCHORS = (
    (0x10371A0, 0xF9415AD6),          # ldr x22, [x22, #0x2b0]  reg file
    (0x1037B6C, 0x97BF4A79),          # bl +0xA550 before its ret
    (0x10DA8C8, 0xF941F508),          # flip: ldr x8, [x8, #0x3e8] game obj
    (0x10DA8D0, 0xFD401900),          # flip: ldr d0, [x8, #0x30] cps
    (0x10DA8E8, 0x97BCBCFA),          # flip: bl +0x9CD0 clock
)
CLOCK = 0x9CD0
TRANSLATE = 0x10FC3A0
SYNC = 0xA550
G_STATUS = 0xE73F18
G_LAST = 0xE74338


def flip_enabled(env=None):
    v = (os.environ if env is None else env).get(FLIP_ENV, '1').strip().lower()
    return v not in ('0', 'off', 'no', 'false', 'stock')


def pace_enabled(env=None):
    v = (os.environ if env is None else env).get(PACE_ENV, '1').strip().lower()
    return v not in ('0', 'off', 'no', 'false', 'stock')


def _movz64(rd, imm, hw):
    return 0xD2800000 | (hw << 21) | ((imm & 0xFFFF) << 5) | rd


def _movk64(rd, imm, hw):
    return 0xF2800000 | (hw << 21) | ((imm & 0xFFFF) << 5) | rd


def build_pace(fps=60.0):
    inv = struct.unpack('<Q', struct.pack('<d', 1.0 / fps))[0]

    def build(at, addr):
        w = []
        emit = w.append
        pc = lambda: addr(len(w))
        emit(A.stp64_pre(29, 30, 31, -0x30))
        emit(_add_sp(29, 0))
        emit(A.stp64_off(19, 20, 31, 0x10))
        emit(A.stp64_off(21, 22, 31, 0x20))
        # status = !status
        emit(A.movz(0, G_STATUS & 0xFFFF))
        emit(A.movk_hi(0, G_STATUS >> 16))
        emit(A.bl(pc(), TRANSLATE))
        emit(A.ldr(8, 0, 0))
        emit(A.cmp_imm(8, 0))
        emit(0x1A9F17E8)                          # cset w8, eq
        emit(A.str_(8, 0, 0))
        # x19 = &last
        emit(A.movz(0, G_LAST & 0xFFFF))
        emit(A.movk_hi(0, G_LAST >> 16))
        emit(A.bl(pc(), TRANSLATE))
        emit(A.mov_reg64(19, 0))
        # x20 = countspersecond / fps
        emit(A.adrp(8, pc(), 0x12CE000))
        emit(A.ldr64(8, 8, 0x3E8))
        emit(A.ldr64(8, 8, 0))
        emit(0xFD401900)                          # ldr d0, [x8, #0x30]
        emit(_movz64(9, inv, 0))
        emit(_movk64(9, inv >> 16, 1))
        emit(_movk64(9, inv >> 32, 2))
        emit(_movk64(9, inv >> 48, 3))
        emit(0x9E670121)                          # fmov d1, x9
        emit(0x1E610800)                          # fmul d0, d0, d1
        emit(0x9E790014)                          # fcvtzu x20, d0
        # loop: x0 = now; if now - last < frame: again
        loop = len(w)
        emit(A.bl(pc(), CLOCK))
        emit(A.ldr64(8, 19, 0))
        emit(A.sub_reg64(9, 0, 8))
        emit(0xEB14013F)                          # cmp x9, x20
        emit(A.adrp(16, pc(), addr(loop)))
        emit(A.add_imm64(16, 16, addr(loop) & 0xFFF))
        n_done = len(w) + 4                       # index of the word after br
        emit(A.adrp(17, pc(), addr(n_done)))
        emit(A.add_imm64(17, 17, addr(n_done) & 0xFFF))
        emit(_csel64(16, 16, 17, 3))              # csel x16, x16, x17, lo
        emit(_br(16))
        assert len(w) == n_done
        # BUILD 556: a DEADLINE cadence. FFNx sets last = now, which loses
        # the timer's overshoot on every call and drifts the frame out of
        # its slot. On time: last += frame (exact 1/60 steps). Late by a
        # whole frame or more: last = now (no catch-up burst).
        emit(0xEB14053F)                          # cmp x9, x20, lsl #1
        emit(0x8B14010A)                          # add x10, x8, x20
        emit(_csel64(10, 10, 0, 3))               # lo ? last+frame : now
        emit(A.str64(10, 19, 0))                  # last = x10
        # esp += 4  (the x86 ret)
        emit(A.adrp(22, pc(), 0x12CE000))
        emit(0xF9415AD6)                          # ldr x22, [x22, #0x2b0]
        emit(A.ldr(8, 22, 0x10))
        emit(A.add_imm(8, 8, 4))
        emit(A.str_(8, 22, 0x10))
        emit(A.bl(pc(), SYNC))
        emit(A.ldp64_off(21, 22, 31, 0x20))
        emit(A.ldp64_off(19, 20, 31, 0x10))
        emit(A.ldp64_post(29, 30, 31, 0x30))
        emit(A.ret())
        return w
    return build


def enabled(env=None):
    raw = (os.environ if env is None else env).get(ENV, '2').strip().lower()
    return raw not in ('1', '0', 'off', 'no', 'false', 'stock')


def _stub_ok(text, va):
    """adrp x16 / ldr x17,[x16,#..] / add x16 / br x17 -- a PLT stub."""
    w = [struct.unpack_from('<I', text, va + 4 * k)[0] for k in range(4)]
    return ((w[0] & 0x9F00001F) == 0x90000010 and
            (w[1] & 0xFFC003FF) == 0xF9400211 and
            (w[2] & 0xFFC003FF) == 0x91000210 and w[3] == 0xD61F0220)


def check_text(text, rodata=None):
    bad = []
    for va, want in ANCHORS:
        if want is None:
            continue
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            bad.append('anchor +%#x holds %08X, expected %08X' % (va, got, want))
    for stub in (STUB_DRAW_ELEMENTS, STUB_DRAW_ARRAYS, STUB_GET_INTEGERV,
                 STUB_VIEWPORT):
        if not _stub_ok(text, stub):
            bad.append('+%#x is not a PLT stub' % stub)
    return bad


def state_text(text):
    got = [struct.unpack_from('<I', text, va)[0] for va, _ in SITES]
    stock = [A.bl(va, stub) for va, stub in SITES]
    if got == stock:
        return 'stock'
    if all((g & 0xFC000000) == 0x94000000 for g in got):
        return 'patched'
    return 'unknown'


# ------------------------------------------------------------------ encoders
def _ldp_w(rt, rt2, rn, imm):          # LDP Wt, Wt2, [Xn, #imm]
    return 0x29400000 | (((imm >> 2) & 0x7F) << 15) | (rt2 << 10) | (rn << 5) | rt


def _add_sp(rd, imm):                  # ADD Xd, SP, #imm
    return 0x91000000 | (imm << 10) | (31 << 5) | rd


def _csel64(rd, rn, rm, cond):
    return 0x9A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd


def _br(rn):
    return 0xD61F0000 | (rn << 5)


def _cmp_w_imm(rn, imm):
    return 0x7100001F | (imm << 10) | (rn << 5)


EQ = 0


def build_body(stub):
    """The GL_LINES path: three draws, viewport nudged, then restored."""
    def build(at, addr):
        pc = lambda i: addr(i)
        w = []
        def emit(x): w.append(x)
        def bl(target): emit(A.bl(pc(len(w)), target))
        emit(A.stp64_pre(29, 30, 31, -0x40))          # frame, 16-aligned
        emit(_add_sp(29, 0))                          # mov x29, sp
        emit(A.stp64_off(19, 20, 31, 0x10))
        emit(A.stp64_off(21, 22, 31, 0x20))
        emit(A.mov_reg(19, 0))                        # mode
        emit(A.mov_reg(20, 1))                        # count / first
        emit(A.mov_reg(22, 2))                        # type  / count
        emit(A.mov_reg64(21, 3))                      # indices
        emit(A.movz(0, GL_VIEWPORT))
        emit(_add_sp(1, 0x30))                        # int v[4] at sp+0x30
        bl(STUB_GET_INTEGERV)

        def draw():
            emit(A.mov_reg(0, 19))
            emit(A.mov_reg(1, 20))
            emit(A.mov_reg(2, 22))
            emit(A.mov_reg64(3, 21))
            bl(stub)

        def viewport(dx, dy):
            emit(_ldp_w(0, 1, 31, 0x30))
            emit(_ldp_w(2, 3, 31, 0x38))
            if dx:
                emit(A.add_imm(0, 0, dx))
            if dy:
                emit(A.add_imm(1, 1, dy))
            bl(STUB_VIEWPORT)

        draw()
        viewport(1, 0)
        draw()
        viewport(0, 1)
        draw()
        viewport(1, 1)             # BUILD 555: the diagonal copy too, a
        draw()                     # full 2x2 pen -- no gaps on slopes
        viewport(0, 0)
        emit(A.ldp64_off(21, 22, 31, 0x20))
        emit(A.ldp64_off(19, 20, 31, 0x10))
        emit(A.ldp64_post(29, 30, 31, 0x40))
        emit(A.ret())
        return w
    return build


def build_dispatch(body_va, stub):
    """mode == GL_LINES ? body : the real stub -- arguments and LR untouched."""
    def build(at, addr):
        w = []
        emit = w.append
        emit(_cmp_w_imm(0, GL_LINES))
        emit(A.adrp(16, addr(len(w)), body_va))
        emit(A.add_imm64(16, 16, body_va & 0xFFF))
        emit(A.adrp(17, addr(len(w)), stub))
        emit(A.add_imm64(17, 17, stub & 0xFFF))
        emit(_csel64(16, 16, 17, EQ))
        emit(_br(16))
        return w
    return build


def patch_text(text, starts, width=True, pct=None, pace=None, place=None,
               flip=None):
    """Install into a bytearray .text. Returns {va: word} written.

    `place(build)` -> (entry, {va: word}) chooses where caves go. Default is
    the padding pool; the build passes the dead-space part (BUILD 557).
    """
    pct = bias_pct() if pct is None else pct
    pace = pace_enabled() if pace is None else pace
    flip = flip_enabled() if flip is None else flip
    bad = check_text(bytes(text))
    if bad:
        raise ValueError('; '.join(bad))
    if width and state_text(bytes(text)) != 'stock':
        raise ValueError('line draw sites are %s, not stock'
                         % state_text(bytes(text)))
    pool = ff7nx_cave.HolePool(bytes(text), starts=starts)
    written = {}
    if place is None:
        def place(build):
            e, pl = ff7nx_cave.emit_laid_out(pool, build,
                                             span=ff7nx_cave.ANY_SPAN)
            return e, pl
    if flip:
        for va, want in FLIP_ANCHORS:
            got = struct.unpack_from('<I', text, va)[0]
            if got != want:
                raise ValueError('flip anchor +%#x holds %08X' % (va, got))
        va, stock_, new_ = FLIP_MASK
        if struct.unpack_from('<I', text, va)[0] != stock_:
            raise ValueError('flip mode mask +%#x is not stock' % va)
        struct.pack_into('<I', text, va, new_)
        written[va] = new_
    if pace:
        for va, want in PACE_ANCHORS:
            got = struct.unpack_from('<I', text, va)[0]
            if got != want:
                raise ValueError('pace anchor +%#x holds %08X, expected %08X'
                                 % (va, got, want))
        got = struct.unpack_from('<I', text, PACE_SITE)[0]
        if got != PACE_STOCK:
            raise ValueError('submarine limiter entry +%#x holds %08X'
                             % (PACE_SITE, got))
        pc_, placed = place(build_pace())
        written.update(placed)
        for va, word in placed.items():
            struct.pack_into('<I', text, va, word)
        pool.img = bytes(text)
        word = A.b(PACE_SITE, pc_)
        struct.pack_into('<I', text, PACE_SITE, word)
        written[PACE_SITE] = word
    if pct > 0:
        for va, stock in zsites():
            got = struct.unpack_from('<I', text, va)[0]
            if got != stock:
                raise ValueError('grid z site +%#x holds %08X, not the stock '
                                 'str w23 (%08X)' % (va, got, stock))
        zc, placed = place(build_zbias(pct / 100.0))
        written.update(placed)
        for va, word in placed.items():
            struct.pack_into('<I', text, va, word)
        pool.img = bytes(text)
        for va, _stock in zsites():
            word = A.bl(va, zc)
            struct.pack_into('<I', text, va, word)
            written[va] = word
    for site, stub in (SITES if width else ()):
        body, placed = place(build_body(stub))
        written.update(placed)
        for va, word in placed.items():
            struct.pack_into('<I', text, va, word)
        pool.img = bytes(text)
        disp, placed = place(build_dispatch(body, stub))
        written.update(placed)
        for va, word in placed.items():
            struct.pack_into('<I', text, va, word)
        pool.img = bytes(text)
        word = A.bl(site, disp)
        struct.pack_into('<I', text, site, word)
        written[site] = word
    return written


def apply_to_nso(src, dest, log=lambda *_: None, width=None, pct=None,
                 pace=None, stock=None, flip=None):
    import ff7nx_audio_cave as AC
    import nxmap
    with open(src, 'rb') as fh:
        blob = fh.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    width = enabled() if width is None else width
    place = None
    if stock is not None:
        # BUILD 557: the dead-space part, contiguous and ours alone.
        import ff7nx_deadspace as DS
        lo, hi = DS.part(src, 'sub', stock)
        bump = DS.Bump(lo, hi)

        def place(build):
            before = dict(bump.placed)
            e = bump.put(build)
            return e, {k: v for k, v in bump.placed.items()
                       if k not in before}
        log('  caves in the dead-space part +0x%X..+0x%X' % (lo, hi))
    written = patch_text(text, set(nxmap.Main(src).arm_starts), width, pct,
                         pace, place, flip)
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, 0)
    chk_segs, chk_raw = AC.segments(out)
    assert chk_segs[0][2] == segs[0][2]
    if width:
        assert state_text(chk_raw[0]) == 'patched'
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as fh:
        fh.write(out)
    return {'words': len(written), 'code': written}
