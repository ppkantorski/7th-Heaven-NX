#!/usr/bin/env python3
"""
field_bg_native.py -- rewrite flevel section 9 for larger truecolor pages.

The data half of ff7nx_fieldbg.py. That module makes the game read, allocate
and address depth==2 background pages at 512x512 (or 1024x1024); this one
makes the file agree.

Two modes, and the difference matters:

  resize   Existing depth==2 pages only. Each is scaled up by an integer
           factor with nearest-neighbour replication, which is EXACTLY
           equivalent on screen because the tile UVs are normalised (see
           ff7nx_fieldbg.py). depth==1 pages are copied through untouched.

           This is the minimum testable set. With the module patched and
           every field resized, the game should look IDENTICAL. If it does,
           sites A/C/D are right. If it does not, they are not, and no art
           has been wasted finding out. Vanilla flevel.lgp has 51 depth-2
           pages across 711 fields, so this costs ~24 MB.

  upgrade  Additionally promotes depth==1 (8-bit paletted) pages to depth==2
           truecolor, taking pixels from the mod's upscaled art where it
           exists and from the field's own palette where it does not. This
           is what actually delivers the upscale, and it is gated on a
           constraint that has to be respected -- see SLOT GROUPS.


PIXEL FORMAT -- MEASURED, not assumed
-------------------------------------
depth==2 pages are R5G6B5, `(r>>3)<<11 | (g>>2)<<5 | (b>>3)`.

Proof: x86 0x63F350, the per-pixel converter field_convert_type2_layers
calls when the display surface is NOT 5:6:5, is

    out = ((in & 0xF800) >> 1) | ((in & 0x07E0) >> 1) | (in & 0x1F)

i.e. it reads a 5:6:5 source and produces X1R5G5B5. field_convert_type2_layers
returns immediately when the surface IS 5:6:5 (`cmp [0xCFFDB8], 0`), because
then the file's bits are already the surface's bits. So the FILE is 5:6:5.

0x0000 is the "empty" value: the same function replaces every 0 pixel with
`convert(0xFF000000)` -- opaque black in the surface format -- so a genuine
black pixel must not be encoded as 0. This module bumps such pixels to
0x0021 (green 1/64), which is below the 8-bit quantisation step and
therefore all but invisible (see NEAR_BLACK).


SLOT GROUPS -- the constraint on `upgrade`
------------------------------------------
`field_load_textures` (x86 0x640292) picks a page's BLEND MODE from its slot
index, and the thresholds are different for the two depths:

    depth 1   slot < 0x0F -> blend 4 (opaque)
              slot < 0x18 -> blend 1 (additive)
              else        -> blend 0 (average)
    depth 2   slot < 0x21 -> blend 4
              slot < 0x28 -> blend 1
              else        -> blend 0

which is not an accident: field_init_bg_pages (x86 0x63F494) pre-assigns
slots 0x00..0x19 as depth 1 and 0x1A..0x29 as depth 2. The 42 slots are a
depth-partitioned, blend-grouped address space:

    blend 4 (opaque)    depth1 0x00..0x0E (15)   depth2 0x1A..0x20 (7)
    blend 1 (additive)  depth1 0x0F..0x17 ( 9)   depth2 0x21..0x27 (7)
    blend 0 (average)   depth1 0x18..0x19 ( 2)   depth2 0x28..0x29 (2)

So promoting a page to depth 2 means MOVING it to the depth-2 slot of the
same blend group, and rewriting every tile that points at it. There are
fewer depth-2 slots than depth-1 slots, so it does not always fit.

Measured over the 709 parseable vanilla fields: 700 fit (98.7%), 9 do not
-- del3, fship_2, fship_22, fship_23, fship_24, fship_25, junbin21,
rcktin7, ujunon3. Those keep their vanilla background. A mod's section 9
is measured the same way at build time; anything that does not fit is left
alone rather than half-converted.
"""
from __future__ import annotations

import os
import struct

