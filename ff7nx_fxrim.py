#!/usr/bin/env python3
r"""
ff7nx_fxrim.py -- pull Kujata's animated field OFF the last texel of its own
capture texture. ONE WORD, IN PLACE, NO CAVE.

WHY THIS SITE, AND NOT ANOTHER GUESS
====================================
Measured from the TV's own before/after screenshots of build 271 (the numbers
are in FINDINGS-308 12):

  * the effect's coverage on screen runs from y ~ 300 to the bottom and from
    x = 0 to x ~ 1170, and the BLACK sits in a band at x 1180..1245 -- i.e.
    immediately at the OUTER RIM of the disc, not inside it;
  * the solid part of that band is 64 px wide.  One ring of the mesh is
    384 world units = 3 UV texels, and 3 texels at that depth measure ~64 px.
    **The black band is exactly the outermost ring**;
  * higher up the same ring is narrower on screen and the black breaks into
    isolated 15..22 px cells -- one texel each -- which is the "squares" and
    the "lines of black squares";
  * build 271 moved the capture WINDOW from (0,0) to (192,112), which changes
    every pixel the texture contains, and the black did not move at all.  So
    the black is NOT captured content.  It is where the rim samples.

THE MESH, EXACTLY (x86 0x500A5E / ARM +0x472170)
================================================
    r0 = 4 * j                      j = 0..31
    r1 = (4*j + 4 >= 0x80) ? 0x7F : 4*j + 4
    u  = 0x80 + (r * rsin(theta) >> 12)      written as a BYTE
    v  = 0x80 + (r * rcos(theta) >> 12)

So for the outermost ring, j = 31: r0 = 124 and r1 = **127**, and the +u side
of that ring addresses u = 0x80 + 127 = **255** -- the very last texel of a
256-wide capture.  Nothing else in the disc ever reaches it.

That single clamp is the `0x7F` here:

    ARM +0x4722B0   orr w28, wzr, #0x7f      <-- this word

THE EXPERIMENT
==============
`SEVENTH_NX_FX_RIM` replaces it.  Only 124..127 are accepted, because r0 is
NOT clamped and is 124 on that ring -- a smaller cap would invert the ring's
UVs instead of insetting them.

    127   stock
    126   the last texel is never addressed
    125
    124   the outermost ring becomes UV-degenerate: it smears the 124 ring
          outward and NO texel past 124 is ever sampled   <-- the default

    black band GONE       it is the capture texture's edge texels, and the
                          real repair is a proper edge inset (or an edge
                          clamp) rather than this blunt one
    black band UNCHANGED  the rim is black for a geometric reason, not a
                          sampling one, and this whole line is closed -- one
                          word back to 0x7F

At 124 the outer 288 world units of the disc (1.2% of its radius) show a
smear of the ring just inside instead of their own texels.  On a rim that is
already the blurriest part of the picture this is not a visible cost, and it
is the strongest signal one word can buy.

WHAT IT DOES NOT TOUCH
======================
The capture, the resample, the disc's geometry, the ripple, KOTR, or any
other effect.  One clamp in Kujata's UV builder.
"""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                # noqa: E402
import nxmap                                                   # noqa: E402

RIM_ENV = 'SEVENTH_NX_FX_RIM'

HOOK = 0x4722B0
STOCK = 0x32001BFC                 # orr w28, wzr, #0x7f
REG = 28

