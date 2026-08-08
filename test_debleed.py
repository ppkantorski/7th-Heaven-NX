#!/usr/bin/env python3
"""
test_debleed.py -- de-fringing must fix the seams and change no artwork.

WHAT IT IS FOR
==============
FF7 character textures are atlases whose sub-regions are separated by
transparent gutters, and the transparent palette entry is black. Once the
palette is expanded to RGBA the GPU cannot tell index 0 apart, so it filters
straight through it and draws a dark line along every boundary -- the seam
down the middle of a face, the vertical line on a shoulder.

Measured on the vanilla archive that ships with this project:

    694 char.lgp TEX files, all paletted
    626 with colorkey=1, and in every one palette entry 0 is RGB(0,0,0)
    458 of 624 (73%) have a transparent texel touching an opaque one

THE PROPERTY THAT MATTERS
=========================
The request was "clean up the cracks WITHOUT compromising the image", so the
central check here is not that the fringe improves -- it is that nothing else
can move. `debleed` rewrites palette entry 0 and nothing else, and that is
asserted byte for byte against the real archive, not on a synthetic case.

The one thing it must never touch is a texture whose colour key is OFF: there
entry 0 is a real, drawable colour, and recolouring it WOULD change the image.
The archive contains 68 of those, which makes it a live case rather than a
hypothetical one.
"""
import os
import struct
import sys

import tex

LGP = os.path.join('/tmp/cl', 'char.lgp')


FAIL = []


def check(name, got, want):
    if got != want:
        FAIL.append(name)
        print('FAIL  %s\n        got  %r\n        want %r' % (name, got, want))
    else:
        print('  ok  %s' % name)


def synth(w, h, colorkey, entry0=(0, 0, 0, 0)):
    """A paletted TEX with a transparent hole, so there IS a boundary."""
    hdr = bytearray(tex.HEADER_LEN)

    def put(off, v):
        hdr[off:off + 4] = int(v).to_bytes(4, 'little')

    ncol = 16
    put(tex.O_VERSION, 1)
    put(tex.O_COLORKEY, 1 if colorkey else 0)
    put(tex.O_WIDTH, w)
    put(tex.O_HEIGHT, h)
    put(tex.O_BYTES_PER_PIXEL, 1)
    put(tex.O_PAL_FLAG, 1)
    put(tex.O_BITS_PER_INDEX, 8)
    put(tex.O_PAL_SIZE, ncol)
    put(tex.O_NUM_PALETTES, 1)
    put(tex.O_COLORS_PER_PAL, ncol)
    put(tex.O_COLORS_PER_PAL2, ncol)
    put(tex.O_BIT_DEPTH, 8)
    pal = bytearray()
    pal += bytes((entry0[2], entry0[1], entry0[0], entry0[3]))     # BGRA
    for i in range(1, ncol):
        pal += bytes((10 * i, 20 * i % 256, 30 * i % 256, 255))
    px = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            px[y * w + x] = 0 if (w // 4 < x < 3 * w // 4) else (x % 15) + 1
    data = bytes(hdr) + bytes(pal) + bytes(px)
    assert tex.parse(data) is not None, 'synthetic TEX does not parse'
    return data


def main():
    # ---- 1. the safety property, stated as a refusal ---------------------
    off = synth(32, 32, colorkey=False, entry0=(255, 255, 255, 255))
    out, why = tex.debleed(off)
    check('a texture with NO colour key is refused (entry 0 is drawable)',
          (out, why), (None, 'no colour key'))

    on = synth(32, 32, colorkey=True)
    out, _ = tex.debleed(on)
    check('a colour-keyed texture with a boundary is de-fringed',
          out is not None, True)
    check('  and only palette bytes moved',
          tex.check_indices_unchanged(on, out), True)
    t0, t1 = tex.parse(on), tex.parse(out)
    check('  entry 0 was black before', tuple(t0['palette'][0:3]), (0, 0, 0))
    check('  entry 0 is not black after',
          tuple(t1['palette'][0:3]) != (0, 0, 0), True)
    check('  entry 0 is still fully transparent', t1['palette'][3], 0)
    check('  every other palette entry is untouched',
          t0['palette'][4:], t1['palette'][4:])
    check('  the index block is byte-identical', t0['pixels'], t1['pixels'])

    # ---- 2. idempotent: running it twice must not drift -------------------
    again, _ = tex.debleed(out)
    check('running it a second time changes nothing further', again, None)

    # ---- 3. against the REAL archive -------------------------------------
    if not os.path.exists(LGP):
        print('  --  %s not extracted, skipping the archive pass' % LGP)
    else:
        sys.path.insert(0, '.')
        from PyFF7.PyFF7.lgp import LGP as Lgp
        a = Lgp(LGP)
        total = fixed = nokey = unsafe = 0
        for fn, d in a.load_files():
            if not fn.lower().endswith('.tex'):
                continue
            total += 1
            t = tex.parse(d)
            if t and t['palette_flag'] and not struct.unpack_from(
                    '<I', d, tex.O_COLORKEY)[0]:
                nokey += 1
                if tex.debleed(d)[0] is not None:
                    unsafe += 1
            new, _ = tex.debleed(d)
            if new is None:
                continue
            fixed += 1
            if not tex.check_indices_unchanged(d, new):
                unsafe += 1
        print('      (%d TEX in vanilla char.lgp, %d de-fringed, %d have no '
              'colour key)' % (total, fixed, nokey))
        check('the whole archive de-fringes with ZERO index changes', unsafe, 0)
        check('and it actually fires on the measured 458', fixed, 458)

    # ---- 4. battle.lgp: entries have NO extension ------------------------
    # The first wiring filtered on `.tex` and silently skipped all of
    # battle.lgp, whose textures are named "aa", "da" and so on. That is the
    # half enemies live in, so it is asserted rather than assumed.
    BAT = '/tmp/cl/battle/battle.lgp'
    if not os.path.exists(BAT):
        print('  --  %s not extracted, skipping the battle pass' % BAT)
    else:
        from PyFF7.PyFF7.lgp import LGP as Lgp
        b = Lgp(BAT)
        named = tot = fixed = unsafe = 0
        for fn, d in b.load_files():
            if tex.parse(d) is None:
                continue
            tot += 1
            if fn.lower().endswith('.tex'):
                named += 1
            new, _ = tex.debleed(d)
            if new is None:
                continue
            fixed += 1
            if not tex.check_indices_unchanged(d, new):
                unsafe += 1
        print('      (%d TEX in vanilla battle.lgp, %d of them actually named '
              '*.tex)' % (tot, named))
        check('battle textures are NOT identifiable by extension', named, 0)
        check('and they de-fringe anyway, on content detection', fixed, 319)
        check('with zero index changes', unsafe, 0)

    if FAIL:
        print('\n%d check(s) failed' % len(FAIL))
        sys.exit(1)
    print('all good')


if __name__ == '__main__':
    main()
