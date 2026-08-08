#!/usr/bin/env python3
r"""
ff7nx_movieclip.py -- clip to the 4:3 picture WHILE A MOVIE PLAYS. v3.

HANDOFF-80 §5.0. During the Reactor 1 explosion Cloud is drawn in the black
16:9 margin beside the movie.

    python3 ff7nx_movieclip.py <exefs/main | sdout> --show
    python3 ff7nx_movieclip.py <exefs/main | sdout> --verify   # executes it
    python3 ff7nx_movieclip.py <exefs/main | sdout> --apply

=============================================================================
0. THREE BUILDS GOT US HERE. WHAT EACH ONE ESTABLISHED.
=============================================================================
Everything in §1-§3 is a hardware result or an image read. The two wrong
builds are written up because each one is a fact now.

**v1 -- hooked `bl +0x11320E0` (vtable +0x188).** The whole picture scaled by
exactly 0.75; Cloud went x~50 -> x~198, which is `640 + (50-640)*0.75`.
  => +0x188 is the VIEWPORT, not the scissor. A scissor cuts, it does not
     scale.
  => the frame rect at that draw was 1280x720 (a 4:3 frame would have
     tripped v1's own no-op guard).
  => **Cloud scaled along with the movie**, so models, movie and field all go
     through one draw path into one framebuffer -- which is what Patrick had
     already said twice -- and `is_playing` is set during the scene.

I took "+0x188 is the scissor" from `ff7nx_scissor.py`'s docstring after
proving the reasoning it rests on was wrong. That is the whole cost of v1.

**v2 -- hooked the paired `bl +0x11320F0` (vtable +0x190), same rect.**
Nothing moved. But the image says +0x190 IS the glScissor path:

    GOT +0xE60 glViewport <- +0x1133F10 <- +0x1137640 <- vtable(+0x12CCAE0) +0x188
    GOT +0xE68 glScissor  <- +0x1133F80 <- +0x1137730 <- vtable(+0x12CCAE0) +0x190

Both cannot be true, so **the renderer in use is a different class sharing
that interface**: its +0x188 is still a viewport, its +0x190 is not
+0x1137730. That is not decidable statically -- the object is
`[[[0x12CE510]]]` and `gfx_drv_init` (+0x10D5194) fills it from
`[[0x12CE188]]`. Chasing it further was the mistake; vtable slots are the
wrong abstraction to patch.

**The probe -- `ff7nx_scissorprobe.py`, deliberately ungated.** Hooked
+0x1133FE8, the last instruction before `b +0x11521C0`, the ONLY tail-call to
the glScissor PLT stub in the module, and clamped every box with
`x += w/4; w /= 2`. On hardware the field, the models AND the 2D save menu
were all cut to x 320..960 of 1280.

    MEASURED, and these are the two facts this module is built on:
      * glScissor is live, is honoured, and reaches the field, the models
        and the UI. The route is real.
      * the incoming box is x = 0, w = 1280 -- because x + w/4 = 320 and
        w/2 = 640 give exactly the 320..960 seen on screen.

=============================================================================
1. THE HOOK -- A FUNCTION, NOT A VTABLE SLOT
=============================================================================
    +0x1133F80   scissor_set(this, const int32 rect[4])   {x, y, w, h}
       ... early-returns if all four match the cache at [this+0x2D0] ...
       +0x1133FE0  ldp w0, w8, [x1]        x, y
       +0x1133FE4  ldp w2, w3, [x1, #8]    w, h
       +0x1133FE8  mov w1, w8              <- HOOK
       +0x1133FEC  b   +0x11521C0          glScissor(x, y, w, h)

This is downstream of every renderer class, every vtable and every call site,
so v2's failure mode cannot recur. `+0x1133F80` is the only function that
reaches the stub; the probe proved it is on the live path.

Registers at the hook: w0 = x, w8 = y, w2 = w, w3 = h. x9/x10 are scratch and
dead -- two instructions later is a tail call to a PLT stub. **x1 must not be
touched**: it still holds the caller's rect pointer and the displaced
`mov w1, w8` overwrites it on the way out. NZCV is not used by this cave.

=============================================================================
2. THE BAND -- THE SHIPPED SHADER'S OWN CONSTANT, BAKED IN
=============================================================================
`wide_screen/*.glsl` does `gl_Position.x *= WS_SCALE`, so FF7's 640-unit game
space always occupies the **central WS_SCALE of whatever rect is in force**,
whatever the render target is. glScissor is in framebuffer pixels, applied
after the vertex shader, so the visible 4:3 picture is that central fraction:

    new_w = (w * S16) >> 16          S16 = ceil(WS_SCALE * 65536)
    new_x = x + (w - new_w) / 2      centred

**WS_SCALE IS NOT A CONSTANT.** `ff7nx_ws.ws_scale()` derives it from the
field buffer width as `320n / W` (HANDOFF-51 §2):

    field_buffer 1   428x240    0.74766355    band 54..374   of 428
    field_buffer 2   854x480    0.74941452    band 107..747  of 854
    field_buffer 3  1280x720    0.75000000    band 160..1120 of 1280   SHIPS
    widescreen OFF   320n x 240n    1.0       -- NOTHING TO CLIP

So the value is read out of the `tlmain_vv.glsl` this build actually ships,
not assumed, and `S16` is baked into the cave at patch time. `ceil` rather
than `round` because it makes all three presets land on their exact band.

**With widescreen off, WS_SCALE is 1.0 and this patch must not be applied at
all** -- a 0.75 shrink there is a regression, not a no-op. v2 got that for
free because its `4*th/(3*tw)` collapsed to 1.0 on a 4:3 target; v3's shift
pair did not, and `enabled()`/`apply_to_nso` now refuse instead.

v2's mistake was the other half of the same confusion: it computed the scale
from the RENDER TARGET, because that is the rule fieldbuf uses to CHOOSE the
constant. At runtime the shader just uses the constant, and the two part
company the moment the bound target is not the field buffer.

    x=0, w=1280, S=0.75  ->  160 .. 1120     the shipping build (MEASURED)

=============================================================================
3. THE GATE -- AND WHY THE PROBE WRECKED THE SAVE MENU
=============================================================================
The probe had no gate, on purpose: it had to be unmissable. This does:

    fw_movie_start  +0x10F1550   ldr x8,[.,#0x7C0]; ldr x8,[x8]
                                 ldr w9,[x8,#0x1FC]; cbz -> str #1
    fw_movie_stop   +0x10F1770   stp xzr, xzr, [x8, #0x1F8]

`[[0x12CE7C0]] + 0x1FC` is 1 from `fw_movie_start` until `fw_movie_stop`
zeroes it. v1 changed the picture only during the movie, so the flag is
confirmed working on hardware. Both indirections are `cbz`-guarded anyway --
this function runs from boot, long before the movie module exists, and an
unguarded load there is a hang on the save-select screen.

Outside playback the cave falls through to the displaced instruction and the
box is byte-identical to what the game asked for. The save menu, the field at
16:9, battle and menus cannot be touched, because the only path that changes
w0/w2 is behind three `cbz`s on that flag.

FFNx gates the same way -- `Renderer::setScissor` branches on
`is_movie_playing && getMovieMode() == WM_DISABLED` (README-45).

=============================================================================
4. WHAT IS STILL NOT VERIFIED
=============================================================================
Only the shape. That the clip happens, in the right place, on the right
draws, is measured. What no offline test can say is whether clipping *every*
draw during playback reads correctly -- the movie already fills the band
exactly, so it should be untouched, but a 2D overlay that deliberately sits
in the margin during a movie would now be cut. None is known to exist.

Failure modes, named in advance:

  * Cloud sliced at the movie edge, everything else normal   -> done.
  * The movie loses a column at each edge  -> the band is a pixel tight;
    `w -= w>>2` becomes `w -= (w>>2) - 2`. Not a redesign.
  * Something outside a movie is clipped   -> the flag is staying set after
    `fw_movie_stop`. Check the flag, not the rect.
  * Nothing changes                        -> the flag is not set for this
    scene, since the probe proved the hook and the clip both work.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                  # noqa: E402

MOVIECLIP_ENV = 'SEVENTH_NX_MOVIE_CLIP'

TITLE_ID = '0100A5B00BDC6000'
SDOUT_MAIN = os.path.join('atmosphere', 'contents', TITLE_ID, 'exefs', 'main')

# ---------------------------------------------------------------- the hook
HOOK_VA = 0x1133FE8
HOOK_ORIG = 0x2A0803E1                  # mov w1, w8

PAGE = 0x12CE000
MOVIE_PTR_OFF = 0x7C0                   # [PAGE + this] -> &movie_object
IS_PLAYING_OFF = 0x1FC                  # movie_object + this

# --------------------------------------------------- the full-screen bypass
# v4. ff7nx_letterbox's uncrop turned this module OFF, and the mechanism is
# an optimisation nobody had read.
#
# +0x1133F80 (`scissor_set`) is not called unconditionally. Its only caller,
# +0x1137700ish, builds the box from the two packed corners and then decides
# whether a scissor is needed at all:
#
#   +0x11377A4  sub  w21, w21, w20      width   = x2 - x1
#   +0x11377A8  sub  w23, w9,  w8       height  = y2 - y1
#   +0x11377B0  sub  w24, w0,  w9       y flipped to bottom-left origin
#   +0x11377B4  cmp  w0, w23            target height == box height ?
#   +0x11377B8  orr  w9, w24, w20       (flipped_y | x1)
#   +0x11377BC  ccmp w21, w22, #0, eq   ... and width == target width ?
#   +0x11377C0  ccmp w9,  #0,  #0, eq   ... and both origins zero ?
#   +0x11377C4  b.eq +0x11377F0         -> set_scissor_enabled(FALSE), return
#   +0x11377D4  bl   +0x1133EE0         set_scissor_enabled(TRUE)
#   +0x11377E8  bl   +0x1133F80         scissor_set  <- THE HOOK IS IN HERE
#
# So when the box already covers the whole render target, the scissor test is
# DISABLED and `scissor_set` is never reached. That is exactly the state
# ff7nx_letterbox's uncrop now produces for the field: it forces the rect to
# the full frame to kill the black bars. Before uncrop the field's box was
# device rows 24..696 of 720 -- not the full target -- so the scissor path ran
# every frame and this module's cave fired. After uncrop it is 0..720, the
# optimisation kicks in, and the cave is never executed.
#
# The clip did not break. It stopped being called.
#
# The fix is one word and needs no cave, no flag and no is_playing test:
# NOP the early-out so the scissor path always runs. The cost is a scissor
# test enabled with a full-frame box, which clips nothing; the benefit is that
# `scissor_set` is reached, so the gated cave downstream gets its chance.
# Both branches stay self-consistent -- the taken path sets w22 = 1 and
# +0x1137800 stores it to [x19+0x58], the "scissor is on" state byte, which is
# now simply always 1.
BYPASS_VA   = 0x11377C4
BYPASS_ORIG = 0x54000160        # b.eq +0x11377F0
BYPASS_NOP  = 0xD503201F

BYPASS_ANCHORS = [
    (0x11377A4, 0x4B1402B5, 'sub w21, w21, w20     width  = x2 - x1'),
    (0x11377B0, 0x4B090018, 'sub w24, w0, w9       y flipped'),
    (0x11377B4, 0x6B17001F, 'cmp w0, w23           target h == box h'),
    (0x11377BC, 0x7A5602A0, 'ccmp w21, w22, #0, eq  ... and w == target w'),
    (0x11377C0, 0x7A400920, 'ccmp w9, #0, #0, eq    ... and origins are 0'),
    (0x11377C4, 0x54000160, 'b.eq +0x11377F0        THE BYPASS'),
    (0x11377D4, 0x97FFF1C3, 'bl +0x1133EE0          set_scissor_enabled'),
    (0x11377E8, 0x97FFF1E6, 'bl +0x1133F80          scissor_set'),
]


ANCHORS = [
    (0x1133F80, 0xB942D009, 'ldr w9, [x0, #0x2D0]  the glScissor state cache'),
    (0x1133FE0, 0x29402020, 'ldp w0, w8, [x1]      x, y'),
    (0x1133FE4, 0x29410C22, 'ldp w2, w3, [x1, #8]  w, h'),
    (0x1133FE8, 0x2A0803E1, 'mov w1, w8            THE HOOK SITE'),
    (0x1133FEC, 0x14007875, 'b +0x11521C0          -> the glScissor stub'),
    (0x11521C0, 0xF0000BD0, 'adrp x16, #0x12CD000  the PLT stub itself'),
    (0x11377E8, 0x97FFF1E6, 'bl +0x1133F80         its only caller'),
    (0x10F1554, 0xF943E108, 'ldr x8, [x8, #0x7C0]  fw_movie_start'),
    (0x10F155C, 0xB941FD09, 'ldr w9, [x8, #0x1FC]  is_playing'),
    (0x10F177C, 0xA91FFD1F, 'stp xzr, xzr, [x8, #0x1F8]   fw_movie_stop'),
]

I_SKIP = 15                             # the displaced instruction
N_WORDS = 17                            # + the return branch

FIXED_ONE = 1 << 16
SHIP_SCALE = 0.75                       # field_buffer 3; see fixed_scale()


def fixed_scale(ws_scale):
    """
    WS_SCALE as 16.16. `ceil` rather than `round`: it is what makes every
    shipping preset land on its exact band instead of one pixel short.

        0.74766355 * 65536 = 48998.4  -> 48999 -> 428*.. >>16 = 320  exact
        0.74941452 * 65536 = 49113.2  -> 49114 -> 854*.. >>16 = 640  exact
        0.75000000 * 65536 = 49152.0  -> 49152 -> 1280*.. >>16 = 960 exact
    """
    import math
    s16 = int(math.ceil(ws_scale * FIXED_ONE))
    if not 1 <= s16 <= 0xFFFF:
        raise ValueError('WS_SCALE %r is not in (0, 1]' % ws_scale)
    return s16


def cave_words(addr, return_va, s16=None):
    """The whole cave, laid out at addr(i), for a baked 16.16 WS_SCALE."""
    s16 = fixed_scale(SHIP_SCALE) if s16 is None else s16
    skip = addr(I_SKIP)
    w = [
        # ---- is a movie playing? ------------------------------------ §3
        A.adrp(9, addr(0), PAGE),             # adrp x9, #0x12CE000
        A.ldr64(9, 9, MOVIE_PTR_OFF),         # ldr  x9, [x9, #0x7C0]
        A.cbz64(9, addr(2), skip),
        A.ldr64(9, 9, 0),                     # ldr  x9, [x9]
        A.cbz64(9, addr(4), skip),
        A.ldr(9, 9, IS_PLAYING_OFF),          # ldr  w9, [x9, #0x1FC]
        A.cbz(9, addr(6), skip),
        # ---- the centred WS_SCALE band ------------------------------- §2
        A.movz(11, s16 & 0xFFFF),             # mov  w11, #S16
        A.movk_hi(11, 0),                     # movk w11, #0, lsl #16
        A.mul(10, 2, 11),                     # mul  w10, w2, w11
        A.lsr(10, 10, 16),                    # lsr  w10, w10, #16   new w
        A.sub_reg(11, 2, 10),                 # sub  w11, w2, w10
        A.lsr(11, 11, 1),                     # lsr  w11, w11, #1
        A.add_reg(0, 0, 11),                  # add  w0, w0, w11     x
        A.mov_reg(2, 10),                     # mov  w2, w10         w
        # ---- SKIP ----------------------------------------------------
        HOOK_ORIG,                            # mov  w1, w8   (displaced)
    ]
    assert len(w) == I_SKIP + 1, 'I_SKIP is %d, body is %d' % (I_SKIP, len(w))
    w.append(A.b(addr(N_WORDS - 1), return_va))
    return w


DISASM = [
    'adrp x9, #0x12ce000', 'ldr x9, [x9, #0x7c0]', 'cbz x9, #skip',
    'ldr x9, [x9]', 'cbz x9, #skip', 'ldr w9, [x9, #0x1fc]', 'cbz w9, #skip',
    'mov w11, #S16', 'movk w11, #0, lsl #16', 'mul w10, w2, w11',
    'lsr w10, w10, #0x10', 'sub w11, w2, w10', 'lsr w11, w11, #1',
    'add w0, w0, w11', 'mov w2, w10', 'mov w1, w8', 'b #return',
]


# --------------------------------------------------- where WS_SCALE comes from
SHADER_REL = os.path.join('romfs', 'ff7', 'shaders', 'tlmain_vv.glsl')


def shipped_ws_scale(main_path, log=lambda *_: None):
    """
    The `#define WS_SCALE` in the shader THIS SD tree ships, walking up from
    `exefs/main`. That file is the thing that moves the picture, so it is the
    only honest source for the band -- not a constant here, and not the
    render target (which is what v2 got wrong).

    Falls back to `ff7nx_ws.ws_scale()` (the value a build WOULD ship) and,
    failing that, to SHIP_SCALE.
    """
    d = os.path.dirname(os.path.abspath(main_path))
    for _ in range(6):
        cand = os.path.join(d, SHADER_REL)
        if os.path.exists(cand):
            import re
            with open(cand) as f:
                m = re.search(r'^\s*#define\s+WS_SCALE\s+([0-9.]+)', f.read(),
                              re.MULTILINE)
            if m:
                log('  WS_SCALE %s   (from %s)' % (m.group(1), cand))
                return float(m.group(1))
        d = os.path.dirname(d)
    try:
        import ff7nx_ws
        s = float(ff7nx_ws.ws_scale())
        log('  WS_SCALE %.8f   (ff7nx_ws.ws_scale(); no shipped shader found)'
            % s)
        return s
    except Exception:                                          # noqa: BLE001
        log('  WS_SCALE %.8f   (fallback)' % SHIP_SCALE)
        return SHIP_SCALE


def clips_anything(ws_scale):
    """False on a 4:3 build. There the shader moves nothing, so neither may we."""
    return ws_scale < 0.999


def check_encoding(log=print, s16=None):
    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    except ImportError:
        log('  (capstone not installed -- encodings NOT checked)')
        return True
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    base = 0x1000
    words = cave_words(lambda i: base + 4 * i, base + 0x100, s16)
    blob = b''.join(struct.pack('<I', x) for x in words)
    got = [(i.mnemonic + ' ' + i.op_str).strip() for i in md.disasm(blob, base)]
    ok = len(got) == len(words)
    if not ok:
        log('  ! capstone decoded %d of %d words' % (len(got), len(words)))
    for k, (g, want) in enumerate(zip(got, DISASM)):
        loose = ('#skip' in want or '#return' in want
                 or want.startswith('adrp') or '#S16' in want)
        if (g.split()[0] if loose else g) != (want.split()[0] if loose
                                              else want):
            log('  ! word %2d encodes `%s`, meant `%s`' % (k, g, want))
            ok = False
    return ok


def cave_patches(img, starts, log=lambda *_: None, s16=None):
    import ff7nx_cave
    return_va = HOOK_VA + 4

    def build(_entry, addr):
        return cave_words(addr, return_va, s16)

    entry, out = ff7nx_cave.emit_laid_out(
        ff7nx_cave.HolePool(img, starts=starts), build, span=0x80000)
    out[HOOK_VA] = A.b(HOOK_VA, entry)
    log('  movie clip cave: %d words in padding, entry +%#x' % (N_WORDS, entry))
    log('  (the 60 FPS cave region is not touched)')
    return out


# ------------------------------------------------------------------ the model
def band(x, w, s16=None):
    """The cave's arithmetic: a centred WS_SCALE shrink, in 16.16."""
    s16 = fixed_scale(SHIP_SCALE) if s16 is None else s16
    nw = (w * s16) >> 16
    return x + ((w - nw) >> 1), nw


