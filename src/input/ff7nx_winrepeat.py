#!/usr/bin/env python3
"""ff7nx_winrepeat.py -- the Config menu's window colour sliders move one
step per press, and repeat only once the button has been held a moment.

BUILD 694. Hardware, 10-16: "when setting the window color in the menu
settings for each click to move it exactly 1 tick, but also allow click and
hold for after a brief pause to continously change values (like how it
currently does)".

WHAT THE GAME DOES
==================
The Config menu's colour editor (x86 0x6C3ACD, ARM +0xCA6840) changes the
selected corner's R, G or B (0xDC10D8 + (corner * 2 + ..) * 4 + 0..2) by one
for every frame left or right is HELD:

    if (key_held_or_mouse(0x8000 left, 8))  value -= 1      0x6F5317
    if (key_held_or_mouse(0x2000 right, 4)) value += 1

0x6F5317 is `[0x9A85D4] & key || mouse & mask`: held, not pressed. So the
shortest tap moves the value by several steps, and a precise colour is a
matter of luck.

WHAT THIS DOES
==============
The six calls (R, G, B x left, right; ARM +0xCA91C4 .. +0xCA9DBC) go
through a stub that calls the real 0x6F5317, then lets its "yes" through
only on the first frame of a hold and again from the REPEAT_AFTER-th held
frame on (one step a frame from then on, as before). One counter per
direction; a new press (0x9A85E0, held & ~held last frame) restarts it, so
a hold that ended while the menu was not asking cannot carry over.

BUILD 695. The cursor sound is timed by the frame counter (`[ebp+8] & 4`:
4 frames in 8), so a single step only clicked half the time. Each row's
sound check (SOUND) reads that word through sound_cave, which forces bit 2
on the press frame: every tap clicks once, and a held repeat keeps the
stock timing.

Nothing else reads 0x6F5317's answer differently: the stub is only on those
six calls. SEVENTH_NX_WINREPEAT=0 leaves them stock.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_deadspace as DS

ENV = 'SEVENTH_NX_WINREPEAT'
VERSION = 'winrepeat-694'

CTX_PTR = 0x12CE2B0
TRANSLATE = 0x10FC3A0
HELD = 0xCC3F30                    # x86 0x6F5317 (key held or mouse)
PRESSED = 0x9A85E0                 # keys pressed this frame (held & ~last)
KEY_LEFT, KEY_RIGHT = 0x8000, 0x2000
# (ARM bl site, key the site pushes) -- R, G, B rows, left then right
SITES = ((0xCA91C4, KEY_LEFT), (0xCA9358, KEY_RIGHT),
         (0xCA98F4, KEY_LEFT), (0xCA9A84, KEY_RIGHT),
         (0xCA9C2C, KEY_LEFT), (0xCA9DBC, KEY_RIGHT))
FUNC = (0xCA6840, 0xCA9F20)        # x86 0x6C3ACD
REPEAT_AFTER = 18                  # held frames before it repeats
# BUILD 695: the cursor sound's frame check after each site (x86
# `test [ebp+8] & 4`, ARM `ldrh w8, [x0]` then `and w9, w8, #4`), R, G, B
# rows, left then right -- one per entry of SITES
SOUND = (0xCA9248, 0xCA93DC, 0xCA9978, 0xCA9B08, 0xCA9CB0, 0xCA9E40)
BSS_BYTES = 12                     # two hold counters, the press flag
HI, EQ = 0x8, 0x0
# what the compiler pushes them with: `mov w8, #key` as a bitmask immediate
KEY_WORD = {KEY_LEFT: 0x321103E8, KEY_RIGHT: 0x321303E8}


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _mov32(e, rd, v):
    e(A.movz(rd, v & 0xFFFF))
    if v >> 16:
        e(A.movk_hi(rd, v >> 16))


def _tst(rn, rm):                          # tst Wn, Wm
    return 0x6A000000 | (rm << 16) | (rn << 5) | 31


def stub(at, key, common):
    """Replaces `bl 0x6F5317` (x30 = the site + 4): which counter, then the
    shared cave, which returns to the site."""
    return [A.movz(15, 4 if key == KEY_RIGHT else 0),
            A.b(at + 4, common)]


def common_cave(at, state):
    """Entered from a stub with x30 = the site + 4 and x15 = 0 (left) or 4
    (right). Calls the real 0x6F5317 (the guest arguments are already
    pushed), then filters its answer in guest eax. After that call x0..x18
    and the flags are dead (the caller reloads everything from the guest
    context); x19..x29 are not touched."""
    a = AC.Asm(at, lambda i: at + 4 * i)
    e = a.emit
    e(A.stp64_pre(15, 30, 31, -16))
    e(A.bl(a.pc(), HELD))
    _mov32(e, 0, PRESSED)
    e(A.bl(a.pc(), TRANSLATE))
    e(A.ldr(14, 0, 0))                               # pressed this frame
    e(A.ldp64_post(15, 30, 31, 16))
    e(A.movz(9, KEY_LEFT))
    e(A.movz(11, KEY_RIGHT))
    e(A.cmp_imm(15, 0))
    e(A.csel(9, 11, 9, 1))                           # NE: right
    e(A.adrp(10, a.pc(), CTX_PTR & ~0xFFF))
    e(A.ldr64(10, 10, CTX_PTR & 0xFFF))             # guest context
    e(A.adrp(12, a.pc(), state & ~0xFFF))
    e(A.add_imm64(12, 12, state & 0xFFF))
    e(A.add_reg64(16, 12, 15))                       # this direction's counter
    e(A.ldr(11, 10, 0))                              # guest eax: held?
    a.cbz(11, 'released')
    e(A.ldr(13, 16, 0))
    e(_tst(14, 9))
    e(A.csel(13, 31, 13, 1))                         # a new press: from 0
    e(A.add_imm(13, 13, 1))
    e(A.movz(14, 0xFFFF))
    e(A.cmp_reg(13, 14))
    e(A.csel(13, 14, 13, HI))                        # never wraps
    e(A.str_(13, 16, 0))
    e(A.cmp_imm(13, 1))
    e(A.csinc(11, 31, 31, 1))                        # 1 on the press frame
    e(A.str_(11, 12, 8))                             # for sound_cave
    a.bcond('yes', EQ)                               # the press itself
    e(A.cmp_imm(13, REPEAT_AFTER))
    a.bcond('yes', HI)                               # held long enough
    e(A.str_(31, 10, 0))                             # eax = 0: not this frame
    a.label('yes')
    e(A.ret())
    a.label('released')
    e(A.str_(31, 16, 0))
    e(A.ret())
    return a.resolve()


def sound_cave(at, state):
    """BUILD 695. Replaces `ldrh w8, [x0]` (the guest frame word the cursor
    sound is timed by: it plays when bit 2 is set, 4 frames in 8). On the
    press frame bit 2 is forced on, so every tap clicks; while repeating
    the stock timing stands. Only w8 and w9 change, and the next
    instruction overwrites w9."""
    return [A.ldrh(8, 0, 0),
            A.adrp(9, at + 4, state & ~0xFFF),
            A.add_imm64(9, 9, state & 0xFFF),
            A.ldr(9, 9, 8),
            A.orr_lsl(8, 8, 9, 2),
            A.ret()]


def check_sites(img):
    bad = []
    for site, key in SITES:
        if not FUNC[0] <= site < FUNC[1]:
            bad.append('+0x%X is outside 0x6C3ACD' % site)
        if _w(img, site) != A.bl(site, HELD):
            bad.append('+0x%X is %08X, not bl 0x6F5317' % (site, _w(img, site)))
        if _w(img, site - 0x14) != KEY_WORD[key]:
            bad.append('+0x%X does not push key %#x' % (site, key))
        if _w(img, site + 8) != A.add_imm(8, 8, 8):
            bad.append('+0x%X: the caller does not pop two arguments' % site)
    for (site, _key), snd in zip(SITES, SOUND):
        if not site < snd < site + 0x100:
            bad.append('+0x%X is not after +0x%X' % (snd, site))
        if (_w(img, snd) != A.ldrh(8, 0, 0)
                or _w(img, snd + 4) != 0x121E0109          # and w9, w8, #4
                or _w(img, snd - 4) != A.bl(snd - 4, TRANSLATE)
                or _w(img, snd - 8) != A.add_imm(0, 8, 8)):  # [ebp + 8]
            bad.append('+0x%X is not the cursor sound check' % snd)
    return bad


def apply_to_nso(src, dest, stock=None, log=lambda *_: None, region=None):
    import nxmap
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    img = nxmap.Main(src).img
    bad = check_sites(img)
    if bad:
        raise ValueError('winrepeat: ' + '; '.join(bad))
    lo, hi = region if region else DS.part(src, 'winrep', stock)
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = (state - base) + BSS_BYTES + AC.bss_tail_slack(segs)
    at = (lo + 15) & ~15
    placed = {}

    def put(words):
        nonlocal at
        entry = at
        for i, x in enumerate(words):
            placed[entry + 4 * i] = x
        at = entry + 4 * len(words)
        return entry
    common = put(common_cave(at, state))
    sound = put(sound_cave(at, state))
    stubs = {}
    for site, key in SITES:
        stubs[site] = put(stub(at, key, common))
    if at > hi:
        raise ValueError('winrepeat: %d bytes do not fit the %d-byte part'
                         % (at - lo, hi - lo))
    for va, x in placed.items():
        struct.pack_into('<I', text, va, x)
    for site, _key in SITES:
        struct.pack_into('<I', text, site, A.bl(site, stubs[site]))
    for snd in SOUND:
        struct.pack_into('<I', text, snd, A.bl(snd, sound))
    raw[0] = bytes(text)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(AC.pack(blob, raw, growth))
    log('  window colour sliders: one step (and one click) per press, '
        'repeating after %d held frames; caves +0x%X..+0x%X, counters +0x%X (%s=0 disables)'
        % (REPEAT_AFTER, lo, at, state, ENV))
    return {'state': state, 'common': common, 'sound': sound, 'stubs': stubs,
            'bytes': at - lo}
