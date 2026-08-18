#!/usr/bin/env python3
"""
ff7nx_palrange.py -- no tile may name a palette that does not exist.

FINDINGS-158.  Cosmos's section 9 was authored against the PC field, whose
palette table is larger than the Switch port's.  `build.py` splices the mod's
section 9 (SAFE_MOD_SECTIONS) and keeps Switch vanilla's section 3, so 13,481
tiles across 92 fields name a palette index at or past the end of the table.

On FFNx that byte is never read -- it replaces the page with the DDS and never
applies a palette.  The Switch port DOES apply it, and the lookup runs off the
end of the palette array into whatever memory follows.  That is the white
speckle in mds5_3 and the black blobs in mds5_5.

IT IS INVISIBLE OFFLINE.  `field_bg_dense` decodes with `pal % npg` and so
does `render_field`, so every renderer we own draws these cells through a
WRAPPED palette and looks correct.  Only the console reads past the end.  Do
not "verify" this with a render that clamps.

SECOND, LARGER EFFECT: `ff7nx_marginart` skips these cells outright --

    if pal >= npg:  st['no_dds'] += len(cs);  continue

-- so they never receive Cosmos's art either.  Fixing the palette BEFORE the
margin passes run therefore does two things at once: it stops the garbage read
and it lets the upscaled art reach 13,481 tiles that were being passed over.

REPLACEMENT RULE: PER CELL, BY RENDERING IT (build 68).  An earlier version
used palette 0 everywhere and that caused the build 67 WALL MARKET REGRESSION
-- flat tan squares.  A vanilla filler cell is entirely index 0, marginart's
keep-0 rule protects index 0 so the art never lands, and the rendered colour
is then decided purely by entry 0 of the palette we name.  Palette 0's entry 0
in mrkt2 is (224,168,104): the tan.

MEASURED, rendered colour of the out-of-range cells vs Cosmos's art:

    field     pal 0 always            best-by-render
    mrkt2     err 134.8  tan 151/151  err 11.9  tan 6/151
    mrkt1     err  76.8  tan   0/ 96  err 10.3  tan 0/ 96
    mrkt4     err  24.3  tan   0/ 64  err  2.6  tan 0/ 64
    mds5_3    err  41.8  tan   0/296  err 12.7  tan 0/296
    mds5_5    err  39.0  tan   0/245  err  1.3  tan 0/245

The old rule was chosen on a fidelity test that never included Wall Market,
and that test ran AFTER the repack -- where most of these cells had been
promoted to truecolor and were invisible to the comparison.  Measure the
thing that ships, on the fields that break.

SUPERSEDED: PALETTE 0.  That is FFNx's own fallback when the exact
palette's DDS is missing (saveload.cpp:138), and Cosmos ships `_00` for ~87%
of pages -- so palette 0 is the table its art was authored against.

MEASURED against Cosmos's art (`_fidelity.py`), four fields, three candidate
rules:

    field      neighbour-modal   wrap (pal %% npg)   PALETTE 0
    mds5_3            9.92            13.37            9.63
    mds5_5            8.01             7.29            7.29
    nivl_b22         42.59            28.96           28.96
    trnad_3          42.79            42.71           42.71

Palette 0 is best or tied-best in all four.  The neighbour rule -- which is
what this module did first -- is actively WORSE in nivl_b22 (29.07 -> 42.59),
so "keep it in the same colour world as its neighbours" sounded right and
measured wrong.

NOTE the baseline cannot be measured offline: `_fidelity` decodes with
`pal %% npg` like every other tool here, so an unfixed field scores as if it
were wrapped.  Only hardware reads past the end.  Compare rules to each
other, never to "no fix".
"""
from __future__ import annotations
from collections import Counter, defaultdict
import os as _os

# THE CELL IS 32 UNITS ON A size_flag PAGE. See _best_palette.
#
# SEVENTH_NX_NO_PALRANGE_BIGCELL=1 restores the 16-unit read, i.e. build 88's
# black square in Mt. Corel's top-left corner.
BIG_CELL = _os.environ.get('SEVENTH_NX_NO_PALRANGE_BIGCELL') != '1'

# SCORE AN ALL-INDEX-0 CELL BY WHAT THE PALETTE CAN HOLD. See _best_palette.
# SEVENTH_NX_NO_PALRANGE_QUANT=1 restores the entry-0 score, i.e. the flat
# pale block in Mt. Corel's top-left corner.
ATLAS_BY_QUANTISE = _os.environ.get('SEVENTH_NX_NO_PALRANGE_QUANT') != '1'

