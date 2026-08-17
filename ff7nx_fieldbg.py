#!/usr/bin/env python3
"""
ff7nx_fieldbg.py -- make field background TRUECOLOR pages bigger than 256x256.

Scope, stated first because it is narrower and safer than the plan in
HANDOFF-field-background-upscale.md:

    ONLY depth==2 (16-bit truecolor) pages change size.
    depth==1 (8-bit paletted) pages keep 256x256, byte for byte.

That is not a compromise, it is the discovery that makes the patch safe. The
handoff feared `w23` (#0x10000) was shared between the depth-1 read and the
depth-2 allocation, and it is -- three readers, traced below. But the
allocation SIZE is not the only lever: the allocator takes (count, size) and
multiplies, and the `size` argument is a SEPARATE hoisted register that only
the depth-2 branch reads. Scaling that instead leaves `w23` untouched, so a
field can hold 256x256 paletted pages and 512x512 truecolor pages at the same
time and both draw correctly.

Everything below is MEASURED against
    exefs/main   md5 c5cbcec798ab854b828a149870deb473  (NSO, 9,357,691 bytes)
    ff7_en       md5 ca7284c38d058f7c167a13e00fe72441  (stock PC 1.02 x86)
and every replacement word was round-tripped through capstone, not derived
on paper. See README-field-bg-512-MEASURED.md for the full derivation.


WHY THIS IS SAFE AT ALL -- the UV question, answered
----------------------------------------------------
The handoff called the tile UV computation "the last thing that can kill the
project" and flagged the previous session's answer as an inference dressed up
as a measurement. It has now been read:

  * `field_tile.u` / `.v` are NOT computed from a pixel coordinate. They are
    read straight out of flevel section 9 as int32 fixed-point and divided by
    1e7 -- x86 0x62BCCE / 0x62BD1C / 0x62BD69 (layer 1) and the three matching
    triples for layers 2/3/4. `fdiv dword ptr [0x7B7890]`, and 0x7B7890 holds
    10000000.0. So the UVs arrive ALREADY NORMALISED, from the file.
  * The per-tile UV EXTENT is a normalised literal, chosen by the page's own
    `size` flag, in the vertex builder at x86 0x6465FB:
        0x6467B7  mov [ebp-4], 0x3D800000   ; 0.0625 == 16/256
        0x6467C9  mov [ebp-4], 0x3E000000   ; 0.125  == 32/256
    In the module those are the only two float literals in the whole 3,012
    instruction body (+0xA09960 and +0xA098DC). There is no `fdiv`, no
    `scvtf`, no `ucvtf` anywhere in it.
  * `add_page_tile` (x86 0x6464BA) only appends {x,y,z,u,v,palette} to a
    per-page list. No arithmetic at all.

Normalised UV + normalised extent == resolution independent. A page that
holds the same layout at 2x carries identical u, v and extent. Nothing to
patch, and nothing that silently draws the wrong quarter.

The one resolution-linked constant in that function is the half-texel bleed
guard, `mov w22, #-0x45200000` at +0xA09680 (0xBAE00000 == -0.4375/256),
which feeds all sixteen D000xx table stores. It is OPTIONAL here and OFF by
default -- see HALVE_BLEED_ENV.


THE SITES -- every one of them, with its readers enumerated
------------------------------------------------------------
A. read_field_background_data, x86 0x62B6F1 -> main +0x92D3A0..+0x937740.
   Four constants in the x86 page loop, hoisted into three registers:

       w23 = #0x10000   3 readers  +0x9374E0 depth-1 alloc count
                                   +0x937540 depth-1 read bytes
                                   +0x937590 depth-2 alloc count
       w25 = #2         1 reader   +0x93757C depth-2 alloc ELEMENT SIZE
       w27 = #0x20000   1 reader   +0x9375F0 depth-2 read bytes
       (w22 = #0xDF2 and w24 = #0xDFF are __LINE__ values; w21 = #1 is the
        depth-1 element size and also the stub readers' return value.)

   The allocator (x86 0x65FF59) is `alloc(count, size, file, line)` and
   computes `count * size + 0x20` -- read at 0x65FF7A, `imul ecx,[ebp+0xc]`.
   So scaling w25 from 2 to 8 gives 0x10000 * 8 == 0x80000 bytes, exactly a
   512x512 16-bit page, WITHOUT touching w23.

   PATCH  +0x9370C8  mov w25, #2        -> #8         (2 * scale^2)
   PATCH  +0x9370CC  mov w27, #0x20000  -> #0x80000   (0x20000 * scale^2)
   LEAVE  +0x9370C0  mov w23, #0x10000               depth-1 stays 256

B. field_init_bg_pages, x86 0x63F494 -> main +0x92CE70..+0x92D3A0.
   Pre-allocates page->data for all 42 slots before the loader replaces it:
   slots 0x00..0x19 as depth 1, slots 0x1A..0x29 as depth 2. Same shape:

       w26 = #0x10000   shared count
       w28 = #1         depth-1 element size   (+0x92D0A0)
       w28 = #2         depth-2 element size   (+0x92D15C)

   PATCH  +0x92D15C  mov w28, #2 -> #8

C. field_convert_type2_layers, x86 0x63F385 -> main +0xA02430..+0xA028D0.
   Walks EVERY pixel of EVERY depth-2 page, replacing colour 0 with the
   converted colour key and remapping 0x821 -> 0x403. The x86 bound is
   `cmp dword [ebp-0x14], 0x10000` at 0x63F421. The recompiler emitted the
   signed compare as sign-of-difference:

       +0xA026C8  sub w9, w8, #0x10, lsl #12    <- the bound, SF term
       +0xA026CC  sub w8, w22, w8               <- OF term, w22 = #0xFFFF

   Left unpatched, three quarters of every 512x512 page keeps raw colour 0
   and shows as black instead of transparent.

   PATCH  +0xA026C8  sub w9, w8, #0x10, lsl #12 -> #0x40, lsl #12
   LEAVE  +0xA02530  mov w22, #0xffff
       -- because the OF term is provably always 0 here: it is
          `(0xFFFF - i) & (hi16(i) << 16)`, and `hi16(i) << 16` has bit 31
          clear for every i below 0x40000. w22 has exactly one reader
          (+0xA026CC) and w21 (#0x29, the 42-slot bound) exactly one
          (+0xA02584), so this is a decision not to touch a proven-dead
          value rather than an omission.

D. make_field_tex_header (the depth==2 configurator), x86 0x63FAAB ->
   main +0xA038D0..+0xA03CF0. Surface descriptor at texheader+0x3C:

   PATCH  +0xA03C44  mov w19, #0x100 -> #0x200   width AND height (one reg)
   PATCH  +0xA03C88  mov w8,  #0x200 -> #0x400   stride == width * 2

   make_field_tex_header_pal (depth==1, x86 0x63FBA3 -> +0xA03CF0) is NOT
   touched. Its `mov w23, #0x100` at +0xA040D8 feeds four stores, two of
   which are palette entry counts -- the trap the old README named. It stops
   being a trap when the depth-1 path simply keeps its old size.


E. THE FIELD DECOMPRESSION BUFFER -- the one that actually crashed
------------------------------------------------------------------
A field file is decompressed WHOLE into one buffer before section 9 is read
out of it, and that buffer is a FIXED size, not the field's size:

    x86 0x6307D6   mov  edx, [0xCFF598]
    x86 0x6307DD   call 0x65FDA1            ; alloc(edx, file, line)
    x86 0x6307E5   mov  [0xCFF594], eax     ; <- the field buffer
    x86 0x630826   call 0x6305C0            ; LZS-decompress into it

`[0xCFF598]` comes from a per-field table at `0xCC233C` (stride 0xD0), which
x86 0x60F65A fills from a data file called `flevel.siz`:

    for i in 0..0x312:  table[i] = flevel_siz[i] + 0x1E8480

**This port has no `flevel.siz`.** It is not in the romfs -- the field
directory holds only the .lgp files. The open fails, the loop never runs, the
table stays zero, and every field is therefore decompressed into exactly
`0 + 0x1E8480` = **2,000,000 bytes**.

MEASURED. Vanilla's largest field is well under that. A build with a handful
of rescaled truecolor pages peaked at 2,209,620 bytes in `zmind1` and
overflowed in 4 late-game fields nobody had walked into. A full repack puts
4-8 truecolor pages in every field, 4-5 MB decompressed, and the first field
loaded smashes the heap.

The fix is the load, not the table -- the table is never written, so patching
its producer would do nothing:

    +0x00921A4C  ldr w23, [x0]   ->   mov w23, #<size>

`size` is computed from the largest field the build ACTUALLY produced, rounded
up to the next power of two (every power of two is a legal AArch64 logical
immediate) with 25% headroom. It is not a guess and not a fixed number: a
build that makes bigger fields patches in a bigger buffer.

This costs one allocation per field load, freed when the field unloads.


WHAT IS PROVABLY NOT A SITE
---------------------------
Three functions index a page's pixels with a hardcoded 256-pixel row stride
or a 0x10000/0x20000 size, and all three are DEAD in this build -- zero call
sites and zero address-taken references anywhere in .text, .rdata or .data:

    0x62A0E7  write_field_background_data (0x62B571 / 0x62B592)
    0x6428B7  page -> locked surface blit  (256 rows, src += 0x200)
    0x623D28  the 25 KB PSX-style tile rasteriser, `data[(y << 8) + x]`,
              reached only through 0x620BD3, which nothing reaches

If a future build ever calls one of them, this patch set is wrong. The
verifier below re-checks that they are still unreferenced.

Requires: lz4 (via nso_patcher), capstone only for the self-test.
"""
import os
import struct
import sys

