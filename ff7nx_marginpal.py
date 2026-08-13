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
        # The art sources now hand back RGBA -- the 4th channel is the mod's
        # own COVERAGE, which `ff7nx_marginart` uses to tell an atlas gap from
        # a black pixel. This scorer only compares colour, so take RGB.
        small = (np.ascontiguousarray(blk[..., :3])
                 .reshape(TILE, k, TILE, k, 3).mean(axis=(1, 3)))
        if small.max() <= 24:                     # EMPTY SOURCE, see fill_field
            continue
        small = small.astype(np.uint8)
        # One dedup for the whole palette sweep. See ff7nx_marginart.quantise.
        #
        # IMPORTED LAZILY, ON PURPOSE. `ff7nx_marginart` imports THIS module at
        # its own import time, so a top-level `from ff7nx_marginart import ...`
        # here is a circular import that raises before the build starts.
        try:
            from ff7nx_marginart import dedup_cell as _dedup_cell
            _dd = _dedup_cell(small)
        except Exception:                                      # noqa: BLE001
            _dd = None
        for p in range(npg):
            ix = quantise(small, prgbs[p], False, _dd)
            err[p] += float(np.abs(prgbs[p][ix].astype(np.int16)
                                   - small.astype(np.int16)).mean())
            nid[p] += int(np.unique(ix).size)
        seen += 1
    if not seen:
        return None, None
    return err / seen, nid / seen


# HOW MUCH EXTRA QUANTISATION ERROR THE LAYER-1 RESTRICTION MAY COST.
#
# 0..255 per channel, same scale as MAX_QUANT_ERR (60, the refuse-outright
# threshold). Above this the art is judged not to belong to layer 1's colour
# world and keeps the palette it would otherwise have chosen. See the use
# site: build 54 had no escape and turned mds6_3's blue-grey roof brown.
#
# 10 is provisional. The build now logs the penalty distribution so the right
# value can be read off a real run instead of guessed at again.
LAYER1_MAX_PENALTY = 10.0


# HOW FAR THE ART'S HUE MAY SIT FROM LAYER 1'S BEFORE THE RESTRICTION LIFTS.
#
# FINDINGS-148. The error-based escape above is a DUD and the build log said
# so: penalty p90 1.33 against a threshold of 10, fired 6 times in 335 pages.
# It cannot work, for a reason that is a property of the measure rather than
# of the threshold.
#
# `err` is a MAGNITUDE. mds6_3's right margin is a dark roof, mean (38, 32,
# 25), and a dark colour is cheap to approximate in ANY palette -- the whole
# candidate set scores within ~1 of each other, so no threshold separates
# "olive palette expressing grey art" from "olive palette expressing olive
# art". That is the trap the project's notes already name, one level down:
# the measure never asks whether the image is the RIGHT image.
#
# CHROMATICITY IS SCALE-FREE. Normalising r,g,b by their sum throws away
# brightness and keeps colour direction, which is exactly the axis that
# survives the darkness. MEASURED on Cosmos's own section 9 + DDS, mds6_3:
#
#     LEFT margin   art ( 85, 67, 33) OLIVE  -> hue gap 0.0000  constrain
#     interior      art (128,118, 60) OLIVE
#     RIGHT margin  art ( 38, 32, 25) GREY   -> hue gap 0.0483  ESCAPE
#
# Those are the two cases whose correct answer is known from hardware: build
# 54 FIXED the left margin and BROKE the right. Any threshold in 0.005..0.048
# gets both right. 0.030 sits on a plateau -- 19 of 175 sampled margin sides
# escape at both 0.030 and 0.035 -- so it is a gap in the distribution rather
# than a value tuned to the sample.
LAYER1_MAX_HUE_GAP = 0.030

# HOW FAR OFF-HUE A PALETTE MAY BE AND STILL COMPETE ON ERROR. FINDINGS-149.
#
# Same units and same calibration as LAYER1_MAX_HUE_GAP -- both are distances
# between chromaticity vectors, and the two known-answer cases put the
# boundary between 0.000 (right) and 0.048 (wrong). mds5_5's sky is a third
# case and it is not close: 0.034 for the palette that works against 0.283
# for the one that flattened it.
PALETTE_MAX_HUE_GAP = 0.030


def _chromaticity(rgb):
    """rgb / (r+g+b) -- colour direction with brightness divided out."""
    v = np.asarray(rgb, float)
    s = float(v.sum())
    return v / s if s > 1e-6 else np.zeros(3)


