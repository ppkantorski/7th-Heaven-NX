#!/usr/bin/env python3
"""
ff7nx_subgrid.py -- BUILD 551. The submarine minigame's PlayStation grid.

WHAT IS MISSING AND WHY
=======================
On the PlayStation the submarine's sea floor is drawn under a wireframe grid
that reaches the horizon; it is how you read depth, slope and heading. The
PC port (and so this Switch port, which is the PC code recompiled) kept the
grid but hid it behind a DEBUG KEY:

  terrain draw  x86 0x7A0190
    0x7A02CB  mov eax, [0xE7475C] ; and eax, 0x40 ; je 0x7A1917
              bit 0x40 set  -> 63 x 63 cell line grid over the whole floor
                               (flatlines object 0xE747B8, colour 0xFF7F7F7F)
                               + the solid floor around the sub
              bit 0x40 clear -> the solid floor around the sub only
  0x77F731  mov [0xE7475C], 0          (minigame init: bit clear)
  0x77E1BC  0x41B22B(0x3C) -> xor [0xE7475C], 0x40
            0x41B22B reads the DirectInput keyboard state; 0x3C is DIK_F2.

Nothing else in the executable writes 0xE7475C (every reference checked,
steam and switch ff7_en identical here). On PC the grid is F2; on the Switch
there is no F2, so it can never appear. The line path itself works (the
heading/target lines in the same object draw), and build 550 made deferred
TLVERTEX primitives -- which these lines are -- draw in screen space.

THE FIX (5 words in place, exefs/main, no cave)
===============================================
Translated body of 0x7A0190 = +0x103DC30:

  +0x103E108  tbz w8, #6, +0x1042E48   -> nop     always take the grid path
  +0x103E11C  mov  w25, #0x7F7F        -> colour low half   } the grid
  +0x103E120  movk w25, #0xFF7F, lsl16 -> colour high half  } colour, kept
  +0x103FE70  mov  w25, #0x7F7F        -> colour low half   } in w25 and
  +0x103FE74  movk w25, #0xFF7F, lsl16 -> colour high half  } re-made once

w25 carries the grid colour for all 16 `str w25, [x0]` TLVERTEX colour
stores (x86 `mov [reg+0x10], 0xFF7F7F7F`). The emulated ZF byte written just
before the gate is left as stock -- nothing reads it before the next flag
setter. The PC grey reads poorly on the dark floor; the default is the
PlayStation's red. D3DCOLOR is ARGB (the game's own red target box is
0xFFFF0000).

BUILD 552: DEPTH BIAS (16 more words)
=====================================
The grid lines are the edges of the very floor cells the solid terrain draws
(same projected corners, same z), so wherever the solid floor or a rock is
under a line, line and triangle tie in the depth test and the line loses
about half its pixels (hardware: dashed red on every blue surface, whole
over black). Each of the 16 line-vertex z stores is

    ldr  w23, [x0]           ; z (float bits) from the x86 local
    add  w0, w8, #8          ; &vertex.z
    str  w23, [x24, #N]      ; the emulated x86 register copy  <-- this word
    bl   translate
    str  w23, [x0]           ; vertex.z

and the marked word becomes `sub w23, w23, #BIAS`: z lowered by BIAS ulps
(relative 2^-23 * BIAS -- 512 is 6e-5, i.e. ~2000 steps of a 24-bit depth
buffer near z = 1), which is always nearer to the camera for the positive z
a visible vertex has. Lines still hide behind rocks in front of them. The
emulated register copy it replaces is dead: for every site the next access
to that slot is a store and w23 is re-loaded before any other use (checked
at build time from the stock module and recorded in ZSITES).

BUILD 553: THE SUBMARINE HUD GETS THE TEXTURE FILTER IT ASKS FOR
=================================================================
The HUD is drawn at ~1.5 screen pixels per native texel (720p). Sampled
NEAREST, no texture at any integer scale draws it cleanly: 1x gives vanilla's
uneven 1-2 px strokes, 2x drops every third texel, 3x every other one (the
lumpy T/M of TRIM, the broken WARNING underline, wonky FORWARD/BACKWARD).
The game ASKS for bilinear here -- FFNx's special_case.cpp turns it off
only for the game's own low-res textures in this mode and keeps it for
replacement art. The port's setrenderstate (+0x10D7E90, V_LINEARFILTER)
grants it only in game modes 3 and 4:

  +0x10D7F80  ldr  w8, [x8]            ; the game mode
  +0x10D7F84  sub  w8, w8, #3      ->  mov  w9, #0x418   ; modes 3, 4, 10
  +0x10D7F88  cmp  w8, #2          ->  lsr  w9, w9, w8
  +0x10D7F8C  b.hs +0x10D7FA8      ->  tbz  w9, #0, +0x10D7FA8

Mode 10 is FF7_MODE_SUBMARINE (FFNx ff7.h). Modes 3 and 4 behave exactly as
before; every other mode is refused exactly as before. w9 is dead here (the
next instruction that reads it writes it first, +0x10D7F94); no flag is
consumed after the old b.hs.

Env:
  SEVENTH_NX_SUB_FILTER=1        bilinear for the submarine (default off)
  SEVENTH_NX_SUB_GRID_ULPS=0     fixed ulp bias (BUILD 552), superseded; see sublines
  SEVENTH_NX_SUB_GRID=0          stock (no grid; restores the words)
  SEVENTH_NX_SUB_GRID_RGB=D03030 grid colour, RRGGBB; 'stock' = PC grey
"""
from __future__ import annotations

