#!/usr/bin/env python3
"""
ff7nx_campreserve.py -- which one-shot SCR2D placements a zero-travel field
keeps. BUILD 533.

THE REGRESSION
==============
BUILD 513 changed `ff7nx_camclamp`'s initializer clamp from `b.gt` to
`b.ge`: a field whose clamp admits exactly one camera position (lo == hi)
skips the clamp, so its authored SCR2D placement survives. That was for
`southmk2`, whose SCR2D (+32, -32) is the anchor its MVCAM movies are
composited against (FINDINGS-495/496, BUILD 511-513).

It was applied to ALL 375 zero-travel fields. Sixteen of them place the
camera off-centre with SCR2D, and in the fourteen that are not movie-camera
fields the placement was never meant to survive widescreen -- FFNx clamps it
in `field_init_scripted_bg_movement` mode 4 for every wide field:

    tin_1   pinned [0, 0]   SCR2D x = 18     black bar left, art cut right
    tin_2   pinned [0, 0]   SCR2D x = 4
    tin_3   pinned [0, 0]   SCR2D x = 16     (the reported train fields)
    ealin_1 / ealin_2 / ealin_12, mkt_s3, min51_2, nivl_b1, sininb31,
    sininb35, trnad_53, utapb, zcoal_1      the same class, not yet reported

(Measured over the shipped flevel: art for the tin fields is exactly the
428-unit window, -214..214, so any camera other than 0 shows black on one
side and cuts the other.)

THE RULE
========
Keep the authored placement in a zero-travel field ONLY when the field drives
a movie camera -- it executes MVCAM -- because only there is the SCR2D value
a movie anchor (the stock MVCAM path re-anchors the field from exactly that
delta every frame, FINDINGS-496). Everywhere else, clamp, as FFNx does and as
this build did before 513. Fields with real travel (lo < hi) are clamped
exactly as before; lo > hi is still skipped (never occurs on x).

`MVCAM_FIELDS` is the union of the fields that execute MVCAM in the vanilla
archive and in the shipped (Echo-S) archive, measured with field_dis over
all 711 fields of each. Of them only `southmk2` and `white2` place a
zero-travel camera off-centre, so for every other field the behaviour is
unchanged from BUILD 513 or returns to pre-513.

HOW
===
`ff7nx_camclamp` installs its initializer cave in the padding pool, where no
extra word fits any more. This pass leaves that cave in place, verifies it is
the current revision, and re-points the one hook branch (+0x9F7AF4) at a
copy in the SHIP part of `ff7nx_deadspace`, identical except for the x-leg
guard, plus a field-id bitmap (maplist index, read from guest 0xCC15D0 --
the same id `echo_s_tutorial` reads during field scripts).
"""
from __future__ import annotations

import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_camclamp as K
import ff7nx_deadspace as DS

ENV = 'SEVENTH_NX_CAM_PRESERVE'          # `off` = leave BUILD 513's rule
CURRENT_FIELD_ID = 0xCC15D0              # u16, maplist index

MVCAM_FIELDS = frozenset((
    # vanilla and shipped both
    'hill2', 'junair', 'las4_2', 'las4_3', 'las4_4', 'las4_42', 'lastmap',
    'md8_52', 'nrthmk', 'southmk2', 'trnad_51', 'white2',
    # vanilla only
    'blackbg2', 'cargoin', 'fship_4', 'ncorel3', 'niv_ti3',
    # shipped (Echo-S) only
    'junpb_1', 'kuro_9', 'mrkt2',
))

LT, GT = 11, 12


def enabled():
    return os.environ.get(ENV, '').strip().lower() not in ('off', '0', 'no',
                                                           'stock')