# (x, w, what) -- boxes the renderer is known or expected to ask for
BOXES = [
    (0, 1280, 'the shipping build -- MEASURED by the probe'),
    (0, 428, 'ff7nx_fieldbuf scale 1'),
    (0, 854, 'ff7nx_fieldbuf scale 2'),
    (0, 1440, 'the main render target'),
    (320, 640, 'a rect that is already a sub-box'),
]


# (buffer width, its WS_SCALE, the band it must produce, name)
PRESETS = [
    (428, 0.74766355, (54, 374), 'field_buffer 1'),
    (854, 0.74941452, (107, 747), 'field_buffer 2'),
    (1280, 0.75000000, (160, 1120), 'field_buffer 3'),
]


def _emu():
    import ws_emu
    import arm64emu

    class Cpu(ws_emu.Cpu):
        def step(self, w, pc):
            if (w & 0x9F000000) == 0x90000000:              # adrp
                rd = w & 31
                imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
                if imm & (1 << 20):
                    imm -= (1 << 21)
                self.set(rd, ((pc & ~0xFFF) + (imm << 12)) & arm64emu.M64)
                return None
            return ws_emu.Cpu.step(self, w, pc)

    return Cpu, arm64emu


def emulate(x, y, w, h, playing, s16=None):
    """Execute the cave's real words on a glScissor rect."""
    Cpu, arm64emu = _emu()
    mem = arm64emu.Mem()
    base = PAGE + 0x40000
    slot, obj = 0x2100000, 0x2101000
    mem.setu(PAGE + MOVIE_PTR_OFF, slot, 8)
    mem.setu(slot, obj, 8)
    mem.setu(obj + IS_PLAYING_OFF, 1 if playing else 0, 4)
    words = cave_words(lambda i: base + 4 * i, base + 4 * N_WORDS, s16)
    cpu = Cpu(mem)
    cpu.set(0, x, True)
    cpu.set(8, y, True)
    cpu.set(2, w, True)
    cpu.set(3, h, True)
    cpu.set(1, 0xBADF00D)                    # must survive: the rect pointer
    cpu.run(base, words, stop_at=base + 4 * N_WORDS)
    return {'x': cpu.get(0, True), 'y': cpu.get(8, True),
            'w': cpu.get(2, True), 'h': cpu.get(3, True),
            'w1': cpu.get(1, True)}