import os
import struct
import sys

ENV = 'SEVENTH_NX_SUB_GRID'
RGB_ENV = 'SEVENTH_NX_SUB_GRID_RGB'
DEFAULT_RGB = 0xD03030
# BUILD 552 shipped 512 ulps here; hardware showed it was far too little
# near the camera (z = 1 - n/d: a fixed ulp count is a fixed WORLD distance
# only at one range). The real bias is now a relative depth pull done in a
# cave by ff7nx_sublines (SEVENTH_NX_SUB_GRID_BIAS, percent). This in-place
# ulp form stays available for experiments, off by default.
BIAS_ENV = 'SEVENTH_NX_SUB_GRID_ULPS'
DEFAULT_BIAS = 0
# (va, stock word): str w23, [x24, #N] -- the 16 line-vertex z sites
ZSITES = (
    (0x103ED74, 0xB9000717), (0x103EEA4, 0xB9000317),
    (0x103EFD4, 0xB9000B17), (0x103F104, 0xB9000717),
    (0x103F2B8, 0xB9000717), (0x103F3E8, 0xB9000317),
    (0x103F59C, 0xB9000317), (0x103F6CC, 0xB9000B17),
    (0x10404D8, 0xB9000717), (0x1040608, 0xB9000317),
    (0x1040738, 0xB9000B17), (0x1040868, 0xB9000717),
    (0x1040A1C, 0xB9000717), (0x1040B4C, 0xB9000317),
    (0x1040D00, 0xB9000317), (0x1040E30, 0xB9000B17),
)


def sub_w23(k):
    return 0x51000000 | (k << 10) | (23 << 5) | 23


def bias(env=None):
    raw = (os.environ if env is None else env).get(BIAS_ENV, '').strip()
    try:
        v = int(raw, 0) if raw else DEFAULT_BIAS
    except ValueError:
        v = DEFAULT_BIAS
    return v if 0 <= v <= 4095 else DEFAULT_BIAS
TITLE_ID = '0100A5B00BDC6000'