BG_MAX_PAGES = 42
VANILLA_PX = 256
EMPTY = 0x0000
# What a genuinely black OPAQUE pixel becomes, so it is not mistaken for
# EMPTY. Green 2/64, NOT 1/64: the green LSB has to stay clear (see
# GREEN_LSB below) and 0x0020 sets it.
# Bytes ONE depth-2 page occupies in section 9 -- the LOADER'S READ SIZE.
#
# For 256/512/768/1024 this is exactly px*px*2. The sizes in between are
# possible only because that read count is PADDED up to the nearest value the
# module's one-word immediate can express: the loader reads a fixed number of
# bytes per page, so the file simply has to hold that many, and the tail is
# never sampled -- the surface descriptor is px wide with a px*2 stride.
#
# The waste is 2-16% of the raw page, and the raw page is only a third of the
# cost (the 32bpp surface the engine builds is the rest), so a 384px page
# still lands at 0.88 MB against 512's 1.50.
#
# ff7nx_fieldbg.read_bytes() computes the same numbers from the encoder and
# test_tex_caps.py checks the two agree. Duplicated rather than imported so
# this module keeps no dependency on the patcher.
PAGE_STORED_BYTES = {
    128: 0x8000,     256: 0x20000,   320: 0x38000,   384: 0x50000,
    448: 0x70000,    512: 0x80000,   768: 0x120000,  1024: 0x200000,
}


def stored_bytes(px, depth):
    """Bytes one page of this size and depth occupies in section 9."""
    if depth != 2:
        return VANILLA_PX * VANILLA_PX
    return PAGE_STORED_BYTES.get(px, px * px * 2)


# THE DIMMEST NON-ZERO COLOUR THAT IS NOT A COLOUR.
#
# 0x0000 means transparent on a truecolor page, so a black pixel has to be
# lifted off it. This was 0x0001 -- blue 8/255, and nothing else. MEASURED on
# `mkt_mens`, counting every pixel the field actually draws:
#
#     vanilla   0x0001 =    400 px (0.28%)
#     ours      0x0001 =  6,368 px (4.52%)
#
# FF7's field art is full of black outlines and shadow, and every one of them
# inside a promoted cell became a BLUE outline. 4.5% of the picture, following
# the art's own edges -- which is why it reads as blue LINES rather than as a
# tint, and why TRUE_BLACK did not help: those cells are not mostly black,
# they are detailed cells with black linework in them.
#
# THE FIX IS NOT HERE, IT IS IN THE SHADER, and changing this value was the
# wrong instinct: it hides a mismatch by altering the art.
#
# `custom_shaders/hd/*.glsl` grades the picture with HD_BLACK_POINT, whose own
# comment says it "undoes exactly the lift the quantiser fix introduced". It
# was set to 0.014 = 3.5/255 while the lift is 8/255, so it undid less than
# half of it and left 4.5/255 of PURE BLUE on every lifted pixel -- which is
# the blue linework in Men's Hall. HD_BLACK_POINT is now 8/255 and both this
# value and any other choice of dimmest-non-zero land on exactly (0,0,0).
#
# So this stays 0x0001, which is what the rest of the project documents.
NEAR_BLACK = 0x0001          # blue 1/31 = 8/255; MUST stay non-zero
# REVERTED to what build 30 shipped. The achromatic argument below is
# still arithmetically right, but it went out in the same build as two
# other unverified changes and the result was worse overall. It is a
# one-line experiment to re-run ON ITS OWN once the baseline is good.
# ACHROMATIC, NOT BLUE. 0x0001 is R0 G0 B8/255 -- pure blue -- and it was used
# here for two builds. The shader's black point cannot remove it, and the
# arithmetic is short enough to check:
#
#   `hd/2xsal_p.glsl` BLENDS BEFORE IT GRADES. A lifted texel is averaged with
#   its neighbours by the 2xSaL filter and only then does `hd_grade_rgb` run:
#
#       lifted 0x0001 (0, 0, 0.0314) blended 50/50 with a mid-grey neighbour
#         -> (0.2500, 0.2500, 0.2657)
#       black point:  max(c - 0.03137, 0) / 0.96863
#         -> (0.2256, 0.2256, 0.2418)        blue excess 0.0162 = 4.1/255
#       HD_SATURATION 1.05 then pushes it further out
#
#   A UNIFORM SUBTRACT CANNOT REMOVE A PER-CHANNEL OFFSET FROM A BRIGHT PIXEL.
#   It only zeroes a pixel lying entirely below the black point -- a flat black
#   area. Edges are not flat black areas, and edges are where the lift lands:
#   FF7's field art is full of black linework, so every outline inside a
#   promoted cell picked up a blue rim. That is the "blue edging on textures"
#   reported from Men's Hall, and it is why raising HD_BLACK_POINT from 0.014
#   to 0.03137 did not remove it.
#
# 0x0841 is the same 8/255 floor with no hue: R 1/31 = 8, G 2/63 = 8, B 1/31 = 8.
# Green's LSB is clear (G = 2), so the 0x07E0 smear rule below still holds.
#
# THE EARLIER GREY TEST FAILED FOR A DIFFERENT REASON. 0x0841 was tried once
# and rejected as "looking grey and weird" -- correctly, because that build ran
# HD_BLACK_POINT at 0.014, which leaves 4.5/255 of grey standing in every flat
# black area. Grey lift and the 0.03137 black point have never been in the same
# build. Together the flats crush to exactly 0 and the edges carry a neutral
# 4/255 instead of a blue one.
# The dimmest non-zero colour R5G6B5 can express, and the reason it has to be
# non-zero at all:
#
#   x86 0x6470E0, the engine's own pixel converter
#     0064719A  test edx, edx
#     0064719C  jne  0x6471A9
#     0064719E  mov  word [ebp-4], 0        ; pixel 0 -> 0, i.e. TRANSPARENT
#     006471B1  cmp  eax, 0x8000            ; opaque black
#     006471C1  mov  word [ebp-4], 0x421    ; -> a minimal NON-ZERO value,
#     006471C9  mov  word [ebp-4], 0x821    ;    never left at 0
#
# So 0 means transparent in this pipeline, and the engine already refuses to
# emit it for opaque black. A background pixel that is transparent writes no
# occlusion, so field models draw straight through it -- that is Cloud
# appearing in FRONT of black scenery he should be behind.
#
# Which value, though, is a perceptual choice, and 0x0040 was the wrong one:
#
#   0x0001 blue           RGB(0,0,8)  luminance 0.9/255   <- this
#   0x0800 red            RGB(8,0,0)  luminance 2.4/255
#   0x0040 green (was)    RGB(0,8,0)  luminance 4.7/255
#   0x0821 engine's own   RGB(8,4,8)  luminance 5.7/255
#
# Green sits where the eye is most sensitive, which is why it read as a
# grey-green wash over unlit scenery -- 17.4% of every truecolor pixel in
# nmkin_1. Blue at the same code level is 5.2x dimmer and is the darkest
# non-zero colour the format has. Occlusion is preserved; the wash is not.

