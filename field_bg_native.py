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


# THE DEPTH-1 PAGE SIDE. FINDINGS-223.
#
# Until now this was welded to VANILLA_PX by a derivation rather than by the
# data: section 9 stores NO size field per page, so both we and the engine
# INFER a page's dimension from its depth. That is why colour depth and
# resolution have been the same decision, and why the 26% of tiles that never
# promote are also the only ones still at 256px.
#
# THIS NUMBER IS THE LOADER'S READ LENGTH. `read_field_background_data` reads
# `w23` bytes per depth-1 page from a strictly sequential stream and advances
# its source cursor by exactly that many (traced to the `rep movsb` at
# +0x9BD038..+0x9BD04C). So a mismatch between this constant and the module's
# `w23` does not degrade the picture -- it desynchronises the TEXTURE walk on
# the FIRST depth-1 page and reads every later page's header out of the
# previous page's pixels.
#
# Raising it to 512 therefore REQUIRES the ten module words in FINDINGS-223
# s4, all of them, in the same build. There is no half-way state and no
# module-only probe. `ff7nx_fieldbg` derives its words from this value so the
# two cannot drift.
D1_PAGE_PX = VANILLA_PX

# The legal values. 512 is the only one asked for; the list exists so a typo
# fails loudly rather than producing a section 9 the engine walks off the end
# of. Each must be a size `PAGE_STORED_BYTES` can express as one ARM64
# immediate, because the read length is a `mov wN, #imm`.
D1_PAGE_PX_CHOICES = (256, 512)


def d1_stored_bytes(px=None):
    """Bytes one depth-1 page occupies in section 9, and the read length."""
    px = D1_PAGE_PX if px is None else px
    if px not in D1_PAGE_PX_CHOICES:
        raise ValueError('depth-1 page side %r is not one of %r'
                         % (px, D1_PAGE_PX_CHOICES))
    return px * px


def stored_bytes(px, depth):
    """Bytes one page of this size and depth occupies in section 9."""
    if depth != 2:
        # `px` is DELIBERATELY IGNORED here, exactly as it was when this
        # returned VANILLA_PX**2. A depth-1 page's side is a property of the
        # BUILD, not of the page -- there is no per-page size field and the
        # loader reads one fixed length -- so callers that pass the depth-2
        # px alongside depth 1 (test_tex_caps does) keep getting the right
        # answer instead of a plausible wrong one.
        return d1_stored_bytes()
    return PAGE_STORED_BYTES.get(px, px * px * 2)


