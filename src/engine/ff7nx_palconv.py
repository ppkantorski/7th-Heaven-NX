#!/usr/bin/env python3
"""
ff7nx_palconv.py -- BUILD 797. The gfx driver's paletted texture
conversion (+0x10D77D0) with the palette translated once per texture.

THE COST (frame probe v10, hardware, uutai1): Wutai's field script pulses
the brightness of four palettes every two frames (STPAL / ADPAL / LDPAL in
a loop), so the driver rebuilds about 1.8 textures a frame. load_texture
(+0x10D6BC0) takes 2.4-2.8 ms of CPU a frame there, most of it the
conversion: for each of a 256x256 page's 65,536 pixels the stock loop
reloads the palette's guest address and calls the translator (+0x10FC3A0)
on it, then reads one entry at that host base.

WHAT THIS DOES
==============
native/palconv.c is that loop with the translation hoisted: one call for
the palette base, the same entry for every index, the same colour key,
alpha substitution (0xFE), red/blue swap, early stop on an index past the
palette count, and nothing written for the cases stock writes nothing.
Stock translates only the base too and indexes from it, so the reads are
the same host words. Any other format (not paletted, or no palette) goes
to the stock function unchanged.

load_texture's one call (+0x10D6FC4, `bl +0x10D77D0`) goes to a stub that
saves x0..x7 and the stack argument in its frame and calls
pc_convert(args, translator, stock). The code lives in the ARM body of
x86 0x62A0E7 (write_field_background_data), which ff7nx_fieldbg proves
dead in the module on every build and which is unreferenced in ff7_en;
its first 0x40 and last 0x100 bytes are left alone (the liveness proof
checks the entry; the padding allocator takes tail words of dead bodies).

WHAT IT DOES NOT DO
===================
The GPU side of a rebuild (the upload, and the background scaler's 4x
filtered copy that 0x10DBCC0 renders for every new texture) is unchanged.
Keeping finished textures for reuse would skip it, but each one carries
that 4x copy (a 256x256 page is 4.25 MB of graphics memory), and Wutai's
loop cycles through about 52 of them.

tests/test_palconv.py runs the stock function and this one on the same
random inputs under the Unicorn emulator and compares the output buffers
and the registers the caller relies on. SEVENTH_NX_PAL_CONV=0 keeps the
stock conversion.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import nxpaths  # noqa: F401  (project paths)
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import nxmap

VERSION = 797
ENV = 'SEVENTH_NX_PAL_CONV'
HERE = nxpaths.ROOT
BLOB = os.path.join(nxpaths.APP, 'native', 'palconv.bin')
ENTRY = os.path.join(nxpaths.APP, 'native', 'palconv.entry')

CONVERT = 0x10D77D0                  # the driver's pixel conversion
CALL = 0x10D6FC4                     # load_texture's call of it
GUEST_TO_HOST = 0x10FC3A0
CONVERT_END = 0x10D7B34              # past its ret
CONVERT_FIRST = 0xD101C3FF           # sub sp, sp, #0x70
DEAD_X86 = 0x62A0E7                  # write_field_background_data
ENTRY_GUARD = 0x40
TAIL_GUARD = 0x100
FRAME = 0x60                         # x0..x7, the stack argument, x29/x30


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def entries():
    out = {}
    with open(ENTRY) as f:
        for line in f:
            name, off = line.split()
            out[name] = int(off, 16)
    return out


def _addr(w, reg, at, value):
    w.append(A.adrp(reg, at + 4 * len(w), value & ~0xFFF))
    w.append(A.add_imm64(reg, reg, value & 0xFFF))


def stub(at, fn):
    """bl here instead of the conversion: its eight register arguments and
    the one on the caller's stack into this frame, then
    pc_convert(frame, translator, stock)."""
    w = [A.sub_imm64(31, 31, FRAME), A.stp64_off(29, 30, 31, 0x50)]
    for r in range(0, 8, 2):
        w.append(A.stp64_off(r, r + 1, 31, 8 * r))
    w += [A.ldr(9, 31, FRAME), A.str64(9, 31, 0x40), A.add_imm64(0, 31, 0)]
    _addr(w, 1, at, GUEST_TO_HOST)
    _addr(w, 2, at, CONVERT)
    w.append(A.bl(at + 4 * len(w), fn))
    w += [A.ldp64_off(29, 30, 31, 0x50), A.add_imm64(31, 31, FRAME), A.ret()]
    return w


def check(text):
    got = struct.unpack_from('<I', text, CALL)[0]
    if got != A.bl(CALL, CONVERT):
        raise ValueError('pal conv: +0x%X holds %08X, not bl +0x%X (already '
                         'hooked, or a different module)' % (CALL, got, CONVERT))
    got = struct.unpack_from('<I', text, CONVERT)[0]
    if got != CONVERT_FIRST:
        raise ValueError('pal conv: +0x%X holds %08X, not the stock conversion'
                         % (CONVERT, got))


def dead_region(src, exe_bytes):
    import ff7nx_fieldbg
    import ff7nx_fxinterp
    m = nxmap.Main(src)
    why = ff7nx_fieldbg._liveness(m, DEAD_X86)
    if not why and exe_bytes is not None:
        why = ff7nx_fxinterp._x86_unreferenced(exe_bytes, DEAD_X86)
    if why:
        raise ValueError('x86 0x%X is not dead: %s' % (DEAD_X86, why))
    lo, hi = m.extent(DEAD_X86)
    return lo + ENTRY_GUARD, hi - TAIL_GUARD


def patch_text(text, lo, hi, code, ent):
    """Write blob + stub into [lo, hi) and redirect the call. Returns
    (code address, stub address, end)."""
    entry = (lo + 15) & ~15
    at = (entry + len(code) + 15) & ~15
    words = stub(at, entry + ent['pc_convert'])
    end = at + 4 * len(words)
    if end > hi:
        raise ValueError('pal conv: %d bytes do not fit %d' % (end - lo, hi - lo))
    text[entry:entry + len(code)] = code
    for va in range(entry + len(code), at, 4):
        struct.pack_into('<I', text, va, A.nop())
    for i, x in enumerate(words):
        struct.pack_into('<I', text, at + 4 * i, x)
    struct.pack_into('<I', text, CALL, A.bl(CALL, at))
    return entry, at, end


def apply_to_nso(src, dest, stock=None, exe=None, log=lambda *_: None):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    check(text)
    exe_bytes = open(exe, 'rb').read() if exe else None
    lo, hi = dead_region(src, exe_bytes)
    if stock is not None:
        st = nxmap.Main(stock).text
        if st[lo:hi] != bytes(text[lo:hi]):
            raise ValueError('the dead body at +0x%X was already written' % lo)
        if st[CONVERT:CONVERT_END] != bytes(text[CONVERT:CONVERT_END]):
            raise ValueError('pal conv: the conversion at +0x%X is not stock '
                             '(patched by an earlier step)' % CONVERT)
    code = open(BLOB, 'rb').read()
    if len(code) % 4 or any(struct.unpack_from('<I', code, k)[0] == 0
                            for k in range(0, len(code), 4)):
        raise ValueError('pal conv: the blob holds a zero word (the padding '
                         'allocator would take it)')
    entry, at, end = patch_text(text, lo, hi, code, entries())
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, 0)
    _s2, raw2 = AC.segments(out)
    if struct.unpack_from('<I', raw2[0], CALL)[0] != A.bl(CALL, at):
        raise ValueError('pal conv: the hook did not survive the repack')
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(out)
    log('  paletted texture conversion: native, palette translated once per '
        'texture; code +0x%X..+0x%X in dead x86 0x%X (%d of %d bytes)'
        % (entry, end, DEAD_X86, end - lo, hi - lo))
    return {'code': entry, 'stub': at, 'end': end}
