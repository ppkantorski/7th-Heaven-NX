#!/usr/bin/env python3
r"""
ff7nx_escapeuv.py -- map Escape's full-frame GPU snapshot onto its mesh.

ONE HOOK, ONE CAVE, AND IT REWRITES THE UVs RATHER THAN THE CODE THAT MAKES
THEM.

THE SYMPTOM
===========
Escape draws the whole screen in its left half and the whole screen again in
its right half. FINDINGS-406 confirmed this by simulation: rendering the mesh
with "each target holds the whole frame" reproduces the reported screenshot
exactly -- doubled landmarks, 4:3 squeeze, black margins.

WHY IT HAPPENS
==============
`escape_setup` asks for two captures that are adjacent HALVES of the frame:

    A = (rect.x,       rect.y, 160, 168)
    B = (rect.x + 160, rect.y, 160, 168)

and `0x682DB2` -- which is a generic object renderer, not a capture routine --
sets that rect as the region and then draws nothing, because Escape's
`field_0` is 0. The target is meant to end up holding just that region.

**RETRACTED (build 408).** This module claimed the port hands BOTH targets the
entire frame. Fitting the simulator to both hardware results instead of one
kills that: build 405 came back ZOOMED 2x, which a whole-frame target forbids.
The surviving model is that the port honours the rect's SIZE but not its
ORIGIN, so both targets hold the same small region at the screen's top-left.

And that makes this module's whole approach unworkable, not just mis-tuned:

    u is a BYTE. One capture is 160 u-units wide. The mesh spans 640 units at
    4:3 and 854 at 16:9. Binding ONE capture for every column can therefore
    never address more than 160 units of screen -- it must zoom, by exactly the
    factor the other capture represented. The two captures exist because one is
    not enough.

That premise is now fixed by ``ff7nx_escapegpu``.  Its logical 256x256 target
contains the complete rendered frame, so this module binds capture A for every
column and maps the full 0..255 UV range across both axes.  Rewriting V is
essential: stock stops at 168 and therefore cannot sample the UI/bottom band.

The mesh then samples u 0..160 of A across its left half and u 0..160 of B
across its right half. Two whole screens, side by side.

WHY THIS FIXES IT WITHOUT DEPENDING ON THAT DEFECT
==================================================
Stop relying on the split. Bind ONE target for every column and address it
across the whole mesh:

    u = col * 4      for col 0..40     ->  0 .. 160 over a 160-texel target

Then it does not matter whether a target holds a half or the whole frame --
the mesh shows exactly one copy either way. The simulator renders this as a
single correct picture (`escape_sim_fix.png`, frame 3).

WHY A CAVE AND NOT FIVE WORD-PATCHES
====================================
The five arithmetic sites that build the UVs cannot be anchored safely:

  * `sub w0, w8, #0x14` appears ~90 times in `escape_uv_init` as the guest
    stack slot `ebp-0x14`, so the `cmp col, 20` is not findable by constant;
  * `ff7nx_guestref` resolves ZERO accesses in this body, because every UV
    store is `base + idx<<7` and constant propagation cannot reach it;
  * and `lsl #3 ; sub #0xA0` -- which looks exactly like the tile-B rebase --
    is actually the FIRST loop computing `x = col*8 - 160`, `y = row*8 - 120`
    for the radial distance. Patching it would have destroyed the ripple and
    left the doubling in place.

So instead of finding the code that computes the UVs, this overwrites the UVs
after they are computed. The record layout is known and checked; nothing
depends on recognising an instruction.

THE RECORDS
===========
840 display-list entries at guest `0xC22460`, stride `0x80`, written by
`escape_uv_init` in `idx = row*40 + col` order (21 rows x 40 columns).

**THE PACKET IS AT record+0x28, NOT record+0.** Build 405 assumed +0 and wrote
its u bytes to +0x0C/+0x14/+0x1C/+0x24 -- three of which the game never writes
at all. It passed its own emulation test (the test checked the model against
itself) and could not work on hardware. The layout was then MEASURED: every
`add w?, w23, #imm` feeding an `idx<<7` address was collected, with w23 proved
to be `0xC2247C` = record + 0x1C. The offsets the game actually touches are

    +0x00  +0x15  +0x24 +0x25  +0x28
    +0x34 +0x35   +0x3C +0x3D   +0x44 +0x45   +0x4C +0x4D

and 0x34/0x3C/0x44/0x4C each take a `strb` from all THREE UV code paths (the
col<20 branch, the col>=20 branch, and the last column) with a `+1` sibling
taking one -- the u and v bytes of a `POLY_FT4`'s four vertices, at the
canonical +0x0C/+0x14/+0x1C/+0x24 of a packet based at +0x28.

    +0x34  u0 v0 clut      +0x3C  u1 v1 tpage
    +0x44  u2 v2           +0x4C  u3 v3

`u` is the low byte and `v` its +1 sibling.  The cave writes both bytes while
leaving CLUT, tpage, colour, geometry and linkage fields untouched:

    u_edge(col) = min(floor(col * 256 / 40), 255)
    v_edge(row) = min(floor(row * 256 / 21), 255)

THE HOOK
========
`escape_uv_init`'s ARM body contains exactly ONE `ret` (+0x83B450). Hooking it
runs the cave after every record has been written, once, with the loop
finished. The cave saves x19-x25 and x30, does its work, restores them, and
then performs the displaced `ret`.

x19-x25 must be saved because at the return the recompiler has already restored
the caller's values into them, and `bl` to the address translator clobbers x30.
The translator itself touches only x0/x8/x9/x10, so nothing else needs saving.
"""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