# THE DIMMEST NON-ZERO COLOUR THAT IS NOT A COLOUR. FINDINGS-132.
#
# 0x0000 means transparent on a truecolor page (x86 0x6470E0), so a black pixel
# has to be lifted off it. A paletted page never needs this: black is an INDEX
# and transparency is index 0, a separate channel -- which is exactly why the
# picture looks right with truecolor off and wrong with it on.
#
# The lift was 0x0001 = RGB(0,0,8) -- PURE BLUE, chosen because blue has the
# lowest luminance of the single-bit options (0.9/255 against 4.7 for green).
# Luminance was never the problem. MEASURED on `mkt_mens`, every pixel the
# field draws:
#
#     vanilla   0x0001 =    400 px (0.28%)
#     ours      0x0001 =  6,368 px (4.52%)
#
# FF7's field art is full of black linework, so 4.5% of the picture followed
# the art's own edges in blue -- which is why it reads as blue LINES and not as
# a tint, and why TRUE_BLACK did not help: those cells are detailed, not mostly
# black.
#
# AND A UNIFORM BLACK POINT CANNOT REMOVE IT. `custom_shaders/hd/*.glsl` BLENDS
# BEFORE IT GRADES -- 2xSaL averages the lifted texel with its neighbours, then
# hd_grade_rgb runs -- so the lift ends up inside BRIGHT pixels, where
# subtracting a constant leaves the per-channel offset behind and HD_SATURATION
# pulls the hue further out.
#
# So the lift must be ACHROMATIC, not merely dim. 0x0841 is R1 G2 B1 =
# RGB(8,8,8), green's LSB clear so the 0x07E0 smear rule still holds, paired
# with HD_BLACK_POINT = 0.03137 = 8/255 sized to cancel exactly this lift:
# flat black crushes to 0, blended edges keep a neutral 4/255 instead of blue.
#
# IT WAS TRIED ONCE AND REJECTED AS "GREY AND WEIRD" -- correctly, because that
# build ran the black point at 0.014, which leaves grey standing in every flat
# black area. The grey lift and the 8/255 black point have NEVER been in the
# same build. Both move together or neither does.
NEAR_BLACK = 0x0841          # ACHROMATIC R1 G2 B1 = RGB(8,8,8) in 565.
#                            # Green LSB clear, so the 0x07E0 smear rule holds.
#                            # Paired with HD_BLACK_POINT = 0.03137 = 8/255,
#                            # which is sized to cancel exactly this lift.
#                            # FINDINGS-132; the reasoning is below.
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
# PROBE, build 52: 3 -> 4. See FINDINGS-141.
#
# The measurement behind 3 is `field_bg_repack.DEFAULT_D2_SLOTS_PER_GROUP`:
# "29 does not allocate, measured on mds6_2". That is an ALLOCATION failure,
# not a slot-index or blend-mode limit -- `field_load_textures` makes one
# texture per present page and aborts the whole loop on the first alloc it
# cannot serve. An allocation failure is a function of HEAP SIZE, and this
# build now raises FF7's heap from the port's hardcoded 64 MB to 256 MB
# (`ff7nx_heap.HEAP_MB = 256`; the log says "heap 64 -> 256 MB"). The
# measurement almost certainly predates that.
#
# The engine's own allocator is not the constraint: field_init_bg_pages
# (+0x92D048) does ONE test, `cmp w28, #0x1a; cset lo`, so every slot from
# 0x1A to 0x29 is depth-2 -- sixteen of them, pre-allocated unconditionally.
# Per-field that is ~9.6 MB; a 4th truecolor page is another 512 KB against
# a 256 MB pool.
#
# ONE SLOT, NOT FOUR. If 29 allocates, 30-32 follow for free and the next
# build can take the group to 7 -- which is what actually matters, because
# the dense repack wants 6.18 pages per field and is being given 2.61.
# If 29 still does not allocate, the failure is loud and specific (black
# rectangles in the ~303 fields that currently fill slot 28) and this is one
# number to put back.
# SEVEN. READ OUT OF THE ORIGINAL x86, NOT INFERRED. FINDINGS-143.
#
# `field_load_textures`, x86 0x640292 -- the function the Switch port
# recompiles, disassembled from game_data_files/ff7_1.02/ff7_en:
#
#     006402C1  cmp  ecx, 0x2a      ; the loop runs slots 0..41
#     006402C4  jge  0x640604
#     006402D3  mov  eax, [edx*4 + 0xcffc70]    ; field_layers[i]
#     006402DA  cmp  dword ptr [eax + 0xc], 0   ; ->present
#     006402DE  je   0x6405ff                   ; absent -> skip, no texture
#     ...
#     006403B8  (type == 2 path)
#     006403C0  cmp  eax, 0x21      ; 33
#     006403C3  jl   0x64042b       ;   < 33 -> blend 4  OPAQUE
#     006403CE  cmp  ecx, 0x28      ; 40
#     006403D1  jl   0x6403dc       ;  33..39 -> blend 1
#     006403D3  mov  [ebp-8], 0     ;  40..41 -> blend 0
#
# and the READER, x86 0x62B6F1, covers the same range:
#
#     0062D0CB  cmp  [ebp-0xb4], 0x2a
#     0062D0E5  add  edx, 0xc       ; &layer->present, READ FROM THE FILE
#
# So opaque truecolor is slots 26..32 -- SEVEN -- and the groups below are
# now an exact mirror of that ladder.
#
# WHY THE OLD VALUE WAS 3, AND WHY THAT WAS NEVER AN ENGINE LIMIT
# ---------------------------------------------------------------
# It came from "every depth-2 page vanilla ships is in slot 26, 27 or 28"
# plus "builds that used 29+ produced black squares". Both true; neither is
# a limit. FFNx's `ff7/field/field.cpp` does say `for(i = 0; i < 29; i++)`,
# but FFNx REPLACES that function (ff7_opengl.cpp:115 `replace_function`) --
# it is FFNx's own narrowing, not this engine.
#
# MEASURED, cells that would become truecolor at each ceiling:
#     3 pages -> 62.8% of cells;  the other 37.2% go through the quantiser,
#     which is where every remaining colour defect in this project lives.
# Pages a field needs to be 100%% truecolor:
#     1p:46  2p:246  3p:166  4p:179  5p:49  6p:11  7p:4  fields
# Seven covers the archive.
#
# EXPLAINED AT LAST -- FINDINGS-168. The paragraph below used to end "STILL
# UNEXPLAINED: build 52 set this to 4 and produced black squares. The engine
# loop, the blend ladder, and our own section-9 writer (round-trip verified,
# slot 29 byte-identical) all permit it."
#
# All three of those DO permit it. None of them is what runs. The Switch port
# does not execute the x86 loop; it calls a NATIVE reimplementation of
# `field_load_textures` at module offset 0x10DC370, and that function ends its
# slot loop at
#
#     0x10DC4A4  cmp x23, #0x1d          ; 29, against the x86's 42
#
# So a page in slot 29 is never loaded, never becomes a texture, and every
# tile naming it draws nothing. Black squares, no crash -- build 52 exactly.
# The reading was right about the archive and looking at the wrong binary.
#
# `ff7nx_fieldbg` now patches that bound to 0x21 whenever this constant would
# put a page past slot 28, which is why 7 is now safe to ask for. The two
# numbers are ONE fact and must move together; `_load_slots_word()` derives
# the bound from this constant rather than repeating it, and refuses outright
# if this goes past 7 -- the depth-2 ADDITIVE band beyond slot 0x20 has no
# blend ladder on this port (that same function gives every depth-2 page
# blend 4 with no slot test), so loading those slots would draw an additive
# effect as an opaque patch.
#
# MEASURED, pages a field needs to be 100% truecolor:
#     1p:46  2p:246  3p:166  4p:179  5p:49  6p:11  7p:4  fields
# Seven covers the archive. Three covers 62.8% of cells.
    # RAISED 3 -> 7, BUILD 106, FINDINGS-216.
    #
    # The "3" above is the record of builds 52 and 55, and the paragraph that
    # explains them (FINDINGS-168) is the reason this can now move: the black
    # squares were the NATIVE `field_load_textures` ending its slot loop at
    # `cmp x23, #0x1d`, not an allocation failure and not the archive.
    # `ff7nx_fieldbg._load_slots_word()` patches that bound, derives it from
    # THIS constant so the two cannot drift, and refuses outright past 7
    # because slot 0x21+ has no blend ladder on this port.
    #
    # The other two numbers that argued for 3 were also stale: the heap is
    # `ff7nx_heap.HEAP_MB` = 256, not the 64 FINDINGS-106 measured, and
    # `max_total_pages()` is 16, not 12.
    #
    # MEASURED, all 741 entries, `_kslotcensus.py`: 0 fields lose a promoted
    # cell, 0 gain a page, 0 exceed the 16-page ceiling, 0 put a depth-2 page
    # above slot 0x20. Six fields gain -- all five Highwind bridge variants
    # (+256 cells each) and crater_1 (+64). Heaviest field background goes
    # 11.75 -> 13.31 MB against a 256 MB heap.
    #
    # ---- REVERTED TO 3 AFTER BUILD 106 CRASHED ON HARDWARE. FINDINGS-218.
    #
    # BUILD 106 RAISED THIS TO 7 AND THE GAME ABORTS LOADING ANY SAVE.
    # The evidence is a clean A/B and it is not arguable:
    #
    #   * every Rocket Town field (where the save was) is BYTE-IDENTICAL in
    #     section 9 between the working build 105 and the crashing 106;
    #   * the ONLY difference between the two `exefs/main` is ONE WORD --
    #     0x10DC4A4, `cmp x23, #0x1d` -> `cmp x23, #0x21`;
    #   * so raising the loader bound, on its own, is what crashes.
    #
    # WHY, FROM THE DISASSEMBLY OF THE LOOP THE BOUND TERMINATES:
    #
    #     0x10DC39C  mov  w20, #0xFC70 ; movk w20, #0xCF, lsl #16
    #     0x10DC3AC  bl   #0x10FC3A0          guest -> host
    #     0x10DC3B0  mov  x22, x0             x22 = the PAGE POINTER TABLE
    #     0x10DC3BC  ldr  w0, [x22, x23, lsl #2]   table[slot], u32 each
    #     0x10DC3C0  cbz  w0, skip
    #     0x10DC3C4  bl   #0x10FC3A0          guest -> host  ON THAT POINTER
    #     0x10DC4A4  cmp  x23, #0x1d          <- THE BOUND
    #     0x10DC4A8  b.ne loop
    #
    # The bound is the ONLY thing keeping this walk inside the populated part
    # of the table. Past it the entries are not valid guest pointers, and
    # handing one to the guest->host translation aborts -- which is exactly
    # the crash: `nn::diag::detail::Abort` reached through 0x10FC3A0 and
    # map_region, with no field data involved.
    #
    # SO FINDINGS-168 WAS WRONG, AND SO WAS I. It read the `cmp` and
    # concluded 29 was "this port's own narrowing" of the x86's 42. The
    # instruction tells you the bound; it does NOT tell you what the table
    # holds beyond it. Builds 52 and 55 (black squares) and build 106 (abort)
    # are the SAME defect seen at two severities.
    #
    # The comment in `7th_heaven_nx.FIELD_BG_TRUECOLOR_CHOICES` said it
    # already, and I talked myself past it: "Do not raise this again without
    # runtime evidence FROM THE PORT ITSELF. Two builds and a full read of
    # the x86 were not enough to predict it." Reading one more instruction is
    # not runtime evidence either. That now costs three builds.
    #
    # WHAT WOULD ACTUALLY BE NEEDED: populate table[29..32] with valid page
    # records before the loader runs -- i.e. patch `field_init_bg_pages`
    # (+0x92CE70..+0x92D3A0), not just the loop bound. Until that is
    # understood and MEASURED, this constant stays at 3.
    #
    # `_load_slots_word()` is subtractive at 3, so this single line also
    # removes the module patch and restores build 105 exactly.
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


