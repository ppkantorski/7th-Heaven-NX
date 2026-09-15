#!/usr/bin/env python3
r"""
ff7nx_escapetall.py -- make the Escape ripple cover the whole 480, sampling
included, with its rings still centred on the middle of the screen.

THREE IN-PLACE WORDS PLUS TWO, AND A THREE-WORD CAVE.

WHAT BUILD 409 LEFT
===================
`ff7nx_escapegpu` + `ff7nx_escapescale` give one undistorted snapshot across
the full 16:9 width, 1:1, with the ripple centred -- and it stops dead at the
battle UI line. It stops there because the effect is *exactly* 1:1 and the
mesh is only 336 tall:

    geometry     final y = raw_y * scale[0x9AD1A8]        = raw_y * 2
    sampling     staging row = v * yscale                 = raw_y * 2
                 (v = row*8 = raw_y, from escape_uv_init)

Both sides are `raw_y * 2`, so mesh row r shows screen row r, and 168 raw rows
* 2 = 336. Nothing is broken; the mesh is simply sized for a 320x240 screen.

WHY BUILD 407's VERSION MOVED THE RIPPLE
========================================
The ripple is a radial SCALE about a fixed centre, built per frame at x86
0x5D60B3:

    dx = col*8 - 160          dy = row*8 - 120
    r  = radius[row][col]                       (the init's sqrt, 0xC1FA1C)
    f  = amp + (sin(r*100 - frame*500) * amp >> 12) + 0x1000
    x  = ((dx * f) >> 12) + 160    y = ((dy * f) >> 12) + 120

so `p' = C + (p - C) * f` about C = raw (160, 120). At scale 2 that centre is
screen (320, 240) -- already the exact middle of the frame, even though raw row
120 of 168 is 71% of the way down the mesh.

Build 407 scaled the geometry by 10/7 to reach 480 and left C alone, which put
the ring centre at screen y 343: "the ripple does not appear centered, the
image is focused higher". A uniform scale cannot map 0->0, 336->480 AND
240->240; those three are inconsistent. So C has to move with it.

**And C is free to move.** At f = 1 the transform is `p' = p` for ANY C, so
changing C cannot shift, stretch or distort the mesh -- it can only move where
the rings are centred. That is the invariant this module relies on, and it is
what makes the change safe rather than a guess.

THE THREE NUMBERS, AND WHY THEY ARE THESE
=========================================
Pick an integer yscale so the sampling can reach row 480 at all:

    staging row = v * yscale,  v = raw_y  ->  yscale 3 reaches raw 160 -> 480

Then the geometry must agree exactly, or the picture stretches:

    final y = raw_y * 2,  wanted raw_y * 3   ->   y' = 3y/2

and then C follows from "the ring centre lands on screen y 240":

    3 * (2 * C) / 2 = 240   ->   C = 80      (raw row 10 of 21)

Which gives, with nothing fitted:

    mesh spans      raw 0..168  ->  screen y 0..504; the screen clips at 480,
                    so 24 of 504 units (4.8%) of the mesh fall off the bottom
                    and the visible band is 0..480 with no gap
    sampling        staging rows 0..480 over screen 0..480, exactly 1:1
    ring centre     screen (320, 240), the true centre
    rings           2.667 game units per raw unit across, 3.0 down: 12.5%
                    taller than wide

That last number is worth stating plainly, because it is an improvement rather
than a cost. `ff7nx_escapescale` widened x by 4/3 and left y alone, so build
409's rings are **33% wider than tall**. This makes them 12.5% taller than
wide. Perfectly circular would need 8/3 on y, which reaches only 448 rows and
leaves a black band -- so full coverage and near-circular rings is the better
trade, and it is the one the report asked for.

THE FIVE WORDS
==============
`yscale` is `struc_91[0x18]`, which `0x682DB2` copies from object +0x110.
`escape_setup` writes it from the same guest slot as xscale (+0x10C), through
two separate load/store pairs -- one per capture object -- so the LOAD can be
replaced with a literal without touching xscale:

    +0x83B920  ldr w20, [x0]   ->   mov w20, #3        capture A
    +0x83BB8C  ldr w20, [x0]   ->   mov w20, #3        capture B

In both, `w20` is also written to a guest scratch slot that is dead before its
next read (`bl #0xd1390` refills [x21] at +0x83B970; [x21,#4] is rewritten at
+0x83BB94 with no read between), which `verify` asserts rather than assumes.

The ripple centre is three words, and each is anchored on the instruction in
front of it rather than on its own constant -- which matters, because
`sub w8, w8, #0x78` appears FOUR more times in `escape_mesh` as the draw
block's local re-centring (+0x83CD64, +0x83CE44, +0x83CF24, +0x83D004), and
those must NOT move:

    +0x839F14  sub w25, w9, #0x78  ->  #0x50   after `lsl w9, w8, #3`   the
                                               init's dy, feeding the radius
    +0x83C058  sub w24, w9, #0x78  ->  #0x50   after `lsl w9, w8, #3`   the
                                               per-frame dy
    +0x83C214  add w8,  w8, #0x78  ->  #0x50   after `asr w8, w8, #0xc` the
                                               re-add after the scale

The X twins of all three (+0x839EE8, +0x83C02C, +0x83C190, all `#0xa0`) are
asserted to stay at 160, so a mix-up of axes fails rather than ships. So are
the four draw-block sites, at `#0x78`.

The geometry is the cave, the Y twin of `ff7nx_escapescale`:

    lsl  s, v, #1        s = 2y
    add  v, v, s         v = 3y
    asr  v, v, #1        v = 3y/2

Three words, no flags touched, hooked on the mesh's one final-y store -- found
through the unique `add w0, <rect.x reg>, #4` (the rect.y fetch), which cannot
collide with the X hook because that one is reached by `mov w0, <rect.x reg>`.

WHAT THIS DOES NOT CHANGE
=========================
xscale stays 2, so Escape remains excluded from `ff7nx_fbcapture`'s origin
correction and `ff7nx_fbresample`'s gate, exactly as the battle-entry swirl is.
`fb_tex.h` becomes `3 * 256` = 768 against a 480-row surface, so the GPU quad
samples v 0..1.6 -- it overhangs, and the overhang is only ever addressed by
mesh rows below screen y 480, which are clipped. With `ff7nx_gpucap` on (the
default) the render target stays `tex_format` = 256x256 whatever `fb_tex.h`
says, so nothing grows.

One thing to know if you A/B with `SEVENTH_NX_ESCAPE_GPU=0`: on the CPU
readback path `fb_tex.w * fb_tex.h * 4` is now 512*768*4 per capture before the
surface scale, so that comparison is heavier than it was. The GPU path is the
default for exactly that reason.

WHAT IS STILL NOT COVERED
=========================
Whether there is anything behind the UI band to show. The capture surface holds
the scene target, and this port renders the battle scene 332 tall
(`battle_enter` stores rect.h = 332; FFNx patches that to 480, this port
deliberately does not -- BUILD-390). If those rows are void rather than UI,
the bottom of the ripple will be black rather than a warped UI. That is a
property of the capture, not of this patch, and it is the one thing here that a
build decides rather than the binary.
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
import ff7nx_escapescale as _X                                 # noqa: E402

ESCAPE_MESH = _X.ESCAPE_MESH
RECT_X = _X.RECT_X           # the hoisted register we anchor THROUGH
RECT_Y_DELTA = 4             # rect.y is reached as rect.x + 4
MESH_SCALE = 2               # [0x9AD1A8], the mesh's raw -> final multiplier
MESH_RAW_H = 168             # 21 rows x 8
MESH_H = MESH_RAW_H * MESH_SCALE          # 336, the battle rect height
FRAME_H = 480                # what it has to reach
GEO_NUM, GEO_DEN = 3, 2      # y' = 3y/2, so raw_y -> raw_y * 3
YSCALE_NEW = 3               # struc_91[0x18], via object +0x110
RIPPLE_C_OLD = 0x78          # raw 120, the stock ripple centre row*8
RIPPLE_C_NEW = 0x50          # raw 80, so 3*(2*80)/2 = 240 = the frame centre
CENTRE_Y = FRAME_H // 2      # 240, where the rings must end up

ESCAPETALL_ENV = 'SEVENTH_NX_ESCAPE_TALL'


# --------------------------------------------------------------- the words
# Each site carries the word in front of it as the witness, because the
# constant alone is not unique: `sub w8, w8, #0x78` is also the draw block's
# local re-centring, four times over, and those must not move.
RIPPLE_Y_SITES = {
    0x839F14: (0x5101E139, 0x839F10, 0x531D7109),   # sub w25,w9,#0x78
    0x83C058: (0x5101E138, 0x83C054, 0x531D7109),   # sub w24,w9,#0x78
    0x83C214: (0x1101E108, 0x83C210, 0x130C7D08),   # add w8, w8,#0x78
}
YSCALE_SITES = (0x83B920, 0x83BB8C)
LDR_W20 = 0xB9400014             # ldr w20, [x0]        the stock load
MOV_W20_3 = 0x52800074           # mov w20, #3          MOVZ, not an ORR

# Things that must NOT have moved. The X twins of the ripple centre prove the
# axis, and the four draw-block subtractions prove we did not catch those.
GUARDS = {
    0x839EE8: 0x51028119,        # sub w25, w8, #0xa0   init dx
    0x83C02C: 0x51028138,        # sub w24, w9, #0xa0   per-frame dx
    0x83C190: 0x11028108,        # add w8,  w8, #0xa0   the x re-add
    0x83CD64: 0x5101E108,        # sub w8, w8, #0x78    draw block, vertex 0
    0x83CE44: 0x5101E108,        # ... vertex 1
    0x83CF24: 0x5101E108,        # ... vertex 2
    0x83D004: 0x5101E108,        # ... vertex 3
    0x83CD08: 0x51028108,        # their x siblings, #0xa0
    0x83CDE4: 0x51028108,
    0x83CEC4: 0x51028108,
    0x83CFA4: 0x51028108,
}


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _reimm(word, old, new):
    """Re-point an add/sub-immediate's imm12 without touching anything else."""
    assert (word >> 10) & 0xFFF == old, '%08X imm is not %#x' % (word, old)
    return (word - (old << 10) + (new << 10)) & 0xFFFFFFFF


