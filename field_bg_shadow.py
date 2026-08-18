"""
field_bg_shadow.py -- Cosmos's 512px art, carried to the depth-1 lift.

BUILD 109. HANDOFF-224.

WHAT THIS IS FOR
================
Build 108 made every depth-1 page a 512px container by REPLICATING each
index four times. The picture did not change and that was the pass
condition. This module is what puts real detail in those pages: at the
moment `ff7nx_marginart` quantises Cosmos's art down to a 256px cell, it
also quantises the UN-REDUCED 512px block against the SAME palette and
hands the result here. `field_bg_native.lift_depth1` then writes that
block instead of replicating.

WHY THIS IS CONTENT-ADDRESSED AND NOT PROVENANCE-TRACKED
========================================================
HANDOFF-224 s3.3 lists four places a cell is copied verbatim between
`marginart` and the lift -- `field_bg_compact._cell_bytes` and its two
callers, `dense_repack`'s relocation, and `field_bg_pagecap`'s split --
and asks each of them to mirror the copy into a parallel 512px buffer.
That works, and it has one failure mode that cannot be designed away: a
hook that is forgotten, or added later, silently drops back to
replication and nothing says so.

So the shadow is keyed by the CONTENT of the 16x16 index block it
shadows, not by where that block lives. Every one of those four
operations copies a cell's bytes UNCHANGED -- that is what makes them
safe to do at all -- so a relocated, merged or duplicated cell hashes to
the same key and finds the same shadow with no hook at any of them. The
compactor merging two byte-identical cells is the case that proves the
key is the right one: the cells were merged BECAUSE they are the same
picture, so they want the same shadow.

Three properties follow, and they are the reason to prefer this:

  * A PASS THAT REWRITES A CELL LOSES ITS SHADOW AUTOMATICALLY. The
    second `ff7nx_blackcell` call, `ff7nx_marginblack`, `clamp_palettes`
    -- anything that changes an index changes the key, the lookup misses,
    and that block falls back to build 108's replication. No pass has to
    know this module exists.

  * A DISCARDED PAYLOAD CANNOT LEAVE A STALE SHADOW. If `fill_field`'s
    result is thrown away for any reason the page still holds vanilla
    indices, which never hash to a key recorded from Cosmos-filled ones.
  * THE FALLBACK IS PER 16x16 BLOCK, not per page and not per field, so
    a miss costs 1/256th of a page rather than the page.

THE MASK IS PRESERVED EXACTLY, AND THAT IS THE LOAD-BEARING PART
================================================================
`marginart` does not write `quantise`'s output. It writes that output
after five guards have overridden individual texels: `keep0` forces the
transparency key, `KEEP_BLACK_PIXELS` restores the vanilla silhouette,
`HONOUR_MOD_ALPHA` restores vanilla where the mod paints nothing, and so
on. Every one of those decisions is about WHAT IS THERE, not about
resolution, and re-deciding them at 512 would be a second change riding
in the same build.

So `record()` takes both the pristine `quantise` output and the final
written block, and any texel where they differ is REPLICATED into the
shadow rather than re-quantised. A cut-out texel becomes four cut-out
texels; a silhouette pixel becomes four silhouette pixels. Decimating
the shadow by taking the top-left of each 2x2 -- which is the exact
inverse of build 108's replication -- returns those texels bit-for-bit.
Only the texels that came straight out of the quantiser carry new
detail, which is precisely the set this build is allowed to sharpen.

THE DRIFT GATE
==============
Falsifier 2 of HANDOFF-224, applied inline per 16x16 block rather than
offline afterwards. Quantising at 512 and then decimating is NOT the
same as box-filtering to 256 and then quantising, so the two are allowed
to differ -- but not by much, and a block that differs a lot is the
signature of the failure that shipped `mds6_2` as a solid yellow square:
a wrong page, a wrong palette, or art that does not belong to this cell.
Such a block is refused and falls back to replication.

STORAGE
=======
One zlib-compressed file per field in a temporary directory, holding
16-byte keys against 1024-byte blocks. ~350k blocks archive-wide is
~350 MB uncompressed, which is more than a build should hold in RAM, and
the access pattern is write-all-of-one-field-then-read-all-of-one-field,
which is exactly what a per-field file wants.

DISARMED BY DEFAULT. `arm()` is called by `build.py` only when the
depth-1 lift is actually going to run at 512; under a 256px build every
entry point here is a no-op and the build is bit-for-bit build 107.
"""