def bitmap(maplist, names=MVCAM_FIELDS):
    """One bit per maplist index; 1 = keep the authored zero-travel SCR2D."""
    bits = bytearray((len(maplist) + 7) // 8)
    for fid, name in enumerate(maplist):
        if name in names:
            bits[fid >> 3] |= 1 << (fid & 7)
    return bytes(bits)


def ldrb_reg(rt, rn, rm):
    """ldrb Wt, [Xn, Wm, UXTW]"""
    return 0x38604800 | (rm << 16) | (rn << 5) | rt


def lsrv(rd, rn, rm):
    return 0x1AC02400 | (rm << 16) | (rn << 5) | rd


def init_cave(addr, bitmap_at, n_ids, vertical=True):
    """ff7nx_camclamp.init_cave_words with the x-leg lo == hi case decided by
    the field bitmap. Registers as the stock cave: x0, x8-x10, x21, x22,
    x30; x20 (the guest context) is never touched."""
    w = []

    def here():
        return addr(len(w))

    lab = {}
    fix = []

    def br(kind, name, *extra):
        fix.append((len(w), kind, name, extra))
        w.append(0)

    w += K._header_ptr(addr, 0, None)[:4]                 # x0 = guest header
    br('cbz', 'skip', 0)                                  # no field loaded
    w.append(A.bl(here(), K.XLAT))                        # -> host header
    w += [A.ldrsh(22, 0, K.RANGE_RIGHT), A.ldrsh(21, 0, K.RANGE_LEFT),
          A.sub_imm(22, 22, K.HALF_W), A.add_imm(21, 21, K.HALF_W),
          A.cmp_reg(21, 22)]
    br('bcond', 'skip', GT)                               # lo > hi: nothing
    br('bcond', 'clamp', LT)                              # real travel
    # lo == hi: a constant. Keep the authored value only in MVCAM fields.
    w += [A.movz(0, CURRENT_FIELD_ID & 0xFFFF),
          A.movk_hi(0, CURRENT_FIELD_ID >> 16)]
    w.append(A.bl(here(), K.XLAT))
    w.append(A.ldrh(8, 0, 0))
    w.append(A.cmp_imm(8, n_ids))
    br('bcond', 'clamp', 2)                               # hs: unknown id
    w.append(A.adr(9, here(), bitmap_at))
    w.append(A.lsr(10, 8, 3))
    w.append(ldrb_reg(10, 9, 10))
    w.append(A.and_mask(8, 8, 3))
    w.append(lsrv(10, 10, 8))
    br('tbnz', 'skip', 0)
    lab['clamp'] = len(w)
    w += [A.movz(0, K.DELTA_X & 0xFFFF), A.movk_hi(0, K.DELTA_X >> 16)]
    w.append(A.bl(here(), K.XLAT))
    w += [A.ldrsh(8, 0, 0), A.sub_reg(8, 31, 8)]
    w += K._init_clamp_tail(addr, len(w))
    if vertical:
        i0 = len(w)
        w += K._header_ptr(addr, i0)                      # 5
        i0 = len(w)
        n_skip_fix = len(w)
        w += K._init_vclip_gate(addr, i0, addr(0))        # patched below
        fix.append((n_skip_fix + 1, 'cbz', 'skip', (22,)))
        i0 = len(w)
        blk = K._clamp_block_abs(addr, i0, addr(0), K.DELTA_Y,
                                 K.RANGE_TOP, K.RANGE_BOTTOM, K.HALF_H)
        w += blk
        fix.append((i0 + 5, 'bcond', 'skip', (K.COND_GE,)))
        w += K._init_clamp_tail(addr, len(w))
    lab['skip'] = len(w)
    w.append(A.b(here(), K.INIT_RETURN_VA))
    for i, kind, name, extra in fix:
        frm, to = addr(i), addr(lab[name])
        if kind == 'bcond':
            w[i] = A.bcond(frm, to, extra[0])
        elif kind == 'cbz':
            w[i] = A.cbz(extra[0], frm, to)
        elif kind == 'tbnz':
            w[i] = A.tbnz(10, extra[0], frm, to)
    return w


def apply_to_nso(src, dest, maplist, stock):
    import nxmap
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    t = bytes(text)
    vertical = None
    for v in (True, False):
        if K.init_cave_current(t, v):
            vertical = v
            break
    if vertical is None:
        raise ValueError('ff7nx_camclamp\'s initializer cave at +0x%X is not '
                         'the current revision -- refusing to redirect it'
                         % K.INIT_HOOK_VA)
    lo, hi = DS.part(src, 'ship', stock)
    pool = DS.Bump(lo, hi)
    bits = bitmap(maplist)
    # the bitmap goes after the cave; lay the cave out twice to learn its size
    n = len(init_cave(lambda i: 4 * i, 4 * 256, len(maplist), vertical))
    rel = ((4 * n + 15) & ~15)           # the bitmap sits right after the cave
    bitmap_at = pool.at + rel
    entry = pool.put(lambda e, ad: init_cave(ad, ad(0) + rel, len(maplist),
                                             vertical))
    got = pool.put_bytes(bits)
    if got != bitmap_at:
        raise ValueError('bitmap landed at +0x%X, cave expected +0x%X'
                         % (got, bitmap_at))
    for va, w in pool.placed.items():
        struct.pack_into('<I', text, va, w)
    struct.pack_into('<I', text, K.INIT_HOOK_VA, A.b(K.INIT_HOOK_VA, entry))
    out = AC.pack(blob, [bytes(text), raw[1], raw[2]], 0)
    with open(dest, 'wb') as handle:
        handle.write(out)
    preserved = sorted(n for n in maplist if n in MVCAM_FIELDS)
    return {'entry': entry, 'words': n, 'bitmap': bitmap_at,
            'vertical': vertical, 'preserved': preserved}
