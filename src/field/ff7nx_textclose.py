#!/usr/bin/env python3
"""
ff7nx_textclose.py -- a dialogue box's text never shows outside the box while
the box shrinks away. BUILD 886.

HARDWARE 11-03 (user): "the text in all dialogues is not vanishing perfectly
with the boxes. the boxes shrink when a dialogue is dismissed and the text
ends up clipping outside of the boxes" ... "the text should never overlap
outside of the shrinking text box as it shrinks."

WHAT FF7 DOES. field_submit_and_draw_text_box_and_text (x86 0x6EBF2C) draws
each window. For a shrinking box (current size != full size) it submits the
box on the rect (2x + W - cw, y + H - ch, 2cw, 2ch) and sets the same rect as
the viewport that clips the window's text (0x66067A). Closing
(0x632EB8) takes a quarter of the full size per tick, so the box is gone in
four ticks.

WHY IT SHOWS HERE. ff7nx_uiclip puts that clip exactly on the widescreen
mapping (`--show`: every rect case lands on 1.5x + 160), but the visible
border is drawn inside the window rect, so text clipped at the rect edge
still reads past the border -- measured 8-9 device px on each side at 1280,
the "B" of "Barret" and the "e" of "hire" cut outside the box.

FIX. The text loop is driven by the character count the draw routine copies
from the window (+0x12) into 0xDC3CCC just before calling the text routine
0x6E706D; the loop stops when it reaches zero. While the box is not at its
full size (current width != width or current height != height -- the same
test the routine itself uses to pick the shrinking rect, x86 0x6EC329), the
count is now 0, so a shrinking box carries no text at all and nothing can
cross its border. A box that has finished opening has current == full
exactly (the opening step clamps to it, x86 0x631840 / 0x6318D5), so open
boxes and paging are untouched.

WHERE. The translator emitted the window loop twice; both copies load the
count with the same word, `ldrh w22, [x0]`, where x0 is the host address of
window +0x12 (the window array, 0xCFF5B8 + 0x30 * i, lies in one page). Each
becomes a `b` to a 12-word cave in verified padding:

    ldrh  w22, [x0]          ; the count (displaced word)
    sub   x11, x0, #0x12     ; the window
    ldrsh w9,  [x11, #8]     ; width
    ldrsh w10, [x11, #0xC]   ; current width
    cmp   w9, w10
    b.ne  zero
    ldrsh w9,  [x11, #0xA]   ; height
    ldrsh w10, [x11, #0xE]   ; current height
    cmp   w9, w10
    b.eq  out
  zero:
    movz  w22, #0
  out:
    b     hook + 4

w9, w10, w11 are free there (the next instruction calls the guest-address
translator). Refused unless both hooks hold the stock word.

SEVENTH_NX_TEXT_CLOSE=0 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import tempfile
from pathlib import Path

import nxpaths  # noqa: F401  (project paths)
import a64 as A
import ff7nx_cave
import nso_patcher

ENV = 'SEVENTH_NX_TEXT_CLOSE'
SDOUT_MAIN = os.path.join('atmosphere', 'contents', '0100A5B00BDC6000',
                          'exefs', 'main')
STOCK = 0x79400016                     # ldrh w22, [x0]
HOOKS = (0xD97194, 0xD988EC)           # both copies of the window loop
W_OFF, H_OFF, CW_OFF, CH_OFF, COUNT_OFF = 0x08, 0x0A, 0x0C, 0x0E, 0x12


def enabled(env=None):
    v = (os.environ if env is None else env).get(ENV, '1').strip().lower()
    return v not in ('0', 'off', 'no', 'false')


def body_words_at(hook, addr):
    w = [
        STOCK,                                  # 0  w22 = count
        A.sub_imm64(11, 0, COUNT_OFF),          # 1  x11 = window
        A.ldrsh(9, 11, W_OFF),                  # 2
        A.ldrsh(10, 11, CW_OFF),                # 3
        A.cmp_reg(9, 10),                       # 4
        None,                                   # 5  b.ne zero
        A.ldrsh(9, 11, H_OFF),                  # 6
        A.ldrsh(10, 11, CH_OFF),                # 7
        A.cmp_reg(9, 10),                       # 8
        None,                                   # 9  b.eq out
        A.movz(22, 0),                          # 10 zero: count = 0
        None,                                   # 11 out: b hook + 4
    ]
    ZERO, OUT = 10, 11
    w[5] = A.bcond(addr(5), addr(ZERO), A.NE)
    w[9] = A.bcond(addr(9), addr(OUT), A.EQ)
    w[11] = A.b(addr(11), hook + 4)
    return w


def resolve_main(path):
    p = str(path)
    if os.path.isdir(p):
        p = os.path.join(p, SDOUT_MAIN)
    return Path(p)


def w32(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _b_target(word, va):
    if (word >> 26) != 0b000101:
        return None
    imm = word & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return va + 4 * imm


def installed(t):
    return all(_b_target(w32(t, h), h) is not None for h in HOOKS)


def build_patches(img, starts, log=print):
    img = bytearray(img)
    for h in HOOKS:
        if w32(img, h) != STOCK:
            log('  ! text close: hook +%#x is %08X, not `ldrh w22,[x0]`'
                % (h, w32(img, h)))
            return None
    pool = ff7nx_cave.HolePool(img, starts=starts)
    words = {}
    for h in HOOKS:
        entry, cw = ff7nx_cave.emit_laid_out(
            pool, lambda _e, addr, h=h: body_words_at(h, addr))
        words.update(cw)
        words[h] = A.b(h, entry)
        log('  text close: hook +%#x -> cave +%#x (%d words)'
            % (h, entry, len(body_words_at(h, lambda i: 4 * i))))
    return words


def apply(main, log=print) -> int:
    """0 on success (or already installed), 1 if it refused."""
    import nxmap
    main = resolve_main(main)
    try:
        m = nxmap.Main(str(main))
    except SystemExit as exc:
        log('  ! text close: %s' % exc)
        return 1
    t = m.text
    if installed(t):
        log('  text close: already installed')
        return 0
    words = build_patches(m.img, set(m.arm_starts), log)
    if words is None:
        log('  refusing to write.')
        return 1
    patches = [{'name': 'ff7nx_textclose +%#09X' % va, 'va': hex(va),
                'expect': struct.pack('<I', w32(t, va)).hex(' '),
                'set': struct.pack('<I', word).hex(' ')}
               for va, word in sorted(words.items())]
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_textclose',
                                             'patches': patches}):
        log('    ' + line)
    blob = nso_patcher.rebuild(nso)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.textclose-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(blob)
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    t2 = nxmap.Main(str(main)).text
    for va, word in words.items():
        if w32(t2, va) != word:
            log('  ! text close: +%#x did not read back. DO NOT BOOT THIS.' % va)
            return 1
    log('  text close (BUILD 886): a shrinking dialogue box draws no text, so '
        'none shows past its border. %s=0 disables.  (%s)'
        % (ENV, hashlib.md5(blob).hexdigest()))
    return 0
