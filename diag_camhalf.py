#!/usr/bin/env python3
r"""
diag_camhalf -- the vertical half-view, measured. NOTHING IS WRITTEN.

=============================================================================
WHY THIS EXISTS
=============================================================================
HANDOFF-93 proposed that the uncrop was compensated horizontally and never
vertically, and asked for a measurement to separate three mechanisms.  The
measurement is no longer needed for the *mechanism* -- Cosmos's own
`config.toml` settles it -- but it IS still needed for the *site*, and this
script produces both halves so the two are never confused again.

THE MECHANISM, FROM COSMOS'S DATA RATHER THAN FROM THEORY
---------------------------------------------------------
FFNx clamps the field camera vertically to a hard, un-gated **120**:

    background.cpp:447   if (point->y > camera_range.bottom - 120) ...
    background.cpp:449   if (point->y < camera_range.top    + 120) ...

(note: NOT `background.cpp:133`'s `enable_uncrop ? 120 : 112` -- that constant
is the layer-3/4 wrap.  HANDOFF-93 cited the wrong line for the right number.
The clamp is 120 unconditionally.)

Cosmos authors `top`/`bottom` per field AGAINST that 120.  From
CONFIG/widescreen/config.toml:

    [md8_1]   top -120  bottom  120      240 units -- exactly 2 x 120
    [md8_3]   top -200  bottom  200      400 units
    [md8_5]   top -120  bottom  112      232 units -- SHORTER than 2 x 120

Our port is stock FF7, which clamps to **112**, because stock FF7's field view
is 224 units tall and 224/2 = 112.  `ff7nx_letterbox`'s uncrop made the view
240 units tall.  The half-view became 120 and the clamp did not move with it.

    stock    view 224   half 112   range +/-120  ->  visible [-120, 120]  flush
    ours     view 240   half 120   clamp +/-112  ->  visible [-128, 128]  8 over

Eight range units.  Camera range is in half-resolution units, 3 device px each
at 720p:

    8 x 3 = 24 device rows

WORKED, FOR THE SCENE THAT REPORTED IT
--------------------------------------
Sector 8, the pan down from the LOVELESS billboards.  `md8_3`, range
top -200 .. bottom 200:

    clamp 112   camera travel y in [-88, +88]   visible [-208, +208]   8 over
    clamp 120   camera travel y in [-80, +80]   visible [-200, +200]   flush

The band can only appear where a script actually drives the camera to the
vertical limit of a range taller than the view.  Most fields never move the
camera vertically at all, which is exactly the reported rarity -- and it means
other scenes later in the game have it too.  It is not a property of md8_3.

WHY THE FIX IS *NOT* A DATA BAKE, WHICH IS THE OPPOSITE OF THE HORIZONTAL CASE
------------------------------------------------------------------------------
`ff7nx_ws` bakes the horizontal compensation into section 8 because FFNx's
`half_width` is range-dependent -- `160 + min(53, size/2 - 160)` -- so it
cannot be an immediate.  Vertically there is no range term: it is the bare
constant 120.  And the data is ALREADY authored for 120 by the mod.  Pulling
`top`/`bottom` in by 8 in the archive would compensate a second time in the
wrong direction and stop the camera 8 units short of art that is there.

    ONE COMPENSATION, ONE OWNER -- and here the owner is the CODE constant.

So `ff7nx_ws.clamped_range()` is CORRECT to leave `top` and `bottom` alone.
That is not the bug.  The bug is that nothing raised 112 to 120.

WHAT IS STILL UNKNOWN, AND IT IS THE ONLY THING
------------------------------------------------
Where the 112 lives in the ARM, and in what order the two compares run.  Both
matter, and neither can be guessed:

  * `field_clip_with_camera_range`        x86 0x6438F6 -> main +0xA11530
  * `field_layer3_clip_with_camera_range` x86 0x643628 -> main +0xA108A0
    (FFNx's `float_sub_643628`; it uses `top + 120` / `bottom - 120` six
     times across its two projection branches, background.cpp:500-514)

The order decides whether a guard is needed.  `md8_5` is `top -120,
bottom 112` -- only 232 units, SHORTER than 2 x 120 -- so at 120 the bounds
invert: lo = top+120 = 0, hi = bottom-120 = -8.  FFNx does not guard this.
It applies bottom first and top second, so the last write wins and y lands on
`top + 120`.  If our port compares in the same order, changing the immediates
in place reproduces FFNx exactly and NO GUARD IS NEEDED.  If it compares the
other way round, a guard is required or those fields pin to the wrong edge.

`--scan` answers that.  Run it and paste the output; it costs no build and no
boot.  Nothing here writes to anything.

=============================================================================
USAGE
=============================================================================
    python3 diag_camhalf.py sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main
    python3 diag_camhalf.py <main> --ranges <flevel.lgp>
    python3 diag_camhalf.py --ranges <flevel.lgp>        (archive half only)

`--window N` widens the disassembly window if the function runs longer than
the default (recompiled ARM is roughly 4-6x the x86 instruction count).
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '7th_heaven_nx')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# --------------------------------------------------------------- the sites
CLIP_VA = 0xA11530          # field_clip_with_camera_range,        x86 0x6438F6
LAYER3_VA = 0xA108A0        # field_layer3_clip_with_camera_range, x86 0x643628

STOCK_HALF_H = 112          # 0x70 -- stock FF7, a 224-unit view
FFNX_HALF_H = 120           # 0x78 -- FFNx, and what a 240-unit view needs
STOCK_HALF_W = 160          # 0xA0 -- for context only; NOT touched by this

RANGE_LEFT, RANGE_TOP, RANGE_RIGHT, RANGE_BOTTOM = 0x0C, 0x0E, 0x10, 0x12

PX_PER_RANGE_UNIT_720P = 3.0

DEFAULT_WINDOW = 0x600      # bytes; ~384 instructions


# --------------------------------------------------------------- decoding
def _u32(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _sxt(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


_COND = ['eq', 'ne', 'hs', 'lo', 'mi', 'pl', 'vs', 'vc',
         'hi', 'ls', 'ge', 'lt', 'gt', 'le', 'al', 'nv']


def decode(w, va):
    """
    A deliberately small decoder: only the shapes an integer clamp can use.

    It is not a disassembler and does not pretend to be.  Anything it does not
    recognise prints as `.word`, which is honest -- a wrong mnemonic would be
    worse than none, and this project has already paid for one confident
    misreading.
    """
    rd, rn, rm = w & 31, (w >> 5) & 31, (w >> 16) & 31
    imm12 = (w >> 10) & 0xFFF
    sh = (w >> 22) & 3
    top = w & 0x7F800000

    def _r(n, sf):
        return ('x' if sf else 'w') + (str(n) if n != 31 else 'zr')

    sf = (w >> 31) & 1

    # add/sub/cmp/cmn immediate
    if (w & 0x7F000000) in (0x11000000, 0x51000000, 0x31000000, 0x71000000):
        op = 'sub' if (w & 0x40000000) else 'add'
        setflags = bool(w & 0x20000000)
        val = imm12 << (12 if sh == 1 else 0)
        if setflags and rd == 31:
            return '%s %s, #%d' % ('cmp' if op == 'sub' else 'cmn',
                                   _r(rn, sf), val), val
        return '%s%s %s, %s, #%d' % (op, 's' if setflags else '',
                                     _r(rd, sf), _r(rn, sf), val), val

    # movz / movk
    if top in (0x52800000, 0x72800000, 0x52A00000, 0x72A00000):
        imm16 = (w >> 5) & 0xFFFF
        hw = (w >> 21) & 3
        kind = 'movk' if (w & 0x20000000) else 'movz'
        if hw:
            return '%s %s, #%d, lsl #%d' % (kind, _r(rd, sf), imm16, hw * 16), None
        return '%s %s, #%d' % (kind, _r(rd, sf), imm16), imm16

    # add/sub/cmp register
    if (w & 0x7F200000) in (0x0B000000, 0x4B000000, 0x2B000000, 0x6B000000):
        op = 'sub' if (w & 0x40000000) else 'add'
        setflags = bool(w & 0x20000000)
        if setflags and rd == 31:
            return 'cmp %s, %s' % (_r(rn, sf), _r(rm, sf)), None
        if op == 'sub' and rn == 31:
            return 'neg %s, %s' % (_r(rd, sf), _r(rm, sf)), None
        return '%s%s %s, %s, %s' % (op, 's' if setflags else '',
                                    _r(rd, sf), _r(rn, sf), _r(rm, sf)), None

    # csel / csinc
    if (w & 0x7FE00000) == 0x1A800000:
        cond = (w >> 12) & 15
        o2 = (w >> 10) & 3
        name = {0: 'csel', 1: 'csinc'}.get(o2, 'cs??')
        return '%s %s, %s, %s, %s' % (name, _r(rd, sf), _r(rn, sf),
                                      _r(rm, sf), _COND[cond]), None

    # b.cond
    if (w & 0xFF000010) == 0x54000000:
        off = _sxt((w >> 5) & 0x7FFFF, 19) * 4
        return 'b.%s #%#x' % (_COND[w & 15], va + off), None

    # b / bl
    if (w & 0x7C000000) == 0x14000000:
        off = _sxt(w & 0x03FFFFFF, 26) * 4
        return '%s #%#x' % ('bl' if (w & 0x80000000) else 'b', va + off), None

    # cbz / cbnz
    if (w & 0x7E000000) == 0x34000000:
        off = _sxt((w >> 5) & 0x7FFFF, 19) * 4
        return '%s %s, #%#x' % ('cbnz' if (w & 0x01000000) else 'cbz',
                                _r(rd, sf), va + off), None

    # ldrsh / ldrh / strh  (the camera range is four shorts)
    for mask, val, name, scale in (
            (0xFFC00000, 0x79C00000, 'ldrsh', 2),
            (0xFFC00000, 0x79400000, 'ldrh', 2),
            (0xFFC00000, 0x79000000, 'strh', 2),
            (0xFFC00000, 0xB9400000, 'ldr', 4),
            (0xFFC00000, 0xB9000000, 'str', 4),
            (0xFFC00000, 0xF9400000, 'ldr', 8),
            (0xFFC00000, 0xF9000000, 'str', 8)):
        if (w & mask) == val:
            off = imm12 * scale
            wide = scale == 8
            return '%s %s, [%s, #%#x]' % (name, _r(rd, wide),
                                          _r(rn, 1), off), None

    # scvtf / fcvtzs / fmov -- the float clamp shapes, named but not decoded
    if (w & 0x7F20FC00) == 0x1E220000:
        return 'scvtf s%d, %s' % (rd, _r(rn, sf)), None
    if (w & 0x7F20FC00) == 0x1E380000:
        return 'fcvtzs %s, s%d' % (_r(rd, sf), rn), None
    if (w & 0xFFE0FC00) == 0x1E204000:
        return 'fmov s%d, s%d' % (rd, rn), None
    if (w & 0xFF000000) == 0x1C000000:
        off = _sxt((w >> 5) & 0x7FFFF, 19) * 4
        return 'ldr s%d, #%#x   <- literal pool' % (rd, va + off), None

    return '.word %#010x' % w, None


# --------------------------------------------------------------- the scan
def scan_function(t, va, name, window, log=print):
    """
    Print the window and flag every site that could carry the half-view.

    Flags, and why each one:
      HALF-H   an immediate of 112 -- a candidate for the 112 -> 120 change
      120      an immediate of 120 -- already compensated, or a coincidence
      HALF-W   an immediate of 160 -- the horizontal half, for orientation
      RANGE    a halfword load at +0x0C/+0x0E/+0x10/+0x12 -- names the axis
    """
    log('')
    log('=' * 74)
    log('%s   main +%#09x   (%d bytes)' % (name, va, window))
    log('=' * 74)
    hits = {'half_h': [], 'ffnx_h': [], 'half_w': [], 'range': []}
    for off in range(0, window, 4):
        cur = va + off
        try:
            w = _u32(t, cur)
        except struct.error:
            log('  ! ran off the end of .text at +%#x' % cur)
            break
        text, imm = decode(w, cur)
        flag = ''
        if imm == STOCK_HALF_H:
            flag = '   <<< HALF-H 112'
            hits['half_h'].append(cur)
        elif imm == FFNX_HALF_H:
            flag = '   <<< 120 (already?)'
            hits['ffnx_h'].append(cur)
        elif imm == STOCK_HALF_W:
            flag = '   <   half-width 160'
            hits['half_w'].append(cur)
        for label, roff in (('left', RANGE_LEFT), ('top', RANGE_TOP),
                            ('right', RANGE_RIGHT), ('bottom', RANGE_BOTTOM)):
            if text.startswith('ldrsh') and (', #%#x]' % roff) in text:
                flag += '   <   camera_range.%s' % label
                hits['range'].append((cur, label))
        log('  +%#09x  %08X  %-38s%s' % (cur, w, text, flag))
    return hits


def report_scan(hits, log=print):
    log('')
    log('-' * 74)
    log('WHAT THE SCAN SETTLES')
    log('-' * 74)
    n112 = len(hits['half_h'])
    n120 = len(hits['ffnx_h'])
    log('  immediates of 112 (0x70) : %d   %s'
        % (n112, ', '.join('+%#x' % v for v in hits['half_h']) or '-'))
    log('  immediates of 120 (0x78) : %d   %s'
        % (n120, ', '.join('+%#x' % v for v in hits['ffnx_h']) or '-'))
    log('  immediates of 160 (0xA0) : %d   %s'
        % (len(hits['half_w']),
           ', '.join('+%#x' % v for v in hits['half_w']) or '-'))
    order = [lab for _v, lab in hits['range']]
    log('  camera_range reads, IN ORDER: %s' % (' -> '.join(order) or '-'))
    log('')
    if order:
        try:
            i_b, i_t = order.index('bottom'), order.index('top')
            if i_b < i_t:
                log('  bottom is compared BEFORE top -- same order as FFNx')
                log('  (background.cpp:447 then :449).  On an inverted range')
                log('  the top bound wins, exactly as FFNx.  NO GUARD NEEDED.')
            else:
                log('  top is compared BEFORE bottom -- the OPPOSITE of FFNx.')
                log('  On an inverted range the BOTTOM bound would win, which')
                log('  is not what FFNx does.  A GUARD IS REQUIRED for the')
                log('  fields --ranges lists as INVERTS.')
        except ValueError:
            log('  ! top and bottom were not both seen in this window --')
            log('    widen it with --window and re-run before concluding.')
    if n112 == 0:
        log('')
        log('  ! NO 112 IMMEDIATE IN THIS WINDOW.  Do not conclude the clamp')
        log('    is absent -- the recompiler may hold it in a register or a')
        log('    float literal.  Widen --window first, then look for the')
        log('    `ldr sN, #...  <- literal pool` lines above.')


# --------------------------------------------------------------- the archive
def report_ranges(flevel, log=print, limit=40):
    """
    Per-field: does raising the clamp 112 -> 120 change anything, and where
    does it invert?
    """
    import ff7nx_wsdata as W
    log('')
    log('=' * 74)
    log('CAMERA RANGES IN THE BUILT ARCHIVE')
    log('=' * 74)
    ranges = W.camera_ranges(flevel, log=lambda *_a: None)
    log('  %d field(s) read from %s' % (len(ranges), flevel))

    inverts, overshoot, pinned = [], [], []
    for name, r in sorted(ranges.items()):
        top, bottom = int(r['top']), int(r['bottom'])
        height = bottom - top
        travel_112 = (bottom - STOCK_HALF_H) - (top + STOCK_HALF_H)
        travel_120 = (bottom - FFNX_HALF_H) - (top + FFNX_HALF_H)
        if travel_120 < 0:
            inverts.append((name, top, bottom, height, travel_120))
        elif travel_112 > 0:
            overshoot.append((name, top, bottom, height, travel_112))
        else:
            pinned.append(name)

    log('')
    log('  height >= 240, camera can reach a vertical limit : %d field(s)'
        % len(overshoot))
    log('    every one of these shows a %.0f px band at the extreme of a'
        % (8 * PX_PER_RANGE_UNIT_720P))
    log('    vertical pan today, IF a script drives the camera there.')
    log('')
    log('  %-12s %6s %7s %7s %9s %9s' % ('field', 'top', 'bottom',
                                         'height', 'trav@112', 'trav@120'))
    for name, top, bottom, height, tr in overshoot[:limit]:
        log('  %-12s %6d %7d %7d %9d %9d'
            % (name, top, bottom, height, tr, tr - 16))
    if len(overshoot) > limit:
        log('  ... and %d more' % (len(overshoot) - limit))

    log('')
    log('  height < 240, the bounds INVERT at 120 : %d field(s)'
        % len(inverts))
    if inverts:
        log('    these are the ones the compare ORDER decides.  FFNx pins')
        log('    them to top+120; our port must do the same or guard.')
        log('')
        log('  %-12s %6s %7s %7s %9s' % ('field', 'top', 'bottom',
                                         'height', 'invert by'))
        for name, top, bottom, height, tr in inverts[:limit]:
            log('  %-12s %6d %7d %7d %9d' % (name, top, bottom, height, -tr))
        if len(inverts) > limit:
            log('  ... and %d more' % (len(inverts) - limit))

    log('')
    log('  height exactly 240, already pinned at both : %d field(s)'
        % len(pinned))

    for probe in ('md8_3', 'md8_32', 'md8_1', 'md8_5'):
        r = ranges.get(probe) or ranges.get(probe.upper())
        if not r:
            continue
        top, bottom = int(r['top']), int(r['bottom'])
        log('')
        log('  %s   top %d  bottom %d  height %d'
            % (probe, top, bottom, bottom - top))
        for half in (STOCK_HALF_H, FFNX_HALF_H):
            lo, hi = top + half, bottom - half
            if lo > hi:
                log('    half %3d  ->  INVERTED (lo %d > hi %d)' % (half, lo, hi))
                continue
            log('    half %3d  ->  camera y in [%+d, %+d]   visible [%+d, %+d]'
                % (half, lo, hi, lo - half, hi + half))
    return ranges


# --------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Measure the field camera vertical half-view. '
                    'Writes nothing.')
    ap.add_argument('main', nargs='?', help='exefs/main from sdout')
    ap.add_argument('--ranges', metavar='FLEVEL',
                    help='also read camera ranges out of a built flevel.lgp')
    ap.add_argument('--window', type=lambda s: int(s, 0),
                    default=DEFAULT_WINDOW,
                    help='bytes to disassemble per function '
                         '(default %#x)' % DEFAULT_WINDOW)
    ap.add_argument('--only', choices=('clip', 'layer3'),
                    help='scan just one of the two functions')
    a = ap.parse_args(argv)

    if not a.main and not a.ranges:
        ap.error('give a path to exefs/main, or --ranges, or both')

    if a.main:
        import nso_tool
        t = nso_tool.parse_nso(str(a.main))['segments']['.text']['data']
        print('  %s' % a.main)
        print('  .text is %d bytes (%d words)' % (len(t), len(t) // 4))
        hits = {'half_h': [], 'ffnx_h': [], 'half_w': [], 'range': []}
        todo = []
        if a.only in (None, 'clip'):
            todo.append((CLIP_VA, 'field_clip_with_camera_range  '
                                  '(x86 0x6438F6) -- THE NORMAL PATH'))
        if a.only in (None, 'layer3'):
            todo.append((LAYER3_VA, 'field_layer3_clip_with_camera_range  '
                                    '(x86 0x643628) -- parallax projection'))
        for va, name in todo:
            got = scan_function(t, va, name, a.window)
            for k in hits:
                hits[k].extend(got[k])
        report_scan(hits)

    if a.ranges:
        report_ranges(a.ranges)

    print('')
    print('  nothing was written.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
