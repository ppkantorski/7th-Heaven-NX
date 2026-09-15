#!/usr/bin/env python3
r"""
ff7nx_fxdepth.py -- scale Kujata's field along DEPTH only, leaving its width
alone. A geometry fix, not a size dial.

WHY THIS EXISTS
===============
`ff7nx_fxdisc` scales the ring radius `R`, and `R` feeds BOTH axes:

    x = (R * rsin(theta)) >> 12
    z = (R * rcos(theta)) >> 12  -  5000

so it can only ever make the same shape bigger or smaller. The operator's
report is that the shape itself is wrong:

> "the scale SHOULD be 100%, 1:1, its the geometry of the layer that is wrong.
>  the dimensions of the layer are causing it to extend further to the back of
>  the battle behind the enemies and behind kujata"
> "scale 100% also makes it wider than it should be... but that was merely a
>  bandaid. it made the sides closer in, but it kept it longer than it should
>  be."

Too long front-to-back, and separately a little too wide. Two axes, two
numbers -- which no single `R` can express.

THE SITE
========
In the recompiled vertex builder the two axes are computed by two different
instructions, and only one of them carries the depth:

    +0x473310   mul  w8, w8, w9            x = R * rsin
    +0x473314   asr  w8, w8, #0xc          >> 12                  WIDTH

    +0x473430   mul  w8, w10, w8           z = -R * rcos
    +0x473434   add  w8, w25, w8, asr #12  >> 12, then -5000       DEPTH  <--
    +0x47343C   str  w8, [x21, #0x18]

`+0x473434` is the whole depth coordinate in one instruction -- the x86's
`sar esi, 0xc` and `sub esi, 0x1388` fused (ff7_en 0x500ED8/0x500EDB). It is
touched by nothing else: `ff7nx_fxdisc` hooks +0x473248, `ff7nx_fxorigin`
hooks +0x4723A0, and the width at +0x473314 is left exactly as it is.

THE CAVE
========
Six words. The shift has to come first -- `-R * rcos` reaches 50M and
multiplying that by the scale would overflow w8, while the shifted value is
at most the outer radius:

    asr  w8, w8, #12          the depth term, |w8| <= 12192
    movz w10, #K              K = round(256 * percent / 100)
    mul  w8, w8, w10          <= 3.1M, no overflow
    asr  w8, w8, #8
    add  w8, w25, w8          the displaced -5000
    b    +0x473438

`w10` held `rcos` and is dead at the hook -- and there is a `bl` two
instructions later, so nothing could have relied on it anyway. `w25` is the
-5000 the stock instruction adds, still live.

At 100 the arithmetic is EXACT, not approximate: `(x >> 12) * 256 >> 8` is
`x >> 12` for every x, positive or negative, because both shifts are
arithmetic. So the dial's identity position reproduces the stock geometry bit
for bit, and the tests execute that rather than assert it.

WHAT THE NUMBER IS, AND WHERE IT CAME FROM
==========================================
Two independent measurements, which is the reason this module exists rather
than another guess:

    0.853   the operator drew a red line on the terrain's far edge (fits to
            0.29 px rms over 683 px). Bisecting the disc's rim against that
            line -- rim r = 127 pushed through FIT_HUV, his own texel probe --
            gives the depth scale whose far edge lands exactly on it.

    0.824   the depth axis is 1.21x too long for 1:1 with the capture
            (FINDINGS 362). This one is worth more than the width figure
            beside it: the vertical chain has no resample and no span in it
            -- texel row = surface row = frame row -- so it rests on FIT_HUV
            alone, with none of the capture-step question that has blocked
            every horizontal fix since build 327.

They agree to 3.5 %, from completely different evidence. 85 is the default
here; the honest range is 82-86.

WHAT IT DOES NOT DO
===================
It does not touch the width, the origin, the capture, the UVs or the ripple.
It cannot fix the softness: that is a 3.9:1 shear between the two texture
axes, and correcting the depth removes part of it (3.9:1 becomes about
3.2:1) but the rest lives in the horizontal, which is still waiting on the
one unresolved number in FINDINGS 362.
"""
from __future__ import annotations

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

DEPTH_ENV = 'SEVENTH_NX_FX_DEPTH'
DEFAULT_PERCENT = 100.0            # identity: the stock geometry, bit for bit

HOOK = 0x473434                    # add w8, w25, w8, asr #12
HOOK_STOCK = 0x0B883328
RETURN_VA = 0x473438
N_WORDS = 6

Z_REG = 8                          # w8  -- the depth term, in and out
TMP_REG = 10                       # w10 -- rcos, dead at the hook
OFF_REG = 25                       # w25 -- the -5000 the stock word adds
SCALE_BITS = 8
STOCK_K = 1 << SCALE_BITS          # 256 == 100 %

# The instructions around the hook, and the WIDTH site, so that a game update
# cannot move this patch silently and so that "width untouched" is checkable.
ANCHORS = {
    0x473428: 0xB9401AA8,          # ldr  w8, [x21, #0x18]     -R
    0x47342C: 0xB94002AA,          # ldr  w10, [x21]           rcos
    0x473430: 0x1B087D48,          # mul  w8, w10, w8
    0x473438: 0x51003120,          # sub  w0, w9, #0xc
    0x47343C: 0xB9001AA8,          # str  w8, [x21, #0x18]
    0x473310: 0x1B097D08,          # mul  w8, w8, w9           the WIDTH path
    0x473314: 0x130C7D08,          # asr  w8, w8, #0xc         ... untouched
    0x473248: 0x5319611A,          # lsl  w26, w8, #7          R = 384j
}


