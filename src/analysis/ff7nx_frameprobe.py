#!/usr/bin/env python3
"""
ff7nx_frameprobe.py -- a per-frame timing probe. DIAGNOSTIC ONLY, off unless
SEVENTH_NX_FRAME_PROBE=1.

WHY
===
Three limiter builds (525/526/527) each rested on a model of the heavy frames
that was never measured. This measures them. Every presented frame is split
into the pieces the code actually has, timed with the hardware counter
(`mrs cntpct_el0`, 19.2 MHz -- the same clock nn::os::GetSystemTick reads):

    sec  name      what is timed                               how
    0    LOGIC     field_loop_sub_63C17F (scripts, movement,   call sites +0x946740
                   camera, bg positions)                        and +0x94BEC0
    1    ANIM      field_animate_3d_models_6392BB               +0x9470A4, +0x94C678
    2    DRAW      field_draw_everything_63A60B                 +0x94B898, +0x94BAC8
    3    LIMIT     the field limiter spin 0x6384E6              +0x94B920, +0x94BBCC
    4    CATCHUP   the debt path's extra frame 0x60E96C         +0x94BA54
    5    DRVFLIP   native gfx_drv_flip, entry -> its tail call  +0x10DA880 / +0x10DABC0
    6    TIMER     the port's timeSetEvent pump +0x10ECC20      entry / ret
                   (run from +0xA550, every 2048th rdtsc call,
                   once per elapsed virtual millisecond)
    7    TEXLOAD   native gfx_drv_load_texture (palette ->      +0x10D6BC0 / ret
                   RGBA conversion + glTexStorage/SubImage)
    8    OGG       vgmstream open (voice probe, player,         +0x10FE090 / ret
                   ambience) -- ANY thread, see caveat
    9    TCB       the x86 timer callbacks that pump fires      call site +0x10ECCB0

VERSION 9 (BUILD 794, SEVENTH_NX_FRAME_PROBE=gpu): the GPU time per
driver function. The port imports no GL timer query, so the GPU is timed
by making the CPU wait for it: a 1x1 glReadPixels (called through its GOT
slot, so the GL profile does not count it) before every outermost driver
call, after it returns, at flip's entry and before eglSwapBuffers. The
wait after a call is the GPU time of what it submitted (the GPU was idle
when it started); the wait before is what other code queued (texture
loads, palette writes). The CPU and GPU no longer overlap, so the frame
times of this build are meaningless; the GPU column is the measurement.
Otherwise the v8 'drv' build, with the per-entry GL column replaced by the
GPU one and three more rows for the waits outside driver calls.
VERSION 10 (BUILD 796): the same, and gfx_drv_load_texture (the palette
-> RGBA conversion and its upload, which v9 left in 'before driver calls')
waits too, on two more rows: before it, and after it (its own GPU time).
VERSION 11 (BUILD 798): the native passes in GPU_SPLIT (end_scene's six,
the render-mode switch the translated field calls directly, and the blit)
wait before and after themselves at any depth, each on its own row in the
game rows' slots (a gpu build leaves the game's call sites alone). A wait
before a pass inside another call is that caller's work so far, on one
more row; every wait inside an outermost call also goes to its top row.

VERSION 6 (BUILD 789, SEVENTH_NX_FRAME_PROBE=drv): the NATIVE gfx driver
per function. With build 788c, a busy field frame still spent ~7 ms of DRAW
outside GL and 1.9 ms in flip. This says how much of that is the port's own
driver code (gfx_drv_*, which can be optimised) and how much is the
translated game. Each driver function in DRV_FUNCS gets its time, its call
count and the GL time inside it, plus one "all, outermost" row that counts
only calls not nested in another hooked one. The hook is at the function's
entry: it notes the time and swaps the return address for a per-function
exit stub, so every `ret` and every tail call out of the function is caught
without finding them. A function already running (recursion) is passed
through untimed. The GL/SWAP sections are kept (v4 style, no GL profile).
The dump: Stack Dump = u16 time, u16 calls per entry, TLS +128 = u16 GL
time per entry, all per-frame averages.

VERSION 5 (BUILD 787, SEVENTH_NX_FRAME_PROBE=gl): the GL time per
FUNCTION. v4 measured 46% of a busy field frame inside GL, over ~7,000 calls.
This says which calls: every hooked import gets its own time and call count
over the field visit, and for each call whose first one to four integer
arguments equal the previous call's to the SAME function, a "repeat" count --
the calls a state filter could drop. (A lower bound for glEnable-style calls,
whose repeats interleave; an over-count for binds that differ per texture
unit.) The dump carries per-frame averages in place of the worst-frames
table: Stack Dump = entries 0..63, TLS +128 = entries 64..71, TLS +160 = the
repeat percentage of each, one byte each. `frameprobe_read.py` prints them.

VERSION 4 (BUILD 786, the multi-core question): slots 6 and 9 now time
the GPU DRIVER from the CPU's side -- every call the port makes into the
GL library (slot 6, GL) and eglSwapBuffers alone (slot 9, SWAP). They are
hooked at the module's own import stubs (the PLT), so every caller is
covered and nothing at a call site changes. The record's Kpx column carries
the number of GL calls in the frame instead. GL and SWAP OVERLAP the other
sections (a draw's GL calls are inside DRAW, the swap is inside DRVFLIP):
they say how much of those sections is time spent inside the GL driver --
the only part of the frame another core could take over. el@lim and drift
are gone (the limiter question they served was settled); debt stays.

VERSION 3 (after the v2 dumps): the timer pump measured 0.01 ms, so it is
NOT what fills the limiter. What v3 records instead, at the frame's LAST
limiter call, is the limiter's own view: elapsed = virtual clock - baseline
(guest 0xCFF8D8), the debt flag (0xCFFA98) and debt amount (0xCFFAA0), and,
per frame, how far the virtual clock drifted from the hardware counter. Slots
6/8/9 carry those (el, debt, drift); the TIMER/TCB hooks are gone.

VERSION 2 (after the first three hardware dumps): shader compiles and the
per-flip checker both measured 0.00 ms in every worst frame, so their slots
now time the emulated multimedia timer. In the first dumps the limiter
section held ~14.6 ms on frames whose work was ALREADY past the deadline --
time the limiter's own spin cannot account for, but a timer pump inside it
can. The kernel also clears x8..x28 on svcBreak (measured), so only x0..x7,
FP and LR carry data; the per-visit sums moved to the TLS dump.

plus per-frame counts: textures created (gfx_create_texture +0x4620) and the
pixels they carry, palette writes (gfx_drv_write_palette +0x10D9800), OGG
opens, shader compiles.

A frame ends where gfx_drv_flip hands over to its tail call, so every mode
(field, battle, menu, world) is recorded; only the field sections are split.

WHAT IT KEEPS
=============
Reset whenever the current field id (guest 0xCFF468, the maplist index ff7nx_daynight uses) changes:
  * sums of every section and of the frame period, over the whole field visit
  * how many frames ran over 16.9 / 18 / 20 / 25 / 33.4 ms
  * the SIXTEEN WORST frames of the visit (after its first 30 frames, so the
    fade-in load does not fill the table), each with its full breakdown
  * the last 52 frame periods

HOW IT GETS OUT
===============
Press the 3x booster (the port's speed toggle, multiplier 1 -> 3 at module
+0x12C99B8). On that edge the probe copies the worst-frames table onto the
live stack (first half) and into the thread's TLS (second half), loads the
aggregates into every register, and calls svcBreak. Atmosphere writes a crash report with the
registers, a 0x100-byte stack dump and a 0x100-byte TLS dump -- all of it
probe data. `frameprobe_read.py <report>` turns that back into a table.

The recompiled call sites get a wrapper that is transparent to its caller:
x9..x12 and x30 are saved and restored, and nothing in it sets the flags.
Native hooks use only registers the ABI already gives the callee.

CAVEAT: OGG opens also happen on the music/ambience worker threads. Their
time lands in whichever frame they overlap; two overlapping opens on different
threads can corrupt one sample. The count is what matters there.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import a64
import ff7nx_audio_cave as AC
import ff7nx_cave  # noqa: F401  (ANY_SPAN kept in call sites)
import nxmap
from ff7nx_audio_cave import Asm

A = a64

ENV = 'SEVENTH_NX_FRAME_PROBE'

(LOGIC, ANIM, DRAW, LIMIT, CATCHUP, DRVFLIP, TIMER, TEXLOAD, OGG,
 TCB) = range(10)
EL_SLOT, DEBT_SLOT, DRIFT_SLOT = TIMER, OGG, TCB       # v3 record slots
GL, SWAP = TIMER, TCB                                   # v4 sections
SECTION_NAMES = ('logic', 'anim', 'draw', 'limit', 'catchup', 'drvflip',
                 'gl', 'texload', 'debt', 'swap')

# recompiled call sites: (site, section). Each is a `bl` in stock.
CALL_SITES = (
    (0x946740, LOGIC), (0x94BEC0, LOGIC),
    (0x9470A4, ANIM), (0x94C678, ANIM),
    (0x94B898, DRAW), (0x94BAC8, DRAW),
    (0x94B920, LIMIT), (0x94BBCC, LIMIT),
    (0x94BA54, CATCHUP),
)
# native functions timed entry -> ret: (entry, entry word, rets, section)
TIMED = (
    (0x10D6BC0, 0xD10203FF, (0x10D7048,), TEXLOAD),
    (0x10FE090, 0xD100C3FF, (0x10FE0EC,), OGG),
)
# v4: GL entry points timed at their import stubs, with their INTEGER
# argument counts. Only functions whose arguments all fit in x0..x7 are
# hooked: the stub cave pushes 16 bytes, which would move a ninth argument
# that the callee reads from the caller's stack. The four texture/blit calls
# with nine or ten are left alone (TEXLOAD already times the texture path).
GL_ARGS = {
    'glActiveTexture': 1, 'glAttachShader': 2, 'glBindBuffer': 2,
    'glBindBufferBase': 3, 'glBindFramebuffer': 2, 'glBindRenderbuffer': 2,
    'glBindTexture': 2, 'glBlendEquationSeparate': 2,
    'glBlendFuncSeparate': 4, 'glBlitFramebuffer': 10, 'glBufferData': 4,
    'glCheckFramebufferStatus': 1, 'glClear': 1, 'glClearColor': 0,
    'glClearDepthf': 0, 'glClearStencil': 1, 'glColorMask': 4,
    'glCompileShader': 1, 'glCompressedTexImage2D': 8,
    'glCompressedTexSubImage2D': 9, 'glCreateProgram': 0,
    'glCreateShader': 1, 'glCullFace': 1, 'glDeleteBuffers': 2,
    'glDeleteFramebuffers': 2, 'glDeleteProgram': 1,
    'glDeleteRenderbuffers': 2, 'glDeleteShader': 1, 'glDeleteTextures': 2,
    'glDepthFunc': 1, 'glDepthMask': 1, 'glDisable': 1, 'glDrawArrays': 3,
    'glDrawElements': 4, 'glEnable': 1, 'glEnableVertexAttribArray': 1,
    'glFramebufferRenderbuffer': 4, 'glFramebufferTexture2D': 5,
    'glFrontFace': 1, 'glGenBuffers': 2, 'glGenFramebuffers': 2,
    'glGenRenderbuffers': 2, 'glGenTextures': 2, 'glGenerateMipmap': 1,
    'glGetBooleanv': 2, 'glGetError': 0, 'glGetFloatv': 2,
    'glGetIntegerv': 2, 'glGetProgramInfoLog': 4, 'glGetProgramiv': 3,
    'glGetRenderbufferParameteriv': 3, 'glGetShaderInfoLog': 4,
    'glGetShaderiv': 3, 'glGetString': 1, 'glGetTexParameteriv': 3,
    'glGetUniformBlockIndex': 2, 'glGetUniformLocation': 2,
    'glIsEnabled': 1, 'glLinkProgram': 1, 'glReadPixels': 7,
    'glRenderbufferStorage': 4, 'glScissor': 4, 'glShaderSource': 4,
    'glStencilFuncSeparate': 4, 'glStencilOpSeparate': 4,
    'glTexImage2D': 9, 'glTexParameteri': 3, 'glTexStorage2D': 5,
    'glTexSubImage2D': 9, 'glUniform1i': 2, 'glUniformBlockBinding': 3,
    'glUseProgram': 1, 'glVertexAttribPointer': 6, 'glViewport': 4,
}
SWAP_NAME = 'eglSwapBuffers'
# v6: the native gfx driver functions timed, by entry. Every one is an
# entry of the port's import table (TABLE, checked at build time). Left out:
# init/cleanup/lock/unlock, the three entries the probe already hooks
# (load_texture, write_palette, flip), the five that are a bare `ret`, and
# draw_smoothlines, a `b` into draw_flatlines (so it is counted there).
DRV_FUNCS = (
    (0x10D6760, 'setviewport'), (0x10D68C0, 'clear_all'),
    (0x10D68D0, 'clear'), (0x10D6A00, 'setbg'),
    (0x10D6A60, 'unload_texture'), (0x10D7C50, 'blendmode'),
    (0x10D7CA0, 'field_64'), (0x10D7E90, 'setrenderstate_6c'),
    (0x10D81F0, 'setrenderstate_70'), (0x10D8230, 'field_74'),
    (0x10D82A0, 'setrenderstate'), (0x10D82E0, 'field_80'),
    (0x10D8310, 'field_78'), (0x10D9000, 'field_84'),
    (0x10D90B0, 'begin_scene'), (0x10D9270, 'end_scene'),
    (0x10D9660, 'palette_changed'), (0x10D9970, 'setmatrix'),
    (0x10DA6D0, 'draw_deferred'), (0x10DAFF0, 'setrenderstate_flat2D'),
    (0x10DB060, 'setrenderstate_textured2D'),
    (0x10DB0D0, 'setrenderstate_paletted2D'),
    (0x10DB140, 'setrenderstate_paletted2D_bis'),
    (0x10DB1B0, 'draw_flat2D'), (0x10DB3D0, 'draw_textured2D'),
    (0x10DB4D0, 'draw_paletted2D'), (0x10DB770, 'setrenderstate_flat3D'),
    (0x10DB870, 'draw_flat3D'), (0x10DB9A0, 'setrenderstate_flatlines'),
    (0x10DBA00, 'setrenderstate_smoothlines'),
    (0x10DBA60, 'draw_flatlines'),
)
DRV_TOP_NAME = '(all, outermost)'
# v6b (BUILD 789b/c): what field_78 (the polygon set draw, 3.1 ms of its own
# per frame in fship_2) spends in its callees. Its three native helpers are
# hooked at their entries like the table functions (every caller counts,
# the model shadows' second draw included: ff7nx_shadows redirects both of
# field_78's calls to the submit helper, so those call sites are not
# stock). Its calls into game code are timed at each stock `bl` / `b CALLX`
# inside field_78; a site an earlier pass changed is left alone.
DRV_EXTRA = (
    (0x10D9D70, 'helper 0x10D9D70 (3D/2D submit)'),
    (0x10D9AA0, 'helper 0x10D9AA0'),
    (0x10D7ED0, 'helper 0x10D7ED0 (texture)'),
)
FIELD_78_SPAN = (0x10D8310, 0x10D9000)
DRV_SUBCALLS = (
    (0xA060, 'field_78 > game code (CALLX)'),
)
# v6e (BUILD 790): the translated game's share of DRAW. Every `bl` out of a
# GAME_GROUPS body is timed at the call site, one row per listed callee,
# with the outermost driver time spent inside it (in the row's GL slot:
# game code reaches GL only through the driver). A callee a group does not
# list goes to that group's "other" row; the guest-address translator
# (AC.GUEST_TRANSLATE, a few instructions at ~1 site in 8) is left alone.
# Names are x86 entries. v6f (790b): the first dumps (fship_2, mrkt2) put
# 5-8 ms in 0x6840DA and 1.4-1.7 ms of plain game code in 0x640F22, so
# field_draw_everything keeps only those two rows and both are split.
GAME_GROUPS = (
    # (body span, group label, ((callee, name), ...), other row name or
    # None: then a callee the group does not list is not hooked at all)
    ((0x9E6E80, 0x9E8270), 'field_draw_everything 0x63A60B', (
        (0xB54AE0, 'game 0x6840DA'), (0x9F2E60, 'game 0x640F22'),
    ), None),
    # v7b (790c): 6840DA's time is 0x660D6E (its driver draw, 3.7-5.5 ms)
    # and 0x660FBF (1.6-2.9 ms of game code), which is only the indirect
    # call through the gfx driver's +0x60, light_polygon_set: 0x6812F0,
    # which calls 0x68E92D. Both are split here.
    ((0xB54AE0, 0xB57DC0), 'game 0x6840DA', (
        (0xA856E0, '6840DA > 0x660D6E (draw)'),
        (0xA88380, '6840DA > 0x660FBF (light)'),
        (0xA1A0, '6840DA > indirect (A1A0)'),
    ), None),
    ((0xB4AEC0, 0xB4AF90), 'light_polygon_set 0x6812F0', (
        (0xB4AF90, '6812F0 > 0x68E92D'),
    ), None),
    ((0xB4AF90, 0xB4D1E0), 'game 0x68E92D', (
        (0xB87C80, '68E92D > 0x69BF90'), (0xB8FA40, '68E92D > 0x69C11F'),
        (0xB8E100, '68E92D > 0x68DE75'), (0xB8D120, '68E92D > 0x68DBF4'),
        (0xB8E020, '68E92D > 0x68DD1E'), (0xB8C310, '68E92D > 0x68DAE1'),
        (0xAE8C80, '68E92D > 0x670144'), (0xAE8EF0, '68E92D > 0x670196'),
        (0xB905A0, '68E92D > 0x68E880'), (0xA1A0, '68E92D > indirect (A1A0)'),
    ), None),
    # 640F22 (the field background, 1.4-1.7 ms on fewer than one frame in
    # two): 640F95, then 640C49 / 64085C when their flags are set, 641358,
    # 6465FB
    ((0x9F2E60, 0x9F30F0), 'game 0x640F22', (
        (0xA095B0, '640F22 > 0x6465FB'), (0xA08630, '640F22 > 0x641358'),
        (0xA07780, '640F22 > 0x640F95'), (0xA06DE0, '640F22 > 0x640C49'),
        (0xA059D0, '640F22 > 0x64085C'),
    ), None),
)
# what a version 7 dump (790b) carries in the game rows
GAME_GROUPS_V7 = (
    (None, 'field_draw_everything 0x63A60B', tuple((0, n) for n in (
        'game 0x6840DA', 'game 0x640F22')), 'draw_everything > the rest'),
    (None, 'game 0x6840DA', tuple((0, '6840DA > 0x' + n) for n in (
        '67C17F', '66CC3B', '6A2A73', '6A2A22', '6A29E4')) + (
        (0, '6840DA > indirect (A1A0)'),) + tuple(
        (0, '6840DA > 0x' + n) for n in (
            '69C69F', '67C9BE', '67BCFE', '67BCD3', '67AF75', '67BE71',
            '660FBF', '660D6E', '660BE3')) + ((0, '6840DA > memmove'),),
     '6840DA > other'),
    (None, 'game 0x640F22', tuple((0, '640F22 > 0x' + n) for n in (
        '6465FB', '641358', '640F95', '640C49', '64085C')),
     '640F22 > other'),
)


# what a version 6 dump (790, v6e) carries in the game rows
GAME_GROUPS_V6 = (
    (None, 'field_draw_everything 0x63A60B', tuple(
        (0, n) for n in ('native 0x10DF6E0 (switch)', 'game 0x640213',
                         'game 0x660C3A', 'game 0x6840DA', 'game 0x66E641',
                         'game 0x6F19F3', 'game 0x6ECA68', 'game 0x6778C8',
                         'game 0x666E43', 'game 0x666E0F', 'game 0x666DF4',
                         'game 0x660EEB', 'game 0x660EC0', 'game 0x660E95',
                         'game 0x660D1E', 'game 0x66067A', 'game 0x63AD97',
                         'game 0x644E90', 'game 0x64B95C', 'game 0x640F22',
                         'game 0x640EB7', 'game 0x60D549', 'game 0x60D4F3')),
     'draw_everything > other calls'),
)
DRV_VERSION = 8                           # v7b: GAME_GROUPS
GPU_VERSION = 11                          # BUILD 794: v8 + the GPU waits
# (v9 had the first three; v10, BUILD 796, adds load_texture's two; v11,
# BUILD 798, the wait before a split call made inside another one)
GPU_ROWS = ('gpu: before driver calls', 'gpu: at flip entry',
            'gpu: before eglSwapBuffers', 'gpu: before load_texture',
            'gpu: load_texture', 'gpu: inside callers, before split calls')
GPU_ROWS_BY_VERSION = {9: 3, 10: 5, 11: 6}
# v11 (BUILD 798): native passes timed on the GPU on their own, at any
# depth (end_scene's six, the render-mode switch the translated field calls
# directly, and the blit both use). They take the game rows' slots: a gpu
# build does not time the game's calls.
GPU_SPLIT = (
    (0x10DF6E0, 'switch 0x10DF6E0 (render mode)'),
    (0x10DF010, 'blit 0x10DF010'),
    (0x10DEB00, 'end_scene > 0x10DEB00'),
    (0x10DECA0, 'end_scene > 0x10DECA0'),
    (0x10DF0F0, 'end_scene > 0x10DF0F0'),
    (0x10DACB0, 'capture blit 0x10DACB0'),
    (0x10DC1B0, 'end_scene > 0x10DC1B0'),
    (0x10D9370, 'end_scene > 0x10D9370'),
)
GL_READ_PIXELS = 'glReadPixels'
# v12 (BUILD 799, SEVENTH_NX_FRAME_PROBE=layer): how much of the screen
# each foreground layer covers. The driver draws a field's depth-tested
# layers into an offscreen target (the RT at GOT +0x12CE5A8), cleared to 0,
# and merges each one over the whole screen in 0x10DF0F0 (2xSaI scale, depth
# copy, mergeforeground): about four full-screen passes per layer at field
# buffer 6. The submit helper's draws into that target are boxed (TL vertex
# x, y; any other vertex type marks the layer "box unknown"), and each merge
# that runs records its box. The rows take the game rows' slots, like v11's.
LAYER_VERSION = 12
LAYER_ROWS = (
    'layer: merges (t = box area / 64)',
    'layer: merges with a 3D draw (box unknown)',
    'layer: merges with nothing drawn',
    'layer: sum of box x min + 1024',
    'layer: sum of box x max + 1024',
    'layer: sum of box y min + 1024',
    'layer: sum of box y max + 1024',
    'layer: merges, box < 25% of 640x480',
    'layer: merges, box 25-50%',
    'layer: merges, box 50-75%',
    'layer: merges, box >= 75%',
    'layer: TL draws into the layer',
    'layer: 3D draws into the layer',
)
LAYER_SUBMIT = 0x10D9DC4           # submit helper, just after the state switch
LAYER_SUBMIT_WORD = 0x36000919     # tbz w25, #0, +0x10D9EE4
LAYER_SUBMIT_TBZ = 0x10D9EE4
LAYER_MERGE = 0x10DF0F0
LAYER_MERGE_WORD = 0xD10403FF      # sub sp, sp, #0x100
GOT_CUR_RT = 0x12CE630
GOT_LAYER_RT = 0x12CE5A8
GOT_MERGE_FLAG = 0x12CE458
LAYER_REF_AREA = 640 * 480


def game_groups(version=DRV_VERSION):
    return {6: GAME_GROUPS_V6, 7: GAME_GROUPS_V7}.get(version, GAME_GROUPS)


def game_rows(version=DRV_VERSION):
    """[(name, group index)] of every game row, in dump order."""
    out = []
    for g, (_span, _label, callees, other) in enumerate(game_groups(version)):
        out += [(n, g) for _va, n in callees]
        if other is not None:
            out.append((other, g))
    return out
DRV_TABLE = 0x12C9A70                     # name-keyed import table
DRV_TABLE_MAX = 512
DRV_DUMP_N = 64                           # entries the v6 dump carries
R_AARCH64_JUMP_SLOT = 1026
CREATE_TEXTURE = 0x4620
CREATE_TEXTURE_WORD = 0xD10283FF          # sub sp, sp, #0xa0
WRITE_PALETTE = 0x10D9800
WRITE_PALETTE_WORD = 0xF81B0FF9           # str x25, [sp, #-0x50]!
FLIP = 0x10DA880
FLIP_WORD = 0xFC1C0FE8                    # str d8, [sp, #-0x40]!
FLIP_TAIL = 0x10DABC0                     # b 0x10fa890
TROPHY_FN = 0x10FA890
RET = 0xD65F03C0
MULT = 0x12C99B8                          # booster multiplier, 1 <-> 3
FIELD_ID = 0xCFF468                       # guest u16 (ff7nx_daynight G_FIELD_ID, maplist index)

# ------------------------------------------------------------- BSS layout
START, ACC, CNT = 0x000, 0x080, 0x100
LAST_END, FRAMES, PHEAD, LAST_MULT = 0x140, 0x148, 0x14C, 0x150
PIX, CTEX, CPAL, LAST_FIELD = 0x154, 0x158, 0x15C, 0x160
SUMS = 0x180                              # 10 x u64
SUMP = 0x1D0                              # u64
BK = 0x1D8                                # 5 x u32
TOT = 0x1EC                               # tex, pal, ogg, shader, pix (u32)
BLOCK_END = 0x200                         # SUMS..BLOCK_END is regs x2..x17
REC = 0x200                               # 32 bytes
PRING = 0x220                             # 52 x u16
PRING_N = 32
TOP = 0x290                               # 16 x 32
TOP_N = 12
REGIMG = 0x490                            # 31 x u64
GPTR, EL, DAMT = 0x168, 0x170, 0x178      # v3: host ptr of guest 0xCFF000
DFLAG, NLIM, PREV_D = 0x590, 0x594, 0x598
BSS_BYTES = 0x5A0 + 16
PROF = 0x5C0                              # v5: 64-byte entry per GL hook
PROF_ENTRY = 64                           # time u64, count u32, repeats u32,
PROF_MAX = 72                             # valid u32, last args 4 x u64
PROF_BYTES = PROF_ENTRY * PROF_MAX
DPROF = PROF                              # v6: 32-byte entries: time u64,
DPROF_ENTRY = 32                          # count u32, pad, GL inside u64
DLIVE = PROF + 0x800                      # per function, never cleared:
DLIVE_ENTRY = 32                          # start, return address, GL at
                                          # start, busy u32
DEPTH = PROF + 0x1000                     # u32 depth, outermost start, its GL
GLMONO = PROF + 0x1020                    # GL+SWAP time, never cleared
ZERO_DRAW_TOTAL = False                   # 664b: see build_frame_end
ACCUM = 0x12CF200                         # the port's virtual rdtsc value
G_PAGE = 0xCFF000
G_BASELINE, G_DFLAG, G_DAMT = 0x8D8, 0xA98, 0xAA0

UNIT_SHIFT = 4                            # stored times are ticks >> 4
CPS = 19200000
BUCKETS_MS = (16.9, 18.0, 20.0, 25.0, 33.4)
WARMUP = 30
MAGIC = 0x42525046                        # 'FPRB'
VERSION = 4

# conditions
EQ, NE, HS, LO, HI, LS = 0, 1, 2, 3, 8, 9


# ---------------------------------------------------- encoders not in a64
def _pair_checked(fn):
    def wrapped(rt, rt2, rn, imm):
        if imm % 8 or not -512 <= imm <= 504:
            raise ValueError('ldp/stp offset %d out of range' % imm)
        return fn(rt, rt2, rn, imm)
    return wrapped


class _A:
    """a64, with the pair offsets range-checked (a64's are masked)."""
    def __getattr__(self, name):
        return getattr(a64, name)
    ldp64_off = staticmethod(_pair_checked(a64.ldp64_off))
    stp64_off = staticmethod(_pair_checked(a64.stp64_off))


A = _A()

def mrs_cntpct(rt):  return 0xD53BE020 | rt
def mrs_tpidrro(rt): return 0xD53BD060 | rt
def lsr64(rd, rn, sh): return 0xD340FC00 | (sh << 16) | (rn << 5) | rd
def mov_sp(rn): return 0x91000000 | (rn << 5) | 31          # mov sp, xN
def stp_zero_post16(rn): return 0xA8817C00 | (31 << 10) | (rn << 5) | 31
def udf(): return 0x00000000
def fmov_d0_x(rn): return 0x9E670000 | (rn << 5)
def fcvtzs_x_d0(rd): return 0x9E780000 | rd
def cneg_lt(rd): return 0xDA80A400 | (rd << 16) | (rd << 5) | rd
def cset_lt(rd): return 0x1A9FA7E0 | rd
GE = 10
def svc_break(): return 0xD40004C1                       # svc #0x26
def blr(rn): return 0xD63F0000 | (rn << 5)


def enabled():
    # BUILD 689: 'draw' builds the battle draw-order probe (ff7nx_drawprobe)
    # in the same slot, so every pass that yields to this probe yields to it
    # BUILD 787: 'gl' is this probe with the per-function GL profile (v5)
    # BUILD 789: 'drv' is this probe with the native driver profile (v6)
    # BUILD 799: 'layer' is the drv probe with the foreground-layer boxes
    return os.environ.get(ENV, '').strip().lower() in ('1', 'on', 'yes',
                                                       'true', 'draw', 'gl',
                                                       'drv', 'gpu', 'layer')


def gl_profile():
    return os.environ.get(ENV, '').strip().lower() == 'gl'


def drv_profile():
    return os.environ.get(ENV, '').strip().lower() in ('drv', 'gpu', 'layer')


def layer_profile():
    """BUILD 799: the 'drv' build with the foreground-layer boxes (v12)."""
    return os.environ.get(ENV, '').strip().lower() == 'layer'


def gpu_profile():
    """BUILD 794: the 'drv' build with the GPU waits (v9)."""
    return os.environ.get(ENV, '').strip().lower() == 'gpu'


def drv_names(version=DRV_VERSION):
    """What a v6/v7/v8/v9 dump's entries are, in order."""
    game = [n for n, _g in game_rows(min(version, DRV_VERSION))]
    if version == LAYER_VERSION:
        game = list(LAYER_ROWS) + [''] * (len(game) - len(LAYER_ROWS))
        return ([n for _va, n in DRV_FUNCS + DRV_EXTRA] + [DRV_TOP_NAME]
                + [n for _va, n in DRV_SUBCALLS] + game)
    if version >= 11:
        game = ([n for _va, n in GPU_SPLIT]
                + [''] * (len(game) - len(GPU_SPLIT)))
    return ([n for _va, n in DRV_FUNCS + DRV_EXTRA] + [DRV_TOP_NAME]
            + [n for _va, n in DRV_SUBCALLS] + game
            + list(GPU_ROWS[:GPU_ROWS_BY_VERSION.get(min(version, GPU_VERSION),
                                                    0)]))


def gpu_row(k):
    """Index of GPU_ROWS[k] in a v9 dump."""
    return len(drv_names(DRV_VERSION)) + k


def game_first():
    """Index of the first v6e game row (its GL slot is driver time)."""
    return len(DRV_FUNCS + DRV_EXTRA) + 1 + len(DRV_SUBCALLS)


def profile_names():
    """The hooked imports in hook order -- what a v5 dump's entries are.
    Every GL import of the module is in GL_ARGS (tested), so this is the
    same list gl_hooks builds; apply_to_nso refuses if it is not."""
    return sorted([n for n, k in GL_ARGS.items() if k <= 8] + [SWAP_NAME])


def _bl_target(word, va):
    if word >> 26 != 0x25:
        return None
    imm = word & 0x3FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return va + imm * 4


def _b_target(word, va):
    if word >> 26 != 0x05:
        return None
    imm = word & 0x3FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return va + imm * 4


# ------------------------------------------------------------- the caves
def build_wrapper(_e, addr, base, callee, sec):
    """bl-site wrapper: time `callee` into ACC[sec], transparent."""
    a = Asm(_e, addr)
    a.emit(A.stp64_pre(9, 10, A.SP, -48))
    a.emit(A.stp64_off(11, 30, A.SP, 16))
    a.emit(mrs_cntpct(9))
    AC.bss_ptr(a, 10, base + START + 8 * sec)
    a.emit(A.str64(9, 10, 0))
    a.emit(A.ldp64_off(9, 10, A.SP, 0))
    a.emit(A.ldr64(11, A.SP, 16))
    a.emit(A.bl(a.pc(), callee))
    a.emit(A.stp64_off(9, 10, A.SP, 0))
    a.emit(A.str64(11, A.SP, 16))
    _emit_accumulate(a, base, sec, count=True)
    a.emit(A.ldp64_off(9, 10, A.SP, 0))
    a.emit(A.ldp64_off(11, 30, A.SP, 16))
    a.emit(A.add_imm64(A.SP, A.SP, 48))
    a.emit(A.ret())
    return a.resolve()


def build_limit_wrapper(_e, addr, base, callee):
    """LIMIT wrapper that also snapshots the limiter's own inputs."""
    a = Asm(_e, addr)
    a.emit(A.stp64_pre(9, 10, A.SP, -64))
    a.emit(A.stp64_off(11, 12, A.SP, 16))
    a.emit(A.stp64_off(13, 30, A.SP, 32))
    a.emit(mrs_cntpct(9))
    AC.bss_ptr(a, 10, base + START + 8 * LIMIT)
    a.emit(A.str64(9, 10, 0))
    AC.bss_ptr(a, 13, base)
    a.emit(A.ldr64(10, 13, GPTR))
    a.cbz64(10, 'skip')
    a.emit(A.ldr64(11, 10, G_BASELINE))
    AC.bss_ptr(a, 12, ACCUM)
    a.emit(A.ldr64(12, 12, 0))
    a.emit(A.sub_reg64(11, 12, 11))
    a.emit(A.str64(11, 13, EL))
    a.emit(A.ldr(11, 10, G_DFLAG))
    a.emit(A.str_(11, 13, DFLAG))
    a.emit(A.ldr64(11, 10, G_DAMT))
    a.emit(A.str64(11, 13, DAMT))
    a.emit(A.ldr(11, 13, NLIM))
    a.emit(A.add_imm(11, 11, 1))
    a.emit(A.str_(11, 13, NLIM))
    a.label('skip')
    a.emit(A.ldp64_off(9, 10, A.SP, 0))
    a.emit(A.ldp64_off(11, 12, A.SP, 16))
    a.emit(A.ldr64(13, A.SP, 32))
    a.emit(A.bl(a.pc(), callee))
    a.emit(A.stp64_off(9, 10, A.SP, 0))
    a.emit(A.stp64_off(11, 12, A.SP, 16))
    _emit_accumulate(a, base, LIMIT, count=True)
    a.emit(A.ldp64_off(9, 10, A.SP, 0))
    a.emit(A.ldp64_off(11, 12, A.SP, 16))
    a.emit(A.ldp64_off(13, 30, A.SP, 32))
    a.emit(A.add_imm64(A.SP, A.SP, 64))
    a.emit(A.ret())
    return a.resolve()


def _emit_accumulate(a, base, sec, count):
    """ACC[sec] += now - START[sec]; CNT[sec]++ -- x9..x11, no flags."""
    a.emit(mrs_cntpct(9))
    AC.bss_ptr(a, 10, base + START + 8 * sec)
    a.emit(A.ldr64(11, 10, 0))
    a.emit(A.sub_reg64(9, 9, 11))
    AC.bss_ptr(a, 10, base + ACC + 8 * sec)
    a.emit(A.ldr64(11, 10, 0))
    a.emit(A.add_reg64(11, 11, 9))
    a.emit(A.str64(11, 10, 0))
    if count:
        AC.bss_ptr(a, 10, base + CNT + 4 * sec)
        a.emit(A.ldr(11, 10, 0))
        a.emit(A.add_imm(11, 11, 1))
        a.emit(A.str_(11, 10, 0))


def build_entry(_e, addr, base, sec, orig, resume, gsync=None, row=0):
    """Function entry: START[sec] = now; replay; resume. x16/x17 only.
    v9 (gsync): wait for the GPU first (x12, x13, x15 too)."""
    a = Asm(_e, addr)
    if gsync is not None:
        _emit_gsync_call(a, gsync, row, True)
    a.emit(mrs_cntpct(16))
    AC.bss_ptr(a, 17, base + START + 8 * sec)
    a.emit(A.str64(16, 17, 0))
    a.emit(orig)
    a.emit(A.b(a.pc(), resume))
    return a.resolve()


def build_exit(_e, addr, base, sec, gsync=None, row=0):
    """At a `ret`: accumulate, count, return. x9..x11 are the callee's.
    v10 (gsync): then wait for the GPU (x12, x13, x15 too)."""
    a = Asm(_e, addr)
    _emit_accumulate(a, base, sec, count=True)
    if gsync is not None:
        _emit_gsync_call(a, gsync, row, True)
    a.emit(A.ret())
    return a.resolve()


def build_stub_cave(_e, addr, got, sec, common, idx=None, check=None,
                    gsync=None, row=0):
    """v4: an import stub, timed. Replays the stub exactly -- x16 = the GOT
    slot and x17 = its target when the call is made, as the lazy binder
    expects -- with the start time and x30 kept on the stack, then hands
    (x16 = start, w15 = section) to the shared tail. v5 (idx given): w14 =
    the hook's profile index, and `check` (the argument-repeat test for its
    argument count, or None) runs first -- at a function's entry x9..x15
    hold nothing the caller needs, and x0..x7 are left as they came."""
    a = Asm(_e, addr)
    if gsync is not None:
        _emit_gsync_call(a, gsync, row, True)
    a.emit(mrs_cntpct(17))
    a.emit(A.stp64_pre(17, 30, A.SP, -16))
    if idx is not None:
        a.emit(A.movz(14, idx))
        if check is not None:
            a.emit(A.bl(a.pc(), check))
    a.emit(A.adrp(16, a.pc(), got & ~0xFFF))
    a.emit(A.ldr64(17, 16, got & 0xFFF))
    a.emit(A.add_imm64(16, 16, got & 0xFFF))
    a.emit(blr(17))
    a.emit(A.ldp64_post(16, 30, A.SP, 16))
    a.emit(A.movz(15, sec))
    if idx is not None:
        a.emit(A.movz(14, idx))
    a.emit(A.b(a.pc(), common))
    return a.resolve()


def build_arg_check(_e, addr, base, nargs):
    """v5: PROF[w14].repeats++ when x0..x(nargs-1) equal that hook's previous
    call; then remember them. x9, x10 and the flags only."""
    a = Asm(_e, addr)
    AC.bss_ptr(a, 9, base + PROF)
    a.emit(A.add_reg64_lsl(9, 9, 14, 6))
    a.emit(A.ldr(10, 9, 16))
    a.cbz(10, 'store')
    for k in range(nargs):
        a.emit(A.ldr64(10, 9, 24 + 8 * k))
        a.emit(A.cmp_reg64(k, 10))
        a.bcond('store', NE)
    a.emit(A.ldr(10, 9, 12))
    a.emit(A.add_imm(10, 10, 1))
    a.emit(A.str_(10, 9, 12))
    a.label('store')
    for k in range(nargs):
        a.emit(A.str64(k, 9, 24 + 8 * k))
    a.emit(A.movz(10, 1))
    a.emit(A.str_(10, 9, 16))
    a.emit(A.ret())
    return a.resolve()


def build_stub_common(_e, addr, base, profile=False, mono=False):
    """v4: ACC[w15] += now - x16; CNT[w15]++; ret. After the callee
    returned, so only registers a caller already expects clobbered by a
    call (x9, x14..x17, the flags) are touched; x0/x1 and v0.. (the
    results) are not. v5: also PROF[w14].time += and .count++. v6 (mono):
    also GLMONO +=, the GL clock the driver profile reads."""
    a = Asm(_e, addr)
    a.emit(mrs_cntpct(17))
    a.emit(A.sub_reg64(17, 17, 16))
    if mono:
        AC.bss_ptr(a, 16, base + GLMONO)
        a.emit(A.ldr64(9, 16, 0))
        a.emit(A.add_reg64(9, 9, 17))
        a.emit(A.str64(9, 16, 0))
    if profile:
        AC.bss_ptr(a, 16, base + PROF)
        a.emit(A.add_reg64_lsl(16, 16, 14, 6))
        a.emit(A.ldr64(9, 16, 0))
        a.emit(A.add_reg64(9, 9, 17))
        a.emit(A.str64(9, 16, 0))
        a.emit(A.ldr(9, 16, 8))
        a.emit(A.add_imm(9, 9, 1))
        a.emit(A.str_(9, 16, 8))
    AC.bss_ptr(a, 16, base + ACC)
    a.emit(A.add_reg64_lsl(16, 16, 15, 3))
    a.emit(A.ldr64(9, 16, 0))
    a.emit(A.add_reg64(9, 9, 17))
    a.emit(A.str64(9, 16, 0))
    AC.bss_ptr(a, 16, base + CNT)
    a.emit(A.add_reg64_lsl(16, 16, 15, 2))
    a.emit(A.ldr(9, 16, 0))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.str_(9, 16, 0))
    a.emit(A.ret())
    return a.resolve()


