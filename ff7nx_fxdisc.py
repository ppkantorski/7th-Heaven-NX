#!/usr/bin/env python3
r"""
ff7nx_fxdisc.py -- scale Kujata's floor disc by a FINE percentage, about its
own centre.

WHY THE CAPTURE SIDE WAS THE WRONG SIDE -- FFNx SAYS SO
=======================================================
`repos/FFNx-master/src/ff7/battle/animations.cpp:1071`, the reference engine's
fix for a battle effect whose framebuffer-snapshot art does not match a
widescreen frame:

```c
// Temporary fix for Pollensalta cold breath bg widescreen fix
// (The correct solution should be to edit the file
//  `magic/ff7/data/battle/special/hubuki/kemu.s` to edit the texture page)
if (widescreen_enabled && texture_ctx == pollensalta_cold_breath_bg_texture_ctx)
{
    float widescreen_multiplier =
        ((float)wide_viewport_width / (float)wide_viewport_height) / (4 / 3.f);
    quad_width  *= widescreen_multiplier;
    quad_height *= widescreen_multiplier;
}
```

Three things in eight lines, and all three are the opposite of what this
project has been doing:

1. **The capture is not touched.** Vanilla never resamples a framebuffer
   snapshot. The correction is applied to the GEOMETRY the snapshot is drawn
   on.
2. **The multiplier is uniform in both axes**, not horizontal-only. And it is
   exactly `(854/480) / (4/3)` = `854/640` = 1.3344 -- the same ratio this
   project has been applying to the source columns.
3. FFNx's own comment says the *correct* fix is to edit the effect's authored
   geometry. Scaling the drawn quad is the shortcut.

Scaling geometry also scales about the geometry's OWN centre, so it cannot
slide the image sideways. Correcting on the capture side cannot do that: the
resample is anchored on the rect's origin, so changing the span pivots about
the left edge and the picture slides across the screen -- which is exactly what
`SEVENTH_NX_FB_SPAN=110` did.

THE TWO WORDS THIS TOUCHES
==========================
The disc's radius is built as `R = 3j << 7 = 384j`:

    +0x473244   add  w8, w8, w8, lsl #1    w8 = 3j
    +0x473248   lsl  w26, w8, #7           R  = 384j        <-- THE HOOK
    +0x473178   mov  w23, #0x2fa0          the j = 32 outer ring, 127 * 96

`ff7nx_fxscale` can only move that `lsl` by whole bits -- 384, 192, 96 -- which
is why shift 6 halved the disc when the correction wanted was a quarter. This
replaces the shift with a multiply, so any percentage is reachable:

    K     = 128 * percent / 100          the multiplier on w8, which is 3j
    R     = 3j * K                       so one ring is worth 3K
    outer = 127 * 3K / 4                 the two stay in lockstep

    percent   R per ring   outer    what it is
      100        384       12192    stock
       75        288        9144    640/854 -- the widescreen ratio, DERIVED
       90        345       10953
       50        192        6096    what fxscale's shift 6 did

REGISTERS
=========
The cave needs a scratch to hold K. It uses **w26**, the destination itself,
which is dead on entry -- so nothing else is borrowed and nothing has to be
restored. w8 (3j) is read before w26 is written.
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

DISC_ENV = 'SEVENTH_NX_FX_DISC'

HOOK = 0x473248                    # lsl w26, w8, #7
HOOK_STOCK = 0x5319611A
RETURN_VA = 0x47324C

OUTER_SITE = 0x473178              # mov w23, #0x2fa0
OUTER_STOCK = 0x5285F417

R_REG, J3_REG = 26, 8              # w26 = R (dead in), w8 = 3j
STOCK_K = 128                      # the shift the stock `lsl #7` applies to 3j
# BUILD 299. NOT a tuning knob any more -- a measured ratio.
#
# The report probe (builds 296/297) came back a flat grey 122 over the whole
# field, twice, reading two different header fields. The capture sheet is
# **244** texels wide. The mesh's UV bytes are authored 0..255:
#
#     u = 0x80 + (r * rsin >> 12),  r <= 127   ->   u reaches 255
#
# So the disc is sized for a 256-texel sheet and is being handed a 244-texel
# one. It is too big by 256/244, and the correction is 244/256 = 0.953.
#
# Patrick compared 94 and 95 on hardware and called 94 the closer of the two.
# This module truncates, so the reachable ratios near there are
#
#     94  ->  k 120, radius 360*j, ratio 0.9375  (= 240/256)   <- chosen
#     95  ->  k 121, radius 363*j, ratio 0.9453
#     96  ->  k 122, radius 366*j, ratio 0.9531  (= 244/256)
#
# 0.9375 and 0.9531 are 1.6% apart, which is inside what an eye can call on a
# blurry rim, so the hardware verdict decides between them.
#
# This is a default so that a plain build is right without anyone having to
# know the number. It is still a SYMPTOM: the real defect is that the sheet is
# 244 while the UVs address 256, and the same mismatch is what makes the rim
# sample past the sheet's high-u edge -- the black silhouette on the curved
# side and nowhere else. Making those two agree removes both, and this default
# goes back to 100 when it does.
# BUILD 302: BACK TO 94, because build 301's fill never engaged.
#
# 301 raised the copy loop's bound to fb_tex.w so the sheet would be written in
# full, guarded by "the source must hold at least 3/4 of the sheet" -- the
# resample's furthest read is 0.749*(dest_w-1). On hardware the field came back
# unchanged except for this default, which means the guard REFUSED the raise:
# the source holds less than three quarters of the sheet.
#
# That is the guard working, not failing. It also says the surface does not
# physically contain the region the effect asks for, so no arrangement of the
# copy can fill the sheet with the right content -- the surface has to grow.
#
# Until it does, 94 is the setting that looks closest, and leaving the default
# at 100 would only make the build worse than the one before it.
DEFAULT_PERCENT = 94.0

# BUILD 321 -- A FRACTIONAL DIAL.
#
# The cave used to be `movz w26, #K ; mul w26, w8, w26`, with K = 128*pct/100.
# K is an integer, so the dial resolved in 1/128 steps of about 0.78% and
# "94" actually installed 120/128 = 93.75%. 93.5 and 94 landed on the same
# word and could not be told apart.
#
# One more word fixes it: keep the multiplier in 8.8 fixed point and shift the
# product back down.
#
#     movz w26, #K8 ; mul w26, w8, w26 ; lsr w26, w26, #8
#
# with K8 = 32768*pct/100, so the radius resolves to one world unit per ring
# -- 1/384, about 0.26% -- and 93.5 is a distinct build from 94. At 100% the
# arithmetic is exact: (3j * 32768) >> 8 = 384j, the stock radius.
SCALE_BITS = 8
STOCK_K8 = STOCK_K << SCALE_BITS          # 32768, i.e. 100%

N_WORDS = 4


def k_for(percent: float) -> int:
    """The 8.8 fixed-point multiplier applied to w8, which holds 3j."""
    return int(round(STOCK_K8 * percent / 100.0))


def pct_of(k8: int) -> float:
    """The percentage a multiplier represents, to two places."""
    return round(k8 * 100.0 / STOCK_K8, 2)


def radius_per_ring(k8: int) -> int:
    """One ring is worth (3 * k8) >> 8 world units."""
    return (3 * k8) >> SCALE_BITS


def outer_for(k8: int) -> int:
    """The j = 32 ring: 127 rings of a quarter -- the identity 0x2FA0
    satisfies at the stock multiplier."""
    return 127 * radius_per_ring(k8) // 4


assert k_for(100) == STOCK_K8
assert radius_per_ring(STOCK_K8) == 384
assert outer_for(STOCK_K8) == 0x2FA0
assert k_for(93.75) == k_for(93.75)
assert radius_per_ring(k_for(93.5)) != radius_per_ring(k_for(94.0))

ANCHORS = {
    0x473244: 0x0B080508,          # add w8, w8, w8, lsl #1   w8 = 3j
    0x47324C: 0xB94016A8,          # ldr w8, [x21, #0x14]     w8 dead after
    0x473230: 0x2A1703FA,          # mov w26, w23             the j = 32 arm
    0x473174: 0x320013F6,          # mov w22, #0x1f           ring loop bound
    0x473180: 0x128270F9,          # mov w25, #-0x1388        z = -5000
}


def _lsr32(rd, rn, shift):
    """LSR Wd, Wn, #shift -- the UBFM alias."""
    return (0x53000000 | (shift << 16) | (31 << 10) | (rn << 5) | rd)