def k_for(percent: float) -> int:
    """The 8.8 multiplier. 100 % is exactly 256, i.e. the identity."""
    return int(round(STOCK_K * percent / 100.0))


def pct_of(k: int) -> float:
    return round(k * 100.0 / STOCK_K, 2)


def percent() -> float:
    v = os.environ.get(DEPTH_ENV)
    if v is None:
        return DEFAULT_PERCENT
    try:
        n = float(v)
    except ValueError:
        return DEFAULT_PERCENT
    if not 25.0 <= n <= 200.0:
        return DEFAULT_PERCENT
    k = k_for(n)
    if not 1 <= k <= 0xFFFF:
        return DEFAULT_PERCENT
    return round(n, 2)


def enabled() -> bool:
    return k_for(percent()) != STOCK_K


def body_words_k(addr, k):
    return [A.asr(Z_REG, Z_REG, 12),                  # asr  w8, w8, #12
            A.movz(TMP_REG, k),                       # movz w10, #K
            A.mul(Z_REG, Z_REG, TMP_REG),             # mul  w8, w8, w10
            A.asr(Z_REG, Z_REG, SCALE_BITS),          # asr  w8, w8, #8
            A.add_reg(Z_REG, OFF_REG, Z_REG),         # add  w8, w25, w8
            A.b(addr(5), RETURN_VA)]


def body_words(addr, percent_):
    return body_words_k(addr, k_for(percent_))


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


def installed_k(img):
    """The multiplier the live image carries, STOCK_K for stock, or None."""
    if _word(img, HOOK) == HOOK_STOCK:
        return STOCK_K
    addrs, _ = _walk(img)
    if len(addrs) != N_WORDS:
        return None
    got = [_word(img, a) for a in addrs]
    w = got[1]
    if (w & 0xFFE0001F) != (0x52800000 | TMP_REG):     # movz w10, #imm16
        return None
    k = (w >> 5) & 0xFFFF
    if got != body_words_k(lambda i: addrs[i], k):
        return None
    return k


def installed(img):
    k = installed_k(img)
    return None if k is None else pct_of(k)


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X' % (va, got, want))
    if installed(img) is None:
        bad.append('+0x%X carries neither the stock depth word nor a scale '
                   'this build wrote' % HOOK)
    return bad


def read_state(img) -> str:
    k = installed_k(img)
    if k is None:
        return 'unknown'
    return ('depth x %g%%%s' % (pct_of(k), ', stock' if k == STOCK_K else ''))


def plan(m, revert=False):
    import cave_space
    img = m.img
    problems = verify(img)
    if problems:
        return [], [], problems
    want = STOCK_K if (revert or not enabled()) else k_for(percent())
    have = installed_k(img)
    if have == want:
        return [], [], []
    if have != STOCK_K and want != STOCK_K:
        return [], [], ['RESIZE']

    patches, notes = [], []
    if have != STOCK_K:
        for va in _walk(img)[1]:
            patches.append({'name': 'clear depth scale cave', 'va': hex(va),
                            'expect': struct.pack('<I', _word(img, va)).hex(),
                            'set': '00000000'})
        patches.append({'name': 'restore the stock depth word',
                        'va': hex(HOOK),
                        'expect': struct.pack('<I', _word(img, HOOK)).hex(),
                        'set': struct.pack('<I', HOOK_STOCK).hex()})
    if want == STOCK_K:
        return patches, ['    field depth back to stock'], []

    pool = ff7nx_cave.HolePool(
        img, starts=set(m.arm_starts),
        named=cave_space.named_targets(img[cave_space.RODATA:]))
    try:
        runs = pool.take(N_WORDS, span=0x80000)
        slots = ff7nx_cave.slots(runs, N_WORDS)
        placed = ff7nx_cave.link(runs, body_words_k(lambda i: slots[i], want))
    except ff7nx_cave.NoRoom as exc:
        return [], [], ['depth scale cave: %s' % exc]
    placed[HOOK] = A.b(HOOK, slots[0])
    for va, wd in sorted(placed.items()):
        patches.append({'name': 'Kujata field depth %g%%' % pct_of(want),
                        'va': hex(va),
                        'expect': struct.pack('<I', _word(img, va)).hex(),
                        'set': struct.pack('<I', wd).hex()})
    notes.append('    field depth x %g%% of stock -- the WIDTH is untouched '
                 '(+0x473314), and ring 0 is 0 at every scale so the centre '
                 'cannot move' % pct_of(want))
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'Kujata field depth', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fxdepth-')
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
        log('  Kujata field depth: re-scaling, reverting first')
        if apply_all(main, revert=True, log=log) != 0:
            return 1
        m = nxmap.Main(str(main))
        patches, notes, problems = plan(m, revert=False)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to change the Kujata field depth.')
        return 1
    log('  Kujata field depth (%s=%g, stock is 100):'
        % (DEPTH_ENV, 100 if (revert or not enabled()) else percent()))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d depth word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