# ---------------------------------------------------------------------------
# THE RENDER SCORE IS THE RIGHT RULE FOR LAYER 1 AND THE WRONG ONE ABOVE IT.
# FINDINGS-230.
#
# `_best_palette` scores a candidate by rendering the cell and comparing it to
# COSMOS'S ART. That asks "which palette best reproduces this picture", which
# is exactly right for layer 1, whose job is to BE the picture.
#
# A layer-2+ tile is drawn through a BLEND MODE. Its job is to CONTRIBUTE to
# the picture -- a green wash, a haze, a light cone -- and the quantity it has
# to match is not Cosmos's RGB for that cell but the tint its NEIGHBOURS are
# laying down. Score an additive overlay by colour distance to a palette-0
# render and the neutral palette wins every time, because neutral is what is
# closest to the source image.
#
# MEASURED, `mds7plr1`, the 16:9 margin overlay Cosmos authors at palette 11
# in a field whose table ends at 10:
#
#     palette  5   mean RGB ( 77.4,  74.1,  55.4)   <- the render score picks
#     palette  9   mean RGB ( 18.1,  37.3,  21.6)   <- the interior overlay uses
#
# Palette 9 is the dark green that lays the wash down; palette 5 is a neutral
# grey-brown that adds haze and no hue. That is "the greenish lighting effect
# is missing exactly in the 16:9 regions", to the byte.
#
# ARCHIVE-WIDE, tiles naming a palette their field does not have:
#
#     layer 1   10,586 tiles   66 fields      <- render score, unchanged
#     layer 2   14,969 tiles  109 fields      <- this rule
#     layer 3    1,452 tiles   17 fields
#     layer 4      287 tiles    6 fields
#               -------
#               27,294         61% on a BLENDED layer
#
# THE DOCSTRING ABOVE REJECTS THE NEIGHBOUR RULE, and that rejection stands
# where it was measured. Its table (mds5_3, mds5_5, nivl_b22, trnad_3) scores
# fidelity to Cosmos's art over ALL LAYERS AT ONCE, so it is dominated by
# layer 1 -- which is not the population this flag touches. Do not read it as
# evidence about layers 2+; it never separated them.
#
# THE RULE ON ITS OWN IS NOT ENOUGH, AND THE MEASUREMENTS SAY SO. Four
# versions, scored on the SEAM across the 4:3 edge (`_kpal230.py`), 83 fields:
#
#     A  copy the neighbour's palette INDEX      35 better  11 worse  +95.8
#     B  render-score to the NEIGHBOURHOOD       35 better   8 worse  +95.8
#     C  B + the per-layer seam veto below       35 better   0 worse   +0.1
#     D  C + the SET_TOL incumbent preference    35 better   0 worse   +0.0
#
# A is the obvious rule and it is wrong: copying a palette INDEX onto a
# different cell's indices is not copying its COLOUR, because the two cells
# carry different index content. `mds5_5` regressed 72 points under it.
#
# The veto is what makes it safe, and the veto had to be debugged before it
# was: it applied the trial assignment to every tile sharing a cell, while
# `fix_field` rewrites only the OUT-OF-RANGE ones. That dragged the inner band
# toward the outer one and made the reference arm look 5x better than it is
# (`mds7plr1`: 7.26 modelled against a true 35.21), so the veto reverted the
# field the whole finding rests on. See `_seam_of`.
#
# `SEVENTH_NX_NO_PALRANGE_LAYER=1` turns the whole thing off -> build 110
# exactly, byte for byte. Containment is proven: 50 of 50 control fields with
# nothing out of range come out byte-identical.
LAYER_SCOPED = _os.environ.get('SEVENTH_NX_NO_PALRANGE_LAYER') != '1'
NEIGHBOUR_LAYERS = frozenset((2, 3, 4))

