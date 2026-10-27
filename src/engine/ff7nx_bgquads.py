#!/usr/bin/env python3
"""
ff7nx_bgquads.py -- BUILD 792. The field background's per-tile quad
builder (x86 0x6465FB, ARM +0xA095B0) in native code.

THE COST (frame probe v8, hardware, shadows on): the background
(0x640F22) is 1.3 ms a frame in fship_2 and 1.7 ms in mrkt2; 0x6465FB is
its largest part (0.51 / 0.72 ms). For each of the 42 layers it writes four
vertices per tile into the layer's vertex buffer: every one of the ~290 x86
instructions a tile takes is a guest-memory access through the translator.

WHAT THIS DOES
==============
native/bgquads.c is the function in C: the same writes of guest memory,
its frame and locals, its two calls (0x676578, 0x66E272) through their
translated bodies with the same arguments, the timer tick at its return,
eax/ecx/edx, the flag bytes and the x87 slot as the translation leaves
them, and the floats as the translation computes x87 (widened to double,
summed in double, one rounding at the fstp). The function's first word
(+0xA095B0) branches to a stub that passes the context, the guest page
table, the x87 state and the callees, then tail-calls the C code. The code
lives in the ARM body of x86 0x621835, proven dead in the module and
unreferenced in ff7_en on every build (its first 0x40 and last 0x100 bytes
are left alone: the liveness proof checks the entry, and the padding
allocator takes tail words of dead bodies).

One difference from the x86's order, the same as the translation's own
freedom: a tile's six sprite words, the layer's sixteen offsets at
0xD00010, ebp-4/-8 and the vertex colour are read once (per tile, per
layer) rather than again before each store. They can change only if a
layer's vertex buffer overlapped them.

BUILD 793: the depth-1 and depth-2 tile passes, x86 0x64085C and 0x640C49
(0.72 / 0.42 ms in uutai1, 0.61 / 0.37 in mrkt2), with add_page_tile
(0x6464BA) inlined: each tile's index, the four edge tests against the
view, its flags, and the visible ones appended to their page. As the
translation runs them, which in 0x64085C's +0x103C path is not the PC
exe's code: the port adds a page shift from +0x1054 (>= 2: +14, >= 3:
+4 more). native/bgquads.c says what is read once. SEVENTH_NX_BG_PICK=0
keeps those two translated.

BUILD 795: with widescreen on, ff7nx_uncrop (the y origin) and
ff7nx_wsclamp (the left/top immediates, and caves that bias the camera
for the right/bottom tests) have already edited those two translations,
so BUILD 793 refused them and left all three translated. ws_params() now
reads those five sites back from the text -- the stock word, or exactly
the shape those passes write -- and the stub hands the values to the C.
Any other change to the two functions is still refused.

tests/test_bgquads.py runs the stock translated function and this one on
the same random state under the Unicorn emulator (the real translator page
table walk, the real 0x676578) and compares every guest page, the context,
the x87 state, the calls and the ticks. SEVENTH_NX_BG_QUADS=0 keeps the
translated function.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import nxpaths  # BUILD 755: project paths (src/nxpaths.py)
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import nxmap

VERSION = 795
ENV = 'SEVENTH_NX_BG_QUADS'
HERE = nxpaths.ROOT
BLOB = os.path.join(nxpaths.APP, 'native', 'bgquads.bin')
ENTRY = os.path.join(nxpaths.APP, 'native', 'bgquads.entry')

X86 = 0x6465FB
ARM = 0xA095B0
FIRST_WORD = 0xD102C3FF              # sub sp, sp, #0xb0
GET_STATE = 0xA87FB0                 # x86 0x676578
ALLOC = 0xABD250                     # x86 0x66E272
ADD_TILE = 0xA06870                  # x86 0x6464BA, add_page_tile
TICK = 0xA550
CALLS = ((0xA09600, GET_STATE), (0xA09A30, ALLOC))   # its only two calls
CTX_GOT = 0x12CE2B0
PAGES_GOT = 0x12CE8C0
DUMMY_GOT = 0x12CE8D8
X87_GOT = 0x12CE2A8

# BUILD 793: the depth-1 and depth-2 tile passes, add_page_tile inlined.
# SEVENTH_NX_BG_PICK=0 keeps their translations (the quad builder stays).
PICK_ENV = 'SEVENTH_NX_BG_PICK'
# (x86, ARM entry, its first word, C entry, its calls, what the stub passes
# after ctx/pages/fallback/x87)
HOOKS = (
    (X86, ARM, FIRST_WORD, 'bg_6465fb', CALLS, (GET_STATE, ALLOC, TICK)),
    (0x64085C, 0xA059D0, 0xFC190FE8, 'bg_64085c', ((0xA067EC, ADD_TILE),), (TICK,)),
    (0x640C49, 0xA06DE0, 0xFC190FE8, 'bg_640c49', ((0xA0769C, ADD_TILE),), (TICK,)),
)

# BUILD 795: the tile window's five sites in each pass, as the widescreen
# passes may leave them: (origin y, top, left, bottom, right)
WS_SITES = {
    0x64085C: (0xA05AA4, 0xA05CF4, 0xA05E00, 0xA05D84, 0xA05E8C),
    0x640C49: (0xA06EA8, 0xA07138, 0xA07244, 0xA071C8, 0xA072D0),
}
WS_STOCK = (0xE0, 0x100, 0x150, 0, 0)
ORR_W9_E0 = 0x321B0BE9               # orr w9, wzr, #0xe0
SUB_W10_W9_W8 = 0x4B08012A


def _b_target(word, at):
    if (word & 0xFC000000) != 0x14000000:
        return None
    off = word & 0x3FFFFFF
    if off & 0x2000000:
        off -= 0x4000000
    return at + 4 * off


def _cave_bias(text, site):
    """The camera bias a wsclamp cave at `site` adds: `b cave` there, and
    the cave `add w8, w8, #v; sub w10, w9, w8; b ...` back to site + 4
    (through at most three plain branches). 0 for the stock word."""
    word = struct.unpack_from('<I', text, site)[0]
    if word == SUB_W10_W9_W8:
        return 0
    cave = _b_target(word, site)
    if cave is None or not 0 <= cave <= len(text) - 12:
        raise ValueError('bg quads: +0x%X is neither stock nor a wsclamp cave' % site)
    add, sub, at = (struct.unpack_from('<I', text, cave)[0],
                    struct.unpack_from('<I', text, cave + 4)[0], cave + 8)
    if (add & 0xFFC003FF) != 0x11000108 or sub != SUB_W10_W9_W8:
        raise ValueError('bg quads: the cave at +0x%X for +0x%X is not '
                         '`add w8, w8, #v; sub w10, w9, w8`' % (cave, site))
    for _ in range(4):
        tgt = _b_target(struct.unpack_from('<I', text, at)[0], at)
        if tgt is None or not 0 <= tgt <= len(text) - 4:
            break
        if tgt == site + 4:
            return (add >> 10) & 0xFFF
        at = tgt
    raise ValueError('bg quads: the cave for +0x%X does not return to +0x%X'
                     % (site, site + 4))


def ws_params(text, x86):
    """(origin y, top, left, right, bottom) of pass `x86` as the text holds
    them, or ValueError for anything but the stock words and the shapes
    ff7nx_uncrop / ff7nx_wsclamp write."""
    so, st, sl, sb, sr = WS_SITES[x86]
    word = struct.unpack_from('<I', text, so)[0]
    if word == ORR_W9_E0:
        oy = 0xE0
    elif (word & 0xFFE0001F) == 0x52800009:          # movz w9, #imm
        oy = (word >> 5) & 0xFFFF
    else:
        raise ValueError('bg quads: +0x%X (the y origin) is %08X' % (so, word))
    lims = []
    for site in (st, sl):
        word = struct.unpack_from('<I', text, site)[0]
        if (word & 0xFFC003FF) != 0x51000109:         # sub w9, w8, #imm
            raise ValueError('bg quads: +0x%X is %08X, not sub w9, w8, #imm'
                             % (site, word))
        lims.append((word >> 10) & 0xFFF)
    out = (oy, lims[0], lims[1], _cave_bias(text, sr), _cave_bias(text, sb))
    if any(not 0 <= v < 0x1000 for v in out):
        raise ValueError('bg quads: a tile window value of 0x%X is out of '
                         'range %r' % (x86, out))
    return out


def ws_word(params):
    oy, top, left, right, bottom = params
    return oy | top << 12 | left << 24 | right << 36 | bottom << 48


DEAD_X86 = 0x621835
ENTRY_GUARD = 0x40
TAIL_GUARD = 0x100


def _on(env, name):
    return env.get(name, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def enabled(env=None):
    return _on(os.environ if env is None else env, ENV)


def hooks(env=None):
    """The HOOKS rows this build installs."""
    env = os.environ if env is None else env
    if not _on(env, ENV):
        return ()
    return tuple(h for h in HOOKS if h[0] == X86 or _on(env, PICK_ENV))


def entries():
    out = {}
    with open(ENTRY) as f:
        for line in f:
            name, off = line.split()
            out[name] = int(off, 16)
    return out


def _bl_target(word, at):
    if (word & 0xFC000000) != 0x94000000:
        return None
    off = word & 0x3FFFFFF
    if off & 0x2000000:
        off -= 0x4000000
    return at + 4 * off


def check(m, text, rows=HOOKS):
    for x86, arm, first, _name, calls, _args in rows:
        if m.x86_to_arm.get(x86) != arm:
            raise ValueError('bg quads: x86 0x%X does not map to +0x%X' % (x86, arm))
        got = struct.unpack_from('<I', text, arm)[0]
        if got != first:
            raise ValueError('bg quads: +0x%X holds %08X, not %08X (already hooked, '
                             'or a different module)' % (arm, got, first))
        for site, tgt in calls:
            if _bl_target(struct.unpack_from('<I', text, site)[0], site) != tgt:
                raise ValueError('bg quads: +0x%X is not its call of +0x%X' % (site, tgt))


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


def _addr(w, reg, at, value):
    w.append(A.adrp(reg, at + 4 * len(w), value & ~0xFFF))
    w.append(A.add_imm64(reg, reg, value & 0xFFF))


def _got(w, reg, at, got):
    w.append(A.adrp(reg, at + 4 * len(w), got & ~0xFFF))
    w.append(A.ldr64(reg, reg, got & 0xFFF))


def _const(w, reg, value):
    w.append(0xD2800000 | (value & 0xFFFF) << 5 | reg)          # movz x
    for hw in range(1, 4):
        part = (value >> (16 * hw)) & 0xFFFF
        if part:
            w.append(0xF2800000 | hw << 21 | part << 5 | reg)   # movk x


def stub(at, fn, args=(GET_STATE, ALLOC, TICK), consts=()):
    """b here from the hooked entry: fn(ctx, &pages, &fallback page, x87,
    *args, *consts) as a tail call (x30 is the caller's)."""
    w = []
    _got(w, 0, at, CTX_GOT)
    _addr(w, 1, at, PAGES_GOT)
    _addr(w, 2, at, DUMMY_GOT)
    _got(w, 3, at, X87_GOT)
    for r, a in enumerate(args):
        _addr(w, 4 + r, at, a)
    for r, c in enumerate(consts):
        _const(w, 4 + len(args) + r, c)
    w.append(A.b(at + 4 * len(w), fn))
    return w


def patch_text(text, lo, hi, code, ent, rows=HOOKS):
    """Write blob + one stub per hook into [lo, hi) and hook the entries.
    The tile passes get their window from the text (ws_params) as they
    are. Returns (code address, {x86: stub address}, end)."""
    entry = (lo + 15) & ~15
    at = (entry + len(code) + 15) & ~15
    stubs = {}
    words = []
    for x86, _arm, _first, name, _calls, args in rows:
        stubs[x86] = at + 4 * len(words)
        consts = (ws_word(ws_params(text, x86)),) if x86 in WS_SITES else ()
        words += stub(stubs[x86], entry + ent[name], args, consts)
    end = at + 4 * len(words)
    if end > hi:
        raise ValueError('bg quads: %d bytes do not fit %d' % (end - lo, hi - lo))
    text[entry:entry + len(code)] = code
    for va in range(entry + len(code), at, 4):
        struct.pack_into('<I', text, va, A.nop())
    for i, x in enumerate(words):
        struct.pack_into('<I', text, at + 4 * i, x)
    for x86, arm, *_ in rows:
        struct.pack_into('<I', text, arm, A.b(arm, stubs[x86]))
    return entry, stubs, end


def apply_to_nso(src, dest, stock=None, exe=None, log=lambda *_: None, rows=None):
    rows = hooks() if rows is None else rows
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    m = nxmap.Main(src)
    check(m, text, rows)
    exe_bytes = open(exe, 'rb').read() if exe else None
    lo, hi = dead_region(src, exe_bytes)
    if stock is not None:
        st = nxmap.Main(stock).text
        if st[lo:hi] != bytes(text[lo:hi]):
            raise ValueError('the dead body at +0x%X was already written' % lo)
        # the C is the stock function: refuse if an earlier patch changed its
        # translation (ff7nx_fieldbg's optional bleed constant at +0xA09680)
        # BUILD 795: but for the tile window's five sites (ws_params checks
        # what they hold) and padding words, zero in stock and never run
        for x86, *_ in rows:
            flo, fhi = m.extent(x86)
            if x86 in WS_SITES:
                ws_params(text, x86)
            ok = set(WS_SITES.get(x86, ()))
            if any(st[va:va + 4] != bytes(text[va:va + 4]) and va not in ok
                   and st[va:va + 4] != bytes(4) for va in range(flo, fhi, 4)):
                raise ValueError('x86 0x%X\'s translation is not stock here '
                                 '(patched by an earlier step)' % x86)
    code = open(BLOB, 'rb').read()
    if len(code) % 4 or any(struct.unpack_from('<I', code, k)[0] == 0
                            for k in range(0, len(code), 4)):
        raise ValueError('bg quads: the blob holds a zero word (the padding '
                         'allocator would take it)')
    entry, stubs, end = patch_text(text, lo, hi, code, entries(), rows)
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, 0)
    _s2, raw2 = AC.segments(out)
    for x86, arm, *_ in rows:
        if struct.unpack_from('<I', raw2[0], arm)[0] != A.b(arm, stubs[x86]):
            raise ValueError('bg quads: the hook did not survive the repack')
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(out)
    log('  background quads: x86 %s native, code +0x%X..+0x%X in dead x86 '
        '0x%X (%d of %d bytes)' % (', '.join('0x%X' % r[0] for r in rows), entry,
                                   end, DEAD_X86, end - lo, hi - lo))
    for x86, *_ in rows:
        if x86 in WS_SITES:
            p = ws_params(text, x86)
            if p != WS_STOCK:
                log('    0x%X tile window as the widescreen passes set it: '
                    'origin y %d, top %d, left %d, right +%d, bottom +%d'
                    % ((x86,) + p))
    return {'code': entry, 'stubs': stubs, 'end': end}