import atexit
import hashlib
import os
import shutil
import struct
import tempfile
import zlib

import numpy as np


# ---------------------------------------------------------------- knobs
# Mean per-channel rendered-colour distance, 0..255, between the shadow
# DECIMATED back to 256 and the 256px block it shadows. Above this the
# block is refused and the lift replicates instead.
#
# It is compared against the same scale as `ff7nx_marginart.MAX_QUANT_ERR`
# (60), which is what that pass allows between its own output and the art
# it was quantising. This is a DIFFERENT quantity -- both sides here have
# already passed that gate -- so it is deliberately tighter. Measured
# distribution is reported by `stats()` so the number can be set from data
# rather than from taste.
MAX_SHADOW_DRIFT = 24.0

# The block the key is computed over. 16 and not 32, even though a
# `size_flag` page has 32-unit cells: a uniform grid means the lift walks
# every depth-1 page the same way and does not have to recover the cell
# size, and it makes a relocated 32-unit cell resolve as four 16-unit
# lookups rather than missing entirely.
BLOCK = 16

# Env override, for an A/B against build 108 without rebuilding anything
# else. Unset or "1"/"on" -> the shadow is used; "0"/"off" -> every block
# falls back to replication, which is build 108 exactly.
ENV = 'SEVENTH_NX_FIELD_BG_D1_ART'


def enabled(env=None):
    raw = str(env if env is not None else os.environ.get(ENV, '1')).strip().lower()
    return raw not in ('0', 'off', 'no', 'none', 'false')


# BUILD 116. Where the mod paints NOTHING, the 512px texel falls back to
# build 108's replication instead of quantising the black the providers
# zeroed it to. See `_record`. SEVENTH_NX_NO_D1_HOLEFILL=1 -> build 115.
HOLE_REPLICATE = os.environ.get('SEVENTH_NX_NO_D1_HOLEFILL') != '1'


# ---------------------------------------------------------------- state
_DIR = None            # temp directory, created on first arm()
_DST_PX = 0            # the size the lift will produce; 0 means disarmed
_SRC_PX = 256
_CUR = None            # (field, {key: block or None}) being accumulated
_STAT = {
    'fields': 0, 'blocks': 0, 'poisoned': 0, 'drift_refused': 0,
    'no_oversample': 0, 'drift_sum': 0.0, 'drift_n': 0, 'drift_max': 0.0,
    'lift_fields': 0, 'lift_hit': 0, 'lift_miss': 0, 'lift_pages': 0,
    'drift_hist': [0] * 16,
    # tier 2
    'maps': 0, 'map_refused': 0, 'map_agree_sum': 0.0, 'map_agree_n': 0,
    'map_hist': [0] * 10, 'lift_hit_map': 0,
    # build 116
    'hole_cells': 0, 'hole_texels': 0,
}


def _cleanup():
    global _DIR
    if _DIR and os.path.isdir(_DIR):
        shutil.rmtree(_DIR, ignore_errors=True)
    _DIR = None


def arm(dst_px, src_px=256):
    """
    Turn the shadow on for a build that will lift depth-1 to `dst_px`.

    Only the exact 2x case is supported. Anything else disarms rather than
    raising: the shadow is an improvement, never a correctness requirement,
    and a build at an unsupported ratio must still produce build 108's
    replication rather than fail.
    """
    global _DIR, _DST_PX, _SRC_PX
    disarm()
    if not enabled() or dst_px != src_px * 2:
        return False
    _DST_PX, _SRC_PX = int(dst_px), int(src_px)
    _DIR = tempfile.mkdtemp(prefix='ff7nx_d1shadow_')
    atexit.register(_cleanup)
    return True


def disarm():
    global _DST_PX, _CUR
    _DST_PX = 0
    _CUR = None
    _cleanup()


def active():
    return _DST_PX != 0


def stats():
    out = dict(_STAT)
    out['drift_mean'] = (out['drift_sum'] / out['drift_n']
                         if out['drift_n'] else 0.0)
    out['map_agree'] = (out['map_agree_sum'] / out['map_agree_n']
                        if out['map_agree_n'] else 0.0)
    return out