GATE = (0x103E108, 0x36326A08, 0xD503201F)
STOCK_ARGB = 0xFF7F7F7F
COLOUR_SITES = ((0x103E11C, 0x103E120), (0x103FE70, 0x103FE74))
REG = 25
ANCHORS = (
    (0x103DC30, 0xD10203FF),   # function prologue sub sp, sp, #0x80
    (0x103E0DC, 0x5288F715),   # mov  w21, #0x47b8
    (0x103E0E0, 0x72A01CF5),   # movk w21, #0xe7, lsl 16  (0xE747B8)
    (0x103E0E4, 0x510172B4),   # sub  w20, w21, #0x5c     (0xE7475C)
    (0x103E0F0, 0x79400008),   # ldrh w8, [x0]            the flag word
    (0x103E0F4, 0x121A0109),   # and  w9, w8, #0x40
    (0x103FE6C, 0x2A1903E0),   # mov  w0, w25 (last use before re-make)
    (0x103EDD4, 0xB9000019),   # str  w25, [x0]  a grid colour store
    (0x1040538, 0xB9000019),   # str  w25, [x0]  one after the re-make
)


FILTER_ENV = 'SEVENTH_NX_SUB_FILTER'
# (va, stock, new)
FILTER_WORDS = (
    (0x10D7F84, 0x51000D08, 0x52808309),   # mov w9, #0x418
    (0x10D7F88, 0x7100091F, 0x1AC82529),   # lsr w9, w9, w8
    (0x10D7F8C, 0x540000E2, 0x360000E9),   # tbz w9, #0, +0x10D7FA8
)
FILTER_ANCHORS = (
    (0x10D7F6C, 0x361001E8),   # tbz w8, #2  (options & V_LINEARFILTER)
    (0x10D7F80, 0xB9400108),   # ldr w8, [x8]   the game mode
    (0x10D7F90, 0x320003E8),   # mov w8, #1     linear
    (0x10D7F94, 0xF0000FA9),   # adrp x9 ...    (w9 rewritten)
    (0x10D7FA8, 0x2A1F03E8),   # mov w8, wzr    nearest
)


def filter_enabled(env=None):
    # BUILD 554b: OFF by default -- hardware says the lumpy glyphs are not
    # a sampling problem (they are in SYW's own art: _mg/trim_src_cmp.png).
    v = (os.environ if env is None else env).get(FILTER_ENV, '0').strip().lower()
    return v in ('1', 'on', 'yes', 'true')


def enabled(env=None):
    v = (os.environ if env is None else env).get(ENV, '1').strip().lower()
    return v not in ('0', 'off', 'no', 'false', 'stock')


def colour(env=None):
    """ARGB the grid is drawn in."""
    raw = (os.environ if env is None else env).get(RGB_ENV, '').strip().lower()
    if raw in ('stock', 'grey', 'gray', 'pc'):
        return STOCK_ARGB
    try:
        rgb = int(raw.lstrip('#'), 16) if raw else DEFAULT_RGB
    except ValueError:
        rgb = DEFAULT_RGB
    if not 0 <= rgb <= 0xFFFFFF:
        rgb = DEFAULT_RGB
    return 0xFF000000 | rgb


def movz(reg, imm):
    return 0x52800000 | ((imm & 0xFFFF) << 5) | reg


def movk16(reg, imm):
    return 0x72A00000 | ((imm & 0xFFFF) << 5) | reg


def _words(argb):
    out = []
    for lo, hi in COLOUR_SITES:
        out.append((lo, movz(REG, argb & 0xFFFF)))
        out.append((hi, movk16(REG, argb >> 16)))
    return out


def _main_path(target):
    if os.path.isdir(target):
        return os.path.join(target, 'atmosphere', 'contents', TITLE_ID,
                            'exefs', 'main')
    return target


def _w(text, va):
    return struct.unpack_from('<I', text, va)[0]


def text_colour(text):
    """ARGB currently in the colour words, or None if they are not ours."""
    vals = set()
    for lo, hi in COLOUR_SITES:
        a, b = _w(text, lo), _w(text, hi)
        if (a & 0xFFE0001F) != 0x52800000 | REG:
            return None
        if (b & 0xFFE0001F) != 0x72A00000 | REG:
            return None
        vals.add(((b >> 5) & 0xFFFF) << 16 | ((a >> 5) & 0xFFFF))
    return vals.pop() if len(vals) == 1 else None