def relocate_first(word, va, at):
    """The words that run `word` (from `va`) at `at` and then continue at
    va + 4, or None if it cannot be moved. Plain words are copied; adrp is
    re-aimed at the same page; cbz/cbnz becomes a branch over a `b` to its
    old target. Any other PC-relative word is refused."""
    resume = va + 4
    if (word & 0x9F000000) == 0x90000000:                 # adrp
        imm = (((word >> 5) & 0x7FFFF) << 2) | ((word >> 29) & 3)
        if imm & (1 << 20):
            imm -= 1 << 21
        page = (va & ~0xFFF) + (imm << 12)
        return [A.adrp(word & 0x1F, at, page), A.b(at + 4, resume)]
    if (word & 0x7E000000) == 0x34000000:                 # cbz / cbnz
        imm = (word >> 5) & 0x7FFFF
        if imm & 0x40000:
            imm -= 0x80000
        target = va + imm * 4
        moved = (word & ~(0x7FFFF << 5)) | (2 << 5)       # -> at + 8
        return [moved, A.b(at + 4, resume), A.b(at + 8, target)]
    if ((word & 0x1F000000) == 0x10000000                 # adr / adrp
            or (word & 0x7C000000) == 0x14000000          # b / bl
            or (word & 0xFF000010) == 0x54000000          # b.cond
            or (word & 0x7E000000) == 0x36000000          # tbz / tbnz
            or (word & 0x3B000000) == 0x18000000):        # ldr literal
        return None
    return [word, A.b(at + 4, resume)]