PAGE_PX_ENV = 'SEVENTH_NX_FIELD_BG_PAGE_PX'
HALVE_BLEED_ENV = 'SEVENTH_NX_FIELD_BG_HALVE_BLEED'

VANILLA_PAGE_PX = 256

# OFF is a SEPARATE value from 256, and that distinction is the whole point
# of this ladder.
#
# Until now `page_px() == 256` meant "do nothing": build.py's
# `if px == field_bg_native.VANILLA_PX: return 0, 0, 0` skipped the entire
# field-background pass, module patch and repack together. So there was no
# way to ask for "pages stay 256x256 but become TRUECOLOR" -- the one row
# HANDOFF-52 3.3 calls the interesting one had never been tried because the
# UI could not express it.
#
#   OFF_PAGE_PX (0)  -> no module patch, no repack. Stock game.
#   256              -> module patch is a no-op (the words already say 256),
#                       but the REPACK runs, so the mod's paletted art is
#                       promoted to truecolor at vanilla resolution.
OFF_PAGE_PX = 0

# WHICH SIZES ARE ACTUALLY LEGAL -- derived, not chosen
# -----------------------------------------------------
# HANDOFF-52 3.1 said "N must be a multiple of 256" because it read the
# element size at SITE_ALLOC_ELEM / SITE_PREALLOC as having to be exactly
# N^2*2/0x10000. That is the wrong constraint twice over:
#
#   * `elem` is an ALLOCATION size, not a stride. The allocator (x86
#     0x65FF59) computes `count * size + 0x20` and hands back one buffer per
#     page, so `elem = ceil(N^2*2 / 0x10000)` is correct and over-allocating
#     is harmless -- it just wastes the tail of the last 64 KB.
#   * The real gate is SITE_READ_BYTES: the loader reads exactly N^2*2 bytes
#     out of the field stream, that value has to be EXACT (reading more
#     desynchronises everything after it), and it has to fit in ONE
#     instruction because a site is one word wide.
#
# Brute-forcing mov_imm_word over every multiple of 16 gives:
#
#     16..176 step 16   (movz, since N^2*2 <= 0xFFFF)
#     256, 512, 768, 1024   (movz lsl 16, since N^2*2 is a multiple of
#                            0x10000 exactly when N is a multiple of 256)
#
# ...if the read has to be EXACT. It does not. The loader reads a fixed
# count per page, so the file simply has to hold that many bytes -- PAD the
# read up to the next encodable immediate and any multiple of 16 becomes
# available, with the tail never sampled (the surface is px wide with a
# px*2 stride). See read_bytes(). That is what puts 320/384/448 on the
# ladder, at 2-16% storage waste on a term that is only a third of the cost:
#
#     320 -> read 0x38000 (11% pad)   0.61 MB a page
#     384 -> read 0x50000 (10% pad)   0.88 MB
#     448 -> read 0x70000 (12% pad)   1.20 MB
#                                     (256 is 0.38, 512 is 1.50)
#
# The second gate is field_bg_native.resize_depth2, which rescales the 51
# vanilla depth-2 pages (27 fields, MEASURED off flevel.lgp) and only does
# INTEGER ratios against 256. That leaves 128 (exact 2:1 down) and the
# multiples of 256 (exact k:1 up). 144/160/176 would need a general
# resampler and buy 0.15-0.18 MB/page against 128's 0.09 -- not worth the
# quality question on those 51 pages.
SUPPORTED_PAGE_PX = (128, 256, 320, 384, 448, 512, 768, 1024)


def read_bytes(px):
    """
    The depth-2 read count for `px` -- what SITE_READ_BYTES must hold, and
    therefore exactly how many bytes section 9 stores per page.

    px*px*2 when that is a one-word immediate, otherwise the next larger
    value that is. The PADDING is what makes 320/384/448 possible at all:
    the loader reads a fixed count, so the file just has to hold it, and the
    tail is never sampled -- the surface is px wide with a px*2 stride.

    field_bg_native.PAGE_STORED_BYTES holds the same numbers for the format
    side; test_tex_caps.py checks they agree.
    """
    want = px * px * 2
    if mov_imm_word(27, want) is not None:
        return want
    v = want
    limit = want * 2 + 0x10000
    while v <= limit:
        v += 0x200
        if mov_imm_word(27, v) is not None:
            return v
    raise ValueError('field bg %dpx: no encodable read size' % px)