# r0 = 4*j is NOT clamped and reaches 124 on the ring this clamp applies to,
# so anything below 124 would make r1 < r0 and invert that ring's UVs.
# BUILD 298. The range used to stop at 124 because the rim cap was being
# guessed. It is now derived, and the derived value is below that floor.
#
# The report probe (build 297) came back a single flat grey 122 over the whole
# field: `fb_tex.w` and `tex_format.width` are BOTH 244. Nothing is short --
# the capture and the UV divisor agree. But the mesh's UV bytes are authored
# 0..255:
#
#     u = 0x80 + (r * rsin >> 12),   r <= 127   ->   u in 1..255
#
# and `u_offset = 1 / tex_format.width = 1/244`, so
#
#     u = 255  ->  255/244 = 1.045      4.5% PAST the right edge of the sheet
#     u = 1    ->    1/244 = 0.004      comfortably inside
#
# Only the HIGH-u side overruns, which is exactly what Patrick has described
# from the beginning: "they always show up like a silhouette along the curved
# side, but not on the left side".
#
# The cap that lands the rim exactly on the sheet's edge is therefore
#
#     127 * 244 / 256 = 121
#
# and 121 is the same 244/256 = 0.953 that the disc dial has been cancelling by
# eye at 94%. One measured number, two derived constants, no dial.
# ... AND THE TEST SAID NO. `r0 = 4*j` is 124 on the ring this clamp serves,
# so any cap below 124 makes that ring's UVs run BACKWARDS. 121 is unreachable
# here, and the overrun has to be fixed at the divisor instead -- by making
# `tex_format.width` 256, which is what the mesh's UV bytes are authored
# against. The floor stays where it was.
LEGAL = (124, 125, 126, 127)

# DISPROVEN ON HARDWARE, BUILD 272 (log 271). Cap 124 was applied and the black
# band did not change at all, so the band is NOT the capture texture's edge
# texels and this module is back to stock by default. Kept because reproducing
# a negative cheaply is worth one word.
DEFAULT_CAP = 127

# The branch that reaches this word and the one that skips it, so the build
# refuses rather than writing into a routine a game update moved.
ANCHORS = {
    0x4722A4: 0x6B0A013F,          # cmp  w9, w10        the >= 0x80 test
    0x4722AC: 0x54000061,          # b.ne +0x4722B8      take the other arm
    0x4722B4: 0x14000007,          # b    +0x4722D0      join
    0x4722B8: 0x51001100,          # sub  w0, w8, #4     the other arm
    0x4722C4: 0x1100111C,          # add  w28, w8, #4    r1 = 4*j + 4
}


def _movz(rd, imm16):
    return 0x52800000 | ((imm16 & 0xFFFF) << 5) | rd


def word_for(cap):
    """Stock keeps its own encoding so a revert is byte-identical."""
    return STOCK if cap == 127 else _movz(REG, cap)


def cap() -> int:
    v = os.environ.get(RIM_ENV)
    if v is None:
        return DEFAULT_CAP
    try:
        n = int(v, 0)
    except ValueError:
        return DEFAULT_CAP
    return n if n in LEGAL else DEFAULT_CAP


def enabled() -> bool:
    return cap() != 127


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X' % (va, got, want))
    got = _word(img, HOOK)
    if got not in {word_for(c) for c in LEGAL}:
        bad.append('+0x%X is %08X, which is none of the legal rim caps'
                   % (HOOK, got))
    return bad


def read_state(img) -> str:
    got = _word(img, HOOK)
    for c in LEGAL:
        if word_for(c) == got:
            return 'rim cap %d%s' % (c, ' (stock)' if c == 127 else '')
    return 'unknown'


def plan(m_, revert=False):
    img = m_.img
    problems = verify(img)
    if problems:
        return [], [], problems
    want = word_for(127 if revert else cap())
    have = _word(img, HOOK)
    if have == want:
        return [], [], []
    name = ('restore stock rim cap (127)' if revert
            else 'Kujata field rim UV cap %d' % cap())
    return ([{'name': name, 'va': hex(HOOK),
              'expect': struct.pack('<I', have).hex(),
              'set': struct.pack('<I', want).hex()}],
            ['    %s' % name], [])


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'Kujata field rim UV cap', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fxrim-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply_all(main, revert=False, log=print):
    m_ = nxmap.Main(str(main))
    patches, notes, problems = plan(m_, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to change the field rim UV cap.')
        return 1
    log('  Kujata field rim UV cap (DIAGNOSTIC, %s=%d, stock is 127):'
        % (RIM_ENV, 127 if revert else cap()))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d rim cap word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
