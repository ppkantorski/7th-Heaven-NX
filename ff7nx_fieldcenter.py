#!/usr/bin/env python3
r"""
ff7nx_fieldcenter.py -- open the field's PARENT WINDOW. It is 448 of 480.

    python3 ff7nx_fieldcenter.py <exefs/main> --show
    python3 ff7nx_fieldcenter.py <exefs/main> --apply     <- the fix
    python3 ff7nx_fieldcenter.py <exefs/main> --revert
    python3 ff7nx_fieldcenter.py --verify                 (offline self-test)

THE MODEL, VALIDATED ON THREE HARDWARE BOOTS WITH ZERO RESIDUAL
===============================================================
Every boot in this investigation fits one formula. Device rows at 720p:

    band = [ 24 , 24 + 1.5 * min(h, 448) ]          h = [0xCFF1EC]

    h = 448  (stock)   predict 24..696   measured 24..696
    h = 480  (v5 / the origin build)     predict 24..696   measured 23..696
    h = 240  (--probe, 2026-08-07)       predict 24..384   measured 24..384

The `min(h, 448)` cap is the term seven previous attempts were missing. It
is why `h = 480` read as a null result: 1.5*480 = 720 is clipped back to
1.5*448 = 672, so the frame is byte-for-byte identical to stock. v5 was
correct and invisible, and so was every later attempt to grow `h`.

In game units the formula says the field's viewport is INTERSECTED with a
parent rect of y 16..464 -- a 640x448 window centred in a 640x480 frame.

WHERE THAT WINDOW IS DEFINED
============================
`field_set_mode` writes a display block, all four stores disassembled:

    [0xCFF1F0] = 2      the tile multiplier
    [0xCFF1F4] = 320    FRAME  half-width
    [0xCFF1F8] = 240    FRAME  half-height
    [0xCFF1FC] = 320    WINDOW half-width
    [0xCFF200] = 224    WINDOW half-height     <- THE LETTERBOX

    240 - 224 = 16, centred  ->  the field is confined to game y 16..464
    at 1.5 device px per unit that is rows 24..696. Exactly the band.

Mode 1 writes the same pair halved -- (160, 112) against a 160 frame --
which is the cross-check that these really are half-sizes and not a
coincidence of two numbers that happen to be 240 and 224.

WHY IT TAKES TWO WORDS, AND WHY NEITHER HAS EVER WORKED ALONE
=============================================================
The window and the viewport are separate gates. Opening one leaves the
other closed, and the frame does not move:

    [0xCFF200] 224 -> 240   opens the parent window to the full 480
    [0xCFF1EC] 448 -> 480   makes the viewport ask for the full height

"attempt 2" shipped the first alone -- the viewport still asked for 448, so
the band stayed 24..696 and it was written off and backed out. "v5" shipped
the second alone -- the window still clipped at 464, so the band stayed
24..696 and it was written off too. `ff7nx_ws.UNCROP_PATCHES` lists exactly
this pair, gated behind SEVENTH_NX_WS_UNCROP and marked EXPERIMENTAL /
untested. They have never been applied in the same module.

WHAT IS ELIMINATED, AND HOW
===========================
The game side is now exhausted, every step disassembled in this session
rather than taken on report:

    set_field_viewport      x86 0x60D810   four stores, verbatim, no clamp
    engine_gfx_setviewport  x86 0x66067A   verbatim pass-through to the driver
    the three field callers 0x60E1C0, 0x60E41A, 0x63A9D1 -- all push
                            [0xCFF1E0..EC] verbatim
    all 44 x86 call sites   decoded; none passes y = 16 or h = 448
    no global in FF7 is ever assigned 448, and no (16,448) rect exists as
                            data in either image
    gfx_drv_setviewport     ws_emu on its real encoded words:
                            (0,16,640,448) -> y1 = 24, y2 = 696
    the two vtable targets  +0x1137640 and +0x1137730, reached through the
                            thunks at +0x11320E0/+0x11320F0 -- pure
                            pass-throughs with a bottom-left flip that is
                            algebraically the identity for any target height

That is why the answer had to be a value the GAME holds, not a rect anyone
passes, and [0xCFF200] is the only candidate that survives all of it.

THE WORDS
=========
Two constants. No cave, no displaced instruction, byte-exactly reversible;
both verified against a multi-word signature read out of the image.

    +0x0929964  orr w8, wzr, #0xe0  -> movz w8, #0xf0    window half-h 224->240
    +0x09298BC  orr w8, wzr, #0x1c0 -> movz w8, #0x1e0   viewport h    448->480

`--origin` is left at its stock 224 by default. The four
`field_layerN_pick_tiles` origins are a SEPARATE knob (FFNx's
`ff7_field_center`); moving them on 2026-08-07 shifted the background 24 px
and left the models behind, because the field has three parallel origins --
[0xCFF1F4/F8] = (320,240) places models (x86 0x644CEC), [0xCFF1FC/200] =
(320,224) places sprites, and the tile loop carries a third hardcoded 224.
Do not move one without the others.

OUTCOMES, NAMED IN ADVANCE
==========================
  no bars, art top and bottom     done.
  no bars but the new strips are
    black on some fields          expected on the 44 of 709 fields whose
                                  layer-1 art is under 240 tile units
                                  (`junon` is 224). `md8_1` and `mrkt2` are
                                  not among them.
  bars gone, picture 7% taller    the window opened but something is
                                  stretching to fill it rather than
                                  revealing -- back out the window word only
                                  (--window 224) and report.
  still exactly 24..696           the parent rect is not [0xCFF200]. Then the
                                  formula's cap is elsewhere and the next
                                  step is a --height 400 boot to confirm the
                                  linear region before anything else.
  models drift from the scenery   [0xCFF200] also feeds sprite placement
                                  (x86 0x64DC28). --window 224 to split it.

Test on `md8_1` (Sector 8) or `mrkt2` (Wall Market) -- both have 240+ units
of art. Not `junon` or `lastmap`.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

STOCK_ORIGIN_Y = 224
CENTRED_ORIGIN_Y = 232

# --------------------------------------------------------------------------
# encodings
# --------------------------------------------------------------------------
ORR_224_W9 = 0x321B0BE9          # orr w9, wzr, #0xe0      (the stock word)
ORR_448_W8 = 0x321A0BE8          # orr w8, wzr, #0x1c0     (the stock word)


def movz(rd: int, imm16: int) -> int:
    """MOVZ Wd, #imm16."""
    if not 0 <= imm16 <= 0xFFFF:
        raise ValueError('movz immediate %d out of range' % imm16)
    return 0x52800000 | (imm16 << 5) | rd


