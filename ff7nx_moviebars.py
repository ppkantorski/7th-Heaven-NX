#!/usr/bin/env python3
r"""
ff7nx_moviebars.py -- paint the FMV's 4:3 margins, last, over the finished frame.

    python3 ff7nx_moviebars.py <exefs/main | sdout> --show
    python3 ff7nx_moviebars.py <exefs/main | sdout> --verify   # executes it
    python3 ff7nx_moviebars.py <exefs/main | sdout> --apply
    python3 ff7nx_moviebars.py <exefs/main | sdout> --revert

=============================================================================
0. THE SYMPTOM, AND THE TWO ATTEMPTS THAT FAILED
=============================================================================
During a field FMV -- the Reactor 1 explosion -- Cloud is drawn OUTSIDE the
4:3 picture: sword and legs hanging into the 16:9 margins and below the
bottom edge. The margins are black (correct); the model paints over them.

    ff7nx_movieclip   narrowed glScissor for the whole frame.
                      Models clipped, but the margins kept the LAST FIELD
                      FRAME drawn before the FMV. RETIRED.
    ff7nx_moviecull   narrowed field_do_draw_3d_model's box during playback.
                      Installed, gated, executing -- and models still drew in
                      the margins on all four sides. RETIRED (FINDINGS-91 §9).

`moviecull` could never have worked and the reason is structural: the cull is
an early-out on the model's ORIGIN with ~50 units of slack per side. It
removes whole models; it cannot slice one. A model whose origin is legal
paints its whole sprite, overhang included.

=============================================================================
1. WHY THE SCISSOR IS ALSO THE WRONG LEVER -- READ, NOT ASSUMED
=============================================================================
FINDINGS-91 §1.1 said the scissor "eats the clear". That is wrong, and the
module says so:

    +0x11362A0   the clear
    +0x11362C0     ldr x0, [x19, #0x98]
    +0x11362C8     mov w1, wzr
    +0x11362CC     bl  +0x1133EE0        <- scissor_test_set(gl, FALSE)
    +0x11364D8     bl  glClear
    +0x1136580     bl  +0x1133EE0        <- restore, w1 = [x19+0x58]

`+0x1133EE0` is the GL_SCISSOR_TEST toggle -- `mov w0, #0xc11` at +0x1133EF4
names it. The clear DISABLES the scissor test around itself. A narrow scissor
cannot touch it.

What movieclip actually scissored was the PRESENTATION BLIT: the full-screen
quad that copies the finished frame to the back buffer. Narrow that and the
back buffer's margins are never written, so they hold whatever was there
last -- which is exactly the frozen field art that was reported, and why it
was frozen rather than merely wrong.

And a scissor narrowed around a game-level draw would not survive anyway:

    +0x10D9370  gl_load_state  memcpy 0xE8 bytes into current_state, then
                re-derives the clip rect from [obj+0x10..0x1C] --
                driver_state.viewport[4] -- writing it at +0x10D9530/34.

Every deferred draw replays with the viewport captured when it was queued.

FINDINGS-91 §9.3 also proposed hooking "the one caller of
field_do_draw_3d_model". That caller is **not** a draw:

    +0x94A1E0  bl +0x9EC300   inside x86 0x6392BB = field_animate_3d_models
    +0x94A1FC  strb w9, [x21, #0x23]      it stores a FLAG
    +0x94A200  cbz  w8, +0x94A418         and skips the model's ANIMATION

The real per-model draw is `draw_3d_model`, x86 0x6840DA, reached from
`field_draw_everything` (x86 0x63A60B) at +0x9E756C -- a different function,
a different phase of the frame. Hooking the animate pass would have narrowed
the scissor long before anything was drawn. Derived here through FFNx's own
chain against `ff7_en`, every link cross-checked against the address encoded
in its FFNx symbol name:

    main_loop 0x4090E6 -> field_main_loop 0x60E5B7 -> field_sub_6388EE
    -> field_draw_everything 0x63A60B -> draw_3d_model 0x6840DA
    field_main_loop +0xF6 -> field_animate_3d_models 0x6392BB
                    +0x203 -> field_culling_model  0x639252

=============================================================================
2. THE RIGHT LEVER -- THE PORT ALREADY PAINTS ITS LETTERBOX
=============================================================================
FINDINGS-88 established that FF7's letterbox on this port is PAINTED, not
clipped: opaque black quads drawn last, over the finished frame, in the flip
path. That function is `+0x10E0680`, and it has exactly ONE caller:

    +0x10DAF74  bl +0x10E0230      draw the frame
    +0x10DAF78  bl +0x10E0680      <- the bar painter
    +0x10DAF7C  bl +0x10E0A70      the overlay painter

It builds THREE quads, all 4 vertices, 0x20 stride, colour 0xFF000000:

    quad 1   x 0..1        y 0..s2         the TOP bar
    quad 2   x 0..1        y s9..1         the BOTTOM bar
    quad 3   x 0..s0       y 0..1          a LEFT pillar

    s2 = bar_px / screenH   where bar_px = *[[0x12CE460]]
    s9 = 1 - s2
    s0 = (screenW - *[[0x12CE558]]) * 0.5 / screenW

so the space is normalised [0,1] over the whole screen, y downward. That is
confirmed by measurement, not by reading: `bar_px` is `screenH * 16/480` = 24
at 720p, and the bars FINDINGS-88 removed were exactly device rows 0..23 and
696..719 across the full 1280. `ff7nx_letterbox` zeroes the source at
+0x10F3DDC, so today s2 = 0, s9 = 1 and quads 1 and 2 are degenerate.

**Drawn last, over the finished frame, means a bar covers overhang without
clipping anything.** No model disappears; the part of it outside the picture
is painted over. That is what a real letterbox does, and it is what Patrick
described asking for: "the models should disappear UNDER the black".

=============================================================================
3. WHERE THE PICTURE ACTUALLY IS
=============================================================================
Horizontally, from the shader this SD tree ships:

    tlmain_vv.glsl   gl_Position.x *= WS_SCALE

so FF7's 640-unit game space occupies the central WS_SCALE of the screen and
the margins are [0, m] and [1-m, 1] with m = (1 - WS_SCALE)/2. At the shipping
1280x720 preset WS_SCALE is 0.75, m is 0.125, and the margins are columns
0..160 and 1120..1280 -- which is exactly what `ff7nx_moviealign` MEASURED
the movie's own pillarbox to be (cols 159..1120).

Vertically, from the movie quad itself (+0x10DE7C0):

    H = min(video_h * 640 / video_w, 480)
    quad = game (0, Y0) .. (640, Y0 + H)

`ff7nx_moviealign` adds 16 to Y0 so the movie meets the field art at tile
origin 232. Whether it did is READ OUT OF THE MODULE here, not assumed: if
+0x10DE8F0 is a branch, moviealign is installed and Y0 = 16; if it is the
stock `strb w8, [sp]`, Y0 = 0. The bars follow.

    every FMV in this build   1280x896 (38), 640x448 (1), 1276x896 (1)
    all 10:7                  H = 448.0, 448.0, 449.4
    with Y0 = 16              picture = game y 16 .. 464

    top bar     y 0 .. Y0/480
    bottom bar  y (Y0+448)/480 .. 1

The one 1276x896 file is 1.4 game units (2 device rows at 720p) taller than
448 and loses that much under the bottom bar. A future FMV pack shipping
something taller than 448 would lose more; `--show` prints the two edges so
it is checkable rather than silent.

=============================================================================
4. THE CAVE
=============================================================================
Hooked at **+0x10E0A4C**, the first word of `+0x10E0680`'s epilogue -- after
the third quad has been issued and before anything is restored.

At that point, read out of the prologue and the body rather than assumed:

    LIVE and needed    x19  *[0x12CE518]   the transient-vertex-buffer object
                       x20  *[0x12CE510]   the renderer
                       x23  0x3F800000     1.0f, already in a register
                       x29  frame pointer, sp   the staging buffer is sp+8
    FREE               x0-x18, x21, x22, x28, x30
                       (x21/x22/x28/x30 are all restored by the epilogue that
                        follows, so their current values are dead; x26/x27 are
                        the CALLER's and are never touched)

`x28` carries the cave's internal return address. It survives the three calls
because x19-x28 are callee-saved by the AAPCS64, and it cannot collide with
x30, which those calls do clobber.

The staging buffer at sp+8 still holds quad 3 -- nothing between +0x10E09E8
and the hook writes it. Four vertices at stride 0x20; only `.x` and `.y`
differ between bars, so the colour (0xFF000000 at +0x10, and quad 3's is
already black) and everything else are left exactly as the game wrote them:

    v0.x sp+0x08   v0.y sp+0x0C
    v1.x sp+0x28   v1.y sp+0x2C
    v2.x sp+0x48   v2.y sp+0x4C
    v3.x sp+0x68   v3.y sp+0x6C

**The 29-word issue block is COPIED OUT OF THE MODULE, not written here.**
Five words from +0x10E0994/+0x10E09A4/+0x10E09A8/+0x10E09AC/+0x10E09E8 and
the contiguous 24-word run +0x10E09EC..+0x10E0A48, with only the three `bl`s
re-encoded for their new address -- and their TARGETS decoded out of the
source words, so no call address is typed in this file either. HANDOFF-90
§2.5's rule and FINDINGS-91 §4.2's lesson: two anchors hand-encoded from a
listing were both wrong.

    gate   (7)   is_playing, behind three cbz guards
    left  (11)   x 0..m        y 0..1        then `bl Lissue`
    right  (7)   x 1-m..1      y 0..1        then `bl Lissue`
    top   (11)   x 0..1        y 0..ytop     then `bl Lissue`
    bottom (7)   x 0..1        y ybot..1     then `bl Lissue`
    Lout   (2)   the displaced `ldp d9, d8, [sp, #0x190]` and `b +0x10E0A50`
    Lissue(31)   `mov x28, x30`, the copied block, `ret x28`
    ---------
    76 words in reclaimed alignment padding. The 60 FPS tail gap is untouched.

`Lissue` sits AFTER the tail and is reached only by `bl`, and the four bars
reach it that way rather than by `b`+`adr`, for one specific reason: it keeps
**the cave's only `b` the one that returns to the game**. That is exactly the
invariant `ff7nx_camclamp` and `ff7nx_moviecull` walk a chained cave by -- "a
`b` that is not to RETURN_VA is one of ff7nx_cave's run-to-run links, follow
it" -- so `--revert` reclaims the whole footprint with the same proven rule
instead of a new heuristic. `ret x28` rather than `br x28` for the same
reason: RET Xn is what the interpreter that verifies this already models.

The bars are issued in the order left, right, top, bottom precisely so each
one only has to write the fields the previous one did not already leave
correct; `--verify` executes the cave and reads the four rects back out of
the vertex buffer rather than trusting that ordering.

=============================================================================
5. THE GATE, AND THE ONE THING IT DOES NOT DISTINGUISH
=============================================================================
    +0x10F1550  is_movie_playing   ldr x8, [.,#0x7C0]; ldr x8,[x8]
                                   ldr w9, [x8, #0x1FC]
    +0x10F1770  fw_movie_stop      stp xzr, xzr, [x8, #0x1F8]

`[[0x12CE7C0]] + 0x1FC`, the same gate `ff7nx_movieclip` proved on hardware
and `ff7nx_moviecull` shipped. Both indirections are `cbz`-guarded: this
function runs from boot, long before the movie module exists.

**NOT VERIFIED, and named so it is not lost.** FFNx gates on
`*word_CC1638 && !modules_global_object->BGMOVIE_flag` -- it excludes a field
whose BACKGROUND is a movie. This gate does not, because the port's own
`is_playing` is one bit and the BGMOVIE distinction lives on the guest side
(`modules_global_object` 0xCC0D88, BGMOVIE_flag 0xCC0DC2, derived here
through FFNx's chain but not read by the cave -- a guest page-table walk in
the flip path is exactly the kind of new mechanism that should not ride along
with the fix it is not needed for).

The fork that decides it is at **+0x5C90**, `tbz w2, #0` choosing between the
4:3 quad at +0x10DE7C0 and the other movie draw at +0x10E0390. If a BGMOVIE
field comes back with black side bars during normal walking, that is this,
and the answer is a flag set on the +0x10DE7C0 path -- not a wider gate.

Note that the bars can only be WRONG there if the port draws a BGMOVIE
widened. If it draws it through the same 4:3 quad, the margins are already
empty and the bars change nothing except hiding a model that had wandered
into black.

=============================================================================
6. WHAT TO LOOK FOR ON HARDWARE
=============================================================================
Reactor 1 explosion, Cloud at the left edge of the picture:

  * Cloud cut off cleanly at the picture edge, margins black  -> done.
  * Cloud still drawn over the margin  -> the gate did not fire. `--show`
    says whether the cave is in; `ff7nx_status.py` says whether moviecull is
    still in with it.
  * the movie loses a column or row at an edge -> the extents are a pixel
    tight. m and the two y edges are printed by `--show`; they are arithmetic,
    not a fit, so this would mean WS_SCALE or moviealign disagrees with what
    was read at apply time.
  * black side bars during ordinary walking -> §5, the BGMOVIE case.
"""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '7th_heaven_nx')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import a64 as A                                                # noqa: E402
import ff7nx_cave                                              # noqa: E402