def _path(field):
    return os.path.join(_DIR, hashlib.blake2b(
        field.encode('utf-8', 'replace'), digest_size=16).hexdigest())


def _key(block):
    """16-byte digest of a BLOCK x BLOCK uint8 index array."""
    return hashlib.blake2b(np.ascontiguousarray(block).tobytes(),
                           digest_size=16).digest()


# ------------------------------------------------------------- recording
def _big(src, edge, k):
    """
    The Cosmos block for this cell at 2x the 256-unit cell size.

    `src` is (edge*k, edge*k, 4) RGBA straight out of the DDS, where `k` is
    the oversample factor the provider decoded at. `marginart` box-filters
    it all the way down to (edge, edge); this stops half way.

    k == 1 means the provider has no more resolution than the page already
    has, so there is no shadow to build and the cell keeps replication.
    """
    if k < 2 or k % 2:
        return None
    h = k // 2
    rgb = np.ascontiguousarray(src[..., :3])
    if h == 1:
        return rgb
    return (rgb.reshape(edge * 2, h, edge * 2, h, 3)
            .mean(axis=(1, 3)))


def _big_cover(src, edge, k):
    """
    `_big`'s alpha: how much of each 512px texel the mod actually covers.

    Same reduction as `_big`, same shape, 0..255. `marginart` computes the
    identical quantity at 256 and calls it `cover`.
    """
    if k < 2 or k % 2:
        return None
    h = k // 2
    a = np.ascontiguousarray(src[..., 3])
    if h == 1:
        return a.astype(np.float32)
    return a.reshape(edge * 2, h, edge * 2, h).mean(axis=(1, 3))


# THE HOLE THRESHOLD. `ff7nx_marginart` calls the same quantity `cover` and
# tests it two ways: `cover >= 128` decides what `_extend_into_gap` may
# rewrite, and `cover > 0` decides whether the mod is SAYING ANYTHING here.
# This is the second question, and 128 is the midpoint of a channel that is,
# MEASURED on `fship_2` at k == 2, entirely binary: 295,240 texels at alpha 0
# and 3,303,096 at alpha 255, nothing in between. The constant is therefore
# not load-bearing on anything measured; it is written as 128 so that a
# provider which DOES decode partial alpha resolves it the same way `_cov`
# resolves it one resolution down.
HOLE_ALPHA = 128


def record(field, idx, idx_q, src, k, prgb, quantise, edge):
    """
    Register the 512px shadow of one cell `marginart` has just written.

    `idx`    -- the final index block, exactly as it goes into the page.
    `idx_q`  -- `quantise`'s pristine output for the same cell, BEFORE the
                mask guards overrode anything. Texels where the two differ
                are replicated rather than re-quantised (see the module
                docstring); pass `idx` for both to replicate everything,
                which is legal and simply records nothing useful.
    `src`    -- the (edge*k, edge*k, 4) RGBA block from the provider.
    `prgb`   -- the (256, 3) palette the cell is DRAWN through, which is
                `eff_pal`'s and not the shipped image's.

    Never raises. A cell that cannot be shadowed is a cell that keeps
    build 108's behaviour, which is the whole safety argument.
    """
    if not _DST_PX:
        return 0
    try:
        return _record(field, idx, idx_q, src, k, prgb, quantise, edge)
    except Exception:                                          # noqa: BLE001
        return 0


