#!/usr/bin/env python3
"""
ff7nx_ddstex.py -- rebuild a native MULTI-PALETTE TEX from an FFNx DDS set.

WHAT THIS IS FOR
================
FFNx mods ship paletted game textures as one DDS per palette:

    magic/alex1_00.dds   alex1.tex rendered through palette 0
    magic/alex1_01.dds   the same bitmap through palette 1

The Switch port has no external-texture loader, so those files have to become
a real TEX again. `tex.convert_for_battle` cannot do it: it takes ONE
truecolor TEX and always emits 1 palette x 256 colours, which is right for an
enemy skin and wrong for a spell -- `magic.lgp` is 1,013 of 1,109 textures at
16 colours with up to 48 palettes, and the palette IS the animation.

WHY THE INDEX BITMAP CANNOT BE BUILT PER PALETTE
================================================
A paletted TEX is ONE index bitmap plus N palettes. The N DDS files are that
one bitmap drawn through N different colour tables. Quantising each DDS
independently would produce N unrelated bitmaps and there is only one slot for
them; whichever you kept, N-1 palettes would be pointing at the wrong pixels.

So the fit is joint, and deliberately in this order:

  1. the palette image with the most colour information decides the SHAPE --
     median-cut it once into the available entries (palette 0 wins ties);
  2. every other palette is then derived from that shape -- entry k of
     palette p is the mean of DDS p over exactly the pixels that got index k.

Step 2 is what keeps palette cycling meaningful: the same texel is entry k in
every palette, which is the relationship the original file has.

WHAT IS COPIED FROM VANILLA AND NEVER CHOSEN
============================================
The converter starts from vanilla's structure and changes only the fields
required by the selected output:

  * the 0xEC header is the vanilla header used as a template. Width, height,
    pitch and the three palette-count fields move when their corresponding
    data moves; colour key, bit depth, index format and unnamed fields stay
    exactly what the port already reads;
  * `num_palettes` is always preserved. The colour count may widen from 16 to
    256 while keeping every palette, because the index format is already 8-bit
    and this avoids crushing detailed SYW art into fifteen visible colours;
  * palette alpha normally follows vanilla. When a DDS carries a measured
    silhouette, its alpha supplies that silhouette instead of enlarging a
    coarse native mask;
  * WHICH indices are transparent. Entries whose vanilla alpha is 0 are
    reserved: transparent source pixels are mapped to the first of them and
    the quantiser is never allowed to spend them on colour.

Every palette ends on one shared whole-number physical canvas because the TEX
has only one index bitmap. Source palette images may have different pixel
resolutions when their normalized X:Y canvas ratio agrees; this preserves the
higher-resolution frames instead of rejecting the whole animation. A true
ratio mismatch is refused because it would distort the effect.
"""
from __future__ import annotations

import os
import struct
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import dds_decode
import tex


#: RETRACTED. The 1x256-above-256px rule was inferred from build 226, whose
#: squares turned out to be the unscaled .s frame tables (FINDINGS-230).
#: Vanilla palette structure is preserved at every size now. The name is kept
#: because other modules still import it.
WIDE_THRESHOLD = 256

# --------------------------------------------------------------------------
# TWO THINGS THIS CONVERTER USED TO THROW AWAY -- FINDINGS-248
# --------------------------------------------------------------------------
# Both were MEASURED on the built archive and the mod's own files, and both
# are the reason SYW art arrives posterised and boxed no matter what cap is
# set. Each is separately switchable so a regression is one variable away.
#
# 1. THE COLOUR CEILING.  967 magic.lgp entries are replaced; 889 of them
#    (92%) come out with at most 16 colours, because the converter mirrors
#    vanilla's `colors_per_palette`. `moon` is a 1024x1024 photograph of the
#    moon rebuilt at 768x768 -- with FIFTEEN colours. That is the banding and
#    the stepped limb.
#
#    Widening is nearly free, and it is NOT build 226's forced 1x256:
#    `bits_per_index` and `bits_per_pixel` are ALREADY 8 in every vanilla
#    entry (measured across magic.lgp: 1x16, 22x16, 1x256 -- all 8/8), and
#    one byte per texel is stored either way. So the ONLY fields that move
#    are `colors_per_palette`, `colors_per_palette2` and `palette_size`.
#    `num_palettes` is preserved, so palette cycling -- which build 226
#    destroyed by forcing n_pal to 1 -- is untouched. Vanilla magic.lgp
#    itself ships a multi-palette 256-colour entry, so the shape is legal.
#
# 2. THE ALPHA CHANNEL.  The comment below this used to say "every magic DDS
#    is fully opaque". Measured over all 2855 of them: 2534 are (min alpha
#    >= 250) -- and 321 ARE NOT. 253 of those are more than half transparent.
#    Odin's `smoke_x` is 78% below alpha 128 and `moon` is 22%. For those the
#    replacement's own silhouette is thrown away and vanilla's is nearest-
#    UPSCALED in its place: a 256px mask stretched to 768 is a 3x3 staircase
#    around every edge, which is the "rough and janky" outline, and on an
#    additively blended sheet the faded-out part of the art is drawn at full
#    strength, which is the lit rectangle.
#
#    So the rule is per FILE, not global: use the DDS alpha when the DDS has
#    any, keep vanilla's mask when it does not.

# 3. THE TILING.  FINDINGS-251. A texture the game SCROLLS has to meet itself
#    at the wrap, and the artist made vanilla do that on exactly the axis it
#    wraps on -- Odin's mist `kiri_2` has a horizontal seam error of 3.8
#    against a typical column-to-column difference of 3.7, and a VERTICAL one
#    of 155.9, because it tiles sideways and not up. The AI upscale does not
#    know that: SYW's `kiri_2` has a horizontal seam error of **144.7**. So
#    the mist scrolls and jumps, which is the "very discontinuous" background
#    in the Zantetsuken scene.
#
#    32 replaced entries are broken this way and some are total -- `missil00`
#    and `yami00` go from a vertical seam of 0.3 to **255.0**. The repair is
#    only applied where vanilla PROVES the axis wraps (its own seam is no
#    worse than its ordinary neighbouring pixels) and the replacement clearly
#    broke it, so a texture that never tiled is never touched.