GSYNC_FRAME = 0x150


def build_gsync(_e, addr, base, got):
    """v9: wait for the GPU. glReadPixels(0, 0, 1, 1, GL_RGBA,
    GL_UNSIGNED_BYTE, buf) through the GOT slot `got` (as its PLT stub
    would: x16 = the slot, x17 = its target), timed; DPROF[w13] and, when
    it differs, DPROF[w12] get the wait at +24. Every register a caller
    could hold live across it is kept: x0..x19, x30, q0..q7 (the
    arguments at an entry, the results at an exit) and the flags."""
    a = Asm(_e, addr)
    a.emit(A.sub_imm64(31, 31, GSYNC_FRAME))
    for k in range(0, 20, 2):
        a.emit(A.stp64_off(k, k + 1, A.SP, 8 * k))
    a.emit(A.str64(30, A.SP, 0xA0))
    for k in range(0, 8, 2):
        a.emit(A.stp_q_off(k, k + 1, A.SP, 0xB0 + 16 * k))
    a.emit(A.mrs_nzcv(9))
    a.emit(A.str64(9, A.SP, 0xA8))
    a.emit(mrs_cntpct(19))
    a.emit(A.movz(0, 0))
    a.emit(A.movz(1, 0))
    a.emit(A.movz(2, 1))
    a.emit(A.movz(3, 1))
    a.emit(A.movz(4, 0x1908))                 # GL_RGBA
    a.emit(A.movz(5, 0x1401))                 # GL_UNSIGNED_BYTE
    a.emit(A.add_imm64(6, A.SP, 0x130))
    a.emit(A.adrp(16, a.pc(), got & ~0xFFF))
    a.emit(A.ldr64(17, 16, got & 0xFFF))
    a.emit(A.add_imm64(16, 16, got & 0xFFF))
    a.emit(blr(17))
    a.emit(mrs_cntpct(9))
    a.emit(A.sub_reg64(9, 9, 19))
    a.emit(A.ldp64_off(12, 13, A.SP, 96))
    AC.bss_ptr(a, 10, base + DPROF + 24)
    a.emit(A.add_reg64_lsl(11, 10, 13, 5))
    a.emit(A.ldr64(14, 11, 0))
    a.emit(A.add_reg64(14, 14, 9))
    a.emit(A.str64(14, 11, 0))
    a.emit(A.cmp_reg64(12, 13))
    a.bcond('one', EQ)
    a.emit(A.add_reg64_lsl(11, 10, 12, 5))
    a.emit(A.ldr64(14, 11, 0))
    a.emit(A.add_reg64(14, 14, 9))
    a.emit(A.str64(14, 11, 0))
    a.label('one')
    a.emit(A.ldr64(9, A.SP, 0xA8))
    a.emit(A.msr_nzcv(9))
    for k in range(0, 8, 2):
        a.emit(A.ldp_q_off(k, k + 1, A.SP, 0xB0 + 16 * k))
    a.emit(A.ldr64(30, A.SP, 0xA0))
    for k in range(0, 20, 2):
        a.emit(A.ldp64_off(k, k + 1, A.SP, 8 * k))
    a.emit(A.add_imm64(31, 31, GSYNC_FRAME))
    a.emit(A.ret())
    return a.resolve()