def body_words_k8(addr, k8):
    return [A.movz(R_REG, k8),                      # movz w26, #K8
            A.mul(R_REG, J3_REG, R_REG),            # mul  w26, w8, w26
            _lsr32(R_REG, R_REG, SCALE_BITS),       # lsr  w26, w26, #8
            A.b(addr(3), RETURN_VA)]


def body_words(addr, percent):
    return body_words_k8(addr, k_for(percent))


def percent() -> float:
    """A fractional percentage, e.g. 93.5. Two decimal places are meaningful;
    the radius resolves to one world unit per ring, about 0.26%."""
    v = os.environ.get(DISC_ENV)
    if v is None:
        return DEFAULT_PERCENT
    try:
        n = float(v)
    except ValueError:
        return DEFAULT_PERCENT
    if not 25.0 <= n <= 150.0:
        return DEFAULT_PERCENT
    k8 = k_for(n)
    if not 1 <= k8 <= 0xFFFF:
        return DEFAULT_PERCENT
    if not 1 <= radius_per_ring(k8) and outer_for(k8) <= 0xFFFF:
        return DEFAULT_PERCENT
    return round(n, 2)


def enabled() -> bool:
    """On whenever the disc is not at the stock 100%."""
    return percent() != 100


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _b_target(word, va):
    if (word & 0xFC000000) != 0x14000000:
        return None
    off = word & 0x03FFFFFF
    if off & 0x02000000:
        off -= 0x04000000
    return va + off * 4