# THE GREEN LSB MUST BE ZERO.
#
# When the display surface is not 5:6:5 -- and on this port it is not -- the
# engine converts every depth-2 pixel with x86 0x63F350:
#
#     out = ((in & 0xF800) >> 1) | ((in & 0x07E0) >> 1) | (in & 0x1F)
#
# That second term takes SIX bits of green and shifts them into a FIVE bit
# field, so green's low bit lands on bit 4 -- the top bit of blue -- and is
# ORed on top of the real blue. Blue gets +16 out of 31 whenever green is
# odd, which is half the time, per pixel, at random.
#
# MEASURED: RGB(160,140,90) comes out with blue 27 instead of 11. That is a
# heavy blue cast with per-pixel noise, and it is exactly what the first
# repacked build looked like on hardware.
#
# It is an engine bug -- the mask should be 0x07C0 -- and it has always been
# there; it just never mattered, because vanilla ships only 51 truecolor
# pages and they are all in late-game fields. Masking green to 5 bits when
# packing makes `in & 0x07E0` equal `in & 0x07C0` and the conversion exact.
# Costs one bit of green, which is below the 8-bit quantisation step.
GREEN_LSB = 0x3E                     # mask applied to the 6-bit green field

# slot -> blend group, per field_load_textures
D1_GROUPS = ((0x00, 0x0F, 4), (0x0F, 0x18, 1), (0x18, 0x1A, 0))
# HOW MANY TRUECOLOR SLOTS THE OPAQUE BAND ACTUALLY HAS.
#
# This was 0x1A..0x21 -- seven slots, 26 through 32 -- and that is what the
# x86 slot comparisons in `field_load_textures` imply (`cmp eax, 0x21`,
# `cmp ecx, 0x28`). But those comparisons pick a BLEND MODE. They do not say
# a slot is loadable.
#
# MEASURED across the ENTIRE vanilla archive, 709 fields: every depth-2 page
# the shipping game contains lives in slot 26, 27 or 28.
#
#     slot 26: 26 pages    slot 27: 21 pages    slot 28: 4 pages
#     slot 29+: NONE
#
# And our builds fail exactly when they cross it. The build that had no black
# squares (`MAX_TRUECOLOR_PAGES = 3`) could only ever reach slots 26-28. Every
# build since raised the ceiling, put pages in 29 and above, and produced
# squares -- 159 of 709 fields in the current one. In Wall Market, `mkt_mens`
# uses {26,27,28} and loads; `mrkt1`, `mrkt2` and `mrkt4` use {26..30} and do
# not.
#
# So `MAX_TRUECOLOR_PAGES = 3` was never "the density measured on hardware".
# It was the width of this band, and nobody knew that is what they were
# measuring.
#
# THIS IS THE CONSERVATIVE READING AND IT IS DELIBERATE. If slots 29-32 turn
# out to be loadable after all, widening this back is one number -- and the
# evidence for widening it would have to be a build that puts a page there
# and draws it, which is exactly the test that keeps failing.
D2_OPAQUE_SLOTS = 3