# Section 9's terminator. `blackbgb` writes it three times and
# `blackbgb.xone` twice; every other field writes it once. See the note at the
# end of parse_texture_block.
END_MARKER = b'ENDFINAL FANTASY7'


def parse_texture_block(sec9, px=VANILLA_PX, strict_tail=True):
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
        side = px if depth == 2 else D1_PAGE_PX
        nbytes = side * side * depth
        nstored = stored_bytes(side, depth)
        if o + nstored > n:
            raise Section9Error('truncated pixels at slot %d' % slot)
        # Page.data stays exactly the PIXELS; the padding a non-power-of-two
        # size needs is a storage detail and never reaches a caller.
        pages.append(Page(slot, size_flag, depth, sec9[o:o + nbytes], side))
        o += nstored
    # WHAT `blackbgb` IS, SO NOBODY HAS TO WONDER AGAIN. (FINDINGS-125)
    #
    # This check catches a DESYNCHRONISED walk: a wrong `px` reads pixel data as
    # headers and ends nowhere near the end of the block, so requiring the walk
    # to land near it is what makes a wrong size fail loudly instead of silently
    # returning garbage.
    #
    # Two fields fall foul of it, in VANILLA, byte-identically in our build --
    # `blackbgb` (148 trailing bytes) and `blackbgb.xone` (134). Their section 9
    # writes the terminator `ENDFINAL FANTASY7` two or three times over with
    # zero padding, where the rest of the game writes it once. A 1997 data
    # quirk. We are not corrupting them; we are declining to read them, and the
    # `! ... 2 field(s) not changed` warnings in every build are exactly that.
    #
    # MEASURED, trailing bytes over all 709 vanilla fields:
    #
    #        3 bytes  583 fields   (just `END`)      20 bytes  1 field (las2_1)
    #       17 bytes  124 fields   (the full marker) 22 bytes  1 field (fship_4)
    #      134 bytes    1 field    (blackbgb.xone)
    #      148 bytes    1 field    (blackbgb)
    #
    # I TRIED TO WIDEN THIS AND IT WAS A MISTAKE. Matching "the tail must be
    # terminators and zeros" rejected 704 of 709 fields, because the usual
    # terminator is the three bytes `END`, not the full string. Matching
    # prefixes instead fixed those but broke `fship_4` and `las2_1`, whose tails
    # are the full marker followed by a SUFFIX fragment -- `...FANTASY7TASY7`,
    # `...FANTASY7SY7`. Fixing two fields by breaking two others is not a fix.
    #
    # And the prize was never worth it: both are BLACK BACKGROUND fields used
    # for fades. There is no margin art to add to a black screen, so skipping
    # them costs nothing visually. The warnings are noise, not damage.
    #
    # Left exactly as it was. If it is ever revisited, the rule has to accept
    # arbitrary marker FRAGMENTS, and it needs to be checked against all 709
    # fields before it ships -- both of my attempts passed the two fields I was
    # looking at and failed the archive.
    if not 0 <= n - o <= 64:
        if strict_tail:
            raise Section9Error(
                'TEXTURE block leaves %d trailing bytes' % (n - o))
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


