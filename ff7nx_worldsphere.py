#!/usr/bin/env python3
"""FFNx's spherical world on the Switch world map. BUILD 584.

WHAT FFNx DOES (src/ff7/world/world.cpp, misc/FFNx.common.sh)
-------------------------------------------------------------
With Cosmos Gaia's external mesh, FFNx switches the game's own curvature
off on the CPU -- three `memset_code` NOPs in world_sub_75F0AD, and
`world_sub_762F9A` (the models' vertical sink) replaced by `return 0` -- and
bends every non-TL world-map vertex in the VERTEX SHADER instead:

    ApplySphericalWorld(viewPos, rate):  rp = -250000 * rate
        plane  = (view.y, |view.xz|)
        circle = rp * exp(plane / rp) - rp          (complex)
        view'  = (im * dir.x, re, im * dir.z)

That is a true arc centred on the camera: the horizon curves the same way
whichever way you turn, and it deepens or flattens with the camera's height.

WHAT THE PORT HAD
-----------------
A hand-written replacement for world_transform_block_vertices at module
+0x10F28B0 that lowers each terrain vertex by

    sink = ((z/4 - onset) * 1/64  +  |screen_x - 320| * 1/32) ** 2

-- a screen-space hack. The screen-x term makes terrain rise and sink as you
turn (FINDINGS-WORLDMAP-DRAW-DISTANCE), and the shape does not follow height.

WHAT THIS MODULE INSTALLS (all or nothing, every word fingerprinted)
--------------------------------------------------------------------
1. `fmsub s0, s0, s0, s1` at +0x10F2AAC -> `fmov s0, s1`: the terrain sink
   is gone (FFNx's three NOPs).
2. The call to world_sub_762F9A inside x86 0x76328F (+0xF93C88) now lands on
   a four-instruction stub returning 0 (FFNx's replace_function). The snake
   and particle callers keep the stock routine: they are TL sprites the GPU
   cannot bend (FFNx rewrites those two on the CPU; not done here yet).
3. ff7nx_daynight's BlockVertex hook writes WORLD_MAGIC into blendMode.w for
   every draw made while the driver mode is the world map.
4. custom_shaders/wide_screen/lmain_vv.glsl applies ApplySphericalWorld when
   it sees that magic, recovering the view-space position from the one
   combined matrix the port uploads (tests/test_worldsphere.py proves the
   recovery exact against world*view*projection*viewport).

SEVENTH_NX_WORLD_SPHERE=0 installs none of it (stock sink back).
"""
from __future__ import annotations

import os
import struct

import a64 as A

ENV = 'SEVENTH_NX_WORLD_SPHERE'

SINK_SITE = 0x10F2AAC
SINK_ORIG = 0x1F008400            # fmsub s0, s0, s0, s1
SINK_NEW = 0x1E204020             # fmov  s0, s1   (y unchanged)

MODEL_CALL = 0xF93C88             # inside x86 0x76328F
MODEL_CALLEE = 0xF65CD0           # translated world_sub_762F9A
MODEL_CALL_ORIG = 0x97FF4812      # bl #0xf65cd0
MODEL_CTX = 21                    # x21 holds the recompiler's register block

WORLD_MAGIC = 0x575253            # 'WRS'
WORLD_LANE = 12                   # blendMode.w, +0x0C in BlockVertex
SHADER_MARK = 'WS_SPHERE_MAGIC 0x575253'


RADIUS_ENV = 'SEVENTH_NX_WORLD_SPHERE_RADIUS'   # BUILD 584 only; now ignored
FFNX_RADIUS = 250000.0            # FFNx.common.sh: rp = -250000 * rate


def radius(env=None):
    """FFNx's 250000, always.

    BUILD 584 let SEVENTH_NX_WORLD_SPHERE_RADIUS pull the horizon in to meet
    the 5x5 window. BUILD 585 draws the rest of the planet (ff7nx_worldfar),
    so the true FFNx radius is what matches Gaia on PC and there is nothing
    left to tune. The variable is ignored.
    """
    return FFNX_RADIUS


