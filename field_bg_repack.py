#!/usr/bin/env python3
"""
field_bg_repack.py -- put Cosmos Limit Break's upscaled field art into the
game's own background pages.

This is the half that makes 512px pages worth having. ff7nx_fieldbg.py lets a
TRUECOLOR page be 512x512; field_bg_native.py rescales the ones the game
already ships. Neither of those touches the mod's art, because the mod's art
is 8-bit PALETTED and 8-bit pages stay 256px on purpose (the loader's
#0x10000 is shared with their read count -- see ff7nx_fieldbg.py).

So the art has to move to a truecolor page. That means moving it, and this is
what makes it possible:

    MEASURED. `src_x_big` (tile record offset 42) and `src_y_big` (offset 46)
    are u32 holding u * 10^7 and v * 10^7 -- the exact values the loader
    divides by 1e7 into field_tile.u/.v. Verified on md1stin: src_x = 16 =>
    src_x_big = 625,000 = 0.0625 = 16/256, for every tile.

Tiles are therefore relocatable by rewriting two u32s, and page slots are
reassignable by rewriting one byte. That turns the conversion into a packing
problem instead of an impossible one.


WHY IT IS A PACKING PROBLEM
---------------------------
Three measurements decide the shape:

1. A page is always a 16x16 GRID OF CELLS, whatever its pixel size, because
   the per-tile UV extent is the normalised literal 1/16 (or 1/8 when the
   page's `size` flag is set -- an 8x8 grid). 512px buys resolution per cell,
   not more cells. Every one of the 645,566 tiles in vanilla flevel.lgp has
   cell-aligned UVs; zero exceptions.

2. A page is drawn with MANY PALETTES. Distinct palette_ID per page across
   vanilla: 441 pages use one, 561 use two, 454 three, 364 four, up to 15.
   Only 38 of 709 fields have one palette per page throughout. So a paletted
   page does not become one truecolor page -- it becomes one truecolor CELL
   per (page, cell, palette) actually referenced. That is why FFNx's dump is
   named `<field>_<page>_<palette>_<hash>.dds`.

3. It fits. Distinct (page, cell, palette) per field: mean 891, max 1,997.
   Against 16 depth-2 slots x 256 cells, grouped by blend mode
   (7 pages = 1,792 opaque cells, 7 = 1,792 additive, 2 = 512 average),
   702 of 709 fields take a complete upgrade of all four layers. The seven
   that do not -- clsin2_1, hyou2, hyou5_2, las0_2, las0_5, ujunon1 and one
   more -- exceed the opaque group and get a partial upgrade, least-used
   pages left paletted.


PALETTE COVERAGE, AND WHY THE STRICT RULE DOES NOT WORK
-------------------------------------------------------
FFNx names a dumped page `field/<field>/<field>_<page>_<palette>.dds` --
`ff7/field/field.cpp:72` builds `field/%s/%s_%02i` from the PAGE index, and
`saveload.cpp:106` appends `_%02i` from `tex_header->palette_index`. So the
parse above is right.

But the coverage is not one image per palette. Measured on Cosmos Limit Break:
4,488 slots against roughly 3,300 paletted pages -- **1.36 images per page**,
while vanilla draws a page with a mean of **2.8** palettes. Requiring every
palette before touching a page therefore upgrades almost nothing: on a real
build it took 71 pages out of 2,478.

So a page is upgraded if the mod covers AT LEAST ONE of its palettes, and a
palette with no image of its own borrows the nearest one that has it. That is
an approximation, and it is the honest description of it: where two palettes
differ only in indices the page does not use -- which is why the dump has
fewer images than palettes in the first place -- the substitution is exact.
Where they differ in colour, those tiles render in the substituted palette's
colours instead of their own.

`Stats` counts the substitutions, so the build log says how much of the
picture is exact and how much is borrowed. Set STRICT_ENV=1 for the old
all-or-nothing rule.

Pages whose `size` flag is set (an 8x8 grid, 265 of 3,315 in vanilla) are left
alone in this version.


ONE TEXTURE PER PAGE, AND WHY THE PALETTE IDs DO NOT LINE UP
------------------------------------------------------------
MEASURED against the real mod, against the real built flevel:

    ancnt1 page 1: tiles carry palette IDs [2, 3], the mod dumped [0]
    ancnt1 page 2: tiles carry [4, 5, 6],          the mod dumped [0]
    ancnt2 page 2: tiles carry [4],                the mod dumped [0]
    ancnt2 page 15: the mod dumped [5, 6, 7, 8]

Pages below 0x0F are dumped as palette 0 only; pages from 0x0F up have real
palette variety. That is the engine's own boundary: `field_load_textures`
(x86 0x640292, at 0x640569) sets `texheader+0xC = 1` only when
`slot >= 0x0F && depth == 1`, and FFNx dumps on texture LOAD, one file per
texture the engine actually creates. Below the boundary the engine creates
ONE texture for the page, every tile on it samples that one texture, and the
`palette_ID` byte in the tile record selects nothing.

So "the mod covers this page" is a question about the PAGE, not about
matching palette IDs. Requiring the mod's palette set to intersect the
tiles' palette IDs was wrong, and it is what left 64% of pages untouched
while the mod in fact ships art for 95.9% of them.

`pals_for(page)` now reports what the mod holds, and every tile on the page
uses it. Where the mod dumped a single image the substitution is not an
approximation at all -- there is only one texture to sample. Where it dumped
several (the 0x0F-and-up overlay pages) a tile whose palette has no image of
its own takes the nearest, and `Stats.cells_borrowed` counts only those.


WHAT IS WRITTEN
---------------
For each upgraded page group:

  * new depth-2 pages, 512x512 R5G6B5 (format proven in field_bg_native.py),
    built by cropping cells out of the mod's 1024x1024 BC7 art scaled to 512;
  * every affected tile's `texture_id` -> the new slot, and its
    `src_x_big`/`src_y_big` -> the new cell. `src_x`/`src_y` (the byte copies
    the PC draw path does not read, but FFNx does) are kept consistent.

An original paletted page is then marked ABSENT if no tile points at it any
more. That was deferred once, on the grounds that 64 KB was not worth an
untraced `layer2_end_page` -- both halves of which were wrong. The cost is not
64 KB of file, it is a whole TEXTURE, and the texture count is the thing that
breaks (see max_new_pages). And `layer2_end_page` (0xCFFE0E) is now traced: it
bounds a page-range walk at x86 0x63A34A which tests `page->present` (+0xC)
before touching a page, exactly as the draw at 0x640213 does. Nothing is
renumbered, so the range keeps its meaning and an absent page is simply
skipped.
"""
from __future__ import annotations

import os
import re
import struct
import zlib

import field_bg_native as FN

TILE = FN.TILE_SIZE
T_SRC_X = 10
T_SRC_Y = 12
T_PALETTE = FN.TILE_PALETTE_ID
T_TEXID = FN.TILE_TEXTURE_ID
T_SRC_X_BIG = 42
T_SRC_Y_BIG = 46
T_USE_FX = 28            # u16; == 1 means the tile draws from fx_page
T_FX_PAGE = FN.TILE_TEXTURE_ID2   # offset 34

STRICT_ENV = 'SEVENTH_NX_FIELD_BG_STRICT_PALETTE'
MAX_PAGES_ENV = 'SEVENTH_NX_FIELD_BG_MAX_NEW_PAGES'
DEFAULT_MAX_NEW_PAGES = 0                # 0 = no count cap; the budget rules

# THE SLOT THAT ACTUALLY FAILS TO ALLOCATE. Measured on hardware, not derived.
#
# D2_GROUPS gives the opaque truecolor group slots 0x1A..0x20 (26..32) and the
# repack fills them densely from 26. On `mds6_2` -- identified from the capture
# `2026-08-04_09-26-50.jpg` at camera dst (-160, -280), and confirmed by
# rendering the archive over the capture pixel-aligned -- the tiles that came
# back BLACK were, per slot:
#
#     L1 slot 26    0 black /  85 drawn     0%
#     L1 slot 27   12 black /  81 drawn    13%     <- overlap, not failure
#     L1 slot 28    0 black /  20 drawn     0%
#     L2 slot  2   10 black / 106 drawn     9%     <- paletted, overlap
#     L2 slot 29   60 black /  40 drawn    60%     <- FAILS
#     L2 slot 30   22 black /   7 drawn    76%     <- FAILS
#
# 26, 27 and 28 allocate. 29 and 30 do not, in the same field, in the same
# frame. `max_new_pages()` describes the abort mechanism correctly but caps
# the COUNT of new pages; what the port actually refuses is a SLOT NUMBER, and
# the count only correlates with it because the repack allocates densely.
#
# `field_bg_max_pages` cannot express this. It counts ALL pages, and the
# heaviest field in a real build holds 13, so every ceiling from 14 upward --
# including "16, the depth-2 slot limit" and unlimited -- is the same setting
# and binds nothing. `mds6_2` shows the defect with SEVEN pages.
#
# Predicts 48 fields, and agrees with all six captures whose field is known:
# mds6_2 (max slot 30, rectangles), mrkt1 (29, rectangles), mrkt3 (27, clean),
# junin2 (27, clean), mds6_3 / mrkt2 / mds7st3 / mds7plr1 (no truecolor page,
# clean). It also settles `2026-08-04_07-09-29.jpg`, which the image match
# could not: it HAS rectangles, and of its two candidates only mrkt1 reaches
# slot 29.
# EXPRESSED PER GROUP, not as an absolute slot number, and that is deliberate.
# The measurement covers the OPAQUE group only (26..32). An absolute ceiling of
# 29 would also empty the blend-1 group (33..39) and the blend-0 group (40..41)
# completely -- a capability change on two groups nothing has tested, to fix a
# defect measured in a third. Neither is used by any field in a real build, so
# the difference is invisible today; it is written this way so that if one ever
# is used, this setting is not silently the reason it broke.
D2_SLOT_ENV = 'SEVENTH_NX_FIELD_BG_D2_SLOTS_PER_GROUP'
DEFAULT_D2_SLOTS_PER_GROUP = 3           # opaque: 26, 27, 28 -- 29 does not
                                         # allocate, measured on mds6_2