# FIELDS WHERE THE RETARGET COSTS MORE THAN IT BUYS. MEASURED, NOT GUESSED.
#
# The palette byte feeds `field_bg_dense`'s seating, and seating is capacity-
# and order-sensitive: change which palette a cell is drawn through and the
# candidate order can shift, which can displace an fx group that only fits one
# way. `mtcrl_8` is the one field in the archive where that happens.
#
#     mtcrl_8   seam 7.92 -> 0.00   truecolor 741 -> 510 tiles   pages 4 -> 5
#
# Build 110 had taken that field from 509 to 741, so the retarget hands back
# almost all of it to close a 7.9-point seam. Bad trade, so it does not run
# there.
#
# IT IS NOT THE TEXTURE SET. Checked directly: (cell, palette) pairs are 511
# either way and the one multi-palette cell is {0, 2} either way, so
# `SET_TOL`'s incumbent preference is already doing its job. The loss is
# downstream, in seating.
#
# THIS LIST IS REGENERATED, NOT MAINTAINED BY HAND:
#
#     python3 _kpalgate.py --names _kpalgate_names.txt --resume
#     -> any field with d_tiles_tc < 0 belongs here
#
# It is a measured exclusion and it is honest about being one. The general
# answer is a field-level admission test that runs the repack both ways and
# keeps the retarget only where it costs no promoted tiles -- the same pattern
# `_multipal_admit` uses one layer down. That is the follow-up; this is what
# makes build 111 shippable without a regression in the meantime.
PALRANGE_LAYER_EXCLUDE = frozenset(('mtcrl_8',))
NEIGHBOUR_K = 8                # modal palette of the K nearest in-range tiles
                               # of the SAME layer. Not the global modal: an
                               # overlay can legitimately change tint across a
                               # field, and a single answer per field would
                               # flatten that into one. Not the single nearest
                               # either -- that propagates one stray tile.

import diag_common as DC
import ff7nx_marginblack as MB
import field_bg_native as FN

T_PAL = MB.T_PAL
# IMPORTED, never retyped (HANDOFF-222 s0.1). The vanilla 4:3 window is
# dst_x in [-160, 160); everything outside it is what the widescreen mod
# authored, and the boundary is where the seam this module now measures lives.
HALF_43 = MB.HALF_43


def palette_rows(sec3):
    """How many palette rows section 3 actually provides."""
    try:
        cols, hdr, npg, cpp = MB.palette_colours(sec3)
        return len(cols) if cols is not None else npg
    except Exception:                                          # noqa: BLE001
        return 0


def _covered(art_for, slot, sx, sy):
    """Does Cosmos actually PAINT this cell? (any texel with alpha > 0)

    BUILD 67 REGRESSION, and this is the whole reason this gate exists.
    Repointing a tile whose cell the mod does not paint hands it to
    ff7nx_marginart, whose keep-0 path then writes index 0 across the whole
    cell (`uncovered` texels keep the key).  Index 0 is DRAWN on a depth-1
    page, through palette 0, whose entry 0 ff7nx_palkey de-fringes to the
    filler colour -- (224,168,104) in Wall Market.  MEASURED: all 151 cells
    repointed in mrkt2 collapsed to ONE index and rendered flat tan.  Before
    the repoint they named an out-of-range palette, marginart skipped them,
    and they kept vanilla content.

    So: only repoint a cell the mod actually paints.  Where it paints nothing
    there is no art to rescue and the repoint can only make it worse.
    """
    if art_for is None:
        return True
    got = None
    for _p in (0,):
        try:
            got = art_for(slot, _p)
        except Exception:                                      # noqa: BLE001
            got = None
        if got is not None:
            break
    img = (got[0] if isinstance(got, tuple) else got) if got is not None else None
    if img is None:
        return False
    tm = getattr(img, 'tmask', None)
    if tm is None:
        return True
    try:
        s = img.px // 256
        blk = tm[sy * s:(sy + 16) * s, sx * s:(sx + 16) * s]
    except Exception:                                          # noqa: BLE001
        return True
    if blk.size == 0:
        return False
    return bool((~blk).any())