def _record(field, idx, idx_q, src, k, prgb, quantise, edge):
    global _CUR
    if _CUR is None or _CUR[0] != field:
        flush()
        _CUR = (field, {})
    table = _CUR[1]

    big = _big(src, edge, k)
    if big is None:
        _STAT['no_oversample'] += 1
        return 0
    if big.shape[:2] != (edge * 2, edge * 2):
        return 0

    s = quantise(big.astype(np.uint8), prgb)
    rep = np.repeat(np.repeat(idx, 2, 0), 2, 1)
    # EVERY TEXEL THE 256px PIPELINE DECIDED BY HAND IS REPLICATED, NOT
    # RE-QUANTISED. `idx != idx_q` is exactly the set of texels a guard
    # overrode: the colour key, the vanilla silhouette, the uncovered
    # fallback, the palette remap. See the module docstring.
    forced = np.repeat(np.repeat(idx != idx_q, 2, 0), 2, 1)

    # ---- AND SO IS EVERY TEXEL THE MOD DOES NOT PAINT. BUILD 116.
    #
    # THE HOLE IS NOT BLACK, IT IS ABSENT, AND THIS PATH QUANTISED IT AS
    # BLACK.
    #
    # The providers ZERO RGB wherever alpha is 0. `ff7nx_marginart` knows
    # that and never quantises a hole: `_extend_into_gap` grows the covered
    # art over every uncovered texel of `small` BEFORE `quantise` sees it,
    # and FINDINGS-138 records the three builds it took to get there --
    # black squares, then dilation artefacts, then the alpha < 128 fringe.
    # `MAX_QUANT_ERR` cannot catch any of it because black is an excellent
    # match for black (measured error 1.4 to 4.5 out of 255).
    #
    # `_record` was handed the SAME `src` and quantised it at 512 with the
    # holes still (0, 0, 0). `forced` does not cover for it, and that is the
    # subtle part: `forced` is `idx != idx_q`, exactly the texels a 256px
    # GUARD overrode, and a unit rescued by `_extend_into_gap` was rescued
    # by the SOURCE being rewritten before `quantise` ever ran -- so
    # `idx == idx_q` there, `forced` is False, and the shadow's raw-black
    # quantisation is what ships at 512.
    #
    # A WHOLLY uncovered unit was already safe: at 256 `HONOUR_MOD_ALPHA`
    # writes vanilla's index over it, which differs from `idx_q`, which
    # makes it `forced`. Only a unit the mod cuts PARTIALLY -- some of its
    # four 512px texels painted and some not -- reaches this line, which is
    # precisely the silhouette boundary. So the 512 page grew a black fringe
    # along every edge where the mod's art stops inside a unit.
    #
    # REPLICATION, NOT DILATION, AND THAT WAS MEASURED RATHER THAN ASSUMED.
    # The obvious repair is to mirror `_extend_into_gap` at 512 and grow the
    # art over the holes. It works, and it is worse on every axis, because
    # at 512 the dilation source is a raw texel while at 256 it is a box
    # filter that has already averaged holes in (FINDINGS-236) -- so it
    # paints a hole BRIGHTER than the unit it sits in and trades a black
    # fringe for a bright one. Over `fship_2`, `mds7plr1` and `nmkin_1`,
    # counting 512px texels that render near-black where their own 256px
    # parent does not, and the mirror population that render bright where
    # the parent is near-black, through the palette the cell is DRAWN with:
    #
    #                  black_new   bright_new   sub-unit drift
    #   115 (black)       41,252       31,352      3.559
    #   dilate            39,250       33,804      3.559   (+2,452 bright)
    #   replicate         39,246       31,352      3.537   (+0     bright)
    #
    # Replication takes the same black away, introduces no bright fringe at
    # all, and lowers the drift. It is also the stronger claim: no index
    # appears at a hole texel that its own unit was not already drawing, so
    # the cell cannot acquire a colour from anywhere.
    #
    # THIS IS NOT FINDINGS-237's MASK RELAXATION AND MUST NOT BE READ AS
    # ONE. Nothing here decides transparency. `quantise` still never emits
    # index 0, every index-0 texel is still `forced`, and the lifted mask is
    # still the EXACT 2x repeat of the 256px one -- `_kshadow` falsifier 2
    # is untouched and still has to pass. This changes only the COLOUR at
    # texels the mod does not paint, which is the half of the split that was
    # never pinned. FINDINGS-237 is still open and still needs its own
    # build.
    if HOLE_REPLICATE:
        cov = _big_cover(src, edge, k)
        if cov is not None and cov.shape == big.shape[:2]:
            hole = cov < HOLE_ALPHA
            n = int((hole & ~forced).sum())
            if n:
                forced = forced | hole
                _STAT['hole_texels'] += n
                _STAT['hole_cells'] += 1

    s = np.where(forced, rep, s).astype(np.uint8)

    # DECIMATION IS THE EXACT INVERSE OF REPLICATION -- top-left of each
    # 2x2 -- so this compares the shadow against the page it shadows at a
    # COMMON RESOLUTION. HANDOFF-224 falsifier 5, third time of asking.
    back = s[::2, ::2]
    n = 0
    for by in range(0, edge, BLOCK):
        for bx in range(0, edge, BLOCK):
            cur = idx[by:by + BLOCK, bx:bx + BLOCK]
            sub = s[by * 2:(by + BLOCK) * 2, bx * 2:(bx + BLOCK) * 2]
            d = float(np.abs(
                prgb[back[by:by + BLOCK, bx:bx + BLOCK]].astype(np.int16)
                - prgb[cur].astype(np.int16)).mean())
            _STAT['drift_sum'] += d
            _STAT['drift_n'] += 1
            _STAT['drift_max'] = max(_STAT['drift_max'], d)
            _STAT['drift_hist'][min(15, int(d / 4.0))] += 1
            if d > MAX_SHADOW_DRIFT:
                _STAT['drift_refused'] += 1
                continue
            key = _key(cur)
            # TAGGED, so tier 1 and tier 2 can share one table and one file.
            # 'S' is a literal 512px index block, 'M' a resample map; both
            # are BLOCK*2 squared bytes, so every value is the same length.
            blob = b'S' + np.ascontiguousarray(sub).tobytes()
            if key not in table:
                table[key] = blob
                _STAT['blocks'] += 1
                n += 1
                continue
            have = table[key]
            if have is None or have != blob:
                # THE SAME 256px BLOCK WANTS TWO DIFFERENT PICTURES.
                # Refuse both. This is the only way content addressing can
                # be wrong, and it costs an improvement rather than a page.
                if have is not None:
                    _STAT['blocks'] -= 1
                    _STAT['poisoned'] += 1
                table[key] = None
    return n


