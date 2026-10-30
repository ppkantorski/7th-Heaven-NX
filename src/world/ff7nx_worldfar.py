#!/usr/bin/env python3
"""The world map's far field: the rest of the planet, drawn. BUILD 585.

WHY
---
The native renderer draws a 5x5 square of map meshes around you and nothing
else (FINDINGS-WORLDMAP-DRAW-DISTANCE). BUILD 584's sphere bent that square,
which is why its silhouette was a tent, its lower corners showed black, and
no radius could look like Gaia: past the square there was nothing to bend.

WHAT
----
Cosmos Gaia on PC draws the whole planet (FFNx renderer.cpp). This draws it
too, but through the port's own terrain pipeline rather than a new renderer:

  * ff7nx_worldfar_data builds wm0.far from the wm0.map this build ships --
    1008 per-mesh L1 tiles and 63 per-sector L2 tiles in the native mesh
    format, with skirts for the level seams.
  * native/worldfar.c (committed as native/worldfar.bin, position
    independent) runs right after world_sub_751EFC has submitted the window.
    It reads wm0.far into the world map's own allocation (made bigger here)
    with the game's own file routine, and for every tile in front of the
    camera and short of the horizon it sets the GTE translation and calls the
    native transform 0x75F0AD, cull 0x75F263 and submit 0x75F68C -- the same
    functions the window uses, so textures (Gaia's too), day/night, the
    spherical world and every later pass apply to it unchanged.
  * Terrain triangles take 0x28 bytes each from a per-frame primitive buffer
    of 0x20800 (3328 triangles) that the window alone nearly fills. It is
    raised to PRIM_BYTES per buffer (three constants), and the C checks the
    remaining room itself before every submission, so it can never overrun.

INSTALLED ONLY WITH THE SPHERE
------------------------------
Without the spherical world the old CPU sink is live and would drop far
terrain by the square of its distance; so this pass does nothing unless the
module it is patching already has the sphere (ff7nx_worldsphere's sink word)
and the card's lmain_vv carries it. SEVENTH_NX_WORLD_FAR=0 turns it off.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import nxpaths  # BUILD 755: project paths (src/nxpaths.py)
import os
import struct

import a64 as A

HERE = nxpaths.ROOT
BLOB = os.path.join(nxpaths.APP, 'native', 'worldfar.bin')
SOURCE = os.path.join(nxpaths.APP, 'native', 'worldfar.c')
ENV = 'SEVENTH_NX_WORLD_FAR'

# the call to world_sub_751EFC in x86 0x75079D (+0x1FB)
HOOK_SITE = 0xF3632C
HOOK_TARGET = 0xF4B9D0
G2H = 0x10FC3A0
CALLX = 0xA060
# the call to the world-map allocator 0x75AB50 (its only caller): a fresh
# allocation may sit at the address of the last one, so the far field is
# marked unloaded every time the world map allocates
ALLOC_CALL = 0xF1D6D0
ALLOC_TARGET = 0xF79940
# the call to the world camera update 0x74E8CE in x86 0x74DB8C (+0x40F):
# the right-stick zoom runs right after it (FFNx's 10000 -> 35000)
CAMERA_CALL = 0xF2A348
CAMERA_TARGET = 0xF353C0
INPUT_GOT = 0x12CE1D0            # ff7nx_analog.INPUT_GOT
STATE_ZOOM = 0x1C                # State.zoom
STATE_TILT = 0x20                # State.tilt
# BUILD 586: world terrain is submitted as pre-transformed (TL) vertices, so
# the sphere has to be applied on the CPU right after the port's terrain
# transform 0x10F28B0. BUILD 587: it has THREE callers -- the 0x75F0AD thunk
# (which only this far field uses) and two calls inlined in world_sub_751EFC
# itself (the 5x5 window). 586 hooked only the thunk: the window never bent.
TRANSFORM_CALLS = (0xF548A4, 0xF4EBC8, 0xF4D20C)
TRANSFORM_CALL = TRANSFORM_CALLS[0]
TRANSFORM_TARGET = 0x10F28B0
# BUILD 587: world_update_player 0x74EA48 from world_mode_loop (x86 0x74DF6C)
INPUT_CALL = 0xF2A2AC
INPUT_TARGET = 0xF306A0
# BUILD 587: ZL/ZR on the world map. The DirectInput emulation maps ZL -> Home
# and ZR -> End (id 7/8 of its switch at 0x10D3974); on the world map those
# turn the camera, and the triggers are the zoom now (FFNx). Both cases get
# a mode test: driver mode 4 (world map, *[0x12CE1F8]) maps them to nothing.
DI_ZL_SITE = 0x10D39D8            # cmp w8, #9   (w8 = driver mode)
DI_ZL_ORIG = 0x7100251F
DI_ZR_SITE = 0x10D39E8            # mov w8, #0x4f
DI_ZR_ORIG = 0x528009E8
DI_SKIP = 0x10D3A60               # next id, no key
DI_STORE = 0x10D3A58              # KEYBUF[w8] = 0x80
MODE_SLOT = 0x12CE1F8
DRIVER_WORLD = 4
# BUILD 587: the Highwind's ceiling. world_update_player compares the
# altitude with [0xDF5420] (4000 on the Highwind) twice: forced descent above
# it, and no climbing within 50*mult of it. FFNx lets the Highwind climb to
# UINT16_MAX/2 - 50*mult - 2000; the same number here, at both compares only
# ([0xDF5420] itself also feeds the take-off/landing height, untouched).
HIGHWIND_CEILING = 0x7FFF - 2000          # 30767
CEILING_WORDS = ((0xF32014, 0x79400008, 'movz w8'),   # ldrh w8, [x0]
                 (0xF32018, 0x79400409, 'movz w9'),   # ldrh w9, [x0, #2]
                 (0xF320FC, 0xB9400008, 'movz w8'))   # ldr  w8, [x0]

# must equal the C's PRIM_BYTES / FAR_* (test_worldfar checks the source).
# BUILD 587: the far field's data is in BSS now; the world allocation only
# gains a 32 KB read buffer, and the primitive buffers (which only the native
# window uses since 586) go from 0x80000 to 0x30000 (stock 0x20800).
PRIM_BYTES = 0x30000
FAR_OFF = 0xA6800 + 2 * PRIM_BYTES
FAR_IO = 0x200
FAR_IO_BYTES = 0x8000
FAR_REGION = FAR_IO + FAR_IO_BYTES
FAR_MAX = 0x400000                  # BUILD 591: + the six story variants
WORLD_ALLOC = FAR_OFF + FAR_REGION + 1          # stock 0xE7801

# (va, stock word, what) -- movz/movk pairs holding 0x20800 / 0xE7801
PRIM_LIMIT = ((0xF21CE0, 0x52810009), (0xF21CE4, 0x72A00049))   # 74C9AF
PRIM_SECOND = ((0xF2B2A8, 0x52810009), (0xF2B2AC, 0x72A00049))  # 74E1E9
ALLOC = ((0xF799B8, 0x528F0028), (0xF799BC, 0x72A001C8))        # 75AB50

N1 = 36 * 28 + 16 * 6            # BUILD 591: + 16 L1 tiles per variant
N2 = 9 * 7 + 6
N_TILES = N1 + N2
NAT_MAX = 64
NSLOT = 544                       # BUILD 605: >= 23 x 23 near cells (596: 320)
SLOT_BYTES = 12 + 256 * 12 + 2 * 128 * 8 + 4 + 128 * 4   # 605: + lit colours
LZ_BYTES = 8192
MAX_TRIS_FRAME = 48000            # BUILD 606 (605: 40000, 596: 26000)
# nstage, draws, hvalid, hpx, hpz; staged tris (605: 96-byte TL, a u16
# texture each) + their order; padding so the batch buffer is 16-aligned;
# the batch; the sky columns
STAGE_BYTES = 20 + MAX_TRIS_FRAME * (96 + 2 + 2) + 12 + 512 * 0x80 + 160 * 64
# BUILD 605: lgen, the light key, the wm0.far tiles' lit colours, TexP[512]
MAX_VERTS_TILE = 120
LIGHT_BYTES = (4 + 48 + 4 * N_TILES + 4 * MAX_VERTS_TILE * N_TILES
               + 512 * 20             # + each texture's UV mapping (TexP)
               + 16)                  # BUILD 606: own_streak, own_frames, 2 pad
# BUILD 605: worldfar's frame-timing stamps for leakprobe v4 -- the 48 bytes
# right before the Highwind block.
PERF_TAIL = 80                    # 606b: + the window caves' totals; 606c: + the terrain draw's
# BUILD 603c: hw_ok, hw_x, hw_y, hw_z, hw_pending, hw_off, hw_lost,
# hw_restores, hw_prev, 3 pad -- the LAST 48 bytes of State (leakprobe reads
# them at state + STATE_BYTES - HW_TAIL).
HW_TAIL = 48
# BUILD 606: State.hw_pad2[0] (at state + STATE_BYTES - 12) is set by the
# window caves: worldfar only owns the window in a module that has them.
OWN_CAVES_OFF = HW_TAIL - 12 + 4                  # from own_window (STATE_BYTES - HW_TAIL - 4)
STATE_BYTES = (0x68 + NAT_MAX * 12 + N_TILES * 32 + 63 + 1
               + N1 + 2 * N1 + NSLOT * SLOT_BYTES + LIGHT_BYTES + LZ_BYTES
               + STAGE_BYTES + FAR_MAX
               + PERF_TAIL + HW_TAIL)  # BUILD 601/603c: the Highwind altitude block
                           # sizeof(State)
BSS_BYTES = (STATE_BYTES + 0x10F) & ~0xFF
BLOB_MAX = 0x7800

# BUILD 588: world_sub_762F9A (the drop models, shadows, effects and the Zolom
# get below their true height) -- all three calls land on worldfar_sink, the
# same world-fixed d^2/2R drop as the terrain. The model call is the one
# ff7nx_worldsphere pointed at its zero stub; it is re-pointed here.
SINK_TARGET = 0xF65CD0
SINK_CALLS = (0xF65BBC, 0xF7F240, 0xF93C88)
SINK_TRACK_RET = 0xF65BC0         # 0x75692A's call + 4: it subtracts 4x
# BUILD 593: the ground-height routine 0x76085F reads the mesh vertices the
# bend has just lowered, so every entity/effect/track height carried the drop
# and the sink then lowered them again. After its two calls in 0x74CC07,
# worldfar_height adds the drop back to the height it wrote.
HEIGHT_TARGET = 0xF8AED0          # x86 0x76085F
HEIGHT_CALLS = (0xF234D0, 0xF23960)
WS_MODEL_CALL = 0xF93C88          # ff7nx_worldsphere.MODEL_CALL
CTX_GOT = 0x12CE2B0               # -> the recompiler's register block
# BUILD 588: the four calls of 0x754493 (one sky quad: clouds, meteor) in
# 0x7547A6: after each, the quad is sliced and lowered to the curved horizon
SKY_TARGET = 0xF60DC0
SKY_CALLS = (0xF39B68, 0xF3A030, 0xF3A324, 0xF3A618)
# BUILD 588: world_get_camera_rotation_x 0x74F916 pitches the Highwind's
# camera by (camera height >> 5) + 0x6D6; above ~11000 that passes straight
# down and the camera flips over. The height is clamped to 4000 (the stock
# ceiling) there, so the pitch stays what it was at the old ceiling.
PITCH_SITE = 0xF44670             # asr w8, w8, #5   (w8 = [0xDE6A04])
PITCH_ORIG = 0x13057D08
PITCH_CLAMP = 4000
# BUILD 588: the player update's call of 0x762E87 (move the player by dx, dz):
# worldfar_move rewrites (dx, dz) from the left stick (360-degree, camera
# relative) before it runs.
# BUILD 598: the minimap in 16:9. x86 0x767D68 places the map at
#     x = 316*s - 11*size        (s = UI scale [0xDE69D8],
#     y = 220*s -  8*size         size = 8*s minimap, 24*s the full map)
# in the 320-wide UI space -- flush with the 4:3 picture's right edge, which
# leaves 73 units of 16:9 margin to its right and 28 below it. Changing the
# two constants to 388 and 14 moves the MINIMAP 48 units right (right margin
# 25.5 units, bottom 28) and leaves the full map exactly where it was
# (388 - 14*24 = 316 - 11*24 = 52). Nothing else reads them.
MINIMAP_WORDS = ((0xF3C75C, 0x52802789, 388, 'mov w9, #316'),
                 (0xF3C774, 0x52800169, 14, 'mov w9, #11'))
# BUILD 715 (user, 10-16: the minimap "on the upper right of the screen ...
# the same distance it currently is from the bottom should be the distance
# it is from the top"). The same routine places it vertically at
#     y = 220*s - 8*size            (0xF3C7A4 mov w9, #220 ... 0xF3C7C8 sub)
# -- the minimap (size 8*s, 7*size = 56 units tall) 28 units above the
# bottom of the 240-unit UI, the full map (size 24*s) at 28. y = 28*s puts
# the minimap 28 units below the top and leaves the full map where it was:
# the constant becomes 28 and the subtraction a plain move (the register
# that held 8*size keeps it; only the result changes). Nothing else reads it.
# BUILD 729 (user, 10-17: "the top gap should be equal to the right side
# gap"). The right margin is 426.67 - 53.33 - (276 + 72) = 25.33 units (76 px
# at 720p, measured on his screenshot; the top was 84 px = 28 units). The
# position is a whole number of units, so 25: 75 px at 720p, 112.5 vs 114
# at 1080p. Constant 25, everything else as 715.
MINIMAP_TOP = 25
# BUILD 857 (user, 10-28: the full map "is a bit higher than it should be
# ... can you vertically center it please", the minimap stays). 715/729 put
# BOTH at y = 25*s; the full map (size 24*s, 7*size = 168 units) centred in
# the 240-unit UI sits at (240 - 168) / 2 = 36. One line through both:
#     y = 25*s + 11/16 * (size - 8*s)      (minimap 25*s, full map 36*s)
#     16*y = 312*s + 11*size
# The constant becomes 312 (w9 = 312*s, held in edx until the end), and the
# `sub` site calls MAPY: w23 = (w9 + 11*size) >> 4 from w8 = 8*size (8 + 2 +
# 1 eighths), exact for both sizes. 5 words after the blob; x30 is the
# routine's own scratch here (it calls the translator around it).
MAP_FULL_TOP = 36
MAP_Y16_S = 16 * MINIMAP_TOP - 8 * (MAP_FULL_TOP - MINIMAP_TOP)     # 312
assert MAP_FULL_TOP - MINIMAP_TOP == 11 and MAP_Y16_S == 312
MAPY_SITE = 0xF3C7C8
MINIMAP_Y_WORDS = ((0xF3C7A4, 0x52801B89, A.movz(9, MAP_Y16_S), 'mov w9, #220'),
                   (MAPY_SITE, 0x4B080137, None, 'sub w23, w9, w8'))


def mapy_words():
    """w23 = (w9 + 11*size) >> 4, w8 = 8*size; x10 the only scratch."""
    return [0x0B08012A,                           # add w10, w9, w8
            0x0B480940 | 10,                      # add w10, w10, w8, lsr #2
            0x0B480D40 | 10,                      # add w10, w10, w8, lsr #3
            0x13047D57,                           # asr w23, w10, #4
            A.ret()]
# BUILD 699/700: the Highwind flies level. Its model pitch (x86 0x763585,
# the ridden Highwind's draw) is [0xE39BB4] - 0x40, in 256ths of a turn:
# the 0x40 (a quarter turn) stands the model the right way up, and
# [0xE39BB4] is the climb/dive pitch + 4 (0x74F3E1) -- a fixed 4/256
# (5.6 degree) slant in level flight. `sub w20, w8, #0x44` leaves the
# quarter turn and the climb/dive pitch (0 in level flight). Nothing else
# reads that sum.
HW_LEVEL_SITE = 0xF93668
HW_LEVEL_ORIG = A.sub_imm(20, 8, 0x40)
HW_LEVEL_NEW = A.sub_imm(20, 8, 0x44)
MOVE_CALL = 0xF33198
MOVE_TARGET = 0xF45A60


# BUILD 606: the game's 5x5 window, drawn by the far field instead. Inside
# world_sub_751EFC each window mesh is projected by 0x75F263 (123 x87-emulated
# transforms) and submitted by 0x75F68C (per triangle: three lighting calls
# and a shape allocation) -- 9.7 ms a frame on average and 15 ms looking down
# (leakprobe v4, logs/crash_lp4_01790610124.log). Both calls, in both of the
# window's mesh loops, go through a cave that skips them while worldfar owns
# the window (State.own_window, set by worldfar_draw when every cell within
# ring 3 of the player is in its mesh cache). The meshes are still
# transformed (0x10F28B0 and the bend): ground heights and the planet are
# untouched. (site, stock target, the next word -- it reads the guest
# context in x24, which the skip uses to pop the call's return slot)
WINDOW_CALLS = ((0xF4CA20, 0xF56F10, 0xB9401708),    # 0x7526C2 -> 0x75F263
                (0xF4CB64, 0xF58410, 0xB9401308),    # 0x7526F9 -> 0x75F68C
                (0xF4EC58, 0xF56F10, 0xB9401708),    # 0x752BF6 -> 0x75F263
                (0xF4ED9C, 0xF58410, 0x29422708))    # 0x752C2D -> 0x75F68C
WINDOW_CTX = 24
OWN_ENV = 'SEVENTH_NX_WORLD_OWNWINDOW'
MRS_CNTPCT_X16 = 0xD53BE030                       # mrs x16, cntpct_el0
MRS_CNTPCT_X17 = 0xD53BE031                       # mrs x17, cntpct_el0
PROJ_TOTAL_OFF = -52                              # State.pf_t_proj - own_window
SUB_TOTAL_OFF = -44                               # State.pf_t_sub - own_window
TERR_TOTAL_OFF = -36                              # State.pf_t_terr - own_window
# BUILD 606c: the world render's two terrain draw loops (x86 0x74C1D2 over the
# 0x16 special texture objects, 0x74C218 over every texture object) -- each
# call of 0x66E641 there is timed into State.pf_t_terr, for leakprobe v6.
# (site, stock target 0x66E641, the next word)
TERR_CALLS = ((0xF1FA68, 0xAD81A0, 0x294226C8),
              (0xF1FBE8, 0xAD81A0, 0xB94012C8))


def own_window_enabled(env=None):
    e = os.environ if env is None else env
    return e.get(OWN_ENV, '1').strip().lower() not in ('0', 'off', 'false', 'no')


def timed_words(at, target, state, total_off):
    """In place of `bl target`: call it and add the ticks it took to the
    running total at own_window + total_off (606c). Transparent: the guest
    stack is the callee's as before, x30 is restored."""
    own = state + STATE_BYTES - HW_TAIL - 4
    w = []

    def pc():
        return at + 4 * len(w)
    w.append(A.stp64_pre(29, 30, 31, -16))
    w.append(MRS_CNTPCT_X17)
    w.append(A.stp64_pre(17, 16, 31, -16))
    w.append(A.bl(pc(), target))
    w.append(A.ldp64_post(17, 16, 31, 16))
    w.append(MRS_CNTPCT_X16)
    w.append(A.sub_reg64(17, 16, 17))
    w.append(A.adrp(16, pc(), own & ~0xFFF))
    w.append(A.add_imm64(16, 16, own & 0xFFF))
    w.append(A.sub_imm64(16, 16, -total_off))
    w.append(A.ldr64(15, 16, 0))
    w.append(A.add_reg64(15, 15, 17))
    w.append(A.str64(15, 16, 0))
    w.append(A.ldp64_post(29, 30, 31, 16))
    w.append(A.ret())
    return w


