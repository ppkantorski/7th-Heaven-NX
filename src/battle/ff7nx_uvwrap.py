#!/usr/bin/env python3
"""ff7nx_uvwrap.py -- BUILD 749: the light lines and "abrupt" panels in
Knights of the Round (and every other scrolling-UV battle effect).

WHAT THE ENGINE DOES
====================
A battle effect whose material has a UV scroll (`field_8`, struc_173
scroll_uv = 2) goes through x86 0x68D07E (ARM +0xB84CB0) once per draw. For
EVERY VERTEX it writes

    u = u_src + u_offset;   if (u >= 1.0) u -= 1.0;
    v = v_src + v_offset;   if (v >= 1.0) v -= 1.0;

The wrap is per vertex. A triangle whose corners fall on both sides of 1.0
gets, say, v = 0.97 at one corner and v = 0.02 at the next, and the GPU
interpolates BACKWARDS across the whole texture inside that one triangle: the
complete picture squeezed and mirrored into a thin strip. On the Switch's
linear-filtered, mip-mapped HD textures that strip reads as a flat light
band (the averaged texture), one mesh row tall, and it jumps from row to row
as the scroll moves -- the line through the KOTR curtain (BG_2 on
CYLINDER.RSD, V scroll) and the vertical line in the aurora (TUBE_1). When
the straddling triangles are a whole face of the cylinder you are standing
inside, it is a whole flat-shaded rectangle for a frame: the "abrupt" frames.

WHY REMOVING THE WRAP IS SAFE
=============================
The wrap only exists to keep the coordinate inside 0..1. The sampler does
that by itself when the texture repeats, and every effect texture here does:
  * the port creates every texture with GL_TEXTURE_WRAP_S/T = GL_REPEAT
    (0x2901, native +0x11395D8);
  * the only thing that ever changes it is the game's render-state path
    (x86 0x6B4F37 -> 0x6A74B6), and only for a .p group whose hundreds
    `features` word carries V_WRAP_U/V_WRAP_V. Not one magic.lgp model in
    the archive does (5,383 groups checked).
With REPEAT, u_src + offset in 0..2 samples exactly the picture the wrapped
value would have -- the same texels, continuously, with no backwards strip.

THE PATCH: two words, in place, no cave.
    +0xB85158  cbnz w8, +0xB851F4   ->  b +0xB851F4    (skip u -= 1.0)
    +0xB853AC  cbnz w8, +0xB84EC0   ->  b +0xB84EC0    (skip v -= 1.0)
Each branch is the translation of the x86 `jne` taken when the value is
below 1.0; the x87 compare before it has already popped, so taking it always
leaves the emulated FPU stack exactly as the stock "below 1.0" path does.

SEVENTH_NX_UV_WRAP=stock leaves the engine's per-vertex wrap.

BUILD 749e -- NEAR-PLANE CLIPPING for the same effects
======================================================
Offset effect geometry (struc_173 add_offsets / field_7: KOTR's curtain and
tube, and every effect drawn through palette_extra offsets) is transformed
on the CPU by x86 0x6B27A9 (ARM +0xC3E330): one loop per vertex, six
copies of it for its three depth modes x offsets on/off. A vertex whose w
is <= 0 -- beside or behind the camera -- is written as the marker
(-1, -1, -1, rhw -1) instead of being projected, so its triangle comes out
as a wrong shape or a hole. On 4:3 those faces were off screen; at 16:9 a
curtain wall face beside the camera shows as a flat block at the frame's
side (hardware, kotr_6 frame 907).

The port draws these pre-transformed vertices with tlmain_vv, which rebuilds
the homogeneous position (xyz * w, w = 1/rhw) before the projection. A
vertex projected normally with w < 0 therefore reaches the GPU as the exact
clip-space point it is, and the GPU clips the triangle at the near plane
like any other 3D draw. So the fix is to project w < 0 normally and keep
the marker only for w == 0 (where 1/w does not exist): each loop's x87
"jump if C0 or C3" mask register (mov wN, #0x4100) becomes C3 only (#0x4000).
Six words, in place. SEVENTH_NX_FX_NEARCLIP=stock reverts.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import a64 as A
import ff7nx_audio_cave as AC

ENV = 'SEVENTH_NX_UV_WRAP'

FUNC = 0xB84CB0                       # x86 0x68D07E
SITES = (
    # (ARM word, stock word, target, what)
    (0xB85158, 0x350004E8, 0xB851F4, 'u: skip the per-vertex u -= 1.0'),
    (0xB853AC, 0x35FFD8A8, 0xB84EC0, 'v: skip the per-vertex v -= 1.0'),
)


NEARCLIP_ENV = 'SEVENTH_NX_FX_NEARCLIP'
NEARCLIP_SITES = (
    # (ARM word, stock `mov wN, #0x4100`, patched `mov wN, #0x4000`)
    (0xC3E824, 0x52882017, 0x52880017),
    (0xC3F004, 0x52882017, 0x52880017),
    (0xC3F7DC, 0x52882017, 0x52880017),
    (0xC3FEF8, 0x52882018, 0x52880018),
    (0xC40480, 0x52882018, 0x52880018),
    (0xC40A30, 0x52882018, 0x52880018),
)


def nearclip_enabled():
    return os.environ.get(NEARCLIP_ENV, '').strip().lower() not in (
        'stock', '0', 'off', 'false', 'no')


def patch_nearclip(text):
    done = 0
    for at, stock, want in NEARCLIP_SITES:
        got, = struct.unpack_from('<I', text, at)
        if got == want:
            continue
        if got != stock:
            raise ValueError('near clip: +0x%X holds %08X, expected %08X'
                             % (at, got, stock))
        struct.pack_into('<I', text, at, want)
        done += 1
    return done


def enabled():
    return os.environ.get(ENV, '').strip().lower() not in (
        'stock', '0', 'off', 'false', 'no')


def patch_text(text):
    """Patch a writable .text in place. Idempotent; raises on a foreign
    word."""
    done = 0
    for at, stock, target, what in SITES:
        want = A.b(at, target)
        got, = struct.unpack_from('<I', text, at)
        if got == want:
            continue
        if got != stock:
            raise ValueError('+0x%X holds %08X, expected %08X (%s)'
                             % (at, got, stock, what))
        struct.pack_into('<I', text, at, want)
        done += 1
    return done


def apply_to_nso(src, dest, log=lambda *_: None):
    blob = open(src, 'rb').read()
    _segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    if enabled():
        patch_text(text)
    if nearclip_enabled():
        patch_nearclip(text)
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, 0)
    _s2, raw2 = AC.segments(out)
    for at, _stock, target, _what in (SITES if enabled() else ()):
        if struct.unpack_from('<I', raw2[0], at)[0] != A.b(at, target):
            raise ValueError('+0x%X did not survive the repack' % at)
    for at, _stock, want in (NEARCLIP_SITES if nearclip_enabled() else ()):
        if struct.unpack_from('<I', raw2[0], at)[0] != want:
            raise ValueError('+0x%X did not survive the repack' % at)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(out)
    if nearclip_enabled():
        log('  offset effects (x86 0x6B27A9): a vertex behind the camera is '
            'projected and GPU-clipped instead of written as a -1 marker '
            '(6 words; %s=stock reverts)' % NEARCLIP_ENV)
    if not enabled():
        return True
    log('  scrolling-UV effects: the per-vertex wrap at x86 0x68D07E is off '
        '(+0x%X, +0x%X); the texture repeat does it without the backwards '
        'strip (KOTR curtain/aurora lines). %s=stock reverts'
        % (SITES[0][0], SITES[1][0], ENV))
    return True