def _best_palette(arrays, pal565, art_for, rows, slot, sx, sy, edge=16):
    """The valid palette that renders this cell closest to Cosmos's art.

    `edge` IS THE CELL'S OWN SIZE AND IT IS NOT ALWAYS 16. FINDINGS-189 E.

    A `size_flag` page is an 8x8 grid of 32-unit cells and the parallax layers
    use those pages. Reading a 16-unit window of one samples its TOP-LEFT
    QUADRANT, and this function then answers a question about a quarter of the
    cell as though it were the whole.

    MEASURED on `mtcrl_4`, slot 5 cell (0, 128) -- the tile in the top-left
    corner of Mt. Corel, reported from hardware as a black square:

        Cosmos's art over the full 32-unit cell   mean RGB (121, 118, 110)
        Cosmos's art over the 16-unit quadrant    mean RGB (6.5, 6.5, 8.0)

    The quadrant is black, so every all-black palette scored err 6.99 and the
    bright ones scored 25 to 158. This function dutifully picked palette 0 --
    whose every entry is (0, 0, 0) -- and was right about the data it was
    given and wrong about the cell.

    The rest follows on its own: `ff7nx_marginart` then cannot quantise sky
    into an all-black table, refuses the cell as wildly off-colour, and the
    tile is left drawing index 0 through palette 0. Black square.
    """
    import numpy as np
    idx = arrays.get(slot)
    if idx is None or art_for is None or not rows:
        return 0
    blk = idx[sy:sy + edge, sx:sx + edge]
    got = None
    try:
        got = art_for(slot, 0)
    except Exception:                                          # noqa: BLE001
        got = None
    img = (got[0] if isinstance(got, tuple) else got) if got is not None else None
    if img is None:
        return 0
    try:
        f = img.px // 256
        page = np.frombuffer(img.buf, '<u2').reshape(img.px, img.px)
        a = page[sy * f:(sy + edge) * f,
                 sx * f:(sx + edge) * f].astype(np.int64)
        tru = np.stack([((a >> 11) & 31) << 3, ((a >> 5) & 63) << 2,
                        (a & 31) << 3], -1).astype(float)
        if f > 1:
            tru = tru.reshape(edge, f, edge, f, 3).mean((1, 3))
    except Exception:                                          # noqa: BLE001
        return 0
    # AN ATLAS GAP IS SCORED BY WHAT THE PALETTE CAN HOLD, NOT BY ITS ENTRY 0.
    #
    # The docstring above says an all-index-0 cell "reduces to the palette whose
    # entry 0 matches the art, which is exactly the question". That was true
    # when such a cell STAYED FLAT. It no longer does: with the size_flag fixes
    # in `ff7nx_marginart`, an atlas gap on a parallax page now receives
    # Cosmos's art, quantised through whatever palette THIS function names. So
    # the question became "which table can represent this art", and scoring
    # entry 0 answers a question nobody is asking any more.
    #
    # MEASURED on `mtcrl_4` slot 5 cell (0,128) -- the tile in Mt. Corel's
    # top-left corner, reported from hardware as "jarring, discontinuous ...
    # that square just stands out". Cosmos's art there is mean RGB
    # (121,118,110) with a per-channel std of (115,112,106): a high-contrast
    # cloud edge, not a flat patch.
    #
    #     palette   entry-0 score (old rule)   quantise score (this rule)
    #        7            116.83                       4.61
    #        1            116.83                       9.44
    #       12             35.93                      10.42
    #        8             53.17                      17.00
    #        9             78.83                      20.75   <- old rule chose
    #        0            116.83                     116.83
    #
    # The old rule cannot see past entry 0, so it ranked eight palettes as
    # identically hopeless and picked among the rest by a colour the cell will
    # not end up drawing. The new rule picks palette 7 and the cell comes out
    # 4.5x closer to the art -- detail instead of a flat block.
    #
    # SCOPED TO THE ALL-ZERO CELL. A cell with real indices keeps the old
    # score, because there the rendering of those indices IS what ships and
    # quantising art the cell will never receive would be the wrong question in
    # the other direction.
    #
    # SCOPED TO THE PARALLAX PAGE, AND THAT SCOPE IS LOAD-BEARING. A cell only
    # RECEIVES art if `ff7nx_marginart`'s atlas arm fires on it, and outside a
    # `size_flag` page that arm still requires a bright entry 0. So on an
    # ordinary page an all-index-0 cell STAYS FLAT, its rendered colour is
    # entry 0, and scoring by quantisation would choose a table for detail the
    # cell never gets while ignoring the one colour it does draw.
    #
    # That is precisely the BUILD 67 WALL MARKET REGRESSION this module's
    # docstring opens with -- 151 flat tan squares in `mrkt2` from a palette
    # chosen on the wrong criterion. `edge > 16` is true only on a size_flag
    # page, so mrkt2, mds5_3, mds5_5 and every other 16-unit field keeps the
    # entry-0 rule exactly as measured there.
    _all0 = not bool(blk.any())
    if _all0 and ATLAS_BY_QUANTISE and edge > 16:
        try:
            import ff7nx_marginart as _MA
            best, bestp = None, 0
            for p in range(min(rows, len(pal565))):
                prgb = _MA.palette_rgb_565(pal565[p]) if hasattr(
                    _MA, 'palette_rgb_565') else None
                if prgb is None:
                    v = pal565[p].astype(np.int64)
                    prgb = np.stack([((v >> 11) & 31) << 3,
                                     ((v >> 5) & 63) << 2,
                                     (v & 31) << 3], -1).astype(np.uint8)
                q = _MA.quantise(tru.astype(np.float32), prgb)
                e = float(np.abs(prgb[q].astype(float) - tru).mean())
                if best is None or e < best:
                    best, bestp = e, p
            if best is not None:
                return bestp
        except Exception:                                      # noqa: BLE001
            pass                    # fall through to the entry-0 rule
    best, bestp = None, 0
    for p in range(min(rows, len(pal565))):
        v = pal565[p][blk].astype(np.int64)
        ren = np.stack([((v >> 11) & 31) << 3, ((v >> 5) & 63) << 2,
                        (v & 31) << 3], -1).astype(float)
        e = float(np.abs(ren - tru).mean())
        if best is None or e < best:
            best, bestp = e, p
    return bestp