def scrub_green_lsb(pages):
    """
    Clear the green LSB on every depth-2 texel. Returns texels changed.

    THE INVARIANT IS DOCUMENTED, VANILLA SATISFIES IT, AND WE BREAK IT.

    The engine's non-565 display path (x86 0x63F350) shifts six bits of green
    into a five-bit field and ORs green's low bit onto the TOP BIT OF BLUE. A
    texel with 0x0020 set therefore gains a large blue component on that path.
    `field_bg_dense` masks its own output twice for this reason.

    MEASURED on the shipped build, 5 fields -- `blin67_4`, `fr_e`, `cosmo`,
    `astage_b`, `cosmo2` -- 2.2 million texels, whole pages at a time:

        vanilla blin67_4 slot 26   green LSB set on   0% of texels
        ours    blin67_4 slot 26   green LSB set on 100%

    and every value is EXACTLY vanilla's plus 0x0020, with the same 1,324
    distinct values:

        vanilla 0x398E -> ours 0x39AE      vanilla 0x4002 -> ours 0x4022
        vanilla 0x39D0 -> ours 0x39F0      vanilla 0x414C -> ours 0x416C

    So something ORs 0x0020 across whole pages. It is NOT `resize_depth2` --
    that moves 2-byte units and never interprets them, verified by running it
    on the vanilla page and getting 0% odd out. **The writer has not been
    found.** One page of `blin67_4` is filled entirely with the constant
    0x0020, which is the exact value the comment above `PAGE_STORED_BYTES`
    warns against, and the constant that comment documents no longer exists in
    this file -- it was deleted and its rationale left behind. That is the
    thread to pull next.

    This is a BACKSTOP, not the fix. It clears one bit of green -- below the
    8-bit quantisation step, the same argument `field_bg_dense` already makes
    for masking its own output -- so it cannot make anything worse, and it
    restores the invariant whatever wrote it. The counter it returns is how we
    find out if the real writer is ever fixed: it should fall to zero.
    """
    import numpy as _np
    n = 0
    for i, p in enumerate(pages):
        if p is None or p.depth != 2:
            continue
        a = _np.frombuffer(p.data, _np.uint16)
        bad = int((a & 0x0020).astype(bool).sum())
        if not bad:
            continue
        n += bad
        pages[i] = Page(p.slot, p.size_flag, p.depth,
                        (a & _np.uint16(0xFFDF)).tobytes(), p.px)
    return n


