#!/usr/bin/env python3
"""
ff7nx_camfit.py -- stop the camera scrolling off the edge of the art.

    python3 ff7nx_camfit.py <flevel.lgp>            report, writes nothing
    python3 ff7nx_camfit.py <flevel.lgp> las4_1 md8_1

THE BUG THIS EXISTS FOR
=======================
`ff7nx_ws.clamped_range` bakes FFNx's camera clamp into section 8 through an
identity: the stock code only ever uses the range as `left + 160` and
`right - 160`, so pulling the edges in by `d` moves the clamp by `d`.

That identity is exact. What it does NOT know is how wide the VIEW is. The
port's own arithmetic assumes 320 units (`#0xa0` twice). This build's framing
stage widens it:

    frame:  game-x -106.67 .. 746.67   ->   426.67 field units, half 213.33

So a field whose clamp lets the camera reach `range.left + 160` shows the
window from `left` to `left + 426.67` -- **106.67 units wider than the code
that computed the clamp believed.** If the art stops before that, the extra
53.33 units per side are BLACK.

MEASURED on build 79, all 709 fields (`diag_fieldcover.py`):

    OK                 479
    CAMERA WALKS OFF   108      <- this
    ART ABSENT          69
    ART SHORT           32
    PAGES BLANK         21

`las4_1`, the bottom of the Northern Cave, is one of the 108:

    camera range  -192 .. 192  (384)   clamped to -32 .. 32
    contiguous art  -224 .. 224  (448)
    CAMERA WALKS OFF (22 units bare at the extremes)

At camera -32 the window starts at -245 and the art starts at -224: a black
bar on the LEFT. Run right to camera +32 and the same bar appears on the
RIGHT. Both were reported from hardware.

`clamped_range` cannot fix this on its own, because the number it needs is
not in section 8. It is in section 9 -- where the tiles actually are.

WHAT THIS DOES
==============
For every field whose contiguous art is at least 428 units wide (i.e. the art
CAN cover a 16:9 window), tighten the baked clamp so the window stays inside
the art:

    clamp_lo = max(current_lo, art_left  + 214)
    clamp_hi = min(current_hi, art_right - 214)

then write it back through the same `+/-160` identity.

**It only ever TIGHTENS.** A field that already fits comes out a no-op by
construction: `worst_band == 0` means `current_lo >= art_left + 214` and
`current_hi <= art_right - 214` already, so both `max`/`min` pick the current
value. No field can gain reach it did not have, so nothing that renders
correctly today can start rendering incorrectly.

Fields with less than 428 units of art are LEFT ALONE. They cannot fill the
frame at any camera position and re-centring them is a separate decision with
its own trade-off; `diag_fieldcover` already reports them as ART SHORT /
ART ABSENT.

WHAT FFNx DOES, FOR COMPARISON
==============================
`field_clip_with_camera_range_float` (background.cpp:417) clamps to
`left + half_width .. right - half_width` with

    half_width = 160 + std::min(53, cameraRangeSize / 2 - 160)

capped at 213, and its comment says "This centers the background if
necessary". For `las4_1` that gives `half_width = 191` and pins the camera at
0, which also keeps the 427-unit window inside the 448 units of art. So FFNx
does not show the bar either -- it just gets there by pinning, where this
gets there by tightening, and tightening leaves the field 20 units of travel
FFNx does not have. Simulated over the 108: FFNx's rule fixes 97 of them and
leaves a mean 47.6 units of travel; this rule fixes 108 and leaves 67.9.
"""
from __future__ import annotations

import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

NEEDED = 428            # 16:9 window in field units, as diag_fieldcover
VIEW_HALF = NEEDED // 2  # 214
STOCK_HALF = 160        # the port's hardwired `#0xa0`
TILE = 16
SECTION_TRIGGERS = 7
SECTION_BACKGROUND = 8


def disabled():
    """SEVENTH_NX_NO_CAMFIT=1 turns the pass off without a code edit."""
    return os.environ.get('SEVENTH_NX_NO_CAMFIT') == '1'


