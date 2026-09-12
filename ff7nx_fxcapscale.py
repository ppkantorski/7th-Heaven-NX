#!/usr/bin/env python3
r"""
ff7nx_fxcapscale.py -- give KUJATA'S capture more texels, using the game's own
xscale/yscale, so nothing else in the game is touched.

WHAT ACTUALLY BROKE THE SWIRL, AND WHAT DID NOT
===============================================
`ff7nx_fbsize` (build 277) changed `fb_tex.w/h` inside `make_framebuffer_tex`,
which runs for **every** framebuffer capture in the game. The swirl's texture
doubled while the stock copy loop that fills it still used
`min(surface, origin + size) - origin` bounds, so most of it was never written.
That module is RETRACTED and off. This one does the same job through Kujata's
own xscale/yscale, so exactly one effect changes.

AND THE GATE WAS NOT THE DISCRIMINATOR I CLAIMED EITHER
=======================================================
I said "xscale > 1 means a page-scaled capture, which is the swirl". Scanning
every store to `struc91 + 0x10C` / `+0x110` -- immediate AND register-sourced --
finds 25 paired creators, and the truth is messier than either claim:

    0x500A34 / 0x500A41   Kujata            xscale = yscale = 1  (immediate)
    0x40248E / 0x40249D   the battle swirl  xscale = [0x9A04B0]  = **2**
                                            (set at x86 0x401726)
    0x595F6B / 0x595F7A   another capture   xscale = [0x9AD1A8]  = 2 in
                                            high-res (set at x86 0x41B51E)
    ... and 22 more, all immediate xscale = yscale = 1

So xscale = 2 is ALREADY IN USE. One condition can never identify Kujata's
capture, whichever condition it is. `ff7nx_fbresample`'s frame gate therefore
tests **five**, and Kujata's capture is the only one that satisfies all of
them:

    fb_tex.w == tex_format.width << k      double-scaled
    fb_tex.h == tex_format.width << k      and square
    tex_format.width == 256                authored 256x256  (the swirl is 160)
    fb_tex.x == 0                          taken at the frame origin
    fb_tex.y == 0

Every other shape is executed under arm64emu in tests/test_fbresample.py and
asserted to reach the untouched stock loop.

THE TWO WORDS
=============
Kujata's own xscale/yscale stores, x86 `0x500A34` / `0x500A41`, recompiled to

    ARM +0x473C98   str w19, [x0]      xscale   (w19 = 1, shared with 4 others)
    ARM +0x473CB8   str w19, [x0]      yscale

w19 is shared with four other stores in the same routine, so the value cannot
be changed at its source. Each store becomes a branch into a 3-word cave that
materialises the constant in w17 -- IP0, dead immediately after the `bl` that
precedes both stores -- and stores that instead.

    1   stock                256 x 256 capture, 13.0 px per texel
    2   double               512 x 512                6.6      <- the default
    4   quadruple           1024 x 1024               3.3

The texture grows; the UVs, the disc, the rect and the other captures do not.
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
import ff7nx_cave                                              # noqa: E402
import nxmap                                                   # noqa: E402

CAPSCALE_ENV = 'SEVENTH_NX_FX_CAPSCALE'

X_SITE = 0x473C98                  # str w19, [x0]   -> struc91 + 0x10C xscale
Y_SITE = 0x473CB8                  # str w19, [x0]   -> struc91 + 0x110 yscale
STORE_STOCK = 0xB9000013
SCRATCH = 17                       # IP0: both sites are preceded by a bl

LEGAL = (1, 2, 4)

# BUILD 287: BACK TO 1, and it must stay there until the gate is fixed.
#
# The capture rect passed to the copy is `tex_w * xscale` = 512 at scale 2, but
# the surface it reads is SMALLER than that -- 320x240, which is what the black
# measures: (320/512) * (240/512) = 29.3% covered, 70.7% black, and Patrick
# measured 71%. Scale 2 is what PUT the black there, on either capture path.
#
# Worse, the swirl's own xscale is already 2 ([0x9A04B0], see below), so at
# scale 2 Kujata's capture and the swirl have the SAME five-condition
# signature and no gate keyed on that shape can tell them apart. See BUILD-287.
DEFAULT_SCALE = 1

N_WORDS = 3                        # movz w17, #k ; str w17, [x0] ; b back

# The `bl TRANSLATE` immediately before each store is what makes w17 free, and
# the loads after are what prove we are on the right two stores.
ANCHORS = {
    0x473C8C: 0x11043100,          # add w0, w8, #0x10c      xscale field
    0x473C94: 0x943221C3,          # bl  TRANSLATE           (w17 dead after)
    0x473C9C: 0xB9401688,          # ldr w8, [x20, #0x14]
    0x473CAC: 0x11044100,          # add w0, w8, #0x110      yscale field
    0x473CB4: 0x943221BB,          # bl  TRANSLATE
    0x473CBC: 0xB9401688,          # ldr w8, [x20, #0x14]
    0x4739EC: 0x320003F3,          # mov w19, #1   -- shared, cannot be changed
}


def body_words(k, ret, addr):
    return [A.movz(SCRATCH, k),
            A.str_(SCRATCH, 0),                # str w17, [x0]
            A.b(addr(2), ret)]


def scale() -> int:
    v = os.environ.get(CAPSCALE_ENV)
    if v is None:
        return DEFAULT_SCALE
    try:
        n = int(v, 0)
    except ValueError:
        return DEFAULT_SCALE
    return n if n in LEGAL else DEFAULT_SCALE


def enabled() -> bool:
    return scale() != 1


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _b_target(word, va):
    if (word & 0xFC000000) != 0x14000000:
        return None
    off = word & 0x03FFFFFF
    if off & 0x02000000:
        off -= 0x04000000
    return va + off * 4


def _walk(img, site):
    """(logical, physical) addresses of one site's cave."""
    entry = _b_target(_word(img, site), site)
    if entry is None:
        return [], []
    ret = site + 4
    logical, physical, seen, va = [], [], set(), entry
    while len(logical) < N_WORDS and va not in seen and 0 <= va <= len(img) - 4:
        seen.add(va)
        physical.append(va)
        tgt = _b_target(_word(img, va), va)
        if tgt is not None and tgt != ret:
            va = tgt                    # a run-to-run link, never logic
            continue
        logical.append(va)
        va += 4
    return logical, physical