def replace_texture_block(sec9, pages, tex_start, tex_end):
    return sec9[:tex_start] + build_texture_block(pages) + sec9[tex_end:]


# ------------------------------------------------- depth-1 resolution lift
def resize_depth1(data, src_px, dst_px):
    """
    Nearest-neighbour integer resize of a PALETTED page. Indices, not colours.

    Replication and not a filter, and here that is not a preference -- an
    index is a name, so the average of index 3 and index 200 is index 101,
    which is a different colour entirely and usually not even a neighbouring
    one. Index 0 is additionally the transparency key, so any blend across a
    transparent edge would invent an opaque colour where the art has a hole.
    This moves single bytes and never interprets them.
    """
    if dst_px == src_px:
        return data
    try:
        import numpy as _n
    except ImportError:
        _n = None
    if dst_px < src_px:
        # DECIMATION, and it exists so the lift can be PROVEN reversible.
        # Taking the top-left of each k x k block is the exact inverse of
        # replication, so `_k512gate`'s falsifier 2 is a real test of where
        # the upscale put its texels rather than a test that some resize
        # ran. It is not for producing art.
        if src_px % dst_px:
            raise ValueError('depth-1 resize %d -> %d is not an integer ratio'
                             % (src_px, dst_px))
        k = src_px // dst_px
        if _n is not None:
            a = _n.frombuffer(data, dtype=_n.uint8, count=src_px * src_px)
            return a.reshape(src_px, src_px)[::k, ::k].tobytes()
        out = bytearray(dst_px * dst_px)
        for y in range(dst_px):
            srow = data[y * k * src_px:y * k * src_px + src_px]
            out[y * dst_px:(y + 1) * dst_px] = srow[::k]
        return bytes(out)
    if dst_px % src_px:
        raise ValueError('depth-1 lift %d -> %d is not an integer ratio'
                         % (src_px, dst_px))
    k = dst_px // src_px
    if _n is not None:
        a = _n.frombuffer(data, dtype=_n.uint8, count=src_px * src_px)
        return _n.repeat(_n.repeat(a.reshape(src_px, src_px), k, 0),
                         k, 1).tobytes()
    out = bytearray(dst_px * dst_px)
    for y in range(src_px):
        row = bytearray(dst_px)
        srow = data[y * src_px:(y + 1) * src_px]
        for x in range(src_px):
            row[x * k:x * k + k] = bytes([srow[x]]) * k
        for r in range(k):
            base = (y * k + r) * dst_px
            out[base:base + dst_px] = row
    return bytes(out)