MOVIEBARS_ENV = 'SEVENTH_NX_MOVIE_BARS'
SDOUT_MAIN = os.path.join('atmosphere', 'contents',
                          '0100A5B00BDC6000', 'exefs', 'main')

# ------------------------------------------------------------------- sites
PAINTER = 0x10E0680             # the flip-path bar painter, one caller
HOOK = 0x10E0A4C                # its epilogue's first word
RETURN_VA = HOOK + 4

# the five scattered head words of quad 3's issue sequence, in order
ISSUE_HEAD = [0x10E0994, 0x10E09A4, 0x10E09A8, 0x10E09AC, 0x10E09E8]
# and its contiguous tail
ISSUE_TAIL = (0x10E09EC, 0x10E0A48)             # inclusive

MOVIE_QUAD_HOOK = 0x10DE8F0     # ff7nx_moviealign's site; a branch iff applied
MOVIEALIGN_STOCK = 0x390003E8   # strb w8, [sp]
MOVIEALIGN_SHIFT = 16           # game units it adds to the quad's y

PAGE = 0x12CE000
MOVIE_PTR_OFF = 0x7C0
IS_PLAYING_OFF = 0x1FC

# the movie quad's height for every FMV this build ships (all 10:7)
MOVIE_H = 448.0
GAME_H = 480.0