def check_terr_sites(text):
    for site, target, nxt in TERR_CALLS:
        got = struct.unpack_from('<II', text, site)
        if got != (A.bl(site, target), nxt):
            raise ValueError('worldfar: the terrain draw call at +0x%X is '
                             '%08X %08X, expected bl +0x%X then %08X'
                             % (site, got[0], got[1], target, nxt))


def window_words(at, target, state, total_off):
    """In place of `bl target`: mark the caves present, then skip the call
    (pop its return slot from the guest stack) while State.own_window is set.
    Otherwise call it and add the time it took (hardware counter ticks) to
    the running total at own_window + total_off (606b: leakprobe splits the
    window with these)."""
    own = state + STATE_BYTES - HW_TAIL - 4          # State.own_window
    w = []

    def pc():
        return at + 4 * len(w)
    w.append(A.adrp(16, pc(), own & ~0xFFF))
    w.append(A.add_imm64(16, 16, own & 0xFFF))
    w.append(A.movz(17, 1))
    w.append(A.str_(17, 16, OWN_CAVES_OFF))          # State.hw_pad2[0]: caves present
    w.append(A.ldr(17, 16, 0))
    n_call = 15                                      # the words of the call path
    skip = pc() + 4 + 4 * n_call
    w.append(A.cbnz(17, pc(), skip))
    w.append(A.stp64_pre(29, 30, 31, -16))
    w.append(MRS_CNTPCT_X17)
    w.append(A.stp64_pre(17, 16, 31, -16))           # the start time
    w.append(A.bl(pc(), target))
    w.append(A.ldp64_post(17, 16, 31, 16))
    w.append(MRS_CNTPCT_X16)
    w.append(A.sub_reg64(17, 16, 17))                # ticks in the call
    w.append(A.adrp(16, pc(), own & ~0xFFF))
    w.append(A.add_imm64(16, 16, own & 0xFFF))
    w.append(A.sub_imm64(16, 16, -total_off))        # &total (below own_window)
    w.append(A.ldr64(15, 16, 0))
    w.append(A.add_reg64(15, 15, 17))
    w.append(A.str64(15, 16, 0))
    w.append(A.ldp64_post(29, 30, 31, 16))
    w.append(A.ret())
    assert pc() == skip
    w.append(A.ldr(16, WINDOW_CTX, 0x10))            # guest esp
    w.append(A.add_imm(16, 16, 4))                   # the callee's pop of its return slot
    w.append(A.str_(16, WINDOW_CTX, 0x10))
    w.append(A.ret())
    return w