def arithmetic_is_consistent():
    """The three numbers are not independent -- assert the relations instead
    of trusting the header.

    1:1 needs the sampling slope to equal the geometry slope, per raw unit.
    The rings need the centre to land on the middle of the frame.
    Coverage needs the mesh to reach at least the bottom of the frame.
    """
    geo_per_raw = MESH_SCALE * GEO_NUM / GEO_DEN
    assert YSCALE_NEW == geo_per_raw, (YSCALE_NEW, geo_per_raw)
    centre = (RIPPLE_C_NEW * MESH_SCALE) * GEO_NUM // GEO_DEN
    assert centre == CENTRE_Y, (centre, CENTRE_Y)
    assert MESH_H * GEO_NUM // GEO_DEN >= FRAME_H
    # and the sampling must not need a v byte it cannot hold
    assert (FRAME_H // YSCALE_NEW) <= 255
    return True


def enabled() -> bool:
    """ON. Ships with ff7nx_escapegpu and ff7nx_escapescale as one effect.

    `SEVENTH_NX_ESCAPE_TALL=0` puts the effect back to build 409 -- full
    width, 1:1, ripple centred, stopping at the UI line -- which is the A/B
    for everything in this module at once.

    Superseded reasoning, kept because it was wrong in an instructive way:

    GEOMETRY WITHOUT SAMPLING. The effect is 1:1 today, and deliberately so:
    the mesh's final y is `raw * scale` and the staging row a UV reaches is
    `v * yscale`, with `v = raw` and `scale = yscale = 2` -- both sides are
    `raw * 2`, so mesh row r shows screen row r over the top 336. Scaling only
    the geometry by 10/7 stretches the picture vertically by 10/7 and leaves
    the bottom 144 rows sampling nothing new. Pairing it needs `v' = 10v/7`,
    which `ff7nx_escapeuv` used to supply and which is now withdrawn.

    THE RIPPLE MOVES. The radial distance is built in the init from
    `(col*8 - 160, row*8 - 120)`, so the origin is raw (160, 120) -- which at
    scale 2 is screen (320, 240), the true centre of the frame, and 120 of 168
    raw rows, i.e. 71% down the mesh. Any transform that maps raw y 0..168
    onto screen 0..480 must therefore move the origin to screen y 343. That
    is what build 407 shipped and what came back as "the ripple does not
    appear centered, the image is focused higher".

    The three properties are not simultaneously available with a 41 x 22 mesh
    whose radial origin is fixed at raw row 120:

        full 480 coverage + circular rings + centred origin

    Pick two. Vanilla picks the last two and covers 336. Widening x by 4/3
    (ff7nx_escapescale) already costs the rings 33% of their circularity
    unless y is widened by 4/3 as well, which reaches 448 rows -- but about
    the origin rather than about y = 0, so the top 80 units fall off and the
    coverage is 0..368. Whichever is wanted, it needs the UV pass back and it
    needs the radial origin moved with it; neither is a change to guess at.

    That was written believing the radial origin was immovable. It is not:
    the ripple is `p' = C + (p - C) * f`, which is the identity at f = 1 for
    any C, so C can be moved without shifting or distorting anything. Three
    words do it, and then all three properties are available at once.
    """
    v = os.environ.get(ESCAPETALL_ENV)
    if v is None:
        return True
    return v.strip().lower() not in ('0', 'off', 'false', 'no')


def body(val_reg, scratch_reg):
    """y' = 3y/2, uniform, three words, no flags touched.

    `asr` rather than `sdiv` because the only y this can see as negative is a
    mesh row the ripple has pushed above the top edge, where a one-unit
    difference in rounding is off-screen either way -- and it saves a word.
    """
    v, s = int(val_reg[1:]), int(scratch_reg[1:])
    return [A.lsl(s, v, 1),          # s = 2y
            A.add_reg(v, v, s),      # v = 3y
            A.asr(v, v, 1)]          # v = 3y/2


def _ratio():
    """FRAME_H / MESH_H in lowest terms -- 480/336 = 10/7."""
    a, b = FRAME_H, MESH_H
    while b:
        a, b = b, a % b
    return FRAME_H // a, MESH_H // a


def expected(y):
    """The model, for the verifier to check the cave against. `asr` floors."""
    return (GEO_NUM * y) >> (GEO_DEN.bit_length() - 1)


def _md():
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = False
    return md


def find_hook(m, md=None):
    """((hook_va, val_reg, scratch_reg), None) or (None, why).

    Anchored on the ONE `add w0, <rect.x reg>, #4` in the mesh body -- the
    rect.y fetch -- then the add/store the recompiler always emits after it.
    Locatable both patched and unpatched, which revert needs.
    """
    md = md or _md()
    lo, hi = m.extent(ESCAPE_MESH)
    ins = list(md.disasm(bytes(m.img[lo:hi]), lo))
    rx = _X._hoisted_reg(ins, RECT_X)
    if rx is None:
        return None, 'rect.x (0x%X) is not hoisted in escape_mesh' % RECT_X
    want = 'w0, %s, #%d' % (rx, RECT_Y_DELTA)
    anchors = [k for k, i in enumerate(ins)
               if i.mnemonic == 'add' and i.op_str == want]
    if len(anchors) != 1:
        return None, ('expected exactly one `add %s`, found %d'
                      % (want, len(anchors)))
    # and the X path must still be the `mov`, or the two hooks could collide
    if not any(i.mnemonic == 'mov' and i.op_str == 'w0, %s' % rx for i in ins):
        return None, 'rect.x is not `mov w0, %s`; the X anchor moved' % rx
    k = anchors[0]
    for j in range(k, min(k + 10, len(ins) - 1)):
        i = ins[j]
        if i.mnemonic != 'add':
            continue
        parts = [p.strip() for p in i.op_str.split(',')]
        if len(parts) != 3 or parts[0] != parts[1] \
                or not parts[2].startswith('w'):
            continue
        st = ins[j + 1]
        if st.mnemonic == 'str' and st.op_str.startswith(parts[0] + ','):
            return (st.address, parts[0], parts[2]), None
        if st.mnemonic == 'b':
            return (st.address, parts[0], parts[2]), None
    return None, 'no `add v,v,s` + store/branch within 10 of the rect.y anchor'


# ------------------------------------------------ the in-place word patches
def verify_words(img) -> list:
    """Anchors, guards, and the liveness the two yscale words depend on."""
    bad = []
    for va, want in sorted(GUARDS.items()):
        if _w(img, va) != want:
            bad.append('guard +0x%X is %08X, expected %08X -- the ripple/draw '
                       'arithmetic has moved' % (va, _w(img, va), want))
    for va, (stock, wva, wword) in sorted(RIPPLE_Y_SITES.items()):
        if _w(img, wva) != wword:
            bad.append('witness +0x%X for ripple site +0x%X is %08X, expected '
                       '%08X' % (wva, va, _w(img, wva), wword))
        have = _w(img, va)
        if have not in (stock, _reimm(stock, RIPPLE_C_OLD, RIPPLE_C_NEW)):
            bad.append('ripple centre site +0x%X is %08X, which is neither '
                       'value this module writes' % (va, have))
    for va in YSCALE_SITES:
        if _w(img, va) not in (LDR_W20, MOV_W20_3):
            bad.append('yscale site +0x%X is %08X, which is neither `ldr w20, '
                       '[x0]` nor `mov w20, #3`' % (va, _w(img, va)))
    return bad


def words_installed(img):
    """True = tall, False = stock, None = a mix (never written by this)."""
    tall = [_w(img, va) == _reimm(st, RIPPLE_C_OLD, RIPPLE_C_NEW)
            for va, (st, _a, _b) in RIPPLE_Y_SITES.items()]
    tall += [_w(img, va) == MOV_W20_3 for va in YSCALE_SITES]
    if all(tall):
        return True
    if not any(tall):
        return False
    return None


def plan_words(img, revert=False):
    problems = verify_words(img)
    if problems:
        return [], problems
    if words_installed(img) is None:
        return [], ['the ripple-centre and yscale words are in a mixed state']
    tall = not revert
    patches = []
    for va, (stock, _a, _b) in sorted(RIPPLE_Y_SITES.items()):
        want = _reimm(stock, RIPPLE_C_OLD, RIPPLE_C_NEW) if tall else stock
        have = _w(img, va)
        if have != want:
            patches.append({
                'name': 'Escape ripple Y centre -> raw %d'
                        % (RIPPLE_C_NEW if tall else RIPPLE_C_OLD) ,
                'va': hex(va),
                'expect': struct.pack('<I', have).hex(),
                'set': struct.pack('<I', want).hex()})
    for n, va in enumerate(YSCALE_SITES):
        want = MOV_W20_3 if tall else LDR_W20
        have = _w(img, va)
        if have != want:
            patches.append({
                'name': 'Escape capture %s yscale -> %s'
                        % ('A' if n == 0 else 'B',
                           str(YSCALE_NEW) if tall else 'stock'),
                'va': hex(va),
                'expect': struct.pack('<I', have).hex(),
                'set': struct.pack('<I', want).hex()})
    return patches, []


def plan(m, revert=False, md=None, pool=None):
    import ff7nx_cave
    md = md or _md()
    found, why = find_hook(m, md)
    if found is None:
        return [], [], [why]
    hook, val, scratch = found
    cur = struct.unpack_from('<I', m.img, hook)[0]
    curi = next(md.disasm(struct.pack('<I', cur), hook), None)
    patched = (curi is not None and curi.mnemonic == 'b')

    if revert:
        if not patched:
            return [], [], []
        cave, why2 = _X.walk_cave(m, hook, md)
        if cave is None:
            return [], [], [why2]
        stores = []
        for _va, w in sorted(cave.items()):
            d = next(md.disasm(struct.pack('<I', w), 0), None)
            if d is not None and d.mnemonic == 'str':
                stores.append(w)
        if len(stores) != 1:
            return [], [], ['expected exactly one store in the cave, found %d'
                            % len(stores)]
        ps = [{'name': 'escape y scale: unhook', 'va': hex(hook),
               'expect': struct.pack('<I', cur).hex(),
               'set': struct.pack('<I', stores[0]).hex()}]
        for va in sorted(cave):
            ps.append({'name': 'escape y scale: clear cave word +0x%x' % va,
                       'va': hex(va),
                       'expect': struct.pack('<I', cave[va]).hex(),
                       'set': '00000000'})
        return ps, ['  escape y scale removed @ +0x%07X (%d cave word(s) '
                    'returned to the pool)' % (hook, len(cave))], []

    if patched:
        return [], ['  escape y scale already installed @ +0x%07X' % hook], []
    if not _X.scratch_is_dead(m, hook, scratch, md):
        return [], [], ['%s is live after +0x%X; the cave would corrupt it'
                        % (scratch, hook)]
    pool = pool or ff7nx_cave.HolePool(m.img, starts=set(m.arm_starts))
    out, entry = ff7nx_cave.emit_hooked(pool, hook, cur, body(val, scratch))
    patches = []
    for va in sorted(out):
        old = struct.unpack_from('<I', m.img, va)[0]
        if va != hook and old != 0:
            return [], [], ['cave word +0x%X is not padding (0x%08X)'
                            % (va, old)]
        patches.append({'name': 'escape y scale +0x%x' % va, 'va': hex(va),
                        'expect': struct.pack('<I', old).hex(),
                        'set': struct.pack('<I', out[va]).hex()})
    notes = ['  Escape ripple height is anchored at y=%d; the lower mesh now '
             'reaches %d instead of stopping at the UI line at %d'
             % (CENTRE_Y, FRAME_H, MESH_H),
             '  hook +0x%07X (%s carries y, %s scratch), cave entry +0x%X, '
             '%d word(s)' % (hook, val, scratch, entry, len(out) - 1)]
    return patches, notes, []


def apply(main, revert=False, log=print) -> int:
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    arithmetic_is_consistent()
    patches, notes, problems = plan(m, revert=revert)
    wpatches, wproblems = plan_words(m.img, revert=revert)
    problems = list(problems) + list(wproblems)
    if problems:
        for p in problems:
            log('  ! escape y scale: %s' % p)
        log('  refusing to touch the Escape mesh height.')
        return 1
    patches = list(patches) + wpatches
    if wpatches:
        notes = list(notes) + [
            '  ripple Y centre raw %d -> %d, capture yscale 2 -> %d: the '
            'sampling reaches staging row %d and the rings stay on y=%d'
            % (RIPPLE_C_OLD, RIPPLE_C_NEW, YSCALE_NEW, FRAME_H, CENTRE_Y)
            if not revert else
            '  ripple Y centre and capture yscale back to stock']
    for n in notes:
        log(n)
    if not patches:
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_escapetall', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.esctall-')
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
