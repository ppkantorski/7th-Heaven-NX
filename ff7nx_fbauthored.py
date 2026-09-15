#!/usr/bin/env python3
r"""
ff7nx_fbauthored.py -- k must scale the SOURCE, never the TEXTURE.

THREE WORDS, IN PLACE, NO CAVE, and identity at k = 1.

WHAT WAS WRONG
==============
`ff7nx_fbsurf`'s k grows the staging surface AND, because the same two
reciprocal divides serve both, it grows `fb_tex.w` and `fb_tex.h` with it:

    +10DBC7C  add   w9, w21, w21, lsl #2     w * 5
    +10DBC80  lsl   w9, w9, #7               w * 640     -> #7+log2(k) by fbsurf
    +10DBC84  umull x9, w9, w12              * 0xCCCCCCCD
    +10DBC88  lsr   x9, x9, #0x29            / 640       -> fb_tex.w = w * k

    +10DBC64  mul   w10, w19, w13            h * 480     -> *480k by fbsurf
    +10DBC68  umull x8, w10, w8              * 0x88888889
    +10DBC6C  lsr   x8, x8, #0x28            / 480       -> fb_tex.h = h * k

So at k = 4 Kujata's 256 x 256 capture becomes a **1024 x 1024 texture**.

That is fatal, and this project already wrote down why, in
`ff7nx_fbcapture`'s header (FINDINGS-308) -- I just never joined the two
halves:

    * `fb_tex.w` IS the created texture's real pixel width. The uploader
      picks it over `tex_format.width` whenever the header carries version
      100 (+0x10D6E50..+0x10D6E80, an anchor in that file)
    * Kujata's disc addresses that texture with UVs **hard-coded in the
      executable as absolute texels**: `u = 128 + 4j*sin(theta)`, radius
      clamped to 127, so u and v only ever run 1..255

FINDINGS-308 proved the "absolute texels" half on hardware from the other
side: with `fb_tex.w` = 191 every texel with u > 191 fell off the right edge
and sampled BLACK. Absolute, not normalised.

Put the two together and k = 4 means:

    the texture is           1024 x 1024
    the disc can address     the top-left 256 x 256 of it
    -> the field shows       a quarter of a quarter -- 1/16 of the capture,
                             magnified onto the whole disc

Run it out to real coordinates. A staging column c is game
x = c*854/(640k) - 107:

    k = 1   texels 1..255 <- staging cols   80.. 271 of  640   game x 0..255
    k = 4   texels 1..255 <- staging cols  320.. 511 of 2560   game x 0.. 64

**At k = 4 the disc is showing a 64-unit-wide sliver of an 854-unit frame.**
Sixty-four units, blown up over the entire field. Every symptom the operator
reported falls straight out of that one number:

    "extremely low res, blocky"          13x magnification of a sliver
    "off to the left / behind the enemy" the sliver is the LEFT edge, and its
                                         centre sits at game x = 32, not 427
    "covering way too much of the field" one sliver stretched over the disc
    "my original build was ok"           k = 1 is the authored geometry
    axes disagree ~4.5 : 1               u carries the 640/854 squeeze, v does
                                         not, so the two crops differ by that
                                         ratio on top of the shared k

And it retires FINDINGS-354's "the capture surface is 88 % black". It is
not. That number divided the content by the 766 columns the *texture* spans;
the disc can only reach 191 of them, and 150 of those 191 hold picture --
78 %, not 20 %. The surface was never the problem. The texture size was.

THE FIX
=======
Keep the texture at its authored size and spend k on the SOURCE instead.
Three words, and every one of them is the identity at k = 1:

    +0x10DBC88  lsr x9, x9, #0x29  ->  #0x29 + log2(k)      fb_tex.w stays w
    +0x10DBC6C  lsr x8, x8, #0x28  ->  #0x28 + log2(k)      fb_tex.h stays h
    +0x10D7234  add x11, x11, x9   ->  add x11, x11, x9, lsl #log2(k)

The first two are safe because the divide is a reciprocal multiply by a
constant that is exact for the whole 32-bit range: 0xCCCCCCCD >> 41 is 1/640,
so >> 43 is 1/2560, and 0x88888889 >> 40 is 1/480, so >> 42 is 1/1920. The
numerator is already k times larger (fbsurf's RX/RW/RH words), so the two
cancel and w, h come out at exactly what the caller asked for. `fb_tex.x` and
`fb_tex.y` keep their own divides untouched and therefore keep scaling with
k, which is what they must do -- they are surface coordinates.

The third is the row walk. `x9` is one source row (stride * 4) and the loop
adds one per destination row; with h no longer scaled it must step k source
rows to cover the same picture.

The fourth piece is not a word, it is a constant: `ff7nx_fbresample`'s
column step becomes `x + floor(i * 640k / 854)` instead of `640 / 854`, via
`column_numerator()` below. Same reason as the row walk, same cancellation.

WHAT THE OPERATOR SHOULD SEE
============================
The framing returns to exactly what k = 1 draws -- the region Kujata
authored, correctly centred -- while every texel is now chosen from a 4x
denser surface instead of one that was already resampled down to 640 x 480
first. Position and scale identical to the build that was "ok"; the
double-resample gone.

WHAT THIS DOES NOT DO
=====================
It does not raise the texel count. 256 x 256 is the ceiling, authored into
the executable, and no surface size can move it. The only thing that can is
rewriting the disc's UV generator (x86 0x500A5E) to address a wider texture
-- `u = 512 + 16j*sin(theta)` for a 1024-wide one. That is a real and
reachable next step, and it is the FIRST time this project has had a
correctly-framed base to try it from.
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

# the two reciprocal divides that turn a k-scaled numerator back into the
# caller's own w and h
WDIV_SITE = 0x10DBC88               # lsr x9, x9, #0x29     -> fb_tex.w
WDIV_STOCK = 0xD369FD29
HDIV_SITE = 0x10DBC6C               # lsr x8, x8, #0x28     -> fb_tex.h
HDIV_STOCK = 0xD368FD08

# the CPU loop's source row advance
ROW_SITE = 0x10D7234                # add x11, x11, x9
ROW_STOCK = 0x8B09016B

WDIV_BASE = 0x29                    # log2(2^41 / 640) reciprocal shift
HDIV_BASE = 0x28                    # log2(2^40 / 480)

SHIFT = {1: 0, 2: 1, 4: 2, 8: 3}    # log2(k), matching ff7nx_fbsurf.LEGAL

# Everything the three words depend on. Two of these are the multiplies that
# make the cancellation exact, and two are the stores that prove which fields
# are being written.
ANCHORS = {
    0x10DBC40: 0x1B0D7F29,   # mul   w9, w25, w13            y * 480k
    0x10DBC64: 0x1B0D7E6A,   # mul   w10, w19, w13           h * 480k
    0x10DBC68: 0x9BA87D48,   # umull x8, w10, w8             * 0x88888889
    0x10DBC70: 0xD369FD6B,   # lsr   x11, x11, #0x29         fb_tex.x, UNTOUCHED
    0x10DBC74: 0xD368FD29,   # lsr   x9, x9, #0x28           fb_tex.y, UNTOUCHED
    0x10DBC78: 0x2902A74B,   # stp   w11, w9, [x26, #0x14]   x, y
    0x10DBC84: 0x9BAC7D29,   # umull x9, w9, w12             * 0xCCCCCCCD
    0x10DBC94: 0x2903A349,   # stp   w9, w8, [x26, #0x1c]    w, h
    0x10D71E8: 0xAA1F03EC,   # mov   x12, xzr                the row top
    0x10D7220: 0xB9401E8C,   # ldr   w12, [x20, #0x1c]       fb_tex.w
    0x10D7224: 0x531E758C,   # lsl   w12, w12, #2            the DEST row
    0x10D7230: 0x8B0C0000,   # add   x0, x0, x12             dest advance
    0x10D7238: 0x54FFFD8B,   # b.lt  #0x10d71e8
}

# the choice that makes fb_tex.w the texture's real width -- the load-bearing
# fact this whole module rests on. Anchored here so it cannot drift silently.
SIZE_CHOICE = {
    0x10D6E68: 0x7101911F,   # cmp  w8, #0x64        FB_TEX_VERSION ?
    0x10D6E6C: 0x9A8A0128,   # csel x8, x9, x10, eq  &fb_tex.w : &tex_format.w
    0x10D6E74: 0xB9400117,   # ldr  w23, [x8]        the width it creates with
}


def _lsr64(rd, rn, sh):
    """LSR Xd, Xn, #sh -- the UBFM alias, the form the stock words use."""
    return 0xD3400000 | (sh << 16) | (63 << 10) | (rn << 5) | rd


