#!/usr/bin/env python3
"""ff7nx_subparallax.py -- layers 3 and 4 scroll in sub-unit steps, as on PC.

BUILD 620j. anfrst_4 (hardware 10-07, again on 620i): "if i walk slow ...
the beams traverse slightly rigidly ... like its taking less steps than it
should be". FIELD-TRACKER 2h: 22 fields.

WHAT THE ENGINE DOES (x86 0x644075 = FFNx's field_update_background_positions,
ARM +0x9F9000; both of its camera paths)

    t        = speed * delta                 (header +0x28/+0x2A layer 3,
                                              +0x2C/+0x2E layer 4)
    local    = sbfx(t, 8, 16) + (pos >> 4)   = floor(t / 256) + floor(pos / 16)
    local   %= width
    bg       = 320 - bg_offset - shake + local      -> guest 0xCC1618..0xCC1624

and the pick functions (layer 3 x86 0x640F95 = +0xA07780, layer 4 x86
0x641358 = +0xA08630) draw every tile at (tile + (320 - bg) * mult) as a
float. The camera moves one unit per two frames when Cloud walks, so a
half-speed layer moved one unit every FOUR frames: the stepping.

FFNx (background.cpp, set_world_and_background_positions) computes the same
position in float: pos / 16.f + speed * delta / 256.f. That is the PC look.

WHAT THIS DOES
  * Each of the 8 `sbfx w8, w8, #8, #16` (2 camera paths x layer 3/4 x x/y)
    calls a 4-word cave that saves the product's low byte -- exactly the
    fraction the shift drops -- in a 4-byte BSS block, then shifts.
  * Each of the 4 float stores of a tile's x / y before add_page_tile
    (`str s8, [x0]`: layer 3 +0xA08500 y, +0xA08590 x; layer 4 +0xA09488 y,
    +0xA09518 x) calls a straight-line cave that subtracts
    ((byte + (pos & 15) * 16) * mult) / 256 from s8 and stores it: the tile
    lands where pos/16 + speed*delta/256 puts it, as on PC.
The integer position still drives the port's wrap and cull. A shift of less
than one unit cannot expose an edge: the port wraps layer 3/4 tiles at
bg-459 / bg+107 (x) and bg-264 / bg+16 (y), at least 8 units outside the
visible -373.5..53.5 / -232..8 (ff7nx_framesim, ff7nx_paraudit.PORT).

Register contract: the sbfx sites sit in a function that saved x30 and the
caves touch only x16 and w8 (as texscale does with w16/w17). The store sites
are followed, within a few words, by the call to add_page_tile; nothing in
between reads x9..x17 or v1 before writing them (checked by tests). The
pick caves keep what must survive the translator (+0x10FC3A0; contract:
x0..x18 and x30 clobbered) in x19..x21, saved with x30 on the stack. The
translator never returns NULL for a non-zero address (an unmapped page reads
a dummy page), so the pick caves need no branch.

SEVENTH_NX_SUBPARALLAX=0 leaves the module stock.
"""
from __future__ import annotations

import os
import shutil
import struct
import tempfile
from pathlib import Path

import a64 as A

ENV = 'SEVENTH_NX_SUBPARALLAX'
TRANSLATE = 0x10FC3A0
HEADER_PTR = 0xCFF454          # guest: field_triggers_header (pointer)
MULT = 0xCFF1F0                # guest: field_bg_multiplier (int)
BSS_BYTES = 4                  # one byte per (layer, axis)

SBFX = 0x13085D08              # sbfx w8, w8, #8, #16
STR_S8 = 0xBD000008            # str s8, [x0]

# (site, slot) -- slot 0 L3x, 1 L3y, 2 L4x, 3 L4y; the header speed offset
# read just before each site is 0x28 + 2 * slot (checked in check_sites)
CAPTURE = ((0x9F94D8, 0), (0x9F955C, 1), (0x9F96B4, 2), (0x9F9734, 3),
           (0x9FA648, 0), (0x9FA6C8, 1), (0x9FA820, 2), (0x9FA8A0, 3))
# (site, slot, header offset of the layer position on that axis)
PICK = ((0xA08500, 1, 0x22), (0xA08590, 0, 0x20),
        (0xA09488, 3, 0x26), (0xA09518, 2, 0x24))
# the add_page_tile call each store feeds (register-liveness window)
PICK_CALL = {0xA08500: 0xA085A0, 0xA08590: 0xA085A0,
             0xA09488: 0xA09528, 0xA09518: 0xA09528}
ADD_PAGE_TILE = 0xA06870


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _scvtf_fix8(rd, rn):
    """SCVTF Sd, Wn, #8 (scalar, fixed point: the integer / 256). The value
    is (0..495) * mult: never negative, so signed is exact."""
    return 0x1E020000 | ((64 - 8) << 10) | (rn << 5) | rd


def capture_body(addr, bss, slot):
    """adrp x16, bss ; strb w8, [x16, #lo+slot] ; sbfx ; ret"""
    lo = (bss & 0xFFF) + slot
    return [A.adrp(16, addr(0), bss & ~0xFFF),
            A.strb(8, 16, lo),
            SBFX,
            A.ret()]