WIDE_PALETTE_ENV = 'SEVENTH_NX_SPELL_WIDE_PALETTE'
DDS_ALPHA_ENV = 'SEVENTH_NX_SPELL_DDS_ALPHA'
RESEAM_ENV = 'SEVENTH_NX_SPELL_RESEAM'

# The renderer demonstrably scrolls this Odin 2 mist horizontally, vanilla's
# left/right boundary is continuous, and SYW's is not.  Keep this explicit:
# an edge-score heuristic is useful evidence, but is not authority to rewrite
# unrelated art throughout magic.lgp.
TARGETED_WRAP_REPAIRS = {'kiri_2.tex': (1,)}

#: Vanilla counts as tiling on an axis when its seam is no worse than the
#: ordinary difference between neighbouring lines (with a floor, so a very
#: smooth texture is not judged on noise). The replacement counts as broken
#: only when its seam is BOTH several times vanilla's and large outright --
#: an upscale is smoother than its source, so a ratio alone flags 143 cases
#: of which most are invisible.
SEAM_VANILLA_FLOOR = 8.0
SEAM_BREAK_FACTOR = 4.0
SEAM_BREAK_ABS = 24.0
#: Fraction of the axis blended back into agreement, and its bounds.
SEAM_BAND_SHARE = 0.04
SEAM_BAND_MIN = 4
SEAM_BAND_MAX = 32

#: A DDS "has alpha" when it is not the flat 250-255 the opaque 89% carry AND
#: enough of it is actually see-through to be a silhouette rather than noise.
ALPHA_OPAQUE_FLOOR = 250
ALPHA_CUT = 128
ALPHA_MIN_SHARE = 0.005


def wide_palette() -> bool:
    """Widen a 16-colour palette to 256 entries. num_palettes is preserved."""
    v = os.environ.get(WIDE_PALETTE_ENV)
    return True if v is None else v not in ('', '0', 'off', 'false', 'no')


def dds_alpha() -> bool:
    """Take transparency from the replacement art when it carries any."""
    v = os.environ.get(DDS_ALPHA_ENV)
    return True if v is None else v not in ('', '0', 'off', 'false', 'no')


def reseam() -> bool:
    """Enable the audited, texture-specific wrap repairs.

    The current list contains only Odin 2's horizontally scrolling `kiri_2`
    mist.  Vanilla still has to prove that the named axis joins and the SYW
    source still has to fail that same check before any pixels are changed.

    Default ON. `SEVENTH_NX_SPELL_RESEAM=0` restores the supplied DDS bytes.
    """
    v = os.environ.get(RESEAM_ENV)
    return True if v is None else v not in ('', '0', 'off', 'false', 'no')


def _seam_stats(rgb, axis):
    """(seam error, typical neighbouring difference) along `axis`.

    axis 1 compares the left and right edges -- a HORIZONTAL wrap.
    """
    a = rgb[:, :, :3].astype(np.int32)
    if axis == 1:
        return (float(np.abs(a[:, 0] - a[:, -1]).mean()),
                float(np.abs(np.diff(a, axis=1)).mean()))
    return (float(np.abs(a[0] - a[-1]).mean()),
            float(np.abs(np.diff(a, axis=0)).mean()))


def broken_wraps(vanilla_rgb, source_rgba):
    """Axes where vanilla tiles and the replacement stopped tiling."""
    out = []
    for axis in (1, 0):
        if vanilla_rgb.shape[axis] < 8 or source_rgba.shape[axis] < 8:
            continue
        vs, vt = _seam_stats(vanilla_rgb, axis)
        ss, _st = _seam_stats(source_rgba, axis)
        if vs > max(vt, SEAM_VANILLA_FLOOR):
            continue                      # vanilla does not tile on this axis
        if ss > max(SEAM_BREAK_FACTOR * vs, SEAM_BREAK_ABS):
            out.append(axis)
    return out