def _add_lsl64(rd, rn, rm, sh):
    """ADD Xd, Xn, Xm, LSL #sh."""
    return 0x8B000000 | (rm << 16) | (sh << 10) | (rn << 5) | rd


assert _lsr64(9, 9, WDIV_BASE) == WDIV_STOCK
assert _lsr64(8, 8, HDIV_BASE) == HDIV_STOCK
assert _add_lsl64(11, 11, 9, 0) == ROW_STOCK


AUTHORED_ENV = 'SEVENTH_NX_FB_AUTHORED'


def enabled() -> bool:
    """OFF by default, and BUILD 357 is why.

    These three words are in the SHARED rect arithmetic and the SHARED row
    walk, so they reach every framebuffer capture in the game. But the
    compensating column step -- `x + floor(i * 640k / 854)` -- lives inside
    ff7nx_fbresample's cave, BEHIND the gate that says "this is Kujata's".
    Every other capture therefore got a texture k times narrower with no
    change to how its columns are read: a 1/k crop of the top-left corner,
    horizontally squashed. That is the regression the operator saw, and it is
    mine.

    Fixing it properly means giving the ungated stock loop a column step too,
    which is a different and larger change. Until then this is opt-in, and
    the supported configuration is `SEVENTH_NX_FB_SURFACE=1`, where every one
    of these words is the identity anyway.
    """
    return os.environ.get(AUTHORED_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def cancels_surface_scale() -> bool:
    """Whether `fb_tex.w` still carries the surface k.

    ff7nx_fbresample's gate asks this so the two can never disagree about
    what fb_tex.w is -- a gate that assumes the wrong answer matches nothing
    and silently disengages every resample body.
    """
    return enabled() and scale() != 1


def scale() -> int:
    """The surface k. One source of truth; this module never has its own."""
    try:
        import ff7nx_fbsurf
        return ff7nx_fbsurf.scale()
    except Exception:                                          # noqa: BLE001
        return 1


def column_numerator(virt_w: int = 640) -> int:
    """What `ff7nx_fbresample`'s step must divide by 854.

    The destination is `virt_w` game units wide however big the surface is,
    so the columns a destination texel must step over grow with k exactly as
    the surface does.
    """
    return virt_w * (scale() if cancels_surface_scale() else 1)


def words_for(k: int) -> dict:
    """The three words, in site order. k = 1 is the stock image verbatim."""
    s = SHIFT[k]
    return {ROW_SITE: _add_lsl64(11, 11, 9, s),
            HDIV_SITE: _lsr64(8, 8, HDIV_BASE + s),
            WDIV_SITE: _lsr64(9, 9, WDIV_BASE + s)}


assert words_for(1) == {ROW_SITE: ROW_STOCK,
                        HDIV_SITE: HDIV_STOCK,
                        WDIV_SITE: WDIV_STOCK}


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def verify(img) -> list:
    bad = []
    for name, table in (('anchor', ANCHORS), ('size choice', SIZE_CHOICE)):
        for va, want in sorted(table.items()):
            got = _word(img, va)
            if got != want:
                bad.append('%s +0x%X is %08X, expected %08X'
                           % (name, va, got, want))
    legal = {k: words_for(k) for k in SHIFT}
    for va in (ROW_SITE, HDIV_SITE, WDIV_SITE):
        got = _word(img, va)
        if got not in {w[va] for w in legal.values()}:
            bad.append('+0x%X is %08X, which is no legal k for this site'
                       % (va, got))
    return bad


def read_state(img) -> str:
    ks = [k for k in sorted(SHIFT)
          if all(_word(img, va) == w for va, w in words_for(k).items())]
    if len(ks) == 1:
        return 'source-scaled k=%d' % ks[0]
    if ks:
        return 'source-scaled k=%s' % '/'.join(str(k) for k in ks)
    return 'MIXED -- the three words disagree about k'


def plan(m, revert=False):
    img = m.img
    problems = verify(img)
    if problems:
        return [], [], problems
    k = scale() if (enabled() and not revert) else 1
    want = words_for(k)
    patches = []
    for va in sorted(want):
        have = _word(img, va)
        if have == want[va]:
            continue
        patches.append({'name': 'authored capture size (%s)'
                        % {ROW_SITE: 'source row walk',
                           HDIV_SITE: 'fb_tex.h', WDIV_SITE: 'fb_tex.w'}[va],
                        'va': hex(va),
                        'expect': struct.pack('<I', have).hex(),
                        'set': struct.pack('<I', want[va]).hex()})
    notes = []
    if patches:
        notes.append('    fb_tex.w/.h stay the authored size; k = %d is spent '
                     'on the source instead' % k)
        notes.append('    destination texel i <- staging column '
                     'x + floor(i * %d / 854), row j <- source row j * %d'
                     % (column_numerator(), k))
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'authored capture size', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fbauthored-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply_all(main, revert=False, log=print):
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to change the capture rect arithmetic.')
        return 1
    log('  capture texture size (authored, k scales the source):')
    for n in notes:
        log(n)
    if not patches:
        log('    already %s' % read_state(m.img))
        return 0
    _write(main, patches, log)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--state', action='store_true')
    a = ap.parse_args(argv)
    if a.state:
        m = nxmap.Main(a.main)
        print(read_state(m.img))
        for p in verify(m.img):
            print('  ! ' + p)
        return 0
    return apply_all(a.main, revert=a.revert)


if __name__ == '__main__':
    sys.exit(main())