def movz_imm(word: int) -> int | None:
    """The immediate of a 32-bit MOVZ, or None."""
    return (word >> 5) & 0xFFFF if (word & 0xFFE00000) == 0x52800000 else None


# --------------------------------------------------------------------------
# the sites, each with a signature READ OUT OF THE IMAGE
# --------------------------------------------------------------------------
# sig is (first_word_va_offset_from_site, [words...]) with the site's own word
# included. Nothing is typed from a disassembly listing -- HANDOFF-85 6 lost a
# build to exactly that (0x941F32BA for a bl whose real word is 0x941F4ABA).
ORIGIN_SITES = [
    # va          name                 sig start   signature words
    (0x0A05AA4, 'layer 2 origin_y', -4,
     [0x79C00008, 0x321B0BE9, 0x2A1403E0, 0xB9000AE8, 0x4B080128]),
    (0x0A06EA8, 'layer 1 origin_y', -4,
     [0x79C00008, 0x321B0BE9, 0x2A1403E0, 0xB9000AC8, 0x4B080128]),
    (0x0A07878, 'layer 3 origin_y', -4,
     [0x79C00008, 0x321B0BE9, 0x2A1503E0, 0xB9000B28, 0x4B080128]),
    (0x0A08728, 'layer 4 origin_y', -4,
     [0x79C00008, 0x321B0BE9, 0x2A1503E0, 0xB9000AE8, 0x4B080128]),
]

HEIGHT_VA = 0x09298BC
HEIGHT_SIG_START = -0x14
HEIGHT_SIG = [
    0x54000D81,   # -0x14  b.ne  #0x929a58     the mode-2 guard
    0xB94012A8,   # -0x10  ldr   w8, [x21, #0x10]
    0x51001100,   # -0x0C  sub   w0, w8, #4
    0xB90012A0,   # -0x08  str   w0, [x21, #0x10]
    0x941F4ABA,   # -0x04  bl    #0x10fc3a0     <- READ, not typed
    0x321A0BE8,   #  0x00  orr   w8, wzr, #0x1c0
    0xB9000008,   # +0x04  str   w8, [x0]
    0xB94012A8,   # +0x08  ldr   w8, [x21, #0x10]
]