def verify(log=print, ws=None):
    ws = SHIP_SCALE if ws is None else ws
    s16 = fixed_scale(ws)
    ok = check_encoding(log, s16)
    log('')
    log('  WS_SCALE %.8f  ->  S16 %d  (baked into the cave)' % (ws, s16))
    log('  the cave, executed (ws_emu + ADRP), on glScissor(x, y, w, h):')
    log('')
    log('    in                     out                    x span    what')
    for x, w, what in BOXES:
        got = emulate(x, 48, w, 672, playing=True, s16=s16)
        wx, ww = band(x, w, s16)
        good = (got['x'], got['w']) == (wx, ww) and (got['y'], got['h']) == (48, 672)
        ok = ok and good
        log('    %4d,%4d  ->  %4d,%4d   x %4d..%-5d %s  %s'
            % (x, w, got['x'], got['w'], got['x'], got['x'] + got['w'],
               'ok ' if good else 'WRONG', what))
    log('')
    log('  and with is_playing = 0 -- every other screen in the game:')
    for x, w, what in BOXES:
        got = emulate(x, 48, w, 672, playing=False, s16=s16)
        good = (got['x'], got['y'], got['w'], got['h']) == (x, 48, w, 672)
        ok = ok and good
        log('    %4d,%4d  ->  %4d,%4d   %s'
            % (x, w, got['x'], got['w'],
               'byte-identical' if good else 'WRONG -- IT CLIPPED'))
    # the displaced `mov w1, w8` is the LAST thing the cave runs, so on the
    # way out w1 must hold y -- that is glScissor's second argument. (An
    # earlier version of this check asserted x1 was unchanged, which is the
    # opposite of what the displaced instruction is for.)
    for playing in (True, False):
        g = emulate(0, 48, 1280, 672, playing=playing, s16=s16)
        good = g['w1'] == 48
        ok = ok and good
        log('')
        log('  is_playing=%d: the displaced `mov w1, w8` still ran -- '
            'w1 = %d (glScissor\'s y): %s'
            % (playing, g['w1'], 'ok' if good else 'NO -- IT WOULD CRASH'))
    log('')
    log('  and every shipping preset, at ITS OWN WS_SCALE:')
    for w, sc, want, what in PRESETS:
        s = fixed_scale(sc)
        got = emulate(0, 0, w, 240, playing=True, s16=s)
        good = (got['x'], got['x'] + got['w']) == want
        ok = ok and good
        log('    %-16s %4d px  S %.8f  ->  %4d..%-5d %s'
            % (what, w, sc, got['x'], got['x'] + got['w'],
               'ok' if good else 'WRONG, want %d..%d' % want))
    log('    %-16s %4d px  S %.8f  ->  %s'
        % ('widescreen OFF', 320, 1.0,
           'REFUSED -- clips_anything() is False'))
    ok = ok and not clips_anything(1.0) and clips_anything(0.75)
    log('')
    log('  %s' % ('every row agrees with band()' if ok
                  else '! SOMETHING DISAGREES -- do not build this'))
    return ok