def enabled(env=None):
    import ff7nx_worldsphere as WS
    e = os.environ if env is None else env
    return (WS.gaia_enabled(e)
            and e.get(ENV, '').strip().lower() not in ('0', 'off', 'false', 'no'))


def _movzk(reg, value):
    return [A.movz(reg, value & 0xFFFF), A.movk_hi(reg, (value >> 16) & 0xFFFF)]


def _hook_words(at, target, entry, state, got=None, s0=None, pre=False,
                ctx=False):
    """Run the original call, then entry(state, g2h, callx[, got]); keep x0.
    `s0`, if given, is a float passed as the first FP argument. With `pre`,
    entry(..., got, 0) also runs BEFORE the call (x0..x8 preserved across it)
    and entry(..., got, 1) after."""
    w = []

    def pc():
        return at + 4 * len(w)

    def args(phase):
        w.append(A.adrp(0, pc(), state & ~0xFFF))
        w.append(A.add_imm64(0, 0, state & 0xFFF))
        w.append(A.adrp(1, pc(), G2H & ~0xFFF))
        w.append(A.add_imm64(1, 1, G2H & 0xFFF))
        w.append(A.adrp(2, pc(), CALLX & ~0xFFF))
        w.append(A.add_imm64(2, 2, CALLX & 0xFFF))
        if got is not None:
            w.append(A.adrp(3, pc(), got & ~0xFFF))
            w.append(A.add_imm64(3, 3, got & 0xFFF))
        if ctx:
            w.append(A.adrp(3, pc(), CTX_GOT & ~0xFFF))
            w.append(A.ldr64(3, 3, CTX_GOT & 0xFFF))
        if phase is not None:
            w.append(A.movz(4, phase))
        if s0 is not None:
            bits = struct.unpack('<I', struct.pack('<f', s0))[0]
            w.append(A.movz(9, bits & 0xFFFF))
            w.append(A.movk_hi(9, bits >> 16))
            w.append(0x1E270120)                     # fmov s0, w9
        w.append(A.bl(pc(), entry))

    w.append(A.stp64_pre(29, 30, 31, -96))
    w.append(A.str64(19, 31, 16))
    if pre:
        w.append(A.stp64_off(0, 1, 31, 32))
        w.append(A.stp64_off(2, 3, 31, 48))
        w.append(A.stp64_off(4, 5, 31, 64))
        w.append(A.stp64_off(6, 7, 31, 80))
        w.append(A.str64(8, 31, 24))
        args(0)
        w.append(A.ldp64_off(0, 1, 31, 32))
        w.append(A.ldp64_off(2, 3, 31, 48))
        w.append(A.ldp64_off(4, 5, 31, 64))
        w.append(A.ldp64_off(6, 7, 31, 80))
        w.append(A.ldr64(8, 31, 24))
    w.append(A.bl(pc(), target))
    w.append(A.mov_reg64(19, 0))
    args(1 if pre else None)
    w.append(A.mov_reg64(0, 19))
    w.append(A.ldr64(19, 31, 16))
    w.append(A.ldp64_post(29, 30, 31, 96))
    w.append(A.ret())
    return w