def write_radius(path, value):
    """Rewrite `#define WS_SPHERE_RADIUS` in place; False if it is absent."""
    import re
    text = open(path).read()
    pat = re.compile(r'^([ \t]*#define[ \t]+WS_SPHERE_RADIUS[ \t]+)([0-9.]+)',
                     re.MULTILINE)
    if not pat.search(text):
        return False
    new = pat.sub(lambda m: m.group(1) + '%.1f' % value, text, count=1)
    tmp = path + '.tmp'
    open(tmp, 'w').write(new)
    os.replace(tmp, path)
    return True


GAIA_ENV = 'SEVENTH_NX_WORLD_GAIA'   # BUILD 587: every world-map change


def _off(e, name):
    return e.get(name, '').strip().lower() in ('0', 'off', 'false', 'no')


def gaia_enabled(env=None):
    """SEVENTH_NX_WORLD_GAIA=0 builds the stock world map: no sphere, no far
    field, no camera/control changes, no Highwind ceiling, the sky dome's
    FFNx-widescreen edge -- the baseline to compare against."""
    e = os.environ if env is None else env
    return not _off(e, GAIA_ENV)


def enabled(env=None):
    e = os.environ if env is None else env
    return gaia_enabled(e) and not _off(e, ENV)


def model_stub_words():
    """`eax = 0; ret` in the recompiler's convention at MODEL_CALL.

    The caller has already done the x86 `call`'s esp -= 4 and reads EAX from
    [x21] afterwards; the callee's `ret` is its esp += 4.
    """
    c = MODEL_CTX
    return [
        A.str_(31, c, 0),               # str  wzr, [x21]        eax = 0
        A.ldr(8, c, 0x10),              # ldr  w8, [x21, #0x10]  esp
        A.add_imm(8, 8, 4),             # add  w8, w8, #4        ret pops
        A.str_(8, c, 0x10),             # str  w8, [x21, #0x10]
        A.ret(),
    ]


def emit_block_flag(a, bss_ptr, bss_reg, bss, mode_off, world_mode, base):
    """Cave fragment: blendMode.w = WORLD_MAGIC in world-map mode, else 0.

    Uses w2/w3 like the rest of the block cave (both are dead at the hook).
    `bss_ptr(a, reg, bss)` materialises the state block address.
    """
    bss_ptr(a, bss_reg, bss)
    a.emit(A.ldr(bss_reg, bss_reg, mode_off))
    a.emit(A.movz(2, 0))
    a.emit(A.cmp_imm(bss_reg, world_mode))
    a.bcond('ws_not_world', A.NE)
    a.emit(A.movz(2, WORLD_MAGIC & 0xFFFF))
    a.emit(A.movk_hi(2, (WORLD_MAGIC >> 16) & 0xFFFF))
    a.label('ws_not_world')
    a.emit(A.str_(2, 31, base + WORLD_LANE))


def check_sites(text):
    for va, want, what in ((SINK_SITE, SINK_ORIG, 'terrain sink fmsub'),
                           (MODEL_CALL, MODEL_CALL_ORIG,
                            'the models\' call to world_sub_762F9A')):
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            raise ValueError('worldsphere: %s at +0x%X is %08X, expected %08X'
                             % (what, va, got, want))
    if A.bl(MODEL_CALL, MODEL_CALLEE) != MODEL_CALL_ORIG:
        raise ValueError('worldsphere: the model call no longer targets '
                         'world_sub_762F9A')


def shader_has_sphere(sdout, title_id):
    path = os.path.join(sdout, 'atmosphere', 'contents', title_id, 'romfs',
                        'ff7', 'shaders', 'lmain_vv.glsl')
    try:
        return SHADER_MARK in open(path).read()
    except OSError:
        return False


def source_shader_has_sphere():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, 'custom_shaders', 'wide_screen', 'lmain_vv.glsl')
    try:
        return SHADER_MARK in open(path).read()
    except OSError:
        return False
