#!/usr/bin/env python3
"""
ff7nx_spelltex.py -- SYW Unified Spell Textures into native ``magic.lgp``.

WHY THIS NEEDS ITS OWN PASS
===========================
``magic.lgp`` is the only archive in this project with DUPLICATE entry names:
5,252 entries, 3,454 unique, 652 names used more than once, and 136 of those
hold genuinely different pictures. The game tells them apart through the LGP
conflict table -- internal directories such as ``ff7/data/battle/blue/chyobo``.

``lgp.Archive`` indexes ``{name: entry}``, so LAST DUPLICATE WINS, and
``_build_inplace_archive`` replaces by name. That is fine for every mod this
project has handled so far (its docstring says as much -- "unique in practice,
verified for cyvadat.*"), and it is NOT fine here: SYW touches all 669 texture
names, 136 of them duplicated. Replacing by name would give one copy the HD
art and leave the others vanilla, silently.

So routing happens by TOC INDEX, never by name.

WHAT DECIDES WHICH ENTRY A FILE LANDS ON: CONTENT
=================================================
Build 225 routed by name, then by the mod's own folder against the conflict
table, and it was WRONG in a way that is invisible in a log and glaring on a
TV. The mod's folders do not correspond to the archive's. Scoring magic.lgp's
five ``exp_1.tex`` entries against the six ``exp_1_NN.dds`` the mod ships:

    entry (conflict folder)   beata   beast   tifa1   alexand   weapon3
    1214  limit2/beast        100.0%   12.6%   12.6%    42.9%     85.0%
    1252  summon/alexand       51.6%   14.6%   14.6%    99.1%     71.5%
    1269  (no folder)          55.3%   20.5%   20.5%    34.5%     99.4%

Entry 1214's own conflict folder says ``beast``; the file that actually
belongs to it is ``beata``. Build 225 believed the folder and clipped ONE
full-frame explosion through a 4x4 animation ATLAS, so every animation frame
drew a different rectangular crop of the same picture -- the "rectangles of
texture, abrupt edges" that build reported.

So names and folders only PROPOSE candidates now. `agreement()` decides,
by asking whether the mod leaves empty what the game does not draw: effect
art is a silhouette on a black field, so the vanilla KEYED region and the
mod's BLACK region are the same shape when, and only when, they are the same
picture. It separates right from wrong by 99% against 12-55%.

A texture with no keyed region at all -- ``ice``, ``cloud_2``, ``magic_a``:
an ice sheet, a sky, a summon circle -- cannot be scored and does not need to
be: there is no silhouette for a wrong pairing to break. Those are accepted
unranked, with the mod's folder as a tie-break. Luminance correlation was
tried as a fallback and is worthless here, because SYW REDRAWS rather than
upscales: a correctly paired ice sheet correlates at ~0 with its own slot.

This is the lesson ``battle_bg_dds_map.json`` already encodes -- that table
was "built once by perceptual content-matching", not by trusting names.

PARTIAL PALETTE SETS ARE REFUSED, NOT PATCHED
=============================================
A paletted TEX has ONE index bitmap shared by all its palettes. If the mod
supplies only some of them, the bitmap cannot be rebuilt without corrupting
the palettes it did not supply. ``ff7nx_ddstex.convert_group`` refuses, and
the entry stays vanilla. SYW is never short (it ships MORE palettes than the
Switch archive declares for 192 stems, never fewer); the check exists so that
a mod which IS short cannot quietly damage an archive.

SIZE
====
``SEVENTH_NX_SPELL_TEX_CAP`` (px). 0/unset means native dimensions. A positive
value selects the largest integer canvas supplied by SYW that fits the cap.
Most art reaches 3x at a 768px cap; a real per-axis canvas may be 3x6, and two
``koudan02`` groups are only supplied at native resolution. Every enlarged TEX
carries its own X/Y logical scale, so the renderer continues to interpret the
unchanged spell coordinates in native texels.
"""
from __future__ import annotations

import hashlib
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_ddstex
import tex

from collections import Counter as collections_Counter

CAP_ENV = 'SEVENTH_NX_SPELL_TEX_CAP'      # every mapped SYW texture
FX_CAP_ENV = 'SEVENTH_NX_SPELL_FX_CAP'    # retired settings compatibility

#: 3,600 bytes of lookup table sit before the conflict table in Archive.middle.
LOOKUP_TABLE_LEN = 3600


#: Retained as a compatibility name for older outside imports. The 256px size
#: and forced-1x256-layout conclusions were both retracted; the real failure
#: was the physical/logical texel mismatch corrected by ff7nx_spelluv.
_RETRACTED_CEILING_NOTE = True

#: Historical compatibility value only. ``cap()`` deliberately ignores it.
BATTLE_TEX_CEILING = 256


def cap():
    """Pixel ceiling for spell textures. 0 means native (vanilla size).

    The converter enforces this through each texture's actual dimensions and
    source canvas. ``BATTLE_TEX_CEILING`` is retained only as an unused legacy
    import name.
    """
    raw = os.environ.get(CAP_ENV, '').strip()
    if not raw:
        return 0
    try:
        val = int(raw)
    except ValueError:
        return 0
    return val if val > 0 else 0


# ------------------------------------------------------- the conflict table

def conflict_folders(archive):
    """``{toc index (0-based): folder}`` from the archive's conflict table.

    Read-only. ``Archive.middle`` stays the verbatim blob it already is; this
    only interprets it, so nothing here can change what gets written back.
    Returns ``{}`` for an archive with no conflict table rather than raising --
    an archive without duplicates simply has nothing to disambiguate.
    """
    blob = archive.middle[LOOKUP_TABLE_LEN:]
    if len(blob) < 2:
        return {}
    out = {}
    try:
        count = struct.unpack_from('<H', blob, 0)[0]
        off = 2
        for _ in range(count):
            n = struct.unpack_from('<H', blob, off)[0]
            off += 2
            for _ in range(n):
                name = blob[off:off + 128].split(b'\0')[0]
                off += 128
                idx = struct.unpack_from('<H', blob, off)[0]
                off += 2
                if idx:
                    out[idx - 1] = name.decode('latin1').lower().replace(
                        '\\', '/')
        if off != len(blob):
            # A trailing-byte mismatch means the layout is not what we think.
            # Refuse the whole map rather than route on a half-parsed table.
            return {}
    except (struct.error, IndexError):
        return {}
    return out


# --------------------------------------------------------------- routing

def _parse_dds_name(base):
    """``('smoke', 0)`` for ``smoke_00.dds``; None if it has no palette
    suffix. The split is greedy so ``alex2_00_01.dds`` reads as palette 1 of
    ``alex2_00``, which is the entry that actually exists."""
    if not base.lower().endswith('.dds'):
        return None
    stem = base[:-4]
    cut = stem.rfind('_')
    if cut <= 0:
        return None
    tail = stem[cut + 1:]
    if not tail.isdigit():
        return None
    return stem[:cut].lower(), int(tail)


# ------------------------------------------------- CONTENT MATCHING
#
# BUILD 225. Name and folder routing is NOT reliable, and the failure is not
# subtle -- it silently pairs a texture with a completely different picture.
#
# magic.lgp holds five `exp_1.tex` entries. Scoring every one against every
# `exp_1_NN.dds` the mod ships, by how much of the vanilla KEYED region the
# mod also leaves empty:
#
#   entry (conflict folder)   beata   beast   tifa1   alexand   weapon3
#   1214  limit2/beast        100.0%   12.6%   12.6%    42.9%     85.0%
#   1252  summon/alexand       51.6%   14.6%   14.6%    99.1%     71.5%
#   1269  (no folder)          55.3%   20.5%   20.5%    34.5%     99.4%
#
# The right answer is unambiguous -- 99-100% against 12-55% -- and for entry
# 1214 it is the `beata` file, NOT the `beast` one its own conflict folder
# names. Build 225 took the folder's word for it and clipped a single
# full-frame explosion through a 4x4 animation atlas, which is the "rectangles
# of texture" that build reported.
#
# This is the same lesson `battle_bg_dds_map.json` already encodes: that table
# was "built once by perceptual content-matching", not by trusting names. So
# names and folders now only propose candidates; content decides.
DARK = 12               # luminance at or below this is "nothing is drawn here"
MIN_AGREEMENT = 0.80    # below this the pairing is refused, not shipped

#: The gate is relative to the best candidate for a texture NAME, with an
#: absolute floor. See the comment at `cutoff` in route().
SHAPE_TOLERANCE = 0.10
ABS_SHAPE_FLOOR = 0.60

#: How much of the canvas the keyed region has to cover before it counts as
#: a SILHOUETTE worth comparing. Below this there is no shape in it.
#:
#: BUILD 239. `break_a.tex` is 0.02% keyed -- thirteen texels of 65,536 --
#: and `agreement` was scoring the mod's whole 1024px picture on whether
#: those thirteen came out dark. They did not, so it returned 0.000 and the
#: art was refused as "content does not match". That is not a mismatch, it
#: is a comparison with nothing in it, and 0% agreement is the tell: a real
#: mismatch scores low but non-zero.
#:
#: The distribution says where to put it. 117 textures have NO keyed region
#: at all and already return None; 8 more are under 0.1%, 23 more under 1%,
#: and then it climbs smoothly. So 1% sits in the gap, and the guard is
#: symmetric -- a texture that is 99% keyed has under 1% DRAWN, which is
#: just as empty a shape to match on.
#:
#: Falling below it returns None, which route() already handles: acceptable,
#: ranked below any real content match, with the mod's own conflict folder
#: breaking the tie. It does NOT lower a threshold -- see the warning in
#: HANDOFF-238 section 3. Nothing that has a silhouette is judged any more
#: leniently than before.
MIN_SILHOUETTE = 0.01


def _keyed_mask(vanilla):
    """(parsed tex, bool mask of the texels the game does not draw)."""
    v = tex.parse(vanilla)
    if v is None or not v['palette_flag'] or v['bytes_per_pixel'] != 1:
        return None, None
    n, c = v['num_palettes'], v['colors_per_palette']
    pal = _np().frombuffer(v['palette'], dtype='uint8')[:n * c * 4]
    if pal.size < n * c * 4:
        return v, None
    pal = pal.reshape(n, c, 4)
    key = [k for k in range(c) if pal[0][k][3] == 0]
    if struct.unpack_from('<I', vanilla, tex.O_COLORKEY)[0] == 1 and 0 not in key:
        key = [0] + key
    if not key:
        return v, None
    idx = _np().frombuffer(v['pixels'], dtype='uint8').reshape(
        v['height'], v['width'])
    return v, _np().isin(idx, _np().asarray(key, dtype='uint16'))