# THE CONSTRAINT THAT ACTUALLY BINDS -- and the one the budget was hiding.
#
# `field_load_textures` (x86 0x640292) makes ONE TEXTURE PER PRESENT PAGE and
# aborts the whole loop on the first allocation it cannot serve. Every page
# after that keeps handle 0, 0x66E272 refuses a null handle, and those tiles
# never reach the GPU -- scattered black squares. So what breaks is the
# NUMBER of pages, not the number of bytes.
#
# The repack ADDS pages rather than replacing them. MEASURED on a real build:
# 1,697 new truecolor pages created against only 184 originals freed, because
# a page can only be freed once NOTHING points at it, and 182,816 tiles kept
# their paletted page for a colour key and 113,599 more for an fx page. Net
# +1,513 pages across 666 fields, about +2.3 each. `gaiin_4` went from 10
# pages to 17.
#
# 12 is the heaviest field VANILLA ships (fship_2), and only 5 fields reach
# it. That is the order the port was provisioned for, so it is the default
# ceiling: no field asks the loader for more textures than the stock game's
# worst case ever did.
#
# This used to be enforced by accident. `budget_bytes()` was 5.5 MB, which at
# 512px pages afforded about three, so the count could not run away. Making
# the budget unlimited removed that side effect and nothing replaced it --
# `max_new_pages()` defaults to 0, and its own comment says "the budget
# rules". With no budget, nothing ruled.
REPLACE_ONLY_ENV = 'SEVENTH_NX_FIELD_BG_REPLACE_ONLY'
MAX_TOTAL_PAGES_ENV = 'SEVENTH_NX_FIELD_BG_MAX_TOTAL_PAGES'
DEFAULT_MAX_TOTAL_PAGES = 12             # 0 = no cap
VANILLA_WORST_PAGES = 12                 # fship_2, measured off flevel.lgp
BUDGET_ENV = 'SEVENTH_NX_FIELD_BG_BUDGET_MB'
PARTIAL_ENV = 'SEVENTH_NX_FIELD_BG_PARTIAL'
# Identical cells share one copy. On by default -- see dedup().
DEDUP_ENV = 'SEVENTH_NX_FIELD_BG_DEDUP'
LEGACY_ENV = 'SEVENTH_NX_FIELD_BG_LEGACY'