# ------------------------------------------------- tier 2: the resample map
#
# THE MULTI-PALETTE VETO IS ABOUT COLOUR AND THIS IS ABOUT RESOLUTION, SO IT
# DOES NOT APPLY HERE. That distinction is the whole of tier 2.
#
# `ff7nx_marginart.fillable_cells` refuses a cell drawn through more than one
# palette, and HANDOFF-224 s2.1 is right that the veto stays: a depth-1 page
# is ONE index array, Cosmos ships a DIFFERENT image per palette, so there is
# no single set of indices that is correct under all of them. Writing
# palette 0's art into a cell a palette-15 tile also draws is how mrkt3 and
# bwhlin came back as coloured static.
#
# MEASURED, and it is why tier 1's coverage looked so bad where it mattered:
# `ancnt1` cell (0, 0, 0) is drawn by 458 tiles at palettes 0, 11, 14 and 15.
# The most-drawn cells are the most likely to be shared, so the veto lands
# hardest on exactly the cells that fill the screen. Tier 1 alone left that
# field at 33% of blocks and 0% of TILES.
#
# So tier 2 never writes an index. It writes a RESAMPLE MAP: for each of the
# 4 output texels of a source texel, which NEIGHBOURING SOURCE TEXEL to copy,
# chosen by asking Cosmos's own 512px art which neighbour that texel most
# looks like. Two properties follow, and both hold under every palette
# simultaneously because neither mentions one:
#
#   * NO INDEX APPEARS THAT WAS NOT ALREADY IN THE CELL. Every output texel
#     is a copy of a source texel from the same 16x16 block, so whatever
#     table the cell is drawn through, every colour drawn was already being
#     drawn there. A shared cell cannot acquire a colour under palette 15
#     that palette 15 was not already showing.
#   * THE COLOUR KEY CANNOT MOVE FARTHER THAN ONE TEXEL. Index 0 is copied
#     like any other index, so transparency is resampled with the art rather
#     than re-decided.
#
# And the top-left texel of each 2x2 is PINNED to the source texel, so
# decimating the result -- top-left of each 2x2, the exact inverse of build
# 108's replication -- returns the original page bit-for-bit. `_k512gate`'s
# reversibility check therefore still passes unchanged, which is a stronger
# guarantee than tier 1 can offer.
#
# What it buys is edge direction. Replication turns one texel into a 2x2
# square; this turns it into whichever of the 9 nearby texels the real art
# says belongs there, so a diagonal edge stops being a staircase of 2x2
# blocks and becomes a staircase of 1x1 ones. It is not the same as tier 1's
# genuine 512px detail -- no new colour is created -- but it is the whole of
# the improvement that is available without touching colour.
_DELTA = [(di, dj) for di in (-1, 0, 1) for dj in (-1, 0, 1)]