# staging-buffer offsets from sp, read off the quad-3 stores
VX = (0x08, 0x28, 0x48, 0x68)
VY = (0x0C, 0x2C, 0x4C, 0x6C)

GATE_SCRATCH = 10               # x10/w10
CONST_SCRATCH = 9               # w9
LINK = 28                       # x28
ONE_F = 23                      # x23 already holds 0x3F800000

# word indices of the cave's labels; asserted in cave_words()
L_GATE, L_LEFT, L_RIGHT, L_TOP, L_BOTTOM, L_OUT, L_ISSUE = 0, 7, 18, 25, 36, 43, 45
N_WORDS = 76

# Anchors. Each says something different, and each is read out of the module
# rather than typed from a listing.
ANCHORS = [
    # -- the painter is the painter
    (0x10E07D8, 0xF9423108, 'ldr x8, [x8, #0x460]   the bar-height pointer'),
    (0x10E07DC, 0xBD400102, 'ldr s2, [x8]           the per-frame bar height'),
    (0x10DF6CC, 0xF9423108, 'ldr x8, [x8, #0x460]   its only setter'),
    (0x10E07F4, 0x32081FF8, 'mov w24, #-0x1000000   the quads are 0xFF000000'),
    (0x10E07C4, 0x32091BF7, 'mov w23, #0x3f800000   1.0f lives in x23'),
    # -- the staging buffer really is quad 3's, at sp+8
    (0x10E09A4, 0x910023E1, 'add x1, sp, #8         quad 3 stages at sp+8'),
    (0x10E09DC, 0xBD002BE0, 'str s0, [sp, #0x28]    v1.x'),
    (0x10E09E4, 0xBD006BE0, 'str s0, [sp, #0x68]    v3.x'),
    (0x10E09A8, 0x321903E2, 'mov w2, #0x80          4 verts x 0x20'),
    # -- the hook site and what follows it
    (0x10E0A4C, 0x6D5923E9, 'ldp d9, d8, [sp, #0x190]   the displaced word'),
    (0x10E0A50, 0xA95E7BFD, 'ldp x29, x30, [sp, #0x1e0] x30 is still dead'),
    (0x10E0A64, 0x9107C3FF, 'add sp, sp, #0x1f0     the frame is 0x1f0'),
    # -- the gate
    (0x10F1554, 0xF943E108, 'ldr x8, [x8, #0x7c0]   is_movie_playing'),
    (0x10F155C, 0xB941FD09, 'ldr w9, [x8, #0x1fc]   is_playing'),
    (0x10F177C, 0xA91FFD1F, 'stp xzr, xzr, [x8, #0x1f8]   fw_movie_stop'),
    # -- the clear disables the scissor test; see the docstring
    (0x11362CC, 0x97FFF705, 'bl +0x1133ee0          clear: scissor test OFF'),
    (0x1133EF4, 0x52818220, 'mov w0, #0xc11         GL_SCISSOR_TEST'),
]