def pick_body(addr, bss, slot, pos_off):
    """Straight-line. Values that must survive the translator are kept in
    x19..x21, saved with x30 on the stack (the translator's contract is
    x0..x18 and x30 clobbered -- ff7nx_audio_cave.guest_ptr)."""
    w = []
    pc = lambda: addr(len(w))
    SP = 31
    w.append(A.stp64_pre(19, 20, SP, -32))
    w.append(A.stp64_off(21, 30, SP, 16))
    w.append(A.mov_reg64(19, 0))                    # host slot of the float
    w.append(A.movz(0, HEADER_PTR & 0xFFFF))
    w.append(A.movk_hi(0, HEADER_PTR >> 16))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldr(20, 0, 0))                       # guest header
    w.append(A.add_imm(0, 20, pos_off))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldrh(20, 0, 0))                      # layer position (x16)
    w.append(A.movz(0, MULT & 0xFFFF))
    w.append(A.movk_hi(0, MULT >> 16))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldr(21, 0, 0))                       # bg multiplier
    w.append(A.adrp(9, pc(), bss & ~0xFFF))
    w.append(A.ldrb(14, 9, (bss & 0xFFF) + slot))   # speed*delta & 0xFF
    w.append(A.and_mask(20, 20, 4))                 # pos & 15
    w.append(A.lsl(20, 20, 4))                      # ... * 16
    w.append(A.add_reg(14, 14, 20))                 # fraction * 256
    w.append(A.mul(14, 14, 21))                     # ... * mult
    w.append(_scvtf_fix8(1, 14))                    # s1 = that / 256
    w.append(A.fsub_s(8, 8, 1))                     # the tile moves with it
    w.append(A.str_s(8, 19, 0))                     # the displaced store
    w.append(A.mov_reg64(0, 19))
    w.append(A.ldp64_off(21, 30, SP, 16))
    w.append(A.ldp64_post(19, 20, SP, 32))
    w.append(A.ret())
    return w


def check_sites(img):
    """[] when every site is stock and the surrounding code is what this
    module was written against; else a list of problems."""
    bad = []
    for site, slot in CAPTURE:
        if _w(img, site) != SBFX:
            bad.append('+0x%X is %08X, not sbfx' % (site, _w(img, site)))
            continue
        want = A.add_imm(0, 8, 0x28 + 2 * slot)
        if not any(_w(img, site - 4 * k) == want for k in range(1, 12)):
            bad.append('+0x%X: no header +0x%X read before it'
                       % (site, 0x28 + 2 * slot))
    for site, _slot, _off in PICK:
        if _w(img, site) != STR_S8:
            bad.append('+0x%X is %08X, not str s8, [x0]'
                       % (site, _w(img, site)))
        call = PICK_CALL[site]
        w = _w(img, call)
        if (w >> 26) != 0x25 or call + 4 * (((w & 0x3FFFFFF) ^ 0x2000000)
                                          - 0x2000000) != ADD_PAGE_TILE:
            bad.append('+0x%X is not bl add_page_tile' % call)
        bad += liveness(img, site + 4, call)
    return bad


def liveness(img, lo, hi):
    """Problems if any instruction in [lo, hi) reads x8..x18 or v1 (which
    the pick caves clobber) before writing it."""
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    md.detail = True

    def key(r):
        n = md.reg_name(r)
        if n[0] in 'wx' and n[1:].isdigit():
            return 'x' + n[1:]
        if n[0] in 'bhsdqv' and n[1:].isdigit():
            return 'v' + n[1:]
        return n
    clob = {'x%d' % i for i in range(8, 19)} | {'v1'}
    written = set()
    bad = []
    for ins in md.disasm(bytes(img[lo:hi]), lo):
        rd, wr = ins.regs_access()
        for r in rd:
            k = key(r)
            if k in clob and k not in written:
                bad.append('+0x%X %s %s reads %s first'
                           % (ins.address, ins.mnemonic, ins.op_str, k))
        written |= {key(r) for r in wr}
    return bad


def build_patches(img, starts, bss, log=print):
    import ff7nx_cave
    bad = check_sites(img)
    if bad:
        for b in bad:
            log('  ! subparallax: ' + b)
        return None
    pool = ff7nx_cave.HolePool(bytearray(img), starts=starts)
    words = {}
    n = 0
    for site, slot in CAPTURE:
        e, w = ff7nx_cave.emit_laid_out(
            pool, lambda _e, ad, s=slot: capture_body(ad, bss, s),
            span=ff7nx_cave.ANY_SPAN)
        words.update(w)
        words[site] = A.bl(site, e)
        n += len(w)
    for site, slot, off in PICK:
        e, w = ff7nx_cave.emit_laid_out(
            pool, lambda _e, ad, s=slot, o=off: pick_body(ad, bss, s, o),
            span=ff7nx_cave.ANY_SPAN)
        words.update(w)
        words[site] = A.bl(site, e)
        n += len(w)
    log('  subparallax: 12 sites, %d words in verified padding, BSS +%#x'
        % (n, bss))
    return words


def installed(img):
    return all((_w(img, s) >> 26) == 0x25 for s, _ in CAPTURE)


def apply(main, log=print):
    import ff7nx_audio_cave as AC
    import nxmap
    main = Path(main)
    m = nxmap.Main(str(main))
    if installed(m.img):
        log('  subparallax: already installed')
        return 0
    blob = main.read_bytes()
    segs, raw = AC.segments(blob)
    bss = AC.scratch_base(blob, segs)
    words = build_patches(m.img, set(m.arm_starts), bss, log)
    if words is None:
        log('  subparallax: sites do not match -- NOT applied')
        return 1
    text = bytearray(raw[0])
    for va, word in words.items():
        struct.pack_into('<I', text, va, word)
    out = AC.pack(blob, [bytes(text), raw[1], raw[2]],
                  BSS_BYTES + AC.bss_tail_slack(segs))
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.subpar-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(out)
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  subparallax (BUILD 620j): layers 3/4 keep the fraction of '
        'speed*camera/256 the engine drops -- they scroll in sub-unit steps '
        'as on PC. %s=0 disables.' % ENV)
    return 0