def stub_words(at, entry, state, uvscale=1.0):
    """The terrain hook: world_sub_751EFC, then the far field."""
    return _hook_words(at, HOOK_TARGET, entry, state, s0=float(uvscale))


def gaia_uv_scale(text):
    """Cosmos Gaia's terrain UV factor if ff7nx_gaia hooked 0x75F68C's six
    UV multiplies in THIS module, else 1.0 (so the far tiles always match
    whatever the near terrain does)."""
    import ff7nx_gaia as G
    hooked = [struct.unpack_from('<I', text, va)[0] != word
              for va, word, _reg in G.SITES]
    if all(hooked):
        return G.scale()
    if any(hooked):
        raise ValueError('Gaia terrain UV hooks are only partly installed')
    return 1.0


def bend_words(at, entry, state):
    """The transform hook: the port's terrain transform, then the bend."""
    return _hook_words(at, TRANSFORM_TARGET, entry, state)


def camera_words(at, entry, state):
    """The camera hook: the game's camera update, then the zoom."""
    return _hook_words(at, CAMERA_TARGET, entry, state, got=INPUT_GOT)


def input_words(at, entry, state):
    """The player-update hook: entry(.., got, 0); update; entry(.., got, 1)."""
    return _hook_words(at, INPUT_TARGET, entry, state, got=INPUT_GOT, pre=True)