def _np():
    import numpy
    return numpy


def drawn_mask(vanilla):
    """(parsed tex, bool mask of the texels that actually show something).

    BUILD 229. `_keyed_mask` only works for textures with a keyed palette
    index, and 396 of the replaced ones have none -- `beam_3.tex` among them,
    which shipped at 768x768 from a 128x128 slot and drew as hard squares.
    For those, transparency is not an index at all: the art is drawn on BLACK
    and the effect blends additively, so black is what does not show.

    That distinction matters for the palette (a keyed index and a black
    texel are not interchangeable, and `convert_group` still treats them
    separately) but NOT for analysis. For deciding whether two pictures are
    the same, and whether a texture is one sprite or a sheet of frames, the
    question is only "where does this texture show something", and black
    answers it exactly as well as a colour key.

    So: keyed region when there is one, non-black region when there is not.
    """
    np = _np()
    v, mask = _keyed_mask(vanilla)
    if v is None:
        return None, None
    if mask is not None and mask.any():
        return v, ~mask
    n, c = v['num_palettes'], v['colors_per_palette']
    pal = np.frombuffer(v['palette'], dtype='uint8')[:n * c * 4]
    if pal.size < n * c * 4:
        return v, None
    pal = pal.reshape(n, c, 4)
    idx = np.frombuffer(v['pixels'], dtype='uint8').reshape(
        v['height'], v['width'])
    lum = pal[0][:, [2, 1, 0]][idx].max(axis=2)
    return v, lum > DARK


#: How uniform the 1px border has to be before it counts as the art's own
#: background colour, and how far a texel may sit from it and still count as
#: background.
BG_BORDER_SHARE = 0.90
BG_TOLERANCE = 24