# gfx_drv_setviewport: the hook site any previous cave replaced, and the
# stock word that belongs there.
HOOK_VA = 0x10D676C
HOOK_STOCK = 0x529999AA          # mov w10, #0xcccd
HOOK_SIG_START = -0x0C
HOOK_SIG = [
    0x90000FC8,   # -0x0C  adrp x8,  #0x12ce000
    0xF942BD08,   # -0x08  ldr  x8,  [x8, #0x578]
    0x90000FCC,   # -0x04  adrp x12, #0x12ce000
    0x529999AA,   #  0x00  mov  w10, #0xcccd          <- the hook site
    0x72B9998A,   # +0x04  movk w10, #0xcccc, lsl #16
]
RETURN_VA = 0x10D6770

# two earlier attempts that must be stock, so a null result stays attributable
ATTEMPT1_VA, ATTEMPT1_STOCK = 0x10D6868, 0x54000061   # b.ne  (_22 := 1.0)

# ---------------------------------------------------------------------------
# THE PARENT WINDOW  --  field_set_mode's half-height, [0xCFF200]
# ---------------------------------------------------------------------------
# [0xCFF1F4]/[0xCFF1F8] = (320, 240) is the FRAME half-size.
# [0xCFF1FC]/[0xCFF200] = (320, 224) is the field WINDOW half-size.
# 240 - 224 = 16, and the window is centred, so the field is confined to
# game y 16..464. Three hardware boots give
#
#     band = [24, 24 + 1.5 * min(h, 448)]      device rows at 720p
#
# with no residual, which is that window intersected with the viewport.
# Opening it needs BOTH halves: the window (here) and the viewport height
# (HEIGHT_VA). Each alone is a no-op, which is why "attempt 2" and "v5"
# both read as null when they were shipped one at a time.
WINDOW_VA = 0x0929964
WINDOW_SIG_START = -0x0C
WINDOW_SIG = [
    0xB9000018,   # -0x0C  str  w24, [x0]          the 320 half-width
    0x11003260,   # -0x08  add  w0, w19, #0xc      &[0xCFF200]
    0x941F4A90,   # -0x04  bl   #0x10fc3a0         <- READ, not typed
    0x321B0BE8,   #  0x00  orr  w8, wzr, #0xe0     224
    0xB9000008,   # +0x04  str  w8, [x0]
]
ORR_224_W8 = 0x321B0BE8


def _window_of(word):
    if word == ORR_224_W8:
        return 224
    v = movz_imm(word)
    return v if v is not None and (word & 31) == 8 else None


# --------------------------------------------------------------------------
# NSO access
# --------------------------------------------------------------------------
def _text(path) -> bytes:
    import lz4.block
    d = Path(path).read_bytes()
    if d[:4] != b'NSO0':
        raise SystemExit('%s is not an NSO0 file' % path)
    fo, mo, ds = struct.unpack('<III', d[0x10:0x1C])
    comp = struct.unpack('<I', d[0x60:0x64])[0]
    flags = struct.unpack('<I', d[0x0C:0x10])[0]
    blob = d[fo:fo + comp]
    return (lz4.block.decompress(blob, uncompressed_size=ds)
            if flags & 1 else blob[:ds])


def w32(t: bytes, va: int) -> int:
    return struct.unpack_from('<I', t, va)[0]


# --------------------------------------------------------------------------
# signature checking
# --------------------------------------------------------------------------
def _sig_bad(t, site, start, sig, name, allow_site_patched=None):
    """
    [] if the signature holds. `allow_site_patched` is the set of words the
    site itself may legitimately already hold (this patch's own output), so
    --apply is idempotent without weakening the check on its neighbours.
    """
    bad = []
    for i, want in enumerate(sig):
        va = site + start + i * 4
        have = w32(t, va)
        if have == want:
            continue
        if va == site and allow_site_patched and have in allow_site_patched:
            continue
        bad.append('+0x%07X holds %08X, signature says %08X  (%s)'
                   % (va, have, want, name))
    return bad


