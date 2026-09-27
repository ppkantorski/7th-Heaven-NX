#!/usr/bin/env python3
"""
ff7nx_deferredvt.py -- BUILD 550. The port's gfx_drv_draw_deferred draws every
deferred primitive as LVERTEX; screen-space (TLVERTEX) ones are re-projected
and land in the wrong place. This is what broke the submarine minigame.

WHAT FF7 DEFERS
===============
The game's polygon-set draw holds back every group whose hundred-data asks
for alpha blending (V_ALPHABLEND / V_TMAPBLEND) and hands each one to the
driver's `draw_deferred` at the end of the scene (FFNx common.cpp
`common_draw_deferred`, FFNx ff7/graphics.cpp for the queueing). The queued
entry points at an `indexed_primitive`:

    +0x00 field_0   +0x04 vertex_size   +0x08 primitivetype   +0x0C vertextype
    +0x10 vertices  +0x14 vertexcount   +0x18 indices         +0x1C indexcount

FFNx draws it with `ip->primitivetype` AND `ip->vertextype`
(gl_draw_without_lighting / gl_draw_with_lighting).

WHAT THE PORT DOES
==================
Native gfx_drv_draw_deferred (+0x10DA6D0, driver table slot 162) reads the
primitive type from +0x08 correctly and then HARD-CODES the vertex type:

    +0x10DA840  mov w1, #2          ; LVERTEX, whatever the primitive says
    ...
    +0x10DA858  bl  +0x10D9D70      ; the common draw (w0 prim, w1 vtype)

FF7's vertex types are VERTEX 1, LVERTEX 2, TLVERTEX 3 (FFNx gl.h). A TLVERTEX
primitive carries pre-projected SCREEN coordinates; drawn as LVERTEX they go
through the world/view/projection matrices a second time. The submarine
minigame software-projects its whole playfield -- the translucent sea-floor
walls, the mines and the red tracking grid (GL_LINES; the port's line path is
fine: +0x10D9D70 -> +0x1137180 maps its type 2 to GL_LINES via the table at
+0x11DE1FC) -- and all of it is alpha-blended, so all of it is deferred. On
hardware the result is the correct picture flattened onto one tilted plane in
front of the camera: a rotated square with the floor pattern and the mines in
it, no grid where the grid should be, and walls you cannot see through.

THE FIX (9 words, in place, no cave)
===================================
Keep LVERTEX for everything except a primitive that says TLVERTEX, which is
drawn as TLVERTEX. Every deferred draw that worked before is byte-for-byte
the same call.

  +0x10DA800  mov x21, x0            -> mov x2, x0          vertices straight
  +0x10DA804  ldp w22, w0, [x19,#20] -> ldp w3, w0, [x19,#20]  into their
  +0x10DA818  mov x21, xzr           -> mov x2, xzr         argument registers
  +0x10DA81C  ldp w22, w0, [x19,#20] -> ldp w3, w0, [x19,#20]  (both paths)
  +0x10DA840  mov w1, #2             -> ldr w9, [x19, #0xc] ip->vertextype
  +0x10DA848  mov x2, x21            -> cmp w9, #3
  +0x10DA84C  mov w3, w22            -> mov w1, #2
  +0x10DA850  strb wzr, [sp, #8]     -> cinc w1, w1, eq     3 if TLVERTEX
  +0x10DA854  strb w8, [sp]          -> stp x8, xzr, [sp]   same two stack
                                                            bytes, one store
Register safety: between +0x10DA800 and the call the only calls are the
pointer translator +0x10FC3A0, which touches x0 and x8..x10 only (read at
build time and checked below), so x2/x3 survive; w9 is loaded after the last
translator call; `cset w7` at +0x10DA83C has consumed the flags before the new
`cmp`; x8 is the zero-extended flag byte from `ldrb` at +0x10DA834, so storing
it as a doubleword leaves byte 0 identical and bytes 1..7 zero, and xzr at
[sp+8] is the stock `strb wzr`. [sp .. sp+0x10) is the call's outgoing
argument area; x23 is saved at [sp+0x10], untouched.

Env SEVENTH_NX_DEFERRED_VT=0 restores the stock words.
"""
from __future__ import annotations

import os
import struct
import sys

ENV = 'SEVENTH_NX_DEFERRED_VT'
TITLE_ID = '0100A5B00BDC6000'