def di_zl_words(at):
    """ZL's case, w8 = the driver mode: stock skips mode 9; also skip 4.
    (The switch is megabytes away: the far branches are plain `b`.)"""
    skip = at + 4 * 6
    return [A.cmp_imm(8, 9),
            A.bcond(at + 4, skip, 0),                    # b.eq skip
            A.cmp_imm(8, DRIVER_WORLD),
            A.bcond(at + 12, skip, 0),
            A.movz(8, 0x47),
            A.b(at + 20, DI_STORE),
            A.b(skip, DI_SKIP)]


def di_zr_words(at):
    """ZR's case: load the driver mode, skip on the world map."""
    skip = at + 4 * 7
    return [A.adrp(8, at, MODE_SLOT & ~0xFFF),
            A.ldr64(8, 8, MODE_SLOT & 0xFFF),
            A.ldr(8, 8, 0),
            A.cmp_imm(8, DRIVER_WORLD),
            A.bcond(at + 16, skip, 0),
            A.movz(8, 0x4F),
            A.b(at + 24, DI_STORE),
            A.b(skip, DI_SKIP)]


def alloc_words(at, entry, state):
    """The allocator hook: allocate, then worldfar_alloc (loads wm0.far once,
    resets zoom/tilt)."""
    return _hook_words(at, ALLOC_TARGET, entry, state)