def _cell_mean(arrays, pal565, slot, sx, sy, pal, edge=16):
    """Mean RGB this cell DRAWS through `pal`, index 0 excluded.

    Index 0 is excluded because on a blended layer it contributes nothing;
    counting it would score a palette by how much empty space the cell happens
    to carry instead of by the colour it lays down.
    """
    a = arrays.get(slot)
    if a is None or pal >= len(pal565):
        return None
    idx = a[sy:sy + edge, sx:sx + edge]
    m = idx != 0
    if not m.any():
        return None
    import numpy as _np
    v = pal565[pal][idx][m].astype(_np.uint16)
    return _np.array([float((((v >> 11) & 31) << 3).mean()),
                      float((((v >> 5) & 63) << 2).mean()),
                      float(((v & 31) << 3).mean())])


# DO NOT GROW A CELL'S PALETTE SET FOR A MARGINAL COLOUR GAIN. FINDINGS-230 s4.
#
# `field_bg_dense` builds one truecolor texture per (cell, PALETTE) actually
# referenced. Retargeting a margin tile onto a palette the cell is not already
# drawn through therefore adds a TEXTURE, which needs a seat, which can need a
# page -- and a field that cannot get the page loses promotions it already had.
#
# MEASURED, `mtcrl_8`: one retargeted cell, seam 7.92 -> 0.00, and 231 tiles
# fell off the truecolor page with one page added. Build 110 had taken that
# field from 509 to 741 truecolor tiles; this handed almost all of it back for
# a 7.9-point seam.
#
# So among candidates that score within `SET_TOL` of the best, prefer one the
# cell ALREADY names. The colour cost is bounded by the tolerance and the
# structural cost goes to zero. Where no incumbent is close enough the best
# candidate still wins -- this is a preference, not a veto.
SET_TOL = 8.0                  # out of 255. Below the 5-bit quantisation step
                               # times two, so an incumbent inside it is not a
                               # visibly different answer.


def _neighbour_palette(bad_tile, in_range, arrays, pal565, rows,
                       k=NEIGHBOUR_K, edge=16, incumbent=()):
    """
    THE PALETTE WHOSE RENDERED CELL BEST MATCHES THE LOCAL NEIGHBOURHOOD.

    NOT "the modal palette of the neighbours", which is what this function did
    first and which MEASURED BADLY: over 83 fields with a measurable seam it
    improved 34 and made 11 WORSE, `mds5_5` by 72 points and `sninn_2` by 96.
    Copying a neighbour's palette index onto a different cell's indices is not
    the same operation as copying its colour -- the two cells carry different
    index content, so the same table can render somewhere else entirely.

    So keep `_best_palette`'s method -- render every candidate and score it --
    and change only the TARGET. `_best_palette` aims at Cosmos's art, which is
    right for layer 1 and picks a neutral palette on a blended layer (see
    LAYER_SCOPED). This aims at what the neighbouring overlay tiles actually
    draw, which is the quantity the seam is made of.

    Returns None when the neighbourhood cannot be rendered, and the caller then
    falls back to `_best_palette` rather than inventing an answer.
    """
    same = in_range.get(bad_tile.layer)
    if not same or pal565 is None:
        return None
    dx, dy = bad_tile.dx, bad_tile.dy
    near = sorted(same, key=lambda t: (t.dx - dx) ** 2 + (t.dy - dy) ** 2)[:k]
    tgt, n = None, 0
    for t in near:
        m = _cell_mean(arrays, pal565, t.slot, t.sx, t.sy, t.pal)
        if m is None:
            continue
        tgt = m if tgt is None else tgt + m
        n += 1
    if not n:
        return None
    tgt = tgt / n
    score = {}
    for p in range(rows):
        m = _cell_mean(arrays, pal565, bad_tile.slot, bad_tile.sx,
                       bad_tile.sy, p, edge)
        if m is not None:
            score[p] = float(abs(m - tgt).mean())
    if not score:
        return None
    best = min(score, key=score.get)
    # See SET_TOL. An incumbent inside the tolerance wins, and the closest
    # incumbent wins among several.
    near = [p for p in incumbent
            if p in score and score[p] <= score[best] + SET_TOL]
    if near:
        return min(near, key=score.get)
    return best