def _emit_gsync_call(a, gsync, row, keep30):
    """bl gsync for GPU_ROWS row `row` (w12 = w13 = its index); x30 kept
    in x15 when `keep30` (at a function entry x9..x15 are free)."""
    if keep30:
        a.emit(A.mov_reg64(15, 30))
    a.emit(A.movz(13, row))
    a.emit(A.movz(12, row))
    a.emit(A.bl(a.pc(), gsync))
    if keep30:
        a.emit(A.mov_reg64(30, 15))


def build_drv_entry(_e, addr, idx, common, word, va):
    """v6: a driver function's entry. w14 = its index, x15 = where to come
    back; the common part notes the start and swaps x30 for the exit stub.
    Then the moved first word, and on into the function. At an entry only
    x9..x17 and the flags are free to use; x0..x8 are the arguments."""
    a = Asm(_e, addr)
    a.emit(A.movz(14, idx))
    a.emit(A.adr(15, a.pc(), a.pc() + 8))
    a.emit(A.b(a.pc(), common))
    for w in relocate_first(word, va, a.pc()):
        a.emit(w)
    return a.resolve()


def build_drv_common_entry(_e, addr, base, exits, gsync=None, split=None,
                           top=None):
    """v6: DLIVE[w14] = (now, x30, GLMONO), busy; the outermost call also
    starts DEPTH's clock; x30 = exits + 8 * w14. A function already busy
    (recursion) runs untimed. Returns to x15. x9..x17 and the flags only.
    v9 (gsync): an outermost call first waits for the GPU. v11: so does a
    split call (index >= `split`) made inside another call, onto
    GPU_ROWS[5] and `top` (the caller's GPU work so far: still inside the
    outermost call)."""
    a = Asm(_e, addr)
    AC.bss_ptr(a, 16, base + DLIVE)
    a.emit(A.add_reg64_lsl(16, 16, 14, 5))
    a.emit(A.ldr(9, 16, 24))
    a.cbnz(9, 'busy')
    a.emit(A.movz(9, 1))
    a.emit(A.str_(9, 16, 24))
    a.emit(A.str64(30, 16, 8))
    if gsync is not None:
        AC.bss_ptr(a, 11, base + DEPTH)
        a.emit(A.ldr(12, 11, 0))
        if split is None:
            a.cbnz(12, 'nosync')
        else:
            a.cbz(12, 'outer')
            a.emit(A.cmp_imm(14, split))
            a.bcond('nosync', LO)
            a.emit(A.movz(13, gpu_row(5)))
            a.emit(A.movz(12, top))
            a.emit(A.bl(a.pc(), gsync))
            a.b('nosync')
            a.label('outer')
        _emit_gsync_call(a, gsync, gpu_row(0), False)
        a.label('nosync')
    a.emit(mrs_cntpct(9))
    a.emit(A.str64(9, 16, 0))
    AC.bss_ptr(a, 10, base + GLMONO)
    a.emit(A.ldr64(10, 10, 0))
    a.emit(A.str64(10, 16, 16))
    AC.bss_ptr(a, 11, base + DEPTH)
    a.emit(A.ldr(12, 11, 0))
    a.emit(A.add_imm(13, 12, 1))
    a.emit(A.str_(13, 11, 0))
    a.cbnz(12, 'inner')
    a.emit(A.str64(9, 11, 8))
    a.emit(A.str64(10, 11, 16))
    a.label('inner')
    # the sizing pass (_e == 0) runs at address 0, out of adr's reach
    a.emit(A.adr(30, a.pc(), exits) if _e else A.nop())
    a.emit(A.add_reg64_lsl(30, 30, 14, 3))
    a.label('busy')
    a.emit(A.ret(15))
    return a.resolve()


def fcvtzs_w_s(rd, rn):
    return 0x1E380000 | (rn << 5) | rd


def _got_ptr(a, reg, got):
    """xreg = *(*got): the variable a GOT slot points at."""
    a.emit(A.adrp(reg, a.pc(), got & ~0xFFF))
    a.emit(A.ldr64(reg, reg, got & 0xFFF))
    a.emit(A.ldr64(reg, reg, 0))


def _row(base, first, k):
    return base + DPROF + DPROF_ENTRY * (first + k)


def build_layer_submit(_e, addr, base, first):
    """v12: replaces the submit helper's `tbz w25, #0, ...` right after its
    layer state switch. While the current target is the layer target, a TL
    draw (w24 == 3) widens the layer's box (DLIVE[first]: x min, x max,
    y min, y max, has-box, has-3D) by its w23 vertices at x22 (32 bytes, x
    and y the first two floats); any other draw marks it unknown. x0..x17
    and v0..v7 are dead here (the helper just called 0x10DCD90 and reloads
    everything after)."""
    a = Asm(_e, addr)
    _got_ptr(a, 9, GOT_CUR_RT)
    _got_ptr(a, 10, GOT_LAYER_RT)
    a.emit(A.cmp_reg64(9, 10))
    a.bcond('out', NE)
    AC.bss_ptr(a, 12, base + DLIVE + DLIVE_ENTRY * first)
    a.emit(A.cmp_imm(24, 3))
    a.bcond('d3', NE)
    AC.bss_ptr(a, 13, _row(base, first, 11))
    _inc32(a, 13, 8)
    a.emit(A.mov_reg64(14, 22))
    a.emit(A.mov_reg(15, 23))
    a.cbz(15, 'out')
    for r, off in ((0, 0), (1, 4), (2, 8), (3, 12), (4, 16)):
        a.emit(A.ldr(r, 12, off))
    a.label('loop')
    a.emit(A.ldr_s(0, 14, 0))
    a.emit(A.ldr_s(1, 14, 4))
    a.emit(fcvtzs_w_s(5, 0))
    a.emit(fcvtzs_w_s(6, 1))
    a.cbnz(4, 'grow')
    a.emit(A.mov_reg(0, 5))
    a.emit(A.mov_reg(1, 5))
    a.emit(A.mov_reg(2, 6))
    a.emit(A.mov_reg(3, 6))
    a.emit(A.movz(4, 1))
    a.b('next')
    a.label('grow')
    a.emit(A.cmp_reg(5, 0))
    a.emit(A.csel(0, 5, 0, A.LT))
    a.emit(A.cmp_reg(5, 1))
    a.emit(A.csel(1, 5, 1, A.GT))
    a.emit(A.cmp_reg(6, 2))
    a.emit(A.csel(2, 6, 2, A.LT))
    a.emit(A.cmp_reg(6, 3))
    a.emit(A.csel(3, 6, 3, A.GT))
    a.label('next')
    a.emit(A.add_imm64(14, 14, 32))
    a.emit(A.sub_imm(15, 15, 1))
    a.cbnz(15, 'loop')
    for r, off in ((0, 0), (1, 4), (2, 8), (3, 12), (4, 16)):
        a.emit(A.str_(r, 12, off))
    a.b('out')
    a.label('d3')
    AC.bss_ptr(a, 13, _row(base, first, 12))
    _inc32(a, 13, 8)
    a.emit(A.movz(5, 1))
    a.emit(A.str_(5, 12, 20))
    a.label('out')
    a.emit(A.tbnz(25, 0, a.pc(), a.pc() + 8))
    a.emit(A.b(a.pc(), LAYER_SUBMIT_TBZ))
    a.emit(A.b(a.pc(), LAYER_SUBMIT + 4))
    return a.resolve()