def check_text(text):
    bad = []
    for va, want in ANCHORS:
        if _w(text, va) != want:
            bad.append('anchor +%#x holds %08X, expected %08X'
                       % (va, _w(text, va), want))
    if _w(text, GATE[0]) not in GATE[1:]:
        bad.append('grid gate +%#x holds %08X' % (GATE[0], _w(text, GATE[0])))
    if text_colour(text) is None:
        bad.append('grid colour words are not a mov/movk w25 pair')
    for va, want in FILTER_ANCHORS:
        if _w(text, va) != want:
            bad.append('filter anchor +%#x holds %08X' % (va, _w(text, va)))
    for va, stock, new in FILTER_WORDS:
        if _w(text, va) not in (stock, new):
            bad.append('filter word +%#x holds %08X' % (va, _w(text, va)))
    for va, stock in ZSITES:
        got = _w(text, va)
        if (got != stock and (got & 0xFFC003FF) != 0x510002F7
                and (got & 0xFC000000) != 0x94000000):
            bad.append('z site +%#x holds %08X' % (va, got))
            break
        # the z it biases: ldr w23,[x0] two words before, str w23,[x0] after
        if _w(text, va - 8) != 0xB9400017 or _w(text, va + 8) != 0xB9000017:
            bad.append('z site +%#x is not ldr/str w23' % va)
            break
    return bad


def state_text(text):
    gate = _w(text, GATE[0])
    c = text_colour(text)
    if gate == GATE[1] and c == STOCK_ARGB:
        return 'stock'
    if gate == GATE[2]:
        return 'grid %08X' % c
    return 'unknown'


def apply_nso(target, revert=None, argb=None, log=print, zbias=None):
    """Patch exefs/main in place. Returns 0 on success, 1 on refusal."""
    if revert is None:
        revert = not enabled()
    argb = STOCK_ARGB if revert else (colour() if argb is None else argb)
    zbias = 0 if revert else (bias() if zbias is None else zbias)
    import nso_patcher
    path = _main_path(str(target))
    try:
        nso = nso_patcher.read_nso(nso_patcher.Path(path))
        text = next(bytes(sg.data) for sg in nso.segments if sg.name == '.text')
        bad = check_text(text)
        if bad:
            raise ValueError('; '.join(bad))
        want = [(GATE[0], GATE[1] if revert else GATE[2])] + _words(argb)
        want += [(va, sub_w23(zbias) if zbias else stock)
                 for va, stock in ZSITES]
        filt = (not revert) and filter_enabled()
        want += [(va, new if filt else stock)
                 for va, stock, new in FILTER_WORDS]
        todo = [(va, _w(text, va), new) for va, new in want
                if _w(text, va) != new]
        if not todo:
            log('  submarine grid: %s (already)' % (
                'stock, hidden (%s=0)' % ENV if revert
                else 'on, colour %06X, depth bias %d' % (argb & 0xFFFFFF,
                                                          zbias)))
            return 0
        spec = {'name': 'submarine grid', 'patches': [{
            'name': 'sub grid +%#x' % va, 'va': '0x%X' % va,
            'expect': struct.pack('<I', old).hex(' '),
            'set': struct.pack('<I', new).hex(' ')} for va, old, new in todo]}
        for line in nso_patcher.apply_spec(nso, spec):
            log('    ' + line)
        nso_patcher.Path(path).write_bytes(nso_patcher.rebuild(nso))
    except (OSError, ValueError, StopIteration, nso_patcher.PatchError) as exc:
        log('  ! submarine grid: %s -- NOT CHANGED' % exc)
        return 1
    log('  submarine grid: %s' % ('stock (hidden behind the PC F2 debug key)'
                                  if revert else
                                  'PlayStation grid on, colour %06X, '
                                  'depth bias %d' % (argb & 0xFFFFFF, zbias)))
    return 0


if __name__ == '__main__':
    sys.exit(apply_nso(sys.argv[1], revert=('--revert' in sys.argv)))
