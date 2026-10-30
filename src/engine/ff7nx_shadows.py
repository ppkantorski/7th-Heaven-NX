#!/usr/bin/env python3
"""
ff7nx_shadows.py -- model shadows in battle (BUILD 645) and on the field
(BUILD 646). GUI "Model shadows".

CURRENT STATE (646l)
====================
Every battle actor and every field model casts its own shape: each body part
is drawn once more, flattened onto the floor along FFNx's default light
(light_rotation 60/60, one direction for the whole scene, as FFNx's shipped
lighting config -- no mod in this set ships a per-field one).
* battle: floor = the round blob's matrix (the blob itself is no longer
  drawn); the light is rebuilt in the battle world from the actor's facing;
  floor x SHADOW_KEEP (0.50).
* field: the parts are drawn in CAMERA space (x86-verified, see field_floor
  in native/shadows.c); floor = the walkmesh under the model, 11 below as
  FFNx; floor x FIELD_KEEP (0.40).
* each floor pixel darkened once (framebuffer alpha, 645o); the shadow is
  re-issued right after the part's own draw (fast path, 645q).
Code: the dead-space 'shadow' part (6 KB, nearly full). State: 0x210 B BSS.
Shaders: one block in lmain_vv.glsl (modes 5/6), one in colortex_p.glsl.
The sections below describe the original 645 battle design.

WHAT
====
In battle every character and enemy casts a shadow of its own shape onto the
floor -- the model drawn a second time, flattened along the light onto the
floor, black at SHADOW_ALPHA -- in place of the game's round blob. FFNx does
this with a shadow map (a 2-4 MB depth texture and its own shader programs);
this port has no cave space or memory to spare for that, and battle floors
are flat under the actors, so a planar projection gives the same picture at
no memory cost: the same vertex buffer is drawn twice.

HOW (see FINDINGS-645-MODEL-SHADOWS.md, native/shadows.c)
=========================================================
x86 0x42F3E8 draws each actor once per frame:
  0x4301AB  the blob, 0x68C9CF       ARM +0xCEF80  -> blob_stub   (mode BLOB)
  0x430212/37/58  the model, 0x684CC6  ARM +0xCF18C/+0xCF234/+0xCF290
                                                   -> actor_stub  (mode ACTOR)
Every 3D draw then reaches the native draw helper +0x10D9D70 from
gfx_drv_field_78 (+0x10D8DB8, +0x10D8F60), and there helper_stub asks
sh_pre what to do:
  BLOB   keep the renderer's model matrix (the floor under this actor), skip
         the draw -- the blob is gone;
  ACTOR  draw as usual, then sh_begin (model . flatten, blend mode 5, alpha
         blend, depth LESS + write, no cull), draw again, sh_end (every field
         back exactly as the port set it, so its state cache stays true).
The blob is semi-transparent, so field_78 queues it for draw_deferred
(outside every mode). alloc_stub notes its entry, sh_blob_done takes the
matrix from it and marks it, and skip_stub makes draw_deferred return on a
marked entry before it sets any state (645f; 645d's forced immediate draw
leaked the blob's transparency into later draws).
The blob's matrix is only kept for the actor whose blob it was: actor_stub
forgets it after the model, so an actor without a blob gets no shadow.

lmain_vv.glsl gets one line: blend mode 5 -> black at SHADOW_ALPHA. No draw
the port makes ever uses mode 5, so the card's shader is safe with any main.

OFF (SEVENTH_NX_SHADOWS=0)
==========================
Nothing is written: no word of main, no BSS, no shader line.

Code: 'shadow' part of ff7nx_deadspace (2 KB; ~1.2 KB used). State: 256 B BSS.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import nxpaths  # BUILD 755: project paths (src/nxpaths.py)
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_deadspace as DS

HERE = nxpaths.ROOT
BLOB = os.path.join(nxpaths.APP, 'native', 'shadows.bin')
ENTRY = os.path.join(nxpaths.APP, 'native', 'shadows.entry')
ENV = 'SEVENTH_NX_SHADOWS'

HELPER = 0x10D9D70                          # the native 3D/2D draw helper
HELPER_CALLS = (0x10D8DB8, 0x10D8F60)       # gfx_drv_field_78's two calls
BLOB_CALL, BLOB_TARGET = 0xCEF80, 0xB543B0  # x86 0x4301AB -> 0x68C9CF
ACTOR_CALLS = (0xCF18C, 0xCF234, 0xCF290)   # x86 0x430212/37/58 -> 0x684CC6
ACTOR_TARGET = 0xB5E630
# BUILD 645f: the blob is semi-transparent, so field_78 normally DEFERS it
# (hundred flags & 0x410 -> a draw_deferred entry) and draws it after the
# frame's opaque pass, outside every mode: no capture, round blob drawn.
# 645d forced it down the immediate path instead, but that path applies the
# blob's render state to the game's state cache, and every later draw
# (models, the top-bar text) inherited its transparency. Now the blob is
# queued exactly as stock; we only note its entry, take the matrix from it
# after the blob call (sh_blob_done) and mark it, and draw_deferred skips a
# marked entry before touching any state.
DEFER_ALLOC, DEFER_ALLOC_WORD = 0x10D864C, 0xAA0003F4   # mov x20, x0 (new entry)
DEFERRED_IN, DEFERRED_IN_WORD = 0x10DA6F0, 0xAA0003F5   # mov x21, x0 (entry)
DEFERRED_OUT = 0x10DA85C                                # draw_deferred epilogue
CTX_ENTRY = 0xD0                                        # Ctx.entry
CTX_BIAS = 0xD8                                         # Ctx.bias (645g)
CTX_PEND, CTX_FASTDONE = 0xE0, 0xE4                     # 645q
CTX_FIELD = 0xEC                                        # Ctx.field (646j)
CTX_SONLY = 0x274                                       # Ctx.sonly (647e)
CTX_FADE = 0x338                                        # Ctx.fade (655)
CTX_NDEF, CTX_DENT, DENT_MAX = 0x340, 0x348, 6          # 667: pass-1 entries
CTX_MARKED, CTX_MORIG = 0x378, 0x37C                    # 667d: the model mark
CTX_CAM = 0x380                                          # 670: ff7nx_subcam's state
RS_BLEND_OFF = 0x0C                                     # renderer state: blend mode
MARK_BIT = 0x100000                                     # 667d: lmain_vv blendMode.x
LO = 0x3                                                # b.lo (unsigned <)
# BUILD 646j: the field light is FFNx's default (60/60), fixed in the world,
# in native/shadows.c; 646f-i's FIELD_TILT is gone.
# BUILD 645q: the draw helper's own draw call (its main path) and the uniform
# ring it uploads BlockVertex through (module +0x570)
DRAW_CALL, DRAW_TARGET = 0x10DA484, 0x1132140
UBO_RING_GOT, UBO_FRAME_GOT = 0x12CE518, 0x12CE520
# BUILD 646: field models. field_draw_everything (x86 0x63A60B) draws each
# model with draw_3d_model (x86 0x6840DA, ARM +0xB54AE0) at these two sites;
# its third argument is the model's struc_110 (646j: its position is the
# model's origin in CAMERA space, see field_floor in native/shadows.c).
# The x86 stack pointer is the context's +0x10; at the call the return slot
# is pushed, so argument n sits at esp + 4n.
FIELD_CALLS, MODEL_TARGET = (0x9E756C, 0x9E7C7C), 0xB54AE0
X86_CTX_GOT, X86_ESP, GUEST_TO_HOST = 0x12CE2B0, 0x10, 0x10FC3A0
# BUILD 647: world-map models. 0x74C6B0 draws each with draw_3d_model at
# these two sites (struc_110 = arg3, as on the field). 0x75E0BA builds each
# model's camera matrix and converts it with 0x661465 (ARM +0x6C90) at
# WCAM_CALL; wcam_stub lets sh_wcam keep the model's up and the light there.
WORLD_CALLS = (0xF2087C, 0xF20B70)
WCAM_CALL, WCAM_TARGET = 0xF863C4, 0x6C90
# BUILD 647b: 0x763B74's call to 0x75DEAA (the round shadow sprite under a
# world object, x86 0x763C2B): wblob_stub skips it for a ground model, which
# now casts its own shadow (FFNx removes the round shadow altogether).
WBLOB_CALL, WBLOB_TARGET = 0xF953DC, 0xF856B0
# BUILD 647b: depth bias (blob radii, see BIAS) on the field and world map.
# The world terrain is bumpy and a model's terrain tilt is smoothed, so a
# flat shadow cut into the ground along a straight edge (hardware, 647):
# lift it more there. The field had no value of its own (battle's, or 0
# before the first battle).
# 647e: the world's shadows are drawn before their model (world_stub), so a
# bigger lift cannot darken the model: 1.5 radii = half the hips' height.
FIELD_BIAS, WORLD_BIAS = 0.15, 1.5
# BUILD 646i: the guest page table +0x10FC3A0 indexes (its GOT entry holds
# the table itself); sh_field reads the field's walkmesh through it.
GUEST_PAGES_GOT = 0x12CE8C0
# BUILD 645g: how far each shadow is moved toward the eye, in blob radii.
# Battle floors are not flat; a shadow lying exactly on the blob's plane
# dipped under every bump. Moved along the eye ray it stays put on screen and
# only gains depth. Too small: bumps show through; too large: the shadow
# would darken an actor's soles. 0.15 radius is about ankle-deep.
BIAS = 0.15
DE_HASMAT, DE_SKIP = 0x10, 0x5AD
RS_GOT = 0x12CE668                          # -> renderer state (matrices, blend)
RB_GOT = 0x12CE548                          # -> render-state block (+0x740..)
DEV_GOT = 0x12CE510                         # -> the GL device (passed, unused)
# BUILD 645o: 645m's stencil (and its depth+stencil clears) is gone -- the
# framebuffer has no stencil buffer (hardware test). Shadowed pixels are
# marked in the framebuffer's alpha instead, in the shadow's one draw
# (native/shadows.c sh_begin).
STATE_BYTES = 0x3A0                         # sizeof(Ctx) = 0x208 (646e FEET rows, 646i pages)
MODE_BLOB, MODE_ACTOR = 1, 2

SHADOW_ALPHA = '0.40'                       # 645-645n: black at this alpha
SHADOW_KEEP = '0.50'                        # the floor keeps 50% (645o: 60%; 645p darker)
# BUILD 645i: colortex_p multiplied the shadow's alpha by the model texture's,
# so the shadow showed the texture's alpha pattern (the shimmer). lmain_vv now
# marks a shadow vertex by moving its UV down by UV_SHIFT (no model UV is
# below 0), and colortex_p draws a marked fragment flat SHADOW_ALPHA black,
# keeping only the texture's cut-outs (alpha < 0.5 discarded).
UV_SHIFT = '16.0'
SHADER_MARK = 'ff7nx_shadows 667d'          # lmain_vv
FP_MARK = 'ff7nx_shadows 645i'              # colortex_p (unchanged since 645i)
OLD_SHADER_LINE = ('\t// Model shadows (ff7nx_shadows): the module draws an actor a '
                   'second time,\n\t// flattened onto the floor, in blend mode 5 -- '
                   'a mode no other draw uses.\n'
                   '\tif (blendMode.x == 5) vColor = vec4(0.0, 0.0, 0.0, %s);\n\n'
                   % SHADOW_ALPHA)
SHADER_ANCHOR = '\tvTextureCoord = TextureCoord;'
_VS = ('\n\t// Model shadows (ff7nx_shadows %s): the module draws an actor a '
       'second time,\n\t// flattened onto the floor, in blend mode 5 -- '
       'a mode no other draw uses.\n\t// %s; the UV is moved below 0 '
       'so colortex_p knows to ignore the texture.\n'
       '\tif (blendMode.x == 5) {\n'
       '\t\tvColor = %s;\n'
       '\t\tvTextureCoord = TextureCoord - vec2(%s);\n'
       '\t}\n')
# 645i: black at SHADOW_ALPHA, alpha-blended over the floor
OLD_SHADER_LINE_645I = _VS % ('645i', 'Black',
                              'vec4(0.0, 0.0, 0.0, %s)' % SHADOW_ALPHA, UV_SHIFT)
# 645o: SHADOW_KEEP in every channel -- the floor colour is multiplied by it
# and its alpha marks the pixel as shadowed (native/shadows.c sh_begin)
OLD_SHADER_LINE_645O = _VS % ('645o', 'Grey 0.60, the share of the floor kept',
                              'vec4(0.60)', UV_SHIFT)
OLD_SHADER_LINE_645P = _VS % ('645p', 'Grey 0.50, the share of the floor kept',
                              'vec4(0.50)', UV_SHIFT)
# BUILD 646k: field shadows are drawn in blend mode 6 and keep FIELD_KEEP of
# the floor, FFNx's field shadow (shadowmap_occlusion 0.3, alpha blended:
# floor x 0.30 where fully shadowed); battle keeps SHADOW_KEEP (645p).
FIELD_KEEP = '0.40'                         # 646l: FFNx's 0.30 was a tad dark (HW)
SHADER_LINE = ('\n\t// Model shadows (ff7nx_shadows 646l): the module draws a model a '
               'second time,\n\t// flattened onto the floor, in blend mode 5 (battle) '
               'or 6 (field) --\n\t// modes no other draw uses. Grey = the share of '
               'the floor kept (battle %s,\n\t// field %s); the UV is moved '
               'below 0 so colortex_p knows to ignore the texture.\n'
               '\tif (blendMode.x == 5 || blendMode.x == 6) {\n'
               '\t\tvColor = vec4(blendMode.x == 6 ? %s : %s);\n'
               '\t\tvTextureCoord = TextureCoord - vec2(%s);\n'
               '\t}\n' % (SHADOW_KEEP, FIELD_KEEP, FIELD_KEEP, SHADOW_KEEP, UV_SHIFT))
# BUILD 655: FFNx's height fade -- the module puts 0..100 in blendMode.y
# and FADE_MAGIC in blendMode.z on a field shadow (sh_begin's fast path),
# and the floor keeps more of itself as the model rises off it.
OLD_SHADER_LINE_646L = SHADER_LINE
FADE_MAGIC = 0x534844
SHADER_LINE = ('\n\t// Model shadows (ff7nx_shadows 655): the module draws a model a '
               'second time,\n\t// flattened onto the floor, in blend mode 5 (battle) '
               'or 6 (field) --\n\t// modes no other draw uses. Grey = the share of '
               'the floor kept (battle %s,\n\t// field %s, rising to 1.0 with the '
               "FFNx height fade in blendMode.y);\n\t// the UV is moved below 0 so "
               'colortex_p knows to ignore the texture.\n'
               '\tif (blendMode.x == 5 || blendMode.x == 6) {\n'
               '\t\tfloat shKeep = blendMode.x == 6 ? %s : %s;\n'
               '\t\tif (blendMode.x == 6 && blendMode.z == %d)\n'
               '\t\t\tshKeep = mix(%s, 1.0, clamp(float(blendMode.y) / 100.0, 0.0, 1.0));\n'
               '\t\tvColor = vec4(shKeep);\n'
               '\t\tvTextureCoord = TextureCoord - vec2(%s);\n'
               '\t}\n' % (SHADOW_KEEP, FIELD_KEEP, FIELD_KEEP, SHADOW_KEEP,
                          FADE_MAGIC, FIELD_KEEP, UV_SHIFT))
# BUILD 658: FFNx offsets its field shadow's depth toward the eye
# (FFNx.field.shadow.frag: depth' = 1.0075 depth - 0.0075, "to prevent some
# weird occlusion problems with field 2D tiles"); the 2D background tiles'
# depth hid our field shadow on some floors (convil_1's item shop).
OLD_SHADER_LINE_655 = SHADER_LINE
SHADER_LINE = SHADER_LINE.replace('655', '658').replace(
    '\t\tvColor = vec4(shKeep);\n',
    '\t\tvColor = vec4(shKeep);\n'
    '\t\tif (blendMode.x == 6) {\n'
    '\t\t\tfloat shD = (gl_Position.z / gl_Position.w + 1.0) * 0.5;\n'
    '\t\t\tshD = 1.0075 * shD - 0.0075;\n'
    '\t\t\tgl_Position.z = (2.0 * shD - 1.0) * gl_Position.w;\n'
    '\t\t}\n')
# BUILD 667d: the MODEL MARK. sh_pre sets blendMode.x = MARK_BIT | fade << 8
# | 4 on an opaque field-model part; the part's framebuffer alpha becomes its
# model's shadow grey s, so a shadow (rgb * (s + 1 - dst alpha), 645o) drawn
# after it leaves it as it is. Colour untouched (mode 4 draws ONE/ZERO).
OLD_SHADER_LINE_658 = SHADER_LINE
SHADER_LINE = SHADER_LINE.replace('658', '667d') + (
    '\t// Model mark (ff7nx_shadows 667d): an opaque field-model part writes\n'
    '\t// its model\'s shadow grey as alpha, so no shadow can darken it.\n'
    '\tif (blendMode.x >= %d)\n'
    '\t\tvColor.a = mix(%s, 1.0, clamp(float((blendMode.x >> 8) & 127) / 100.0, 0.0, 1.0));\n'
    % (MARK_BIT, FIELD_KEEP))
# 646k (test only): field 0.30, FFNx's
OLD_SHADER_LINE_646K = (OLD_SHADER_LINE_646L.replace('646l', '646k')
                        .replace('field %s); the UV' % FIELD_KEEP, 'field 0.30 as FFNx); the UV')
                        .replace('== 6 ? %s :' % FIELD_KEEP, '== 6 ? 0.30 :'))
FP_ANCHOR = '\tvec4 color = texture(Sampler0, uv) * vColor;'
FP_LINE = ('\n\t// Model shadows (ff7nx_shadows 645i): lmain_vv moved this UV '
           'below 0 -- a\n\t// shadow. Flat black, only the texture\'s '
           'cut-outs kept.\n'
           '\tif (vTextureCoord.x < -%s * 0.5) {\n'
           '\t\tif (texture(Sampler0, uv + vec2(%s)).a < 0.5) discard;\n'
           '\t\tcolor = vColor;\n'
           '\t}\n' % (UV_SHIFT, UV_SHIFT))


def enabled(env=None):
    v = (os.environ if env is None else env).get(ENV, '1').strip().lower()
    return v not in ('0', 'off', 'false', 'no')


def entries():
    out = {}
    for line in open(ENTRY):
        name, off = line.split()
        out[name] = int(off, 16)
    return out


def _addr(w, reg, at, value):
    w.append(A.adrp(reg, at + 4 * len(w), value & ~0xFFF))
    w.append(A.add_imm64(reg, reg, value & 0xFFF))


def _got(w, reg, at, got):
    w.append(A.adrp(reg, at + 4 * len(w), got & ~0xFFF))
    w.append(A.ldr64(reg, reg, got & 0xFFF))


def helper_stub(at, state, fn):
    """bl here instead of the helper: x0..x7 and two stack bytes are its args.

    BUILD 645q: when sh_pre wants a shadow, Ctx.pend is set around the
    model's own helper call; fast_stub (inside the helper, at its draw call)
    then issues the shadow right after the model and sets Ctx.fastdone. Only
    if it did not (a draw that took one of the helper's other paths) is the
    shadow drawn the old way, through a second helper call."""
    FR = 0x70
    w = [A.sub_imm64(31, 31, FR), A.stp64_off(29, 30, 31, 0x60),
         A.stp64_off(0, 1, 31, 0x10), A.stp64_off(2, 3, 31, 0x20),
         A.stp64_off(4, 5, 31, 0x30), A.stp64_off(6, 7, 31, 0x40),
         A.ldr64(9, 31, FR), A.str64(9, 31, 0),          # the caller's [sp]
         A.ldr64(9, 31, FR + 8), A.str64(9, 31, 8)]      # and [sp, #8]
    _addr(w, 0, at, state)
    _got(w, 1, at, RS_GOT)
    w.append(A.ldr(2, 31, 0x38))                         # w5: the index count (645p)
    w.append(A.bl(at + 4 * len(w), fn['sh_pre']))
    w.append(A.str_(0, 31, 0x50))
    w.append(A.cmp_imm(0, 1))
    done = []

    def reload():
        w.extend([A.ldp64_off(0, 1, 31, 0x10), A.ldp64_off(2, 3, 31, 0x20),
                  A.ldp64_off(4, 5, 31, 0x30), A.ldp64_off(6, 7, 31, 0x40)])
    done.append(len(w)); w.append(None)                  # b.eq done (skip)
    # pend = (action == SHADOW); fastdone = 0
    _addr(w, 9, at, state)
    w += [A.cmp_imm(0, 2), A.csinc(10, 31, 31, A.NE),    # w10 = (w0 == 2)
          A.str_(10, 9, CTX_PEND), A.str_(31, 9, CTX_FASTDONE)]
    reload()
    w.append(A.bl(at + 4 * len(w), HELPER))
    _addr(w, 9, at, state)
    w += [A.str_(31, 9, CTX_PEND), A.ldr(10, 9, CTX_FASTDONE)]
    done.append(len(w)); w.append(None)                  # cbnz w10, done
    w.append(A.ldr(9, 31, 0x50))
    w.append(A.cmp_imm(9, 2))
    done.append(len(w)); w.append(None)                  # b.ne done
    for name in ('sh_begin', None, 'sh_end'):
        if name is None:
            reload()
            w.append(A.bl(at + 4 * len(w), HELPER))
            continue
        _addr(w, 0, at, state)
        _got(w, 1, at, RS_GOT)
        _got(w, 2, at, RB_GOT)
        _got(w, 3, at, DEV_GOT)
        if name == 'sh_begin':
            w.append(A.mov_reg64(4, 31))                 # fast = NULL: the slow path
        w.append(A.bl(at + 4 * len(w), fn[name]))
    end = len(w)
    w += [A.ldp64_off(29, 30, 31, 0x60), A.add_imm64(31, 31, FR), A.ret()]
    w[done[0]] = A.bcond(at + 4 * done[0], at + 4 * end, A.EQ)
    w[done[1]] = A.cbnz(10, at + 4 * done[1], at + 4 * end)
    w[done[2]] = A.bcond(at + 4 * done[2], at + 4 * end, A.NE)
    return w


def fast_stub(at, state, fn, cam=None):
    """bl here instead of the draw helper's own draw call (DRAW_CALL): make
    that draw, then -- when helper_stub marked this model draw (Ctx.pend) --
    sh_begin with the draw's arguments issues the shadow (645q)."""
    FR = 0x90
    w = [A.stp64_pre(29, 30, 31, -FR),
         A.stp64_off(0, 1, 31, 0x10), A.stp64_off(2, 3, 31, 0x20),
         A.stp64_off(4, 5, 31, 0x30)]
    # BUILD 647e: in a shadow-only pass the part's own draw is skipped; the
    # shadow below still finds everything the helper bound for it.
    _addr(w, 9, at, state)
    w += [A.ldr(10, 9, CTX_SONLY), A.ldr(11, 9, CTX_PEND)]
    d1 = len(w); w.append(None)                          # cbz w10, draw
    d2 = len(w); w.append(None)                          # cbz w11, draw
    w.append(A.str64(31, 31, 0x40))                      # skipped: x0 = 0
    joined = len(w); w.append(None)                      # b joined
    draw = len(w)
    w.append(A.bl(at + 4 * len(w), DRAW_TARGET))
    w.append(A.str64(0, 31, 0x40))
    w[d1] = A.cbz(10, at + 4 * d1, at + 4 * draw)
    w[d2] = A.cbz(11, at + 4 * d2, at + 4 * draw)
    w[joined] = A.b(at + 4 * joined, at + 4 * len(w))
    _addr(w, 9, at, state)
    w.append(A.ldr(10, 9, CTX_PEND))
    out = len(w); w.append(None)                         # cbz w10, out
    # fast[0..4] = x1..x5 of the draw, fast[5] / [6] = the uniform ring's GOTs
    w += [A.ldp64_off(10, 11, 31, 0x18), A.stp64_off(10, 11, 31, 0x48),  # x1 x2
          A.ldp64_off(10, 11, 31, 0x28), A.stp64_off(10, 11, 31, 0x58),  # x3 x4
          A.ldr64(10, 31, 0x38), A.str64(10, 31, 0x68)]                  # x5
    _got(w, 10, at, UBO_RING_GOT)
    _got(w, 11, at, UBO_FRAME_GOT)
    w.append(A.stp64_off(10, 11, 31, 0x70))
    _addr(w, 0, at, state)
    # BUILD 670: ff7nx_subcam's state for the fast shadow block (0: none)
    if cam:
        _addr(w, 10, at, cam)
        w.append(A.str64(10, 0, CTX_CAM))
    else:
        w.append(A.str64(31, 0, CTX_CAM))
    _got(w, 1, at, RS_GOT)
    _got(w, 2, at, RB_GOT)
    _got(w, 3, at, DEV_GOT)
    w.append(A.add_imm64(4, 31, 0x48))
    w.append(A.bl(at + 4 * len(w), fn['sh_begin']))
    _addr(w, 9, at, state)
    w += [A.str_(0, 9, CTX_FASTDONE), A.str_(31, 9, CTX_PEND)]
    end = len(w)
    w += [A.ldr64(0, 31, 0x40), A.ldp64_post(29, 30, 31, FR), A.ret()]
    w[out] = A.cbz(10, at + 4 * out, at + 4 * end)
    return w


def field_stub(at, state, fn, setup='sh_field', bias=None):
    """bl here from field_draw_everything instead of draw_3d_model: hand
    sh_field the model's struc_110 (guest address) and the guest page table,
    draw it in mode ACTOR, then clear the mode, the floor and the field flag
    (646, 646j).

    BUILD 667c: back to ONE pass. 667's two passes (shadow first, as the
    world map) cost a second draw_3d_model per field model -- 52-57 fps in
    mds7pb_1 (hardware). What it was fixing was not draw order: that field's
    floor squares had depth at the floor itself while the soles stand 10
    below it, so the boot bottoms were hidden and the shadowed floor showed
    there (ff7nx_floordepth SOLE_SINK).

    BUILD 667d: one pass, and sh_pre MARKS each opaque part (lmain_vv writes
    its alpha as the model's shadow grey) so no shadow drawn after it can
    darken it. The renderer's blend mode is a cached state, so the last
    part's mark is taken off here when the model is done."""
    w = [A.stp64_pre(29, 30, 31, -0x10)]
    _got(w, 9, at, X86_CTX_GOT)
    w += [A.ldr(0, 9, X86_ESP), A.add_imm(0, 0, 12)]      # &arg3 (guest)
    w.append(A.bl(at + 4 * len(w), GUEST_TO_HOST))
    w.append(A.ldr(1, 0, 0))                              # w1: arg3, struc_110 (guest)
    _addr(w, 0, at, state)
    _got(w, 2, at, GUEST_PAGES_GOT)
    w.append(A.bl(at + 4 * len(w), fn[setup]))
    _addr(w, 9, at, state)
    if bias is not None:
        bits = struct.unpack('<I', struct.pack('<f', bias))[0]
        w += [A.movz(10, bits & 0xFFFF), A.movk_hi(10, bits >> 16),
              A.str_(10, 9, CTX_BIAS)]
    w += [A.movz(10, MODE_ACTOR), A.str_(10, 9, 0)]
    w.append(A.bl(at + 4 * len(w), MODEL_TARGET))
    _addr(w, 9, at, state)
    w.append(A.ldr(10, 9, CTX_MARKED))
    skip = len(w); w.append(None)                         # cbz w10, unmarked
    _got(w, 11, at, RS_GOT)
    w += [A.ldr(10, 9, CTX_MORIG), A.str_(10, 11, RS_BLEND_OFF),
          A.str_(31, 9, CTX_MARKED)]
    w[skip] = A.cbz(10, at + 4 * skip, at + 4 * len(w))
    w += [A.str_(31, 9, 0), A.str_(31, 9, 4),              # mode 0, have_blob 0
          A.str_(31, 9, CTX_FIELD),                        # field 0
          A.ldp64_post(29, 30, 31, 0x10), A.ret()]
    return w


def wcam_stub(at, state, fn):
    """bl here instead of 0x75E0BA's call to 0x661465 (an x86-convention
    call: its arguments are on the guest stack): make the call, then
    sh_wcam(ctx, x86 context, guest page table) (647)."""
    w = [A.stp64_pre(29, 30, 31, -0x10)]
    w.append(A.bl(at + 4 * len(w), WCAM_TARGET))
    _addr(w, 0, at, state)
    _got(w, 1, at, X86_CTX_GOT)
    _got(w, 2, at, GUEST_PAGES_GOT)
    w.append(A.bl(at + 4 * len(w), fn['sh_wcam']))
    w += [A.ldp64_post(29, 30, 31, 0x10), A.ret()]
    return w


def wblob_stub(at, state, fn):
    """bl here instead of 0x763B74's call to 0x75DEAA (x86 convention, the
    world object on the guest stack): skip it when sh_wblob says the object
    casts its own shadow (647b)."""
    w = [A.stp64_pre(29, 30, 31, -0x10)]
    _addr(w, 0, at, state)
    _got(w, 1, at, X86_CTX_GOT)
    _got(w, 2, at, GUEST_PAGES_GOT)
    w.append(A.bl(at + 4 * len(w), fn['sh_wblob']))
    skip = len(w); w.append(None)                        # cbnz w0, pop
    w.append(A.bl(at + 4 * len(w), WBLOB_TARGET))
    done = len(w); w.append(None)                        # b out
    # BUILD 647c: a skipped call must still pop its return slot, as the
    # callee's epilogue does (x86 ret: esp += 4; +0xF85C28). 647b did not,
    # and the guest stack drifted 4 bytes per shadowed object: crash on
    # entering the world map (hardware).
    pop = len(w)
    _got(w, 9, at, X86_CTX_GOT)
    w += [A.ldr(10, 9, X86_ESP), A.add_imm(10, 10, 4), A.str_(10, 9, X86_ESP)]
    out = len(w)
    w += [A.ldp64_post(29, 30, 31, 0x10), A.ret()]
    w[skip] = A.cbnz(0, at + 4 * skip, at + 4 * pop)
    w[done] = A.b(at + 4 * done, at + 4 * out)
    return w


def world_stub(at, state, fn):
    """bl here from 0x74C6B0 instead of draw_3d_model (647e): sh_world, then
    the two passes (two_pass_stub) with the world's bias."""
    return two_pass_stub(at, state, fn, 'sh_world', WORLD_BIAS)


def two_pass_stub(at, state, fn, setup, bias):
    """`setup`(ctx, struc_110, guest page table); then -- when the model has
    a shadow -- draw_3d_model in the shadow-only pass, then once more as
    usual with no shadow. Each call of the translated draw_3d_model pops the
    return slot its caller pushed, so the slot is pushed again (guest
    esp -= 4) before the second call (647e)."""
    w = [A.stp64_pre(29, 30, 31, -0x10)]
    _got(w, 9, at, X86_CTX_GOT)
    w += [A.ldr(0, 9, X86_ESP), A.add_imm(0, 0, 12)]      # &arg3 (guest)
    w.append(A.bl(at + 4 * len(w), GUEST_TO_HOST))
    w.append(A.ldr(1, 0, 0))                              # w1: struc_110 (guest)
    _addr(w, 0, at, state)
    _got(w, 2, at, GUEST_PAGES_GOT)
    w.append(A.bl(at + 4 * len(w), fn[setup]))
    _addr(w, 9, at, state)
    if bias is not None:
        bits = struct.unpack('<I', struct.pack('<f', bias))[0]
        w += [A.movz(10, bits & 0xFFFF), A.movk_hi(10, bits >> 16),
              A.str_(10, 9, CTX_BIAS)]
    w += [A.movz(10, MODE_ACTOR), A.str_(10, 9, 0),
          A.ldr(11, 9, 4)]                                # have_blob
    no = len(w); w.append(None)                           # cbz w11, normal
    w += [A.movz(10, 1), A.str_(10, 9, CTX_SONLY), A.str_(31, 9, CTX_NDEF)]
    w.append(A.bl(at + 4 * len(w), MODEL_TARGET))         # pass 1: shadows
    # BUILD 667: the deferred entries pass 1 queued are drawn by pass 2's
    # own entries; mark these so draw_deferred skips them (alloc_stub)
    _addr(w, 9, at, state)
    skips = []
    for i in range(DENT_MAX):
        w += [A.ldr(11, 9, CTX_NDEF), A.cmp_imm(11, i + 1)]
        skips.append(len(w)); w.append(None)              # b.lo marked
        w += [A.ldr64(10, 9, CTX_DENT + 8 * i), A.movz(12, DE_SKIP),
              A.str_(12, 10, DE_HASMAT)]
    marked = len(w)
    for k in skips:
        w[k] = A.bcond(at + 4 * k, at + 4 * marked, LO)
    w.append(A.str_(31, 9, CTX_NDEF))
    _got(w, 9, at, X86_CTX_GOT)
    w += [A.ldr(10, 9, X86_ESP), A.sub_imm(10, 10, 4), A.str_(10, 9, X86_ESP)]
    normal = len(w)
    _addr(w, 9, at, state)
    w += [A.str_(31, 9, CTX_SONLY), A.str_(31, 9, 4)]     # no shadow in pass 2
    w.append(A.bl(at + 4 * len(w), MODEL_TARGET))
    _addr(w, 9, at, state)
    w += [A.str_(31, 9, 0), A.str_(31, 9, 4), A.str_(31, 9, CTX_FIELD),
          A.str_(31, 9, CTX_SONLY),
          A.ldp64_post(29, 30, 31, 0x10), A.ret()]
    w[no] = A.cbz(11, at + 4 * no, at + 4 * normal)
    return w


def alloc_stub(at, state):
    """bl here instead of field_78's `mov x20, x0` (x0: the new deferred
    entry): the same move, and in mode BLOB note the entry. x16/x17 are
    unused in field_78."""
    w = [0xAA0003F4]                                     # mov x20, x0
    _addr(w, 16, at, state)
    w += [A.ldr(17, 16, 0), A.cmp_imm(17, MODE_BLOB)]
    w.append(A.bcond(at + 4 * len(w), at + 4 * (len(w) + 3), A.NE))
    w += [A.str64(0, 16, CTX_ENTRY), A.ret()]           # (667: b.ne -> below)
    # BUILD 667: an entry queued during a shadow-only pass is the model's
    # semi-transparent part, which pass 2 queues again: note it, and
    # two_pass_stub marks it DE_SKIP so it is drawn once, not twice.
    w.append(A.ldr(17, 16, CTX_SONLY))
    out = []
    out.append(len(w)); w.append(None)                   # cbz w17, ret
    w += [A.ldr(17, 16, CTX_NDEF), A.cmp_imm(17, DENT_MAX)]
    out.append(len(w)); w.append(None)                   # b.hs ret
    w += [A.add_imm(17, 17, 1), A.str_(17, 16, CTX_NDEF),
          A.sub_imm(17, 17, 1),
          A.add_reg64_lsl(16, 16, 17, 3), A.str64(0, 16, CTX_DENT)]
    end = len(w)
    w.append(A.ret())
    w[out[0]] = A.cbz(17, at + 4 * out[0], at + 4 * end)
    w[out[1]] = A.bcond(at + 4 * out[1], at + 4 * end, A.HS)
    return w


def skip_stub(at):
    """b here instead of draw_deferred's `mov x21, x0` (x0: the entry): a
    marked blob entry returns at once, anything else carries on unchanged.
    x9 is written before it is read in draw_deferred."""
    w = [0xAA0003F5,                                     # mov x21, x0
         A.ldr(9, 0, DE_HASMAT), A.cmp_imm(9, DE_SKIP)]
    w.append(A.bcond(at + 4 * len(w), at + 4 * (len(w) + 2), A.NE))
    w.append(A.b(at + 4 * len(w), DEFERRED_OUT))
    w.append(A.b(at + 4 * len(w), DEFERRED_IN + 4))
    return w


def mode_stub(at, state, target, mode, forget, done=None, bias=None):
    """bl here from a translated call site: set the mode, make the call,
    clear it (and, after the model, forget the blob's matrix). `done` is
    called with the state after the call, still in the mode (645f)."""
    w = [A.stp64_pre(29, 30, 31, -0x10)]
    _addr(w, 9, at, state)
    w += [A.movz(10, mode), A.str_(10, 9, 0)]
    if bias is not None:
        bits = struct.unpack('<I', struct.pack('<f', bias))[0]
        w += [A.movz(10, bits & 0xFFFF), A.movk_hi(10, bits >> 16),
              A.str_(10, 9, CTX_BIAS)]
    w.append(A.bl(at + 4 * len(w), target))
    if done is not None:
        # sh_blob_done(ctx, x86 context, guest page table): 646l reads the
        # actor's facing through them
        _addr(w, 0, at, state)
        _got(w, 1, at, X86_CTX_GOT)
        _got(w, 2, at, GUEST_PAGES_GOT)
        w.append(A.bl(at + 4 * len(w), done))
    _addr(w, 9, at, state)
    w.append(A.str_(31, 9, 0))                            # mode = 0
    if forget:
        w.append(A.str_(31, 9, 4))                        # have_blob = 0
    w += [A.ldp64_post(29, 30, 31, 0x10), A.ret()]
    return w


SITES = ([(s, HELPER, 'gfx_drv_field_78 draw helper call') for s in HELPER_CALLS]
         + [(BLOB_CALL, BLOB_TARGET, 'battle blob shadow draw')]
         + [(DRAW_CALL, DRAW_TARGET, 'draw helper: the draw call')]
         + [(s, MODEL_TARGET, 'field model draw') for s in FIELD_CALLS]
         + [(s, MODEL_TARGET, 'world-map model draw') for s in WORLD_CALLS]
         + [(WCAM_CALL, WCAM_TARGET, 'world-map model camera')]
         + [(WBLOB_CALL, WBLOB_TARGET, 'world-map round shadow')]
         + [(s, ACTOR_TARGET, 'battle actor model draw') for s in ACTOR_CALLS])


def check(text):
    for va, want, what in ((DEFER_ALLOC, DEFER_ALLOC_WORD, 'field_78 deferred entry'),
                           (DEFERRED_IN, DEFERRED_IN_WORD, 'draw_deferred entry')):
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            raise ValueError('shadows: +0x%X (%s) is %08X, not %08X'
                             % (va, what, got, want))
    for site, target, what in SITES:
        got = struct.unpack_from('<I', text, site)[0]
        if got != A.bl(site, target):
            raise ValueError('shadows: +0x%X (%s) is %08X, not bl +0x%X'
                             % (site, what, got, target))


def apply_to_nso(src, dest, stock=None, log=lambda *_: None):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    check(text)
    lo, hi = DS.part(src, 'shadow', stock)
    code = open(BLOB, 'rb').read()
    ent = entries()
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = (state - base) + STATE_BYTES + AC.bss_tail_slack(segs)
    entry = (lo + 15) & ~15
    fn = {k: entry + v for k, v in ent.items()}
    at = (entry + len(code) + 15) & ~15
    words = {}
    stubs = {}

    def put(name, ws):
        nonlocal at
        stubs[name] = at
        for i, x in enumerate(ws):
            words[at + 4 * i] = x
        at = (at + 4 * len(ws) + 15) & ~15

    put('helper', helper_stub(at, state, fn))
    put('blob', mode_stub(at, state, BLOB_TARGET, MODE_BLOB, False,
                          fn['sh_blob_done']))
    put('actor', mode_stub(at, state, ACTOR_TARGET, MODE_ACTOR, True,
                           bias=BIAS))
    cam = None
    try:                                     # BUILD 670: installed before us
        import ff7nx_subcam
        import nxmap
        cam = ff7nx_subcam.state_of(nxmap.Main(src).img)
    except Exception:                                          # noqa: BLE001
        cam = None
    put('fast', fast_stub(at, state, fn, cam))
    put('field', field_stub(at, state, fn, bias=FIELD_BIAS))
    put('world', world_stub(at, state, fn))
    put('wblob', wblob_stub(at, state, fn))
    put('wcam', wcam_stub(at, state, fn))
    put('alloc', alloc_stub(at, state))
    put('skip', skip_stub(at))
    if at > hi:
        raise ValueError('shadows: %d bytes do not fit the %d-byte shadow part'
                         % (at - lo, hi - lo))
    text[entry:entry + len(code)] = code
    for va, x in words.items():
        struct.pack_into('<I', text, va, x)
    for s in HELPER_CALLS:
        struct.pack_into('<I', text, s, A.bl(s, stubs['helper']))
    struct.pack_into('<I', text, BLOB_CALL, A.bl(BLOB_CALL, stubs['blob']))
    struct.pack_into('<I', text, DRAW_CALL, A.bl(DRAW_CALL, stubs['fast']))
    for s in FIELD_CALLS:
        struct.pack_into('<I', text, s, A.bl(s, stubs['field']))
    for s in WORLD_CALLS:
        struct.pack_into('<I', text, s, A.bl(s, stubs['world']))
    struct.pack_into('<I', text, WCAM_CALL, A.bl(WCAM_CALL, stubs['wcam']))
    struct.pack_into('<I', text, WBLOB_CALL, A.bl(WBLOB_CALL, stubs['wblob']))
    struct.pack_into('<I', text, DEFER_ALLOC, A.bl(DEFER_ALLOC, stubs['alloc']))
    struct.pack_into('<I', text, DEFERRED_IN, A.b(DEFERRED_IN, stubs['skip']))
    for s in ACTOR_CALLS:
        struct.pack_into('<I', text, s, A.bl(s, stubs['actor']))
    raw[0] = bytes(text)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(AC.pack(blob, raw, growth))
    rep = {'code': entry, 'bytes': at - lo, 'stubs': stubs, 'state': state,
           'bss': growth, 'entries': fn, 'cam': cam}
    log('  model shadows: battle actors (blob replaced), field and world-map models '
        'cast their own shape; code +0x%X (%d bytes of the shadow part), state +0x%X (%d B BSS)'
        % (entry, at - lo, state, growth))
    return rep


def patch_shader(path, log=lambda *_: None):
    """Add the blend-mode-5 lines to an lmain_vv.glsl (idempotent; replaces
    the 645 line on a card that has it)."""
    text = open(path).read()
    if SHADER_MARK in text:
        return False
    text = text.replace(OLD_SHADER_LINE, '')
    for old in (OLD_SHADER_LINE_645I, OLD_SHADER_LINE_645O, OLD_SHADER_LINE_645P,
                OLD_SHADER_LINE_646K, OLD_SHADER_LINE_646L, OLD_SHADER_LINE_655,
                OLD_SHADER_LINE_658):
        text = text.replace('\n' + old, '')
    if SHADER_ANCHOR not in text:
        raise ValueError('shadows: %s has no "%s" to insert after'
                         % (path, SHADER_ANCHOR.strip()))
    text = text.replace(SHADER_ANCHOR, SHADER_ANCHOR + '\n' + SHADER_LINE, 1)
    _write(path, text)
    log('  model shadows: lmain_vv keeps %s of the floor in battle (mode 5), %s on '
        'the field (mode 6), marked once' % (SHADOW_KEEP, FIELD_KEEP))
    return True


def patch_fragment(path, log=lambda *_: None):
    """Add the shadow branch to a colortex_p.glsl (idempotent)."""
    text = open(path).read()
    if FP_MARK in text:
        return False
    if FP_ANCHOR not in text:
        raise ValueError('shadows: %s has no "%s" to insert after'
                         % (path, FP_ANCHOR.strip()))
    text = text.replace(FP_ANCHOR, FP_ANCHOR + '\n' + FP_LINE, 1)
    _write(path, text)
    log('  model shadows: colortex_p draws shadows flat (texture alpha ignored)')
    return True


def _write(path, text):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        f.write(text)
    os.replace(tmp, path)
