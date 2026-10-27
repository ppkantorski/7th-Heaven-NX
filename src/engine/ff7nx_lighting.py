#!/usr/bin/env python3
"""
ff7nx_lighting.py -- BUILD 675. Model lighting as FFNx draws it by default
(game_lighting = 1, "Per-Vertex ... closely match the look of the PSX
version"), in place of the PC version's CPU lighting.

THE REPORT (hardware, 10-14): on del3's beach Cloud is darker than everyone
else and dark patches "shimmer" over his body and hair as he walks; at night
too. FFNx on PC does not look like this.

WHY (x86, checked; see native/lighting.c)
=========================================
The game lights every model part on the CPU, per vertex, x86 0x68E92D (the
gfx driver's light_polygon_set; ARM +0xB4AF90). For a field model's light
(flags 9: mode 1, directional) each vertex goes to 0x68DD1E (ARM: a thunk to
the port's native +0x10F23C0); with a scripted light colour (flag 4) to
0x68DE75, which uses 0x66CFE8 (ARM +0x7E40). Per channel:

    PC:    I = max(amb, amb + sum_i C[ch][i] (n . L_i))   -- signed, summed
    FFNx:  I = amb + sum_i C[ch][i] saturate(n . L_i)^2   -- each light clamped

The PC sum lets a light BEHIND a surface subtract from the lights in front.
del3's first two lights point almost opposite ways, so half of every model's
vertices sit on the flat ambient floor (0.52) with a hard crease where the
sum crosses it; on a walking model the crease sweeps over the limbs. Measured
on Cloud's mesh with del3's lights (_scratch/671/litstats.py): PC 48-52% of
vertices on the floor, mean 0.70; FFNx 5-6%, mean 0.85. Standing NPCs show
the same crease, but it does not move.

WHAT THIS DOES
==============
* 0x69C69F's call of 0x69C3D7 (ARM +0xB5D5D4): first lt_setup -- keep this
  part's light directions in its own space, DM = D x bone^T (D = light
  +0x38, exactly as the game builds M = (C x D) x bone^T at +0xF8), and the
  colours C (+0x78). Only for a mode-1 light.
* 0x68DD1E's call of the native vertex routine (+0xB8E0B4): lt_vtx, the same
  arguments and result with FFNx's intensity.
* 0x68DE75's / 0x68DBF4's call of 0x66CFE8 (+0xB8E1F0, +0xB8D214): the call,
  then lt_mvfix puts FFNx's intensity in its result (the caller then applies
  the scripted colour as stock).
Any other matrix (another light mode, a call not set up) takes the stock
path.

675b (hardware: "looks about the same"): 675 kept only the LAST part set up
and missed the fourth vertex variant, 0x68DAE1 (a mode-1 light without flag
8; its 0x66CFE8 call +0xB8C404). The field lights a model through its own
routines (0x64CA65 / 0x64C382 via 0x685028), with any of the four. Each
light now carries its own record in its ramp (+0x1A4; read only by a ramp
light, +0x17C == 2, which is never written and whose ramp is rebuilt whole
when a light becomes one), and all four variants are covered. Cost: the same vertex loop, three clamps and squares more per vertex.

675c (hardware probe 675p: del3's Cloud alone fell back -- his light has a
ramp sub-mode, +0x17C 2/3, set by the field script, and 675b would not write
a record into a ramp): the records live in our own table, 128 slots keyed by
the light's +0xF8 address. No light is refused; nothing in a light is
written.

675d (probe 675c: del3's Cloud still green): the field's scripted light
(x86 0x64A1E9..) sets the model's light up through 0x69C69F BEFORE it makes
it mode 1 (0x64A281 flags |= 1), then lights it (0x685028 -> 0x64CA65).
lt_setup refused a light that was not mode 1 yet; it now keeps a record for
every light and the mode is tested where the record is used.

679 (hardware: shadows off -- the pulse stays; the light flat -- gone;
"the same pattern with each leg"): del3's KAWAI sub-op 9 (x86 0x64A541)
draws the model each frame with its light out of mode 1 (each part's light
set up from its own bone, 0x69C69F, then 0x68E92D, which lights nothing),
then makes the light mode 1 and relights every part (0x685028 -> 0x64CA65 /
0x64C382) with the light's +0xF8 matrix AS IT STANDS -- the last part's
bone. Face and hair were lit as if turned like the last limb drawn. PC
FFNx never shows it (its shader lights each part at draw time with that
part's matrices and ignores the CPU colours). Now: 0x68E92D's entry binds
the fresh setup to its polygon set (a 1,024-slot part table), and the
relight routines' entries make the set's own record the cache, so each
part is relit with its own bone (FFNx's intensity, as 675d).

Code: 'light' dead-space part (2 KB; one .text.lt section, entries from the
symbols; stubs packed). State: Lt header 96 B + 128 records
x 80 B = 10,336 B BSS.
SEVENTH_NX_LIGHTING=pc keeps the PC lighting (writes nothing).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import nxpaths  # BUILD 755: project paths (src/nxpaths.py)
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_deadspace as DS

VERSION = 6790   # 679: each part relit with its own bone
ENV = 'SEVENTH_NX_LIGHTING'
HERE = nxpaths.ROOT
BLOB = os.path.join(nxpaths.APP, 'native', 'lighting.bin')
ENTRY = os.path.join(nxpaths.APP, 'native', 'lighting.entry')

X86_CTX_GOT, X86_ESP, GUEST_TO_HOST = 0x12CE2B0, 0x10, 0x10FC3A0
SETUP_CALL, SETUP_TARGET = 0xB5D5D4, 0xBC7280      # x86 0x69C69F -> 0x69C3D7
VTX_CALL, VTX_TARGET = 0xB8E0B4, 0x10F23C0         # x86 0x68DD1E -> native vertex
MV_CALLS, MV_TARGET = (0xB8E1F0, 0xB8D214, 0xB8C404), 0x7E40  # 0x68DE75 / 0x68DBF4 / 0x68DAE1 -> 0x66CFE8
STATE_BYTES = 96 + 128 * 80 + 1024 * 44   # 675c: Lt header + per-light records; 679: + per-part records
# 679: entries hooked (first word displaced): x86 0x68E92D light_polygon_set
# (kind 0: bind the fresh setup to the set), 0x64CA65 / 0x64C382 the field's
# relight (kind 1: the set's own record)
ENTRY_SITES = ((0xB4AF90, 0, 'light_polygon_set 0x68E92D'),
               (0xA2BEE0, 1, 'field relight 0x64CA65'),
               (0xA29B20, 1, 'field relight 0x64C382'))
ENTRY_WORD = 0xF81D0FF5                            # str x21, [sp, #-0x30]!
SITES = ([(SETUP_CALL, SETUP_TARGET, 'light setup (0x69C3D7)'),
          (VTX_CALL, VTX_TARGET, 'per-vertex light (native)')]
         + [(s, MV_TARGET, 'light matrix x normal (0x66CFE8)') for s in MV_CALLS])


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, 'ffnx').strip().lower() not in ('pc', '0', 'off', 'no', 'false')


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


def setup_stub(at, state, fn):
    """bl here instead of 0x69C69F's call of 0x69C3D7 (x86 convention: the
    return slot at guest esp, the arguments above it): lt_setup(state, esp,
    guest->host), then the call itself (a tail branch: x30 is the site's)."""
    w = [A.stp64_pre(29, 30, 31, -0x10)]
    _got(w, 9, at, X86_CTX_GOT)
    w.append(A.ldr(1, 9, X86_ESP))
    _addr(w, 0, at, state)
    _addr(w, 2, at, GUEST_TO_HOST)
    w.append(A.bl(at + 4 * len(w), fn['lt_setup']))
    w.append(A.ldp64_post(29, 30, 31, 0x10))
    w.append(A.b(at + 4 * len(w), SETUP_TARGET))
    return w


def vtx_stub(at, state, fn):
    """bl here instead of the native vertex routine (w0..w4 its arguments):
    lt_vtx(w0..w4, state, guest->host, the routine)."""
    w = [A.stp64_pre(29, 30, 31, -0x10)]
    _addr(w, 5, at, state)
    _addr(w, 6, at, GUEST_TO_HOST)
    _addr(w, 7, at, VTX_TARGET)
    w.append(A.bl(at + 4 * len(w), fn['lt_vtx']))
    w += [A.ldp64_post(29, 30, 31, 0x10), A.ret()]
    return w


def mv_stub(at, state, fn):
    """bl here instead of a call of +0x7E40 (0x66CFE8, x86 convention): note
    the guest esp, make the call (it pops the return slot), then
    lt_mvfix(state, esp, guest->host). x19 is kept."""
    w = [A.stp64_pre(29, 30, 31, -0x20), A.str64(19, 31, 0x10)]
    _got(w, 9, at, X86_CTX_GOT)
    w.append(A.ldr(19, 9, X86_ESP))
    w.append(A.bl(at + 4 * len(w), MV_TARGET))
    _addr(w, 0, at, state)
    w.append(A.mov_reg(1, 19))
    _addr(w, 2, at, GUEST_TO_HOST)
    w.append(A.bl(at + 4 * len(w), fn['lt_mvfix']))
    w += [A.ldr64(19, 31, 0x10), A.ldp64_post(29, 30, 31, 0x20), A.ret()]
    return w


def entry_common(at, state, fn):
    """679: lt_entry(state, esp, guest->host, w3 = kind), a tail call (x30 is
    the entry stub's)."""
    w = []
    _got(w, 9, at, X86_CTX_GOT)
    w.append(A.ldr(1, 9, X86_ESP))
    _addr(w, 0, at, state)
    _addr(w, 2, at, GUEST_TO_HOST)
    w.append(A.b(at + 4 * len(w), fn['lt_entry']))
    return w


def entry_stub(at, site, kind, common):
    """679: b here from a function's first word: lt_entry at its entry (the
    guest esp is the return slot, the polygon set and light above it), then
    the displaced word and back."""
    w = [A.stp64_pre(29, 30, 31, -0x10), A.movz(3, kind)]
    w.append(A.bl(at + 4 * len(w), common))
    w += [A.ldp64_post(29, 30, 31, 0x10), ENTRY_WORD]
    w.append(A.b(at + 4 * len(w), site + 4))
    return w


def check(text):
    for site, kind, what in ENTRY_SITES:
        got = struct.unpack_from('<I', text, site)[0]
        if got != ENTRY_WORD:
            raise ValueError('lighting: +0x%X (%s) is %08X, not %08X'
                             % (site, what, got, ENTRY_WORD))
    for site, target, what in SITES:
        got = struct.unpack_from('<I', text, site)[0]
        if got != A.bl(site, target):
            raise ValueError('lighting: +0x%X (%s) is %08X, not bl +0x%X'
                             % (site, what, got, target))


def apply_to_nso(src, dest, stock=None, log=lambda *_: None, region=None):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    check(text)
    lo, hi = region if region else DS.part(src, 'light', stock)
    code = open(BLOB, 'rb').read()
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = (state - base) + STATE_BYTES + AC.bss_tail_slack(segs)
    entry = (lo + 15) & ~15
    fn = {k: entry + v for k, v in entries().items()}
    at = (entry + len(code) + 15) & ~15
    stubs, words = {}, {}

    def put(name, ws):
        nonlocal at
        stubs[name] = at
        for i, x in enumerate(ws):
            words[at + 4 * i] = x
        at = at + 4 * len(ws)            # 679: packed, word-aligned

    put('setup', setup_stub(at, state, fn))
    put('vtx', vtx_stub(at, state, fn))
    put('mv', mv_stub(at, state, fn))
    put('ecommon', entry_common(at, state, fn))
    for site, kind, what in ENTRY_SITES:
        put('entry%X' % site, entry_stub(at, site, kind, stubs['ecommon']))
    if at > hi:
        raise ValueError('lighting: %d bytes do not fit the %d-byte part'
                         % (at - lo, hi - lo))
    text[entry:entry + len(code)] = code
    for va, x in words.items():
        struct.pack_into('<I', text, va, x)
    struct.pack_into('<I', text, SETUP_CALL, A.bl(SETUP_CALL, stubs['setup']))
    struct.pack_into('<I', text, VTX_CALL, A.bl(VTX_CALL, stubs['vtx']))
    for s in MV_CALLS:
        struct.pack_into('<I', text, s, A.bl(s, stubs['mv']))
    for site, kind, what in ENTRY_SITES:
        struct.pack_into('<I', text, site, A.b(site, stubs['entry%X' % site]))
    raw[0] = bytes(text)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(AC.pack(blob, raw, growth))
    log('  model lighting: FFNx per-vertex (each light clamped, squared, plus the '
        'ambient); code +0x%X (%d bytes of the light part), state +0x%X'
        % (entry, at - lo, state))
    return {'code': entry, 'bytes': at - lo, 'stubs': stubs, 'state': state,
            'bss': growth, 'entries': fn}