def sky_words(at, entry, state):
    """BUILD 591: IN PLACE of 0x754493 (one stock sky quad):
    worldfar_sky(state, g2h, callx, ctx) draws nothing and pops the return
    address -- worldfar_draw draws the clouds and the meteor itself."""
    return sink_words(at, entry, state)


def move_words(at, entry, state):
    """worldfar_move(state, g2h, callx, ctx, 0) before the move, (.., 1) after."""
    return _hook_words(at, MOVE_TARGET, entry, state, pre=True, ctx=True)


def sink_words(at, entry, state):
    """In place of world_sub_762F9A: worldfar_sink(state, g2h, callx, ctx),
    which reads the guest-stack args, sets eax and pops the return address.
    x4 carries the ARM call site's return address (see SINK_TRACK_RET)."""
    w = []

    def pc():
        return at + 4 * len(w)
    w.append(A.stp64_pre(29, 30, 31, -16))
    w.append(A.adrp(0, pc(), state & ~0xFFF))
    w.append(A.add_imm64(0, 0, state & 0xFFF))
    w.append(A.adrp(1, pc(), G2H & ~0xFFF))
    w.append(A.add_imm64(1, 1, G2H & 0xFFF))
    w.append(A.adrp(2, pc(), CALLX & ~0xFFF))
    w.append(A.add_imm64(2, 2, CALLX & 0xFFF))
    w.append(A.adrp(3, pc(), CTX_GOT & ~0xFFF))
    w.append(A.ldr64(3, 3, CTX_GOT & 0xFFF))
    # BUILD 592: x4 = the caller's return address as a module VA (the guest
    # stack slot holds no return address), so the track caller can be told
    # apart: x4 = x30 - (runtime pc here) + (link-time pc here)
    here = pc()
    w.append(A.adr(5, here, here))
    w.append(A.sub_reg64(4, 30, 5))
    w.append(A.movz(6, here & 0xFFFF))
    w.append(A.movk_hi(6, here >> 16))
    w.append(A.add_reg64(4, 4, 6))
    w.append(A.bl(pc(), entry))
    w.append(A.ldp64_post(29, 30, 31, 16))
    w.append(A.ret())
    return w


def height_words(at, entry, state):
    """0x76085F, then worldfar_height(state, g2h, callx, ctx)."""
    return _hook_words(at, HEIGHT_TARGET, entry, state, ctx=True)


def pitch_words(at):
    """w8 = min(w8, PITCH_CLAMP) >> 5, then back (w9 is scratch there)."""
    return [A.movz(9, PITCH_CLAMP),
            A.cmp_reg(8, 9),
            A.csel(8, 8, 9, 11),            # lt
            A.asr(8, 8, 5),
            A.b(at + 16, PITCH_SITE + 4)]


def entries():
    """{'draw': offset, 'camera': offset} inside worldfar.bin."""
    out = {}
    for line in open(os.path.join(nxpaths.APP, 'native', 'worldfar.entry')):
        name, off = line.split()
        out[name] = int(off, 16)
    return out


def blob():
    data = open(BLOB, 'rb').read()
    e = entries()
    if (e.get('draw') != 0 or not 0 < e.get('camera', 0) < len(data)
            or not 0 < e.get('bend', 0) < len(data)
            or not 0 < e.get('input', 0) < len(data)
            or not 0 < e.get('alloc', 0) < len(data)
            or not 0 < e.get('sink', 0) < len(data)
            or not 0 < e.get('sky', 0) < len(data)
            or not 0 < e.get('move', 0) < len(data)
            or not 0 < e.get('height', 0) < len(data)
            or len(data) % 4 or len(data) > BLOB_MAX):
        raise ValueError('worldfar.bin: entries %r, %d bytes' % (e, len(data)))
    return data


def check_window_sites(text):
    for site, target, nxt in WINDOW_CALLS:
        got = struct.unpack_from('<II', text, site)
        if got != (A.bl(site, target), nxt):
            raise ValueError('worldfar: the window call at +0x%X is %08X %08X, '
                             'expected bl +0x%X then %08X'
                             % (site, got[0], got[1], target, nxt))