def _origin_of(word):
    """The origin this word encodes, or None if it is neither form."""
    if word == ORR_224_W9:
        return STOCK_ORIGIN_Y
    v = movz_imm(word)
    return v if v is not None and (word & 31) == 9 else None


def _height_of(word):
    if word == ORR_448_W8:
        return 448
    v = movz_imm(word)
    return v if v is not None and (word & 31) == 8 else None


# --------------------------------------------------------------------------
# the stale cave at gfx_drv_setviewport
# --------------------------------------------------------------------------
def walk_cave(t):
    """
    Every word a previous uncrop cave owns, or None if the hook is stock.

    The chain is walked by TARGET, not by index: HANDOFF-85 3b lost a run to a
    walker that identified a chain hop positionally and ate the return branch.
    A word is a hop iff it is an unconditional B whose target is not
    RETURN_VA.
    """
    hook = w32(t, HOOK_VA)
    if hook == HOOK_STOCK:
        return None
    if (hook & 0xFC000000) != 0x14000000:
        raise SystemExit('+0x%07X holds %08X, which is neither the stock word '
                         'nor a branch -- refusing to guess' % (HOOK_VA, hook))
    imm = hook & 0x03FFFFFF
    if imm & (1 << 25):
        imm -= (1 << 26)
    a = HOOK_VA + imm * 4
    words, seen = [], set()
    for _ in range(256):
        if a in seen:
            raise SystemExit('cave chain loops at +0x%07X' % a)
        seen.add(a)
        w = w32(t, a)
        words.append(a)
        if (w & 0xFC000000) == 0x14000000:            # unconditional B
            i = w & 0x03FFFFFF
            if i & (1 << 25):
                i -= (1 << 26)
            tgt = a + i * 4
            if tgt == RETURN_VA:
                return words
            a = tgt
            continue
        a += 4
    raise SystemExit('cave chain did not return to +0x%07X in 256 words'
                     % RETURN_VA)


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------
def plan(t, origin_y: int, height: int = 480, revert: bool = False,
         window: int = 240):
    """(patches, notes) -- patches are nso_patcher dicts."""
    notes, ps = [], []

    bad = []
    for va, name, st, sig in ORIGIN_SITES:
        bad += _sig_bad(t, va, st, sig, name,
                        allow_site_patched={w32(t, va)}
                        if _origin_of(w32(t, va)) is not None else None)
    bad += _sig_bad(t, HEIGHT_VA, HEIGHT_SIG_START, HEIGHT_SIG,
                    'field_set_mode h',
                    allow_site_patched={w32(t, HEIGHT_VA)}
                    if _height_of(w32(t, HEIGHT_VA)) is not None else None)
    bad += _sig_bad(t, WINDOW_VA, WINDOW_SIG_START, WINDOW_SIG,
                    'field window half-height',
                    allow_site_patched={w32(t, WINDOW_VA)}
                    if _window_of(w32(t, WINDOW_VA)) is not None else None)
    # the hook's neighbours must be stock even when the hook itself is a cave
    bad += _sig_bad(t, HOOK_VA, HOOK_SIG_START, HOOK_SIG,
                    'gfx_drv_setviewport',
                    allow_site_patched={w32(t, HOOK_VA)})
    if bad:
        return None, bad

    want_o = STOCK_ORIGIN_Y if revert else origin_y
    want_h = 448 if revert else height
    want_win = 224 if revert else window

    cur = w32(t, WINDOW_VA)
    have = _window_of(cur)
    new = ORR_224_W8 if want_win == 224 else movz(8, want_win)
    if cur == new:
        notes.append('  field window half-h  already %d' % have)
    else:
        ps.append({'name': 'field window half-height %d -> %d @ +0x%07X  '
                           '(the parent rect: %d x %d of %d x %d)'
                           % (have, want_win, WINDOW_VA, 640, want_win * 2,
                              640, 480),
                   'va': WINDOW_VA, 'expect': cur, 'set': new})

    for va, name, _st, _sig in ORIGIN_SITES:
        cur = w32(t, va)
        have = _origin_of(cur)
        new = ORR_224_W9 if want_o == STOCK_ORIGIN_Y else movz(9, want_o)
        if cur == new:
            notes.append('  %-18s already %d' % (name, have))
            continue
        ps.append({'name': '%s %d -> %d @ +0x%07X' % (name, have, want_o, va),
                   'va': va, 'expect': cur, 'set': new})

    cur = w32(t, HEIGHT_VA)
    have = _height_of(cur)
    new = ORR_448_W8 if want_h == 448 else movz(8, want_h)
    if cur == new:
        notes.append('  field_set_mode h   already %d' % have)
    else:
        ps.append({'name': 'field_set_mode h %d -> %d @ +0x%07X'
                           % (have, want_h, HEIGHT_VA),
                   'va': HEIGHT_VA, 'expect': cur, 'set': new})

    cave = walk_cave(t)
    if cave:
        ps.append({'name': 'gfx_drv_setviewport: remove the stale cave hook '
                           '@ +0x%07X' % HOOK_VA,
                   'va': HOOK_VA, 'expect': w32(t, HOOK_VA),
                   'set': HOOK_STOCK})
        for a in cave:
            ps.append({'name': 'return cave word +0x%07X to the padding pool'
                               % a,
                       'va': a, 'expect': w32(t, a), 'set': 0})
        notes.append('  tore out a %d-word cave at gfx_drv_setviewport'
                     % len(cave))
    else:
        notes.append('  gfx_drv_setviewport   stock, no cave')

    if w32(t, ATTEMPT1_VA) != ATTEMPT1_STOCK:
        ps.append({'name': 'back out attempt 1 (_22) @ +0x%07X' % ATTEMPT1_VA,
                   'va': ATTEMPT1_VA, 'expect': w32(t, ATTEMPT1_VA),
                   'set': ATTEMPT1_STOCK})
    else:
        notes.append('  attempt 1 (_22)      stock')
    return ps, notes


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def show(t, log=print):
    log('  field background origin_y (FFNx ff7_field_center):')
    for va, name, _s, _g in ORIGIN_SITES:
        v = _origin_of(w32(t, va))
        log('    +0x%07X  %-18s %s' % (va, name,
                                       ('%d' % v) if v is not None
                                       else 'UNRECOGNISED %08X' % w32(t, va)))
    wv = _window_of(w32(t, WINDOW_VA))
    log('    +0x%07X  %-18s %s   -> parent rect 640 x %s of 640 x 480'
        % (WINDOW_VA, 'window half-height', wv,
           (wv * 2) if wv is not None else '?'))
    h = _height_of(w32(t, HEIGHT_VA))
    log('    +0x%07X  %-18s %s' % (HEIGHT_VA, 'field_set_mode h',
                                   ('%d' % h) if h is not None
                                   else 'UNRECOGNISED %08X'
                                        % w32(t, HEIGHT_VA)))
    cave = walk_cave(t)
    if cave:
        log('  ! gfx_drv_setviewport carries a %d-word cave:' % len(cave))
        for a in cave:
            log('      +0x%07X  %08X' % (a, w32(t, a)))
        for a in cave:
            v = movz_imm(w32(t, a))
            if v is not None and (w32(t, a) & 31) == 3:
                log('      ...it forces the viewport HEIGHT to %d' % v)
    else:
        log('    gfx_drv_setviewport  stock (no cave)')
    log('')
    o = _origin_of(w32(t, ORIGIN_SITES[1][0]))
    if o is not None and h is not None and wv is not None:
        log(predict(o, h, window=wv))


