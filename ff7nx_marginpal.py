#!/usr/bin/env python3
r"""
ff7nx_marginpal.py -- give a margin placeholder the palette that can HOLD the
art we are about to quantise into it.

THE BUG THIS FIXES, MEASURED ON `md8_1`
=======================================
Sector 8, straight after the Reactor 1 escape. The extended 16:9 region draws
as a flat tan-mauve column with a near-black block in it, hard up against real
wood-and-rust detail. HANDOFF-80 5.1 called it "flat output means the cell is
drawn from a FLAT SOURCE" and stopped there. It is one step further back than
that: **the source is not flat, the palette is too small to hold it.**

The chain, every step measured against the real archives:

1.  Vanilla `md8_1` has 219 layer-1 tiles. COSMOS ships 279 -- it authors its
    own 16:9 extension, 30 new tiles in two columns at dst x -224 and -208.

2.  All 30 point at FLAT cells: one palette index over the whole 16x16,
    index 1, **palette 0**. That is a placeholder, not art.

3.  Cosmos ships the real art as `md8_1_00_00.dds`. Decoded, all 30 cells
    carry 83-256 distinct colours. **The art is there.** Nothing is missing
    from the mod.

4.  Under FFNx that DDS REPLACES page 0 and the tile's palette is never
    applied (`saveload.cpp:138`, HANDOFF-80 4.1). So on FFNx the palette byte
    of a margin tile is unconstrained -- and Cosmos left it at 0.

5.  We have no per-palette 8-bit path. `ff7nx_marginart` quantises that RGB
    against **the palette the tile names**. `md8_1` palette 0 holds 103
    distinct colours with mean RGB (24, 15, 10) -- a dark table. The margin
    art is bright olive and tan, mean up to (206, 171, 100).

    Quantised against palette 0, per cell:

        cell        DDS mean          vs PALETTE 0        vs PALETTE 3
        (64,224)   (206,171,100)   err 63.3  n=1  REFUSED  err 7.2  n=22
        (96,224)   (198,161, 90)   err 53.7  n=1           err 5.3  n=18
        (128,224)  (189,149, 80)   err 43.2  n=1           err 4.1  n=19
        (224,208)  (153,145, 57)   err 31.5  n=5           err 2.5  n=50

    `n=1` IS THE FLAT BLOCK. `REFUSED` (err > MAX_QUANT_ERR) is the black
    square -- that cell keeps the vanilla placeholder, index 1, RGB(24,8,16).
    Both artefacts in the screenshot, predicted from the numbers.

THE SCALE
=========
`diag_marginpal.py` / `diag_marginpal2.py`, over the mod's own `chunk.9`:

    fields where COSMOS adds layer-1 tiles vanilla lacks        279
    new layer-1 tiles                                        40,131
    of those FLAT in the mod's page (placeholders)            38,285
    palette they name                    palette 0: 33,358 (87%)

and scoring 5,432 of those cells against every palette in their field:

    choice        mean err   mean idx   flat (n=1)   REFUSED
    named           10.82      21.54        529         227     <- today
    per cell         2.22      28.99         11           0     <- the ceiling
    PER PAGE         3.20      25.51         12           0     <- SHIPS
    adjacent        10.52      21.56        496         227

WHY PER PAGE AND NOT PER CELL
-----------------------------
A depth-1 page is drawn through ONE palette. Choosing per cell would put
several palettes on one page and re-create the Sector 6 yellow. Per page
reaches 3.20 against a 2.22 ceiling and a 10.82 baseline -- most of the
available gain, with the invariant intact.

WHY NOT "USE THE PICTURE'S PALETTE"
-----------------------------------
It sounds right -- the margin is a continuation of the scenery beside it -- and
**it is worth almost nothing**: 10.52 against 10.82. Measured before it was
written, which is the only reason it did not ship. The picture's most-common
palette is not the one that fits the margin art.

THE GUARD, AND WHY IT IS NARROW
===============================
A page repointed here must not end up carrying MORE palettes than it does now.
So a slot is repointed only when **every** layer-1 margin tile on it samples a
placeholder cell -- then its margin palette set goes from whatever it was to
exactly one, which is an improvement or a no-op, never a regression. Slots
with a mixed margin are left alone and keep today's behaviour.

The cells themselves are the `placeholder` set `ff7nx_marginart.fillable_cells`
already computes: sampled ONLY by layer-1 tiles wholly outside the 4:3 picture,
and flat. No picture tile can see them, so no picture pixel can move.

WHAT THIS PASS DOES NOT DO
==========================
It does not touch the art, the quantiser, the key, or any interior cell. It
writes ONE BYTE per tile -- `T_PAL` -- and returns the palette `fill_field`
should quantise that page's placeholder cells against. If it is turned off the
build is byte-identical to before.
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_marginblack as MB

TILE = 16
T_PAL = MB.T_PAL                    # 22


# Mean per-channel error, 0..255, that the new palette must BEAT the named one
# by before a page is repointed. A page the two tables serve equally well is
# left alone.
#
# MEASURED by `verify_marginpal.py --limit 30 --gain N`, running the real pass
# over 30 fields both ways:
#
#     gain   pages repointed   cells REFUSED   cells flat (1 index)
#     0.0          15                0                 183
#     1.0          12                0                 183
#     4.0           8                0                 187
#    10.0           5                0                 179
#    20.0           3                7                 240
#
# The two numbers that ARE the reported artefact are REFUSED (the black square)
# and flat (the tan block). Both are already at their floor by 1.0. Dropping to
# 0.0 buys nothing and rewrites 3 more pages; 20.0 lets 7 black squares back.
#
# 1.0 is where the curve flattens, so it is what ships. Note that no page in
# the sample is a marginal call -- the ones this pass touches are wrong by
# 15-40, not by 2.
MIN_ERR_GAIN = 1.0

# Set False for an A/B. With this off the build is byte-identical to the one
# before this module existed.
ENABLED = True


def _enabled_env(env=None):
    env = os.environ if env is None else env
    raw = env.get('FF7NX_MARGIN_PAL')
    if raw is None:
        return ENABLED
    return raw.strip().lower() not in ('0', 'off', 'false', 'no')


def candidate_slots(sec9, surv, pages, placeholder):
    """
    {slot: (placeholder_tiles, other_margin_tiles)} for every depth-1 slot
    carrying at least one layer-1 margin PLACEHOLDER tile.

    `placeholder` is `ff7nx_marginart.fillable_cells`' fourth return value:
    (slot, sx, sy) cells sampled ONLY by layer-1 tiles wholly outside the 4:3
    picture, and flat. Nothing in the picture can see one, so repointing its
    tiles cannot move a pixel inside the 4:3 frame.

    The two groups are returned separately because the safety test in `choose`
    is about what the page's margin palette SET becomes, and the tiles this
    pass does not touch are half of that set.

    A FIRST VERSION REQUIRED THE WHOLE MARGIN TO BE PLACEHOLDER and repointed
    nothing at all on `md8_1`, the field the bug was reported in: its slot 0
    carries 30 placeholder tiles at palette 0 AND 44 real-art margin tiles at
    palette 3. That is not an obstacle, it is the answer -- see `choose`.
    """
    ph, other = defaultdict(list), defaultdict(list)
    for t in MB.read_tiles(sec9, surv, pages):
        p = pages.get(t.slot)
        if p is None or p.depth != 1:
            continue
        if t.layer != 1 or not t.outside_43:
            continue
        if (t.slot, t.sx, t.sy) in placeholder:
            ph[t.slot].append(t)
        else:
            other[t.slot].append(t)
    return {s: (ts, other.get(s, [])) for s, ts in ph.items()}


def score_slot(img, cells, prgbs, quantise, npg):
    """
    (err_per_palette, idx_per_palette) summed over `cells`, or (None, None).

    `img` is the mod's decoded page as (H, W, 3) uint8 at any multiple of 256.
    `cells` is an iterable of (sx, sy) in 256-space. Cells whose art is
    near-black are skipped -- `fill_field` handles those as EMPTY SOURCE and
    the palette makes no difference to a black cell.
    """
    k = img.shape[1] // 256
    if k < 1:
        return None, None
    err = np.zeros(npg)
    nid = np.zeros(npg)
    seen = 0
    for sx, sy in cells:
        blk = img[sy * k:(sy + TILE) * k, sx * k:(sx + TILE) * k]
        if blk.shape[:2] != (TILE * k, TILE * k):
            continue
        small = blk.reshape(TILE, k, TILE, k, 3).mean(axis=(1, 3))
        if small.max() <= 24:                     # EMPTY SOURCE, see fill_field
            continue
        small = small.astype(np.uint8)
        for p in range(npg):
            ix = quantise(small, prgbs[p])
            err[p] += float(np.abs(prgbs[p][ix].astype(np.int16)
                                   - small.astype(np.int16)).mean())
            nid[p] += int(np.unique(ix).size)
        seen += 1
    if not seen:
        return None, None
    return err / seen, nid / seen


def choose(sec9, surv, pages, placeholder, art_for, prgbs, quantise, npg,
           min_gain=None):
    """
    ({slot: palette}, stats). `art_for(page, palette)` returns (rgb, used_pal)
    or None -- exactly `ff7nx_marginart`'s `art` callable with the field name
    already bound.

    THE ART IS FETCHED AT THE PALETTE THE TILE NAMES TODAY, not at the one
    this function picks. Cosmos ships `_00` for these pages and nothing else,
    so the two are the same image; fetching at the new key would silently
    swap in a different dump on the handful of pages the AA folder covers,
    and the point of this pass is to change the COLOUR TABLE, not the art.

    `min_gain` DEFAULTS TO None AND IS RESOLVED HERE, not bound in the
    signature. `min_gain=MIN_ERR_GAIN` as a default is evaluated once at
    import, so a harness that sets `ff7nx_marginpal.MIN_ERR_GAIN` sees no
    effect -- which is exactly how a threshold sweep came back byte-identical
    at 0.25, 1.0, 3.0, 8.0, 20.0 and 40.0 and briefly looked like a finding
    about the data rather than a bug in the harness.
    """
    if min_gain is None:
        min_gain = MIN_ERR_GAIN
    st = {'slots': 0, 'slots_repointed': 0, 'tiles': 0, 'cells': 0,
          'err_before': [], 'err_after': [], 'idx_before': [], 'idx_after': [],
          'chosen': Counter(), 'from': {}}
    chosen = {}
    cand = candidate_slots(sec9, surv, pages, placeholder)
    for slot, (ts, other) in sorted(cand.items()):
        st['slots'] += 1
        pals = {t.pal for t in ts}
        if len(pals) != 1:
            continue                      # already mixed; not ours to worsen
        cur = next(iter(pals))
        if cur >= npg:
            continue
        got = art_for(slot, cur)
        if got is None:
            continue
        img = got[0] if isinstance(got, tuple) else got
        cells = sorted({(t.sx, t.sy) for t in ts})
        err, nid = score_slot(img, cells, prgbs, quantise, npg)
        if err is None:
            continue

        # THE SAFETY TEST, AND IT IS ABOUT THE SET, NOT THE VALUE.
        #
        # A depth-1 page is drawn through ONE palette. What makes a margin go
        # yellow is a page whose margin tiles disagree about which -- so the
        # only thing this pass must never do is ADD a palette to that set.
        #
        # before = {every palette a layer-1 margin tile on this page names}
        # after  = the same with the placeholder tiles moved to `p`
        #
        # `md8_1` slot 0 is the case worth naming: before {0, 3}, and moving
        # the 30 placeholders from 0 to 3 gives {3}. The page gets STRICTLY
        # more coherent, which is the outcome HANDOFF-80 5.1 asked for.
        rest = {t.pal for t in other}
        before = rest | {cur}
        ok = [p for p in range(npg) if len(rest | {p}) <= len(before)]
        best = min(ok, key=lambda p: err[p]) if ok else cur
        if best == cur or (err[cur] - err[best]) < min_gain:
            continue
        chosen[slot] = best
        st['tiles_other'] = st.get('tiles_other', 0) + len(other)
        st['from'][slot] = cur
        st['slots_repointed'] += 1
        st['tiles'] += len(ts)
        st['cells'] += len(cells)
        st['err_before'].append(err[cur])
        st['err_after'].append(err[best])
        st['idx_before'].append(nid[cur])
        st['idx_after'].append(nid[best])
        st['chosen'][(cur, best)] += 1
    return chosen, st


def apply_repoint(sec9, surv, pages, chosen, placeholder):
    """
    (new_sec9, n_tiles). Writes T_PAL on the layer-1 margin tiles of a chosen
    slot THAT SAMPLE A PLACEHOLDER CELL, and on nothing else. One byte per
    tile; no offset, page, uv or dst changes.

    Restricting it to the placeholder set is what makes the pass provably
    unable to move a pixel inside the 4:3 picture: by construction no other
    tile samples those cells.
    """
    if not chosen:
        return sec9, 0
    buf = bytearray(sec9)
    n = 0
    for t in MB.read_tiles(sec9, surv, pages):
        if t.layer != 1 or not t.outside_43:
            continue
        p = chosen.get(t.slot)
        if p is None or (t.slot, t.sx, t.sy) not in placeholder:
            continue
        if buf[t.off + T_PAL] == p:
            continue
        buf[t.off + T_PAL] = p
        n += 1
    return bytes(buf), n


def summarise(st):
    """The build-log line. Empty when the pass repointed nothing."""
    if not st or not st.get('slots_repointed'):
        return ''
    return (
        'margin palette: %s page(s) in %s field(s) repointed -- Cosmos leaves '
        'the palette byte of its 16:9 placeholder tiles at whatever it was '
        '(87%% name palette 0) because FFNx replaces the page with the DDS '
        'and never applies it; we quantise against it, so a dark table turns '
        'bright margin art into one flat index. %s tile(s) rewritten, %s '
        'placeholder cell(s) affected, %s cell(s) remapped rather than '
        'written. Mean quantisation error %.2f -> %.2f, mean palette indices '
        'per cell %.1f -> %.1f. A cell that collapses to 1 index IS the flat '
        'block on screen; one over MAX_QUANT_ERR is refused and keeps the '
        'vanilla filler, which is the black square.'
        % (f"{st['slots_repointed']:,}", f"{st.get('fields', 0):,}",
           f"{st.get('tiles', 0):,}", f"{st['cells']:,}",
           f"{st.get('remapped', 0):,}",
           float(np.mean(st['err_before'])), float(np.mean(st['err_after'])),
           float(np.mean(st['idx_before'])), float(np.mean(st['idx_after']))))