def check_sites(text):
    got = struct.unpack_from('<I', text, HOOK_SITE)[0]
    if got != A.bl(HOOK_SITE, HOOK_TARGET):
        raise ValueError('worldfar: +0x%X is %08X, not the call to '
                         'world_sub_751EFC' % (HOOK_SITE, got))
    for site in TRANSFORM_CALLS:
        got = struct.unpack_from('<I', text, site)[0]
        if got != A.bl(site, TRANSFORM_TARGET):
            raise ValueError('worldfar: +0x%X is %08X, not a call to the '
                             'terrain transform' % (site, got))
    got = struct.unpack_from('<I', text, INPUT_CALL)[0]
    if got != A.bl(INPUT_CALL, INPUT_TARGET):
        raise ValueError('worldfar: +0x%X is %08X, not the call to '
                         'world_update_player' % (INPUT_CALL, got))
    for va, want, what in ((DI_ZL_SITE, DI_ZL_ORIG, 'ZL key case'),
                           (DI_ZR_SITE, DI_ZR_ORIG, 'ZR key case')):
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            raise ValueError('worldfar: %s at +0x%X is %08X, expected %08X'
                             % (what, va, got, want))
    for site in SINK_CALLS:
        got = struct.unpack_from('<I', text, site)[0]
        ok = got == A.bl(site, SINK_TARGET)
        if site == WS_MODEL_CALL:
            ok = ok or (got >> 26) == 0x25      # ff7nx_worldsphere's zero stub
        if not ok:
            raise ValueError('worldfar: +0x%X is %08X, not a call of '
                             'world_sub_762F9A' % (site, got))
    for site in HEIGHT_CALLS:
        got = struct.unpack_from('<I', text, site)[0]
        if got != A.bl(site, HEIGHT_TARGET):
            raise ValueError('worldfar: +0x%X is %08X, not a call of the '
                             'ground-height routine' % (site, got))
    for site in SKY_CALLS:
        got = struct.unpack_from('<I', text, site)[0]
        if got != A.bl(site, SKY_TARGET):
            raise ValueError('worldfar: +0x%X is %08X, not a call of the sky '
                             'quad routine' % (site, got))
    got = struct.unpack_from('<I', text, MOVE_CALL)[0]
    if got != A.bl(MOVE_CALL, MOVE_TARGET):
        raise ValueError('worldfar: +0x%X is %08X, not the player update\'s '
                         'move call' % (MOVE_CALL, got))
    got = struct.unpack_from('<I', text, PITCH_SITE)[0]
    if got != PITCH_ORIG:
        raise ValueError('worldfar: camera pitch word at +0x%X is %08X, '
                         'expected %08X' % (PITCH_SITE, got, PITCH_ORIG))
    for va, want, what in CEILING_WORDS:
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            raise ValueError('worldfar: Highwind ceiling word at +0x%X is '
                             '%08X, expected %08X' % (va, got, want))
    got = struct.unpack_from('<I', text, HW_LEVEL_SITE)[0]
    if got != HW_LEVEL_ORIG:
        raise ValueError('worldfar: +0x%X is %08X, not the Highwind pitch '
                         'bias (sub w20, w8, #0x40)' % (HW_LEVEL_SITE, got))
    for va, want, _new, what in MINIMAP_WORDS + MINIMAP_Y_WORDS:
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            raise ValueError('worldfar: minimap word (%s) at +0x%X is %08X, '
                             'expected %08X' % (what, va, got, want))
    got = struct.unpack_from('<I', text, CAMERA_CALL)[0]
    if got != A.bl(CAMERA_CALL, CAMERA_TARGET):
        raise ValueError('worldfar: +0x%X is %08X, not the call to the '
                         'world camera update' % (CAMERA_CALL, got))
    got = struct.unpack_from('<I', text, ALLOC_CALL)[0]
    if got != A.bl(ALLOC_CALL, ALLOC_TARGET):
        raise ValueError('worldfar: +0x%X is %08X, not the call to the '
                         'world allocator' % (ALLOC_CALL, got))
    for pairs, what in ((PRIM_LIMIT, 'primitive limit'),
                        (PRIM_SECOND, 'second primitive buffer'),
                        (ALLOC, 'world allocation size')):
        for va, want in pairs:
            got = struct.unpack_from('<I', text, va)[0]
            if got != want:
                raise ValueError('worldfar: %s word at +0x%X is %08X, '
                                 'expected %08X' % (what, va, got, want))


def sphere_installed(text):
    import ff7nx_worldsphere as WS
    return struct.unpack_from('<I', text, WS.SINK_SITE)[0] == WS.SINK_NEW