def build_layer_merge(_e, addr, base, first):
    """v12: 0x10DF0F0's entry. When its flag says a merge runs, the layer's
    box goes to the rows (count, area >> 2 in the time slot, the four
    extents + 1024, a coverage bucket of 640x480) and is reset. x9..x17 and
    the flags only (w0 is the argument); then the moved `sub sp`."""
    a = Asm(_e, addr)
    a.emit(A.adrp(9, a.pc(), GOT_MERGE_FLAG & ~0xFFF))
    a.emit(A.ldr64(9, 9, GOT_MERGE_FLAG & 0xFFF))
    a.emit(A.ldrb(9, 9, 0))
    a.cbz(9, 'out')
    AC.bss_ptr(a, 12, base + DLIVE + DLIVE_ENTRY * first)
    AC.bss_ptr(a, 13, _row(base, first, 0))
    _inc32(a, 13, 8)
    a.emit(A.ldr(10, 12, 20))
    a.cbz(10, 'tl')
    AC.bss_ptr(a, 14, _row(base, first, 1))
    _inc32(a, 14, 8)
    a.b('reset')
    a.label('tl')
    a.emit(A.ldr(10, 12, 16))
    a.cbnz(10, 'box')
    AC.bss_ptr(a, 14, _row(base, first, 2))
    _inc32(a, 14, 8)
    a.b('reset')
    a.label('box')
    for k, off in ((3, 0), (4, 4), (5, 8), (6, 12)):
        a.emit(A.ldr(10, 12, off))
        a.emit(A.add_imm(10, 10, 1024))
        AC.bss_ptr(a, 14, _row(base, first, k))
        a.emit(A.ldr64(9, 14, 0))
        a.emit(A.add_reg64(9, 9, 10))
        a.emit(A.str64(9, 14, 0))
    a.emit(A.ldr(14, 12, 0))
    a.emit(A.ldr(15, 12, 4))
    a.emit(A.ldr(16, 12, 8))
    a.emit(A.ldr(17, 12, 12))
    a.emit(A.sub_reg(10, 15, 14))
    a.emit(A.sub_reg(11, 17, 16))
    a.emit(A.mul(10, 10, 11))                  # the box's area
    a.emit(A.lsr(11, 10, 2))
    a.emit(A.ldr64(9, 13, 0))
    a.emit(A.add_reg64(9, 9, 11))
    a.emit(A.str64(9, 13, 0))
    for k, limit in ((7, LAYER_REF_AREA // 4), (8, LAYER_REF_AREA // 2),
                     (9, 3 * LAYER_REF_AREA // 4)):
        AC.mov32(a, 9, limit)
        a.emit(A.cmp_reg(10, 9))
        a.bcond('b%d' % k, LO)
    AC.bss_ptr(a, 14, _row(base, first, 10))
    a.b('bump')
    for k in (7, 8, 9):
        a.label('b%d' % k)
        AC.bss_ptr(a, 14, _row(base, first, k))
        a.b('bump')
    a.label('bump')
    _inc32(a, 14, 8)
    a.label('reset')
    a.emit(A.str_(31, 12, 16))
    a.emit(A.str_(31, 12, 20))
    a.label('out')
    a.emit(LAYER_MERGE_WORD)
    a.emit(A.b(a.pc(), LAYER_MERGE + 4))
    return a.resolve()


def build_drv_exits(_e, addr, n, common_exit):
    """v6: n two-word exit stubs, `movz w14, #i; b common_exit`."""
    a = Asm(_e, addr)
    for i in range(n):
        a.emit(A.movz(14, i))
        a.emit(A.b(a.pc(), common_exit))
    return a.resolve()


def _add_elapsed(a, now, start_reg, start_off, acc_reg, acc_off):
    """[acc + acc_off] += now - [start + start_off]. x10, x11."""
    a.emit(A.ldr64(10, start_reg, start_off))
    a.emit(A.sub_reg64(10, now, 10))
    a.emit(A.ldr64(11, acc_reg, acc_off))
    a.emit(A.add_reg64(11, 11, 10))
    a.emit(A.str64(11, acc_reg, acc_off))


def _inc32(a, reg, off):
    a.emit(A.ldr(11, reg, off))
    a.emit(A.add_imm(11, 11, 1))
    a.emit(A.str_(11, reg, off))


def build_sub_site(_e, addr, idx, pre, post, callee):
    """v6b: replaces a `bl callee` / `b callee` in field_78. The caller's
    x30 goes to x15, `pre` notes the start (w9 = 1 if this row is already
    running), the callee is called, and `post` adds the time and returns to
    the saved x30. Nothing on the stack moves, so stack arguments still
    reach the callee. x9..x16 are what a call may clobber anyway."""
    a = Asm(_e, addr)
    a.emit(A.movz(14, idx))
    a.emit(A.mov_reg64(15, 30))
    a.emit(A.bl(a.pc(), pre))
    a.cbnz(9, 'busy')
    a.emit(A.bl(a.pc(), callee))
    a.emit(A.movz(14, idx))
    a.emit(A.b(a.pc(), post))
    a.label('busy')
    a.emit(A.mov_reg64(30, 15))
    a.emit(A.b(a.pc(), callee))
    return a.resolve()


def build_sub_pre(_e, addr, base, mono=GLMONO):
    """v6b: DLIVE[w14] = (now, x15, [mono]) and busy, unless busy already:
    w9 = that busy flag. x9..x11, x16. v6e: mono = the outermost driver
    row's time for the game rows."""
    a = Asm(_e, addr)
    AC.bss_ptr(a, 16, base + DLIVE)
    a.emit(A.add_reg64_lsl(16, 16, 14, 5))
    a.emit(A.ldr(9, 16, 24))
    a.cbnz(9, 'out')
    a.emit(A.movz(10, 1))
    a.emit(A.str_(10, 16, 24))
    a.emit(A.str64(15, 16, 8))
    a.emit(mrs_cntpct(10))
    a.emit(A.str64(10, 16, 0))
    AC.bss_ptr(a, 11, base + mono)
    a.emit(A.ldr64(11, 11, 0))
    a.emit(A.str64(11, 16, 16))
    a.label('out')
    a.emit(A.ret())
    return a.resolve()


def sub_sites(text):
    """[(site, callee index, is_b)] of every bl / b in field_78 to a
    DRV_SUBCALLS callee (in the stock module: compare before hooking)."""
    lo, hi = FIELD_78_SPAN
    want = {va: k for k, (va, _n) in enumerate(DRV_SUBCALLS)}
    out = []
    for va in range(lo, hi, 4):
        w = struct.unpack_from('<I', text, va)[0]
        for tgt, is_b in ((_bl_target(w, va), False),
                          (_b_target(w, va), True)):
            if tgt in want:
                out.append((va, want[tgt], is_b))
    return out


def game_sites(text):
    """v6e: [(site, row k, callee)] of every `bl` out of each GAME_GROUPS
    body but the guest translator; k counts from the first game row."""
    out = []
    first = 0
    for (lo, hi), _label, callees, other in GAME_GROUPS:
        want = {va: k for k, (va, _n) in enumerate(callees)}
        for va in range(lo, hi, 4):
            tgt = _bl_target(struct.unpack_from('<I', text, va)[0], va)
            if tgt is None or lo <= tgt < hi or tgt == AC.GUEST_TRANSLATE:
                continue
            if tgt in want:
                out.append((va, first + want[tgt], tgt))
            elif other is not None:
                out.append((va, first + len(callees), tgt))
        first += len(callees) + (other is not None)
    return out


def build_drv_common_exit(_e, addr, base, top, outermost=True, mono=GLMONO,
                          gsync=None, split=None):
    """v6: the function w14 returned (or tail-called out and that returned).
    DPROF[w14] += time, call, GL inside; the outermost call also adds to
    DPROF[top]; then back to the saved return address. x0..x8 and the FP
    registers are the results and are not touched: x9..x16 and flags only."""
    a = Asm(_e, addr)
    AC.bss_ptr(a, 16, base + DLIVE)
    a.emit(A.add_reg64_lsl(16, 16, 14, 5))
    AC.bss_ptr(a, 15, base + DPROF)
    a.emit(A.add_reg64_lsl(15, 15, 14, 5))
    a.emit(mrs_cntpct(9))
    AC.bss_ptr(a, 12, base + mono)
    a.emit(A.ldr64(12, 12, 0))
    _add_elapsed(a, 9, 16, 0, 15, 0)
    _inc32(a, 15, 8)
    _add_elapsed(a, 12, 16, 16, 15, 16)
    if outermost:
        AC.bss_ptr(a, 13, base + DEPTH)
        a.emit(A.ldr(10, 13, 0))
        a.emit(A.sub_imm(10, 10, 1))
        a.emit(A.str_(10, 13, 0))
        a.cbnz(10, 'inner')
        AC.bss_ptr(a, 15, base + DPROF + DPROF_ENTRY * top)
        _add_elapsed(a, 9, 13, 8, 15, 0)
        _inc32(a, 15, 8)
        _add_elapsed(a, 12, 13, 16, 15, 16)
        if gsync is not None:
            # v9: what this outermost call queued, on the GPU
            a.emit(A.mov_reg(13, 14))
            a.emit(A.movz(12, top))
            a.emit(A.bl(a.pc(), gsync))
        if split is not None:
            a.b('done')
        a.label('inner')
        if split is not None:
            # v11: a split call inside another one: its row and top (all
            # the GPU work of outermost calls stays on top)
            a.emit(A.cmp_imm(14, split))
            a.bcond('done', LO)
            a.emit(A.mov_reg(13, 14))
            a.emit(A.movz(12, top))
            a.emit(A.bl(a.pc(), gsync))
            a.label('done')
    a.emit(A.ldr64(30, 16, 8))
    a.emit(A.str_(31, 16, 24))
    a.emit(A.ret())
    return a.resolve()


def build_counter(_e, addr, base, off, orig, resume, pixels=False):
    """Function entry: ++u32 at `off` (and PIX += w2*w3 >> 10)."""
    a = Asm(_e, addr)
    if pixels:
        a.emit(A.mul(16, 2, 3))
        AC.bss_ptr(a, 17, base + PIX)
        a.emit(A.ldr(9, 17, 0))
        a.emit(A.add_reg_lsr(9, 9, 16, 10))
        a.emit(A.str_(9, 17, 0))
    AC.bss_ptr(a, 17, base + off)
    a.emit(A.ldr(9, 17, 0))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.str_(9, 17, 0))
    a.emit(orig)
    a.emit(A.b(a.pc(), resume))
    return a.resolve()


def build_flip_tail(_e, addr, base, frame_end):
    """Replaces flip's `b 0x10fa890`: time it, then close the frame."""
    a = Asm(_e, addr)
    a.emit(A.stp64_pre(29, 30, A.SP, -16))
    a.emit(mrs_cntpct(9))
    # DRVFLIP ends here: ACC[5] += now - START[5]
    AC.bss_ptr(a, 10, base + START + 8 * DRVFLIP)
    a.emit(A.ldr64(11, 10, 0))
    a.emit(A.sub_reg64(11, 9, 11))
    AC.bss_ptr(a, 10, base + ACC + 8 * DRVFLIP)
    a.emit(A.ldr64(12, 10, 0))
    a.emit(A.add_reg64(12, 12, 11))
    a.emit(A.str64(12, 10, 0))
    a.emit(A.bl(a.pc(), TROPHY_FN))
    a.emit(A.stp64_pre(0, 1, A.SP, -16))
    a.emit(A.bl(a.pc(), frame_end))
    a.emit(A.ldp64_post(0, 1, A.SP, 16))
    a.emit(A.ldp64_post(29, 30, A.SP, 16))
    a.emit(A.ret())
    return a.resolve()


def _sat16(a, reg):
    """reg = min(reg >> 4, 0xFFFF) on X reg. Uses x12."""
    a.emit(lsr64(reg, reg, UNIT_SHIFT))
    a.emit(A.movz(12, 0xFFFF))
    a.emit(A.cmp_reg64(reg, 12))
    a.emit(A.csel64(reg, 12, reg, HI))


def _min_w(a, reg, cap):
    """w reg = min(w reg, cap) for cap <= 0xFFFF. Uses w12."""
    a.emit(A.movz(12, cap))
    a.emit(A.cmp_reg(reg, 12))
    a.emit(A.csel(reg, 12, reg, HI))


def _zero(a, base, lo, hi, label):
    """Zero [base+lo, base+hi), 16-byte multiples. x9, x10."""
    AC.bss_ptr(a, 9, base + lo)
    AC.bss_ptr(a, 10, base + hi)
    a.label(label)
    a.emit(stp_zero_post16(9))
    a.emit(A.cmp_reg64(9, 10))
    a.bcond(label, LO)


def _emit_v3_slots(a, base):
    """Record slots el / debt / drift (abs, 16-tick units) and w26 = flags:
    bit0 debt flag at the last limiter call, bit1 el < 0, bit2 debt < 0,
    bit3 drift < 0 (virtual clock gained on the counter), bits4-7 limiter
    calls this frame. x19 = now, x20 = base, x24 = record."""
    a.emit(A.ldr(26, 20, DFLAG))
    a.emit(A.cmp_imm(26, 0))
    a.emit(A.csinc(26, 31, 31, EQ))          # w26 = dflag != 0
    # el
    a.emit(A.ldr64(9, 20, EL))
    a.emit(A.cmp_reg64(9, 31))
    a.emit(cset_lt(10))
    a.emit(A.orr_lsl(26, 26, 10, 1))
    a.emit(cneg_lt(9))
    _sat16(a, 9)
    a.emit(A.strh(9, 24, 2 + 2 * EL_SLOT))
    # debt amount (double) -> ticks
    a.emit(A.ldr64(9, 20, DAMT))
    a.emit(fmov_d0_x(9))
    a.emit(fcvtzs_x_d0(9))
    a.emit(A.cmp_reg64(9, 31))
    a.emit(cset_lt(10))
    a.emit(A.orr_lsl(26, 26, 10, 2))
    a.emit(cneg_lt(9))
    _sat16(a, 9)
    a.emit(A.strh(9, 24, 2 + 2 * DEBT_SLOT))
    # drift = (counter - virtual) now minus the same last frame
    AC.bss_ptr(a, 10, ACCUM)
    a.emit(A.ldr64(10, 10, 0))
    a.emit(A.sub_reg64(10, 19, 10))
    a.emit(A.ldr64(9, 20, PREV_D))
    a.emit(A.str64(10, 20, PREV_D))
    a.emit(A.sub_reg64(9, 10, 9))
    a.emit(A.cmp_reg64(9, 31))
    a.emit(cset_lt(10))
    a.emit(A.orr_lsl(26, 26, 10, 3))
    a.emit(cneg_lt(9))
    _sat16(a, 9)
    a.emit(A.strh(9, 24, 2 + 2 * DRIFT_SLOT))
    a.emit(A.ldr(9, 20, NLIM))
    _min_w(a, 9, 15)
    a.emit(A.orr_lsl(26, 26, 9, 4))


def _emit_v4_slots(a, base):
    """v4: the debt slot and w26 = flags (bit0 debt flag, bit2 debt < 0,
    bits4-7 limiter calls). Slots 6 and 9 keep the GL / SWAP times the
    section loop stored. x20 = base, x24 = record."""
    a.emit(A.ldr(26, 20, DFLAG))
    a.emit(A.cmp_imm(26, 0))
    a.emit(A.csinc(26, 31, 31, EQ))          # w26 = dflag != 0
    a.emit(A.ldr64(9, 20, DAMT))
    a.emit(fmov_d0_x(9))
    a.emit(fcvtzs_x_d0(9))
    a.emit(A.cmp_reg64(9, 31))
    a.emit(cset_lt(10))
    a.emit(A.orr_lsl(26, 26, 10, 2))
    a.emit(cneg_lt(9))
    _sat16(a, 9)
    a.emit(A.strh(9, 24, 2 + 2 * DEBT_SLOT))
    a.emit(A.ldr(9, 20, NLIM))
    _min_w(a, 9, 15)
    a.emit(A.orr_lsl(26, 26, 9, 4))


def _emit_profile_dump(a, base):
    """v5, at the dump: per-frame averages of each PROF entry -- u16 time
    (16-tick units) and u16 calls, entries 0..63 onto the live stack, 64..71
    at TLS +128 -- and a repeat percentage byte per entry at TLS +160.
    x20 = base; x9..x15 only."""
    a.emit(A.ldr(13, 20, FRAMES))
    a.emit(A.cmp_imm(13, 0))
    a.emit(A.csinc(13, 13, 31, NE))          # frames, at least 1
    AC.bss_ptr(a, 14, base + PROF)
    a.emit(A.add_imm64(15, A.SP, 0))         # out: the live stack
    a.emit(A.movz(12, 0))                    # entry index
    a.label('p_loop')
    a.emit(A.ldr64(9, 14, 0))
    a.emit(lsr64(9, 9, UNIT_SHIFT))          # 16-tick units fit 32 bits
    a.emit(A.udiv(9, 9, 13))                 # for 3.6 M frames of 10 ms
    _min_w9(a)
    a.emit(A.strh(9, 15, 0))
    a.emit(A.ldr(9, 14, 8))
    a.emit(A.udiv(9, 9, 13))
    _min_w9(a)
    a.emit(A.strh(9, 15, 2))
    # repeat percentage byte at TLS + 160 + index
    a.emit(A.ldr(9, 14, 12))
    a.emit(A.movz(10, 100))
    a.emit(A.mul(9, 9, 10))
    a.emit(A.ldr(10, 14, 8))
    a.emit(A.udiv(9, 9, 10))                 # count 0 -> 0
    a.emit(mrs_tpidrro(10))
    a.emit(A.add_reg64(10, 10, 12))
    a.emit(A.strb(9, 10, 160))
    a.emit(A.add_imm64(14, 14, PROF_ENTRY))
    a.emit(A.add_imm64(15, 15, 4))
    a.emit(A.add_imm(12, 12, 1))
    a.emit(A.cmp_imm(12, 64))
    a.bcond('p_same', NE)
    a.emit(mrs_tpidrro(15))
    a.emit(A.add_imm64(15, 15, 128))         # entries 64.. go to TLS +128
    a.label('p_same')
    a.emit(A.cmp_imm(12, PROF_MAX))
    a.bcond('p_loop', LO)


def _emit_drv_dump(a, base, slot=16):
    """v6, at the dump: per-frame averages of each DPROF entry -- u16 time
    and u16 calls onto the live stack, u16 GL time at TLS +128. x20 = base;
    x9..x15 only."""
    a.emit(A.ldr(13, 20, FRAMES))
    a.emit(A.cmp_imm(13, 0))
    a.emit(A.csinc(13, 13, 31, NE))          # frames, at least 1
    AC.bss_ptr(a, 14, base + DPROF)
    a.emit(A.add_imm64(15, A.SP, 0))
    a.emit(mrs_tpidrro(11))
    a.emit(A.add_imm64(11, 11, 128))
    a.emit(A.movz(12, 0))
    a.label('d_loop')
    a.emit(A.ldr64(9, 14, 0))
    a.emit(lsr64(9, 9, UNIT_SHIFT))
    a.emit(A.udiv(9, 9, 13))
    _min_w9(a)
    a.emit(A.strh(9, 15, 0))
    a.emit(A.ldr(9, 14, 8))
    a.emit(A.udiv(9, 9, 13))
    _min_w9(a)
    a.emit(A.strh(9, 15, 2))
    a.emit(A.ldr64(9, 14, slot))
    a.emit(lsr64(9, 9, UNIT_SHIFT))
    a.emit(A.udiv(9, 9, 13))
    _min_w9(a)
    a.emit(A.strh(9, 11, 0))
    a.emit(A.add_imm64(14, 14, DPROF_ENTRY))
    a.emit(A.add_imm64(15, 15, 4))
    a.emit(A.add_imm64(11, 11, 2))
    a.emit(A.add_imm(12, 12, 1))
    a.emit(A.cmp_imm(12, DRV_DUMP_N))
    a.bcond('d_loop', LO)


def _min_w9(a):
    """w9 = min(w9, 0xFFFF). w10."""
    a.emit(A.movz(10, 0xFFFF))
    a.emit(A.cmp_reg(9, 10))
    a.emit(A.csel(9, 10, 9, HI))


def build_frame_end(_e, addr, base, profile=False, drv=False):
    a = Asm(_e, addr)
    a.emit(A.stp64_pre(29, 30, A.SP, -96))
    a.emit(A.stp64_off(19, 20, A.SP, 16))
    a.emit(A.stp64_off(21, 22, A.SP, 32))
    a.emit(A.stp64_off(23, 24, A.SP, 48))
    a.emit(A.stp64_off(25, 26, A.SP, 64))
    a.emit(mrs_cntpct(19))
    AC.bss_ptr(a, 20, base)
    # period
    a.emit(A.ldr64(9, 20, LAST_END))
    a.emit(A.sub_reg64(21, 19, 9))
    a.emit(A.str64(19, 20, LAST_END))
    # field id
    AC.mov32(a, 0, FIELD_ID)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.ldrh(22, 0, 0))
    AC.mov32(a, 0, G_PAGE)
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.str64(0, 20, GPTR))
    a.emit(A.ldr(9, 20, LAST_FIELD))
    a.emit(A.cmp_reg(9, 22))
    a.bcond('same_field', EQ)
    a.emit(A.str_(22, 20, LAST_FIELD))
    a.emit(A.str_(31, 20, FRAMES))
    _zero(a, base, SUMS, BLOCK_END, 'z_sums')
    _zero(a, base, TOP, TOP + 32 * TOP_N, 'z_top')
    if profile:
        _zero(a, base, PROF, PROF + PROF_BYTES, 'z_prof')
    if drv:
        _zero(a, base, DPROF, DPROF + DPROF_ENTRY * DRV_DUMP_N, 'z_dprof')
    a.label('same_field')
    a.emit(A.ldr(23, 20, FRAMES))
    a.emit(A.add_imm(23, 23, 1))
    a.emit(A.str_(23, 20, FRAMES))
    # ---- the record
    a.emit(A.add_imm64(24, 20, REC))
    a.emit(A.mov_reg64(9, 21))
    _sat16(a, 9)
    a.emit(A.strh(9, 24, 0))
    a.emit(A.mov_reg(25, 9))                 # w25 = period units
    for k in range(10):
        a.emit(A.ldr64(9, 20, ACC + 8 * k))
        a.emit(A.ldr64(10, 20, SUMS + 8 * k))
        a.emit(A.add_reg64(10, 10, 9))
        a.emit(A.str64(10, 20, SUMS + 8 * k))
        _sat16(a, 9)
        a.emit(A.strh(9, 24, 2 + 2 * k))
    # counts
    _emit_v4_slots(a, base)
    for lo_off, hi_off, slot, tot_lo, tot_hi in (
            (CTEX, CPAL, 22, TOT + 0, TOT + 4),):
        a.emit(A.ldr(9, 20, lo_off))
        a.emit(A.ldr(11, 20, tot_lo))
        a.emit(A.add_reg(11, 11, 9))
        a.emit(A.str_(11, 20, tot_lo))
        _min_w(a, 9, 255)
        a.emit(A.ldr(10, 20, hi_off))
        a.emit(A.ldr(11, 20, tot_hi))
        a.emit(A.add_reg(11, 11, 10))
        a.emit(A.str_(11, 20, tot_hi))
        if ZERO_DRAW_TOTAL:
            # BUILD 664b (diagnostic): with the 'pal' counter on the draw
            # function, TOT+12 counts presented frames with NO draw at all
            a.cbnz(10, 'drew')
            a.emit(A.ldr(11, 20, TOT + 12))
            a.emit(A.add_imm(11, 11, 1))
            a.emit(A.str_(11, 20, TOT + 12))
            a.label('drew')
        a.emit(A.mov_reg(13, 9))
        a.emit(A.mov_reg(9, 10))
        _min_w(a, 9, 255)
        a.emit(A.orr_lsl(9, 13, 9, 8))
        a.emit(A.strh(9, 24, slot))
    # ogg opens | flags
    a.emit(A.ldr(9, 20, CNT + 4 * OGG))
    a.emit(A.ldr(11, 20, TOT + 8))
    a.emit(A.add_reg(11, 11, 9))
    a.emit(A.str_(11, 20, TOT + 8))
    _min_w(a, 9, 255)
    a.emit(A.orr_lsl(9, 9, 26, 8))
    a.emit(A.strh(9, 24, 24))
    a.emit(A.ldr(9, 20, CNT + 4 * GL))       # v4: GL calls, not Kpixels
    a.emit(A.ldr(11, 20, TOT + 16))
    a.emit(A.add_reg(11, 11, 9))
    a.emit(A.str_(11, 20, TOT + 16))
    _min_w(a, 9, 0xFFFF)
    a.emit(A.strh(9, 24, 26))
    a.emit(A.strh(23, 24, 28))
    a.emit(A.strh(22, 24, 30))
    # ---- period sum and buckets (loads excluded: saturated period)
    a.emit(A.movz(12, 0xFFFF))
    a.emit(A.cmp_reg(25, 12))
    a.bcond('no_sum', EQ)
    a.emit(A.ldr64(10, 20, SUMP))
    a.emit(A.add_reg64(10, 10, 21))
    a.emit(A.str64(10, 20, SUMP))
    a.label('no_sum')
    for i, ms in enumerate(BUCKETS_MS):
        ticks = int(round(ms * CPS / 1000.0))
        AC.mov32(a, 10, ticks)
        a.emit(A.cmp_reg64(21, 10))
        a.bcond('bk%d' % i, LS)
        a.emit(A.ldr(11, 20, BK + 4 * i))
        a.emit(A.add_imm(11, 11, 1))
        a.emit(A.str_(11, 20, BK + 4 * i))
        a.label('bk%d' % i)
    # ---- period ring
    a.emit(A.ldr(9, 20, PHEAD))
    a.emit(A.add_imm64(10, 20, PRING))
    a.emit(A.add_reg64_lsl(10, 10, 9, 1))
    a.emit(A.strh(25, 10, 0))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.cmp_imm(9, PRING_N))
    a.emit(A.csel(9, 9, 31, LO))
    a.emit(A.str_(9, 20, PHEAD))
    # ---- top 16 (after warm-up, not loads)
    a.emit(A.cmp_imm(23, WARMUP))
    a.bcond('no_top', LS)
    a.emit(A.movz(12, 0xFFFF))
    a.emit(A.cmp_reg(25, 12))
    a.bcond('no_top', EQ)
    a.emit(A.add_imm64(9, 20, TOP))          # cursor
    a.emit(A.mov_reg64(26, 9))                # min slot
    a.emit(A.ldrh(13, 9, 0))                  # min value
    a.emit(A.add_imm64(10, 20, TOP + 32 * TOP_N))
    a.label('scan')
    a.emit(A.ldrh(11, 9, 0))
    a.emit(A.cmp_reg(11, 13))
    a.emit(A.csel(13, 11, 13, LO))
    a.emit(A.csel64(26, 9, 26, LO))
    a.emit(A.add_imm64(9, 9, 32))
    a.emit(A.cmp_reg64(9, 10))
    a.bcond('scan', LO)
    a.emit(A.cmp_reg(25, 13))
    a.bcond('no_top', LS)
    a.emit(A.ldp64_off(9, 10, 24, 0))
    a.emit(A.stp64_off(9, 10, 26, 0))
    a.emit(A.ldp64_off(9, 10, 24, 16))
    a.emit(A.stp64_off(9, 10, 26, 16))
    a.label('no_top')
    # ---- clear this frame's accumulators
    _zero(a, base, ACC, CNT + 0x40, 'z_acc')
    a.emit(A.str64(31, 20, EL))
    a.emit(A.str64(31, 20, DAMT))
    a.emit(A.str_(31, 20, DFLAG))
    a.emit(A.str_(31, 20, NLIM))
    a.emit(A.str_(31, 20, PIX))
    a.emit(A.str_(31, 20, CTEX))
    a.emit(A.str_(31, 20, CPAL))
    # ---- trigger: booster 1 -> 3
    AC.bss_ptr(a, 9, MULT)
    a.emit(A.ldr(9, 9, 0))
    a.emit(A.ldr(10, 20, LAST_MULT))
    a.emit(A.str_(9, 20, LAST_MULT))
    a.emit(A.cmp_imm(10, 1))
    a.bcond('done', NE)
    a.emit(A.cmp_imm(9, 3))
    a.bcond('done', NE)
    a.b('dump')
    a.label('done')
    a.emit(A.ldp64_off(19, 20, A.SP, 16))
    a.emit(A.ldp64_off(21, 22, A.SP, 32))
    a.emit(A.ldp64_off(23, 24, A.SP, 48))
    a.emit(A.ldp64_off(25, 26, A.SP, 64))
    a.emit(A.ldp64_post(29, 30, A.SP, 96))
    a.emit(A.ret())
    # ---- dump: build the register image, then crash on purpose
    a.label('dump')
    a.emit(A.add_imm64(24, 20, REGIMG))
    AC.mov32(a, 9, MAGIC)
    AC.mov32(a, 10, (GPU_VERSION if gpu_profile() else LAYER_VERSION
                     if layer_profile() else DRV_VERSION) if drv
             else 5 if profile else VERSION)
    a.emit(A.bfi64(9, 10, 32, 32))
    a.emit(A.str64(9, 24, 0))
    a.emit(A.ldr(9, 20, FRAMES))
    a.emit(A.bfi64(9, 22, 32, 16))
    a.emit(A.ldr(10, 20, PHEAD))
    a.emit(A.bfi64(9, 10, 48, 16))
    a.emit(A.str64(9, 24, 8))
    # regs x2..x7, FP, LR: the last 32 periods (the kernel zeroes x8..x28)
    for i in range(0, 48, 8):
        a.emit(A.ldr64(9, 20, PRING + i))
        a.emit(A.str64(9, 24, 16 + i))
    a.emit(A.ldr64(9, 20, PRING + 48))
    a.emit(A.str64(9, 24, 8 * 29))
    a.emit(A.ldr64(9, 20, PRING + 56))
    a.emit(A.str64(9, 24, 8 * 30))
    # worst frames 0..7 -> the live stack ("Stack Dump"); the per-visit sums
    # block and worst frames 8..11 -> the thread's TLS ("TLS Dump"). SP stays
    # on the real stack: creport only dumps a stack in the stack region.
    a.emit(mrs_tpidrro(10))
    a.emit(A.add_imm64(13, 20, TOP))
    a.emit(A.add_imm64(14, 20, SUMS))
    for i in range(0, 256, 16):
        a.emit(A.ldp64_off(11, 12, 13, i))
        a.emit(A.stp64_off(11, 12, A.SP, i))
    for i in range(0, BLOCK_END - SUMS, 16):
        a.emit(A.ldp64_off(11, 12, 14, i))
        a.emit(A.stp64_off(11, 12, 10, i))
    for i in range(0, 128, 16):
        a.emit(A.ldp64_off(11, 12, 13, 256 + i))
        a.emit(A.stp64_off(11, 12, 10, 128 + i))
    if profile:
        _emit_profile_dump(a, base)
    if drv:
        _emit_drv_dump(a, base, 24 if gpu_profile() else 16)
    a.emit(A.mov_reg64(30, 24))
    for r in range(0, 30, 2):
        a.emit(A.ldp64_off(r, r + 1, 30, 8 * r))
    a.emit(A.ldr64(30, 30, 240))
    # svcBreak, not a CPU exception: nnSdk's user exception handler would
    # catch a fault and abort from its own context (FINDINGS-302 -- every
    # register zero). A Break is reported with THIS thread's registers.
    # w0 = 'FPRB' has bit 31 clear, so it is not a notification-only break.
    a.emit(svc_break())
    a.emit(udf())
    return a.resolve()