# ------------------------------------------------------------------ plumbing
def enabled(env=None):
    """
    RETIRED. OFF unless explicitly switched on.

    ================= WHY THIS MODULE IS NO LONGER SHIPPED =================
    It worked, and that is the problem. Narrowing glScissor to the central
    4:3 clips EVERY draw made while a movie plays, and two of those draws
    are the ones that paint the 16:9 margins -- the frame clear and the
    letterbox fill. So the last field frame before the FMV started stays
    frozen in the left and right margins for the whole video.

    Measured on hardware, both states:

        movieclip        models in margin      margin contents
        ---------------  --------------------  --------------------------
        ON               clipped   (correct)   STALE field art  (wrong)
        OFF              drawn over the movie  black            (correct)

    No band arithmetic fixes that: a scissor is frame state and cannot tell
    a model draw from a clear. `ff7nx_moviecull` does the job at the only
    place that can -- `field_do_draw_3d_model`, which decides per model
    whether to draw at all and touches no render state.

    The derivation in this file is kept because two of its results are still
    load-bearing and were expensive: the `is_playing` decode
    ([[0x12CE7C0]] + 0x1FC), which ff7nx_moviecull reuses verbatim, and the
    proof that glScissor is live and honoured on this port at all.

    `SEVENTH_NX_MOVIE_CLIP=1` still turns it on for an A/B. Leave it off.
    """
    raw = (env if env is not None
           else os.environ.get(MOVIECLIP_ENV, '0')).strip().lower()
    return raw not in ('0', 'off', 'no', 'none', 'false')


