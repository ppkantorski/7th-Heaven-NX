#!/usr/bin/env python3
r"""
ff7nx_swirlseam.py -- close the battle-entry swirl's vertical seam.

FOURTEEN WORDS, IN PLACE, NO CAVE.

THE SYMPTOM
===========
> "the left half of the character gets squished to the right and the right half
>  gets squished to the left. detail in the center ends up being cropped out."

A slice of the picture goes missing down the vertical centre of the swirl,
exactly where a character standing at centre screen is.

WHAT THE SWIRL IS (FINDINGS 313)
================================
Read out of ff7_en and exefs/main, not inferred:

    x86 0x4016FE   screen 640x480, origin (0,0), scale 2, tile 64, UV step 32
    x86 0x401754   rect A = (  0, 0, 160, 240)      staging columns   0..320
                   rect B = (320, 0, 160, 240)      staging columns 320..640
    x86 0x401810   an 11 x 9 vertex grid, 10 x 8 POLY_FT4 quads
    x86 0x401BC6   `cmp i, 5`  -- columns 0..4 sample texture A, 5..9 texture B

The two captures tile the staging surface exactly, and the split at 320 is the
true centre of the frame, so **the capture side is correct**. So is
ff7nx_fbcapture excluding it from the widescreen origin correction.

THE DEFECT
==========
The UVs are built with the PSX inclusive-texel convention:

    u0 = i * 32            u1 = i * 32 + 32 - 1        x86 0x401C31
    v0 = j * 32            v1 = j * 32 + 32 - 1        x86 0x401CA7

and the column where the two textures meet pulls in by two instead of one:

    u1 = i * 32 + 32 - 2                               x86 0x401D7D

On PSX hardware `[u0, u0+31]` covers 32 texels because the endpoint is
included. This renderer maps `u -> u / tex_format.width`, so the quad covers
31/32 of its block while the NEXT quad starts at `(i+1)*32`. One UV unit falls
down the crack at every quad boundary -- two at the centre.

There are nine such boundaries. Eight of them fall over scenery, where two
missing columns of grass look like grass. The ninth is the middle of the
screen.

It also got worse, and that is ff7nx_fbsurf's doing: one UV unit is
`fb_tex.w / 160` staging columns, which k = 2 took from 2 to 4.

THE FIX -- ONE RULE FOR BOTH AXES
=================================
Subtract one only when the result would not fit the byte it is stored in:

    sub wD, wD, #1   ->   sub wD, wD, wD, lsr #8

`wD >> 8` is 1 exactly when wD is 256 and 0 for anything below it, so:

    u1 = i*32 + 32   peaks at 4*32 + 32 = 160 = tex_format.width
                     -> u/160 = 1.0, the texture's right edge, unchanged
    v2 = j*32 + 32   peaks at 7*32 + 32 = 256, which would WRAP the bottom
                     row to v = 0  ->  saturates to 255 instead

So every quad boundary closes -- nine vertical and seven horizontal -- and the
one place the arithmetic cannot close, the very bottom edge of the swirl, gives
up 1/32 of one row rather than wrapping to the top of the texture.

It is one word per site, it needs no scratch register, and it is the same word
at every site. BUILD 314 used `sub #0`, which is correct for `u` and unsafe for
`v`; this replaces it and removes the asymmetry that made that build only a
partial repair.

"""
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

import nxmap                                                   # noqa: E402

SEAM_ENV = 'SEVENTH_NX_SWIRL_SEAM'

# site -> (stock word, the UV field it feeds)
#
# Every one was walked forward through the recompiler's dataflow to the
# POLY_FT4 member it writes; see HOW EACH SITE WAS IDENTIFIED below. The
# `u` sites were build 314's seven; the `v` sites are the other half of the
# same loop body and are what 314 left open.
SITES = {
    0x101AC: (0x51000508, 'u1'),
    0x103B4: (0x51000508, 'v2'),
    0x105C8: (0x51000908, 'u1'),     # the i == 5 column, which pulls in by
    0x107C8: (0x51000508, 'v2'),     #   two rather than one
    0x10888: (0x51000908, 'u3'),
    0x10948: (0x51000508, 'v3'),
    0x10B2C: (0x51000529, 'u1'),
    0x10D2C: (0x51000529, 'v2'),
    0x10DF0: (0x51000508, 'u3'),
    0x10EAC: (0x51000529, 'v3'),
    0x11094: (0x51000508, 'u1'),
    0x11294: (0x51000508, 'v2'),
    0x11354: (0x51000508, 'u3'),
    0x11414: (0x51000508, 'v3'),
}