# IS COSMOS'S ART ACTUALLY A GUIDE TO THIS CELL, OR A DIFFERENT PICTURE?
#
# The map is only as good as the correspondence between the indices in the
# cell and the art used to steer them. Where a cell holds vanilla content the
# mod does not match -- a borrowed page, a cell the fill refused, a field
# Cosmos redrew -- a map built from that guide would sharpen along the WRONG
# edges. That is the tier-2 shape of the failure that shipped mds6_2 yellow,
# and it needs a gate.
#
# THE GATE IS A HELD-OUT PREDICTION, WHICH IS THE ONLY HONEST KIND AVAILABLE
# WITHOUT A PALETTE. For the top-left texel of each 2x2 the answer is already
# known: it must be the source texel itself, which is why `_build_map` pins
# it. So run the art-driven argmin on those texels ANYWAY, WITHOUT the pin,
# and ask how often it independently picks the centre. A guide aligned with
# the cell picks the centre nearly always; a guide that is a different
# picture picks a neighbour at chance.
#
# This is a real test rather than a plausible-looking statistic: it scores
# the exact mechanism the map uses, against ground truth the map did not see.
#
# FIRST ATTEMPT, RECORDED BECAUSE IT LOOKED REASONABLE AND WAS NOT. It
# compared "these two texels share an index" against "these two texels are
# close in the art" over adjacent pairs and required 80% agreement. Both
# sides are dominated by their base rates -- adjacent texels in a 256-entry
# palette rarely share an index, adjacent art colours usually are close -- so
# the measure sat at 0.49 archive-wide, which is chance, and it rejected
# 1,417 of 1,549 blocks on `ancnt1` for no reason connected to correctness.
MAP_MIN_PIN = 0.75

# ...AND THE HELD-OUT TEST IS NOT VALID EITHER, SO TIER 2 DOES NOT SHIP.
#
# The pin test compares `big[2p]` -- one quarter of a source texel at full
# resolution -- against `small[p]`, which is the MEAN over that texel's whole
# footprint. The centre is therefore not favoured by construction: a
# neighbour's mean can easily sit closer to a corner of the centre texel than
# the centre's own mean does. MEASURED over `ancnt1`, `anfrst_1` and
# `ancnt3`, 10,179 blocks: pin score 0.204 / 0.210 / 0.206 against a 1-in-9
# chance floor of 0.111. That is barely above chance, and it is a statement
# about the test rather than about the guide.
#
# So there are now two rejected gates and no accepted one, which means tier 2
# has no way to tell a guide that matches the cell from one that does not.
# The tier-1 shadow has such a test -- `MAX_SHADOW_DRIFT`, which works
# because both sides can be rendered through a known palette -- and tier 2's
# whole premise is that no palette is known. That may not be fixable in this
# form.
#
# The code is kept, off, because the measurements are worth preserving and
# because the IDEA is still the only route to sharpening a multi-palette
# cell. Set SEVENTH_NX_FIELD_BG_D1_MAP=1 to A/B it. It is not in build 109,
# and it must not be switched on for a ship build until it has a gate that
# has been shown to separate a matching guide from a mismatched one.
MAP_ENV = 'SEVENTH_NX_FIELD_BG_D1_MAP'


def map_enabled(env=None):
    raw = str(env if env is not None
              else os.environ.get(MAP_ENV, '0')).strip().lower()
    return raw not in ('0', 'off', 'no', 'none', 'false', '')


def _pin_score(small, big, edge):
    """
    Fraction of top-left texels where the unpinned argmin picks the centre.

    See MAP_MIN_PIN. Returns 0.0 rather than raising on a degenerate cell.
    """
    p = np.arange(0, edge * 2, 2) // 2          # the pinned rows/cols only
    b16 = big[0::2, 0::2].astype(np.int16)
    bestd = None
    best = None
    for k, (di, dj) in enumerate(_DELTA):
        py = np.clip(p + di, 0, edge - 1)
        px = np.clip(p + dj, 0, edge - 1)
        d = np.abs(small[py[:, None], px[None, :]].astype(np.int16)
                   - b16).sum(-1)
        if bestd is None:
            bestd, best = d, np.full(d.shape, k, np.uint8)
            continue
        m = d < bestd
        bestd = np.where(m, d, bestd)
        best = np.where(m, np.uint8(k), best)
    return float((best == 4).mean())