def _mod_background(img):
    """The art's own background colour, or None if it does not have one.

    BUILD 239 -- WHY THIS EXISTS. `agreement` asked "is the mod DARK where
    the game keys out", because effect art is drawn on black and blended
    additively. True for most of SYW's magic set and false for a visible
    minority: the tarot and slot cards (`club`, `dia`, `spade`, `heart`, ...)
    are drawn on a flat LIGHT GREY field, and `flea_1` on near-white.

    For those the old test could not return anything but zero -- not one
    texel of the keyed region is dark -- so a perfectly good card scored
    0.000 and was reported as "content does not match any candidate entry".
    That is the 0% signature HANDOFF-238 section 3 flagged: a real mismatch
    scores low but non-zero, and an exact zero means the comparison itself
    had nothing in it.

    The background is read from the art, never from the vanilla entry it is
    being compared against -- deriving it from the thing under test would
    make the test pass by construction. The 1px border's modal colour, at
    5-bit precision, is unanimous (share 1.00) on every case measured.

    Returns None for black backgrounds, which is the common case and needs
    no special handling: DARK already covers it.
    """
    np = _np()
    if img.shape[0] < 8 or img.shape[1] < 8:
        return None
    edge = np.concatenate([img[0, :, :3], img[-1, :, :3],
                           img[:, 0, :3], img[:, -1, :3]])
    q = (edge >> 3).astype(np.int32)
    key = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    vals, counts = np.unique(key, return_counts=True)
    top = int(counts.argmax())
    if counts[top] < BG_BORDER_SHARE * key.size:
        return None                       # no single background colour
    k = int(vals[top])
    bg = np.array([(k // 1024) << 3, ((k // 32) % 32) << 3, (k % 32) << 3],
                  dtype=np.int16)
    if int(bg.max()) <= DARK:
        return None                       # black: DARK already covers it
    return bg


def agreement(vanilla, dds_bytes, _cache={}):
    """How well one DDS matches one vanilla entry, in 0..1.

    Primary signal: the mod must leave empty what the game does not draw.
    Effect art is a silhouette on a flat field, so the keyed region of the
    vanilla texture and the background region of the mod's art are the same
    shape when -- and only when -- they are the same picture. It separates
    right from wrong pairings by 99% vs 12-55%, which is not a close call.

    "Background" is black for most of the set and the art's own flat colour
    where it has one -- see `_mod_background`. A detected non-black
    background is only honoured when it does NOT also swallow the vanilla's
    DRAWN region, because a background that covers everything is not a
    silhouette, it is a way of scoring 1.0 against anything.

    A texture with no usable silhouette returns None rather than a score.
    """
    np = _np()
    v, drawn = drawn_mask(vanilla)
    mask = None if drawn is None else ~drawn
    if v is None:
        return 0.0
    key = id(dds_bytes)
    hit = _cache.get(key)
    if hit is None or hit[0] is not dds_bytes:
        img, _w, _h = ff7nx_ddstex._decode(dds_bytes)
        hit = (dds_bytes, img, _mod_background(img))
        if len(_cache) > 64:
            _cache.clear()
        _cache[key] = hit
    if mask is None or mask.mean() < MIN_SILHOUETTE \
            or mask.mean() > 1.0 - MIN_SILHOUETTE:
        # NOTHING TO JUDGE ON, and that is not a failure.
        #
        # A fully opaque texture -- `ice`, `cloud_2`, `magic_a`: an ice sheet,
        # a sky, a summon circle -- has no keyed region, so there is no
        # silhouette to compare and no way for a wrong pairing to show itself
        # as a hard rectangle either. Scoring these on luminance correlation
        # was tried and is worthless: SYW redraws rather than upscales, so a
        # correctly paired ice sheet correlates at ~0 with its own vanilla
        # slot. Refusing on that threw away 16% of the mod for no reason.
        #
        # Return None, meaning "acceptable, but do not rank it above a real
        # content match".
        return None
    img = ff7nx_ddstex._resample(hit[1], v['width'], v['height'])
    lum = img[:, :, :3].max(axis=2).astype('int16')
    empty = lum <= DARK
    bg = hit[2]
    if bg is not None:
        near = (np.abs(img[:, :, :3].astype('int16') - bg[None, None, :])
                .max(axis=2) <= BG_TOLERANCE)
        # VETO. If the art's flat colour also covers the vanilla's drawn
        # region there is no shape in the comparison and the ordinary
        # black-only test is the honest one.
        if float((empty | near)[drawn].mean()) < 0.5:
            empty = empty | near
    return float(empty[mask].mean())


# ------------------------------------------------- EFFECT FRAME TABLES
#
# `magic.lgp` holds 806 `.s` entries and they are what actually samples an
# effect texture. Each is an 8-byte header (magic 0x2023, record count) then
# N 24-byte records, and each record names ONE animation cell by ABSOLUTE
# TEXEL coordinate:
#
#     hit1.s   U = 0, 64, 128, 192 at V = 0, then again at V = 64, cell 64x64
#
# So the texture's size is part of the data contract. Scale the texture and
# leave the table alone and "the 64x64 cell at texel (192,64)" names a
# quarter of the intended frame -- build 224-229's discontinuous squares.
#
# The table can be scaled WITH the texture, and that is what makes upscaling
# possible at all. Two limits decide when:
#
#   * U and V are u16, so they scale freely;
#   * the cell width/height are u8. 1,645 frames are 64x64 and 872 are 32x32
#     -- those double fine -- but 46 frames are 128x127 and some reach 255,
#     and those cannot. A texture whose table would overflow is not scaled.
#
# The table is matched to its texture BY NAME (`baku1.s` -> `baku1.tex`),
# confirmed by the extent fitting the texture exactly: a_max 128x256,
# baku1 256x256, awa_1 64x63 into 64x64.
S_MAGIC = 0x2023
S_HEADER = 8          # magic, record count
S_REC_HEAD = 4        # u16 flag, u16 sub-entry count
S_SUB = 20            # one animation cell

#: Byte offsets inside a 20-byte sub-entry.
S_SUB_X = 4           # i16 x, i16 y   -- quad origin, in EIGHTHS of a unit
S_SUB_U = 8           # u16 U (uScale), u16 V (vScale)
S_SUB_W = 16          # u16 field_10 -- LOW BYTE is the extent width
S_SUB_H = 18          # u16 field_12 -- LOW BYTE is the extent height
S_SUB_CELL = S_SUB_W  # compatibility alias; prefer S_SUB_W / S_SUB_H

# ---- WHAT THE RECORD IS, FROM THE ENGINE'S OWN CODE ----------------------
#
# FINDINGS-257. Read out of FFNx's decompilation of the exact function that
# consumes this record -- `repos/FFNx-master/src/ff7/battle/animations.cpp`,
# the `page_spt_ptr` loop -- rather than inferred from the data:
#
#     x_left      = 8 * spt->field_4              quad origin x
#     y_top       = 8 * spt->field_6              quad origin y
#     quad_width  = 8 * (byte)spt->field_10       quad WIDTH   <- byte at +16
#     quad_height = 8 * (byte)spt->field_12       quad HEIGHT  <- byte at +18
#
#     u_left  = spt->uScale * obj->u_offset + obj->u_offset / 2.0
#     u_right = u_left + obj->u_offset * ((byte)spt->field_10 - 1)
#
# Three things follow, and each one corrects a belief this project has been
# building on since build 230:
#
#   1. THE HEIGHT IS AT +18, NOT +17. `field_10` and `field_12` are two bytes
#      apart, and the code takes the LOW BYTE of each. Bytes +17 and +19 are
#      high bytes it masks away. We have been reading the height from +17,
#      which differs from +18 in 2,597 of the 7,178 sub-entries -- so every
#      WHOLE / SUBRECT / FOREIGN decision was made partly on a wrong number.
#      `exp_1` is the proof: max(V + byte17) = 447 against a 256-tall texture,
#      while max(V + byte18) = 256 exactly.
#
#   2. THE EXTENT BYTE IS THE QUAD SIZE. It sets `quad_width` directly, and
#      it sets the UV span. Multiplying it does not move a texture
#      coordinate -- it makes the sprite that many times bigger ON SCREEN.
#      That is build 234's "the fx texture repeats itself in MORE OF THE
#      SCREEN than it should be", and it means **the `.s` tables must never
#      be rescaled**, at any factor, for any texture.
#
#   3. THE TEXTURE'S SIZE ENTERS IN EXACTLY ONE PLACE: `obj->u_offset`, at
#      +0x24 of the graphics object. The record itself carries no size. So
#      upscaling an effect texture is not an archive problem at all -- it is
#      one float per drawable, and no classification of textures into WHOLE,
#      SUBRECT or FOREIGN can substitute for it.
#
# There was never a real "must stay vanilla" rule. There was a reciprocal
# nobody had located.

# ---- WHAT +16 AND +18 ACTUALLY ARE, MEASURED -----------------------------
#
# FINDINGS-256. Every build since 230 has treated bytes +16..+19 as one
# 4-byte cell rectangle and multiplied all four by the scale factor. They are
# TWO rectangles in TWO DIFFERENT SPACES:
#
#     +16  u8 w, u8 h    the SOURCE rectangle, in page texels
#     +18  u8 w, u8 h    the DESTINATION size, in SCREEN units
#
# Three measurements over all 7,178 sub-entries in `magic.lgp`, none of which
# survives the one-rectangle reading:
#
#   * `max(U + w)` over +16 equals the texture's size EXACTLY -- sky_2
#     256x256 into a 256x256 file, kiri_2 256x128 into 256x128. The same sum
#     over +18 gives 256x253 and 256x127, which match nothing. +16 is the
#     rectangle that addresses the file.
#
#   * `elec3` has +16 = 16x16 and +18 = 248x248 while `elec3.tex` is 64 wide.
#     248 does not fit a 64-wide source, so +18 is not a source rectangle.
#     It is a 16x16 patch stretched over a 248x248 area of the screen.
#     `bimu11` does the same at 16x16 -> 240x240 from x=-8, y=-240.
#
#   * 848 of 7,178 sub-entries (11.8%) have the two rectangles differing by
#     more than rounding, spread across every flag value -- no bit selects
#     the behaviour. The 1:1 majority is why the fields look like duplicates.
#
# THIS IS WHY EVERY ATTEMPT TO SCALE THE TABLES LOOKED CATASTROPHIC. Scaling
# +18 by k does not move a texture coordinate: it makes the sprite k times
# BIGGER ON SCREEN. Build 234's hardware report was
#
#     "I see the fx texture repeat itself in MORE OF THE SCREEN than it
#      should be"
#
# -- more of the screen. That is a destination quad tripled in size, reported
# exactly, and it was read as a UV wrap and used to conclude the tables can
# never move. They can. The destination must simply be left alone.
#
# 32 of the 357 tables already draw at least one cell magnified 2x or more,
# and `kemu1` -- Odin's smoke -- blows a 32x32 source up to 128x127 on
# screen. A 4x magnification of a 32x32 patch is the "low quality, abrupt
# edges" that has been reported on those clouds from the beginning.


def _iter_subs(payload):
    """Yield the file offset of every 20-byte sub-entry, or None if the file
    is not a frame table.

    THE RECORD IS VARIABLE LENGTH, and getting that wrong cost build 232.
    A record is `u16 flag, u16 count` followed by `count` sub-entries. When
    every record holds exactly one, a record is 24 bytes -- which is what the
    first decode assumed, and it parsed 685 of 806 files. The other 121 have
    multi-entry records, so they were skipped entirely: their textures were
    scaled and their frame tables were not, which is precisely the mismatch
    this module exists to prevent. All 806 parse with the real layout.
    """
    if len(payload) < S_HEADER:
        return None
    magic, n = struct.unpack_from('<II', payload, 0)
    if magic != S_MAGIC or n <= 0 or n > 4096:
        return None
    off = S_HEADER
    out = []
    for _ in range(n):
        if off + S_REC_HEAD > len(payload):
            return None
        _flag, cnt = struct.unpack_from('<HH', payload, off)
        if cnt <= 0 or cnt > 256:
            return None
        off += S_REC_HEAD
        if off + cnt * S_SUB > len(payload):
            return None
        for _i in range(cnt):
            out.append(off)
            off += S_SUB
    return out if off == len(payload) else None


def parse_frame_table(payload):
    """[(U, V, w, h)] for every animation cell, or None."""
    offs = _iter_subs(payload)
    if offs is None:
        return None
    out = []
    for o in offs:
        u, v = struct.unpack_from('<HH', payload, o + S_SUB_U)
        # The height is the LOW BYTE OF +18, not the byte at +17. See
        # FINDINGS-257 in the layout notes above; +17 is the high byte of
        # field_10 and the engine masks it off.
        out.append((u, v, payload[o + S_SUB_W], payload[o + S_SUB_H]))
    return out


#: Rewrite `.s` frame tables when the FX cap is raised. OFF -- see below.
FX_TABLES_ENV = 'SEVENTH_NX_SPELL_FX_TABLES'

# The two V35 switches are RETRACTED (FINDINGS-257). `dest_hold` rested on
# reading +18 as a destination size; +18 is the extent HEIGHT. `cell_split`
# cut cells to beat a u8 ceiling that was never what held the textures back.
# Both are gone rather than left as options, because an option nobody should
# ever set is a trap for the next session.


def fx_tables():
    """False. `.s` frame tables are never rescaled. FINDINGS-257.

    The engine computes the sprite's on-screen size from the same extent byte
    the table uses for its cell:

        quad_width  = 8 * (byte)spt->field_10      (the byte at +16)
        quad_height = 8 * (byte)spt->field_12      (the byte at +18)

    -- read out of FFNx's decompilation of the function that consumes the
    record, `ff7/battle/animations.cpp`, not inferred from the archive. So
    multiplying an extent does not move a texture coordinate, it makes the
    sprite that many times BIGGER ON SCREEN. Build 234 reported exactly that
    ("the fx texture repeats itself in MORE OF THE SCREEN than it should be")
    and it was misread as a UV wrap for twenty builds.

    Build 249's V35 tried again on the reading that +18 was a separate
    destination size. It is the HEIGHT. On hardware Odin drew as disconnected
    bands of cloud.

    There is no factor and no texture for which rescaling a table is right.
    The texture's size reaches the draw in exactly one place -- `u_offset` at
    +0x24 of the graphics object -- and that is where the fix belongs.

    The old environment name remains import-compatible but cannot re-enable
    the destructive archive rewrite.
    """
    return False


def scale_frame_table(payload, k, split=False, hold=True):
    """The SOURCE rectangle multiplied by `k`, or None if it cannot be.

    The destination at +18 is left alone (`dest_hold`) and a source cell too
    big for the u8 extent is cut into pieces (`cell_split`). Both are
    FINDINGS-256; the module header explains what the two rectangles are.

    U and V are u16 and the largest vanilla value is 248, so the coordinate
    fields have room for any factor. The EXTENT bytes are the problem, and
    not because they are u8.

    NO CORRECT USE OF THIS FUNCTION EXISTS -- FINDINGS-257. The extent byte
    is the quad size (`quad_width = 8 * (byte)field_10`), so scaling it
    enlarges the sprite on screen instead of moving a texture coordinate.
    It is kept, refusing rather than clamping, only so the archive-side
    machinery stays testable. `fx_tables()` is off and no setting of it is
    right; the real fix is `obj->u_offset` in the binary.

    Build 236 and earlier CLAMPED the extent to 255 and carried on. That is
    the one thing not to do: the cell no longer covers its sprite, the table
    reports success, and the mismatch shows up on a TV with nothing in the
    log. Refusing is the honest answer.
    """
    if k == 1:
        return payload
    if split:
        raise ValueError('cell splitting is retracted -- FINDINGS-257')
    offs = _iter_subs(payload)
    if offs is None:
        return None
    out = bytearray(payload)
    for o in offs:
        u, v = struct.unpack_from('<HH', payload, o + S_SUB_U)
        if max(u * k, v * k) > 0xFFFF:
            return None
        struct.pack_into('<HH', out, o + S_SUB_U, u * k, v * k)
        for j in (S_SUB_W, S_SUB_H):
            cell = payload[o + j] * k
            if cell > 0xFF:
                return None
            out[o + j] = cell
    return bytes(out)


def fx_cap():
    """Pixel ceiling for FX textures. 0 (default) means vanilla size."""
    raw = os.environ.get(FX_CAP_ENV, '').strip()
    if not raw:
        return 0
    try:
        val = int(raw)
    except ValueError:
        return 0
    return val if val > 0 else 0


def fx_scale():
    """Historical separate-FX diagnostic factor; production does not use it.

    Resized mapped textures now use ``uniform_scale`` and carry their actual
    per-axis scale in the TEX marker.  The `.s` tables remain native.
    """
    px = fx_cap()
    return max(1, px // 256) if px else 1


PALETTE_BUDGET_ENV = 'SEVENTH_NX_SPELL_PALETTE_BUDGET_MB'

#: Megabytes of decoded RGBA a single TEX may cost across ALL its palettes.
#: 0 disables the budget and restores the flat uniform scale.
#: 24 rather than 16 deliberately: at 16 MB `kujata00/01` fall all the way to
#: 1x, and holding art at vanilla size is not an acceptable outcome here. 24
#: is the smallest budget at which NOTHING lands at 1x.
PALETTE_BUDGET_MB = 24


def palette_budget_bytes():
    """Per-TEX ceiling on decoded surface memory, palettes included.

    THE COST OF A TEX IS NOT ITS PIXELS. It is pixels x palettes. A paletted
    TEX with `num_palettes` > 1 is an ANIMATION: the indices are shared and
    each palette is a different frame, so the decoded form is one full-size
    RGBA surface per palette. Scaling k x multiplies that by k^2 across the
    whole set at once.

    ```
    vanilla 256x256            1 palette   0.2 MB      17 palettes   4.2 MB
    at 4x (1024x1024)          1 palette   4.0 MB      17 palettes  68.0 MB
    ```

    MEASURED AGAINST WHAT THE SCREEN DID. Bahamut ZERO's landing is
    `atomic00..03`, twelve palettes each: 4 x 48 MB = 192 MB at 4x against a
    256 MB pool, and 4 x 27 = 108 MB at 3x. Kujata is `kujata00/01` at
    SEVENTEEN palettes: 136 MB at 4x, 76 MB at 3x. On hardware at 3x Bahamut
    ZERO was flawless and Kujata -- summoned straight after it -- corrupted.
    At 4x both corrupted. That is dose-dependent on k^2 x palettes, in the
    order the effects were played, which is a memory ceiling and not a layout
    or a UV defect. `moon` is one palette, 4 MB at 4x, and has been correct
    throughout.

    ff7nx_texcache's own record says the same failure mode out loud: "16.5 MB
    of dead weight in vanilla becomes 137.6 MB here, out of a 256 MB pool.
    CONFIRMED ON HARDWARE: with the cache off, the texture corruption stops."
    That bound was sized when every texture was <= 256px. It cannot help here,
    because `small` mode only ever caches surfaces <= 256x256 -- these are
    never cached at all, so nothing is hoarding them. The pressure is live
    peak, not residency.

    SO THE SCALE IS PER TEXTURE, NOT PER ARCHIVE. Single-palette art -- 575 of
    649 entries, `moon` among them -- keeps the full 4x. The palette-heavy
    animations take the largest k their own frame count can afford. Nothing
    is held at vanilla size to buy headroom for something else, and at the
    default budget nothing lands at 1x at all.

    ```
    budget   4x    3x    2x    1x     worst single TEX
      16 MB  533    67    39    10          16.0 MB   <- kujata falls to 1x
      24 MB  575    45    29     -          24.0 MB   <- default
      32 MB  602    34    13     -          32.0 MB
      off    649     -     -     -          96.0 MB   <- build 253, corrupts
    ```

    For reference, the 3x archive that ran Bahamut ZERO flawlessly and only
    corrupted on the summon after it peaked at 4 x 27 = 108 MB for that one
    effect. The default budget puts the same effect at 4 x 12 = 48 MB.
    """
    raw = os.environ.get(PALETTE_BUDGET_ENV, '').strip()
    mb = PALETTE_BUDGET_MB
    if raw:
        try:
            mb = int(raw)
        except ValueError:
            mb = PALETTE_BUDGET_MB
    return max(0, mb) * 1024 * 1024


def budgeted_scale(vanilla, k):
    """Largest scale <= k whose whole palette set fits the budget.

    Returns `k` unchanged when the budget is off, when the TEX cannot be
    parsed, or when even 1x exceeds it -- a vanilla-sized entry is never made
    smaller, since its cost is what the stock game already pays.
    """
    budget = palette_budget_bytes()
    if not budget or k <= 1:
        return k
    t = tex.parse(vanilla)
    if not t:
        return k
    cell = t['width'] * t['height'] * 4 * max(1, t['num_palettes'])
    for cand in range(k, 1, -1):
        if cell * cand * cand <= budget:
            return cand
    return 1


def uniform_scale():
    """Requested maximum integer scale for mapped spell TEX canvases.

    Each conversion may clamp this independently to the source resolution.
    The actual X/Y factor is recorded in that TEX and consumed by the native
    logical-step bridge.  No frame table is rescaled.
    """
    px = cap()
    if not px:
        return 1
    # magic.lgp's largest vanilla texture is 256px, so the cap IS the factor.
    return max(1, px // 256)


def upscale_vanilla_tex(payload, k):
    """Enlarge a paletted TEX by replicating its index bytes k x k.

    Exact: the palette is untouched and every source texel becomes a k x k
    block of the same index, so the picture is identical, just bigger. Used
    for the textures the mod does not supply, because UNIFORM scaling needs
    every texture in the archive to move by the same factor -- see convert().
    """
    if k == 1:
        return payload
    np = _np()
    t = tex.parse(payload)
    if t is None:
        return None
    w, h, bpp = t['width'], t['height'], t['bytes_per_pixel']
    if bpp < 1:
        return None
    # Paletted (1 byte per texel) and truecolor (2/3/4) alike: replicate whole
    # PIXELS, whatever their width. `ground_1` and `rock_1` are 16-bit with no
    # palette, and an earlier version silently skipped them -- leaving two
    # textures at 1x while their frame tables went to 2x.
    idx = np.frombuffer(t['pixels'], dtype='uint8').reshape(h, w * bpp)
    if bpp == 1:
        big = idx.repeat(k, axis=0).repeat(k, axis=1)
    else:
        big = idx.reshape(h, w, bpp).repeat(k, axis=0).repeat(k, axis=1)
    hdr = bytearray(payload[:tex.HEADER_LEN])
    struct.pack_into('<I', hdr, tex.O_WIDTH, w * k)
    struct.pack_into('<I', hdr, tex.O_HEIGHT, h * k)
    pitch = struct.unpack_from('<I', payload, tex.O_PITCH)[0]
    if pitch:
        struct.pack_into('<I', hdr, tex.O_PITCH, pitch * k)
    out = bytes(hdr) + payload[tex.HEADER_LEN:
                               tex.HEADER_LEN + t['palette_size'] * 4] \
        + big.tobytes()
    chk = tex.parse(out)
    if chk is None or (chk['width'], chk['height']) != (w * k, h * k):
        return None
    return out


def texture_classes(archive):
    """Historical classifier retained for audits; production does not gate on it.

    Returns ``{toc index: 'model' | 'fx' | 'both' | 'unknown'}``.  The text
    below records the route that led to the retired vanilla-size workaround;
    :func:`convert` now resizes every content-matched target under one cap and
    relies on the native logical reciprocal instead.

    WHICH TEXTURES MAY BE RESIZED, AND WHY. This is the distinction builds
    224-234 never drew, and it is the reason they kept regressing.

    ``magic.lgp`` holds two kinds of texture, drawn by two paths with
    different UV semantics:

      MODEL   named by an `.rsd`, so it is bound to a `.p` mesh. FF7 `.p`
              geometry stores texture coordinates as FLOATS in 0..1 --
              verified, max |UV| 0.99 across all 574 parseable `.p` files.
              The texture's size appears nowhere, so resizing is free. This
              is exactly why char.lgp's field models already ship at 512px.

      FX      addressed by a `.s` frame table, which names each animation
              cell by ABSOLUTE TEXEL (hit1.s: U = 0, 64, 128, 192, cell
              64x64). The size is part of the data. Resizing breaks it.

      UNREF   named by NOTHING in the archive. No `.rsd` binds it to a mesh
              and no `.s` -- under any spelling -- indexes it. Build 236 held
              these at vanilla and that was the wrong call: `unknown` was
              being read as "might be fx" when the evidence says the exact
              opposite. Nothing in `magic.lgp` addresses them by texel, so
              there is no texel to invalidate. They follow the MODEL cap.

    TWO CORRECTIONS THIS FUNCTION EXISTS TO CARRY (build 237):

    1. THE `.s` IS NAMED AFTER THE EFFECT, NOT THE TEXTURE. `blaver.s` drives
       `blaver00.tex` and `blaver01.tex`; there is no `blaver00.s`. Matching
       stem-for-stem missed every numbered series -- 30 textures, Cloud's
       BRAVER among them. Worse, 12 of those (a1..a8, coin1, coin2, ...) also
       have an `.rsd`, so they were classed `model` and SCALED at 3x while
       their frame table stayed at 1x. That is the texel mismatch this module
       exists to prevent, shipped in build 236 as a live defect.

    2. THE ARCHIVE NAMES NOTHING. Searched every entry: 310 of the 339
       "unknown" textures appear nowhere in any non-texture payload, and no
       texture name appears in `main`, `sdk` or `subsdk0-2` either. The
       binding is by TOC INDEX, which is also why the archive tolerates
       hundreds of duplicate names. So "unreferenced" is a positive result --
       it means no data structure encodes the size -- not an absence of
       evidence.

    Measured over 1,133 texture entries after both corrections: 530 model,
    177 fx, 93 both, 23 unknown-but-.s-adjacent, 310 unreferenced.

    The final class-to-cap policy described above is historical and is not
    consulted by production conversion.
    """
    import re
    rsd = set()
    for e in archive.entries:
        if not e['name'].lower().endswith('.rsd'):
            continue
        for m in re.finditer(rb'TEX\[\d+\]\s*=\s*([^\r\n]+)', e['payload']):
            rsd.add(m.group(1).decode('latin1').strip().lower()
                    .replace('.tim', '.tex'))

    # Every stem that has a real frame table, matched two ways: exactly, and
    # against the texture stem with its trailing frame number removed. See
    # correction 1 in the docstring -- the second form is what catches
    # blaver.s -> blaver00.tex.
    s_stems = {e['name'].lower().rpartition('.')[0]
               for e in archive.entries
               if e['name'].lower().endswith('.s')
               and parse_frame_table(e['payload']) is not None}

    # Anything a non-texture entry mentions by name is referenced by SOMETHING
    # whose semantics are unknown, so it does not get the unref promotion.
    named = b'\x00'.join(e['payload'] for e in archive.entries
                         if tex.parse(e['payload']) is None).lower()

    out = {}
    for i, e in enumerate(archive.entries):
        if tex.parse(e['payload']) is None:
            continue
        n = e['name'].lower()
        stem = n.rpartition('.')[0]
        r = n in rsd
        f = stem in s_stems or re.sub(r'[_]?\d+$', '', stem) in s_stems
        if r and f:
            out[i] = 'both'
        elif r:
            out[i] = 'model'
        elif f:
            out[i] = 'fx'
        elif stem.encode() not in named:
            out[i] = 'unref'
        else:
            out[i] = 'unknown'
    return out


def mesh_uv_textures(archive):
    """``{toc index}`` of textures whose consumer is a NORMALISED-UV mesh.

    BUILD 239. This is the measurement build 238's gate was a stand-in for,
    and it is the reason 168 textures were being held for no reason.

    Build 238 admitted a texture only if its drawn area was ONE connected
    sprite, on the argument that a packed sheet is carved up by absolute
    texel and therefore breaks when the canvas grows. That is true of a sheet
    carved by a `.s` frame table. It is NOT true of a sheet carved by a `.p`
    mesh, and 166 of the held textures are exactly that.

    THE MEASUREMENT, over the whole archive rather than a sample:

        574 `.p` files carry texture coordinates
        52,970 coordinate values in total
        min 0.00390625 (= 1/256)      max 0.9921875 (= 254/256)
        values above 1.0:  ZERO

    Every coordinate is a normalised float. Where the mesh has several quads
    they tile the canvas in the same normalised space -- `stop.tex` is a 4x4
    grid of 12 sprites and its `.p` puts UV vertices at texels 1, 37, 64, 90
    and 127 of 128, i.e. on the CELL BOUNDARIES, which is why no vertex lands
    inside a sprite and why a "does a UV point hit each blob" test reads as a
    miss. The mesh spans 0.98 of the canvas in both axes for every one of
    these files.

    A normalised coordinate names a FRACTION of the texture. Double the
    canvas and 0.25 is still one quarter across, which is still the same
    picture -- now with twice the texels. The cell boundaries stay on texel
    boundaries because k times an integer is an integer. There is nothing to
    invalidate, exactly as for `char.lgp`'s field models, and the sprite
    count is irrelevant.

    The same holds for the animated UV offset. FINDINGS-235 read FFNx's
    `battle/animations.cpp` -- `u_offset = (byte) * 0.00390625` -- as a size
    dependency, and it is not one: dividing a byte by a constant 256 yields a
    NORMALISED offset, added to normalised UVs. It scrolls by a fraction of
    the texture, so it lands on the same picture feature at any size. That
    retracts the blocker FINDINGS-235 section 2 put on this whole path.

    What this does NOT cover, and must not be read as covering: a texture no
    `.p` and no `.s` addresses (`unref`). Nothing in the archive describes
    those, the binding is by TOC index, and code holding a hardcoded texel
    rectangle would leave no trace -- FINDINGS-238 section 2 is right about
    them. They keep the single-sprite rule.

    Verified structurally, not by name alone: the `.rsd` must name the
    texture AND resolve to a `.p` that actually carries texture coordinates,
    all of them inside 0..1.
    """
    import re
    np = _np()
    # .p stem -> True when it carries texture coordinates and every one of
    # them is a normalised float. A .p that fails either test proves nothing
    # and does not license a resize.
    normalised = {}
    for e in archive.entries:
        name = e['name'].lower()
        if not name.endswith('.p'):
            continue
        d = e['payload']
        if len(d) < 0x80:
            continue
        try:
            nv, nn, nu1, ntc = struct.unpack_from('<4I', d, 0x0C)
        except struct.error:
            continue
        stem = name[:-2]
        if not (0 < ntc < 200000 and nv < 200000 and nn < 200000
                and nu1 < 200000):
            normalised[stem] = False
            continue
        off = 0x80 + nv * 12 + nn * 12 + nu1 * 12
        if off + ntc * 8 > len(d):
            normalised[stem] = False
            continue
        uv = np.frombuffer(d, dtype='<f4', count=ntc * 2, offset=off)
        good = bool(np.isfinite(uv).all() and uv.min() >= -0.001
                    and uv.max() <= 1.001)
        # A duplicate name that is NOT normalised vetoes the stem: the
        # archive cannot tell us which of the two a given .rsd meant.
        normalised[stem] = good and normalised.get(stem, True)

    # EVERY COPY OF THE NAME NEEDS ITS OWN MESH, not just one of them.
    #
    # magic.lgp holds hundreds of duplicate names -- `1.tex` five times,
    # `baku1.tex` seventeen -- and an `.rsd` names its texture by NAME, so a
    # single reference cannot say which copy it meant. Promoting all of them
    # off one reference is the build 237 mistake in miniature: a real fact
    # about one entry used to license the others.
    #
    # So the reference is COUNTED. A name is admitted only when at least as
    # many distinct `.rsd` entries reference it through a normalised `.p` as
    # there are textures carrying that name; then every copy has a mesh
    # whichever way the loader pairs them up. Measured: this covers 522 of
    # the 528 that the loose test would, and leaves 16 names partly covered
    # (`baku1` 2 references to 17 copies, `smoke_1` 1 to 11) which fall back
    # to the single-sprite rule.
    refs = {}
    for e in archive.entries:
        if not e['name'].lower().endswith('.rsd'):
            continue
        txt = e['payload'].decode('latin1', 'replace')
        ply = None
        texs = set()
        for line in txt.splitlines():
            line = line.strip()
            m = re.match(r'TEX\[\d+\]\s*=\s*(\S+)', line, re.I)
            if m:
                texs.add(m.group(1).lower().rpartition('.')[0])
            m = re.match(r'PLY\s*=\s*(\S+)', line, re.I)
            if m:
                ply = m.group(1).lower().rpartition('.')[0]
        if ply and normalised.get(ply):
            for t in texs:
                refs[t] = refs.get(t, 0) + 1

    copies = {}
    for e in archive.entries:
        if tex.parse(e['payload']) is None:
            continue
        stem = e['name'].lower().rpartition('.')[0]
        copies[stem] = copies.get(stem, 0) + 1

    out = set()
    for i, e in enumerate(archive.entries):
        if tex.parse(e['payload']) is None:
            continue
        stem = e['name'].lower().rpartition('.')[0]
        if refs.get(stem, 0) >= copies[stem]:
            out.add(i)
    return out


#: Cell classes returned by frame_cells(). See its docstring.
CELL_WHOLE = 'whole'
CELL_SUBRECT = 'subrect'
CELL_FOREIGN = 'foreign'

#: How much of a texture's DRAWN AREA the cell rectangles have to account
#: for before the cells are accepted as describing this file's layout.
CELL_COVERAGE = 0.98


def _cells_cover_the_art(payload, cells, w, h):
    """Do these cell rectangles actually contain what the texture draws?

    BUILD 239, SECOND PASS -- THE FIT TEST IS NOT ENOUGH ON ITS OWN.

    "Every cell lands inside the texture" is nearly free for a 256x256 file,
    because the `.s` coordinates are PSX page coordinates and a PSX page is
    256 wide. So the geometric test alone re-admitted a set of textures whose
    tables describe a DIFFERENT part of the page:

        mini_1.tex    32 cells   0% of the drawn art inside them
        zibaku1.tex   16 cells   0%      (every cell is zero-sized)
        enmaku.tex     6 cells   4%
        exp2.tex       1 cell    6%
        faiga_1.tex   32 cells  50%      the file holds twice the table
        blaver00.tex   7 cells  70%      Braver, the canary, failing

    Scaling those with their tables is build 234's defect exactly: the cells
    move, the art they were supposed to name does not, and the frame draws a
    crop of the wrong region -- the clipped flat edge.

    So the class has to be corroborated FROM THE PIXELS, which is the rule
    FINDINGS-238 section 4 arrived at and which this function is the `.s`
    half of: if the cells are this file's own layout, essentially everything
    the file draws lies inside one of them.
    """
    np = _np()
    got = drawn_mask(payload)
    drawn = got[1] if isinstance(got, tuple) else got
    if drawn is None or not drawn.any():
        return False
    mask = np.zeros((h, w), dtype=bool)
    for u, v, cw, ch in cells:
        mask[v:v + ch, u:u + cw] = True
    return float(drawn[mask].sum()) / float(drawn.sum()) >= CELL_COVERAGE


def frame_cells(archive, classes=None):
    """``({tex toc index: cell class}, {s toc index that may be rescaled})``.

    BUILD 239 -- THE `.s` COORDINATES ARE NOT ALWAYS IN THE TEXTURE'S SPACE.

    FINDINGS-230 read the 20-byte sub-entry as an absolute texel rectangle
    into the same-named `.tex`, and built `scale_frame_table` on that. It is
    right for some files and provably wrong for others, and the difference is
    measurable per texture rather than something to pick one answer for.

    WHAT THE RECORD ACTUALLY IS. Dumped raw, a sub-entry is:

        +0  u16 flags        +8  u16 U        +16 u8 w, u8 h
        +4  i16 x offset     +10 u16 V        +18 u8 w2, u8 h2
        +6  i16 y offset     +12 u16 tpage    +14 u16 clut

    `+12` takes 14 distinct values, all in 0x2C-0x3C, and `+14` steps in
    units of 0x40 under a constant 0x78 high byte. Those are a PSX GPU
    **texture page** and **CLUT** word. So U and V are PSX page-relative
    coordinates in a 256-wide page -- not coordinates into the PC port's
    `.tex` files, which were cut out of those pages at conversion time.

    THE PROOF, and it is not subtle:

        bomb_2.s   one cell 64x63 at page (64, 0)      bomb_2.tex  is 64x64
        bomb_9.s   one cell 64x63 at page (0, 128)     bomb_9.tex  is 64x64
        bomb_16.s  one cell 64x63 at page (192, 192)   bomb_16.tex is 64x64

    Sixteen 64x64 files whose tables walk a 4x4 grid of a 256x256 page. The
    coordinate is where the frame WAS; the file is the frame. `hit1.s` and
    `baku11.s` are byte-identical while `hit1.tex` is 256x128 and
    `baku11.tex` is 64x128, which no per-texture texel reading survives.

    So each `.s`-named texture falls into one of three:

        WHOLE     every cell's SIZE equals the texture's size. The file is
                  the cell. Nothing addresses a sub-region of it, so its
                  size is unobservable and it resizes like a mesh texture.
                  25 textures, e.g. bomb_10..bomb_16, awa_1.

        SUBRECT   the cells fit inside the texture and tile it -- the file
                  IS the page. Here FINDINGS-230 is right: the coordinates
                  are this texture's texels and they must move with it.
                  118 textures, e.g. a_max, baku1, aqua, ayasii_1.

        FOREIGN   the cells lie outside the texture. The table is in a page
                  space this file does not span, so it says nothing about
                  the file's own layout -- and nothing about what happens
                  when the file grows either, because whatever maps page to
                  file is in the binary. 139 textures, e.g. a1..a8.
                  Held, and treated exactly like `unref`.

    The second return value is the set of `.s` entries that may be rescaled
    with their textures: those that address at least one SUBRECT texture and
    no WHOLE or FOREIGN one. A table serving both kinds cannot move, so the
    SUBRECT textures it serves are dropped back to vanilla with it.

    THIS IS WHY THE OLD FX KNOB WAS UNTESTABLE. It rewrote all 806 tables
    and scaled all 282 fx textures whatever their class, so raising it broke
    WHOLE and FOREIGN in order to fix SUBRECT, and build 234 -- which scaled
    every texture and every table by 2 together -- failed for that reason
    rather than because the tables cannot be scaled at all.
    """
    import re
    if classes is None:
        classes = texture_classes(archive)

    parsed = {}
    by_stem = {}
    for i, e in enumerate(archive.entries):
        n = e['name'].lower()
        if not n.endswith('.s'):
            continue
        recs = parse_frame_table(e['payload'])
        if not recs:
            continue
        parsed[i] = recs
        by_stem.setdefault(n[:-2], []).append(i)

    def tables_of(name):
        """The frame tables that address this texture.

        RETRACTED, BUILD 246. FINDINGS-251 changed the digit-stripped stem
        from an ADDITION to a FALLBACK, on the reasoning that a texture with
        a table of its own should be described by that table and not also by
        a sibling's. The reasoning is sound and the effect was not: it
        reclassified `moon_1.tex` from FOREIGN to WHOLE, which let it grow
        from 128x128 to 384x384, and on hardware the Zantetsuken moon then
        drew as **the top-left third of an enlarged texture inside a hard
        rectangle**.

        That is the signature of a consumer that normalises against the
        texture's ACTUAL dimensions, not its vanilla ones -- so `moon_1`'s
        size IS observable, WHOLE's premise ("the file is the cell, nothing
        addresses a sub-region, its size is unobservable") is false for it,
        and the union was holding it back for the wrong reason but with the
        right result.

        It also contradicts FINDINGS-239 section 3C, which had the divisor as
        the vanilla dimensions. Both cannot be true of every consumer, and
        which one applies to a given texture is not decidable from the
        archive -- it needs the draw traced. Until then the wider union
        stands, because it is the conservative answer.
        """
        stem = name.lower().rpartition('.')[0]
        out = list(by_stem.get(stem, ()))
        alt = re.sub(r'[_]?\d+$', '', stem)
        if alt != stem:
            out += list(by_stem.get(alt, ()))
        return out

    cell_class = {}
    serves = {}
    for i, e in enumerate(archive.entries):
        if classes.get(i) not in FX_CLASSES:
            continue
        t = tex.parse(e['payload'])
        if not t or not t['width'] or not t['height']:
            continue
        w, h = t['width'], t['height']
        cells = set()
        used = tables_of(e['name'])
        for j in used:
            cells.update(parsed[j])
        # A ZERO-SIZED CELL IS NOT A RECTANGLE, and it fits inside anything.
        # `zibaku1` and `mini_1` have nothing but zero cells, so the "does
        # the extent fit" test passed them on evidence that does not exist.
        cells = {c for c in cells if c[2] > 0 and c[3] > 0}
        if not cells:
            cell_class[i] = CELL_FOREIGN
            for j in used:
                serves.setdefault(j, set()).add(CELL_FOREIGN)
            continue
        # The PSX stores an inclusive extent, so a 64-texel cell is written
        # 63 as often as 64. One texel of slack, not more.
        if all(abs(cw - w) <= 1 and abs(ch - h) <= 1
               for _u, _v, cw, ch in cells):
            k = CELL_WHOLE
        elif (max(u + cw for u, _v, cw, _h in cells) <= w
              and max(v + ch for _u, v, _w, ch in cells) <= h
              and _cells_cover_the_art(e['payload'], cells, w, h)):
            k = CELL_SUBRECT
        else:
            k = CELL_FOREIGN
        cell_class[i] = k
        for j in used:
            serves.setdefault(j, set()).add(k)

    scalable = {j for j, kinds in serves.items() if kinds == {CELL_SUBRECT}}
    # A SUBRECT texture whose table also serves another kind cannot move.
    for i, k in list(cell_class.items()):
        if k != CELL_SUBRECT:
            continue
        if not all(j in scalable for j in tables_of(archive.entries[i]['name'])
                   if j in parsed):
            cell_class[i] = CELL_FOREIGN
    return cell_class, scalable


def frame_tables_for(archive):
    """``{tex toc index: [s toc index, ...]}``.

    Matched by name and verified by extent: the table's furthest texel must
    land inside the texture it is claimed to address. A table that fits none
    of its same-named textures is left unmapped, and any texture it might
    belong to is treated as unscalable.
    """
    by_name = {}
    for i, e in enumerate(archive.entries):
        by_name.setdefault(e['name'].lower(), []).append(i)
    out = {}
    orphan = []
    for i, e in enumerate(archive.entries):
        name = e['name'].lower()
        if not name.endswith('.s'):
            continue
        recs = parse_frame_table(e['payload'])
        if recs is None:
            continue
        mu = max(u + w for u, _v, w, _h in recs)
        mv = max(v + h for _u, v, _w, h in recs)
        hit = False
        for j in by_name.get(name[:-2] + '.tex', ()):
            t = tex.parse(archive.entries[j]['payload'])
            if t and mu <= t['width'] and mv <= t['height']:
                out.setdefault(j, []).append(i)
                hit = True
        if not hit:
            orphan.append((i, mu, mv))
    return out, orphan


def colour_agreement(vanilla, dds_bytes):
    """How well two pictures agree in HUE, 0..1. None if either has no colour.

    BUILD 230. `agreement()` scores SHAPE, and shape alone cannot separate a
    texture from its own recolours. `452.tex` exists four times -- the same
    sprite under `special/gas` (blue), `special/kaen` and `kaen2` (red, kaen
    = flame) and `special/oil` -- and the mod ships a correctly coloured DDS
    in each of those folders. Every pairing scores ~1.0 on shape, so the
    greedy pick was arbitrary and all three replaced entries got the same
    orange art. The Beam Gun went from blue to red/yellow that way.

    Compared as a direction in RGB rather than a distance, because SYW's art
    is uniformly brighter than vanilla's: what has to match is the hue, not
    the level. Cosine similarity, rescaled so that 1.0 is identical hue and
    0.0 is orthogonal or worse.
    """
    np = _np()
    v = tex.parse(vanilla)
    if v is None or not v['palette_flag']:
        return None
    n, c = v['num_palettes'], v['colors_per_palette']
    pal = np.frombuffer(v['palette'], dtype='uint8')[:n * c * 4]
    if pal.size < n * c * 4:
        return None
    pal = pal.reshape(n, c, 4)
    rgb = pal[0][:, [2, 1, 0]].astype('float64')
    keep = (pal[0][:, 3] > 0) & (rgb.max(axis=1) > DARK)
    if not keep.any():
        return None
    a = rgb[keep].mean(axis=0)
    try:
        img, _w, _h = ff7nx_ddstex._decode(dds_bytes)
    except Exception:                                          # noqa: BLE001
        return None
    px = img[:, :, :3].reshape(-1, 3).astype('float64')
    lit = px.max(axis=1) > DARK
    if not lit.any():
        return None
    b = px[lit].mean(axis=0)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-6 or nb < 1e-6:
        return None
    return float(max(0.0, np.dot(a, b) / (na * nb)))


def _palettes_needed(entry):
    t = tex.parse(entry['payload'])
    if not t or not t['palette_flag']:
        return 1
    return max(1, t['num_palettes'])


def _fits(group, need):
    """True if `group` covers palettes 0..need-1."""
    return all(p in group for p in range(need))


def _max_weight_assignment(weight, rows, cols):
    """``{col: row}`` maximising the total weight. Hungarian, O(n^3).

    BUILD 239 -- THE EIGHT-ELEMENT CEILING WAS SILENTLY DROPPING WHOLE NAMES.

    Build 230 replaced greedy assignment with an exact one by enumerating
    every injective map, guarded by `len(groups) <= 8 and len(entries) <= 8`
    because the enumeration is factorial. Nothing handled the else branch:
    a name with nine or more of either got `best_pick = {}` and EVERY group
    for that name was reported as "content does not match", every entry as
    unclaimed. Measured against the shipped mod:

        baku1     17 groups / 17 entries      scores 0.945 - 1.000
        hit1      14 / 14        smoke_1  12 / 11        isi   12 / 12
        domu1     10 / 10        sonic_1   9 /  9        moto1  9 /  9

    83 DDS sets and 82 archive entries -- a third of both totals in build
    237's log -- were being thrown away by the guard, not by the gate. The
    explosion, the physical-hit flash, the rock and the smoke sheets are all
    in that list, so this was visible in play as vanilla art among upscaled
    art.

    Enumeration is replaced by the Hungarian algorithm, which is exact at any
    size and cheaper than the permutation walk was at eight. Ineligible pairs
    carry weight 0 and are filtered out of the result afterwards, so an entry
    is left unclaimed rather than fed art that did not pass the gate.

    The habit this is an instance of (FINDINGS-234 section 4): a guard was
    written for the case in hand and its else branch was left to mean
    "nothing", which is not a refusal, it is a silent drop. A refusal has to
    say so.
    """
    if not rows or not cols:
        return {}
    # Hungarian minimises; feed it negated weights. Rows must not outnumber
    # columns, so transpose when they do and un-transpose the answer.
    flip = len(rows) > len(cols)
    a, b = (cols, rows) if flip else (rows, cols)
    n, m = len(a), len(b)
    INF = float('inf')
    cost = [[-weight.get((y, x) if flip else (x, y), 0.0)
             for y in b] for x in a]
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    out = {}
    for j in range(1, m + 1):
        if not p[j]:
            continue
        x, y = a[p[j] - 1], b[j - 1]
        row, col = (y, x) if flip else (x, y)
        if weight.get((row, col), 0.0) > 0.0:
            out[col] = row
    return out


def route(archive, files, log=lambda *_: None):
    """Group mod DDS by the TOC entry they replace, decided by CONTENT.

    ``files`` is an iterable of ``(relative path, absolute path)``. Returns
    ``(targets, report)`` where ``targets`` is ``{toc index: {palette: path}}``.

    Names and folders only PROPOSE. A ``<stem>_<NN>.dds`` set proposes itself
    for every ``<stem>.tex`` in the archive; which one it actually lands on --
    if any -- is decided by `agreement`, and a pairing that does not agree is
    dropped rather than shipped. See the comment above DARK for why: build 225
    trusted the mod's own conflict-folder name and put a full-frame explosion
    through a 4x4 animation atlas.
    """
    by_name = {}
    for i, entry in enumerate(archive.entries):
        by_name.setdefault(entry['name'].lower(), []).append(i)
    folders = conflict_folders(archive)

    # ---- collect: one group per (stem, source folder) --------------------
    groups = {}
    report = {'matched': 0, 'weak': 0, 'no_entry': 0, 'unparsed': 0,
              'duplicate': 0, 'short': 0, 'unclaimed': 0}
    for rel, full in sorted(files, key=lambda rf: (
            rf[0].replace('\\', '/').lower(),
            str(rf[1]).replace('\\', '/').lower())):
        norm = rel.replace('\\', '/').lower()
        parsed = _parse_dds_name(os.path.basename(norm))
        if parsed is None:
            report['unparsed'] += 1
            continue
        stem, palette = parsed
        if (stem + '.tex') not in by_name:
            report['no_entry'] += 1
            continue
        slot = groups.setdefault((stem, os.path.dirname(norm)), {})
        if palette in slot:
            report['duplicate'] += 1
            continue
        slot[palette] = full

    # ---- decide: content, per name ---------------------------------------
    targets = {}
    weak = []
    for stem in sorted({s for s, _d in groups}):
        idxs = sorted(by_name[stem + '.tex'])
        gkeys = sorted(k for k in groups if k[0] == stem)
        need = [_palettes_needed(archive.entries[i]) for i in idxs]

        # Score every group against every candidate entry. Each group is
        # decoded ONCE and scored against all candidates while it is in hand.
        # `score` ranks (shape + hue); `gate` is shape alone, which is what
        # MIN_AGREEMENT judges -- a good hue must never let bad art through.
        score = {}
        gate = {}
        for gk in gkeys:
            grp = groups[gk]
            if 0 not in grp:
                continue
            try:
                with open(grp[0], 'rb') as f:
                    blob = f.read()
            except OSError:
                continue
            for slot, i in enumerate(idxs):
                if not _fits(grp, need[slot]):
                    continue          # cannot rebuild it; do not even score
                try:
                    s = agreement(archive.entries[i]['payload'], blob)
                except Exception:                          # noqa: BLE001
                    continue
                if s is None:
                    # Unjudgeable (no keyed region). Accept, but rank below
                    # any real content match.
                    s = MIN_AGREEMENT
                gate[(gk, i)] = s
                # HUE breaks the ties shape cannot. A texture and its own
                # recolours are identical in shape, so without this the pick
                # among them is arbitrary -- see colour_agreement().
                col = colour_agreement(archive.entries[i]['payload'], blob)
                if col is not None:
                    s += 0.5 * col
                # THE MOD'S OWN FOLDER BREAKS WHAT IS LEFT.
                #
                # BUILD 239. `452.tex` under special/kaen and special/kaen2 is
                # the same sprite in the same colour, so shape and hue are
                # both EXACTLY equal and the assignment has a true tie. Which
                # of the two equal-value assignments a solver returns is an
                # implementation detail -- the permutation walk happened to
                # return one, the Hungarian returns the other, and the test
                # that pins each entry to its own conflict folder caught it.
                #
                # A tie is not a coin toss: the mod put the file in a folder
                # named after the entry it is for. This applies the bonus to
                # RANKING only (`score`), never to the gate (`gate`), so a
                # flattering folder can no more admit bad art than a
                # flattering hue can, and at 0.01 it cannot outweigh a real
                # difference in either signal.
                if folders.get(i) and gk[1].endswith(folders[i]):
                    s += 0.01
                score[(gk, i)] = s

        # Greedy over the best remaining pair. The sets here are tiny (one
        # name), and greedy on a matrix this separated (99% vs 12-55%) is
        # the same answer an optimal assignment gives.
        # OPTIMAL assignment, not greedy.
        #
        # Greedy takes the best single pair first and can strand a better
        # overall fit. On `452.tex` the four candidates are the same sprite
        # recoloured, so every shape score is ~0.80 and the whole decision
        # rests on hue margins of a few hundredths -- exactly where greedy
        # is fragile. These sets are one texture NAME, so at most a handful
        # of groups; solving them exactly costs nothing and cannot strand.
        # THE GATE IS RELATIVE, WITH AN ABSOLUTE FLOOR.
        #
        # BUILD 230. An absolute threshold is the wrong frame for a choice
        # BETWEEN candidates. `452.tex` is one sprite in four colours, so
        # every shape score sits around 0.80 and the correct blue pairing
        # scored 0.799 against a 0.800 threshold -- eliminated by one
        # thousandth, which forced the blue entry onto red art and turned
        # the Beam Gun orange.
        #
        # So a pairing is admitted when it is close to the BEST shape score
        # available for this texture name, and separately must clear a floor
        # that still rejects a gross mismatch (exp_1's wrong pairings scored
        # 0.12-0.55 against a correct 1.00).
        best_shape = max(gate.values()) if gate else 0.0
        cutoff = max(ABS_SHAPE_FLOOR, best_shape - SHAPE_TOLERANCE)
        eligible = {(gk, i): score[(gk, i)] for (gk, i) in score
                    if gate.get((gk, i), 0.0) >= cutoff
                    and score[(gk, i)] > 0.0}
        gk_list = sorted({gk for gk, _i in eligible})
        i_list = sorted({i for _gk, i in eligible})
        # EXACT at any size. The old enumeration was exact only up to eight
        # of each and returned NOTHING beyond that -- see the note on
        # _max_weight_assignment for the 83 sets that cost.
        best_pick = _max_weight_assignment(eligible, gk_list, i_list)
        used_g = set()
        for i in sorted(best_pick):
            gk = best_pick[i]
            targets[i] = groups[gk]
            used_g.add(gk)
            report['matched'] += 1
        for gk in gkeys:
            if gk in used_g:
                continue
            best = max((gate.get((gk, i), 0.0) for i in idxs), default=0.0)
            if any(_fits(groups[gk], need[s]) for s in range(len(idxs))):
                report['weak'] += 1
                if len(weak) < 400:
                    weak.append(('%s/%s' % gk if gk[1] else gk[0], best))
            else:
                report['short'] += 1
        report['unclaimed'] += sum(1 for i in idxs if i not in best_pick)

    if report['duplicate']:
        log('  spell textures: %d duplicate copy/copies of an already-grouped '
            'palette ignored' % report['duplicate'])
    if weak:
        worst = sorted(weak, key=lambda w: w[1])[:3]
        log('  spell textures: %d extra DDS set(s) unused; content does not '
            'match any candidate entry (worst: %s)'
            % (report['weak'],
               ', '.join('%s %.0f%%' % (n, 100 * s) for n, s in worst)))
    if report['short']:
        log('  spell textures: %d extra DDS set(s) unused; incomplete palette '
            'set for every candidate' % report['short'])
    return targets, report


CONVERSION_VERSION = b'SPELLTEX-V40-LOGICAL-IN-WORD04'

# Historical audit classes. Production conversion does not use either set;
# every content-matched SYW target follows ``uniform_scale()``.
MODEL_CLASSES = ('model', 'unref', 'unknown')

# Retained for old investigations and external imports.
FX_CLASSES = ('fx', 'both')


def resizable_textures(archive, classes=None, mesh=None, cells=None):
    """Retired vanilla-size gate, retained only for audits and old tests.

    Production :func:`convert` does not call this function.  The native
    reciprocal bridge made this classification unnecessary: absolute texels
    remain logical even when the physical texture is enlarged.

    Historical rule: ``{toc index}`` was considered resizable when any of
    the following held.

    A texture is resizable when ANY of these holds:

      1. a `.p` mesh with normalised float UVs addresses it and no `.s`
         table does -- see mesh_uv_textures(); the sprite count is then
         irrelevant, or
      2. no `.s` table addresses it and its drawn area is ONE connected
         sprite, or
      3. an `.s` table addresses it but every cell in that table is the SIZE
         OF THE WHOLE TEXTURE -- the file IS the animation frame, so there
         is no sub-rectangle to invalidate. See frame_cells(): this is the
         `bomb_10`..`bomb_16` shape, sixteen 64x64 files cut out of one
         256x256 PSX page, and build 238 held all of them because a `.s`
         merely NAMED them.

    BUILD 239 ADDED CLAUSE 1, AND IT IS WORTH SAYING WHY, because build 238
    wrote that packed sheets can never be data-resized and that was too
    strong. A sheet is only unsafe if something carves it up in ABSOLUTE
    TEXELS. A `.s` table does. A `.p` mesh does not -- all 52,970 texture
    coordinates in the archive are normalised floats, none above 0.9922 --
    so a mesh-drawn sheet grows in step with its own UVs and the cell
    boundaries stay on texel boundaries. 166 model-class sheets were being
    held on an argument that only ever applied to the `.s` ones.

    Clause 2 still governs `unref`: nothing in the archive describes those,
    so a hardcoded texel rectangle in code cannot be ruled out, and a single
    blob covering its own quad is the conservative answer there.

    WHY THE SPRITE TEST MATTERS AT ALL. Builds 224-237 gated on the
    name graph alone -- who references whom -- and every one of them shipped
    clipped effects. The name graph cannot answer the question, because
    `magic.lgp` IS ADDRESSED BY TOC INDEX: no texture name appears in any of
    the five executables (FINDINGS-237). Code that reaches an entry by index
    can carry a hardcoded texel rectangle for it and leave no name anywhere.
    So "nothing names it" was never evidence that nothing measures it.

    What a packed sheet looks like is visible in the texture itself:

        blaver00.tex   256x256    7 sprites      Cloud's Braver
        blaver01.tex   256x256   10 sprites
        atomic00.tex   256x256   14 sprites      promoted in build 237
        zibaku1.tex    256x256   17 sprites
        bomb_a.tex     256x128   18 sprites

    Multiply that canvas by 3 and every sprite lands three times further from
    the origin than whatever carves it up expects. The consumer keeps reading
    the vanilla rectangle and gets a third of the sprite plus a slab of its
    neighbour -- the flat edges, every time, on exactly these files.

        single connected blob   533 of 1,113 readable
        packed sheet            580

    A single blob covers its quad, so its coordinates are 0..1 by
    construction and there is nothing to invalidate. That, plus the mesh
    clause above, is the whole rule, and unlike every gate before it both
    halves are measured -- the pixels for one, all 52,970 stored texture
    coordinates for the other -- rather than inferred from the file names.

    A sheet that only CODE addresses is still not resizable by any data
    change; it needs the draw path taught the new size, the way ff7nx_gaia
    patches world_us.lgp's six UV computations. That is now a much smaller
    population than build 238 believed: the `unref` sheets, not every sheet.
    """
    if classes is None:
        classes = texture_classes(archive)
    if mesh is None:
        mesh = mesh_uv_textures(archive)
    if cells is None:
        cells = frame_cells(archive, classes)[0]
    out = set()
    for i, e in enumerate(archive.entries):
        if cells.get(i) == CELL_WHOLE:
            out.add(i)
            continue
        if classes.get(i) not in MODEL_CLASSES:
            continue
        if i in mesh:
            # Addressed by normalised UVs. Nothing stores a texel into it.
            out.add(i)
            continue
        try:
            got = drawn_mask(e['payload'])
            drawn = got[1] if isinstance(got, tuple) else got
            if drawn is None or ff7nx_ddstex.sprite_count(drawn) > 1:
                continue
        except Exception:
            # Unreadable is not a licence to resize.
            continue
        out.add(i)
    return out


def _table_factor(name, table_k):
    """The factor this texture's own frame table moved by, or 1.

    Same two spellings `texture_classes` and `_stem_stuck` use: `hit1.tex`
    -> `hit1.s`, and `blaver00.tex` -> `blaver.s`. A texture whose table did
    not move must not move either -- that mismatch is the thunder wrap in one
    direction and the chopped sheet in the other (FINDINGS-255).
    """
    if not table_k:
        return 1
    import re
    stem = name.lower().rpartition('.')[0]
    if stem in table_k:
        return table_k[stem]
    alt = re.sub(r'[_]?\d+$', '', stem)
    return table_k.get(alt, 1)


def _stem_stuck(name, stuck):
    """True if this texture is driven by a frame table that refused to scale.

    Same two spellings texture_classes uses: `hit1.tex` -> `hit1.s`, and
    `blaver00.tex` -> `blaver.s`.
    """
    if not stuck:
        return False
    import re
    stem = name.lower().rpartition('.')[0]
    return stem in stuck or re.sub(r'[_]?\d+$', '', stem) in stuck


def _cache_key(vanilla, paths, scale):
    h = hashlib.sha1(CONVERSION_VERSION)
    h.update(hashlib.sha1(vanilla).digest())
    h.update(struct.pack('<I', scale))
    for p in sorted(paths):
        h.update(str(p).encode())
        try:
            st = os.stat(paths[p])
            h.update(struct.pack('<qq', st.st_size, int(st.st_mtime)))
        except OSError:
            h.update(b'missing')
    return h.hexdigest()


def convert(archive, targets, cache_dir, log=lambda *_: None):
    """Build the replacement payloads.

    Returns ``({toc index: tex bytes}, stats)``. An entry that cannot be
    rebuilt exactly is simply absent from the result, so the archive keeps
    its vanilla bytes for it.
    """
    os.makedirs(cache_dir, exist_ok=True)
    k = uniform_scale()
    # `.s` records contain source coordinates and on-screen sprite extents.
    # They stay byte-identical. The marked TEX and ff7nx_spelluv instead make
    # the graphics object's reciprocal use the original logical canvas.
    table_out = {}

    out = {}
    stats = {'converted': 0, 'cached': 0, 'refused': 0, 'scales': {},
             'in_bytes': 0, 'out_bytes': 0, 'tables': 0,
             'vanilla_upscaled': 0, 'marked': 0}
    refusals = []
    held_multi = 0
    for idx in sorted(targets):
        entry = archive.entries[idx]
        vanilla = entry['payload']
        scale = budgeted_scale(vanilla, k)
        if scale != k:
            held_multi += 1
        paths = targets[idx]
        key = _cache_key(vanilla, paths, scale)
        hit = os.path.join(cache_dir, key)
        data = None
        if os.path.exists(hit):
            try:
                with open(hit, 'rb') as f:
                    data = f.read()
                stats['cached'] += 1
            except OSError:
                data = None
        if data is None:
            blobs = {}
            try:
                for p, path in paths.items():
                    with open(path, 'rb') as f:
                        blobs[p] = f.read()
                blank_placeholder = False
                try:
                    data, _note = ff7nx_ddstex.convert_group(
                        vanilla, blobs, scale=scale, tables_handled=False,
                        texture_name=entry['name'])
                except ff7nx_ddstex.BlankReplacement:
                    # A few SYW groups contain only black placeholder images.
                    # Rebuilding from those would erase visible vanilla art,
                    # but leaving the TEX physically 1x recreates the same
                    # logical/physical mismatch this path exists to remove.
                    # Replicate the vanilla indices onto the requested canvas
                    # and mark it like every other resized TEX. This preserves
                    # the picture while keeping its sampling units coherent.
                    data = upscale_vanilla_tex(vanilla, scale)
                    if data is None:
                        raise ValueError('blank SYW placeholder and vanilla TEX '
                                         'could not be upscaled')
                    blank_placeholder = True
                got, native = tex.parse(data), tex.parse(vanilla)
                if not got or not native:
                    raise ValueError('converter did not return a valid TEX')
                if (got['width'] % native['width'] or
                        got['height'] % native['height']):
                    raise ValueError('physical canvas is not an integer '
                                     'multiple of its native canvas')
                sx = got['width'] // native['width']
                sy = got['height'] // native['height']
                if not (1 <= sx <= 16 and 1 <= sy <= 16):
                    raise ValueError('physical canvas scale is %dx%d' % (sx, sy))
                if sx > 1 or sy > 1:
                    data = tex.mark_logical_scale(data, sx, sy)
            except (ff7nx_ddstex.ConvertError, OSError, ValueError) as exc:
                stats['refused'] += 1
                if len(refusals) < 5:
                    refusals.append('%s: %s' % (entry['name'], exc))
                continue
            stats['converted'] += 1
            if blank_placeholder:
                stats['vanilla_upscaled'] += 1
            # Keep the five placeholder fallbacks uncached. They are cheap to
            # reproduce, and doing so keeps the build report honest on every
            # run instead of losing the `vanilla_upscaled` classification on
            # a cache hit.
            if not blank_placeholder:
                try:
                    with open(hit, 'wb') as f:
                        f.write(data)
                except OSError:
                    pass
        chk = tex.parse(data)
        van_chk = tex.parse(vanilla)
        if not chk or not van_chk or not van_chk['width'] or not van_chk['height']:
            stats['refused'] += 1
            continue
        used_x = chk['width'] // van_chk['width']
        used_y = chk['height'] // van_chk['height']
        if (chk['width'] != van_chk['width'] * used_x or
                chk['height'] != van_chk['height'] * used_y or
                not (1 <= used_x <= 16 and 1 <= used_y <= 16)):
            log('  ! spell texture %s has invalid physical/native canvas '
                '%dx%d over %dx%d -- left vanilla'
                % (entry['name'], chk['width'], chk['height'],
                   van_chk['width'], van_chk['height']))
            stats['refused'] += 1
            continue
        marked = used_x > 1 or used_y > 1
        if marked != (tex.logical_scale(data) == (used_x, used_y)):
            log('  ! spell texture %s has a missing or invalid logical-scale '
                'marker -- left vanilla' % entry['name'])
            stats['refused'] += 1
            continue
        out[idx] = data
        used = used_x if used_x == used_y else (used_x, used_y)
        stats['scales'][used] = stats['scales'].get(used, 0) + 1
        stats['marked'] += int(marked)
        if min(used_x, used_y) < scale:
            stats['clamped'] = stats.get('clamped', 0) + 1
        stats['in_bytes'] += len(vanilla)
        stats['out_bytes'] += len(data)
    stats['worse'] = 0

    for why in refusals:
        log('  ! spell texture %s' % why)
    if stats['refused'] > len(refusals):
        log('  ! %d more spell texture(s) left vanilla'
            % (stats['refused'] - len(refusals)))
    stats['multipal_held'] = held_multi
    stats['palette_budget_mb'] = palette_budget_bytes() // (1024 * 1024)
    return out, table_out, stats


def summarise(report, stats):
    if not report:
        return ''
    def _scale_label(value):
        return ('%dx%d' % value if isinstance(value, tuple) else '%dx' % value)
    scales = ', '.join('%s%s' % (_scale_label(k),
                                 '' if v == 1 else ' (%d)' % v)
                       for k, v in sorted(stats['scales'].items(),
                                          key=lambda kv: (kv[0] if isinstance(
                                              kv[0], tuple) else
                                              (kv[0], kv[0]))))
    if stats.get('clamped'):
        scales += ' [%d held below the cap by their source resolution]' % (
            stats['clamped'])
    if stats.get('multipal_held'):
        scales += (' [%d multi-palette entry/entries took a smaller scale to '
                   'fit the %d MB per-TEX palette budget (%s); pixels x '
                   'palettes is what a paletted TEX actually costs decoded]'
                   % (stats['multipal_held'],
                      stats.get('palette_budget_mb', PALETTE_BUDGET_MB),
                      PALETTE_BUDGET_ENV))
    extra = ('; %d resized TEX payload(s) carry their own logical scale; every '
             '.s frame table remains byte-identical'
             % stats.get('marked', 0))
    if stats.get('vanilla_upscaled'):
        extra += ('; %d blank SYW placeholder(s) preserve the vanilla picture '
                  'on the same enlarged logical canvas'
                  % stats['vanilla_upscaled'])
    # FINDINGS-248. The two things that decide how the art actually LOOKS,
    # reported every build because both are one environment variable away
    # from being off.
    import ff7nx_ddstex as _D
    extra += ('\n  spell textures: palette widening %s (%s=0 to revert) -- '
              'vanilla 16-colour entries are rebuilt with 256, num_palettes '
              'untouched so palette cycling is unaffected; 92%% of replaced '
              'entries were capped at 16 colours before this.'
              % ('ON' if _D.wide_palette() else 'OFF', _D.WIDE_PALETTE_ENV))
    extra += ('\n  spell textures: targeted wrap repair %s (%s=0 to revert) '
              '-- Odin 2 kiri_2 only: its horizontal SYW boundary jumps '
              '144.7 levels where vanilla\'s scrolling boundary differs by '
              '3.8; the final edge is tapered into the preserved first edge. '
              'No archive-wide seam heuristic edits unrelated textures.'
              % ('ON' if _D.reseam() else 'OFF', _D.RESEAM_ENV))
    extra += ('\n  spell textures: replacement alpha %s (%s=0 to revert) -- '
              'the 321 SYW magic DDS that carry a real silhouette now supply '
              'it instead of vanilla\'s 256px mask being nearest-upscaled, '
              'which is what left a hard black halo around every puff; the '
              'other 2534 are opaque and unaffected.'
              % ('ON' if _D.dds_alpha() else 'OFF', _D.DDS_ALPHA_ENV))
    return ('spell textures: %d entries rebuilt from %d content-matched DDS '
            'sets (%d conversion refusals; %d extra DDS sets unused after '
            'content matching, %d incomplete DDS sets; %d archive entries '
            'had no matched art); scale %s; '
            'magic.lgp texture payload %.1f -> %.1f MB'
            % (stats['converted'] + stats['cached'], report['matched'],
               stats['refused'], report['weak'], report['short'],
               report['unclaimed'],
               scales or 'native',
               stats['in_bytes'] / 1e6, stats['out_bytes'] / 1e6) + extra)