# A column of cells whose every texel is our own black filler is NOT art.
#
# `field_bg_native.NEAR_BLACK` is 0x0841 -- RGB(8, 8, 8), mean luminance
# exactly 8.0. It is the 0.9/255 lift the promotion applies to a fully black
# cell, because 0x0000 has to mean TRANSPARENT on a depth-2 page. So a black
# cell that has been promoted reads back as "has content" to any test that
# only asks whether a texel is non-zero.
#
# MEASURED on build 81, the leftmost 16-unit column of three unrelated fields:
#
#     md8_1    x -224..-208   mean luminance 8.0 / 255
#     md8_3    x -224..-208   mean luminance 8.0 / 255
#     las4_1   x -224..-208   mean luminance 8.0 / 255
#
# Exactly 8.0 in three fields is not a picture. Counting it let the camera
# travel into 16 units of filler, which is the black pillar reported from
# hardware on `md8_1`: "it is assuming the map is wider than it is visually".
#
# The floor sits just above NEAR_BLACK so that lift alone can never qualify.
# A column is kept if ANY tile covering it is above the floor, so real art
# that merely sits beside black is never dropped.
LIT_FLOOR = 10.0

# Below this much camera travel, pin instead. See `fit`.
PIN_SLACK = 24


INTERIOR = 160          # inside this is the 4:3 picture; it is always art