def apply_to_nso(src, dest, stock=None, own=None):
    import ff7nx_audio_cave as AC
    import ff7nx_deadspace as DS
    if own is None:
        own = own_window_enabled()
    with open(src, 'rb') as fh:
        nso = fh.read()
    segs, raw = AC.segments(nso)
    text = bytearray(raw[0])
    if not sphere_installed(text):
        raise ValueError('the spherical world is not in this module')
    check_sites(text)
    if own:
        check_window_sites(text)
    check_terr_sites(text)
    code = blob()
    lo, hi = DS.part(src, 'far', stock)
    base = AC.scratch_base(nso, segs)
    state = (base + 15) & ~15
    growth = (state - base) + BSS_BYTES + AC.bss_tail_slack(segs)   # 605b
    ent = entries()
    uv = gaia_uv_scale(text)
    # layout: the hook stubs, the two key-case caves, then the blob
    at = (lo + 15) & ~15
    places = {}
    for name, n in (('stub', len(stub_words(at, at, state))),
                    ('camera', len(camera_words(at, at, state))),
                    ('bend', len(bend_words(at, at, state))),
                    ('input', len(input_words(at, at, state))),
                    ('alloc', len(alloc_words(at, at, state))),
                    ('sink', len(sink_words(at, at, state))),
                    ('height', len(height_words(at, at, state))),
                    ('sky', len(sky_words(at, at, state))),
                    ('pitch', len(pitch_words(at))),
                    ('move', len(move_words(at, at, state))),
                    ('di_zl', len(di_zl_words(at))),
                    ('di_zr', len(di_zr_words(at))),
                    ('win263', len(window_words(at, at, state, PROJ_TOTAL_OFF))),
                    ('win68c', len(window_words(at, at, state, SUB_TOTAL_OFF))),
                    ('terr', len(timed_words(at, at, state, TERR_TOTAL_OFF)))):
        places[name] = at
        at = (at + 4 * n + 15) & ~15
    entry = at
    # BUILD 857: the full map's y, after the blob (nothing above moves)
    places['mapy'] = (entry + len(code) + 15) & ~15
    if places['mapy'] + 4 * len(mapy_words()) > hi:
        raise ValueError('worldfar does not fit its dead-space part '
                         '(%d bytes of %d)' % (places['mapy'] + 20 - lo,
                                               hi - lo))
    for va in range(lo, entry, 4):
        struct.pack_into('<I', text, va, 0)
    P = places
    for va0, words in (
            (P['stub'], stub_words(P['stub'], entry + ent['draw'], state, uv)),
            (P['camera'], camera_words(P['camera'], entry + ent['camera'], state)),
            (P['bend'], bend_words(P['bend'], entry + ent['bend'], state)),
            (P['input'], input_words(P['input'], entry + ent['input'], state)),
            (P['alloc'], alloc_words(P['alloc'], entry + ent['alloc'], state)),
            (P['sink'], sink_words(P['sink'], entry + ent['sink'], state)),
            (P['height'], height_words(P['height'], entry + ent['height'], state)),
            (P['sky'], sky_words(P['sky'], entry + ent['sky'], state)),
            (P['pitch'], pitch_words(P['pitch'])),
            (P['move'], move_words(P['move'], entry + ent['move'], state)),
            (P['di_zl'], di_zl_words(P['di_zl'])),
            (P['di_zr'], di_zr_words(P['di_zr'])),
            (P['win263'], window_words(P['win263'], WINDOW_CALLS[0][1], state, PROJ_TOTAL_OFF)),
            (P['win68c'], window_words(P['win68c'], WINDOW_CALLS[1][1], state, SUB_TOTAL_OFF)),
            (P['terr'], timed_words(P['terr'], TERR_CALLS[0][1], state, TERR_TOTAL_OFF))):
        for i, word in enumerate(words):
            struct.pack_into('<I', text, va0 + 4 * i, word)
    text[entry:entry + len(code)] = code
    struct.pack_into('<I', text, HOOK_SITE, A.bl(HOOK_SITE, P['stub']))
    struct.pack_into('<I', text, CAMERA_CALL, A.bl(CAMERA_CALL, P['camera']))
    for site in TRANSFORM_CALLS:
        struct.pack_into('<I', text, site, A.bl(site, P['bend']))
    struct.pack_into('<I', text, INPUT_CALL, A.bl(INPUT_CALL, P['input']))
    struct.pack_into('<I', text, ALLOC_CALL, A.bl(ALLOC_CALL, P['alloc']))
    for site in SINK_CALLS:
        struct.pack_into('<I', text, site, A.bl(site, P['sink']))
    for site in HEIGHT_CALLS:
        struct.pack_into('<I', text, site, A.bl(site, P['height']))
    for site in SKY_CALLS:
        struct.pack_into('<I', text, site, A.bl(site, P['sky']))
    struct.pack_into('<I', text, PITCH_SITE, A.b(PITCH_SITE, P['pitch']))
    struct.pack_into('<I', text, MOVE_CALL, A.bl(MOVE_CALL, P['move']))
    struct.pack_into('<I', text, DI_ZL_SITE, A.b(DI_ZL_SITE, P['di_zl']))
    struct.pack_into('<I', text, DI_ZR_SITE, A.b(DI_ZR_SITE, P['di_zr']))
    for site, _t, _n in TERR_CALLS:                  # BUILD 606c: timed
        struct.pack_into('<I', text, site, A.bl(site, P['terr']))
    if own:                                          # BUILD 606
        for site, target, _n in WINDOW_CALLS:
            cave = P['win263'] if target == WINDOW_CALLS[0][1] else P['win68c']
            struct.pack_into('<I', text, site, A.bl(site, cave))
    for va, _w, what in CEILING_WORDS:
        reg = 9 if what == 'movz w9' else 8
        val = 0 if reg == 9 else HIGHWIND_CEILING
        struct.pack_into('<I', text, va, A.movz(reg, val))
    for va, _w, new, _what in MINIMAP_WORDS:
        struct.pack_into('<I', text, va, A.movz(9, new))
    for va, _w, word, _what in MINIMAP_Y_WORDS:                  # BUILD 715
        if word is None:                                         # BUILD 857
            word = A.bl(va, P['mapy'])
        struct.pack_into('<I', text, va, word)
    for i, word in enumerate(mapy_words()):
        struct.pack_into('<I', text, P['mapy'] + 4 * i, word)
    struct.pack_into('<I', text, HW_LEVEL_SITE, HW_LEVEL_NEW)   # BUILD 699
    for pairs, value, reg in ((PRIM_LIMIT, PRIM_BYTES, 9),
                              (PRIM_SECOND, PRIM_BYTES, 9),
                              (ALLOC, WORLD_ALLOC, 8)):
        for (va, _w), word in zip(pairs, _movzk(reg, value)):
            struct.pack_into('<I', text, va, word)
    raw[0] = bytes(text)
    out = AC.pack(nso, raw, growth)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as fh:
        fh.write(out)
    out = dict(P)
    out.update({'own': bool(own), 'entry': entry, 'code': len(code), 'uv': uv,
                'state': state, 'bss': growth})
    return out


def write_data(map_path, dest):
    import ff7nx_worldfar_data as F
    data, stats = F.build(map_path)
    if len(data) > FAR_MAX:
        raise ValueError('wm0.far is %d bytes, the BSS state holds %d'
                         % (len(data), FAR_MAX))
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = dest + '.tmp'
    with open(tmp, 'wb') as fh:
        fh.write(data)
    os.replace(tmp, dest)
    stats['bytes'] = len(data)
    return stats