def predict(origin_y: int, height: int, cam_y: int = 112,
            tile_lo: int = -120, tile_hi: int = 120, px_per_unit=1.5,
            window: int = 224) -> str:
    """
    The frame md8_1 should produce, from the measured model.

    dst_y   = 2 * (origin_y + tile.y - cam_y)
    device  = 1.5 * dst_y                       (1280x720 field buffer)
    band    = [24, min(24 + 1.5*h, art_end)]    measured over four boots
    """
    def dev(ty):
        return px_per_unit * 2 * (origin_y + ty - cam_y)
    a0, a1 = dev(tile_lo), dev(tile_hi)
    # VALIDATED ON THREE BOOTS, zero residual:
    #   band = [ 1.5*inset , 1.5*(inset + min(h, 2*window)) ]
    #   inset = 240 - window   (the frame half-height minus the window's)
    inset = 240 - window
    w0 = px_per_unit * inset
    w1 = px_per_unit * (inset + min(height, 2 * window))
    b0, b1 = max(a0, w0, 0.0), min(a1, w1, 720.0)
    out = ['  predicted md8_1 frame (origin_y %d, h %d):' % (origin_y, height),
           '    art      device rows %7.1f .. %7.1f' % (a0, a1),
           '    window   device rows %7.1f .. %7.1f' % (w0, w1),
           '    visible  device rows %7.1f .. %7.1f' % (b0, b1)]
    top, bot = b0 - 0.0, 720.0 - b1
    out.append('    -> top bar %.0f px, bottom bar %.0f px' % (top, bot))
    return '\n'.join(out)


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------
def apply(main, origin_y=STOCK_ORIGIN_Y, height=480, revert=False,
          dry_run=False, log=print, window=240) -> int:
    import nso_patcher
    main = str(main)
    t = _text(main)
    ps, notes = plan(t, origin_y, height, revert, window)
    if ps is None:
        log('! refusing to patch -- this is not the module this patch was '
            'built against:')
        for b in notes:
            log('    ' + b)
        return 2
    for n in notes:
        log(n)
    if not ps:
        log('  nothing to do -- the module already carries this patch')
        return 0
    for p in ps:
        log('  ' + p['name'])
    if dry_run:
        log('  (dry run -- nothing written)')
        return 0
    spec = {'name': 'field centre + uncrop height',
            'patches': [{'name': p['name'], 'va': p['va'],
                         'expect': struct.pack('<I', p['expect']).hex(),
                         'set': struct.pack('<I', p['set']).hex()}
                        for p in ps]}
    nso = nso_patcher.read_nso(Path(main))
    for line in nso_patcher.apply_spec(nso, spec):
        log('    ' + line)
    tmp = main + '.fctmp'
    Path(tmp).write_bytes(nso_patcher.rebuild(nso))
    os.replace(tmp, main)

    # READ IT BACK. HANDOFF-85 6: a log line that reports what it MEANT to
    # write instead of what the module carries is worse than no log line.
    t2 = _text(main)
    log('  read back from the written module:')
    for va, name, _s, _g in ORIGIN_SITES:
        log('    +0x%07X  %-18s %s' % (va, name, _origin_of(w32(t2, va))))
    log('    +0x%07X  %-18s %s' % (HEIGHT_VA, 'field_set_mode h',
                                   _height_of(w32(t2, HEIGHT_VA))))
    log('    +0x%07X  %-18s %s' % (WINDOW_VA, 'window half-height',
                                   _window_of(w32(t2, WINDOW_VA))))
    log('    gfx_drv_setviewport cave: %s'
        % ('%d words STILL PRESENT' % len(walk_cave(t2) or [])
           if walk_cave(t2) else 'gone'))
    log('')
    log(predict(_origin_of(w32(t2, ORIGIN_SITES[1][0])),
                _height_of(w32(t2, HEIGHT_VA)),
                window=_window_of(w32(t2, WINDOW_VA))))
    return 0