def art_chroma(img, cells):
    """
    Mean chromaticity of the mod's art over `cells`, or None.

    Near-black texels are excluded on the SAME test `score_slot` and
    `fill_field` use (max <= 24 is EMPTY SOURCE). Black has no hue -- letting
    it in drags every page toward grey and the gate stops discriminating.
    """
    k = img.shape[1] // 256
    if k < 1:
        return None
    acc = []
    for sx, sy in cells:
        blk = img[sy * k:(sy + TILE) * k, sx * k:(sx + TILE) * k]
        if blk.shape[:2] != (TILE * k, TILE * k):
            continue
        px = np.ascontiguousarray(blk[..., :3]).reshape(-1, 3).astype(float)
        px = px[px.max(1) > 24]
        if px.size:
            acc.append(px.mean(0))
    if not acc:
        return None
    return _chromaticity(np.array(acc).mean(0))


def pal_chroma(prgb):
    """Mean chromaticity of a palette's drawable entries, or None.

    ENTRY 0 IS EXCLUDED. On a depth-1 page index 0 is drawn, but it is also
    what every transparency pass rewrites, so its colour says more about this
    build's key handling than about the palette's colour world.
    """
    v = np.asarray(prgb, float)[1:]
    v = v[v.max(1) > 0]
    if not v.size:
        return None
    return _chromaticity(v.mean(0))