try:
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
except ImportError:                                          # pragma: no cover
    sys.exit('need capstone:  pip install capstone --break-system-packages')

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import nxmap                                                   # noqa: E402
import a64 as A                                                # noqa: E402

UV_INIT = 0x5D59B0             # guest escape_uv_init
RECORDS = 0xC22460             # guest base of the display list
STRIDE = 0x80                  # bytes per record
ROWS, COLS = 21, 40            # the mesh's quad counts
PACKET = 0x28                  # the POLY_FT4 sits at record+0x28, NOT record+0
UV_OFFS = (PACKET + 0x0C, PACKET + 0x14,      # 0x34, 0x3C  (v0 left, v1 right)
           PACKET + 0x1C, PACKET + 0x24)      # 0x44, 0x4C  (v2 left, v3 right)
U_LEFT = (UV_OFFS[0], UV_OFFS[2])
V_OFFS = tuple(o + 1 for o in UV_OFFS)
UV_SIZE = 256
UV_MAX = 255
TRANSLATE = 0x10FC3A0

ESCAPEUV_ENV = 'SEVENTH_NX_ESCAPE_UV'


def enabled() -> bool:
    """OFF. WITHDRAWN -- the premise was measured to be false.

    This module binds capture A for every column and re-addresses the UVs
    across the whole mesh, on the theory that one target holds the entire
    frame. `ff7nx_escapegpu`'s header now carries the measurement:

      * the graphics mode `[0x9ACB5C]` is 2 -- the registry default set by
        sub_404D80, and the value Kujata's rect builder at x86 0x500858
        tests to reach the 256x256 that `ff7nx_fbcapture`'s hardware-
        confirmed black-tile chain is built on;
      * so `escape_setup` takes its mode-2 arm: A = (0,0,160,168) and
        B = (320,0,160,168), both at xscale 2;
      * a mesh UV lands on staging column `fb_tex.x + u * xscale`, so A
        covers staging 0..320 and B covers 320..640 -- the two tiles tile
        the whole frame, exactly as FINDINGS-313 measured for the swirl.

    Each target therefore holds HALF the frame, and the stock UVs are already
    correct. Binding one target for all 40 columns can only ever show half
    the screen stretched across the whole mesh, which is what builds 405 and
    407 came back as ("stretched wide, zoomed in"). No UV formula fixes that.

    The doubling was never in the UVs: it is `ff7nx_fbwindow` sliding capture
    B's origin from staging 320 to 128, because `fb_tex.w` is `xscale * POT(160)`
    = 512 and overhangs the 640-column surface. `ff7nx_escapegpu` fixes it.

    Kept, off, because the record-layout measurement in here is sound and
    reusable: the POLY_FT4 sits at record+0x28 and its UV bytes at +0x34/
    +0x3C/+0x44/+0x4C with `v` in each +1 sibling. Anything that ever needs
    to rewrite these UVs -- a vertical extension, or closing the two-column
    seam the stock `u = 159` last column leaves at dead centre -- should start
    from that and from the hook on `escape_uv_init`'s single `ret`.
    """
    v = os.environ.get(ESCAPEUV_ENV)
    if v is not None:
        return v.strip().lower() not in ('', '0', 'off', 'false', 'no')
    return False