def page_cost_bytes(px, depth=2):
    """
    What ONE page of this size costs in memory: its own pixels PLUS the
    32bpp surface the engine builds from them (x86 0x63FAAB writes
    bits-per-pixel 0x20 at +0x28). Mirrors field_bg_repack._page_bytes.

    At depth 2 this is 6*px^2. MEASURED across all 709 fields of a real
    flevel, whose heaviest (fship_2) has 12 pages:

        page size     per page     fship_2 fully promoted
        128            0.09 MB      1.12 MB
        256            0.38 MB      4.50 MB
        512            1.50 MB     18.00 MB   <- black bars measured here
        768            3.38 MB     40.50 MB
        1024           6.00 MB     72.00 MB
        vanilla d1     0.31 MB      3.75 MB

    So 128 truecolor is CHEAPER than the paletted pages the game already
    ships, and 256 truecolor costs 21% more than they do.
    """
    return px * px * depth + px * px * 4

# --------------------------------------------------------------------- sites
# (module offset, original word, register, what it holds at 256)
SITE_ALLOC_ELEM = 0x9370C8      # mov w25, #2        loader, depth-2 elem size
SITE_READ_BYTES = 0x9370CC      # mov w27, #0x20000  loader, depth-2 read
SITE_PREALLOC   = 0x92D15C      # mov w28, #2        init, depth-2 elem size
SITE_CVT_BOUND  = 0xA026C8      # sub w9, w8, #0x10, lsl #12
SITE_SURF_WH    = 0xA03C44      # mov w19, #0x100    width and height
SITE_SURF_PITCH = 0xA03C88      # mov w8,  #0x200    stride
SITE_BLEED      = 0xA09680      # movz w22, #0xBAE0, lsl #16
SITE_FIELD_BUF  = 0x921A4C      # ldr w23, [x0]  <- [0xCFF598], the field
                                #                   decompression buffer size

# ------------------------------------------------------------- FINDINGS-168
# THE PORT LOADS 29 PAGE SLOTS. THE x86 LOADS 42.
#
# `field_load_textures` is x86 0x640292. Through nxmap that is a recompiler
# stub at 0x937AE0 which calls the NATIVE reimplementation at 0x10DC370.
# Identified three ways, all measured, none inferred:
#
#     0x10DC39C  mov  w20, #0xfc70
#     0x10DC3A0  movk w20, #0xcf, lsl #16      ; w20 = 0x00CFFC70, field_layers
#     0x10DC3CC  ldr  w8, [x24, #0xc]          ; ->present, as the x86 reads it
#     0x10DC3D4  ldrh w8, [x24, #0x1a]         ; the page TYPE, from the file
#
# and its loop terminates at
#
#     0x10DC4A0  add  x23, x23, #1
#     0x10DC4A4  cmp  x23, #0x1d               ; 29  <-- THIS
#     0x10DC4A8  b.ne #0x10dc3bc
#
# against the x86's `cmp ecx, 0x2a` -- 42. It is FFNx's `for(i = 0; i < 29;
# i++)` (ff7/field/field.cpp) baked into the Switch reimplementation.
#
# THIS IS WHY BUILDS 52 AND 55 PRODUCED BLACK SQUARES. A page in slot 29 is
# never loaded, so it never becomes a texture, so every tile naming it draws
# nothing. No crash, black squares -- exactly what was reported, and what
# field_bg_native.py records as "STILL UNEXPLAINED ... needs runtime
# evidence". It needed static reading of the right binary.
#
# NOTHING ELSE IS SIZED 29. Whole-image scan for every site that materialises
# 0xCFFC70 -- nine of them:
#
#     0x92CE94  field_init_bg_pages   cmp w8, #0x2a   42   <- the ALLOCATOR
#     0x9E9A10                        cmp w8, #0x2a   42   (x2)
#     0xA02958                        cmp w8, #0x2a   42   (x3)
#     0xA02BEC                        cmp w8, #0x2a   42
#     0x9F1FC8 0x9F3108 0xA02450      no slot bound
#     0x10DC39C/0x10DC580             cmp x23, #0x1d  29   <- THE ONLY OUTLIER
#
# The storage for all 42 slots is allocated; only the loader refuses to look
# past 29. There is no 29-element array to overrun, which is what makes this
# a bound change rather than a rewrite.
#
# WHY 33 AND NOT 42. 33 (0x21) is the end of the depth-2 OPAQUE band
# (0x1A..0x20), so it covers every slot a page we write can legally occupy.
# The bands past it -- depth-2 additive 0x21..0x27 and average 0x28..0x29 --
# are unreachable for a second, independent reason: this same function gives
# EVERY depth-2 page blend 4 with no slot test at all
#
#     0x10DC3D8  cmp   w8, #1
#     0x10DC3DC  b.ne  #0x10dc410      ; depth 2 ->
#     0x10DC410  mov   w4, #4          ;   OPAQUE, unconditionally
#
# so the x86's depth-2 additive ladder has no counterpart here to switch on.
# Loading those slots would buy nothing and widen the blast radius. Raise
# this to 0x2a only alongside a real blend-ladder trampoline.
SITE_LOAD_SLOTS = 0x10DC4A4     # cmp x23, #0x1d   field_load_textures bound

# The slot the loop must reach to cover the whole depth-2 opaque band. The
# loop is `cmp / b.ne`, i.e. exclusive, so this is one past slot 0x20.
LOAD_SLOTS_FOR_D2_OPAQUE = 0x21
ORIG_LOAD_SLOTS_BOUND = 0x1D            # what the stock module holds

ORIG = {
    SITE_ALLOC_ELEM: 0x321F03F9,
    SITE_READ_BYTES: 0x320F03FB,
    SITE_PREALLOC:   0x321F03FC,
    SITE_CVT_BOUND:  0x51404109,
    SITE_SURF_WH:    0x321803F3,
    SITE_SURF_PITCH: 0x321703E8,
    SITE_BLEED:      0x52B75C16,
    SITE_FIELD_BUF:  0xB9400017,
    SITE_LOAD_SLOTS: 0xF10076FF,                        # cmp x23, #0x1d
}

# The one word we deliberately do NOT touch, checked so a future build that
# moved it fails loudly instead of being patched half-way.
SITE_W23_KEEP = 0x9370C0
ORIG_W23 = 0x321003F7                                   # mov w23, #0x10000