# --------------------------------------------------------------- primitives
def _f32(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def _fmt(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def _text(path):
    import nso_tool
    return nso_tool.parse_nso(str(path))['segments']['.text']['data']


def w32(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _bl_target(word, va):
    if (word & 0xFC000000) != 0x94000000:
        return None
    imm = word & 0x03FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return va + imm * 4


def _b_target(word, va):
    if (word & 0xFC000000) != 0x14000000:
        return None
    imm = word & 0x03FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return va + imm * 4


# ------------------------------------------------------------ the geometry
def movie_y0(t):
    """
    The movie quad's top edge in game units: 16 if ff7nx_moviealign is
    installed, 0 if it is not. Read out of the module; never assumed.
    """
    got = w32(t, MOVIE_QUAD_HOOK)
    if got == MOVIEALIGN_STOCK:
        return 0
    if _b_target(got, MOVIE_QUAD_HOOK) is not None:
        return MOVIEALIGN_SHIFT
    raise ValueError('+%#09x is %08X -- neither ff7nx_moviealign\'s stock '
                     'word nor a branch into its cave; refusing to guess '
                     'where the movie is' % (MOVIE_QUAD_HOOK, got))


def extents(ws_scale, y0):
    """(m, 1-m, ytop, ybot) as float32 bit patterns, plus the float values."""
    m = (1.0 - ws_scale) / 2.0
    ytop = y0 / GAME_H
    ybot = (y0 + MOVIE_H) / GAME_H
    return {
        'm': m, 'rm': 1.0 - m, 'ytop': ytop, 'ybot': ybot,
        'm_b': _f32(m), 'rm_b': _f32(1.0 - m),
        'ytop_b': _f32(ytop), 'ybot_b': _f32(ybot),
    }


# ------------------------------------------------------------------ the cave
def issue_block(t):
    """
    The 29 words that submit one quad, lifted out of the module.

    Returns a list of (source_va, word). The three `bl`s keep their SOURCE
    address here so the builder can re-encode each one for wherever it lands,
    with the target decoded from the word rather than typed.
    """
    out = [(va, w32(t, va)) for va in ISSUE_HEAD]
    for va in range(ISSUE_TAIL[0], ISSUE_TAIL[1] + 4, 4):
        out.append((va, w32(t, va)))
    if len(out) != 29:
        raise ValueError('issue block is %d words, expected 29' % len(out))
    n_bl = sum(1 for va, w in out if _bl_target(w, va) is not None)
    if n_bl != 4:
        raise ValueError('issue block has %d bl(s), expected 4' % n_bl)
    return out


def cave_words(addr, displaced, ext, block):
    """
    The 76 words, laid out at addr(i).

    `addr(i)` is the REAL address of word i under the scattered padding
    layout, so every branch, cbz and adrp resolves against where it actually
    lands rather than against a pretend contiguous cave.
    """
    g, c, one = GATE_SCRATCH, CONST_SCRATCH, ONE_F
    SP, Z = A.SP, A.WZR
    words = []

    def bar(stores):
        w = list(stores)
        w.append(A.bl(addr(len(words) + len(w)), addr(L_ISSUE)))
        return w

    # ---- gate --------------------------------------------------------- 7
    lout = addr(L_OUT)
    words += [
        A.adrp(g, addr(0), PAGE),
        A.ldr64(g, g, MOVIE_PTR_OFF),
        A.cbz64(g, addr(2), lout),
        A.ldr64(g, g, 0),
        A.cbz64(g, addr(4), lout),
        A.ldr(g, g, IS_PLAYING_OFF),
        A.cbz(g, addr(6), lout),
    ]
    assert len(words) == L_LEFT

    # ---- left bar: x 0..m, y 0..1 ------------------------------------ 11
    words += bar(A.movz_movk(c, ext['m_b']) + [
        A.str_(Z, SP, VX[0]), A.str_(c, SP, VX[1]),
        A.str_(Z, SP, VX[2]), A.str_(c, SP, VX[3]),
        A.str_(Z, SP, VY[0]), A.str_(Z, SP, VY[1]),
        A.str_(one, SP, VY[2]), A.str_(one, SP, VY[3]),
    ])
    assert len(words) == L_RIGHT

    # ---- right bar: x 1-m..1, y left as the left bar set it ----------- 7
    words += bar(A.movz_movk(c, ext['rm_b']) + [
        A.str_(c, SP, VX[0]), A.str_(one, SP, VX[1]),
        A.str_(c, SP, VX[2]), A.str_(one, SP, VX[3]),
    ])
    assert len(words) == L_TOP

    # ---- top bar: x 0..1, y 0..ytop ---------------------------------- 11
    words += bar(A.movz_movk(c, ext['ytop_b']) + [
        A.str_(Z, SP, VX[0]), A.str_(one, SP, VX[1]),
        A.str_(Z, SP, VX[2]), A.str_(one, SP, VX[3]),
        A.str_(Z, SP, VY[0]), A.str_(Z, SP, VY[1]),
        A.str_(c, SP, VY[2]), A.str_(c, SP, VY[3]),
    ])
    assert len(words) == L_BOTTOM

    # ---- bottom bar: x left as the top bar set it, y ybot..1 ---------- 7
    words += bar(A.movz_movk(c, ext['ybot_b']) + [
        A.str_(c, SP, VY[0]), A.str_(c, SP, VY[1]),
        A.str_(one, SP, VY[2]), A.str_(one, SP, VY[3]),
    ])
    assert len(words) == L_OUT

    # ---- tail: fallen into from the bottom bar, jumped to by the gate - 2
    words.append(displaced)
    words.append(A.b(addr(L_OUT + 1), RETURN_VA))
    assert len(words) == L_ISSUE

    # ---- the issue block, copied out of the module ------------------- 31
    words.append(A.add_imm64(LINK, 30, 0))          # mov x28, x30
    for k, (src_va, word) in enumerate(block):
        tgt = _bl_target(word, src_va)
        words.append(word if tgt is None
                     else A.bl(addr(L_ISSUE + 1 + k), tgt))
    words.append(A.ret(LINK))                       # ret x28

    assert len(words) == N_WORDS, 'N_WORDS is %d, body is %d' % (N_WORDS,
                                                                len(words))
    return words


# ------------------------------------------------------------------- state
def cave_state(t):
    """'stock', 'patched', or 'unknown'."""
    got = w32(t, HOOK)
    if got == 0x6D5923E9:                      # ldp d9, d8, [sp, #0x190]
        return 'stock'
    if _b_target(got, HOOK) is not None:
        return 'patched'
    return 'unknown'


def installed(t):
    return cave_state(t) == 'patched'


def check_anchors(t, log=lambda *_: None):
    bad = []
    for va, want, what in ANCHORS:
        if va == HOOK and cave_state(t) == 'patched':
            continue                            # ours; walk() checks it
        got = w32(t, va)
        if got != want:
            bad.append('+%#09x is %08X, expected %08X -- %s'
                       % (va, got, want, what))
    if cave_state(t) == 'unknown':
        bad.append('hook +%#09x is %08X -- neither the stock word nor a '
                   'branch; refusing' % (HOOK, w32(t, HOOK)))
    for b in bad:
        log('  ! ' + b)
    return bad


# ----------------------------------------------------------------- walking
def walk(t):
    """
    The cave's LOGICAL word list: the 76 words as written, with the chaining
    branches ff7nx_cave inserts between padding holes removed.

    The rule is the one `ff7nx_camclamp` and `ff7nx_moviecull` already use,
    and it is exact HERE because the cave was shaped to make it exact: a `b`
    to RETURN_VA is the cave's own return and is part of the logic, and it is
    the ONLY `b` the cave contains, so every other `b` is a run-to-run link.
    """
    entry = _b_target(w32(t, HOOK), HOOK)
    if entry is None:
        return None
    va, out = entry, []
    while len(out) < N_WORDS and va not in [a for a, _ in out]:
        x = w32(t, va)
        b = _b_target(x, va)
        if b is not None and b != RETURN_VA:
            va = b                             # chain link, not logic
            continue
        out.append((va, x))
        va += 4
    return out


def walk_physical(t):
    """
    Every ADDRESS the cave occupies, chaining branches included.

    revert needs the footprint, not the logic: zeroing only the logical words
    leaves link branches behind as live code in someone else's padding, which
    is what makes the next module's allocator skip a usable hole and the next
    apply->revert fail its byte-identity check.
    """
    entry = _b_target(w32(t, HOOK), HOOK)
    if entry is None:
        return []
    va, out, logical = entry, [], 0
    while logical < N_WORDS and va not in out:
        x = w32(t, va)
        out.append(va)
        b = _b_target(x, va)
        if b is not None and b != RETURN_VA:
            va = b
            continue
        logical += 1
        va += 4
    return out


# ------------------------------------------------------------------ planning
def build_patches(img, starts, ws_scale, log=print):
    """{va: word} for the cave and the hook, or None if it cannot be done."""
    img = bytearray(img)
    t = img
    displaced = w32(t, HOOK)
    if displaced != 0x6D5923E9:
        log('  ! hook +%#09x is %08X, not the epilogue word' % (HOOK, displaced))
        return None
    try:
        y0 = movie_y0(t)
    except ValueError as e:
        log('  ! %s' % e)
        return None
    ext = extents(ws_scale, y0)
    block = issue_block(t)

    pool = ff7nx_cave.HolePool(img, starts=starts)
    entry, words = ff7nx_cave.emit_laid_out(
        pool,
        lambda entry_va, addr, _d=displaced, _e=ext, _b=block:
            cave_words(addr, _d, _e, _b),
        span=0x80000)
    words[HOOK] = A.b(HOOK, entry)
    log('  movie margin bars: %d words in padding, entry +%#x' % (N_WORDS, entry))
    log('    movie quad y0 %d game units (%s)'
        % (y0, 'ff7nx_moviealign installed' if y0 else 'moviealign NOT installed'))
    log('    left   x 0.000000 .. %.6f' % ext['m'])
    log('    right  x %.6f .. 1.000000' % ext['rm'])
    log('    top    y 0.000000 .. %.6f' % ext['ytop'])
    log('    bottom y %.6f .. 1.000000' % ext['ybot'])
    return words


def revert_patches(t, log=print):
    """{va: word} that puts the hook back and returns the padding."""
    if not installed(t):
        return {}
    phys = walk_physical(t)
    if phys is None:
        log('  ! the hook is a branch but the cave cannot be walked')
        return None
    logical = [w32(t, va) for va in phys]
    if 0x6D5923E9 not in logical:
        log('  ! the cave does not contain the displaced word; refusing to '
            'guess what to restore')
        return None
    out = {HOOK: 0x6D5923E9}
    for va in phys:
        out[va] = 0
    log('  movie margin bars removed (%d word(s) of padding returned)'
        % len(phys))
    return out


# ---------------------------------------------------------------- emulation
def _external_calls(words_map):
    """
    {target: the cave word that calls it} for every `bl` leaving the cave.

    The copy target -- the transient vertex-buffer allocator the game reaches
    at +0x510 -- is the FIRST of them in the issue block, which is the instant
    the staging buffer is handed over and therefore the instant to read it.
    """
    out = {}
    for va in sorted(words_map):
        tgt = _bl_target(words_map[va], va)
        if tgt is not None and tgt not in words_map:
            out.setdefault(tgt, va)
    return out


def _copy_target(t):
    """The +0x510-equivalent, decoded out of the module rather than typed."""
    return _bl_target(w32(t, ISSUE_HEAD[4]), ISSUE_HEAD[4])


def _cpu_class():
    """
    `arm64emu.Cpu` plus the two forms the copied issue block and the displaced
    word use and the shared interpreter does not decode:

        ldur x4, [x29, #-0x58]     twice, in the copied block
        ldp  d9, d8, [sp, #0x190]  the displaced word, executed on every path

    Subclassed here rather than added to `arm64emu` for the same reason
    `ff7nx_moviecull` subclasses it for `adrp`: every other module's `--verify`
    is a witness to that file's current behaviour, and a new decode belongs to
    the module that needs it until something else does.
    """
    import arm64emu

    class Cpu(arm64emu.Cpu):
        def step(self, w, pc):
            if (w & 0xFFE00C00) in (0xF8400000, 0xB8400000):     # LDUR Xt/Wt
                wide = (w & 0x40000000) != 0
                imm = (w >> 12) & 0x1FF
                if imm & 0x100:
                    imm -= 0x200
                rn, rt = (w >> 5) & 0x1F, w & 0x1F
                base = self.sp if rn == 31 else self.x[rn]
                self.set(rt, self.mem.u(base + imm, 8 if wide else 4),
                         w=not wide)
                return None
            if (w & 0xFFC00000) == 0x6D400000:      # LDP Dt, Dt2, [Xn, #imm]
                imm = (w >> 15) & 0x7F
                if imm & 0x40:
                    imm -= 0x80
                rn = (w >> 5) & 0x1F
                base = self.sp if rn == 31 else self.x[rn]
                self.fp[w & 0x1F] = self.mem.u(base + imm * 8, 8)
                self.fp[(w >> 10) & 0x1F] = self.mem.u(base + imm * 8 + 8, 8)
                return None
            return arm64emu.Cpu.step(self, w, pc)

    return Cpu


def _emu_run(words_map, entry, playing, copy_tgt):
    """
    Execute the cave and read back every rect it submits.

    Returns (captured, exit_pc). `captured` is one 4-vertex tuple per quad,
    sampled at the copy call -- the moment the game takes the staging buffer.
    """
    import arm64emu

    SP = 0x70000000
    MOVIE_PTR, MOVIE_OBJ = 0x12E5070, 0x80000000

    mem = arm64emu.Mem()
    mem.setu(PAGE + MOVIE_PTR_OFF, MOVIE_PTR, 8)
    mem.setu(MOVIE_PTR, MOVIE_OBJ if playing is not None else 0, 8)
    if playing is not None:
        mem.setu(MOVIE_OBJ + IS_PLAYING_OFF, 1 if playing else 0, 4)

    cpu = _cpu_class()(mem)
    cpu.sp = SP
    cpu.set(19, 0x81000000)                    # the vertex-buffer object
    cpu.set(20, 0x82000000)                    # the renderer
    cpu.set(23, _f32(1.0))                     # 1.0f, as the painter leaves it
    cpu.set(29, SP + 0x1E0)

    # the staging buffer as quad 3 leaves it: x 0..s0, y 0..1, opaque black
    for k in range(4):
        mem.setu(SP + VX[k], 0, 4)
        mem.setu(SP + VY[k], _f32(1.0) if k >= 2 else 0, 4)
        mem.setu(SP + 8 + 0x20 * k + 0x10, 0xFF000000, 4)

    got = []

    def capture(_c):
        got.append([(mem.u(SP + VX[k], 4), mem.u(SP + VY[k], 4))
                    for k in range(4)])

    cpu.native = {tgt: (capture if tgt == copy_tgt else (lambda _c: None))
                  for tgt in _external_calls(words_map)}
    exit_pc = cpu.run(entry, None, code=words_map, start_pc=entry,
                      max_steps=20000)
    return got, exit_pc


def _rects(captured):
    """[(x0, x1, y0, y1)] as floats, from the captured vertex tuples."""
    def f(b):
        return struct.unpack('<f', struct.pack('<I', b))[0]
    out = []
    for v in captured:
        xs = sorted({f(x) for x, _ in v})
        ys = sorted({f(y) for _, y in v})
        out.append((xs[0], xs[-1], ys[0], ys[-1]))
    return out


# ------------------------------------------------------------------- verify
def verify(main=None, log=print):
    import nxmap
    import ff7nx_movieclip

    fails = [0]
    n = [0]

    def ck(cond, what):
        n[0] += 1
        log('  %s  %s' % ('ok  ' if cond else 'FAIL', what))
        if not cond:
            fails[0] += 1
        return cond

    # ---- 1. the two instructions this file encodes by hand -------------
    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
        md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)

        def d(word):
            i = next(md.disasm(struct.pack('<I', word), 0))
            return '%s %s' % (i.mnemonic, i.op_str.strip())
        ck(d(A.ret(LINK)) == 'ret x28', 'ret x28 encodes as `%s`'
           % d(A.ret(LINK)))
        ck(d(A.add_imm64(LINK, 30, 0)) in ('mov x28, x30', 'add x28, x30, #0'),
           'the link save encodes as `%s`' % d(A.add_imm64(LINK, 30, 0)))
        ck(d(A.str_(A.WZR, A.SP, 0x68)) == 'str wzr, [sp, #0x68]',
           'str wzr, [sp, #0x68] encodes as `%s`' % d(A.str_(A.WZR, A.SP, 0x68)))
        ck(d(A.str_(ONE_F, A.SP, 0x0c)) == 'str w23, [sp, #0xc]',
           'str w23, [sp, #0xc] encodes as `%s`' % d(A.str_(ONE_F, A.SP, 0x0c)))
    except ImportError:
        log('  ..    capstone not installed; encoder checks skipped')

    # ---- 2. the geometry, independent of any module --------------------
    e = extents(0.75, 16)
    ck(abs(e['m'] - 0.125) < 1e-9,
       'WS_SCALE 0.75 -> margin 0.125, i.e. 160 px of 1280')
    ck(abs(e['ytop'] - 16.0 / 480) < 1e-9,
       'quad y0 16 -> top bar 0 .. 0.033333, i.e. 24 rows of 720')
    ck(abs(e['ybot'] - 464.0 / 480) < 1e-9,
       'quad y0 16 -> bottom bar 0.966667 .. 1, i.e. row 696 of 720')
    e0 = extents(0.75, 0)
    ck(e0['ytop'] == 0.0 and abs(e0['ybot'] - 448.0 / 480) < 1e-9,
       'without moviealign the bars follow the quad to rows 0 and 672')
    e1 = extents(1.0, 16)
    ck(e1['m'] == 0.0 and e1['rm'] == 1.0,
       'a 4:3 build (WS_SCALE 1.0) would give zero-width side bars')

    mains = []
    if main:
        mains.append(resolve_main(main))
    else:
        for cand in ('dump/exefs/main', os.path.join('sdout', SDOUT_MAIN)):
            p = os.path.join(_HERE, cand)
            if os.path.exists(p):
                mains.append(p)
    if not mains:
        log('  ..    no module found; module checks skipped')
        return 1 if fails[0] else 0

    for path in mains:
        log('')
        log('  %s' % path)
        m = nxmap.Main(path)
        t = m.text
        bad = check_anchors(t, log)
        ck(not bad, 'all %d anchors match the module' % len(ANCHORS))
        if bad:
            continue

        # ---- 3. the issue block really is quad 3's -------------------
        block = issue_block(t)
        ck(block[1][1] == 0x910023E1, 'the issue block stages from sp+8')
        ck(sum(1 for va, w in block if _bl_target(w, va) is not None) == 4,
           'the issue block contains exactly 4 calls')
        copy_tgt = _copy_target(t)
        ck(copy_tgt is not None and copy_tgt < ISSUE_HEAD[4],
           'the copy call resolves to +%#x' % (copy_tgt or 0))

        ws = ff7nx_movieclip.shipped_ws_scale(path, log=lambda *_: None)
        y0 = movie_y0(t)
        ext = extents(ws, y0)
        log('    WS_SCALE %.8f   movie quad y0 %d game units' % (ws, y0))

        # ---- 4. lay the cave out and EXECUTE it ---------------------
        if installed(t):
            log('    (already installed -- executing the cave in the module)')
            phys = walk_physical(t)
            words_map = {va: w32(t, va) for va in phys}
            entry = _b_target(w32(t, HOOK), HOOK)
        else:
            words = build_patches(m.img, set(m.arm_starts), ws,
                                  log=lambda *_: None)
            if not ck(words is not None, 'the cave can be laid out'):
                continue
            entry = _b_target(words[HOOK], HOOK)
            words_map = {va: w for va, w in words.items() if va != HOOK}

        ck(len(words_map) >= N_WORDS,
           'the layout holds all %d words (+%d chain link(s))'
           % (N_WORDS, len(words_map) - N_WORDS))
        ck(sum(1 for va, w in words_map.items()
               if _b_target(w, va) == RETURN_VA) == 1,
           'exactly one `b` in the cave returns to the game')

        captured, exit_pc = _emu_run(words_map, entry, True, copy_tgt)
        ck(len(captured) == 4,
           'four quads are submitted while a movie plays (got %d)'
           % len(captured))
        if len(captured) == 4:
            r = _rects(captured)
            want = [
                (0.0, ext['m'], 0.0, 1.0, 'left  '),
                (ext['rm'], 1.0, 0.0, 1.0, 'right '),
                (0.0, 1.0, 0.0, ext['ytop'], 'top   '),
                (0.0, 1.0, ext['ybot'], 1.0, 'bottom'),
            ]
            for got_r, (x0, x1, ya, yb, name) in zip(r, want):
                ok = all(abs(a - b) < 1e-6
                         for a, b in zip(got_r, (x0, x1, ya, yb)))
                ck(ok, '%s bar -> x %.6f .. %.6f   y %.6f .. %.6f'
                   % (name, got_r[0], got_r[1], got_r[2], got_r[3]))
            ck((min(a[0] for a in r), max(a[1] for a in r)) == (0.0, 1.0)
               and (min(a[2] for a in r), max(a[3] for a in r)) == (0.0, 1.0),
               'the four bars between them reach every edge of the frame')
            ck(all(a[0] <= a[1] and a[2] <= a[3] for a in r),
               'no bar is inside-out')
            ck(ext['m'] < ext['rm'] and ext['ytop'] < ext['ybot'],
               'the picture is left open: x %.6f..%.6f  y %.6f..%.6f'
               % (ext['m'], ext['rm'], ext['ytop'], ext['ybot']))
        ck(exit_pc == RETURN_VA,
           'playing: the cave returns to +%#x (got +%#x)'
           % (RETURN_VA, exit_pc))

        # ---- 5. the gate really gates -------------------------------
        idle, pc_idle = _emu_run(words_map, entry, False, copy_tgt)
        ck(not idle, 'not playing: no quad is submitted')
        ck(pc_idle == RETURN_VA,
           'not playing: still returns to +%#x (got +%#x)'
           % (RETURN_VA, pc_idle))
        nullo, pc_null = _emu_run(words_map, entry, None, copy_tgt)
        ck(not nullo and pc_null == RETURN_VA,
           'a null movie object is guarded, not dereferenced')

        # ---- 6. the cave disassembles -------------------------------
        ck(_disasm_ok(words_map, entry, log),
           'every word of the cave disassembles to a real instruction')

        # ---- 7. apply -> revert is byte-identical -------------------
        if not installed(t):
            img = bytearray(m.img)
            before = bytes(img)
            for va, w in build_patches(m.img, set(m.arm_starts), ws,
                                       log=lambda *_: None).items():
                struct.pack_into('<I', img, va, w)
            rev = revert_patches(img, log=lambda *_: None)
            ck(rev is not None, 'the written cave can be reverted')
            if rev:
                for va, w in rev.items():
                    struct.pack_into('<I', img, va, w)
                ck(bytes(img) == before,
                   'apply -> revert is byte-identical over the whole image')

    log('')
    log('  %d check(s), %d failed' % (n[0], fails[0]) if fails[0]
        else '  %d check(s), all pass' % n[0])
    return 1 if fails[0] else 0


def _disasm_ok(words_map, entry, log=lambda *_: None):
    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    except ImportError:
        return True
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    for va, w in sorted(words_map.items()):
        got = list(md.disasm(struct.pack('<I', w), va))
        if not got:
            log('  ! +%#09x %08X does not disassemble' % (va, w))
            return False
        if got[0].mnemonic in ('udf', 'unknown'):
            log('  ! +%#09x %08X is %s' % (va, w, got[0].mnemonic))
            return False
    return True


# ------------------------------------------------------------------ plumbing
def enabled(env=None):
    """
    ON whenever 16:9 is, off when it is not -- the same rule
    `ff7nx_moviealign` and `ff7nx_modelcull` follow.

    Not a plain default-True. At 4:3 the picture already fills the frame,
    `m` is 0, and there is no margin to paint; switching the pass ON there
    would make `build.apply_field_frame` run its whole copy-and-patch chain
    for four zero-area quads.

    There is deliberately NO GUI checkbox writing this variable. FINDINGS-91
    §6: the save path writes every key on every build, so a checkbox is how a
    module default stops being a gate.
    """
    raw = env if env is not None else os.environ.get(MOVIEBARS_ENV)
    if raw is not None:
        return str(raw).strip().lower() not in ('', '0', 'off', 'no', 'false')
    try:
        import ff7nx_moviealign
        return ff7nx_moviealign.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def resolve_main(path):
    if os.path.isdir(path):
        cand = os.path.join(path, SDOUT_MAIN)
        if os.path.exists(cand):
            return cand
        raise SystemExit('no %s under %s' % (SDOUT_MAIN, path))
    return path


def show(main, log=print):
    import ff7nx_movieclip
    main = resolve_main(main)
    t = _text(main)
    log('  %s' % main)
    log('    +%#09X  %s  hook  %s'
        % (HOOK, _fmt(w32(t, HOOK)), cave_state(t)))
    try:
        y0 = movie_y0(t)
    except ValueError as e:
        log('    ! %s' % e)
        y0 = None
    if y0 is not None:
        ws = ff7nx_movieclip.shipped_ws_scale(main, log=lambda *_: None)
        ext = extents(ws, y0)
        log('    WS_SCALE %.8f    movie quad y0 %d game units' % (ws, y0))
        if installed(t):
            log('    while a movie plays, painted last over the frame:')
            log('      left   x 0.000000 .. %.6f' % ext['m'])
            log('      right  x %.6f .. 1.000000' % ext['rm'])
            log('      top    y 0.000000 .. %.6f' % ext['ytop'])
            log('      bottom y %.6f .. 1.000000' % ext['ybot'])
            log('      picture left open: x %.6f..%.6f  y %.6f..%.6f'
                % (ext['m'], ext['rm'], ext['ytop'], ext['ybot']))
            phys = walk_physical(t)
            log('      cave: %d word(s) of padding at +%#x'
                % (len(phys), phys[0]))
        else:
            log('    not installed -- nothing is painted during playback')
    bad = check_anchors(t, log)
    log('    anchors: %s' % ('OK' if not bad else '%d FAILED' % len(bad)))
    return 1 if bad else 0


def apply(main, revert=False, log=print):
    import nso_patcher
    import nxmap
    import ff7nx_movieclip
    main = Path(resolve_main(main))
    m = nxmap.Main(str(main))
    t = m.text

    if check_anchors(t, log):
        log('  refusing to write.')
        return 1

    if revert:
        words = revert_patches(t, log)
        if words is None:
            return 1
    else:
        if installed(t):
            log('  movie margin bars: already installed')
            return 0
        ws = ff7nx_movieclip.shipped_ws_scale(str(main), log=log)
        if not ff7nx_movieclip.clips_anything(ws):
            log('  movie margin bars: WS_SCALE is %.8f -- this is a 4:3 '
                'build, there are no margins to paint. Not applied.' % ws)
            return 0
        words = build_patches(m.img, set(m.arm_starts), ws, log)
        if words is None:
            return 1
    if not words:
        log('  movie margin bars: nothing to do')
        return 0

    patches = [{'name': 'ff7nx_moviebars +%#09X' % va,
                'va': hex(va),
                'expect': _fmt(w32(t, va)),
                'set': _fmt(word)}
               for va, word in sorted(words.items())]
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_moviebars',
                                             'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.moviebars-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    t2 = _text(main)
    log('  read back from the written module:')
    log('    hook +%#09X is %s' % (HOOK, cave_state(t2)))
    if not revert:
        phys = walk_physical(t2)
        if phys is None or len(phys) < N_WORDS:
            log('  ! the written cave cannot be walked. DO NOT BOOT THIS.')
            return 1
        wm = {va: w32(t2, va) for va in phys}
        entry = _b_target(w32(t2, HOOK), HOOK)
        if not _disasm_ok(wm, entry, log):
            log('  ! the written cave does not disassemble. DO NOT BOOT THIS.')
            return 1
        ws = ff7nx_movieclip.shipped_ws_scale(str(main), log=lambda *_: None)
        ext = extents(ws, movie_y0(t2))
        captured, out_pc = _emu_run(wm, entry, True, _copy_target(t2))
        if len(captured) != 4 or out_pc != RETURN_VA:
            log('  ! the written cave submits %d quad(s) and exits to +%#x. '
                'DO NOT BOOT THIS.' % (len(captured), out_pc))
            return 1
        for (x0, x1, y0_, y1), nm in zip(_rects(captured),
                                         ('left', 'right', 'top', 'bottom')):
            log('    %-6s x %.6f .. %.6f   y %.6f .. %.6f'
                % (nm, x0, x1, y0_, y1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split('\n')[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('main', nargs='?')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args(argv)

    if a.show:
        if not a.main:
            ap.error('need a path to exefs/main')
        return show(a.main)
    if a.apply or a.revert:
        if not a.main:
            ap.error('need a path to exefs/main')
        return apply(a.main, revert=a.revert)
    print('ff7nx_moviebars -- paint the FMV margins, do not clip the frame')
    print('')
    return verify(a.main, log=print)


if __name__ == '__main__':
    raise SystemExit(main())