def _reseam_axis(rgba, axis):
    """Blend the two edges of `axis` back into agreement.

    A linear taper over a narrow band: at the very edge both sides become
    their average, so the seam error is zero, and the correction falls to
    nothing `band` pixels in. Detail is preserved -- each line is shifted by
    a constant, not resampled.
    """
    n = rgba.shape[axis]
    band = int(min(max(round(n * SEAM_BAND_SHARE), SEAM_BAND_MIN),
                   SEAM_BAND_MAX, n // 2))
    if band < 1:
        return rgba
    out = rgba.astype(np.int32)
    view = out if axis == 0 else out.transpose(1, 0, 2)
    first, last = view[0].copy(), view[-1].copy()
    mid = (first + last) // 2
    for k in range(band):
        w = 1.0 - float(k) / band
        view[k] = view[k] + ((mid - first) * w).astype(np.int32)
        view[-1 - k] = view[-1 - k] + ((mid - last) * w).astype(np.int32)
    return np.clip(out, 0, 255).astype(np.uint8)


def _reseam_to_first(rgba, axis, edge_hold=1):
    """Make the final edge meet the first without altering the first edge.

    A scrolling wrap places the final edge beside the first.  Preserving one
    side avoids the old repair's pair of opposing ramps toward an invented
    average.  Only a narrow band at the final edge changes, with full
    correction on the boundary and no correction at the inner end.
    """
    n = rgba.shape[axis]
    band = int(min(max(round(n * SEAM_BAND_SHARE), SEAM_BAND_MIN),
                   SEAM_BAND_MAX, n // 2))
    if band < 1:
        return rgba
    edge_hold = max(1, min(int(edge_hold), band))
    out = rgba.astype(np.int32)
    view = out if axis == 0 else out.transpose(1, 0, 2)
    target = view[0].copy()
    for k in range(band):
        # Nearest-neighbour reduction may not sample the literal final source
        # line. Keep enough outer lines at full correction that the final
        # destination texel still meets the preserved first edge.
        weight = min(1.0, float(band - k) / max(1, band - edge_hold))
        current = view[-1 - k].copy()
        view[-1 - k] += np.rint((target - current) * weight).astype(np.int32)
    return np.clip(out, 0, 255).astype(np.uint8)


def alpha_is_meaningful(rgba) -> bool:
    """True when this DDS carries a real silhouette rather than flat opacity."""
    a = rgba[:, :, 3]
    return bool(a.min() < ALPHA_OPAQUE_FLOOR
                and (a < ALPHA_CUT).mean() >= ALPHA_MIN_SHARE)


class ConvertError(RuntimeError):
    """The group cannot be converted without changing its meaning."""


class BlankReplacement(ConvertError):
    """The supplied group is a blank placeholder, not replacement artwork."""


def shared_canvas_scale(source_scales, requested, native_width, native_height):
    """Choose one physical canvas for a possibly mixed-resolution palette set.

    FF7 stores one index bitmap shared by every palette, so every palette must
    end on one physical canvas.  SYW occasionally mixes, for example, 2x and
    4x images in the same animation.  Those images describe the same canvas;
    their normalized X:Y scale ratio is the invariant.  Use the most detailed
    member to choose the output scale, bounded by the requested 256px-based
    cap.  Reject a real ratio mismatch because combining those images would
    stretch at least one axis and change the effect's coordinates.
    """
    import math
    if not source_scales:
        raise ConvertError('replacement has no source canvases')
    normalized = []
    detail = []
    for sx, sy in source_scales:
        if min(sx, sy) < 1:
            raise ConvertError('replacement source is smaller than native canvas')
        common = math.gcd(sx, sy)
        normalized.append((sx // common, sy // common))
        detail.append(common)
    if len(set(normalized)) != 1:
        raise ConvertError('palette images disagree on logical canvas aspect')
    rx, ry = normalized[0]
    limit = 256 * int(requested)
    q = min(int(requested), max(detail))
    while q > 0 and (native_width * rx * q > limit or
                     native_height * ry * q > limit):
        q -= 1
    if q < 1:
        raise ConvertError('replacement aspect cannot fit the selected cap')
    return rx * q, ry * q


def sprite_count(drawn, min_frac=0.0015):
    """How many separate sprites the drawn region contains.

    Run-length union-find, one pass. Runs per row are few, so this is a few
    hundred operations per texture rather than one per pixel, and it needs no
    scipy -- the build does not gain a dependency for it.

    Specks below `min_frac` of the image are ignored: antialiased crumbs
    around a sprite are not a second sprite.
    """
    h, w = drawn.shape
    parent = []

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    size = []
    prev = []                       # [(x0, x1, label)] from the row above
    for y in range(h):
        row = drawn[y]
        if not row.any():
            prev = []
            continue
        edges = np.flatnonzero(np.diff(np.concatenate(
            ([0], row.view(np.int8), [0]))))
        cur = []
        for i in range(0, len(edges), 2):
            x0, x1 = int(edges[i]), int(edges[i + 1])
            lbl = len(parent)
            parent.append(lbl)
            size.append(x1 - x0)
            for px0, px1, plbl in prev:
                if px0 < x1 and x0 < px1:       # 4-connected overlap
                    union(lbl, plbl)
            cur.append((x0, x1, lbl))
        prev = cur
    if not parent:
        return 0
    totals = {}
    for lbl, n in enumerate(size):
        r = find(lbl)
        totals[r] = totals.get(r, 0) + n
    floor = max(16, drawn.size * min_frac)
    return sum(1 for n in totals.values() if n >= floor)


# --------------------------------------------------------------- decoding

def _decode(dds_bytes):
    """DDS -> (uint8 array [h, w, 4] RGBA, w, h)."""
    rgba, w, h = dds_decode.decode_dds(dds_bytes)
    a = np.frombuffer(rgba, dtype=np.uint8)
    if a.size != w * h * 4:
        raise ConvertError('DDS payload is %d bytes, expected %d'
                           % (a.size, w * h * 4))
    return a.reshape(h, w, 4), w, h


def _premultiply(rgba, alpha):
    """RGB scaled by alpha/255, alpha left as it was.

    For a sheet with no keyed palette entry there is no way to mark a texel
    transparent, so the only honest translation of "the artist faded this
    out" is to fade the COLOUR out. Under the additive blend these sheets use
    that is exact: alpha 0 becomes black and adds nothing.
    """
    out = rgba.copy()
    scale = alpha.astype(np.uint16)
    for ch in range(3):
        out[:, :, ch] = ((rgba[:, :, ch].astype(np.uint16) * scale + 127)
                         // 255).astype(np.uint8)
    return out


def _resample(img, tw, th):
    """Nearest-neighbour to (th, tw). Same rule as tex._resample_rgba, so a
    texture converted here lands on the same texels as one converted there."""
    h, w = img.shape[:2]
    if (w, h) == (tw, th):
        return img
    ys = (np.arange(th) * h) // th
    xs = (np.arange(tw) * w) // tw
    return img[ys[:, None], xs[None, :]]


# ------------------------------------------------------------ the palette

def _vanilla_palette(van):
    """(rgb, alpha) arrays shaped [num_palettes, colors_per_palette, 3] and
    [num_palettes, colors_per_palette]. TEX stores BGRA on disk."""
    n, c = van['num_palettes'], van['colors_per_palette']
    raw = np.frombuffer(van['palette'], dtype=np.uint8)
    if raw.size < n * c * 4:
        raise ConvertError('palette is %d bytes, expected %d'
                           % (raw.size, n * c * 4))
    raw = raw[:n * c * 4].reshape(n, c, 4)
    rgb = raw[:, :, [2, 1, 0]].copy()          # BGRA -> RGB
    return rgb, raw[:, :, 3].copy()


#: Luminance at or below this contributes nothing to an additive effect, so
#: it is "nothing is drawn here". Module level since build 239 -- the frame
#: chosen to derive the index layout is picked before convert_group's local
#: copy existed.
DARK_LEVEL = 12

#: FINDINGS-252. `DARK_LEVEL` was calibrated when a palette held FIFTEEN
#: colours and pure black had to share a bucket with the falloff around it --
#: median cut returned the bucket's mean a few levels above zero, and additive
#: blending turned that into a faint lit rectangle over the whole quad. The
#: answer was to reserve a slot, pin it to (0,0,0), and put everything at or
#: below luminance 12 into it.
#:
#: With 256 colours that trade is gone: the quantiser has slots to spare and
#: can hold pure black AND several steps of the falloff. Meanwhile the
#: threshold is doing real damage to art that is legitimately dark. Measured
#: on SYW's `sky_2`, the night sky behind Odin:
#:
#:     luminance      0        27.3%   the intended void
#:     luminance   1..12       48.5%   REAL CLOUD DETAIL, crushed to black
#:     luminance  13..255      24.2%   all that survived
#:
#: 75.8% of the image came out flat black, which is the "isolated cloud
#: clumps on abrupt flat edges" in the Zantetsuken background. The hard
#: threshold IS the abrupt edge.
#:
#: So when the palette is wide the reservation only pins EXACT black, and
#: that keeps both properties at once. Measured on the build-226 canaries at
#: three thresholds, where "glow" is the mean output brightness over the
#: texels the source left exactly black -- the number the reservation exists
#: to hold at zero:
#:
#:     threshold   black kept   dark detail kept   glow on black
#:         2          100%           49-67%            0.00
#:         1          100%           70-94%            0.00
#:         0          100%            100%             0.00
#:
#: Nothing is traded. The exactly-black texels are still excluded from the
#: quantiser, so they still come out (0,0,0); everything above zero now has
#: 255 slots to land in instead of being swept into the black one. `sky_2`
#: goes from 75.8% flat black to 27.2%, which is the source's own.
DARK_LEVEL_WIDE = 0

_QBITS = 5                     # same coarseness tex._palette_lut works at
_QSHIFT = 8 - _QBITS
_QN = 1 << _QBITS

#: How fine the histogram may get when 5 bits is not enough, and how many
#: occupied buckets `tex._median_cut_hist` (a Python loop) may be handed. The
#: budget is what keeps the refinement free: an image that runs out of
#: colours at 5 bits has FEW buckets at 8 bits too -- Odin's premultiplied
#: smoke has 21 at 5 bits and 157 at 8 -- while an image that already fills
#: its palette at 5 bits never refines at all.
_QBITS_MAX = 8
_QBUCKET_BUDGET = 40000


def _key_bits(flat, bits):
    """RGB bucket per pixel at `bits` per channel."""
    sh, n = 8 - bits, 1 << bits
    return (((flat[:, 0] >> sh).astype(np.int64) * n
             + (flat[:, 1] >> sh).astype(np.int64)) * n
            + (flat[:, 2] >> sh).astype(np.int64))


def _key5(flat):
    """15-bit RGB bucket per pixel, matching tex._palette_lut's grid."""
    return _key_bits(flat, _QBITS).astype(np.int32)


def _quantise(rgba, n_colors, opaque):
    """Median-cut the opaque pixels of one RGBA image into <= n_colors.

    The histogram is binned to 5 bits per channel first. That is not a
    shortcut: `tex._median_cut_hist` walks its boxes in Python, and an HD
    source has ~200k distinct colours, which would make the split loop the
    whole runtime of the build. Binning caps the histogram at 32,768 entries
    and costs nothing in the result, because the colour that actually lands
    in the palette is recomputed at FULL precision afterwards by
    `_mean_by_index` -- the median cut only decides how the colours are
    grouped, not what they are.

    `opaque` is a [h, w] bool mask of the pixels that are actually drawn.
    It is NOT derived from the DDS alpha channel -- see convert_group.

    Returns (palette, index array [h, w]); transparent pixels get -1.
    """
    opaque = opaque.reshape(-1)
    flat = rgba[:, :, :3].reshape(-1, 3)

    # ---- REFINE THE HISTOGRAM UNTIL IT CAN FILL THE PALETTE ---------------
    #
    # FINDINGS-248. Median cut can only return as many colours as there are
    # OCCUPIED buckets, so a 5-bit histogram caps the palette at whatever the
    # art happens to occupy at 5 bits. Odin's premultiplied smoke occupies 21
    # -- so a 256-entry palette came back with 22 colours and the plume
    # banded. It has 157 buckets at 8 bits, which is 7x the gradation for no
    # meaningful cost, because the refinement only runs when 5 bits FAILED to
    # fill the palette and such an image is sparse at every depth.
    bits = _QBITS
    while True:
        keys = _key_bits(flat, bits)
        counts = np.bincount(keys[opaque], minlength=(1 << bits) ** 3)
        used = np.nonzero(counts)[0]
        if used.size == 0:
            raise ConvertError('every pixel of palette 0 is transparent')
        if (used.size >= n_colors or bits >= _QBITS_MAX
                or used.size > _QBUCKET_BUDGET):
            break
        probe = np.unique(_key_bits(flat, bits + 1)[opaque]).size
        if probe <= used.size or probe > _QBUCKET_BUDGET:
            break
        bits += 1

    n = 1 << bits
    shift = 8 - bits
    half = (1 << (shift - 1)) if shift else 0
    hist = {}
    for bucket in used.tolist():
        b = bucket % n
        g = (bucket // n) % n
        r = bucket // (n * n)
        hist[((r << shift) + half, (g << shift) + half,
              (b << shift) + half, 255)] = int(counts[bucket])
    palette, _ = tex._median_cut_hist(hist, n_colors)
    if not palette:
        raise ConvertError('median cut produced no colours')

    # ---- NEAREST ENTRY PER OCCUPIED BUCKET, NOT PER GRID CELL -------------
    #
    # `tex._palette_lut` tabulates a (2**bits)**3 grid, which is affordable
    # only at 5 bits -- at 8 it would be 16.7 million cells. It also cannot
    # separate palette colours that share a grid cell, which is exactly the
    # case a refined histogram creates.
    #
    # There are at most `_QBUCKET_BUDGET` OCCUPIED buckets and at most 256
    # palette entries, so the assignment is a small dense argmin: cheaper
    # than the 5-bit table it replaces, and exact at bucket resolution.
    centres = np.empty((used.size, 3), dtype=np.int32)
    centres[:, 2] = (used % n)
    centres[:, 1] = ((used // n) % n)
    centres[:, 0] = (used // (n * n))
    centres = (centres << shift) + half
    pal_rgb = np.asarray([[p[0], p[1], p[2]] for p in palette],
                         dtype=np.int32)
    nearest = np.empty(used.size, dtype=np.int16)
    step = 4096
    for s in range(0, used.size, step):
        chunk = centres[s:s + step, None, :] - pal_rgb[None, :, :]
        nearest[s:s + step] = (chunk * chunk).sum(axis=2).argmin(axis=1)
    pos = np.searchsorted(used, keys)
    np.clip(pos, 0, used.size - 1, out=pos)
    idx = nearest[pos].astype(np.int16)
    idx[~opaque] = -1
    return palette, idx.reshape(rgba.shape[:2])


def _mean_by_index(rgba, idx, n_slots):
    """Mean RGB of `rgba` over the pixels holding each index slot.

    Returns (means [n_slots, 3] float, counts [n_slots]). Slots with no
    pixels come back as count 0 and the caller keeps the vanilla colour --
    inventing one would put an arbitrary hue into a palette the game cycles.
    """
    flat = idx.reshape(-1)
    keep = flat >= 0
    sel = flat[keep].astype(np.int64)
    px = rgba[:, :, :3].reshape(-1, 3)[keep].astype(np.int64)
    counts = np.bincount(sel, minlength=n_slots)
    sums = np.stack([np.bincount(sel, weights=px[:, ch], minlength=n_slots)
                     for ch in range(3)], axis=1)
    safe = np.maximum(counts, 1)[:, None]
    return sums / safe, counts


def boundary_connected_black(rgba):
    """Exact-black pixels connected to the image boundary (4-neighbour).

    Opaque-alpha FFNx spell DDS files commonly encode their transparent
    canvas as RGB zero. Padding by one pixel lets one flood fill select every
    boundary component in a single operation while preserving enclosed black
    detail. The mask is made at source resolution and resized only afterward.
    """
    from PIL import Image, ImageDraw
    black = np.all(np.asarray(rgba)[:, :, :3] == 0, axis=2)
    h, w = black.shape
    # 0 is traversable exact black/exterior; 255 is coloured art. Fill the
    # padded exterior with 128 and inspect only the original image rectangle.
    field = np.full((h + 2, w + 2), 255, dtype=np.uint8)
    field[0, :] = field[-1, :] = 0
    field[:, 0] = field[:, -1] = 0
    field[1:-1, 1:-1][black] = 0
    # fromarray may expose a read-only buffer (Pillow then accepts floodfill
    # but leaves every pixel unchanged), so force an owned writable image.
    im = Image.fromarray(field, mode='L').copy()
    ImageDraw.floodfill(im, (0, 0), 128, thresh=0)
    return np.asarray(im)[1:-1, 1:-1] == 128


# ------------------------------------------------------------- the driver

def convert_group(vanilla_tex, dds_by_palette, scale=1,
                  tables_handled=False, texture_name=None):
    """Rebuild one TEX from its FFNx DDS set.

    `vanilla_tex`     the entry's current bytes -- the structural template.
    `dds_by_palette`  {palette index: DDS bytes}; must cover 0..N-1.
    `scale`           whole-number size multiplier, >= 1.

    Returns (tex_bytes, note). Raises ConvertError and changes nothing if the
    group cannot be rebuilt exactly.
    """
    van = tex.parse(vanilla_tex)
    if van is None:
        raise ConvertError('vanilla entry is not a TEX')
    if not van['palette_flag'] or van['bytes_per_pixel'] != 1:
        raise ConvertError('vanilla entry is not 8-bit paletted')
    n_pal = van['num_palettes']
    cpp = van['colors_per_palette']
    if n_pal < 1 or cpp not in (16, 256):
        raise ConvertError('unsupported palette shape %dx%d' % (n_pal, cpp))
    if van['palette_size'] != n_pal * cpp:
        raise ConvertError('palette_size %d != %d*%d'
                           % (van['palette_size'], n_pal, cpp))
    missing = [p for p in range(n_pal) if p not in dds_by_palette]
    if missing:
        raise ConvertError('mod supplies %d of %d palettes (missing %s)'
                           % (n_pal - len(missing), n_pal,
                              ','.join(str(m) for m in missing[:6])))
    if scale < 1:
        raise ConvertError('scale must be >= 1')

    # ---- ABOVE 256px THE LAYOUT MUST BE 1 PALETTE x 256 COLOURS -----------
    #
    # MEASURED across the whole shipped tree, paletted textures over 256px:
    #
    #     battle.lgp    574 of 574 are 1x256   renders correctly at 768px
    #     world_us.lgp   66 of  66 are 1x256   renders correctly at 768px
    #     magic.lgp      85 of 861 are 1x256   BROKEN at 512px
    #
    # The other 776 in magic.lgp were 16-colour, because this converter
    # mirrored vanilla's palette structure. That is right for fidelity at the
    # vanilla size -- vanilla IS 16-colour and renders -- and it is exactly
    # what fails when the texture is enlarged. `tex.convert_for_battle` has
    # said so since v4: "Mirroring vanilla 16-color / multi-palette layouts
    # produced BLACK models in every build that tried it, regardless of pixel
    # content, so vanilla palette structure is deliberately ignored."
    #
    # So the layout follows the SIZE, not the source:
    #
    #   output <= 256px   mirror vanilla exactly (proven: it is vanilla)
    #   output >  256px   1 palette x 256 colours (proven: 640 textures of it
    #                     already ship and render)
    #
    # A multi-palette texture cannot take the second form without losing the
    # palette cycling that animates it, so those are held at 256 instead --
    # see the clamp below.
    wide = None
    van_rgb, van_alpha = _vanilla_palette(van)

    # ---- SCALE IS CLAMPED TO WHAT THE SOURCE ACTUALLY HAS -----------------
    #
    # `_resample` is nearest-neighbour, so upscaling past the source's own
    # resolution does not add detail, it adds BLOCKS. Build 224 shipped a
    # 32x32 vanilla slot at 16x (512px) from a 128x128 source: every texel
    # became a hard 4x4 square, which is the "rectangles of texture, abrupt
    # edges" that build reported. `tex.convert_for_battle` already clamps
    # this way (`k = min(cap//nw, cap//nh, w//nw, h//nh)`); this did not,
    # and that was the bug.
    # THE INDEX LAYOUT COMES FROM A PALETTE THAT HAS A PICTURE IN IT.
    #
    # BUILD 239. `base` decides only which texels SHARE a colour -- every
    # palette's actual colours are recomputed from its own DDS further down.
    # Taking it from palette 0 unconditionally fails on a cycling animation
    # whose frame 0 is dark: `chocob03` has 22 palettes and SYW's
    # `chocob03_00.dds` is entirely black (max luminance 0 over the whole
    # 1024px image), so the quantiser was handed nothing and the entry was
    # refused with "every pixel of palette 0 is transparent" -- losing all
    # 22 frames over one blank one.
    #
    # The index array is shared by every palette by definition, so any frame
    # can derive it and a frame with content derives it better. Palette 0
    # stays the reference whenever it has any; the search only runs when it
    # does not, so nothing that worked before decodes differently.
    # FINDINGS-248 refines this. "Not entirely black" is a low bar: a frame
    # that is nearly black passes it and still partitions the image into two
    # or three slots, and the OTHER 21 frames are then averaged over that
    # partition -- which is how `chocob03` came out of a 22-frame animation
    # with 2 colours. The partition is shared by every palette by definition,
    # so the frame that should derive it is the one with the most to say:
    # whichever decodes to the most distinct colours. Palette 0 still wins
    # ties, so a set whose frame 0 is already the richest decodes exactly as
    # it did before.
    # Decoded ONCE, here, and reused by the per-palette loop below. Choosing
    # the reference used to need only palette 0, so decoding was lazy; now
    # that the choice looks at every frame, decoding twice would double the
    # cost of every multi-palette entry.
    decoded = {}
    for _p in sorted(dds_by_palette):
        _img, _w, _h = _decode(dds_by_palette[_p])
        decoded[_p] = _img
    # ---- RESTORE A WRAP THE REPLACEMENT BROKE ----------------------------
    #
    # Decided against VANILLA, which is the only evidence available that the
    # game tiles this texture on this axis, and applied at source resolution
    # so the taper has room to work.
    reseamed = []
    repair_axes = TARGETED_WRAP_REPAIRS.get(
        os.path.basename(texture_name or '').lower(), ())
    if reseam() and repair_axes:
        _vp = _vanilla_palette(van)[0]
        _vi = np.frombuffer(van['pixels'], dtype=np.uint8).reshape(
            van['height'], van['width'])
        _vrgb = _vp[0][_vi]
        broken = set(broken_wraps(_vrgb, decoded[0]))
        reseamed = [axis for axis in repair_axes if axis in broken]

    # A GROUP WITH NOTHING IN IT IS NOT A REPLACEMENT.
    #
    # Some SYW groups contain only black placeholder images. Rebuilding from
    # one replaces visible vanilla art with nothing. Tell the archive driver
    # this specific fact so it can preserve that art on an enlarged canvas;
    # all other conversion errors remain hard refusals.
    if all(im[:, :, :3].max() <= DARK_LEVEL for im in decoded.values()):
        raise BlankReplacement('every supplied palette image is blank (max RGB '
                               '%d over %d image(s))'
                               % (max(int(im[:, :, :3].max())
                                      for im in decoded.values()), len(decoded)))

    _ref = 0
    base = decoded[0]
    bw, bh = base.shape[1], base.shape[0]
    if len(decoded) > 1:
        def _variety(im):
            f = im[:, :, :3].reshape(-1, 3)
            lit = f.max(axis=1) > DARK_LEVEL
            if not lit.any():
                return 0
            return int(np.unique(_key5(f)[lit]).size)
        best = _variety(base)
        for _p in sorted(decoded):
            if _p == 0:
                continue
            _v = _variety(decoded[_p])
            if _v > best:
                _ref, base, best = _p, decoded[_p], _v
                bw, bh = base.shape[1], base.shape[0]
    # The physical scale is an exact property of this texture. Require every
    # palette to describe an integer multiple of the native canvas. Palette
    # frames MAY have different resolutions: SYW has valid animations mixing
    # 2x and 4x frames. Their normalized X:Y ratio must agree, then every frame
    # is resampled onto the best shared canvas available under the selected
    # cap. The TEX marker carries that canvas scale to the runtime.
    vw, vh = van['width'], van['height']
    source_scales = []
    for p in sorted(decoded):
        im = decoded[p]
        sh, sw = im.shape[:2]
        crop_w = sw - 1 if sw % vw and (sw - 1) % vw == 0 else sw
        crop_h = sh - 1 if sh % vh and (sh - 1) % vh == 0 else sh
        if (crop_w, crop_h) != (sw, sh):
            decoded[p] = im[:crop_h, :crop_w]
            sh, sw = crop_h, crop_w
        if sw % vw or sh % vh:
            raise ConvertError('palette %d source canvas %dx%d is not an '
                               'integer multiple of native %dx%d'
                               % (p, sw, sh, vw, vh))
        source_scales.append((sw // vw, sh // vh))
    scale_x, scale_y = shared_canvas_scale(source_scales, scale, vw, vh)

    # ---- NO PER-TEXTURE FREEZES ANY MORE ---------------------------------
    #
    # Builds 228-231 held back frame sheets, full-frame art and multi-palette
    # textures because their logical coordinates did not follow a larger TEX.
    # V37 records the scale in this TEX and fixes the object reciprocal once;
    # frame tables stay native, so there is nothing left to classify or hold.
    sprites = None
    tw, th = van['width'] * scale_x, van['height'] * scale_y
    # UNIFORM SCALING keeps vanilla's palette structure at every size.
    #
    # The 1x256 rule came from build 226, where 512px textures drew as
    # squares and I attributed it to the layout. Build 231 found the real
    # cause -- the .s frame tables index by texel and were not being scaled.
    # The layout attribution was very likely wrong, and switching layout also
    # forces multi-palette textures (272 of them, whose palettes ARE the
    # animation) to stay small. Preserving vanilla structure is both the more
    # conservative choice and the one that lets everything scale.
    wide = False
    # Apply the audited wrap repair only after the physical canvas is known.
    # A nearest reduction can skip the literal last source row/column, so the
    # fully corrected outer band must cover every source texel which can map
    # to the final destination texel.
    for _axis in reseamed:
        _target_n = th if _axis == 0 else tw
        for _p in decoded:
            _source_n = decoded[_p].shape[_axis]
            _hold = max(1, (_source_n + _target_n - 1) // _target_n)
            decoded[_p] = _reseam_to_first(
                decoded[_p], _axis, edge_hold=_hold)
    base = decoded[_ref]
    # Keep the reference at its SOURCE resolution: `alpha_is_meaningful` has
    # to see the art the mod shipped, not a resampled copy of it.
    base_src = base
    base = _resample(base, tw, th)

    # ---- TRANSPARENCY COMES FROM VANILLA, NOT FROM THE DDS ALPHA ----------
    #
    # MEASURED on SYW's own files: every magic DDS is fully opaque (alpha
    # 251-255, 0.0% below 128). These effects key their transparency on the
    # PALETTE, and the mod simply draws the keyed region black. Testing DDS
    # alpha therefore found nothing transparent, the quantiser spent a real
    # colour on the background, and the whole rectangle got drawn -- build
    # 224's black boxes behind thunder and bolt.
    #
    # The authority on which texels are transparent is the vanilla index
    # bitmap, and `colorkey` says whether index 0 is the keyed one:
    #
    #   colorkey=1   879 entries, index 0 covers ~45-54% of pixels  <- the key
    #   colorkey=0   228 entries, index 0 covers ~6%                <- a colour
    #
    # 852 of the 879 also mark that entry alpha 0; 27 rely on the flag alone,
    # so both are honoured. The mask is nearest-upscaled with the SAME rule
    # the art is resampled by, so the two align texel for texel.
    colorkey = struct.unpack_from('<I', vanilla_tex, tex.O_COLORKEY)[0]
    clear = [k for k in range(cpp) if van_alpha[0][k] == 0]
    if colorkey == 1 and 0 not in clear:
        clear = [0] + clear

    # ---- WIDEN THE PALETTE -- see FINDINGS-248 at the top of this file ----
    #
    # `clear` is computed from the VANILLA entries above, so the slots added
    # here are all opaque and all land in `pool`. Nothing else about the
    # texture changes: same num_palettes, same 8-bit indices, same one byte
    # per texel, same colour key.
    widened = 0
    if wide_palette() and cpp < 256:
        widened = cpp
        rgb2 = np.zeros((n_pal, 256, 3), dtype=np.uint8)
        rgb2[:, :cpp] = van_rgb
        a2 = np.full((n_pal, 256), 255, dtype=np.uint8)
        a2[:, :cpp] = van_alpha
        van_rgb, van_alpha, cpp = rgb2, a2, 256

    if wide:
        # The proven wide layout, matching tex.convert_for_battle exactly:
        # entry 0 reserved transparent, 1..255 available for colour.
        cpp = 256
        n_pal = 1
        clear = [0]
        van_rgb = np.zeros((1, 256, 3), dtype=np.uint8)
        van_alpha = np.zeros((1, 256), dtype=np.uint8)
        van_alpha[0][1:] = 255
    pool = [k for k in range(cpp) if k not in clear]
    if not pool:
        raise ConvertError('vanilla palette 0 has no opaque entries')
    clear_index = clear[0] if clear else None

    # ---- TRANSPARENCY: ALWAYS THE REPLACEMENT'S SHAPE ---------------------
    #
    # `use_alpha` is decided per FILE. 2534 of the mod's 2855 magic DDS are
    # flat-opaque and fall through to the vanilla mask exactly as before; the
    # 321 that carry a silhouette get to keep it.
    #
    # Two shapes, because the two kinds of texture mean different things by
    # "transparent":
    #
    #   a keyed entry exists   the art's alpha becomes the KEY mask, at the
    #                          replacement's own resolution instead of a
    #                          256px vanilla mask blown up 3x.
    #   no keyed entry         nothing can be marked transparent, so the art
    #                          is PREMULTIPLIED: alpha 0 becomes black, which
    #                          adds nothing under the additive blend these
    #                          sheets are drawn with, and partial alpha dims
    #                          in proportion. That is what the artist drew.
    use_alpha = dds_alpha() and alpha_is_meaningful(base_src)
    src_alpha = (_resample(base_src[:, :, 3:4], tw, th)[:, :, 0]
                 if use_alpha else None)
    premultiply = use_alpha and clear_index is None
    if premultiply:
        base = _premultiply(base, src_alpha)

    if clear_index is None:
        opaque = np.ones((th, tw), dtype=bool)
    elif use_alpha:
        opaque = src_alpha >= ALPHA_CUT
        if not opaque.any():
            raise ConvertError('replacement art is entirely transparent')
    else:
        # Flat alpha does not mean the black canvas is opaque. SYW's keyed
        # spell art stores transparency as exact RGB zero. Flood only from the
        # boundary so a black pupil, hole, or interior shadow remains colour.
        keyed = boundary_connected_black(base_src)
        opaque = ~_resample(keyed[:, :, None], tw, th)[:, :, 0]
        if not opaque.any():
            raise ConvertError('replacement keyed canvas is entirely clear')

    # ---- EXACT BLACK IS RESERVED, NEVER QUANTISED -------------------------
    #
    # These effects are drawn additively: pure black adds nothing and is how
    # the art says "no contribution here". A median cut does not know that --
    # it merges the black field with the dark falloff around it and returns a
    # bucket mean a few levels above zero, which additive blending turns into
    # a faint lit rectangle over the whole quad.
    #
    # MEASURED on the shipped build 226 archive, brightness where vanilla was
    # pure black: smoke_13 lifted to a mean of 11.8 with 12.5% of its black
    # above 8; smoke_14 7.5; sky_a 7.8. So one palette slot is reserved and
    # pinned to (0,0,0), near-black source texels are assigned to it, and the
    # quantiser never sees them. This is the same rule the field background
    # pass enforces for its own colour-key cells.
    # FINDINGS-252: the threshold follows the palette width. 15 slots needed
    # the wide net; 255 do not, and the wide net eats genuinely dark art.
    DARK = DARK_LEVEL_WIDE if cpp >= 256 else DARK_LEVEL
    lum = base[:, :, :3].max(axis=2)
    dark = opaque & (lum <= DARK)
    # `opaque & ~dark` must have something in it or the quantiser has no
    # colours to cut. A frame that is genuinely all black falls through to
    # the plain branch, which produces a one-colour black palette rather
    # than an exception.
    if dark.any() and (opaque & ~dark).any() and len(pool) > 1:
        palette, slot_idx = _quantise(base, len(pool) - 1, opaque & ~dark)
        black_slot = len(palette)
        palette = list(palette) + [(0, 0, 0, 255)]
        slot_idx = np.where(dark, black_slot, slot_idx)
    else:
        black_slot = None
        palette, slot_idx = _quantise(base, len(pool), opaque)

    # slot -> real palette entry, and the keyed entry for everything else.
    # With no keyed entry there is nothing transparent, so the fallback is
    # the first colour rather than a reserved one.
    fallback = clear_index if clear_index is not None else pool[0]
    slot_to_entry = np.array(pool[:len(palette)] + [fallback], dtype=np.uint8)
    entries = np.where(slot_idx < 0, len(slot_to_entry) - 1, slot_idx)
    index_bytes = slot_to_entry[entries].astype(np.uint8).tobytes()

    # Every palette, including 0, is derived the same way -- palette 0 from
    # its own median cut would round-trip differently from the others and
    # make frame 0 subtly disagree with frames 1..N-1.
    out_rgb = van_rgb.copy()
    empty_slots = 0
    for p in range(n_pal):
        img = _resample(decoded[p], tw, th)
        if img.shape[:2] != (th, tw):
            raise ConvertError('palette %d resampled to %r, expected %r'
                               % (p, img.shape[:2], (th, tw)))
        if premultiply:
            # Every palette gets the SAME treatment the reference got, or
            # frame 0 would fade and frames 1..N-1 would not.
            img = _premultiply(img, img[:, :, 3])
        means, counts = _mean_by_index(img, slot_idx, len(palette))
        for slot in range(len(palette)):
            if slot == black_slot:
                # Pinned. Averaging the source here would reintroduce the
                # lift this reservation exists to prevent.
                out_rgb[p][pool[slot]] = (0, 0, 0)
                continue
            if counts[slot] == 0:
                empty_slots += 1
                continue                        # keep the vanilla colour
            out_rgb[p][pool[slot]] = np.clip(
                np.rint(means[slot]), 0, 255).astype(np.uint8)

    # BGRA on disk; alpha is vanilla's, per palette, per entry.
    pal_out = np.empty((n_pal, cpp, 4), dtype=np.uint8)
    pal_out[:, :, 0] = out_rgb[:, :, 2]
    pal_out[:, :, 1] = out_rgb[:, :, 1]
    pal_out[:, :, 2] = out_rgb[:, :, 0]
    pal_out[:, :, 3] = van_alpha

    if wide:
        # tex._paletted_header is the layout every other >256px paletted
        # texture in this build already ships with; do not hand-roll it.
        hdr = tex._paletted_header(vanilla_tex, tw, th, 1, 256)
    else:
        hdr = bytearray(vanilla_tex[:tex.HEADER_LEN])
        struct.pack_into('<I', hdr, tex.O_WIDTH, tw)
        struct.pack_into('<I', hdr, tex.O_HEIGHT, th)
        old_pitch = struct.unpack_from('<I', vanilla_tex, tex.O_PITCH)[0]
        if old_pitch:
            struct.pack_into('<I', hdr, tex.O_PITCH, old_pitch * scale_x)
        if widened:
            # THREE fields. Everything else -- num_palettes, colour key,
            # bits_per_index, bits_per_pixel, bytes_per_pixel -- is already
            # what a 256-colour entry needs and is left exactly as vanilla
            # wrote it. Asserted by the self-check below.
            struct.pack_into('<I', hdr, tex.O_COLORS_PER_PAL, cpp)
            struct.pack_into('<I', hdr, tex.O_COLORS_PER_PAL2, cpp)
            struct.pack_into('<I', hdr, tex.O_PAL_SIZE, n_pal * cpp)

    out = bytes(hdr) + pal_out.tobytes() + index_bytes

    # Self-check: it must parse, and every structural field must still be
    # vanilla's. A replacement that fails this is dropped, never shipped.
    chk = tex.parse(out)
    if chk is None:
        raise ConvertError('output does not parse as a TEX')
    if wide:
        want = {'num_palettes': 1, 'colors_per_palette': 256,
                'palette_size': 256, 'palette_flag': 1, 'bytes_per_pixel': 1}
    else:
        want = {f: van[f] for f in ('num_palettes', 'colors_per_palette',
                                    'palette_size', 'palette_flag',
                                    'bytes_per_pixel')}
        if widened:
            # num_palettes, palette_flag and bytes_per_pixel stay vanilla on
            # purpose: widening must not be able to turn into build 226's
            # forced 1x256, which is what destroyed palette cycling.
            want['colors_per_palette'] = cpp
            want['palette_size'] = n_pal * cpp
            if struct.unpack_from('<I', out, tex.O_COLORKEY)[0] != colorkey:
                raise ConvertError('widening changed the colour key')
            for off, label in ((tex.O_BITS_PER_INDEX, 'bits_per_index'),
                               (tex.O_BITS_PER_PIXEL, 'bits_per_pixel')):
                if (struct.unpack_from('<I', out, off)[0]
                        != struct.unpack_from('<I', vanilla_tex, off)[0]):
                    raise ConvertError('widening changed %s' % label)
    for field, expect in want.items():
        if chk[field] != expect:
            raise ConvertError('output %s is %r, expected %r'
                               % (field, chk[field], expect))

    if (chk['width'], chk['height']) != (tw, th):
        raise ConvertError('output is %dx%d, expected %dx%d'
                           % (chk['width'], chk['height'], tw, th))
    if bytes(out[:tex.HEADER_LEN]) != bytes(hdr):
        raise ConvertError('header was not written verbatim')

    scale_label = (str(scale_x) if scale_x == scale_y else
                   '%dx%d' % (scale_x, scale_y))
    note = ('%dx%d -> %dx%d x%s, %d palettes x %d colours%s%s, %d/%d entries '
            'used, %.0f%% keyed%s'
            % (van['width'], van['height'], tw, th, scale_label, n_pal, cpp,
               (' (widened from %d)' % widened) if widened else '',
               (', alpha from the art' if use_alpha and not premultiply
                else ', art premultiplied by its alpha' if premultiply
                else '')
               + (', %s wrap restored'
                  % '+'.join('horizontal' if x == 1 else 'vertical'
                             for x in reseamed) if reseamed else ''),
               len(palette), len(pool), 100.0 * (1.0 - opaque.mean()),
               (', %d sprites so held at vanilla size' % sprites)
               if sprites and sprites > 1 else
               (', full-frame and unverifiable so held at vanilla size'
                if sprites is None and scale == 1 and
                max(van['width'], van['height']) * 1 == tw else '')))
    if empty_slots:
        note += ', %d palette slots kept vanilla' % empty_slots
    if (bw, bh) != (tw, th):
        note += ' (source %dx%d)' % (bw, bh)
    return out, note


def max_scale(vanilla_tex, cap):
    """Largest whole scale whose result still fits under `cap` pixels.

    `cap` of 0/None means "native": scale 1, the vanilla dimensions exactly.
    """
    if not cap:
        return 1
    van = tex.parse(vanilla_tex)
    if van is None:
        return 1
    biggest = max(van['width'], van['height'])
    if biggest <= 0:
        return 1
    return max(1, cap // biggest)