# ------------------------------------------------------------- FINDINGS-223
# DEPTH-1 AT 512px. THE OTHER HALF OF THE WELD.
#
# Everything above this line deliberately left depth-1 at 256 and said so:
# "LEAVE +0x9370C0 ... depth-1 stays 256". That was right for a depth-2-only
# change. This is the leg that moves it, and it is a bigger set than
# FINDINGS-221 predicted -- ten words, not three, and two of the three it did
# name were wrong.
#
# WHY THERE IS NO PROBE. `w23` is the depth-1 alloc count AND the depth-1
# READ LENGTH, and the read consumes the stream: `+0x9BCF30` is a recompiled
# `rep movsb` whose source cursor is advanced by exactly the byte count
# (`add w8, w8, w20 ; str w8, [x22, #0x18]` at +0x9BD040). Section 9's TEXTURE
# block is a strictly sequential walk with no per-page offset, so this number
# and `field_bg_native.D1_PAGE_PX` are the SAME NUMBER seen from two sides.
# Raise one without the other and the walk desynchronises on the first
# depth-1 page of every field. HANDOFF-222 s6.3 proposed exactly that as a
# no-op probe; it is not one.
#
# THE ALLOCATION ARITHMETIC IS EXACT, NOT WASTEFUL. FINDINGS-221 s2.2
# accepted a 4x depth-2 over-allocation as the price of sharing `w23`. It is
# not the price: the allocator is `count * size` and `size` is a separate
# depth-2-only register, so `w25` goes BACK TO ITS STOCK VALUE OF 2 and
# 0x40000 * 2 is exactly the 0x80000 it is today. `elem` below computes this
# from the count rather than from a hardcoded 0x10000, which is the only
# reason it comes out right at both settings.
#
#     w23 = 0x40000   d1 alloc 0x40000*1   d1 read 0x40000
#                     d2 alloc 0x40000*2 = 0x80000   d2 read w27 = 0x80000
#
# AND `+0xA040D8` IS NOT THE DEPTH-1 TWIN OF `+0xA03C44`. Section D above
# already warned that its `mov w23, #0x100` "feeds four stores, two of which
# are palette entry counts". That warning is correct and FINDINGS-221 s2.6
# argued past it. Read out of the module, the depth-1 surface struct carries
# five fields the depth-2 one does not:
#
#     +0x00  width       w23   0xA0413C     <- must become 512
#     +0x04  height      w23   0xA0415C     <- must become 512
#     +0x08  stride      w8    0xA0417C     <- must become 0x400
#     +0x10  1           w21   0xA041A0
#     +0x14  8           w20   0xA041C0        bits per index
#     +0x1c  (pal<<8)+0x200   0xA04228
#     +0x20  0x100       w23   0xA04248     <- PALETTE ENTRY COUNT. 256.
#   texhdr +0x34  0x100  w23   0xA040DC     <- PALETTE ENTRY COUNT. 256.
#
# So `w23` must NOT move here. The two dimensions get their own register
# instead. `+0x10FC3A0` is a twelve-instruction leaf that clobbers only
# x0/x8/x9/x10, and this function uses only w19..w23 of the callee-saved set,
# so w11 survives every `bl` between the sites. The word to set it in comes
# from `+0xA040D0 str w8, [x22]`, a recompiler spill into scratch slot 0
# whose value is never reloaded -- slot 0 is next WRITTEN at +0xA04134 with
# no read in between. THAT IS THE ONE PIECE OF THIS THAT IS SURGERY RATHER
# THAN A CONSTANT, and `verify_module` checks the exact original word so a
# module that moved fails loudly instead of being patched half-way.
#
# AND TWO SITES NOBODY HAD FOUND, in the NATIVE reimplementation rather than
# the recompiled code. `field_load_textures` registers the +14 / +18 ALIAS
# pages for slots 15..20 by allocating and copying a hardcoded 0x10000:
#
#     0x10DC624  mov w2, #0x10000    ; alloc size  -> 256*256*1
#     0x10DC688  mov w2, #0x10000    ; memcpy len  -> 256*256*1
#
# Both alias arms test `ldrh w8,[x24,#0x1a] ; cmp w8,#1 ; b.ne` at +0x10DC444
# and +0x10DC484, so this path fires for DEPTH-1 PAGES ONLY -- which is
# precisely why a hardcoded 256x256x1 has been correct until now, and why
# FINDINGS-194 could leave it alone. At 512 the alias would get a 64 KB
# buffer for a 256 KB page and a texture header claiming 512x512 over it.
D1_PX_ENV = 'SEVENTH_NX_FIELD_BG_D1_PX'
VANILLA_D1_PX = 256
SUPPORTED_D1_PX = (256, 512)

# The build-wide depth-1 page side. build.py sets this from
# `field_bg_native.D1_PAGE_PX` so the module and the archive cannot disagree;
# `d1_page_px()` lets the env var override it for an A/B.
D1_PAGE_PX = VANILLA_D1_PX

SITE_D1_COUNT      = 0x9370C0   # mov w23, #0x10000  d1 alloc count AND read
SITE_D1_PRECOUNT   = 0x92CF24   # mov w26, #0x10000  init, shared page count
SITE_D1_SCRATCH    = 0xA040D0   # str w8, [x22]      dead spill -> w11 = px
SITE_D1_SURF_W     = 0xA0413C   # str w23, [x0]      d1 surface width
SITE_D1_SURF_H     = 0xA0415C   # str w23, [x0]      d1 surface height
SITE_D1_SURF_PITCH = 0xA0417C   # mov w8, #0x200     d1 surface stride
SITE_D1_ALIAS_SIZE = 0x10DC624  # mov w2, #0x10000   alias page alloc size
SITE_D1_ALIAS_COPY = 0x10DC688  # mov w2, #0x10000   alias page memcpy length

# THE WORD THAT MUST NOT MOVE, and it gets a name because writing its
# encoding by hand once already produced the WRONG ONE: `mov w19, #0x100` is
# 0x321803F3 and `mov w23, #0x100` is 0x321803F7, the register is the last
# nibble, and the depth-2 site three lines up uses w19. The pre-flight caught
# it; a named constant checked against the real module means the next reader
# does not have to.
SITE_D1_PAL_COUNT = 0xA040D8
ORIG_D1_PAL_COUNT = 0x321803F7                          # mov w23, #0x100

ORIG_D1 = {
    SITE_D1_COUNT:      0x321003F7,                     # mov w23, #0x10000
    SITE_D1_PRECOUNT:   0x321003FA,                     # mov w26, #0x10000
    SITE_D1_SCRATCH:    0xB90002C8,                     # str w8, [x22]
    SITE_D1_SURF_W:     0xB9000017,                     # str w23, [x0]
    SITE_D1_SURF_H:     0xB9000017,                     # str w23, [x0]
    SITE_D1_SURF_PITCH: 0x321703E8,                     # mov w8, #0x200
    SITE_D1_ALIAS_SIZE: 0x321003E2,                     # mov w2, #0x10000
    SITE_D1_ALIAS_COPY: 0x321003E2,                     # mov w2, #0x10000
}

# `str w11, [x0]` -- STR (immediate, unsigned offset), 32-bit, imm12 0, Rn 0.
# Round-tripped through capstone rather than derived on paper, per the note
# at the top of this file.
STR_W11_X0 = 0xB900000B

