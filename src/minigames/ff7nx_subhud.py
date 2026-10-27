#!/usr/bin/env python3
"""
ff7nx_subhud.py -- BUILD 770. The submarine's DEPTH and SPEED gauges moved
out toward the 16:9 edges. exefs/main only, 29 in-place words, no cave.

    SEVENTH_NX_SUB_HUD=0          stock gauge positions
    SEVENTH_NX_SUB_HUD_SHIFT=67   the offset, in HUD units (default 67)

REPORT
======
User (BUILD 769 screenshot, white/yellow boxes): at 16:9 the two gauges sit
where the 4:3 frame had them and the HUD looks crammed; move everything in
the DEPTH box left by ~100 px (720p) and everything in the SPEED box right
by the same amount.

HOW THE HUD IS DRAWN
====================
x86 0x78F4A0 (ARM +0x105DF80) draws the whole HUD every frame: 61 calls to
the sprite submitter 0x7A3110, each with (x, y, u, v, w, h, ...) pushed by
value. x and y are a sprite record's position plus a screen origin; the
records are 36-byte entries the minigame's HUD init, x86 0x793555 (ARM
+0x101B...), fills ONCE from immediates. Positions are centred HUD units:
screen x = 640 + 1.5 * x at 720p (2.25 px per unit at 1080p), measured by
overlaying every emulated sprite on the user's screenshot.

WHICH SPRITE IS WHICH (emulated HUD draw order, arm64emu)
---------------------------------------------------------
  DEPTH box  0 label, 1..7 bar/ticks/frame, 16..19 TRIM marker + cyan bar,
             20..24 the moving readout ("0624")
  SPEED box  8 label, 9..15 bar/FORWARD/BACKWARD, 25..29 the readout ("+100")
  untouched  30..33 compass, 34..39 sonar dials, 40..46 button icons and
             sonar frame, 47..60 centre markers and the timer

Every x was traced to its source by nudging each candidate immediate in the
two functions by one and re-running both in the emulator (4,553 + 8 candidates):
each gauge sprite's x comes from exactly one immediate, and none of those
immediates feeds any y or any other sprite -- with ONE exception, below.

THE WORDS
=========
init 0x793555, record x (movz/orr -> movz with the shifted value):
  left  +0x101BD80 (label, 0xE995A0)  +0x101BD90 (1)  +0x101BFD8 (2..5)
        +0x101C364 (6)  +0x101C44C (7)  +0x101CF8C (16)  +0x101D098 (17,18)
        +0x101D260 (19)  +0x101D4A0..+0x101D8E0 (20..24)
  right +0x101C688 (label, 0xE988B0)  +0x101C7E4 (9)  +0x101C8E0 (10..13)
        +0x101CC68 (14)  +0x101CD4C (15)  +0x101DB1C (25)  +0x101DC84 (26)
        +0x101DE58 (28)  +0x101DF3C (29)   (27: see below)
HUD 0x78F4A0, the two labels' language-specific x (helper +0x10FAD60
returns 0..4; cases 0/4 read the record, the others are immediates):
  left  +0x105E300 -0x115  +0x105E318 -0xFE  +0x105E320 -0x117
  right +0x105F6E8 0xC8    +0x105F700 0xA3

THE SHARED REGISTERS (found by comparing EVERY argument of all 61 draws,
in all five languages, stock vs patched). Two init registers each feed a
gauge x AND something that must not move.

1. The init loads 0xFF12 (-238) into w27 once and stores it twice: the DEPTH label's Y (0xE995A2) and sprite 1's X (0xE995C4).
Shifting w27 alone would lift the label 67 units in two languages.
0xE995A2 has exactly two references in the module -- this init store and
one load in the HUD (+0x105E2A4, cases 0/4) -- so the load becomes the
constant it always read:
  +0x105E2A4  ldrsh w8, [x0]  ->  mov w8, #-0xEE      (-238, as stock)
and w27 is free to carry the new x. The translate call before it is left
in place (it only computes an address; nothing reads the result).

2. `orr w27, wzr, #0xF8` (+0x101DCC4) is sprite 26's texture u (the "+"
glyph) AND sprite 27's x. It stays stock; 0xE982C0 (27's x) is read only
by the HUD (+0x106248C), so that load becomes the shifted constant:
  +0x106248C  ldrsh w8, [x0]  ->  mov w8, #(248 + shift)

Off a 16:9 build (ff7nx_minifade's gate) nothing is written.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import a64 as A

ENV = 'SEVENTH_NX_SUB_HUD'
SHIFT_ENV = 'SEVENTH_NX_SUB_HUD_SHIFT'
DEFAULT_SHIFT = 67           # 100.5 px at 720p (the user asked ~100)

# (site, stock word, stock value, side)  side -1 = DEPTH (left), +1 = SPEED
INIT_SITES = (
    (0x101BD80, None, -256, -1),   # mov w8,  #0xff00  label
    (0x101BD90, None, -238, -1),   # mov w27, #0xff12  1 (+ label y)
    (0x101BFD8, None, -218, -1),   # mov w28, #0xff26  2..5
    (0x101C364, None, -254, -1),   # mov w8,  #0xff02  6
    (0x101C44C, None, -260, -1),   # mov w8,  #0xfefc  7
    (0x101CF8C, None, -208, -1),   # mov w8,  #0xff30  16
    (0x101D098, None, -212, -1),   # mov w27, #0xff2c  17, 18
    (0x101D260, None, -208, -1),   # mov w8,  #0xff30  19
    (0x101D4A0, None, -278, -1),   # mov w8,  #0xfeea  20
    (0x101D614, None, -270, -1),   # mov w8,  #0xfef2  21
    (0x101D708, None, -262, -1),   # mov w8,  #0xfefa  22
    (0x101D7F8, None, -254, -1),   # mov w8,  #0xff02  23
    (0x101D8E0, None, -246, -1),   # mov w8,  #0xff0a  24
    (0x101C688, None, 200, 1),     # mov w8,  #0xc8    label
    (0x101C7E4, None, 220, 1),     # mov w8,  #0xdc    9
    (0x101C8E0, None, 216, 1),     # mov w28, #0xd8    10..13
    (0x101CC68, None, 220, 1),     # mov w8,  #0xdc    14
    (0x101CD4C, None, 216, 1),     # mov w8,  #0xd8    15
    (0x101DB1C, None, 220, 1),     # mov w8,  #0xdc    25
    (0x101DC84, None, 240, 1),           # orr w8,  wzr, #0xf0   26
    (0x101DE58, None, 256, 1),           # orr w8,  wzr, #0x100  28
    (0x101DF3C, None, 264, 1),     # mov w8,  #0x108   29
)
# The labels' language variants in the HUD routine (movn / movz).
HUD_SITES = (
    (0x105E300, None, -0x115, -1),
    (0x105E318, None, -0xFE, -1),
    (0x105E320, None, -0x117, -1),
    (0x105F6E8, None, 0xC8, 1),
    (0x105F700, None, 0xA3, 1),
)
# Loads in the HUD routine replaced by the constant they read (see the
# module note, THE SHARED REGISTERS): (site, field, stock value, side).
# side 0 = keep the value (the DEPTH label's y); +1 = shift right.
LDRSH_W8_X0 = 0x79C00008             # ldrsh w8, [x0]
CONST_READS = (
    (0x105E2A4, 0xE995A2, -0xEE, 0),     # DEPTH label y, always -238
    (0x106248C, 0xE982C0, 248, 1),       # SPEED readout sprite 27 x
)

ANCHORS = (       # (site, stock word, what) -- checked, never written
    (0x105E29C, 0x1101FAA0, 'add w0, w21, #0x7e   &0xE995A2 (label y)'),
    (0x105E308, 0x1101F2A0, 'add w0, w21, #0x7c   &0xE995A0 (label x)'),
    (0x105F6F0, 0x5109E280, 'sub w0, w20, #0x278  &0xE988B0 (SPEED label x)'),
    (0x101BD88, 0x11053A80, 'add w0, w20, #0x14e  init: &0xE995A2'),
    (0x101BD94, 0x7900001B, 'strh w27, [x0]       init: label y from w27'),
    (0x101C680, 0x110BF260, 'add w0, w19, #0x2fc  init: &0xE988B0'),
    (0x1062484, 0x5121A280, 'sub w0, w20, #0x868  &0xE982C0 (readout 27 x)'),
    (0x101DCC4, 0x321D13FB, 'mov w27, #0xf8       init: 26 u AND 27 x (kept)'),
    (0x101DD74, 0x7900001B, 'strh w27, [x0]       init: 27 x from w27'),
)


def _on(env, name, default='1'):
    v = (os.environ if env is None else env).get(name, default).strip().lower()
    return v not in ('0', 'off', 'no', 'false', 'stock')


def enabled(env=None):
    if not _on(env, ENV):
        return False
    try:
        import ff7nx_minifade
        return ff7nx_minifade.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def shift(env=None):
    raw = (os.environ if env is None else env).get(SHIFT_ENV, '')
    try:
        v = int(raw) if raw.strip() else DEFAULT_SHIFT
    except ValueError:
        v = DEFAULT_SHIFT
    return max(0, min(v, 100))       # 100 units = 150 px at 720p: still in frame


def _w(text, va):
    return struct.unpack_from('<I', text, va)[0]


def _value_of(word):
    """Signed value an in-place `mov wN, #imm` (movz/movn/orr-bitmask) sets."""
    rd = word & 31
    if (word & 0xFF800000) == 0x52800000:                      # movz w
        v = (word >> 5) & 0xFFFF
        return rd, v - 0x10000 if v & 0x8000 else v, 'movz'
    if (word & 0xFF800000) == 0x12800000:                      # movn w
        v = ~((word >> 5) & 0xFFFF) & 0xFFFFFFFF
        return rd, v - (1 << 32) if v >> 31 else v, 'movn'
    if (word & 0xFF8003E0) == 0x320003E0:                      # orr w, wzr
        n = (word >> 22) & 1
        immr, imms = (word >> 16) & 0x3F, (word >> 10) & 0x3F
        v = _bitmask32(n, immr, imms)
        if v is None:
            return None
        return rd, v - (1 << 32) if v >> 31 else v, 'orr'
    return None


def _bitmask32(n, immr, imms):
    """32-bit logical immediate (DecodeBitMasks), self-contained so the
    build needs no disassembler for this pass."""
    if n:
        return None                                # 64-bit only
    combined = (~imms) & 0x3F
    if combined == 0:
        return None
    length = combined.bit_length() - 1
    esize = 1 << length
    levels = esize - 1
    s_, r = imms & levels, immr & levels
    if s_ == levels:
        return None
    welem = (1 << (s_ + 1)) - 1
    elem = ((welem >> r) | (welem << (esize - r))) & ((1 << esize) - 1)
    out = 0
    for i in range(0, 32, esize):
        out |= elem << i
    return out & 0xFFFFFFFF


def _mov(rd, value, halfword):
    """`mov wN, #value`. A halfword store only keeps the low 16 bits, so
    the init sites use movz with the 16-bit pattern (as stock does)."""
    if halfword or value >= 0:
        return A.movz(rd, value & 0xFFFF)
    return 0x12800000 | ((~value & 0xFFFF) << 5) | rd           # movn


def words(text, d=None):
    """{site: (stock, new, why)} or raise if a stock word is not found."""
    d = shift() if d is None else d
    out = {}
    for sites, half in ((INIT_SITES, True), (HUD_SITES, False)):
        for va, want, val, side in sites:
            w = _w(text, va)
            got = _value_of(w)
            same = got is not None and (
                (got[1] - val) % 0x10000 == 0 if half else got[1] == val)
            if not same or (want is not None and w != want):
                raise ValueError('sub HUD site +%#x holds %08X, not the stock '
                                 'value %d' % (va, w, val))
            rd = got[0]
            out[va] = (w, _mov(rd, val + side * d, half),
                       'sub HUD %s x %d -> %d' % ('DEPTH' if side < 0
                                                  else 'SPEED',
                                                  val, val + side * d))
    for va, field, val, side in CONST_READS:
        w = _w(text, va)
        if w != LDRSH_W8_X0:
            raise ValueError('sub HUD read site +%#x holds %08X' % (va, w))
        out[va] = (w, _mov(8, val + side * d, False),
                   'sub HUD 0x%X read as the constant %d' % (field,
                                                            val + side * d))
    return out


def _check_anchors(text):
    bad = ['+%#x holds %08X, not %08X (%s)' % (va, _w(text, va), want, what)
           for va, want, what in ANCHORS if _w(text, va) != want]
    if bad:
        raise ValueError('sub HUD anchors moved: ' + '; '.join(bad))


def patch_text(text, d=None):
    """Write the words into `text` (bytearray). Idempotent; refuses a mixed
    state. Returns {site: new}."""
    d = shift() if d is None else d
    _check_anchors(text)
    t = bytes(text)
    applied = True
    for sites, half in ((INIT_SITES, True), (HUD_SITES, False)):
        for va, _want, val, side in sites:
            got = _value_of(_w(t, va))
            if got is None or _w(t, va) != _mov(got[0], val + side * d, half):
                applied = False
    for va, _field, val, side in CONST_READS:
        if _w(t, va) != _mov(8, val + side * d, False):
            applied = False
    if applied:
        return {}
    ws = words(text, d)          # raises on anything not stock
    out = {}
    for va, (_old, new, _why) in ws.items():
        struct.pack_into('<I', text, va, new)
        out[va] = new
    return out


def apply_to_nso(src, dest, log=lambda *_: None, d=None):
    import ff7nx_audio_cave as AC
    with open(src, 'rb') as fh:
        blob = fh.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    d = shift() if d is None else d
    written = patch_text(text, d)
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, 0)
    chk_segs, _chk = AC.segments(out)
    assert chk_segs[0][2] == segs[0][2]
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as fh:
        fh.write(out)
    return {'words': len(written), 'shift': d}