# --------------------------------------------------------------------------
# offline self-test
# --------------------------------------------------------------------------
def verify(main=None, log=print) -> int:
    checks, fails = 0, 0

    def ck(cond, what):
        nonlocal checks, fails
        checks += 1
        if not cond:
            fails += 1
            log('  FAIL  %s' % what)

    # encodings
    ck(movz(9, 232) == 0x52801D09, 'movz w9, #232 encodes as 52801D09')
    ck(movz(8, 480) == 0x52803C08, 'movz w8, #480 encodes as 52803C08')
    ck(movz_imm(0x52801D09) == 232, 'movz_imm round-trips 232')
    ck(movz_imm(0x52803C08) == 480, 'movz_imm round-trips 480')
    ck(movz_imm(ORR_224_W9) is None, 'the stock ORR is not read as a MOVZ')
    ck(_origin_of(ORR_224_W9) == 224, 'stock word decodes to origin 224')
    ck(_origin_of(movz(9, 232)) == 232, 'patched word decodes to origin 232')
    ck(_origin_of(movz(8, 232)) is None, 'a MOVZ into w8 is not an origin')
    ck(_height_of(ORR_448_W8) == 448, 'stock word decodes to h 448')
    ck(_height_of(movz(8, 480)) == 480, 'patched word decodes to h 480')

    # THE MODEL, held to all three measured boots
    def band(h, window=224):
        inset = 240 - window
        return (1.5 * inset, 1.5 * (inset + min(h, 2 * window)))
    ck(band(448) == (24.0, 696.0), 'boot 1: h=448 stock -> measured 24..696')
    ck(band(480) == (24.0, 696.0), 'boot 2: h=480 -> 24..696, the min() cap')
    ck(band(240) == (24.0, 384.0), 'boot 3: --probe h=240 -> measured 24..384')
    ck(band(480, 240) == (0.0, 720.0), 'the PAIR opens the full frame')
    ck(band(448, 240) == (0.0, 672.0), 'window alone leaves a 48px bottom bar')
    ck(band(480, 224) == (24.0, 696.0), 'viewport alone changes nothing')
    ck(band(240, 240) == (0.0, 360.0), 'the pair still honours a small h')

    if main:
        t = _text(main)
        # signatures hold on the real module
        bad = []
        for va, name, st, sig in ORIGIN_SITES:
            bad += _sig_bad(t, va, st, sig, name,
                            allow_site_patched={w32(t, va)})
        ck(not bad, 'all four origin signatures hold: %s' % '; '.join(bad))

        ps, notes = plan(t, CENTRED_ORIGIN_Y, 480)
        ck(ps is not None, 'plan() accepts the module')
        if ps is not None:
            ck(all(w32(t, p['va']) == p['expect'] for p in ps),
               'every patch expects the word actually in the module')
            ck(len({p['va'] for p in ps}) == len(ps),
               'no address is written twice')

        # mutation: a corrupted signature must be REFUSED, not sailed past
        m = bytearray(t)
        struct.pack_into('<I', m, ORIGIN_SITES[0][0] + 8, 0xDEADBEEF)
        ck(plan(bytes(m), CENTRED_ORIGIN_Y, 480)[0] is None,
           'a mutated neighbour word is refused')
        m = bytearray(t)
        struct.pack_into('<I', m, HEIGHT_VA - 4, 0x941F32BA)   # the typo word
        ck(plan(bytes(m), CENTRED_ORIGIN_Y, 480)[0] is None,
           'the mistyped bl (0x941F32BA) is refused')

        # idempotence: applying to an already-patched image plans nothing
        m = bytearray(t)
        for va, _n, _s, _g in ORIGIN_SITES:
            struct.pack_into('<I', m, va, movz(9, 232))
        struct.pack_into('<I', m, HEIGHT_VA, movz(8, 480))
        struct.pack_into('<I', m, HOOK_VA, HOOK_STOCK)
        struct.pack_into('<I', m, ATTEMPT1_VA, ATTEMPT1_STOCK)
        struct.pack_into('<I', m, WINDOW_VA, movz(8, 240))
        ck(plan(bytes(m), CENTRED_ORIGIN_Y, 480)[0] == [],
           'applying twice plans zero writes')
        # and revert from there restores exactly the stock words
        rp, _ = plan(bytes(m), CENTRED_ORIGIN_Y, 480, revert=True)
        ck(rp is not None and len(rp) == 6,
           'revert plans exactly the six constants (4 origins + h + window)')
        ck(all(p['set'] in (ORR_224_W9, ORR_448_W8, ORR_224_W8) for p in rp),
           'revert restores the module\'s own stock encodings, not MOVZ forms')

    log('  %d checks, %d failure(s)' % (checks, fails))
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('main', nargs='?', help='exefs/main')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--origin', type=int, default=STOCK_ORIGIN_Y,
                    help='ORIGIN_Y to write (224 = stock, 232 = centred)')
    ap.add_argument('--window', type=int, default=240,
                    help='field window HALF-height [0xCFF200] '
                         '(224 = stock/letterboxed, 240 = open)')
    ap.add_argument('--height', type=int, default=480,
                    help='field_set_mode viewport height (448 = stock)')
    a = ap.parse_args(argv)

    if a.verify:
        return verify(a.main)
    if not a.main:
        ap.error('need a path to exefs/main')
    if a.apply or a.revert:
        return apply(a.main, a.origin, a.height, revert=a.revert,
                     dry_run=a.dry_run, window=a.window)
    show(_text(a.main))
    return 0


if __name__ == '__main__':
    sys.exit(main())