# The register the two depth-1 dimension stores are repointed at. Caller-
# saved, but the only calls between the write and the reads are
# `bl #0x10FC3A0`, which touches x0/x8/x9/x10 and nothing else.
D1_SCRATCH_REG = 11


def d1_page_px():
    """The depth-1 page side this build writes. Must match the archive."""
    raw = os.environ.get(D1_PX_ENV)
    if raw is None:
        return D1_PAGE_PX
    try:
        val = int(raw, 0)
    except ValueError:
        return D1_PAGE_PX
    return val if val in SUPPORTED_D1_PX else D1_PAGE_PX


def d1_read_bytes(d1_px=None):
    """Bytes the loader must allocate for, and read into, one depth-1 page.

    This is `field_bg_native.d1_stored_bytes()` seen from the module side and
    the two must agree exactly -- see the note above."""
    px = d1_page_px() if d1_px is None else px_or(d1_px)
    return px * px


def px_or(val):
    if val not in SUPPORTED_D1_PX:
        raise ValueError('unsupported depth-1 page size %r' % (val,))
    return val

# Dead-code sites: x86 addresses that must stay unreferenced.
DEAD_X86 = {
    0x62A0E7: 'write_field_background_data',
    0x6428B7: 'field page -> locked surface blit',
    0x620BD3: 'PSX tile rasteriser trampoline',
    0x623D28: 'PSX tile rasteriser',
}
# 0x620BD3 must be proven dead before 0x623D28, because the only branch to
# 0x623D28 in the whole module comes from inside it.
DEAD_ORDER = (0x62A0E7, 0x6428B7, 0x620BD3, 0x623D28)


# --------------------------------------------------------------- encoding
def _decode_bitmask(sf, n, immr, imms):
    """
    ARM ARM `DecodeBitMasks`, immediate variant. None if reserved.

    Lifted verbatim from ff7nx_resolve.decode_bitmask, which test_bitmask.py
    checks against capstone over every legal encoding. Duplicated rather than
    imported so this module has no dependency on the resolver.
    """
    width = 64 if sf else 32
    if n and not sf:
        return None
    bits = (n << 6) | ((~imms) & 0x3F)
    ln = bits.bit_length() - 1
    if ln < 1:
        return None
    esize = 1 << ln
    if esize > width:
        return None
    levels = esize - 1
    s = imms & levels
    r = immr & levels
    if s == levels:                      # all-ones element is reserved
        return None
    welem = (1 << (s + 1)) - 1
    if r:
        welem = ((welem >> r) | (welem << (esize - r))) & ((1 << esize) - 1)
    val = 0
    for i in range(0, width, esize):
        val |= welem << i
    return val & ((1 << width) - 1)


def _encode_bitmask(value, sf=0):
    """(N, immr, imms) for a 32-bit AArch64 logical immediate, or None."""
    key = value & (0xFFFFFFFFFFFFFFFF if sf else 0xFFFFFFFF)
    for n in ((0, 1) if sf else (0,)):
        for immr in range(64):
            for imms in range(64):
                if _decode_bitmask(sf, n, immr, imms) == key:
                    return n, immr, imms
    return None


def mov_imm_word(rd, value):
    """
    One word setting w<rd> to `value`, or None if it cannot be done in one.

    Three encodings are tried, and the second and third are why 768px pages
    are possible at all. Every site patched here holds `mov wN, #imm`, which
    the recompiler emitted as ORR-immediate -- and ORR-immediate can only hold
    constants whose bit pattern repeats. 512px needs 8, 0x80000 and 1024,
    which all qualify. 768px needs 18 and 0x120000, which do not, so the size
    was simply rejected.

    But `movz` holds any 16-bit value, optionally shifted left 16, and
    `movz wD, #imm` is the same instruction `mov wD, #imm` already is: one
    word, same register, same result, upper 32 bits zeroed either way. The
    only reason ORR was there is that it is what the recompiler happened to
    pick.

    Nothing here writes two words. A site is one instruction wide and a
    movz/movk pair would run off the end of it, so a value needing both is
    still refused -- which is what keeps `words()` able to promise that an
    unsupported size fails before anything is written.
    """
    enc = _encode_bitmask(value)
    if enc is not None:
        n, immr, imms = enc
        return (0x32000000 | (n << 22) | (immr << 16) | (imms << 10)
                | (31 << 5) | rd)
    if 0 <= value <= 0xFFFF:                          # movz wD, #imm
        return 0x52800000 | (value << 5) | rd
    if not value & 0xFFFF and value <= 0xFFFF0000:    # movz wD, #imm, lsl #16
        return 0x52A00000 | ((value >> 16) << 5) | rd
    return None


def _sub_imm12_lsl12(orig_word, imm12):
    if not 0 <= imm12 <= 0xFFF:
        return None
    return (orig_word & ~(0xFFF << 10)) | (imm12 << 10)


def _movz_hi16(orig_word, imm16):
    return (orig_word & ~(0xFFFF << 5)) | ((imm16 & 0xFFFF) << 5)


def _cmp_imm12(orig_word, imm12):
    """
    `cmp xN, #imm` with a new immediate, or None if it does not fit.

    Same imm12 field as `_sub_imm12_lsl12` -- `cmp` IS `subs xzr, xN, #imm`
    -- but WITHOUT the lsl #12 shift, so the value is used as-is. Kept
    separate rather than sharing that helper: the shift bit at 22 is part of
    what distinguishes them, and a bound that silently gained a 4096x factor
    is exactly the class of error this module exists to avoid.
    """
    if not 0 <= imm12 <= 0xFFF:
        return None
    if (orig_word >> 22) & 1:                   # sh bit set -> it IS shifted
        return None
    return (orig_word & ~(0xFFF << 10)) | (imm12 << 10)


# ------------------------------------------------------------------ settings
def page_px():
    """
    0 (off), 128, 256, 512, 768 or 1024. Anything else -> off.

    NOTE the changed default. Unset now means OFF_PAGE_PX, not 256, because
    256 is a live setting (truecolor pages at vanilla resolution) rather
    than a synonym for "do nothing". Callers must ask `enabled()` rather
    than comparing against VANILLA_PAGE_PX.
    """
    raw = os.environ.get(PAGE_PX_ENV, '').strip()
    if not raw:
        return OFF_PAGE_PX
    try:
        val = int(raw)
    except ValueError:
        return OFF_PAGE_PX
    return val if val in SUPPORTED_PAGE_PX else OFF_PAGE_PX


def enabled():
    """True when the field-background pass should run at all."""
    return page_px() != OFF_PAGE_PX


def patches_module(px=None, max_raw=None):
    """
    True when the chosen size actually needs `exefs/main` written.

    256 writes none of the six SIZE words -- the module already says 256 --
    so a 256px build usually needs no module at all, only the repacked
    flevel, which makes it the one setting testable without a full game
    dump. "Usually", because a big enough build still needs the field
    decompression buffer widened; pass `max_raw` to find out.
    """
    px = page_px() if px is None else px
    return bool(words(px, None, max_raw))