def _live_columns(sec9, off, pages, prgbs, npg):
    """
    Which of a tile's 16 destination columns contain PICTURE, as a bool list.

    "Picture" is any texel that is neither transparent nor NEAR_BLACK. The
    distinction is the whole point: Cosmos pads its widescreen art out to the
    16-unit tile grid with flat filler, and that filler is indistinguishable
    from art to anything that counts tiles.
    """
    import numpy as np
    import field_bg_native as FN
    import field_bg_repack as RP

    page = pages.get(sec9[off + RP.T_TEXID])
    if page is None:
        return None
    n = len(page.data) // (2 if page.depth == 2 else 1)
    side = int(round(n ** 0.5))
    if side * side != n:
        return None
    u, v = struct.unpack_from('<II', sec9, off + RP.T_SRC_X_BIG)
    cx = int(round(u / 1e7 * side))
    cy = int(round(v / 1e7 * side))
    sc = max(1, side // 256)
    if cx + TILE * sc > side or cy + TILE * sc > side:
        return None
    if page.depth == 2:
        blk = np.frombuffer(page.data, '<u2').reshape(side, side)
        blk = blk[cy:cy + TILE * sc, cx:cx + TILE * sc]
        real = (blk != FN.EMPTY) & (blk != FN.NEAR_BLACK)
    else:
        pal = sec9[off + RP.T_PALETTE]
        if pal >= npg or not prgbs:
            return None
        idx = np.frombuffer(page.data, np.uint8).reshape(side, side)
        idx = idx[cy:cy + TILE * sc, cx:cx + TILE * sc]
        real = prgbs[pal][idx].sum(axis=2) > 26      # > RGB(8,8,8) summed
    colsum = real.sum(axis=0)
    return [bool(colsum[k * sc:(k + 1) * sc].any()) for k in range(TILE)]


def _cell_luma(sec9, off, pages, prgbs, npg):
    """Mean luminance 0..255 of the page cell one tile samples, or None."""
    import diag_common as DC
    import field_bg_repack as RP
    import numpy as np

    page = pages.get(sec9[off + RP.T_TEXID])
    if page is None:
        return None
    n = len(page.data) // (2 if page.depth == 2 else 1)
    side = int(round(n ** 0.5))
    if side * side != n:
        return None
    u, v = struct.unpack_from('<II', sec9, off + RP.T_SRC_X_BIG)
    cx = int(round(u / 1e7 * side))
    cy = int(round(v / 1e7 * side))
    if cx + TILE > side or cy + TILE > side:
        return None
    if page.depth == 2:
        arr = np.frombuffer(page.data, '<u2').reshape(side, side)
        blk = arr[cy:cy + TILE, cx:cx + TILE]
        r = ((blk >> 11) & 31).astype(np.float32) * 8
        g = ((blk >> 5) & 63).astype(np.float32) * 4
        b = (blk & 31).astype(np.float32) * 8
        return float((r + g + b).mean() / 3.0)
    idx = np.frombuffer(page.data, np.uint8).reshape(side, side)
    pal = sec9[off + RP.T_PALETTE]
    if pal >= npg or not prgbs:
        return None
    return float(prgbs[pal][idx[cy:cy + TILE, cx:cx + TILE]]
                 .astype(np.float32).mean())


def art_run(sec9, midpoint, parts=None):
    """
    (left, right) of the contiguous LIT layer-1/2 art run containing
    `midpoint`.

    Same measurement as `diag_fieldcover.merged_columns`, and deliberately
    NOT the bounding box: a single stray tile ten thousand units away makes a
    512-unit field measure 10,272, and a clamp built on that number would be
    built on sand. Returns None if the field has no layer 1/2 tiles.

    `parts` is the whole field. Given it, a tile whose cell is entirely black
    filler is dropped -- see LIT_FLOOR. Without it the old behaviour (every
    tile counts) is kept, so the CLI still works on a bare section 9.
    """
    import diag_common as DC

    surv = DC.survey(sec9)
    prgbs, npg, pages = [], 0, {}
    if parts is not None:
        try:
            import ff7nx_marginart as MA
            import ff7nx_marginblack as MB
            cols, _hdr, npg, _cpp = MB.palette_colours(parts[3])
            prgbs = [MA.palette_rgb(cols[i]) for i in range(npg)]
            pages = {p.slot: p for p in surv['pages']}
        except Exception:                                      # noqa: BLE001
            prgbs, npg, pages = [], 0, {}
    iv = []
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        if layer not in (1, 2):
            continue
        for o in offs:
            x = struct.unpack_from('<h', sec9, o + DC.TILE_DST_X)[0]
            # PIXEL GRANULARITY AT THE EDGES, TILE GRANULARITY INSIDE.
            # Only the outermost tiles decide where the run ends, and they are
            # the only ones that can be part filler, so the expensive test is
            # confined to them. Inside +/-160 is the 4:3 picture by definition.
            if pages and (x + TILE > INTERIOR or x < -INTERIOR):
                live = _live_columns(sec9, o, pages, prgbs, npg)
                if live is not None:
                    if not any(live):
                        continue
                    iv.append((x + live.index(True),
                               x + TILE - live[::-1].index(True)))
                    continue
            iv.append((x, x + TILE))
    if not iv:
        return None
    iv.sort()
    runs = []
    for a, b in iv:
        if runs and a <= runs[-1][1]:
            runs[-1] = (runs[-1][0], max(runs[-1][1], b))
        else:
            runs.append((a, b))
    return (next((r for r in runs if r[0] <= midpoint <= r[1]), None)
            or max(runs, key=lambda r: r[1] - r[0]))


VIEW_HALF_Y = 120       # the frame is game-y 0..480 = 240 field units
STOCK_HALF_Y = 120      # and the port's vertical clamp is `+/-120`
INTERIOR_Y = 120


def _live_rows(sec9, off, pages, prgbs, npg):
    """`_live_columns`, transposed: which of a tile's 16 ROWS hold picture."""
    import numpy as np
    import field_bg_native as FN
    import field_bg_repack as RP

    page = pages.get(sec9[off + RP.T_TEXID])
    if page is None:
        return None
    n = len(page.data) // (2 if page.depth == 2 else 1)
    side = int(round(n ** 0.5))
    if side * side != n:
        return None
    u, v = struct.unpack_from('<II', sec9, off + RP.T_SRC_X_BIG)
    cx = int(round(u / 1e7 * side))
    cy = int(round(v / 1e7 * side))
    sc = max(1, side // 256)
    if cx + TILE * sc > side or cy + TILE * sc > side:
        return None
    if page.depth == 2:
        blk = np.frombuffer(page.data, '<u2').reshape(side, side)
        blk = blk[cy:cy + TILE * sc, cx:cx + TILE * sc]
        real = (blk != FN.EMPTY) & (blk != FN.NEAR_BLACK)
    else:
        pal = sec9[off + RP.T_PALETTE]
        if pal >= npg or not prgbs:
            return None
        idx = np.frombuffer(page.data, np.uint8).reshape(side, side)
        idx = idx[cy:cy + TILE * sc, cx:cx + TILE * sc]
        real = prgbs[pal][idx].sum(axis=2) > 26
    rowsum = real.sum(axis=1)
    return [bool(rowsum[k * sc:(k + 1) * sc].any()) for k in range(TILE)]


def art_run_y(sec9, midpoint, parts=None):
    """(top, bottom) of the contiguous LIT run containing `midpoint`, in y."""
    import diag_common as DC

    surv = DC.survey(sec9)
    prgbs, npg, pages = [], 0, {}
    if parts is not None:
        try:
            import ff7nx_marginart as MA
            import ff7nx_marginblack as MB
            cols, _hdr, npg, _cpp = MB.palette_colours(parts[3])
            prgbs = [MA.palette_rgb(cols[i]) for i in range(npg)]
            pages = {p.slot: p for p in surv['pages']}
        except Exception:                                      # noqa: BLE001
            prgbs, npg, pages = [], 0, {}
    iv = []
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        if layer not in (1, 2):
            continue
        for o in offs:
            y = struct.unpack_from('<h', sec9, o + DC.TILE_DST_X + 2)[0]
            if pages and (y + TILE > INTERIOR_Y or y < -INTERIOR_Y):
                live = _live_rows(sec9, o, pages, prgbs, npg)
                if live is not None:
                    if not any(live):
                        continue
                    iv.append((y + live.index(True),
                               y + TILE - live[::-1].index(True)))
                    continue
            iv.append((y, y + TILE))
    if not iv:
        return None
    iv.sort()
    runs = []
    for a, b in iv:
        if runs and a <= runs[-1][1]:
            runs[-1] = (runs[-1][0], max(runs[-1][1], b))
        else:
            runs.append((a, b))
    return (next((r for r in runs if r[0] <= midpoint <= r[1]), None)
            or max(runs, key=lambda r: r[1] - r[0]))


def clamp_of_y(rng):
    """
    (lo, hi) -- the port's vertical clamp, AND IT CAN COME OUT INVERTED.

    `field_clip_with_camera_range` applies the two comparisons in sequence:

        if (y > bottom - 120) y = bottom - 120;
        if (y < top + 120)    y = top + 120;

    When `top + 120 > bottom - 120` the second wins and the camera lands on
    `top + 120`. That is not a range, it is an accident of ordering -- and
    `bugin1b`, Bugenhagen's observatory, is exactly that case: y -112..120
    gives bounds 8..0, and which edge loses its 8 units of picture depends on
    which comparison ran last. Returned unclamped so `fit_y` can see it.
    """
    return int(rng['top']) + STOCK_HALF_Y, int(rng['bottom']) - STOCK_HALF_Y


def fit_y(rng, art):
    """A copy of `rng` with top/bottom fitted to the art, or None."""
    if art is None:
        return None
    a0, a1 = art
    lo, hi = clamp_of_y(rng)
    want_lo, want_hi = a0 + VIEW_HALF_Y, a1 - VIEW_HALF_Y
    if want_lo > want_hi:                    # picture shorter than the frame
        want_lo = want_hi = (a0 + a1) // 2   # -- centre it, best available
    nlo, nhi = max(lo, want_lo), min(hi, want_hi)
    if nlo > nhi:
        if lo > hi:
            # The field's own bounds are the inverted, order-dependent kind.
            # There is nothing to preserve, so take what the picture asks for.
            nlo = nhi = (want_lo + want_hi) // 2
        else:
            # They disagree but the field's range is sane -- stay inside it.
            nlo = nhi = min(max((want_lo + want_hi) // 2, lo), hi)
    if (nlo, nhi) == (lo, hi):
        return None
    out = dict(rng)
    out['top'] = nlo - STOCK_HALF_Y
    out['bottom'] = nhi + STOCK_HALF_Y
    if not (-0x8000 <= out['top'] <= 0x7FFF
            and -0x8000 <= out['bottom'] <= 0x7FFF):
        return None
    out['height'] = out['bottom'] - out['top']
    return out


def bare_y(lo, hi, art):
    """Worst uncovered height over every reachable camera position."""
    a0, a1 = art
    if lo > hi:
        lo = hi = lo              # what the port's ordering actually lands on
    return max(max(0, a0 - (c - VIEW_HALF_Y)) + max(0, (c + VIEW_HALF_Y) - a1)
               for c in (lo, hi, (lo + hi) // 2))


def clamp_of(rng):
    """(lo, hi) -- what the port's `+/-160` code makes of a written range."""
    lo = int(rng['left']) + STOCK_HALF
    hi = int(rng['right']) - STOCK_HALF
    if lo > hi:
        lo = hi = (lo + hi) // 2
    return lo, hi


def bare(lo, hi, art):
    """Worst-case uncovered width over every reachable camera position."""
    a0, a1 = art
    return max(max(0, a0 - (c - VIEW_HALF)) + max(0, (c + VIEW_HALF) - a1)
               for c in (lo, hi, (lo + hi) // 2))


def fit(rng, art):
    """
    A tightened copy of `rng`, or None to leave it exactly as it is.

    None is returned -- and this is the safety property -- whenever the field
    already fits, when the art is too narrow to fit at any position, or when
    the result would leave int16.
    """
    if art is None:
        return None
    a0, a1 = art
    if a1 - a0 < NEEDED:
        return None                      # ART SHORT / ABSENT: not ours
    # A range far larger than the art plus one window is not DESCRIBING this
    # art -- it belongs to a scripted camera that goes somewhere the tiles in
    # section 9 do not. `startmap` is the case: range -970..970 (1940 units)
    # against 512 units of art in a single run. Tightening it to +/-42 would
    # be acting on a number that was never about the background, so refuse
    # and let the caller say so out loud.
    if int(rng['right']) - int(rng['left']) > (a1 - a0) + NEEDED:
        return None
    lo, hi = clamp_of(rng)
    nlo = max(lo, a0 + VIEW_HALF)
    nhi = min(hi, a1 - VIEW_HALF)
    if nlo > nhi:                        # the art window sits outside the
        nlo = nhi = min(max((a0 + a1) // 2, lo), hi)   # reachable range
    elif nhi - nlo <= PIN_SLACK:
        # A SLIVER OF TRAVEL IS WORSE THAN NONE.
        #
        # When the art is barely wider than the window, what is left after
        # tightening is a few units of scroll. That is imperceptible AS
        # MOTION and very perceptible as the picture sliding off its own edge
        # -- and it puts the field one rounding error from a bar.
        #
        # `md8_1` (Sector 8, before Aerith) is the case, reported from
        # hardware: "during the cutscene the background looks correct, after
        # it the camera moves and reveals black bars on the side. It should
        # not trigger scrolling here." Its lit art is 432 units against a 427
        # window -- five units of slack. Pinning to the art's own midpoint
        # centres the window in the picture and stops the scroll dead.
        nlo = nhi = min(max((a0 + a1) // 2, nlo), nhi)
    if (nlo, nhi) == (lo, hi):
        return None
    out = dict(rng)
    out['left'] = nlo - STOCK_HALF
    out['right'] = nhi + STOCK_HALF
    if not (-0x8000 <= out['left'] <= 0x7FFF
            and -0x8000 <= out['right'] <= 0x7FFF):
        return None
    out['width'] = out['right'] - out['left']
    return out


def fit_plan(raw_of, ranges, log=lambda *_: None):
    """
    {field: tightened range} for the fields that need one.

    `raw_of(field)` returns the DECOMPRESSED field bytes or None -- the
    caller owns the decompression, because in a build it has them already.
    `ranges` is {field: range} AFTER `ff7nx_ws.plan_ranges`, i.e. what is
    about to be written.
    """
    import lgp

    out = {}
    stats = {'measured': 0, 'no_art': 0, 'short': 0, 'fitted': 0,
             'worst_before': 0, 'worst_after': 0, 'scripted': [],
             'fitted_y': 0, 'worst_y_before': 0, 'worst_y_after': 0,
             'inverted': []}
    for name in sorted(ranges):
        raw = raw_of(name)
        if raw is None:
            continue
        try:
            parts = lgp.split_sections(raw)
            sec9 = parts[SECTION_BACKGROUND]
            rng = ranges[name]
            mid = (int(rng['left']) + int(rng['right'])) // 2
            art = art_run(sec9, mid, parts)
        except Exception:                                      # noqa: BLE001
            stats['no_art'] += 1
            continue
        if art is None:
            stats['no_art'] += 1
            continue
        stats['measured'] += 1
        if art[1] - art[0] < NEEDED:
            stats['short'] += 1
            continue
        # ---- vertical, measured and fitted the same way
        try:
            arty = art_run_y(sec9, (int(rng['top']) + int(rng['bottom'])) // 2,
                             parts)
        except Exception:                                      # noqa: BLE001
            arty = None
        if arty is not None:
            vbefore = bare_y(*clamp_of_y(rng), arty)
            inverted = clamp_of_y(rng)[0] > clamp_of_y(rng)[1]
            # ONLY ACT WHERE THERE IS SOMETHING WRONG.
            #
            # Unlike the horizontal fit, a vertical tighten costs real camera
            # travel in fields that climb, and `fit_y` will happily narrow a
            # field whose window already sits inside its picture. MEASURED:
            # acting on every field touched 115 of them; requiring a defect
            # first confines it to the ones actually showing black plus the
            # order-dependent ones.
            newy = fit_y(rng, arty) if (vbefore or inverted) else None
            if newy is not None:
                vafter = bare_y(*clamp_of_y(newy), arty)
                if vafter <= vbefore:
                    rng = newy
                    stats['fitted_y'] = stats.get('fitted_y', 0) + 1
                    stats['worst_y_before'] = max(stats.get('worst_y_before', 0),
                                                  vbefore)
                    stats['worst_y_after'] = max(stats.get('worst_y_after', 0),
                                                 vafter)
                    if inverted:
                        stats.setdefault('inverted', []).append(name)
                    ranges[name] = rng
                    out[name] = rng
        before = bare(*clamp_of(rng), art)
        new = fit(rng, art)
        if new is None:
            if before and (int(rng['right']) - int(rng['left'])
                           > (art[1] - art[0]) + NEEDED):
                stats['scripted'].append((name, before))
            continue
        after = bare(*clamp_of(new), art)
        if after > before:                       # cannot happen; refuse anyway
            log('  ! camfit: %s would get worse (%d -> %d), skipped'
                % (name, before, after))
            continue
        out[name] = new
        stats['fitted'] += 1
        stats['worst_before'] = max(stats['worst_before'], before)
        stats['worst_after'] = max(stats['worst_after'], after)
    return out, stats


# --------------------------------------------------------------------- CLI
def main(argv=None):
    import lgp

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.exit(__doc__)
    path, wanted = argv[0], set(argv[1:])
    import ff7nx_wsdata as W

    archive = lgp.Archive(path)
    ranges, raws = {}, {}
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        if wanted and name not in wanted:
            continue
        try:
            raw = archive.decompressed(entry)
            parts = lgp.split_sections(raw)
            ranges[name] = W.read_section8_range(parts[SECTION_TRIGGERS])
            raws[name] = raw
        except Exception:                                      # noqa: BLE001
            continue
    plan, stats = fit_plan(raws.get, ranges, log=print)
    for name in sorted(plan):
        rng, new = ranges[name], plan[name]
        _p = lgp.split_sections(raws[name])
        art = art_run(_p[SECTION_BACKGROUND],
                      (int(rng['left']) + int(rng['right'])) // 2, _p)
        print('%-14s art %5d..%-5d  range %5d..%-5d -> %5d..%-5d   '
              'clamp %4d..%-4d -> %4d..%-4d   bare %3d -> %d'
              % (name, art[0], art[1],
                 rng['left'], rng['right'], new['left'], new['right'],
                 *clamp_of(rng), *clamp_of(new),
                 bare(*clamp_of(rng), art), bare(*clamp_of(new), art)))
    if stats['scripted']:
        print('\nLEFT ALONE -- range far wider than the art, so it is not '
              'describing it (scripted camera):')
        for nm, b in stats['scripted']:
            print('   %-14s %d units bare' % (nm, b))
    if stats.get('fitted_y'):
        print('\nvertical: %d field(s) fitted, worst bare band %d -> %d units'
              % (stats['fitted_y'], stats['worst_y_before'],
                 stats['worst_y_after']))
        if stats.get('inverted'):
            print('   %d had INVERTED bounds (top+120 > bottom-120), where the '
                  'camera landed on whichever clamp ran last: %s'
                  % (len(stats['inverted']), ', '.join(stats['inverted'][:8])))
    print('\nmeasured %(measured)d, tightened %(fitted)d, art too short '
          '%(short)d, unreadable %(no_art)d; worst bare band %(worst_before)d '
          '-> %(worst_after)d units' % stats)
    return 0


if __name__ == '__main__':
    sys.exit(main())
