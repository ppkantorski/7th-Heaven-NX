#!/usr/bin/env python3
"""
ff7nx_autosize.py -- BUILD 860. "Autosize dialogue boxes": FFNx's
`ff7_field_autosize_text_box` (default off there too), on the Switch.

WHAT IT DOES
============
FF7's scripts give every dialogue window a fixed size (WINDOW / WSIZW), and
many are wrong for the text that ends up in them -- too wide for a short
line, or a long line (a name, a mod's longer translation, Echo-S's speaker
header) running into the border. FFNx can size each box to its text when it
opens. This is the same rule, on the English native-font path FFNx takes
for this game:

  width   the widest line on any page, in the game's own glyph metrics
          (the table the field text draw reads, so ESUI's 00-Spacing widths
          count when ESUI is in), + 16 px;
  height  the most lines on any page (16 px a line) + 25 px, never TALLER
          than the script made the box;
  both    width <= 320, height <= 224; a box the new size pushed off the
          320x224 screen is moved back onto it.

Field (MESSAGE, ASK) and world map (MESSAGE, ASK) windows, once per box, on
its first opening frame -- exactly where FFNx does it. The countdown timer
and the other special windows (WSPCL), text with a JP font-page byte, and
anything else it cannot measure exactly keep the script's size. See
native/autosize.c.

WHERE
=====
A `b` at the first word of each opening step -- field x86 0x6317A9 (ARM
+0x9CEE70, called from the MESSAGE and ASK opcodes 0x630D50 / 0x6310A1),
world 0x769A66 (+0xFA5870, from 0x769050 / 0x7693A1) -- to a stub that
calls as_open(guest esp, translator, world), then runs the displaced word
and returns into the function. Both entries are the same stock word,
`stp x22, x21, [sp, #-0x30]!`, and refused if anything else is there.

The code lives in the translated body of x86 0x63CFD0 (5.4 KB of ARM), dead
in the module (no branch, no relocation but the map's own record) and in
the exe (no call or jump, its address nowhere in ff7_en), and refused if
another pass already wrote it. No BSS: as_open keeps no state.

Off by default, as in FFNx. GUI: Settings -> "Autosize dialogue boxes"
(not a developer setting). SEVENTH_NX_TEXT_AUTOSIZE=1 turns it on; off writes
nothing at all.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import nxpaths  # BUILD 755: project paths (src/nxpaths.py)
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC

VERSION = 860
ENV = 'SEVENTH_NX_TEXT_AUTOSIZE'
BLOB = os.path.join(nxpaths.APP, 'native', 'autosize.bin')
ENTRY = os.path.join(nxpaths.APP, 'native', 'autosize.entry')

X86_CTX_GOT, X86_ESP, GUEST_TO_HOST = 0x12CE2B0, 0x10, 0x10FC3A0
ENTRY_WORD = 0xA9BD57F6                      # stp x22, x21, [sp, #-0x30]!
# (ARM entry, world flag, what)
SITES = ((0x9CEE70, 0, 'field window opening 0x6317A9'),
         (0xFA5870, 1, 'world window opening 0x769A66'))

OWN_DEAD_X86 = 0x63CFD0
OWN_GUARD = 0x40
OWN_TAIL = 0x40

# guest layout native/autosize.c reads (kept here for the tests)
WIN_ARRAY, WIN_STRIDE = 0xCFF5B8, 0x30
FIELD_STRINGS, WORLD_STRINGS = 0xCBF578, 0xE3B210
SPACING_PTR = 0xDB958C
NAME_INDEX, NAMES, NAME_STRIDE = 0x919928, 0xDBFD9C, 0x84


def enabled(env=None):
    v = (os.environ if env is None else env).get(ENV, '0').strip().lower()
    return v in ('1', 'on', 'true', 'yes')


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


def entry_stub(at, site, world, fn):
    """b here from the opening step's first word: as_open(esp, translator,
    world), then the displaced word and back to site + 4. Nothing is live
    in a register at a translated function's entry (its prologue saves
    x19..x22 and loads the context from the GOT itself)."""
    w = [A.stp64_pre(29, 30, 31, -0x10)]
    _got(w, 9, at, X86_CTX_GOT)
    w.append(A.ldr(0, 9, X86_ESP))
    _addr(w, 1, at, GUEST_TO_HOST)
    w.append(A.movz(2, world))
    w.append(A.bl(at + 4 * len(w), fn['as_open']))
    w += [A.ldp64_post(29, 30, 31, 0x10), ENTRY_WORD]
    w.append(A.b(at + 4 * len(w), site + 4))
    return w


def check(text):
    for site, _world, what in SITES:
        got = struct.unpack_from('<I', text, site)[0]
        if got != ENTRY_WORD:
            raise ValueError('autosize: +0x%X (%s) is %08X, not %08X'
                             % (site, what, got, ENTRY_WORD))


def _exe_bytes():
    for p in (os.path.join(nxpaths.ROOT, 'dump', 'romfs', 'ff7', 'resources',
                           'ff7_1.02', 'ff7_en'),
              os.path.join(nxpaths.ROOT, 'ff7_en')):
        if os.path.isfile(p):
            with open(p, 'rb') as f:
                return f.read()
    return None


def own_region(src, stock=None, exe=None):
    """(lo, hi) of this pass's dead body in `src`, proven dead and unused."""
    import nxmap
    import ff7nx_fieldbg
    m = nxmap.Main(src)
    why = ff7nx_fieldbg._liveness(m, OWN_DEAD_X86)
    if not why:
        exe = _exe_bytes() if exe is None else exe
        if exe is None:
            why = 'ff7_en not found to prove it'
        else:
            import ff7nx_fxinterp
            why = ff7nx_fxinterp._x86_unreferenced(exe, OWN_DEAD_X86)
    if why:
        raise ValueError('x86 0x%X is not dead: %s' % (OWN_DEAD_X86, why))
    lo, hi = m.extent(OWN_DEAD_X86)
    lo, hi = lo + OWN_GUARD, hi - OWN_TAIL
    if stock is not None:
        if nxmap.Main(stock).text[lo:hi] != m.text[lo:hi]:
            raise ValueError('the body at +0x%X was already modified by '
                             'another pass' % lo)
    return lo, hi


