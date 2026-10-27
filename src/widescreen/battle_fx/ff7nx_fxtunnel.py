"""
ff7nx_fxtunnel.py -- BUILD 680: the camera-locked "tunnel" enemy attacks
cover the 16:9 frame, not just its 4:3 middle.

THE SYMPTOM
===========
Refuse (hardware, 10-14): a green rotating shape fills the 4:3 middle; in
the 16:9 margins its corners sweep past and show the battle behind it.

WHAT IT IS
==========
Refuse is enemy attack effect 20 (enemy_atk_effects_fn_table 0x8FE5E8[20] =
0x550D40, effect ID byte 0x0D of attack 0x17E in scene.bin). Its frame
function 0x551A1C draws OKURI2.RSD -- a 12-sided tube, radius 5, length 80
-- locked to the camera:

    0x6628DE   R = rotation (0x420, frame*16, 0)
    0x663390   R = R x diag(scale)         scale = [0x8A6FE8] (x, y, z)
    0x663673 / 0x663707   GTE rotation R, translation (0, -400, 8000)
    0x5D1BA4   draw the model with it

so the tube's cross-section (model x and z) is sized by two .data words,
0x1800 (1.5) each; y (its length) is rewritten every frame (0x2000 +
frame*300). The cross-section was sized for a 4:3 view.

THE SAME SHAPE ELSEWHERE (a census, not a guess)
================================================
Every 0x663707 (GTE translation) in the battle-effect range 0x430000..
0x5E0000 whose translation is built from immediates was listed
(_scratch/671/overlay_census.py, 22 sites). Exactly three take this
camera-locked (0, -400, 8000) form, all three the same tube code with
their own scale vectors, and each owner was resolved through the enemy
attack table:

    effect  frame fn   scale @    model         attacks
    20      0x551A1C   0x8A6FE8   OKURI2 tube   Refuse, Isogin Smog
    132     0x51AE91   0x89AD58   NIZI tube     Voice of Ages, Big Sound
    137     0x521C4C   0x89DBB8   NIZI tube     Materia-jammer, Aurora Fence

(The fourth camera-locked form, 0x43DB31.. at (0, 0, 4000/6000), is Super
Nova's, effect 143: a different, centred effect -- not changed here.)

THE CHANGE
==========
Each scale vector's x and z: 0x1800 -> 0x2400 (1.5 -> 2.25, x1.5). The 16:9
frame is 4/3 the width of 4:3 at the same height, so a cross-section that
covered the 4:3 view covers 16:9 once scaled about its axis by 4/3; 1.5
leaves a margin for the tube's own turn (frame*16 about the vertical). y
is untouched (the game writes it every frame). Six .data words in ff7_en,
nothing in exefs, no shared value: each vector has exactly one reader.
SEVENTH_NX_FX_TUNNEL=0 leaves them stock.

BUILD 705: GREAT GOSPEL'S SKY
=============================
Aerith's Great Gospel (limit_break_effects_fn_table[20] = 0x473A70, files
LIMIT/EVA) draws its sky through 0x475568: the model sky_a.p (a 20 x 20
dome over model x/z, its bulge in y, textured with sky_1.tex) through the
static matrix at 0x7F36C8 (s16[3][3] + translation at +0x12; only the x
and z translation are written, 0x4755C9/0x4755D6) combined with the
effect's camera matrix (0x661E85). That matrix is [[1,0,0],[0,0,-1],
[0,1,0]] (model y -> depth). Its only reader is 0x4755E4. On 16:9 the
dome's edges come into view at the sides and the bottom corners (black
behind them).

The dome is enlarged x1.5 in its own plane (model x and z: m[0][0] 0x1000
-> 0x1800 at +0x00, m[1][2] -0x1000 -> -0x1800 at +0x0A), about its own
centre (the light), with the bulge untouched -- the same "slight zoom"
the Refuse square got (682), the texture magnified with it. sky_1.tex
moves to SYW's 1024 px canvas (ff7nx_spelltex.FULL_RES_TEXTURES) so the
zoom costs no sharpness.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

VERSION = 680
ENV = 'SEVENTH_NX_FX_TUNNEL'
STOCK = 0x1800
WIDE = 0x2400
# (scale vector VA, the frame function that reads it, attacks)
VECTORS = (
    (0x8A6FE8, 0x551A1C, 'Refuse / Isogin Smog (effect 20)'),
    (0x89AD58, 0x51AE91, 'Voice of Ages / Big Sound (effect 132)'),
    (0x89DBB8, 0x521C4C, 'Materia-jammer / Aurora Fence (effect 137)'),
)


# (va, stock s16, wide s16) -- Great Gospel's sky matrix, model x and z
GOSPEL_SKY = 0x7F36C8
GOSPEL_READER = 0x4755E4      # the imm of `push 0x7F36C8` (0x4755E3)
GOSPEL_WORDS = ((GOSPEL_SKY + 0x00, 0x1000, 0x1800),
                (GOSPEL_SKY + 0x0A, -0x1000, -0x1800))
# the rest of the matrix: must be stock and is never written
GOSPEL_REST = ((GOSPEL_SKY + 0x02, 0), (GOSPEL_SKY + 0x04, 0),
               (GOSPEL_SKY + 0x06, 0), (GOSPEL_SKY + 0x08, 0),
               (GOSPEL_SKY + 0x0C, 0), (GOSPEL_SKY + 0x0E, 0x1000),
               (GOSPEL_SKY + 0x10, 0))


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


# BUILD 749e: KOTR's curtain draws CYLINDER.RSD twice end to end, the second
# copy z_offset 2462.0 further on (x86 0x481391: fadd [0x7B763C]). The mesh is
# 2 x 6.155721 x 200 = 2462.2886 long, so the copies OVERLAP by 0.29 units;
# drawn additively, that sliver is a one-pixel bright line wherever it lands
# on a pixel row (hardware, kotr_6). 2462.29 makes them meet (a 0.0014-unit
# gap, far below a pixel). Only 0x481391 reads this float.
KOTR_STEP_VA = 0x7B763C
KOTR_STEP_STOCK, KOTR_STEP_WIDE = 0x4519E000, 0x4519E4A4   # 2462.0, 2462.29
KOTR_STEP_ENV = 'SEVENTH_NX_KOTR_STEP'


def patch_exe(data, va2off, log=lambda *_: None):
    """(new bytes, words changed). Refuses unless every word is stock or
    already widened (a rebuild over its own output is a no-op)."""
    out = bytearray(data)
    n = 0
    for va, fn, what in VECTORS:
        for k in (0, 8):                    # x and z; y (+4) is per frame
            off = va2off(va + k)
            v = struct.unpack_from('<i', out, off)[0]
            if v == WIDE:
                continue
            if v != STOCK:
                raise ValueError('fx tunnel: [0x%X] is 0x%X, not 0x%X (%s)'
                                 % (va + k, v, STOCK, what))
            struct.pack_into('<i', out, off, WIDE)
            n += 1
        w = struct.unpack_from('<i', out, va2off(va + 12))[0]
        if w != 0:
            raise ValueError('fx tunnel: [0x%X] (pad) is 0x%X, not 0' % (va + 12, w))
    for va, want in GOSPEL_REST:
        v = struct.unpack_from('<h', out, va2off(va))[0]
        if v != want:
            raise ValueError('fx tunnel: Great Gospel sky [0x%X] is %d, not %d'
                             % (va, v, want))
    g = 0
    for va, stock, wide in GOSPEL_WORDS:
        off = va2off(va)
        v = struct.unpack_from('<h', out, off)[0]
        if v == wide:
            continue
        if v != stock:
            raise ValueError('fx tunnel: Great Gospel sky [0x%X] is %d, not %d'
                             % (va, v, stock))
        struct.pack_into('<h', out, off, wide)
        g += 1
    log('  fx tunnel: %d word(s); cross-section x1.5 for %s'
        % (n, ', '.join(w for _, _, w in VECTORS)))
    log('  fx tunnel: %d word(s); Great Gospel sky dome x1.5 (BUILD 705)' % g)
    k = 0
    if os.environ.get(KOTR_STEP_ENV, '').strip().lower() not in (
            '0', 'off', 'stock', 'false', 'no'):
        off = va2off(KOTR_STEP_VA)
        v = struct.unpack_from('<I', out, off)[0]
        if v not in (KOTR_STEP_STOCK, KOTR_STEP_WIDE):
            raise ValueError('KOTR curtain step [0x%X] is 0x%X, not 0x%X'
                             % (KOTR_STEP_VA, v, KOTR_STEP_STOCK))
        if v != KOTR_STEP_WIDE:
            struct.pack_into('<I', out, off, KOTR_STEP_WIDE)
            k = 1
        log('  KOTR curtain: copy step 2462.0 -> 2462.29, the mesh length, '
            'so the two copies meet instead of overlapping (BUILD 749e; '
            '%s=0 reverts)' % KOTR_STEP_ENV)
    return bytes(out), n + g + k