def _seam_of(tiles, layer, assign, arrays, pal565, rows, band=48):
    """
    The rendered discontinuity across the edge of the vanilla 4:3 window, for
    one layer, under a given palette assignment.

    THIS IS THE ARTEFACT ITSELF. The defect FINDINGS-230 describes is a hard
    vertical line where the widescreen margin meets the original picture, so
    the quantity to minimise is the jump between the band just inside that edge
    and the band just outside it. Both are the same part of the scene under the
    same light, which is what makes the comparison fair -- unlike "margin mean
    vs interior mean", which conflates tint with content and scored this change
    backwards on the first attempt.

    `assign` maps (slot, sx, sy, layer) -> palette for the tiles being
    rewritten; every other tile keeps the palette it names.

    THE `t.pal >= rows` TEST IS LOad-BEARING AND ITS ABSENCE WAS A BUG.
    `fix_field` rewrites ONLY the tiles whose palette is out of range, but
    several tiles share one source cell and some of those name a palette that
    exists. Keying the override by cell alone therefore reassigned tiles the
    build never touches -- including in-range tiles in the INNER band, which
    dragged the inner mean toward the outer one and made the reference arm
    look 5x better than it is (`mds7plr1`: 7.26 against a true 35.21). The
    veto then reverted the field this whole finding is built on.
    """
    def _mean(sel):
        acc, n = None, 0
        for t in sel:
            p = (assign.get((t.slot, t.sx, t.sy, t.layer), t.pal)
                 if t.pal >= rows else t.pal)
            m = _cell_mean(arrays, pal565, t.slot, t.sx, t.sy, p)
            if m is None:
                continue
            acc = m if acc is None else acc + m
            n += 1
        return (acc / n) if n else None

    inner = [t for t in tiles if t.layer == layer
             and (-HALF_43 <= t.dx < -HALF_43 + band
                  or HALF_43 - band <= t.dx < HALF_43)]
    outer = [t for t in tiles if t.layer == layer
             and (-HALF_43 - band <= t.dx + 16 <= -HALF_43
                  or HALF_43 <= t.dx < HALF_43 + band)]
    a, b = _mean(inner), _mean(outer)
    if a is None or b is None:
        return None
    return float(abs(a - b).mean())