def place(lo, hi):
    """{va: word} for the blob and the two stubs, and the stub addresses."""
    code = open(BLOB, 'rb').read()
    entry = (lo + 15) & ~15
    fn = {k: entry + v for k, v in entries().items()}
    words = {}
    for i in range(0, len(code) + (-len(code) % 4), 4):
        words[entry + i] = int.from_bytes(code[i:i + 4].ljust(4, b'\0'),
                                          'little')
    at = (entry + len(code) + 15) & ~15
    stubs = {}
    for site, world, _what in SITES:
        ws = entry_stub(at, site, world, fn)
        stubs[site] = at
        for i, x in enumerate(ws):
            words[at + 4 * i] = x
        at += 4 * len(ws)
    if at > hi:
        raise ValueError('autosize: %d bytes do not fit the %d-byte body'
                         % (at - lo, hi - lo))
    return words, stubs, fn, at - entry


def apply_to_nso(src, dest, stock=None, log=lambda *_: None):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    check(text)
    lo, hi = own_region(src, stock)
    words, stubs, fn, size = place(lo, hi)
    for va, x in words.items():
        struct.pack_into('<I', text, va, x)
    for site, _world, _what in SITES:
        struct.pack_into('<I', text, site, A.b(site, stubs[site]))
    raw[0] = bytes(text)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(AC.pack(blob, raw, 0))
    log('  autosize dialogue boxes: field and world windows sized to their '
        'text as they open (FFNx rule); code +0x%X (%d bytes)'
        % (fn['as_open'], size))
    return {'entry': fn['as_open'], 'stubs': stubs, 'bytes': size}