D2_GROUPS = ((0x1A, 0x1A + D2_OPAQUE_SLOTS, 4), (0x21, 0x28, 1),
             (0x28, 0x2A, 0))

TILE_SIZE = 52               # verified by round-trip over all 709 fields
TILE_TEXTURE_ID = 32
TILE_TEXTURE_ID2 = 34
TILE_PALETTE_ID = 22


class Section9Error(ValueError):
    """Section 9 is not the layout this module understands."""


# ------------------------------------------------------------------ TEXTURE
class Page:
    __slots__ = ('slot', 'size_flag', 'depth', 'data', 'px')

    def __init__(self, slot, size_flag, depth, data, px):
        self.slot = slot
        self.size_flag = size_flag
        self.depth = depth
        self.data = data
        self.px = px

    def __repr__(self):
        return ('Page(slot=%d, size=%d, depth=%d, %dx%d)'
                % (self.slot, self.size_flag, self.depth, self.px, self.px))


def parse_texture_block(sec9, px=VANILLA_PX):
    """
    (pages, tex_start, tex_end) where `pages` has BG_MAX_PAGES entries, each
    a Page or None.

    `px` is the size the FILE uses for depth-2 pages; depth-1 pages are
    always 256. The walk is required to consume the block exactly, which is
    what makes a wrong start offset or a wrong `px` fail instead of silently
    reading garbage.
    """
    start = sec9.find(b'TEXTURE')
    if start < 0:
        raise Section9Error('no TEXTURE marker')
    o = start + 7
    n = len(sec9)
    pages = []
    for slot in range(BG_MAX_PAGES):
        if o + 2 > n:
            raise Section9Error('truncated at slot %d' % slot)
        present, = struct.unpack_from('<H', sec9, o)
        o += 2
        if not present:
            pages.append(None)
            continue
        if o + 4 > n:
            raise Section9Error('truncated header at slot %d' % slot)
        size_flag, depth = struct.unpack_from('<HH', sec9, o)
        o += 4
        if depth not in (1, 2):
            raise Section9Error('slot %d has depth %d' % (slot, depth))
        side = px if depth == 2 else VANILLA_PX
        nbytes = side * side * depth
        nstored = stored_bytes(side, depth)
        if o + nstored > n:
            raise Section9Error('truncated pixels at slot %d' % slot)
        # Page.data stays exactly the PIXELS; the padding a non-power-of-two
        # size needs is a storage detail and never reaches a caller.
        pages.append(Page(slot, size_flag, depth, sec9[o:o + nbytes], side))
        o += nstored
    if not 0 <= n - o <= 64:
        raise Section9Error('TEXTURE block leaves %d trailing bytes' % (n - o))
    return pages, start, o