def fix_field(sec3, sec9, name='', log=None, art_for=None):
    """Return (sec9, stats). Rewrites out-of-range palette bytes in place."""
    st = {'tiles': 0, 'cells': 0, 'pals': Counter(), 'rows': 0}
    rows = palette_rows(sec3)
    st['rows'] = rows
    if rows <= 0:
        return sec9, st
    try:
        surv = DC.survey(sec9)
        pages = {p.slot: p for p in surv['pages']}
        tiles = list(MB.read_tiles(sec9, surv, pages))
    except Exception:                                          # noqa: BLE001
        return sec9, st

    bad = [t for t in tiles
           if pages.get(t.slot) is not None
           and pages[t.slot].depth == 1 and t.pal >= rows]
    # NO COVERAGE GATE. An earlier version skipped cells the mod does not
    # paint, to dodge the tan square -- but that leaves the tile naming a
    # palette that still does not exist, i.e. the console still reads past
    # the array. _best_palette solves the colour properly, so every
    # out-of-range tile is fixed and none are left behind.
    st['skipped_unpainted'] = 0
    if not bad:
        return sec9, st

    try:
        import field_bg_dense as _FD
        pal565, _npg, _cpp = _FD._pal_rgb(sec3)
    except Exception:                                          # noqa: BLE001
        pal565 = None
    arrays = {}
    for sl, pg in pages.items():
        if pg.depth == 1:
            try:
                import numpy as _np
                arrays[sl] = _np.frombuffer(pg.data, _np.uint8).reshape(256, 256)
            except Exception:                                  # noqa: BLE001
                pass
    buf = bytearray(sec9)
    cells = set()
    # CHOOSE PER CELL, BY RENDERING IT. FINDINGS-158 part 3.
    #
    # Palette 0 as a blanket answer caused the build 67 Wall Market
    # regression. A vanilla FILLER cell is entirely index 0, and marginart's
    # keep-0 rule protects index 0, so the art never lands -- the rendered
    # colour is decided ENTIRELY by entry 0 of whichever palette we name.
    # Palette 0's entry 0 in mrkt2 is (224,168,104): the tan square.
    #
    # So score the candidates the way the screen will: render THIS cell's
    # actual indices through each valid palette and take the one closest to
    # Cosmos's art. For an all-index-0 cell that reduces to "the palette
    # whose entry 0 matches the art", which is exactly the question. For a
    # normal cell it is the ordinary best-palette choice.
    #
    # KEYED BY (CELL, LAYER), NOT BY CELL. See LAYER_SCOPED. One source cell
    # can be drawn by a layer-1 tile and a layer-2 tile at once, and those two
    # now get different answers -- which the format allows, because the palette
    # byte lives on the TILE. Keying by cell alone would force the overlay and
    # the background to share one palette and silently undo half of this.
    in_range = {}
    incumbent = {}
    # See PALRANGE_LAYER_EXCLUDE. The name is the field's, lower-cased the way
    # `build.py` passes it.
    _scoped = LAYER_SCOPED and (name or '').lower() not in PALRANGE_LAYER_EXCLUDE
    if _scoped:
        for t in tiles:
            _pg = pages.get(t.slot)
            if _pg is None or _pg.depth != 1 or t.pal >= rows:
                continue
            # The palettes this CELL is already drawn through, over every
            # layer -- `field_bg_dense` keys its textures by (cell, palette)
            # and does not care which layer asked. See SET_TOL.
            incumbent.setdefault((t.slot, t.sx, t.sy), set()).add(t.pal)
            if t.layer in NEIGHBOUR_LAYERS:
                in_range.setdefault(t.layer, []).append(t)
    choice = {}
    st['by_neighbour'] = 0
    st['by_render'] = 0
    for t in bad:
        key = (t.slot, t.sx, t.sy, t.layer)
        if key in choice:
            continue
        _pg = pages.get(t.slot)
        _edge = 32 if (BIG_CELL and _pg is not None and _pg.size_flag) else 16
        new = None
        if _scoped and t.layer in NEIGHBOUR_LAYERS:
            new = _neighbour_palette(
                t, in_range, arrays, pal565, rows, edge=_edge,
                incumbent=sorted(incumbent.get((t.slot, t.sx, t.sy), ())))
        if new is None:
            new = (0 if pal565 is None else
                   _best_palette(arrays, pal565, art_for, rows,
                                 t.slot, t.sx, t.sy, _edge))
            st['by_render'] += 1
        else:
            st['by_neighbour'] += 1
        choice[key] = new
    # ---- ADMISSION RUNS THE FALSIFIER, PER LAYER.
    #
    # The neighbourhood rule is right in the general case and wrong in
    # particular fields, and no amount of argument settles which is which.
    # MEASURED over the 83 fields with a measurable seam: it improved 35 and
    # made 8 worse, `sninn_2` by 96 points. So do not argue -- render both
    # assignments, keep whichever leaves the smaller seam, and let a field
    # where the old rule was already better simply keep it.
    #
    # This cannot do worse than build 110 on the quantity it targets, which is
    # the whole point of deciding it here instead of in a comment.
    if _scoped and st['by_neighbour'] and pal565 is not None:
        _ref = {}
        for t in bad:
            k = (t.slot, t.sx, t.sy, t.layer)
            if t.layer in NEIGHBOUR_LAYERS and k not in _ref:
                _pg = pages.get(t.slot)
                _e = 32 if (BIG_CELL and _pg is not None and _pg.size_flag) else 16
                _ref[k] = _best_palette(arrays, pal565, art_for, rows,
                                        t.slot, t.sx, t.sy, _e)
        for layer in sorted({t.layer for t in bad}
                            & set(NEIGHBOUR_LAYERS)):
            mix = dict(choice)
            mix.update({k: v for k, v in _ref.items() if k[3] == layer})
            s_new = _seam_of(tiles, layer, choice, arrays, pal565, rows)
            s_ref = _seam_of(tiles, layer, mix, arrays, pal565, rows)
            if s_new is None or s_ref is None:
                continue
            if s_new > s_ref + 0.5:
                # COUNT WHAT IS ACTUALLY REVERTED, not every key on the layer.
                # `_ref` holds a reference answer for every out-of-range cell
                # on this layer, but only the ones the neighbourhood rule
                # actually decided were ever counted in `by_neighbour` -- the
                # rest fell through to `_best_palette` and are already there.
                # Subtracting the whole set drove the counter negative.
                _moved = sum(1 for k, v in _ref.items()
                             if k[3] == layer and choice.get(k) != v)
                choice.update({k: v for k, v in _ref.items()
                               if k[3] == layer})
                st['seam_reverted'] = st.get('seam_reverted', 0) + 1
                st['by_neighbour'] -= _moved
                st['by_render'] += _moved
    for t in bad:
        new = choice[(t.slot, t.sx, t.sy, t.layer)]
        st['pals'][t.pal] += 1
        st['tiles'] += 1
        cells.add((t.slot, t.sx, t.sy))
        buf[t.off + T_PAL] = new
    st['cells'] = len(cells)
    if log and st['tiles']:
        log('    %s: %d tile(s) named a palette >= %d' % (name, st['tiles'], rows))
    return bytes(buf), st


