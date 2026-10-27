#!/usr/bin/env python3
"""
ff7nx_spitreach.py -- BUILD 681: the "spit" enemy attacks (Piazzo Shower and
the eight that share its code) leave the mouth and land on the target.

THE SYMPTOM
===========
Piazzo Shower (Zenene, hardware 10-15): the poison cloud starts inside the
enemy's head, travels level at head height, and stops about two thirds of
the way to the target -- it never touches them. The PC version does this
too; on the PS1 the cloud reaches the target.

WHY (x86, ff7_en)
=================
Enemy attack effect 9 (0x548B50) and effect 122 (0x54935E, the same code
with attach point 8) both run main 0x548C5E. On its first frame it records

    start  = the attacker's attach-point joint (head)       [slot+4..8]
    target = the target's attach-point 0 (body)              [slot+0xC..0x10]
    heading = atan2(dz, dx)                                   emitter +0x14
    speed   = sqrt(dx^2 + dz^2) / 24                          emitter +0x18
                                                     (0x548D57 mov ecx,0x18)

and the emitter (0x5490D1, 32 frames) spawns one sprite a frame at `start`
with velocity Ry(heading) * R(0, wobble, 45deg) * (0, 0, speed) -- level,
no y term. Each sprite (0x549228) moves by its velocity until its .s
animation ends: doku1.s is 16 frames. 16 frames at 1/24 of the distance is
two thirds of the way, at head height.

Effect 60 (0x548330: Liquid Poison, Gas, Toxic Barf, Acid Rain) is a second
copy of exactly the same code (main 0x5483E6, emitter 0x548829, sprite
0x548980; instruction-for-instruction identical apart from addresses), with
the same 16-frame doku sprite and the same /24.

THE CHANGE (exefs/main, both copies)
====================================
* Region A, the 25 words from the `add esp,4` after the sqrt call to the
  speed store (x86 0x548D53..0x548D61): speed = dist / 17 (DIV) instead of
  the /24 magic multiply, and emitter +0x16 = (target.y - start.y) / 17.
  Nothing else reads +0x16 in either emitter. The remainder (edx) and the
  ecx/edx values this block leaves are dead: the next word is a join point
  that the later-frame path reaches with neither set, and both are written
  again before they are read.
* Region B, the 29 words from the `add esp,0xc` after the emitter's
  velocity transform to the frame-counter test (x86 counter++ and reload):
  the same counter update with fewer guest-address calls, plus sprite
  vy += emitter +0x16 and a call to the shared tail, which moves the sprite
  2 * velocity (LEAD) along before its first draw.

A sprite is drawn before it moves, so it is now drawn at 2/17 .. 17/17 of
the way: it appears just ahead of the mouth instead of inside the head and
its last frame is on the target's body, at the body's height. The emitter's
+/-22.5 degree wobble (the snake shape) is untouched.

Attacks: Piazzo Shower, Poison Storm (effect 9); Poison Blow, Poison
Breath, Bio Gas (122); Liquid Poison, Gas, Toxic Barf, Acid Rain (60).

Both regions are rewritten in place: a fully built main has almost no free
padding left (45 single-instruction holes on the 10-15 build), so only the
16-word tail comes from the hole pool. No direct branch in .text lands
inside either region (checked by site_state on every build). The
guest-address helper clobbers x0, x8-x10 and the flags only; the registers
the regions use beyond that (x11-x17) are dead at both ends, and x20/x21,
which the stock words left holding temporaries, are not read again before
being written or restored. SEVENTH_NX_SPIT_REACH=0 leaves the module stock.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: run directly, find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
if _nx_os.path.join(_nx_root, 'src') not in _nx_sys.path:
    _nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

_HERE = nxpaths.ROOT
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                # noqa: E402

VERSION = 681
ENV = 'SEVENTH_NX_SPIT_REACH'
DIV = 17            # sprite lifetime 16 frames + 1: last drawn at 17/17
LEAD = 2            # first drawn at 2/17 of the way, ahead of the mouth
STOCK_DIV = 24
LEAD_SHIFT = {1: 0, 2: 1, 4: 2}

# Stock words of both regions; None is a `bl` to the guest-address helper.
BL = None
REGION_A_STOCK = (
    0x29422668, 0x11001108, 0xB9001268, 0xB9400268, 0x131F7D0A, 0xAA0803EB,
    0xB3607D4B, 0xB201F3EA, 0xF295556A, 0xF2E5554A, 0x9B4A7D6A, 0x9342FD4B,
    0x8B4AFD6A, 0x321D07EB, 0x1B0BA148, 0x51007120, 0x29002E6A, 0xB9000A68,
    BL, 0xB9400008, 0x79400275, 0x11006100, 0xB9000A68, BL, 0x79000015)
REGION_B_STOCK = (
    0x29422668, 0x11003108, 0x51001120, 0xB9001268, BL, 0xB9400008,
    0x11000900, 0xB9000668, BL, 0x79400008, 0x11000508, 0x79001268,
    0xB9401668, 0x51001100, BL, 0xB9400008, 0x79401274, 0x11000900,
    0xB9000268, BL, 0x79000014, 0xB9401668, 0x51001100, BL, 0xB9400008,
    0x11000900, 0xB9000668, BL, 0x79C00008)
REGION_B_PRE = 0x94000000         # the velocity transform call just before

# (what, region A, region B) -- module offsets in the 1.0.3 main.
SITES = (
    ('effect 9/122 (Piazzo Shower, Poison Storm, Poison Blow, Poison Breath, '
     'Bio Gas)', 0x5AF99C, 0x5B0DC4),
    ('effect 60 (Liquid Poison, Gas, Toxic Barf, Acid Rain)',
     0x5AD1FC, 0x5AE584),
)

NOP = 0xD503201F


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def sdiv(rd, rn, rm):
    return 0x1AC00C00 | (rm << 16) | (rn << 5) | rd


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _bl_target(word, va):
    if (word & 0xFC000000) != 0x94000000:
        return None
    off = word & 0x03FFFFFF
    if off & 0x02000000:
        off -= 0x04000000
    return va + off * 4


def ldp_w(rt, rt2, rn, imm):
    return 0x29400000 | ((imm >> 2) << 15) | (rt2 << 10) | (rn << 5) | rt


def stp_w(rt, rt2, rn, imm):
    return 0x29000000 | ((imm >> 2) << 15) | (rt2 << 10) | (rn << 5) | rt


def sxth(rd, rn):
    return 0x13003C00 | (rn << 5) | rd


def region_a(va, helper, div=DIV):
    """Speed = dist / div and emitter+0x16 = (target.y - start.y) / div.

    Stock state it must leave: esp += 4 (cleanup of the sqrt call), the
    emitter's speed (+0x18) stored. x19 eax/ecx get the quotient and the
    divisor as stock; edx (stock: the emitter pointer) is dead -- see the
    module docstring.
    """
    w = [ldp_w(8, 13, 19, 0x10), A.add_imm(8, 8, 4), A.str_(8, 19, 0x10),
         A.ldr(8, 19, 0),                            # eax = sqrt result
         A.movz(11, div), sdiv(14, 8, 11),           # speed
         stp_w(14, 11, 19, 0),                       # eax, ecx as stock
         A.sub_imm(0, 13, 0xC)]                      # [ebp-0xc] main slot
    w.append(A.bl(va + 4 * len(w), helper))
    w += [A.ldr(15, 0), A.add_imm(0, 15, 0xE)]       # target.y
    w.append(A.bl(va + 4 * len(w), helper))
    w += [A.ldrsh(16, 0), A.add_imm(0, 15, 6)]       # start.y
    w.append(A.bl(va + 4 * len(w), helper))
    w += [A.ldrsh(17, 0), A.sub_reg(16, 16, 17),
          sdiv(16, 16, 11),                          # vy
          A.sub_imm(0, 13, 0x1C)]                    # [ebp-0x1c] emitter
    w.append(A.bl(va + 4 * len(w), helper))
    w += [A.ldr(12, 0), A.add_imm(0, 12, 0x16)]
    w.append(A.bl(va + 4 * len(w), helper))
    # emitter slots start at 0x18 mod 0x20, so +0x16..+0x19 never cross a
    # 4 KB guest page: one translation serves both stores.
    w += [A.strh(16, 0), A.strh(14, 0, 2)]
    assert len(w) == len(REGION_A_STOCK)
    return w


def region_b(va, helper, tail):
    """The stock counter update (esp += 0xc; emitter+2 += 1, with eax/ecx =
    emitter and dx = the new count as stock; w8 = the count sign-extended,
    as the reload at the end of the stock region leaves it), plus sprite
    vy += emitter+0x16 and the call to the shared tail."""
    w = [ldp_w(8, 13, 19, 0x10), A.add_imm(8, 8, 0xC), A.str_(8, 19, 0x10),
         A.sub_imm(0, 13, 4)]                        # [ebp-4] emitter
    w.append(A.bl(va + 4 * len(w), helper))
    w += [A.ldr(14, 0), stp_w(14, 14, 19, 0),        # eax = ecx = emitter
          A.add_imm(0, 14, 2)]
    w.append(A.bl(va + 4 * len(w), helper))
    w += [A.ldrh(12, 0), A.add_imm(12, 12, 1), A.strh(12, 0),
          A.strh(12, 19, 8),                         # dx = count
          A.add_imm(0, 14, 0x16)]
    w.append(A.bl(va + 4 * len(w), helper))
    w += [A.ldrsh(15, 0),                            # vy
          A.sub_imm(0, 13, 8)]                       # [ebp-8] sprite
    w.append(A.bl(va + 4 * len(w), helper))
    w += [A.ldr(14, 0), A.add_imm(0, 14, 8)]
    w.append(A.bl(va + 4 * len(w), helper))          # x0 -> sprite+8..+0x11
    w += [A.ldrh(8, 0, 6), A.add_reg(8, 8, 15), A.strh(8, 0, 6)]
    w.append(A.bl(va + 4 * len(w), tail))
    w.append(sxth(8, 12))
    w += [NOP] * (len(REGION_B_STOCK) - len(w))
    assert len(w) == len(REGION_B_STOCK)
    return w


def tail(helper, lead=LEAD):
    """Shared by both copies: sprite position += lead * velocity.

    Entered by `bl` with x0 = host(sprite+8) and w14 = the sprite's guest
    address. Slots are 32 bytes from 0xBFB718 (0x18 mod 0x20), so
    sprite+8..+0x11 (z, velocity) and sprite+4..+7 (x, y) each stay inside
    one 4 KB guest page whatever the slot.
    """
    shift = LEAD_SHIFT[lead]

    def build(entry, addr):
        w = [A.mov_reg64(17, 30),
             A.ldrsh(9, 0, 8), A.ldrh(8, 0, 0),
             A.add_reg_lsl(8, 8, 9, shift), A.strh(8, 0, 0),         # z
             A.ldrsh(15, 0, 4), A.ldrsh(16, 0, 6),                   # vx, vy
             A.add_imm(0, 14, 4)]
        w.append(A.bl(addr(len(w)), helper))
        w += [A.ldrh(8, 0, 0), A.add_reg_lsl(8, 8, 15, shift),
              A.strh(8, 0, 0),                                       # x
              A.ldrh(8, 0, 2), A.add_reg_lsl(8, 8, 16, shift),
              A.strh(8, 0, 2),                                       # y
              A.ret(17)]
        return w
    return build


def _match(img, va, stock, helper):
    for i, x in enumerate(stock):
        got = _w(img, va + 4 * i)
        if x is None:
            if _bl_target(got, va + 4 * i) != helper:
                return False
        elif got != x:
            return False
    return True


def _branch_target(word, va):
    """Target of a direct b/bl/b.cond/cbz/cbnz/tbz/tbnz, else None."""
    if (word & 0x7C000000) == 0x14000000:                 # b, bl
        off = word & 0x03FFFFFF
        if off & 0x02000000:
            off -= 0x04000000
        return va + 4 * off
    if (word & 0xFF000010) == 0x54000000 or (word & 0x7E000000) == 0x34000000:
        off = (word >> 5) & 0x7FFFF
        if off & 0x40000:
            off -= 0x80000
        return va + 4 * off
    if (word & 0x7E000000) == 0x36000000:
        off = (word >> 5) & 0x3FFF
        if off & 0x2000:
            off -= 0x4000
        return va + 4 * off
    return None


def branches_into(img, text_len, ranges):
    """Every direct branch in .text landing strictly inside one of
    `ranges` ([(first, end), ...]); a region may be entered only at its
    first word, which none of these are anyway."""
    hits = []
    words = struct.unpack_from('<%dI' % (text_len // 4), img, 0)
    for i, x in enumerate(words):
        t = _branch_target(x, 4 * i)
        if t is None:
            continue
        for a, b in ranges:
            if a < t < b and not a <= 4 * i < b:
                hits.append((4 * i, t))
    return hits


def site_state(img, ra, rb, helper):
    """'stock', 'patched' or a reason string."""
    if (_w(img, rb - 4) & 0xFC000000) != REGION_B_PRE:
        return 'no call before +0x%X' % rb
    a_stock = _match(img, ra, REGION_A_STOCK, helper)
    b_stock = _match(img, rb, REGION_B_STOCK, helper)
    if a_stock and b_stock:
        return 'stock'
    if not a_stock and not b_stock and _w(img, ra) == ldp_w(8, 13, 19, 0x10):
        got_a = [_w(img, ra + 4 * i) for i in range(len(REGION_A_STOCK))]
        calls = [_bl_target(_w(img, rb + 4 * i), rb + 4 * i)
                 for i in range(len(REGION_B_STOCK))]
        t = next((c for c in calls if c is not None and c != helper), None)
        if t is not None:
            got_b = [_w(img, rb + 4 * i) for i in range(len(REGION_B_STOCK))]
            if got_a == region_a(ra, helper) and got_b == region_b(rb, helper, t):
                return 'patched'
    return 'neither stock nor this build\'s patch at +0x%X / +0x%X' % (ra, rb)


def _helper(img, ra):
    i = REGION_A_STOCK.index(BL)
    return _bl_target(_w(img, ra + 4 * i), ra + 4 * i)


def plan(m):
    import cave_space
    import ff7nx_cave
    img = m.img
    patches, notes, problems = [], [], []
    # the helper every stock `bl` in both regions calls (checked below)
    helper = None
    for what, ra, rb in SITES:
        if _bl_target(_w(img, rb + 4 * 4), rb + 4 * 4) is not None:
            helper = _bl_target(_w(img, rb + 4 * 4), rb + 4 * 4)
            break
    if helper is None:
        return [], [], ['no guest-address helper call found']
    todo = []
    for what, ra, rb in SITES:
        st = site_state(img, ra, rb, helper)
        if st == 'patched':
            notes.append('    %s: already patched' % what)
            continue
        if st != 'stock':
            problems.append('%s: %s' % (what, st))
            continue
        todo.append((what, ra, rb))
    if problems or not todo:
        return [], notes, problems
    ranges = [(ra, ra + 4 * len(REGION_A_STOCK)) for _, ra, _ in todo]
    ranges += [(rb, rb + 4 * len(REGION_B_STOCK)) for _, _, rb in todo]
    hits = branches_into(img, len(m.text), ranges)
    if hits:
        return [], [], ['a branch lands inside a region: ' + ', '.join(
            '+0x%X -> +0x%X' % h for h in hits[:4])]
    pool = ff7nx_cave.HolePool(
        img, starts=set(m.arm_starts),
        named=cave_space.named_targets(img[cave_space.RODATA:]))
    try:
        et, wt = ff7nx_cave.emit_laid_out(pool, tail(helper),
                                          span=ff7nx_cave.ANY_SPAN)
    except ff7nx_cave.NoRoom as exc:
        return [], [], ['spit reach tail: %s' % exc]
    placed = dict(wt)
    for what, ra, rb in todo:
        for i, x in enumerate(region_a(ra, helper)):
            placed[ra + 4 * i] = x
        for i, x in enumerate(region_b(rb, helper, et)):
            placed[rb + 4 * i] = x
        notes.append('    %s: /%d -> /%d, descends to the target, first drawn '
                     '%d/%d of the way out' % (what, STOCK_DIV, DIV, LEAD, DIV))
    notes.append('    tail +0x%X (%d words in %d hole(s))'
                 % (et, len(wt), len(pool.used)))
    for va, wd in sorted(placed.items()):
        if _w(img, va) == wd:
            continue
        patches.append({'name': 'spit reach', 'va': hex(va),
                        'expect': struct.pack('<I', _w(img, va)).hex(),
                        'set': struct.pack('<I', wd).hex()})
    return patches, notes, []


def apply_to_nso(src, dest, log=print):
    """Write `src` patched to `dest`. True if dest was written."""
    import nso_patcher
    import nxmap
    m = nxmap.Main(str(src))
    patches, notes, problems = plan(m)
    for n in notes:
        log(n)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  spit reach: nothing written')
        return False
    if not patches:
        return False
    nso = nso_patcher.read_nso(Path(src))
    nso_patcher.apply_spec(nso, {'name': 'spit reach', 'patches': patches})
    fd, tmp = tempfile.mkstemp(dir=str(Path(dest).parent), prefix='.spit-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(dest))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  spit reach: %d word(s)' % len(patches))
    return True


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('src')
    ap.add_argument('dest')
    a = ap.parse_args()
    raise SystemExit(0 if apply_to_nso(a.src, a.dest) else 1)