def resolve_main(path):
    if os.path.isdir(path):
        cand = os.path.join(path, SDOUT_MAIN)
        if os.path.exists(cand):
            return cand
        raise SystemExit('no %s under %s' % (SDOUT_MAIN, path))
    return path


def _hex(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def _word(text, va):
    return struct.unpack_from('<I', text, va)[0]


def state(text):
    got = _word(text, HOOK_VA)
    if got == HOOK_ORIG:
        return 'stock'
    if (got & 0xFC000000) == 0x14000000:
        return 'patched'
    return 'unknown'


def bypass_state(text):
    got = _word(text, BYPASS_VA)
    if got == BYPASS_ORIG:
        return 'stock'
    if got == BYPASS_NOP:
        return 'patched'
    return 'unknown'


def verify_bypass_anchors(text, log=print):
    ok = True
    for va, want, what in BYPASS_ANCHORS:
        got = _word(text, va)
        if va == BYPASS_VA and got == BYPASS_NOP:
            continue
        if got != want:
            log('  ! +0x%07X %s: expected %08X, found %08X'
                % (va, what, want, got))
            ok = False
    return ok


def verify_anchors(text, log=print):
    ok = True
    for va, want, what in ANCHORS:
        got = _word(text, va)
        if va == HOOK_VA and (got & 0xFC000000) == 0x14000000:
            continue
        if got != want:
            log('  ! +0x%07X %s: expected %08X, found %08X'
                % (va, what, want, got))
            ok = False
    return ok


def show(path, log=print):
    import nxmap
    m = nxmap.Main(path)
    log('module %s' % path)
    log('  movie 4:3 clip: %s'
        % {'stock': 'NOT installed',
           'patched': 'INSTALLED  (+0x%07X is a branch)' % HOOK_VA,
           'unknown': 'UNRECOGNISED -- do not patch'}[state(m.text)])
    log('  hooks: +0x1133FE8, the last word before `b +0x11521C0` (glScissor)')
    log('  full-screen bypass: %s'
        % {'stock': 'NOT removed  <- with uncrop on, scissor_set is never '
                    'reached and the clip CANNOT fire',
           'patched': 'removed (+0x%07X is a nop)' % BYPASS_VA,
           'unknown': 'UNRECOGNISED -- do not patch'}[bypass_state(m.text)])
    log('  bypass anchors: %s'
        % ('pass' if verify_bypass_anchors(m.text, lambda *_: None) else 'FAIL'))
    log('  anchors: %s'
        % ('pass' if verify_anchors(m.text, lambda *_: None) else 'FAIL'))
    ws = shipped_ws_scale(path, log)
    log('  clips: %s' % ('yes' if clips_anything(ws)
                         else 'NO -- 4:3 build, --apply will refuse'))
    log('')
    log('  the cave it would place:')
    base = 0x1000
    s16 = fixed_scale(ws)
    for k, w in enumerate(cave_words(lambda i: base + 4 * i, base + 0x100,
                                     s16)):
        log('    %2d  %08X  %s' % (k, w, DISASM[k]))


def apply_to_nso(src, dest, log=lambda *_: None):
    try:
        import nso_patcher
        import nxmap
    except ImportError as exc:                                 # noqa: BLE001
        log('! movie clip: cannot import %s' % exc)
        return False
    try:
        m = nxmap.Main(src)
        if state(m.text) == 'patched' and bypass_state(m.text) == 'patched':
            log('  already installed; nothing to do')
            return True
        if bypass_state(m.text) == 'unknown':
            log('! movie clip: +0x%07X is neither the stock b.eq nor a nop; '
                'refusing' % BYPASS_VA)
            return False
        if not verify_bypass_anchors(m.text, log):
            log('! movie clip: the scissor caller does not match; refusing to '
                'remove the full-screen bypass')
            return False
        if not verify_anchors(m.text, log):
            log('! movie clip: this module is not the one the offsets were '
                'derived from; refusing to patch')
            return False
        if not check_encoding(log):
            log('! movie clip: an encoder disagrees with capstone; refusing')
            return False
        ws = shipped_ws_scale(src, log)
        if not clips_anything(ws):
            log('! movie clip: WS_SCALE is %.8f -- this is a 4:3 build and '
                'the shader moves nothing, so there is no margin to clip '
                'out of. NOT applied (applying would shrink every movie).'
                % ws)
            return False
        s16 = fixed_scale(ws)
        log('  band: the central %.5f of every scissor box  (S16 %d)'
            % (ws, s16))
        words = cave_patches(m.img, set(m.arm_starts), log, s16) \
            if state(m.text) != 'patched' else {}
        if bypass_state(m.text) != 'patched':
            words[BYPASS_VA] = BYPASS_NOP
            log('  full-screen bypass removed @ +0x%07X -- without this, '
                'uncrop makes the field box cover the whole target and '
                'scissor_set is never called' % BYPASS_VA)
        nso = nso_patcher.read_nso(Path(src))
        applied = nso_patcher.apply_spec(nso, {
            'name': 'movie 4:3 clip',
            'patches': [
                {'name': ('hook -> cave' if va == HOOK_VA else
                          'full-screen bypass -> nop' if va == BYPASS_VA else
                          'cave word'),
                 'va': '0x%X' % va,
                 'expect': _hex(struct.unpack_from('<I', m.img, va)[0]),
                 'set': _hex(word)}
                for va, word in sorted(words.items())
            ],
        })
        Path(dest).write_bytes(nso_patcher.rebuild(nso))
    except Exception as exc:                                   # noqa: BLE001
        log('! movie clip: %s' % exc)
        return False
    log('  %d word(s) verified and applied' % len(applied))
    return True


def revert(src, dest, log=print):
    """Restore `mov w1, w8`. The cave words are left inert."""
    import nso_patcher
    import nxmap
    m = nxmap.Main(src)
    got = _word(m.text, HOOK_VA)
    if got == HOOK_ORIG:
        log('  not installed; nothing to do')
        return True
    if (got & 0xFC000000) != 0x14000000:
        log('! +0x%07X is neither the stock word nor a branch' % HOOK_VA)
        return False
    ps = [{'name': 'restore mov w1, w8', 'va': '0x%X' % HOOK_VA,
           'expect': _hex(got), 'set': _hex(HOOK_ORIG)}]
    if bypass_state(m.text) == 'patched':
        ps.append({'name': 'restore the full-screen bypass',
                   'va': '0x%X' % BYPASS_VA,
                   'expect': _hex(BYPASS_NOP), 'set': _hex(BYPASS_ORIG)})
    nso = nso_patcher.read_nso(Path(src))
    nso_patcher.apply_spec(nso, {'name': 'remove the movie clip',
                                 'patches': ps})
    Path(dest).write_bytes(nso_patcher.rebuild(nso))
    log('  movie clip removed')
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='clip to the 4:3 picture while a movie plays')
    ap.add_argument('main', nargs='?')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('-o', '--out')
    a = ap.parse_args(argv)
    if a.verify or not a.main:
        ws = (shipped_ws_scale(resolve_main(a.main)) if a.main
              else SHIP_SCALE)
        return 0 if verify(ws=ws) else 1
    src = resolve_main(a.main)
    if a.revert:
        return 0 if revert(src, a.out or src) else 1
    if a.apply:
        return 0 if apply_to_nso(src, a.out or src, log=print) else 1
    show(src)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