def summarise(total_tiles, total_cells, fields, pals):
    if not total_tiles:
        return ('  field background: palette range -- every tile names a palette '
                'that exists. (If this ever prints a non-zero count again, the '
                'console is reading past the end of section 3 -- FINDINGS-158.)')
    top = ', '.join('%d x%d' % (p, n) for p, n in sorted(pals.items())[:6])
    return ('  field background: PALETTE RANGE -- %s tile(s) across %s cell(s) in '
            '%s field(s) named a palette index at or past the end of section 3 '
            'and were repointed to the palette that renders each cell CLOSEST TO COSMOS\'S ART (chosen per cell, not a fixed index -- a fixed palette 0 caused the build 67 Wall Market tan squares). These come '
            'from COSMOS\'s own section 9, which was authored against the PC '
            'field and its larger palette table; FFNx never reads the byte '
            'because it replaces the page with the DDS, but this port applies '
            'it and the lookup runs off the end of the palette array -- the '
            'white speckle in mds5_3 and the black blobs in mds5_5. It is '
            'INVISIBLE to every offline renderer we own because they all decode '
            'with pal %% npg. Offending indices: %s. FINDINGS-158. '
            'SECOND EFFECT: ff7nx_marginart skipped these cells entirely '
            '("pal >= npg -> no_dds"), so they never received Cosmos\'s art '
            'either -- fixing the byte before the margin passes lets the '
            'upscaled art reach them.'
            % (f'{total_tiles:,}', f'{total_cells:,}', f'{fields:,}', top))


def apply_to_flevel(archive, payloads, encode=None, log=print, fields=None,
                    art=None):
    """
    Same contract as ff7nx_marginart.apply_to_flevel: a field already in
    `payloads` is taken from there, so this composes with the mod replacement
    passes instead of competing with them.

    MUST RUN BEFORE ff7nx_marginart. marginart skips any cell whose palette
    byte is >= npg ("no_dds"), so fixing the byte first is what lets Cosmos's
    art reach those cells at all.
    """
    import lgp

    st = {'read': 0, 'changed': 0, 'tiles': 0, 'cells': 0, 'fields': 0,
          'unpainted': 0, 'pals': Counter(), 'refused': []}
    encode = encode or (lambda raw: archive.encode_field(raw))
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        if fields and name not in fields:
            continue
        payload = payloads.get(name, entry.get('payload'))
        if not payload:
            continue
        try:
            raw = (lgp.lzs_decompress(payload[4:]) if name in payloads
                   else archive.decompressed(entry))
            secs = lgp.split_sections(raw)
            _af = art.open(name) if art is not None else None
            new9, s = fix_field(secs[3], secs[8], name, art_for=_af)
            st['read'] += 1
            st['unpainted'] += s.get('skipped_unpainted', 0)
            if not s['tiles']:
                continue
            secs[8] = new9
            payloads[name] = encode(lgp.join_sections(secs))
            st['changed'] += 1
            st['fields'] += 1
            st['tiles'] += s['tiles']
            st['cells'] += s['cells']
            st['pals'].update(s['pals'])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, '%s: %s'
                                  % (type(exc).__name__, str(exc)[:60])))
    return st