def build_texture_block(pages):
    """The bytes of a TEXTURE block, marker included."""
    out = [b'TEXTURE']
    for slot in range(BG_MAX_PAGES):
        p = pages[slot] if slot < len(pages) else None
        if p is None:
            out.append(b'\0\0')
            continue
        want = p.px * p.px * p.depth
        if len(p.data) != want:
            raise Section9Error('slot %d: %d bytes for a %dx%d depth-%d page, '
                                'expected %d'
                                % (slot, len(p.data), p.px, p.px, p.depth,
                                   want))
        out.append(struct.pack('<HHH', 1, p.size_flag, p.depth))
        out.append(p.data)
        pad = stored_bytes(p.px, p.depth) - want
        if pad:
            out.append(b'\0' * pad)
    return b''.join(out)


def replace_texture_block(sec9, pages, tex_start, tex_end):
    return sec9[:tex_start] + build_texture_block(pages) + sec9[tex_end:]


# ------------------------------------------------------------------- pixels
def resize_depth2(data, src_px, dst_px):
    """
    Nearest-neighbour integer RESIZE of a 16-bit page, either direction.

    Format agnostic -- it moves 2-byte units and never interprets them, so it
    is correct for 5:6:5, 1:5:5:5 or anything else the file might hold.

    Downscale exists for the 128px setting. The 51 vanilla depth-2 pages
    (27 fields, measured off flevel.lgp) are authored at 256, so asking for
    128px pages means they have to come DOWN, and this used to raise. It is
    decimation, not a box filter, for the same reason the upscale is
    replication: a 16-bit page may be 5:6:5 or 1:5:5:5 and averaging two
    packed pixels of an unknown layout is meaningless. Nearest also keeps
    colour 0 exactly 0, which matters -- convert_type2_layers turns raw
    colour 0 into the transparency key, and a filter that blended a
    transparent pixel with an opaque neighbour would invent a colour that is
    neither, putting a halo on every sprite edge.
    """
    if dst_px == src_px:
        return data
    if src_px % dst_px == 0 and dst_px < src_px:
        k = src_px // dst_px
        out = bytearray(dst_px * dst_px * 2)
        for y in range(dst_px):
            sy = y * k
            srow = data[sy * src_px * 2:(sy + 1) * src_px * 2]
            base = y * dst_px * 2
            for x in range(dst_px):
                sx = x * k * 2
                out[base + x * 2:base + x * 2 + 2] = srow[sx:sx + 2]
        return bytes(out)
    if dst_px % src_px:
        # GENERAL RATIO -- what makes 320/384/448 reachable.
        #
        # Nearest-neighbour, for the same reason the other two directions
        # are: a 16-bit page may be 5:6:5 or 1:5:5:5, so averaging two
        # packed pixels of an unknown layout is meaningless, and any filter
        # that blended a transparent pixel (colour 0) with an opaque one
        # would invent a colour that is neither and halo every sprite edge.
        # This moves 2-byte units and never interprets them.
        row_src = [(x * src_px) // dst_px for x in range(dst_px)]
        out = bytearray(dst_px * dst_px * 2)
        for y in range(dst_px):
            sy = (y * src_px) // dst_px
            base = sy * src_px * 2
            row = bytearray(dst_px * 2)
            for x, sx in enumerate(row_src):
                row[x * 2:x * 2 + 2] = data[base + sx * 2:base + sx * 2 + 2]
            out[y * dst_px * 2:(y + 1) * dst_px * 2] = row
        return bytes(out)
    k = dst_px // src_px
    out = bytearray(dst_px * dst_px * 2)
    for y in range(src_px):
        row = data[y * src_px * 2:(y + 1) * src_px * 2]
        # widen the row k times horizontally
        wide = bytearray(dst_px * 2)
        for x in range(src_px):
            px = row[x * 2:x * 2 + 2]
            base = x * k * 2
            for i in range(k):
                wide[base + i * 2:base + i * 2 + 2] = px
        for i in range(k):
            oy = y * k + i
            out[oy * dst_px * 2:(oy + 1) * dst_px * 2] = wide
    return bytes(out)


def rgb_to_565(r, g, b, a=255, alpha_cut=8, black_ok=False):
    """
    One R5G6B5 pixel.

    `black_ok` keeps genuine black as 0x0000. NEAR_BLACK exists only because
    EMPTY used to double as the transparency sentinel for the per-cell opacity
    gate, so black had to be nudged off it -- and it was 17.4% of every
    truecolor pixel in nmkin_1, so its exact value matters. The gate
    reads the art's alpha now (PageArt.tmask), so callers writing art should
    pass black_ok=True. Rounds rather than truncates, onto the level*8 grid the
    engine reconstructs.
    """
    if a < alpha_cut:
        return EMPTY
    q = lambda c: min(31, max(0, int(c * 0.125 + 0.5)))
    v = (q(r) << 11) | ((q(g) << 1) << 5) | q(b)
    if v == EMPTY and not black_ok:
        return NEAR_BLACK
    return v


def rgba_bytes_to_565(rgba, npx, alpha_cut=8, black_ok=False):
    """`npx` RGBA8888 pixels -> packed R5G6B5 little-endian."""
    out = bytearray(npx * 2)
    for i in range(npx):
        j = i * 4
        struct.pack_into('<H', out, i * 2,
                         rgb_to_565(rgba[j], rgba[j + 1], rgba[j + 2],
                                    rgba[j + 3], alpha_cut, black_ok))
    return bytes(out)


def paletted_to_565(indices, palette_rgba, npx=None):
    """
    depth-1 page pixels -> R5G6B5, using one palette page.

    `palette_rgba` is 256 (r, g, b, a) tuples. FF7 field palettes mark
    "transparent" as index 0 with the palette's own zero entry, so index 0
    maps to EMPTY regardless of what the palette says -- the same rule the
    engine applies.
    """
    npx = npx if npx is not None else len(indices)
    lut = bytearray(512)
    for i, c in enumerate(palette_rgba[:256]):
        v = EMPTY if i == 0 else rgb_to_565(c[0], c[1], c[2],
                                            c[3] if len(c) > 3 else 255)
        struct.pack_into('<H', lut, i * 2, v)
    out = bytearray(npx * 2)
    for i in range(npx):
        k = indices[i] * 2
        out[i * 2] = lut[k]
        out[i * 2 + 1] = lut[k + 1]
    return bytes(out)


# -------------------------------------------------------------- slot remap
def _group_of(slot, groups):
    for lo, hi, blend in groups:
        if lo <= slot < hi:
            return blend
    return None


def plan_promotion(pages):
    """
    {old_slot: new_slot} promoting every depth-1 page into the depth-2 slot
    of its own blend group, or None if it does not fit.

    Existing depth-2 pages keep their slots and consume capacity. Order
    within a group is preserved so a field's page numbering stays monotonic,
    which keeps the layer-2 `layer2_end_page` global meaningful.
    """
    used = {}
    for p in pages:
        if p is not None and p.depth == 2:
            used.setdefault(_group_of(p.slot, D2_GROUPS), []).append(p.slot)
    mapping = {}
    for lo, hi, blend in D2_GROUPS:
        free = [s for s in range(lo, hi) if s not in used.get(blend, [])]
        want = [p.slot for p in pages
                if p is not None and p.depth == 1
                and _group_of(p.slot, D1_GROUPS) == blend]
        if len(want) > len(free):
            return None
        for old, new in zip(want, free):
            mapping[old] = new
    return mapping


# ----------------------------------------------------------- tile rewriting
def _layer_tile_spans(sec9, back_start, tex_start):
    """
    Byte offsets of every tile record between BACK and TEXTURE.

    Rather than re-deriving four slightly different layer headers, the tile
    array is found structurally: a layer header ends with a u16 tile count,
    the records are a fixed 52 bytes, and the block must land exactly where
    the next layer header or the TEXTURE marker begins. Any layout that does
    not satisfy that raises, so a mod section this does not understand is
    skipped instead of corrupted.
    """
    spans = []
    o = back_start + 4                       # "BACK"
    # layer 1 header: width, height, ntiles, depth, blank
    _w, _h, n1, _d, _b = struct.unpack_from('<HHHHH', sec9, o)
    o += 10
    spans += [o + i * TILE_SIZE for i in range(n1)]
    o += n1 * TILE_SIZE + 2                  # + trailing blank
    # layers 2/3/4: a presence flag, then width, height, ntiles, an unused
    # block (16 bytes on layer 2, 10 on layers 3 and 4), a blank, the tiles
    # and a trailing blank. Sizes taken from PyFF7's SIZE table.
    for unused in (16, 10, 10):
        if o >= tex_start:
            break
        flag = sec9[o]
        o += 1
        if flag == 0:
            continue
        if flag != 1:
            raise Section9Error('layer flag %d at %d' % (flag, o - 1))
        _w, _h, n = struct.unpack_from('<HHH', sec9, o)
        o += 6 + unused + 2
        spans += [o + i * TILE_SIZE for i in range(n)]
        o += n * TILE_SIZE + 2
    if o != tex_start:
        raise Section9Error('layer walk ended at %d, TEXTURE at %d'
                            % (o, tex_start))
    return spans


def remap_tile_pages(sec9, mapping, back_start, tex_start):
    """sec9 with every tile's texture_id / texture_id2 moved by `mapping`."""
    if not mapping:
        return sec9
    buf = bytearray(sec9)
    for off in _layer_tile_spans(sec9, back_start, tex_start):
        for field in (TILE_TEXTURE_ID, TILE_TEXTURE_ID2):
            old = buf[off + field]
            if old in mapping:
                buf[off + field] = mapping[old]
    return bytes(buf)


# ------------------------------------------------------------------ drivers
def resize_section9(sec9, page_px, src_px=VANILLA_PX):
    """
    `resize` mode. Returns (new_sec9, n_pages_resized), or (sec9, 0) if the
    section has no depth-2 pages.
    """
    pages, s, e = parse_texture_block(sec9, src_px)
    n = 0
    for p in pages:
        if p is None or p.depth != 2 or p.px == page_px:
            continue
        p.data = resize_depth2(p.data, p.px, page_px)
        p.px = page_px
        n += 1
    if not n:
        return sec9, 0
    return replace_texture_block(sec9, pages, s, e), n


def survey(sec9, src_px=VANILLA_PX):
    """Facts about one section 9, for logging and for the fit decision."""
    pages, s, e = parse_texture_block(sec9, src_px)
    back = sec9.find(b'BACK')
    present = [p for p in pages if p is not None]
    plan = plan_promotion(pages)
    return {
        'pages': len(present),
        'depth1': sum(1 for p in present if p.depth == 1),
        'depth2': sum(1 for p in present if p.depth == 2),
        'promotable': plan is not None,
        'plan': plan,
        'tex_start': s,
        'tex_end': e,
        'back_start': back,
    }


def bytes_after(sec9, page_px, promote, src_px=VANILLA_PX):
    """How big the TEXTURE block becomes -- for the page-budget cap."""
    pages, _s, _e = parse_texture_block(sec9, src_px)
    total = 0
    for p in pages:
        if p is None:
            total += 2
            continue
        d2 = p.depth == 2 or promote
        side = page_px if d2 else VANILLA_PX
        total += 6 + side * side * (2 if d2 else 1)
    return total


if __name__ == '__main__':
    import argparse
    import sys
    ap = argparse.ArgumentParser(description='survey flevel section 9')
    ap.add_argument('flevel')
    ap.add_argument('--px', type=int, default=512)
    a = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import lgp
    arc = lgp.Archive(a.flevel)
    n = ok = fit = d2 = 0
    grew = 0
    for name in sorted(arc.index):
        e = arc.index[name]
        if not arc.is_field(e):
            continue
        try:
            secs = lgp.split_sections(arc.decompressed(e))
            info = survey(secs[8])
        except Exception:
            continue
        n += 1
        ok += 1
        d2 += info['depth2']
        fit += 1 if info['promotable'] else 0
        grew += bytes_after(secs[8], a.px, True)
    print('fields parsed      %d' % n)
    print('depth-2 pages      %d' % d2)
    print('promotable fields  %d (%.1f%%)' % (fit, 100.0 * fit / max(n, 1)))
    print('TEXTURE bytes if every field were promoted to %dpx: %.2f GB'
          % (a.px, grew / 1024 ** 3))