# (va, stock word, new word)
WORDS = (
    (0x10DA800, 0xAA0003F5, 0xAA0003E2),
    (0x10DA804, 0x29428276, 0x29428263),
    (0x10DA818, 0xAA1F03F5, 0xAA1F03E2),
    (0x10DA81C, 0x29428276, 0x29428263),
    (0x10DA840, 0x321F03E1, 0xB9400E69),
    (0x10DA848, 0xAA1503E2, 0x71000D3F),
    (0x10DA84C, 0x2A1603E3, 0x321F03E1),
    (0x10DA850, 0x390023FF, 0x1A811421),
    (0x10DA854, 0x390003E8, 0xA9007FE8),
)
# Untouched words the patch relies on.
ANCHORS = (
    (0x10DA6D0, 0xD10143FF),   # draw_deferred prologue
    (0x10DA7F0, 0xB9400A74),   # ldr w20, [x19, #8]   ip->primitivetype
    (0x10DA7FC, 0x940086E9),   # bl translator (vertices)
    (0x10DA80C, 0x940086E5),   # bl translator (indices)
    (0x10DA82C, 0xB9401E65),   # ldr w5, [x19, #0x1c] indexcount
    (0x10DA834, 0x39400108),   # ldrb w8, [x8]
    (0x10DA83C, 0x1A9F07E7),   # cset w7, ne
    (0x10DA844, 0x2A1403E0),   # mov w0, w20
    (0x10DA858, 0x97FFFD46),   # bl +0x10D9D70
    (0x10D9D90, 0x71000C3F),   # common draw: cmp w1, #3 (accepts 0..3)
)
# The translator must touch only x0, x8, x9, x10.
TRANSLATOR = (0x10FC3A0, (0x34000180, 0xD0000E88, 0xF9446108, 0x530C7C09,
                          0xF8695908, 0xD0000E8A, 0xF9446D4A, 0x12002C09,
                          0x8B090109, 0xF100011F, 0x9A890140, 0xD65F03C0,
                          0xAA1F03E0, 0xD65F03C0))


def enabled(env=None):
    v = (os.environ if env is None else env).get(ENV, '1').strip().lower()
    return v not in ('0', 'off', 'no', 'false', 'stock')


def _main_path(target):
    if os.path.isdir(target):
        return os.path.join(target, 'atmosphere', 'contents', TITLE_ID,
                            'exefs', 'main')
    return target


def check_text(text):
    """Problems with a .text image, [] if the patch applies/reverts cleanly."""
    w = lambda va: struct.unpack_from('<I', text, va)[0]
    bad = []
    for va, want in ANCHORS:
        if w(va) != want:
            bad.append('anchor +%#x holds %08X, expected %08X' % (va, w(va), want))
    va0, words = TRANSLATOR
    for k, want in enumerate(words):
        if w(va0 + 4 * k) != want:
            bad.append('translator +%#x changed' % (va0 + 4 * k))
            break
    cur = [w(va) for va, _s, _n in WORDS]
    if cur != [s for _v, s, _n in WORDS] and cur != [n for _v, _s, n in WORDS]:
        bad.append('patch site is neither stock nor patched: %s'
                   % ' '.join('%08X' % x for x in cur))
    return bad


def state_text(text):
    w = lambda va: struct.unpack_from('<I', text, va)[0]
    cur = [w(va) for va, _s, _n in WORDS]
    if cur == [s for _v, s, _n in WORDS]:
        return 'stock'
    if cur == [n for _v, _s, n in WORDS]:
        return 'patched'
    return 'unknown'


def apply_nso(target, revert=None, log=print):
    """Patch exefs/main in place. Returns 0 on success, 1 on refusal."""
    if revert is None:
        revert = not enabled()
    import nso_patcher
    path = _main_path(str(target))
    try:
        nso = nso_patcher.read_nso(nso_patcher.Path(path))
        text = next(bytes(sg.data) for sg in nso.segments if sg.name == '.text')
        bad = check_text(text)
        if bad:
            raise ValueError('; '.join(bad))
        st = state_text(text)
        if st == ('stock' if revert else 'patched'):
            log('  deferred draws: %s' % ('stock vertex type (%s=0)' % ENV
                                          if revert else
                                          'TLVERTEX kept (already applied)'))
            return 0
        spec = {'name': 'draw_deferred vertex type', 'patches': [{
            'name': 'draw_deferred +%#x' % va, 'va': '0x%X' % va,
            'expect': struct.pack('<I', new if revert else stock).hex(' '),
            'set': struct.pack('<I', stock if revert else new).hex(' ')}
            for va, stock, new in WORDS]}
        for line in nso_patcher.apply_spec(nso, spec):
            log('    ' + line)
        nso_patcher.Path(path).write_bytes(nso_patcher.rebuild(nso))
    except (OSError, ValueError, StopIteration, nso_patcher.PatchError) as exc:
        log('  ! deferred draws: %s -- NOT CHANGED' % exc)
        return 1
    log('  deferred draws: %s' % ('stock (every deferred primitive as LVERTEX)'
                                  if revert else
                                  'TLVERTEX primitives drawn as TLVERTEX '
                                  '(submarine playfield, grid, mines)'))
    return 0


if __name__ == '__main__':
    sys.exit(apply_nso(sys.argv[1], revert=('--revert' in sys.argv)))