def _walk(img):
    entry = _b_target(_word(img, HOOK), HOOK)
    if entry is None:
        return [], []
    logical, physical, seen, va = [], [], set(), entry
    while len(logical) < N_WORDS and va not in seen and 0 <= va <= len(img) - 4:
        seen.add(va)
        physical.append(va)
        tgt = _b_target(_word(img, va), va)
        if tgt is not None and tgt != RETURN_VA:
            va = tgt
            continue
        logical.append(va)
        va += 4
    return logical, physical


def installed_k8(img):
    """The 8.8 multiplier installed, STOCK_K8 for stock, or None.

    Read straight out of the cave's `movz` rather than searched for, which is
    what makes a fractional dial identifiable at all: there are 32768 possible
    multipliers and no list to compare against.
    """
    if _word(img, HOOK) == HOOK_STOCK:
        return STOCK_K8 if _word(img, OUTER_SITE) == OUTER_STOCK else None
    addrs, _ = _walk(img)
    if len(addrs) != N_WORDS:
        return None
    got = [_word(img, a) for a in addrs]
    w = got[0]
    if (w & 0xFFE0001F) != (0x52800000 | R_REG):     # movz w26, #imm16
        return None
    k8 = (w >> 5) & 0xFFFF
    if got != body_words_k8(lambda i: addrs[i], k8):
        return None
    if _word(img, OUTER_SITE) != A.movz(23, outer_for(k8)):
        return None
    return k8


def installed(img):
    """The percentage installed, or None."""
    k8 = installed_k8(img)
    return None if k8 is None else pct_of(k8)


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X' % (va, got, want))
    if installed(img) is None:
        bad.append('+0x%X / +0x%X carry neither the stock disc radius nor a '
                   'scale this build wrote' % (HOOK, OUTER_SITE))
    return bad


def read_state(img) -> str:
    k8 = installed_k8(img)
    if k8 is None:
        return 'unknown'
    return ('disc radius %d*j, outer %d (%g%%%s)'
            % (radius_per_ring(k8), outer_for(k8), pct_of(k8),
               ', stock' if k8 == STOCK_K8 else ''))


def plan(m, revert=False):
    import cave_space
    img = m.img
    problems = verify(img)
    if problems:
        return [], [], problems
    want = STOCK_K8 if (revert or not enabled()) else k_for(percent())
    have = installed_k8(img)
    if have == want:
        return [], [], []
    if have != STOCK_K8 and want != STOCK_K8:
        return [], [], ['RESIZE']

    patches, notes = [], []
    if have != STOCK_K8:
        for va in _walk(img)[1]:
            patches.append({'name': 'clear disc scale cave', 'va': hex(va),
                            'expect': struct.pack('<I', _word(img, va)).hex(),
                            'set': '00000000'})
        patches.append({'name': 'restore the disc radius shift',
                        'va': hex(HOOK),
                        'expect': struct.pack('<I', _word(img, HOOK)).hex(),
                        'set': struct.pack('<I', HOOK_STOCK).hex()})
        patches.append({'name': 'restore the outer ring', 'va': hex(OUTER_SITE),
                        'expect': struct.pack('<I',
                                              _word(img, OUTER_SITE)).hex(),
                        'set': struct.pack('<I', OUTER_STOCK).hex()})
    if want == STOCK_K8:
        return patches, ['    disc radius back to stock (384*j)'], []

    pool = ff7nx_cave.HolePool(
        img, starts=set(m.arm_starts),
        named=cave_space.named_targets(img[cave_space.RODATA:]))
    try:
        runs = pool.take(N_WORDS, span=0x80000)
        slots = ff7nx_cave.slots(runs, N_WORDS)
        placed = ff7nx_cave.link(runs,
                                 body_words_k8(lambda i: slots[i], want))
    except ff7nx_cave.NoRoom as exc:
        return [], [], ['disc scale cave: %s' % exc]
    placed[HOOK] = A.b(HOOK, slots[0])
    placed[OUTER_SITE] = A.movz(23, outer_for(want))
    for va, wd in sorted(placed.items()):
        patches.append({'name': 'Kujata disc scale %g%%' % pct_of(want),
                        'va': hex(va),
                        'expect': struct.pack('<I', _word(img, va)).hex(),
                        'set': struct.pack('<I', wd).hex()})
    notes.append('    disc radius %d*j, outer ring %d (%g%% of stock; the '
                 'disc scales about its own centre, so nothing slides)'
                 % (radius_per_ring(want), outer_for(want), pct_of(want)))
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'Kujata disc scale', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fxdisc-')
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
        log('  Kujata disc scale: re-scaling, reverting first')
        if apply_all(main, revert=True, log=log) != 0:
            return 1
        m = nxmap.Main(str(main))
        patches, notes, problems = plan(m, revert=False)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to change the Kujata disc scale.')
        return 1
    log('  Kujata floor disc scale (%s=%g, stock is 100):'
        % (DISC_ENV, 100 if (revert or not enabled()) else percent()))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d disc scale word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