# ------------------------------------------------------------- imports
def _image(segs, raw):
    """The module image: each decompressed segment at its memory offset."""
    end = max(mo + len(r) for (_fo, mo, _sz), r in zip(segs, raw))
    img = bytearray(end)
    for (_fo, mo, _sz), r in zip(segs, raw):
        img[mo:mo + len(r)] = r
    return img


def import_stubs(segs, raw):
    """{import name: (stub va, GOT slot)} for every PLT stub in .text.

    The jump-slot relocations name each GOT slot; a stub is the four words
    `adrp x16, P; ldr x17, [x16, #o]; add x16, x16, #o; br x17` whose P+o
    is one of those slots. All four words are checked."""
    img = _image(segs, raw)
    mod0 = struct.unpack_from('<I', img, 4)[0]
    if img[mod0:mod0 + 4] != b'MOD0':
        raise ValueError('no MOD0 header')
    dyn = mod0 + struct.unpack_from('<i', img, mod0 + 4)[0]
    tags = {}
    while True:
        tag, val = struct.unpack_from('<qQ', img, dyn)
        dyn += 16
        if tag == 0:
            break
        tags.setdefault(tag, val)
    strtab, symtab = tags[5], tags[6]
    jmprel, pltsz = tags[23], tags[2]
    slots = {}
    for off in range(jmprel, jmprel + pltsz, 24):
        where, info, _add = struct.unpack_from('<QQq', img, off)
        if info & 0xFFFFFFFF != R_AARCH64_JUMP_SLOT:
            continue
        name_off = struct.unpack_from('<I', img, symtab + 24 * (info >> 32))[0]
        end = img.index(b'\0', strtab + name_off)
        slots[where] = img[strtab + name_off:end].decode('latin-1')
    text = raw[0]
    out = {}
    for va in range(0, len(text) - 15, 4):
        w0, w1, w2, w3 = struct.unpack_from('<4I', text, va)
        if (w0 & 0x9F00001F) != 0x90000010 or w3 != 0xD61F0220:
            continue
        if (w1 & 0xFFC003FF) != 0xF9400211 or (w2 & 0xFFC003FF) != 0x91000210:
            continue
        imm = (((w0 >> 5) & 0x7FFFF) << 2) | ((w0 >> 29) & 3)
        if imm & (1 << 20):
            imm -= 1 << 21
        page = (va & ~0xFFF) + (imm << 12)
        lo = ((w1 >> 10) & 0xFFF) * 8
        if ((w2 >> 10) & 0xFFF) != lo or (w2 >> 22) & 1:
            continue
        name = slots.get(page + lo)
        if name is not None and name not in out:
            out[name] = (va, page + lo)
    return out