def csel(rd, rn, rm, cond):
    """CSEL Wd, Wn, Wm, cond  ->  Wd = cond ? Wn : Wm."""
    return 0x1A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd


COND_GT = 0xC

# register plan
IDX, ROW, COL, REC, BASE, UL, UR, VT, VB, TMP = range(19, 29)
SAVED = (IDX, ROW, COL, REC, BASE, UL, UR, VT, VB, TMP, 30)
FRAME = 0x60                   # 11 saved x-registers, 16-byte aligned


def expected_u(col):
    """The model: what the two u bytes of column `col` must become."""
    return (col * UV_SIZE // COLS,
            min((col + 1) * UV_SIZE // COLS, UV_MAX))


def expected_v(row):
    """The model: what the two v bytes of row `row` must become."""
    def edge(r):
        y = r * 16
        if y > 240:
            y = (5 * y - 720) // 2
        return min(y * UV_SIZE // 480, UV_MAX)
    return edge(row), edge(row + 1)


def build(entry_va, addr, displaced, return_va):
    """The cave body. `addr(i)` is the final address of word i."""
    w = []

    def at(i):
        return addr(i)

    # ---- prologue: save what we use ---------------------------------
    w.append(A.sub_imm64(31, 31, FRAME))
    for n, r in enumerate(SAVED):
        w.append(A.str64(r, 31, n * 8))
    w.append(A.movz(IDX, 0))
    w.append(A.movz(ROW, 0))
    w.append(A.movz(BASE, RECORDS & 0xFFFF))
    w.append(A.movk_hi(BASE, RECORDS >> 16))

    outer = len(w)
    w.append(A.movz(COL, 0))

    inner = len(w)
    w.append(A.lsl(REC, IDX, 7))                  # idx * 0x80
    w.append(A.add_reg(REC, REC, BASE))           # record guest address
    # U edges: floor(col*256/40), last edge clamped to byte 255.
    w.append(A.movz(TMP, UV_SIZE))
    w.append(A.mul(UL, COL, TMP))
    w.append(A.movz(9, COLS))
    w.append(A.udiv(UL, UL, 9))
    w.append(A.add_imm(UR, COL, 1))
    w.append(A.mul(UR, UR, TMP))
    w.append(A.udiv(UR, UR, 9))
    w.append(A.movz(9, UV_MAX))
    w.append(A.cmp_reg(UR, 9))
    w.append(csel(UR, 9, UR, COND_GT))
    # V follows the center-anchored mesh geometry, not uniform row number.
    # That makes screen-y / texture-v linear and keeps the captured picture
    # undistorted even though the old mesh had too few rows below y=240.
    for dst, add in ((VT, 0), (VB, 1)):
        if add:
            w.append(A.add_imm(dst, ROW, add))
            w.append(A.lsl(dst, dst, 4))
        else:
            w.append(A.lsl(dst, ROW, 4))          # stock final y = row*16
        w.append(A.cmp_imm(dst, 240))
        w.append(A.movz(8, 5))
        w.append(A.mul(8, dst, 8))
        w.append(A.sub_imm(8, 8, 720))
        w.append(A.asr(8, 8, 1))
        w.append(A.csel(dst, dst, 8, A.LE))
        w.append(A.mul(dst, dst, TMP))
        w.append(A.movz(9, 480))
        w.append(A.udiv(dst, dst, 9))
        w.append(A.movz(9, UV_MAX))
        w.append(A.cmp_reg(dst, 9))
        w.append(csel(dst, 9, dst, COND_GT))

    # Translate the record once, then write all four (u,v) pairs.
    w.append(A.mov_reg(0, REC))
    w.append(0)                                   # bl TRANSLATE -- fixed below
    for off, u, v in ((UV_OFFS[0], UL, VT), (UV_OFFS[1], UR, VT),
                      (UV_OFFS[2], UL, VB), (UV_OFFS[3], UR, VB)):
        w.append(A.strb(u, 0, off))
        w.append(A.strb(v, 0, off + 1))

    w.append(A.add_imm(IDX, IDX, 1))
    w.append(A.add_imm(COL, COL, 1))
    w.append(A.cmp_imm(COL, COLS))
    w.append(0)                                   # b.lt inner
    lt_inner = len(w) - 1
    w.append(A.add_imm(ROW, ROW, 1))
    w.append(A.cmp_imm(ROW, ROWS))
    w.append(0)                                   # b.lt outer
    lt_outer = len(w) - 1

    # ---- epilogue: restore, then the displaced instruction -----------
    for n, r in enumerate(SAVED):
        w.append(A.ldr64(r, 31, n * 8))
    w.append(A.add_imm64(31, 31, FRAME))
    w.append(displaced)
    w.append(0)                                   # b back

    # ---- patch the PC-relative words now the layout is known ---------
    out = list(w)
    for i, word in enumerate(out):
        if word != 0:
            continue
        if i == len(out) - 1:
            out[i] = A.b(at(i), return_va)
        elif i == lt_inner:
            out[i] = A.bcond(at(i), at(inner), 0xB)     # b.lt
        elif i == lt_outer:
            out[i] = A.bcond(at(i), at(outer), 0xB)     # b.lt
        else:
            out[i] = A.bl(at(i), TRANSLATE)
    return out


ESCAPE_MESH = 0x5D602A         # the body holding both draw blocks
A_HANDLE = 0x8FF0E8            # capture A's slot-id global; B is A + 4
ESCAPE_SETUP = 0x5D577B        # where the capture scale is chosen


# NOTE: build 405 carried a `find_scale` here that located the dword at
# ebp-4 in escape_setup's battle arm and flipped it from 1 to the default
# arm's 2, calling it a "capture scale". It is not one -- see the descriptor
# layout above and FINDINGS-407. The function is gone rather than left
# unused, so it cannot be re-wired by someone reading only its name.


def _md():
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = False
    return md


def find_binds(m, md=None, imm=4):
    """The two `add w?, <A-handle reg>, #4` that select capture B.

    Each draw block reaches B by adding 4 to the A handle's address. Anchored
    on a register PROVED to hold 0x8FF0E8 -- either materialised in the two
    instructions before, or hoisted earlier in the body and not rewritten
    since. Exactly two must be found or we refuse.
    """
    md = md or _md()
    lo, hi = m.extent(ESCAPE_MESH)
    ins = list(md.disasm(bytes(m.img[lo:hi]), lo))
    lo16, hi16 = A_HANDLE & 0xFFFF, A_HANDLE >> 16
    holds = {}                       # register -> True while it holds A_HANDLE
    out = []
    skip = -1
    for k, i in enumerate(ins):
        if k == skip:
            continue                 # the movk of a materialisation we took
        # a materialisation: mov r,#lo ; movk r,#hi
        if (i.mnemonic in ('mov', 'movz') and k + 1 < len(ins)
                and ins[k + 1].mnemonic == 'movk'):
            try:
                v = int(i.op_str.rsplit('#', 1)[1], 0)
            except ValueError:
                v = None
            rd = i.op_str.split(',')[0].strip()
            if v == lo16 and ('#%#x' % hi16) in ins[k + 1].op_str \
                    and ins[k + 1].op_str.split(',')[0].strip() == rd:
                holds[rd] = True
                skip = k + 1         # do not let the movk invalidate it
                continue
        if i.mnemonic == 'add':
            parts = [x.strip() for x in i.op_str.split(',')]
            # A BIND computes the B handle's address into w0 and immediately
            # hands it to the translator. `add w19, w19, #4` in the prologue
            # advances the register itself and is NOT a bind -- requiring
            # dst == w0 and a `bl` next separates them exactly.
            if (len(parts) == 3 and parts[2] == '#%d' % imm
                    and holds.get(parts[1])
                    and parts[0] == 'w0' and parts[0] != parts[1]
                    and k + 1 < len(ins) and ins[k + 1].mnemonic == 'bl'
                    and int(ins[k + 1].op_str.lstrip('#'), 0) == TRANSLATE):
                out.append(i.address)
                holds.pop(parts[0], None)
                continue
        # any write to a register invalidates what it held
        dst = i.op_str.split(',')[0].strip() if i.op_str else ''
        if dst.startswith('w') or dst.startswith('x'):
            if i.mnemonic not in ('cmp', 'cmn', 'str', 'strb', 'strh', 'stp',
                                  'tst', 'b', 'bl', 'ret'):
                holds.pop(dst, None)
                holds.pop('x' + dst[1:], None)
                holds.pop('w' + dst[1:], None)
    return out


def find_hook(m, md=None):
    """(hook_va, kind) or (None, why).

    The hook is the LAST non-padding word of `escape_uv_init`'s body. Unhooked
    that is the body's single `ret`; hooked it is our branch into the cave. It
    is locatable in both states, which revert needs.
    """
    md = md or _md()
    lo, hi = m.extent(UV_INIT)
    va = None
    for a in range(hi - 4, lo - 1, -4):
        if struct.unpack_from('<I', m.img, a)[0] != 0:
            va = a
            break
    if va is None:
        return None, 'escape_uv_init body is empty'
    i = next(md.disasm(bytes(m.img[va:va + 4]), va), None)
    if i is None:
        return None, 'undecodable word at the end of escape_uv_init'
    if i.mnemonic == 'ret':
        rets = [j.address for j in md.disasm(bytes(m.img[lo:hi]), lo)
                if j.mnemonic == 'ret']
        if len(rets) != 1 or rets[0] != va:
            return None, ('expected exactly one `ret`, at the end; found %d'
                          % len(rets))
        return (va, 'ret'), None
    if i.mnemonic == 'b':
        return (va, 'hooked'), None
    return None, ('escape_uv_init ends with `%s`, not `ret` or a hook branch'
                  % i.mnemonic)


def walk_cave(m, hook, md=None, limit=200):
    """Recover the cave's words from a patched image, for an exact revert."""
    md = md or _md()
    first = next(md.disasm(bytes(m.img[hook:hook + 4]), hook), None)
    if first is None or first.mnemonic != 'b':
        return None, 'hook +0x%X is not a branch' % hook
    va = int(first.op_str.lstrip('#'), 0)
    out = {}
    for _ in range(limit):
        if va in out:
            return None, 'cave loops at +0x%X' % va
        w = struct.unpack_from('<I', m.img, va)[0]
        i = next(md.disasm(bytes(m.img[va:va + 4]), va), None)
        if i is None:
            return None, 'undecodable cave word at +0x%X' % va
        out[va] = w
        if i.mnemonic == 'b':
            tgt = int(i.op_str.lstrip('#'), 0)
            if tgt == hook + 4:
                return out, None
            # an internal back-edge stays inside the cave; only follow forward
            # links we have not seen, so the loop body is walked once
            va = tgt if tgt not in out else va + 4
        else:
            va += 4
    return None, 'cave did not terminate within %d words' % limit


def plan(m, revert=False, md=None, pool=None):
    import ff7nx_cave as C
    md = md or _md()
    found, why = find_hook(m, md)
    if found is None:
        return [], [], [why]
    hook, kind = found

    if revert:
        binds = find_binds(m, md, imm=0)
        if kind == 'ret':
            return [], [], []                 # not applied
        cave, why2 = walk_cave(m, hook, md)
        if cave is None:
            return [], [], [why2]
        # the displaced instruction is the one `ret` the cave carries
        rets = [w for _va, w in sorted(cave.items())
                if (next(md.disasm(struct.pack('<I', w), 0), None) or
                    _Dummy()).mnemonic == 'ret']
        if len(rets) != 1:
            return [], [], ['expected one `ret` in the cave, found %d'
                            % len(rets)]
        ps = [{'name': 'escape uv: unhook', 'va': hex(hook),
               'expect': struct.pack(
                   '<I', struct.unpack_from('<I', m.img, hook)[0]).hex(),
               'set': struct.pack('<I', rets[0]).hex()}]
        for va in sorted(cave):
            ps.append({'name': 'escape uv: clear cave word +0x%x' % va,
                       'va': hex(va),
                       'expect': struct.pack('<I', cave[va]).hex(),
                       'set': '00000000'})
        if len(binds) != 2:
            return [], [], ['expected 2 bound-A selects to restore, found %d'
                            % len(binds)]
        for va in binds:
            w = struct.unpack_from('<I', m.img, va)[0]
            ps.append({'name': 'escape uv: restore B select at +0x%x' % va,
                       'va': hex(va),
                       'expect': struct.pack('<I', w).hex(),
                       'set': struct.pack('<I', w | (4 << 10)).hex()})
        return ps, ['  escape UVs removed @ +0x%07X (%d cave word(s) returned '
                    'to the pool)' % (hook, len(cave))], []

    if kind == 'hooked':
        return [], ['  escape UVs already installed @ +0x%07X' % hook], []
    binds = find_binds(m, md, imm=4)
    if len(binds) != 2:
        return [], [], ['expected exactly 2 capture-B selects, found %d'
                        % len(binds)]
    displaced = struct.unpack_from('<I', m.img, hook)[0]
    pool = pool or C.HolePool(m.img, starts=set(m.arm_starts))
    entry, out = C.emit_laid_out(
        pool, lambda entry_va, addr: build(entry_va, addr, displaced, hook + 4))
    patches = []
    for va in sorted(out):
        old = struct.unpack_from('<I', m.img, va)[0]
        if old != 0:
            return [], [], ['cave word +0x%X is not padding (0x%08X)'
                            % (va, old)]
        patches.append({'name': 'escape uv +0x%x' % va, 'va': hex(va),
                        'expect': '00000000',
                        'set': struct.pack('<I', out[va]).hex()})
    patches.append({'name': 'escape uv: hook the return', 'va': hex(hook),
                    'expect': struct.pack('<I', displaced).hex(),
                    'set': struct.pack('<I', A.b(hook, entry)).hex()})
    for va in binds:
        w = struct.unpack_from('<I', m.img, va)[0]
        patches.append({'name': 'escape uv: bind A at +0x%x' % va,
                        'va': hex(va),
                        'expect': struct.pack('<I', w).hex(),
                        'set': struct.pack('<I', w & ~(0xFFF << 10)).hex()})
    # NOTE: build 405 also flipped the dword at ebp-4 (battle 1 -> the default
    # 2) on the theory that it was a capture scale. It is NOT. The prologue at
    # +0x83B4C4.. shows the stack object is TWO RECTS plus that dword:
    #
    #   ebp-0x14 A.x   -0x12 A.y   -0x10 A.w=0xA0   -0x0E A.h=0xA8
    #   ebp-0x0C B.x   -0x0A B.y   -0x08 B.w=0xA0   -0x06 B.h=0xA8
    #   ebp-0x04 <dword, default 2; the battle arm overrides it to 1>
    #
    # and the `mov w20,#2` the patch copied from is the DEFAULT arm's value for
    # that same field, not "the same field in another mode". The field's meaning
    # is not established, so it is not touched. See FINDINGS-407.
    notes = ['  Escape UVs span 0..255 across %dx%d quads; capture A bound '
             'for every column -- one full frame including the UI'
             % (COLS, ROWS),
             '  hook +0x%07X (the body\'s only `ret`), cave entry +0x%X, '
             '%d cave word(s) + 2 bind word(s)' % (hook, entry, len(out))]
    return patches, notes, []


class _Dummy(object):
    mnemonic = ''


def apply(main, revert=False, log=print) -> int:
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! escape uv: %s' % p)
        log('  refusing to touch the Escape UVs.')
        return 1
    for n in notes:
        log(n)
    if not patches:
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_escapeuv', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.escuv-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args(argv)
    return apply(a.main, revert=a.revert)


if __name__ == '__main__':
    raise SystemExit(main())
