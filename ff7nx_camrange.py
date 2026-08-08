#!/usr/bin/env python3
"""
ff7nx_camrange.py -- FALSIFIED ON HARDWARE. DERIVATION ONLY. DO NOT SHIP.

    "this camera scrolling feels kind of off. it turns pages with no
     left / right scrolling into scrolling. i am testing it on pc right
     now. it definitely does not do that."      -- the user, 2026-08

They are right, it is not subtle, and the mechanism is exact.

WHAT THIS MODULE DID
====================
It read each field's contiguous layer-1/2 art and wrote

    left' = art_low + 54       right' = art_high - 54

so the stock `+/-160` clip code would produce camera bounds of
`[art_low + 214, art_high - 214]`. The reasoning was that a 428-unit 16:9
view would then be inside the art at every camera position.

The arithmetic is correct. The premise is not.

WHY IT IS WRONG
===============
`md8_1`, measured before and after:

    before   range -160..160   ->  bounds   0..0    travel  0   STATIC SCREEN
    after    range -170..170   ->  bounds -10..10   travel 20   IT SCROLLS

The field was authored as a fixed camera. Widening its range invented
twenty units of pan that no version of FF7 has ever had. Multiply by the
432 fields this rewrote and that is what the user saw immediately.

THE INVARIANT IT BROKE
======================
FFNx, `field_clip_with_camera_range_float`, background.cpp:417:

    float half_width = 160;
    auto camera_range = field_triggers_header_ptr->camera_range;
    if (widescreen_enabled || enable_uncrop)
        camera_range = widescreen.getCameraRange();
    if (is_fieldmap_wide()) {
        camera_range.left += 1; camera_range.right -= 1;
        int size = camera_range.right - camera_range.left;
        half_width = 160 + std::min(53, size / 2 - 160);
    }
    clamp point->x into [left + half_width, right - half_width]

`half_width` starts at 160 and widescreen can only RAISE it, to at most 213.
Raising `half_width` SHRINKS the interval. And `camera_range` itself is only
ever read -- from the field, or from the config -- never computed, and never
widened.

    16:9 camera travel <= 4:3 camera travel, on every field, always.

    size 320 :  4:3 travel   0   ->  16:9 travel   0    (equal)
    size 512 :  4:3 travel 192   ->  16:9 travel  84    (reduced)
    size 746+:  4:3 travel s-320 ->  16:9 travel s-428  (reduced)

**Widescreen never adds camera movement. It widens the frustum so a FIXED
camera sees more of the painted background.** The 64 units of extra Cosmos
art each side exist to be REVEALED by the wider view, not panned into. That
distinction is the whole thing, and this module got it backwards.

WHY THERE IS NO SALVAGE
=======================
A "trim only, never widen" variant was written and measured: it changes
0 of 709 fields on the rebuilt archive, because this module had already
pushed every bound flush against the art edge, leaving nothing to pull in.
It also cannot undo the damage. The only correct action is to not run the
pass; `build._build_flevel` always starts from the vanilla archive, so a
rebuild with it unwired restores the original ranges exactly.

STATUS
======
Unwired from `build.py` and removed from the GUI. The measurement helpers
below (`merged_columns`, `art_run`) are correct and are kept -- contiguous
column coverage is still the right way to measure field art, and
`diag_fieldcover.py` uses the same approach. Nothing here writes.

See HANDOFF-62 §2.2 and dead-list items 21-22.
"""
from __future__ import annotations

import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

SECTION8, SECTION9 = 7, 8
RANGE_AT = 12               # left, top, right, bottom -- four int16
NEEDED = 428                # the 16:9 view, in tile units
HALF = NEEDED // 2          # 214
STOCK_HALF = 160            # the module's `#0xa0`
FFNX_HALF_CAP = 53          # FFNx's std::min cap; 160 + 53 = 213
TILE = 16


class CamRangeError(ValueError):
    pass


# --------------------------------------------------------------------------
# measurement -- CORRECT, and still used. Nothing below writes anything.
# --------------------------------------------------------------------------
def merged_columns(sec9, surv=None):
    """
    [(x0, x1)] -- layer 1+2 tile coverage, merged into contiguous runs.

    Contiguous runs, not a bounding box. `nivinn_2` and `cos_btm` both have
    a 10,272-unit bounding box for 512 units of real art because one tile
    sits ten thousand units away. See HANDOFF-62 §1.2.

    Layers 3 and 4 are excluded: they WRAP rather than cull and their
    extents mean something different (HANDOFF-60 §3.5).
    """
    import diag_common as DC
    surv = surv or DC.survey(sec9)
    iv = []
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        if layer not in (1, 2):
            continue
        for o in offs:
            x = struct.unpack_from('<h', sec9, o + DC.TILE_DST_X)[0]
            iv.append((x, x + TILE))
    iv.sort()
    out = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def art_run(sec9, mid, surv=None):
    """(x0, x1) -- the contiguous run a camera at `mid` is standing in."""
    runs = merged_columns(sec9, surv)
    if not runs:
        raise CamRangeError('no layer 1/2 tiles')
    for r in runs:
        if r[0] <= mid <= r[1]:
            return r
    return max(runs, key=lambda r: r[1] - r[0])


def read_range(sec8):
    """(left, top, right, bottom). Y grows DOWN, so bottom > top."""
    if len(sec8) < RANGE_AT + 8:
        raise CamRangeError('section 8 is %d bytes' % len(sec8))
    return struct.unpack_from('<4h', sec8, RANGE_AT)


def bounds(left, right):
    """The camera bounds the port's stock +/-160 clip code produces."""
    lo, hi = left + STOCK_HALF, right - STOCK_HALF
    if lo > hi:
        lo = hi = (lo + hi) // 2
    return lo, hi


def ffnx_bounds(left, right, wide):
    """
    FFNx's own bounds, background.cpp:417. The reference, for comparison.

    Use this to check any future proposal against the invariant: for every
    field, `travel(wide=True) <= travel(wide=False)`. A proposal that fails
    that test invents camera movement, which is what this module did.
    """
    if not wide:
        return left + STOCK_HALF, right - STOCK_HALF
    left += 1
    right -= 1
    hw = STOCK_HALF + min(FFNX_HALF_CAP, (right - left) // 2 - STOCK_HALF)
    return left + hw, right - hw


def travel(lo, hi):
    return max(0, hi - lo)


def check_invariant(left, right):
    """
    (ok, wide_travel, narrow_travel) -- FFNx never adds camera movement.

    Kept as an executable statement of the rule this module broke.
    """
    w = travel(*ffnx_bounds(left, right, True))
    n = travel(*ffnx_bounds(left, right, False))
    return w <= n, w, n