def gl_hooks(segs, raw):
    """[(name, stub va, GOT slot, section)] the v4 probe times, and the
    GL imports it deliberately leaves alone."""
    stubs = import_stubs(segs, raw)
    hooks, skipped = [], []
    for name in sorted(stubs):
        va, got = stubs[name]
        if name == SWAP_NAME:
            hooks.append((name, va, got, SWAP))
        elif name.startswith('gl'):
            if GL_ARGS.get(name, 99) <= 8:
                hooks.append((name, va, got, GL))
            else:
                skipped.append(name)
    return hooks, skipped


# ------------------------------------------------------------- applying
def _verify(text):
    """(call sites usable, problems) -- refuses a word it does not expect."""
    bad, sites = [], []
    for site, sec in CALL_SITES:
        w = struct.unpack_from('<I', text, site)[0]
        tgt = _bl_target(w, site)
        if tgt is None:
            bad.append('call site +0x%X holds %08X, not a bl' % (site, w))
        else:
            sites.append((site, sec, tgt))
    for entry, word, rets, _sec in TIMED:
        if struct.unpack_from('<I', text, entry)[0] != word:
            bad.append('+0x%X entry is not %08X' % (entry, word))
        for r in rets:
            if struct.unpack_from('<I', text, r)[0] != RET:
                bad.append('+0x%X is not ret' % r)
    for va, word in ((CREATE_TEXTURE, CREATE_TEXTURE_WORD),
                     (WRITE_PALETTE, WRITE_PALETTE_WORD), (FLIP, FLIP_WORD)):
        if struct.unpack_from('<I', text, va)[0] != word:
            bad.append('+0x%X is not %08X' % (va, word))
    if _b_target(struct.unpack_from('<I', text, FLIP_TAIL)[0],
                 FLIP_TAIL) != TROPHY_FN:
        bad.append('+0x%X is not b 0x%X' % (FLIP_TAIL, TROPHY_FN))
    return sites, bad


# ------------------------------------------------------------- code space
# The padding pool is spent by the time the late passes run (measured on a
# finished build: ~195 usable words, almost all single-word holes). The probe
# needs ~1000. It borrows the translated body of a function PROVEN dead
# instead: x86 0x623D28, the 25 KB PSX tile rasteriser that
# ff7nx_fieldbg already proves unreachable (reached only from 0x620BD3, which
# nothing reaches). Its ARM body is 72 KB, and a probe build is diagnostic --
# the shipping build never carries this.
DEAD_TRAMPOLINE = 0x620BD3
DEAD_BODY = 0x623D28


def _dead_region(src, stock=None):
    import ff7nx_fieldbg
    m = nxmap.Main(src)
    spans = []
    for va in (DEAD_TRAMPOLINE, DEAD_BODY):
        why = ff7nx_fieldbg._liveness(m, va, spans)
        if why:
            raise ValueError('x86 0x%X is not dead: %s' % (va, why))
        spans.append(m.extent(va))
    lo, hi = m.extent(DEAD_BODY)
    if stock is not None:
        st = nxmap.Main(stock)
        if st.text[lo:hi] != m.text[lo:hi]:
            raise ValueError('the dead body at +0x%X was already modified '
                             'by another pass' % lo)
    return lo, hi


class Bump:
    """Contiguous allocator in the dead body, 16-byte aligned caves."""

    def __init__(self, lo, hi):
        self.at, self.hi = (lo + 15) & ~15, hi
        self.placed = {}

    def put(self, build):
        n = len(build(0, lambda i: 4 * i))
        entry = self.at
        words = build(entry, lambda i: entry + 4 * i)
        if len(words) != n or entry + 4 * n > self.hi:
            raise ValueError('probe cave does not fit')
        for i, w in enumerate(words):
            self.placed[entry + 4 * i] = w
        self.at = (entry + 4 * n + 15) & ~15
        return entry


# BUILD 786: the timing probe has a dead body of its OWN, so a probe build
# keeps every shipping pass. Until 786 it borrowed ff7nx_deadspace's 'probe'
# part, which is the same space as 'light', 'subcam' and 'blank' -- so a
# timing build silently ran without the model lighting, the sub-unit camera
# and the blank-frame hold, and measured a lighter game than the one shipped.
# x86 0x66ED3D (12 KB of ARM) is proven dead in the module being patched AND
# unreferenced in ff7_en (no call/jump to it, its address nowhere in the
# exe), every build. Its last 0x40 bytes are left alone too: the padding
# allocator takes the tail words of dead bodies (measured on a full 785
# build: 0x621835, the first choice, had three tail words taken, and this
# one none). The draw probe (=draw) still uses the shared part.
OWN_DEAD_X86 = 0x66ED3D
OWN_GUARD = 0x40                 # never at the entry the liveness proof checks
OWN_TAIL = 0x40                  # nor in the tail the padding allocator uses


def uses_shared_space():
    """True when a probe build takes ff7nx_deadspace's 'probe' part (the
    draw probe), so the passes that own that space must skip."""
    import ff7nx_drawprobe
    return enabled() and ff7nx_drawprobe.enabled()


def own_region(src, stock=None, exe=None):
    """(lo, hi) of the timing probe's dead body in `src`, proven dead."""
    import ff7nx_fieldbg
    m = nxmap.Main(src)
    why = ff7nx_fieldbg._liveness(m, OWN_DEAD_X86)
    if not why:
        if exe is None:
            path = os.path.join(nxpaths_root(), 'dump', 'romfs', 'ff7',
                                'resources', 'ff7_1.02', 'ff7_en')
            with open(path, 'rb') as handle:
                exe = handle.read()
        import ff7nx_fxinterp
        why = ff7nx_fxinterp._x86_unreferenced(exe, OWN_DEAD_X86)
    if why:
        raise ValueError('x86 0x%X is not dead: %s' % (OWN_DEAD_X86, why))
    lo, hi = m.extent(OWN_DEAD_X86)
    lo, hi = lo + OWN_GUARD, hi - OWN_TAIL
    if stock is not None:
        if nxmap.Main(stock).text[lo:hi] != m.text[lo:hi]:
            raise ValueError('the probe body at +0x%X was already modified '
                             'by another pass' % lo)
    return lo, hi