def installed_scale(img):
    """The k both sites carry, or None."""
    found = set()
    for site in (X_SITE, Y_SITE):
        if _word(img, site) == STORE_STOCK:
            found.add(1)
            continue
        addrs, _ = _walk(img, site)
        if len(addrs) != N_WORDS:
            return None
        hit = None
        for k in LEGAL:
            if k == 1:
                continue
            if [_word(img, a) for a in addrs] == body_words(
                    k, site + 4, lambda i: addrs[i]):
                hit = k
        if hit is None:
            return None
        found.add(hit)
    return found.pop() if len(found) == 1 else None


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X' % (va, got, want))
    if installed_scale(img) is None:
        bad.append('+0x%X/+0x%X carry neither the stock store nor a capture '
                   'scale this build wrote' % (X_SITE, Y_SITE))
    return bad


def read_state(img) -> str:
    k = installed_scale(img)
    if k is None:
        return 'unknown'
    return 'capture scale %dx%s' % (k, ' (stock)' if k == 1 else '')


def plan(m, revert=False):
    import cave_space
    img = m.img
    problems = verify(img)
    if problems:
        return [], [], problems
    want = 1 if revert else scale()
    have = installed_scale(img)
    if have == want:
        return [], [], []
    if have != 1 and want != 1:
        # Two live caves cannot be rewritten in place: the hook words would be
        # expected in two states at once. apply_all reverts first and comes
        # back here on a clean image.
        return [], [], ['RESIZE']

    patches, notes = [], []
    # Always take the old caves out first: the two sites must never be left
    # carrying different scales, and a cave must never be stranded.
    for site in (X_SITE, Y_SITE):
        if _word(img, site) == STORE_STOCK:
            continue
        for va in _walk(img, site)[1]:
            patches.append({'name': 'clear capture scale cave', 'va': hex(va),
                            'expect': struct.pack('<I', _word(img, va)).hex(),
                            'set': '00000000'})
        patches.append({'name': 'restore xscale/yscale store', 'va': hex(site),
                        'expect': struct.pack('<I', _word(img, site)).hex(),
                        'set': struct.pack('<I', STORE_STOCK).hex()})
    if want == 1:
        return patches, ['    capture scale back to stock (1x)'], []

    pool = ff7nx_cave.HolePool(
        img, starts=set(m.arm_starts),
        named=cave_space.named_targets(img[cave_space.RODATA:]))
    for site, what in ((X_SITE, 'xscale'), (Y_SITE, 'yscale')):
        try:
            runs = pool.take(N_WORDS, span=0x80000)
            slots = ff7nx_cave.slots(runs, N_WORDS)
            words = body_words(want, site + 4, lambda i: slots[i])
            placed = ff7nx_cave.link(runs, words)
        except ff7nx_cave.NoRoom as exc:
            return [], [], ['capture scale cave: %s' % exc]
        placed[site] = A.b(site, slots[0])
        for va, wd in sorted(placed.items()):
            patches.append({'name': 'capture %s x%d' % (what, want),
                            'va': hex(va),
                            'expect': struct.pack('<I', _word(img, va)).hex(),
                            'set': struct.pack('<I', wd).hex()})
        notes.append('    Kujata capture %s = %d (cave entry +0x%X)'
                     % (what, want, slots[0]))
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'Kujata capture xscale/yscale',
                  'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fxcapscale-')
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
    if problems == ['RESIZE']:
        log('  Kujata capture scale: %s -> %sx, reverting first'
            % (read_state(m.img), scale()))
        if apply_all(main, revert=True, log=log) != 0:
            return 1
        m = nxmap.Main(str(main))
        patches, notes, problems = plan(m, revert=False)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to change the Kujata capture scale.')
        return 1
    log('  Kujata capture xscale/yscale (%s=%d, stock is 1; NO other capture '
        'in the game is touched):' % (CAPSCALE_ENV, 1 if revert else scale()))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d capture scale word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