def _build_map(small, big, edge):
    """(2*edge, 2*edge) uint8, each entry an index into `_DELTA`."""
    p = np.arange(edge * 2) // 2
    best = np.full((edge * 2, edge * 2), 4, np.uint8)
    bestd = None
    b16 = big.astype(np.int16)
    for k, (di, dj) in enumerate(_DELTA):
        py = np.clip(p + di, 0, edge - 1)
        px = np.clip(p + dj, 0, edge - 1)
        d = np.abs(small[py[:, None], px[None, :]].astype(np.int16)
                   - b16).sum(-1)
        if bestd is None:
            bestd, best[:] = d, k
            continue
        m = d < bestd
        bestd = np.where(m, d, bestd)
        best = np.where(m, np.uint8(k), best)
    # PINNED, and this is what keeps the lift reversible. See the note above.
    best[0::2, 0::2] = 4
    return best


def _apply_map(cur, mp, edge):
    p = np.arange(edge * 2) // 2
    di = (mp // 3).astype(np.int16) - 1
    dj = (mp % 3).astype(np.int16) - 1
    py = np.clip(p[:, None] + di, 0, edge - 1)
    px = np.clip(p[None, :] + dj, 0, edge - 1)
    return cur[py, px]


def record_map(field, cur, src, k, edge):
    """
    Register the tier-2 resample map for one depth-1 cell.

    `cur` is the cell's CURRENT 256px indices -- whatever they are, vanilla
    or filled -- and `src` the provider's RGBA block for it. No palette is
    involved anywhere, which is the point.

    Tier 1 wins where both exist: a real 512px quantisation carries detail a
    resample cannot invent. This only ever fills in behind it.
    """
    if not _DST_PX or not map_enabled():
        return 0
    try:
        return _record_map(field, cur, src, k, edge)
    except Exception:                                          # noqa: BLE001
        return 0


def _record_map(field, cur, src, k, edge):
    global _CUR
    if _CUR is None or _CUR[0] != field:
        flush()
        _CUR = (field, {})
    table = _CUR[1]
    big = _big(src, edge, k)
    if big is None or big.shape[:2] != (edge * 2, edge * 2):
        return 0
    small = (np.ascontiguousarray(src[..., :3])
             .reshape(edge, k, edge, k, 3).mean(axis=(1, 3)))
    n = 0
    for by in range(0, edge, BLOCK):
        for bx in range(0, edge, BLOCK):
            c = cur[by:by + BLOCK, bx:bx + BLOCK]
            s = small[by:by + BLOCK, bx:bx + BLOCK]
            b = big[by * 2:(by + BLOCK) * 2, bx * 2:(bx + BLOCK) * 2]
            if len(np.unique(c)) < 2:
                # A FLAT BLOCK HAS NOTHING TO RESAMPLE. Every candidate is
                # the same index, so the map is the identity and storing it
                # would only add a way to be wrong.
                continue
            a = _pin_score(s, b, BLOCK)
            _STAT['map_agree_sum'] += a
            _STAT['map_agree_n'] += 1
            _STAT['map_hist'][min(9, int(a * 10))] += 1
            if a < MAP_MIN_PIN:
                _STAT['map_refused'] += 1
                continue
            key = _key(c)
            if key in table:
                # Tier 1 already owns it, or another cell claimed it. Either
                # way do not overwrite and do not poison -- a map and a
                # shadow of the same block are both correct.
                continue
            table[key] = b'M' + _build_map(s, b, BLOCK).tobytes()
            _STAT['maps'] += 1
            n += 1
    return n


def flush():
    """Write the field being accumulated to disk and drop it from memory."""
    global _CUR
    if _CUR is None:
        return
    field, table = _CUR
    _CUR = None
    if not _DIR:
        return
    items = [(k, v) for k, v in table.items() if v is not None]
    if not items:
        return
    keys = b''.join(k for k, _ in items)
    blocks = b''.join(v for _, v in items)
    body = zlib.compress(struct.pack('<II', len(items), len(items[0][1]))
                         + keys + blocks, 1)
    with open(_path(field), 'wb') as fh:
        fh.write(body)
    _STAT['fields'] += 1


def _load(field):
    if not _DIR:
        return None
    p = _path(field)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, 'rb') as fh:
            raw = zlib.decompress(fh.read())
    except Exception:                                          # noqa: BLE001
        return None
    n, blen = struct.unpack_from('<II', raw, 0)
    off = 8
    keys = raw[off:off + n * 16]
    off += n * 16
    blocks = raw[off:off + n * blen]
    return {keys[i * 16:(i + 1) * 16]: blocks[i * blen:(i + 1) * blen]
            for i in range(n)}