def drv_table(src):
    """{function va: [names]} of the gfx_drv_* entries of the port's import
    table (0x20 bytes each, +0x08 name, +0x10 function; both relocated)."""
    m = nxmap.Main(src)
    out = {}
    for i in range(DRV_TABLE_MAX):
        e = DRV_TABLE + 0x20 * i
        name_off = m.rel.get(e + 0x08)
        if name_off is None:
            break
        name = m.img[name_off:m.img.find(b'\0', name_off)].decode('latin1')
        if name.startswith('gfx_drv_'):
            out.setdefault(m.rel.get(e + 0x10), []).append(name[8:])
    return out


def _is_sp_prologue(w):
    """sub sp, sp, #imm / stp, str or str d pre-indexed on sp: a first word
    that sets nothing but sp and memory."""
    return ((w & 0xFF8003FF) == 0xD10003FF
            or (w & 0xFFC003E0) == 0xA98003E0
            or (w & 0xFFE00FE0) == 0xF8000FE0
            or (w & 0xFFE00FE0) == 0xFC000FE0)


def _second_word_hook(text, stock_text, va, placed):
    """(va + 4, its word) when the entry at va is now a pass's `b` and both
    the stock first word (a pure sp prologue, so x9..x17 hold nothing the
    function reads) and the second word are untouched. A cave that replays
    the first word and resumes at va + 4 then runs into the hook with the
    caller's x30, so the timing starts one word late and is otherwise the
    same. None otherwise."""
    word = struct.unpack_from('<I', text, va)[0]
    if _b_target(word, va) is None or va + 4 in placed:
        return None
    if not _is_sp_prologue(struct.unpack_from('<I', stock_text, va)[0]):
        return None
    second = struct.unpack_from('<I', text, va + 4)[0]
    if second != struct.unpack_from('<I', stock_text, va + 4)[0]:
        return None
    return va + 4, second


def _hook_drv(src, text, stock, put, base, placed, gsync=None):
    """v6: hook every DRV_FUNCS entry that is what the table says and is
    still the stock word. Returns ([names hooked], [names skipped, why])."""
    table = drv_table(src)
    for va, name in DRV_FUNCS:
        if name not in table.get(va, ()):
            raise ValueError('gfx_drv_%s is not at +0x%X in the import table'
                             % (name, va))
    stock_text = nxmap.Main(stock).text if stock is not None else None
    funcs = DRV_FUNCS + DRV_EXTRA
    top = len(funcs)
    if len(drv_names(GPU_VERSION)) > DRV_DUMP_N:
        raise ValueError('too many driver functions for the v6 dump')
    # v11: a gpu build times GPU_SPLIT in the game rows' slots instead
    split = game_first() if gsync is not None else None
    entries = list(enumerate(funcs))
    n_exits = top
    if split is not None:
        entries += [(split + k, f) for k, f in enumerate(GPU_SPLIT)]
        n_exits = split + len(GPU_SPLIT)
    cx = put(lambda e, ad: build_drv_common_exit(e, ad, base, top,
                                                 gsync=gsync, split=split))
    exits = put(lambda e, ad: build_drv_exits(e, ad, n_exits, cx))
    ce = put(lambda e, ad: build_drv_common_entry(e, ad, base, exits, gsync,
                                                  split, top))
    hooked, skipped = [], []
    for idx, (va, name) in entries:
        word = struct.unpack_from('<I', text, va)[0]
        if va in placed:
            skipped.append('%s (probe hook)' % name)
            continue
        if stock_text is not None and \
                struct.unpack_from('<I', stock_text, va)[0] != word:
            # BUILD 789d: a pass that owns the entry (blankhold counts every
            # submit there) and resumes at va + 4 -- hook the second word.
            alt = _second_word_hook(text, stock_text, va, placed)
            if alt is None:
                skipped.append('%s (changed by a pass)' % name)
                continue
            va, word = alt
            name += ' (at +4)'
        if relocate_first(word, va, 0) is None:
            skipped.append('%s (first word is PC-relative)' % name)
            continue
        cave = put(lambda e, ad, i=idx, w=word, v=va:
                   build_drv_entry(e, ad, i, ce, w, v))
        placed[va] = A.b(va, cave)
        hooked.append(name)
    # v6b: field_78's callees
    sites = sub_sites(stock_text if stock_text is not None else text)
    keep = []
    for site, k, is_b in sites:
        if stock_text is not None and struct.unpack_from('<I', text, site) \
                != struct.unpack_from('<I', stock_text, site):
            skipped.append('CALLX site +0x%X (changed by a pass)' % site)
        else:
            keep.append((site, k, is_b))
    sites = keep
    if sites:
        pre = put(lambda e, ad: build_sub_pre(e, ad, base))
        post = put(lambda e, ad: build_drv_common_exit(e, ad, base, top,
                                                       False))
        for site, k, is_b in sites:
            if site in placed:
                raise ValueError('field_78 call site +0x%X already hooked'
                                 % site)
            callee = DRV_SUBCALLS[k][0]
            cave = put(lambda e, ad, i=top + 1 + k, c=callee:
                       build_sub_site(e, ad, i, pre, post, c))
            placed[site] = A.b(site, cave) if is_b else A.bl(site, cave)
        hooked.append('field_78 game-code calls (%d sites)' % len(sites))
    # v12: the foreground-layer boxes, in the game rows' slots
    layer = layer_profile()
    if layer:
        for va, want in ((LAYER_SUBMIT, LAYER_SUBMIT_WORD),
                         (LAYER_MERGE, LAYER_MERGE_WORD)):
            if va in placed or struct.unpack_from('<I', text, va)[0] != want:
                raise ValueError('layer probe: +0x%X is not stock' % va)
        first = game_first()
        sc = put(lambda e, ad: build_layer_submit(e, ad, base, first))
        mc = put(lambda e, ad: build_layer_merge(e, ad, base, first))
        placed[LAYER_SUBMIT] = A.b(LAYER_SUBMIT, sc)
        placed[LAYER_MERGE] = A.b(LAYER_MERGE, mc)
        hooked.append('foreground layer boxes (submit +0x%X, merge +0x%X)'
                      % (LAYER_SUBMIT, LAYER_MERGE))
    # v6e: the game's calls (GAME_GROUPS), with the driver time inside each
    # (not in a gpu or layer build: v11's / v12's rows take their slots)
    ref = stock_text if stock_text is not None else text
    gsites = []
    for site, k, tgt in (game_sites(ref) if split is None and not layer
                         else ()):
        if struct.unpack_from('<I', text, site) \
                != struct.unpack_from('<I', ref, site) or site in placed:
            skipped.append('game call site +0x%X (changed by a pass)' % site)
        else:
            gsites.append((site, k, tgt))
    if gsites:
        mono = DPROF + DPROF_ENTRY * top
        gpre = put(lambda e, ad: build_sub_pre(e, ad, base, mono))
        gpost = put(lambda e, ad: build_drv_common_exit(e, ad, base, top,
                                                        False, mono))
        first = game_first()
        caves = {}                     # a cave keeps no site of its own
        for site, k, tgt in gsites:
            if (k, tgt) not in caves:
                caves[k, tgt] = put(lambda e, ad, i=first + k, c=tgt:
                                    build_sub_site(e, ad, i, gpre, gpost, c))
            placed[site] = A.bl(site, caves[k, tgt])
        hooked.append('game calls (%d sites)' % len(gsites))
    return hooked, skipped


def nxpaths_root():
    import nxpaths
    return nxpaths.ROOT


def apply_to_nso(src, dest, stock=None):
    import ff7nx_drawprobe
    if ff7nx_drawprobe.enabled():
        return ff7nx_drawprobe.apply_to_nso(src, dest, stock)
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    sites, bad = _verify(text)
    if bad:
        raise ValueError('; '.join(bad))
    lo, hi = own_region(src, stock)                     # BUILD 786: its own
    scratch = AC.scratch_base(blob, segs)
    base = (scratch + 15) & ~15
    pool = Bump(lo, hi)
    placed = {}

    def put(build, span=None):
        return pool.put(build)

    profile = gl_profile()
    drv = drv_profile()
    gsync = None
    if gpu_profile():
        stubs = import_stubs(segs, raw)
        if GL_READ_PIXELS not in stubs:
            raise ValueError('no %s import for the GPU probe' % GL_READ_PIXELS)
        rp_got = stubs[GL_READ_PIXELS][1]
        gsync = put(lambda e, ad: build_gsync(e, ad, base, rp_got))
    frame_end = put(lambda e, ad: build_frame_end(e, ad, base, profile, drv))
    tail = put(lambda e, ad: build_flip_tail(e, ad, base, frame_end),
               ff7nx_cave.ANY_SPAN)
    placed[FLIP_TAIL] = A.b(FLIP_TAIL, tail)
    fe = put(lambda e, ad: build_entry(e, ad, base, DRVFLIP, FLIP_WORD,
                                       FLIP + 4, gsync, gpu_row(1)),
             ff7nx_cave.ANY_SPAN)
    placed[FLIP] = A.b(FLIP, fe)
    wrappers = {}
    for site, sec, tgt in sites:
        key = (tgt, sec)
        if key not in wrappers:
            if sec == LIMIT:
                wrappers[key] = put(lambda e, ad, t=tgt:
                                    build_limit_wrapper(e, ad, base, t))
            else:
                wrappers[key] = put(lambda e, ad, t=tgt, s=sec:
                                    build_wrapper(e, ad, base, t, s),
                                    ff7nx_cave.ANY_SPAN)
        placed[site] = A.bl(site, wrappers[key])
    for entry, word, rets, sec in TIMED:
        # v10: load_texture's GPU time (its uploads), on its own rows
        gs = gsync if sec == TEXLOAD else None
        ent = put(lambda e, ad, s=sec, w=word, r=entry + 4, g=gs:
                  build_entry(e, ad, base, s, w, r, g, gpu_row(3)),
                  ff7nx_cave.ANY_SPAN)
        placed[entry] = A.b(entry, ent)
        ex = put(lambda e, ad, s=sec, g=gs:
                 build_exit(e, ad, base, s, g, gpu_row(4)),
                 ff7nx_cave.ANY_SPAN)
        for r in rets:
            placed[r] = A.b(r, ex)
    ct = put(lambda e, ad: build_counter(e, ad, base, CTEX,
                                         CREATE_TEXTURE_WORD,
                                         CREATE_TEXTURE + 4, pixels=True),
             ff7nx_cave.ANY_SPAN)
    placed[CREATE_TEXTURE] = A.b(CREATE_TEXTURE, ct)
    wp = put(lambda e, ad: build_counter(e, ad, base, CPAL,
                                         WRITE_PALETTE_WORD,
                                         WRITE_PALETTE + 4),
             ff7nx_cave.ANY_SPAN)
    placed[WRITE_PALETTE] = A.b(WRITE_PALETTE, wp)
    # v4: the GL driver, timed at the import stubs
    hooks, skipped = gl_hooks(segs, raw)
    if not any(sec == SWAP for _n, _v, _g, sec in hooks):
        raise ValueError('no %s import stub' % SWAP_NAME)
    common = put(lambda e, ad: build_stub_common(e, ad, base, profile, drv))
    checks = {}
    if profile:
        names = [n for n, _v, _g, _s in hooks]
        if names != profile_names() or len(names) > PROF_MAX:
            raise ValueError('GL hooks %d do not match the v5 profile list'
                             % len(names))
        for n in range(1, 5):
            checks[n] = put(lambda e, ad, k=n: build_arg_check(e, ad, base, k))
    for idx, (name, va, got, sec) in enumerate(hooks):
        if profile:
            nargs = 2 if name == SWAP_NAME else min(GL_ARGS[name], 4)
            cave = put(lambda e, ad, g=got, s=sec, i=idx, c=checks.get(nargs):
                       build_stub_cave(e, ad, g, s, common, i, c))
        elif gsync is not None and name == SWAP_NAME:
            cave = put(lambda e, ad, g=got, s=sec:
                       build_stub_cave(e, ad, g, s, common, gsync=gsync,
                                       row=gpu_row(2)))
        else:
            cave = put(lambda e, ad, g=got, s=sec:
                       build_stub_cave(e, ad, g, s, common))
        placed[va] = A.b(va, cave)
    drv_hooked, drv_skipped = [], []
    if drv:
        drv_hooked, drv_skipped = _hook_drv(src, text, stock, put, base,
                                            placed, gsync)
    placed.update(pool.placed)
    # the rest of the dead body becomes a trap, so a stray jump into it
    # stops loudly instead of running half an old rasteriser
    for va in range(pool.at, hi, 4):
        placed[va] = udf()
    for va, w in placed.items():
        struct.pack_into('<I', text, va, w)
    # BUILD 664: the probe is the LAST pass, so its state must be covered
    # by the loader's mapping: BSS is mapped from the RAW end of .data, the
    # scratch base from the page-aligned one (AC.bss_tail_slack, 605b). The
    # first hardware run of probe 664 data-aborted at the title screen
    # writing the last of its state past the mapped end.
    growth = (base - scratch + (PROF + PROF_BYTES if profile or drv
                                else BSS_BYTES)
              + AC.bss_tail_slack(segs))
    out = AC.pack(blob, [bytes(text), raw[1], raw[2]], growth)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'base': base, 'frame_end': frame_end, 'sites': len(sites),
            'words': (pool.at - lo) // 4, 'region': lo,
            'bss': growth, 'gl_hooks': len(hooks), 'gl_skipped': skipped,
            'profile': profile, 'drv': drv, 'gpu': gsync is not None,
            'drv_hooked': drv_hooked,
            'drv_skipped': drv_skipped}