def hue_gap(img, cells, prgbs, ok, keep):
    """
    How much closer in hue the UNRESTRICTED best is than the layer-1 best.

    None when it cannot be measured -- caller must then fall back to the
    error test rather than treat an unmeasurable gap as an escape.
    """
    ac = art_chroma(img, cells)
    if ac is None:
        return None
    d = {}
    for p in set(ok) | set(keep):
        pc = pal_chroma(prgbs[p])
        if pc is not None:
            d[p] = float(np.linalg.norm(ac - pc))
    dk = [d[p] for p in keep if p in d]
    do = [d[p] for p in ok if p in d]
    if not dk or not do:
        return None
    return min(dk) - min(do)


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
    # Palettes the INTERIOR backdrop uses -- layer 1, inside the 4:3 picture.
    # See the note at the selection below.
    interior_l1 = {t.pal for t in MB.read_tiles(sec9, surv, pages)
                   if t.layer == 1 and not t.outside_43 and t.pal < npg}
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
        # A LAYER-1 MARGIN MUST USE A LAYER-1 PALETTE. FINDINGS-142.
        #
        # `ok` above only asks "does this keep the page's palette set from
        # growing", and `best` then takes the LOWEST QUANTISATION ERROR over
        # every palette in the field -- including the LAYER-2 overlay
        # palettes. That is the trap this project's own notes name: "'can the
        # destination palette express this image' -- and a grey image is
        # expressible in almost any palette. It never asks whether the image
        # is the RIGHT image."
        #
        # MEASURED on the build-53 archive, all 631 fields with margin tiles:
        # 93 of them have their 16:9 margin drawn through a palette LAYER 1
        # NEVER USES -- 9,041 tiles. `mds6_3`, the field before Wall Market,
        # is the reported one:
        #
        #     pal 0 -> layer 1, 114 tiles   (126.7, 117.2, 57.3)  OLIVE
        #     pal 1 -> layer 1,  61 tiles   (122.0, 112.9, 56.6)  OLIVE
        #     pal 2 -> layer 2, 128 tiles   ( 71.0,  71.8, 51.1)  GREY
        #     pal 3 -> layer 2,  64 tiles   ( 67.3,  64.2, 43.2)  GREY
        #     built margin: pal 2 (39 tiles), pal 3 (81 tiles)
        #
        # The margin is layer-1 backdrop wearing the overlay's colour table,
        # and the dense repack then promotes those cells to truecolor and
        # bakes it in permanently. Grey margin, olive picture.
        #
        # INTERIOR layer 1, not all of layer 1: the margin tiles are
        # themselves layer 1, so including them would let a wrong choice
        # justify itself on the next build.
        #
        # EMPTY SET MEANS DO NOT RESTRICT. `ship_2` has no interior layer-1
        # tile at all, so there is nothing to be consistent with and the old
        # behaviour is the only defined one.
        if interior_l1:
            _keep = [p for p in ok if p in interior_l1]
            if _keep:
                # ...UNLESS THE ART GENUINELY IS NOT THAT COLOUR. FINDINGS-147.
                #
                # Build 54 applied this restriction unconditionally and that
                # was too blunt. It fixed mds6_3's LEFT margin, where the art
                # is olive and a grey layer-2 palette had won on a technicality
                # -- and it broke the RIGHT margin of the same field, where the
                # art really is the blue-grey of a roof. Forced onto olive
                # palette 0, the roof came out brown, and the user saw the
                # discontinuity immediately:
                #
                #   mds6_3 build 57, layer-1 tiles
                #     interior      pal 0/1  OLIVE     (126.7, 117.2, 57.3)
                #     RIGHT margin  pal 0 x45          forced -- was pal 2/3
                #     pal 2/3                GREY/BLUE ( 71.0,  71.8, 51.1)
                #
                # The restriction exists to stop a grey palette winning for
                # OLIVE art. It must not force olive onto art that is grey. So
                # ask what the restriction COSTS: `err` is already computed for
                # every palette, and if the best layer-1 palette is far worse
                # than the best palette overall, the art is telling us it does
                # not belong to layer 1's colour world and we leave it alone.
                _b_all = min(ok, key=lambda q: err[q])
                _b_l1 = min(_keep, key=lambda q: err[q])
                _penalty = float(err[_b_l1] - err[_b_all])
                st.setdefault('layer1_penalty', []).append(_penalty)
                # HUE DECIDES; ERROR IS ONLY A BACKSTOP. FINDINGS-148.
                #
                # `_penalty` stays because a genuinely enormous quantisation
                # cost is real information (max 78.77 on build 58) and it is
                # cheap to keep. But it fired 6 times in 335 pages, so it is
                # NOT the mechanism -- `_gap` is. They are OR'd, and counted
                # apart so the log can attribute every escape to one of them.
                _gap = hue_gap(img, cells, prgbs, ok, _keep)
                if _gap is not None:
                    st.setdefault('layer1_hue_gap', []).append(_gap)
                _by_hue = _gap is not None and _gap > LAYER1_MAX_HUE_GAP
                if _by_hue or _penalty > LAYER1_MAX_PENALTY:
                    st['layer1_escaped'] = st.get('layer1_escaped', 0) + 1
                    if _by_hue:
                        st['layer1_escaped_hue'] = (
                            st.get('layer1_escaped_hue', 0) + 1)
                else:
                    if len(_keep) != len(ok):
                        st['layer1_constrained'] = (
                            st.get('layer1_constrained', 0) + 1)
                    ok = _keep
        # THE FINAL CHOICE IS HUE-GATED TOO. FINDINGS-149.
        #
        # Build 59 fixed the ESCAPE with chromaticity and left THIS line on
        # pure error, so the same blindness survived one level down.
        #
        # MEASURED, mds5_5 (Sector 5 slum outskirts), the flat-olive sky the
        # user reported: this pass repointed slot 1 from palette 0 to
        # palette 1 because err fell 14.3 -> 9.3.
        #
        #     Cosmos's art there   (103.0, 106.8, 102.1)  cool grey sky
        #     pal 0  (107.8, 107.8, 92.4)  max blue 189   hue dist 0.034
        #     pal 1  (110.2,  87.1, 23.5)  max blue  41   hue dist 0.283
        #
        # Palette 1 CANNOT REPRESENT BLUE -- its bluest entry is 41 -- and
        # the sky rendered at blue 12.7 against the interior's 15..27. Error
        # still preferred it, because the art is desaturated and mean
        # absolute error is dominated by mid-tones while hue direction is
        # exactly what it throws away.
        #
        # So: keep only the palettes whose hue is within PALETTE_MAX_HUE_GAP
        # of the best available, THEN let error choose among them. Error is
        # demoted to a tie-breaker between palettes that are already the
        # right colour -- which is the only question it can actually answer.
        best = cur
        if ok:
            _pool = ok
            _ac = art_chroma(img, cells)
            if _ac is not None:
                _hd = {}
                for _p in ok:
                    _pc = pal_chroma(prgbs[_p])
                    if _pc is not None:
                        _hd[_p] = float(np.linalg.norm(_ac - _pc))
                if _hd:
                    _lim = min(_hd.values()) + PALETTE_MAX_HUE_GAP
                    _near = [p for p in ok if _hd.get(p, 9.9) <= _lim]
                    if _near:
                        _e = min(ok, key=lambda q: err[q])
                        if _e not in _near:
                            st['hue_vetoed'] = st.get('hue_vetoed', 0) + 1
                            st.setdefault('hue_veto_dist', []).append(
                                float(_hd.get(_e, 0.0) - min(_hd.values())))
                        _pool = _near
            best = min(_pool, key=lambda p: err[p])
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
    # THE BASE LINE IS FORMATTED FIRST, THEN THE SUFFIX IS APPENDED.
    #
    # `%` binds tighter than `+`. Writing `base + suffix % args` folds the
    # suffix INTO the format string, its own %s gets eaten by the suffix's
    # operand, and the outer tuple then has nothing left to fill --
    # "TypeError: not all arguments converted during string formatting",
    # which is exactly how build 54 died after 20 minutes. Parenthesise the
    # base format, then concatenate. `test_marginpal_summarise` below calls
    # this function, because py_compile does not.
    base = (
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
    n = st.get('layer1_constrained', 0)
    esc = st.get('layer1_escaped', 0)
    pen = st.get('layer1_penalty') or []
    if n:
        base += (
            ' -- LAYER-1 CONSTRAINT: %s page(s) had a LAYER-2 palette excluded '
            'from the candidate set. The 16:9 margin is layer-1 backdrop; '
            'scoring it against every palette in the field let a grey overlay '
            'table win on quantisation error, so the margin came out a '
            'different colour from the picture (mds6_3, the field before Wall '
            'Market: interior on olive palettes 0/1, margin on grey layer-2 '
            'palettes 2/3).' % f"{n:,}")
    hg = st.get('layer1_hue_gap') or []
    if esc or pen:
        base += (
            ' -- ESCAPE: %s page(s) kept the palette they would otherwise have '
            'chosen -- %s of them on HUE, the rest on quantisation error -- '
            'because the art genuinely is not layer 1\'s colour. Build 54 '
            'applied the restriction with no escape and turned mds6_3\'s '
            'blue-grey ROOF brown by forcing it onto olive palette 0, which is '
            'the discontinuity reported on build 57. Cosmos widened layer 1 in '
            '261 fields and layer 2 to match in only 68, so in the other 193 '
            'the artist painted what the missing overlay would have shown INTO '
            'the layer-1 margin art: that art really is the roof\'s colour.'
            % (f"{esc:,}", f"{st.get('layer1_escaped_hue', 0):,}"))
    if pen:
        base += (
            ' Penalty (error, threshold %.1f): mean %.2f, median %.2f, p90 '
            '%.2f, max %.2f over %s page(s).'
            % (LAYER1_MAX_PENALTY,
               float(np.mean(pen)), float(np.median(pen)),
               float(np.percentile(pen, 90)), float(np.max(pen)),
               f"{len(pen):,}"))
    vet = st.get('hue_vetoed', 0)
    vd = st.get('hue_veto_dist') or []
    if vet:
        base += (
            ' -- HUE VETO: on %s page(s) the LOWEST-ERROR palette was rejected '
            'because its hue is more than %.3f from the art\'s, and error only '
            'chose between the palettes that were already the right colour. '
            'mds5_5 (Sector 5 slum outskirts) is the case this was built from: '
            'the pass moved slot 1 to palette 1 on error 14.3 -> 9.3, and '
            'palette 1\'s bluest entry is 41, so the cool grey sky rendered '
            'flat olive at blue 12.7 against the interior\'s 15-27. Rejected '
            'hue distance: mean %.4f, median %.4f, max %.4f.'
            % (f"{vet:,}", PALETTE_MAX_HUE_GAP,
               float(np.mean(vd)) if vd else 0.0,
               float(np.median(vd)) if vd else 0.0,
               float(np.max(vd)) if vd else 0.0))
    if hg:
        base += (
            ' Hue gap (threshold %.3f): mean %.4f, median %.4f, p90 %.4f, max '
            '%.4f over %s page(s). READ THESE TWO TOGETHER -- error p90 near '
            'the threshold means the error test is inert and hue is doing the '
            'work, which is the state FINDINGS-148 predicts.'
            % (LAYER1_MAX_HUE_GAP,
               float(np.mean(hg)), float(np.median(hg)),
               float(np.percentile(hg, 90)), float(np.max(hg)),
               f"{len(hg):,}"))
    return base