# ------------------------------------------------------------- the lift
def lift_art(field):
    """
    The `art` callable `field_bg_native.lift_depth1` takes, or None.

    Signature is `f(page, dst_px) -> bytes | None`, where `page` is the
    FINAL `field_bg_native.Page` -- after the repack, the compactor and
    the page cap have all had it -- and the return is a complete
    `dst_px * dst_px` index page.

    Composition is per BLOCK: the page starts as build 108's 2x
    replication and every 16x16 block with a recorded shadow overwrites
    its 32x32 quadrant. So a page with no shadows at all returns bytes
    identical to `resize_depth1`'s, and a page with some returns the rest
    of build 108 around them.
    """
    if not _DST_PX:
        return None
    # The last field `record()` saw is still in memory -- the flush is
    # lazy and fires when the field CHANGES, so nothing has closed it.
    # Idempotent, and a no-op on every call after the first.
    flush()
    table = _load(field)
    if not table:
        return None
    _STAT['lift_fields'] += 1

    def art(page, dst):
        if dst != _DST_PX or page.px != _SRC_PX:
            return None
        a = np.frombuffer(page.data, np.uint8, count=_SRC_PX * _SRC_PX)
        a = a.reshape(_SRC_PX, _SRC_PX)
        out = np.repeat(np.repeat(a, 2, 0), 2, 1)
        hit = hitm = 0
        for by in range(0, _SRC_PX, BLOCK):
            for bx in range(0, _SRC_PX, BLOCK):
                cur = a[by:by + BLOCK, bx:bx + BLOCK]
                rec = table.get(_key(cur))
                if rec is None:
                    _STAT['lift_miss'] += 1
                    continue
                payload = np.frombuffer(rec, np.uint8, offset=1).reshape(
                    BLOCK * 2, BLOCK * 2)
                if rec[:1] == b'M':
                    payload = _apply_map(cur, payload, BLOCK)
                    hitm += 1
                else:
                    hit += 1
                out[by * 2:(by + BLOCK) * 2,
                    bx * 2:(bx + BLOCK) * 2] = payload
        _STAT['lift_hit'] += hit
        _STAT['lift_hit_map'] += hitm
        _STAT['lift_pages'] += 1
        return out.tobytes()

    return art


def summarise():
    """One line for the build log, or '' if the shadow never ran."""
    s = stats()
    if not s['lift_pages'] and not s['blocks']:
        return ''
    tot = s['lift_hit'] + s['lift_hit_map'] + s['lift_miss']
    pct = (100.0 * (s['lift_hit'] + s['lift_hit_map']) / tot) if tot else 0.0
    return ('  depth-1 ART: of %s 16x16 block(s) on %s paletted page(s) in %s '
            'field(s), %s carry Cosmos art quantised at 512px and %s are '
            'art-directed resamples of the indices already there; %s still '
            'replicate as in build 108. Recorded %s block(s) (%s refused, '
            'drift mean %.2f max %.2f of 255, %s ambiguous) and %s map(s) '
            '(%s refused, agreement mean %.2f). %s texel(s) in %s cell(s) '
            'sit where the mod paints nothing and replicate their own unit '
            'rather than quantise the black the decoder left there.'
            '  [%.1f%% covered]'
            % (f'{tot:,}', f"{s['lift_pages']:,}", f"{s['lift_fields']:,}",
               f"{s['lift_hit']:,}", f"{s['lift_hit_map']:,}",
               f"{s['lift_miss']:,}", f"{s['blocks']:,}",
               f"{s['drift_refused']:,}", s['drift_mean'], s['drift_max'],
               f"{s['poisoned']:,}", f"{s['maps']:,}",
               f"{s['map_refused']:,}", s['map_agree'],
               f"{s['hole_texels']:,}", f"{s['hole_cells']:,}", pct))