def halve_bleed():
    return os.environ.get(HALVE_BLEED_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def page_bytes(depth, px=None):
    """Bytes one page of `depth` occupies in section 9 and in memory."""
    px = px or page_px() or VANILLA_PAGE_PX
    return (px * px * depth) if depth == 2 else (VANILLA_PAGE_PX ** 2)


# -------------------------------------------------------------------- words
def field_buffer_bytes(max_raw):
    """Buffer size to patch in for a build whose largest field
    decompresses to `max_raw` bytes: next power of two at or above
    125% of it, never below the stock 2,000,000."""
    want = max(int(max_raw * 1.25), 0x1E8480)
    n = 1 << (want - 1).bit_length()
    return n


def words(px=None, bleed=None, max_raw=None):
    """
    {module_offset: (original_word, replacement_word, why)} for `px`.

    Raises ValueError if any replacement is not encodable, so an unsupported
    size fails here rather than writing a half patch.
    """
    px = px if px is not None else page_px()
    if px == OFF_PAGE_PX:
        return {}
    if px not in SUPPORTED_PAGE_PX:
        raise ValueError('unsupported field background page size %r' % px)
    if px == VANILLA_PAGE_PX:
        # 256 needs none of the six SIZE words -- the module already holds
        # 256 everywhere, so writing them would be a no-op.
        #
        # It still needs the FIELD BUFFER, though, and that is easy to miss:
        # promoting a page from paletted to truecolor doubles it (256*256*1
        # -> 256*256*2) WITHOUT changing its dimensions, so a 12-page field
        # goes from 786 KB to 1.57 MB of section 9 and the fixed 2,000,000
        # byte decompression buffer (section E) is suddenly in play. The size
        # did not change; the DEPTH did.
        #
        # The loader bound is orthogonal to page SIZE -- it counts slots, not
        # pixels -- so a 256px truecolor build needs it for the same reason a
        # 512px one does.
        out = _field_buffer_word(max_raw)
        out.update(_load_slots_word())
        out.update(_d1_words())
        return out

    pixels = px * px                                  # 0x40000 at 512
    read = read_bytes(px)                             # 0x80000 at 512

    # `elem` is an ALLOCATION size in units of w23 (#0x10000), not a stride:
    # alloc(count=0x10000, size=elem) yields 0x10000*elem bytes for ONE page.
    # It therefore only has to be big ENOUGH, so round up. This is what makes
    # sizes that are not multiples of 256 possible at all -- HANDOFF-52 3.1
    # required it to divide exactly and concluded, wrongly, that only
    # multiples of 256 exist.
    #
    # FINDINGS-223: the count is `w23`/`w26`, which is NO LONGER always
    # 0x10000 -- the depth-1 leg raises it to 0x40000. Deriving `elem` from
    # the live count rather than from the literal is what makes the depth-2
    # allocation stay exact instead of over-allocating 4x: at a 0x40000 count
    # a 512px depth-2 page needs elem 2, which is the module's STOCK value.
    count = d1_read_bytes()                           # 0x10000 or 0x40000
    elem = -(-read // count)                          # ceil; 8 at 512/256, 2 at 512/512
    alloc_bytes = elem * count

    # The pixel-fixup loop bound is `sub w9, w8, #imm12, lsl #12`, so it can
    # only express multiples of 0x1000. Round UP: every real pixel must be
    # visited (missing ones keep raw colour 0 and draw black instead of
    # transparent), and the overshoot lands in the tail of our own
    # over-allocation, which nothing else reads and the GPU never samples --
    # the surface descriptor is px wide with a px*2 stride.
    bound = -(-pixels // 0x1000) * 0x1000
    if bound * 2 > alloc_bytes:
        raise ValueError(
            'field bg %dpx: pixel loop bound %d px would run past the %d byte '
            'allocation' % (px, bound, alloc_bytes))
    if (bound >> 12) > 0xFFF:
        raise ValueError('field bg %dpx: loop bound does not fit imm12' % px)

    out = {}

    def put(off, word, why):
        if word is None:
            raise ValueError('field bg %dpx: cannot encode %s' % (px, why))
        out[off] = (ORIG[off], word, why)

    put(SITE_ALLOC_ELEM, mov_imm_word(25, elem),
        'loader: depth-2 alloc element size 2 -> %d (0x%X bytes for a %d '
        'byte page, %d spare)'
        % (elem, alloc_bytes, read, alloc_bytes - read))
    put(SITE_READ_BYTES, mov_imm_word(27, read),
        'loader: depth-2 read 0x20000 -> 0x%X bytes' % read)
    put(SITE_PREALLOC, mov_imm_word(28, elem),
        'init_bg_pages: depth-2 pre-alloc element size 2 -> %d' % elem)
    put(SITE_CVT_BOUND, _sub_imm12_lsl12(ORIG[SITE_CVT_BOUND], bound >> 12),
        'convert_type2_layers: pixel loop bound 0x10000 -> 0x%X%s'
        % (bound, '' if bound == pixels
           else ' (%d px rounded up to the imm12 grid)' % pixels))
    put(SITE_SURF_WH, mov_imm_word(19, px),
        'depth-2 surface width AND height 256 -> %d' % px)
    put(SITE_SURF_PITCH, mov_imm_word(8, px * 2),
        'depth-2 surface stride 0x200 -> 0x%X' % (px * 2))

    out.update(_field_buffer_word(max_raw))
    out.update(_load_slots_word())
    out.update(_d1_words())
    if bleed if bleed is not None else halve_bleed():
        # -0.4375/256 -> -0.4375/px, so the tile-edge bleed guard stays the
        # same fraction of a TEXEL. Costs correctness on the depth-1 pages
        # that stay 256, which is why it is off by default.
        #
        # DERIVED rather than tabulated. The site is `movz w22, #imm, lsl 16`,
        # so the float32 has to have a zero low half -- true for 128,256,512
        # and 1024 (all powers of two: dividing by one only walks the
        # exponent) and FALSE for 768, where -0.4375/768 is 0xBA155555. The
        # old {512: 0xBA60, 1024: 0xB9E0} table encoded that fact by omission
        # and would KeyError on 768 or 128; now it is stated and checked.
        hi = _bleed_hi16(px)
        if hi is None:
            raise ValueError(
                'field bg %dpx: the bleed guard -0.4375/%d is 0x%08X, whose '
                'low half is not zero, so it cannot be written with the '
                'single movz at +0x%X. Leave %s off at this size.'
                % (px, px, _f32_bits(-0.4375 / px), SITE_BLEED,
                   HALVE_BLEED_ENV))
        put(SITE_BLEED, _movz_hi16(ORIG[SITE_BLEED], hi),
            'tile bleed guard -0.4375/256 -> -0.4375/%d' % px)
    return out


def _d1_words(d1_px=None):
    """
    {offset: (orig, new, why)} for the depth-1 page size. {} at 256.

    All eight or none. There is no partial state that renders: the loader's
    read length, the file's stored size and the surface dimensions are one
    decision seen from three places, and any two of them disagreeing
    desynchronises the TEXTURE walk rather than degrading the picture.
    """
    px = d1_page_px() if d1_px is None else px_or(d1_px)
    if px == VANILLA_D1_PX:
        return {}
    n = px * px

    out = {}

    def put(off, word, why):
        if word is None:
            raise ValueError('field bg d1 %dpx: cannot encode %s' % (px, why))
        out[off] = (ORIG_D1[off], word, why)

    put(SITE_D1_COUNT, mov_imm_word(23, n),
        'loader: depth-1 alloc count AND read length 0x10000 -> 0x%X '
        '(shared with the depth-2 alloc count, which `elem` compensates '
        'for exactly)' % n)
    put(SITE_D1_PRECOUNT, mov_imm_word(26, n),
        'init_bg_pages: shared page count 0x10000 -> 0x%X (the depth-1 '
        'element size at +0x92D0A0 stays 1, so depth-1 pre-allocates 0x%X)'
        % (n, n))
    put(SITE_D1_SCRATCH, mov_imm_word(D1_SCRATCH_REG, px),
        'make_field_tex_header_pal: a DEAD recompiler spill (scratch slot 0, '
        'never reloaded before +0xA04134 overwrites it) becomes w%d = %d, '
        'because w23 cannot move -- it also feeds two palette entry counts'
        % (D1_SCRATCH_REG, px))
    put(SITE_D1_SURF_W, STR_W11_X0,
        'depth-1 surface WIDTH now comes from w%d (%d), not w23'
        % (D1_SCRATCH_REG, px))
    put(SITE_D1_SURF_H, STR_W11_X0,
        'depth-1 surface HEIGHT now comes from w%d (%d), not w23'
        % (D1_SCRATCH_REG, px))
    put(SITE_D1_SURF_PITCH, mov_imm_word(8, px * 2),
        'depth-1 surface stride 0x200 -> 0x%X (the engine builds a 2-byte '
        'surface for a paletted page too, expanding the palette on the way)'
        % (px * 2))
    put(SITE_D1_ALIAS_SIZE, mov_imm_word(2, n),
        'field_load_textures: +14/+18 ALIAS page alloc 0x10000 -> 0x%X '
        '(depth-1 only path, guarded at +0x10DC444 / +0x10DC484)' % n)
    put(SITE_D1_ALIAS_COPY, mov_imm_word(2, n),
        'field_load_textures: +14/+18 ALIAS page memcpy 0x10000 -> 0x%X' % n)
    return out


def _field_buffer_word(max_raw):
    """
    {SITE_FIELD_BUF: (orig, new, why)} if this build's biggest field needs a
    bigger decompression buffer than the stock 2,000,000, else {}.

    Split out of words() because it is the one patch that a 256px build still
    needs -- see the VANILLA_PAGE_PX branch there.
    """
    if not max_raw:
        return {}
    n = field_buffer_bytes(max_raw)
    if n <= 0x1E8480:
        return {}
    word = mov_imm_word(23, n)
    if word is None:
        raise ValueError('field bg: cannot encode a %d byte field buffer' % n)
    return {SITE_FIELD_BUF: (
        ORIG[SITE_FIELD_BUF], word,
        'field decompression buffer 2,000,000 -> %d bytes '
        '(largest field in this build is %d)' % (n, max_raw))}


def _load_slots_word():
    """
    {SITE_LOAD_SLOTS: (orig, new, why)} when the pipeline may use a slot the
    stock loader will not reach, else {}.

    See the FINDINGS-168 block above SITE_LOAD_SLOTS. The gate is
    `D2_OPAQUE_SLOTS`, because that constant is the only thing in the
    pipeline that decides whether a page can land at slot 29 or beyond: the
    depth-2 opaque band starts at 0x1A, so N slots reach 0x1A+N-1 and the
    loop must run to 0x1A+N.

    SUBTRACTIVE BY CONSTRUCTION. At the shipped D2_OPAQUE_SLOTS = 3 the
    highest page is slot 28 and the stock bound of 29 already covers it, so
    this returns {} and the module is byte-identical to today. The patch only
    appears when something would otherwise be silently dropped -- it can
    never remove a page that renders now.
    """
    try:
        import field_bg_native as FN
        want = 0x1A + int(FN.D2_OPAQUE_SLOTS)
    except Exception:                                          # noqa: BLE001
        return {}
    if want <= ORIG_LOAD_SLOTS_BOUND:
        return {}
    if want > LOAD_SLOTS_FOR_D2_OPAQUE:
        # Past the opaque band the blend ladder is the binding constraint,
        # not the loop -- a depth-2 page there still draws OPAQUE (see the
        # FINDINGS-168 block). Refuse rather than load slots whose pages
        # would render with the wrong blend mode.
        raise ValueError(
            'field bg: D2_OPAQUE_SLOTS = %d would need the texture loader to '
            'reach slot 0x%X, past the depth-2 OPAQUE band that ends at 0x20. '
            'This port gives every depth-2 page blend 4 regardless of slot, '
            'so a page above 0x20 would draw opaque instead of additive. '
            'Raising this needs a blend-ladder trampoline, not a bound change.'
            % (FN.D2_OPAQUE_SLOTS, want - 1))
    word = _cmp_imm12(ORIG[SITE_LOAD_SLOTS], want)
    if word is None:
        raise ValueError('field bg: cannot encode texture loader bound %d'
                         % want)
    return {SITE_LOAD_SLOTS: (
        ORIG[SITE_LOAD_SLOTS], word,
        'field_load_textures: slot loop bound %d -> %d, so the %d depth-2 '
        'OPAQUE slot(s) 0x1A..0x%X all load (FINDINGS-168; the x86 bound is '
        '42 and this port narrowed it to 29, which is why builds 52 and 55 '
        'drew black squares at slot 29)'
        % (ORIG_LOAD_SLOTS_BOUND, want, FN.D2_OPAQUE_SLOTS, want - 1))}


def _f32_bits(value):
    """The IEEE-754 single-precision bit pattern of `value`."""
    return struct.unpack('<I', struct.pack('<f', value))[0]


def _bleed_hi16(px):
    """
    High half of float32(-0.4375/px), or None if the low half is not zero
    and the constant therefore needs two instructions.
    """
    bits = _f32_bits(-0.4375 / px)
    return None if bits & 0xFFFF else bits >> 16


def spec(px=None, bleed=None, max_raw=None):
    """A patch spec for nso_patcher, which verifies every original byte."""
    px = page_px() if px is None else px
    return {
        'name': 'field background truecolor pages %dx%d' % (px, px),
        'patches': [
            {'name': why,
             'va': '0x%X' % off,
             'expect': _hex(orig),
             'set': _hex(new)}
            for off, (orig, new, why) in sorted(
                words(px, bleed, max_raw).items())
        ],
    }


def _hex(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


# ----------------------------------------------------------------- verifier
def verify_module(nso_path, log=lambda *_: None, px=None):
    """
    Re-measure every assumption against the module before patching it.

    Returns True only if:
      * every site still holds its expected original word;
      * `w23` (#0x10000) is still there, i.e. the depth-1 path is intact;
      * the three dead functions are still unreferenced.

    This is the part that must not be weakened. A module that moved one of
    these is a module this patch set does not understand.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import nxmap
    except ImportError as exc:
        log('! field bg: cannot import nxmap (%s)' % exc)
        return False
    try:
        m = nxmap.Main(nso_path)
    except SystemExit as exc:
        log('! field bg: %s' % exc)
        return False

    ok = True
    for off, (orig, _new, why) in sorted(words(px).items()):
        got = struct.unpack_from('<I', m.text, off)[0]
        if got != orig:
            log('! field bg: +0x%08X holds %08X, expected %08X  (%s)'
                % (off, got, orig, why))
            ok = False
    # +0x9370C0 is the depth-1 constant. At 256 it MUST NOT MOVE and this is
    # the check that says so. At 512 it is `SITE_D1_COUNT` and the loop above
    # has already checked it against the same original word, so asserting it
    # is unpatched here would contradict the patch set rather than guard it.
    if d1_page_px() == VANILLA_D1_PX:
        got = struct.unpack_from('<I', m.text, SITE_W23_KEEP)[0]
        if got != ORIG_W23:
            log('! field bg: +0x%08X (the depth-1 constant that must NOT '
                'move) holds %08X, expected %08X'
                % (SITE_W23_KEEP, got, ORIG_W23))
            ok = False
    else:
        # The palette-entry-count constant. FINDINGS-223 s2: `mov w23, #0x100`
        # at +0xA040D8 feeds the depth-1 surface dimensions AND two palette
        # entry counts, so the depth-1 leg routes the dimensions through w11
        # and leaves this alone. If a future module merged those stores this
        # patch set is wrong, and this is what says so.
        got = struct.unpack_from('<I', m.text, SITE_D1_PAL_COUNT)[0]
        if got != ORIG_D1_PAL_COUNT:
            log('! field bg: +0x%08X (the depth-1 PALETTE ENTRY COUNT, which '
                'must stay 256) holds %08X, expected %08X'
                % (SITE_D1_PAL_COUNT, got, ORIG_D1_PAL_COUNT))
            ok = False

    # Order matters: a body is only allowed to be referenced from a body
    # already proven dead, so prove them outermost-first.
    dead_spans = []
    for va in DEAD_ORDER:
        name = DEAD_X86[va]
        why = _liveness(m, va, dead_spans)
        if not why and va in m.x86_to_arm:
            dead_spans.append(m.extent(va))
        if why:
            log('! field bg: %s (x86 0x%X) is no longer dead -- %s. It '
                'hardcodes a 256-pixel row stride and this patch set '
                'assumed nothing reaches it.' % (name, va, why))
            ok = False
    return ok


def _liveness(m, x86_va, dead_spans=()):
    """
    '' if the translated body of `x86_va` is unreachable, else why not.

    Two independent checks against the module, not against the x86 exe:
      * no `b`/`bl` in .text targets its ARM entry, EXCEPT from inside
        another body already known to be dead -- 0x623D28 is reached only
        from 0x620BD3, and 0x620BD3 is reached from nothing;
      * no R_AARCH64_RELATIVE addend equals its ARM entry, other than the
        recompilation map's own record for it. The map lists every translated
        function whether or not anything calls it, so that one record is
        expected and is not a reference.
    """
    arm = m.x86_to_arm.get(x86_va)
    if arm is None:
        return ''                       # not translated at all
    text = m.text
    for off in range(0, len(text) - 3, 4):
        w = struct.unpack_from('<I', text, off)[0]
        if (w & 0x7C000000) != 0x14000000:          # B (0x14) / BL (0x94)
            continue
        imm = w & 0x03FFFFFF
        if imm & 0x02000000:
            imm -= 0x04000000
        if off + imm * 4 != arm:
            continue
        if any(lo <= off < hi for lo, hi in dead_spans):
            continue
        return 'a branch at +0x%X targets it' % off
    for off, add in m.rel.items():
        if add != arm:
            continue
        # the map record for this function: key at off-8 is its x86 address
        if off >= 8 and struct.unpack_from('<I', m.img, off - 8)[0] == x86_va:
            continue
        return 'a relocation at +0x%X points at it' % off
    return ''


# ------------------------------------------------------------------- apply
def apply_to_nso(src, dest, log=lambda *_: None, px=None, bleed=None,
                 max_raw=None):
    """Patch `main` at `src` -> `dest`. True on success, nothing written on
    failure."""
    px = page_px() if px is None else px
    # `max_raw` MUST be forwarded here. Without it this asked
    # `patches_module(px)`, which at 256px answers False -- none of the six
    # SIZE words are needed -- and the function then refused to write even
    # though the FIELD BUFFER word was wanted. build.py had already decided
    # to call this (it asks with max_raw), so the build reported
    # "field background: FAILED" on a 256px build that was in fact fine.
    if not patches_module(px, max_raw):
        # OFF writes nothing; 256 writes nothing because the module already
        # holds 256 everywhere. Both are "no module patch needed", which is
        # not a failure -- but this returns False either way, so callers keep
        # their existing "did I patch main?" semantics.
        return False
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import nso_patcher
    except ImportError as exc:
        log('! field bg: cannot import nso_patcher (%s)' % exc)
        return False
    if not verify_module(src, log, px):
        log('  nothing was written; the module is unchanged')
        return False
    from pathlib import Path
    try:
        nso = nso_patcher.read_nso(Path(src))
        applied = nso_patcher.apply_spec(nso, spec(px, bleed, max_raw))
        data = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        log('! field bg: %s' % exc)
        log('  nothing was written; the module is unchanged')
        return False
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(data)
    for line in applied:
        log('  ' + line)
    log('  depth-1 (8-bit paletted) pages are UNCHANGED at 256x256 -- a '
        'field may mix both and each draws at its own size')
    return True


# -------------------------------------------------------------------- main
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--nso', required=True, help='stock or fps-patched main')
    ap.add_argument('--out')
    ap.add_argument('--px', type=int, default=512, choices=SUPPORTED_PAGE_PX)
    ap.add_argument('--halve-bleed', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args(argv)
    if a.verify or not a.out:
        ok = verify_module(a.nso, print, a.px)
        print('== %dpx word list' % a.px)
        for off, (orig, new, why) in sorted(words(a.px,
                                                  a.halve_bleed).items()):
            print('  +0x%08X  %08X -> %08X   %s' % (off, orig, new, why))
        print('verify:', 'OK' if ok else 'FAILED')
        return 0 if ok else 1
    return 0 if apply_to_nso(a.nso, a.out, print, a.px,
                             a.halve_bleed) else 1


if __name__ == '__main__':
    raise SystemExit(main())