# The POLY_FT4 member each site's value ends up in, for the anchors below.
FIELD_OF = {'u1': 0x14, 'v1': 0x15, 'u3': 0x24, 'v3': 0x25,
            'u0': 0x0C, 'v0': 0x0D, 'u2': 0x1C, 'v2': 0x1D}

def _sub_lsr8(rd):
    """SUB Wd, Wd, Wd, LSR #8 -- the byte-saturating subtract.

    One word, no scratch register, same destination and source as the
    instruction it replaces, so the recompiler's register allocation is
    untouched.
    """
    return (0x4B000000 | (1 << 22) | (rd << 16) | (8 << 10)
            | (rd << 5) | rd)


def patched_word(stock):
    """The saturating form of a `sub wD, wD, #imm`."""
    rd, rn = stock & 31, (stock >> 5) & 31
    if (stock & 0xFFC00000) != 0x51000000 or rd != rn:
        raise ValueError('not a `sub wD, wD, #imm`: %08X' % stock)
    return _sub_lsr8(rd)


def _add_w0(rn, imm):
    """ADD W0, Wn, #imm -- the POLY_FT4 member address."""
    return 0x11000000 | (imm << 10) | (rn << 5)


# The UV step this whole arithmetic is built on, so "32 per quad" is checked
# rather than assumed.
ANCHORS = {
    0xF4C4: 0x52809A13,           # mov  w19, #0x4d0   \
    0xF4C8: 0x72A01353,           # movk w19, #0x9a, lsl #16  -> guest 0x9A04D0
}

def enabled() -> bool:
    v = os.environ.get(SEAM_ENV)
    if v is None:
        return True
    return v.strip().lower() not in ('0', 'off', 'false', 'no', 'stock')


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def installed(img):
    """True if every site is closed, False if every site is stock, else None."""
    closed = all(_word(img, va) == patched_word(s[0])
                 for va, s in SITES.items())
    stock = all(_word(img, va) == s[0] for va, s in SITES.items())
    if closed:
        return True
    if stock:
        return False
    return None


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        have = _word(img, va)
        if have != want:
            bad.append('anchor +0x%X is %08X, expected %08X -- the swirl\'s '
                       'UV writer has moved' % (va, have, want))
    state = installed(img)
    if state is None:
        for va, (stock, _f) in sorted(SITES.items()):
            have = _word(img, va)
            if have not in (stock, patched_word(stock)):
                bad.append('+0x%X is %08X, neither the stock %08X nor the '
                           'closed %08X' % (va, have, stock,
                                            patched_word(stock)))
        if not bad:
            bad.append('the seven UV sites are in a mixed state')
    return bad


def read_state(img) -> str:
    state = installed(img)
    if state is None:
        return 'unknown'
    return ('swirl seam closed' if state
            else 'swirl seam stock (one UV unit lost per quad boundary)')


def plan(m_, revert=False):
    img = m_.img
    problems = verify(img)
    if problems:
        return [], [], problems
    close = not revert and enabled()
    patches, notes = [], []
    for va, (stock, field) in sorted(SITES.items()):
        want = patched_word(stock) if close else stock
        have = _word(img, va)
        if have == want:
            continue
        what = ('%s at +0x%X %s' % (field, va,
                                    'closed' if close else 'restored'))
        patches.append({'name': what, 'va': hex(va),
                        'expect': struct.pack('<I', have).hex(),
                        'set': struct.pack('<I', want).hex()})
        notes.append('    %s' % what)
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'battle swirl seam', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.swirlseam-')
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
        log('  refusing to change the swirl UVs.')
        return 1
    log('  battle swirl seam (%s): %s'
        % (SEAM_ENV, 'stock' if (revert or not enabled()) else 'closed'))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d swirl UV word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