def legacy():
    """
    SEVENTH_NX_FIELD_BG_LEGACY=1 -- behave exactly as the code did before any
    of the compaction work.

    This exists because "turn it off" turned out to mean four separate
    switches, and a user told to roll back got a build that still ran two of
    them. One switch, and `test_legacy_is_byte_identical` proves it produces
    the same section 9 as the pre-compaction code on the real mod data.

    It forces: no compaction, no cell dedup, nearest-palette substitution,
    no no-growth loop. It does NOT touch page size, the ceiling, the budget
    or replace-only -- those predate this work and are the user's to set.
    """
    return os.environ.get(LEGACY_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


BORROW_ENV = 'SEVENTH_NX_FIELD_BG_BORROW'

# THE SLOT WHERE A PALETTE STARTS MEANING SOMETHING.
#
# `field_load_textures` (x86 0x640292, at 0x640569) sets `texheader+0xC = 1`
# only when `slot >= 0x0F && depth == 1`. Below that the engine builds ONE
# texture for the whole page and the tile's palette_ID selects nothing; at or
# above it, each palette is its own texture.
#
# That single fact decides whether substituting a palette is free or wrong:
#
#   below 0x0F   there is one texture and one image, so using the mod's only
#                dump for every tile on the page is EXACT, not an approximation
#   0x0F and up  the tile really would have been drawn in its own palette, so
#                borrowing a neighbour's renders it in the wrong COLOURS
PAL_TEXTURE_BOUNDARY = 0x0F
# Unlimited is the default now. See budget_bytes() for why the measured
# bracket that produced 5.5 stopped being the right thing to tune.
DEFAULT_BUDGET_MB = 0.0
# Big enough that no field can reach it, small enough to stay an int and
# print without scientific notation.
UNLIMITED = 1 << 60

# A page costs its raw pixels PLUS the surface the engine builds from them, and
# that surface is 32bpp: x86 0x63FAAB (the depth-2 descriptor, reached from
# field_load_textures at 0x6404CE) writes bits-per-pixel 0x20 at +0x28.
SURFACE_BPP = 4
def _page_bytes(px, depth):
    return px * px * depth + px * px * SURFACE_BPP

UV_SCALE = 10_000_000
D1_GROUPS = FN.D1_GROUPS
D2_GROUPS = FN.D2_GROUPS

DDS_RE = re.compile(r'^(?P<page>\d+)_(?P<pal>\d+)(?:_(?P<hash>[0-9a-fA-F]+))?$')


# ------------------------------------------------------------------ the index
def index_field_dds(entries, allowed=None):
    """
    {(field, page, palette): [entry name, ...]} from an .iro entry listing.

    `entries` are raw names as stored (backslashes, original case); nothing is
    extracted. `allowed` is an optional set of lowercase option-folder
    prefixes -- Cosmos ships the same field under several options, and without
    this filter a slot looks ambiguous when only one of its candidates is
    actually enabled.

    The field name is taken from the DIRECTORY, not the filename, because
    field names contain underscores and digits (`blin67_4`) and splitting
    `blin67_4_00_0_deadbeef` from the right is ambiguous. FFNx dumps to
    `.../field/<field>/<field>_<page>_<palette>[_<hash>].dds`, so the
    directory is authoritative.
    """
    out = {}
    for name in entries:
        low = name.replace('\\', '/').lower()
        if not low.endswith('.dds'):
            continue
        parts = low.split('/')
        if len(parts) < 3 or parts[-3] != 'field':
            continue
        if allowed is not None and not any(low.startswith(p) for p in allowed):
            continue
        field = parts[-2]
        base = parts[-1][:-4]
        if not base.startswith(field + '_'):
            continue
        m = DDS_RE.match(base[len(field) + 1:])
        if not m:
            continue
        key = (field, int(m.group('page')), int(m.group('pal')))
        out.setdefault(key, []).append(name)
    return out


def _is_base_dump(name, field):
    """
    True when `name` is `<field>_<page>_<palette>.dds` with no trailing
    `_<hash>` -- the page's BASE state.

    Uses exactly the parse `index_field_dds` used, off the same `DDS_RE`, so
    the two cannot disagree about what a name means.
    """
    base = name.replace('\\', '/').rsplit('/', 1)[-1].lower()
    if not base.endswith('.dds'):
        return False
    base = base[:-4]
    if not base.startswith(field + '_'):
        return False
    m = DDS_RE.match(base[len(field) + 1:])
    return bool(m) and m.group('hash') is None


def resolve(index, keep_ambiguous=True, stats=None):
    """
    {(field, page, palette): entry}, one candidate per slot.

    A slot with several candidates after option filtering is the same page
    dumped in more than one state -- FFNx's animated form, `_<hash>` on the
    end. Dropping those used to be the safe choice; with the palette fallback
    it is not, because dropping one palette of a page no longer leaves the
    page alone, it just makes another palette get borrowed for it.

    WHERE THERE IS A CHOICE, THE BASE DUMP WINS.
    ---------------------------------------------
    FFNx writes `<field>_<page>_<palette>.dds` for the texture the engine
    creates for a page and appends `_<hash>` for every FURTHER state of that
    same page -- animation frames, mostly (`saveload.cpp` dumps on texture
    LOAD, once per texture the engine actually creates).

    The previous rule was `sorted(v)[0]`, which is deterministic but
    arbitrary: it picked whichever hash sorted first, so an animated page
    could be drawn in a frame it is only in for a few ticks -- real art, in
    the right cells, from the wrong moment. Preferring the hashless dump
    picks the state the page spends most of its time in. Where no candidate
    is hashless the old sort still decides, because something has to.

    `keep_ambiguous=False` restores the old drop-it behaviour.

    `stats`, if given, is filled with 'base' and 'arbitrary': how many
    multi-candidate slots this rule settled, and how many had no base dump
    and still had to be sorted. The build log reports both, so a picture
    that looks like the wrong animation frame can be checked against a
    number instead of a guess.
    """
    out = {}
    n_base = n_arb = 0
    for k, v in index.items():
        if len(v) == 1:
            out[k] = v[0]
            continue
        if not keep_ambiguous:
            continue
        base = [n for n in v if _is_base_dump(n, k[0])]
        if base:
            out[k] = sorted(base)[0]
            n_base += 1
        else:
            out[k] = sorted(v)[0]
            n_arb += 1
    if stats is not None:
        stats['base'] = n_base
        stats['arbitrary'] = n_arb
    return out


class IroReader:
    """Pull many entries out of one .iro, reading the directory once."""

    def __init__(self, path):
        import iro
        self._iro = iro
        self.path = path
        with open(path, 'rb') as f:
            _ver, _flags, entries = iro.read_entries(f)
        self.size = os.path.getsize(path)
        self.by_name = {n.lower().replace('\\', '/'): (fl, off, sz)
                        for n, fl, off, sz in entries}
        self._fh = None

    def __enter__(self):
        self._fh = open(self.path, 'rb')
        return self

    def __exit__(self, *_a):
        if self._fh:
            self._fh.close()
            self._fh = None

    def read(self, name):
        rec = self.by_name.get(name.lower().replace('\\', '/'))
        if rec is None:
            return None
        flags, off, size = rec
        f = self._fh or open(self.path, 'rb')
        try:
            f.seek(off)
            data = f.read(min(size + 16, self.size - off))
        finally:
            if self._fh is None:
                f.close()
        if flags == 1:
            return zlib.decompress(data)
        if flags == 2:
            return self._iro._decompress_lzma(data)
        return data[:size]


# ------------------------------------------------------------------- pixels
try:
    import numpy as _np
except ImportError:                                            # pragma: no cover
    _np = None


NO_DITHER_ENV = 'SEVENTH_NX_FIELD_BG_NO_DITHER'
TRUE_BLACK_ENV = 'SEVENTH_NX_FIELD_BG_TRUE_BLACK'

# Bayer 8x8, the classic recursive ordered-dither matrix.
_BAYER8 = (
    ( 0, 32,  8, 40,  2, 34, 10, 42),
    (48, 16, 56, 24, 50, 18, 58, 26),
    (12, 44,  4, 36, 14, 46,  6, 38),
    (60, 28, 52, 20, 62, 30, 54, 22),
    ( 3, 35, 11, 43,  1, 33,  9, 41),
    (51, 19, 59, 27, 49, 17, 57, 25),
    (15, 47,  7, 39, 13, 45,  5, 37),
    (63, 31, 55, 23, 61, 29, 53, 21),
)


def black_cell_threshold():
    """
    A cell whose OPAQUE-BLACK fraction reaches this keeps its paletted page,
    where black is exact. Default 0.25. 0 disables it; 1.0 only rejects cells
    that are entirely black.

    WHY A THRESHOLD AND NOT A FLAG. A paletted page has two channels -- an
    index and a colour -- so index 0 is the colour key and any OTHER index may
    be pure black AND opaque. R5G6B5 has no index: colour is the only channel,
    0x0000 has to mean both, and the engine resolves the clash in favour of
    transparent (x86 0x6470E0: pixel 0 -> 0). Hence NEAR_BLACK, and hence a
    faint lift on what should be dead black.

    Making 0 opaque engine-wide is not available: vanilla's own truecolor pages
    carry 69,789 transparent pixels across 882 cells on occluding layers, all
    deliberate see-through holes. Forcing them opaque fills the holes in.

    So it is per cell, and MEASURED over ten fields of a real build the curve
    has an obvious knee -- 5.6% of upgraded pixels are opaque black, but they
    are concentrated in a few very dark cells:

        reject cells that are   cells kept at 512px   black made TRUE black
              100% black              98.8%                  22.1%
               75% black              96.9%                  50.2%
               50% black              95.4%                  67.5%
               25% black              92.5%                  85.5%   <- default
                5% black              87.2%                  98.1%
                any black             79.2%                 100.0%

    A cell that is a quarter black or more has almost no detail to lose by
    staying at 256px -- it is mostly black. A cell with a few stray black
    pixels keeps its upscale and pays a 0.9/255 blue lift on pixels too few to
    see. 25% takes 85% of the benefit for 7.5% of the cost.

    Rejection is exactly vanilla behaviour for that cell, so this cannot break
    anything: the cell simply keeps the page it already had.
    """
    raw = os.environ.get(TRUE_BLACK_ENV, '').strip()
    if not raw:
        return 0.25
    try:
        return min(1.0, max(0.0, float(raw)))
    except ValueError:
        return 0.25


def dither_on():
    return os.environ.get(NO_DITHER_ENV, '').strip().lower() not in (
        '1', 'true', 'yes', 'on')


# DITHER AMPLITUDE AT THE 8-BIT -> R5G6B5 STEP. 0.0 = none, 1.0 = one full
# quantisation step. The PATTERN is a hash of (x, y), not a Bayer matrix --
# see the note in rgba_to_565_buf, and read this before changing either.
#
# WHY THE MATRIX HAD TO GO
# ========================
# An 8x8 Bayer matrix breaks 565 banding beautifully at 1:1. The 16:9 framing
# patch presents the field at exactly 3.0000 pixels per texel, so every dither
# cell becomes a SOLID 3x3 BLOCK on screen. Bayer's dominant structure is at
# period 2 in the source, which at 3x is a 6-pixel dot grid over the entire
# picture -- far too coarse for the eye to integrate back, so it reads as
# grain rather than as dither.
#
# REPORTED FROM HARDWARE as "the entire screen has this grainy look", and
# unmoved by every shader in the set (hd, hd2, FXAA on, FXAA off, docked and
# handheld). MEASURED on the user's captures: the mod-6 phase spread runs
# 5-17x the mod-2/3/4 spread, i.e. the period is 6 pixels = 2 texels, which no
# sub-texel shader effect can produce. That is what pointed here.
#
# THE SWEEP, 8 real Cosmos pages through this function, upscaled 3x.
# "dots" = mod-6 phase spread, the artefact. Lower is better:
#
#     pattern   amplitude    dots
#     bayer       0.00      0.0480     no dither at all -- but then it BANDS
#     bayer       0.25      0.0580
#     bayer       1.00      0.1597     what shipped, the grain
#     hash        0.50      0.0479
#     hash        0.75      0.0483
#     hash        1.00      0.0485     <- now: full dither, no lattice
#     hash        1.25      0.0755     past useful, the noise starts to show
#
# A hash at FULL amplitude scores the same as no dither at all on the
# artefact, because there is no period for the magnification to lock onto.
# The banding the dither exists to prevent is fixed; the grain it caused is
# not. Turning the dither off instead (0.0) fixed the grain and brought the
# banding straight back -- reported as "muddy", which is what this replaces.
#
# The Bayer matrix was chosen for two properties: deterministic, so a build is
# reproducible, and position-locked, so a tile relocated into a truecolor page
# dithers identically and neighbouring cells cut from the same art stay
# seamless. A hash of (x, y) has both. It only lacks the period, which was
# never a feature.
#
# `PageArt` is the only production caller, so this affects field backgrounds
# and nothing else.
DITHER_AMPLITUDE = 1.0


def rgba_to_565_buf(rgba, npx, width=None, black_ok=False):
    """
    `npx` RGBA8888 pixels -> packed little-endian R5G6B5.

    DITHERED, and ROUNDED rather than truncated. Both matter, and both were
    wrong before.

    MEASURED on a real build: the truecolor pages hold exactly 32 distinct
    levels per channel and every step between neighbouring pixels is a multiple
    of 8/255 -- 24.8% of horizontally adjacent pixels jump a whole step. That
    is 32x32x32 = 32,768 colours (green is 32, not 64, because its LSB is
    masked off to dodge the engine's 0x07E0 bug), and on the slow gradients
    that fill these scenes -- sky, water, painted walls -- it reads as banding.
    No amount of work in the scaling shader can undo it, because the
    information is already gone by the time the shader runs.

    `>> 3` also TRUNCATES, which throws away up to 7/255 and never adds any, so
    every page was biased dark by ~3.5/255 on average.

    Ordered dithering fixes both. Adding a sub-step offset from an 8x8 Bayer
    matrix before rounding turns the hard band edge into a stable, fine
    checkerboard that the eye integrates back into the intermediate colour --
    which is exactly what the 3x upscale and the Catmull-Rom reconstruction in
    the shader then do for real. Perceived depth goes back to roughly what
    24-bit would look like on this material, for zero bytes and no engine
    change.

    Bayer rather than blue noise on purpose: it is deterministic and
    position-locked, so a tile relocated into a truecolor page dithers the same
    way every build, and neighbouring cells cut from the same art stay
    seamless. `width` is needed to recover (x, y) from the flat index; without
    it the dither is skipped rather than applied wrongly.
    """
    if _np is None:
        return FN.rgba_bytes_to_565(rgba, npx, black_ok=black_ok)
    a = _np.frombuffer(rgba, dtype=_np.uint8, count=npx * 4).reshape(npx, 4)
    r = a[:, 0].astype(_np.float32)
    g = a[:, 1].astype(_np.float32)
    b = a[:, 2].astype(_np.float32)

    if width and dither_on() and DITHER_AMPLITUDE > 0.0:
        i = _np.arange(npx, dtype=_np.uint32)
        x = i % _np.uint32(width)
        y = i // _np.uint32(width)
        # A DETERMINISTIC HASH OF (x, y), NOT A REPEATING MATRIX.
        # Position-locked and reproducible -- the two properties the Bayer
        # matrix was chosen for -- but with no period to survive magnification.
        h = (x * _np.uint32(374761393)) + (y * _np.uint32(668265263))
        h = (h ^ (h >> _np.uint32(13))) * _np.uint32(1274126177)
        h = h ^ (h >> _np.uint32(16))
        d = ((h & _np.uint32(0xFFFF)).astype(_np.float32) / 65536.0 - 0.5) \
            * _np.float32(DITHER_AMPLITUDE)
    else:
        d = _np.float32(0.0)

    # Quantise onto the grid the ENGINE RECONSTRUCTS, which is level * 8, not
    # level * 255/31. The port expands 5 bits back to 8 with a plain shift
    # (0x63F350 widens 565 -> 1555, and 5 bits -> 8 is << 3), so level 31 comes
    # back as 248 and never as 255. Dividing by 255/31 instead of by 8 lands
    # every value about a third of a step low and leaves a systematic -2.7/255
    # darkening that dithering cannot remove, because it is a scale error, not
    # a rounding error. MEASURED both ways below.
    #
    # Green's LSB has to stay clear for the 0x07E0 bug, so its 6-bit field only
    # ever holds even values -- i.e. green is on this same 31-level grid too.
    q = lambda c: _np.clip(_np.floor(c * 0.125 + d + 0.5),
                           0, 31).astype(_np.uint16)
    v = (q(r) << 11) | ((q(g) << 1) << 5) | q(b)

    # `black_ok` keeps genuine black as 0x0000 instead of nudging it to
    # NEAR_BLACK. NEAR_BLACK is 0x0040 -- R0 G8 B0, a flat DARK GREEN, not
    # black. MEASURED on a real build: 17.4% of every truecolor pixel in
    # nmkin_1 and 14.8% in elmin1_1 were that value, which is a visible grey
    # -green wash over what should be unlit shadow.
    #
    # It was only ever there because EMPTY doubled as the "transparent"
    # sentinel for the per-cell opacity gate, so a black pixel had to be
    # nudged off it or a black cell would look transparent. The gate now reads
    # the art's ALPHA directly (PageArt.tmask), so the sentinel is not needed
    # and black can be black. field_convert_type2_layers already renders
    # 0x0000 on a truecolor page as OPAQUE BLACK, which is exactly what is
    # wanted here.
    if not black_ok:
        v = _np.where(v == FN.EMPTY, _np.uint16(FN.NEAR_BLACK), v)
    v = _np.where(a[:, 3] < 8, _np.uint16(FN.EMPTY), v)
    return v.astype('<u2').tobytes()


# --------------------------------------------------------------------------
# resampling
# --------------------------------------------------------------------------
# How a 1024x1024 Cosmos page becomes a 256px (or 512px) background page.
#
# WHAT WAS HERE BEFORE, AND WHAT WAS ACTUALLY WRONG WITH IT
# ---------------------------------------------------------
# `img.resize(..., Image.BOX if w > page_px else Image.NEAREST)`.
#
# For 1024 -> 256 that BOX is an exact 4x4 area average, and a two-pass
# bilinear halving (1024 -> 512 -> 256) computes the SAME thing to within
# rounding, because bilinear at an exact 2:1 step IS a 2x2 average. So
# "two-pass bilinear" on its own is not the fix -- it is a different spelling
# of the filter that is already running.
#
# A THEORY THAT WAS TESTED AND IS FALSE -- do not re-derive it
# -----------------------------------------------------------
# The obvious suspect was alpha bleed: PIL averaging the RGB of fully
# transparent texels into their opaque neighbours at equal weight, which
# would put a dark fringe on every cutout edge. It does not happen. MEASURED
# with a synthetic 1024px page, opaque grey 160 up to x=513 and transparent
# black beyond, resized to 256 with BOX:
#
#     output col 128 (source 512..515 = 2 opaque + 2 transparent)
#         naive per-band average would be    rgb  80, alpha 128
#         Pillow actually returns            rgb 159, alpha 128
#
# 159, not 80. Pillow premultiplies internally for RGBA. Adding our own
# premultiply/un-premultiply on top DOUBLE-APPLIES it and drives edge pixels
# to 255 -- which is what the first version of this function did before the
# test above caught it. There is nothing to fix here.
#
# WHAT IS ACTUALLY CHANGED
# ------------------------
#   1. NEAREST on the way UP -> BILINEAR. Any page whose art is smaller than
#      the target got point sampling: hard blocky edges sitting next to
#      correctly filtered neighbours in the same picture. This is a real
#      defect and it is the only unambiguous one.
#
#   2. A halving ladder before the final step. At 1024 -> 256 this is a
#      no-op to within rounding -- two 2:1 BOX passes and one 4:1 BOX pass
#      compute the same average -- so it changes nothing today. It matters at
#      the sizes we are heading for: 1024 -> 320 is 3.2:1, where one BOX pass
#      buckets 3 or 4 source texels per output pixel and the bucket
#      boundaries beat against the 16-texel tile grid. Halving to 640 first
#      leaves a clean 2:1 remainder.
#
# SO IF THE 256px PAGES STILL LOOK WRONG, IT IS NOT THIS FUNCTION. The next
# suspects, in order, are the RGB565 quantiser with its ordered dither
# (`rgba_to_565_buf` above) and the BORROW path -- the build log's own
# numbers say only 67,579 of 324,712 promoted cells are exact art from the
# mod and 204,868 are borrowed from another (page, palette).
RESAMPLE_DOWN = 'BOX'         # per ladder step when shrinking
RESAMPLE_UP = 'BILINEAR'      # was NEAREST -- see (1) above
RESAMPLE_LADDER = True        # halve repeatedly before the final step


def _filter(name):
    from PIL import Image
    try:
        return getattr(Image.Resampling, name)
    except AttributeError:                                     # Pillow < 9.1
        return getattr(Image, name)


def resample_rgba(rgba, w, h, px, log=None):
    """(w x h) RGBA bytes -> (px x px) RGBA bytes, alpha-weighted.

    Returns the input unchanged when it is already the right size, so a
    correctly sized page costs nothing and stays byte-identical.
    """
    from PIL import Image
    if (w, h) == (px, px):
        return rgba
    img = Image.frombytes('RGBA', (w, h), rgba)
    if w > px or h > px:
        # Pillow already weights RGBA colour by alpha; see the note above for
        # the measurement that proves it. Nothing to premultiply here.
        f = _filter(RESAMPLE_DOWN)
        cw, ch = w, h
        # ONLY when the ratio is not a whole number.
        #
        # MEASURED on random 1024px pages: at 1024 -> 256 and 1024 -> 512 a
        # single BOX pass IS the exact area average, and halving first is
        # very slightly WORSE because the intermediate is re-quantised to 8
        # bits (max |delta| 7/255, mean 1.0). At 1024 -> 320 the two differ
        # by a mean of 15.6 -- that is the beat this exists to remove.
        #
        # Gating on `w % px` therefore makes this function a byte-exact
        # no-op at every page size we ship today, so it cannot be blamed for
        # anything in the current build, and it only engages at the awkward
        # sizes.
        if RESAMPLE_LADDER and (w % px or h % px):
            while (cw // 2 >= px and ch // 2 >= px
                   and (cw // 2, ch // 2) != (px, px)):
                cw, ch = cw // 2, ch // 2
                img = img.resize((cw, ch), f)
        if (cw, ch) != (px, px):
            img = img.resize((px, px), f)
        return img.tobytes()
    return img.resize((px, px), _filter(RESAMPLE_UP)).tobytes()


class PageArt:
    """One (page, palette) image as a packed 565 page, ready to crop cells."""

    __slots__ = ('px', 'buf', '_op', 'tmask', 'bmask', 'hmask')

    def __init__(self, dds_bytes, page_px):
        import dds_decode
        rgba, w, h = dds_decode.decode_dds(dds_bytes)
        rgba = resample_rgba(rgba, w, h, page_px)
        self.px = page_px
        n = page_px * page_px
        # black_ok stays FALSE: 0 means transparent to the engine and a
        # transparent background pixel writes no occlusion. NEAR_BLACK is
        # now the dimmest non-zero colour the format has, not the green one.
        self.buf = rgba_to_565_buf(rgba, n, page_px)
        # What disqualifies a cell from being upgraded. Transparency always
        # does, and it comes from the art's ALPHA rather than a reserved
        # colour. With true_black() on, opaque black disqualifies it too --
        # see true_black() for why that is the only exact way to keep it.
        #
        # `hmask` IS A DIFFERENT QUESTION FROM `tmask` AND THE DIFFERENCE
        # MATTERS. FINDINGS-247.
        #
        # `tmask` exists to DISQUALIFY a cell from promotion, so its
        # threshold is deliberately paranoid: alpha < 8 -- one part in 32 of
        # transparency is enough to say "this cell contains transparency".
        # That is right for a per-cell veto and WRONG as a per-texel "should
        # this be drawn" test, because its complement calls a texel at 4%
        # alpha fully painted.
        #
        # It matters now because `field_bg_dense.SUBUNIT_KEY` turns the mod's
        # alpha into a 1-bit decision PER TEXEL, and the honest 1-bit
        # reduction of an 8-bit alpha is a 50% threshold: at 128 the error is
        # symmetric either way. MEASURED on the newly-opaque texels of
        # `mtcrl_4`, `mtcrl_5` and `wcrimb_2` -- 45%, 27% and 21% of them sit
        # at alpha 8..127, i.e. mostly TRANSPARENT, and Cosmos draws a dark
        # outline along exactly those boundaries. Drawing a 25%-alpha dark
        # outline at full strength is a black fringe one texel wide around
        # every overlay, which is the defect build 116 spent a whole session
        # removing. `hmask` is what keeps this change from reintroducing it.
        #
        # Pillow's BOX resize is alpha-weighted -- verified again here, a
        # quarter-covered texel comes back (199,179,159,128) from
        # (200,180,160,255) -- so the COLOUR at a partial texel is the art's
        # own and the only thing that is wrong is drawing it opaque.
        #
        # Costs one bool page (256 KB at 512px) beside the two that already
        # exist, and nothing reads it unless SUBUNIT_KEY is on.
        if _np is not None:
            a = _np.frombuffer(rgba, dtype=_np.uint8, count=n * 4)
            op = a[3::4] >= 8
            self.tmask = (~op).reshape(page_px, page_px)
            self.bmask = (op & ((a[0::4] | a[1::4] | a[2::4]) == 0)
                          ).reshape(page_px, page_px)
            self.hmask = (a[3::4] >= 128).reshape(page_px, page_px)
        else:
            self.tmask = bytes(1 if rgba[i * 4 + 3] < 8 else 0
                               for i in range(n))
            self.bmask = bytes(
                1 if (rgba[i * 4 + 3] >= 8
                      and not (rgba[i * 4] | rgba[i * 4 + 1]
                               | rgba[i * 4 + 2])) else 0
                for i in range(n))
            self.hmask = bytes(1 if rgba[i * 4 + 3] >= 128 else 0
                               for i in range(n))
        self._op = {}

    def cell_opaque(self, cx, cy, grid):
        """
        True if this cell of the ART has no transparent pixel.

        The authority on transparency is the art, not the paletted page it
        replaces. FFNx builds the .dds through `pal2bgra` (common.cpp:1726):

            if (color_key && pixel == 0) return 0;      // index 0 -> alpha 0
            color = palette[palette_offset + pixel];    // ANY other index
            return color;                               //   carries the
                                                        //   palette's own alpha

        so index 0 is not the only source of transparency -- any palette
        entry with alpha 0 produces it too. Checking the source page for
        index 0 misses those, they pack as EMPTY, and
        field_convert_type2_layers turns EMPTY into OPAQUE BLACK. That is the
        black left in the town and the reactor.
        """
        g = self._op.get(grid)
        if g is None:
            g = self._opacity_grid(grid)
            self._op[grid] = g
        return g[cy][cx]

    def _opacity_grid(self, grid):
        """grid x grid booleans, computed for the whole page at once."""
        side = self.px // grid
        if _np is not None:
            # (grid, side, grid, side) -> any TRANSPARENT pixel in each cell.
            # Read from the alpha mask, not from the packed colour: black is a
            # legitimate colour and must not read as transparent.
            ok = ~self.tmask.reshape(grid, side, grid, side).any(axis=(1, 3))
            thr = black_cell_threshold()
            if thr > 0.0:
                # a mostly-black cell keeps its paletted page, where black is
                # exact -- see black_cell_threshold()
                bf = self.bmask.reshape(grid, side, grid, side).mean(axis=(1, 3))
                ok = ok & (bf < thr)
            return ok.tolist()
        out = []
        thr = black_cell_threshold()
        for cy in range(grid):
            row_out = []
            for cx in range(grid):
                ok = True
                nblack = 0
                for y in range(cy * side, (cy + 1) * side):
                    base = y * self.px + cx * side
                    if 1 in self.tmask[base:base + side]:
                        ok = False
                        break
                    nblack += sum(self.bmask[base:base + side])
                if ok and thr > 0.0 and nblack >= thr * side * side:
                    ok = False
                row_out.append(ok)
            out.append(row_out)
        return out

    def blit_into(self, dst, dst_px, grid, scx, scy, dcx, dcy):
        """Copy one grid cell into `dst`, row by row. Both are packed 565."""
        side = self.px // grid
        src = self.buf
        sw = self.px * 2
        dw = dst_px * 2
        s0 = (scy * side) * sw + scx * side * 2
        d0 = (dcy * side) * dw + dcx * side * 2
        n = side * 2
        for y in range(side):
            s = s0 + y * sw
            d = d0 + y * dw
            dst[d:d + n] = src[s:s + n]


# -------------------------------------------------------------------- repack

_OPAQUE_CACHE = {}


def _opaque(page, cx, cy):
    """True if this 16x16 cell of a paletted page has no colour-key pixel."""
    key = (id(page), cx, cy)
    hit = _OPAQUE_CACHE.get(key)
    if hit is None:
        d = page.data
        hit = True
        for y in range(cy * 16, (cy + 1) * 16):
            if d.find(0, y * 256 + cx * 16, y * 256 + cx * 16 + 16) >= 0:
                hit = False
                break
        _OPAQUE_CACHE[key] = hit
    return hit


def _group(slot, table):
    for lo, hi, blend in table:
        if lo <= slot < hi:
            return blend
    return None


def strict():
    return os.environ.get(STRICT_ENV, '').strip().lower() in ('1', 'true',
                                                              'yes', 'on')


def max_new_pages():
    """
    How many truecolor pages one field may gain. 0 disables the cap.

    EVERY present page becomes a texture, and `field_load_textures`
    (x86 0x640292) ABORTS THE WHOLE LOOP the moment one fails:

        0064058A  call 0x6710AC          ; _load_texture
        006405A2  mov  [page+8], eax
        006405B5  cmp  dword [page+8], 0
        006405B9  jne  0x6405D6
        006405D2  xor  eax, eax
        006405D4  jmp  0x640609          ; return 0

    Every page after the failure keeps handle 0, and 0x66E272 returns 0 on a
    null handle, so field_pick_tiles_make_vertices skips those pages entirely
    -- their tiles simply do not draw. On screen that is scattered black
    squares, because one page holds cells from all over the picture.

    A repacked field asks for its original pages PLUS the new ones, and a
    512x512 truecolor page is four times the pixels of a 256x256 one. Capping
    the new pages keeps the request near what the port already copes with.
    """
    raw = os.environ.get(MAX_PAGES_ENV, '').strip()
    if not raw:
        return DEFAULT_MAX_NEW_PAGES
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_MAX_NEW_PAGES


def d2_slots_per_group():
    """
    How many truecolor page slots a field may take from ONE blend group.
    0 disables the cap and restores the old behaviour exactly.

    See DEFAULT_D2_SLOTS_PER_GROUP for the measurement. This is a ceiling on
    WHICH SLOT gets used, which is what `field_load_textures` actually refuses.
    Every other control in this module caps a page count or a byte total, and
    on a real build none of them binds at all: the heaviest field holds 13
    pages, so `field_bg_max_pages` at 14, 15, 16 or unlimited are the same
    setting, and `mds6_2` shows the defect with SEVEN.
    """
    raw = os.environ.get(D2_SLOT_ENV, '').strip()
    if not raw:
        return DEFAULT_D2_SLOTS_PER_GROUP
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_D2_SLOTS_PER_GROUP


def budget_bytes():
    """
    How much field-background texture one field may ask for, in bytes.

    MEASURED against a real build and the player's own report of which fields
    were clean and which were full of black squares:

        elmin1_1, elmin1_2, elmin2_1, elmin2_2  CLEAN  1 x 512  2.44 MB
        nmkin_1                                 BLACK  3 x 512  6.06 MB
        nmkin_2, nmkin_3, nmkin_4, mds5_1       BLACK  4 x 512  7.88-8.50 MB

    Every clean field had exactly one truecolor page; every black one had three
    or four. mds5_1 is 100% truecolor tiles and fully black, so this is not the
    paletted tiles failing -- it is the sheer volume of truecolor. Vanilla's
    heaviest field is 11 pages at 256 = 3.44 MB, which is the order the port
    was provisioned for.

    That places the real limit somewhere in [2.44, 6.06) MB. 4.0 MB is the
    middle of the measured bracket, not a derived number -- the allocation
    fails inside the recompiled driver where the size is not readable.

    WHAT THAT STORY GOT WRONG, and why 0 now means UNLIMITED
    -------------------------------------------------------
    Every number above was taken at 512px pages, and the model behind it --
    "the field runs out of texture memory" -- does not survive its own data:

      * failure is NOT MONOTONIC in the budget. Hardware showed black bars at
        18 MB and a clean picture at 14 MB. No ceiling produces that.
      * lowering the budget to 4.0 MB made the margins WORSE, not better.
      * a per-field byte cap only ever produced PARTIAL promotion -- 984 of
        4,488 available pages upscaled in the last build, 1,363 left paletted
        -- which is what puts a truecolor page next to a paletted one on the
        same field and is its own source of inconsistency.

    So the cap is no longer the thing being tuned; the PAGE SIZE is. At 128px
    a page costs 0.09 MB and the heaviest field in the game (12 pages) totals
    1.13 MB -- less than the 3.75 MB the same field already costs as vanilla
    paletted pages. There is nothing left for a budget to protect.

    0 (or "unlimited") therefore means NO LIMIT, where it used to mean
    "promote nothing" -- max(0.0, mb) made an empty budget silently disable
    the feature. The numeric entries are kept for bisecting.
    """
    raw = os.environ.get(BUDGET_ENV, '').strip()
    if raw.lower() in ('0', 'unlimited', 'none', 'off', 'inf'):
        return UNLIMITED
    try:
        mb = float(raw) if raw else DEFAULT_BUDGET_MB
    except ValueError:
        mb = DEFAULT_BUDGET_MB
    if mb <= 0.0:
        return UNLIMITED
    return int(mb * 1048576)


def safety_note():
    """
    One line saying what guarantee the current settings actually give.

    Written because the controls multiplied faster than the understanding
    did, and only ONE of them is a guarantee rather than a guess:

      * page size, budget, max pages -- all TUNING. Every value is a bet on
        where an allocator gives up, and the only hardware evidence is
        non-monotonic (18 MB black, 14 MB clean), so none of them can be
        argued to a safe number.
      * replace_only -- a PROOF. The field ends with no more pages than
        vanilla ships, so it asks the loader for no more textures than the
        stock game does. Verified over all 711 fields, 1,418 checks, zero
        violations of pages_after <= pages_vanilla.

    If the stock game loads a field, a replace-only build of that field
    loads too, because it makes strictly fewer or equal allocations of the
    same kind. That is the only statement here that does not depend on
    guessing the ceiling.
    """
    if no_growth():
        return ('no-growth: every field is MEASURED after compaction and, if '
                'it still holds more pages than it started with, the dense '
                'repack is RE-RUN at a lower truecolor ceiling until it does '
                'not. A field that is still over with the repack fully off '
                'is NAMED above rather than silently allowed -- its extra '
                'page came from ff7nx_marginpage, not from this stage. '
                '(This loop lived in field_bg_repack, which stopped being '
                'called; it was restored to _convert_field_backgrounds after '
                'a build grew 123 fields while this line claimed otherwise.)')
    if replace_only():
        return ('replace-only: no field ends with more pages than vanilla '
                'ships, so the loader is never asked for more textures than '
                'the stock game asks for (but only ~13% of pages promote -- '
                'no-growth gives the same promise and 2.4x the art)')
    cap = max_total_pages()
    return ('page count may GROW above vanilla (up to %s per field); this is '
            'a tuned limit, not a guarantee -- turn on replace-only for one'
            % (cap or 'unlimited'))


def replace_only():
    """
    True when a page is promoted ONLY if doing so frees the original.

    The lever for getting a big page size without black squares: it holds the
    texture COUNT at vanilla's while letting the page SIZE go up. Off by
    default because it promotes strictly less art than the unrestricted mode;
    turn it on when the page ceiling is the thing holding you back.
    """
    return os.environ.get(REPLACE_ONLY_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def max_total_pages():
    """
    Ceiling on pages PRESENT in one field after the repack. 0 = no cap.

    See MAX_TOTAL_PAGES_ENV for why this is the constraint that matters.
    """
    raw = os.environ.get(MAX_TOTAL_PAGES_ENV, '').strip()
    if not raw:
        return DEFAULT_MAX_TOTAL_PAGES
    if raw.lower() in ('0', 'unlimited', 'none', 'off'):
        return 0
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return DEFAULT_MAX_TOTAL_PAGES


def all_or_nothing():
    """
    True when a field promotes EVERY page it can or none at all.

    This is the setting that buys consistency, and it is the default. Partial
    promotion is what mixes a truecolor page and a paletted one inside one
    picture; taking "the busiest N pages until the money runs out" optimises
    for coverage of a single screen and against the picture looking like one
    thing. When the whole field does not fit, the field keeps its
    Switch-vanilla background -- correct, just not upgraded.

    Set SEVENTH_NX_FIELD_BG_PARTIAL=1 to get the old behaviour back for
    comparison.
    """
    return os.environ.get(PARTIAL_ENV, '').strip().lower() not in (
        '1', 'true', 'yes', 'on')


def borrow_exact():
    """
    True when a cell may only be promoted in ITS OWN palette.

    This is the setting that removes the colour discontinuity, and it is the
    default because the alternative is not an approximation, it is a wrong
    answer that the build was reporting as a success.

    When the mod has no image for a tile's palette, the old rule substituted
    the nearest palette that did have one. MEASURED on a real build:
    **8,558 of 40,956 promoted cells -- 20.9% -- rendered in a neighbouring
    palette's colours.** One promoted cell in five was the wrong colour, sat
    next to cells that were the right colour, in the same picture. That is
    exactly the "stock textures and upscaled textures side by side" report,
    and no page size, ceiling or compaction can fix it because it is a colour
    error, not a memory problem.

    The rule now follows the engine instead of guessing:

      * BELOW slot 0x0F the substitution is EXACT and stays on. The engine
        builds one texture for the page and the palette_ID selects nothing
        (see PAL_TEXTURE_BOUNDARY), so the mod's single dump is what every
        tile there was always going to sample.
      * At 0x0F AND UP a borrowed palette is a real colour error, so the cell
        keeps its paletted page instead. It still DRAWS -- in its own,
        correct colours, at the same 256x256 size -- it just does not get the
        colour-depth upgrade.

    So the promoted set gets smaller and everything in it is right. Set
    SEVENTH_NX_FIELD_BG_BORROW=nearest for the old behaviour.
    """
    if legacy():
        return False
    return os.environ.get(BORROW_ENV, '').strip().lower() not in (
        'nearest', 'near', 'substitute', '0', 'off')


def dedup():
    """
    True when identical cells share ONE destination cell instead of a copy
    each. On by default; SEVENTH_NX_FIELD_BG_DEDUP=0 turns it off.

    This is the cheapest page-count reduction there is, because it is not a
    trade at all -- the cells it collapses are BYTE-IDENTICAL, so the picture
    that comes out is the same picture. Two independent duplications:

    1. PALETTE SUBSTITUTION. `cells_of` was keyed by the tile's raw
       `palette_ID`, so a page drawn with four palettes allocated four copies
       of every cell. But what is actually blitted is
       `art_for(slot, sub[(slot, pal)])` -- the SUBSTITUTED palette. Cosmos
       Limit Break ships a mean of 1.36 images per page against vanilla's
       2.8 palettes per page, and every page below slot 0x0F is dumped as
       palette 0 ONLY (field_load_textures at x86 0x640569 sets
       texheader+0xC = 1 only when slot >= 0x0F and depth == 1, so the engine
       makes one texture for the whole page and the palette byte selects
       nothing). So most of those copies were the same image cropped at the
       same coordinates. Keying by `sub` collapses them.

    2. CONTENT. Flat sky, repeated masonry, and any two palettes that agree
       on the indices a cell actually uses produce identical PIXELS from
       different sources. The key here is the cell's bytes themselves, not a
       hash -- a field's cells are a few thousand at 512 bytes each, so
       exactness is free and there is no collision to reason about.

    Sharing a destination cell is safe for the same reason it is invisible: a
    tile carries a page slot and a u,v, and two tiles pointing at one cell
    sample the same texels they would have sampled from two identical copies.
    Neither duplication can be an animation frame -- tiles with an fx page are
    excluded from promotion before this point, because they share one u,v
    across two pages.
    """
    if legacy():
        return False
    return os.environ.get(DEDUP_ENV, '').strip().lower() not in (
        '0', 'false', 'no', 'off')


class Stats:
    def __init__(self):
        self.pages_upgraded = 0
        self.pages_exact = 0        # every palette had its own image
        self.pages_single = 0       # the mod dumped ONE image for it
        self.pages_uncovered = 0
        self.pages_sizeflag = 0
        self.pages_nofit = 0
        self.cells = 0
        self.cells_borrowed = 0     # palette substituted from a neighbour
        self.cells_transparent = 0  # left paletted: a truecolor page cannot
                                    # hold a colour key
        self.tiles_fx = 0           # left paletted: shares one u,v with an
                                    # fx page that is not being moved
        self.cells_art_transparent = 0   # the mod's art is transparent here
        self.pages_capped = 0       # skipped to stay under the page budget
        self.pages_allornothing = 0  # the field would have promoted this many
                                    # pages, but not ALL of them, so it kept
                                    # its vanilla background instead. See
                                    # all_or_nothing().
        self.pages_dropped = 0      # original page freed: nothing points at
                                    # it any more, so it need not be a texture
        self.new_pages = 0
        self.tiles = 0
        self.pages_notfull = 0      # replace-only: the page keeps some tiles
                                    # that cannot move, so promoting it would
                                    # ADD a texture rather than replace one
        self.cells_wrong_palette = 0  # kept paletted: the mod has no image for
                                      # this cell's own palette, and at or
                                      # above slot 0x0F borrowing a
                                      # neighbour's is a real colour error
        self.cells_merged_pal = 0   # dedup: palettes that resolve to the same
                                    # image now share one cell
        self.cells_merged_px = 0    # dedup: cells whose PIXELS are identical
                                    # now share one cell

    def __bool__(self):
        return self.pages_upgraded > 0


def repack_section9(sec9, field, art_for, page_px=512, log=None,
                    src_px=None, pals_for=None, total_cap=None):
    """
    Rewrite one section 9 so the mod's art is used at `page_px`.

    `art_for(page, palette)` returns a PageArt or None. Returns
    (new_sec9, Stats); the section is returned unchanged when nothing could be
    upgraded.
    """
    st = Stats()
    _OPAQUE_CACHE.clear()
    # `src_px` is the size depth-2 pages ALREADY have in this section --
    # field_bg_native.resize_section9 normally runs first, so by the time we
    # get here the pre-existing truecolor pages are already at `page_px`.
    pages, tex_start, tex_end = FN.parse_texture_block(
        sec9, src_px if src_px is not None else FN.VANILLA_PX)
    spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    pmap = {p.slot: p for p in pages if p is not None}

    # ---- what each paletted page is drawn with
    pal_of = {}                      # page -> set(palette)
    cells_of = {}                    # page -> set((cx, cy, palette))
    tiles_of = {}                    # page -> [(off, cx, cy, pal)]
    all_tiles_of = {}                # page -> how many tiles draw from it AT
                                     # ALL, movable or not. This is what says
                                     # whether promoting the page can FREE it;
                                     # see replace_only().
    # A page is ALSO kept alive by being some OTHER tile's fx page, and that
    # reference is invisible to `all_tiles_of` -- it is counted against the
    # tile's own T_TEXID, not against the fx page. The free pass at the end
    # of this function tests `still_used`, which includes T_FX_PAGE, so a
    # projection that ignored it declared pages freed that never are.
    #
    # MEASURED consequence: a build with the ceiling set to 12 produced a
    # 15-page field, because three pages the projection had written off were
    # still fx targets. Every page is a texture, so the over-count went
    # straight into the allocation failures.
    fx_referenced = set()
    for off in spans:
        fxp = sec9[off + T_FX_PAGE]
        if fxp:
            fx_referenced.add(fxp)
    for off in spans:
        slot = sec9[off + T_TEXID]
        p = pmap.get(slot)
        if p is None or p.depth != 1:
            continue
        all_tiles_of[slot] = all_tiles_of.get(slot, 0) + 1

        grid = 8 if p.size_flag else 16
        u, v = struct.unpack_from('<II', sec9, off + T_SRC_X_BIG)
        cx = int(round(u / UV_SCALE * grid))
        cy = int(round(v / UV_SCALE * grid))
        if not (0 <= cx < grid and 0 <= cy < grid):
            continue
        # A TILE WITH AN FX PAGE CANNOT BE MOVED.
        #
        # FFNx's own field/background.cpp:199 -- the line the engine runs:
        #
        #     page = tile.use_fx_page ? tile.fx_page : tile.page;
        #     add_page_tile(x, y, z, tile.u, tile.v, tile.palette_index, page);
        #
        # ONE u,v, TWO pages. Relocating the tile rewrites u,v for the new
        # truecolor page, and the fx page then gets sampled at coordinates
        # that mean nothing in it -- so an animated tile shows a block of
        # some unrelated part of the old page, or an unwritten cell, which is
        # black. That is the "animated effects on squares they should not be
        # on" and the last of the black rectangles.
        #
        # MEASURED: `u16 at offset 28 == 1` is exactly the set of tiles with
        # a non-zero fx page -- 80,058 of them, zero disagreement either way
        # (x86 0x62BB85 reads that u16 and compares it to 1 to set
        # field_tile.use_fx_page at +0x103C). A further 36,529 carry an fx
        # page with the flag clear at load time, and the animation may set it
        # later, so the test here is the fx page itself, not the flag.
        #
        # Moving these properly means allocating the tile's cell at the SAME
        # grid position in two parallel truecolor pages, one for each. That
        # is a constrained packing and it is not done here; costs 20.6% of
        # the otherwise-upgradable tiles, and they are the animated overlays.
        if sec9[off + T_FX_PAGE]:
            st.tiles_fx += 1
            continue

        # A TRUECOLOR PAGE CANNOT BE TRANSPARENT.
        #
        # field_convert_type2_layers (x86 0x63F385) replaces every depth-2
        # pixel equal to 0 with convert(0xFF000000) -- OPAQUE BLACK. On a
        # paletted page index 0 is the colour key; move that cell to a
        # truecolor page and the cut-out turns into a black rectangle. That
        # is what the first repacked build looked like.
        #
        # So the decision is per CELL, not per page: cells are being
        # relocated individually anyway, and a tile whose source cell has any
        # index 0 in it simply keeps pointing at the original paletted page,
        # which is still present. MEASURED on the real build: 67.7% of used
        # cells are fully opaque and do upgrade.
        if not _opaque(pmap[slot], cx, cy):
            st.cells_transparent += 1
            continue
        pal = sec9[off + T_PALETTE]
        pal_of.setdefault(slot, set()).add(pal)
        cells_of.setdefault(slot, set()).add((cx, cy, pal))
        tiles_of.setdefault(slot, []).append((off, cx, cy, pal))

    # ---- which pages may be upgraded, and which palette each one borrows
    #
    # `sub[(slot, palette)]` is the palette whose image is actually used.
    # Identity where the mod has that palette; the nearest available one
    # otherwise. See "PALETTE COVERAGE" above for why a strict rule leaves
    # 97% of the game paletted.
    strict_mode = strict()
    candidates = []
    sub = {}
    for slot, pals in pal_of.items():
        p = pmap[slot]
        if p.size_flag:
            st.pages_sizeflag += 1
            continue
        # What the mod HAS for this page -- not the intersection with the
        # palette IDs the tiles carry. Those two are different things and
        # confusing them is what left 97% of the game paletted. See
        # "ONE TEXTURE PER PAGE" above.
        avail = sorted(pals_for(slot)) if pals_for is not None else sorted(pals)
        avail = [q for q in avail if art_for(slot, q) is not None]
        if not avail:
            st.pages_uncovered += 1
            continue
        if strict_mode and not set(pals) <= set(avail):
            st.pages_uncovered += 1
            continue
        if len(avail) == 1:
            st.pages_single += 1
        elif set(pals) <= set(avail):
            st.pages_exact += 1
        for q in pals:
            sub[(slot, q)] = q if q in avail else min(
                avail, key=lambda h, _q=q: (abs(h - _q), h))
        candidates.append(slot)
    if not candidates:
        return sec9, st

    # ---- drop cells whose ART is not fully opaque (see cell_opaque)
    GRID0 = 16
    keep = []
    orig_cells_of = {}               # page -> the (cx, cy, RAW palette) set,
                                     # kept only so cells_borrowed keeps
                                     # meaning what it meant before dedup
    exact_only = borrow_exact()
    for slot in candidates:
        good = set()
        n_wrong_pal = 0
        for cx, cy, pal in cells_of[slot]:
            use = sub[(slot, pal)]
            # A BORROWED PALETTE AT OR ABOVE 0x0F IS THE WRONG COLOUR.
            # Below it the engine builds one texture for the page and the
            # palette selects nothing, so the substitution is exact. Above it
            # the tile would really have been drawn in its own palette, and
            # promoting it in someone else's is the discontinuity people see.
            # See borrow_exact().
            if exact_only and use != pal and slot >= PAL_TEXTURE_BOUNDARY:
                n_wrong_pal += 1
                continue
            art = art_for(slot, use)
            if art is not None and art.cell_opaque(cx, cy, GRID0):
                good.add((cx, cy, pal))
        st.cells_wrong_palette += n_wrong_pal
        dropped = len(cells_of[slot]) - len(good) - n_wrong_pal
        if dropped:
            st.cells_art_transparent += dropped
        if not good:
            continue
        orig_cells_of[slot] = good
        # ---- DEDUP 1: palettes that resolve to the same image
        #
        # `good` is keyed by the tile's RAW palette_ID, but the pixels that
        # get blitted come from `art_for(slot, sub[(slot, pal)])`. Where two
        # palettes substitute to one image -- which is every page below slot
        # 0x0F, and most above it, because the mod ships 1.36 images per page
        # against 2.8 palettes -- those are the SAME cell cropped at the same
        # coordinates. Keying by the substituted palette collapses them and
        # changes not one pixel. See dedup().
        cells_of[slot] = {(cx, cy, sub[(slot, pal)]) for cx, cy, pal in good}
        st.cells_merged_pal += len(good) - len(cells_of[slot])
        tiles_of[slot] = [(off, cx, cy, sub[(slot, pal)], pal)
                          for off, cx, cy, pal in tiles_of[slot]
                          if (cx, cy, pal) in good]
        if tiles_of[slot]:
            keep.append(slot)
    candidates = keep
    if not candidates:
        return sec9, st

    # ---- DEDUP 2: cells whose PIXELS are identical
    #
    # Flat sky, repeated masonry, and any two palettes that happen to agree on
    # the indices a cell uses all produce the same bytes from different
    # sources. The key is the cell's bytes themselves rather than a hash: a
    # field's cells are a few thousand at 512 bytes each (2 KB at 512px), so
    # being exact costs nothing and there is no collision to argue about.
    #
    # With dedup off the key is made unique per (slot, cell, palette), so the
    # take loop and the allocator below run unchanged and merge nothing --
    # the two paths are the same code, not two implementations.
    dedup_on = dedup()
    cellbuf = bytearray((page_px // GRID0) * (page_px // GRID0) * 2)
    cell_px = page_px // GRID0
    src_of = {}                      # key -> (art, scx, scy)
    key_of = {}                      # (slot, cx, cy, use) -> key
    keys_of = {}                     # slot -> distinct keys, in cell order
    bytes_cache = {}                 # (slot, use, cx, cy) -> the cell's bytes
    for slot in candidates:
        seen = {}
        for cx, cy, use in sorted(cells_of[slot]):
            art = art_for(slot, use)
            if dedup_on:
                bk = (slot, use, cx, cy)
                k = bytes_cache.get(bk)
                if k is None:
                    art.blit_into(cellbuf, cell_px, GRID0, cx, cy, 0, 0)
                    k = bytes_cache[bk] = bytes(cellbuf)
            else:
                k = (slot, cx, cy, use)
            key_of[(slot, cx, cy, use)] = k
            if k not in seen:
                seen[k] = None
                src_of.setdefault(k, (art, cx, cy))
        keys_of[slot] = list(seen)
        st.cells_merged_px += len(cells_of[slot]) - len(seen)

    # ---- REPLACE-ONLY: never grow the page count
    #
    # This is the setting that fixes black squares without giving up page
    # SIZE, and it works because it attacks the cause rather than the
    # symptom.
    #
    # The repack normally ADDS a page: the promoted cells go to a new
    # truecolor page, but the original paletted page has to stay alive for
    # every tile that could not move (colour-key cells, fx-page tiles, cells
    # the mod draws transparent). Two textures where there was one. MEASURED
    # on a real build: 1,697 new pages against 184 freed, +2.3 per field.
    #
    # A page whose tiles ALL move is different -- nothing references it
    # afterwards, the free pass below drops it, and the net page count is
    # unchanged or lower. Promoting only those keeps the texture count at
    # vanilla's, so the page size is free to go up without adding a single
    # allocation for the loader to fail on.
    #
    # It also happens to be the most consistent result available: a partly
    # evacuated page is exactly the case where the same original art appears
    # twice in one picture, once truecolor and once paletted.
    # The per-page test below is a PREFILTER, not the guarantee. A page whose
    # tiles all move can still become SEVERAL pages, because `cells_of` is
    # keyed by (cx, cy, PALETTE): one page drawn with three palettes needs up
    # to three times the cells and therefore up to three new pages. An
    # earlier version stopped here and called that a guarantee; it is not,
    # and a build produced a 15-page field from a 12-page one with it on.
    #
    # The real enforcement is in the take loop, which refuses any candidate
    # whose PROJECTED page count exceeds what the field started with. That is
    # exact by construction and cannot be wrong about palettes, blend-group
    # packing or freeing.
    if replace_only():
        full = [s for s in candidates
                if len(tiles_of[s]) >= all_tiles_of.get(s, 0)]
        st.pages_notfull = len(candidates) - len(full)
        candidates = full
        if not candidates:
            return sec9, st

    # ---- capacity, per blend group
    free = {}
    per_group = d2_slots_per_group()
    for lo, hi, blend in D2_GROUPS:
        # The ceiling is applied HERE, where capacity is declared, and not as
        # a separate refusal further down. Everything below already reasons
        # about `len(free[blend])` -- `pages_nofit`, the projection, and
        # all_or_nothing -- so a page that no longer fits is counted and
        # reported through the paths that already exist. A page dropped for
        # this reason is indistinguishable, to the rest of the function, from
        # one dropped for want of room, which is what it is.
        top = min(hi, lo + per_group) if per_group else hi
        free[blend] = [s for s in range(lo, top) if s not in pmap]
    # busiest pages first, so a field that cannot fit keeps the art that
    # covers the most of the screen
    candidates.sort(key=lambda s: -len(tiles_of[s]))

    # How many NEW truecolor pages this field can afford. Every present page
    # becomes a texture, and field_load_textures (x86 0x640292) aborts the
    # whole loop the moment one fails -- every page after it keeps handle 0,
    # 0x66E272 refuses a null handle, and those tiles never reach the GPU. On
    # screen that is scattered black squares, because one page holds cells
    # from all over the picture. See budget_bytes() for the measurements.
    #
    # The paletted pages already present are charged first, as an upper bound:
    # some will be freed below, but that is not known yet and guessing high is
    # the safe direction.
    # THE BUDGET USED TO DOUBLE-COUNT THE TRANSITION, and it is worth being
    # precise about why, because the setting was wrong rather than badly
    # tuned. It read:
    #
    #     spent  = every page currently present, charged in full
    #     afford = (budget - spent) // per_new
    #
    # `spent` charges pages that promoting will FREE. So the arithmetic bills
    # the old page and its replacement at the same time, as if both existed
    # at once, when after the repack only one of them does. The effect is not
    # merely conservative, it is backwards:
    #
    #     field with  2 paletted pages -> affords 3 new  (all promoted)
    #     field with  5                -> affords 2      (mixed)
    #     field with  8                -> affords 2      (mixed)
    #     field with 12                -> affords 1      (mixed)
    #
    # The heaviest field -- the one with the most art to fix -- got ONE page,
    # so the setting meant to prevent a half-paletted picture reliably
    # produced one. That is the "budget was always screwed up" report, and it
    # is arithmetic, not tuning.
    #
    # The replacement does not pre-compute an allowance at all. It PROJECTS
    # the section as it would actually be -- freed pages removed, new pages
    # added -- and tests that. `all_tiles_of` makes "will this page be freed?"
    # answerable up front: a page is freed exactly when every tile that draws
    # from it moves, which is the same test replace_only() uses.
    budget = budget_bytes()
    # `total_cap` overrides the environment so repack_and_compact() can walk
    # the ceiling down without touching global state.
    if total_cap is None:
        total_cap = max_total_pages()
    hard = max_new_pages()

    def _projected(taken, need_map):
        """(bytes, pages) for the section as it would stand with `taken`."""
        n_new = sum(-(-n // 256) for n in need_map.values())
        total = 0
        kept = 0
        for s, p in pmap.items():
            if (s in taken and p.depth == 1
                    and s not in fx_referenced
                    and len(tiles_of.get(s, ())) >= all_tiles_of.get(s, 0)):
                continue                      # fully evacuated -> freed
            kept += 1
            total += _page_bytes(
                FN.D1_PAGE_PX if p.depth == 1 else page_px, p.depth)
        return total + n_new * _page_bytes(page_px, 2), kept + n_new

    # `need[blend]` is the SET of distinct cells that group has taken, not a
    # count, because two pages in the same group can share a cell. Union, not
    # sum -- so a page that is entirely sky costs the group nothing once one
    # page of sky is already in it, and the projection sees that before the
    # take decision rather than after.
    take, need, taken = [], {}, set()
    for slot in candidates:
        blend = _group(slot, D1_GROUPS)
        merged = dict(need.get(blend) or {})
        for k in keys_of[slot]:
            merged[k] = None
        want = len(merged)
        if want > len(free.get(blend, [])) * 256:
            st.pages_nofit += 1
            continue
        trial = {b: len(m) for b, m in need.items()}
        trial[blend] = want
        if hard and sum(-(-n // 256) for n in trial.values()) > hard:
            st.pages_capped += 1
            continue
        n_bytes, n_pages = _projected(taken | {slot}, trial)
        # replace-only, ENFORCED: the field may never end with more pages
        # than it started with. Exact, because `_projected` accounts for
        # blend-group packing, multi-palette expansion and freeing together.
        if replace_only() and n_pages > len(pmap):
            st.pages_notfull += 1
            continue
        if n_bytes > budget or (total_cap and n_pages > total_cap):
            st.pages_capped += 1
            continue
        need[blend] = merged
        take.append(slot)
        taken.add(slot)

    # ---- all or nothing
    #
    # If anything was held back -- by the budget or for want of a free
    # truecolor slot -- promote NOTHING and leave the field exactly as it
    # was. A field that is half truecolor and half paletted is the
    # inconsistency this mode exists to remove, and the half-built version is
    # strictly worse than the untouched one: same picture, two different
    # colour treatments, and a per-field memory bill that bought neither.
    #
    # `pages_nofit` is counted here too, not just `pages_capped`, because
    # from the picture's point of view they are the same event -- a page that
    # should have been promoted was not.
    #
    # `pages_uncovered` deliberately does NOT trigger this. That one means the
    # mod ships no art for the page at all, which no budget and no page size
    # can fix; treating it as a failure would switch the whole feature off for
    # every field the mod only partly covers.
    if all_or_nothing() and take and (st.pages_capped or st.pages_nofit):
        st.pages_allornothing = len(take)
        st.pages_capped = 0
        st.pages_nofit = 0
        return sec9, st
    if not take:
        return sec9, st

    # ---- allocate cells
    #
    # One destination cell per DISTINCT key, walking the blend groups in the
    # order the take loop filled them. `need` already holds exactly the set
    # that has to be allocated, so the count here cannot disagree with the
    # count the projection tested against the ceiling.
    GRID = 16
    alloc = {}                       # key -> (slot, ncx, ncy)
    bufs = {}                        # new slot -> bytearray
    cursor = {b: 0 for b in free}
    order = {b: list(free[b]) for b in free}
    for blend in sorted(need, key=lambda b: (b is None, b)):
        for k in need[blend]:
            i = cursor[blend]
            new_slot = order[blend][i // 256]
            ncx, ncy = (i % 256) % GRID, (i % 256) // GRID
            cursor[blend] = i + 1
            alloc[k] = (new_slot, ncx, ncy)
            if new_slot not in bufs:
                bufs[new_slot] = bytearray(page_px * page_px * 2)
            st.cells += 1

    # ---- fill
    #
    # Once per destination cell, from the source that first claimed it. Every
    # other cell that merged into it is byte-identical by construction (dedup
    # 2) or the same crop of the same image (dedup 1), so which one writes is
    # not a choice that can be got wrong.
    single = {s_ for s_ in take if len({sub[(s_, q)] for q in pal_of[s_]}) == 1
              and len(pal_of[s_]) > 1}
    for slot in take:
        if slot in single:
            continue
        for cx, cy, pal in orig_cells_of.get(slot, ()):
            if sub[(slot, pal)] != pal:
                st.cells_borrowed += 1
    for k, (new_slot, ncx, ncy) in alloc.items():
        art, scx, scy = src_of[k]
        art.blit_into(bufs[new_slot], page_px, GRID, scx, scy, ncx, ncy)

    # ---- rewrite tiles
    buf = bytearray(sec9)
    step = UV_SCALE // GRID                       # 625000, exact
    for slot in take:
        for off, cx, cy, use, pal in tiles_of[slot]:
            new_slot, ncx, ncy = alloc[key_of[(slot, cx, cy, use)]]
            buf[off + T_TEXID] = new_slot
            struct.pack_into('<II', buf, off + T_SRC_X_BIG,
                             ncx * step, ncy * step)
            buf[off + T_SRC_X] = (ncx * (256 // GRID)) & 0xFF
            buf[off + T_SRC_Y] = (ncy * (256 // GRID)) & 0xFF
            st.tiles += 1

    # ---- free original pages nothing references any more
    #
    # Safe: the page-range walks check `present` first -- 0x63A34A tests
    # page->[0xC] before touching a page, and so does the draw at 0x640213.
    # Nothing renumbers, so `layer2_end_page` (0xCFFE0E) keeps its meaning.
    # Worth doing because every present page costs a texture, and the texture
    # count is what breaks (see max_new_pages).
    still_used = set()
    for off in spans:
        still_used.add(buf[off + T_TEXID])
        if buf[off + T_FX_PAGE]:
            still_used.add(buf[off + T_FX_PAGE])
    for slot in take:
        if slot not in still_used and pmap[slot].depth == 1:
            pages[slot] = None
            st.pages_dropped += 1

    # ---- emit
    for new_slot, data in bufs.items():
        pages[new_slot] = FN.Page(new_slot, 0, 2, bytes(data), page_px)
    st.pages_upgraded = len(take)
    st.new_pages = len(bufs)
    out = FN.replace_texture_block(bytes(buf), pages, tex_start, tex_end)
    if log:
        log('  %s: %d page(s) -> %d truecolor page(s), %d cell(s), %d tile(s)'
            % (field, st.pages_upgraded, st.new_pages, st.cells, st.tiles))
    return out, st


# --------------------------------------------------- promote, then pay for it
NO_GROWTH_ENV = 'SEVENTH_NX_FIELD_BG_NO_GROWTH'


def no_growth():
    """
    True when a field may never end with more pages than it started with.

    This is the guarantee `replace_only` was reaching for, obtained a
    different way and at a fraction of the cost. Replace-only got it by
    REFUSING to promote any page that would leave a tile behind -- which is
    87% of them, so the picture barely changes. This gets it by promoting
    freely and then PAYING for the new pages out of the old ones, which are
    mostly empty afterwards (see field_bg_compact).

    MEASURED over all 709 fields at 256px, ceiling 12, all-or-nothing:

        replace-only          341 pages promoted    81,024 tiles
        no-growth + compact  ~2,000 pages promoted  ~300,000 tiles

    with the same promise in both cases: the loader is never asked for more
    textures than the archive already asked it for.
    """
    if legacy():
        return False
    return os.environ.get(NO_GROWTH_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


# Private bookkeeping for apply_growth_mode: the compaction value THIS module
# last wrote. Not a setting; never read by anything else. See the comment in
# apply_growth_mode for why remembering it is necessary.
_OWNED_COMPACT_ENV = 'SEVENTH_NX_FIELD_BG_COMPACT_AUTO'

# Was SEVENTH_NX_FIELD_BG_COMPACT set BEFORE this module was imported?
#
# That is the only moment at which "the user set it" is unambiguous. Anything
# appearing later in the same process was written by apply_growth_mode itself,
# and treating that as a user override is exactly the bug documented below.
_COMPACT_PRESET_BY_USER = bool(
    os.environ.get('SEVENTH_NX_FIELD_BG_COMPACT', '').strip())


def apply_growth_mode(value, env=None):
    """
    One GUI control -> the two switches it drives.

    0 = Off, 1 = Replace only, 2 = No growth. Kept here rather than in the
    GUI so a headless build and the dialog cannot drift apart, which is
    exactly how `field_bg_partial` ended up meaning different things in the
    two places.
    """
    if env is None:
        env = os.environ
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 0
    env[REPLACE_ONLY_ENV] = '1' if v == 1 else '0'
    env[NO_GROWTH_ENV] = '1' if v == 2 else '0'
    # COMPACTION RIDES WITH NO-GROWTH, and nothing else.
    #
    # These were independent switches and that was wrong in the way that
    # matters: someone told to "go back to Replace only" to undo a bad build
    # got a build that still ran the compaction, because it defaults on and
    # the growth control did not touch it. The rollback was not a rollback and
    # the next report was measured against the wrong thing.
    #
    # Compaction exists to PAY for no-growth's promotions. Off and Replace
    # only do not need paying for -- Off accepts the growth and Replace only
    # refuses to create any -- so for those two the safest thing is also the
    # honest one: leave the archive's own page layout alone.
    #
    # SEVENTH_NX_FIELD_BG_COMPACT set explicitly still wins; this only decides
    # the default that the GUI implies.
    #
    # AND IT USED TO POISON ITSELF, WHICH COST A BUILD.
    #
    # The old test was "only write if the variable is absent or blank". In a
    # one-shot process that is right. In the GUI it is not: the GUI keeps ONE
    # process across many builds, so the second call saw the value THIS
    # FUNCTION had written on the first call, decided the user had set it, and
    # refused to touch it. Once it wrote '0' it could never write '1' again.
    #
    # MEASURED on two consecutive real builds. Page growth was moved Off -> No
    # growth and the log still said:
    #
    #     field background: PAGE GROWTH = NO GROWTH
    #     compaction is OFF (SEVENTH_NX_FIELD_BG_COMPACT=0)
    #
    # and no-growth without compaction is the worst of both: COVERAGE fell
    # 42% -> 7%, `left paletted by the NO-GROWTH ceiling` rose 52 -> 158, and
    # the heaviest field went 13 pages -> 15 (against vanilla's 12), because
    # nothing was paying for the mod's own page bloat any more.
    #
    # So the rule is now: the variable is the USER'S only if it was already set
    # when this module was imported (`_COMPACT_PRESET_BY_USER`). Otherwise the
    # growth control owns it, every call, and a wrong build can be undone by
    # moving the dropdown back -- which is the whole point of the control.
    #
    # `env` is passed explicitly by the tests, so the ownership question is
    # asked of `env` too: a value we recorded in `_OWNED_COMPACT_ENV` and that
    # is still there is ours to change.
    import field_bg_compact as _FC
    cur = env.get(_FC.COMPACT_ENV, '')
    ours = env.get(_OWNED_COMPACT_ENV, '')
    user_owns = (_COMPACT_PRESET_BY_USER if env is os.environ
                 else bool(cur.strip()) and not ours)
    if not user_owns:
        want = '1' if v == 2 else '0'
        env[_FC.COMPACT_ENV] = want
        env[_OWNED_COMPACT_ENV] = want
    return v


def _present(sec9, px):
    pages, _s, _e = FN.parse_texture_block(sec9, px)
    return sum(1 for p in pages if p is not None)


def repack_and_compact(sec9, field, art_for, page_px=512, log=None,
                       src_px=None, pals_for=None, compact=None,
                       vanilla_pages=None, post_promote=None):
    # `post_promote` IS ACCEPTED AND IGNORED. Two builds ran the margin
    # split from inside this function and both regressed: from inside
    # `once` it starved promotion (COVERAGE 71% -> 12%), and after the
    # ceiling with its pages counted into the baseline it made the
    # no-growth guarantee vacuous (529 fields over budget, mean 5.7 ->
    # 6.5 pages). The split runs as its own pass before this stage, where
    # it was when the build worked.
    """
    Promote, compact, and -- if no_growth() is on -- keep lowering the
    promotion ceiling until the field genuinely fits.

    Returns (new_sec9, Stats, CompactStats or None).

    WHY IT ITERATES. The repack's ceiling is enforced on its own projection,
    which cannot know what the compaction is about to recover; the compaction
    runs afterwards and only ever removes pages. So the honest way to hold a
    ceiling on the FINAL count is to measure the final count and, if it is
    still over, retry with the promotion ceiling lowered by exactly the
    overshoot. That converges in a step or two because each page of ceiling
    removed takes at least one page off the result.

    It is cheap despite the loop: the expensive part of a repack is decoding
    the mod's .dds, and `ArtProvider` caches those for the whole field. The
    retries re-run the packing arithmetic, not the decode.
    """
    import field_bg_compact as FC
    if compact is None:
        compact = FC.enabled()
    src = src_px if src_px is not None else page_px

    def once(cap):
        out, st = repack_section9(sec9, field, art_for, page_px, log,
                                  src_px=src_px, pals_for=pals_for,
                                  total_cap=cap)
        # THE PROMOTION CHECKS ITSELF TOO. It rewrites tile records and frees
        # pages, so it can produce exactly the same failure compaction can --
        # a tile naming an absent page, which is a null texture handle and a
        # field that does not draw. A field that fails is left alone rather
        # than shipped and looked at on hardware. See FC.self_check.
        if out is not sec9:
            why = FC.self_check(sec9, out, page_px)
            if why is not None:
                if log:
                    log('  ! field background: %s left alone -- promotion '
                        'self-check: %s' % (field, why))
                return sec9, Stats(), None
        cst = None
        if compact:
            try:
                out, cst = FC.compact_section9(out, src_px=page_px)
            except Exception:                                  # noqa: BLE001
                cst = None
        return out, st, cst

    def finish(out, st, cst, limit):
        return out, st, cst

    if not no_growth():
        return once(None)

    try:
        started_with = _present(sec9, src)
    except Exception:                                          # noqa: BLE001
        return once(None)

    # THE TARGET IS VANILLA'S PAGE COUNT, NOT THIS SECTION'S.
    #
    # MEASURED off Cosmos Limit Break's own `chunk.9` sections against the
    # stock archive: the mod ships **more pages than vanilla in 169 fields,
    # +242 pages in total, and 9 fields above 12** -- `fship_2` arrives with
    # 15 where vanilla has 12. All of that is before promotion.
    #
    # So measuring growth against "what this section started with" measured
    # against an already-inflated baseline and never bit. The console was
    # provisioned for VANILLA's worst case; that is the number
    # `field_load_textures` (x86 0x640292) has to be able to serve, and the
    # only one worth holding.
    #
    # HANDOFF-53 4g called the mod's section the baseline. That is correct for
    # the question it was asking -- "did WE grow this field" -- and wrong for
    # this one, which is "can the console load it".
    ceiling = max_total_pages()
    target = started_with if vanilla_pages is None else min(started_with,
                                                            vanilla_pages)
    if ceiling:
        target = min(ceiling, target)

    # START HIGH, not at the target. The repack's ceiling is applied to its
    # own projection, BEFORE compaction has recovered anything -- so setting
    # it to the target on the first attempt refuses the very promotions that
    # compaction was going to pay for, and with all-or-nothing on it refuses
    # the whole field. Begin at the configured ceiling and walk down only
    # when the FINAL count says to.
    cap = ceiling or 0
    best = None
    for _attempt in range(6):
        out, st, cst = once(cap)
        try:
            now = _present(out, page_px)
        except Exception:                                      # noqa: BLE001
            return sec9, Stats(), None
        if now <= target:
            return out, st, cst
        best = (out, st, cst)
        nxt = (cap or now) - (now - target)
        if nxt >= (cap or now) or nxt < 1:
            break
        cap = nxt
    # Could not get under the target at any ceiling -- the field's own
    # untouched section already satisfies it, so use that. Promoting nothing
    # is the correct answer to "this cannot be done without growing".
    # Promoting nothing is the correct answer to "this cannot be done without
    # growing" -- but the margin split still applies to the untouched section,
    # bounded by the same target.
    del best
    return sec9, Stats(), None


# ------------------------------------------------------------------ provider
class ArtProvider:
    """
    Serves PageArt for (field, page, palette) out of one or more .iro files.

    Built from entry LISTINGS only -- nothing is extracted to disk. The
    18,270 field .dds in Cosmos Limit Break are 3 GB; the ones a build
    actually needs are read straight out of the archive, decoded, and thrown
    away. `_no_switch_loader` in build.py can go on skipping them.
    """

    def __init__(self, sources, page_px=512, log=lambda *_: None):
        """`sources` is [(iro_path, allowed_prefixes or None), ...]."""
        self.page_px = page_px
        self.log = log
        self.slots = {}                     # (field, page, pal) -> (path, e)
        self.by_page = {}                   # (field, page) -> {palette, ...}
        self.readers = {}
        self.ambiguous = 0
        self.ambiguous_base = 0        # settled on the page's base dump
        self.ambiguous_arbitrary = 0   # no base dump; first by sorted name
        import iro
        for path, allowed in sources:
            try:
                entries = iro.list_entries(path)
            except Exception as exc:                           # noqa: BLE001
                log('! field art: cannot read %s (%s)' % (path, exc))
                continue
            idx = index_field_dds(entries, allowed)
            self.ambiguous += sum(1 for v in idx.values() if len(v) > 1)
            st = {}
            for key, entry in resolve(
                    idx, not strict(), st).items():
                self.slots[key] = (path, entry)      # later source wins
            self.ambiguous_base += st.get('base', 0)
            self.ambiguous_arbitrary += st.get('arbitrary', 0)
        for (f, pg, q) in self.slots:
            self.by_page.setdefault((f, pg), set()).add(q)
        self._cache = {}
        self._field = None

    def fields(self):
        return {k[0] for k in self.slots}

    def __bool__(self):
        return bool(self.slots)

    def open(self, field):
        """An `art_for(page, palette)` for one field. Caches within the field
        only -- a whole flevel's worth of decoded pages would not fit."""
        self._field = field.lower()
        self._cache = {}
        return self._art_for

    def palettes(self, page):
        """Palette indices the mod actually holds for this page."""
        return self.by_page.get((self._field, page), ())

    def close(self):
        self._cache = {}

    def _art_for(self, page, palette):
        key = (page, palette)
        if key in self._cache:
            return self._cache[key]
        rec = self.slots.get((self._field, page, palette))
        art = None
        if rec is not None:
            path, entry = rec
            try:
                reader = self.readers.get(path)
                if reader is None:
                    reader = self.readers[path] = IroReader(path)
                blob = reader.read(entry)
                if blob:
                    art = PageArt(blob, self.page_px)
            except Exception as exc:                           # noqa: BLE001
                self.log('! field art: %s page %d pal %d -- %s'
                         % (self._field, page, palette, exc))
                art = None
        self._cache[key] = art
        return art