def lift_depth1(sec9, page_px, src_d1_px=VANILLA_PX, dst_d1_px=None,
                art=None, tolerate_tail=True):
    """
    Rewrite every depth-1 page in `sec9` from `src_d1_px` to `dst_d1_px`.

    THIS IS THE ONLY PLACE THE DEPTH-1 PAGE SIZE CHANGES, and it runs LAST,
    after every pass that reads or writes paletted art. Everything upstream
    keeps working in 256-unit coordinates and does not know this exists --
    which is what makes the resolution lift separable from the ART change
    that follows it (FINDINGS-223 s6).

    `art`, when given, is `f(slot, px) -> bytes | None`: a replacement index
    page already at `dst_d1_px`, for the build that sources Cosmos's own
    resolution instead of replicating. Left None this is a pure 2x nearest
    upscale, whose EXPECTED ON-SCREEN RESULT IS AN IDENTICAL PICTURE -- the
    same texels, four times each, over the same normalised UV extent.

    Returns (new_sec9, n_lifted). Raises rather than half-converting.
    """
    global D1_PAGE_PX
    dst = D1_PAGE_PX if dst_d1_px is None else dst_d1_px
    if dst not in D1_PAGE_PX_CHOICES:
        raise ValueError('depth-1 page side %r is not one of %r'
                         % (dst, D1_PAGE_PX_CHOICES))
    keep = D1_PAGE_PX
    try:
        # Parse at the size the section ACTUALLY holds, which is not
        # necessarily the size this build writes. Doing this by hand rather
        # than by threading a parameter through six call sites is deliberate:
        # the walk has to consume the block exactly, so a wrong src_d1_px
        # raises here instead of returning a plausible garbage page.
        D1_PAGE_PX = src_d1_px
        try:
            pages, s, e = parse_texture_block(sec9, page_px)
        except Section9Error:
            # `blackbgb` AND `blackbgb.xone`, AND NOTHING ELSE. Their section
            # 9 writes the END marker two or three times with zero padding,
            # so the trailing-tail check refuses them and every build to date
            # has reported "2 field(s) not changed" and moved on.
            #
            # THAT IS NO LONGER AN OPTION. Declining to read a field used to
            # mean shipping it unchanged, which was harmless. Under a 512px
            # module it means shipping two fields whose paletted pages are
            # still 256 while the loader reads 0x40000 -- a desynchronised
            # walk on a field the game really does load between scenes.
            #
            # The PAGES parse perfectly; only the tail is odd. So walk them
            # with the tail check off and keep everything past `tex_end`
            # byte-for-byte, which is what `replace_texture_block` does
            # anyway. This is deliberately NOT a general widening of the
            # check -- the check is what makes a wrong `px` fail loudly, and
            # widening it was tried once and was a mistake (see the note in
            # parse_texture_block).
            if not tolerate_tail:
                raise
            pages, s, e = parse_texture_block(sec9, page_px, strict_tail=False)
    finally:
        D1_PAGE_PX = keep
    n = 0
    for p in pages:
        if p is None or p.depth != 1:
            continue
        if p.px != src_d1_px:
            raise ValueError('slot %d is %dpx, expected %d'
                             % (p.slot, p.px, src_d1_px))
        new = art(p.slot, dst) if art is not None else None
        if new is None:
            new = resize_depth1(p.data, src_d1_px, dst)
        if len(new) != dst * dst:
            raise ValueError('slot %d lifted to %d bytes, expected %d'
                             % (p.slot, len(new), dst * dst))
        p.data = new
        p.px = dst
        n += 1
    if not n:
        return sec9, 0
    try:
        D1_PAGE_PX = dst
        out = replace_texture_block(sec9, pages, s, e)
    finally:
        D1_PAGE_PX = keep
    return out, n


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
        side = page_px if d2 else D1_PAGE_PX
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
